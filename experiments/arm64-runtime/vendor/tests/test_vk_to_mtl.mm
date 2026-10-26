// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/vk_to_mtl: every Vulkan format the renderer can produce maps to a
// Metal format the device can create, and every enum conversion matches the Metal enum it is
// meant to select (the expected values below are written against the Metal enums, not the code
// under test). Needs a Metal device, no window or game.

#import <Metal/Metal.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <set>
#include <string>

#include "video_core/renderer_metal/vk_to_mtl.h"

namespace VkToMtl = Metal::VkToMtl;
using VkToMtl::TextureSwizzle;

static int g_formats = 0;
static int g_checks = 0;
static int g_failures = 0;
static int g_gaps = 0;

#define CHECK(cond, ...)                                                                           \
    do {                                                                                           \
        ++g_checks;                                                                                \
        if (!(cond)) {                                                                             \
            ++g_failures;                                                                          \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                                       \
            std::printf(__VA_ARGS__);                                                              \
            std::printf("\n");                                                                     \
        }                                                                                          \
    } while (0)

// One row per Vulkan format: the expected Metal pixel format (Invalid = no texture equivalent),
// the swizzle string ("" = identity, else the texture channel each of r, g, b, a reads, from
// "rgba01") and the expected vertex format.
struct FormatCase {
    vk::Format format;
    MTLPixelFormat pixel;
    const char* swizzle;
    MTLVertexFormat vertex;
};

