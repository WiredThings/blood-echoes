#include "bb_stutter_audit.h"
#include "bbport_cpu.h"
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <format>
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_pass_continuation.h"

#include "bbport_toggles.h"
#include "bbport_submit_timing.h"
#import <QuartzCore/CABase.h>
#include "bbport_flush_reason.h"
#include "common/assert.h"
#include "common/logging/log.h"
#include "common/thread.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"

namespace Metal {
namespace {
using EncoderKind = Scheduler::EncoderKind;

constexpr u64 WaitSliceMs = 100;     ///< one bounded wait of Wait()
constexpr u64 StuckWarningMs = 10000; ///< Wait() warns once after this long

// Emitted only for the already bounded single-frame metadata diagnostic. Requested barriers
// and actual drops are separate from Scheduler::MemoryBarrier requests and physical fences.
void CaptureRenderBarriers(const RenderList& list, uintptr_t queue) {
    if (!VideoCore::FrameCapture::NativeRecording()) return;
    const auto counts = list.BarrierSnapshot();
    if (counts.requested)
        VideoCore::FrameCapture::Native(std::format(
            "{{\"op\":\"render_barriers\",\"queue\":{},\"requested\":{},\"dropped\":{}}}",
            queue, counts.requested, counts.dropped));
}

/// A fence wait before everything the encoder does from here on. Render encoders take stages:
/// vertex is the first one every draw goes through.
/// BB_MTL_FENCES=0 or experiment bit MtlNoFences (diagnostics only, unsafe): no fence waits or
/// updates, to measure their cost.
bool FencesEnabled() {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_MTL_FENCES");
        return !(env && env[0] == '0');
    }();
    return enabled && !BbToggle::Experiment(BbToggle::MtlNoFences);
}

void WaitFence(Scheduler::Impl& impl, EncoderKind kind, u32 slot) {
    if (!FencesEnabled()) {
        impl.outstanding_fences &= ~(1u << slot);
        return;
    }
    impl.profile->FenceWait();
    if (VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format(
            "{{\"op\":\"fence_wait\",\"owner\":{},\"kind\":{},\"slot\":{},\"fragment\":false}}",
            (uintptr_t)&impl, unsigned(kind), slot));
    id<MTLFence> fence = impl.fences[slot];
    switch (kind) {
    case EncoderKind::Render:
        [impl.render waitForFence:fence beforeStages:MTLRenderStageVertex];
        break;
    case EncoderKind::Compute:
        [impl.compute waitForFence:fence];
        break;
    case EncoderKind::Blit:
        [impl.blit waitForFence:fence];
        break;
    case EncoderKind::None:
        break;
    }
    impl.outstanding_fences &= ~(1u << slot);
}

void WaitOutstandingFences(Scheduler::Impl& impl, EncoderKind kind) {
    for (u32 slot = 0; slot < Scheduler::Impl::FenceCount; ++slot) {
        if (impl.outstanding_fences & (1u << slot)) {
            WaitFence(impl, kind, slot);
        }
    }
}

// Full clears have no resource payload, and the queue is drained before every other GPU
// encoder. Only this local boundary proves a dead clear-store / next-load pair. Attachment
// adjacency in a diagnostic trace alone is insufficient (guest pointers/aliases may read).
bool FoldClearsEnabled() {
    static const bool enabled=[] {const char* v=std::getenv("BB_MTL_FOLD_CLEARS");return v&&v[0]=='1';}();
    return enabled;
}
void CaptureClearLifetime(const char* action, uintptr_t queue, unsigned count) {
    if(VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format(
            "{{\"op\":\"clear_lifetime\",\"queue\":{},\"action\":\"{}\",\"targets\":{}}}",queue,action,count));
}
// Render-pass clears cover the pass's effective size, which may be clipped by a smaller MRT.
// All queued targets are standalone 2D, single-mip/slice images (QueueColorClear's contract).
bool FullClearExtent(MTLRenderPassDescriptor* pass, id<MTLTexture> target) {
    if(pass.renderTargetArrayLength>1 ||
       (pass.renderTargetWidth && pass.renderTargetWidth<target.width) ||
       (pass.renderTargetHeight && pass.renderTargetHeight<target.height))return false;
    auto large_enough=[&](MTLRenderPassAttachmentDescriptor* a) {
        if(!a.texture)return true;
        return a.level<a.texture.mipmapLevelCount &&
            std::max<NSUInteger>(1,a.texture.width>>a.level)>=target.width &&
            std::max<NSUInteger>(1,a.texture.height>>a.level)>=target.height;
    };
    for(unsigned i=0;i<8;++i)if(!large_enough(pass.colorAttachments[i]))return false;
    return large_enough(pass.depthAttachment) && large_enough(pass.stencilAttachment);
}

bool PreciseHostResidency() {
    static const bool enabled=[] {const char* v=std::getenv("BB_MTL_PRECISE_RESIDENCY");return v&&v[0]=='1';}();
    return enabled;
}

/// Every encoder declares the guest chunks used, as MoltenVK does for every device-address
/// buffer: this is what makes them resident (they are not in the residency set, P8.5; device
/// addresses read and write nothing without residency, D6). BB_MTL_USE_GUEST=0 turns it off
/// (only with BB_MTL_GUEST_IN_SET=1).
void UseGuestChunks(Scheduler::Impl& impl, EncoderKind kind) {
    {
        std::scoped_lock lock{impl.dev.bda_mutex};
        const auto& leaves=impl.dev.bda_leaves;
        if(!leaves.empty()) {
            if(kind==EncoderKind::Render)
                [impl.render useResources:leaves.data() count:leaves.size()
                    usage:MTLResourceUsageRead stages:MTLRenderStageVertex|MTLRenderStageFragment];
            else if(kind==EncoderKind::Compute)
                [impl.compute useResources:leaves.data() count:leaves.size() usage:MTLResourceUsageRead];
        }
    }

    static const bool enabled = [] {
        const char* env = std::getenv("BB_MTL_USE_GUEST");
        return !(env && env[0] == '0');
    }();
    if (!enabled) {
        return;
    }
    const std::size_t count = impl.dev.guest_count.load(std::memory_order_acquire);
    if (count == 0) {
        return;
    }
    const id<MTLResource> __unsafe_unretained* list = impl.dev.guest_buffers.data();
    const MTLResourceUsage usage = MTLResourceUsageRead | MTLResourceUsageWrite;
    switch (kind) {
    case EncoderKind::Render:
        [impl.render useResources:list
                            count:count
                            usage:usage
                           stages:MTLRenderStageVertex | MTLRenderStageFragment];
        break;
    case EncoderKind::Compute:
        [impl.compute useResources:list count:count usage:usage];
        break;
    default:
        break;
    }
}

/// Everything the NSError says: domain, code, text and what each encoder was doing.
std::string DescribeError(NSError* error) {
    if (!error) {
        return "no NSError";
    }
    NSMutableString* text = [NSMutableString
        stringWithFormat:@"%@ code %ld: %@", error.domain, static_cast<long>(error.code),
                         error.localizedDescription];
    NSArray* infos = error.userInfo[MTLCommandBufferEncoderInfoErrorKey];
    for (id<MTLCommandBufferEncoderInfo> info in infos) {
        NSString* state = @"unknown";
        switch (info.errorState) {
        case MTLCommandEncoderErrorStateCompleted:
            state = @"completed";
            break;
        case MTLCommandEncoderErrorStateAffected:
            state = @"affected";
            break;
        case MTLCommandEncoderErrorStatePending:
            state = @"pending";
            break;
        case MTLCommandEncoderErrorStateFaulted:
            state = @"FAULTED";
            break;
        default:
            break;
        }
        [text appendFormat:@"\n  encoder '%@': %@", info.label ?: @"", state];
        for (NSString* signpost in info.debugSignposts) {
            [text appendFormat:@"\n    signpost: %@", signpost];
        }
    }
    return text.UTF8String;
}
} // namespace

