// SPDX-License-Identifier: MIT
// Opt-in metadata only. No guest pointers are followed and selection is unchanged.
#pragma once
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <algorithm>
namespace BbLayoutAudit {
constexpr unsigned Limit=64;
struct Budget {
 std::atomic<unsigned> emitted{};
 unsigned Claim() noexcept {
  auto n=emitted.load(std::memory_order_relaxed);
  while(n<Limit)if(emitted.compare_exchange_weak(n,n+1,std::memory_order_relaxed))return n+1;
  return false;
 }
};
inline Budget& Counter(){static Budget b;return b;}
inline bool Enabled(){static const bool enabled=[] {const char*v=std::getenv("BB_ARM_LAYOUT_AUDIT");const bool result=v&&v[0]=='1'&&v[1]=='\0';if(result)std::fputs("BB_LAYOUT_AUDIT_READY schema=1 limit=64\n",stderr);return result;}();return enabled;}
template<class Info> bool SameByteLayout(const Info&a,const Info&b) noexcept {
 if(a.guest_size!=b.guest_size || a.size!=b.size || a.resources!=b.resources ||
    a.pitch!=b.pitch || a.num_bits!=b.num_bits || a.num_samples!=b.num_samples ||
    a.tile_mode!=b.tile_mode || a.array_mode!=b.array_mode ||
    a.bank_swizzle!=b.bank_swizzle || a.alt_tile!=b.alt_tile ||
    a.props.is_volume!=b.props.is_volume || a.props.is_block!=b.props.is_block ||
    a.props.is_tiled!=b.props.is_tiled || a.props.is_pow2!=b.props.is_pow2 ||
    a.micro_tiled_mips!=b.micro_tiled_mips || a.resources.levels>a.mips_layout.size())return false;
 for(unsigned m=0;m<a.resources.levels;++m){const auto&x=a.mips_layout[m];const auto&y=b.mips_layout[m];
  if(x.size!=y.size||x.pitch!=y.pitch||x.height!=y.height||x.offset!=y.offset)return false;
 }return true;
}
template<class Info> bool IsMismatch(const Info&a,const Info&b) noexcept {
 return a.guest_address==b.guest_address&&a.guest_size==b.guest_size&&
        a.size==b.size&&a.resources==b.resources&&!SameByteLayout(a,b);
}
struct Text {
 char bytes[4096]{};size_t at{};bool failed{};
 template<class...Args> void Add(const char*fmt,Args...args) noexcept {
  if(failed)return;int n;
  if constexpr(sizeof...(Args)==0)n=std::snprintf(bytes+at,sizeof(bytes)-at,"%s",fmt);
  else n=std::snprintf(bytes+at,sizeof(bytes)-at,fmt,args...);
  if(n<0||size_t(n)>=sizeof(bytes)-at){failed=true;return;}at+=size_t(n);
 }
};
template<class Info> void Describe(Text&w,const Info&i) noexcept {
 w.Add("{\"type\":%u,\"format\":%u,\"extent\":[%u,%u,%u],\"levels\":%u,\"layers\":%u,\"pitch\":%u,\"bits\":%u,\"samples\":%u,\"tile\":%u,\"array\":%u,\"bank\":%u,\"alt\":%u,\"volume\":%u,\"block\":%u,\"tiled\":%u,\"pow2\":%u,\"micro_mips\":%u,\"mips\":[",
  unsigned(i.type),unsigned(i.pixel_format),i.size.width,i.size.height,i.size.depth,i.resources.levels,i.resources.layers,i.pitch,i.num_bits,i.num_samples,unsigned(i.tile_mode),unsigned(i.array_mode),unsigned(i.bank_swizzle),unsigned(i.alt_tile),unsigned(i.props.is_volume),unsigned(i.props.is_block),unsigned(i.props.is_tiled),unsigned(i.props.is_pow2),i.micro_tiled_mips);
 for(unsigned n=0;n<std::min<unsigned>(i.resources.levels,i.mips_layout.size());++n){const auto&m=i.mips_layout[n];w.Add("%s[%u,%u,%u,%u]",n?",":"",m.size,m.pitch,m.height,m.offset);}
 w.Add("]}");
}
// Caller holds the cache mutex, uses an owned decoded descriptor, and claims one
// slot only after Enabled/IsMismatch. No resources are retained by this function.
template<class Info> bool Print(unsigned event,const Info&request,const Info&selected,const char*path,
 uint64_t uid,uintptr_t backing,uint64_t registry,uint64_t allocation,
 uint32_t flags,uint32_t binding,uint32_t roles,uint64_t tick,uint64_t write_tick) noexcept {
 Text w;w.Add("BB_LAYOUT_AUDIT {\"schema\":1,\"event\":%u,\"path\":\"%s\",\"uid\":%llu,\"backing\":%llu,\"address\":%llu,\"bytes\":%u,\"registry\":%llu,\"allocation\":%llu,\"flags\":%u,\"binding\":%u,\"roles\":%u,\"tick\":%llu,\"write_tick\":%llu,\"requested\":",
  event,path,(unsigned long long)uid,(unsigned long long)backing,(unsigned long long)request.guest_address,request.guest_size,(unsigned long long)registry,(unsigned long long)allocation,flags,binding,roles,(unsigned long long)tick,(unsigned long long)write_tick);
 Describe(w,request);w.Add(",\"selected\":");Describe(w,selected);w.Add("}\n");
 if(w.failed)return false;return std::fwrite(w.bytes,1,w.at,stderr)==w.at;
}
}