#define ROW(vk_name, pixel, swizzle, vertex)                                                       \
    {vk::Format::e##vk_name, MTLPixelFormat##pixel, swizzle, MTLVertexFormat##vertex}

// Group A: the table behind LiverpoolToVK::SurfaceFormat / DepthFormat (liverpool_to_vk.cpp). Every
// guest image, render target, depth buffer and vertex attribute format comes from it. Rows with
// pixel Invalid are known gaps (no Metal texture format); the vertex column matters for the
// buffer-capable formats.
static const FormatCase kSurfaceFormats[] = {
    ROW(A1R5G5B5UnormPack16, BGR5A1Unorm, "", Invalid),
    ROW(A2B10G10R10SintPack32, Invalid, "", Invalid),
    ROW(A2B10G10R10SnormPack32, Invalid, "", Int1010102Normalized),
    ROW(A2B10G10R10UintPack32, RGB10A2Uint, "", Invalid),
    ROW(A2B10G10R10UnormPack32, RGB10A2Unorm, "", UInt1010102Normalized),
    ROW(B10G11R11UfloatPack32, RG11B10Float, "", Invalid),
    ROW(B4G4R4A4UnormPack16, ABGR4Unorm, "bgra", Invalid),
    ROW(Bc1RgbaSrgbBlock, BC1_RGBA_sRGB, "", Invalid),
    ROW(Bc1RgbaUnormBlock, BC1_RGBA, "", Invalid),
    ROW(Bc2SrgbBlock, BC2_RGBA_sRGB, "", Invalid),
    ROW(Bc2UnormBlock, BC2_RGBA, "", Invalid),
    ROW(Bc3SrgbBlock, BC3_RGBA_sRGB, "", Invalid),
    ROW(Bc3UnormBlock, BC3_RGBA, "", Invalid),
    ROW(Bc4SnormBlock, BC4_RSnorm, "", Invalid),
    ROW(Bc4UnormBlock, BC4_RUnorm, "", Invalid),
    ROW(Bc5SnormBlock, BC5_RGSnorm, "", Invalid),
    ROW(Bc5UnormBlock, BC5_RGUnorm, "", Invalid),
    ROW(Bc6HSfloatBlock, BC6H_RGBFloat, "", Invalid),
    ROW(Bc6HUfloatBlock, BC6H_RGBUfloat, "", Invalid),
    ROW(Bc7SrgbBlock, BC7_RGBAUnorm_sRGB, "", Invalid),
    ROW(Bc7UnormBlock, BC7_RGBAUnorm, "", Invalid),
    ROW(D16Unorm, Depth16Unorm, "", Invalid),
    ROW(D16UnormS8Uint, Depth32Float_Stencil8, "", Invalid),
    ROW(D32Sfloat, Depth32Float, "", Invalid),
    ROW(D32SfloatS8Uint, Depth32Float_Stencil8, "", Invalid),
    ROW(E5B9G9R9UfloatPack32, RGB9E5Float, "", Invalid),
    ROW(R16G16B16A16Sfloat, RGBA16Float, "", Half4),
    ROW(R16G16B16A16Sint, RGBA16Sint, "", Short4),
    ROW(R16G16B16A16Snorm, RGBA16Snorm, "", Short4Normalized),
    ROW(R16G16B16A16Uint, RGBA16Uint, "", UShort4),
    ROW(R16G16B16A16Unorm, RGBA16Unorm, "", UShort4Normalized),
    ROW(R16G16Sfloat, RG16Float, "", Half2),
    ROW(R16G16Sint, RG16Sint, "", Short2),
    ROW(R16G16Snorm, RG16Snorm, "", Short2Normalized),
    ROW(R16G16Uint, RG16Uint, "", UShort2),
    ROW(R16G16Unorm, RG16Unorm, "", UShort2Normalized),
    ROW(R16Sfloat, R16Float, "", Half),
    ROW(R16Sint, R16Sint, "", Short),
    ROW(R16Snorm, R16Snorm, "", ShortNormalized),
    ROW(R16Uint, R16Uint, "", UShort),
    ROW(R16Unorm, R16Unorm, "", UShortNormalized),
    ROW(R32G32B32A32Sfloat, RGBA32Float, "", Float4),
    ROW(R32G32B32A32Sint, RGBA32Sint, "", Int4),
    ROW(R32G32B32A32Uint, RGBA32Uint, "", UInt4),
    ROW(R32G32B32Sfloat, Invalid, "", Float3),
    ROW(R32G32B32Sint, Invalid, "", Int3),
    ROW(R32G32B32Uint, Invalid, "", UInt3),
    ROW(R32G32Sfloat, RG32Float, "", Float2),
    ROW(R32G32Sint, RG32Sint, "", Int2),
    ROW(R32G32Uint, RG32Uint, "", UInt2),
    ROW(R32Sfloat, R32Float, "", Float),
    ROW(R32Sint, R32Sint, "", Int),
    ROW(R32Uint, R32Uint, "", UInt),
    ROW(R5G5B5A1UnormPack16, A1BGR5Unorm, "", Invalid),
    ROW(R5G6B5UnormPack16, B5G6R5Unorm, "", Invalid),
    ROW(R8G8B8A8Sint, RGBA8Sint, "", Char4),
    ROW(R8G8B8A8Snorm, RGBA8Snorm, "", Char4Normalized),
    ROW(R8G8B8A8Srgb, RGBA8Unorm_sRGB, "", Invalid),
    ROW(R8G8B8A8Uint, RGBA8Uint, "", UChar4),
    ROW(R8G8B8A8Unorm, RGBA8Unorm, "", UChar4Normalized),
    ROW(R8G8Sint, RG8Sint, "", Char2),
    ROW(R8G8Snorm, RG8Snorm, "", Char2Normalized),
    ROW(R8G8Srgb, RG8Unorm_sRGB, "", Invalid),
    ROW(R8G8Uint, RG8Uint, "", UChar2),
    ROW(R8G8Unorm, RG8Unorm, "", UChar2Normalized),
    ROW(R8Sint, R8Sint, "", Char),
    ROW(R8Snorm, R8Snorm, "", CharNormalized),
    ROW(R8Srgb, R8Unorm_sRGB, "", Invalid),
    ROW(R8Uint, R8Uint, "", UChar),
    ROW(R8Unorm, R8Unorm, "", UCharNormalized),
};

// Group B: formats the Vulkan backend names directly: image_info.cpp ConvertPixelFormat and
// vk_presenter.cpp GetFrameViewFormat (VideoOut frames), vk_swapchain.cpp, vk_instance.cpp
// (D24S8 and its fallbacks), vk_temporal_upscaler.cpp, IsFormatStencilCompatible.
static const FormatCase kBackendFormats[] = {
    ROW(A2R10G10B10UnormPack32, BGR10A2Unorm, "", Invalid),
    ROW(B8G8R8A8Unorm, BGRA8Unorm, "", UChar4Normalized_BGRA),
    ROW(B8G8R8A8Srgb, BGRA8Unorm_sRGB, "", Invalid),
    ROW(D24UnormS8Uint, Depth32Float_Stencil8, "", Invalid),
    ROW(S8Uint, Stencil8, "", Invalid),
};

// Group C: the rest of the compatibility table of texture_cache/host_compatibility.cpp (every
// format the texture cache knows about). Scaled and 3-channel formats have no texture twin.
static const FormatCase kOtherFormats[] = {
    ROW(A2B10G10R10SscaledPack32, Invalid, "", Invalid),
    ROW(A2B10G10R10UscaledPack32, Invalid, "", Invalid),
    ROW(A2R10G10B10SintPack32, Invalid, "", Invalid),
    ROW(A2R10G10B10SnormPack32, Invalid, "", Invalid),
    ROW(A2R10G10B10SscaledPack32, Invalid, "", Invalid),
    ROW(A2R10G10B10UintPack32, Invalid, "", Invalid),
    ROW(A2R10G10B10UscaledPack32, Invalid, "", Invalid),
    ROW(A4B4G4R4UnormPack16, ABGR4Unorm, "abgr", Invalid),
    ROW(A4R4G4B4UnormPack16, ABGR4Unorm, "gbar", Invalid),
    ROW(A8B8G8R8SintPack32, RGBA8Sint, "", Char4),
    ROW(A8B8G8R8SnormPack32, RGBA8Snorm, "", Char4Normalized),
    ROW(A8B8G8R8SrgbPack32, RGBA8Unorm_sRGB, "", Invalid),
    ROW(A8B8G8R8SscaledPack32, Invalid, "", Char4),
    ROW(A8B8G8R8UintPack32, RGBA8Uint, "", UChar4),
    ROW(A8B8G8R8UnormPack32, RGBA8Unorm, "", UChar4Normalized),
    ROW(A8B8G8R8UscaledPack32, Invalid, "", UChar4),
    ROW(B5G5R5A1UnormPack16, A1BGR5Unorm, "bgra", Invalid),
    ROW(B5G6R5UnormPack16, B5G6R5Unorm, "bgra", Invalid),
    ROW(B8G8R8A8Sint, RGBA8Sint, "bgra", Invalid),
    ROW(B8G8R8A8Snorm, RGBA8Snorm, "bgra", Invalid),
    ROW(B8G8R8A8Sscaled, Invalid, "", Invalid),
    ROW(B8G8R8A8Uint, RGBA8Uint, "bgra", Invalid),
    ROW(B8G8R8A8Uscaled, Invalid, "", Invalid),
    ROW(B8G8R8Sint, Invalid, "", Invalid),
    ROW(B8G8R8Snorm, Invalid, "", Invalid),
    ROW(B8G8R8Srgb, Invalid, "", Invalid),
    ROW(B8G8R8Sscaled, Invalid, "", Invalid),
    ROW(B8G8R8Uint, Invalid, "", Invalid),
    ROW(B8G8R8Unorm, Invalid, "", Invalid),
    ROW(B8G8R8Uscaled, Invalid, "", Invalid),
    ROW(Bc1RgbSrgbBlock, BC1_RGBA_sRGB, "rgb1", Invalid),
    ROW(Bc1RgbUnormBlock, BC1_RGBA, "rgb1", Invalid),
    ROW(R10X6G10X6Unorm2Pack16, Invalid, "", Invalid),
    ROW(R10X6UnormPack16, Invalid, "", Invalid),
    ROW(R12X4G12X4Unorm2Pack16, Invalid, "", Invalid),
    ROW(R12X4UnormPack16, Invalid, "", Invalid),
    ROW(R16G16B16A16Sscaled, Invalid, "", Short4),
    ROW(R16G16B16A16Uscaled, Invalid, "", UShort4),
    ROW(R16G16B16Sfloat, Invalid, "", Half3),
    ROW(R16G16B16Sint, Invalid, "", Short3),
    ROW(R16G16B16Snorm, Invalid, "", Short3Normalized),
    ROW(R16G16B16Sscaled, Invalid, "", Short3),
    ROW(R16G16B16Uint, Invalid, "", UShort3),
    ROW(R16G16B16Unorm, Invalid, "", UShort3Normalized),
    ROW(R16G16B16Uscaled, Invalid, "", UShort3),
    ROW(R16G16Sscaled, Invalid, "", Short2),
    ROW(R16G16Uscaled, Invalid, "", UShort2),
    ROW(R16Sscaled, Invalid, "", Short),
    ROW(R16Uscaled, Invalid, "", UShort),
    ROW(R4G4B4A4UnormPack16, ABGR4Unorm, "", Invalid),
    ROW(R4G4UnormPack8, Invalid, "", Invalid),
    ROW(R64G64B64A64Sfloat, Invalid, "", Invalid),
    ROW(R64G64B64A64Sint, Invalid, "", Invalid),
    ROW(R64G64B64A64Uint, Invalid, "", Invalid),
    ROW(R64G64B64Sfloat, Invalid, "", Invalid),
    ROW(R64G64B64Sint, Invalid, "", Invalid),
    ROW(R64G64B64Uint, Invalid, "", Invalid),
    ROW(R64G64Sfloat, Invalid, "", Invalid),
    ROW(R64G64Sint, Invalid, "", Invalid),
    ROW(R64G64Uint, Invalid, "", Invalid),
    ROW(R64Sfloat, Invalid, "", Invalid),
    ROW(R64Sint, Invalid, "", Invalid),
    ROW(R64Uint, Invalid, "", Invalid),
    ROW(R8G8B8A8Sscaled, Invalid, "", Char4),
    ROW(R8G8B8A8Uscaled, Invalid, "", UChar4),
    ROW(R8G8B8Sint, Invalid, "", Char3),
    ROW(R8G8B8Snorm, Invalid, "", Char3Normalized),
    ROW(R8G8B8Srgb, Invalid, "", Invalid),
    ROW(R8G8B8Sscaled, Invalid, "", Char3),
    ROW(R8G8B8Uint, Invalid, "", UChar3),
    ROW(R8G8B8Unorm, Invalid, "", UChar3Normalized),
    ROW(R8G8B8Uscaled, Invalid, "", UChar3),
    ROW(R8G8Sscaled, Invalid, "", Char2),
    ROW(R8G8Uscaled, Invalid, "", UChar2),
    ROW(R8Sscaled, Invalid, "", Char),
    ROW(R8Uscaled, Invalid, "", UChar),
    ROW(X8D24UnormPack32, Depth32Float, "", Invalid),
};

/// Formats whose Metal twin is another texel size or value range (see IsExactPixelFormat).
static bool ExpectInexact(vk::Format format) {
    return format == vk::Format::eD16UnormS8Uint || format == vk::Format::eD24UnormS8Uint ||
           format == vk::Format::eX8D24UnormPack32;
}

static u8 SwizzleChannel(char c) {
    switch (c) {
    case '0':
        return VkToMtl::SwizzleZero;
    case '1':
        return VkToMtl::SwizzleOne;
    case 'r':
        return VkToMtl::SwizzleRed;
    case 'g':
        return VkToMtl::SwizzleGreen;
    case 'b':
        return VkToMtl::SwizzleBlue;
    default:
        return VkToMtl::SwizzleAlpha;
    }
}

static TextureSwizzle ParseSwizzle(const char* s) {
    if (s[0] == '\0') {
        return VkToMtl::IdentitySwizzle;
    }
    return {SwizzleChannel(s[0]), SwizzleChannel(s[1]), SwizzleChannel(s[2]),
            SwizzleChannel(s[3])};
}

static MTLTextureSwizzleChannels ToChannels(TextureSwizzle s) {
    return MTLTextureSwizzleChannelsMake(static_cast<MTLTextureSwizzle>(s.r),
                                         static_cast<MTLTextureSwizzle>(s.g),
                                         static_cast<MTLTextureSwizzle>(s.b),
                                         static_cast<MTLTextureSwizzle>(s.a));
}

static bool IsBlockCompressed(MTLPixelFormat format) {
    return format >= MTLPixelFormatBC1_RGBA && format <= MTLPixelFormatBC7_RGBAUnorm_sRGB;
}

static void TestFormats(id<MTLDevice> device, const char* group, const FormatCase* cases,
                        size_t count) {
    for (size_t i = 0; i < count; ++i) {
        const FormatCase& c = cases[i];
        const std::string name = vk::to_string(c.format);
        ++g_formats;

        const auto pixel = static_cast<MTLPixelFormat>(VkToMtl::PixelFormat(c.format));
        CHECK(pixel == c.pixel, "%s %s: PixelFormat %lu, expected %lu", group, name.c_str(),
              static_cast<unsigned long>(pixel), static_cast<unsigned long>(c.pixel));
        const TextureSwizzle swizzle = VkToMtl::FormatSwizzle(c.format);
        CHECK(swizzle == ParseSwizzle(c.swizzle), "%s %s: FormatSwizzle %d%d%d%d, expected \"%s\"",
              group, name.c_str(), swizzle.r, swizzle.g, swizzle.b, swizzle.a, c.swizzle);
        const auto vertex = static_cast<MTLVertexFormat>(VkToMtl::VertexFormat(c.format));
        CHECK(vertex == c.vertex, "%s %s: VertexFormat %lu, expected %lu", group, name.c_str(),
              static_cast<unsigned long>(vertex), static_cast<unsigned long>(c.vertex));
        CHECK(VkToMtl::IsExactPixelFormat(c.format) == !ExpectInexact(c.format),
              "%s %s: IsExactPixelFormat", group, name.c_str());

        // Depth and stencil helpers, on the Vulkan and the Metal side.
        const bool depth = c.pixel == MTLPixelFormatDepth16Unorm ||
                           c.pixel == MTLPixelFormatDepth32Float ||
                           c.pixel == MTLPixelFormatDepth32Float_Stencil8;
        const bool stencil = c.pixel == MTLPixelFormatStencil8 ||
                             c.pixel == MTLPixelFormatDepth32Float_Stencil8;
        CHECK(VkToMtl::IsDepthFormat(c.format) == depth, "%s %s: IsDepthFormat", group,
              name.c_str());
        CHECK(VkToMtl::HasStencil(c.format) == stencil, "%s %s: HasStencil", group, name.c_str());
        CHECK(VkToMtl::IsDepthPixelFormat(c.pixel) == depth, "%s %s: IsDepthPixelFormat", group,
              name.c_str());
        CHECK(VkToMtl::HasStencilPixelFormat(c.pixel) == stencil, "%s %s: HasStencilPixelFormat",
              group, name.c_str());

        if (c.pixel == MTLPixelFormatInvalid) {
            if (group[0] != 'C') {
                ++g_gaps;
                std::printf("gap: %s %s has no Metal texture format\n", group, name.c_str());
            }
            continue;
        }
        if (IsBlockCompressed(c.pixel) && !device.supportsBCTextureCompression) {
            std::printf("skip: %s %s: the device has no BC support\n", group, name.c_str());
            continue;
        }
        // The format is creatable (depth formats as render targets, private storage).
        MTLTextureDescriptor* descriptor =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:c.pixel
                                                               width:8
                                                              height:8
                                                           mipmapped:NO];
        descriptor.storageMode = MTLStorageModePrivate;
        descriptor.usage = MTLTextureUsageShaderRead;
        if (depth || stencil) {
            descriptor.usage |= MTLTextureUsageRenderTarget;
        }
        id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
        CHECK(texture != nil, "%s %s: no texture of MTLPixelFormat %lu", group, name.c_str(),
              static_cast<unsigned long>(c.pixel));
        if (texture != nil && !(swizzle == VkToMtl::IdentitySwizzle)) {
            // The composed swizzle is accepted by a view.
            const TextureSwizzle composed = VkToMtl::ComposeSwizzle({}, c.format);
            id<MTLTexture> view = [texture
                newTextureViewWithPixelFormat:c.pixel
                                  textureType:MTLTextureType2D
                                       levels:NSMakeRange(0, 1)
                                       slices:NSMakeRange(0, 1)
                                      swizzle:ToChannels(composed)];
            CHECK(view != nil, "%s %s: no swizzled view", group, name.c_str());
        }
    }
}

