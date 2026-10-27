#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include "video_core/renderer_metal/mtl_copy_watch.h"
#include "video_core/renderer_metal/mtl_texture_probe.h"
#include "video_core/renderer_metal/mtl_texture_lineage.h"
#include "common/scope_exit.h"
#include "video_core/renderer_metal/mtl_occlusion.h"
// SPDX-License-Identifier: GPL-2.0-or-later

#import <Metal/Metal.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unordered_map>

#include "video_core/renderer_metal/mtl_motion_blur.h"
#include "bbport_settings.h"
#include "video_core/renderer_metal/mtl_velocity_mirror.h"
#include "bbport_sections.h"
#include "bbport_timeline.h"
#include "common/assert.h"
#include "common/logging/log.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/renderer_metal/mtl_camera_motion.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_geometry.h"
#include "video_core/renderer_metal/mtl_guest_memory.h"
#include "video_core/renderer_metal/mtl_image_usage_log.h"
#include "video_core/renderer_metal/mtl_index_expand.h"
#include "video_core/renderer_metal/mtl_raster_util.h"
#include "video_core/renderer_metal/mtl_rasterizer.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_scheduler.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_tessellation.h"
#include "video_core/renderer_metal/mtl_pass_continuation.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/mtl_upscaler.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/texture_cache/image_view.h"

namespace Metal {

namespace {
void NoteAttachmentContent(const RenderState& state, MemoryStats::Content kind, bool clears_only=false) {
    if(!MemoryStats::ContentEnabled())return;
    for(u32 i=0;i<std::min<u32>(state.num_color_attachments,state.color_attachments.size());++i) {
        const auto& a=state.color_attachments[i];
        if(a.image_view && (!clears_only || a.is_clear))MemoryStats::NoteContent(
            reinterpret_cast<const void*>(a.image_view),kind);
    }
    const auto& ds=state.depth_stencil_attachment;
    if(ds.image_view && (!clears_only || ds.depth_clear || ds.stencil_clear))
        MemoryStats::NoteContent(reinterpret_cast<const void*>(ds.image_view),kind);
}

/// The resource tables are indexed by the recompiler's running binding (see the header); a
/// pipeline has at most 5 stages of Shader::NUM_BUFFERS + NUM_IMAGES + NUM_SAMPLERS of them, mip
/// arrays add elements.
constexpr u32 kMaxBindings = 1024;
constexpr NSUInteger kDummyBufferSize = 64 * 1024;
/// attributeStride of a constant vertex slot (any non-zero multiple of 4: every vertex reads the
/// data at the buffer offset).
constexpr NSUInteger kConstantVertexStride = 16;

} // namespace

struct Rasterizer::Impl {
    static constexpr u32 kNumDrops = static_cast<u32>(Drop::Count);

    /// A buffer binding: plain pointers, no retains per draw (the caches keep the buffers alive
    /// until the commands that use them have completed).
    struct BufferEntry {
        void* buffer{}; ///< id<MTLBuffer>; null: the table has no entry
        u64 offset{};
        u64 size{};
    };

    Device::Impl& dev;
    Scheduler::Impl& sched; ///< compute encoder only; render commands go through `commands`
    RenderList& commands;
    RenderList* capture_commands = nullptr;
    RenderList& Commands() { return capture_commands ? *capture_commands : commands; }
    id<MTLBuffer> dummy;                 ///< zeroed, bound for a null buffer
    id<MTLSamplerState> default_sampler; ///< for a sampler the tables do not have
    std::array<BufferEntry, kMaxBindings> buffers{};
    std::array<void*, kMaxBindings> textures{}; ///< id<MTLTexture>
    std::array<void*, kMaxBindings> samplers{}; ///< id<MTLSamplerState>
    /// Highest written index + 1 (the textures and samplers are written by the bind helper).
    u32 num_buffers{}, num_textures{}, num_samplers{};

    // Draws and dispatches dropped, by reason, and bindings that had a hole.
    std::array<std::atomic<u64>, kNumDrops> drops{};
    std::array<std::atomic<bool>, kNumDrops> drop_logged{};
    std::atomic<u64> recorded_draws{}, recorded_dispatches{};
    std::atomic<u64> hoisted_tessellation{}; ///< tessellated draws whose kernels went before the pass
    std::atomic<u64> hoisted_geometry{};     ///< geometry draws whose kernels went before the pass
    /// P7.1b: the prep and copy kernels of indirect geometry draws (made on the first one).
    std::unique_ptr<Geometry::IndirectKernels> geometry_indirect;
    /// Draws and dispatches whose pipeline could not be made (PipelineCache counters say why).
    std::atomic<u64> no_pipeline_draws{};
    std::atomic<u64> null_textures{}, null_samplers{}, missing_bindings{};
    std::atomic<bool> null_texture_logged{}, overflow_logged{};
    std::atomic<bool> storage_usage_logged{}; ///< P10.1 safety net (BindTextureSet)
    std::atomic<u32> missing_logs{};
    /// BbStats draw drops: the current draw's missing bindings / null textures are counted once
    /// (reset by BeginResourceBindings).
    bool draw_missing_counted{}, draw_null_texture_counted{};
    std::atomic<s64> last_stats_ns{};
    /// Pipelines without depth/stencil formats for passes without a depth buffer (recording
    /// thread only).
    std::unordered_map<const RenderPipeline*, const RenderPipeline*> no_depth_variants;
    bool velocity_replaying = false;
    const RenderPipeline* velocity_pipeline = nullptr;
    std::unordered_map<const GraphicsPipeline*, const RenderPipeline*> velocity_variants;
    bool velocity_requested = false;
    id<MTLBuffer> copy_watch_buffer=nil;
    TextureProbe texture_probe;
    void TraceStorage(Scheduler& scheduler,const CompiledShader& shader,u64 hash,const char* phase)noexcept try {
        auto& trace=TextureLineage::Get();if(!trace.Armed())return;
        for(const auto& binding:shader.bindings) {
            if(binding.kind!=ShaderBinding::Kind::StorageTexture)continue;
            for(u32 e=0;e<binding.count;++e) {
                const u64 index=u64(binding.binding)+e;
                if(index>=num_textures)continue;
                auto texture=(__bridge id<MTLTexture>)textures[index];
                if(!TextureLineage::Candidate(texture))continue;
                if(scheduler.HoldingPass()){trace.Gap("hoisted-storage-not-snapshotted",texture);continue;}
                trace.Texture(phase,scheduler,dev.device,texture,nil,std::format(
                    "{{\"shader\":{},\"slot\":{},\"msl_index\":{}}}",hash,index,binding.msl_index+e));
            }
        }
    }catch(...){TextureLineage::Get().Gap("storage-context-unavailable",nil);}
    struct CopyWatchRecord {u64 sequence{},shader{};u32 reads{},writes{};};
    std::array<CopyWatchRecord,128> copy_watch_records{};
    size_t copy_watch_count{};
    void DrainCopyWatch(Scheduler& scheduler) {
        if(!copy_watch_count)return;
        // Opt-in diagnostics only: complete this traced window before reading
        // 64-byte counters. No resource contents or game memory are read back.
        scheduler.Finish();
        for(size_t i=0;i<copy_watch_count;++i) {
            const auto& r=copy_watch_records[i];CopyWatchData d{};
            std::memcpy(&d,static_cast<const char*>(copy_watch_buffer.contents)+i*256,sizeof(d));
            VideoCore::FrameCapture::Native(std::format("{{\"op\":\"gpu_copy_pointer_watch\",\"capture_sequence\":{},\"shader\":{},\"watch\":{},\"bytes\":{},\"flags\":{},\"read_expressions\":{},\"write_expressions\":{},\"current_hits\":{},\"guest_hits\":{},\"overflow\":{},\"mapping\":{},\"read_sites\":{},\"write_sites\":{}}}",
                r.sequence,r.shader,d.guest,d.bytes,d.flags,d.reads,d.writes,d.current_hits,d.guest_hits,d.overflow,d.mapping,r.reads,r.writes));
        }
        copy_watch_count=0;
    }
    u64 blur_copies = 0;
    bool late_velocity_logged = false;
    bool blur_fallback_logged = false;
    const bool frame_stats{std::getenv("BB_FRAME_STATS") != nullptr};
    /// BB_PASS_LABELS=1: the label of the render pass SetupDraw begins next, and passes so far.
    std::string next_pass_label;
    u64 pass_number{};

    Impl(Device& device, Scheduler& scheduler)
        : dev{device.GetImpl()}, sched{scheduler.GetImpl()}, commands{scheduler.Commands()} {
        dummy = [dev.device newBufferWithLength:kDummyBufferSize
                                        options:MTLResourceStorageModeShared];
        dummy.label = @"bbport dummy buffer";
        std::memset(dummy.contents, 0, kDummyBufferSize);
        default_sampler = [dev.device newSamplerStateWithDescriptor:[MTLSamplerDescriptor new]];
    }

    enum class StageKind { Vertex, Fragment, Compute };

    /// P6.6: per-draw bytes (push constants, buffer sizes) go into blocks of staging memory and
    /// are bound as buffers instead of setBytes (whose data is copied into the command buffer's
    /// own storage, which the kernel wires per submission). A block serves one tick: the
    /// staging pool frees it after that tick's command buffer.
    struct BytesBlock {
        VideoCore::StagingSpan span{};
        u64 used = 0;
        u64 tick = 0;
    } bytes_block;
    static constexpr u64 kBytesBlockSize = 64 * 1024;
    static constexpr u64 kBytesAlignment = 256;
    /// Copies `length` bytes into the current block; returns its buffer and offset.
    std::pair<id<MTLBuffer>, u64> StoreBytes(Runtime& runtime, const Scheduler& scheduler,
                                             const void* data, u64 length) {
        const u64 tick = scheduler.CurrentTick();
        const u64 size = (length + kBytesAlignment - 1) & ~(kBytesAlignment - 1);
        if (!bytes_block.span.buffer || bytes_block.tick != tick ||
            bytes_block.used + size > bytes_block.span.size) {
            bytes_block.span = runtime.StagingRequest(std::max(kBytesBlockSize, size),
                                                      VideoCore::MemoryType::HostUncached,
                                                      kBytesAlignment);
            bytes_block.used = 0;
            bytes_block.tick = tick;
        }
        std::memcpy(bytes_block.span.mapped + bytes_block.used, data, length);
        const u64 offset = bytes_block.span.offset + bytes_block.used;
        bytes_block.used += size;
        return {NativeBuffer(*bytes_block.span.buffer), offset};
    }
    Runtime* runtime_ptr = nullptr;
    Scheduler* scheduler_ptr = nullptr;

