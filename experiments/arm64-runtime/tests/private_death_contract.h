// SPDX-License-Identifier: GPL-3.0-or-later
// Test instrumentation only. Not included in or enabled by the shipped runtime.
#pragma once
#include <array>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>
namespace BbPrivateDeath {
constexpr uint64_t SetHpOffset=0x1a0d470;
constexpr std::array<unsigned char,11> Signature={0x44,0x8b,0x47,0x08,0x85,0xf6,0x78,0x0a,0x8b,0x47,0x0c};
inline std::vector<unsigned char> Thunk(uint64_t target,const unsigned char*code,size_t size){
 if(!target||size<Signature.size()||memcmp(code,Signature.data(),Signature.size()))throw std::runtime_error("private SetHp signature rejected");
 // Zero the float argument; this x86 thunk is executed by FEX, never by ARM.
 std::vector<unsigned char> v={0x0f,0x57,0xc0,0x48,0xb8};
 for(unsigned i=0;i<8;++i)v.push_back((target>>(8*i))&255);
 v.insert(v.end(),{0xff,0xe0});return v;
}
inline bool Health(int hp,int max,int mhp,int mmax){return max>0&&max<=100000&&hp>=0&&hp<=max&&mmax==max&&mhp>=0&&mhp<=max;}
struct Gate {
 uint64_t player{};unsigned stable{},sequence{};
 bool Observe(uint64_t p,bool world,bool loading,bool valid,int hp){
  if(player!=p||!world||loading||!valid||hp<=0)stable=0;
  player=p;if(world&&!loading&&valid&&hp>0&&stable<5)++stable;
  return stable>=5;
 }
 bool Request(unsigned wanted,bool world,bool loading,bool valid,int hp){
  if(wanted!=sequence+1||wanted>2||stable<5||!world||loading||!valid||hp<=0)return false;
  sequence=wanted;stable=0;return true;
 }
};
}
