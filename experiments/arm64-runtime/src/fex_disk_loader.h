// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_io.h"
#include "bb_cpu_cache_validation.h"
#include "fex_compiled_units.h"
#include "fex_verified_shard.h"
#include "fex_paged_catalog.h"
#include "git_version.h"
#include "Interface/Context/Context.h"
#include "Interface/Core/LookupCache.h"
#include <FEXCore/Debug/InternalThreadState.h>
#include <FEXCore/Utils/AllocatorHooks.h>
#include <FEXCore/HLE/SyscallHandler.h>
#include <mutex>
#include <atomic>
#include <set>
#include <chrono>
namespace BbFexDisk {
class Loader {
 fs::path root;BbCpuCache::Identity identity;Directory index;
 std::vector<std::pair<uint64_t,uint64_t>> sections;uintptr_t base;
 std::mutex status_mutex,first_read_mutex;std::vector<bool> bad;
 std::unique_ptr<OwnedDirectory> files;ShardProofs proofs;
 std::atomic<uint64_t> hits{},misses{},rejected{},copied{},budget{};
 std::atomic<uint64_t> load_ns{},jit_count{},jit_ns{};
 std::atomic<uint64_t> whole_reads{},read_bytes{},hash_bytes{},proof_hits{},writer_wait_ns{},writer_hold_ns{},raced{},catalog_reads{},root_reads{},unit_metadata_reads{},metadata_bytes{},native_bytes{};
 bool Bad(size_t shard){std::scoped_lock guard{status_mutex};return bad[shard];}
 void Reject(size_t shard,const char* reason){
  {std::scoped_lock guard{status_mutex};if(bad[shard])return;bad[shard]=true;}++rejected;
  std::fprintf(stderr,"FEX DISK rejected shard=%zu reason=%s; own JIT retained\n",shard,reason);
  // A bounded append requests local repair on the next normal preparation.
  int fd=open((root/"repair-requests.bin").c_str(),O_WRONLY|O_CREAT|O_APPEND|O_NOFOLLOW|O_CLOEXEC,0600);
  if(fd>=0){struct stat st{};if(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_size<65536){uint64_t n=shard;(void)write(fd,&n,8);}close(fd);}
 }
 struct Input {
  // At most one retained full payload exists. Hot range reads do not acquire
  // this gate; it is separate from every FEX JIT/lookup/invalidation lock.
  std::unique_lock<std::mutex> first_read;
  std::shared_ptr<const VerifiedShard> proof;std::vector<std::byte> bytes;size_t offset{},unit{};
 };
 Input PreparePaged(size_t shard,uint64_t guest){
  static_assert(sizeof(size_t)>=8);
  const auto& meta=index.shards[shard];Input in;auto native=files->Open(Filename(meta.seeds),BbCpuCache::PrefixBytes+(16u<<20));auto stamp=native.OpenedStamp();Require(uint64_t(stamp.value.st_size)==meta.file_bytes,"FEX paged native size");
  auto routing=proofs.Find(shard,stamp);size_t selected{};std::shared_ptr<const VerifiedShard> unit;
  auto lookup=[&]{if(routing){selected=routing->Select(guest);unit=proofs.Find(((shard+1)<<32)|selected,stamp);}};lookup();
  if(unit)++proof_hits;
  else{
   std::scoped_lock gate{first_read_mutex};stamp=native.Stamp();routing=proofs.Find(shard,stamp);lookup();
   if(!unit){
    auto catalog=files->Open(CatalogFilename(meta.catalog_hash),16u<<20);const auto observed=catalog.OpenedStamp();Require(uint64_t(observed.value.st_size)==meta.catalog_bytes,"FEX paged file size");
    if(!routing){auto data=catalog.ReadRange(0,meta.catalog_root_bytes,observed);read_bytes+=data.size();hash_bytes+=data.size();metadata_bytes+=data.size();++catalog_reads;++root_reads;auto root=DecodePagedRoot(data,meta,index.key,{index.image_bytes,sections,GIT_HASH});root->stamp=stamp;routing=std::move(root);proofs.Put(shard,routing);}
    selected=routing->Select(guest);const auto& range=routing->catalog_ranges.at(selected);auto data=catalog.ReadRange(range.offset,range.bytes,observed);read_bytes+=data.size();hash_bytes+=data.size();metadata_bytes+=data.size();++catalog_reads;++unit_metadata_reads;auto decoded=DecodePagedUnit(data,*routing,selected,meta,index.key,{index.image_bytes,sections,GIT_HASH});decoded->stamp=stamp;unit=std::move(decoded);proofs.Put(((shard+1)<<32)|selected,unit);
   }
  }
  Require(unit&&unit->units.size()==1&&unit->catalog_ranges.empty(),"FEX paged selected proof");in.proof=std::move(unit);const auto& u=in.proof->units[0];in.bytes=native.ReadRange(BbCpuCache::PrefixBytes+in.proof->metadata.code_offset+u.begin,u.end-u.begin,stamp);read_bytes+=in.bytes.size();hash_bytes+=in.bytes.size();native_bytes+=in.bytes.size();Require(BbCpuCache::Hash(in.bytes)==in.proof->unit_hashes[0],"FEX paged native checksum");return in;
 }
 Input Prepare(size_t shard,uint64_t guest){
  if(index.shards[shard].catalog_root_bytes)return PreparePaged(shard,guest);
  const auto& meta=index.shards[shard];Input in;auto file=files->Open(Filename(meta.seeds),BbCpuCache::PrefixBytes+(16u<<20));auto stamp=file.OpenedStamp();
  Require(uint64_t(stamp.value.st_size)==meta.file_bytes,"shard file size");in.proof=proofs.Find(shard,stamp);bool reused=bool(in.proof);
  if(!in.proof){
   in.first_read=std::unique_lock{first_read_mutex};stamp=file.Stamp();in.proof=proofs.Find(shard,stamp);reused=bool(in.proof);
   if(!in.proof){
    if(meta.catalog_bytes){
     auto catalog=files->Open(CatalogFilename(meta.catalog_hash),16u<<20);Require(uint64_t(catalog.OpenedStamp().value.st_size)==meta.catalog_bytes,"FEX unit catalog file size");auto data=catalog.ReadRange(0,meta.catalog_bytes,catalog.OpenedStamp());read_bytes+=data.size();hash_bytes+=data.size();++catalog_reads;
     auto verified=DecodeCatalog(data,meta,index.key,{index.image_bytes,sections,GIT_HASH});verified->stamp=stamp;in.proof=std::move(verified);proofs.Put(shard,in.proof);std::vector<std::byte>{}.swap(data);
     in.first_read.unlock();
    }else{
    in.bytes=file.ReadRange(0,size_t(stamp.value.st_size),stamp);++whole_reads;read_bytes+=in.bytes.size();hash_bytes+=in.bytes.size();
    Require(BbCpuCache::Hash(in.bytes)==meta.hash,"shard checksum");
    auto raw=BbCpuCache::Check(in.bytes,identity);hash_bytes+=raw.size();auto checked=BbCacheValidation::Validate(raw,{index.image_bytes,sections,GIT_HASH});
    Require(checked&&checked->code_bytes==meta.code_bytes,"shard compatibility/structure");auto source=raw.subspan(checked->code_offset,checked->code_bytes);
    auto verified=std::make_shared<VerifiedShard>();verified->stamp=stamp;verified->units=CompiledUnits(source,*checked);verified->metadata=std::move(*checked);verified->unit_hashes.reserve(verified->units.size());
    for(const auto& u:verified->units){auto bytes=source.subspan(u.begin,u.end-u.begin);verified->unit_hashes.push_back(BbCpuCache::Hash(bytes));hash_bytes+=bytes.size();}
    in.proof=std::move(verified);in.unit=in.proof->Select(guest);in.offset=BbCpuCache::PrefixBytes+in.proof->metadata.code_offset+in.proof->units[in.unit].begin;
    proofs.Put(shard,in.proof);
    // Legacy v1 admission still checks the complete payload. Retain only the
    // selected unit before allowing another full read, never wait for FEX
    // publication while holding the full-payload gate or its allocation.
    const auto& u=in.proof->units[in.unit];std::vector<std::byte> selected(in.bytes.begin()+in.offset,in.bytes.begin()+in.offset+u.end-u.begin);
    std::vector<std::byte>{}.swap(in.bytes);in.bytes=std::move(selected);in.offset=0;in.first_read.unlock();return in;
    }
   }
   if(in.first_read.owns_lock())in.first_read.unlock();
  }
  if(reused)++proof_hits;in.unit=in.proof->Select(guest);const auto& u=in.proof->units[in.unit];
  in.bytes=file.ReadRange(BbCpuCache::PrefixBytes+in.proof->metadata.code_offset+u.begin,u.end-u.begin,stamp);read_bytes+=in.bytes.size();hash_bytes+=in.bytes.size();
  Require(BbCpuCache::Hash(in.bytes)==in.proof->unit_hashes[in.unit],"verified compiled unit checksum");return in;
 }
public:
 static uint64_t Now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
 void Report(){std::printf("FEX DISK totals hits=%llu misses=%llu rejected=%llu copied_bytes=%llu budget_fallbacks=%llu load_ms=%.3f fallback_compiles=%llu fallback_path_ms=%.3f whole_reads=%llu read_bytes=%llu hash_bytes=%llu proof_hits=%llu proof_bytes=%zu writer_wait_ms=%.3f writer_hold_ms=%.3f\n",(unsigned long long)hits.load(),(unsigned long long)misses.load(),(unsigned long long)rejected.load(),(unsigned long long)copied.load(),(unsigned long long)budget.load(),load_ns.load()/1e6,(unsigned long long)jit_count.load(),jit_ns.load()/1e6,(unsigned long long)whole_reads.load(),(unsigned long long)read_bytes.load(),(unsigned long long)hash_bytes.load(),(unsigned long long)proof_hits.load(),proofs.Bytes(),writer_wait_ns.load()/1e6,writer_hold_ns.load()/1e6);std::printf("FEX DISK unit-index catalog_reads=%llu root_reads=%llu unit_metadata_reads=%llu metadata_bytes=%llu native_bytes=%llu\n",(unsigned long long)catalog_reads.load(),(unsigned long long)root_reads.load(),(unsigned long long)unit_metadata_reads.load(),(unsigned long long)metadata_bytes.load(),(unsigned long long)native_bytes.load());const auto churn=proofs.Churn();std::printf("FEX DISK proof-cache evictions=%zu invalidations=%zu oversized=%zu raced_publications=%llu\n",churn[0],churn[1],churn[2],(unsigned long long)raced.load());}
 void EndCompilation(uint64_t start){jit_ns.fetch_add(Now()-start,std::memory_order_relaxed);if((jit_count.fetch_add(1,std::memory_order_relaxed)+1)%256==0)Report();}
 Loader(fs::path directory,const BbCpuCache::Identity& expected,std::span<const std::pair<uint64_t,uint64_t>> ranges,uintptr_t image,uint64_t bytes)
 :root(std::move(directory)),identity(expected),sections(ranges.begin(),ranges.end()),base(image){
  Require(root.is_absolute()&&fs::canonical(root)==root&&root.string().starts_with("/Users/Shared/BloodEchoesDevelopment/"),"FEX prepared directory must stay private");
  files=std::make_unique<OwnedDirectory>(root);
  index=Decode(Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),bytes);bad.resize(index.shards.size());
  BbCacheValidation::Policy policy{bytes,sections,GIT_HASH};
  for(auto& e:index.entries)Require(BbCacheValidation::Inside(policy,e.guest),"FEX directory entry outside registered code");
  for(auto& p:index.pages)Require(BbCacheValidation::Inside(policy,p.guest)&&BbCacheValidation::Inside(policy,p.guest+p.bytes-1),"FEX directory page outside registered code");
  std::printf("FEX DISK directory shards=%zu entries=%zu metadata_bytes=%zu; payloads deferred, original JIT budget unchanged\n",index.shards.size(),index.entries.size(),index.entries.size()*sizeof(Entry)+index.shards.size()*sizeof(Shard)+index.pages.size()*sizeof(Page));
 }
 ~Loader(){Report();}
 uint64_t Hits()const{return hits.load(std::memory_order_relaxed);}
 uint64_t CopiedBytes()const{return copied.load(std::memory_order_relaxed);}
 uint64_t CatalogReads()const{return catalog_reads.load(std::memory_order_relaxed);}
 uint64_t WholeReads()const{return whole_reads.load(std::memory_order_relaxed);}
 uint64_t NativeBytes()const{return native_bytes.load(std::memory_order_relaxed);}
 uint64_t ReadBytes()const{return read_bytes.load(std::memory_order_relaxed);}
 size_t ProofBytes(){return proofs.Bytes();}
 uintptr_t Load(FEXCore::Core::InternalThreadState* thread,uint64_t rip){
  if(rip<base)return 0;const auto offset=rip-base;if(offset>=index.image_bytes){++misses;return 0;}
  const auto entry=std::lower_bound(index.entries.begin(),index.entries.end(),offset,[](auto& e,uint64_t n){return e.guest<n;});
  if(entry==index.entries.end()||entry->guest!=offset){++misses;return 0;}
  auto& ctx=*static_cast<FEXCore::Context::ContextImpl*>(thread->CTX);
  struct Timing{Loader& self;uint64_t start=Now();~Timing(){self.load_ns.fetch_add(Now()-start,std::memory_order_relaxed);}} timing{*this};
  const auto shard=entry->shard;if(Bad(shard))return 0;
  try{
   auto input=Prepare(shard,offset);const auto& checked=input.proof->metadata;const auto unit=input.proof->units[input.unit];const auto needed=unit.end-unit.begin;
   bool target=false;std::set<uint64_t> pages;
   for(auto& b:checked.blocks)if(b.host>=unit.begin&&b.host<unit.end){target|=b.guest==offset;pages.insert(b.pages.begin(),b.pages.end());}
   Require(target,"directory target absent from shard");
   std::vector<FEXCore::CPU::Relocation> relocations;
   for(auto r:checked.relocs)if(r.offset>=unit.begin&&r.offset<unit.end){
    auto out=FEXCore::CPU::Relocation::Default();out.Header.Offset=r.offset;out.Header.Type=FEXCore::CPU::RelocationTypes(r.kind);
    if(r.kind==0)out.NamedSymbolLiteral.Symbol=FEXCore::CPU::RelocNamedSymbolLiteral::NamedSymbol::SYMBOL_LITERAL_EXITFUNCTION_LINKER;
    else{out.GuestRIP.RegisterIndex=uint8_t(r.reg);out.GuestRIP.GuestRIP=r.rip;}relocations.push_back(out);
   }
   // I/O, checksums, complete FXCC parsing and relocation selection precede the
   // JIT write lock. Another compiler may have published/rotated meanwhile.
   bool installed=false;const auto result=[&]() -> uintptr_t {
   auto invalidation=FEXCore::GuardSignalDeferringSection<std::shared_lock>(ctx.GetCodeInvalidationMutex(),thread);
   const auto wait=Now();std::unique_lock writer{ctx.CodeBufferWriteMutex};writer_wait_ns+=Now()-wait;
   struct HoldTiming{Loader& self;uint64_t start=Now();bool active=true;void Finish(){if(active){self.writer_hold_ns+=Now()-start;active=false;}}~HoldTiming(){Finish();}} hold{*this};
   auto buffer=ctx.GetLatest();if(thread->LookupCache->Shared!=buffer->LookupCache.get())return 0;
   if(auto existing=thread->LookupCache->FindBlock(thread,rip))return existing;
   const auto start=(ctx.LatestOffset+15)&~size_t{15};
   if(start>buffer->UsableSize()||needed>buffer->UsableSize()-start){++budget;return 0;}
   if(Bad(shard))return 0;
   // This also prevents a precise invalidation from re-publishing an old block
   // after code changed. Source-page checks occur on a disk miss, never per draw.
   for(auto page:pages){auto p=std::lower_bound(index.pages.begin(),index.pages.end(),page,[](auto& p,uint64_t n){return p.guest<n;});
    Require(p!=index.pages.end()&&p->guest==page&&BbCpuCache::Hash({reinterpret_cast<const std::byte*>(base+page),size_t(p->bytes)})==p->hash,"actual guest code page changed");}
   auto destination=std::span{reinterpret_cast<std::byte*>(buffer->Ptr)+start,needed};
   {
    FEXCore::Allocator::ScopedJITWriteProtect write;
    std::memcpy(destination.data(),input.bytes.data()+input.offset,needed);
    Require(ctx.GetCodeCache().ApplyCodeRelocations(base,destination,relocations,uint32_t(unit.begin),false),"compiled-unit relocations");
    __builtin___clear_cache(reinterpret_cast<char*>(destination.data()),reinterpret_cast<char*>(destination.data()+destination.size()));
   }
   ctx.LatestOffset=start+destination.size();
   // Publish only after all bytes and relocations are ready. These entries use
   // the NORMAL buffer's lifetime, backpatching, delinking and invalidation.
   for(auto& b:checked.blocks)if(b.host>=unit.begin&&b.host<unit.end&&!thread->LookupCache->FindBlock(thread,base+b.guest)){
    fextl::vector<uint64_t> code_pages;for(auto p:b.pages)code_pages.push_back(base+p);
    thread->LookupCache->AddBlockMapping(thread,base+b.guest,code_pages,destination.data()+b.host-unit.begin);
    fextl::set<uint64_t> entries{base+b.guest};
    for(auto p:code_pages)if(thread->LookupCache->AddBlockExecutableRange(thread,entries,p,4096))ctx.SyscallHandler->MarkGuestExecutableRange(thread,p,4096);
   }
   const auto result=thread->LookupCache->FindBlock(thread,rip);Require(result!=0,"prepared target publication");
   installed=true;
   return result;
   }();
   if(!result)return 0;
   // Console writes and full-payload destruction stay outside BOTH FEX locks.
   std::vector<std::byte>{}.swap(input.bytes);if(input.first_read.owns_lock())input.first_read.unlock();
   if(!installed){++raced;return result;}
   ++hits;copied+=needed;
   if(hits.load()<=8)std::printf("FEX DISK loaded shard=%zu unit_bytes=%zu shard_code_bytes=%zu; original compiled unit only, no IR/codegen\n",size_t(shard),needed,checked.code_bytes);
   if(hits.load()%256==0)Report();
   return result;
  }catch(const std::exception& error){Reject(shard,error.what());return 0;}
 }
};
}
