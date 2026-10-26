// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#ifndef __OBJC__
#error "mtl_format_convert_source.h is for Objective-C++ only"
#endif
#import <Foundation/Foundation.h>
namespace Metal::FormatConvert {
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
inline NSString* const KernelSource = @"#include <metal_stdlib>\n"
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

} // namespace Metal::FormatConvert