id<MTLCommandBuffer> Scheduler::Impl::EnsureCommandBuffer() {
    if (!cmdbuf) {
        MTLCommandBufferDescriptor* descriptor = [MTLCommandBufferDescriptor new];
        // D6: nothing is retained for the GPU; the caches defer every destroy past the tick.
        descriptor.retainedReferences = NO;
        static const bool encoder_status = [] {
            const char* env = std::getenv("BB_MTL_ENCODER_STATUS");
            return !env || env[0] != '0';
        }();
        if (encoder_status) {
            descriptor.errorOptions = MTLCommandBufferErrorOptionEncoderExecutionStatus;
        }
        // The recording threads have no autorelease pool of their own that drains: without this
        // one every command buffer (and what it references) stayed alive for good (P7.3 soak:
        // footprint +45 MB/min, +7 GB/min with the validation layer).
        @autoreleasepool {
            cmdbuf = [dev.queue commandBufferWithDescriptor:descriptor];
            profile->Begin(cmdbuf);
            cmdbuf.label = [NSString stringWithFormat:@"%@ #%llu", name,
                                                      static_cast<unsigned long long>(tick.load())];
        }
    }
    return cmdbuf;
}

Scheduler::Scheduler(Device& device_, const char* name_)
    : device{device_}, impl{std::make_unique<Impl>(device_.GetImpl(), @(name_), current_tick)},
      name{name_} {
    impl->fragment_pass_barriers = [impl->dev.device supportsFamily:MTLGPUFamilyApple1];
    if (impl->fragment_pass_barriers) {
        commands.SetBarrierEncoder(this, [](void* owner, void* encoder) {
            return static_cast<Scheduler*>(owner)->RenderBarrierEncoder(encoder);
        });
    }
    impl->profile = std::make_shared<GpuProfile>(impl->dev.device);
    impl->event = [impl->dev.device newSharedEvent];
    ASSERT_MSG(impl->event, "Could not create the Metal shared event");
    for (auto& fence : impl->fences) {
        fence = [impl->dev.device newFence];
        ASSERT_MSG(fence, "Could not create a Metal fence");
    }
    const char* labels = std::getenv("BB_MTL_LABELS");
    labels_enabled = labels && labels[0] == '1';
    const char* pass = std::getenv("BB_PASS_LABELS");
    pass_labels = (pass && pass[0] == '1') || impl->profile->Enabled();
    const char* hold = std::getenv("BB_MTL_HOLD");
    hold_env = !(hold && hold[0] == '0');
    hold_passes = hold_env;
    const char* replay = std::getenv("BB_MTL_REPLAY_THREAD");
    replay_enabled = hold_env && !(replay && replay[0] == '0');
    if (replay_enabled) {
        replay_thread = std::jthread(std::bind_front(&Scheduler::ReplayThread, this));
    }
}

Scheduler::~Scheduler() {
    // The callback belongs to the rasterizer, which is gone by now.
    on_submit = VideoCore::SubmitFunc{};
    JoinReplay();
    if (replay_thread.joinable()) {
        replay_thread.request_stop();
        replay_posted.fetch_add(1, std::memory_order_acq_rel); // wakes it (no job)
        replay_posted.notify_all();
        replay_thread.join();
        replay_done.store(replay_posted.load(std::memory_order_acquire), std::memory_order_release);
    }
    priority_thread.request_stop();
    priority_cv.notify_all();
    if (priority_thread.joinable()) {
        priority_thread.join();
    }
    // The completed handlers hold `this`: wait for every committed command buffer first.
    if (open_encoder != EncoderKind::None || impl->cmdbuf) {
        Flush();
    }
    Wait(CurrentTick() - 1);
    // The event is signaled before the command buffer is retired: its handler (and Metal's own
    // bookkeeping of what it used) comes a moment later.
    while (handlers_in_flight.load(std::memory_order_acquire) != 0 && !HasFailed()) {
        std::this_thread::yield();
    }
    PopPendingOperations();
    std::queue<PendingOp> priority_rest;
    {
        std::scoped_lock lock{priority_mutex};
        priority_rest.swap(priority_pending_ops);
    }
    while (!priority_rest.empty()) {
        priority_rest.front().callback();
        priority_rest.pop();
    }
}

bool Scheduler::HasOpenCommandBuffer() const noexcept {
    return impl->cmdbuf != nil;
}

void Scheduler::SetRenderTail(std::function<void()> callback) {
    InvalidatePreparedCompute();
    EndEncoder();
    render_tail = std::move(callback);
}

void Scheduler::SetVisibilityQuery(VisibilityQuery query, VisibilityMergePolicy policy) {
    InvalidatePreparedCompute();
    EndEncoder(); // joins replay before the callback or its buffers can change
    visibility_query = std::move(query);
    visibility_merge_policy = policy;
}

void Scheduler::BeginEncoder(EncoderKind kind, const void* pass_descriptor, bool guest, bool bound_only) {
    // Host/direct encoders execute immediately; folding cannot cross hoisted work here.
    if(kind==EncoderKind::Render) pass_descriptor=FoldColorClears(pass_descriptor);
    EndEncoder();
    if (kind == EncoderKind::None) {
        return;
    }
    OpenPhysical(kind, pass_descriptor, std::exchange(need_fence_wait, false), guest, bound_only);
    if(kind==EncoderKind::Render) CommitFoldedClears();
    open_encoder = kind;
}

