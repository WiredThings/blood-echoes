// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_io.h"
#include "fex_compiled_units.h"
#include <memory>
#include <list>
#include <mutex>
namespace BbFexDisk {
struct VerifiedShard {
 FileStamp stamp;
 BbCacheValidation::Validated metadata;
 std::vector<Unit> units;
 std::vector<Digest> unit_hashes;
 size_t RetainedBytes()const{
  size_t n=sizeof(*this)+metadata.blocks.capacity()*sizeof(BbCacheValidation::Block)+metadata.relocs.capacity()*sizeof(BbCacheValidation::Reloc)+units.capacity()*sizeof(Unit)+unit_hashes.capacity()*sizeof(Digest);
  for(const auto& b:metadata.blocks)n+=b.pages.capacity()*sizeof(uint64_t);return n;
 }
 size_t Select(uint64_t guest)const{
  auto b=std::lower_bound(metadata.blocks.begin(),metadata.blocks.end(),guest,[](const auto& b,uint64_t n){return b.guest<n;});
  Require(b!=metadata.blocks.end()&&b->guest==guest,"directory target absent from shard");
  auto u=std::upper_bound(units.begin(),units.end(),size_t(b->host),[](size_t h,const Unit& u){return h<u.begin;});
  Require(u!=units.begin(),"prepared target unit absent");--u;Require(b->host<u->code_end,"prepared target outside unit");return size_t(u-units.begin());
 }
};
// Auxiliary proof cache only: no executable payload and no change to the
// original JIT budget. Eviction drops proofs, never compiled code or products.
class ShardProofs {
 struct Row{size_t shard;std::shared_ptr<const VerifiedShard> proof;};
 std::mutex mutex;std::list<Row> rows;size_t bytes{},evictions{},invalidations{},oversized{};
public:
 static constexpr size_t MaxBytes=2u<<20,MaxRows=256;
 std::shared_ptr<const VerifiedShard> Find(size_t shard,const FileStamp& stamp){
  std::scoped_lock guard{mutex};
  for(auto i=rows.begin();i!=rows.end();++i)if(i->shard==shard){
   if(!(i->proof->stamp==stamp)){bytes-=i->proof->RetainedBytes()+sizeof(Row)+2*sizeof(void*);rows.erase(i);++invalidations;return {};}
   auto proof=i->proof;rows.splice(rows.begin(),rows,i);return proof;
  }
  return {};
 }
 void Put(size_t shard,std::shared_ptr<const VerifiedShard> proof){
  std::scoped_lock guard{mutex};const auto size=proof->RetainedBytes()+sizeof(Row)+2*sizeof(void*);
  if(size>MaxBytes){++oversized;return;}
  for(auto i=rows.begin();i!=rows.end();++i)if(i->shard==shard){bytes-=i->proof->RetainedBytes()+sizeof(Row)+2*sizeof(void*);rows.erase(i);break;}
  while(!rows.empty()&&(bytes>MaxBytes-size||rows.size()>=MaxRows)){bytes-=rows.back().proof->RetainedBytes()+sizeof(Row)+2*sizeof(void*);rows.pop_back();++evictions;}
  rows.push_front({shard,std::move(proof)});bytes+=size;
 }
 size_t Bytes(){std::scoped_lock guard{mutex};return bytes;}
 std::array<size_t,3> Churn(){std::scoped_lock guard{mutex};return {evictions,invalidations,oversized};}
};
}
