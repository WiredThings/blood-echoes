// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the coordinate conventions of the Metal rasterizer's recording
// (renderer_metal/mtl_rasterizer.mm, plan P4.3b): the viewport conversion, the front face and the
// scissor clamp, with the helper functions of renderer_metal/mtl_raster_util.h. It renders
// triangles given in Vulkan clip space with small MSL shaders into a 64x64 texture and reads the
// pixels back. Needs a Metal device, no window, game or Liverpool.
//
//   mtl-raster-test
//
// Exit 0 when every check passed.
#import <Metal/Metal.h>

#include <setjmp.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "video_core/renderer_metal/mtl_raster_util.h"

// The runtime services libbbgpu takes from bb-probe; this test has no guest process.
extern "C" {
__thread sigjmp_buf* runtime_fault_recover = nullptr;
uint64_t runtime_disabled_optimizations = 0;
uint64_t runtime_experiment_bits = 0;
}

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

namespace RU = Metal::RasterUtil;

static constexpr int Dim = 64;

static NSString* const Source = @R"(
#include <metal_stdlib>
using namespace metal;
// The positions are Vulkan clip space, as the recompiler's vertex shaders write them.
vertex float4 vs(uint vid [[vertex_id]], constant float2* positions [[buffer(0)]]) {
    return float4(positions[vid], 0.0, 1.0);
}
fragment float4 fs() { return float4(1.0, 0.0, 0.0, 1.0); }
)";

struct Gpu {
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    id<MTLRenderPipelineState> pipeline;
    id<MTLTexture> target;
};

static bool Setup(Gpu& gpu) {
    gpu.device = MTLCreateSystemDefaultDevice();
    if (!gpu.device) {
        std::printf("no Metal device\n");
        return false;
    }
    gpu.queue = [gpu.device newCommandQueue];
    NSError* error = nil;
    id<MTLLibrary> library = [gpu.device newLibraryWithSource:Source options:nil error:&error];
    if (!library) {
        std::printf("MSL: %s\n", error.localizedDescription.UTF8String);
        return false;
    }
    MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = [library newFunctionWithName:@"vs"];
    desc.fragmentFunction = [library newFunctionWithName:@"fs"];
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    gpu.pipeline = [gpu.device newRenderPipelineStateWithDescriptor:desc error:&error];
    if (!gpu.pipeline) {
        std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
        return false;
    }
    MTLTextureDescriptor* td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                           width:Dim
                                                          height:Dim
                                                       mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    gpu.target = [gpu.device newTextureWithDescriptor:td];
    return true;
}

struct Draw {
    vk::Viewport viewport{0, 0, Dim, Dim, 0, 1};
    vk::Rect2D scissor{{0, 0}, {Dim, Dim}};
    vk::FrontFace front_face = vk::FrontFace::eCounterClockwise;
    vk::CullModeFlags cull = vk::CullModeFlagBits::eNone;
    std::array<float, 6> clip; ///< three vertices, Vulkan clip space
};

