// SPDX-License-Identifier: GPL-2.0-or-later
// Read-only ABI assertions against actual public declarations; private tail proposal only.
#include "video_core/texture_cache/tile_manager.h"
#include "bb_tiler_bounds.h"
#include <type_traits>
#include <cstddef>
#include <cstdio>
using namespace VideoCore;
struct LogicalMipBounds {u32 elements_per_row,rows,slices;};
struct TilerParamsV0 {TilingInfo physical;std::array<LogicalMipBounds,16> logical;};
struct TilerParamsV1 {TilingInfo physical;u32 logical_bounds_version;std::array<LogicalMipBounds,16> logical;};
static_assert(std::is_standard_layout_v<TilingMip> && std::is_standard_layout_v<TilingInfo> && std::is_standard_layout_v<TilerParamsV1>);
static_assert(sizeof(TilingMip)==28 && alignof(TilingMip)==4);
static_assert(offsetof(TilingMip,size)==0 && offsetof(TilingMip,pitch)==4 && offsetof(TilingMip,height)==8 && offsetof(TilingMip,offset)==12 && offsetof(TilingMip,padded_pitch)==16 && offsetof(TilingMip,padded_height)==20 && offsetof(TilingMip,slices)==24);
static_assert(sizeof(TilingInfo)==460 && alignof(TilingInfo)==4 && offsetof(TilingInfo,mips)==12);
static_assert(sizeof(KernelBuffer)==24 && alignof(KernelBuffer)==8);
static_assert(sizeof(TilingKey)==16 && sizeof(TilingDispatch)==160 && alignof(TilingDispatch)==8);
static_assert(offsetof(TilingDispatch,key)==0 && offsetof(TilingDispatch,array_mode)==16 && offsetof(TilingDispatch,is_macro)==20 && offsetof(TilingDispatch,constants)==24 && offsetof(TilingDispatch,num_constants)==72 && offsetof(TilingDispatch,tiled)==80 && offsetof(TilingDispatch,linear)==104 && offsetof(TilingDispatch,params)==128 && offsetof(TilingDispatch,group_count_x)==152);
static_assert(sizeof(LogicalMipBounds)==12 && alignof(LogicalMipBounds)==4);
static_assert(offsetof(TilerParamsV0,physical)==0 && offsetof(TilerParamsV0,logical)==460 && sizeof(TilerParamsV0)==652 && alignof(TilerParamsV0)==4);
static_assert(offsetof(TilerParamsV1,physical)==0 && offsetof(TilerParamsV1,logical_bounds_version)==460 && offsetof(TilerParamsV1,logical)==464 && sizeof(TilerParamsV1)==656 && alignof(TilerParamsV1)==4);
static_assert(sizeof(VideoCore::TilerBounds::ParamsV1)==652 && offsetof(VideoCore::TilerBounds::ParamsV1,logical)==460);
int main(){printf("{\"private_no_version_bytes\":%zu,\"private_logical_offset\":%zu}\n",sizeof(TilerParamsV0),offsetof(TilerParamsV0,logical));printf("{\"TilingMip\":%zu,\"TilingInfo\":%zu,\"KernelBuffer\":%zu,\"TilingKey\":%zu,\"TilingDispatch\":%zu,\"proposed_tiler_params\":%zu,\"proposed_version_offset\":%zu,\"proposed_logical_offset\":%zu}\n",sizeof(TilingMip),sizeof(TilingInfo),sizeof(KernelBuffer),sizeof(TilingKey),sizeof(TilingDispatch),sizeof(TilerParamsV1),offsetof(TilerParamsV1,logical_bounds_version),offsetof(TilerParamsV1,logical));}