void Scheduler::OpenPhysical(EncoderKind kind, const void* pass_descriptor, bool wait_all, bool guest, bool bound_only, bool fragment_continuation) {
    id<MTLCommandBuffer> cmdbuf = impl->EnsureCommandBuffer();
    const auto encode_start = impl->profile->Enabled() ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    impl->profile->Open(static_cast<unsigned>(kind), guest);
    // Encoders are autoreleased: the pool drains them once the scheduler holds its reference.
    @autoreleasepool {
    switch (kind) {
    case EncoderKind::Render: {
        MTLRenderPassDescriptor* descriptor = (__bridge MTLRenderPassDescriptor*)pass_descriptor;
        // Never mutate the caller's descriptor or replay it with a consumed Clear.
        if (impl->fragment_pass_barriers || impl->profile->HasSample()) descriptor = [descriptor copy];
        std::pair<void*, u64> query{};
        if (fragment_continuation) {
            query = impl->render_query;
            if (query.first) {
                ASSERT_MSG([impl->dev.device supportsFamily:MTLGPUFamilyApple7],
                           "Fragment pass continuation needs visibility count accumulation");
                if (@available(macOS 26.0, *)) descriptor.visibilityResultType = MTLVisibilityResultTypeAccumulate;
                else UNREACHABLE_MSG("Visibility count accumulation requires macOS 26");
            }
        } else if (guest && visibility_query) {
            query = visibility_query(pass_descriptor);
        }
        if (query.first) descriptor.visibilityResultBuffer = (__bridge id<MTLBuffer>)query.first;
        impl->render_query = query;
        impl->render_guest = guest;
        impl->render_bound_only = bound_only;
        if (impl->fragment_pass_barriers) {
            // Defer only the choice of store action. Keep the original actions for the
            // logical pass's final segment; an intermediate segment stores ALL samples.
            impl->render_pass = [descriptor copy];
            for (unsigned i=0;i<8;++i) if (descriptor.colorAttachments[i].texture) {
                ASSERT_MSG(descriptor.colorAttachments[i].storeAction != MTLStoreActionUnknown, "Unknown final color store action");
                descriptor.colorAttachments[i].storeAction = MTLStoreActionUnknown;
            }
            for (auto attachment : std::array<MTLRenderPassAttachmentDescriptor*,2>{descriptor.depthAttachment, descriptor.stencilAttachment}) if (attachment.texture) {
                ASSERT_MSG(attachment.storeAction != MTLStoreActionUnknown, "Unknown final depth/stencil store action");
                attachment.storeAction = MTLStoreActionUnknown;
            }
        }
        impl->profile->Render(descriptor);
        impl->render = [cmdbuf renderCommandEncoderWithDescriptor:descriptor];
        ASSERT_MSG(impl->render, "Could not create a Metal render encoder");
        if (query.first) {
            [impl->render setVisibilityResultMode:MTLVisibilityResultModeCounting offset:query.second];
        }
        break;
    }
    case EncoderKind::Compute:
        impl->compute = impl->profile->HasSample()
            ? [cmdbuf computeCommandEncoderWithDescriptor:impl->profile->ComputePass()]
            : [cmdbuf computeCommandEncoder];
        break;
    case EncoderKind::Blit:
        impl->blit = impl->profile->HasSample()
            ? [cmdbuf blitCommandEncoderWithDescriptor:impl->profile->BlitPass()]
            : [cmdbuf blitCommandEncoder];
        break;
    case EncoderKind::None:
        return;
    }
    }
    if(VideoCore::FrameCapture::NativeRecording()) {
        VideoCore::FrameCapture::Native(std::format("{{\"op\":\"open\",\"queue\":{},\"kind\":{},\"guest\":{},\"tick\":{}}}",(uintptr_t)this,unsigned(kind),guest?"true":"false",CurrentTick()));
        if(kind==EncoderKind::Render) {
            auto* d=(__bridge MTLRenderPassDescriptor*)pass_descriptor;
            auto attachment=[&](int slot,MTLRenderPassAttachmentDescriptor* a) {
                id<MTLTexture> t=a.texture;if(!t)return;
                id<MTLTexture> parent=t.parentTexture;
                VideoCore::FrameCapture::Native(std::format("{{\"op\":\"attachment\",\"queue\":{},\"slot\":{},\"id\":{},\"parent\":{},\"w\":{},\"h\":{},\"format\":{},\"level\":{},\"slice\":{},\"load\":{},\"store\":{}}}",(uintptr_t)this,slot,(uintptr_t)(__bridge void*)t,(uintptr_t)(__bridge void*)parent,t.width,t.height,unsigned(t.pixelFormat),a.level,a.slice,unsigned(a.loadAction),unsigned(a.storeAction)));
                if(t.label)VideoCore::FrameCapture::NativeLabel(std::format("texture {} {}",(uintptr_t)(__bridge void*)t,t.label.UTF8String).c_str(),(uintptr_t)this);
            };
            for(unsigned i=0;i<8;++i)attachment(int(i),d.colorAttachments[i]);
            attachment(-1,d.depthAttachment);attachment(-2,d.stencilAttachment);
        }
    }
    // Direct bindings already declare their resources. Only explicitly classified host
    // kernels may omit the broad indirect-guest set; every unknown/game shader keeps it.
    const bool indirect_guest=kind!=EncoderKind::Blit && !(PreciseHostResidency() && bound_only);
    if(kind==EncoderKind::Compute)impl->compute_guest_declared=indirect_guest;
    if(kind==EncoderKind::Render)impl->render_guest_declared=indirect_guest;
    if(indirect_guest)UseGuestChunks(*impl, kind);
    if(VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format("{{\"op\":\"residency\",\"queue\":{},\"indirect_guest\":{}}}",(uintptr_t)this,indirect_guest?"true":"false"));
    // The slot this encoder updates at its end may still wait for a waiter (more encoders than
    // slots without a barrier): that one waits now, which keeps the chain.
    const u32 slot = impl->next_fence;
    if (impl->outstanding_fences & (1u << slot)) {
        WaitFence(*impl, kind, slot);
    }
    if (wait_all) {
        WaitOutstandingFences(*impl, kind);
    }
    if (labels_enabled) {
        for (const std::string& group : debug_groups) {
            NSString* label = @(group.c_str());
            switch (kind) {
            case EncoderKind::Render:
                [impl->render pushDebugGroup:label];
                break;
            case EncoderKind::Compute:
                [impl->compute pushDebugGroup:label];
                break;
            case EncoderKind::Blit:
                [impl->blit pushDebugGroup:label];
                break;
            case EncoderKind::None:
                break;
            }
        }
    }
    if (impl->profile->Enabled()) impl->profile->CpuEncode(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-encode_start).count());
}

void Scheduler::RenderEncoder(const void* pass_descriptor) {
    InvalidatePreparedCompute();
    ASSERT_MSG(pass_descriptor, "RenderEncoder without a pass descriptor");
    BeginEncoder(EncoderKind::Render, pass_descriptor);
    render_state.reset();
}

void Scheduler::HostRenderEncoder(const void* pass_descriptor) {
    InvalidatePreparedCompute();
    ASSERT_MSG(pass_descriptor, "HostRenderEncoder without a pass descriptor");
    BeginEncoder(EncoderKind::Render,pass_descriptor,false,true);
    render_state.reset();
}
bool Scheduler::CanInlineBufferUpdate() const noexcept {
    return open_encoder==EncoderKind::Render && render_state && commands_active &&
        !hoisting && !render_tail_pending && !running_render_tail && !impl->pending_clears;
}
bool Scheduler::InlineBufferUpdate(void* pipeline,void* buffer,u64 offset,const void* data,u32 bytes) {
    InvalidatePreparedCompute();
    if(!CanInlineBufferUpdate())return false;
    if(!holding) {JoinReplay();WaitOutstandingFences(*impl,EncoderKind::Render);}
    commands.VertexBufferUpdate(pipeline,buffer,offset,data,bytes);
    return true;
}

void Scheduler::RenderEncoder(const void* pass_descriptor, const VideoCore::RenderState& state, bool guest, bool bound_only) {
    InvalidatePreparedCompute();
    ASSERT_MSG(pass_descriptor, "RenderEncoder without a pass descriptor");
    // Experiment bit MtlNoHold switches holding off while the game runs (taken at a pass begin).
    hold_passes = hold_env && !BbToggle::Experiment(BbToggle::MtlNoHold);
    if (!hold_passes) {
        BeginEncoder(EncoderKind::Render, pass_descriptor, guest, bound_only);
        render_state = state;
        commands.BeginDirect((__bridge void*)impl->render);
        commands.TrackConsumedClear(state, guest && bound_only);
        commands_active = true;
        return;
    }
    pass_descriptor=FoldColorClears(pass_descriptor);
    if (holding && !render_tail_pending && replay_enabled && open_encoder == EncoderKind::Render &&
        !BbToggle::Experiment(BbToggle::MtlNoReplayThread)) {
        PostReplay(); // the held pass is encoded on the replay thread while this one is recorded
    } else {
        EndEncoder();
    }
    impl->EnsureCommandBuffer(); // (open already after a PostReplay: no Metal call)
    // Held: the descriptor is kept (the caller's is autoreleased) until the pass is released.
    impl->held_pass = (__bridge MTLRenderPassDescriptor*)pass_descriptor;
    open_encoder = EncoderKind::Render;
    render_state = state;
    holding = true;
    held_guest = guest;
    held_bound_only = bound_only;
    ++held_serial;
    hoisting = false;
    hoisted = false;
    held_label.clear();
    commands.BeginRecording();
    commands.TrackConsumedClear(state, guest && bound_only);
    commands_active = true;
}

u32 Scheduler::ContinuationBlockers() const noexcept {
    return (!commands_active ? 1u : 0u) | (!OpenRenderState() ? 2u : 0u) |
        (commands.HasBarrier() ? 4u : 0u) | (need_fence_wait ? 8u : 0u) |
        ((hoisting || hoisted) ? 16u : 0u) |
        ((render_tail_pending || running_render_tail) ? 32u : 0u) |
        (impl->pending_clears ? 64u : 0u) | (impl->folded_clears ? 128u : 0u);
}

bool Scheduler::ContinueDepthLayout(const VideoCore::RenderState& next) {
    InvalidatePreparedCompute();
    if (!PassContinuation::DepthLayoutEnabled() || ContinuationBlockers() ||
        !commands.CanContinueDepthLayout() ||
        !PassContinuation::DepthLayoutWritePromotion(*render_state, next)) return false;
    if (visibility_query) {
        if (visibility_merge_policy == VisibilityMergePolicy::Forbidden) return false;
        if (visibility_merge_policy == VisibilityMergePolicy::NativeDensity) {
            auto texture = (__bridge id<MTLTexture>)reinterpret_cast<void*>(next.depth_stencil_attachment.image_view);
            // At reduced dimensions the resolver rounds up EACH pass's scaled count.
            // ceil(a*s)+ceil(b*s) need not equal ceil((a+b)*s). Retain the split there.
            if (texture.pixelFormat != MTLPixelFormatDepth32Float_Stencil8 ||
                !PassContinuation::QueryDensityIsAdditive(texture.width, texture.height)) return false;
        }
    }
    // Metal's physical attachment descriptor has no Vulkan image-layout field.
    // Keep the original encoder, depth/stencil contents, pending commands and query
    // counter. Only the logical memo changes; fixed-function states still bind normally.
    render_state = next;
    if (!PassContinuation::DepthLayoutMayPromote(next.depth_stencil_attachment.image_layout))
        commands.InvalidateConsumedClear();
    if (VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format("{{\"op\":\"depth_layout_continued\",\"queue\":{}}}", uintptr_t(this)));
    return true;
}

