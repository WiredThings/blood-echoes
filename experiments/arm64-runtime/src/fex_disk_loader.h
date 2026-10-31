// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_io.h"
#include "bb_cpu_cache_validation.h"
#include "fex_compiled_units.h"
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
 std::mutex mutex;std::vector<bool> bad;
 std::atomic<uint64_t> hits{},misses{},rejected{},copied{},budget{};
 std::atomic<uint64_t> load_ns{},jit_count{},jit_ns{};
 void Reject(size_t shard,const char* reason){
  bad[shard]=true;++rejected;
  std::fprintf(stderr,"FEX DISK rejected shard=%zu reason=%s; own JIT retained\n",shard,reason);
  // A bounded append requests local repair on the next normal preparation.
  int fd=open((root/"repair-requests.bin").c_str(),O_WRONLY|O_CREAT|O_APPEND|O_NOFOLLOW|O_CLOEXEC,0600);
  if(fd>=0){struct stat st{};if(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_size<65536){uint64_t n=shard;(void)write(fd,&n,8);}close(fd);}
 }
public:
 static uint64_t Now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
 void Report()const{std::printf("FEX DISK totals hits=%llu misses=%llu rejected=%llu copied_bytes=%llu budget_fallbacks=%llu load_ms=%.3f fallback_compiles=%llu fallback_path_ms=%.3f\n",(unsigned long long)hits.load(),(unsigned long long)misses.load(),(unsigned long long)rejected.load(),(unsigned long long)copied.load(),(unsigned long long)budget.load(),load_ns.load()/1e6,(unsigned long long)jit_count.load(),jit_ns.load()/1e6);}
 void EndCompilation(uint64_t start){jit_ns.fetch_add(Now()-start,std::memory_order_relaxed);if((jit_count.fetch_add(1,std::memory_order_relaxed)+1)%256==0)Report();}
 Loader(fs::path directory,const BbCpuCache::Identity& expected,std::span<const std::pair<uint64_t,uint64_t>> ranges,uintptr_t image,uint64_t bytes)
 :root(std::move(directory)),identity(expected),sections(ranges.begin(),ranges.end()),base(image){
  Require(root.is_absolute()&&fs::canonical(root)==root&&root.string().starts_with("/Users/Shared/BloodEchoesDevelopment/"),"FEX prepared directory must stay private");
  index=Decode(Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),bytes);bad.resize(index.shards.size());
  BbCacheValidation::Policy policy{bytes,sections,GIT_HASH};
  for(auto& e:index.entries)Require(BbCacheValidation::Inside(policy,e.guest),"FEX directory entry outside registered code");
  for(auto& p:index.pages)Require(BbCacheValidation::Inside(policy,p.guest)&&BbCacheValidation::Inside(policy,p.guest+p.bytes-1),"FEX directory page outside registered code");
  std::printf("FEX DISK directory shards=%zu entries=%zu metadata_bytes=%zu; payloads deferred, original JIT budget unchanged\n",index.shards.size(),index.entries.size(),index.entries.size()*sizeof(Entry)+index.shards.size()*sizeof(Shard)+index.pages.size()*sizeof(Page));
 }
 ~Loader(){Report();}
 uint64_t Hits()const{return hits.load(std::memory_order_relaxed);}
 uint64_t CopiedBytes()const{return copied.load(std::memory_order_relaxed);}
 uintptr_t Load(FEXCore::Core::InternalThreadState* thread,uint64_t rip){
  if(rip<base)return 0;const auto offset=rip-base;
  const auto entry=std::lower_bound(index.entries.begin(),index.entries.end(),offset,[](auto& e,uint64_t n){return e.guest<n;});
  if(entry==index.entries.end()||entry->guest!=offset){++misses;return 0;}
  auto& ctx=*static_cast<FEXCore::Context::ContextImpl*>(thread->CTX);
  struct Timing{Loader& self;uint64_t start=Now();~Timing(){self.load_ns.fetch_add(Now()-start,std::memory_order_relaxed);}} timing{*this};
  std::scoped_lock guard{mutex};const auto shard=entry->shard;if(bad[shard])return 0;
  // Match the normal compiler's buffer -> lookup lock order. Never rotate or
  // shrink a code cache here; its original compiler handles a full/stale buffer.
  std::unique_lock writer{ctx.CodeBufferWriteMutex};auto buffer=ctx.GetLatest();
  if(thread->LookupCache->Shared!=buffer->LookupCache.get())return 0;
  if(auto existing=thread->LookupCache->FindBlock(thread,rip))return existing;
  const auto start=(ctx.LatestOffset+15)&~size_t{15};const auto& meta=index.shards[shard];
  if(start>=buffer->UsableSize()){++budget;return 0;}
  try{
   auto file=Read(root/Filename(meta.seeds),BbCpuCache::PrefixBytes+(16u<<20));
   Require(file.size()==meta.file_bytes&&BbCpuCache::Hash(file)==meta.hash,"shard checksum/size");
   auto raw=BbCpuCache::Check(file,identity);auto checked=BbCacheValidation::Validate(raw,{index.image_bytes,sections,GIT_HASH});
   Require(checked&&checked->code_bytes==meta.code_bytes,"shard compatibility/structure");
   auto source_code=raw.subspan(checked->code_offset,checked->code_bytes);const auto units=CompiledUnits(source_code,*checked);
   const auto target_block=std::find_if(checked->blocks.begin(),checked->blocks.end(),[&](auto& b){return b.guest==offset;});
   Require(target_block!=checked->blocks.end(),"directory target absent from shard");
   auto selected=std::find_if(units.begin(),units.end(),[&](const auto& u){return target_block->host>=u.begin&&target_block->host<u.end;});Require(selected!=units.end(),"target compiled unit absent");
   const auto unit=*selected;const auto needed=unit.end-unit.begin;
   if(needed>buffer->UsableSize()-start){++budget;return 0;}
   bool target=false;std::set<uint64_t> pages;
   for(auto& b:checked->blocks)if(b.host>=unit.begin&&b.host<unit.end){target|=b.guest==offset;pages.insert(b.pages.begin(),b.pages.end());}
   Require(target,"directory target absent from shard");
   // This also prevents a precise invalidation from re-publishing an old block
   // after code changed. Source-page checks occur on a disk miss, never per draw.
   for(auto page:pages){auto p=std::lower_bound(index.pages.begin(),index.pages.end(),page,[](auto& p,uint64_t n){return p.guest<n;});
    Require(p!=index.pages.end()&&p->guest==page&&BbCpuCache::Hash({reinterpret_cast<const std::byte*>(base+page),size_t(p->bytes)})==p->hash,"actual guest code page changed");}
   auto destination=std::span{reinterpret_cast<std::byte*>(buffer->Ptr)+start,needed};
   {
    FEXCore::Allocator::ScopedJITWriteProtect write;
    std::memcpy(destination.data(),source_code.data()+unit.begin,needed);
    const auto* all=reinterpret_cast<const FEXCore::CPU::Relocation*>(raw.data()+checked->relocation_offset);std::vector<FEXCore::CPU::Relocation> relocations;
    for(size_t n=0;n<checked->relocs.size();++n)if(all[n].Header.Offset>=unit.begin&&all[n].Header.Offset<unit.end)relocations.push_back(all[n]);
    Require(ctx.GetCodeCache().ApplyCodeRelocations(base,destination,relocations,uint32_t(unit.begin),false),"compiled-unit relocations");
    __builtin___clear_cache(reinterpret_cast<char*>(destination.data()),reinterpret_cast<char*>(destination.data()+destination.size()));
   }
   ctx.LatestOffset=start+destination.size();
   // Publish only after all bytes and relocations are ready. These entries use
   // the NORMAL buffer's lifetime, backpatching, delinking and invalidation.
   for(auto& b:checked->blocks)if(b.host>=unit.begin&&b.host<unit.end&&!thread->LookupCache->FindBlock(thread,base+b.guest)){
    fextl::vector<uint64_t> code_pages;for(auto p:b.pages)code_pages.push_back(base+p);
    thread->LookupCache->AddBlockMapping(thread,base+b.guest,code_pages,destination.data()+b.host-unit.begin);
    fextl::set<uint64_t> entries{base+b.guest};
    for(auto p:code_pages)if(thread->LookupCache->AddBlockExecutableRange(thread,entries,p,4096))ctx.SyscallHandler->MarkGuestExecutableRange(thread,p,4096);
   }
   const auto result=thread->LookupCache->FindBlock(thread,rip);Require(result!=0,"prepared target publication");
   ++hits;copied+=destination.size();
   if(hits.load()<=8)std::printf("FEX DISK loaded shard=%zu unit_bytes=%zu shard_code_bytes=%zu; original compiled unit only, no IR/codegen\n",size_t(shard),destination.size(),checked->code_bytes);
   if(hits.load()%256==0)Report();
   return result;
  }catch(const std::exception& error){Reject(shard,error.what());return 0;}
 }
};
}
