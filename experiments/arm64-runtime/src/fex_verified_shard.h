// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_io.h"
#include "fex_compiled_units.h"
#include <memory>
#include <list>
#include <mutex>
namespace BbFexDisk {
struct CatalogRoute {uint64_t guest,host;};
struct CatalogRange {uint64_t offset,bytes;Digest hash;};
struct VerifiedShard {
 FileStamp stamp;
 BbCacheValidation::Validated metadata;
 std::vector<Unit> units;
 std::vector<Digest> unit_hashes;
 std::vector<CatalogRange> catalog_ranges;
 std::vector<CatalogRoute> catalog_routes;
 size_t RetainedBytes()const{
  size_t n=sizeof(*this)+metadata.blocks.capacity()*sizeof(BbCacheValidation::Block)+metadata.relocs.capacity()*sizeof(BbCacheValidation::Reloc)+units.capacity()*sizeof(Unit)+unit_hashes.capacity()*sizeof(Digest)+catalog_ranges.capacity()*sizeof(CatalogRange)+catalog_routes.capacity()*sizeof(CatalogRoute);
  for(const auto& b:metadata.blocks)n+=b.pages.capacity()*sizeof(uint64_t);return n;
 }
 size_t Select(uint64_t guest)const{
  if(!catalog_routes.empty()){auto b=std::lower_bound(catalog_routes.begin(),catalog_routes.end(),guest,[](const auto& b,uint64_t n){return b.guest<n;});Require(b!=catalog_routes.end()&&b->guest==guest,"FEX catalog route absent");return UnitForHost(b->host);}
  auto b=std::lower_bound(metadata.blocks.begin(),metadata.blocks.end(),guest,[](const auto& b,uint64_t n){return b.guest<n;});
  Require(b!=metadata.blocks.end()&&b->guest==guest,"directory target absent from shard");
  return UnitForHost(b->host);
 }
 size_t UnitForHost(size_t host,bool entry=true)const{
  auto u=std::upper_bound(units.begin(),units.end(),host,[](size_t h,const Unit& u){return h<u.begin;});
  Require(u!=units.begin(),"prepared target unit absent");--u;Require(host<(entry?u->code_end:u->end),"prepared target outside unit");return size_t(u-units.begin());
 }
};
// Auxiliary proof cache only: no executable payload and no change to the
// original JIT budget. Eviction drops proofs, never compiled code or products.
class ShardProofs {
 struct Part{size_t key;std::shared_ptr<const VerifiedShard> proof;};
 struct Row{size_t shard;std::list<Part> parts;size_t bytes;};
 std::mutex mutex;std::list<Row> rows;size_t bytes{},evictions{},invalidations{},oversized{};
 static constexpr size_t RowBytes=sizeof(Row)+2*sizeof(void*);
 static size_t PartBytes(const VerifiedShard& proof){return proof.RetainedBytes()+sizeof(Part)+2*sizeof(void*);}
 // Root and range keys belong to ONE original logical shard. Counting each
 // range against MaxRows would shrink effective reuse despite spare byte budget.
 static size_t Group(size_t key){static_assert(sizeof(size_t)>=8);return key>>32?(key>>32)-1:key;}
 void EvictLast(){evictions+=rows.back().parts.size();bytes-=rows.back().bytes;rows.pop_back();}
public:
 static constexpr size_t MaxBytes=2u<<20,MaxRows=256;
 std::shared_ptr<const VerifiedShard> Find(size_t key,const FileStamp& stamp){
  std::scoped_lock guard{mutex};const auto group=Group(key);
  for(auto i=rows.begin();i!=rows.end();++i)if(i->shard==group){
   if(!(i->parts.front().proof->stamp==stamp)){bytes-=i->bytes;rows.erase(i);++invalidations;return {};}
   for(auto p=i->parts.begin();p!=i->parts.end();++p)if(p->key==key){
    auto proof=p->proof;i->parts.splice(i->parts.begin(),i->parts,p);rows.splice(rows.begin(),rows,i);return proof;
   }
   return {};
  }
  return {};
 }
 void Put(size_t key,std::shared_ptr<const VerifiedShard> proof){
  std::scoped_lock guard{mutex};const auto size=PartBytes(*proof),group=Group(key);
  if(size>MaxBytes-RowBytes){++oversized;return;}
  // Allocate before mutating rows. Once a new empty row exists below, splice
  // cannot throw and leave an empty group visible after allocation failure.
  std::list<Part> incoming;incoming.push_front({key,proof});
  auto row=std::find_if(rows.begin(),rows.end(),[&](const auto& r){return r.shard==group;});
  if(row!=rows.end()&&!(row->parts.front().proof->stamp==proof->stamp)){bytes-=row->bytes;rows.erase(row);row=rows.end();++invalidations;}
  if(row==rows.end()){
   while(!rows.empty()&&(bytes>MaxBytes-RowBytes-size||rows.size()>=MaxRows))EvictLast();
   rows.push_front({group,{},RowBytes});bytes+=RowBytes;
  }else{
   rows.splice(rows.begin(),rows,row);
   for(auto p=rows.front().parts.begin();p!=rows.front().parts.end();++p)if(p->key==key){const auto old=PartBytes(*p->proof);bytes-=old;rows.front().bytes-=old;rows.front().parts.erase(p);break;}
   while(bytes>MaxBytes-size){
    if(rows.size()>1)EvictLast();
    else{auto& r=rows.front();const auto old=PartBytes(*r.parts.back().proof);bytes-=old;r.bytes-=old;r.parts.pop_back();++evictions;}
   }
  }
  rows.front().parts.splice(rows.front().parts.begin(),incoming,incoming.begin());rows.front().bytes+=size;bytes+=size;
 }
 size_t Bytes(){std::scoped_lock guard{mutex};return bytes;}
 std::array<size_t,3> Churn(){std::scoped_lock guard{mutex};return {evictions,invalidations,oversized};}
};
}