static void TestComposeSwizzle() {
    using CS = vk::ComponentSwizzle;
    const auto compose = [](vk::ComponentMapping m, vk::Format f) {
        return VkToMtl::ComposeSwizzle(m, f);
    };
    const TextureSwizzle identity = VkToMtl::IdentitySwizzle;
    // Identity everywhere for formats without a twin swizzle, whatever the view says.
    CHECK(compose({}, vk::Format::eR8G8B8A8Unorm) == identity, "compose: identity");
    CHECK(compose({CS::eR, CS::eG, CS::eB, CS::eA}, vk::Format::eR8G8B8A8Unorm) == identity,
          "compose: explicit rgba");
    const TextureSwizzle zero_one = compose({CS::eZero, CS::eOne, CS::eB, CS::eIdentity},
                                            vk::Format::eR8G8B8A8Unorm);
    CHECK(zero_one == (TextureSwizzle{VkToMtl::SwizzleZero, VkToMtl::SwizzleOne,
                                      VkToMtl::SwizzleBlue, VkToMtl::SwizzleAlpha}),
          "compose: zero/one");
    // A view that moves channels around.
    CHECK(compose({CS::eA, CS::eR, CS::eG, CS::eB}, vk::Format::eR8G8B8A8Unorm) ==
              (TextureSwizzle{VkToMtl::SwizzleAlpha, VkToMtl::SwizzleRed, VkToMtl::SwizzleGreen,
                              VkToMtl::SwizzleBlue}),
          "compose: rotate");
    // bgra twin: the view's r reads Metal's b.
    CHECK(compose({}, vk::Format::eB8G8R8A8Snorm) ==
              (TextureSwizzle{VkToMtl::SwizzleBlue, VkToMtl::SwizzleGreen, VkToMtl::SwizzleRed,
                              VkToMtl::SwizzleAlpha}),
          "compose: bgra identity");
    CHECK(compose({CS::eB, CS::eZero, CS::eR, CS::eOne}, vk::Format::eB8G8R8A8Snorm) ==
              (TextureSwizzle{VkToMtl::SwizzleRed, VkToMtl::SwizzleZero, VkToMtl::SwizzleBlue,
                              VkToMtl::SwizzleOne}),
          "compose: bgra view");
    // BC1 without alpha: alpha is one however it is selected.
    const TextureSwizzle bc1 = {VkToMtl::SwizzleRed, VkToMtl::SwizzleGreen, VkToMtl::SwizzleBlue,
                                VkToMtl::SwizzleOne};
    CHECK(compose({}, vk::Format::eBc1RgbUnormBlock) == bc1, "compose: bc1 rgb");
    CHECK(compose({CS::eA, CS::eA, CS::eA, CS::eA}, vk::Format::eBc1RgbUnormBlock) ==
              (TextureSwizzle{VkToMtl::SwizzleOne, VkToMtl::SwizzleOne, VkToMtl::SwizzleOne,
                              VkToMtl::SwizzleOne}),
          "compose: bc1 rgb alpha broadcast");
    // A4R4G4B4 reads (A, R, G, B) as (r, g, b, a): the view's g reads Metal's b.
    CHECK(compose({CS::eIdentity, CS::eG, CS::eIdentity, CS::eIdentity},
                  vk::Format::eA4R4G4B4UnormPack16).g == VkToMtl::SwizzleBlue,
          "compose: a4r4g4b4");
}


