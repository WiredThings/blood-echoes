// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_spirv_pervertex (P3.1b): PerVertexKHR fragment inputs, which
// SPIRV-Cross cannot translate, are rewritten into ordinary inputs plus barycentric arithmetic.
//   (a) the four cached game shaders that declare such inputs compile through ShaderCompiler;
//   (b) GPU correctness: small fragment shaders (GLSL with `pervertexEXT`, compiled offline by
//       glslangValidator --target-env vulkan1.2, embedded below) read the three vertex values of
//       a perspective-projected triangle; the rendered colours must reproduce the vertex values
//       and the vertex order (v[k] is the k-th vertex of the draw), for both windings.
// Needs a Metal device, no window or game.
//
//   mtl-pervertex-test [<shader cache dir>] [--dump <dir>]
//
// The directory defaults to the clinic run's cache (~/Games/Bloodborne/bench/testuser/cache/
// CUSA03173, read only); --dump writes the rewritten SPIR-V of every case there (for spirv-val).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <span>
#include <string>
#include <vector>

#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_spirv_pervertex.h"

// common/assert.cpp pulls in the emulator; a failed ASSERT here just aborts.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

namespace fs = std::filesystem;
using Metal::CompileStatus;

static int g_failures = 0;

static void Fail(const char* what) {
    std::printf("FAIL %s\n", what);
    ++g_failures;
}

// ---- (b) fragment shaders: glslangValidator -V --target-env vulkan1.2 ---------------------------

// t1.frag (three floats, all vertices)
static const uint32_t kT1Spirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x0000001c, 0x00000000, 0x00020011, 0x00000001, 0x00020011,
    0x000014a4, 0x000a000a, 0x5f565053, 0x5f52484b, 0x67617266, 0x746e656d, 0x6168735f, 0x5f726564,
    0x79726162, 0x746e6563, 0x00636972, 0x0006000b, 0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e,
    0x00000000, 0x0003000e, 0x00000000, 0x00000001, 0x0007000f, 0x00000004, 0x00000004, 0x6e69616d,
    0x00000000, 0x00000009, 0x0000000e, 0x00030010, 0x00000004, 0x00000007, 0x00030003, 0x00000002,
    0x000001c2, 0x000a0004, 0x455f4c47, 0x665f5458, 0x6d676172, 0x5f746e65, 0x64616873, 0x625f7265,
    0x63797261, 0x72746e65, 0x00006369, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00040005,
    0x00000009, 0x6f6c6f63, 0x00000072, 0x00030005, 0x0000000e, 0x00000076, 0x00040047, 0x00000009,
    0x0000001e, 0x00000000, 0x00040047, 0x0000000e, 0x0000001e, 0x00000000, 0x00030047, 0x0000000e,
    0x000014a5, 0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002, 0x00030016, 0x00000006,
    0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000004, 0x00040020, 0x00000008, 0x00000003,
    0x00000007, 0x0004003b, 0x00000008, 0x00000009, 0x00000003, 0x00040015, 0x0000000a, 0x00000020,
    0x00000000, 0x0004002b, 0x0000000a, 0x0000000b, 0x00000003, 0x0004001c, 0x0000000c, 0x00000006,
    0x0000000b, 0x00040020, 0x0000000d, 0x00000001, 0x0000000c, 0x0004003b, 0x0000000d, 0x0000000e,
    0x00000001, 0x00040015, 0x0000000f, 0x00000020, 0x00000001, 0x0004002b, 0x0000000f, 0x00000010,
    0x00000000, 0x00040020, 0x00000011, 0x00000001, 0x00000006, 0x0004002b, 0x0000000f, 0x00000014,
    0x00000001, 0x0004002b, 0x0000000f, 0x00000017, 0x00000002, 0x0004002b, 0x00000006, 0x0000001a,
    0x3f800000, 0x00050036, 0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8, 0x00000005,
    0x00050041, 0x00000011, 0x00000012, 0x0000000e, 0x00000010, 0x0004003d, 0x00000006, 0x00000013,
    0x00000012, 0x00050041, 0x00000011, 0x00000015, 0x0000000e, 0x00000014, 0x0004003d, 0x00000006,
    0x00000016, 0x00000015, 0x00050041, 0x00000011, 0x00000018, 0x0000000e, 0x00000017, 0x0004003d,
    0x00000006, 0x00000019, 0x00000018, 0x00070050, 0x00000007, 0x0000001b, 0x00000013, 0x00000016,
    0x00000019, 0x0000001a, 0x0003003e, 0x00000009, 0x0000001b, 0x000100fd, 0x00010038,
};