bool Scheduler::ContinueConsumedClear(const VideoCore::RenderState& next) {
    InvalidatePreparedCompute();
    if (!PassContinuation::Enabled() || !commands_active || !OpenRenderState() ||
        !commands.CanContinueConsumedClear() || commands.HasBarrier() || need_fence_wait || hoisting || hoisted ||
        render_tail_pending || running_render_tail || impl->pending_clears || impl->folded_clears ||
        !PassContinuation::ConsumedClearBecomesLoad(*render_state, next)) return false;
    // Remember the same load state a separate loading encoder would have had.
    render_state = next;
    commands.InvalidateConsumedClear();
    if (VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format("{{\"op\":\"consumed_clear_continued\",\"queue\":{},\"width\":{},\"height\":{}}}", uintptr_t(this), next.width, next.height));
    return true;
}

void Scheduler::RequireGuestRender() {
    InvalidatePreparedCompute();
    commands.InvalidateConsumedClear();
    if(!PreciseHostResidency())return;
    if(holding) {held_bound_only=false;return;}
    JoinReplay();
    if(open_encoder==EncoderKind::Render && impl->render && !impl->render_guest_declared) {
        UseGuestChunks(*impl,EncoderKind::Render);impl->render_guest_declared=true;
    }
}

void Scheduler::ReleaseHeldPass() {
    InvalidatePreparedCompute();
    JoinReplay();
    if (!holding) {
        return;
    }
    CommitFoldedClears();
    holding = false;
    hoisting = false;
    // What goes before the pass first: the hoisted compute encoder, whose writes the pass waits
    // for (as it waits for earlier encoders when a barrier was recorded in it).
    if (impl->compute) {
        ClosePhysical(EncoderKind::Compute);
    }
    const bool preserve_hoist_order=hoisted;
    if (hoisted || commands.HasBarrier()) {
        need_fence_wait = true;
    }
    hoisted = false;
    OpenPhysical(EncoderKind::Render, (__bridge const void*)impl->held_pass,
                 std::exchange(need_fence_wait, false), held_guest, held_bound_only);
    impl->held_pass = nil;
    // The render pass consumes the hoisted producer's fence, but its next
    // encoder may still read the producer's indirect/untracked buffer. Carry
    // that dependency through an empty render pass too; do not rely on CPU pace.
    need_fence_wait |= preserve_hoist_order;
    if (pass_labels && !held_label.empty()) {
        impl->render.label = @(held_label.c_str());
        VideoCore::FrameCapture::NativeLabel(held_label.c_str(),(uintptr_t)this);
        impl->profile->Label(held_label.c_str());
    }
    commands.Replay((__bridge void*)impl->render);
    CaptureRenderBarriers(commands, (uintptr_t)this);
    commands.ContinueDirect((__bridge void*)impl->render);
}

void Scheduler::PostReplay() {
    CommitFoldedClears(); // recording thread, before the immutable descriptor goes to replay
    const u32 posted = replay_posted.load(std::memory_order_relaxed); // (only this thread posts)
    WaitReplay(posted + 1 - ReplayDepth); // a free slot
    const u32 slot = posted % ReplayDepth;
    ReplayJob& job = replay_ring[slot];
    // What ReleaseHeldPass and EndEncoder do on this thread, but the encoding goes to the job.
    // The hoisted compute encoder is the only one open while a pass is held.
    job.guest = held_guest;
    job.bound_only = held_bound_only;
    job.close_compute = hoisted;
    job.wait_all = std::exchange(need_fence_wait, false) || hoisted || commands.HasBarrier();
    need_fence_wait = hoisted; // the following encoder also follows the producer
    job.label = pass_labels ? held_label : std::string{};
    holding = false;
    hoisting = false;
    hoisted = false;
    impl->replay_passes[slot] = impl->held_pass;
    impl->held_pass = nil;
    commands.TakeRecording(job.list);
    commands.End();
    commands_active = false;
    open_encoder = EncoderKind::None;
    render_state.reset();
    ++replay_jobs;
    replay_posted.store(posted + 1, std::memory_order_release);
    replay_posted.notify_one();
}

void Scheduler::JoinReplay() {
    WaitReplay(replay_posted.load(std::memory_order_acquire));
}

void Scheduler::WaitReplay(u32 target) {
    BbAudit::Scope detailed_audit{BbAudit::MetalReplayWait,target,2000000};
    // Jobs done reaches `target` (wrapping counters).
    const auto reached = [target](u32 done) { return static_cast<s32>(done - target) >= 0; };
    u32 done = replay_done.load(std::memory_order_acquire);
    if (reached(done)) {
        return;
    }
    const auto start = std::chrono::steady_clock::now();
    for (int spin = 0; !reached(done) && spin < 4096; ++spin) {
        BbCpu::Pause();
        done = replay_done.load(std::memory_order_acquire);
    }
    while (!reached(done)) {
        replay_done.wait(done, std::memory_order_acquire);
        done = replay_done.load(std::memory_order_acquire);
    }
    const auto now = std::chrono::steady_clock::now();
    const u64 waited = std::chrono::duration_cast<std::chrono::nanoseconds>(now - start).count();
    BbStats::rec_replay_wait_ns.fetch_add(waited, std::memory_order_relaxed);
    if (BbStats::enabled) {
        ++replay_joins;
        replay_wait_ns += waited;
        if (now - replay_window >= std::chrono::seconds(5)) {
            if (replay_window != std::chrono::steady_clock::time_point{}) {
                const double seconds = std::chrono::duration<double>(now - replay_window).count();
                std::printf("Metal replay thread: %.0f passes/s, the recording thread waited for "
                            "it %.0f/s (%.2f ms/s)\n",
                            replay_jobs / seconds, replay_joins / seconds,
                            replay_wait_ns / 1e6 / seconds);
            }
            replay_window = now;
            replay_jobs = replay_joins = replay_wait_ns = 0;
        }
    }
}

void Scheduler::RunReplayJob(u32 slot) {
    ReplayJob& job = replay_ring[slot];
    @autoreleasepool {
        if (job.close_compute) {
            CloseNative(EncoderKind::Compute);
        }
        OpenPhysical(EncoderKind::Render, (__bridge const void*)impl->replay_passes[slot],
                     job.wait_all, job.guest, job.bound_only);
        impl->replay_passes[slot] = nil;
        if (!job.label.empty()) {
            impl->render.label = @(job.label.c_str());
            VideoCore::FrameCapture::NativeLabel(job.label.c_str(),(uintptr_t)this);
            impl->profile->Label(job.label.c_str());
        }
        job.list.Replay((__bridge void*)impl->render);
        CaptureRenderBarriers(job.list, (uintptr_t)this);
        job.list.End();
        CloseNative(EncoderKind::Render);
    }
}

void Scheduler::ReplayThread(std::stop_token stoken) {
    Common::SetCurrentThreadName("bb:MtlReplay");
    u32 done = 0;
    while (true) {
        u32 posted = replay_posted.load(std::memory_order_acquire);
        // Passes come every few hundred microseconds: a short spin before sleeping.
        for (int spin = 0; posted == done && spin < 2048; ++spin) {
            BbCpu::Pause();
            posted = replay_posted.load(std::memory_order_acquire);
        }
        if (posted == done) {
            replay_posted.wait(done, std::memory_order_acquire);
            continue;
        }
        if (stoken.stop_requested()) {
            return;
        }
        for (; done != posted; ++done) {
            RunReplayJob(done % ReplayDepth);
            replay_done.store(done + 1, std::memory_order_release);
            replay_done.notify_one();
        }
    }
}