// ---- Texel order on the GPU -------------------------------------------------------------------
// The packed formats and the BGRA twins are only right if Metal's component order (named from the
// least significant bit) matches the swizzle FormatSwizzle returns. Write a known Vulkan-packed
// word into a texture of PixelFormat(), read it through a view with the composed swizzle in a
// compute kernel and compare with the fields decoded as the Vulkan spec defines them.

struct PackedCase {
    vk::Format format;
    const char* layout; ///< Vulkan name, fields from the most significant bit: "R5G6B5"
    int bytes;
};

static const PackedCase kPackedFormats[] = {
    {vk::Format::eR5G6B5UnormPack16, "R5G6B5", 2},
    {vk::Format::eB5G6R5UnormPack16, "B5G6R5", 2},
    {vk::Format::eR5G5B5A1UnormPack16, "R5G5B5A1", 2},
    {vk::Format::eB5G5R5A1UnormPack16, "B5G5R5A1", 2},
    {vk::Format::eA1R5G5B5UnormPack16, "A1R5G5B5", 2},
    {vk::Format::eR4G4B4A4UnormPack16, "R4G4B4A4", 2},
    {vk::Format::eB4G4R4A4UnormPack16, "B4G4R4A4", 2},
    {vk::Format::eA4R4G4B4UnormPack16, "A4R4G4B4", 2},
    {vk::Format::eA4B4G4R4UnormPack16, "A4B4G4R4", 2},
    {vk::Format::eA2B10G10R10UnormPack32, "A2B10G10R10", 4},
    {vk::Format::eA2R10G10B10UnormPack32, "A2R10G10B10", 4},
};

static NSString* const kKernelSource = @"#include <metal_stdlib>\n"
                                        "using namespace metal;\n"
                                        "kernel void read_float(texture2d<float, access::read> t "
                                        "[[texture(0)]], device float4* out [[buffer(0)]]) {\n"
                                        "    out[0] = t.read(uint2(0, 0));\n"
                                        "}\n"
                                        "kernel void read_uint(texture2d<uint, access::read> t "
                                        "[[texture(0)]], device uint4* out [[buffer(0)]]) {\n"
                                        "    out[0] = t.read(uint2(0, 0));\n"
                                        "}\n";