// t2.frag (vec4, vertices 1 and 2)
static const uint32_t kT2Spirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x0000001a, 0x00000000, 0x00020011, 0x00000001, 0x00020011,
    0x000014a4, 0x000a000a, 0x5f565053, 0x5f52484b, 0x67617266, 0x746e656d, 0x6168735f, 0x5f726564,
    0x79726162, 0x746e6563, 0x00636972, 0x0006000b, 0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e,
    0x00000000, 0x0003000e, 0x00000000, 0x00000001, 0x0007000f, 0x00000004, 0x00000004, 0x6e69616d,
    0x00000000, 0x00000009, 0x0000000e, 0x00030010, 0x00000004, 0x00000007, 0x00030003, 0x00000002,
    0x000001c2, 0x000a0004, 0x455f4c47, 0x665f5458, 0x6d676172, 0x5f746e65, 0x64616873, 0x625f7265,
    0x63797261, 0x72746e65, 0x00006369, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00040005,
    0x00000009, 0x6f6c6f63, 0x00000072, 0x00030005, 0x0000000e, 0x00000077, 0x00040047, 0x00000009,
    0x0000001e, 0x00000000, 0x00040047, 0x0000000e, 0x0000001e, 0x00000000, 0x00030047, 0x0000000e,
    0x000014a5, 0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002, 0x00030016, 0x00000006,
    0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000004, 0x00040020, 0x00000008, 0x00000003,
    0x00000007, 0x0004003b, 0x00000008, 0x00000009, 0x00000003, 0x00040015, 0x0000000a, 0x00000020,
    0x00000000, 0x0004002b, 0x0000000a, 0x0000000b, 0x00000003, 0x0004001c, 0x0000000c, 0x00000007,
    0x0000000b, 0x00040020, 0x0000000d, 0x00000001, 0x0000000c, 0x0004003b, 0x0000000d, 0x0000000e,
    0x00000001, 0x00040015, 0x0000000f, 0x00000020, 0x00000001, 0x0004002b, 0x0000000f, 0x00000010,
    0x00000002, 0x00040020, 0x00000011, 0x00000001, 0x00000007, 0x0004002b, 0x0000000f, 0x00000014,
    0x00000001, 0x0004002b, 0x00000006, 0x00000017, 0x3f000000, 0x00050036, 0x00000002, 0x00000004,
    0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x00050041, 0x00000011, 0x00000012, 0x0000000e,
    0x00000010, 0x0004003d, 0x00000007, 0x00000013, 0x00000012, 0x00050041, 0x00000011, 0x00000015,
    0x0000000e, 0x00000014, 0x0004003d, 0x00000007, 0x00000016, 0x00000015, 0x0005008e, 0x00000007,
    0x00000018, 0x00000016, 0x00000017, 0x00050081, 0x00000007, 0x00000019, 0x00000013, 0x00000018,
    0x0003003e, 0x00000009, 0x00000019, 0x000100fd, 0x00010038,
};

