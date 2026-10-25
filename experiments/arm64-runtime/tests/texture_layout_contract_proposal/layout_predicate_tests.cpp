// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/amdgpu/resource.h"
#include "layout_candidate.h"
#include <cassert>
#include <cstdio>
using namespace VideoCore;using WhiteModelReview::SameGuestByteLayout;
int main(){ImageInfo a{};a.type=AmdGpu::ImageType::Color2D;a.size={64,64,1};a.resources={1,1};a.pitch=64;a.num_bits=32;a.guest_size=16384;a.pixel_format=vk::Format::eR8G8B8A8Unorm;a.mips_layout[0]={16384,64,64,0};unsigned n=0;
#define REJECT(change) {auto b=a;change;assert(!SameGuestByteLayout(a,b));++n;}
#define ACCEPT(change) {auto b=a;change;assert(SameGuestByteLayout(a,b));++n;}
ACCEPT((void)0);ACCEPT(b.guest_address=0x10000);ACCEPT(b.pixel_format=vk::Format::eR8G8B8A8Srgb);ACCEPT(b.props.is_depth=1);ACCEPT(b.type=AmdGpu::ImageType::Color1D);
REJECT(b.pitch=128);REJECT(b.tile_mode=AmdGpu::TileMode::Thin1DThin);REJECT(b.array_mode=AmdGpu::ArrayMode::Array1DTiledThin1);REJECT(b.bank_swizzle=1);REJECT(b.alt_tile=true);REJECT(b.props.is_volume=1);REJECT(b.props.is_block=1);REJECT(b.props.is_tiled=1);REJECT(b.props.is_pow2=1);REJECT(b.resources.levels=2);REJECT(b.resources.layers=2);REJECT(b.size.width=32);REJECT(b.num_bits=16);REJECT(b.num_samples=2);REJECT(b.micro_tiled_mips=1);REJECT(b.mips_layout[0].size=8192);REJECT(b.mips_layout[0].pitch=128);REJECT(b.mips_layout[0].height=32);REJECT(b.mips_layout[0].offset=256);REJECT(b.resources.levels=17);
ACCEPT(b.mips_layout[15].offset=99); // unused layout bytes are not identity
printf("LAYOUT_PREDICATE controls=%u PASS (not integrated)\n",n);
}
