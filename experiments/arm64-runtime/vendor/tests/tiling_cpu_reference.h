// SPDX-License-Identifier: GPL-2.0-or-later
// A CPU implementation of the GCN (Southern/Sea Islands) surface address of a texel, for the test
// of the tiling kernel (test_mtl_tiling.mm). Written from the semantics of the AMD address library
// (addrlib: EgBasedAddrLib::ComputeSurfaceAddrFromCoordMicroTiled / MacroTiled), texel by texel
// and not from the kernel: the micro tile order comes from tables of bit sources, the bank and
// pipe hashes from tables of address bits.
//
// The address library works on the PADDED surface: the pitch is a multiple of the macro tile pitch
// and the height of the macro tile height (micro tiled surfaces: 8), and a slice is
// padded pitch * padded height texels. The logical size of an image (what the game draws into, the
// rows the linear copy holds) may be smaller; the padding is addressed as everything else but
// never holds image data. Offset() therefore takes the padded dimensions; PaddedPitch/PaddedHeight
// round the logical ones up.
#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <vector>

namespace TilingCpu {

using u32 = std::uint32_t;
using u64 = std::uint64_t;

/// The fixed properties of a tile mode at a texel size, as the address library takes them.
struct Config {
    u32 bits = 32;       ///< bits per texel (per block of a block compressed format)
    u32 samples = 1;
    u32 micro_mode = 1;  ///< 0 display, 1 thin, 2 depth, 3 rotated, 4 thick
    u32 thickness = 1;   ///< of the micro tile of the array mode: 1, 4 or 8 slices
    bool macro = false;  ///< the array mode is macro tiled (2D/3D, PRT or not)
    bool prt = false;    ///< PRT: the bank and pipe come from the position in the macro tile
    bool mode_3d = false;
    bool mode_2d = false;
    bool split_rotates = false; ///< the tile split slices rotate the banks (2D/3D thin modes)
    u32 num_pipes = 8;
    u32 pipe_config = 0; ///< 0 P2, 10 P8_32x32_8x16, 12 P8_32x32_16x16
    u32 bank_width = 1, bank_height = 1, num_banks = 8, macro_aspect = 1;
    u32 tile_split_bytes = 256;
    u32 bank_swizzle = 0;
};

inline u32 Log2(u32 v) {
    u32 r = 0;
    while ((1u << (r + 1)) <= v) {
        ++r;
    }
    return r;
}

inline u32 Bit(u32 v, u32 b) {
    return (v >> b) & 1;
}

inline u32 AlignUp(u32 v, u32 a) {
    return (v + a - 1) / a * a;
}

inline u32 MacroTilePitch(const Config& c) {
    return 8 * c.bank_width * c.num_pipes * c.macro_aspect;
}

inline u32 MacroTileHeight(const Config& c) {
    return 8 * c.bank_height * c.num_banks / c.macro_aspect;
}

/// The micro tiled and macro tiled padding of a logical pitch/height.
inline u32 PaddedPitch(const Config& c, bool micro_mip, u32 pitch) {
    return AlignUp(pitch, c.macro && !micro_mip ? MacroTilePitch(c) : 8);
}

inline u32 PaddedHeight(const Config& c, bool micro_mip, u32 height) {
    return AlignUp(height, c.macro && !micro_mip ? MacroTileHeight(c) : 8);
}

/// The index of the texel in its micro tile, as a list of the coordinate bits from the least
/// significant bit of the index on ("x0 y0 x1 ...", z0 and z1 for thick tiles).
inline std::string PixelOrder(const Config& c, u32 thickness) {
    if (c.micro_mode == 0) {
        switch (c.bits) {
        case 8: return "x0 x1 x2 y1 y0 y2";
        case 16: return "x0 x1 x2 y0 y1 y2";
        case 32: return "x0 x1 y0 x2 y1 y2";
        case 64: return "x0 y0 x1 x2 y1 y2";
        case 128: return "x0 y0 x1 y1 x2 y2";
        }
        std::abort();
    }
    if (c.micro_mode == 1 || c.micro_mode == 2) {
        return "x0 y0 x1 y1 x2 y2";
    }
    std::string order;
    switch (c.bits) {
    case 8:
    case 16: order = "x0 y0 x1 y1 z0 z1"; break;
    case 32: order = "x0 y0 x1 z0 y1 z1"; break;
    case 64:
    case 128: order = "x0 y0 z0 x1 y1 z1"; break;
    default: std::abort();
    }
    order += " x2 y2";
    if (thickness == 8) {
        order += " z2";
    }
    return order;
}

inline u32 PixelIndex(const Config& c, u32 thickness, u32 x, u32 y, u32 z) {
    const std::string order = PixelOrder(c, thickness);
    u32 index = 0, position = 0;
    for (size_t i = 0; i + 1 < order.size(); i += 3) {
        const u32 b = order[i + 1] - '0';
        const u32 v = order[i] == 'x' ? x : order[i] == 'y' ? y : z;
        index |= Bit(v, b) << position++;
    }
    return index;
}

/// The byte offset of the texel (sample 0) in a micro tiled surface of pitch x height texels per
/// slice (both multiples of 8); `thickness` slices share a micro tile.
inline u64 MicroTiledOffset(const Config& c, u32 thickness, u32 x, u32 y, u32 slice, u32 pitch,
                            u32 height) {
    const u64 tile_bytes = u64(64) * thickness * c.bits * c.samples / 8;
    const u64 slice_bytes = (u64(pitch) * height * thickness * c.bits * c.samples + 7) / 8;
    const u64 tile_index = u64(y / 8) * (pitch / 8) + x / 8;
    const u32 pixel = PixelIndex(c, thickness, x, y, slice);
    // depth: the samples of a texel are next to each other; otherwise a sample is a plane
    const u64 element = c.micro_mode == 2 ? u64(pixel) * c.bits * c.samples / 8
                                          : u64(pixel) * c.bits / 8;
    return u64(slice / thickness) * slice_bytes + tile_index * tile_bytes + element;
}

inline u32 PipeOfTile(const Config& c, u32 x, u32 y, u32 slice) {
    // x and y are texel coordinates: the bits 3.. are the micro tile's
    const u32 x3 = Bit(x, 3), x4 = Bit(x, 4), x5 = Bit(x, 5);
    const u32 y3 = Bit(y, 3), y4 = Bit(y, 4), y5 = Bit(y, 5);
    u32 pipe = 0;
    switch (c.pipe_config) {
    case 0: pipe = x3 ^ y3; break;
    case 10: pipe = (x4 ^ y3 ^ x5) | ((x3 ^ y4) << 1) | ((x5 ^ y5) << 2); break;
    case 12: pipe = (x3 ^ y3 ^ x4) | ((x4 ^ y4) << 1) | ((x5 ^ y5) << 2); break;
    default: std::abort();
    }
    if (c.mode_3d) {
        const u32 swizzle = std::max(1u, c.num_pipes / 2 - 1) * (slice / c.thickness);
        pipe ^= swizzle & (c.num_pipes - 1);
    }
    return pipe;
}

inline u32 BankOfTile(const Config& c, u32 x, u32 y, u32 slice, u32 tile_split_slice) {
    // x bits above the pipes (and bank width) of the tile, y bits above the bank height
    const u32 xs = 3 + Log2(c.bank_width * c.num_pipes);
    const u32 ys = 3 + Log2(c.bank_height);
    const u32 x3 = Bit(x, xs), x4 = Bit(x, xs + 1), x5 = Bit(x, xs + 2), x6 = Bit(x, xs + 3);
    const u32 y3 = Bit(y, ys), y4 = Bit(y, ys + 1), y5 = Bit(y, ys + 2), y6 = Bit(y, ys + 3);
    u32 bank = 0;
    switch (c.num_banks) {
    case 2: bank = x3 ^ y3; break;
    case 4: bank = (x3 ^ y4) | ((x4 ^ y3) << 1); break;
    case 8: bank = (x3 ^ y5) | ((x4 ^ y4 ^ y5) << 1) | ((x5 ^ y3) << 2); break;
    case 16:
        bank = (x3 ^ y6) | ((x4 ^ y5 ^ y6) << 1) | ((x5 ^ y4) << 2) | ((x6 ^ y3) << 3);
        break;
    default: std::abort();
    }
    u32 rotation = 0;
    if (c.mode_2d) {
        rotation = (c.num_banks / 2 - 1) * (slice / c.thickness);
    } else if (c.mode_3d) {
        rotation = std::max(1u, c.num_pipes / 2 - 1) * (slice / c.thickness) / c.num_pipes;
    }
    bank ^= c.bank_swizzle + rotation;
    if (c.split_rotates) {
        bank ^= (c.num_banks / 2 + 1) * tile_split_slice;
    }
    return bank & (c.num_banks - 1);
}

/// The byte offset of the texel (sample 0) in a macro tiled surface of pitch x height texels per
/// slice (a multiple of the macro tile pitch and height).
inline u64 MacroTiledOffset(const Config& c, u32 x, u32 y, u32 slice, u32 pitch, u32 height) {
    const u32 pixel = PixelIndex(c, c.thickness, x, y, slice);
    u64 element = c.micro_mode == 2 ? u64(pixel) * c.bits * c.samples / 8
                                    : u64(pixel) * c.bits / 8;
    const u64 micro_tile_bytes = u64(64) * c.thickness * c.bits * c.samples / 8;
    // a micro tile of thin modes is cut in tile split slices when it is bigger than the split
    u64 tile_bytes = micro_tile_bytes;
    u32 split_slices = 1, split_slice = 0;
    if (micro_tile_bytes > c.tile_split_bytes && c.thickness == 1) {
        split_slices = u32(micro_tile_bytes / c.tile_split_bytes);
        split_slice = u32(element / c.tile_split_bytes);
        element %= c.tile_split_bytes;
        tile_bytes = c.tile_split_bytes;
    }
    const u32 macro_pitch = MacroTilePitch(c);
    const u32 macro_height = MacroTileHeight(c);
    const u64 macro_bytes = tile_bytes * (macro_pitch / 8) * (macro_height / 8) /
                            (c.num_pipes * c.num_banks);
    const u64 macros_per_row = pitch / macro_pitch;
    const u64 macros_per_slice = macros_per_row * (height / macro_height);
    const u64 slice_bytes = macros_per_slice * macro_bytes;
    const u64 slice_offset =
        slice_bytes * (split_slice + u64(split_slices) * (slice / c.thickness));
    const u64 macro_offset = ((y / macro_height) * macros_per_row + x / macro_pitch) * macro_bytes;
    const u64 tile_in_bank = u64((y / 8) % c.bank_height) * c.bank_width +
                             ((x / 8) / c.num_pipes) % c.bank_width;
    const u64 total = slice_offset + macro_offset + tile_in_bank * tile_bytes + element;

    u32 px = x, py = y;
    if (c.prt) {
        px %= macro_pitch;
        py %= macro_height;
    }
    const u32 pipe = PipeOfTile(c, px, py, slice);
    const u32 bank = BankOfTile(c, px, py, slice, split_slice);
    // the pipe interleave is 256 bytes: pipe and bank bits sit above the lowest 8 bits
    const u32 pipe_bits = Log2(c.num_pipes), bank_bits = Log2(c.num_banks);
    return (total & 255) | (u64(pipe) << 8) | (u64(bank) << (8 + pipe_bits)) |
           ((total >> 8) << (8 + pipe_bits + bank_bits));
}

/// The layout of a mip as the kernel gets it (see tile_manager.h's TilingMip): the logical
/// pitch/height (the rows of the linear copy), `slices` whole slices of the padded surface.
struct Mip {
    u32 size, pitch, height, offset;
    bool micro; ///< micro tiled (a macro mode's small mips; all mips of the micro modes)
};

inline u64 Offset(const Config& c, const Mip& m, u32 x, u32 y, u32 slice) {
    const u32 pitch = PaddedPitch(c, m.micro, m.pitch);
    const u32 height = PaddedHeight(c, m.micro, m.height);
    if (!c.macro) {
        return MicroTiledOffset(c, c.thickness, x, y, slice, pitch, height);
    }
    if (m.micro) {
        return MicroTiledOffset(c, std::min(c.thickness, 4u), x, y, slice, pitch, height);
    }
    return MacroTiledOffset(c, x, y, slice, pitch, height);
}

/// Whole slices of the padded surface that a mip of `size` bytes holds: of whole micro tiles,
/// which hold `thickness` slices.
inline u32 Slices(const Config& c, const Mip& m) {
    const u64 slice_bytes = u64(PaddedPitch(c, m.micro, m.pitch)) *
                            PaddedHeight(c, m.micro, m.height) * c.bits * c.samples / 8;
    const u32 thickness = c.macro && m.micro ? std::min(c.thickness, 4u) : c.thickness;
    const u32 slices = slice_bytes ? u32(m.size / slice_bytes) : 0;
    return slices - slices % thickness;
}

} // namespace TilingCpu
