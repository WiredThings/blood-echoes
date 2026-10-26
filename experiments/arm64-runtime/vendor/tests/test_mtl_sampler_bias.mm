// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the sampler LOD bias of the Metal backend (renderer_metal/mtl_runtime MakeLimits
// and CreateSampler): the guest S# bias and the extra bias of the upscaler's scene mip bias both
// reach the MTLSamplerState (MTLSamplerDescriptor.lodBias, macOS 26). A 64x64 texture with seven
// mips, each a solid colour, is drawn by a full-screen quad into a 16x16 target (implicit LOD 2)
// with a nearest sampler read in a fragment shader, and the colour of every pixel is checked
// against the mip the bias must select. The bias is clamped with BackendLimits the way
// texture_cache/sampler.cpp does: the test drives the real path, VideoCore::Sampler ->
// Runtime::CreateSampler. Below macOS 26 the limit is 0 and the expectation is mip 2 in every
// case (the bias is dropped, documented). Needs a Metal device, no window or game. Run it with
// MTL_DEBUG_LAYER=1 as well.
//
//   mtl-sampler-bias-test
//
// Exit 0 when every case passed.
#import <Metal/Metal.h>

#include <setjmp.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "common/slot_vector.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/texture_cache/sampler.h"

// The runtime services libbbgpu takes from bb-probe; this test has no guest process.
extern "C" {
    __thread sigjmp_buf* runtime_fault_recover = nullptr;
    uint64_t runtime_disabled_optimizations = 0;
    uint64_t runtime_experiment_bits = 0;
    uint64_t runtime_tsc_frequency() { return 1000000000; }
    int runtime_file_translate(const char*, char*, size_t) { std::abort(); }
    uint64_t runtime_memory_clamp(uintptr_t, uint64_t) { std::abort(); }
    uint64_t runtime_process_time_us() {
        return std::chrono::duration_cast<std::chrono::microseconds>(
                   std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    int runtime_memory_region(uintptr_t, uintptr_t*, uintptr_t*, int*) { std::abort(); }
    void runtime_memory_set_gpu_hooks(void (*)(uintptr_t, uint64_t), void (*)(uintptr_t, uint64_t),
                                      void (*)(uintptr_t, uint64_t)) { std::abort(); }
    void runtime_thread_attach_host(const char*) {}
    void runtime_memory_gpu_protect(uintptr_t, uint64_t, int, int) { std::abort(); }
    void runtime_restart() { std::abort(); }
    uint64_t runtime_process_time_counter() { return runtime_process_time_us() * 1000; }
    int32_t* runtime_errno() { std::abort(); }
    int runtime_memory_write_backing(uintptr_t, const void*, uint64_t) { std::abort(); }
    void runtime_memory_read_backing(uintptr_t, void*, uint64_t) { std::abort(); }
    const uint64_t* runtime_memory_generation() {
        static const uint64_t generation = 0;
        return &generation;
    }
    void runtime_memory_note_write(uintptr_t, uint64_t) { std::abort(); }
    void runtime_memory_set_note_write_hook(void (*)(uintptr_t, uint64_t)) {}
    void runtime_memory_set_cpu_write_hook(void (*)(uintptr_t, uint64_t)) {}
    void runtime_memory_set_write_watch(uintptr_t, uint64_t, int) { std::abort(); }
    void runtime_memory_set_guest_chunk_allocator(int (*)(uint64_t, uint64_t)) {}
    void runtime_memory_set_guest_chunk_whole(int) {}
    int runtime_memory_vma_info(uintptr_t, int*, int*, uintptr_t*) { std::abort(); }
    void runtime_memory_trap(uintptr_t, uint64_t, unsigned, int) { std::abort(); }
    unsigned runtime_memory_trap_reasons(uintptr_t) { std::abort(); }
    int runtime_memory_direct_phys(uintptr_t, uint64_t*, uintptr_t*) { std::abort(); }
    void* runtime_memory_backing_pointer(uint64_t) { return nullptr; }
    uintptr_t runtime_memory_resolve(const char*) { std::abort(); }
    void runtime_wait_report(double) {}
    void runtime_sleep_stats(uint64_t* calls, uint64_t* ns) {
        *calls = 0;
        *ns = 0;
    }
    uint64_t runtime_heap_growths() { return 0; }
}

using namespace VideoCore;
using Metal::Runtime;
using Metal::Scheduler;

static constexpr u32 TextureSize = 64;
static constexpr u32 MipCount = 7;
static constexpr u32 TargetSize = 16;

struct Fixture {
    Metal::Device device;
    Scheduler scheduler{device, "test-sampler-bias"};
    Runtime runtime{device, scheduler};
    id<MTLDevice> mtl = device.GetImpl().device;

    Fixture() {
        scheduler.SetErrorHandler([](const std::string& report) {
            std::printf("  FAIL command buffer error: %s\n", report.c_str());
            std::exit(1);
        });
    }
};

static NSString* const shader_source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct VOut { float4 position [[position]]; float2 uv; };\n"
    "vertex VOut quad_vs(uint vid [[vertex_id]]) {\n"
    "    const float2 corners[6] = {float2(0, 0), float2(1, 0), float2(0, 1),\n"
    "                               float2(1, 0), float2(1, 1), float2(0, 1)};\n"
    "    VOut out;\n"
    "    out.uv = corners[vid];\n"
    "    out.position = float4(corners[vid] * 2.0 - 1.0, 0.0, 1.0);\n"
    "    return out;\n"
    "}\n"
    "fragment float4 quad_fs(VOut in [[stage_in]], texture2d<float> tex [[texture(0)]],\n"
    "                        sampler smp [[sampler(0)]]) {\n"
    "    return tex.sample(smp, in.uv);\n"
    "}\n";

/// The solid colour of mip `level` (distinct in every channel).
static void MipColor(u32 level, u8 out[4]) {
    out[0] = u8(20 + level * 30);
    out[1] = u8(255 - level * 30);
    out[2] = u8(5 + level * 10);
    out[3] = 255;
}

static id<MTLTexture> MakeMipTexture(id<MTLDevice> device) {
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:TextureSize
                                                          height:TextureSize
                                                       mipmapped:YES];
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    if (!texture || texture.mipmapLevelCount != MipCount) {
        std::printf("FAIL texture (mips %lu)\n",
                    texture ? (unsigned long)texture.mipmapLevelCount : 0ul);
        std::exit(2);
    }
    for (u32 level = 0; level < MipCount; ++level) {
        const u32 size = std::max(TextureSize >> level, 1u);
        std::vector<u8> texels(size * size * 4);
        for (u32 i = 0; i < size * size; ++i) {
            MipColor(level, &texels[i * 4]);
        }
        [texture replaceRegion:MTLRegionMake2D(0, 0, size, size)
                   mipmapLevel:level
                     withBytes:texels.data()
                   bytesPerRow:size * 4];
    }
    return texture;
}

struct Case {
    const char* name;
    float guest_bias; ///< the S# bias
    float extra_bias; ///< the upscaler's scene mip bias (Sampler's extra_lod_bias)
    int expected_mip; ///< with LOD bias support (macOS 26)
};

int main() {
    int cases = 0;
    int failed = 0;
    @autoreleasepool {
        Fixture f;
        const BackendLimits& limits = f.runtime.Limits();
        bool supported = false;
        if (@available(macOS 26.0, *)) {
            supported = true;
        }
        std::printf("sampler LOD bias %s, limit %f\n", supported ? "supported" : "unsupported",
                    limits.max_sampler_lod_bias);
        if (supported && limits.max_sampler_lod_bias != 15.984375f) {
            std::printf("FAIL limit on macOS 26 must be 15.984375\n");
            ++failed;
        }
        if (!supported && limits.max_sampler_lod_bias != 0.0f) {
            std::printf("FAIL limit below macOS 26 must be 0\n");
            ++failed;
        }

        id<MTLTexture> texture = MakeMipTexture(f.mtl);
        MTLTextureDescriptor* target_descriptor = [MTLTextureDescriptor
            texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                         width:TargetSize
                                        height:TargetSize
                                     mipmapped:NO];
        target_descriptor.usage = MTLTextureUsageRenderTarget;
        target_descriptor.storageMode = MTLStorageModeShared;
        id<MTLTexture> target = [f.mtl newTextureWithDescriptor:target_descriptor];

        NSError* error = nil;
        id<MTLLibrary> library = [f.mtl newLibraryWithSource:shader_source options:nil error:&error];
        if (!library) {
            std::printf("shader library: %s\n", error.localizedDescription.UTF8String);
            return 2;
        }
        MTLRenderPipelineDescriptor* pipeline_descriptor = [MTLRenderPipelineDescriptor new];
        pipeline_descriptor.vertexFunction = [library newFunctionWithName:@"quad_vs"];
        pipeline_descriptor.fragmentFunction = [library newFunctionWithName:@"quad_fs"];
        pipeline_descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        id<MTLRenderPipelineState> pipeline =
            [f.mtl newRenderPipelineStateWithDescriptor:pipeline_descriptor error:&error];
        if (!pipeline) {
            std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
            return 2;
        }
        id<MTLCommandQueue> queue = [f.mtl newCommandQueue];

        const Case table[] = {
            {"bias 0", 0.0f, 0.0f, 2},
            {"scene bias -1", 0.0f, -1.0f, 1},
            {"scene bias +1", 0.0f, 1.0f, 3},
            {"scene bias -2", 0.0f, -2.0f, 0},
            {"guest bias -1", -1.0f, 0.0f, 1},
            {"guest +1 and scene -1", 1.0f, -1.0f, 2},
            {"bias +20 (clamped, LOD max 6)", 0.0f, 20.0f, 6},
            {"bias -20 (clamped, LOD min 0)", 0.0f, -20.0f, 0},
        };
        for (const Case& c : table) {
            ++cases;
            // A guest sampler as the game's S# would be: point filters, point mips, the LOD range
            // 0..15, and the 14 bit signed bias in 1/256 steps.
            AmdGpu::Sampler guest{};
            guest.raw0 = u64(15 * 256) << 44;
            const u64 bias_bits = u64(s64(c.guest_bias * 256.0f)) & 0x3fff;
            guest.raw1 = bias_bits | (u64(AmdGpu::Filter::Point) << 20) |
                         (u64(AmdGpu::Filter::Point) << 22) |
                         (u64(AmdGpu::MipFilter::Point) << 26);
            // sampler.cpp clamps guest + extra to the backend's limit
            Sampler sampler{f.runtime, guest, AmdGpu::BorderColorBuffer{}, false, c.extra_bias};
            if (!sampler.native) {
                std::printf("FAIL %s: no sampler\n", c.name);
                ++failed;
                continue;
            }

            const int expected = supported ? c.expected_mip : 2;
            u8 want[4];
            MipColor(u32(expected), want);

            std::vector<u8> pixels(TargetSize * TargetSize * 4, 0);
            @autoreleasepool {
                MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
                pass.colorAttachments[0].texture = target;
                pass.colorAttachments[0].loadAction = MTLLoadActionClear;
                pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
                pass.colorAttachments[0].storeAction = MTLStoreActionStore;
                id<MTLCommandBuffer> cb = [queue commandBuffer];
                id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:pass];
                [enc setRenderPipelineState:pipeline];
                [enc setFragmentTexture:texture atIndex:0];
                [enc setFragmentSamplerState:(__bridge id<MTLSamplerState>)sampler.native
                                     atIndex:0];
                [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:6];
                [enc endEncoding];
                [cb commit];
                [cb waitUntilCompleted];
                if (cb.status != MTLCommandBufferStatusCompleted) {
                    std::printf("FAIL %s: command buffer status %ld\n", c.name, (long)cb.status);
                    ++failed;
                    continue;
                }
            }
            [target getBytes:pixels.data()
                 bytesPerRow:TargetSize * 4
                  fromRegion:MTLRegionMake2D(0, 0, TargetSize, TargetSize)
                 mipmapLevel:0];
            u32 bad = 0;
            for (u32 i = 0; i < TargetSize * TargetSize; ++i) {
                if (std::memcmp(&pixels[i * 4], want, 4) != 0) {
                    ++bad;
                }
            }
            std::printf("  %-32s want mip %d (%3u %3u %3u), got (%3u %3u %3u) %s\n", c.name,
                        expected, want[0], want[1], want[2], pixels[0], pixels[1], pixels[2],
                        bad ? "FAIL" : "ok");
            if (bad) {
                std::printf("    %u of %u pixels differ\n", bad, TargetSize * TargetSize);
                ++failed;
            }
        }
        f.runtime.Finish();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (f.scheduler.HasFailed()) {
            std::printf("FAIL scheduler failed\n");
            ++failed;
        }
    }
    std::printf("%d cases, %d failed\n", cases, failed);
    return failed ? 1 : 0;
}
