// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// CPU-neutral effective-code export. The original boot and ordered patch
// algorithms are reused; no translator, FEX bridge or renderer is involved.
#include "boot_image.h"
#include "bb_source_hash.h"
#include <algorithm>
#include <set>
#include <span>

namespace BbCpuImage {
using BbBoot::u64;
inline void Require(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
struct ElfHeader {
 unsigned char ident[16];uint16_t type,machine;uint32_t version;
 u64 entry,phoff,shoff;uint32_t flags;uint16_t ehsize,phentsize,phnum,shentsize,shnum,shstrndx;
};
struct Program {uint32_t type,flags;u64 offset,address,paddr,filesz,memsz,align;};
static_assert(sizeof(ElfHeader)==64&&sizeof(Program)==56);
struct Layout {std::vector<Program> loads;Program eh{};};
inline bool Main(const BbBoot::Boot& boot,u64 at,u64 bytes){
 if(!boot.covered(at,bytes))return false;
 for(const auto& m:boot.modules)if(at<m.base+m.size&&m.base<at+bytes)return false;
 return true;
}
inline Layout ReadLayout(std::span<const unsigned char> elf,const BbBoot::Boot& boot){
 Require(elf.size()>=sizeof(ElfHeader),"truncated source ELF");ElfHeader h{};memcpy(&h,elf.data(),sizeof(h));
 constexpr unsigned char magic[]={0x7f,'E','L','F',2,1,1};
 Require(!memcmp(h.ident,magic,sizeof(magic))&&h.machine==62&&h.version==1&&h.ehsize==64&&h.phentsize==56&&h.phnum&&h.phnum<=64&&h.entry==boot.entry,"source ELF does not match linked-image entry/layout");
 Require(h.phoff<=elf.size()&&size_t(h.phnum)*sizeof(Program)<=elf.size()-h.phoff,"source program headers outside ELF");
 Layout result;unsigned eh_count=0;
 for(unsigned i=0;i<h.phnum;++i){Program p{};memcpy(&p,elf.data()+h.phoff+i*sizeof(p),sizeof(p));
  Require(p.offset<=elf.size()&&p.filesz<=elf.size()-p.offset,"source program bytes outside ELF");
  if(p.type==1){
   Require(p.memsz&&p.filesz<=p.memsz&&p.flags<=7&&Main(boot,p.address,p.memsz),"source load outside main linked image");
   bool exact=false;for(auto s:boot.segments)exact|=s.address==p.address&&s.size==p.memsz&&s.flags==p.flags;
   Require(exact,"source load differs from linked-image segment");
   for(auto old:result.loads)Require(p.address+p.memsz<=old.address||old.address+old.memsz<=p.address,"overlapping source loads");
   Require(result.loads.size()<8,"too many main loads for upstream scanner");result.loads.push_back(p);
  }else if(p.type==0x6474e550){result.eh=p;++eh_count;}
 }
 size_t main_count=0;for(auto s:boot.segments)main_count+=Main(boot,s.address,s.size);
 Require(result.loads.size()==main_count&&main_count&&eh_count==1&&result.eh.filesz&&Main(boot,result.eh.address,result.eh.filesz),"incomplete main segment/unwind catalog");
 bool eh_backed=false;for(auto p:result.loads)eh_backed|=result.eh.address>=p.address&&result.eh.address-p.address<=p.filesz&&result.eh.filesz<=p.filesz-(result.eh.address-p.address);
 Require(eh_backed,"unwind header is not backed by original main ELF bytes");
 std::sort(result.loads.begin(),result.loads.end(),[](auto a,auto b){return a.address<b.address;});return result;
}
struct Relocation {u64 target,kind,value,addend;};
inline std::vector<Relocation> MainRelocations(const BbBoot::Boot& boot){
 std::vector<Relocation> result;
 for(auto r:boot.relocs)if(Main(boot,r.target,8))result.push_back({r.target,r.kind,r.value,r.addend});
 std::sort(result.begin(),result.end(),[](auto a,auto b){return a.target<b.target;});
 for(size_t i=1;i<result.size();++i)Require(result[i].target>=result[i-1].target+8,"overlapping main relocation slots");
 return result;
}
// Rebase image pointers to virtual offsets; imports stay dynamic. The source
// worker applies kind-0 relocations before original guarded startup patches,
// exactly as the game host does. No invented import address is ever compiled.
inline void RelocateMain(void* image,const BbBoot::Boot& boot){
 const auto base=reinterpret_cast<uintptr_t>(image);
 for(auto r:MainRelocations(boot))if(!r.kind){Require(r.value<=UINTPTR_MAX-base,"image relocation overflow");u64 value=base+r.value;memcpy(static_cast<unsigned char*>(image)+r.target,&value,8);}
}
struct Summary {
 std::string identity;std::vector<std::pair<u64,u64>> excluded;
 std::set<u64> pointers;u64 bytes{};
};
template<class Sink>Summary Normalize(const BbBoot::Boot& boot,const Layout& layout,const void* image,Sink sink){
 const auto base=reinterpret_cast<uintptr_t>(image);auto relocs=MainRelocations(boot);Summary result;
 CC_SHA256_CTX digest;CC_SHA256_Init(&digest);
 auto mix=[&](const void* p,size_t n){CC_SHA256_Update(&digest,p,static_cast<CC_LONG>(n));};
 constexpr char domain[]="BloodEchoes.bbcpu.effective-image.v1";mix(domain,sizeof(domain));
 mix(&boot.entry,sizeof(boot.entry));mix(&layout.eh.address,sizeof(u64));mix(&layout.eh.filesz,sizeof(u64));
 const u64 relocation_count=relocs.size(),load_count=layout.loads.size();mix(&relocation_count,sizeof(u64));mix(&load_count,sizeof(u64));
 std::vector<std::pair<u64,u64>> normalized;
 for(auto r:relocs){
  u64 value=0;
  if(!r.kind){memcpy(&value,static_cast<const unsigned char*>(image)+r.target,8);
   Require(value>=base&&value-base<boot.size&&boot.covered(value-base,1),"effective image pointer outside mapped image");value-=base;
   if(Main(boot,value,1)&&boot.covered(value,1,true))result.pointers.insert(value);
  }
  normalized.emplace_back(r.target,value);
  mix(&r.target,sizeof(u64));mix(&r.kind,sizeof(u64));mix(&r.addend,sizeof(u64));
  if(boot.covered(r.target,8,true))result.excluded.emplace_back(r.target,8);
 }
 std::array<unsigned char,65536> buffer{};
 for(auto p:layout.loads){
  Require(Main(boot,p.address,p.memsz),"effective image layout no longer matches boot");
  mix(&p.address,sizeof(u64));mix(&p.memsz,sizeof(u64));mix(&p.flags,sizeof(p.flags));
  // Full segment extents include zero-fill and stable patches in BSS. This is
  // temporary streaming I/O, not another retained game-image allocation.
  for(u64 offset=0;offset<p.memsz;){const auto n=size_t(std::min<u64>(buffer.size(),p.memsz-offset));const auto at=p.address+offset;
   memcpy(buffer.data(),static_cast<const unsigned char*>(image)+at,n);
   auto first=std::lower_bound(normalized.begin(),normalized.end(),at>=7?at-7:0,[](auto r,u64 a){return r.first<a;});
   for(auto it=first;it!=normalized.end()&&it->first<at+n;++it){
    const auto start=std::max(at,it->first),end=std::min(at+n,it->first+8);
    if(start<end)memcpy(buffer.data()+start-at,reinterpret_cast<const unsigned char*>(&it->second)+start-it->first,size_t(end-start));
   }
   mix(buffer.data(),n);sink(p,offset,std::span<const unsigned char>(buffer.data(),n));offset+=n;result.bytes+=n;
  }
 }
 BbCpuCache::Digest value{};CC_SHA256_Final(value.data(),&digest);result.identity=BbStaticCode::Hex(value);return result;
}
}
