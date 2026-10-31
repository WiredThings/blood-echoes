// SPDX-License-Identifier: MIT
// Bounded, content-identified metadata. ARM payloads remain individual FXCC
// files on disk; the directory never embeds process addresses or game bytes.
#pragma once
#include "bb_cpu_cache_identity.h"
#include <vector>
#include <string>
#include <stdexcept>
#include <limits>
namespace BbFexDisk {
using Digest=BbCpuCache::Digest;
inline void Require(bool b,const char* text){if(!b)throw std::runtime_error(text);}
struct Shard {uint64_t file_bytes,code_bytes;Digest hash,seeds;};
struct Entry {uint64_t guest,shard;};
struct Page {uint64_t guest,bytes;Digest hash;};
struct Directory {uint64_t image_bytes{};Digest key{};std::vector<Shard> shards;std::vector<Entry> entries;std::vector<Page> pages;};
inline constexpr size_t MaxEntries=4000000,MaxShards=65536,MaxPages=131072,MaxDirectoryBytes=96u<<20;
inline std::vector<std::byte> Encode(const Directory& d){
 Require(d.image_bytes&&d.image_bytes<=512ull<<20&&d.shards.size()<=MaxShards&&d.entries.size()<=MaxEntries&&d.pages.size()<=MaxPages,"FEX directory budget");
 const auto total=104+80*d.shards.size()+16*d.entries.size()+48*d.pages.size();Require(total<=MaxDirectoryBytes,"FEX directory byte budget");
 std::vector<std::byte> out;out.reserve(total);auto bytes=[&](const void* p,size_t n){auto b=static_cast<const std::byte*>(p);out.insert(out.end(),b,b+n);};
 auto word=[&](uint64_t n){for(unsigned i=0;i<8;++i)out.push_back(std::byte((n>>(i*8))&255));};
 bytes("BFXDIR01",8);word(d.image_bytes);bytes(d.key.data(),32);word(d.shards.size());word(d.entries.size());word(d.pages.size());
 for(auto& r:d.shards){word(r.file_bytes);word(r.code_bytes);bytes(r.hash.data(),32);bytes(r.seeds.data(),32);}
 for(auto& r:d.entries){word(r.guest);word(r.shard);}
 for(auto& r:d.pages){word(r.guest);word(r.bytes);bytes(r.hash.data(),32);}
 const auto sum=BbCpuCache::Hash(out);bytes(sum.data(),32);return out;
}
inline Directory Decode(std::span<const std::byte> raw,const Digest& key,uint64_t image){
 Require(raw.size()>=104&&raw.size()<=MaxDirectoryBytes&&!memcmp(raw.data(),"BFXDIR01",8),"FEX directory format/size");
 auto sum=BbCpuCache::Hash(raw.first(raw.size()-32));Require(!memcmp(sum.data(),raw.data()+raw.size()-32,32),"FEX directory checksum");
 size_t at=8;auto word=[&](){Require(at<=raw.size()&&raw.size()-at>=8,"FEX directory truncated");uint64_t n=0;for(unsigned i=0;i<8;++i)n|=uint64_t(std::to_integer<unsigned>(raw[at++]))<<(8*i);return n;};
 auto digest=[&](){Require(at<=raw.size()&&raw.size()-at>=32,"FEX digest truncated");Digest d{};memcpy(d.data(),raw.data()+at,32);at+=32;return d;};
 Directory d;d.image_bytes=word();d.key=digest();Require(d.image_bytes==image&&image&&image<=512ull<<20&&d.key==key,"FEX directory incompatible input");
 const auto shards=word(),entries=word(),pages=word();Require(shards<=MaxShards&&entries<=MaxEntries&&pages<=MaxPages&&104+80*shards+16*entries+48*pages==raw.size(),"FEX directory shape/budget");
 d.shards.reserve(shards);d.entries.reserve(entries);d.pages.reserve(pages);
 for(uint64_t i=0;i<shards;++i){Shard s{word(),word(),digest(),digest()};Require((!s.file_bytes&&!s.code_bytes)||(s.file_bytes>BbCpuCache::PrefixBytes&&s.file_bytes<=BbCpuCache::PrefixBytes+(16u<<20)&&s.code_bytes&&s.code_bytes<=16u<<20&&s.code_bytes%4096==0),"FEX shard bounds");d.shards.push_back(s);}
 for(uint64_t i=0;i<entries;++i){Entry e{word(),word()};Require(e.guest<image&&e.shard<shards&&d.shards[e.shard].file_bytes&&(!i||e.guest>d.entries.back().guest),"FEX entry order/range");d.entries.push_back(e);}
 for(uint64_t i=0;i<pages;++i){Page p{word(),word(),digest()};Require(p.guest%4096==0&&p.bytes&&p.bytes<=4096&&p.guest<image&&p.bytes<=image-p.guest&&(!i||p.guest>d.pages.back().guest),"FEX page order/range");d.pages.push_back(p);}
 Require(at+32==raw.size(),"FEX directory trailing data");return d;
}
inline std::string Filename(size_t shard){return "shard-"+std::to_string(shard)+".becc";}
inline std::string Filename(const Digest& seeds){std::string text="shard-";for(auto b:seeds){text+="0123456789abcdef"[b>>4];text+="0123456789abcdef"[b&15];}return text+".becc";}
}
