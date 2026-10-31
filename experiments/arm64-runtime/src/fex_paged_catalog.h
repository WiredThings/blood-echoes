// SPDX-License-Identifier: MIT
// Root digest -> exact metadata ranges -> original native-unit digest.
#pragma once
#include "fex_unit_catalog.h"
namespace BbFexDisk {
struct PagedCatalog {std::vector<std::byte> bytes;size_t root_bytes{};Digest hash;};
inline PagedCatalog EncodePagedCatalog(const VerifiedShard& proof,const Shard& shard,const Digest& key,uint64_t image,std::span<const uint8_t,20> fex){
 Require(!proof.units.empty()&&proof.units.size()==proof.unit_hashes.size(),"FEX paged units");
 std::vector<VerifiedShard> pieces(proof.units.size());
 for(size_t i=0;i<pieces.size();++i){auto& p=pieces[i];p.metadata.code_offset=proof.metadata.code_offset;p.metadata.code_bytes=proof.metadata.code_bytes;p.units.push_back(proof.units[i]);p.unit_hashes.push_back(proof.unit_hashes[i]);}
 for(const auto& b:proof.metadata.blocks)pieces[proof.UnitForHost(b.host)].metadata.blocks.push_back(b);
 for(const auto& r:proof.metadata.relocs)pieces[proof.UnitForHost(r.offset,false)].metadata.relocs.push_back(r);
 std::vector<std::vector<std::byte>> blobs;blobs.reserve(pieces.size());
 const size_t root_bytes=148+16*proof.metadata.blocks.size()+72*pieces.size();size_t total=root_bytes;
 for(const auto& p:pieces){Require(!p.metadata.blocks.empty(),"FEX paged unit lacks entries");auto b=EncodeCatalog(p,shard,key,image,fex);Require(total<=16u<<20&&b.size()<=(16u<<20)-total,"FEX paged catalog budget");total+=b.size();blobs.push_back(std::move(b));}
 PagedCatalog out;out.root_bytes=root_bytes;out.bytes.reserve(total);
 auto bytes=[&](const void* p,size_t n){auto b=static_cast<const std::byte*>(p);out.bytes.insert(out.bytes.end(),b,b+n);};
 auto word=[&](uint64_t n){for(unsigned i=0;i<8;++i)out.bytes.push_back(std::byte((n>>(8*i))&255));};
 bytes("BFXPAGE1",8);bytes(key.data(),32);bytes(shard.hash.data(),32);bytes(fex.data(),20);
 word(image);word(shard.file_bytes);word(proof.metadata.code_offset);word(proof.metadata.code_bytes);word(proof.metadata.blocks.size());word(pieces.size());word(total);
 for(const auto& b:proof.metadata.blocks){word(b.guest);word(b.host);}
 size_t offset=root_bytes;
 for(size_t i=0;i<pieces.size();++i){const auto& u=proof.units[i];word(u.begin);word(u.end);word(u.code_end);word(offset);word(blobs[i].size());auto hash=BbCpuCache::Hash(blobs[i]);bytes(hash.data(),32);offset+=blobs[i].size();}
 Require(out.bytes.size()==root_bytes&&offset==total,"FEX paged root shape");out.hash=BbCpuCache::Hash(out.bytes);
 for(const auto& b:blobs)bytes(b.data(),b.size());return out;
}
inline std::shared_ptr<VerifiedShard> DecodePagedRoot(std::span<const std::byte> raw,const Shard& shard,const Digest& key,const BbCacheValidation::Policy& policy){
 Require(raw.size()==shard.catalog_root_bytes&&raw.size()>=148&&raw.size()<=16u<<20&&BbCpuCache::Hash(raw)==shard.catalog_hash,"FEX paged root checksum");
 Require(!memcmp(raw.data(),"BFXPAGE1",8),"FEX paged root version");size_t at=8;
 auto word=[&](){Require(at<=raw.size()&&raw.size()-at>=8,"FEX paged root truncated");uint64_t n=0;for(unsigned i=0;i<8;++i)n|=uint64_t(std::to_integer<unsigned>(raw[at++]))<<(8*i);return n;};
 auto digest=[&](){Require(at<=raw.size()&&raw.size()-at>=32,"FEX paged root digest truncated");Digest d{};memcpy(d.data(),raw.data()+at,32);at+=32;return d;};
 Require(digest()==key&&digest()==shard.hash,"FEX paged root identity");Require(!memcmp(raw.data()+at,policy.fex.data(),20),"FEX paged root compiler");at+=20;
 Require(word()==policy.image_bytes&&word()==shard.file_bytes,"FEX paged root image/file");
 auto out=std::make_shared<VerifiedShard>();auto& m=out->metadata;m.code_offset=word();m.code_bytes=word();
 Require(m.code_bytes==shard.code_bytes&&m.code_offset>=4096&&m.code_offset%4096==0&&shard.file_bytes>=4096&&m.code_offset<=shard.file_bytes-4096&&m.code_bytes<=shard.file_bytes-4096-m.code_offset,"FEX paged code bounds");
 const auto routes=word(),units=word(),total=word();Require(routes&&routes<=65536&&units&&units<=65536&&total==shard.catalog_bytes&&total<=16u<<20&&148+16*routes+72*units==raw.size(),"FEX paged root counts");
 out->catalog_routes.reserve(routes);out->units.reserve(units);out->catalog_ranges.reserve(units);
 for(uint64_t i=0;i<routes;++i){CatalogRoute r{word(),word()};Require(BbCacheValidation::Inside(policy,r.guest)&&(!i||r.guest>out->catalog_routes.back().guest)&&r.host%4==0&&r.host<m.code_bytes,"FEX paged route range/order");out->catalog_routes.push_back(r);}
 uint64_t next=raw.size();
 for(uint64_t i=0;i<units;++i){Unit u{size_t(word()),size_t(word()),size_t(word())};CatalogRange r{word(),word(),digest()};
  Require(u.begin==(i?((out->units.back().end+15)&~size_t{15}):0)&&u.end<=m.code_bytes&&u.begin<u.code_end&&sizeof(FEXCore::CPU::CPUBackend::JITCodeHeader)<=u.code_end-u.begin&&u.code_end<=u.end&&sizeof(FEXCore::CPU::CPUBackend::JITCodeTail)<=u.end-u.code_end&&u.end%4==0,"FEX paged native unit range");
  Require(r.offset==next&&r.offset<=total&&r.bytes>=148&&r.bytes<=total-r.offset,"FEX paged metadata extent");next+=r.bytes;out->units.push_back(u);out->catalog_ranges.push_back(r);
 }
 Require(at==raw.size()&&next==total,"FEX paged trailing data");
 for(const auto& r:out->catalog_routes){const auto& u=out->units[out->UnitForHost(r.host)];Require(r.host>=u.begin+sizeof(FEXCore::CPU::CPUBackend::JITCodeHeader),"FEX paged route in header");}
 return out;
}
inline std::shared_ptr<VerifiedShard> DecodePagedUnit(std::span<const std::byte> raw,const VerifiedShard& root,size_t unit,const Shard& shard,const Digest& key,const BbCacheValidation::Policy& policy){
 Require(unit<root.units.size()&&unit<root.catalog_ranges.size(),"FEX paged unit index");const auto& range=root.catalog_ranges[unit];auto selected=shard;selected.catalog_bytes=range.bytes;selected.catalog_hash=range.hash;
 auto out=DecodeCatalog(raw,selected,key,policy,root.units[unit]);Require(out->metadata.code_offset==root.metadata.code_offset&&out->metadata.code_bytes==root.metadata.code_bytes,"FEX paged unit code layout");
 // Every published entry has the same location as the authenticated route.
 for(const auto& b:out->metadata.blocks){auto r=std::lower_bound(root.catalog_routes.begin(),root.catalog_routes.end(),b.guest,[](const auto& r,uint64_t guest){return r.guest<guest;});Require(r!=root.catalog_routes.end()&&r->guest==b.guest&&r->host==b.host,"FEX paged unit/route mismatch");}
 return out;
}
}
