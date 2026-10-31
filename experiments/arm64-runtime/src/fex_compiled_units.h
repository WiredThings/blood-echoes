// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_format.h"
#include "bb_cpu_cache_validation.h"
#include "Interface/Core/CPUBackend.h"
namespace BbFexDisk {
struct Unit {size_t begin,end,code_end;};
// FXCC contains the original FEX units unchanged. Use their ACTUAL emitted
// header/tail Size, never guest-function lengths or adjacent entrypoint guesses.
inline std::vector<Unit> CompiledUnits(std::span<const std::byte> code,const BbCacheValidation::Validated& metadata){
 using Header=FEXCore::CPU::CPUBackend::JITCodeHeader;using Tail=FEXCore::CPU::CPUBackend::JITCodeTail;
 Require(!metadata.blocks.empty(),"FEX unit block inventory absent");
 size_t last=0;for(const auto& block:metadata.blocks)last=std::max(last,size_t(block.host));
 std::vector<Unit> units;size_t at=0;
 while(at<=last){
  Require(units.size()<65536&&at<=code.size()&&code.size()-at>=sizeof(Header),"FEX unit header bounds");
  Header header{};memcpy(&header,code.data()+at,sizeof(header));const size_t tail=header.OffsetToBlockTail;
  Require(tail>=sizeof(Header)&&tail%alignof(Tail)==0&&tail<=code.size()-at&&sizeof(Tail)<=code.size()-at-tail,"FEX unit tail bounds");
  Tail info{};memcpy(&info,code.data()+at+tail,sizeof(info));
  Require(info.Size>=tail+sizeof(Tail)&&info.Size<=code.size()-at,"FEX emitted unit size");
  Require(info.Size%4==0,"FEX emitted unit alignment");
  Require(info.SpinLockFutex==0,"FEX emitted unit futex");
  // A genuine one-instruction guest block (e.g. a jump) legitimately has this
  // bit set. Check its representation without reading an untrusted C++ bool.
  Require(std::to_integer<unsigned char>(code[at+tail+offsetof(Tail,SingleInst)])<=1,"FEX emitted unit flag encoding");
  Require(info.OffsetToRIPEntries==sizeof(Tail),"FEX emitted unit RIP metadata offset");
  Require(info.NumberOfRIPEntries<=info.Size-tail-sizeof(Tail),"FEX unit RIP metadata budget");
  units.push_back({at,at+info.Size,at+tail});at=(at+info.Size+15)&~size_t{15};
 }
 for(const auto& block:metadata.blocks){
  auto unit=std::upper_bound(units.begin(),units.end(),size_t(block.host),[](size_t n,const Unit& u){return n<u.begin;});
  Require(unit!=units.begin(),"FEX entry lacks actual compiled unit");--unit;
  Require(block.host>=unit->begin+sizeof(Header)&&block.host<unit->code_end&&block.host%4==0,"FEX entry not in emitted instructions");
 }
 for(auto reloc:metadata.relocs){const size_t width=reloc.kind==3?16:8;
  auto unit=std::upper_bound(units.begin(),units.end(),size_t(reloc.offset),[](size_t n,const Unit& u){return n<u.begin;});
  Require(unit!=units.begin(),"FEX relocation lacks unit");--unit;Require(reloc.offset>=unit->begin&&reloc.offset<=unit->end&&width<=unit->end-reloc.offset,"FEX relocation crosses emitted unit");
 }
 return units;
}
}
