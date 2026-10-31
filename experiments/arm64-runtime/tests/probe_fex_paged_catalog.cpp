// Read-only codec costs over existing PRIVATE QA metadata, no guest execution.
// The recorded key/pages are inputs for this codec probe, not a new runtime
// compatibility attestation. No native shard or game/save file is opened.
#include "fex_paged_catalog.h"
#include "git_version.h"
#include <chrono>
#include <cstdio>
#include <cassert>
int main(int argc,char**argv){
 using namespace BbFexDisk;assert(argc==2);const fs::path root=argv[1];Require(root.is_absolute()&&fs::canonical(root)==root&&root.string().starts_with("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/"),"private QA metadata only");
 auto raw=Read(root/"directory.bin",MaxDirectoryBytes);Require(raw.size()>=104,"probe directory");Digest key{};memcpy(key.data(),raw.data()+16,32);uint64_t image{};memcpy(&image,raw.data()+8,8);auto directory=Decode(raw,key,image);
 std::vector<std::pair<uint64_t,uint64_t>> sections;
 for(const auto& p:directory.pages){if(!sections.empty()&&sections.back().second==p.guest)sections.back().second+=p.bytes;else sections.emplace_back(p.guest,p.guest+p.bytes);}
 const BbCacheValidation::Policy policy{image,sections,GIT_HASH};std::vector<size_t> candidates;
 for(size_t i=0;i<directory.shards.size();++i)if(directory.shards[i].catalog_bytes&&!directory.shards[i].catalog_root_bytes)candidates.push_back(i);
 Require(!candidates.empty(),"probe requires existing flat catalogs");const size_t count=std::min<size_t>(32,candidates.size());uint64_t old_bytes=0,new_bytes=0,flat_ns=0,paged_ns=0,old_retained=0,new_retained=0;
 constexpr size_t Repeats=8;
 for(size_t n=0;n<count;++n){const auto id=candidates[n*candidates.size()/count];const auto& meta=directory.shards[id];auto flat=Read(root/CatalogFilename(meta.catalog_hash),16u<<20);auto full=DecodeCatalog(flat,meta,key,policy);auto paged=EncodePagedCatalog(*full,meta,key,image,GIT_HASH);auto updated=meta;updated.catalog_bytes=paged.bytes.size();updated.catalog_root_bytes=paged.root_bytes;updated.catalog_hash=paged.hash;
  auto prefix=std::span(paged.bytes).first(paged.root_bytes);auto routes=DecodePagedRoot(prefix,updated,key,policy);
  for(size_t pick:{size_t(0),full->metadata.blocks.size()/2,full->metadata.blocks.size()-1}){const auto guest=full->metadata.blocks[pick].guest;const auto unit=routes->Select(guest);const auto& range=routes->catalog_ranges[unit];auto blob=std::span(paged.bytes).subspan(range.offset,range.bytes);auto selected=DecodePagedUnit(blob,*routes,unit,updated,key,policy);Require(selected->Select(guest)==0,"probe selected unit");
   old_bytes+=flat.size();new_bytes+=prefix.size()+blob.size();old_retained+=full->RetainedBytes();new_retained+=routes->RetainedBytes()+selected->RetainedBytes();
   auto flat_cost=[&]{auto start=std::chrono::steady_clock::now();for(size_t i=0;i<Repeats;++i){auto p=DecodeCatalog(flat,meta,key,policy);Require(p->Select(guest)<p->units.size(),"probe flat lookup");}flat_ns+=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();};
   auto paged_cost=[&]{auto start=std::chrono::steady_clock::now();for(size_t i=0;i<Repeats;++i){auto p=DecodePagedRoot(prefix,updated,key,policy);auto u=DecodePagedUnit(blob,*p,unit,updated,key,policy);Require(u->Select(guest)==0,"probe paged lookup");}paged_ns+=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();};
   if((n+pick)%2){paged_cost();flat_cost();}else{flat_cost();paged_cost();}
  }
 }
 std::printf("{\"schema\":2,\"shards\":%zu,\"modeled_cold_requests\":%zu,\"repeats\":%zu,\"flat_metadata_bytes\":%llu,\"paged_metadata_bytes\":%llu,\"flat_decode_ms\":%.6f,\"paged_decode_ms\":%.6f,\"flat_retained_sum\":%llu,\"paged_retained_sum\":%llu,\"codec_order\":\"alternating\",\"metadata_io_modeled\":true,\"native_payload_read\":false,\"guest_executed\":false,\"runtime_identity_attestation\":false,\"source_modified\":false}\n",count,3*count,Repeats,(unsigned long long)old_bytes,(unsigned long long)new_bytes,flat_ns/1e6,paged_ns/1e6,(unsigned long long)old_retained,(unsigned long long)new_retained);
}
