#include "video_core/renderer_metal/mtl_local_library.h"
// SPDX-License-Identifier: GPL-3.0-or-later
// The scale, counter sharing and max-of-three history follow Awakenchan's OcclusionModel
// (BloodBorneButPc 4f93643e); Metal counting, GPU snapshots and retirement are implemented here.
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include <algorithm>
#include <array>
#include <cstring>
#include <cstdlib>
#include <format>
#include "video_core/frame_capture.h"
#include "bbport_toggles.h"
#include "common/assert.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/renderer_metal/mtl_occlusion.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"

namespace Metal {
namespace {
constexpr u32 Slots = 4096;
constexpr u32 Histories = 4096;
// Each history is {key, begin, previous[2], has_begin, initialized}: 6 ulongs.
constexpr u64 HistoryBytes = Histories * 6 * sizeof(u64);
struct Push { u32 count, poison, pairs, end; u64 key; };
static_assert(sizeof(Push) == 24);
NSString* const source = @R"MSL(
#include <metal_stdlib>
using namespace metal;
struct Push { uint count, poison, pairs, end; ulong key; };
struct History { ulong key, begin, p0, p1, has_begin, initialized; };
kernel void snapshot(device ulong* counts [[buffer(0)]],
                     device ulong* total [[buffer(1)]],
                     device History* history [[buffer(2)]],
                     device ulong* destination [[buffer(3)]],
                     constant Push& p [[buffer(4)]],
                     device const uint* scales [[buffer(5)]]) {
    const ulong unmeasured = 0x2fffffful;
    const ulong valid = 1ul << 63;
    ulong sum = total[0];
    for (uint i = 0; i < p.count; ++i) {
        const ulong n = counts[i];
        // Fixed point scale back to PS4 sample density; zero stays zero, positive rounds up.
        sum += max(n, (n * ulong(scales[i]) + 65535ul) >> 16);
        counts[i] = 0; // no reuse until this dispatch and its fence precede the next pass
    }
    if (p.poison) sum += unmeasured;
    ulong hash = (p.key >> 4) * 0x9e3779b97f4a7c15ul;
    device History& h = history[(hash ^ (hash >> 32)) & 4095ul];
    if (!h.initialized || h.key != p.key) {
        h.key = p.key; h.p0 = 1; h.p1 = 1; h.has_begin = 0; h.initialized = 1;
    }
    if (p.end) {
        if (h.has_begin) {
            const ulong measured = sum - h.begin;
            const ulong report = max(measured, max(h.p0, h.p1));
            sum += report - measured;
            h.p1 = h.p0; h.p0 = measured;
            h.has_begin = 0;
        } else {
            sum += unmeasured; // a collision or unmatched end is never an occluded result
        }
    } else {
        h.begin = sum; h.has_begin = 1;
    }
    for (uint i = 0; i < p.pairs; ++i) {
        destination[2 * i] = (sum / p.pairs + (i < sum % p.pairs ? 1ul : 0ul)) | valid;
    }
    total[0] = sum;
}
)MSL";
}

struct OcclusionQueries::Impl {
    Device& device;
    Scheduler& scheduler;
    Runtime& runtime;
    VideoCore::BufferCache* cache;
    id<MTLBuffer> results, total, history;
    id<MTLComputePipelineState> pipeline;
    std::array<u32, Slots> scales{};
    u32 count = 0;
    bool poison = false;
    u64 drop_count = 0;
    std::pair<void*, u64> Begin(const void* descriptor) {
        if (count == Slots) {
            poison = true; // do not overwrite unresolved results or block to find a free slot
            return {};
        }
        auto* pass = (__bridge MTLRenderPassDescriptor*)descriptor;
        id<MTLTexture> target = pass.depthAttachment.texture ?: pass.colorAttachments[0].texture;
        const u64 pixels = target ? target.width * target.height : 0;
        const u64 native = 1920ull * 1080;
        scales[count] = pixels && pixels < native ? u32(std::min<u64>((native << 16) / pixels, 0xffffffff)) : 65536;
        if (!target) poison = true;
        return {(__bridge void*)results, u64(count++) * sizeof(u64)};
    }
    static u64 Drops() {
        u64 n = 0;
        for (const auto& drop : BbStats::draw_drops) n += drop.load(std::memory_order_relaxed);
        return n;
    }
};

OcclusionQueries::OcclusionQueries(Device& device, Scheduler& scheduler, Runtime& runtime,
                                   VideoCore::BufferCache* cache)
    : impl{std::make_unique<Impl>(device, scheduler, runtime, cache)} {
    auto& d = device.GetImpl();
    NSError* error = nil;
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.mathMode = MTLMathModeSafe;
    id<MTLLibrary> library = Metal::LocalLibrary::Build(d.device, source, options, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"snapshot"}); });
    ASSERT_MSG(library, "Metal occlusion resolve compile: {}", error.localizedDescription.UTF8String);
    impl->pipeline = [d.device newComputePipelineStateWithFunction:[library newFunctionWithName:@"snapshot"] error:&error];
    ASSERT_MSG(impl->pipeline, "Metal occlusion pipeline: {}", error.localizedDescription.UTF8String);
    impl->results = [d.device newBufferWithLength:Slots * sizeof(u64) options:MTLResourceStorageModeShared];
    impl->total = [d.device newBufferWithLength:sizeof(u64) options:MTLResourceStorageModeShared];
    impl->history = [d.device newBufferWithLength:HistoryBytes options:MTLResourceStorageModeShared];
    ASSERT_MSG(impl->results && impl->total && impl->history, "Metal occlusion buffer allocation failed");
    for (id<MTLBuffer> buffer in @[impl->results, impl->total, impl->history]) {
        std::memset(buffer.contents, 0, buffer.length);
        MemoryStats::Track((__bridge const void*)buffer, MemoryStats::Kind::Occlusion);
        d.AddResident(buffer);
    }
    impl->drop_count = Impl::Drops();
    scheduler.SetVisibilityQuery([this](const void* descriptor) { return impl->Begin(descriptor); },
        Scheduler::VisibilityMergePolicy::NativeDensity);
    std::printf("Metal occlusion: counting visibility results, GPU-ordered snapshots, max-of-3 history; 224 KiB fixed buffers\n");
}

