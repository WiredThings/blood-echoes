#include "video_core/renderer_metal/mtl_local_library.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// Guest formats Metal has no texture for: the table, and the kernels that convert the bytes of a
// transfer (design in mtl_format_convert.h).

#include <algorithm>
#include <cstring>
#include <string>

#include "common/assert.h"
#include "common/logging/log.h"
#include "video_core/renderer_metal/mtl_format_convert_impl.h"

#include <vulkan/vulkan_format_traits.hpp>

namespace Metal::FormatConvert {

namespace {

/// The kernels. One thread per texel; every access is by byte (the guest side has no alignment:
/// a 3-component texel is 3, 6 or 12 bytes and buffers hold mips at any offset).
///
/// `rgb_convert`: to host (mode 0) copies the 3 components and writes `one` (the bits of 1 in a
/// component, comp_bytes long) as the 4th; to guest (mode 1) drops the 4th.
///
/// `depth_convert`: to host reads 2 (comp_bytes) or 4 bytes little endian, keeps the low `one`
/// (the depth bits) and stores code / (2^bits - 1) as float32; to guest turns the float back into
/// the code nearest to it, rint(f * max) clamped. The division is correctly rounded
/// (precise::divide), so f is within half an ulp (2^-25 below 1) of code / max, which makes
/// f * max less than 0.5 away from the code for 16 and 24 bits alike: the round trip of every
/// code is exact (mtl-format-convert-test runs all of them). The unused top byte of a 4 byte
/// texel is written as zero.
NSString* const Source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct Params {\n"
    "    ulong guest_offset;\n"
    "    ulong host_offset;\n"
    "    uint width, height, slices;\n"
    "    uint mode;\n"
    "    uint guest_row_pitch, guest_slice_pitch;\n"
    "    uint host_row_pitch, host_slice_pitch;\n"
    "    uint comp_bytes;\n"
    "    uint one;\n"
    "};\n"
    "kernel void rgb_convert(device uchar* guest [[buffer(0)]], device uchar* host [[buffer(1)]],\n"
    "                        constant Params& p [[buffer(2)]], uint3 id [[thread_position_in_grid]]) {\n"
    "    if (id.x >= p.width || id.y >= p.height || id.z >= p.slices) {\n"
    "        return;\n"
    "    }\n"
    "    const uint n = p.comp_bytes;\n"
    "    const ulong g = p.guest_offset + ulong(id.z) * p.guest_slice_pitch +\n"
    "                    ulong(id.y) * p.guest_row_pitch + ulong(id.x) * (3 * n);\n"
    "    const ulong h = p.host_offset + ulong(id.z) * p.host_slice_pitch +\n"
    "                    ulong(id.y) * p.host_row_pitch + ulong(id.x) * (4 * n);\n"
    "    if (p.mode == 0) {\n"
    "        for (uint i = 0; i < 3 * n; ++i) {\n"
    "            host[h + i] = guest[g + i];\n"
    "        }\n"
    "        for (uint i = 0; i < n; ++i) {\n"
    "            host[h + 3 * n + i] = uchar(p.one >> (8 * i));\n"
    "        }\n"
    "    } else {\n"
    "        for (uint i = 0; i < 3 * n; ++i) {\n"
    "            guest[g + i] = host[h + i];\n"
    "        }\n"
    "    }\n"
    "}\n"
    "kernel void depth_convert(device uchar* guest [[buffer(0)]], device uchar* host [[buffer(1)]],\n"
    "                          constant Params& p [[buffer(2)]], uint3 id [[thread_position_in_grid]]) {\n"
    "    if (id.x >= p.width || id.y >= p.height || id.z >= p.slices) {\n"
    "        return;\n"
    "    }\n"
    "    const uint n = p.comp_bytes;\n"
    "    const ulong g = p.guest_offset + ulong(id.z) * p.guest_slice_pitch +\n"
    "                    ulong(id.y) * p.guest_row_pitch + ulong(id.x) * n;\n"
    "    const ulong h = p.host_offset + ulong(id.z) * p.host_slice_pitch +\n"
    "                    ulong(id.y) * p.host_row_pitch + ulong(id.x) * 4;\n"
    "    const uint max_code = (1u << p.one) - 1u;\n"
    "    const float max_float = float(max_code);\n"
    "    if (p.mode == 0) {\n"
    "        uint code = 0;\n"
    "        for (uint i = 0; i < n; ++i) {\n"
    "            code |= uint(guest[g + i]) << (8 * i);\n"
    "        }\n"
    "        code &= max_code;\n"
    "        const float value = precise::divide(float(code), max_float);\n"
    "        const uint bits = as_type<uint>(value);\n"
    "        for (uint i = 0; i < 4; ++i) {\n"
    "            host[h + i] = uchar(bits >> (8 * i));\n"
    "        }\n"
    "    } else {\n"
    "        uint bits = 0;\n"
    "        for (uint i = 0; i < 4; ++i) {\n"
    "            bits |= uint(host[h + i]) << (8 * i);\n"
    "        }\n"
    "        float value = as_type<float>(bits);\n"
    "        value = isnan(value) ? 0.0f : clamp(value, 0.0f, 1.0f);\n"
    "        const int guess = clamp(int(rint(value * max_float)), 0, int(max_code));\n"
    "        const uint code = uint(guess);\n"
    "        for (uint i = 0; i < n; ++i) {\n"
    "            guest[g + i] = uchar(code >> (8 * i));\n"
    "        }\n"
    "    }\n"
    "}\n";

/// The struct `Params` of the kernels.
struct Params {
    u64 guest_offset;
    u64 host_offset;
    u32 width, height, slices;
    u32 mode;
    u32 guest_row_pitch, guest_slice_pitch;
    u32 host_row_pitch, host_slice_pitch;
    u32 comp_bytes;
    u32 one;
};
static_assert(sizeof(Params) == 56);

/// The bits of 1 in one component of the (RGBA) host format.
u32 OneBits(vk::Format format, u32 comp_bytes) {
    const char* numeric = vk::componentNumericFormat(format, 0);
    const auto is = [&](const char* name) { return std::strcmp(numeric, name) == 0; };
    if (is("UINT") || is("SINT")) {
        return 1;
    }
    switch (comp_bytes) {
    case 1:
        return is("SNORM") ? 0x7Fu : 0xFFu; // UNORM and SRGB
    case 2:
        if (is("SFLOAT")) {
            return 0x3C00;
        }
        return is("SNORM") ? 0x7FFFu : 0xFFFFu;
    default:
        return 0x3F800000u; // 32 bit float (the integer formats returned above)
    }
}

} // namespace

vk::Format RgbaFormatOf(vk::Format guest) {
    using F = vk::Format;
    switch (guest) {
    case F::eR8G8B8Unorm:
        return F::eR8G8B8A8Unorm;
    case F::eR8G8B8Snorm:
        return F::eR8G8B8A8Snorm;
    case F::eR8G8B8Uint:
        return F::eR8G8B8A8Uint;
    case F::eR8G8B8Sint:
        return F::eR8G8B8A8Sint;
    case F::eR8G8B8Srgb:
        return F::eR8G8B8A8Srgb;
    case F::eB8G8R8Unorm:
        return F::eB8G8R8A8Unorm;
    case F::eB8G8R8Snorm:
        return F::eB8G8R8A8Snorm;
    case F::eB8G8R8Uint:
        return F::eB8G8R8A8Uint;
    case F::eB8G8R8Sint:
        return F::eB8G8R8A8Sint;
    case F::eB8G8R8Srgb:
        return F::eB8G8R8A8Srgb;
    case F::eR16G16B16Unorm:
        return F::eR16G16B16A16Unorm;
    case F::eR16G16B16Snorm:
        return F::eR16G16B16A16Snorm;
    case F::eR16G16B16Uint:
        return F::eR16G16B16A16Uint;
    case F::eR16G16B16Sint:
        return F::eR16G16B16A16Sint;
    case F::eR16G16B16Sfloat:
        return F::eR16G16B16A16Sfloat;
    case F::eR32G32B32Uint:
        return F::eR32G32B32A32Uint;
    case F::eR32G32B32Sint:
        return F::eR32G32B32A32Sint;
    case F::eR32G32B32Sfloat:
        return F::eR32G32B32A32Sfloat;
    default:
        return F::eUndefined;
    }
}

bool IsPackedDepth(vk::Format guest) {
    return guest == vk::Format::eD24UnormS8Uint || guest == vk::Format::eD16UnormS8Uint ||
           guest == vk::Format::eX8D24UnormPack32;
}

bool Convertible(vk::Format guest, vk::Format host) {
    if (IsPackedDepth(guest)) {
        return true;
    }
    const vk::Format rgba = RgbaFormatOf(guest);
    return rgba != vk::Format::eUndefined && rgba == host;
}

Conversion Describe(vk::Format guest, vk::Format host, vk::ImageAspectFlags aspect) {
    Conversion conversion;
    if (IsPackedDepth(guest)) {
        if (aspect == vk::ImageAspectFlagBits::eStencil) {
            return conversion; // a byte per texel on both sides
        }
        conversion.kind = Kind::DepthUnorm;
        conversion.depth_bits = guest == vk::Format::eD16UnormS8Uint ? 16 : 24;
        conversion.comp_bytes = guest == vk::Format::eD16UnormS8Uint ? 2 : 4;
        conversion.guest_texel_bytes = conversion.comp_bytes;
        conversion.host_texel_bytes = 4; // Depth32Float
        return conversion;
    }
    const vk::Format rgba = RgbaFormatOf(guest);
    if (rgba != vk::Format::eUndefined && rgba == host) {
        conversion.kind = Kind::RgbPad;
        conversion.guest_texel_bytes = vk::blockSize(guest);
        conversion.comp_bytes = conversion.guest_texel_bytes / 3;
        conversion.host_texel_bytes = conversion.comp_bytes * 4;
        conversion.one = OneBits(host, conversion.comp_bytes);
    }
    return conversion;
}

std::atomic<u32>& UnhandledCount() {
    static std::atomic<u32> count{};
    return count;
}

u64 Converter::HostBytes(const Conversion& conversion, const Region& region) {
    return u64{conversion.host_texel_bytes} * region.width * region.height * region.slices;
}

id<MTLComputePipelineState> Converter::Pipeline(Kind kind) {
    __strong id<MTLComputePipelineState>& slot = kind == Kind::RgbPad ? rgb : depth;
    if (slot) {
        return slot;
    }
    NSError* error = nil;
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    options.mathMode = MTLMathModeSafe;
    id<MTLLibrary> library = Metal::LocalLibrary::Build(device, Source, options, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"rgb_convert", @"depth_convert"}); });
    ASSERT_MSG(library, "Format conversion kernels did not compile: {}",
               error ? error.localizedDescription.UTF8String : "unknown");
    NSString* name = kind == Kind::RgbPad ? @"rgb_convert" : @"depth_convert";
    slot = [device newComputePipelineStateWithFunction:[library newFunctionWithName:name]
                                                 error:&error];
    ASSERT_MSG(slot, "Format conversion pipeline {} failed: {}", name.UTF8String,
               error ? error.localizedDescription.UTF8String : "unknown");
    return slot;
}

