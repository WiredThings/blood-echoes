// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the GCN tiling and detiling kernel (host_shaders/tiling.comp, its MSL by SPIRV-Cross;
// docs/METAL_BACKEND_PLAN.md P8.4): runs the shipped kernel and the reference kernel
// (tests/tiling_reference.comp, one thread per element, the shipped kernel's addressing without
// its work distribution) as tiler and as detiler for the game's tile modes (macro tiled 2D thin
// 14, depth 0, micro tiled 13 and 9), mip chains, thick and 3D modes, every bits per pixel, block
// formats, bank swizzles, and layouts whose logical height is smaller than the padded one (a 720
// row depth buffer in 768 rows). The dispatch (constants, group count) and the kernel's parameters
// are the ones of TileManager (MakeDispatch, MakeTilingInfo).
//
// The correctness reference is the CPU address function of tests/tiling_cpu_reference.h (the
// address library's semantics, written independently of the kernels). Per case it requires
//  - the map from the linear texels (the logical rows) to tiled bytes to be injective and to stay
//    inside the mip of the tiled buffer (the guest memory of the image);
//  - the padded sizes and the slice count that TileManager gives the kernel to be the ones of the
//    CPU reference;
//  - both kernels to give, in both directions, exactly the buffers the CPU reference gives (the
//    tiled and the linear buffer whole, with a guard area behind the image, so a stray write
//    fails), and to give the same twice (a race on one address fails);
//  - everything above to be done: a comparison that is not made is a failure (the summary counts
//    the skipped ones, there are none).
// Prints the GPU time of both kernels (MTLCommandBuffer GPUStartTime/GPUEndTime, median). Needs a
// Metal device only.
//
//   mtl-tiling-test [name part of the cases to run]
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include "tiling_cpu_reference.h"
#include "video_core/renderer_metal/msl_host_shader.h"
#include "video_core/texture_cache/image_info.h"
#include "video_core/texture_cache/tile.h"
#include "video_core/texture_cache/tile_manager.h"

#include "video_core/host_shaders_msl/tiling_macro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_macro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_ref_micro_96_comp_msl.h"

namespace H = Metal::HostShaders;
using AmdGpu::TileMode;