/// Reads texel (0, 0) of `view` with the kernel `name` into 4 32-bit values (floats or uints).
static bool ReadTexel(id<MTLDevice> device, id<MTLCommandQueue> queue, id<MTLLibrary> library,
                      NSString* name, id<MTLTexture> view, void* out) {
    id<MTLFunction> function = [library newFunctionWithName:name];
    NSError* error = nil;
    id<MTLComputePipelineState> pipeline = [device newComputePipelineStateWithFunction:function
                                                                                 error:&error];
    if (pipeline == nil) {
        return false;
    }
    id<MTLBuffer> buffer = [device newBufferWithLength:16 options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> commands = [queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setTexture:view atIndex:0];
    [encoder setBuffer:buffer offset:0 atIndex:0];
    [encoder dispatchThreads:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    [encoder endEncoding];
    [commands commit];
    [commands waitUntilCompleted];
    std::memcpy(out, buffer.contents, 16);
    return commands.status == MTLCommandBufferStatusCompleted;
}

/// Decodes `word` by the Vulkan packing (fields in the name's order from the MSB); channels the
/// layout lacks read (0, 0, 0, 1).
static void DecodePacked(const char* layout, u32 word, int bits, float rgba[4]) {
    rgba[0] = rgba[1] = rgba[2] = 0.0f;
    rgba[3] = 1.0f;
    int shift = bits;
    for (const char* p = layout; *p != '\0';) {
        const char channel = *p++;
        int width = 0;
        while (*p >= '0' && *p <= '9') {
            width = width * 10 + (*p++ - '0');
        }
        shift -= width;
        const u32 value = (word >> shift) & ((1u << width) - 1);
        const float f = static_cast<float>(value) / static_cast<float>((1u << width) - 1);
        rgba[channel == 'R' ? 0 : channel == 'G' ? 1 : channel == 'B' ? 2 : 3] = f;
    }
}

static void TestTexelOrder(id<MTLDevice> device) {
    id<MTLCommandQueue> queue = [device newCommandQueue];
    NSError* error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:kKernelSource options:nil error:&error];
    if (library == nil) {
        CHECK(false, "texel order: kernel did not compile: %s",
              error.localizedDescription.UTF8String);
        return;
    }
    static const u32 words[] = {0x2C5A9E71u, 0xD3A7418Cu, 0x5B0E67F2u};
    for (const auto& c : kPackedFormats) {
        const std::string name = vk::to_string(c.format);
        const auto pixel = static_cast<MTLPixelFormat>(VkToMtl::PixelFormat(c.format));
        for (const u32 word : words) {
            const u32 value = c.bytes == 2 ? (word & 0xFFFFu) : word;
            MTLTextureDescriptor* descriptor =
                [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
                                                                   width:1
                                                                  height:1
                                                               mipmapped:NO];
            descriptor.storageMode = MTLStorageModeShared;
            descriptor.usage = MTLTextureUsageShaderRead;
            id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
            CHECK(texture != nil, "texel order %s: no texture", name.c_str());
            if (texture == nil) {
                continue;
            }
            [texture replaceRegion:MTLRegionMake2D(0, 0, 1, 1)
                       mipmapLevel:0
                         withBytes:&value
                       bytesPerRow:c.bytes];
            const TextureSwizzle swizzle = VkToMtl::ComposeSwizzle({}, c.format);
            id<MTLTexture> view = [texture newTextureViewWithPixelFormat:pixel
                                                             textureType:MTLTextureType2D
                                                                  levels:NSMakeRange(0, 1)
                                                                  slices:NSMakeRange(0, 1)
                                                                 swizzle:ToChannels(swizzle)];
            float got[4]{};
            float expected[4];
            DecodePacked(c.layout, value, c.bytes * 8, expected);
            CHECK(ReadTexel(device, queue, library, @"read_float", view, got),
                  "texel order %s: kernel failed", name.c_str());
            bool equal = true;
            for (int i = 0; i < 4; ++i) {
                equal = equal && std::abs(got[i] - expected[i]) < 0.003f;
            }
            CHECK(equal, "texel order %s word %08x: got (%.3f %.3f %.3f %.3f), expected "
                         "(%.3f %.3f %.3f %.3f)",
                  name.c_str(), value, got[0], got[1], got[2], got[3], expected[0], expected[1],
                  expected[2], expected[3]);
        }
    }

    // B8G8R8A8 Uint: memory bytes are B, G, R, A.
    const u8 bgra[4] = {0x11, 0x22, 0x33, 0x44};
    MTLTextureDescriptor* descriptor = [MTLTextureDescriptor
        texture2DDescriptorWithPixelFormat:static_cast<MTLPixelFormat>(
                                               VkToMtl::PixelFormat(vk::Format::eB8G8R8A8Uint))
                                     width:1
                                    height:1
                                 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    [texture replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:bgra bytesPerRow:4];
    id<MTLTexture> view = [texture
        newTextureViewWithPixelFormat:descriptor.pixelFormat
                          textureType:MTLTextureType2D
                               levels:NSMakeRange(0, 1)
                               slices:NSMakeRange(0, 1)
                              swizzle:ToChannels(
                                          VkToMtl::ComposeSwizzle({}, vk::Format::eB8G8R8A8Uint))];
    u32 got[4]{};
    CHECK(ReadTexel(device, queue, library, @"read_uint", view, got), "texel order bgra8: kernel");
    CHECK(got[0] == 0x33 && got[1] == 0x22 && got[2] == 0x11 && got[3] == 0x44,
          "texel order B8G8R8A8Uint: got (%x %x %x %x)", got[0], got[1], got[2], got[3]);
}

template <typename Vk, typename Mtl>
struct EnumCase {
    Vk vk;
    Mtl mtl;
};

template <typename Vk, typename Mtl, size_t N, typename Fn>
static void TestEnum(const char* what, const EnumCase<Vk, Mtl> (&cases)[N], Fn convert) {
    for (const auto& c : cases) {
        const auto got = convert(c.vk);
        CHECK(static_cast<unsigned long>(got) == static_cast<unsigned long>(c.mtl),
              "%s(%s): %lu, expected %lu", what, vk::to_string(c.vk).c_str(),
              static_cast<unsigned long>(got), static_cast<unsigned long>(c.mtl));
    }
}

static void TestEnums() {
    using BF = vk::BlendFactor;
    static const EnumCase<BF, MTLBlendFactor> blend_factors[] = {
        {BF::eZero, MTLBlendFactorZero},
        {BF::eOne, MTLBlendFactorOne},
        {BF::eSrcColor, MTLBlendFactorSourceColor},
        {BF::eOneMinusSrcColor, MTLBlendFactorOneMinusSourceColor},
        {BF::eDstColor, MTLBlendFactorDestinationColor},
        {BF::eOneMinusDstColor, MTLBlendFactorOneMinusDestinationColor},
        {BF::eSrcAlpha, MTLBlendFactorSourceAlpha},
        {BF::eOneMinusSrcAlpha, MTLBlendFactorOneMinusSourceAlpha},
        {BF::eDstAlpha, MTLBlendFactorDestinationAlpha},
        {BF::eOneMinusDstAlpha, MTLBlendFactorOneMinusDestinationAlpha},
        {BF::eConstantColor, MTLBlendFactorBlendColor},
        {BF::eOneMinusConstantColor, MTLBlendFactorOneMinusBlendColor},
        {BF::eConstantAlpha, MTLBlendFactorBlendAlpha},
        {BF::eOneMinusConstantAlpha, MTLBlendFactorOneMinusBlendAlpha},
        {BF::eSrcAlphaSaturate, MTLBlendFactorSourceAlphaSaturated},
        {BF::eSrc1Color, MTLBlendFactorSource1Color},
        {BF::eOneMinusSrc1Color, MTLBlendFactorOneMinusSource1Color},
        {BF::eSrc1Alpha, MTLBlendFactorSource1Alpha},
        {BF::eOneMinusSrc1Alpha, MTLBlendFactorOneMinusSource1Alpha},
    };
    TestEnum("BlendFactor", blend_factors, VkToMtl::BlendFactor);

    using BO = vk::BlendOp;
    static const EnumCase<BO, MTLBlendOperation> blend_ops[] = {
        {BO::eAdd, MTLBlendOperationAdd},
        {BO::eSubtract, MTLBlendOperationSubtract},
        {BO::eReverseSubtract, MTLBlendOperationReverseSubtract},
        {BO::eMin, MTLBlendOperationMin},
        {BO::eMax, MTLBlendOperationMax},
    };
    TestEnum("BlendOp", blend_ops, VkToMtl::BlendOp);

    // Color write mask: all 16 combinations, bit by bit.
    for (u32 bits = 0; bits < 16; ++bits) {
        vk::ColorComponentFlags flags{};
        MTLColorWriteMask expected = MTLColorWriteMaskNone;
        if (bits & 1) {
            flags |= vk::ColorComponentFlagBits::eR;
            expected |= MTLColorWriteMaskRed;
        }
        if (bits & 2) {
            flags |= vk::ColorComponentFlagBits::eG;
            expected |= MTLColorWriteMaskGreen;
        }
        if (bits & 4) {
            flags |= vk::ColorComponentFlagBits::eB;
            expected |= MTLColorWriteMaskBlue;
        }
        if (bits & 8) {
            flags |= vk::ColorComponentFlagBits::eA;
            expected |= MTLColorWriteMaskAlpha;
        }
        CHECK(VkToMtl::ColorWriteMask(flags) == expected, "ColorWriteMask(%u)", bits);
    }

    using CO = vk::CompareOp;
    static const EnumCase<CO, MTLCompareFunction> compares[] = {
        {CO::eNever, MTLCompareFunctionNever},
        {CO::eLess, MTLCompareFunctionLess},
        {CO::eEqual, MTLCompareFunctionEqual},
        {CO::eLessOrEqual, MTLCompareFunctionLessEqual},
        {CO::eGreater, MTLCompareFunctionGreater},
        {CO::eNotEqual, MTLCompareFunctionNotEqual},
        {CO::eGreaterOrEqual, MTLCompareFunctionGreaterEqual},
        {CO::eAlways, MTLCompareFunctionAlways},
    };
    TestEnum("CompareFunction", compares, VkToMtl::CompareFunction);
    TestEnum("SamplerCompare", compares, VkToMtl::SamplerCompare);

    using SO = vk::StencilOp;
    static const EnumCase<SO, MTLStencilOperation> stencil_ops[] = {
        {SO::eKeep, MTLStencilOperationKeep},
        {SO::eZero, MTLStencilOperationZero},
        {SO::eReplace, MTLStencilOperationReplace},
        {SO::eIncrementAndClamp, MTLStencilOperationIncrementClamp},
        {SO::eDecrementAndClamp, MTLStencilOperationDecrementClamp},
        {SO::eInvert, MTLStencilOperationInvert},
        {SO::eIncrementAndWrap, MTLStencilOperationIncrementWrap},
        {SO::eDecrementAndWrap, MTLStencilOperationDecrementWrap},
    };
    TestEnum("StencilOp", stencil_ops, VkToMtl::StencilOp);

    // Every topology liverpool_to_vk.cpp PrimitiveType returns, plus the others of the enum.
    using PT = vk::PrimitiveTopology;
    struct TopologyCase {
        PT topology;
        MTLPrimitiveType type;
        bool emulated;
    };
    static const TopologyCase topologies[] = {
        {PT::ePointList, MTLPrimitiveTypePoint, false},
        {PT::eLineList, MTLPrimitiveTypeLine, false},
        {PT::eLineStrip, MTLPrimitiveTypeLineStrip, false},
        {PT::eTriangleList, MTLPrimitiveTypeTriangle, false},
        {PT::eTriangleStrip, MTLPrimitiveTypeTriangleStrip, false},
        {PT::eTriangleFan, MTLPrimitiveTypeTriangle, true},
        {PT::eLineListWithAdjacency, MTLPrimitiveTypeLine, true},
        {PT::eLineStripWithAdjacency, MTLPrimitiveTypeLineStrip, true},
        {PT::eTriangleListWithAdjacency, MTLPrimitiveTypeTriangle, true},
        {PT::eTriangleStripWithAdjacency, MTLPrimitiveTypeTriangleStrip, true},
        {PT::ePatchList, MTLPrimitiveTypeTriangle, true},
    };
    for (const auto& c : topologies) {
        const auto got = VkToMtl::PrimitiveType(c.topology);
        CHECK(got.type == c.type && got.needs_emulation == c.emulated,
              "PrimitiveType(%s): {%u, %d}", vk::to_string(c.topology).c_str(), got.type,
              got.needs_emulation);
    }

    using CM = vk::CullModeFlagBits;
    struct CullCase {
        vk::CullModeFlags flags;
        MTLCullMode mode;
        bool cull_all;
    };
    static const CullCase culls[] = {
        {CM::eNone, MTLCullModeNone, false},
        {CM::eFront, MTLCullModeFront, false},
        {CM::eBack, MTLCullModeBack, false},
        {CM::eFrontAndBack, MTLCullModeNone, true},
    };
    for (const auto& c : culls) {
        const auto got = VkToMtl::CullMode(c.flags);
        CHECK(got.mode == c.mode && got.cull_all == c.cull_all, "CullMode(%s)",
              vk::to_string(c.flags).c_str());
    }

    CHECK(VkToMtl::Winding(vk::FrontFace::eClockwise) == MTLWindingClockwise, "Winding cw");
    CHECK(VkToMtl::Winding(vk::FrontFace::eCounterClockwise) == MTLWindingCounterClockwise,
          "Winding ccw");

    using PM = vk::PolygonMode;
    struct FillCase {
        PM mode;
        MTLTriangleFillMode fill;
        bool emulated;
    };
    static const FillCase fills[] = {
        {PM::eFill, MTLTriangleFillModeFill, false},
        {PM::eLine, MTLTriangleFillModeLines, false},
        {PM::ePoint, MTLTriangleFillModeFill, true},
    };
    for (const auto& c : fills) {
        const auto got = VkToMtl::PolygonMode(c.mode);
        CHECK(got.mode == c.fill && got.needs_emulation == c.emulated, "PolygonMode(%s)",
              vk::to_string(c.mode).c_str());
    }

    using IT = vk::IndexType;
    struct IndexCase {
        IT type;
        MTLIndexType mtl;
        bool convert;
    };
    static const IndexCase indices[] = {
        {IT::eUint16, MTLIndexTypeUInt16, false},
        {IT::eUint32, MTLIndexTypeUInt32, false},
        {IT::eUint8, MTLIndexTypeUInt16, true},
    };
    for (const auto& c : indices) {
        const auto got = VkToMtl::IndexType(c.type);
        CHECK(got.type == c.mtl && got.needs_conversion == c.convert, "IndexType(%s)",
              vk::to_string(c.type).c_str());
    }

    static const EnumCase<vk::Filter, MTLSamplerMinMagFilter> filters[] = {
        {vk::Filter::eNearest, MTLSamplerMinMagFilterNearest},
        {vk::Filter::eLinear, MTLSamplerMinMagFilterLinear},
    };
    TestEnum("SamplerFilter", filters, VkToMtl::SamplerFilter);

    static const EnumCase<vk::SamplerMipmapMode, MTLSamplerMipFilter> mips[] = {
        {vk::SamplerMipmapMode::eNearest, MTLSamplerMipFilterNearest},
        {vk::SamplerMipmapMode::eLinear, MTLSamplerMipFilterLinear},
    };
    TestEnum("MipFilter", mips, VkToMtl::MipFilter);

    using SA = vk::SamplerAddressMode;
    static const EnumCase<SA, MTLSamplerAddressMode> addresses[] = {
        {SA::eRepeat, MTLSamplerAddressModeRepeat},
        {SA::eMirroredRepeat, MTLSamplerAddressModeMirrorRepeat},
        {SA::eClampToEdge, MTLSamplerAddressModeClampToEdge},
        {SA::eClampToBorder, MTLSamplerAddressModeClampToBorderColor},
        {SA::eMirrorClampToEdge, MTLSamplerAddressModeMirrorClampToEdge},
    };
    TestEnum("AddressMode", addresses, VkToMtl::AddressMode);

    using BC = vk::BorderColor;
    struct BorderCase {
        BC color;
        MTLSamplerBorderColor mtl;
        bool supported;
    };
    static const BorderCase borders[] = {
        {BC::eFloatTransparentBlack, MTLSamplerBorderColorTransparentBlack, true},
        {BC::eIntTransparentBlack, MTLSamplerBorderColorTransparentBlack, true},
        {BC::eFloatOpaqueBlack, MTLSamplerBorderColorOpaqueBlack, true},
        {BC::eIntOpaqueBlack, MTLSamplerBorderColorOpaqueBlack, true},
        {BC::eFloatOpaqueWhite, MTLSamplerBorderColorOpaqueWhite, true},
        {BC::eIntOpaqueWhite, MTLSamplerBorderColorOpaqueWhite, true},
        {BC::eFloatCustomEXT, MTLSamplerBorderColorTransparentBlack, false},
        {BC::eIntCustomEXT, MTLSamplerBorderColorTransparentBlack, false},
    };
    for (const auto& c : borders) {
        const auto got = VkToMtl::BorderColor(c.color);
        CHECK(got.color == c.mtl && got.supported == c.supported, "BorderColor(%s)",
              vk::to_string(c.color).c_str());
    }
}

/// The sampler and the depth/stencil state built from the converted values are accepted by the
/// device (catches values outside the Metal enums).
static void TestObjects(id<MTLDevice> device) {
    MTLSamplerDescriptor* sampler = [MTLSamplerDescriptor new];
    for (const auto mode : {vk::SamplerAddressMode::eRepeat, vk::SamplerAddressMode::eMirroredRepeat,
                            vk::SamplerAddressMode::eClampToEdge,
                            vk::SamplerAddressMode::eClampToBorder,
                            vk::SamplerAddressMode::eMirrorClampToEdge}) {
        sampler.sAddressMode = static_cast<MTLSamplerAddressMode>(VkToMtl::AddressMode(mode));
        sampler.borderColor = static_cast<MTLSamplerBorderColor>(
            VkToMtl::BorderColor(vk::BorderColor::eFloatOpaqueWhite).color);
        sampler.minFilter = static_cast<MTLSamplerMinMagFilter>(
            VkToMtl::SamplerFilter(vk::Filter::eLinear));
        sampler.mipFilter = static_cast<MTLSamplerMipFilter>(
            VkToMtl::MipFilter(vk::SamplerMipmapMode::eLinear));
        sampler.compareFunction = static_cast<MTLCompareFunction>(
            VkToMtl::SamplerCompare(vk::CompareOp::eLessOrEqual));
        CHECK([device newSamplerStateWithDescriptor:sampler] != nil, "sampler for %s",
              vk::to_string(mode).c_str());
    }
    MTLStencilDescriptor* stencil = [MTLStencilDescriptor new];
    stencil.stencilCompareFunction = static_cast<MTLCompareFunction>(
        VkToMtl::CompareFunction(vk::CompareOp::eAlways));
    stencil.stencilFailureOperation = static_cast<MTLStencilOperation>(
        VkToMtl::StencilOp(vk::StencilOp::eIncrementAndWrap));
    MTLDepthStencilDescriptor* depth_stencil = [MTLDepthStencilDescriptor new];
    depth_stencil.depthCompareFunction = static_cast<MTLCompareFunction>(
        VkToMtl::CompareFunction(vk::CompareOp::eGreaterOrEqual));
    depth_stencil.frontFaceStencil = stencil;
    depth_stencil.backFaceStencil = stencil;
    CHECK([device newDepthStencilStateWithDescriptor:depth_stencil] != nil, "depth stencil state");
}

// P10.1: which views of a texture made without MTLTextureUsagePixelFormatView are legal (step 1 of
// docs/metal/texture_compression_design.md: the sRGB twin and same-format views need nothing, any
// other format of the same texel size needs the usage, an incompatible format is not made at all).
static void TestViewUsage() {
    using F = vk::Format;
    const auto needs = [](F base, F view) { return VkToMtl::ViewNeedsPixelFormatUsage(base, view); };
    // Needs nothing
    CHECK(!needs(F::eR8G8B8A8Unorm, F::eR8G8B8A8Unorm), "same format");
    CHECK(!needs(F::eR8G8B8A8Unorm, F::eR8G8B8A8Srgb), "rgba8 -> its sRGB twin");
    CHECK(!needs(F::eR8G8B8A8Srgb, F::eR8G8B8A8Unorm), "rgba8 sRGB -> its linear twin");
    CHECK(!needs(F::eB8G8R8A8Srgb, F::eB8G8R8A8Unorm), "bgra8 sRGB -> linear");
    CHECK(!needs(F::eBc1RgbaUnormBlock, F::eBc1RgbaSrgbBlock), "bc1 -> its sRGB twin");
    CHECK(!needs(F::eBc7UnormBlock, F::eBc7SrgbBlock), "bc7 -> its sRGB twin");
    // Needs the usage: another format of the same texel size
    CHECK(needs(F::eR16G16B16A16Sfloat, F::eR32G32Uint), "rgba16f -> rg32 uint");
    CHECK(needs(F::eR8G8B8A8Unorm, F::eR8G8B8A8Uint), "rgba8 -> rgba8 uint");
    CHECK(needs(F::eR8G8B8A8Unorm, F::eR32Uint), "rgba8 -> r32 uint");
    CHECK(needs(F::eR8G8B8A8Unorm, F::eB8G8R8A8Unorm), "rgba8 -> bgra8");
    CHECK(needs(F::eR8G8B8A8Srgb, F::eB8G8R8A8Srgb), "rgba8 sRGB -> bgra8 sRGB (display buffers)");
    CHECK(needs(F::eR8G8B8A8Uint, F::eR8G8B8A8Srgb), "rgba8 uint -> rgba8 sRGB");
    CHECK(needs(F::eR16G16B16A16Sfloat, F::eR16G16B16A16Uint), "rgba16f -> rgba16 uint");
    CHECK(needs(F::eA2B10G10R10UnormPack32, F::eR32Uint), "rgb10a2 -> r32 uint");
    // Not made (CreateImageView falls back to the base format): nothing to promote for
    CHECK(!needs(F::eR8G8B8A8Unorm, F::eR16Sfloat), "different texel size is not a view");
    CHECK(!needs(F::eB10G11R11UfloatPack32, F::eR32Uint), "rg11b10f has no views");
    CHECK(!needs(F::eD32SfloatS8Uint, F::eR32Sfloat), "depth is never promoted this way");
    CHECK(!needs(F::eBc1RgbaUnormBlock, F::eR32G32Uint), "bc1 -> rg32 uint is refused");
    CHECK(!needs(F::eBc1RgbaUnormBlock, F::eBc3UnormBlock), "bc1 -> bc3 is another family");
    CHECK(!needs(F::eUndefined, F::eR8G8B8A8Unorm), "undefined base");
    // The twin relation is symmetric and only about sRGB-ness.
    const u32 rgba8 = VkToMtl::PixelFormat(F::eR8G8B8A8Unorm);
    const u32 rgba8_srgb = VkToMtl::PixelFormat(F::eR8G8B8A8Srgb);
    const u32 bgra8_srgb = VkToMtl::PixelFormat(F::eB8G8R8A8Srgb);
    CHECK(VkToMtl::IsSrgbTwinPixelFormat(rgba8, rgba8_srgb) &&
              VkToMtl::IsSrgbTwinPixelFormat(rgba8_srgb, rgba8),
          "twin symmetric");
    CHECK(!VkToMtl::IsSrgbTwinPixelFormat(rgba8, rgba8), "a format is not its own twin");
    CHECK(!VkToMtl::IsSrgbTwinPixelFormat(rgba8_srgb, bgra8_srgb), "rgba and bgra differ");
    CHECK(!VkToMtl::IsSrgbTwinPixelFormat(0, 0), "invalid");
}

int main() {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            std::printf("FAIL: no Metal device\n");
            return 1;
        }
        std::printf("device: %s, BC textures: %s\n", device.name.UTF8String,
                    device.supportsBCTextureCompression ? "yes" : "no");

        // The groups together must not list a format twice.
        std::set<vk::Format> seen;
        const auto add = [&](const FormatCase* cases, size_t count) {
            for (size_t i = 0; i < count; ++i) {
                CHECK(seen.insert(cases[i].format).second, "duplicate row for %s",
                      vk::to_string(cases[i].format).c_str());
            }
        };
        add(kSurfaceFormats, std::size(kSurfaceFormats));
        add(kBackendFormats, std::size(kBackendFormats));
        add(kOtherFormats, std::size(kOtherFormats));

        TestFormats(device, "A", kSurfaceFormats, std::size(kSurfaceFormats));
        TestFormats(device, "B", kBackendFormats, std::size(kBackendFormats));
        TestFormats(device, "C", kOtherFormats, std::size(kOtherFormats));
        CHECK(VkToMtl::PixelFormat(vk::Format::eUndefined) == MTLPixelFormatInvalid,
              "Undefined has a pixel format");
        CHECK(VkToMtl::VertexFormat(vk::Format::eUndefined) == MTLVertexFormatInvalid,
              "Undefined has a vertex format");
        TestComposeSwizzle();
        TestViewUsage();
        TestEnums();
        TestObjects(device);
        TestTexelOrder(device);
    }
    std::printf("%d known gaps (formats without a Metal texture format)\n", g_gaps);
    std::printf("vk-to-mtl-test: %d formats, %d checks, %d failures\n", g_formats, g_checks,
                g_failures);
    return g_failures == 0 ? 0 : 1;
}
