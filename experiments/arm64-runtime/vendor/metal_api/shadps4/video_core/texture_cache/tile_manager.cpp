// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <bit>
#include <cstdlib>
#include <cstring>
#include "bb_tiler_bounds.h"

#include "video_core/cache_backend.h"
#include "video_core/texture_cache/image.h"
#include "video_core/texture_cache/image_info.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/tile_manager.h"

namespace VideoCore {

TileManager::TileManager(CacheBackend& backend_, StreamBuffer& stream_buffer_)
    : backend{backend_}, stream_buffer{stream_buffer_} {}

TileManager::~TileManager() = default;

// The macro tile (pitch, height) in texels of the tile mode of `info`: what the macro tile
// addressing of the kernel (MACRO_TILE_PITCH, MACRO_TILE_HEIGHT in tiling.comp) is made of.
static std::pair<u32, u32> MacroTileExtent(const ImageInfo& info) {
    const auto macro_tile_mode =
        AmdGpu::CalculateMacrotileMode(info.tile_mode, info.num_bits, info.num_samples);
    const u32 num_pipes = AmdGpu::GetPipeConfig(info.tile_mode) == AmdGpu::PipeConfig::P2 ? 2 : 8;
    const u32 aspect = AmdGpu::GetMacrotileAspect(macro_tile_mode);
    return {8 * AmdGpu::GetBankWidth(macro_tile_mode) * num_pipes * aspect,
            8 * AmdGpu::GetBankHeight(macro_tile_mode) * AmdGpu::GetNumBanks(macro_tile_mode) /
                aspect};
}

TilingInfo TileManager::MakeTilingInfo(const ImageInfo& info, u32 num_mips) {
    ASSERT(num_mips <= info.resources.levels && num_mips <= info.mips_layout.size());
    const bool is_macro = AmdGpu::IsMacroTiled(info.array_mode);
    const auto [macro_pitch, macro_height] =
        is_macro ? MacroTileExtent(info) : std::pair<u32, u32>{8, 8};
    TilingInfo params{};
    params.bank_swizzle = info.bank_swizzle;
    params.micro_tiled_mips = info.micro_tiled_mips;
    params.num_mips = num_mips;
    for (u32 mip = 0; mip < num_mips; ++mip) {
        const auto& layout = info.mips_layout[mip];
        auto& mip_info = params.mips[mip];
        mip_info.size = layout.size;
        mip_info.pitch = layout.pitch;
        mip_info.height = layout.height;
        mip_info.offset = layout.offset;
        if (info.props.is_block) {
            mip_info.pitch = std::max((mip_info.pitch + 3) / 4, 1U);
            mip_info.height = std::max((mip_info.height + 3) / 4, 1U);
        }
        // The surface is laid out padded to its macro tiles (the small mips of a macro tiled image
        // are micro tiled: to 8x8 texels), whatever logical size the image has.
        const bool micro = !is_macro || (info.micro_tiled_mips >> mip & 1) != 0;
        const u32 align_pitch = micro ? 8 : macro_pitch;
        const u32 align_height = micro ? 8 : macro_height;
        mip_info.padded_pitch = (mip_info.pitch + align_pitch - 1) / align_pitch * align_pitch;
        mip_info.padded_height = (mip_info.height + align_height - 1) / align_height * align_height;
        const u64 slice_bytes = u64(mip_info.padded_pitch) * mip_info.padded_height *
                                info.num_bits * info.num_samples / 8;
        // A micro tile holds `thickness` slices: only whole groups of them fit the size.
        const u32 array_thickness = AmdGpu::GetMicroTileThickness(info.array_mode);
        const u32 thickness = is_macro && micro ? std::min(array_thickness, 4u) : array_thickness;
        const u32 slices = static_cast<u32>(slice_bytes ? layout.size / slice_bytes : 0);
        mip_info.slices = slices - slices % thickness;
    }
    return params;
}

TilingDispatch TileManager::MakeDispatch(const ImageInfo& info, bool is_tiler,
                                         const KernelBuffer& tiled, const KernelBuffer& linear,
                                         const KernelBuffer& params) {
    const auto micro_tile_mode = AmdGpu::GetMicroTileMode(info.tile_mode);
    const bool is_macro = AmdGpu::IsMacroTiled(info.array_mode);
    TilingDispatch dispatch{
        .key =
            {
                .tile_mode = info.tile_mode,
                .num_bits = info.num_bits,
                .num_samples = info.num_samples,
                .is_tiler = is_tiler,
            },
        .array_mode = info.array_mode,
        .is_macro = is_macro,
        .constants =
            {
                info.num_samples,
                u32(micro_tile_mode),
                AmdGpu::GetMicroTileThickness(info.array_mode),
                u32(is_tiler),
            },
        .num_constants = is_macro ? 12u : 4u,
        .tiled = tiled,
        .linear = linear,
        .params = params,
    };
    // 64 threads a group, eight threads (a row of 8 texels each) per unit of 64 texels, units
    // of 64 texels per mip (tiling.comp): the groups handle 8 units, the rest of the last group
    // ends at once. The mips are the ones the kernel is given (TilingInfo, levels).
    const u32 bytes_per_texel = info.num_bits / 8;
    u32 units = 0;
    for (u32 mip = 0; mip < std::min<u32>(info.resources.levels, info.mips_layout.size()); ++mip) {
        units += (info.mips_layout[mip].size / bytes_per_texel + 63) / 64;
    }
    dispatch.group_count_x = (units + 7) / 8;
    if (is_macro) {
        auto& spec_data = dispatch.constants;
        const auto macro_tile_mode =
            AmdGpu::CalculateMacrotileMode(info.tile_mode, info.num_bits, info.num_samples);
        spec_data[4] = u32(info.array_mode);
        spec_data[5] = u32(AmdGpu::GetPipeConfig(info.tile_mode));
        spec_data[6] = AmdGpu::GetBankWidth(macro_tile_mode);
        spec_data[7] = AmdGpu::GetBankHeight(macro_tile_mode);
        spec_data[8] = AmdGpu::GetNumBanks(macro_tile_mode);
        spec_data[9] = std::bit_width(spec_data[8]) - 1;
        spec_data[10] = AmdGpu::CalculateTileSplit(info.tile_mode, info.array_mode, micro_tile_mode,
                                                   info.num_bits);
        spec_data[11] = AmdGpu::GetMacrotileAspect(macro_tile_mode);
    }
    return dispatch;
}

std::pair<const Buffer*, u64> TileManager::DetileImage(const VideoCore::Buffer* in_buffer,
                                                       u64 in_offset, const ImageInfo& info) {
    if (!info.props.is_tiled) {
        return {in_buffer, in_offset};
    }

    const TilingInfo params = MakeTilingInfo(info, info.resources.levels);

    const KernelBuffer params_buffer_info{
        .buffer = &stream_buffer,
        .offset =
            stream_buffer.Copy(&params, sizeof(params), backend.Limits().uniform_min_alignment),
        .range = sizeof(params),
    };

    const auto staging = backend.StagingRequest(info.guest_size, MemoryType::DeviceLocal);

    backend.EndRendering();
    backend.FlushBarriers();

    const KernelBuffer tiled_buffer_info{
        .buffer = in_buffer,
        .offset = in_offset,
        .range = info.guest_size,
    };
    const KernelBuffer linear_buffer_info{
        .buffer = staging.buffer,
        .offset = staging.offset,
        .range = info.guest_size,
    };
    // bbport: the backend records with copies of the buffer ranges (threaded recording).
    backend.DispatchTiling(
        MakeDispatch(info, false, tiled_buffer_info, linear_buffer_info, params_buffer_info));
    // bbport: the source may be the arena (BB_PREUPLOAD), which later uploads write: a copy into
    // it must wait for this read.
    backend.AccessBuffer(in_buffer, in_offset, info.guest_size,
                         vk::PipelineStageFlagBits2::eComputeShader,
                         vk::AccessFlagBits2::eShaderRead);

    backend.AccessBuffer(staging.buffer, staging.offset, info.guest_size,
                         vk::PipelineStageFlagBits2::eComputeShader,
                         vk::AccessFlagBits2::eShaderWrite);

    return {staging.buffer, staging.offset};
}

void TileManager::TileImage(Image& in_image, std::span<vk::BufferImageCopy> buffer_copies,
                            const VideoCore::Buffer* out_buffer, u64 out_offset) {
    const auto& info = in_image.info;
    if (!info.props.is_tiled) {
        for (auto& copy : buffer_copies) {
            copy.bufferOffset += out_offset;
        }
        backend.DownloadImage(&in_image, out_buffer, buffer_copies);
        return;
    }

    const TilingInfo params = MakeTilingInfo(info, static_cast<u32>(buffer_copies.size()));
    TilerBounds::ParamsV1 bounded{params};
    static const bool requested = [] {
        const auto* value = std::getenv("BB_ARM_TILER_BOUNDS");
        return value && std::strcmp(value, "1") == 0;
    }();
    const bool use_bounds = requested && TilerBounds::Describe(info, buffer_copies, bounded);
    const void* parameter_data = use_bounds ? static_cast<const void*>(&bounded) : &params;
    const u64 parameter_bytes = use_bounds ? sizeof(bounded) : sizeof(params);
    const KernelBuffer params_buffer_info{
        .buffer = &stream_buffer,
        .offset = stream_buffer.Copy(parameter_data, parameter_bytes,
                                    backend.Limits().uniform_min_alignment),
        .range = parameter_bytes,
    };

    // Give a capable backend the same tiling description before allocating a linear snapshot.
    // Other backends and unsupported formats keep the original path without extra resources.
    const auto direct = MakeDispatch(info, true,
        {out_buffer, out_offset, info.guest_size}, {}, params_buffer_info);
    if (backend.DownloadTiledImage(&in_image, direct)) {
        backend.AccessBuffer(out_buffer, out_offset, info.guest_size,
                             vk::PipelineStageFlagBits2::eComputeShader,
                             vk::AccessFlagBits2::eShaderWrite);
        return;
    }
    const auto staging = backend.StagingRequest(info.guest_size, MemoryType::DeviceLocal);
    for (auto& copy : buffer_copies) {
        copy.bufferOffset += staging.offset;
    }

    backend.DownloadImage(&in_image, staging.buffer, buffer_copies);
    backend.FlushBarriers();

    const KernelBuffer tiled_buffer_info{
        .buffer = out_buffer,
        .offset = out_offset,
        .range = info.guest_size,
    };
    const KernelBuffer linear_buffer_info{
        .buffer = staging.buffer,
        .offset = staging.offset,
        .range = info.guest_size,
    };
    // bbport: the backend records with copies of the buffer ranges (threaded recording).
    backend.DispatchTiling(
        MakeDispatch(info, true, tiled_buffer_info, linear_buffer_info, params_buffer_info));

    backend.AccessBuffer(out_buffer, out_offset, info.guest_size,
                         vk::PipelineStageFlagBits2::eComputeShader,
                         vk::AccessFlagBits2::eShaderWrite);
}

} // namespace VideoCore