void Scheduler::ComputeEncoder(const char* label, bool bound_only) {
    InvalidatePreparedCompute();
    JoinReplay();
    if (holding && hoisting) {
        RestoreFoldedClears(); // a dispatch may read ANY queued color, including indirect aliases
        // Before the held pass: one compute encoder for everything hoisted into this pass, after
        // every encoder before it (the barrier Vulkan records before a hoisted dispatch).
        if (!impl->compute) {
            need_fence_wait = false; // (it waits for everything before it anyway)
            OpenPhysical(EncoderKind::Compute, nullptr, true, false, bound_only);
            hoisted = true;
            if (label) {
                LabelEncoder(label);
            }
        }
        // A later hoisted guest dispatch can reuse the host encoder: upgrade before it runs.
        if(!bound_only && !impl->compute_guest_declared) {UseGuestChunks(*impl,EncoderKind::Compute);impl->compute_guest_declared=true;}
        return;
    }
    if (open_encoder != EncoderKind::Compute) {
        BeginEncoder(EncoderKind::Compute, nullptr, false, bound_only);
        if (label) {
            LabelEncoder(label);
        }
    }
    if(!bound_only && !impl->compute_guest_declared) {UseGuestChunks(*impl,EncoderKind::Compute);impl->compute_guest_declared=true;}
}

void Scheduler::BlitEncoder(const char* label) {
    InvalidatePreparedCompute();
    JoinReplay();
    if (open_encoder != EncoderKind::Blit) {
        BeginEncoder(EncoderKind::Blit, nullptr);
        if (label) {
            LabelEncoder(label);
        }
    }
}

bool Scheduler::QueueColorClear(void* handle, const std::array<double,4>& color) {
    InvalidatePreparedCompute();
    static const bool enabled=[]{const char* v=std::getenv("BB_MTL_BATCH_CLEARS");return v&&v[0]=='1';}();
    if(!enabled)return false;
    id<MTLTexture> texture=(__bridge id<MTLTexture>)handle;
    // Private, standalone single-layer images cannot alias another queued target.
    if(!texture || texture.textureType!=MTLTextureType2D || texture.sampleCount!=1 ||
       texture.mipmapLevelCount!=1 || texture.arrayLength!=1 || texture.parentTexture ||
       texture.buffer || texture.iosurface || texture.heap || texture.isAliasable ||
       texture.storageMode!=MTLStorageModePrivate || !(texture.usage&MTLTextureUsageRenderTarget))return false;
    switch(texture.pixelFormat) {
    case MTLPixelFormatDepth16Unorm:case MTLPixelFormatDepth32Float:case MTLPixelFormatStencil8:
    case MTLPixelFormatDepth24Unorm_Stencil8:case MTLPixelFormatDepth32Float_Stencil8:
    case MTLPixelFormatX24_Stencil8:case MTLPixelFormatX32_Stencil8:return false;
    default:break;
    }
    if(impl->pending_clears) {
        auto first=impl->pending_clears.colorAttachments[0].texture;
        if(first.width!=texture.width || first.height!=texture.height)EndEncoder();
    }
    if(!impl->pending_clears) {
        EndEncoder();
        if(open_encoder!=EncoderKind::None)EndEncoder(); // a render-tail callback may encode work
        impl->EnsureCommandBuffer();
        impl->pending_clears=[MTLRenderPassDescriptor renderPassDescriptor];
    }
    unsigned slot=0;
    for(;slot<impl->pending_clear_count;++slot)if(impl->pending_clears.colorAttachments[slot].texture==texture)break;
    if(slot==8) {EndEncoder();return QueueColorClear(handle,color);}
    auto attachment=impl->pending_clears.colorAttachments[slot];
    attachment.texture=texture;attachment.loadAction=MTLLoadActionClear;
    attachment.storeAction=MTLStoreActionStore;
    attachment.clearColor=MTLClearColorMake(color[0],color[1],color[2],color[3]);
    impl->pending_clear_count=std::max(impl->pending_clear_count,slot+1);++impl->clear_queued;
    if(std::getenv("BB_FRAME_STATS")) {
        const auto now=std::chrono::steady_clock::now();
        if(now-impl->clear_reported>std::chrono::seconds(5)) {
            std::printf("Metal color clear batching: clears=%llu, batches=%llu; standalone existing proxies, no new textures; folded=%llu targets=%llu hoist-fallbacks=%llu\n",(unsigned long long)impl->clear_queued,(unsigned long long)impl->clear_passes,(unsigned long long)impl->clear_folded,(unsigned long long)impl->clear_folded_targets,(unsigned long long)impl->clear_fold_fallbacks);
            impl->clear_reported=now;
        }
    }
    return true;
}

const void* Scheduler::FoldColorClears(const void* raw) {
    if(!FoldClearsEnabled() || !impl->pending_clears || impl->folded_clears ||
       open_encoder!=EncoderKind::None || holding || render_tail_pending || running_render_tail)
        return raw;
    auto* pass=(__bridge MTLRenderPassDescriptor*)raw;
    if(!pass)return raw;
    auto* clears=impl->pending_clears;
    // All-or-nothing: an unmatched target's clear may have a shader/CPU/alias reader later.
    for(unsigned i=0;i<impl->pending_clear_count;++i) {
        auto target=clears.colorAttachments[i].texture;
        bool match=false;
        for(unsigned slot=0;slot<8;++slot) {
            auto a=pass.colorAttachments[slot];
            if(a.texture!=target)continue;
            if(a.level || a.slice || a.depthPlane || a.resolveTexture ||
               (a.loadAction!=MTLLoadActionLoad && a.loadAction!=MTLLoadActionClear) ||
               !FullClearExtent(pass,target))return raw;
            if(match)return raw; // reject duplicate binding of one target
            match=true;
        }
        if(!match)return raw;
    }
    // The descriptor is already retained by the existing clear queue; no texture/buffer/cache
    // allocation. Copy only the incoming CPU descriptor so the caller's load state is unchanged.
    pass=[pass copy];
    for(unsigned slot=0;slot<8;++slot) {
        auto a=pass.colorAttachments[slot];
        impl->folded_original_loads[slot]=a.loadAction;
        impl->folded_original_colors[slot]=a.clearColor;
        for(unsigned i=0;i<impl->pending_clear_count;++i) {
            if(a.texture==clears.colorAttachments[i].texture && a.loadAction==MTLLoadActionLoad) {
                a.loadAction=MTLLoadActionClear;
                a.clearColor=clears.colorAttachments[i].clearColor;
            }
        }
    }
    impl->folded_clears=clears;
    impl->folded_pass=pass;
    impl->pending_clears=nil;
    impl->pending_clear_count=0;
    need_fence_wait|=std::exchange(impl->pending_clear_barrier,false);
    return (__bridge const void*)pass;
}

void Scheduler::CommitFoldedClears() {
    if(!impl->folded_clears)return;
    unsigned count=0;
    for(unsigned i=0;i<8;++i)if(impl->folded_clears.colorAttachments[i].texture)++count;
    ++impl->clear_folded;impl->clear_folded_targets+=count;
    CaptureClearLifetime("folded",(uintptr_t)this,count);
    impl->folded_clears=nil;
    impl->folded_pass=nil;
}

void Scheduler::RestoreFoldedClears() {
    if(!impl->folded_clears)return;
    // Only called before the first real hoisted compute dispatch. Keep the original clear
    // before it; the held pass keeps its ORIGINAL load/clear action after the dispatch.
    for(unsigned i=0;i<8;++i) {
        impl->held_pass.colorAttachments[i].loadAction=impl->folded_original_loads[i];
        impl->held_pass.colorAttachments[i].clearColor=impl->folded_original_colors[i];
    }
    impl->pending_clears=impl->folded_clears;impl->folded_clears=nil;
    impl->folded_pass=nil;
    for(unsigned i=0;i<8;++i)if(impl->pending_clears.colorAttachments[i].texture)++impl->pending_clear_count;
    ++impl->clear_fold_fallbacks;
    CaptureClearLifetime("hoist_fallback",(uintptr_t)this,impl->pending_clear_count);
    // Honor dependencies on BOTH sides, including the pending clear's original barrier.
    need_fence_wait=true;impl->pending_clear_barrier=true;
    FlushColorClears();
}