// t3.frag (one float, vertex 0 only (flat))
static const uint32_t kT3Spirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x00000017, 0x00000000, 0x00020011, 0x00000001, 0x00020011,
    0x000014a4, 0x000a000a, 0x5f565053, 0x5f52484b, 0x67617266, 0x746e656d, 0x6168735f, 0x5f726564,
    0x79726162, 0x746e6563, 0x00636972, 0x0006000b, 0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e,
    0x00000000, 0x0003000e, 0x00000000, 0x00000001, 0x0007000f, 0x00000004, 0x00000004, 0x6e69616d,
    0x00000000, 0x00000009, 0x0000000e, 0x00030010, 0x00000004, 0x00000007, 0x00030003, 0x00000002,
    0x000001c2, 0x000a0004, 0x455f4c47, 0x665f5458, 0x6d676172, 0x5f746e65, 0x64616873, 0x625f7265,
    0x63797261, 0x72746e65, 0x00006369, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00040005,
    0x00000009, 0x6f6c6f63, 0x00000072, 0x00030005, 0x0000000e, 0x00000076, 0x00040047, 0x00000009,
    0x0000001e, 0x00000000, 0x00040047, 0x0000000e, 0x0000001e, 0x00000000, 0x00030047, 0x0000000e,
    0x000014a5, 0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002, 0x00030016, 0x00000006,
    0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000004, 0x00040020, 0x00000008, 0x00000003,
    0x00000007, 0x0004003b, 0x00000008, 0x00000009, 0x00000003, 0x00040015, 0x0000000a, 0x00000020,
    0x00000000, 0x0004002b, 0x0000000a, 0x0000000b, 0x00000003, 0x0004001c, 0x0000000c, 0x00000006,
    0x0000000b, 0x00040020, 0x0000000d, 0x00000001, 0x0000000c, 0x0004003b, 0x0000000d, 0x0000000e,
    0x00000001, 0x00040015, 0x0000000f, 0x00000020, 0x00000001, 0x0004002b, 0x0000000f, 0x00000010,
    0x00000000, 0x00040020, 0x00000011, 0x00000001, 0x00000006, 0x0004002b, 0x00000006, 0x00000014,
    0x00000000, 0x0004002b, 0x00000006, 0x00000015, 0x3f800000, 0x00050036, 0x00000002, 0x00000004,
    0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x00050041, 0x00000011, 0x00000012, 0x0000000e,
    0x00000010, 0x0004003d, 0x00000006, 0x00000013, 0x00000012, 0x00070050, 0x00000007, 0x00000016,
    0x00000013, 0x00000014, 0x00000014, 0x00000015, 0x0003003e, 0x00000009, 0x00000016, 0x000100fd,
    0x00010038,
};

// t4.frag (a: vertex 0 only (flat) and b: all vertices)
static const uint32_t kT4Spirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x0000001e, 0x00000000, 0x00020011, 0x00000001, 0x00020011,
    0x000014a4, 0x000a000a, 0x5f565053, 0x5f52484b, 0x67617266, 0x746e656d, 0x6168735f, 0x5f726564,
    0x79726162, 0x746e6563, 0x00636972, 0x0006000b, 0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e,
    0x00000000, 0x0003000e, 0x00000000, 0x00000001, 0x0008000f, 0x00000004, 0x00000004, 0x6e69616d,
    0x00000000, 0x00000009, 0x0000000e, 0x00000014, 0x00030010, 0x00000004, 0x00000007, 0x00030003,
    0x00000002, 0x000001c2, 0x000a0004, 0x455f4c47, 0x665f5458, 0x6d676172, 0x5f746e65, 0x64616873,
    0x625f7265, 0x63797261, 0x72746e65, 0x00006369, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000,
    0x00040005, 0x00000009, 0x6f6c6f63, 0x00000072, 0x00030005, 0x0000000e, 0x00000061, 0x00030005,
    0x00000014, 0x00000062, 0x00040047, 0x00000009, 0x0000001e, 0x00000000, 0x00040047, 0x0000000e,
    0x0000001e, 0x00000000, 0x00030047, 0x0000000e, 0x000014a5, 0x00040047, 0x00000014, 0x0000001e,
    0x00000001, 0x00030047, 0x00000014, 0x000014a5, 0x00020013, 0x00000002, 0x00030021, 0x00000003,
    0x00000002, 0x00030016, 0x00000006, 0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000004,
    0x00040020, 0x00000008, 0x00000003, 0x00000007, 0x0004003b, 0x00000008, 0x00000009, 0x00000003,
    0x00040015, 0x0000000a, 0x00000020, 0x00000000, 0x0004002b, 0x0000000a, 0x0000000b, 0x00000003,
    0x0004001c, 0x0000000c, 0x00000006, 0x0000000b, 0x00040020, 0x0000000d, 0x00000001, 0x0000000c,
    0x0004003b, 0x0000000d, 0x0000000e, 0x00000001, 0x00040015, 0x0000000f, 0x00000020, 0x00000001,
    0x0004002b, 0x0000000f, 0x00000010, 0x00000000, 0x00040020, 0x00000011, 0x00000001, 0x00000006,
    0x0004003b, 0x0000000d, 0x00000014, 0x00000001, 0x0004002b, 0x0000000f, 0x00000017, 0x00000001,
    0x0004002b, 0x0000000f, 0x0000001a, 0x00000002, 0x00050036, 0x00000002, 0x00000004, 0x00000000,
    0x00000003, 0x000200f8, 0x00000005, 0x00050041, 0x00000011, 0x00000012, 0x0000000e, 0x00000010,
    0x0004003d, 0x00000006, 0x00000013, 0x00000012, 0x00050041, 0x00000011, 0x00000015, 0x00000014,
    0x00000010, 0x0004003d, 0x00000006, 0x00000016, 0x00000015, 0x00050041, 0x00000011, 0x00000018,
    0x00000014, 0x00000017, 0x0004003d, 0x00000006, 0x00000019, 0x00000018, 0x00050041, 0x00000011,
    0x0000001b, 0x00000014, 0x0000001a, 0x0004003d, 0x00000006, 0x0000001c, 0x0000001b, 0x00070050,
    0x00000007, 0x0000001d, 0x00000013, 0x00000016, 0x00000019, 0x0000001c, 0x0003003e, 0x00000009,
    0x0000001d, 0x000100fd, 0x00010038,
};

