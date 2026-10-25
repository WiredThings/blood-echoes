// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>
#include <limits>
namespace BbArmHooks {
using U=uint64_t;using B=uint8_t;
enum class Kind:unsigned {Copy,RangeAlloc,HeapReturn,RangeFree,HeapFree,HeapPlain,HeapAligned,Count};
struct Site {U offset;Kind kind;std::vector<B> expected;unsigned covered;};
inline std::vector<Site> GameSites(){return {
 {0x216d5ea,Kind::Copy,{0xe8,0xc1,0xcb,0x4a,0},5},
 {0x2171fea,Kind::Copy,{0xe8,0xc1,0x81,0x4a,0},5},
 {0x26aa255,Kind::RangeAlloc,{0x48,0x83,0xc4,0x18,0x5b},5},
 {0x11e6629,Kind::HeapReturn,{0x48,0x89,0xc3,0x48,0x85,0xdb},6},
 {0x11e6736,Kind::HeapReturn,{0x48,0x89,0xc3,0x48,0x85,0xdb},6},
 {0x11e6846,Kind::HeapReturn,{0x48,0x89,0xc3,0x48,0x85,0xdb},6},
 {0x26aa986,Kind::RangeFree,{0x48,0x8b,0x4c,0x24,0x28,0x48,0xff,0x89,0xc8,0,0,0},5},
 {0x20858a0,Kind::HeapFree,{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56},6},
 {0x2085730,Kind::HeapPlain,{0x53,0x31,0xc0,0x48,0x39,0x77,0x18},7},
 {0x2085a60,Kind::HeapAligned,{0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54},6}};}
inline std::vector<Site> AuthoredSites(){auto s=GameSites();const U offsets[]={0x1800,0x1900,0x1a00,0x1b00,0x1c00,0x1d00,0x1e00,0x2000,0x2100,0x2200};for(size_t i=0;i<s.size();++i)s[i].offset=offsets[i];for(size_t i=0;i<2;++i){int32_t d=0x2600-s[i].offset-5;memcpy(s[i].expected.data()+1,&d,4);}return s;}
inline void Require(bool b,const char* msg){if(!b)throw std::runtime_error(msg);}
inline uint32_t Relative(U next,U target){
 if(target>=next){Require(target-next<=INT32_MAX,"hook target outside rel32");return uint32_t(target-next);}
 Require(next-target<=U(INT32_MAX)+1,"hook target outside rel32");return uint32_t(0-uint32_t(next-target));
}
struct Emitter {
 std::vector<B> code;
 void Bytes(std::initializer_list<B> b){code.insert(code.end(),b);}
 template<class T>void Value(T v){auto p=reinterpret_cast<B*>(&v);code.insert(code.end(),p,p+sizeof(v));}
 void JumpAbs(U at){Bytes({0xff,0x25,0,0,0,0});Value(at);}
 void CallAbs(U at){Bytes({0x48,0xb8});Value(at);Bytes({0xff,0xd0});}
 // Inline sites are not ABI call boundaries: preserve flags, red zone, every
 // caller GPR, x87/MXCSR, and both halves of all sixteen AVX registers.
 void SaveFull(){
  Bytes({0x48,0x8d,0xa4,0x24,0x80,0xff,0xff,0xff,0x9c});
  Bytes({0x50,0x51,0x52,0x56,0x57,0x41,0x50,0x41,0x51,0x41,0x52,0x41,0x53,0x41,0x54});
  Bytes({0x49,0x89,0xe4,0x48,0x83,0xe4,0xf0,0x48,0x81,0xec});Value(uint32_t(1056));
  Bytes({0x48,0x0f,0xae,0x04,0x24});
  for(unsigned i=0;i<16;++i){Bytes({0xc5,B(i<8?0xfe:0x7e),0x7f,B(0x84+8*(i&7)),0x24});Value(uint32_t(512+32*i));}
 }
 void RestoreFull(){
  Bytes({0x48,0x0f,0xae,0x0c,0x24});
  for(unsigned i=0;i<16;++i){Bytes({0xc5,B(i<8?0xfe:0x7e),0x6f,B(0x84+8*(i&7)),0x24});Value(uint32_t(512+32*i));}
  Bytes({0x4c,0x89,0xe4,0x41,0x5c,0x41,0x5b,0x41,0x5a,0x41,0x59,0x41,0x58,0x5f,0x5e,0x5a,0x59,0x58,0x9d,0x48,0x8d,0xa4,0x24,0x80,0,0,0});
 }
};
struct Patch {U offset;std::vector<B> bytes;};
struct Plan {std::vector<B> stubs;std::vector<Patch> patches;std::array<U,3> originals{};};
inline Plan Prepare(std::span<const B> image,U image_base,U stub_base,size_t stub_capacity,
                    std::span<const Site> sites,const std::array<U,unsigned(Kind::Count)>& veneers,bool conflict=false,bool fast_dispatch=false,bool leaf_dispatch=false,bool payload_gate=false){
 Require(!leaf_dispatch||fast_dispatch,"leaf dispatch requires guarded resource dispatch");
 // Protect before the original allocator, not after a guest leaf already ran.
 // All requested dispatch modes use full host wrapping in this experiment.
 if(payload_gate){fast_dispatch=false;leaf_dispatch=false;}
 Require(!conflict,"conflicting guest diagnostic hooks");
 Require(image_base&&image.size()<=UINT64_MAX-image_base,"image address overflow");
 Require(stub_base&&stub_capacity<=UINT64_MAX-stub_base,"stub address overflow");
 Require(stub_base>=image_base+image.size()||image_base>=stub_base+stub_capacity,"stub overlaps image");
 Require(sites.size()==10,"incomplete resource hook coverage");
 std::array<unsigned,unsigned(Kind::Count)> counts{};
 // Validate every original and every overlap before producing any patch. The
 // caller commits only after all targets, trampolines and capacity checks pass.
 for(size_t i=0;i<sites.size();++i){const auto& s=sites[i];
  Require(unsigned(s.kind)<counts.size(),"invalid hook kind");++counts[unsigned(s.kind)];
  Require(s.covered>=5&&s.covered<=s.expected.size()&&s.offset<=image.size()&&s.expected.size()<=image.size()-s.offset,"hook image bounds");
  Require(!memcmp(image.data()+s.offset,s.expected.data(),s.expected.size()),"hook original mismatch");
  Require(veneers[unsigned(s.kind)],"missing hook veneer");
  for(size_t j=0;j<i;++j)Require(s.offset+s.expected.size()<=sites[j].offset||sites[j].offset+sites[j].expected.size()<=s.offset,"overlapping hook sites");
 }
 Require(counts==std::array<unsigned,7>{2,1,3,1,1,1,1},"incomplete hook kinds");
 Plan plan;
 auto place=[&](const Emitter& e){size_t rounded=(e.code.size()+15)&~size_t(15);Require(plan.stubs.size()<=stub_capacity&&rounded<=stub_capacity-plan.stubs.size(),"hook stub capacity");U at=stub_base+plan.stubs.size();plan.stubs.insert(plan.stubs.end(),e.code.begin(),e.code.end());plan.stubs.resize(plan.stubs.size()+rounded-e.code.size(),0x90);return at;};
 auto jump_back=[&](Emitter& e,U target){U next=stub_base+plan.stubs.size()+e.code.size()+5;e.Bytes({0xe9});e.Value(Relative(next,target));};
 for(const auto& s:sites){Emitter e;const U address=image_base+s.offset;
  if(s.kind==Kind::HeapFree||s.kind==Kind::HeapPlain||s.kind==Kind::HeapAligned){
   e.code.insert(e.code.end(),s.expected.begin(),s.expected.begin()+s.covered);jump_back(e,address+s.covered);
   const U original=place(e);plan.originals[unsigned(s.kind)-unsigned(Kind::HeapFree)]=original;e=Emitter{};
   const auto bypass=[&](B condition){U next=stub_base+plan.stubs.size()+e.code.size()+6;e.Bytes({0x0f,condition});e.Value(Relative(next,original));};
   if(leaf_dispatch && s.kind==Kind::HeapPlain){
    // The guarded original body is a leaf. Run it directly in the guest, at
    // SysV alignment, retaining allocator+requested size for observation.
    // Even an unexpected GPU return is observed: no allocator classification
    // assumption, no skipped original function, and no recursive reallocation.
    e.Bytes({0x57,0x56,0x50});
    U next=stub_base+plan.stubs.size()+e.code.size()+5;e.Bytes({0xe8});e.Value(Relative(next,original));
    e.Bytes({0x48,0x8d,0x64,0x24,0x08,0x5e,0x5f,0x48,0x89,0xc2}); // result in rax+rdx
    std::array<size_t,2> done{};
    auto ret_branch=[&](B condition,unsigned i){e.Bytes({0x0f,condition});done[i]=e.code.size();e.Value(uint32_t(0));};
    e.Bytes({0x49,0xba});e.Value(U(0x7000000000));e.Bytes({0x4c,0x39,0xd0});ret_branch(0x82,0);
    e.Bytes({0x49,0xba});e.Value(U(0xf000000000));e.Bytes({0x4c,0x39,0xd0});ret_branch(0x83,1);
    e.JumpAbs(veneers[unsigned(s.kind)]);
    const size_t finish=e.code.size();e.Bytes({0xc3});
    for(auto patch:done){uint32_t relative=uint32_t(finish-patch-4);memcpy(e.code.data()+patch,&relative,4);}
   }else if(fast_dispatch && s.kind==Kind::HeapFree){
    // runtime_memory.c only places direct VMAs in [0x7000000000,
    // 0xf000000000). Outside it FexGpuScope is necessarily false. Execute
    // the original function in guest code, preserving all arguments.
    e.Bytes({0x48,0xb8});e.Value(U(0x7000000000));e.Bytes({0x48,0x39,0xc6});bypass(0x82);
    e.Bytes({0x48,0xb8});e.Value(U(0xf000000000));e.Bytes({0x48,0x39,0xc6});bypass(0x83);
   }else if(fast_dispatch && s.kind==Kind::HeapAligned){
    // Guarded game profile: <=16 either fails without allocating, or tail
    // jumps to the already hooked plain allocator with unchanged size. Keep
    // the real body and all its validation; avoid wrapping that nested call.
    e.Bytes({0x48,0x83,0xfa,0x10});bypass(0x86);
   }
   if(!(leaf_dispatch&&s.kind==Kind::HeapPlain)){
    if(s.kind==Kind::HeapPlain)e.Bytes({0x31,0xd2});
    e.JumpAbs(veneers[unsigned(s.kind)]);
   }
  }else if(s.kind==Kind::Copy)e.JumpAbs(veneers[unsigned(s.kind)]);
  else {
   e.SaveFull();
   if(s.kind==Kind::RangeAlloc)e.Bytes({0x48,0x89,0xc7}); // rdi=rax node
   else if(s.kind==Kind::RangeFree)e.Bytes({0x4c,0x89,0xf7}); // rdi=r14 node
   else e.Bytes({0x48,0x89,0xc7,0x48,0x8b,0x75,0xb0}); // address=rax, size=[rbp-0x50]
   e.CallAbs(veneers[unsigned(s.kind)]);e.RestoreFull();
   e.code.insert(e.code.end(),s.expected.begin(),s.expected.begin()+s.covered);jump_back(e,address+s.covered);
  }
  U at=place(e);std::vector<B> patch(s.covered,0x90);patch[0]=s.kind==Kind::Copy?0xe8:0xe9;auto rel=Relative(address+5,at);memcpy(patch.data()+1,&rel,4);plan.patches.push_back({s.offset,std::move(patch)});
 }
 return plan;
}
} // namespace BbArmHooks