void Scheduler::FlushColorClears() {
    if(!impl->pending_clears)return;
    MTLRenderPassDescriptor* pass=impl->pending_clears;
    impl->pending_clears=nil;impl->pending_clear_count=0;
    const bool after=std::exchange(impl->pending_clear_barrier,false);
    OpenPhysical(EncoderKind::Render,(__bridge const void*)pass,std::exchange(need_fence_wait,false),false,true);
    impl->render.label=@"Scene color clear batch";
    VideoCore::FrameCapture::NativeLabel("Scene color clear batch",(uintptr_t)this);
    impl->profile->Label("Scene color clear batch");
    CloseNative(EncoderKind::Render);++impl->clear_passes;
    if(after)need_fence_wait=true;
}

void Scheduler::EndEncoder() {
    InvalidatePreparedCompute();
    JoinReplay();
    FlushColorClears();
    if (open_encoder != EncoderKind::None) {
        ReleaseHeldPass();
        ClosePhysical(open_encoder);
        open_encoder = EncoderKind::None;
        render_state.reset();
    }
    if (render_tail_pending && !running_render_tail) {
        render_tail_pending = false;
        running_render_tail = true;
        if (render_tail) render_tail();
        running_render_tail = false;
    }
}

void Scheduler::ClosePhysical(EncoderKind kind) {
    if (kind == EncoderKind::Render && commands_active) {
        CaptureRenderBarriers(commands, (uintptr_t)this);
        commands.End();
        commands_active = false;
    }
    CloseNative(kind);
}

void Scheduler::CloseNative(EncoderKind kind, bool preserve_render_attachments) {
    if(VideoCore::FrameCapture::NativeRecording())VideoCore::FrameCapture::Native(std::format("{{\"op\":\"close\",\"queue\":{},\"kind\":{}}}",(uintptr_t)this,unsigned(kind)));
    const auto encode_start = impl->profile->Enabled() ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
    // Every encoder updates the next fence of the ring at its end (see MemoryBarrier).
    const u32 slot = impl->next_fence;
    id<MTLFence> fence = impl->fences[slot];
    switch (kind) {
    case EncoderKind::Render:
        if (impl->fragment_pass_barriers) {
            const auto store = [&](MTLRenderPassAttachmentDescriptor* a) {
                if (preserve_render_attachments) {
                    ASSERT_MSG(a.texture.storageMode != MTLStorageModeMemoryless,
                               "Cannot preserve a memoryless attachment across a fragment dependency");
                    // A descriptor carrying a resolve target may not select Store-only.
                    // Keep the multisample surface AND resolve it; the final segment still
                    // uses the caller's original final-store/resolve action.
                    return a.resolveTexture ? MTLStoreActionStoreAndMultisampleResolve : MTLStoreActionStore;
                }
                return a.storeAction;
            };
            for (unsigned i=0;i<8;++i) if (auto a=impl->render_pass.colorAttachments[i]; a.texture)
                [impl->render setColorStoreAction:store(a) atIndex:i];
            if (auto a=impl->render_pass.depthAttachment; a.texture) [impl->render setDepthStoreAction:store(a)];
            if (auto a=impl->render_pass.stencilAttachment; a.texture) [impl->render setStencilStoreAction:store(a)];
        }

        for (size_t i = 0; labels_enabled && i < debug_groups.size(); ++i) {
            [impl->render popDebugGroup];
        }
        if (FencesEnabled() || preserve_render_attachments) {
            [impl->render updateFence:fence afterStages:MTLRenderStageFragment];
        }
        [impl->render endEncoding];
        impl->render = nil;
        impl->render_pass = nil;
        break;
    case EncoderKind::Compute:
        for (size_t i = 0; labels_enabled && i < debug_groups.size(); ++i) {
            [impl->compute popDebugGroup];
        }
        if (FencesEnabled()) {
            [impl->compute updateFence:fence];
        }
        [impl->compute endEncoding];
        impl->compute = nil;
        break;
    case EncoderKind::Blit:
        for (size_t i = 0; labels_enabled && i < debug_groups.size(); ++i) {
            [impl->blit popDebugGroup];
        }
        if (FencesEnabled()) {
            [impl->blit updateFence:fence];
        }
        [impl->blit endEncoding];
        impl->blit = nil;
        break;
    case EncoderKind::None:
        return;
    }
    impl->profile->Close();
    if (impl->profile->Enabled()) impl->profile->CpuEncode(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-encode_start).count());
    impl->outstanding_fences |= 1u << slot;
    impl->next_fence = (slot + 1) % Impl::FenceCount;
}

void Scheduler::EndRendering() {
    InvalidatePreparedCompute();
    if(VideoCore::FrameCapture::NativeMode()&&VideoCore::FrameCapture::Active())JoinReplay();
    if (open_encoder == EncoderKind::Render || impl->pending_clears) {
        EndEncoder();
    }
}

void Scheduler::BeginOrderedHostCompute(const char* label) {
    InvalidatePreparedCompute();
    EndRendering();
    FlushRenderTail();
    JoinReplay(); // query count/scales may have been produced on the replay thread
    MemoryBarrier();
    ComputeEncoder(label, true);
}

void Scheduler::OrderAfterHostCompute() {
    InvalidatePreparedCompute();
    ASSERT_MSG(open_encoder == EncoderKind::Compute && !holding,
               "Ordered host compute requires an unheld compute encoder");
    MemoryBarrier();
    need_fence_wait = true; // preserved until a NEW physical encoder consumes it
}

void* Scheduler::RenderBarrierEncoder(void* encoder) {
    // May run on the replay thread. No JoinReplay/EndEncoder, submission, tail execution,
    // tick advancement or retirement is allowed inside a recording's native execution.
    ASSERT_MSG(encoder == (__bridge void*)impl->render && impl->render_pass,
               "Render barrier owner/encoder mismatch");
    MTLRenderPassDescriptor* next = [impl->render_pass copy];
    NSString* label = impl->render.label;
    const bool guest = impl->render_guest;
    // RequireGuestRender may have upgraded a previously bound-only encoder. Preserve
    // its ACTUAL residency union, while retaining the immutable guest/host query tag.
    const bool bound_only = impl->render_bound_only && !impl->render_guest_declared;
    id<MTLFence> boundary = impl->fences[impl->next_fence];
    for (unsigned i=0;i<8;++i) if(next.colorAttachments[i].texture) next.colorAttachments[i].loadAction=MTLLoadActionLoad;
    for (auto a : std::array<MTLRenderPassAttachmentDescriptor*,2>{next.depthAttachment,next.stencilAttachment}) if(a.texture) a.loadAction=MTLLoadActionLoad;
    // CloseNative uses the ring's real after-fragment producer fence. OpenPhysical waits
    // before vertex, redeclares the same guest/residency ownership, and reopens profiling.
    CloseNative(EncoderKind::Render, true);
    OpenPhysical(EncoderKind::Render, (__bridge void*)next, true, guest, bound_only, true);
    // The unsafe no-fence experiment must not erase this required intra-logical-pass edge.
    if (!FencesEnabled()) [impl->render waitForFence:boundary beforeStages:MTLRenderStageVertex];
    impl->render.label=label;
    if(label) impl->profile->Label(label.UTF8String);
    return (__bridge void*)impl->render;
}

