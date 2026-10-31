#include "fex_disk_format.h"
#include <cassert>
#include <cstdio>
using namespace BbFexDisk;
int main(){
 Digest key{};key.fill(7);Shard shard{8192,4096,key,key};
 Directory good{16384,key,{shard,{0,0,{},key}},{{0,0},{4096,0}},{{0,4096,key},{4096,4096,key}}};
 auto raw=Encode(good);auto decoded=Decode(raw,key,16384);assert(decoded.entries.size()==2&&decoded.shards[1].file_bytes==0);
 // A v1 index has 80-byte rows; it remains readable without catalog metadata.
 auto legacy=std::vector<std::byte>(raw.begin(),raw.begin()+72);legacy[7]=std::byte{'1'};
 for(size_t i=0;i<good.shards.size();++i)legacy.insert(legacy.end(),raw.begin()+72+128*i,raw.begin()+72+128*i+80);
 legacy.insert(legacy.end(),raw.begin()+72+128*good.shards.size(),raw.end()-32);auto old_sum=BbCpuCache::Hash(legacy);auto old_bytes=std::as_bytes(std::span(old_sum));legacy.insert(legacy.end(),old_bytes.begin(),old_bytes.end());
 assert(Decode(legacy,key,16384).shards[0].catalog_bytes==0);
 auto v2=std::vector<std::byte>(raw.begin(),raw.begin()+72);v2[7]=std::byte{'2'};
 for(size_t i=0;i<good.shards.size();++i)v2.insert(v2.end(),raw.begin()+72+128*i,raw.begin()+72+128*i+120);
 v2.insert(v2.end(),raw.begin()+72+128*good.shards.size(),raw.end()-32);auto v2sum=BbCpuCache::Hash(v2);auto v2bytes=std::as_bytes(std::span(v2sum));v2.insert(v2.end(),v2bytes.begin(),v2bytes.end());assert(Decode(v2,key,16384).shards[0].catalog_root_bytes==0);
 unsigned rejects=0;auto refuse=[&](auto bytes,auto expected,uint64_t image){try{Decode(bytes,expected,image);assert(false);}catch(const std::runtime_error&){++rejects;}};
 for(auto offset:{size_t(0),size_t(8),size_t(16),raw.size()-1}){auto damaged=raw;damaged[offset]^=std::byte{1};refuse(damaged,key,16384);}
 auto wrong=key;wrong[0]^=1;refuse(raw,wrong,16384);refuse(raw,key,32768);
 auto alter=[&](auto update){auto candidate=good;update(candidate);refuse(Encode(candidate),key,16384);};
 alter([](auto& d){d.entries[1].guest=0;});alter([](auto& d){d.entries[0].shard=1;});alter([](auto& d){d.entries[0].shard=3;});
 alter([](auto& d){d.shards[0].code_bytes=4097;});alter([](auto& d){d.shards[0].catalog_bytes=148;d.shards[0].catalog_root_bytes=149;});alter([](auto& d){d.pages[0].bytes=8192;});alter([](auto& d){d.pages[1].guest=0;});
 auto extra=raw;extra.push_back(std::byte{});refuse(extra,key,16384);raw.resize(raw.size()-1);refuse(raw,key,16384);
 assert(Filename(key).find("..") == std::string::npos);std::printf("FEX_DISK_FORMAT positive=1 refusals=%u\n",rejects);
 // Real-game scanning surpassed the old one-million entry writer limit.
 // Writer and reader must share ONE budget, without a full ARM payload in RAM.
 Directory large{uint64_t(8)<<20,key,{shard},{},{}};large.entries.reserve(1000001);
 for(uint64_t n=0;n<1000001;++n)large.entries.push_back({n*4,0});
 auto bytes=Encode(large);auto verified=Decode(bytes,key,large.image_bytes);assert(verified.entries.size()==1000001);
 std::puts("FEX_DISK_FORMAT million-entry writer/reader regression passed");
}