// ---- (a) game shaders -----------------------------------------------------------------------------

static std::vector<u32> ReadSpirv(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    std::vector<u32> spirv(raw.size() / 4);
    std::memcpy(spirv.data(), raw.data(), spirv.size() * 4);
    return spirv;
}

static void WriteSpirv(const fs::path& path, const std::vector<u32>& spirv) {
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(spirv.data()), spirv.size() * 4);
}

static void GameShaders(Metal::ShaderCompiler& compiler, const fs::path& dir, const fs::path& dump) {
    std::printf("== game shaders (%s)\n", dir.c_str());
    const char* names[] = {"0x000000000089e4b2_0", "0x00000000ab4e5342_0", "0x00000000b6e87124_0",
                           "0x00000000c575bc29_0"};
    for (const char* name : names) {
        const fs::path path = dir / (std::string{name} + ".spv");
        if (!fs::exists(path)) {
            std::printf("  %s: not found, skipped\n", name);
            continue;
        }
        const std::vector<u32> spirv = ReadSpirv(path);
        const u64 hash = std::strtoull(name + 2, nullptr, 16);
        if (!Metal::HasPerVertexInputs(spirv)) {
            std::printf("  %s: no PerVertexKHR input?\n", name);
            Fail(name);
            continue;
        }
        const Metal::PerVertexRewrite rewrite = Metal::RewritePerVertexInputs(spirv);
        const Metal::CompileResult result = compiler.Compile(spirv, hash, name);
        std::printf("  %s: rewrite %s (flat %u, interpolated %u), compile %s%s\n", name,
                    rewrite.ok ? "ok" : rewrite.error.c_str(), rewrite.flat_inputs,
                    rewrite.interpolated_inputs,
                    result.status == CompileStatus::Ok ? "ok" : result.error.c_str(),
                    result.shader && result.shader->from_disk_cache ? " (disk cache)" : "");
        if (!rewrite.ok || result.status != CompileStatus::Ok) {
            Fail(name);
        }
        if (rewrite.ok && !dump.empty()) {
            WriteSpirv(dump / (std::string{name} + ".rewritten.spv"), rewrite.spirv);
        }
    }
}

// ---- (b) GPU correctness ---------------------------------------------------------------------------