void Scheduler::MemoryBarrier(bool proven_empty_raw_pass) {
    InvalidatePreparedCompute();
    if(VideoCore::FrameCapture::NativeRecording())VideoCore::FrameCapture::Native(std::format("{{\"op\":\"barrier\",\"kind\":{},\"holding\":{},\"hoisting\":{}}}",unsigned(open_encoder),holding?"true":"false",hoisting?"true":"false"));
    impl->profile->Barrier();
    if(impl->pending_clears) {
        // All queued operations are independent constant overwrites. Preserve a
        // conservative fence both before the batch and before its next consumer.
        need_fence_wait=true;impl->pending_clear_barrier=true;return;
    }
    FlushRenderTail();
    if (holding) {
        if (hoisting) {
            JoinReplay(); // (the job may still end the previous pass's hoisted encoder)
            if (impl->compute) {
                [impl->compute memoryBarrierWithScope:MTLBarrierScopeBuffers | MTLBarrierScopeTextures];
                WaitOutstandingFences(*impl, EncoderKind::Compute);
            }
            // (else the hoisted encoder, when it comes, waits for everything before it)
            return;
        }
        // In the pass: recorded; the render encoder waits for the earlier encoders' fences when
        // it is made (HasBarrier).
        commands.MemoryBarrier();
        return;
    }
    JoinReplay();
    switch (open_encoder) {
    case EncoderKind::Compute:
        [impl->compute memoryBarrierWithScope:MTLBarrierScopeBuffers | MTLBarrierScopeTextures];
        WaitOutstandingFences(*impl, EncoderKind::Compute);
        break;
    case EncoderKind::Render:
        if (commands_active) {
            // Guest direct draws already go through this list. Its optional filter knows
            // whether a draw occurred since the last identical render-only barrier.
            commands.MemoryBarrier();
        } else {
            if (impl->fragment_pass_barriers) {
                ASSERT_MSG(proven_empty_raw_pass,
                    "Raw render state is not tracked: generic fragment barriers require RenderList, or an explicitly proven empty raw pass");
                RenderBarrierEncoder((__bridge void*)impl->render);
            } else {
                [impl->render memoryBarrierWithScope:MTLBarrierScopeBuffers | MTLBarrierScopeTextures
                                         afterStages:MTLRenderStageVertex | MTLRenderStageFragment
                                        beforeStages:MTLRenderStageVertex | MTLRenderStageFragment];
            }
        }
        WaitOutstandingFences(*impl, EncoderKind::Render);
        break;
    case EncoderKind::Blit:
        // Blit encoders have no memory barrier: the next encoder starts after this one.
        EndEncoder();
        need_fence_wait = true;
        break;
    case EncoderKind::None:
        need_fence_wait = true;
        break;
    }
}

void Scheduler::PushDebugGroup(const char* group) {
    InvalidatePreparedCompute();
    if (!labels_enabled) {
        return;
    }
    ReleaseHeldPass(); // BB_MTL_LABELS (diagnostics): groups apply to physical encoders only
    debug_groups.emplace_back(group);
    NSString* label = @(group);
    switch (open_encoder) {
    case EncoderKind::Render:
        [impl->render pushDebugGroup:label];
        break;
    case EncoderKind::Compute:
        [impl->compute pushDebugGroup:label];
        break;
    case EncoderKind::Blit:
        [impl->blit pushDebugGroup:label];
        break;
    case EncoderKind::None:
        break;
    }
}

void Scheduler::PopDebugGroup() {
    InvalidatePreparedCompute();
    if (!labels_enabled || debug_groups.empty()) {
        return;
    }
    ReleaseHeldPass();
    debug_groups.pop_back();
    switch (open_encoder) {
    case EncoderKind::Render:
        [impl->render popDebugGroup];
        break;
    case EncoderKind::Compute:
        [impl->compute popDebugGroup];
        break;
    case EncoderKind::Blit:
        [impl->blit popDebugGroup];
        break;
    case EncoderKind::None:
        break;
    }
}

void Scheduler::InsertDebugSignpost(const char* text) {
    InvalidatePreparedCompute();
    if (!labels_enabled) {
        return;
    }
    if (holding && !hoisting) {
        commands.InsertDebugSignpost(text);
        return;
    }
    JoinReplay();
    NSString* label = @(text);
    switch (open_encoder) {
    case EncoderKind::Render:
        [impl->render insertDebugSignpost:label];
        break;
    case EncoderKind::Compute:
        [impl->compute insertDebugSignpost:label];
        break;
    case EncoderKind::Blit:
        [impl->blit insertDebugSignpost:label];
        break;
    case EncoderKind::None:
        if (impl->cmdbuf) {
            [impl->cmdbuf pushDebugGroup:label];
            [impl->cmdbuf popDebugGroup];
        }
        break;
    }
}

void Scheduler::LabelEncoder(const char* text) {
    InvalidatePreparedCompute();
    if (!pass_labels) {
        return;
    }
    if (holding && !hoisting) {
        held_label = text;
        return;
    }
    JoinReplay();
    if (holding) {
        if (impl->compute) {
            impl->compute.label = @(text); // the hoisted encoder
            impl->profile->Label(text);
        }
        return;
    }
    impl->profile->Label(text);
    VideoCore::FrameCapture::NativeLabel(text,(uintptr_t)this);
    NSString* label = @(text);
    switch (open_encoder) {
    case EncoderKind::Render:
        impl->render.label = label;
        break;
    case EncoderKind::Compute:
        impl->compute.label = label;
        break;
    case EncoderKind::Blit:
        impl->blit.label = label;
        break;
    case EncoderKind::None:
        break;
    }
}

namespace {
/// Raises an atomic tick to `value` (never lowers it).
void AdvanceTo(std::atomic<u64>& counter, u64 value) {
    u64 current = counter.load(std::memory_order_acquire);
    while (current < value && !counter.compare_exchange_weak(current, value)) {
    }
}
} // namespace

void Scheduler::SetSubmitCallback(VideoCore::SubmitFunc&& callback) {
    InvalidatePreparedCompute();
    on_submit = std::move(callback);
}

void Scheduler::WaitSubmitted(u64 tick) {
    BbAudit::Scope detailed_audit{BbAudit::MetalSubmitWait,tick,2000000};
    u64 current = CurrentTick();
    while (current <= tick) {
        current_tick.wait(current, std::memory_order_acquire);
        current = CurrentTick();
    }
}

void Scheduler::ReportStuck(const char* where) {
    LOG_WARNING(Render, "Metal scheduler '{}' ({}): open tick {}, GPU known tick {}, retired {}, {} "
                        "command buffers in flight, {} deferred operations",
                name, where, CurrentTick(), known_tick.load(), retired_tick.load(),
                handlers_in_flight.load(), num_pending_ops.load());
}

void Scheduler::Flush() {
    InvalidatePreparedCompute();
    JoinReplay();
    if (on_submit && !in_submit_callback) {
        // The rasterizer flushes its barriers and hands the queue operations that go out with the
        // submission (none on Metal, but the buffer cache's arena binds ride here on Vulkan).
        in_submit_callback = true;
        VideoCore::SubmitInfo info;
        on_submit(info);
        for (const auto& operation : info.before_submit) {
            operation();
        }
        in_submit_callback = false;
    }
    EndEncoder();
    const u64 tick = CurrentTick();
    if (!impl->cmdbuf && retired_tick.load(std::memory_order_acquire) >= tick - 1) {
        // Nothing recorded and every earlier command buffer retired: the tick is done as soon as
        // it is signaled, no empty command buffer needed.
        impl->event.signaledValue = tick;
        AdvanceTo(known_tick, tick);
        AdvanceTo(retired_tick, tick);
        BbFlushReason::Skipped();
    } else @autoreleasepool {
        BbFlushReason::Commit();
        if (!impl->cmdbuf) {
            BbFlushReason::SignalOnly(); // nothing encoded: the command buffer only signals
        }
        id<MTLCommandBuffer> cmdbuf = impl->EnsureCommandBuffer();
        [cmdbuf encodeSignalEvent:impl->event value:tick];
        NSString* label = cmdbuf.label;
        const unsigned timing_frame=BbStats::frame_number.load(std::memory_order_relaxed);
        const unsigned timing_index=BbSubmitTiming::Begin(timing_frame);
        const double timing_commit=timing_index<BbSubmitTiming::Capacity ? CACurrentMediaTime() : 0;
        const auto timing_queue=reinterpret_cast<std::uintptr_t>(this);
        handlers_in_flight.fetch_add(1, std::memory_order_acq_rel);
        [cmdbuf addCompletedHandler:^(id<MTLCommandBuffer> completed) {
            if (completed.status == MTLCommandBufferStatusError) {
                ReportFailure(tick, label.UTF8String, DescribeError(completed.error));
            }
            BbStats::NoteGpuInterval(completed.GPUStartTime, completed.GPUEndTime);
            BbSubmitTiming::Complete(timing_index,{timing_queue,tick,timing_frame,0,timing_commit,
                                                  completed.GPUStartTime,completed.GPUEndTime,
                                                  timing_index<BbSubmitTiming::Capacity ? CACurrentMediaTime() : 0});
            AdvanceTo(retired_tick, tick);
            handlers_in_flight.fetch_sub(1, std::memory_order_acq_rel);
        }];
        impl->profile->Detach();
        const auto commit_start = impl->profile->Enabled() ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};
        [cmdbuf commit];
        if (impl->profile->Enabled()) impl->profile->CpuCommit(std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-commit_start).count());
        impl->cmdbuf = nil;
    }
    current_tick.store(tick + 1, std::memory_order_release);
    current_tick.notify_all(); // WaitSubmitted
    RefreshTicks();
    PopPendingOperations();
}

