// SPDX-License-Identifier: MIT
#pragma once
#include "bb_cpu_cache_identity.h"
#include "core/libraries/kernel/threads/exception.h"
#include <string_view>
#include <utility>
namespace BbOwnedSignalLibc {
inline constexpr std::string_view SourceSHA="4378b47f46f1d856824a6f971db1a0c3f41833b77f49d2fba9538f667a139166";
// Only offsets and hashes, no original guest bytes. Verified from the fixed
// local 1.09 libc ELF VA domain, not raw ELF file offsets.
struct Region{size_t offset,size;std::string_view sha;};
inline constexpr std::array<Region,6> Regions{{
 {0x546c0,240,"9ad775a0e3827b8817f39b76817f26f03d48538fee0f66ad72c58ff063cb9704"},
 {0x54570,329,"e8186da30b9bee3e10867f0f494065027a2e2e30f790d6b8d391ba1513692090"},
 {0x93a30,112,"24df497d627a5f8f91eea14f6a15d9bf33d713836dfb4db3b8d8b6195ede58eb"},
 {0x54510,90,"ec86313251768b1f18e9a0b1c39ce46cc8bb1ee19fc243111fa0e621a19b4850"},
 {0x52ab0,36,"e77c5b608dffffc2b26519c04e60b7dd557cfee4c34be3d9f464de40ecd49558"},
 {0x520,6,"0655b139556e8836e69fe1852b18cd7ec0e2f9dc9aa5186f967bd6d0033c61f4"}}};
// Actual fixed libc DWARF register-to-Ucontext-address mapping.
inline constexpr std::array<size_t,17> RegisterOffsets{0x78,0x58,0x60,0x80,0x50,0x48,0x88,0xf8,0x68,0x70,0x90,0x98,0xa0,0xa8,0xb0,0xb8,0xe0};
inline bool Admit(std::span<const std::byte> image){
 constexpr char hex[]="0123456789abcdef";
 for(auto region:Regions){if(region.offset>image.size()||region.size>image.size()-region.offset)return false;
  auto hash=BbCpuCache::Hash(image.subspan(region.offset,region.size));
  for(size_t n=0;n<hash.size();++n)if(region.sha[2*n]!=hex[hash[n]>>4]||region.sha[2*n+1]!=hex[hash[n]&15])return false;
 }return true;
}
using M=Libraries::Kernel::Mcontext;using U=Libraries::Kernel::Ucontext;
static_assert(sizeof(U)==0x500&&sizeof(M)==0x480&&offsetof(U,uc_mcontext)==0x40);
static_assert(offsetof(M,mc_rsp)+0x80==0x138&&offsetof(M,mc_rip)+0x80==0x120);
static_assert(offsetof(M,mc_fpstate)+0x40==0x140);
}
