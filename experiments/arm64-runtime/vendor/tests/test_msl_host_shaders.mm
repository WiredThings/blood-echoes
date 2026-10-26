// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the host shaders' MSL (docs/METAL_BACKEND_PLAN.md D11): every generated header of
// build/include/video_core/host_shaders_msl/ is compiled with newLibraryWithSource (the options of
// renderer_metal/mtl_shader.mm), its entry function is created (with the function constants at
// their defaults) and, for compute shaders, a compute pipeline. Prints a summary; exit 1 when
// anything fails. Needs a Metal device only, no window or game.
//
//   msl-host-shaders-test
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>

#include "video_core/renderer_metal/mtl_shader.h"

#include "video_core/host_shaders_msl/color_to_ms_depth_frag_msl.h"
#include "video_core/host_shaders_msl/ms_image_blit_frag_msl.h"
#include "video_core/host_shaders_msl/ms_image_blit_msaa_frag_msl.h"
#include "video_core/host_shaders_msl/fault_buffer_process_comp_msl.h"
#include "video_core/host_shaders_msl/fs_tri_vert_msl.h"
#include "video_core/host_shaders_msl/fsr_easu_comp_msl.h"
#include "video_core/host_shaders_msl/fsr_rcas_comp_msl.h"
#include "video_core/host_shaders_msl/post_process_frag_msl.h"
#include "video_core/host_shaders_msl/camera_motion_comp_msl.h"
#include "video_core/host_shaders_msl/upscale_merge_comp_msl.h"
#include "video_core/host_shaders_msl/upscale_reactive_comp_msl.h"
#include "video_core/host_shaders_msl/taa_comp_msl.h"
#include "video_core/host_shaders_msl/taa_sharpen_comp_msl.h"
#include "video_core/host_shaders_msl/taa_sharpen_ldr_comp_msl.h"
#include "video_core/host_shaders_msl/buffer_multi_copy_comp_msl.h"
#include "video_core/host_shaders_msl/occlusion_resolve_comp_msl.h"
#include "video_core/host_shaders_msl/gpu_timestamp_comp_msl.h"
#include "video_core/host_shaders_msl/indirect_guard_comp_msl.h"
#include "video_core/host_shaders_msl/depth_resample_frag_msl.h"
#include "video_core/host_shaders_msl/depth_stencil_resample_frag_msl.h"
#include "video_core/host_shaders_msl/depth_stencil_bits_frag_msl.h"
#include "video_core/host_shaders_msl/camera_motion_debug_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_128_comp_msl.h"

namespace H = Metal::HostShaders;

// The generated layout must stay the one of the game shaders (mtl_shader.h).
#define CHECK_KIND(k) \
    static_assert(static_cast<int>(H::Binding::Kind::k) == \
                  static_cast<int>(Metal::ShaderBinding::Kind::k))
CHECK_KIND(Buffer);
CHECK_KIND(Texture);
CHECK_KIND(StorageTexture);
CHECK_KIND(Sampler);
CHECK_KIND(TexelBuffer);
CHECK_KIND(PushConstants);
#undef CHECK_KIND

#define HOST_SHADERS(X) \
    X(color_to_ms_depth_frag, COLOR_TO_MS_DEPTH_FRAG) \
    X(ms_image_blit_frag, MS_IMAGE_BLIT_FRAG) \
    X(ms_image_blit_msaa_frag, MS_IMAGE_BLIT_MSAA_FRAG) \
    X(fault_buffer_process_comp, FAULT_BUFFER_PROCESS_COMP) \
    X(fs_tri_vert, FS_TRI_VERT) \
    X(fsr_easu_comp, FSR_EASU_COMP) \
    X(fsr_rcas_comp, FSR_RCAS_COMP) \
    X(post_process_frag, POST_PROCESS_FRAG) \
    X(camera_motion_comp, CAMERA_MOTION_COMP) \
    X(upscale_merge_comp, UPSCALE_MERGE_COMP) \
    X(upscale_reactive_comp, UPSCALE_REACTIVE_COMP) \
    X(taa_comp, TAA_COMP) \
    X(taa_sharpen_comp, TAA_SHARPEN_COMP) \
    X(taa_sharpen_ldr_comp, TAA_SHARPEN_LDR_COMP) \
    X(buffer_multi_copy_comp, BUFFER_MULTI_COPY_COMP) \
    X(occlusion_resolve_comp, OCCLUSION_RESOLVE_COMP) \
    X(gpu_timestamp_comp, GPU_TIMESTAMP_COMP) \
    X(indirect_guard_comp, INDIRECT_GUARD_COMP) \
    X(depth_resample_frag, DEPTH_RESAMPLE_FRAG) \
    X(depth_stencil_resample_frag, DEPTH_STENCIL_RESAMPLE_FRAG) \
    X(depth_stencil_bits_frag, DEPTH_STENCIL_BITS_FRAG) \
    X(camera_motion_debug_comp, CAMERA_MOTION_DEBUG_COMP) \
    X(tiling_micro_8_comp, TILING_MICRO_8_COMP) \
    X(tiling_macro_8_comp, TILING_MACRO_8_COMP) \
    X(tiling_micro_16_comp, TILING_MICRO_16_COMP) \
    X(tiling_macro_16_comp, TILING_MACRO_16_COMP) \
    X(tiling_micro_32_comp, TILING_MICRO_32_COMP) \
    X(tiling_macro_32_comp, TILING_MACRO_32_COMP) \
    X(tiling_micro_64_comp, TILING_MICRO_64_COMP) \
    X(tiling_macro_64_comp, TILING_MACRO_64_COMP) \
    X(tiling_micro_96_comp, TILING_MICRO_96_COMP) \
    X(tiling_macro_96_comp, TILING_MACRO_96_COMP) \
    X(tiling_micro_128_comp, TILING_MICRO_128_COMP) \
    X(tiling_macro_128_comp, TILING_MACRO_128_COMP)

