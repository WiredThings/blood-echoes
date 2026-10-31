// SPDX-License-Identifier: MIT
// Producer-authored metadata, bound by the directory's exact catalog digest.
// Contains no native payload, absolute pointers, or alternate compiler recipe.
#pragma once
#include "fex_verified_shard.h"
namespace BbFexDisk {
inline std::string CatalogFilename(const Digest& hash){return "units-"+Filename(hash)+".bin";}
inline std::vector<std::byte> EncodeCatalog(const VerifiedShard& proof,const Shard& shard,const Digest& key,uint64_t image,std::span<const uint8_t,20> fex){
 Require(proof.units.size()==proof.unit_hashes.size(),"FEX catalog unit hashes");
 std::vector<std::byte> out;
 auto bytes=[&](const void* p,size_t n){auto b=static_cast<const std::byte*>(p);out.insert(out.end(),b,b+n);};
 auto word=[&](uint64_t n){for(unsigned i=0;i<8;++i)out.push_back(std::byte((n>>(8*i))&255));};
 bytes("BFXUNIT1",8);bytes(key.data(),32);bytes(shard.hash.data(),32);bytes(fex.data(),20);word(image);word(shard.file_bytes);word(proof.metadata.code_offset);word(proof.metadata.code_bytes);
 word(proof.metadata.blocks.size());word(proof.metadata.relocs.size());word(proof.units.size());
 for(const auto& b:proof.metadata.blocks){word(b.guest);word(b.host);word(b.pages.size());for(auto p:b.pages)word(p);}
 for(const auto& r:proof.metadata.relocs){word(r.offset);word(r.rip);word(r.kind);word(r.reg);}
 for(size_t i=0;i<proof.units.size();++i){const auto& u=proof.units[i];word(u.begin);word(u.end);word(u.code_end);bytes(proof.unit_hashes[i].data(),32);}
 Require(out.size()<=16u<<20,"FEX unit catalog byte budget");return out;
}
inline std::shared_ptr<VerifiedShard> DecodeCatalog(std::span<const std::byte> raw,const Shard& shard,const Digest& key,const BbCacheValidation::Policy& policy,std::optional<Unit> selected={}){
 Require(raw.size()==shard.catalog_bytes&&BbCpuCache::Hash(raw)==shard.catalog_hash,"FEX unit catalog checksum");
 Require(raw.size()>=148&&raw.size()<=16u<<20&&!memcmp(raw.data(),"BFXUNIT1",8),"FEX unit catalog format");
 size_t at=8;auto word=[&](){Require(at<=raw.size()&&raw.size()-at>=8,"FEX unit catalog truncated");uint64_t n=0;for(unsigned i=0;i<8;++i)n|=uint64_t(std::to_integer<unsigned>(raw[at++]))<<(8*i);return n;};
 auto digest=[&](){Require(at<=raw.size()&&raw.size()-at>=32,"FEX unit catalog digest truncated");Digest d{};memcpy(d.data(),raw.data()+at,32);at+=32;return d;};
 Require(digest()==key&&digest()==shard.hash,"FEX unit catalog incompatible identity");
 Require(!memcmp(raw.data()+at,policy.fex.data(),20),"FEX unit catalog compiler mismatch");at+=20;
 Require(word()==policy.image_bytes&&word()==shard.file_bytes,"FEX unit catalog image/file size");
 auto out=std::make_shared<VerifiedShard>();auto& m=out->metadata;m.code_offset=word();m.code_bytes=word();
 Require(m.code_bytes==shard.code_bytes&&m.code_offset>=4096&&m.code_offset%4096==0&&shard.file_bytes>=4096&&m.code_offset<=shard.file_bytes-4096&&m.code_bytes<=shard.file_bytes-4096-m.code_offset,"FEX unit catalog code bounds");
 const auto blocks=word(),relocs=word(),units=word();
 Require(blocks&&blocks<=65536&&blocks<=raw.size()/32&&relocs<=131072&&relocs<=raw.size()/32&&units&&units<=65536&&units<=raw.size()/56,"FEX unit catalog counts");
 m.blocks.reserve(blocks);m.relocs.reserve(relocs);out->units.reserve(units);out->unit_hashes.reserve(units);
 size_t edges=0;
 for(uint64_t i=0;i<blocks;++i){BbCacheValidation::Block b{};b.guest=word();b.host=word();const auto n=word();
  Require(BbCacheValidation::Inside(policy,b.guest)&&(!i||b.guest>m.blocks.back().guest)&&b.host%4==0&&b.host<m.code_bytes&&n&&n<=131072-edges&&n<=(raw.size()-at)/8,"FEX unit catalog block/pages");
  edges+=n;bool own=false;b.pages.reserve(n);
  for(uint64_t j=0;j<n;++j){const auto p=word();Require(p%4096==0&&BbCacheValidation::Inside(policy,p)&&(!j||p>b.pages.back()),"FEX unit catalog source page");b.pages.push_back(p);own|=p==(b.guest&~uint64_t(4095));}
  Require(own,"FEX unit catalog missing own page");m.blocks.push_back(std::move(b));
 }
 uint64_t previous=0;
 for(uint64_t i=0;i<relocs;++i){const auto offset=word(),rip=word(),kind=word(),reg=word();const size_t width=kind==3?16:8;
  Require((kind==0||kind==2||kind==3)&&offset%4==0&&offset>=previous&&offset<=m.code_bytes&&width<=m.code_bytes-offset&&(kind==0?reg==0:(rip<policy.image_bytes&&(kind!=3||(reg<=30&&reg!=18)))),"FEX unit catalog relocation");
  previous=offset+width;m.relocs.push_back({offset,rip,uint32_t(kind),uint32_t(reg)});
 }
 for(uint64_t i=0;i<units;++i){Unit u{size_t(word()),size_t(word()),size_t(word())};auto hash=digest();
  Require(u.begin==(i?((out->units.back().end+15)&~size_t{15}):(selected?selected->begin:0))&&u.end<=m.code_bytes&&u.begin<u.code_end&&sizeof(FEXCore::CPU::CPUBackend::JITCodeHeader)<=u.code_end-u.begin&&u.code_end<=u.end&&sizeof(FEXCore::CPU::CPUBackend::JITCodeTail)<=u.end-u.code_end&&u.end%4==0,"FEX unit catalog emitted range");
  out->units.push_back(u);out->unit_hashes.push_back(hash);
 }
 Require(at==raw.size(),"FEX unit catalog trailing data");
 if(selected)Require(out->units.size()==1&&out->units[0].begin==selected->begin&&out->units[0].end==selected->end&&out->units[0].code_end==selected->code_end,"FEX selected catalog range mismatch");
 for(const auto& b:m.blocks){const auto& u=out->units[out->UnitForHost(b.host)];Require(b.host>=u.begin+sizeof(FEXCore::CPU::CPUBackend::JITCodeHeader),"FEX unit catalog entry in header");}
 for(const auto& r:m.relocs){auto u=std::upper_bound(out->units.begin(),out->units.end(),r.offset,[](size_t h,const Unit& u){return h<u.begin;});Require(u!=out->units.begin(),"FEX unit catalog relocation lacks unit");--u;Require(r.offset<=u->end&&(r.kind==3?16:8)<=u->end-r.offset,"FEX unit catalog relocation crosses unit");}
 return out;
}
// Called only on bytes already accepted by the original full FXCC validator.
inline VerifiedShard MakeCatalog(std::span<const std::byte> file,const BbCacheValidation::Validated& checked){
 VerifiedShard proof;proof.metadata=checked;auto code=file.subspan(BbCpuCache::PrefixBytes+checked.code_offset,checked.code_bytes);proof.units=CompiledUnits(code,checked);proof.unit_hashes.reserve(proof.units.size());
 for(const auto& u:proof.units)proof.unit_hashes.push_back(BbCpuCache::Hash(code.subspan(u.begin,u.end-u.begin)));return proof;
}
}