void Scheduler::Finish() {
    BbFlushReason::Scope reason{BbFlushReason::Reason::SchedulerFinish, true};
    Flush();
    Wait(CurrentTick() - 1);
}

void Scheduler::RefreshTicks() {
    AdvanceTo(known_tick, impl->event.signaledValue);
}

bool Scheduler::IsFree(u64 tick) noexcept {
    if (IsFreeKnown(tick)) {
        return true;
    }
    RefreshTicks();
    return IsFreeKnown(tick);
}

bool Scheduler::WaitTick(u64 tick, u64 timeout_ns) {
    if (IsFreeKnown(tick)) {
        return true;
    }
    if (HasFailed()) {
        return false;
    }
    const u64 timeout_ms = (timeout_ns + 999999) / 1000000;
    [impl->event waitUntilSignaledValue:tick timeoutMS:timeout_ms];
    return IsFree(tick);
}

void Scheduler::Wait(u64 tick) {
    BbAudit::Scope detailed_audit{BbAudit::MetalGPUWait,tick,2000000};
    if (tick >= CurrentTick()) {
        BbFlushReason::Scope reason{BbFlushReason::Reason::SchedulerWait, true};
        Flush(); // the open tick: it only completes once submitted
    }
    const auto start = std::chrono::steady_clock::now();
    bool warned = false;
    while (!WaitTick(tick, WaitSliceMs * 1000000)) {
        if (HasFailed()) {
            return; // reported; the signal of a failed command buffer may never come
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                                 std::chrono::steady_clock::now() - start)
                                 .count();
        if (!warned && static_cast<u64>(elapsed) >= StuckWarningMs) {
            warned = true;
            LOG_WARNING(Render, "Metal scheduler '{}': tick {} not signaled after {} ms", name,
                        tick, elapsed);
        }
    }
    BbStats::rec_gpu_wait_ns.fetch_add(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start)
            .count(),
        std::memory_order_relaxed);
}

void Scheduler::DeferOperation(Common::UniqueFunction<void>&& func) {
    std::scoped_lock lock{pending_ops_mutex};
    pending_ops.push(PendingOp{std::move(func), CurrentTick()});
    num_pending_ops.fetch_add(1, std::memory_order_release);
}

void Scheduler::DeferPriorityOperation(Common::UniqueFunction<void>&& func) {
    {
        std::scoped_lock lock{priority_mutex};
        priority_pending_ops.push(PendingOp{std::move(func), CurrentTick()});
        if (!priority_thread.joinable()) {
            priority_thread = std::jthread(std::bind_front(&Scheduler::PriorityPendingOpsThread, this));
        }
    }
    priority_cv.notify_one();
}

void Scheduler::PopPendingOperations() {
    if (num_pending_ops.load(std::memory_order_acquire) == 0) {
        return; // every draw comes here
    }
    std::vector<PendingOp> ready;
    {
        std::scoped_lock lock{pending_ops_mutex};
        if (pending_ops.empty()) {
            return;
        }
        // Retired, not just signalled: a deferred destroy releases objects the command buffer
        // may still reference until Metal retires it (the completed handler advances this).
        const u64 retired = retired_tick.load(std::memory_order_acquire);
        while (!pending_ops.empty() && pending_ops.front().gpu_tick <= retired) {
            ready.push_back(std::move(pending_ops.front()));
            pending_ops.pop();
        }
        num_pending_ops.fetch_sub(static_cast<u32>(ready.size()), std::memory_order_release);
    }
    // Outside the lock: an operation may defer another.
    for (PendingOp& op : ready) {
        op.callback();
    }
}

void Scheduler::PriorityPendingOpsThread(std::stop_token stoken) {
    Common::SetCurrentThreadName("bbport:MtlSchedPriorityOps");

    while (!stoken.stop_requested()) {
        PendingOp op;
        {
            std::unique_lock lock{priority_mutex};
            priority_cv.wait(lock, stoken, [this] { return !priority_pending_ops.empty(); });
            if (stoken.stop_requested()) {
                break;
            }
            op = std::move(priority_pending_ops.front());
            priority_pending_ops.pop();
        }
        // Bounded waits, so a stop request is seen (the tick may never be flushed).
        while (!WaitTick(op.gpu_tick, WaitSliceMs * 1000000)) {
            if (stoken.stop_requested() || HasFailed()) {
                break;
            }
        }
        if (stoken.stop_requested() || HasFailed()) {
            // The destructor runs what is left (the operation goes back for it).
            std::scoped_lock lock{priority_mutex};
            priority_pending_ops.push(std::move(op));
            break;
        }
        op.callback();
    }
}

void Scheduler::SetErrorHandler(ErrorHandler handler) {
    std::scoped_lock lock{error_mutex};
    error_handler = std::move(handler);
}

void Scheduler::ReportFailure(u64 tick, const std::string& label, const std::string& what) {
    failed.store(true, std::memory_order_release);
    const std::string report =
        "Metal command buffer '" + label + "' (tick " + std::to_string(tick) + ") failed: " + what;
    ErrorHandler handler;
    {
        std::scoped_lock lock{error_mutex};
        handler = error_handler;
    }
    if (handler) {
        handler(report);
        return;
    }
    // As a lost Vulkan device: say what, then stop.
    std::fprintf(stderr, "%s\n", report.c_str());
    ASSERT_MSG(false, "GPU lost during submit: {}", report);
}

} // namespace Metal

namespace Metal {
std::optional<Scheduler::PreparedComputeStamp> Scheduler::ArmPreparedCompute() noexcept {
    if(HasPreparedCompute() || holding || hoisting || hoisted || render_tail_pending ||
       open_encoder!=EncoderKind::Compute || !impl->compute || !impl->cmdbuf) return {};
    const auto epoch=prepared_epoch.fetch_add(1,std::memory_order_relaxed)+1;
    PreparedComputeStamp stamp{epoch,CurrentTick(),(__bridge void*)impl->compute,
        (__bridge void*)impl->cmdbuf,std::this_thread::get_id()};
    prepared_armed.store(true,std::memory_order_release);
    return stamp;
}
bool Scheduler::ConsumePreparedCompute(const PreparedComputeStamp& s) noexcept {
    if(s.thread!=std::this_thread::get_id() || s.tick!=CurrentTick() ||
       s.epoch!=prepared_epoch.load(std::memory_order_acquire) || holding || hoisting || hoisted ||
       render_tail_pending || open_encoder!=EncoderKind::Compute ||
       s.encoder!=(__bridge void*)impl->compute || s.command_buffer!=(__bridge void*)impl->cmdbuf)
        return false;
    bool expected=true;
    return prepared_armed.compare_exchange_strong(expected,false,std::memory_order_acq_rel);
}
void Scheduler::CancelPreparedCompute(const PreparedComputeStamp& s) noexcept {
    if(s.epoch==prepared_epoch.load(std::memory_order_acquire))
        prepared_armed.store(false,std::memory_order_release);
}
} // namespace Metal