struct Entry {
    const char* name;
    const H::Shader* shader;
};
#define X(name, SYMBOL) {#name, &H::SYMBOL},
static const Entry kShaders[] = {HOST_SHADERS(X)};
#undef X

static const char* StageName(H::Stage stage) {
    return stage == H::Stage::Vertex ? "vert" : stage == H::Stage::Fragment ? "frag" : "comp";
}

static const char* SpecTypeName(H::SpecConstant::Type type) {
    switch (type) {
    case H::SpecConstant::Type::Bool:
        return "bool";
    case H::SpecConstant::Type::U32:
        return "u32";
    case H::SpecConstant::Type::I32:
        return "i32";
    case H::SpecConstant::Type::F32:
        return "f32";
    }
    return "?";
}

/// Creates the function; function constants stay unset (the MSL falls back to the SPIR-V default).
static id<MTLFunction> MakeFunction(id<MTLLibrary> library, const H::Shader& shader,
                                    std::string& error) {
    NSString* entry = [[NSString alloc] initWithBytes:shader.entry.data()
                                               length:shader.entry.size()
                                             encoding:NSUTF8StringEncoding];
    id<MTLFunction> function = nil;
    if (shader.spec_constants.empty()) {
        function = [library newFunctionWithName:entry];
        if (!function) {
            error = "function '" + std::string(shader.entry) + "' not found";
        }
    } else {
        NSError* ns_error = nil;
        MTLFunctionConstantValues* values = [MTLFunctionConstantValues new];
        function = [library newFunctionWithName:entry constantValues:values error:&ns_error];
        if (!function) {
            error = std::string("function with default constants: ") +
                    (ns_error ? ns_error.localizedDescription.UTF8String : "?");
        }
    }
    return function;
}

int main() {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            std::fprintf(stderr, "no Metal device\n");
            return 1;
        }
        MTLCompileOptions* options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_1;
        if (@available(macOS 15.0, *)) {
            options.mathMode = MTLMathModeSafe;
        } else {
            options.fastMathEnabled = NO;
        }
        int failures = 0, compute_pipelines = 0;
        size_t msl_bytes = 0;
        const auto start = std::chrono::steady_clock::now();
        for (const Entry& e : kShaders) {
            @autoreleasepool {
                const H::Shader& shader = *e.shader;
                std::string error, note;
                const auto t0 = std::chrono::steady_clock::now();
                msl_bytes += shader.msl.size();
                NSError* ns_error = nil;
                id<MTLLibrary> library = [device
                    newLibraryWithSource:[[NSString alloc] initWithBytes:shader.msl.data()
                                                                  length:shader.msl.size()
                                                                encoding:NSUTF8StringEncoding]
                                 options:options
                                   error:&ns_error];
                id<MTLFunction> function = nil;
                if (!library) {
                    error = std::string("library: ") +
                            (ns_error ? ns_error.localizedDescription.UTF8String : "?");
                } else {
                    function = MakeFunction(library, shader, error);
                }
                if (function && shader.stage == H::Stage::Compute) {
                    const uint32_t threads =
                        shader.local_size[0] * shader.local_size[1] * shader.local_size[2];
                    if (threads == 0) {
                        error = "compute shader without a workgroup size";
                    } else {
                        NSError* pso_error = nil;
                        id<MTLComputePipelineState> pso =
                            [device newComputePipelineStateWithFunction:function error:&pso_error];
                        if (!pso) {
                            error = std::string("compute pipeline: ") +
                                    (pso_error ? pso_error.localizedDescription.UTF8String : "?");
                        } else if (threads > pso.maxTotalThreadsPerThreadgroup) {
                            error = "workgroup larger than the pipeline's thread limit";
                        } else {
                            ++compute_pipelines;
                            note = " max_threads=" + std::to_string(pso.maxTotalThreadsPerThreadgroup);
                        }
                    }
                }
                if (error.empty() && shader.bindings.empty()) {
                    error = "no bindings (push constants entry missing)";
                }
                const double ms = std::chrono::duration<double, std::milli>(
                                      std::chrono::steady_clock::now() - t0)
                                      .count();
                std::printf("%-30s %s %-4s local=%ux%ux%u push=%uB bufs=%u tex=%u smp=%u spec=%zu%s %6.1f ms%s%s\n",
                            e.name, error.empty() ? "ok  " : "FAIL", StageName(shader.stage),
                            shader.local_size[0], shader.local_size[1], shader.local_size[2],
                            shader.push_constant_size, shader.num_buffers, shader.num_textures,
                            shader.num_samplers, shader.spec_constants.size(), note.c_str(), ms,
                            error.empty() ? "" : "  ", error.c_str());
                if (!error.empty()) {
                    ++failures;
                }
                if (!shader.spec_constants.empty()) {
                    std::printf("    spec constants:");
                    for (const H::SpecConstant& s : shader.spec_constants) {
                        std::printf(" %u:%s=%u", s.id, SpecTypeName(s.type), s.default_value);
                    }
                    std::printf("\n");
                }
            }
        }
        const double total = std::chrono::duration<double, std::milli>(
                                 std::chrono::steady_clock::now() - start)
                                 .count();
        const size_t count = sizeof(kShaders) / sizeof(kShaders[0]);
        std::printf("%zu host shaders, %d failed, %d compute pipelines, %zu bytes of MSL, %.0f ms\n",
                    count, failures, compute_pipelines, msl_bytes, total);
        return failures ? 1 : 0;
    }
}