void Converter::Encode(id<MTLComputeCommandEncoder> compute, const Conversion& conversion,
                       bool to_host, const GuestSide& guest, const HostSide& host,
                       const Region& region) {
    ASSERT(conversion.kind != Kind::None);
    if (region.width == 0 || region.height == 0 || region.slices == 0) {
        return;
    }
    id<MTLComputePipelineState> pipeline = Pipeline(conversion.kind);
    const u32 host_row_pitch = region.width * conversion.host_texel_bytes;
    const Params params{
        .guest_offset = guest.offset,
        .host_offset = host.offset,
        .width = region.width,
        .height = region.height,
        .slices = region.slices,
        .mode = to_host ? 0u : 1u,
        .guest_row_pitch = guest.row_pitch,
        .guest_slice_pitch = guest.slice_pitch,
        .host_row_pitch = host_row_pitch,
        .host_slice_pitch = host_row_pitch * region.height,
        .comp_bytes = conversion.comp_bytes,
        .one = conversion.kind == Kind::RgbPad ? conversion.one : conversion.depth_bits,
    };
    [compute setComputePipelineState:pipeline];
    [compute setBuffer:guest.buffer offset:0 atIndex:0];
    [compute setBuffer:host.buffer offset:0 atIndex:1];
    [compute setBytes:&params length:sizeof(params) atIndex:2];
    const NSUInteger width = 16;
    const NSUInteger height =
        std::min<NSUInteger>(16, pipeline.maxTotalThreadsPerThreadgroup / width);
    [compute dispatchThreads:MTLSizeMake(region.width, region.height, region.slices)
        threadsPerThreadgroup:MTLSizeMake(width, std::max<NSUInteger>(height, 1), 1)];
}

} // namespace Metal::FormatConvert