namespace VideoCore {
std::unique_ptr<PreparedExactImageExport> TileManager::PrepareExactImageExport(
    Image& image, const Buffer* raw, u64 raw_offset,
    const std::function<void()>& release_input_lease) {
    // This is the FIRST operation: no hook, stream write or pool allocation before
    // the backend atomically consumes one attempt, even if validation later fails.
    auto attempt=backend.ClaimExactImageExportAttempt();
    if(!attempt || !raw || !backend.CanPrepareExactImageExport(image)) return {};
    const auto& info=image.info;
    if(!info.props.is_tiled || !info.guest_size || info.guest_size>(8ull<<20) ||
       info.resources.levels==0 || info.resources.levels>16 ||
       raw_offset>raw->SizeBytes() || info.guest_size>raw->SizeBytes()-raw_offset)return {};
    std::array<vk::BufferImageCopy,16> copies{};
    for(u32 mip=0;mip<info.resources.levels;++mip){
        const auto& m=info.mips_layout[mip];
        copies[mip]={.bufferOffset=m.offset,.bufferRowLength=m.pitch,.bufferImageHeight=m.height,
            .imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.mipLevel=mip,
                .baseArrayLayer=0,.layerCount=info.resources.layers},
            .imageOffset={0,0,0},.imageExtent={std::max(info.size.width>>mip,1u),
                std::max(info.size.height>>mip,1u),1}};
    }
    auto span=std::span(copies).first(info.resources.levels);
    TilerBounds::ParamsV1 params{MakeTilingInfo(info,info.resources.levels)};
    if(!TilerBounds::Describe(info,span,params))return {};
    // No stream/pool allocation here: Runtime owns both private staging spans.
    return backend.PrepareExactImageExport(std::move(attempt),&image,raw,raw_offset,span,
        std::span<const u8>{reinterpret_cast<const u8*>(&params),sizeof(params)},release_input_lease);
}
} // namespace VideoCore
