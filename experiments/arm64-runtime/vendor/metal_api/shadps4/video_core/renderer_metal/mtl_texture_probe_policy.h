// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <optional>
namespace Metal::TextureProbePolicy {
constexpr uint64_t MaxBytes=16u*1024u*1024u;
constexpr unsigned MaxImages=32, MaxLevels=12;
struct Plane {uint64_t offset{}, row{}, rows{}, bytes{};};
// Raw blocks, without shader conversion. Reject unknown shapes instead of guessing.
inline std::optional<Plane> Layout(uint64_t width,uint64_t height,unsigned block,
                                  unsigned bytes_per_block,uint64_t offset) {
    if(!width||!height||width>16384||height>16384||
       (block!=1&&block!=4)||!bytes_per_block||bytes_per_block>16||offset>MaxBytes)return {};
    const uint64_t blocks=(width+block-1)/block;
    const uint64_t rows=(height+block-1)/block;
    const uint64_t row=(blocks*bytes_per_block+255)&~uint64_t(255);
    offset=(offset+255)&~uint64_t(255);
    if(offset>MaxBytes||rows>(MaxBytes-offset)/row)return {};
    return Plane{offset,row,rows,row*rows};
}
}
