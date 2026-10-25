// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <span>
#include <stdexcept>
#include <vector>
#include "../vendor/bb_runtime/src/low_heap.h"
namespace BbGuestModules {
struct Segment {uint64_t address;uint32_t size;int32_t prot;};
// OrbisKernelModuleInfoEx, x86-64 guest layout. Values on disk are image-relative.
struct Info {
 uint64_t size;char name[256];int32_t id;uint32_t tls_index;
 uint64_t tls_init;uint32_t tls_init_size,tls_size,tls_offset,tls_align;
 uint64_t init,fini,reserved1,reserved2,eh_hdr,eh_frame;
 uint32_t eh_hdr_size,eh_frame_size;std::array<Segment,4> segments;
 uint32_t segment_count,padding;
};
static_assert(sizeof(Segment)==16&&sizeof(Info)==424);
static_assert(offsetof(Info,id)==264&&offsetof(Info,tls_init)==272&&offsetof(Info,init)==296);
static_assert(offsetof(Info,eh_hdr)==328&&offsetof(Info,segments)==352&&offsetof(Info,segment_count)==416);
inline void Require(bool ok,const char*why){if(!ok)throw std::runtime_error(why);}
inline constexpr uint64_t Round(uint64_t n){return(n+16383)&~uint64_t(16383);}
inline bool Contains(const Segment&s,uint64_t at){return at>=s.address&&at-s.address<s.size;}
inline bool Match(const Info& info,uint64_t address){
 if(info.size!=sizeof(Info)||!info.segment_count||info.segment_count>4)return false;
 // Validate the whole table before accepting any member; never dereference the
 // addresses it describes. The caller may use a copied ModuleInfo structure.
 for(size_t i=0;i<info.segment_count;++i){const auto&s=info.segments[i];if(!s.size||s.prot<0||s.prot>7||s.address>UINT64_MAX-s.size)return false;}
 for(size_t i=0;i<info.segment_count;++i)if((info.segments[i].prot&1)&&Contains(info.segments[i],address))return true;
 return false;
}
class Registry {
 std::vector<Info> rows;
public:
 bool Ready()const{return !rows.empty();}size_t Size()const{return rows.size();}
 const Info* Find(uint64_t address)const{
  for(const auto&r:rows)for(size_t i=0;i<r.segment_count;++i)if(Contains(r.segments[i],address))return&r;
  return nullptr;
 }
 template<class Boot>void Load(const std::filesystem::path&path,std::span<const uint8_t,32> digest,const Boot&boot,uint64_t image_base){
  Require(!Ready()&&image_base&&image_base<=UINT64_MAX-Round(boot.size),"module registry lifecycle/base");
  Require(!std::filesystem::is_symlink(path)&&std::filesystem::is_regular_file(path),"module metadata must be regular");
  auto length=std::filesystem::file_size(path);Require(length>=56&&length<=56+9*sizeof(Info),"module metadata length");
  std::ifstream f(path,std::ios::binary);auto read=[&](void*p,size_t n){Require(bool(f.read((char*)p,n)),"truncated module metadata");};
  char magic[8];std::array<uint8_t,32> actual;uint64_t size,count;read(magic,8);read(actual.data(),32);read(&size,8);read(&count,8);
  Require(!memcmp(magic,"BBMOD001",8)&&std::equal(actual.begin(),actual.end(),digest.begin())&&size==boot.size,"module metadata identity");
  Require(count==boot.modules.size()+1&&count<=9&&length==56+count*sizeof(Info),"module metadata count");
  std::vector<Info> pending(count);read(pending.data(),pending.size()*sizeof(Info));Require(f.peek()==EOF,"module metadata trailer");
  size_t segments_seen=0;
  for(size_t i=0;i<pending.size();++i){auto&r=pending[i];
   Require(r.size==sizeof(Info)&&r.id==int32_t(i)&&r.name[0]&&memchr(r.name,0,sizeof(r.name))&&!r.reserved1&&!r.reserved2&&!r.padding&&r.segment_count&&r.segment_count<=4,"module descriptor header");
   const uint64_t lo=i?boot.modules[i-1].base:0,hi=i?lo+boot.modules[i-1].size:boot.modules.front().base;
   Require(lo<hi&&hi<=boot.size,"module ownership bounds");
   auto own=[&](uint64_t a,uint64_t n){return n&&a>=lo&&a<=hi&&n<=hi-a;};
   const auto tls_index=i?boot.modules[i-1].tls_module:(boot.main_tls[2]?1:0);
   const auto tls_address=i?boot.modules[i-1].tls_address:boot.main_tls[0];
   const auto tls_mem=i?boot.modules[i-1].tls_memsz:boot.main_tls[2];
   const auto tls_files=i?boot.modules[i-1].tls_filesz:boot.main_tls[1];
   Require(r.tls_index==tls_index&&r.tls_size==tls_mem&&r.tls_init_size==tls_files,"module TLS identity");
   if(tls_index){Require(r.tls_init==tls_address&&r.tls_align&&r.tls_align<=4096&&!(r.tls_align&(r.tls_align-1))&&own(r.tls_init,r.tls_size)&&boot.covered(r.tls_init,r.tls_size),"module TLS bounds");Require(i?r.tls_align<=bb_low_allocation_alignment(tls_mem):r.tls_align==(boot.main_tls[3]?boot.main_tls[3]:16),"module TLS allocator alignment");Require(r.tls_offset==(i?0:(tls_mem+r.tls_align-1)&~uint64_t(r.tls_align-1)),"module TLS offset");r.tls_init+=image_base;}
   else Require(!r.tls_init&&!r.tls_align&&!r.tls_offset,"module without TLS");
   for(auto*address:{&r.init,&r.fini})if(*address){Require(own(*address,1)&&boot.covered(*address,1,true),"module init/fini bounds");*address+=image_base;}
   if(i)Require(r.init==image_base+boot.modules[i-1].init,"module initializer identity");
   Require(bool(r.eh_hdr)==bool(r.eh_hdr_size)&&bool(r.eh_frame)==bool(r.eh_frame_size),"module EH pair");
   if(r.eh_hdr){Require(r.eh_hdr_size>=12&&own(r.eh_hdr,r.eh_hdr_size)&&own(r.eh_frame,r.eh_frame_size)&&r.eh_frame<r.eh_hdr&&r.eh_hdr-r.eh_frame==r.eh_frame_size&&boot.covered(r.eh_hdr,r.eh_hdr_size)&&boot.covered(r.eh_frame,r.eh_frame_size),"module EH bounds");r.eh_hdr+=image_base;r.eh_frame+=image_base;
    std::array<uint8_t,12> header;std::memcpy(header.data(),reinterpret_cast<const void*>(r.eh_hdr),header.size());int32_t delta;uint32_t count;std::memcpy(&delta,header.data()+4,4);std::memcpy(&count,header.data()+8,4);
    Require(header[0]==1&&header[1]==0x1b&&header[2]==3&&header[3]==0x3b&&r.eh_hdr_size>=12&&uint64_t(count)*8<=r.eh_hdr_size-12&&int64_t(r.eh_hdr-image_base)+4+delta==int64_t(r.eh_frame-image_base),"module EH bytes differ from loaded image");
   }
   for(size_t j=0;j<4;++j){auto&s=r.segments[j];if(j>=r.segment_count){Require(!s.address&&!s.size&&!s.prot,"unused module segment");continue;}
    Require(s.size&&s.address>=lo&&s.address<hi&&s.address<=boot.size&&s.size<=Round(boot.size)-s.address&&s.prot>=0&&s.prot<=7,"module segment bounds");
    Require(segments_seen<boot.segments.size(),"too many module segments");const auto&expected=boot.segments[segments_seen++];Require(s.address==expected.address&&s.size==Round(expected.size)&&s.prot==expected.flags,"module segment identity");s.address+=image_base;
   }
  }
  Require(segments_seen==boot.segments.size(),"missing module segments");rows=std::move(pending);
 }
};
}