namespace {

const H::Shader& GetShader(bool reference, u32 bits, bool macro) {
#define PICK(BITS)                                                                                 \
    case BITS:                                                                                     \
        return reference ? (macro ? H::TILING_REF_MACRO_##BITS##_COMP                              \
                                  : H::TILING_REF_MICRO_##BITS##_COMP)                             \
                         : (macro ? H::TILING_MACRO_##BITS##_COMP : H::TILING_MICRO_##BITS##_COMP)
    switch (bits) {
        PICK(8);
        PICK(16);
        PICK(32);
        PICK(64);
        PICK(96);
        PICK(128);
    }
#undef PICK
    std::abort();
}

u32 BufferSlot(const H::Shader& shader, u32 binding) {
    for (const H::Binding& b : shader.bindings) {
        if (b.kind == H::Binding::Kind::Buffer && b.set == 0 && b.binding == binding) {
            return b.msl_index;
        }
    }
    std::abort();
}

/// A level of a hand made layout: the logical pitch and height (the linear copy's rows) and the
/// size in half slices of the padded surface (2: one slice, 3: a slice and a half that the kernel
/// does not tile past the first, 0: an empty level); `micro`: a micro tiled level of a macro mode.
struct CustomMip {
    u32 pitch, height, half_slices;
    bool micro;
};

struct Case {
    const char* name;
    TileMode tile_mode;
    u32 bits;
    u32 width, height;
    u32 depth;           ///< depth slices of a volume
    u32 layers;
    u32 levels;
    bool render_target;  ///< one level the way ImageInfo(ColorBuffer) lays it out (padded pitch and height)
    u32 samples;
    bool block;          ///< block compressed (the kernel works on blocks)
    bool pow2;
    u32 bank_swizzle;
    bool timed;
    /// Hand made mips (layouts ImageInfo does not produce: logical sizes in padded surfaces, sizes
    /// that are no whole slices, sizes that are no multiple of 8); levels is then their count.
    std::array<CustomMip, 4> custom{};
};

constexpr Case kCases[] = {
    // name                  mode                   bpp  w     h     d  l  lv rt     smp blk  pow2 swz timed
    {"rt 64bpp m14 1280x720", TileMode::Thin2DThin, 64, 1280, 720, 1, 1, 1, true, 1, false, false, 0, true},
    {"64bpp m14 1280x720 pad", TileMode::Thin2DThin, 64, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false},
    {"rt 32bpp m14 1280x720", TileMode::Thin2DThin, 32, 1280, 720, 1, 1, 1, true, 1, false, false, 0, true},
    {"32bpp m14 1280x720 pad", TileMode::Thin2DThin, 32, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false},
    {"rt 32bpp m0 1280x720", TileMode::Depth2DThin64, 32, 1280, 720, 1, 1, 1, true, 1, false, false, 0, true},
    {"32bpp m0 1280x720 pad", TileMode::Depth2DThin64, 32, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false},
    {"rt 32bpp m14 640x360", TileMode::Thin2DThin, 32, 640, 360, 1, 1, 1, true, 1, false, false, 0, false},
    {"rt 32bpp m14 1920x1080", TileMode::Thin2DThin, 32, 1920, 1080, 1, 1, 1, true, 1, false, false, 0, false},
    {"rt 64bpp m14 1920x1080", TileMode::Thin2DThin, 64, 1920, 1080, 1, 1, 1, true, 1, false, false, 0, false},
    {"32bpp m14 256x256 mips", TileMode::Thin2DThin, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m14 256x256 mips swz", TileMode::Thin2DThin, 32, 256, 256, 1, 1, 9, false, 1, false, false, 5, false},
    {"64bpp m14 512x512 mips", TileMode::Thin2DThin, 64, 512, 512, 1, 1, 10, false, 1, false, false, 0, false},
    {"32bpp m14 100x60 mips", TileMode::Thin2DThin, 32, 100, 60, 1, 1, 7, false, 1, false, false, 0, false},
    {"32bpp m14 300x200 pow2 mips", TileMode::Thin2DThin, 32, 300, 200, 1, 1, 9, false, 1, false, true, 0, false},
    {"32bpp m14 128x128 x6 layers", TileMode::Thin2DThin, 32, 128, 128, 1, 6, 8, false, 1, false, false, 0, false},
    {"32bpp m0 256x256 mips", TileMode::Depth2DThin64, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m1 256x256 mips", TileMode::Depth2DThin128, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m2 256x256 mips", TileMode::Depth2DThin256, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m4 256x256 mips", TileMode::Depth2DThin1K, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m5 256x256 mips", TileMode::Depth1DThin, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m6 256x256 mips", TileMode::Depth2DThinPrt256, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m10 256x256 mips", TileMode::Display2DThin, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m11 256x256 mips", TileMode::DisplayThinPrt, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m12 256x256 mips", TileMode::Display2DThinPrt, 32, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"32bpp m13 512x512 mips", TileMode::Thin1DThin, 32, 512, 512, 1, 1, 10, false, 1, false, false, 0, true},
    {"rt 32bpp m13 1280x720", TileMode::Thin1DThin, 32, 1280, 720, 1, 1, 1, true, 1, false, false, 0, false},
    {"32bpp m9 512x512 mips", TileMode::Display1DThin, 32, 512, 512, 1, 1, 10, false, 1, false, false, 0, false},
    {"rt 32bpp m9 1280x720", TileMode::Display1DThin, 32, 1280, 720, 1, 1, 1, true, 1, false, false, 0, true},
    {"rt 64bpp m9 1280x720", TileMode::Display1DThin, 64, 1280, 720, 1, 1, 1, true, 1, false, false, 0, false},
    {"32bpp m15 64x64x8 vol", TileMode::Thin3DThin, 32, 64, 64, 8, 1, 4, false, 1, false, false, 0, false},
    {"32bpp m16 128x128 mips", TileMode::ThinThinPrt, 32, 128, 128, 1, 1, 8, false, 1, false, false, 0, false},
    {"32bpp m17 128x128 mips", TileMode::Thin2DThinPrt, 32, 128, 128, 1, 1, 8, false, 1, false, false, 0, false},
    {"32bpp m18 64x64x8 vol", TileMode::Thin3DThinPrt, 32, 64, 64, 8, 1, 4, false, 1, false, false, 0, false},
    {"32bpp m19 64x64x8 vol", TileMode::Thick1DThick, 32, 64, 64, 8, 1, 4, false, 1, false, false, 0, false},
    {"32bpp m20 128x128x8 vol", TileMode::Thick2DThick, 32, 128, 128, 8, 1, 6, false, 1, false, false, 0, false},
    {"64bpp m20 64x64x4 vol", TileMode::Thick2DThick, 64, 64, 64, 4, 1, 5, false, 1, false, false, 0, false},
    {"32bpp m21 64x64x8 vol", TileMode::Thick3DThick, 32, 64, 64, 8, 1, 4, false, 1, false, false, 0, false},
    {"32bpp m22 128x128x8 vol", TileMode::ThickThickPrt, 32, 128, 128, 8, 1, 6, false, 1, false, false, 0, false},
    {"32bpp m23 128x128x8 vol", TileMode::Thick2DThickPrt, 32, 128, 128, 8, 1, 6, false, 1, false, false, 0, false},
    {"32bpp m25 128x128x8 vol", TileMode::Thick2DXThick, 32, 128, 128, 8, 1, 6, false, 1, false, false, 0, false},
    {"32bpp m26 64x64x8 vol", TileMode::Thick3DXThick, 32, 64, 64, 8, 1, 4, false, 1, false, false, 0, false},
    {"8bpp m14 256x256 mips", TileMode::Thin2DThin, 8, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"8bpp m13 256x256 mips", TileMode::Thin1DThin, 8, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"16bpp m14 256x256 mips", TileMode::Thin2DThin, 16, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"16bpp m13 256x256 mips", TileMode::Thin1DThin, 16, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"16bpp m0 256x256 mips", TileMode::Depth2DThin64, 16, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"96bpp m13 256x256 mips", TileMode::Thin1DThin, 96, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"128bpp m14 256x256 mips", TileMode::Thin2DThin, 128, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"128bpp m13 256x256 mips", TileMode::Thin1DThin, 128, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"128bpp m10 256x256 mips", TileMode::Display2DThin, 128, 256, 256, 1, 1, 9, false, 1, false, false, 0, false},
    {"64bpp m14 BC 512x512 mips", TileMode::Thin2DThin, 64, 512, 512, 1, 1, 10, false, 1, true, false, 0, false},
    {"128bpp m14 BC 300x200 mips", TileMode::Thin2DThin, 128, 300, 200, 1, 1, 9, false, 1, true, false, 0, false},
    {"64bpp m13 BC 256x256 mips", TileMode::Thin1DThin, 64, 256, 256, 1, 1, 9, false, 1, true, false, 0, false},
    {"32bpp m0 2 samples 256x256", TileMode::Depth2DThin64, 32, 256, 256, 1, 1, 1, false, 2, false, false, 0, false},
};

constexpr Case kCustomCases[] = {
    // The buffers of the game at 720p: a render target is a surface of 768 rows with the height of
    // 768 (the height of ImageInfo(ColorBuffer) is the slice size over the pitch), a depth buffer
    // a surface of 768 rows with the height of 720 (ImageInfo(DepthBuffer): the register's height).
    {"padded 64bpp m14 1280x768", TileMode::Thin2DThin, 64, 1280, 768, 1, 1, 1, false, 1, false, false, 0, true,
     {{{1280, 768, 2}}}},
    {"padded 32bpp m14 1280x768", TileMode::Thin2DThin, 32, 1280, 768, 1, 1, 1, false, 1, false, false, 0, false,
     {{{1280, 768, 2}}}},
    {"padded 32bpp m0 1280x768", TileMode::Depth2DThin64, 32, 1280, 768, 1, 1, 1, false, 1, false, false, 0, false,
     {{{1280, 768, 2}}}},
    {"depth 64bpp m14 1280x720/768", TileMode::Thin2DThin, 64, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false,
     {{{1280, 720, 2}}}},
    {"depth 32bpp m14 1280x720/768", TileMode::Thin2DThin, 32, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false,
     {{{1280, 720, 2}}}},
    {"depth 32bpp m0 1280x720/768", TileMode::Depth2DThin64, 32, 1280, 720, 1, 1, 1, false, 1, false, false, 0, false,
     {{{1280, 720, 2}}}},
    {"depth 32bpp m0 1280x720/768 x2", TileMode::Depth2DThin64, 32, 1280, 720, 1, 2, 1, false, 1, false, false, 0, false,
     {{{1280, 720, 4}}}},

    // Logical height or pitch below the padded one, not a multiple of 8, partial last slice, empty
    // level, micro tiled levels of a macro mode, undersized levels; macro and micro modes.
    {"custom 64bpp m14 64x12", TileMode::Thin2DThin, 64, 64, 12, 1, 1, 1, false, 1, false, false, 0, false,
     {{{64, 12, 2}}}},
    {"custom 32bpp m14 64x32 1.5 slices", TileMode::Thin2DThin, 32, 64, 32, 1, 1, 1, false, 1, false, false, 0, false,
     {{{64, 32, 3}}}},
    {"custom 32bpp m13 64x32 1.5 slices", TileMode::Thin1DThin, 32, 64, 32, 1, 1, 1, false, 1, false, false, 0, false,
     {{{64, 32, 3}}}},
    {"custom 32bpp m14 12x16", TileMode::Thin2DThin, 32, 12, 16, 1, 1, 1, false, 1, false, false, 0, false,
     {{{12, 16, 2}}}},
    {"custom 32bpp m13 12x12", TileMode::Thin1DThin, 32, 12, 12, 1, 1, 1, false, 1, false, false, 0, false,
     {{{12, 12, 2}}}},
    {"custom 64bpp m13 100x64", TileMode::Thin1DThin, 64, 100, 64, 1, 1, 1, false, 1, false, false, 0, false,
     {{{100, 64, 2}}}},
    {"custom 32bpp m0 100x60", TileMode::Depth2DThin64, 32, 100, 60, 1, 1, 1, false, 1, false, false, 0, false,
     {{{100, 60, 2}}}},
    {"custom 32bpp m13 irregular chain", TileMode::Thin1DThin, 32, 64, 64, 1, 1, 4, false, 1, false, false, 0, false,
     {{{64, 64, 2}, {32, 24, 2}, {16, 16, 0}, {16, 16, 2}}}},
    {"custom 32bpp m14 irregular chain", TileMode::Thin2DThin, 32, 64, 64, 1, 1, 4, false, 1, false, false, 3, false,
     {{{64, 64, 2}, {40, 40, 2}, {16, 16, 0}, {16, 16, 2}}}},
    {"custom 32bpp m14 chain micro mips", TileMode::Thin2DThin, 32, 64, 64, 1, 1, 3, false, 1, false, false, 3, false,
     {{{64, 64, 2}, {40, 40, 2, true}, {12, 16, 2, true}}}},
    {"custom 128bpp m14 chain", TileMode::Thin2DThin, 128, 64, 64, 1, 1, 3, false, 1, false, false, 0, false,
     {{{64, 64, 2}, {32, 32, 2}, {8, 16, 2, true}}}},
    {"undersized 32bpp m14 64x64", TileMode::Thin2DThin, 32, 64, 64, 1, 1, 1, false, 1, false, false, 0, false,
     {{{64, 64, 1}}}},
    {"undersized 32bpp m13 64x64", TileMode::Thin1DThin, 32, 64, 64, 1, 1, 1, false, 1, false, false, 0, false,
     {{{64, 64, 1}}}},
};

/// The tile mode's properties as the address library takes them, from the AmdGpu tables.
TilingCpu::Config MakeConfig(const VideoCore::ImageInfo& info) {
    using AmdGpu::ArrayMode;
    TilingCpu::Config cfg;
    cfg.bits = info.num_bits;
    cfg.samples = info.num_samples;
    cfg.micro_mode = static_cast<u32>(AmdGpu::GetMicroTileMode(info.tile_mode));
    cfg.thickness = AmdGpu::GetMicroTileThickness(info.array_mode);
    cfg.macro = AmdGpu::IsMacroTiled(info.array_mode);
    cfg.bank_swizzle = info.bank_swizzle;
    if (!cfg.macro) {
        return cfg;
    }
    const ArrayMode am = info.array_mode;
    cfg.prt = AmdGpu::IsPrt(am);
    cfg.mode_2d = am == ArrayMode::Array2DTiledThin1 || am == ArrayMode::Array2DTiledThick ||
                  am == ArrayMode::Array2DTiledXThick;
    cfg.mode_3d = am == ArrayMode::Array3DTiledThin1 || am == ArrayMode::Array3DTiledThick ||
                  am == ArrayMode::Array3DTiledXThick;
    cfg.split_rotates = am == ArrayMode::Array2DTiledThin1 || am == ArrayMode::Array3DTiledThin1 ||
                        am == ArrayMode::ArrayPrt2DTiledThin1 ||
                        am == ArrayMode::ArrayPrt3DTiledThin1;
    const AmdGpu::PipeConfig pipe_config = AmdGpu::GetPipeConfig(info.tile_mode);
    cfg.pipe_config = static_cast<u32>(pipe_config);
    cfg.num_pipes = AmdGpu::GetPipeCount(pipe_config);
    const AmdGpu::MacroTileMode mode =
        AmdGpu::CalculateMacrotileMode(info.tile_mode, info.num_bits, info.num_samples);
    cfg.bank_width = AmdGpu::GetBankWidth(mode);
    cfg.bank_height = AmdGpu::GetBankHeight(mode);
    cfg.num_banks = AmdGpu::GetNumBanks(mode);
    cfg.macro_aspect = AmdGpu::GetMacrotileAspect(mode);
    cfg.tile_split_bytes = AmdGpu::CalculateTileSplit(
        info.tile_mode, info.array_mode, AmdGpu::GetMicroTileMode(info.tile_mode), info.num_bits);
    return cfg;
}

/// The image of a case: UpdateSize for textures; the other layouts are the ones ImageInfo makes
/// of a render target (padded pitch and height, the size of the padded slice) and, with a hand made
/// level list, of a depth buffer (the register's height in the padded slice) and of layouts
/// ImageInfo does not make.
VideoCore::ImageInfo MakeInfo(const Case& c) {
    VideoCore::ImageInfo info{};
    info.props.is_tiled = true;
    info.props.is_block = c.block;
    info.props.is_pow2 = c.pow2;
    info.tile_mode = c.tile_mode;
    info.array_mode = AmdGpu::GetArrayMode(c.tile_mode);
    info.num_bits = c.bits;
    info.num_samples = c.samples;
    info.pitch = c.width;
    info.size = {c.width, c.height, c.depth};
    info.resources = {c.levels, c.layers};
    info.bank_swizzle = static_cast<u8>(c.bank_swizzle);
    const TilingCpu::Config cfg = MakeConfig(info);
    if (c.custom[0].pitch != 0) {
        u32 offset = 0;
        for (u32 mip = 0; mip < c.levels; ++mip) {
            const CustomMip& m = c.custom[mip];
            const bool micro = m.micro && cfg.macro;
            const u64 slice_bytes = u64(TilingCpu::PaddedPitch(cfg, micro, m.pitch)) *
                                    TilingCpu::PaddedHeight(cfg, micro, m.height) * c.bits *
                                    c.samples / 8;
            const u32 size = static_cast<u32>(slice_bytes * m.half_slices / 2);
            info.mips_layout[mip] = {size, m.pitch, m.height, offset};
            if (micro) {
                info.micro_tiled_mips |= 1u << mip;
            }
            offset += size;
        }
        info.guest_size = offset;
    } else if (c.render_target) {
        // ImageInfo(ColorBuffer): the pitch of the register, the slice size of its tile count, the
        // height that makes (a multiple of the macro tile height as the SDK allocates it: the
        // game's 1280x720 targets are 3932160 bytes = 1280x768x4)
        const u32 pitch = TilingCpu::PaddedPitch(cfg, false, c.width);
        const u32 height = TilingCpu::PaddedHeight(cfg, false, c.height);
        info.pitch = pitch;
        info.guest_size = pitch * height * (c.bits / 8) * c.layers;
        info.mips_layout[0] = {info.guest_size, pitch, height, 0};
    } else {
        info.UpdateSize();
    }
    return info;
}

struct Rng {
    u64 state;
    u32 Next() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        return static_cast<u32>(state >> 16);
    }
};

struct Kernel {
    id<MTLComputePipelineState> pipeline;
    const H::Shader* shader;
};

/// The function of a host shader with the constants of the dispatch (renderer_metal's
/// HostKernels::Function) and its compute pipeline.
Kernel MakeKernel(id<MTLDevice> device, const H::Shader& shader, const std::array<u32, 12>& constants,
                  u32 count) {
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    if (@available(macOS 15.0, *)) {
        options.mathMode = MTLMathModeSafe;
    } else {
        options.fastMathEnabled = NO;
    }
    NSError* error = nil;
    id<MTLLibrary> library = [device
        newLibraryWithSource:[[NSString alloc] initWithBytes:shader.msl.data()
                                                      length:shader.msl.size()
                                                    encoding:NSUTF8StringEncoding]
                     options:options
                       error:&error];
    if (!library) {
        std::fprintf(stderr, "library: %s\n", error.localizedDescription.UTF8String);
        std::exit(1);
    }
    MTLFunctionConstantValues* values = [MTLFunctionConstantValues new];
    for (const H::SpecConstant& constant : shader.spec_constants) {
        if (constant.id >= count) {
            continue;
        }
        u32 value = constants[constant.id];
        [values setConstantValue:&value type:MTLDataTypeUInt atIndex:constant.id];
    }
    id<MTLFunction> function =
        [library newFunctionWithName:@"main0" constantValues:values error:&error];
    if (!function) {
        std::fprintf(stderr, "function: %s\n", error.localizedDescription.UTF8String);
        std::exit(1);
    }
    id<MTLComputePipelineState> pipeline =
        [device newComputePipelineStateWithFunction:function error:&error];
    if (!pipeline) {
        std::fprintf(stderr, "pipeline: %s\n", error.localizedDescription.UTF8String);
        std::exit(1);
    }
    return {pipeline, &shader};
}

/// A plain copy of as many bytes as the image has, one element per thread: the least any kernel
/// that reads one buffer and writes the other has to spend on this GPU.
Kernel MakeCopyKernel(id<MTLDevice> device, u32 bits) {
    static const char* source = R"(
#include <metal_stdlib>
using namespace metal;
kernel void copy_u32(device const uint* a [[buffer(0)]], device uint* b [[buffer(1)]],
                     uint i [[thread_position_in_grid]]) { b[i] = a[i]; }
kernel void copy_u64(device const uint2* a [[buffer(0)]], device uint2* b [[buffer(1)]],
                     uint i [[thread_position_in_grid]]) { b[i] = a[i]; }
)";
    NSError* error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:[NSString stringWithUTF8String:source]
                                                  options:nil
                                                    error:&error];
    id<MTLFunction> function =
        [library newFunctionWithName:bits == 64 ? @"copy_u64" : @"copy_u32"];
    return {[device newComputePipelineStateWithFunction:function error:&error], nullptr};
}

struct Buffers {
    id<MTLBuffer> tiled;
    id<MTLBuffer> linear;
    id<MTLBuffer> params;
    size_t tiled_size, linear_size;
};

/// Runs the kernel `repeat` times in one command buffer (the dispatches run one after the other);
/// returns the GPU time of the command buffer per dispatch in milliseconds.
double Run(id<MTLCommandQueue> queue, const Kernel& kernel, const Buffers& b, u32 groups,
           u32 repeat = 1) {
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
    [enc setComputePipelineState:kernel.pipeline];
    [enc setBuffer:b.tiled offset:0 atIndex:BufferSlot(*kernel.shader, 0)];
    [enc setBuffer:b.linear offset:0 atIndex:BufferSlot(*kernel.shader, 1)];
    [enc setBuffer:b.params offset:0 atIndex:BufferSlot(*kernel.shader, 2)];
    for (u32 i = 0; i < repeat; ++i) {
        [enc dispatchThreadgroups:MTLSizeMake(groups, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(kernel.shader->local_size[0], 1, 1)];
    }
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    if (cb.error) {
        std::fprintf(stderr, "command buffer: %s\n", cb.error.localizedDescription.UTF8String);
        std::exit(1);
    }
    return (cb.GPUEndTime - cb.GPUStartTime) * 1000.0 / repeat;
}

/// Times the copy kernel over `bytes` bytes of the buffers (`repeat` dispatches).
double RunCopy(id<MTLCommandQueue> queue, const Kernel& kernel, const Buffers& b, u32 bits,
               size_t bytes, u32 repeat = 1) {
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
    [enc setComputePipelineState:kernel.pipeline];
    [enc setBuffer:b.tiled offset:0 atIndex:0];
    [enc setBuffer:b.linear offset:0 atIndex:1];
    for (u32 i = 0; i < repeat; ++i) {
        [enc dispatchThreads:MTLSizeMake(bytes / (bits == 64 ? 8 : 4), 1, 1)
            threadsPerThreadgroup:MTLSizeMake(64, 1, 1)];
    }
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    return (cb.GPUEndTime - cb.GPUStartTime) * 1000.0 / repeat;
}

double Median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v[v.size() / 2];
}

/// The padded size of a surface comes from two places: the kernel (and its tests) pad to the macro
/// tile the bank and pipe parameters make (MacroTilePitch/MacroTileHeight), the layout of an image
/// (ImageInfo::UpdateSize) to the table of macro tile extents (tile.h). They have to be the same
/// for every macro tiled mode and texel size, or a mip of ImageInfo would not be whole padded
/// slices. Returns the number of combinations that differ.
int CheckExtentTables(u32& combinations) {
    int differ = 0;
    for (u32 mode = 0; mode < AmdGpu::NUM_TILE_MODES; ++mode) {
        const TileMode tile_mode = static_cast<TileMode>(mode);
        if (mode == 8 || mode >= 27) { // linear
            continue;
        }
        const AmdGpu::ArrayMode array_mode = AmdGpu::GetArrayMode(tile_mode);
        if (!AmdGpu::IsMacroTiled(array_mode)) {
            continue;
        }
        for (const u32 bits : {8u, 16u, 32u, 64u, 128u}) {
            for (const u32 samples : {1u, 2u, 4u, 8u}) {
                VideoCore::ImageInfo info{};
                info.tile_mode = tile_mode;
                info.array_mode = array_mode;
                info.num_bits = bits;
                info.num_samples = samples;
                const TilingCpu::Config cfg = MakeConfig(info);
                const auto [table_pitch, table_height] =
                    VideoCore::GetMacroTileExtents(tile_mode, bits, samples, false);
                ++combinations;
                if (table_pitch != TilingCpu::MacroTilePitch(cfg) ||
                    table_height != TilingCpu::MacroTileHeight(cfg)) {
                    ++differ;
                    std::printf("macro tile of mode %u, %u bpp, %u samples: table %ux%u, banks and "
                                "pipes %ux%u\n",
                                mode, bits, samples, table_pitch, table_height,
                                TilingCpu::MacroTilePitch(cfg), TilingCpu::MacroTileHeight(cfg));
                }
            }
        }
    }
    return differ;
}

int g_failures = 0;
int g_skipped = 0;

/// The mips as the CPU reference takes them: what MakeTilingInfo is expected to give the kernel.
std::vector<TilingCpu::Mip> CpuMips(const VideoCore::ImageInfo& info, u32 num_mips) {
    std::vector<TilingCpu::Mip> mips;
    for (u32 mip = 0; mip < num_mips; ++mip) {
        const auto& layout = info.mips_layout[mip];
        TilingCpu::Mip m{layout.size, layout.pitch, layout.height, layout.offset,
                         (info.micro_tiled_mips >> mip & 1) != 0};
        if (info.props.is_block) {
            m.pitch = std::max((m.pitch + 3) / 4, 1U);
            m.height = std::max((m.height + 3) / 4, 1U);
        }
        mips.push_back(m);
    }
    return mips;
}

/// One texel of the linear image and the tiled bytes it belongs to, as byte offsets of the buffers.
struct TexelMap {
    u64 linear, tiled;
};

/// What the map from the logical texels to tiled bytes does, by the CPU reference.
struct MapStats {
    u64 texels = 0, out_of_mip = 0, collisions = 0, not_contiguous = 0;
    std::string first;
};

/// The map of all texels of the logical slices of all mips: the linear texel (x, y, slice) of a mip
/// is at byte offset + ((slice * height + y) * pitch + x) * bytes of the linear buffer. Checks that
/// the tiled bytes of the texels are inside the mip and that no two texels share one.
std::vector<TexelMap> BuildMap(const TilingCpu::Config& cfg, const std::vector<TilingCpu::Mip>& mips,
                               u32 guest_size, MapStats& stats) {
    const u32 bytes = cfg.bits / 8;
    std::vector<TexelMap> map;
    std::vector<u8> owned(guest_size, 0);
    u64 expected_offset = 0;
    for (u32 m = 0; m < mips.size(); ++m) {
        const TilingCpu::Mip& mip = mips[m];
        if (mip.offset != expected_offset) {
            ++stats.not_contiguous;
        }
        expected_offset += mip.size;
        const u32 slices = TilingCpu::Slices(cfg, mip);
        for (u32 slice = 0; slice < slices; ++slice) {
            for (u32 y = 0; y < mip.height; ++y) {
                for (u32 x = 0; x < mip.pitch; ++x) {
                    const u64 tiled = mip.offset + TilingCpu::Offset(cfg, mip, x, y, slice);
                    const u64 linear = mip.offset + (u64(slice) * mip.height + y) * mip.pitch * bytes +
                                       u64(x) * bytes;
                    ++stats.texels;
                    bool bad = false;
                    if (tiled + bytes > u64(mip.offset) + mip.size) {
                        ++stats.out_of_mip;
                        bad = true;
                    } else {
                        for (u32 i = 0; i < bytes; ++i) {
                            if (owned[tiled + i]++) {
                                bad = true;
                            }
                        }
                        stats.collisions += bad;
                    }
                    if (bad && stats.first.empty()) {
                        char text[120];
                        std::snprintf(text, sizeof(text), "mip %u texel (%u,%u,%u) -> tiled byte %llu",
                                      m, x, y, slice, (unsigned long long)tiled);
                        stats.first = text;
                    }
                    map.push_back({linear, tiled});
                }
            }
        }
    }
    return map;
}

/// What the buffers have to hold after the kernel: the tiler copies the texels of the map into the
/// tiled buffer, the detiler into the linear one; everything else stays as it was.
struct Expected {
    std::vector<u8> tiled, linear;
};

Expected ExpectedBuffers(const std::vector<TexelMap>& map, u32 bytes, bool is_tiler,
                         const std::vector<u32>& tiled_init, const std::vector<u32>& linear_init) {
    Expected e;
    e.tiled.assign(reinterpret_cast<const u8*>(tiled_init.data()),
                   reinterpret_cast<const u8*>(tiled_init.data()) + tiled_init.size() * 4);
    e.linear.assign(reinterpret_cast<const u8*>(linear_init.data()),
                    reinterpret_cast<const u8*>(linear_init.data()) + linear_init.size() * 4);
    for (const TexelMap& t : map) {
        if (is_tiler) {
            std::memcpy(&e.tiled[t.tiled], &e.linear[t.linear], bytes);
        } else {
            std::memcpy(&e.linear[t.linear], &e.tiled[t.tiled], bytes);
        }
    }
    return e;
}

/// How many bytes differ and where the first is.
std::string Diff(const u8* got, const std::vector<u8>& want, const char* what) {
    size_t count = 0, first = 0;
    for (size_t i = 0; i < want.size(); ++i) {
        if (got[i] != want[i]) {
            if (!count) {
                first = i;
            }
            ++count;
        }
    }
    if (!count) {
        return {};
    }
    char text[120];
    std::snprintf(text, sizeof(text), "%zu %s bytes differ (first %zu)", count, what, first);
    return text;
}

// The checks of one case: the layout (the sizes the kernel is given, the map) and, per direction
// and kernel, the buffers against the CPU reference and the repeat run.
constexpr int kChecksPerCase = 2 + 2 * 2 * 2;

void RunCase(id<MTLDevice> device, id<MTLCommandQueue> queue, const Case& c, u32 index) {
    const VideoCore::ImageInfo info = MakeInfo(c);
    const u32 num_mips = info.resources.levels;
    const VideoCore::TilingInfo params = VideoCore::TileManager::MakeTilingInfo(info, num_mips);
    const TilingCpu::Config cfg = MakeConfig(info);
    const std::vector<TilingCpu::Mip> mips = CpuMips(info, num_mips);
    const u32 bytes = c.bits / 8;

    bool ok = true;
    int checks = 0;
    std::string note;
    auto fail = [&](const std::string& what) {
        ok = false;
        note += " [" + what + "]";
    };

    // The sizes TileManager gives the kernel against the padded sizes of the CPU reference.
    ++checks;
    for (u32 m = 0; m < num_mips; ++m) {
        const VideoCore::TilingMip& t = params.mips[m];
        const TilingCpu::Mip& r = mips[m];
        if (t.size != r.size || t.pitch != r.pitch || t.height != r.height || t.offset != r.offset ||
            t.padded_pitch != TilingCpu::PaddedPitch(cfg, r.micro, r.pitch) ||
            t.padded_height != TilingCpu::PaddedHeight(cfg, r.micro, r.height) ||
            t.slices != TilingCpu::Slices(cfg, r)) {
            char text[160];
            std::snprintf(text, sizeof(text),
                          "mip %u kernel layout %ux%u padded %ux%u slices %u, reference padded "
                          "%ux%u slices %u",
                          m, t.pitch, t.height, t.padded_pitch, t.padded_height, t.slices,
                          TilingCpu::PaddedPitch(cfg, r.micro, r.pitch),
                          TilingCpu::PaddedHeight(cfg, r.micro, r.height), TilingCpu::Slices(cfg, r));
            fail(text);
        }
    }

    // The map of the CPU reference: injective, inside the mips.
    ++checks;
    MapStats stats;
    const std::vector<TexelMap> map = BuildMap(cfg, mips, info.guest_size, stats);
    if (stats.not_contiguous || stats.out_of_mip || stats.collisions) {
        char text[200];
        std::snprintf(text, sizeof(text),
                      "map of %llu texels: %llu outside the mip, %llu colliding, %llu mips not "
                      "contiguous; %s",
                      (unsigned long long)stats.texels, (unsigned long long)stats.out_of_mip,
                      (unsigned long long)stats.collisions, (unsigned long long)stats.not_contiguous,
                      stats.first.c_str());
        fail(text);
    }

    const size_t image_bytes = info.guest_size;
    // The guest size plus a guard area of random bytes that no write may change.
    const size_t tiled_size = image_bytes + (1u << 16);
    const size_t linear_size = image_bytes + (1u << 16);
    Buffers b;
    b.tiled_size = tiled_size;
    b.linear_size = linear_size;
    b.tiled = [device newBufferWithLength:tiled_size options:MTLResourceStorageModeShared];
    b.linear = [device newBufferWithLength:linear_size options:MTLResourceStorageModeShared];
    b.params = [device newBufferWithBytes:&params
                                   length:sizeof(params)
                                  options:MTLResourceStorageModeShared];
    Buffers r;
    r.tiled_size = tiled_size;
    r.linear_size = linear_size;
    r.tiled = [device newBufferWithLength:tiled_size options:MTLResourceStorageModeShared];
    r.linear = [device newBufferWithLength:linear_size options:MTLResourceStorageModeShared];
    r.params = b.params;

    std::vector<u32> tiled_init(tiled_size / 4), linear_init(linear_size / 4);
    Rng rng{0x9E3779B97F4A7C15ull * (index + 1)};
    for (u32& v : tiled_init) {
        v = rng.Next();
    }
    for (u32& v : linear_init) {
        v = rng.Next();
    }

    double timing[2][2]{}; // [tiler][reference], warm
    double cold[2][2]{};   // the same, one dispatch after an idle GPU
    double floor_warm = 0, floor_cold = 0; // a plain copy of the image
    for (const bool is_tiler : {false, true}) {
        const char* direction = is_tiler ? "tiler" : "detiler";
        const auto dispatch = VideoCore::TileManager::MakeDispatch(info, is_tiler, {}, {}, {});
        const bool macro = dispatch.is_macro;
        const Kernel fresh =
            MakeKernel(device, GetShader(false, c.bits, macro), dispatch.constants,
                       dispatch.num_constants);
        const Kernel ref = MakeKernel(device, GetShader(true, c.bits, macro), dispatch.constants,
                                      dispatch.num_constants);
        const u32 ref_groups = static_cast<u32>((info.guest_size / bytes + 63) / 64);
        const u32 new_groups = dispatch.group_count_x;
        const Expected want = ExpectedBuffers(map, bytes, is_tiler, tiled_init, linear_init);

        auto reset = [&](const Buffers& buffers) {
            std::memcpy(buffers.tiled.contents, tiled_init.data(), tiled_size);
            std::memcpy(buffers.linear.contents, linear_init.data(), linear_size);
        };
        // Each kernel runs twice from the same buffers: the runs have to agree (colliding
        // addresses would race) and to be the CPU reference's.
        for (const bool reference : {false, true}) {
            const char* name = reference ? "reference kernel" : "kernel";
            const Kernel& kernel = reference ? ref : fresh;
            const Buffers& buffers = reference ? r : b;
            const u32 groups = reference ? ref_groups : new_groups;
            std::vector<u8> first_tiled, first_linear;
            for (int run = 0; run < 2; ++run) {
                reset(buffers);
                Run(queue, kernel, buffers, groups);
                const u8* got_tiled = static_cast<const u8*>(buffers.tiled.contents);
                const u8* got_linear = static_cast<const u8*>(buffers.linear.contents);
                if (run == 0) {
                    first_tiled.assign(got_tiled, got_tiled + tiled_size);
                    first_linear.assign(got_linear, got_linear + linear_size);
                    ++checks;
                    std::string d = Diff(got_tiled, want.tiled, "tiled");
                    const std::string dl = Diff(got_linear, want.linear, "linear");
                    d += d.empty() || dl.empty() ? dl : ", " + dl;
                    if (!d.empty()) {
                        fail(std::string(name) + " " + direction + " against the CPU reference: " + d);
                    }
                } else {
                    ++checks;
                    if (std::memcmp(first_tiled.data(), got_tiled, tiled_size) != 0 ||
                        std::memcmp(first_linear.data(), got_linear, linear_size) != 0) {
                        fail(std::string(name) + " " + direction + " not deterministic");
                    }
                }
            }
        }

        // GPU time: runs alternate between the two kernels, ten dispatches per command buffer,
        // median of the command buffers.
        const u32 runs = c.timed ? 21 : 5;
        std::vector<double> t_ref, t_new;
        for (u32 i = 0; i < runs + 2; ++i) {
            const double tr = Run(queue, ref, r, ref_groups, 10);
            const double tn = Run(queue, fresh, b, new_groups, 10);
            if (i >= 2) {
                t_ref.push_back(tr);
                t_new.push_back(tn);
            }
        }
        timing[is_tiler][0] = Median(t_ref);
        timing[is_tiler][1] = Median(t_new);
        if (c.timed) {
            // The game runs the kernel on a GPU that has little else to do and is clocked down:
            // single dispatches after 30 ms of idle time.
            t_ref.clear();
            t_new.clear();
            for (u32 i = 0; i < 21; ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(30));
                t_ref.push_back(Run(queue, ref, r, ref_groups));
                std::this_thread::sleep_for(std::chrono::milliseconds(30));
                t_new.push_back(Run(queue, fresh, b, new_groups));
            }
            cold[is_tiler][0] = Median(t_ref);
            cold[is_tiler][1] = Median(t_new);
            if (is_tiler) {
                const Kernel copy = MakeCopyKernel(device, c.bits);
                std::vector<double> warm_floor, cold_floor;
                for (u32 i = 0; i < 15; ++i) {
                    warm_floor.push_back(RunCopy(queue, copy, b, c.bits, image_bytes, 10));
                    std::this_thread::sleep_for(std::chrono::milliseconds(30));
                    cold_floor.push_back(RunCopy(queue, copy, b, c.bits, image_bytes));
                }
                floor_warm = Median(warm_floor);
                floor_cold = Median(cold_floor);
            }
        }
    }
    // A check that was not made fails the case.
    if (checks != kChecksPerCase) {
        g_skipped += kChecksPerCase - checks;
        fail("checks not made");
    }
    std::printf("%-4s %-30s tile %6.3f -> %6.3f ms (x%.2f)  detile %6.3f -> %6.3f ms (x%.2f)%s\n",
                ok ? "ok" : "FAIL", c.name, timing[1][0], timing[1][1], timing[1][0] / timing[1][1],
                timing[0][0], timing[0][1], timing[0][0] / timing[0][1], note.c_str());
    if (c.timed) {
        std::printf("     %-30s cold: tile %6.3f -> %6.3f ms (x%.2f)  detile %6.3f -> %6.3f ms (x%.2f)"
                    "   plain copy of the image: warm %.3f ms, cold %.3f ms\n",
                    "", cold[1][0], cold[1][1], cold[1][0] / cold[1][1], cold[0][0], cold[0][1],
                    cold[0][0] / cold[0][1], floor_warm, floor_cold);
    }
    if (!ok) {
        ++g_failures;
    }
}

} // namespace

int main(int argc, char** argv) {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            std::fprintf(stderr, "no Metal device\n");
            return 1;
        }
        id<MTLCommandQueue> queue = [device newCommandQueue];
        std::printf("GPU: %s; times are reference kernel -> kernel (median GPU time)\n",
                    device.name.UTF8String);
        {
            u32 combinations = 0;
            const int differ = CheckExtentTables(combinations);
            std::printf("macro tile extents of the layout table against banks and pipes: %u modes x sizes, %d differ\n", combinations, differ);
            g_failures += differ ? 1 : 0;
        }
        u32 index = 0;
        // an argument selects the cases whose name contains it
        u32 run = 0;
        for (const auto& list : {std::span<const Case>(kCases), std::span<const Case>(kCustomCases)}) {
            for (const Case& c : list) {
                @autoreleasepool {
                    if (argc < 2 || std::strstr(c.name, argv[1])) {
                        RunCase(device, queue, c, index);
                        ++run;
                    }
                    ++index;
                }
            }
        }
        if (run == 0) {
            std::printf("no case matches\n");
            return 1;
        }
        std::printf("%u cases, %d failed, %d skipped\n", run, g_failures, g_skipped);
        return g_failures || g_skipped ? 1 : 0;
    }
}