    /// Binds `length` bytes at buffer slot `index` of `stage` (StoreBytes, or setBytes).
    void Bytes(StageKind stage, const void* data, u64 length, u32 index);
    /// Sets the Metal slots of `shader`'s resources on the open encoder from the tables.
    void BindStage(StageKind stage, const CompiledShader& shader, const Shader::PushData& push);
};

Rasterizer::Rasterizer(Device& device_, Scheduler& scheduler_, Runtime& runtime_,
                       AmdGpu::Liverpool* liverpool_)
    : RasterizerCore{runtime_, scheduler_, liverpool_}, device{device_}, scheduler{scheduler_},
      runtime{runtime_}, impl{std::make_unique<Impl>(device_, scheduler_)} {
    impl->runtime_ptr = &runtime;
    impl->scheduler_ptr = &scheduler;
    pipeline_cache = std::make_unique<PipelineCache>(device, runtime.Limits(), liverpool,
                                                     buffer_cache.GetSparsePageShift(), std::filesystem::path{}, &runtime);
    // Before the rasterizer is bound: Liverpool enqueues buffers only once it sees it.
    draw_prep = std::make_unique<Vulkan::DrawPreparation>(*pipeline_cache);
    // Scene services (P5.4, P7.5): the camera motion, the reduced scene targets of the live
    // path and the upscaler (UI composition of a scaled session, the MetalFX temporal scaler fed
    // by the camera motion vectors, the live path's upscale of the HDR scene); no object motion:
    // the core's null object.
    camera_motion = std::make_unique<CameraMotion>(device, scheduler, runtime.GetImpl().kernels);
    metal_scene_targets =
        std::make_unique<SceneTargets>(device, scheduler, runtime, texture_cache);
    scene_adapter = std::make_unique<SceneTargetsAdapter>(*metal_scene_targets);
    metal_upscaler = std::make_unique<Upscaler>(device, scheduler, runtime, texture_cache,
                                                *camera_motion, *metal_scene_targets);
    const char* velocity = std::getenv("BB_METAL_OBJECT_MOTION");
    impl->velocity_requested = velocity && std::strcmp(velocity, "1") == 0;
    velocity_mirror = std::make_unique<VelocityMirror>(device, scheduler, *metal_scene_targets,
                                                     impl->velocity_requested);
    metal_upscaler->SetVelocityMirror(velocity_mirror.get());
    SetSceneServices(scene_adapter.get(), metal_upscaler.get(), camera_motion.get(), nullptr);
    if (!EmulatorSettings.IsNullGPU()) {
        liverpool->BindRasterizer(this);
    }
    GuestMemory::Install(device); // before the runtime maps direct memory (D6)
    memory->SetRasterizer(this);

    if (DrawPipeWanted()) {
        constant_ring = std::make_unique<VideoCore::ConstantRing>(runtime);
        draw_pipe = std::make_unique<Vulkan::DrawPipe>(
            &RunDrawPacket, static_cast<VideoCore::RasterizerCore*>(this));
    }
    // This thread joins the texture binding helper before it changes image state.
    runtime.SetImageAccessHook(&JoinBindHelper, static_cast<VideoCore::RasterizerCore*>(this));
    scheduler.SetSubmitCallback([this](VideoCore::SubmitInfo&) { runtime.FlushBarriers(); });
    pass_labels = scheduler.PassLabels();
    VideoCore::FrameCapture::SetNativeBoundaryDrain(this, [](void* context) {
        auto& rasterizer=*static_cast<Rasterizer*>(context);
        auto& scheduler=rasterizer.scheduler;
        scheduler.EndEncoder(); // joins replay and closes compute/blit as well as render
        scheduler.EndEncoder(); // a velocity render-tail callback may have encoded host work
        rasterizer.impl->DrainCopyWatch(scheduler);
        rasterizer.impl->texture_probe.Drain(scheduler);
    });
}

Rasterizer::~Rasterizer() {
    // The recording thread and the signal thread call into this class and its pipeline cache:
    // they stop, and the draw preparation workers (which hold the pipeline cache) go, first.
    ShutdownThreads();
    impl->DrainCopyWatch(scheduler);
    impl->texture_probe.Drain(scheduler);
    if(impl->copy_watch_buffer)MemoryStats::Retire((__bridge const void*)impl->copy_watch_buffer);
    VideoCore::FrameCapture::ClearNativeBoundaryDrain(this);
    draw_prep.reset();
    scheduler.SetSubmitCallback({});
    metal_upscaler->SetVelocityMirror(nullptr);
    velocity_mirror.reset();
    occlusion.reset(); // detach the replay hook and retire query buffers before Metal members die
}

std::unique_ptr<VideoCore::OcclusionApi> Rasterizer::CreateOcclusion() {
    auto queries = std::make_unique<OcclusionQueries>(device, scheduler, runtime, &buffer_cache);
    return queries;
}

// ---- RasterizerApi: pipeline selection, then the core's record paths (as Vulkan's) ----

void Rasterizer::Draw(bool is_indexed, u32 index_offset, const Vulkan::PreparedDraw* prepared) {
    BbStats::draws.fetch_add(1, std::memory_order_relaxed);
    const bool pipelined = UseDrawPipe();
    if (pipelined && !FilterDrawPasses() &&
        !BbToggle::Disabled(BbToggle::PipelinedMemoryWrites)) {
        PostDraw(nullptr, nullptr, false, 0);
        return;
    }
    if (!pipelined || !FilterDrawPasses()) {
        DrainDrawPipe(Vulkan::DrawPipe::ReasonDraw);
        scheduler.PopPendingOperations();
        if (!FilterDraw()) {
            return;
        }
    }
    const u64 explained_before = BbStats::ExplainedNullPipelines();
    const GraphicsPipeline* pipeline = pipeline_cache->GetGraphicsPipeline({}, prepared);
    const Vulkan::PreparedDraw* used_prepared = pipeline_cache->UsedPrepared();
    if (prepared) {
        draw_prep->Count(pipeline != nullptr &&
                         prepared->state.load(std::memory_order_acquire) ==
                             Vulkan::PreparedDraw::Ready &&
                         prepared->reg_checksum == liverpool->gfx_reg_checksum);
    }
    if (!pipeline) {
        if (BbStats::ExplainedNullPipelines() == explained_before) {
            BbStats::NoteDrop(BbStats::DropKind::NoPipeline);
        }
        impl->no_pipeline_draws.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (pipelined && FilterDrawPasses()) {
        PostDraw(pipeline, used_prepared, is_indexed, index_offset);
        return;
    }
    DrawRecord(pipeline, used_prepared, is_indexed, index_offset);
}

void Rasterizer::DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 stride,
                              u32 max_count, VAddr count_address, u16 vertex_sgpr_offset,
                              u16 instance_sgpr_offset) {
    const bool pipelined = UseDrawPipe() && FilterDrawPasses() &&
                           !BbToggle::Disabled(BbToggle::PipelinedIndirectDraws);
    if (!pipelined) {
        DrainDrawPipe();
        scheduler.PopPendingOperations();
        if (!FilterDraw()) {
            return;
        }
    }
    const VideoCore::DrawIndirectParams params = {
        .vertex_sgpr_offset = vertex_sgpr_offset,
        .instance_sgpr_offset = instance_sgpr_offset,
    };
    const u64 explained_before = BbStats::ExplainedNullPipelines();
    const GraphicsPipeline* pipeline = pipeline_cache->GetGraphicsPipeline(params, nullptr, true);
    if (!pipeline) {
        if (BbStats::ExplainedNullPipelines() == explained_before) {
            BbStats::NoteDrop(BbStats::DropKind::NoPipeline);
        }
        impl->no_pipeline_draws.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    const IndirectDraw indirect = {arg_address + offset, count_address, stride, max_count};
    if (pipelined) {
        PostDraw(pipeline, nullptr, is_indexed, 0, nullptr, &indirect);
        return;
    }
    DrawIndirectRecord(pipeline, is_indexed, indirect);
}

void Rasterizer::DispatchDirect() {
    BbStats::dispatches.fetch_add(1, std::memory_order_relaxed);
    const bool pipelined = UseDrawPipe() && !BbToggle::Disabled(BbToggle::PipelinedDispatch);
    if (!pipelined) {
        DrainDrawPipe();
    }
    const ComputeShaderPipeline* pipeline = pipeline_cache->GetComputePipeline();
    if (!pipeline) {
        BbStats::NoteDrop(BbStats::DropKind::NoPipeline);
        impl->no_pipeline_draws.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (pipelined) {
        PostDraw(pipeline, nullptr, false, 0, &CsRegs());
        return;
    }
    DispatchRecord(pipeline);
}

void Rasterizer::DispatchIndirect(VAddr address, u32 offset, u32 size) {
    const bool pipelined = UseDrawPipe() && !BbToggle::Disabled(BbToggle::PipelinedIndirectDraws);
    if (!pipelined) {
        DrainDrawPipe();
    }
    const ComputeShaderPipeline* pipeline = pipeline_cache->GetComputePipeline();
    if (!pipeline) {
        BbStats::NoteDrop(BbStats::DropKind::NoPipeline);
        impl->no_pipeline_draws.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (pipelined) {
        const IndirectDraw indirect = {address + offset, 0, size, 1};
        PostDraw(pipeline, nullptr, false, 0, &CsRegs(), &indirect);
        return;
    }
    DispatchIndirectRecord(pipeline, address + offset, size);
}

u64 Rasterizer::Flush() {
    BB_SECTION(Flush);
    DrainDrawPipe();
    PublishSignals(); // the recording thread's (it is idle here, or this is it)
    last_flush_ns.store(std::chrono::steady_clock::now().time_since_epoch().count(),
                        std::memory_order_relaxed);
    const u64 current_tick = scheduler.CurrentTick();
    scheduler.Flush();
    BbTimeline::Note(BbTimeline::VulkanSubmit, current_tick,
                     Vulkan::DrawPipe::OnStageB() ? 1 : 0);
    return current_tick;
}

// ---- Markers (BB_MTL_LABELS=1 in the scheduler; guest/host marker settings as on Vulkan) ----

static bool MarkersWanted(bool from_guest) {
    return from_guest ? EmulatorSettings.IsVkGuestMarkersEnabled()
                      : EmulatorSettings.IsVkHostMarkersEnabled();
}

void Rasterizer::ScopeMarkerBegin(const std::string_view& str, bool from_guest) {
    DrainDrawPipe();
    if (MarkersWanted(from_guest)) {
        scheduler.PushDebugGroup(std::string{str}.c_str());
    }
}

void Rasterizer::ScopeMarkerEnd(bool from_guest) {
    DrainDrawPipe();
    if (MarkersWanted(from_guest)) {
        scheduler.PopDebugGroup();
    }
}

void Rasterizer::ScopedMarkerInsert(const std::string_view& str, bool from_guest) {
    DrainDrawPipe();
    if (MarkersWanted(from_guest)) {
        scheduler.InsertDebugSignpost(std::string{str}.c_str());
    }
}

void Rasterizer::ScopedMarkerInsertColor(const std::string_view& str, const u32 color,
                                         bool from_guest) {
    ScopedMarkerInsert(str, from_guest);
}

// ---- Resources: the tables of the Bind*Set hooks, and their Metal slots at a draw ----

namespace {

using StageKind = Rasterizer::Impl::StageKind;

constexpr RenderList::Stage ListStage(StageKind stage) {
    return stage == StageKind::Vertex ? RenderList::Stage::Vertex : RenderList::Stage::Fragment;
}

void SetBytes(Scheduler::Impl& sched, RenderList& list, StageKind stage, const void* bytes,
              NSUInteger length, NSUInteger index) {
    if (stage == StageKind::Compute) {
        [sched.compute setBytes:bytes length:length atIndex:index];
    } else {
        list.SetBytes(ListStage(stage), bytes, static_cast<u32>(length), static_cast<u32>(index));
    }
}

void SetBuffer(Scheduler::Impl& sched, RenderList& list, StageKind stage, id<MTLBuffer> buffer,
               NSUInteger offset, NSUInteger index, u64 known_size=0) {
    if(stage==StageKind::Fragment && list.FragmentGuardEnabled()) {
        if(!buffer || buffer.heap || offset>=buffer.length || known_size>buffer.length-offset)
            list.FragmentUnknown(FragmentAccessGuard::InvalidRange);
        else list.FragmentBuffer((__bridge void*)buffer,offset,known_size ? known_size : buffer.length-offset);
    }
    if (stage == StageKind::Compute) {
        [sched.compute setBuffer:buffer offset:offset atIndex:index];
    } else {
        list.SetBuffer(ListStage(stage), (__bridge void*)buffer, offset,
                       static_cast<u32>(index));
    }
}

void SetTexture(Scheduler::Impl& sched, RenderList& list, StageKind stage, id<MTLTexture> texture,
                NSUInteger index) {
    if(stage==StageKind::Fragment && list.FragmentGuardEnabled()) {
        unsigned depth=0;
        for(id<MTLTexture> t=texture;t;t=t.parentTexture) {
            if(++depth>8 || t.heap){list.FragmentUnknown(FragmentAccessGuard::UnknownTexture);break;}
            if(t.buffer){list.FragmentBuffer((__bridge void*)t.buffer,0,t.buffer.length);break;}
        }
        if(!texture)list.FragmentUnknown(FragmentAccessGuard::UnknownTexture);
    }
    if (stage == StageKind::Compute) {
        [sched.compute setTexture:texture atIndex:index];
    } else {
        list.SetTexture(ListStage(stage), (__bridge void*)texture, static_cast<u32>(index));
    }
}

void SetSampler(Scheduler::Impl& sched, RenderList& list, StageKind stage,
                id<MTLSamplerState> sampler, NSUInteger index) {
    if (stage == StageKind::Compute) {
        [sched.compute setSamplerState:sampler atIndex:index];
    } else {
        list.SetSampler(ListStage(stage), (__bridge void*)sampler, static_cast<u32>(index));
    }
}

} // namespace

void Rasterizer::Impl::Bytes(StageKind stage, const void* data, u64 length, u32 index) {
    if (BbToggle::Experiment(BbToggle::MtlNoBytesRing) || !runtime_ptr) {
        SetBytes(sched, Commands(), stage, data, length, index);
        return;
    }
    const auto [buffer, offset] = StoreBytes(*runtime_ptr, *scheduler_ptr, data, length);
    SetBuffer(sched, Commands(), stage, buffer, offset, index,length);
}

void Rasterizer::Impl::BindStage(StageKind stage, const CompiledShader& shader,
                                 const Shader::PushData& push) {
    if(stage==StageKind::Fragment && (shader.uses_device_address || shader.variant.kind!=CompileVariant::Kind::Plain))
        Commands().FragmentUnknown(FragmentAccessGuard::RawAddress);
    if(stage!=StageKind::Compute && shader.uses_device_address)scheduler_ptr->RequireGuestRender();
    std::array<u32, kMaxBuffers> buffer_sizes{}; // SPIRV-Cross' spvBufferSizeConstants
    const auto missing = [&](const char* what, const ShaderBinding& b) {
        missing_bindings.fetch_add(1, std::memory_order_relaxed);
        if (!draw_missing_counted) {
            draw_missing_counted = true;
            BbStats::NoteDrop(BbStats::DropKind::MissingBinding);
        }
        // Expected never: a shader declares a binding only where the draw's pipeline numbered it
        // (a permutation is reused across start bindings only when its resource bitset is empty
        // and it declares nothing at a binding, see StageSpecialization::operator==; the
        // RectList vertex shaders' ClipPlanes buffer is declared only with user clip planes on).
        // The first few are logged with their stage; a null resource is bound.
        if (missing_logs.fetch_add(1, std::memory_order_relaxed) < 8) {
            LOG_WARNING(Render,
                        "Metal: the {} binding {} (msl index {}) of a {} shader has no resource in "
                        "the draw's tables (a permutation numbered for another pipeline); a null "
                        "resource is bound",
                        what, b.binding, b.msl_index,
                        stage == StageKind::Vertex     ? "vertex"
                        : stage == StageKind::Fragment ? "fragment"
                                                       : "compute");
        }
    };
    for (const ShaderBinding& b : shader.bindings) {
        switch (b.kind) {
        case ShaderBinding::Kind::PushConstants:
            if (shader.uses_push_constants) {
                Bytes(stage, &push, sizeof(Shader::PushData), kPushConstantBuffer);
            }
            break;
        case ShaderBinding::Kind::Buffer:
            for (u32 e = 0; e < b.count; ++e) {
                const u32 index = b.binding + e;
                const BufferEntry* entry =
                    index < num_buffers && buffers[index].buffer ? &buffers[index] : nullptr;
                if (!entry) {
                    missing("buffer", b);
                }
                SetBuffer(sched, Commands(), stage,
                          entry ? (__bridge id<MTLBuffer>)entry->buffer : dummy,
                          entry ? entry->offset : 0, b.msl_index + e,entry ? entry->size : 0);
                if (b.msl_index + e < buffer_sizes.size()) {
                    buffer_sizes[b.msl_index + e] =
                        entry ? static_cast<u32>(std::min<u64>(entry->size, ~0u)) : 0;
                }
            }
            break;
        case ShaderBinding::Kind::Texture:
        case ShaderBinding::Kind::StorageTexture:
        case ShaderBinding::Kind::TexelBuffer:
            for (u32 e = 0; e < b.count; ++e) {
                const u32 index = b.binding + e;
                if (index >= num_textures) {
                    missing("texture", b);
                }
                SetTexture(sched, Commands(), stage,
                           index < num_textures ? (__bridge id<MTLTexture>)textures[index] : nil,
                           b.msl_index + e);
            }
            break;
        case ShaderBinding::Kind::Sampler:
            for (u32 e = 0; e < b.count; ++e) {
                const u32 index = b.binding + e;
                void* sampler = index < num_samplers ? samplers[index] : nullptr;
                if (!sampler) {
                    missing("sampler", b);
                    null_samplers.fetch_add(1, std::memory_order_relaxed);
                }
                SetSampler(sched, Commands(), stage,
                           sampler ? (__bridge id<MTLSamplerState>)sampler : default_sampler,
                           b.msl_index + e);
            }
            break;
        }
    }
    if (shader.buffer_size_buffer != ~0u) {
        Bytes(stage, buffer_sizes.data(), shader.buffer_size_buffer * sizeof(u32),
              shader.buffer_size_buffer);
    }
}

void Rasterizer::BeginResourceBindings(u32 num_descriptors) {
    // Only what the last draw wrote is cleared (the tables are large).
    std::fill_n(impl->buffers.begin(), impl->num_buffers, Impl::BufferEntry{});
    std::fill_n(impl->textures.begin(), impl->num_textures, nullptr);
    std::fill_n(impl->samplers.begin(), impl->num_samplers, nullptr);
    impl->num_buffers = impl->num_textures = impl->num_samplers = 0;
    impl->draw_missing_counted = impl->draw_null_texture_counted = false;
}

void Rasterizer::BindBufferSet(std::span<const BufferBinding> buffers,
                               Shader::Backend::Bindings& binding, u32& write_index) {
    for (const BufferBinding& bound : buffers) {
        const u32 index = binding.unified++;
        if (index >= kMaxBindings) {
            if (!impl->overflow_logged.exchange(true)) {
                LOG_ERROR(Render, "Metal: a pipeline has more than {} resources", kMaxBindings);
            }
            continue;
        }
        Impl::BufferEntry& entry = impl->buffers[index];
        if (bound.constant_ring) {
            entry = {(__bridge void*)NativeBuffer(constant_ring->GetBuffer()), bound.offset,
                     bound.size};
        } else if (!bound.buffer) {
            // A null descriptor: zeros to read, and a size of 0 for the buffer-size buffer.
            entry = {(__bridge void*)impl->dummy, 0, 0};
        } else {
            entry = {(__bridge void*)NativeBuffer(*bound.buffer), bound.offset, bound.size};
        }
        impl->num_buffers = std::max(impl->num_buffers, index + 1);
    }
    write_index += buffers.size();
}

void Rasterizer::BindTextureSet(std::span<const TextureBinding> textures,
                                Shader::Backend::Bindings& binding, u32& write_index) {
    // Also runs on the texture bind helper thread: only the texture and sampler tables are
    // written there (BindBufferSet writes the buffer table here at the same time).
    for (const TextureBinding& texture : textures) {
        const u32 index = binding.unified++;
        if (texture.array_index == 0) {
            ++write_index;
        }
        if (index >= kMaxBindings) {
            if (!impl->overflow_logged.exchange(true)) {
                LOG_ERROR(Render, "Metal: a pipeline has more than {} resources", kMaxBindings);
            }
            continue;
        }
        void* native = nullptr;
        if (texture.replacement_view) {
            native = reinterpret_cast<void*>(texture.replacement_view);
        } else if (texture.view) {
            native = texture_cache.GetImageView(texture.view).native;
        }
        if (!native) {
            impl->null_textures.fetch_add(1, std::memory_order_relaxed);
            if (!impl->draw_null_texture_counted) {
                impl->draw_null_texture_counted = true;
                BbStats::NoteDrop(BbStats::DropKind::NullTexture);
            }
            if (!impl->null_texture_logged.exchange(true)) {
                LOG_WARNING(Render, "Metal: a texture binding has no image view (a null "
                                    "descriptor binds nil); counted in BB_FRAME_STATS");
            }
        }
        if (texture.storage && native && !texture.replacement_view) {
            // P10.1 safety net: a storage binding of a texture without ShaderWrite (a view
            // inherits the usage of its base; the promotion in Image::EnsureViewUsage makes sure
            // it has it). The writes would be dropped or rejected by the driver: never silent.
            @autoreleasepool {
                id<MTLTexture> bound = (__bridge id<MTLTexture>)native;
                id<MTLTexture> root = bound.parentTexture ? bound.parentTexture : bound;
                if (!((bound.usage | root.usage) & MTLTextureUsageShaderWrite) &&
                    !impl->storage_usage_logged.exchange(true)) {
                    LOG_ERROR(Render, "Metal: a storage binding of a texture without ShaderWrite "
                                      "(\"{}\", usage {}); its shader writes are lost",
                              root.label ? [root.label UTF8String] : "?",
                              static_cast<unsigned long long>(root.usage));
                }
            }
        }
        if (ImageUsageLog::Enabled()) {
            // A storage binding is one the shader writes (BindingType::Storage: is_written).
            ImageUsageLog::Bound(native, texture.storage ? ImageUsageLog::Use::Storage
                                                         : ImageUsageLog::Use::Sampled);
        }
        if(texture.storage && native)MemoryStats::NoteContent(native,MemoryStats::Content::StorageBinding);
        impl->textures[index] = native;
        impl->num_textures = std::max(impl->num_textures, index + 1);
    }
}

void Rasterizer::BindSamplerSet(std::span<const VideoCore::SamplerRef> samplers,
                                Shader::Backend::Bindings& binding, u32& write_index) {
    for (const VideoCore::SamplerRef& sampler : samplers) {
        const u32 index = binding.unified++;
        if (index >= kMaxBindings) {
            continue; // reported by the buffer or texture binds
        }
        impl->samplers[index] = sampler.native;
        impl->num_samplers = std::max(impl->num_samplers, index + 1);
    }
    write_index += samplers.size();
}

// ---- Dropped work ----

namespace {

constexpr const char* kDropNames[] = {
    "none",
    "primitive type needs emulation (QuadList, fans, adjacency, patches: P4.3d)",
    "polygon mode point",
    "rasterizer discard",
    "front and back culling of triangles",
    "empty scissor",
    "uint8 indices (P4.3d)",
    "index buffer offset not a multiple of the index size",
    "vertex stride not a multiple of 4",
    "indirect draw with a count buffer (P4.3d)",
    "no instances or no indices",
    "indexed draw without an index buffer",
    "no vertex buffers",
    "dispatch of no groups",
    "indirect arguments (offset or stride) not a multiple of 4",
    "pipeline without a usable shader",
    "indexed or indirect RectList draw (P4.3d)",
    "RectList draw whose first vertex is not a multiple of 3",
    "index expansion: the index buffer has no CPU view",
    "indirect draw of an expanded topology (QuadList, fans, uint8 indices)",
    "tessellated draw of more than one instance",
    "indirect tessellated draw",
    "geometry draw of more than one instance",
    "geometry draw needing more scratch memory than the limit",
};
} // namespace

void Rasterizer::NoteDrop(Drop reason) {
    static_assert(std::size(kDropNames) == Impl::kNumDrops);
    const u32 index = static_cast<u32>(reason);
    impl->drops[index].fetch_add(1, std::memory_order_relaxed);
    // BbStats: work that is empty by construction (nothing to draw, culled, discarded) is not a
    // loss; geometry limits are counted with the geometry draws a backend skips.
    switch (reason) {
    case Drop::EmptyDraw:
    case Drop::EmptyDispatch:
    case Drop::EmptyScissor:
    case Drop::RasterizerDiscard:
    case Drop::CullAll:
        break;
    case Drop::GeometryInstanced:
    case Drop::GeometryTooLarge:
        BbStats::NoteDrop(BbStats::DropKind::GeometrySkipped);
        break;
    default:
        BbStats::NoteDrop(BbStats::DropKind::Other);
        break;
    }
    if (!impl->drop_logged[index].exchange(true)) {
        LOG_WARNING(Render, "Metal: draws or dispatches are dropped: {}", kDropNames[index]);
    }
    MaybePrintStats();
}

void Rasterizer::MaybePrintStats() {
    if (!impl->frame_stats) {
        return;
    }
    using Clock = std::chrono::steady_clock;
    const s64 now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        Clock::now().time_since_epoch())
                        .count();
    s64 last = impl->last_stats_ns.load(std::memory_order_relaxed);
    if (last == 0) {
        impl->last_stats_ns.compare_exchange_strong(last, now);
        return;
    }
    if (now - last < 5'000'000'000 || !impl->last_stats_ns.compare_exchange_strong(last, now)) {
        return;
    }
    std::printf("Metal rasterizer: %llu draws and %llu dispatches recorded (%llu dispatches and "
                "%llu tessellated and %llu geometry draws' kernels hoisted before their pass); "
                "null textures %llu, null samplers %llu, missing bindings %llu\n",
                (unsigned long long)impl->recorded_draws.load(std::memory_order_relaxed),
                (unsigned long long)impl->recorded_dispatches.load(std::memory_order_relaxed),
                (unsigned long long)BbStats::hoisted_dispatches.load(std::memory_order_relaxed),
                (unsigned long long)impl->hoisted_tessellation.load(std::memory_order_relaxed),
                (unsigned long long)impl->hoisted_geometry.load(std::memory_order_relaxed),
                (unsigned long long)impl->null_textures.load(std::memory_order_relaxed),
                (unsigned long long)impl->null_samplers.load(std::memory_order_relaxed),
                (unsigned long long)impl->missing_bindings.load(std::memory_order_relaxed));
    if (impl->geometry_indirect && impl->geometry_indirect->Ok()) {
        const auto counters = impl->geometry_indirect->Read();
        std::printf("Metal indirect geometry draws: %llu run, %llu with more than one instance "
                    "(instance 0 only), %llu truncated to the scratch capacity; %llu primitives, "
                    "at most %llu in one draw\n",
                    (unsigned long long)counters.draws, (unsigned long long)counters.instanced,
                    (unsigned long long)counters.truncated, (unsigned long long)counters.primitives,
                    (unsigned long long)counters.max_primitives);
    }
    const auto& pc = pipeline_cache->GetCounters();
    const auto& sc = pipeline_cache->GetShaderCompiler().GetCounters();
    std::printf("Metal: %llu draws/dispatches without a pipeline\n",
                (unsigned long long)impl->no_pipeline_draws.load(std::memory_order_relaxed));
    std::printf("Metal pipelines: %llu created (%llu modules, %llu unusable); not created: %llu "
                "geometry (unsupported shape), %llu stage combination, %llu tessellation "
                "(isolines, point mode), %llu shader, %llu RectList rewrite, %llu PSO; shader compiles %llu, failures %llu, PerVertex failures %llu\n",
                (unsigned long long)pc.pipelines.load(), (unsigned long long)pc.modules.load(),
                (unsigned long long)pc.unusable_modules.load(),
                (unsigned long long)pc.geometry_unsupported.load(),
                (unsigned long long)pc.unsupported_stage.load(),
                (unsigned long long)pc.tessellation_unsupported.load(),
                (unsigned long long)pc.unusable_shader.load(),
                (unsigned long long)pc.rect_list_unrewritable.load(),
                (unsigned long long)pc.pso_failures.load(), (unsigned long long)sc.compiled.load(),
                (unsigned long long)sc.compile_failures.load(),
                (unsigned long long)sc.pervertex_failures.load());
    for (u32 i = 1; i < Impl::kNumDrops; ++i) {
        if (const u64 count = impl->drops[i].load(std::memory_order_relaxed)) {
            std::printf("  dropped %llu: %s\n", (unsigned long long)count, kDropNames[i]);
        }
    }
}

// ---- Recording hooks (P4.3b) ----

RenderState Rasterizer::BuildRenderState(const RenderTargets& targets) {
    const auto attachment_of = [&](const TargetAttachment& source) {
        RenderAttachment attachment{};
        if (source.replacement_view) {
            attachment.image_view = source.replacement_view;
            attachment.image_layout = source.replacement_layout;
        } else if (source.view) {
            attachment.image_view =
                reinterpret_cast<VideoCore::NativeView>(texture_cache.GetImageView(source.view).native);
            attachment.image_layout = source.layout;
        }
        attachment.clear_value = source.clear_value;
        if (ImageUsageLog::Enabled() && attachment.image_view) {
            ImageUsageLog::Bound(reinterpret_cast<void*>(attachment.image_view),
                                 ImageUsageLog::Use::RenderTarget);
        }
        return attachment;
    };
    RenderState state;
    state.width = targets.width;
    state.height = targets.height;
    state.num_layers = targets.num_layers;
    state.num_color_attachments = targets.num_color_attachments;
    for (u32 cb = 0; cb < state.color_attachments.size(); ++cb) {
        state.color_attachments[cb] = attachment_of(targets.color_attachments[cb]);
        state.color_attachments[cb].is_clear = targets.color_attachments[cb].is_clear;
    }
    const auto& depth = targets.depth_stencil_attachment;
    auto& attachment = state.depth_stencil_attachment;
    attachment = attachment_of(depth);
    attachment.is_clear = 0;
    attachment.has_depth = depth.has_depth;
    attachment.depth_clear = depth.depth_clear;
    attachment.has_stencil = depth.has_stencil;
    attachment.stencil_clear = depth.stencil_clear;
    return state;
}

namespace {

bool IsUintPixelFormat(MTLPixelFormat format) {
    switch (format) {
    case MTLPixelFormatR8Uint:
    case MTLPixelFormatR16Uint:
    case MTLPixelFormatR32Uint:
    case MTLPixelFormatRG8Uint:
    case MTLPixelFormatRG16Uint:
    case MTLPixelFormatRG32Uint:
    case MTLPixelFormatRGBA8Uint:
    case MTLPixelFormatRGBA16Uint:
    case MTLPixelFormatRGBA32Uint:
    case MTLPixelFormatRGB10A2Uint:
        return true;
    default:
        return false;
    }
}

bool IsSintPixelFormat(MTLPixelFormat format) {
    switch (format) {
    case MTLPixelFormatR8Sint:
    case MTLPixelFormatR16Sint:
    case MTLPixelFormatR32Sint:
    case MTLPixelFormatRG8Sint:
    case MTLPixelFormatRG16Sint:
    case MTLPixelFormatRG32Sint:
    case MTLPixelFormatRGBA8Sint:
    case MTLPixelFormatRGBA16Sint:
    case MTLPixelFormatRGBA32Sint:
        return true;
    default:
        return false;
    }
}

/// VkClearColorValue's 4 words as a clear color of the texture's format: raw integers for the
/// integer formats, float bits for the others.
MTLClearColor ClearColorOf(MTLPixelFormat format, const std::array<u32, 4>& value) {
    if (IsUintPixelFormat(format)) {
        return MTLClearColorMake(value[0], value[1], value[2], value[3]);
    }
    if (IsSintPixelFormat(format)) {
        return MTLClearColorMake(std::bit_cast<s32>(value[0]), std::bit_cast<s32>(value[1]),
                                 std::bit_cast<s32>(value[2]), std::bit_cast<s32>(value[3]));
    }
    return MTLClearColorMake(std::bit_cast<float>(value[0]), std::bit_cast<float>(value[1]),
                             std::bit_cast<float>(value[2]), std::bit_cast<float>(value[3]));
}

id<MTLTexture> TextureOf(VideoCore::NativeView view) {
    return (__bridge id<MTLTexture>)reinterpret_cast<void*>(view);
}

/// The pass of `state`: loads, clears and stores of the attachments (clears are the first use of
/// a target in the pass), a layered target, or the size of a pass without attachments.
MTLRenderPassDescriptor* MakePassDescriptor(const RenderState& state, const RenderPipeline& r) {
    MTLRenderPassDescriptor* desc = [MTLRenderPassDescriptor renderPassDescriptor];
    bool any_attachment = false;
    const u32 num_colors = std::min<u32>(state.num_color_attachments, state.color_attachments.size());
    for (u32 cb = 0; cb < num_colors; ++cb) {
        const auto& attachment = state.color_attachments[cb];
        if (!attachment.image_view) {
            continue;
        }
        id<MTLTexture> texture = TextureOf(attachment.image_view);
        MTLRenderPassColorAttachmentDescriptor* color = desc.colorAttachments[cb];
        color.texture = texture;
        color.storeAction = MTLStoreActionStore;
        if (attachment.is_clear) {
            color.loadAction = MTLLoadActionClear;
            color.clearColor = ClearColorOf(texture.pixelFormat, attachment.clear_value);
        } else {
            color.loadAction = MTLLoadActionLoad;
        }
        any_attachment = true;
    }
    const auto& ds = state.depth_stencil_attachment;
    if (ds.image_view) {
        id<MTLTexture> texture = TextureOf(ds.image_view);
        const u32 format = static_cast<u32>(texture.pixelFormat);
        if (ds.has_depth && VkToMtl::IsDepthPixelFormat(format)) {
            desc.depthAttachment.texture = texture;
            desc.depthAttachment.storeAction = MTLStoreActionStore;
            if (ds.depth_clear) {
                desc.depthAttachment.loadAction = MTLLoadActionClear;
                desc.depthAttachment.clearDepth = std::bit_cast<float>(ds.clear_value[0]);
            } else {
                desc.depthAttachment.loadAction = MTLLoadActionLoad;
            }
            any_attachment = true;
        }
        if (ds.has_stencil && VkToMtl::HasStencilPixelFormat(format)) {
            desc.stencilAttachment.texture = texture;
            desc.stencilAttachment.storeAction = MTLStoreActionStore;
            if (ds.stencil_clear) {
                desc.stencilAttachment.loadAction = MTLLoadActionClear;
                desc.stencilAttachment.clearStencil = ds.clear_value[1];
            } else {
                desc.stencilAttachment.loadAction = MTLLoadActionLoad;
            }
            any_attachment = true;
        }
    }
    if (state.num_layers > 1) {
        desc.renderTargetArrayLength = state.num_layers;
    }
    if (!any_attachment) {
        desc.renderTargetWidth = state.width;
        desc.renderTargetHeight = state.height;
        desc.defaultRasterSampleCount = std::max<u32>(r.sample_count, 1);
    }
    return desc;
}

} // namespace

const RenderPipeline& Rasterizer::PipelineForPass(const GraphicsPipeline& pipeline,
                                                  const RenderState& state) {
    // The key has the depth buffer's formats whenever the registers describe a valid one, but
    // the core attaches it only when the draw tests or writes depth or stencil (Vulkan and
    // MoltenVK accept the difference; Metal requires the pipeline's formats to match the pass).
    // Such draws take a variant without the depth and stencil formats.
    const RenderPipeline& r = pipeline.Render();
    if ((!r.depth_pixel_format && !r.stencil_pixel_format) ||
        state.depth_stencil_attachment.image_view != 0) {
        return r;
    }
    auto [it, inserted] = impl->no_depth_variants.try_emplace(&r, nullptr);
    if (inserted) {
        Vulkan::GraphicsPipelineKey key = r.key;
        key.z_format = AmdGpu::DepthBuffer::ZFormat::Invalid;
        key.stencil_format = AmdGpu::DepthBuffer::StencilFormat::Invalid;
        const RenderPipelineInput input{.vertex_attributes = pipeline.VertexAttributes(),
                                        .rect_list_triangles = r.rect_list && !r.primitive_emulated,
                                        .tessellation_eval = r.tessellation,
                                        .geometry = r.geometry,
                                        .geometry_output = r.primitive_type == MTLPrimitiveTypeLineStrip
                                                               ? GsOutputPrimitive::LineStrip
                                                               : GsOutputPrimitive::TriangleStrip};
        it->second = pipeline_cache->GetPsoCache().GetRenderPipeline(key, r.vs, r.fs, input);
        if (it->second && !key.motion_vectors) {
            GraphicsSerialization serialized;
            const auto attributes = pipeline.VertexAttributes();
            serialized.vertex_attributes.assign(attributes.begin(), attributes.end());
            VideoCore::RegisterPipelineData(key, std::hash<VideoCore::GraphicsPipelineKey>{}(key), serialized);
        }
    }
    return it->second ? *it->second : r;
}

RenderState WithoutClears(const RenderState& state) {
    RenderState load = state;
    for (RenderAttachment& attachment : load.color_attachments) {
        attachment.is_clear = 0;
    }
    load.depth_stencil_attachment.depth_clear = false;
    load.depth_stencil_attachment.stencil_clear = false;
    return load;
}

namespace {
/// Whether a draw of `state` (no depth attached: it neither tests nor writes depth or stencil)
/// can go into the open pass `open`: the same colour targets and size, no clears of its own, and
/// a depth buffer of the formats the draw's pipeline was made for (the core attaches depth only
/// to draws that use it; the HUD switched between the two kinds of passes ~20 times a frame).
bool FitsOpenDepthPass(const RenderState& open, const RenderState& state, const RenderPipeline& r) {
    const auto& ds = open.depth_stencil_attachment;
    if (state.depth_stencil_attachment.image_view != 0 || ds.image_view == 0 ||
        open.width != state.width || open.height != state.height ||
        open.num_layers != state.num_layers ||
        open.num_color_attachments != state.num_color_attachments) {
        return false;
    }
    for (u32 cb = 0; cb < state.color_attachments.size(); ++cb) {
        if (state.color_attachments[cb].is_clear ||
            open.color_attachments[cb].image_view != state.color_attachments[cb].image_view) {
            return false;
        }
    }
    id<MTLTexture> depth = (__bridge id<MTLTexture>)reinterpret_cast<void*>(ds.image_view);
    const u32 format = static_cast<u32>(depth.pixelFormat);
    const bool has_depth = ds.has_depth && VkToMtl::IsDepthPixelFormat(format);
    const bool has_stencil = ds.has_stencil && VkToMtl::HasStencilPixelFormat(format);
    return r.depth_pixel_format == (has_depth ? format : 0) &&
           r.stencil_pixel_format == (has_stencil ? format : 0);
}
} // namespace

const RenderState& Rasterizer::OpenPass(const GraphicsPipeline& pipeline, const RenderState& state) {
    const auto& r=pipeline.Render();
    // Plain shader metadata comes from the SPIR-V capability scan, also on disk-cache hits.
    // Unknown/synthesized tessellation/geometry/rect-list programs remain conservative.
    const bool bound_only=r.vs && r.vs->variant.kind==CompileVariant::Kind::Plain &&
        !r.vs->uses_device_address && (!r.fs || !r.fs->uses_device_address) &&
        !r.tessellation && !r.geometry && !r.rect_list;
    if (impl->velocity_replaying) {if(!bound_only)scheduler.RequireGuestRender();return state;} // record host commands without interrupting guest
    MarkPass(&pipeline, state);
    if (const RenderState* open = scheduler.OpenRenderState();
        open && !BbToggle::Experiment(BbToggle::MtlNoDepthPassMerge) &&
        FitsOpenDepthPass(*open, state, pipeline.Render())) {
        if(!bound_only)scheduler.RequireGuestRender();
        return *open;
    }
    if (PassContinuation::DepthLayoutEnabled() && bound_only) {
        if (const auto* open = scheduler.OpenRenderState(); open &&
            PassContinuation::DepthStencilLayoutOnly(*open, state) &&
            PassContinuation::TexturesExcludeAttachments(state,
                std::span<void* const>(impl->textures.data(), impl->num_textures)) &&
            scheduler.ContinueDepthLayout(state)) return state;
    }
    if (PassContinuation::Enabled() && bound_only) {
        if (const auto* open = scheduler.OpenRenderState(); open &&
            PassContinuation::ConsumedClearBecomesLoad(*open, state) &&
            PassContinuation::TexturesExcludeAttachments(state,
                std::span<void* const>(impl->textures.data(), impl->num_textures)) &&
            scheduler.ContinueConsumedClear(state)) return state;
    }
    // A pass re-opened after a tessellation draw's kernels is the loading variant of the same
    // state (WithoutClears): the draws after it continue it instead of clearing again.
    if (!scheduler.IsRenderingWith(state) && !scheduler.IsRenderingWith(WithoutClears(state))) {
        if(VideoCore::FrameCapture::NativeRecording())if(const auto* old=scheduler.OpenRenderState()) {
            // Metadata on the recording thread: this is a requested split, not a timed
            // GPU pass or proof that merging would preserve texture feedback/queries.
            unsigned changes=0;
            if(old->width!=state.width || old->height!=state.height || old->num_layers!=state.num_layers)changes|=1;
            if(old->num_color_attachments!=state.num_color_attachments)changes|=2;
            for(unsigned slot=0;slot<state.color_attachments.size();++slot) {
                const auto& a=old->color_attachments[slot];const auto& b=state.color_attachments[slot];
                if(a.image_view!=b.image_view)changes|=4;
                if(a.image_layout!=b.image_layout)changes|=64;
                if(a.is_clear!=b.is_clear)changes|=32;
                if(a.clear_value!=b.clear_value)changes|=128;
            }
            const auto& a=old->depth_stencil_attachment;const auto& b=state.depth_stencil_attachment;
            if(a.image_view!=b.image_view)changes|=8;
            if(a.has_depth!=b.has_depth || a.has_stencil!=b.has_stencil)changes|=16;
            if(a.depth_clear!=b.depth_clear || a.stencil_clear!=b.stencil_clear)changes|=32;
            if(a.image_layout!=b.image_layout)changes|=64;
            if(a.clear_value!=b.clear_value)changes|=128;
            static std::atomic<u64> split_sequence{};
            const u64 observation=++split_sequence;
            VideoCore::FrameCapture::Native(std::format(
                "{{\"op\":\"render_pass_state_split\",\"queue\":{},\"observation\":{},\"changes\":{},\"old_to_load_matches\":{},\"old_colors\":{},\"new_colors\":{},\"width\":{},\"height\":{},\"layers\":{},\"samples\":{}}}",
                uintptr_t(&scheduler),observation,changes,WithoutClears(*old)==state,old->num_color_attachments,
                state.num_color_attachments,state.width,state.height,state.num_layers,r.sample_count));
            if (changes == 64 && PassContinuation::DepthStencilLayoutOnly(*old, state)) {
                const bool excludes = PassContinuation::TexturesExcludeAttachments(state,
                    std::span<void* const>(impl->textures.data(), impl->num_textures));
                VideoCore::FrameCapture::Native(std::format(
                    "{{\"op\":\"depth_layout_candidate\",\"queue\":{},\"observation\":{},\"old_layout\":{},\"new_layout\":{},\"bound_only\":{},\"current_textures_exclude\":{},\"blockers\":{}}}",
                    uintptr_t(&scheduler), observation, unsigned(old->depth_stencil_attachment.image_layout),
                    unsigned(state.depth_stencil_attachment.image_layout), bound_only, excludes,
                    scheduler.ContinuationBlockers()));
            }
            if(!(changes&15)) {
                const auto slot_state=[&](int slot,const RenderAttachment& a,const RenderAttachment& b) {
                    if(a.image_layout==b.image_layout && a.is_clear==b.is_clear && a.clear_value==b.clear_value)return;
                    VideoCore::FrameCapture::Native(std::format(
                        "{{\"op\":\"render_pass_slot_state\",\"queue\":{},\"observation\":{},\"slot\":{},\"view\":{},\"old_layout\":{},\"new_layout\":{},\"old_flags\":{},\"new_flags\":{},\"old_value\":[{},{},{},{}],\"new_value\":[{},{},{},{}]}}",
                        uintptr_t(&scheduler),observation,slot,a.image_view,unsigned(a.image_layout),unsigned(b.image_layout),
                        a.is_clear,b.is_clear,a.clear_value[0],a.clear_value[1],a.clear_value[2],a.clear_value[3],
                        b.clear_value[0],b.clear_value[1],b.clear_value[2],b.clear_value[3]));
                };
                for(unsigned slot=0;slot<state.color_attachments.size();++slot)
                    slot_state(int(slot),old->color_attachments[slot],state.color_attachments[slot]);
                slot_state(-1,old->depth_stencil_attachment,state.depth_stencil_attachment);
            }
        }
        MTLRenderPassDescriptor* desc = MakePassDescriptor(state, pipeline.Render());
        NoteAttachmentContent(state,MemoryStats::Content::Clear,true);
        scheduler.RenderEncoder((__bridge const void*)desc, state, !impl->velocity_replaying, bound_only);
        if (scheduler.PassLabels()) {
            scheduler.LabelEncoder(impl->next_pass_label.c_str());
        }
    }
    if(!bound_only)scheduler.RequireGuestRender();
    return state;
}

bool Rasterizer::SetupDraw(const GraphicsPipeline& pipeline, const RenderState& state,
                           const DynamicDrawState& dynamic, bool is_indexed, Drop forced) {
    const RenderPipeline& r = pipeline.Render();
    // The pass is begun for dropped draws as well: its clears are the first thing it does.
    const RenderState& pass_state = OpenPass(pipeline, state);

    // ---- What Metal cannot draw (yet) ----
    const bool triangles = r.primitive_type == MTLPrimitiveTypeTriangle ||
                           r.primitive_type == MTLPrimitiveTypeTriangleStrip;
    const VkToMtl::CullInfo cull = VkToMtl::CullMode(dynamic.primitive.cull_mode);
    const auto& viewport_scissors = dynamic.viewport_scissors;
    std::array<RenderList::Scissor, AmdGpu::NUM_VIEWPORTS> scissors;
    const u32 num_viewports =
        std::max<u32>(1, std::min<u32>(viewport_scissors.viewports.size(), scissors.size()));
    bool scissor_empty = false;
    for (u32 i = 0; i < num_viewports; ++i) {
        RasterUtil::ScissorRect rect{0, 0, state.width, state.height};
        bool nonempty = true;
        if (i < viewport_scissors.scissors.size()) {
            const vk::Rect2D& scissor = viewport_scissors.scissors[i];
            nonempty = RasterUtil::ClampScissor(scissor.offset.x, scissor.offset.y,
                                                scissor.extent.width, scissor.extent.height,
                                                state.width, state.height, rect);
        }
        scissors[i] = RenderList::Scissor{rect.x, rect.y, rect.width, rect.height};
        scissor_empty |= !nonempty && i == 0; // viewport arrays are not used by the game
    }
    const VkToMtl::IndexInfo index_info = VkToMtl::IndexType(index_bind.type);
    // Topologies Metal lacks and uint8 indices are expanded into 32/16-bit indices (RecordDraw):
    // the guest's index buffer is read there, not bound.
    const bool expanded = (r.primitive_emulated && IndexExpand::FromGuest(r.key.prim_type)) ||
                          (is_indexed && index_info.needs_conversion);
    Drop drop = forced;
    if (drop == Drop::None) {
        if (r.primitive_emulated && !expanded) {
            drop = Drop::PrimitiveEmulated;
        } else if (r.fill_point) {
            drop = Drop::FillPoint;
        } else if (dynamic.primitive.rasterizer_discard) {
            drop = Drop::RasterizerDiscard;
        } else if (cull.cull_all && triangles && !r.rect_list) {
            drop = Drop::CullAll;
        } else if (scissor_empty) {
            drop = Drop::EmptyScissor;
        } else if (is_indexed && !index_bind.buffer) {
            drop = Drop::NoIndexBuffer;
        } else if (is_indexed && !expanded &&
                   index_bind.offset % (index_info.type == MTLIndexTypeUInt16 ? 2 : 4) != 0) {
            drop = Drop::IndexOffsetAlignment;
        }
    }

    // Vertex slots: the pipeline's attribute (by location) names the guest stream.
    struct VertexSlot {
        id<MTLBuffer> buffer;
        NSUInteger offset, stride;
        NSUInteger index;
    };
    std::array<VertexSlot, Vulkan::MaxVertexBufferCount> vertex_slots;
    const u32 num_slots = std::min<u32>(r.num_vertex_slots, vertex_slots.size());
    for (u32 j = 0; j < num_slots && drop == Drop::None; ++j) {
        const VertexBufferSlot& slot = r.vertex_slots[j];
        VertexSlot out{impl->dummy, 0, kConstantVertexStride, slot.slot};
        for (const auto& attribute : vertex_binds.attributes) {
            if (attribute.location != slot.location) {
                continue;
            }
            const u32 b = attribute.binding;
            if (b < vertex_binds.num_buffers && b < vertex_binds.host_buffers.size() &&
                vertex_binds.host_buffers[b]) {
                out.buffer = NativeBuffer(*vertex_binds.host_buffers[b]);
                out.offset = vertex_binds.host_offsets[b];
                out.stride = vertex_binds.host_strides[b];
            }
            break;
        }
        if (slot.constant) {
            out.stride = kConstantVertexStride;
        } else if (out.stride % 4 != 0) {
            drop = Drop::VertexStrideAlignment;
        }
        vertex_slots[j] = out;
    }
    if (drop != Drop::None) {
        NoteDrop(drop);
        return false;
    }

    // ---- Encoder state ----
    RenderList& list = impl->Commands();
    list.SetRenderPipelineState(impl->velocity_pipeline ? impl->velocity_pipeline->state
                                                     : PipelineForPass(pipeline, pass_state).state);

    const DepthStencilState& ds = dynamic.depth_stencil;
    const auto face = [](const VideoCore::StencilOps& ops, u32 compare_mask, u32 write_mask) {
        return DepthStencilKey::Face{ops.fail_op,      ops.pass_op,
                                     ops.depth_fail_op, ops.compare_op,
                                     u8(compare_mask & 0xff), u8(write_mask & 0xff)};
    };
    DepthStencilKey ds_key;
    ds_key.depth_test = ds.depth_test_enabled;
    ds_key.depth_write = ds.depth_write_enabled;
    ds_key.depth_compare = ds.depth_compare_op;
    ds_key.stencil_test = ds.stencil_test_enabled;
    if (ds.stencil_test_enabled) {
        ds_key.front = face(ds.front_ops, ds.front_compare_mask, ds.front_write_mask);
        ds_key.back = face(ds.back_ops, ds.back_compare_mask, ds.back_write_mask);
    }
    if (void* ds_state = pipeline_cache->GetPsoCache().GetDepthStencilState(ds_key)) {
        list.SetDepthStencilState(ds_state);
    }
    if (ds.stencil_test_enabled) {
        list.SetStencilReference(ds.front_reference & 0xff, ds.back_reference & 0xff);
    }
    // The depth bounds test does not exist in Metal.
    if (ds.depth_bias_enabled) {
        list.SetDepthBias(ds.depth_bias_constant, ds.depth_bias_slope, ds.depth_bias_clamp);
    } else {
        list.SetDepthBias(0, 0, 0);
    }

    // Primitive restart is always on in Metal for indexed draws (the 0xffff / 0xffffffff index):
    // the guest's restart enable needs nothing here.
    // (RectLists are never culled: the core already passes cull mode none for them, and the
    // winding of the two triangles the rewritten vertex shader emits does not matter.)
    list.SetCullMode(r.rect_list ? static_cast<u32>(MTLCullModeNone) : cull.mode);
    list.SetFrontFacingWinding(RasterUtil::FrontWinding(dynamic.primitive.front_face));
    list.SetTriangleFillMode(r.fill_mode);
    list.SetDepthClipMode(r.depth_clip ? static_cast<u32>(MTLDepthClipModeClip)
                                       : static_cast<u32>(MTLDepthClipModeClamp));
    const auto& blend = Regs().blend_constants;
    list.SetBlendColor(blend[0], blend[1], blend[2], blend[3]);

    std::array<RenderList::Viewport, AmdGpu::NUM_VIEWPORTS> viewports;
    for (u32 i = 0; i < num_viewports; ++i) {
        if (i < viewport_scissors.viewports.size()) {
            const vk::Viewport& v = viewport_scissors.viewports[i];
            const auto m = RasterUtil::ToMetalViewport(v.x, v.y, v.width, v.height, v.minDepth,
                                                       v.maxDepth);
            viewports[i] = {m.origin_x, m.origin_y, m.width, m.height, m.znear, m.zfar};
        } else {
            viewports[i] = {0, 0, double(state.width), double(state.height), 0, 1};
        }
    }
    list.SetViewports(viewports.data(), num_viewports);
    list.SetScissorRects(scissors.data(), num_viewports);

    for (u32 j = 0; j < num_slots; ++j) {
        const VertexSlot& slot = vertex_slots[j];
        list.SetVertexBufferWithStride((__bridge void*)slot.buffer, slot.offset, slot.stride,
                                       static_cast<u32>(slot.index));
    }

    impl->BindStage(Impl::StageKind::Vertex, *r.vs, push_data);
    if (r.fs) {
        impl->BindStage(Impl::StageKind::Fragment, *r.fs, push_data);
    }
    list.FragmentReady(); // every actual draw consumes this reflected-binding classification
    return true;
}

void Rasterizer::RecordTessellationDraw(const GraphicsPipeline& graphics, const RenderState& state,
                                        const DrawCall& draw, const DynamicDrawState& dynamic) {
    const TessellationPipeline& tess = *graphics.Tessellation();
    const u32 patches = draw.num_indices / tess.input_control_points;
    Drop forced = Drop::None;
    if (draw.num_instances == 0 || patches == 0) {
        forced = Drop::EmptyDraw;
    } else if (draw.num_instances > 1) {
        forced = Drop::TessellationInstanced;
    }

    // The indices: 16 and 32 bit ones are read from the guest's buffer as they are, 8 bit ones
    // are widened into staging memory (the stage input fetches by index, never by restart).
    Tessellation::DrawParams params;
    params.num_vertices = patches * tess.input_control_points;
    params.first_vertex = draw.first_vertex;
    params.first_instance = draw.first_instance;
    VideoCore::StagingSpan widened;
    if (draw.is_indexed && forced == Drop::None) {
        const VkToMtl::IndexInfo index_info = VkToMtl::IndexType(index_bind.type);
        if (!index_bind.buffer) {
            forced = Drop::NoIndexBuffer;
        } else if (index_info.needs_conversion) {
            const u8* data = static_cast<const u8*>(NativeBuffer(*index_bind.buffer).contents);
            if (!data) {
                forced = Drop::IndexDataUnreadable;
            } else {
                widened = runtime.StagingRequest(u64(params.num_vertices) * 2,
                                                 VideoCore::MemoryType::HostUncached, 4);
                IndexExpand::WidenU8(data + index_bind.offset, params.num_vertices, false,
                                     reinterpret_cast<u16*>(widened.mapped));
                widened.Flush();
                params.index_type = 1;
                params.index_buffer = (__bridge void*)NativeBuffer(*widened.buffer);
                params.index_offset = widened.offset;
            }
        } else if (index_bind.offset % (index_info.type == MTLIndexTypeUInt16 ? 2 : 4) != 0) {
            forced = Drop::IndexOffsetAlignment;
        } else {
            params.index_type = index_info.type == MTLIndexTypeUInt16 ? 1 : 2;
            params.index_buffer = (__bridge void*)NativeBuffer(*index_bind.buffer);
            params.index_offset = index_bind.offset;
        }
    }

    // The vertex streams of the kernel's stage input (as SetupDraw resolves the render
    // pipeline's: the pipeline's attribute, by location, names the guest stream).
    std::array<Tessellation::VertexBinding, Vulkan::MaxVertexBufferCount> bindings;
    const u32 num_bindings = std::min<u32>(tess.num_vertex_slots, bindings.size());
    for (u32 j = 0; j < num_bindings && forced == Drop::None; ++j) {
        const VertexBufferSlot& slot = tess.vertex_slots[j];
        Tessellation::VertexBinding out{(__bridge void*)impl->dummy, 0, kConstantVertexStride,
                                        slot.slot};
        for (const auto& attribute : vertex_binds.attributes) {
            if (attribute.location != slot.location) {
                continue;
            }
            const u32 b = attribute.binding;
            if (b < vertex_binds.num_buffers && b < vertex_binds.host_buffers.size() &&
                vertex_binds.host_buffers[b]) {
                out.buffer = (__bridge void*)NativeBuffer(*vertex_binds.host_buffers[b]);
                out.offset = vertex_binds.host_offsets[b];
                out.stride = vertex_binds.host_strides[b];
            }
            break;
        }
        if (slot.constant) {
            out.stride = kConstantVertexStride;
        } else if (out.stride % 4 != 0) {
            forced = Drop::VertexStrideAlignment;
        }
        bindings[j] = out;
    }
    if (forced != Drop::None) {
        // The pass is begun for dropped draws as well: its clears are the first thing it does.
        SetupDraw(graphics, state, dynamic, false, forced);
        return;
    }

    // A pass whose clears have not happened yet is begun (and ended again below) first: the
    // kernels interrupt it, and the pass that follows loads what was cleared.
    const auto has_clears = [&] {
        const u32 num_colors = std::min<u32>(state.num_color_attachments, state.color_attachments.size());
        bool clears = state.depth_stencil_attachment.depth_clear ||
                      state.depth_stencil_attachment.stencil_clear;
        for (u32 cb = 0; cb < num_colors; ++cb) {
            clears |= state.color_attachments[cb].is_clear != 0;
        }
        return clears;
    };
    // P6.2: in a held pass whose earlier draws touched nothing this draw reads (nor read what it
    // writes), the kernels go before the pass and only the patch draw is in it.
    bool hoist = false;
    if (scheduler.HoistEnabled()) {
        OpenPass(graphics, state);
        hoist = CanHoistDrawWork();
    }
    if (hoist) {
        scheduler.BeginHoist();
        scheduler.ComputeEncoder("Hoisted kernels");
        scheduler.MemoryBarrier(); // after the kernels hoisted before (scratch is fresh, inputs not)
        impl->hoisted_tessellation.fetch_add(1, std::memory_order_relaxed);
    } else {
        if (has_clears() && !scheduler.IsRenderingWith(state)) {
            OpenPass(graphics, state);
        }
        scheduler.EndRendering();
        scheduler.ComputeEncoder();
        scheduler.LabelEncoder("Tessellation kernels");
    }

    const Tessellation::Plan plan = Tessellation::PlanScratch(tess, patches);
    const VideoCore::StagingSpan scratch =
        runtime.StagingRequest(plan.total, VideoCore::MemoryType::HostUncached, 16);
    id<MTLBuffer> scratch_buffer = NativeBuffer(*scratch.buffer);
    Tessellation::EncodeKernels(
        (__bridge void*)impl->sched.compute, tess, params,
        std::span<const Tessellation::VertexBinding>{bindings.data(), num_bindings},
        (__bridge void*)scratch_buffer, scratch.offset, plan,
        [&](const CompiledShader& shader) {
            impl->BindStage(Impl::StageKind::Compute, shader, push_data);
        },
        [&](const CompiledShader& shader) {
            impl->BindStage(Impl::StageKind::Compute, shader, push_data);
        });
    if (hoist) {
        scheduler.EndHoist();
    }

    // The render pass: loads (never clears) what the kernels interrupted; a held pass that the
    // kernels went before is still the one begun with `state`.
    const RenderState load_state = hoist ? state : WithoutClears(state);
    if (!SetupDraw(graphics, load_state, dynamic, false, Drop::None)) {
        return;
    }
    Tessellation::EncodeDraw(impl->Commands(), tess, (__bridge void*)scratch_buffer, scratch.offset,
                             plan);
    impl->recorded_draws.fetch_add(1, std::memory_order_relaxed);
    MaybePrintStats();
}

namespace {
/// BB_GS_DEBUG (P7.1c): the draw's depth-stencil state without the depth write (as SetupDraw
/// builds it), so that every geometry primitive shows.
template <typename DS>
void ApplyGeometryDebugDepth(PsoCache& pso, RenderList& list, const DS& ds) {
    const auto face = [](const auto& ops, u32 compare_mask, u32 write_mask) {
        return DepthStencilKey::Face{ops.fail_op,       ops.pass_op,
                                     ops.depth_fail_op, ops.compare_op,
                                     u8(compare_mask & 0xff), u8(write_mask & 0xff)};
    };
    DepthStencilKey key;
    key.depth_test = ds.depth_test_enabled;
    key.depth_write = false;
    key.depth_compare = ds.depth_compare_op;
    key.stencil_test = ds.stencil_test_enabled;
    if (ds.stencil_test_enabled) {
        key.front = face(ds.front_ops, ds.front_compare_mask, ds.front_write_mask);
        key.back = face(ds.back_ops, ds.back_compare_mask, ds.back_write_mask);
    }
    if (void* state = pso.GetDepthStencilState(key)) {
        list.SetDepthStencilState(state);
    }
}
} // namespace

Rasterizer::Drop Rasterizer::ResolveGeometryStreams(const GeometryPipeline& geo,
                                                    Tessellation::VertexBinding* out_bindings,
                                                    u32 count) {
    Drop forced = Drop::None;
    for (u32 j = 0; j < count && forced == Drop::None; ++j) {
        const VertexBufferSlot& slot = geo.vertex_slots[j];
        Tessellation::VertexBinding out{(__bridge void*)impl->dummy, 0, kConstantVertexStride,
                                        slot.slot};
        for (const auto& attribute : vertex_binds.attributes) {
            if (attribute.location != slot.location) {
                continue;
            }
            const u32 b = attribute.binding;
            if (b < vertex_binds.num_buffers && b < vertex_binds.host_buffers.size() &&
                vertex_binds.host_buffers[b]) {
                out.buffer = (__bridge void*)NativeBuffer(*vertex_binds.host_buffers[b]);
                out.offset = vertex_binds.host_offsets[b];
                out.stride = vertex_binds.host_strides[b];
            }
            break;
        }
        if (slot.constant) {
            out.stride = kConstantVertexStride;
        } else if (out.stride % 4 != 0) {
            forced = Drop::VertexStrideAlignment;
        }
        out_bindings[j] = out;
    }
    return forced;
}

void Rasterizer::RecordGeometryDrawIndirect(const GraphicsPipeline& graphics,
                                            const RenderState& state, bool is_indexed,
                                            const IndirectDraw& indirect,
                                            const IndirectBuffers& buffers,
                                            const DynamicDrawState& dynamic) {
    const GeometryPipeline& geo = *graphics.Geometry();
    // Every draw of a multi-draw has its own scratch span, all live until the passes' draws.
    constexpr u32 kMaxScratchBytes = 256u << 20;
    Drop forced = Drop::None;
    VkToMtl::IndexInfo index_info{};
    u32 index_size = 0;
    if (BbToggle::Experiment(BbToggle::MtlNoGeometry)) {
        forced = Drop::EmptyDraw;
    } else if (indirect.count != 0) {
        forced = Drop::IndirectCount; // the draw count comes from a GPU buffer
    } else if (!buffers.args || indirect.max_count == 0) {
        forced = Drop::EmptyDraw;
    } else if (buffers.args_offset % 4 != 0 || indirect.stride % 4 != 0) {
        forced = Drop::IndirectAlignment;
    } else if (u64(indirect.max_count) * Geometry::kMaxIndirectBytes > kMaxScratchBytes) {
        forced = Drop::GeometryTooLarge;
    } else if (is_indexed) {
        index_info = VkToMtl::IndexType(index_bind.type);
        index_size = index_info.needs_conversion ? 1 : index_info.type == MTLIndexTypeUInt16 ? 2 : 4;
        if (!index_bind.buffer) {
            forced = Drop::NoIndexBuffer;
        } else if (index_bind.offset % index_size != 0) {
            forced = Drop::IndexOffsetAlignment;
        }
    }
    if (forced == Drop::None) {
        if (!impl->geometry_indirect) {
            impl->geometry_indirect = std::make_unique<Geometry::IndirectKernels>(device);
            if (!impl->geometry_indirect->Ok()) {
                LOG_ERROR(Render, "Metal: {}", impl->geometry_indirect->Error());
            }
        }
        if (!impl->geometry_indirect->Ok()) {
            forced = Drop::NoShader;
        }
    }
    std::array<Tessellation::VertexBinding, Vulkan::MaxVertexBufferCount> bindings;
    const u32 num_bindings = std::min<u32>(geo.num_vertex_slots, bindings.size());
    if (forced == Drop::None) {
        forced = ResolveGeometryStreams(geo, bindings.data(), num_bindings);
    }
    if (forced != Drop::None) {
        SetupDraw(graphics, state, dynamic, false, forced);
        return;
    }

    // As the direct geometry draw: the kernels (prep, copy, export, geometry; barriers between
    // them are encoded by EncodeIndirectKernels) go before a held pass when nothing in it
    // conflicts, else they interrupt the pass.
    const auto has_clears = [&] {
        const u32 num_colors = std::min<u32>(state.num_color_attachments, state.color_attachments.size());
        bool clears = state.depth_stencil_attachment.depth_clear ||
                      state.depth_stencil_attachment.stencil_clear;
        for (u32 cb = 0; cb < num_colors; ++cb) {
            clears |= state.color_attachments[cb].is_clear != 0;
        }
        return clears;
    };
    bool hoist = false;
    if (scheduler.HoistEnabled()) {
        OpenPass(graphics, state);
        hoist = CanHoistDrawWork();
    }
    if (hoist) {
        scheduler.BeginHoist();
        scheduler.ComputeEncoder("Hoisted kernels");
        scheduler.MemoryBarrier();
        impl->hoisted_geometry.fetch_add(1, std::memory_order_relaxed);
    } else {
        if (has_clears() && !scheduler.IsRenderingWith(state)) {
            OpenPass(graphics, state);
        }
        scheduler.EndRendering();
        scheduler.ComputeEncoder();
        scheduler.LabelEncoder("Geometry kernels (indirect)");
    }

    const Geometry::IndirectPlan plan = Geometry::PlanIndirect(geo, is_indexed);
    struct Scratch {
        VideoCore::StagingSpan span;
    };
    std::vector<Scratch> spans;
    id<MTLBuffer> args = NativeBuffer(*buffers.args);
    for (u32 i = 0; i < indirect.max_count; ++i) {
        Scratch& scratch = spans.emplace_back();
        // (256-aligned: the geometry kernel's params are a constant buffer at +256.)
        scratch.span = runtime.StagingRequest(plan.total, VideoCore::MemoryType::HostUncached, 256);
        Geometry::IndirectDraw guest;
        guest.args = (__bridge void*)args;
        guest.args_offset = buffers.args_offset + u64(i) * indirect.stride;
        guest.index_size = index_size;
        if (is_indexed) {
            id<MTLBuffer> index_buffer = NativeBuffer(*index_bind.buffer);
            guest.index_buffer = (__bridge void*)index_buffer;
            guest.index_offset = index_bind.offset;
            guest.index_limit =
                index_bind.offset < index_buffer.length
                    ? (index_buffer.length - index_bind.offset) / index_size
                    : 0;
        }
        Geometry::EncodeIndirectKernels(
            (__bridge void*)impl->sched.compute, geo, *impl->geometry_indirect, guest,
            std::span<const Tessellation::VertexBinding>{bindings.data(), num_bindings},
            (__bridge void*)NativeBuffer(*scratch.span.buffer), scratch.span.offset, plan,
            [&](const CompiledShader& shader) {
                impl->BindStage(Impl::StageKind::Compute, shader, push_data);
            },
            [&](const CompiledShader& shader) {
                impl->BindStage(Impl::StageKind::Compute, shader, push_data);
            });
        if(VideoCore::FrameCapture::NativeRecording())
            VideoCore::FrameCapture::NativeBufferUse(uintptr_t(buffers.args),
                uintptr_t(buffers.args->buffer.native),guest.args_offset,is_indexed?20:16,
                indirect.args+u64(i)*indirect.stride,7,0,false,"indirect_geometry_args");
    }
    if (hoist) {
        scheduler.EndHoist();
    }

    // The render pass loads (never clears) what the kernels interrupted.
    const RenderState load_state = hoist ? state : WithoutClears(state);
    if (!SetupDraw(graphics, load_state, dynamic, false, Drop::None)) {
        return;
    }
    if (GeometryDebugEnabled()) { // BB_GS_DEBUG
        ApplyGeometryDebugDepth(pipeline_cache->GetPsoCache(), impl->Commands(), dynamic.depth_stencil);
        if (Geometry::DebugWantsDraw(geo)) {
            const GeometryPipeline* pipe = &geo;
            const u8* mapped = spans[0].span.mapped;
            const Geometry::Plan debug_plan = plan.plan;
            scheduler.DeferOperation([pipe, mapped, debug_plan] {
                Geometry::DebugLog(*pipe, mapped, debug_plan, true, 0);
            });
        }
    }
    for (const Scratch& scratch : spans) {
        Geometry::EncodeIndirectDraw(impl->Commands(), geo,
                                     (__bridge void*)NativeBuffer(*scratch.span.buffer),
                                     scratch.span.offset, plan);
    }
    impl->recorded_draws.fetch_add(1, std::memory_order_relaxed);
    MaybePrintStats();
}

void Rasterizer::RecordGeometryDraw(const GraphicsPipeline& graphics, const RenderState& state,
                                    const DrawCall& draw, const DynamicDrawState& dynamic) {
    const GeometryPipeline& geo = *graphics.Geometry();
    // The scratch memory of all chunks of one draw is live at once (the draws come after the
    // kernels): bound it.
    constexpr u64 kMaxDrawScratch = 256ull << 20;
    const u32 prims = draw.num_indices / geo.info.input_vertices;
    Drop forced = Drop::None;
    if (draw.num_instances == 0 || prims == 0 || BbToggle::Experiment(BbToggle::MtlNoGeometry)) {
        forced = Drop::EmptyDraw;
    } else if (draw.num_instances > 1) {
        forced = Drop::GeometryInstanced;
    } else if (Geometry::PlanScratch(geo, prims).total > kMaxDrawScratch) {
        forced = Drop::GeometryTooLarge;
    }

    // The export kernel's vertices: indices as for the tessellated draws (16 and 32 bit ones are
    // read from the guest's buffer, 8 bit ones are widened into staging memory).
    Tessellation::DrawParams params;
    params.num_vertices = prims * geo.info.input_vertices;
    params.first_vertex = draw.first_vertex;
    params.first_instance = draw.first_instance;
    u64 index_size = 0;
    VideoCore::StagingSpan widened;
    if (draw.is_indexed && forced == Drop::None) {
        const VkToMtl::IndexInfo index_info = VkToMtl::IndexType(index_bind.type);
        if (!index_bind.buffer) {
            forced = Drop::NoIndexBuffer;
        } else if (index_info.needs_conversion) {
            const u8* data = static_cast<const u8*>(NativeBuffer(*index_bind.buffer).contents);
            if (!data) {
                forced = Drop::IndexDataUnreadable;
            } else {
                widened = runtime.StagingRequest(u64(params.num_vertices) * 2,
                                                 VideoCore::MemoryType::HostUncached, 4);
                IndexExpand::WidenU8(data + index_bind.offset, params.num_vertices, false,
                                     reinterpret_cast<u16*>(widened.mapped));
                widened.Flush();
                params.index_type = 1;
                params.index_buffer = (__bridge void*)NativeBuffer(*widened.buffer);
                params.index_offset = widened.offset;
                index_size = 2;
            }
        } else if (index_bind.offset % (index_info.type == MTLIndexTypeUInt16 ? 2 : 4) != 0) {
            forced = Drop::IndexOffsetAlignment;
        } else {
            params.index_type = index_info.type == MTLIndexTypeUInt16 ? 1 : 2;
            params.index_buffer = (__bridge void*)NativeBuffer(*index_bind.buffer);
            params.index_offset = index_bind.offset;
            index_size = params.index_type == 1 ? 2 : 4;
        }
    }

    // The export kernel's vertex streams (as SetupDraw resolves the render pipeline's).
    std::array<Tessellation::VertexBinding, Vulkan::MaxVertexBufferCount> bindings;
    const u32 num_bindings = std::min<u32>(geo.num_vertex_slots, bindings.size());
    if (forced == Drop::None) {
        forced = ResolveGeometryStreams(geo, bindings.data(), num_bindings);
    }
    if (forced != Drop::None) {
        // The pass is begun for dropped draws as well: its clears are the first thing it does.
        SetupDraw(graphics, state, dynamic, false, forced);
        return;
    }

    // As the tessellated draws: a pass whose clears have not happened yet is begun first, the
    // kernels go before a held pass when nothing in it conflicts (P6.2), else they interrupt it.
    const auto has_clears = [&] {
        const u32 num_colors = std::min<u32>(state.num_color_attachments, state.color_attachments.size());
        bool clears = state.depth_stencil_attachment.depth_clear ||
                      state.depth_stencil_attachment.stencil_clear;
        for (u32 cb = 0; cb < num_colors; ++cb) {
            clears |= state.color_attachments[cb].is_clear != 0;
        }
        return clears;
    };
    bool hoist = false;
    if (scheduler.HoistEnabled()) {
        OpenPass(graphics, state);
        hoist = CanHoistDrawWork();
    }
    if (hoist) {
        scheduler.BeginHoist();
        scheduler.ComputeEncoder("Hoisted kernels");
        scheduler.MemoryBarrier();
        impl->hoisted_geometry.fetch_add(1, std::memory_order_relaxed);
    } else {
        if (has_clears() && !scheduler.IsRenderingWith(state)) {
            OpenPass(graphics, state);
        }
        scheduler.EndRendering();
        scheduler.ComputeEncoder();
        scheduler.LabelEncoder("Geometry kernels");
    }

    struct Chunk {
        Geometry::Plan plan;
        VideoCore::StagingSpan scratch;
    };
    std::vector<Chunk> chunks;
    const u32 chunk_prims = Geometry::MaxPrimitivesPerChunk(geo);
    for (u32 first = 0; first < prims; first += chunk_prims) {
        Chunk& chunk = chunks.emplace_back();
        const u32 count = std::min(chunk_prims, prims - first);
        chunk.plan = Geometry::PlanScratch(geo, count);
        chunk.scratch =
            runtime.StagingRequest(chunk.plan.total, VideoCore::MemoryType::HostUncached, 16);
        Tessellation::DrawParams chunk_draw = params;
        chunk_draw.num_vertices = count * geo.info.input_vertices;
        const u64 first_vertex = u64(first) * geo.info.input_vertices;
        if (draw.is_indexed) {
            chunk_draw.index_offset = params.index_offset + first_vertex * index_size;
        } else {
            chunk_draw.first_vertex = params.first_vertex + static_cast<u32>(first_vertex);
        }
        Geometry::EncodeKernels(
            (__bridge void*)impl->sched.compute, geo, chunk_draw, first,
            std::span<const Tessellation::VertexBinding>{bindings.data(), num_bindings},
            (__bridge void*)NativeBuffer(*chunk.scratch.buffer), chunk.scratch.offset, chunk.plan,
            [&](const CompiledShader& shader) {
                impl->BindStage(Impl::StageKind::Compute, shader, push_data);
            },
            [&](const CompiledShader& shader) {
                impl->BindStage(Impl::StageKind::Compute, shader, push_data);
            });
    }
    if (hoist) {
        scheduler.EndHoist();
    }

    // The render pass loads (never clears) what the kernels interrupted.
    const RenderState load_state = hoist ? state : WithoutClears(state);
    if (!SetupDraw(graphics, load_state, dynamic, false, Drop::None)) {
        return;
    }
    if (GeometryDebugEnabled()) { // BB_GS_DEBUG
        ApplyGeometryDebugDepth(pipeline_cache->GetPsoCache(), impl->Commands(), dynamic.depth_stencil);
        if (Geometry::DebugWantsDraw(geo)) {
            const GeometryPipeline* pipe = &geo;
            const u8* mapped = chunks[0].scratch.mapped;
            const Geometry::Plan debug_plan = chunks[0].plan;
            scheduler.DeferOperation([pipe, mapped, debug_plan] {
                Geometry::DebugLog(*pipe, mapped, debug_plan, false, debug_plan.prims);
            });
        }
    }
    for (const Chunk& chunk : chunks) {
        Geometry::EncodeDraw(impl->Commands(), geo, (__bridge void*)NativeBuffer(*chunk.scratch.buffer),
                             chunk.scratch.offset, chunk.plan);
    }
    impl->recorded_draws.fetch_add(1, std::memory_order_relaxed);
    MaybePrintStats();
}

namespace {
u32 FragmentBindingBase(const GraphicsPipeline& pipeline) {
    u32 base = 0;
    const auto* fragment = pipeline.GetStages()[u32(Shader::SwStage::Fragment)];
    for (const auto* stage : pipeline.GetStages()) {
        if (stage == fragment) break;
        if (stage) base += stage->buffers.size() + stage->images.size() + stage->samplers.size();
    }
    return base;
}
bool ReadOnlyStages(const GraphicsPipeline& pipeline) {
    for (const auto* stage : pipeline.GetStages()) {
        if (!stage) continue;
        for (const auto& buffer : stage->buffers) if (buffer.is_written) return false;
        for (const auto& image : stage->images) if (image.is_written || image.is_atomic) return false;
    }
    return true;
}
bool FullViewport(const auto& dynamic, const RenderState& state) {
    const auto& v = dynamic.viewport_scissors;
    return v.viewports.size() == 1 && v.scissors.size() == 1 &&
           !dynamic.primitive.rasterizer_discard &&
           std::abs(v.viewports[0].width) == float(state.width) &&
           std::abs(v.viewports[0].height) == float(state.height);
}
}
bool Rasterizer::BypassMotionBlur(const GraphicsPipeline& pipeline, const RenderState& state,
                                   const DynamicDrawState& dynamic) {
    const auto* fs = pipeline.GetStages()[u32(Shader::SwStage::Fragment)];
    if (!impl->velocity_requested || BbSettings::Get().startup_effects[2] || !fs ||
        fs->pgm_hash != 0xe0305cef || !ReadOnlyStages(pipeline)) return false;
    // The exact known shader's source 0 is the unblurred scene. Like IFreemz's image-copy
    // route, image extents determine the copy, not the game's oversized viewport/scissor.
    const u32 index = FragmentBindingBase(pipeline) + fs->buffers.size();
    const void* src = index < impl->num_textures ? impl->textures[index] : nullptr;
    const void* dst = reinterpret_cast<void*>(state.color_attachments[0].image_view);
    BlurCopy result = BlurCopy::Missing;
    if (state.num_color_attachments == 1 && state.num_layers == 1 && !fs->images.empty())
        result = CopyWithoutMotionBlur(scheduler, *metal_scene_targets, src, dst);
    if (!BlurCopied(result)) {
        if (!impl->blur_fallback_logged) {
            id<MTLTexture> a = (__bridge id<MTLTexture>)src, b = (__bridge id<MTLTexture>)dst;
            std::printf("Metal motion blur: bypass rejected: %s; targets %u layers %u, pass %ux%u, source %lux%lu fmt %lu usage %lu, destination %lux%lu fmt %lu\n",
                        BlurCopyName(result),state.num_color_attachments,state.num_layers,state.width,state.height,
                        a.width,a.height,a.pixelFormat,a.usage,b.width,b.height,b.pixelFormat);
            impl->blur_fallback_logged = true;
        }
        return false;
    }
    if (++impl->blur_copies <= 2) std::printf("Metal motion blur: final blur shader replaced with scene copy (%s), pass %llu\n", BlurCopyName(result),impl->blur_copies);
    return true;
}
std::optional<Rasterizer::VelocityDraw> Rasterizer::PrepareVelocity(const GraphicsPipeline& pipeline,
                                     const RenderState& state, const DynamicDrawState& dynamic) {
    const auto reject = [this]() -> std::optional<VelocityDraw> { scheduler.FlushRenderTail(); return {}; };
    if (!velocity_mirror->Enabled() || pipeline.Tessellation() || pipeline.Geometry()) return reject();
    const auto* fs = pipeline.GetStages()[u32(Shader::SwStage::Fragment)];
    const int packing = fs ? VelocityMirror::PackingBinding(fs->pgm_hash) : -1;
    if (packing < 0 || !FullViewport(dynamic, state)) return reject();
    if (!metal_upscaler->WantsVelocity()) {
        if (!impl->late_velocity_logged) {
            std::printf("Metal object motion: game velocity outside active pre-upscale phase; camera fallback\n");
            impl->late_velocity_logged = true;
        }
        return reject();
    }
    const auto& r = pipeline.Render();
    if (r.key.num_color_attachments != 1 || r.key.num_samples != 1) return reject();
    const u32 index = FragmentBindingBase(pipeline) + packing;
    if (u32(packing) >= fs->buffers.size() || index >= impl->num_buffers) return reject();
    auto [entry, added] = impl->velocity_variants.try_emplace(&pipeline,nullptr);
    if (added && ReadOnlyStages(pipeline)) {
        auto key = r.key;
        key.color_buffers[0].data_format = AmdGpu::DataFormat::Format16_16_16_16;
        key.color_buffers[0].num_format = AmdGpu::NumberFormat::Float;
        const RenderPipelineInput input{.vertex_attributes = pipeline.VertexAttributes(),
                                        .rect_list_triangles = r.rect_list && !r.primitive_emulated};
        entry->second = pipeline_cache->GetPsoCache().GetRenderPipeline(key,r.vs,r.fs,input);
        if (entry->second && !key.motion_vectors) {
            GraphicsSerialization serialized;
            const auto attributes = pipeline.VertexAttributes();
            serialized.vertex_attributes.assign(attributes.begin(), attributes.end());
            VideoCore::RegisterPipelineData(key, std::hash<VideoCore::GraphicsPipelineKey>{}(key), serialized);
        }
    }
    if (!entry->second) return reject();
    // Only consecutive draws of one guest pass can share a host mirror batch. Bound CPU
    // command storage; changing pass, any non-velocity work or hazards flushes it first.
    if (velocity_mirror->PendingDraws() >= VelocityMirror::MaxBatchDraws ||
        (!scheduler.IsRenderingWith(state) && !scheduler.IsRenderingWith(WithoutClears(state))))
        scheduler.FlushRenderTail();
    VelocityDraw plan;
    plan.pipeline = entry->second;
    plan.guest = state;
    bool clear = state.depth_stencil_attachment.depth_clear || state.depth_stencil_attachment.stencil_clear;
    for (u32 n=0;n<state.num_color_attachments;++n) clear |= state.color_attachments[n].is_clear != 0;
    if (clear) {
        // Apply the original pass's clears before seeding, but do not copy depth written by
        // the original velocity geometry (strict LESS would self-occlude the mirror).
        OpenPass(pipeline,state);
        scheduler.EndWithBarrier();
        plan.guest = WithoutClears(state);
        if (state.depth_stencil_attachment.depth_clear || state.depth_stencil_attachment.stencil_clear)
            velocity_mirror->InvalidateDepthSeed();
    }
    const auto& buffer = impl->buffers[index];
    if (!velocity_mirror->Capture(buffer.buffer,buffer.offset,buffer.size)) return reject();
    const auto size = metal_upscaler->VelocitySize();
    if (!velocity_mirror->Prepare(state,size[0],size[1],plan.mirror,
                                  db_desc.first ? texture_cache.GetImage(db_desc.first).image_uid : 0)) return reject();
    plan.dynamic = dynamic;
    const float fx = float(plan.mirror.width)/state.width, fy = float(plan.mirror.height)/state.height;
    auto& vp = plan.dynamic.viewport_scissors.viewports[0];
    vp.x*=fx;vp.y*=fy;vp.width*=fx;vp.height*=fy;
    auto& sc = plan.dynamic.viewport_scissors.scissors[0];
    sc.offset.x=std::lround(sc.offset.x*fx);sc.offset.y=std::lround(sc.offset.y*fy);
    sc.extent.width=std::lround(sc.extent.width*fx);sc.extent.height=std::lround(sc.extent.height*fy);
    return plan;
}
void Rasterizer::ReplayVelocity(const GraphicsPipeline& pipeline, const DrawCall& draw,
                                const VelocityDraw& plan) {
    impl->velocity_replaying = true;
    impl->velocity_pipeline = plan.pipeline;
    impl->capture_commands = &velocity_mirror->Record(plan.mirror);
    const u64 before=impl->recorded_draws.load(std::memory_order_relaxed);
    RecordDraw(&pipeline,plan.mirror,draw,plan.dynamic);
    impl->capture_commands = nullptr;
    impl->velocity_pipeline = nullptr;
    impl->velocity_replaying = false;
    if (impl->recorded_draws.load(std::memory_order_relaxed)!=before) velocity_mirror->QueuedDraw();
}

void Rasterizer::RecordDraw(const VideoCore::PipelineBase* pipeline, const RenderState& state,
                            const DrawCall& draw, const DynamicDrawState& dynamic) {
    const u64 content_draws_before=MemoryStats::ContentEnabled()?impl->recorded_draws.load(std::memory_order_relaxed):0;
    SCOPE_EXIT {
        if(MemoryStats::ContentEnabled() && impl->recorded_draws.load(std::memory_order_relaxed)!=content_draws_before)
            NoteAttachmentContent(state,MemoryStats::Content::Draw);
    };
    @autoreleasepool {
        const auto& graphics = *static_cast<const GraphicsPipeline*>(pipeline);
        if (!impl->velocity_replaying && BypassMotionBlur(graphics, state, dynamic)) return;
        std::optional<VelocityDraw> velocity;
        if (!impl->velocity_replaying) velocity = PrepareVelocity(graphics,state,dynamic);
        const auto& guest_state = velocity ? velocity->guest : state;
        if (graphics.Tessellation()) {
            RecordTessellationDraw(graphics, state, draw, dynamic);
            return;
        }
        if (graphics.Geometry()) {
            RecordGeometryDraw(graphics, state, draw, dynamic);
            return;
        }
        const RenderPipeline& r = graphics.Render();
        // A RectList pipeline's vertex shader makes the rectangle from guest vertices 3 i .. 3 i +
        // 2 and runs for 6 vertices per rectangle (mtl_spirv_rectlist.h).
        const bool rect_list = r.rect_list && !r.primitive_emulated;
        Drop forced = draw.num_instances == 0 || draw.num_indices == 0 ? Drop::EmptyDraw : Drop::None;
        if (rect_list && forced == Drop::None) {
            forced = draw.is_indexed                  ? Drop::RectListDraw
                     : draw.first_vertex % 3 != 0     ? Drop::RectListVertexStart
                     : draw.num_indices / 3 == 0      ? Drop::EmptyDraw
                                                      : Drop::None;
        }
        // Topologies Metal lacks (quad lists, fans, ...) and uint8 indices: indices expanded on
        // the CPU from the guest's index data (shared memory) into the staging ring.
        const auto topology = r.primitive_emulated && !rect_list
                                  ? IndexExpand::FromGuest(r.key.prim_type)
                                  : std::nullopt;
        const bool widen = draw.is_indexed && VkToMtl::IndexType(index_bind.type).needs_conversion;
        const u8* index_data = nullptr;
        if ((topology || widen) && draw.is_indexed && forced == Drop::None && index_bind.buffer) {
            index_data = static_cast<const u8*>(NativeBuffer(*index_bind.buffer).contents);
            if (!index_data) {
                forced = Drop::IndexDataUnreadable;
            }
        }
        std::optional<RenderState> diagnostic_continuation;
        if (!impl->velocity_replaying && graphics.Render().fs && VideoCore::FrameCapture::NativeRecording()) {
            const auto& stages=graphics.GetStages();
            const auto* vs=stages[u32(Shader::SwStage::Vertex)];
            const auto* ps=stages[u32(Shader::SwStage::Fragment)];
            // A diagnostic blit splits an existing pass. Its clear was already consumed;
            // resuming the same attachments must load, exactly like tessellation's split.
            const bool continuing=scheduler.IsRenderingWith(guest_state) ||
                scheduler.IsRenderingWith(WithoutClears(guest_state));
            if(vs&&ps&&impl->texture_probe.Draw(scheduler,impl->dev.device,vs->pgm_hash,
                ps->pgm_hash,draw.num_indices,*graphics.Render().fs,impl->textures.data(),impl->num_textures)&&continuing)
                diagnostic_continuation=WithoutClears(guest_state);
        }
        if (!SetupDraw(graphics, diagnostic_continuation?*diagnostic_continuation:guest_state, dynamic, draw.is_indexed, forced)) {
            return;
        }
        RenderList& list = impl->Commands();
        const auto type = static_cast<u32>(r.primitive_type);
        if (topology || widen) {
            // (The index data is read now: a GPU write of it earlier in this frame would not be
            // seen. Index buffers the GPU writes are not known in this game.)
            const u32 src_size = !draw.is_indexed                        ? 4
                                 : index_bind.type == vk::IndexType::eUint8EXT ? 1
                                 : index_bind.type == vk::IndexType::eUint16   ? 2
                                                                               : 4;
            const u8* src = index_data ? index_data + index_bind.offset : nullptr;
            const bool restart = draw.is_indexed && dynamic.primitive.restart_enabled;
            VideoCore::StagingSpan span;
            u32 index_type = MTLIndexTypeUInt32;
            u32 out_type = type;
            u32 count = 0;
            if (topology) {
                const u32 max_count = IndexExpand::OutputCount(*topology, draw.num_indices, restart);
                span = runtime.StagingRequest(u64(std::max(max_count, 1u)) * 4,
                                              VideoCore::MemoryType::HostUncached, 4);
                count = IndexExpand::Expand(*topology, src, src_size, draw.num_indices, 0, restart,
                                            reinterpret_cast<u32*>(span.mapped));
                switch (IndexExpand::OutputOf(*topology)) {
                case IndexExpand::OutputPrimitive::Triangle:
                    out_type = MTLPrimitiveTypeTriangle;
                    break;
                case IndexExpand::OutputPrimitive::TriangleStrip:
                    out_type = MTLPrimitiveTypeTriangleStrip;
                    break;
                case IndexExpand::OutputPrimitive::Line:
                    out_type = MTLPrimitiveTypeLine;
                    break;
                case IndexExpand::OutputPrimitive::LineStrip:
                    out_type = MTLPrimitiveTypeLineStrip;
                    break;
                }
            } else {
                span = runtime.StagingRequest(u64(std::max(draw.num_indices, 1u)) * 2,
                                              VideoCore::MemoryType::HostUncached, 4);
                IndexExpand::WidenU8(src, draw.num_indices, restart,
                                     reinterpret_cast<u16*>(span.mapped));
                index_type = MTLIndexTypeUInt16;
                count = draw.num_indices;
            }
            span.Flush();
            if (count != 0) {
                // Non-indexed sources expand to 0, 1, ...: the first vertex is the base vertex.
                list.DrawIndexedPrimitives(out_type, count, index_type,
                                           (__bridge void*)NativeBuffer(*span.buffer), span.offset,
                                           draw.num_instances, static_cast<s32>(draw.first_vertex),
                                           draw.first_instance);
            }
        } else if (draw.is_indexed) {
            const VkToMtl::IndexInfo index_info = VkToMtl::IndexType(index_bind.type);
            list.DrawIndexedPrimitives(type, draw.num_indices, index_info.type,
                                       (__bridge void*)NativeBuffer(*index_bind.buffer),
                                       index_bind.offset, draw.num_instances,
                                       static_cast<s32>(draw.first_vertex), draw.first_instance);
        } else if (rect_list) {
            // vertex_id of the vertex shader = vertexStart + k: (id / 6) * 3 = first_vertex + 3 i.
            list.DrawPrimitives(type, 2 * u64(draw.first_vertex), u64(draw.num_indices) / 3 * 6,
                                draw.num_instances, draw.first_instance);
        } else {
            list.DrawPrimitives(type, draw.first_vertex, draw.num_indices, draw.num_instances,
                                draw.first_instance);
        }
        impl->recorded_draws.fetch_add(1, std::memory_order_relaxed);
        MaybePrintStats();
        if (velocity) ReplayVelocity(graphics, draw, *velocity);
    }
}

void Rasterizer::RecordDrawIndirect(const VideoCore::PipelineBase* pipeline,
                                    const RenderState& state, bool is_indexed,
                                    const IndirectDraw& indirect, const IndirectBuffers& buffers,
                                    const DynamicDrawState& dynamic) {
    scheduler.FlushRenderTail();
    const u64 content_draws_before=MemoryStats::ContentEnabled()?impl->recorded_draws.load(std::memory_order_relaxed):0;
    SCOPE_EXIT {
        if(MemoryStats::ContentEnabled() && impl->recorded_draws.load(std::memory_order_relaxed)!=content_draws_before)
            NoteAttachmentContent(state,MemoryStats::Content::Draw);
    };
    @autoreleasepool {
        const auto& graphics = *static_cast<const GraphicsPipeline*>(pipeline);
        // The count buffer (draw count from the GPU) needs a compute pass that clamps and
        // translates the commands: P4.3d.
        const RenderPipeline& pipeline_state = graphics.Render();
        if (graphics.Tessellation()) {
            SetupDraw(graphics, state, dynamic, is_indexed, Drop::TessellationIndirect);
            return;
        }
        if (graphics.Geometry()) {
            RecordGeometryDrawIndirect(graphics, state, is_indexed, indirect, buffers, dynamic);
            return;
        }
        const bool expanded =
            (pipeline_state.primitive_emulated && !pipeline_state.rect_list) ||
            (is_indexed && VkToMtl::IndexType(index_bind.type).needs_conversion);
        const Drop forced = pipeline_state.rect_list && !pipeline_state.primitive_emulated
                                ? Drop::RectListDraw
                            : expanded ? Drop::IndirectEmulated
                            : indirect.count != 0                      ? Drop::IndirectCount
                            : !buffers.args || indirect.max_count == 0 ? Drop::EmptyDraw
                            : buffers.args_offset % 4 != 0 || indirect.stride % 4 != 0
                                ? Drop::IndirectAlignment
                                : Drop::None;
        if (!SetupDraw(graphics, state, dynamic, is_indexed, forced)) {
            return;
        }
        const RenderPipeline& r = graphics.Render();
        RenderList& list = impl->Commands();
        const auto type = static_cast<u32>(r.primitive_type);
        id<MTLBuffer> args = NativeBuffer(*buffers.args);
        // The Vulkan commands (VkDrawIndirectCommand, VkDrawIndexedIndirectCommand) have the
        // layout of MTLDrawPrimitivesIndirectArguments / MTLDrawIndexedPrimitivesIndirectArguments.
        for (u32 i = 0; i < indirect.max_count; ++i) {
            const NSUInteger offset = buffers.args_offset + u64(i) * indirect.stride;
            if(VideoCore::FrameCapture::NativeRecording())
                VideoCore::FrameCapture::NativeBufferUse(uintptr_t(buffers.args),
                    uintptr_t(buffers.args->buffer.native),offset,is_indexed?20:16,
                    indirect.args+u64(i)*indirect.stride,7,0,false,"indirect_draw_args");
            if (is_indexed) {
                const VkToMtl::IndexInfo index_info = VkToMtl::IndexType(index_bind.type);
                list.DrawIndexedPrimitivesIndirect(
                    type, index_info.type, (__bridge void*)NativeBuffer(*index_bind.buffer),
                    index_bind.offset, (__bridge void*)args, offset);
            } else {
                list.DrawPrimitivesIndirect(type, (__bridge void*)args, offset);
            }
        }
        impl->recorded_draws.fetch_add(1, std::memory_order_relaxed);
        MaybePrintStats();
    }
}

void Rasterizer::RecordDispatch(const VideoCore::PipelineBase* pipeline, const Shader::Info& cs,
                                const AmdGpu::ComputeProgram& cs_program) {
    @autoreleasepool {
        const ComputePipeline& compute =
            static_cast<const ComputeShaderPipeline*>(pipeline)->Compute();
        if (!compute.state || !compute.shader) {
            NoteDrop(Drop::NoShader);
            return;
        }
        if (cs_program.dim_x == 0 || cs_program.dim_y == 0 || cs_program.dim_z == 0) {
            NoteDrop(Drop::EmptyDispatch);
            return;
        }
        // Requires the existing precise-residency gate as well: its encoder-level
        // union still upgrades a later BDA dispatch before any indirect access.
        static const bool precise_compute = [] {
            const char* value = std::getenv("BB_MTL_PRECISE_COMPUTE");
            return value && value[0] == '1';
        }();
        static const bool copy_watch=[] {
            const char* flag=std::getenv("BB_CAPTURE_COPY_POINTERS");return flag && flag[0]=='1';
        }();
        if(copy_watch && VideoCore::FrameCapture::NativeRecording() && cs.pgm_hash==0xfefebf9full)
            PrepareCopyWatchPipeline(device,compute);
        const bool direct_resources=compute.shader->HasOnlyDirectComputeResources();
        const bool bound_only=precise_compute && direct_resources &&
            !(copy_watch && VideoCore::FrameCapture::NativeRecording() && cs.pgm_hash==0xfefebf9full);
        scheduler.ComputeEncoder(nullptr, bound_only);
        if(VideoCore::FrameCapture::NativeRecording()) {
            VideoCore::FrameCapture::Native(std::format("{{\"op\":\"compute_access\",\"queue\":{},\"direct_resources\":{},\"requested_bound_only\":{},\"guest_declared\":{},\"bda\":{},\"paged\":{},\"indirect\":false}}",(uintptr_t)&scheduler,
                direct_resources,bound_only,impl->sched.compute_guest_declared,
                compute.shader->uses_device_address,compute.shader->uses_bda_pagetable));
        }
        id<MTLComputeCommandEncoder> enc = impl->sched.compute;
        if (scheduler.PassLabels() && !enc.label) {
            char text[48];
            std::snprintf(text, sizeof(text), "CS %llx", (unsigned long long)cs.pgm_hash);
            scheduler.LabelEncoder(text);
        }
        bool watched=false;
        if(copy_watch && VideoCore::FrameCapture::NativeRecording() && cs.pgm_hash==0xfefebf9full) {
            const char* input=std::getenv("BB_WATCH");char* end=nullptr;
            const u64 address=input?std::strtoull(input,&end,0):0;
            const u64 bytes=end && *end==','?std::strtoull(end+1,&end,0):0;
            const u32 shift=buffer_cache.GetSparsePageShift();
            const u64 plane=buffer_cache.DiagnosticBdaGuestOffset();
            if(compute.copy_watch_state && input && end && !*end && address && address<(u64{1}<<40) &&
               bytes==192 && shift<32 && (address&((u64{1}<<shift)-1))+bytes<=(u64{1}<<shift) &&
               plane && plane<=~u32{0} && buffer_cache.GetBdaPageTableBuffer()->SizeBytes()>=plane*16 &&
               impl->copy_watch_count<impl->copy_watch_records.size()) {
                if(!impl->copy_watch_buffer) {
                    impl->copy_watch_buffer=[impl->dev.device newBufferWithLength:128*256 options:MTLResourceStorageModeShared];
                    if(impl->copy_watch_buffer)MemoryStats::Track((__bridge const void*)impl->copy_watch_buffer,MemoryStats::Kind::Staging);
                }
                if(impl->copy_watch_buffer) {
                    const auto slot=impl->copy_watch_count++;
                    CopyWatchData d{};d.guest=address;d.bytes=bytes;d.shift=shift;
                    d.plane_offset=plane;d.two_level=buffer_cache.DiagnosticBdaTwoLevel();
                    std::memcpy(static_cast<char*>(impl->copy_watch_buffer.contents)+slot*256,&d,sizeof(d));
                    impl->copy_watch_records[slot]={VideoCore::FrameCapture::NativeCaptureSequence(),cs.pgm_hash,
                        compute.shader->copy_watch_reads,compute.shader->copy_watch_writes};
                    [enc setBuffer:impl->copy_watch_buffer offset:slot*256 atIndex:CopyWatchSlot];
                    [enc setBuffer:(__bridge id<MTLBuffer>)buffer_cache.GetBdaPageTableBuffer()->buffer.native
                        offset:0 atIndex:CopyWatchRootSlot];
                    watched=true;
                }
            }
            if(!watched)VideoCore::FrameCapture::Native("{\"op\":\"gpu_copy_pointer_uncovered\",\"kind\":\"direct\"}");
        }
        if(compute.shader->copy_pair_merged && !watched) {
            static bool logged=false;if(!logged){logged=true;std::printf("Metal copy paired-store merge: active; same physical pointer only, distinct guest/current views retain both stores\n");}
        }
        [enc setComputePipelineState:(__bridge id<MTLComputePipelineState>)(watched?compute.copy_watch_state:compute.state)];
        impl->BindStage(Impl::StageKind::Compute, *compute.shader, push_data);
        [enc dispatchThreadgroups:MTLSizeMake(cs_program.dim_x, cs_program.dim_y, cs_program.dim_z)
            threadsPerThreadgroup:MTLSizeMake(compute.threads_per_group[0],
                                              compute.threads_per_group[1],
                                              compute.threads_per_group[2])];
        impl->recorded_dispatches.fetch_add(1, std::memory_order_relaxed);
        impl->TraceStorage(scheduler,*compute.shader,cs.pgm_hash,"compute-write-result");
        MaybePrintStats();
    }
}

void Rasterizer::RecordDispatchIndirect(const VideoCore::PipelineBase* pipeline, VAddr address,
                                        u32 size, const VideoCore::Buffer* args,
                                        u64 args_offset) {
    @autoreleasepool {
        const ComputePipeline& compute =
            static_cast<const ComputeShaderPipeline*>(pipeline)->Compute();
        if (!compute.state || !compute.shader || !args) {
            NoteDrop(Drop::NoShader);
            return;
        }
        if (args_offset % 4 != 0) {
            NoteDrop(Drop::IndirectAlignment);
            return;
        }
        // Requires the existing precise-residency gate as well: its encoder-level
        // union still upgrades a later BDA dispatch before any indirect access.
        static const bool precise_compute = [] {
            const char* value = std::getenv("BB_MTL_PRECISE_COMPUTE");
            return value && value[0] == '1';
        }();
        const bool direct_resources=compute.shader->HasOnlyDirectComputeResources();
        const bool bound_only=precise_compute && direct_resources;
        scheduler.ComputeEncoder(nullptr, bound_only);
        if(VideoCore::FrameCapture::NativeRecording()) {
            VideoCore::FrameCapture::Native(std::format("{{\"op\":\"compute_access\",\"queue\":{},\"direct_resources\":{},\"requested_bound_only\":{},\"guest_declared\":{},\"bda\":{},\"paged\":{},\"indirect\":true}}",(uintptr_t)&scheduler,
                direct_resources,bound_only,impl->sched.compute_guest_declared,
                compute.shader->uses_device_address,compute.shader->uses_bda_pagetable));
        }
        id<MTLComputeCommandEncoder> enc = impl->sched.compute;
        if(VideoCore::FrameCapture::NativeRecording()) {
            const char* flag=std::getenv("BB_CAPTURE_COPY_POINTERS");
            const auto& stage=pipeline->GetStage(Shader::SwStage::Compute);
            if(flag && flag[0]=='1' && stage.pgm_hash==0xfefebf9full)
                VideoCore::FrameCapture::Native("{\"op\":\"gpu_copy_pointer_uncovered\",\"kind\":\"indirect\"}");
        }
        [enc setComputePipelineState:(__bridge id<MTLComputePipelineState>)compute.state];
        impl->BindStage(Impl::StageKind::Compute, *compute.shader, push_data);
        [enc dispatchThreadgroupsWithIndirectBuffer:NativeBuffer(*args)
                               indirectBufferOffset:args_offset
                              threadsPerThreadgroup:MTLSizeMake(compute.threads_per_group[0],
                                                                compute.threads_per_group[1],
                                                                compute.threads_per_group[2])];
        if(VideoCore::FrameCapture::NativeRecording())
            VideoCore::FrameCapture::NativeBufferUse(uintptr_t(args),uintptr_t(args->buffer.native),
                args_offset,12,address,7,0,false,"indirect_dispatch_args");
        impl->recorded_dispatches.fetch_add(1, std::memory_order_relaxed);
        impl->TraceStorage(scheduler,*compute.shader,pipeline->GetStage(Shader::SwStage::Compute).pgm_hash,"indirect-compute-write-result");
        MaybePrintStats();
    }
}

void Rasterizer::EmitDrawBuffers(bool vertex, bool index) {
    // Nothing to record yet: the core's vertex_binds and index_bind are valid for the draw that
    // follows, and RecordDraw applies them once the render encoder is open.
}

void Rasterizer::RecordHoistedDispatch(const VideoCore::PipelineBase* pipeline,
                                       const Shader::Info& cs,
                                       const AmdGpu::ComputeProgram& cs_program) {
    // Before the held pass (P6.2): the scheduler's compute encoder in front of it, ordered after
    // what was hoisted before; the pass waits for it.
    scheduler.BeginHoist();
    scheduler.ComputeEncoder("Hoisted dispatches");
    scheduler.MemoryBarrier();
    RecordDispatch(pipeline, cs, cs_program);
    scheduler.EndHoist();
}

void Rasterizer::SetPassLabel(const PassLabel& label) {
    char text[96];
    std::snprintf(text, sizeof(text), "P%llu %ux%u C%u%s vs %llx ps %llx",
                  (unsigned long long)++impl->pass_number, label.width, label.height,
                  label.num_color_attachments, label.has_depth ? " D" : "",
                  (unsigned long long)label.vs_hash, (unsigned long long)label.ps_hash);
    impl->next_pass_label = text;
}

bool Rasterizer::WaitGpuTick(u64 tick, u64 timeout_ns) {
    return scheduler.WaitTick(tick, timeout_ns);
}

void Rasterizer::RecordBufferMarker(const VideoCore::Buffer* buffer, u64 offset, u64 value,
                                    u32 num_bytes) {
    UNREACHABLE_MSG("Metal labels are written by the signal thread (BufferMarkersSupported)");
}

} // namespace Metal
