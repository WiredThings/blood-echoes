// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <cstddef>
#include <type_traits>
#include "video_core/texture_cache/tile_manager.h"
#include "video_core/texture_cache/image_info.h"
namespace VideoCore::TilerBounds {
struct LogicalMip { u32 width, height, slices; };
// Private, versioned UBO extension. Public TilingInfo and TilingDispatch stay unchanged.
struct ParamsV1 { TilingInfo physical; std::array<LogicalMip, 16> logical{}; };
inline constexpr u64 ParamsV1Bytes = 652;
static_assert(sizeof(TilingMip) == 28 && sizeof(TilingInfo) == 460);
static_assert(std::is_standard_layout_v<ParamsV1>);
static_assert(offsetof(ParamsV1, logical) == 460 && sizeof(ParamsV1) == ParamsV1Bytes);
inline bool Enabled(const TilingDispatch& dispatch) {
    return dispatch.key.is_tiler && dispatch.params.range == ParamsV1Bytes;
}
// Preserve omitted shader defaults when adding the independently supplied ID13.
// Caller validates provided.size()<=12 before forming its span.
template<class Specs>
inline std::array<u32, 14> Constants(std::span<const u32> provided,
                                     const Specs& defaults, bool bounded) {
    std::array<u32, 14> result{};
    for (const auto& spec : defaults)
        if (spec.id < result.size()) result[spec.id] = spec.default_value;
    std::copy(provided.begin(), provided.end(), result.begin());
    result[13] = bounded;
    return result;
}
// Only complete, canonical color exports are admitted. Other copy shapes retain the
// existing path; this is not an interpretation of partial-copy or stencil metadata.
inline bool Describe(const ImageInfo& info, std::span<const vk::BufferImageCopy> copies,
                     ParamsV1& out) {
    if (info.num_samples != 1 || info.props.is_block || info.props.is_volume ||
        info.props.is_depth || info.props.has_stencil || !info.resources.levels ||
        !info.resources.layers || copies.size() != info.resources.levels ||
        copies.size() > out.logical.size()) return false;
    for (u32 mip = 0; mip < copies.size(); ++mip) {
        const auto& copy = copies[mip];
        const auto& physical = out.physical.mips[mip];
        const auto width = std::max(info.size.width >> mip, 1u);
        const auto height = std::max(info.size.height >> mip, 1u);
        if (copy.bufferOffset != info.mips_layout[mip].offset ||
            copy.bufferRowLength != info.mips_layout[mip].pitch ||
            copy.bufferImageHeight != info.mips_layout[mip].height ||
            copy.imageOffset.x || copy.imageOffset.y || copy.imageOffset.z ||
            copy.imageSubresource.aspectMask != vk::ImageAspectFlagBits::eColor ||
            copy.imageSubresource.mipLevel != mip || copy.imageSubresource.baseArrayLayer ||
            copy.imageSubresource.layerCount != info.resources.layers ||
            copy.imageExtent.width != width || copy.imageExtent.height != height ||
            copy.imageExtent.depth != 1 || width > physical.pitch ||
            height > physical.height || info.resources.layers > physical.slices)
            return false;
        out.logical[mip] = {width, height, info.resources.layers};
    }
    return true;
}
} // namespace VideoCore::TilerBounds