/// Renders one triangle the way the rasterizer sets the state; returns the red channel of every
/// pixel (row 0 is the top of the image). `scissor_nonempty` is false when the draw is dropped.
static std::vector<uint8_t> Render(Gpu& gpu, const Draw& draw, bool* scissor_nonempty = nullptr) {
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = gpu.target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> cmdbuf = [gpu.queue commandBuffer];
    id<MTLRenderCommandEncoder> enc = [cmdbuf renderCommandEncoderWithDescriptor:pass];
    [enc setRenderPipelineState:gpu.pipeline];

    const auto m = RU::ToMetalViewport(draw.viewport.x, draw.viewport.y, draw.viewport.width,
                                       draw.viewport.height, draw.viewport.minDepth,
                                       draw.viewport.maxDepth);
    [enc setViewport:MTLViewport{m.origin_x, m.origin_y, m.width, m.height, m.znear, m.zfar}];
    RU::ScissorRect rect;
    const bool nonempty = RU::ClampScissor(draw.scissor.offset.x, draw.scissor.offset.y,
                                           draw.scissor.extent.width, draw.scissor.extent.height,
                                           Dim, Dim, rect);
    if (scissor_nonempty) {
        *scissor_nonempty = nonempty;
    }
    if (nonempty) {
        [enc setScissorRect:MTLScissorRect{rect.x, rect.y, rect.width, rect.height}];
        const bool front = bool(draw.cull & vk::CullModeFlagBits::eFront);
        const bool back = bool(draw.cull & vk::CullModeFlagBits::eBack);
        [enc setCullMode:front ? MTLCullModeFront : back ? MTLCullModeBack : MTLCullModeNone];
        [enc setFrontFacingWinding:static_cast<MTLWinding>(RU::FrontWinding(draw.front_face))];
        [enc setVertexBytes:draw.clip.data() length:sizeof(float) * 6 atIndex:0];
        [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    }
    [enc endEncoding];
    [cmdbuf commit];
    [cmdbuf waitUntilCompleted];
    CHECK(cmdbuf.status == MTLCommandBufferStatusCompleted);

    std::vector<uint32_t> pixels(Dim * Dim);
    [gpu.target getBytes:pixels.data()
             bytesPerRow:Dim * 4
              fromRegion:MTLRegionMake2D(0, 0, Dim, Dim)
             mipmapLevel:0];
    std::vector<uint8_t> red(Dim * Dim);
    for (int i = 0; i < Dim * Dim; ++i) {
        red[i] = pixels[i] & 0xff; // RGBA8: red is the lowest byte
    }
    return red;
}

static bool Covered(const std::vector<uint8_t>& image, int x, int y) {
    return image[y * Dim + x] != 0;
}

static int CountCovered(const std::vector<uint8_t>& image) {
    int n = 0;
    for (uint8_t v : image) {
        n += v != 0;
    }
    return n;
}

/// The clip space position (Vulkan: y down) that lands on window position (wx, wy) with `viewport`.
static std::array<float, 2> ClipOf(const vk::Viewport& viewport, float wx, float wy) {
    return {(wx - viewport.x) / viewport.width * 2.0f - 1.0f,
            (wy - viewport.y) / viewport.height * 2.0f - 1.0f};
}

static std::array<float, 6> ClipTriangle(const vk::Viewport& viewport,
                                         std::array<std::array<float, 2>, 3> window) {
    std::array<float, 6> out;
    for (int i = 0; i < 3; ++i) {
        const auto c = ClipOf(viewport, window[i][0], window[i][1]);
        out[i * 2] = c[0];
        out[i * 2 + 1] = c[1];
    }
    return out;
}

static void TestViewport(Gpu& gpu) {
    std::printf("viewport conversion\n");
    // A triangle in the top-left quarter of the window for the Vulkan viewport {0, 0, 64, 64}
    // (clip y -1 is the top row): its corners are the window positions (0,0), (32,0), (0,32).
    Draw draw;
    draw.clip = {-1, -1, 0, -1, -1, 0};
    draw.viewport = {0, 0, Dim, Dim, 0, 1};
    auto image = Render(gpu, draw);
    CHECK(Covered(image, 4, 4));
    CHECK(!Covered(image, 60, 60));
    CHECK(!Covered(image, 4, 60));
    CHECK(!Covered(image, 60, 4));
    const int upright = CountCovered(image);
    CHECK(upright > 400 && upright < 600); // half of a 32x32 quarter: 512

    // The same clip space triangle with the flipped viewport {0, 64, 64, -64}: clip y -1 is the
    // bottom row now, the triangle lands in the bottom-left quarter.
    draw.viewport = {0, Dim, Dim, -Dim, 0, 1};
    image = Render(gpu, draw);
    CHECK(Covered(image, 4, 60));
    CHECK(!Covered(image, 4, 4));
    CHECK(!Covered(image, 60, 60));
    CHECK(!Covered(image, 60, 4));
    CHECK(CountCovered(image) == upright);

    // A sub-rectangle viewport with an offset: {16, 8, 32, 32} places clip (-1,-1) at (16, 8).
    draw.viewport = {16, 8, 32, 32, 0, 1};
    image = Render(gpu, draw);
    CHECK(Covered(image, 18, 10));
    CHECK(!Covered(image, 14, 6));
    CHECK(!Covered(image, 40, 40));
    // ... and flipped: {16, 40, 32, -32} puts it at the bottom of the same rectangle.
    draw.viewport = {16, 40, 32, -32, 0, 1};
    image = Render(gpu, draw);
    CHECK(Covered(image, 18, 38));
    CHECK(!Covered(image, 18, 10));

    // The conversion itself.
    const auto up = RU::ToMetalViewport(0, 0, 64, 64, 0.25f, 0.75f);
    CHECK(up.origin_y == 64 && up.height == -64 && up.znear == 0.25 && up.zfar == 0.75);
    const auto down = RU::ToMetalViewport(0, 64, 64, -64, 0, 1);
    CHECK(down.origin_y == 0 && down.height == 64);
    const auto wide = RU::ToMetalViewport(0, 0, 64, 64, -1.0f, 2.0f);
    CHECK(wide.znear == 0.0 && wide.zfar == 1.0);
}

static void TestWinding(Gpu& gpu) {
    std::printf("front face\n");
    // Window space triangles, as displayed with y down: (8,8) -> (8,56) -> (56,8) turns
    // counter-clockwise on screen (Vulkan's signed area a = -1/2 sum(x_i y_i+1 - x_i+1 y_i) is
    // positive: 1152), the reverse order clockwise.
    const std::array<std::array<float, 2>, 3> ccw = {{{8, 8}, {8, 56}, {56, 8}}};
    const std::array<std::array<float, 2>, 3> cw = {{ccw[0], ccw[2], ccw[1]}};
    const vk::Viewport viewports[2] = {{0, 0, Dim, Dim, 0, 1}, {0, Dim, Dim, -Dim, 0, 1}};
    for (const vk::Viewport& viewport : viewports) {
        std::printf("  vulkan viewport height %g\n", viewport.height);
        // The sample pixel is inside both triangles.
        const auto drawn = [&](const std::array<std::array<float, 2>, 3>& window,
                               vk::FrontFace face, vk::CullModeFlags cull) {
            Draw draw;
            draw.viewport = viewport;
            draw.front_face = face;
            draw.cull = cull;
            draw.clip = ClipTriangle(viewport, window);
            return Covered(Render(gpu, draw), 16, 16);
        };
        using vk::CullModeFlagBits;
        using vk::FrontFace;
        // Front face counter-clockwise: the counter-clockwise triangle is front facing.
        CHECK(drawn(ccw, FrontFace::eCounterClockwise, CullModeFlagBits::eNone));
        CHECK(drawn(cw, FrontFace::eCounterClockwise, CullModeFlagBits::eNone));
        CHECK(drawn(ccw, FrontFace::eCounterClockwise, CullModeFlagBits::eBack));
        CHECK(!drawn(cw, FrontFace::eCounterClockwise, CullModeFlagBits::eBack));
        CHECK(!drawn(ccw, FrontFace::eCounterClockwise, CullModeFlagBits::eFront));
        CHECK(drawn(cw, FrontFace::eCounterClockwise, CullModeFlagBits::eFront));
        // Front face clockwise: the other way round.
        CHECK(!drawn(ccw, FrontFace::eClockwise, CullModeFlagBits::eBack));
        CHECK(drawn(cw, FrontFace::eClockwise, CullModeFlagBits::eBack));
        CHECK(drawn(ccw, FrontFace::eClockwise, CullModeFlagBits::eFront));
        CHECK(!drawn(cw, FrontFace::eClockwise, CullModeFlagBits::eFront));
    }
}

static void TestScissor() {
    std::printf("scissor clamp\n");
    RU::ScissorRect r;
    CHECK(RU::ClampScissor(0, 0, 64, 64, 64, 64, r) && r.x == 0 && r.y == 0 && r.width == 64 &&
          r.height == 64);
    CHECK(RU::ClampScissor(10, 20, 30, 40, 64, 64, r) && r.x == 10 && r.y == 20 && r.width == 30 &&
          r.height == 40);
    // Negative offsets cut the rectangle.
    CHECK(RU::ClampScissor(-10, -10, 40, 30, 64, 64, r) && r.x == 0 && r.y == 0 &&
          r.width == 30 && r.height == 20);
    // Beyond the target.
    CHECK(RU::ClampScissor(50, 50, 100, 100, 64, 64, r) && r.x == 50 && r.y == 50 &&
          r.width == 14 && r.height == 14);
    CHECK(RU::ClampScissor(0, 0, 0xffffffffu, 0xffffffffu, 64, 64, r) && r.width == 64 &&
          r.height == 64);
    CHECK(RU::ClampScissor(-0x7fffffff, -0x7fffffff, 0xffffffffu, 0xffffffffu, 64, 64, r) &&
          r.x == 0 && r.y == 0 && r.width == 64 && r.height == 64);
    // Empty after clamping: the draw is dropped.
    CHECK(!RU::ClampScissor(0, 0, 0, 10, 64, 64, r) && r.width == 0 && r.height == 0);
    CHECK(!RU::ClampScissor(0, 0, 10, 0, 64, 64, r));
    CHECK(!RU::ClampScissor(64, 0, 10, 10, 64, 64, r));
    CHECK(!RU::ClampScissor(0, 100, 10, 10, 64, 64, r));
    CHECK(!RU::ClampScissor(-20, 0, 10, 10, 64, 64, r));
    CHECK(!RU::ClampScissor(-20, -20, 20, 20, 64, 64, r)); // ends exactly at 0
}

static void TestScissorDraw(Gpu& gpu) {
    std::printf("scissor draw\n");
    // A triangle covering the whole target, scissored to a rectangle that sticks out.
    Draw draw;
    draw.clip = {-1, -1, 3, -1, -1, 3};
    draw.scissor = {{-10, -10}, {40, 40}}; // clamped to (0,0)-(30,30)
    bool nonempty = false;
    auto image = Render(gpu, draw, &nonempty);
    CHECK(nonempty);
    CHECK(Covered(image, 0, 0) && Covered(image, 29, 29));
    CHECK(!Covered(image, 30, 30) && !Covered(image, 40, 5) && !Covered(image, 5, 40));
    CHECK(CountCovered(image) == 30 * 30);
    draw.scissor = {{70, 0}, {10, 10}};
    image = Render(gpu, draw, &nonempty);
    CHECK(!nonempty);
    CHECK(CountCovered(image) == 0);
}

int main() {
    @autoreleasepool {
        Gpu gpu;
        if (!Setup(gpu)) {
            return 1;
        }
        TestViewport(gpu);
        TestWinding(gpu);
        TestScissor();
        TestScissorDraw(gpu);
    }
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return 1;
    }
    std::printf("mtl-raster-test: all checks passed\n");
    return 0;
}
