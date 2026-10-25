// SPDX-License-Identifier: MIT
// Authored metadata/counter controls only; no GPU/game/state allocation.
#include "bb_layout_audit.h"
#include <array>
#include <thread>
#include <cassert>
#include <limits>
#include <string_view>
struct Extent{unsigned width=64,height=64,depth=1;auto operator<=>(const Extent&)const=default;};
struct Resources{unsigned levels=1,layers=1;auto operator<=>(const Resources&)const=default;};
struct Info{
 uint64_t guest_address=0x10000;unsigned guest_size=16384;Extent size;Resources resources;
 unsigned pitch=64,num_bits=32,num_samples=1,tile_mode=8,array_mode=1,bank_swizzle=0,alt_tile=0;
 struct {unsigned is_volume=0,is_block=0,is_tiled=0,is_pow2=0;}props;
 unsigned micro_tiled_mips=0,type=2,pixel_format=37;
 struct Mip{unsigned size=16384,pitch=64,height=64,offset=0;};std::array<Mip,16>mips_layout{};
};
int main(int argc,char**argv){
 assert(argc==2);std::string_view mode=argv[1];
 if(mode=="disabled"){assert(!BbLayoutAudit::Enabled());assert(BbLayoutAudit::Counter().emitted==0);return 0;}
 if(mode=="budget"){
  BbLayoutAudit::Budget budget;std::array<std::atomic<unsigned>,65>seen{};std::array<std::thread,8>threads;
  for(auto&t:threads)t=std::thread([&]{for(unsigned n=0;n<10000;++n)if(auto ticket=budget.Claim()){assert(ticket<=64);++seen[ticket];}});
  for(auto&t:threads)t.join();assert(budget.emitted==64);for(unsigned i=1;i<=64;++i)assert(seen[i]==1);assert(budget.Claim()==0);return 0;
 }
 Info a{},b=a;assert(BbLayoutAudit::SameByteLayout(a,b));b.tile_mode=13;assert(BbLayoutAudit::IsMismatch(a,b));b.guest_address+=16384;assert(!BbLayoutAudit::IsMismatch(a,b));b=a;b.pixel_format=43;b.type=1;assert(BbLayoutAudit::SameByteLayout(a,b));b.mips_layout[15].offset=99;assert(BbLayoutAudit::SameByteLayout(a,b));b=a;b.resources.levels=17;assert(!BbLayoutAudit::SameByteLayout(b,b));
 assert(BbLayoutAudit::Enabled());b=a;b.tile_mode=13;b.props.is_tiled=1;
 if(mode=="maximum"){
  a.resources.levels=b.resources.levels=16;a.resources.layers=b.resources.layers=0xffffffffu;
  a.pitch=b.pitch=a.num_bits=b.num_bits=a.num_samples=b.num_samples=0xffffffffu;
  for(auto&i:a.mips_layout)i={0xffffffffu,0xffffffffu,0xffffffffu,0xffffffffu};b.mips_layout=a.mips_layout;
 }
 for(unsigned i=0;i<1000;++i)if(auto event=BbLayoutAudit::Counter().Claim())assert(BbLayoutAudit::Print(event,a,b,"test",12,13,14,15,16,17,18,19,20));
 assert(BbLayoutAudit::Counter().emitted==64);return 0;
}