namespace {

constexpr int kSize = 256;

struct Vertex {
    float pos[4];  // clip space
    float a0[4];
    float a1[4];
};

/// Vertex i of the draw: NDC position, 1/w-style perspective (clip = ndc * w), attributes.
struct Corner {
    float x, y, w;
    float a0[4];
    float a1[4];
};

struct Case {
    const char* name;
    const uint32_t* spirv;
    size_t words;
    const char* decls;    // vertex output members
    const char* assigns;  // vertex output assignments
    int interpolated;     // expected rewrite counters
    int flat;
};

id<MTLFunction> MakeVertexFunction(id<MTLDevice> device, const Case& c) {
    NSString* source = [NSString
        stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\n"
                          "struct In { float4 pos; float4 a0; float4 a1; };\n"
                          "struct Out { float4 position [[position]]; %s };\n"
                          "vertex Out vs(uint vid [[vertex_id]], constant In* v [[buffer(0)]]) {\n"
                          "  Out o; o.position = v[vid].pos; %s return o; }\n",
                          c.decls, c.assigns];
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    NSError* error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:options error:&error];
    if (!library) {
        std::printf("vertex MSL: %s\n", error.localizedDescription.UTF8String);
        return nil;
    }
    return [library newFunctionWithName:@"vs"];
}

/// Renders one triangle (clear value -999) and returns kSize*kSize RGBA32F pixels.
bool Render(Metal::Device& device, id<MTLFunction> vs, id<MTLFunction> frag, const Corner corners[3],
            std::vector<float>& pixels) {
    id<MTLDevice> mtl = device.GetImpl().device;
    MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = vs;
    desc.fragmentFunction = frag;
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    NSError* error = nil;
    id<MTLRenderPipelineState> pso = [mtl newRenderPipelineStateWithDescriptor:desc error:&error];
    if (!pso) {
        std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
        return false;
    }
    MTLTextureDescriptor* td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
                                                           width:kSize
                                                          height:kSize
                                                       mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [mtl newTextureWithDescriptor:td];
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(-999, -999, -999, -999);

    Vertex vertices[3];
    for (int i = 0; i < 3; ++i) {
        const Corner& c = corners[i];
        const float pos[4] = {c.x * c.w, c.y * c.w, 0.5f * c.w, c.w};
        std::memcpy(vertices[i].pos, pos, sizeof(pos));
        std::memcpy(vertices[i].a0, c.a0, sizeof(c.a0));
        std::memcpy(vertices[i].a1, c.a1, sizeof(c.a1));
    }
    id<MTLCommandBuffer> cb = [device.GetImpl().queue commandBuffer];
    id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:pass];
    [enc setRenderPipelineState:pso];
    [enc setVertexBytes:vertices length:sizeof(vertices) atIndex:0];
    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    if (cb.error) {
        std::printf("command buffer: %s\n", cb.error.localizedDescription.UTF8String);
        return false;
    }
    pixels.assign(size_t(kSize) * kSize * 4, 0.0f);
    [target getBytes:pixels.data()
         bytesPerRow:kSize * 16
          fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
         mipmapLevel:0];
    return true;
}

/// Smallest screen-space barycentric coordinate of the pixel centre (negative: outside).
float MinBary(const Corner c[3], int px, int py) {
    const float x = (px + 0.5f) / (kSize / 2) - 1.0f;
    const float y = 1.0f - (py + 0.5f) / (kSize / 2);
    const float d = (c[1].y - c[2].y) * (c[0].x - c[2].x) + (c[2].x - c[1].x) * (c[0].y - c[2].y);
    const float l0 = ((c[1].y - c[2].y) * (x - c[2].x) + (c[2].x - c[1].x) * (y - c[2].y)) / d;
    const float l1 = ((c[2].y - c[0].y) * (x - c[2].x) + (c[0].x - c[2].x) * (y - c[2].y)) / d;
    return std::fmin(std::fmin(l0, l1), 1.0f - l0 - l1);
}

struct Stats {
    long checked = 0, bad = 0, unwritten = 0;
    double max_rel = 0;
};

void Compare(Stats& stats, float got, float want, float scale, bool exact) {
    ++stats.checked;
    if (got == -999.0f) {
        ++stats.unwritten;
        return;
    }
    if (exact) {
        if (std::memcmp(&got, &want, 4) != 0) {
            ++stats.bad;
        }
        return;
    }
    const double rel = std::fabs(double(got) - double(want)) / double(scale);
    stats.max_rel = std::fmax(stats.max_rel, rel);
    if (!(rel <= 1e-3)) {  // also catches NaN
        ++stats.bad;
    }
}

float BitsToFloat(uint32_t bits) {
    float f;
    std::memcpy(&f, &bits, 4);
    return f;
}

