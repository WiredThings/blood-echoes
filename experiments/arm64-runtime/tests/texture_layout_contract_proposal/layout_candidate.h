// SPDX-License-Identifier: GPL-2.0-or-later
// Review-only predicate. Not integrated into any cache or resource selection.
#pragma once
#include "video_core/texture_cache/image_info.h"
namespace WhiteModelReview {
// Byte interpretation, not image identity. Address, logical dimension and typed
// format compatibility remain separate decisions at the existing caller.
inline bool SameGuestByteLayout(const VideoCore::ImageInfo& a,const VideoCore::ImageInfo& b) {
 if(a.guest_size!=b.guest_size || a.size!=b.size || a.resources!=b.resources ||
    a.pitch!=b.pitch || a.num_bits!=b.num_bits || a.num_samples!=b.num_samples ||
    a.tile_mode!=b.tile_mode || a.array_mode!=b.array_mode ||
    a.bank_swizzle!=b.bank_swizzle || a.alt_tile!=b.alt_tile ||
    a.props.is_volume!=b.props.is_volume || a.props.is_block!=b.props.is_block ||
    a.props.is_tiled!=b.props.is_tiled || a.props.is_pow2!=b.props.is_pow2 ||
    a.micro_tiled_mips!=b.micro_tiled_mips || a.resources.levels>a.mips_layout.size())return false;
 for(unsigned m=0;m<a.resources.levels;++m) {
  const auto& x=a.mips_layout[m];const auto& y=b.mips_layout[m];
  if(x.size!=y.size || x.pitch!=y.pitch || x.height!=y.height || x.offset!=y.offset)return false;
 }return true;
}
}