OcclusionQueries::~OcclusionQueries() {
    impl->scheduler.SetVisibilityQuery({});
    auto* device = &impl->device.GetImpl();
    NSArray* buffers = @[impl->results, impl->total, impl->history];
    id<MTLComputePipelineState> pipeline = impl->pipeline;
    for (id<MTLBuffer> buffer in buffers) MemoryStats::Retire((__bridge const void*)buffer);
    impl->scheduler.DeferOperation([device, buffers, pipeline] {
        for (id<MTLBuffer> buffer in buffers) device->RemoveResident(buffer);
        (void)pipeline; // keep it alive until the command buffer's completed handler, too
    });
    impl->scheduler.Finish();
}

void OcclusionQueries::MarkUnmeasured() {
    impl->scheduler.EndWithBarrier(); // also joins the replay thread, which owns count/poison
    impl->poison = true;
}

void OcclusionQueries::Event(VAddr address, u32 pairs) {
    ASSERT_MSG(impl->cache && pairs > 0 && pairs <= 16 && (address & 7) == 0,
               "Invalid Metal occlusion event");
    auto& cache = *impl->cache;
    const bool previous = cache.force_writes_in_place;
    cache.force_writes_in_place = true;
    const auto [buffer, offset] = cache.ObtainBuffer(address, (pairs - 1) * 16 + 8, true);
    cache.force_writes_in_place = previous;
    Snapshot(buffer, offset, address, pairs);
}

void OcclusionQueries::Snapshot(const VideoCore::Buffer* destination, u64 offset,
                                VAddr address, u32 pairs) {
    ASSERT_MSG(pairs > 0 && pairs <= 16 && (offset & 7) == 0 && (address & 7) == 0,
               "Unaligned or oversized Metal occlusion snapshot");
    auto& s = impl->scheduler;
    static const bool chained = [] {
        const char* flag = std::getenv("BB_MTL_QUERY_COMPUTE_CHAIN");
        return flag && flag[0] == '1';
    }();
    if (chained) s.BeginOrderedHostCompute("Occlusion snapshot");
    else s.EndWithBarrier(); // join replay before reading count/scales
    const u64 dropped = Impl::Drops();
    const Push push{impl->count, u32(impl->poison || dropped != impl->drop_count), pairs,
                    u32((address & 15) == 8), address & ~15ull};
    impl->drop_count = dropped;
    const u64 bytes = std::max<u64>(sizeof(u32), impl->count * sizeof(u32));
    auto scales = impl->runtime.StagingRequest(bytes, VideoCore::MemoryType::HostUncached, 16);
    std::memcpy(scales.mapped, impl->scales.data(), bytes);
    if (!chained) {
        s.MemoryBarrier();
        s.ComputeEncoder("Occlusion snapshot", true);
    }
    id<MTLComputeCommandEncoder> encoder = s.GetImpl().compute;
    [encoder setComputePipelineState:impl->pipeline];
    [encoder setBuffer:impl->results offset:0 atIndex:0];
    [encoder setBuffer:impl->total offset:0 atIndex:1];
    [encoder setBuffer:impl->history offset:0 atIndex:2];
    [encoder setBuffer:NativeBuffer(*destination) offset:offset atIndex:3];
    [encoder setBytes:&push length:sizeof(push) atIndex:4];
    [encoder setBuffer:NativeBuffer(*scales.buffer) offset:scales.offset atIndex:5];
    [encoder dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    // Snapshot owns the compute ordering below. It bypasses Runtime::AccessBuffer,
    // so notify the source-copy version separately without duplicating its fences.
    impl->runtime.NoteOrderedGuestBufferWrite();
    if (VideoCore::FrameCapture::NativeRecording()) {
        // The query resolver writes one u64 every 16 bytes. Record its enclosing
        // guest/native interval conservatively; the snapshot below preserves the
        // actual pair count, destination and strided-write geometry.
        VideoCore::FrameCapture::NativeBufferUse(uintptr_t(destination),
            uintptr_t(destination->buffer.native),offset,(pairs-1)*16+8,address,
            7,0,true,"occlusion_snapshot");
        VideoCore::FrameCapture::Native(std::format(
            "{{\"op\":\"occlusion_snapshot\",\"queue\":{},\"count\":{},\"pairs\":{},\"end\":{},\"poison\":{},\"chained\":{},\"address\":{},\"native\":{},\"offset\":{},\"stride\":16,\"write_bytes\":8}}",
            (uintptr_t)&s, push.count, push.pairs, push.end, push.poison, chained?"true":"false",
            address,uintptr_t(destination->buffer.native),offset));
    }
    // Both paths preserve counter reset, history and guest-write ordering. The chained path
    // keeps a serial compute encoder for a following tiny clear/query; future render/blit
    // encoders still wait for its fence. No CPU reset or query-result readback is introduced.
    if (chained) s.OrderAfterHostCompute();
    else s.EndWithBarrier();
    impl->count = 0;
    impl->poison = false;
    ++events;
}
} // namespace Metal