/// One case, both windings. `want` fills the expected colour of a pixel from the corners.
bool RunCase(Metal::Device& device, Metal::ShaderCompiler& compiler, const Case& c, u64 hash,
             const fs::path& dump) {
    id<MTLDevice> mtl = device.GetImpl().device;
    const std::span<const u32> spirv{c.spirv, c.words};
    const Metal::PerVertexRewrite rewrite = Metal::RewritePerVertexInputs(spirv);
    std::printf("-- %s: rewrite %s (flat %u, interpolated %u)\n", c.name,
                rewrite.ok ? "ok" : rewrite.error.c_str(), rewrite.flat_inputs,
                rewrite.interpolated_inputs);
    if (!rewrite.ok || int(rewrite.flat_inputs) != c.flat ||
        int(rewrite.interpolated_inputs) != c.interpolated) {
        Fail("rewrite counters");
        return false;
    }
    if (!dump.empty()) {
        WriteSpirv(dump / (std::string{c.name} + ".rewritten.spv"), rewrite.spirv);
    }
    Metal::CompileResult result = compiler.Compile(spirv, hash, c.name);
    if (result.status != CompileStatus::Ok) {
        std::printf("   compile: %s\n", result.error.c_str());
        Fail("compile");
        return false;
    }
    id<MTLFunction> frag = (__bridge id<MTLFunction>)result.shader->function;
    id<MTLFunction> vs = MakeVertexFunction(mtl, c);
    if (!vs) {
        Fail("vertex function");
        return false;
    }

    // Vertex values per case: a0 = (v0, ...) of vertex k. Perspective: w = 1, 2.5, 4.
    const float w[3] = {1.0f, 2.5f, 4.0f};
    struct Layout {
        const char* name;
        float xy[3][2];
    };
    const Layout layouts[2] = {
        {"ccw", {{-0.9f, -0.9f}, {0.9f, -0.8f}, {-0.2f, 0.9f}}},
        {"cw", {{-0.9f, -0.9f}, {-0.2f, 0.9f}, {0.9f, -0.8f}}},
    };
    const float scalar[3] = {1.5f, -2.25f, 7.0f};
    const float flat_bits[3] = {BitsToFloat(0x41234567), 3.0f, -5.0f};  // vertex 0 is exact-compared
    const float second[3] = {0.25f, 100.0f, -8.0f};
    bool all_ok = true;
    for (const Layout& layout : layouts) {
        Corner corners[3];
        for (int i = 0; i < 3; ++i) {
            corners[i].x = layout.xy[i][0];
            corners[i].y = layout.xy[i][1];
            corners[i].w = w[i];
            const bool bits = std::strstr(c.name, "flat_only") != nullptr;
            const float a = bits ? flat_bits[i] : scalar[i];
            const float vec[4] = {a, a + 1, a + 2, a + 3};
            std::memcpy(corners[i].a0, vec, sizeof(vec));
            const float b[4] = {second[i], 0, 0, 0};
            std::memcpy(corners[i].a1, b, sizeof(b));
        }
        std::vector<float> pixels;
        if (!Render(device, vs, frag, corners, pixels)) {
            Fail("render");
            return false;
        }
        Stats stats;
        for (int py = 0; py < kSize; ++py) {
            for (int px = 0; px < kSize; ++px) {
                if (MinBary(corners, px, py) < 0.03f) {
                    continue;
                }
                const float* got = &pixels[(size_t(py) * kSize + px) * 4];
                if (std::strstr(c.name, "float3")) {
                    for (int k = 0; k < 3; ++k) {
                        Compare(stats, got[k], scalar[k], 7.0f, false);
                    }
                } else if (std::strstr(c.name, "vec4")) {
                    for (int k = 0; k < 4; ++k) {  // w[2] + 0.5 w[1], components a0[k] = a + k
                        const float want = (scalar[2] + k) + 0.5f * (scalar[1] + k);
                        Compare(stats, got[k], want, 10.0f, false);
                    }
                } else if (std::strstr(c.name, "flat_only")) {
                    Compare(stats, got[0], flat_bits[0], 1.0f, true);
                } else {  // mixed: (a[0] exact, b[0], b[1], b[2])
                    Compare(stats, got[0], scalar[0], 1.0f, true);
                    for (int k = 0; k < 3; ++k) {
                        Compare(stats, got[1 + k], second[k], 100.0f, false);
                    }
                }
            }
        }
        std::printf("   %s: checked %ld, bad %ld, unwritten %ld, max relative error %.2e\n",
                    layout.name, stats.checked, stats.bad, stats.unwritten, stats.max_rel);
        if (stats.checked < 10000 || stats.bad || stats.unwritten) {
            Fail(c.name);
            all_ok = false;
        }
    }
    return all_ok;
}

