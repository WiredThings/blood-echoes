// SPDX-License-Identifier: GPL-2.0-or-later
// Guest formats Metal has no texture for: the table, and the kernels that convert the bytes of a
// transfer (design in mtl_format_convert.h).

#include <algorithm>
#include <cstring>
#include <string>

#include "common/assert.h"
#include "common/logging/log.h"
#include "video_core/renderer_metal/mtl_format_convert_impl.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"

#include <vulkan/vulkan_format_traits.hpp>

namespace Metal::FormatConvert {

namespace {


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
    NSString* name = kind == Kind::RgbPad ? @"rgb_convert" : @"depth_convert";
    slot = kernels.FormatConversionPipeline(kind != Kind::RgbPad, &error);
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
