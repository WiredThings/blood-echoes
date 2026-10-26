#include "video_core/renderer_metal/mtl_local_library.h"
#include "video_core/renderer_metal/mtl_phase_counters.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// Velocity shader identities, RGBA16F mirror and packing decode follow IFreemz, commit
// 149f63645bbb40fa587daa68fbf003b1f6dbcd7d. Camera fallback, Metal hazards/residency and
// completed-handler retirement are integrated with this backend; no vertex history cache.
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include <bit>
#include <cstdio>
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_velocity_mirror.h"
namespace Metal {
namespace {
NSString* const source = @R"MSL(
#include <metal_stdlib>
using namespace metal;
kernel void merge_velocity(texture2d<float, access::read> mirror [[texture(0)]],
                           texture2d<float, access::write> motion [[texture(1)]],
                           device const float2& packing [[buffer(0)]],
                           uint2 p [[thread_position_in_grid]]) {
    uint2 extent(motion.get_width(), motion.get_height());
    if (any(p >= extent)) return;
    float4 m = mirror.read(p);
    if (m.a <= .5f || !all(isfinite(packing)) || any(abs(packing) <= 1e-6f)) return;
    float2 delta = (m.rg - .5f) * float2(-float(extent.x), float(extent.y)) / packing;
    // Bad/missing game vectors leave the already generated camera motion intact.
    if (!all(isfinite(delta)) || any(abs(delta) > float2(extent))) return;
    motion.write(float4(delta, 0, 0), p);
}
)MSL";
}
struct VelocityMirror::Impl {
    Device& device;
    Scheduler& scheduler;
    SceneTargets& targets;
    bool enabled, captured = false, drawn = false, failed = false;
    u64 frames = 0, draws = 0, merged = 0, batches = 0, seeds = 0;
    RenderList commands;
    VideoCore::RenderState pending_state{};
    u32 pending = 0;
    const void* seeded = nullptr;
    u64 seeded_uid = 0;
    id<MTLTexture> color, depth, snapshot;
    id<MTLBuffer> packing;
    id<MTLComputePipelineState> pipeline;
    void Retire(id<MTLResource> resource) {
        if (!resource) return;
        auto* d = &device.GetImpl();
        MemoryStats::Retire((__bridge const void*)resource);
        scheduler.DeferOperation([d, resource] { d->RemoveResident(resource); });
    }
    id<MTLTexture> Texture(MTLPixelFormat format, u32 w, u32 h, NSString* label) {
        auto* desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
        desc.storageMode = MTLStorageModePrivate;
        desc.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead | MTLTextureUsagePixelFormatView;
        id<MTLTexture> t = [device.GetImpl().device newTextureWithDescriptor:desc];
        if (t) { MemoryStats::Track((__bridge const void*)t, MemoryStats::Kind::Velocity); t.label = label; device.GetImpl().AddResident(t); }
        return t;
    }
    void Fail() {
        if (!failed) std::printf("Metal object motion: allocation/compile failed; camera fallback\n");
        failed = true;
    }
};
VelocityMirror::VelocityMirror(Device& d, Scheduler& s, SceneTargets& t, bool enabled)
    : impl{std::make_unique<Impl>(d, s, t, enabled)} {
    if (!enabled) return;
    s.SetRenderTail([this] { Flush(); });
    NSError* error = nil;
    auto* options = [MTLCompileOptions new];
    options.mathMode = MTLMathModeSafe;
    auto library = Metal::LocalLibrary::Build(d.GetImpl().device, source, options, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"merge_velocity"}); });
    if (library) {BbMetalPhase::Call bb_metal_call_75{BbMetalPhase::Kind::Compute}; impl->pipeline = [d.GetImpl().device newComputePipelineStateWithFunction:[library newFunctionWithName:@"merge_velocity"] error:&error]; bb_metal_call_75.Finish(bool(impl->pipeline));}
    impl->packing = [d.GetImpl().device newBufferWithLength:8 options:MTLResourceStorageModePrivate];
    if (impl->packing) { MemoryStats::Track((__bridge const void*)impl->packing, MemoryStats::Kind::Velocity); d.GetImpl().AddResident(impl->packing); }
    if (!impl->pipeline || !impl->packing) {
        if (error) std::printf("Metal object motion error: %s\n", error.localizedDescription.UTF8String);
        impl->Fail();
    }
    std::printf("Metal object motion: game velocity mirror %s; camera fallback; no CPU readback\n", Enabled() ? "enabled" : "unavailable");
}
VelocityMirror::~VelocityMirror() {
    if (impl->enabled) impl->scheduler.SetRenderTail({});
    impl->scheduler.EndWithBarrier();
    impl->Retire(impl->color); impl->Retire(impl->depth); impl->Retire(impl->snapshot); impl->Retire(impl->packing);
    auto pipeline = impl->pipeline;
    impl->scheduler.DeferOperation([pipeline] { (void)pipeline; });
    if (impl->enabled) std::printf("Metal object motion: %llu mirrored draws in %llu batches, %llu depth seeds, %llu merged frames / %llu frames\n", impl->draws, impl->batches, impl->seeds, impl->merged, impl->frames);
}
bool VelocityMirror::Enabled() const { return impl->enabled && !impl->failed; }
int VelocityMirror::PackingBinding(u64 hash) {
    if (hash == 0x34bc187c) return 0;
    if (hash == 0x749e4f9e || hash == 0xb25e4fae) return 1;
    return -1;
}
void VelocityMirror::FrameStart() {
    impl->scheduler.FlushRenderTail();
    impl->captured = impl->drawn = false;
    impl->seeded = nullptr;
    ++impl->frames;
    if (Enabled() && impl->frames % 300 == 0)
        std::printf("Metal object motion: %llu mirrored draws in %llu batches, %llu depth seeds, %llu merged frames / %llu frames\n", impl->draws, impl->batches, impl->seeds, impl->merged, impl->frames);
}
bool VelocityMirror::Capture(const void* buffer, u64 offset, u64 size) {
    if (!Enabled()) return false;
    if (impl->captured) return true;
    id<MTLBuffer> src = (__bridge id<MTLBuffer>)buffer;
    if (!src || size < 8 || (offset & 3) || offset > src.length || 8 > src.length - offset) return false;
    impl->scheduler.EndWithBarrier();
    impl->scheduler.BlitEncoder("Snapshot game velocity packing");
    [impl->scheduler.GetImpl().blit copyFromBuffer:src sourceOffset:offset toBuffer:impl->packing destinationOffset:0 size:8];
    impl->scheduler.EndWithBarrier();
    impl->captured = true;
    return true;
}
bool VelocityMirror::Prepare(const VideoCore::RenderState& guest, u32 w, u32 h, VideoCore::RenderState& out, u64 source_uid) {
    if (!Enabled() || !impl->captured || !w || !h || w > 8192 || h > 8192 ||
        !guest.width || !guest.height || guest.num_layers != 1 || guest.num_color_attachments != 1 ||
        !guest.depth_stencil_attachment.has_depth) return false;
    id<MTLTexture> ds = (__bridge id<MTLTexture>)reinterpret_cast<void*>(guest.depth_stencil_attachment.image_view);
    id<MTLTexture> cb = (__bridge id<MTLTexture>)reinterpret_cast<void*>(guest.color_attachments[0].image_view);
    if (!ds || !cb || cb.pixelFormat != MTLPixelFormatRGBA8Unorm ||
        ds.textureType != MTLTextureType2D || cb.textureType != MTLTextureType2D ||
        ds.sampleCount != 1 || cb.sampleCount != 1 ||
        cb.width < guest.width || cb.height < guest.height ||
        ds.width < guest.width || ds.height < guest.height ||
        (ds.pixelFormat != MTLPixelFormatDepth32Float_Stencil8 && ds.pixelFormat != MTLPixelFormatDepth16Unorm) ||
        guest.depth_stencil_attachment.has_stencil != (ds.pixelFormat == MTLPixelFormatDepth32Float_Stencil8)) return false;
    // Bound only this small auxiliary allocation, never shrink the shared caches. At 4K
    // D32S8 the mirror can approach 128 MiB; larger configurations use camera motion.
    const u64 depth_bytes = ds.pixelFormat == MTLPixelFormatDepth16Unorm ? 2 : 8;
    if (u64(w) * h * (8 + depth_bytes) + u64(guest.width) * guest.height * depth_bytes > 128ull * 1024 * 1024)
        return false;
    auto& s = impl->scheduler;
    if (!impl->color || impl->color.width != w || impl->color.height != h || impl->depth.pixelFormat != ds.pixelFormat) {
        s.FlushRenderTail();
        s.EndWithBarrier();
        impl->Retire(impl->color); impl->Retire(impl->depth);
        impl->color = impl->Texture(MTLPixelFormatRGBA16Float, w, h, @"Game velocity mirror");
        impl->depth = impl->Texture(ds.pixelFormat, w, h, @"Game velocity private depth");
        impl->drawn = false; impl->seeded = nullptr;
        if (!impl->color || !impl->depth) { impl->Fail(); return false; }
    }
    if (impl->seeded != (__bridge const void*)ds || impl->seeded_uid != source_uid ||
        impl->snapshot.width != guest.width || impl->snapshot.height != guest.height) {
        s.FlushRenderTail();
        s.EndWithBarrier();
        if (!impl->snapshot || impl->snapshot.width != guest.width || impl->snapshot.height != guest.height || impl->snapshot.pixelFormat != ds.pixelFormat) {
            impl->Retire(impl->snapshot);
            impl->snapshot = impl->Texture(ds.pixelFormat, guest.width, guest.height, @"Velocity depth snapshot");
            if (!impl->snapshot) { impl->Fail(); return false; }
        }
        // Cache textures may lack PixelFormatView. Blit to our small source-sized snapshot,
        // then reuse the depth+stencil nearest scaler without promoting any cached image.
        s.BlitEncoder("Snapshot velocity depth and stencil");
        [s.GetImpl().blit copyFromTexture:ds sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
                              sourceSize:MTLSizeMake(guest.width, guest.height, 1) toTexture:impl->snapshot
                        destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
        s.EndWithBarrier();
        impl->targets.CopyTextures((__bridge const void*)impl->snapshot, (__bridge const void*)impl->depth, "Velocity depth rescale");
        s.EndWithBarrier();
        impl->seeded = (__bridge const void*)ds;
        impl->seeded_uid = source_uid;
        ++impl->seeds;
    }
    if (!impl->draws) std::printf("Metal velocity resources: %.2f MiB (mirror, private depth, source snapshot)\n",
        double(impl->color.allocatedSize + impl->depth.allocatedSize + impl->snapshot.allocatedSize) / (1024 * 1024));
    out = guest;
    out.width = w; out.height = h;
    out.depth_stencil_attachment.image_view = reinterpret_cast<u64>((__bridge void*)impl->depth);
    out.depth_stencil_attachment.depth_clear = out.depth_stencil_attachment.stencil_clear = false;
    out.color_attachments[0].image_view = reinterpret_cast<u64>((__bridge void*)impl->color);
    out.color_attachments[0].is_clear = impl->drawn ? 0 : 1;
    out.color_attachments[0].clear_value = {std::bit_cast<u32>(.5f), std::bit_cast<u32>(.5f), 0, 0};
    return true;
}
void VelocityMirror::InvalidateDepthSeed() {
    impl->scheduler.FlushRenderTail();
    impl->seeded = nullptr;
}
void VelocityMirror::Drawn() {
    impl->drawn = true;
    if (++impl->draws == 1) std::printf("Metal object motion: first game velocity draw mirrored at %lux%lu\n", impl->color.width, impl->color.height);
}
RenderList& VelocityMirror::Record(const VideoCore::RenderState& state) {
    if (!impl->pending) {
        impl->pending_state = state;
        impl->commands.BeginRecording();
    }
    return impl->commands;
}
void VelocityMirror::QueuedDraw() {
    ++impl->pending;
    Drawn();
    impl->scheduler.RequestRenderTail();
    if (impl->pending >= MaxBatchDraws) impl->scheduler.FlushRenderTail();
}
u32 VelocityMirror::PendingDraws() const { return impl->pending; }
u64 VelocityMirror::BatchCount() const { return impl->batches; }
void VelocityMirror::Flush() {
    if (!impl->pending) return;
    const auto state = impl->pending_state;
    auto* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = (__bridge id<MTLTexture>)reinterpret_cast<void*>(state.color_attachments[0].image_view);
    pass.colorAttachments[0].loadAction = state.color_attachments[0].is_clear ? MTLLoadActionClear : MTLLoadActionLoad;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(.5,.5,0,0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.depthAttachment.texture = (__bridge id<MTLTexture>)reinterpret_cast<void*>(state.depth_stencil_attachment.image_view);
    pass.depthAttachment.loadAction = MTLLoadActionLoad;
    pass.depthAttachment.storeAction = MTLStoreActionStore;
    if (state.depth_stencil_attachment.has_stencil) {
        pass.stencilAttachment.texture = pass.depthAttachment.texture;
        pass.stencilAttachment.loadAction = MTLLoadActionLoad;
        pass.stencilAttachment.storeAction = MTLStoreActionStore;
    }
    impl->pending = 0; // reentrant EndEncoder calls must not replay this list twice
    auto& s = impl->scheduler;
    s.HostRenderEncoder((__bridge const void*)pass); // host: never a guest visibility query
    s.LabelEncoder("Batched game velocity mirror");
    impl->commands.Replay((__bridge void*)s.GetImpl().render);
    impl->commands.End();
    s.EndWithBarrier();
    ++impl->batches;
}
void VelocityMirror::Merge(const void* texture, u32 w, u32 h) {
    impl->scheduler.FlushRenderTail();
    if (!Enabled() || !impl->drawn || !impl->captured || impl->color.width != w || impl->color.height != h) return;
    id<MTLTexture> motion = (__bridge id<MTLTexture>)texture;
    if (!motion || motion.width != w || motion.height != h || motion.pixelFormat != MTLPixelFormatRG16Float) return;
    auto& s = impl->scheduler;
    s.EndWithBarrier();
    s.ComputeEncoder("Merge game object velocity into camera motion", true);
    auto encoder = s.GetImpl().compute;
    [encoder setComputePipelineState:impl->pipeline];
    [encoder setTexture:impl->color atIndex:0];
    [encoder setTexture:motion atIndex:1];
    [encoder setBuffer:impl->packing offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(w,h,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];
    s.EndWithBarrier();
    if (++impl->merged == 1) std::printf("Metal object motion: first velocity frame merged for MetalFX\n");
}
} // namespace Metal