/// A triangle of about two pixels: the degenerate-derivative fallback must not produce NaN/inf.
void RunTiny(Metal::Device& device, Metal::ShaderCompiler& compiler, const Case& c, u64 hash) {
    Metal::CompileResult result = compiler.Compile({c.spirv, c.words}, hash, c.name);
    id<MTLFunction> vs = MakeVertexFunction(device.GetImpl().device, c);
    if (result.status != CompileStatus::Ok || !vs) {
        Fail("tiny compile");
        return;
    }
    const float xy[3][2] = {{0.0f, 0.0f}, {0.012f, 0.0f}, {0.0f, 0.012f}};
    Corner corners[3];
    for (int i = 0; i < 3; ++i) {
        corners[i] = {xy[i][0], xy[i][1], 1.0f + i, {1.0f * i, 0, 0, 0}, {0, 0, 0, 0}};
    }
    std::vector<float> pixels;
    if (!Render(device, vs, (__bridge id<MTLFunction>)result.shader->function, corners, pixels)) {
        Fail("tiny render");
        return;
    }
    long written = 0, bad = 0;
    for (size_t i = 0; i < pixels.size(); ++i) {
        if (pixels[i] != -999.0f) {
            ++written;
            bad += !std::isfinite(pixels[i]);
        }
    }
    std::printf("-- tiny triangle: %ld values written, %ld not finite\n", written, bad);
    if (written == 0 || bad) {
        Fail("tiny triangle");
    }
}

} // namespace

int main(int argc, char** argv) {
    fs::path dir, dump;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--dump") && i + 1 < argc) {
            dump = argv[++i];
        } else if (argv[i][0] != '-' && dir.empty()) {
            dir = argv[i];
        } else {
            std::fprintf(stderr, "usage: %s [<spv dir>] [--dump <dir>]\n", argv[0]);
            return 2;
        }
    }
    if (dir.empty()) {
        const char* home = std::getenv("HOME");
        dir = fs::path{home ? home : "."} / "Games/Bloodborne/bench/testuser/cache/CUSA03173";
    }
    if (!dump.empty()) {
        fs::create_directories(dump);
    }
    const fs::path cache = fs::temp_directory_path() / "bbport-mtl-pervertex-test-cache";
    std::error_code ec;
    fs::remove_all(cache, ec);  // always convert: the rewrite is what is under test

    Metal::Device device;
    Metal::ShaderCompiler compiler{device, cache};
    GameShaders(compiler, dir, dump);

    std::printf("== GPU correctness (%dx%d, w = 1, 2.5, 4)\n", kSize, kSize);
    const Case cases[] = {
        {"float3_all_vertices", kT1Spirv, std::size(kT1Spirv), "float v [[user(locn0)]];",
         "o.v = v[vid].a0.x;", 1, 0},
        {"vec4_vertices_1_2", kT2Spirv, std::size(kT2Spirv), "float4 w [[user(locn0)]];",
         "o.w = v[vid].a0;", 1, 0},
        {"flat_only_vertex0", kT3Spirv, std::size(kT3Spirv), "float v [[user(locn0)]];",
         "o.v = v[vid].a0.x;", 0, 1},
        {"mixed_flat_and_all", kT4Spirv, std::size(kT4Spirv),
         "float a [[user(locn0)]]; float b [[user(locn1)]];",
         "o.a = v[vid].a0.x; o.b = v[vid].a1.x;", 1, 1},
    };
    u64 hash = 0x7e57000;
    for (const Case& c : cases) {
        RunCase(device, compiler, c, hash++, dump);
    }
    RunTiny(device, compiler, cases[0], hash++);

    std::printf("\n%s\n", g_failures ? "FAIL" : "PASS");
    return g_failures ? 1 : 0;
}
