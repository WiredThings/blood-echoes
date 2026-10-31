// SPDX-License-Identifier: MIT
// The existing FEX compiler and FXCC serializer consume the real mapped image.
// Never execute guest code during preparation, and never invent an IR recipe.
#pragma once
#include "fex_disk_loader.h"
#include <fstream>
#include <map>
namespace BbFexDisk {
inline void EnsureCatalog(const fs::path& output,Shard& meta,const BbCpuCache::Identity& identity,
 std::span<const std::pair<uint64_t,uint64_t>> sections,uint64_t image,
 std::span<const std::byte> supplied={},const BbCacheValidation::Validated* validated=nullptr){
 if(!meta.file_bytes)return;
 if(meta.catalog_root_bytes){struct stat st{};const auto p=output/CatalogFilename(meta.catalog_hash);
  if(!lstat(p.c_str(),&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_nlink==1&&!(st.st_mode&0022)&&uint64_t(st.st_size)==meta.catalog_bytes)return;
 }
 std::shared_ptr<VerifiedShard> previous;
 if(supplied.empty()&&meta.catalog_bytes&&!meta.catalog_root_bytes){
  try{auto data=Read(output/CatalogFilename(meta.catalog_hash),16u<<20);previous=DecodeCatalog(data,meta,BbCpuCache::Key(identity),{image,sections,GIT_HASH});}catch(const std::exception&){}
 }
 std::vector<std::byte> owned;std::optional<BbCacheValidation::Validated> checked;
 if(!previous){
  if(supplied.empty()){owned=Read(output/Filename(meta.seeds),BbCpuCache::PrefixBytes+(16u<<20));supplied=owned;}
  Require(supplied.size()==meta.file_bytes&&BbCpuCache::Hash(supplied)==meta.hash,"FEX catalog producer shard checksum");
  if(!validated){auto raw=BbCpuCache::Check(supplied,identity);checked=BbCacheValidation::Validate(raw,{image,sections,GIT_HASH});Require(bool(checked),"FEX catalog producer FXCC validation");validated=&*checked;}
  previous=std::make_shared<VerifiedShard>(MakeCatalog(supplied,*validated));
 }
 std::vector<std::byte> bytes;
 try{auto paged=EncodePagedCatalog(*previous,meta,BbCpuCache::Key(identity),image,GIT_HASH);meta.catalog_root_bytes=paged.root_bytes;meta.catalog_hash=paged.hash;bytes=std::move(paged.bytes);}
 catch(const std::runtime_error& error){
  if(std::string_view(error.what())!="FEX paged catalog budget")throw;
  // An oversized expanded catalog keeps the original bounded flat format.
  bytes=EncodeCatalog(*previous,meta,BbCpuCache::Key(identity),image,GIT_HASH);meta.catalog_root_bytes=0;meta.catalog_hash=BbCpuCache::Hash(bytes);
  std::fprintf(stderr,"FEX PREPARE paged catalog exceeds budget; verified flat catalog retained\n");
 }
 meta.catalog_bytes=bytes.size();
 const auto path=output/CatalogFilename(meta.catalog_hash);
 if(fs::exists(path)){try{if(Read(path,16u<<20)==bytes)return;}catch(const std::exception&){}Preserve(path);}
 Publish(path,bytes);
}
inline std::vector<uint64_t> Plan(const fs::path& path,uint64_t image){
 auto raw=Read(path,8+8+4000000*8);Require(raw.size()>=16&&!memcmp(raw.data(),"BFXPLAN1",8),"FEX preparation plan");
 uint64_t count{};memcpy(&count,raw.data()+8,8);Require(count&&count<=4000000&&raw.size()==16+count*8,"FEX entry plan budget");
 std::vector<uint64_t> entries(count);memcpy(entries.data(),raw.data()+16,count*8);
 for(size_t i=0;i<entries.size();++i)Require(entries[i]<image&&(!i||entries[i]>entries[i-1]),"FEX entry plan order/range");return entries;
}
inline uint64_t Generate(FEXCore::Context::ContextImpl& ctx,FEXCore::Core::InternalThreadState* thread,
 const fs::path& plan,const fs::path& output,const BbCpuCache::Identity& identity,
 std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t base,uint64_t image,
 const FEXCore::ExecutableFileInfo& source){
 Require(ctx.GetCodeCache().EnableCodeCaching()&&ctx.LatestOffset==0,"FEX offline context must collect relocations before any execution");
 Require(fs::canonical(output)==output&&output.string().starts_with("/Users/Shared/BloodEchoesDevelopment/"),"FEX output private directory");
 const auto entries=Plan(plan,image);Directory result{image,BbCpuCache::Key(identity)};
 std::optional<Directory> previous;try{previous=Decode(Read(output/"directory.bin",96u<<20),result.key,image);}catch(const std::exception&){}
 std::set<size_t> repairs;
 if(fs::exists(output/"repair-requests.bin")){auto raw=Read(output/"repair-requests.bin",65536);Require(raw.size()%8==0,"FEX repair request shape");for(size_t at=0;at<raw.size();at+=8){uint64_t n;memcpy(&n,raw.data()+at,8);repairs.insert(n);}}
 std::map<Digest,size_t> completed;std::map<size_t,std::vector<uint64_t>> old_entries;
 if(previous){for(size_t id=0;id<previous->shards.size();++id)if(!repairs.contains(id))completed.emplace(previous->shards[id].seeds,id);for(auto e:previous->entries)old_entries[e.shard].push_back(e.guest);}
 ctx.GetCodeCache().InitiateCacheGeneration();
 std::map<uint64_t,Page> used_pages;std::map<uint64_t,uint64_t> published;
 if(previous)for(auto page:previous->pages)used_pages.emplace(page.guest,page);
 uint64_t compiled=0,reused=0,rejected=0,batches=0;
 constexpr size_t Batch=64;
 for(size_t begin=0;begin<entries.size();){
  const auto page_end=(entries[begin]|uint64_t(4095))+1;
  const auto end=std::min(begin+Batch,size_t(std::lower_bound(entries.begin()+begin,entries.end(),page_end)-entries.begin()));const size_t id=result.shards.size();
  const auto seeds=BbCpuCache::Hash(std::as_bytes(std::span(entries).subspan(begin,end-begin)));
  if(auto done=completed.find(seeds);done!=completed.end()){
   auto meta=previous->shards[done->second];struct stat st{};
   // A completed directory binds the real compiler/source identity and shard
   // checksum. Carry its proof forward; runtime still hashes/validates payloads
   // at use. Do not read every unchanged ARM payload for incremental captures.
   if(!meta.file_bytes||(!lstat((output/Filename(seeds)).c_str(),&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&uint64_t(st.st_size)==meta.file_bytes)){
    EnsureCatalog(output,meta,identity,sections,image);
    result.shards.push_back(meta);for(auto guest:old_entries[done->second])published.try_emplace(guest,id);++reused;
    std::printf("{\"event\":\"compile-progress\",\"done\":%zu,\"total\":%zu,\"active\":1,\"capacity\":1,\"backend\":\"fex\"}\n",end,entries.size());std::fflush(stdout);begin=end;continue;
   }
  }
  // Stable requested batches are independent checkpoints. A missing/corrupt
  // payload is rebuilt; completed valid shards survive interruption unchanged.
  const auto path=output/Filename(seeds);std::vector<std::byte> file;std::optional<BbCacheValidation::Validated> checked;
  try{
   auto seed=Read(fs::path(path.string()+".seed"),64);Require(seed.size()==64&&!memcmp(seed.data(),result.key.data(),32)&&!memcmp(seed.data()+32,seeds.data(),32),"FEX saved seed batch differs");
   file=Read(path,BbCpuCache::PrefixBytes+(16u<<20));
   auto payload=BbCpuCache::Check(file,identity);checked=BbCacheValidation::Validate(payload,{image,sections,GIT_HASH});Require(bool(checked),"FEX old shard invalid");
   ++reused;
  }catch(const std::exception&){file.clear();checked.reset();}
  if(!checked){
   auto buffer=ctx.GetLatest();
   for(size_t i=begin;i<end;++i){
    const auto address=base+entries[i];auto owned=ctx.SyscallHandler->LookupExecutableFileSection(thread,address);
    if(!owned||!ctx.CheckIfBlockIsCacheable(*thread,address,FEXCore::Config::Get_MAXINST()())){++rejected;continue;}
    FEXCore::Allocator::ScopedJITWriteProtect write;
    const auto old=ctx.LatestOffset;ctx.CompileRIP(thread,address);Require(ctx.GetLatest()==buffer,"FEX offline shard overflow");compiled+=ctx.LatestOffset!=old;
   }
   if(!thread->LookupCache->Shared->BlockList.empty()){
    const auto raw_path=output/("payload-"+std::to_string(getpid())+".tmp");int fd=open(raw_path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);Require(fd>=0,"FEX raw output");
    bool saved;{FEXCore::Allocator::ScopedJITWriteProtect write;saved=ctx.GetCodeCache().SaveData(*thread,fd,{source,base,base,base+image},0);}
    const bool durable=saved&&fsync(fd)==0;close(fd);Require(durable,"FEX serializer failed");
    auto payload=Read(raw_path,16u<<20);checked=BbCacheValidation::Validate(payload,{image,sections,GIT_HASH});
    if(checked){auto prefix=BbCpuCache::Prefix(payload,identity);file.assign(prefix.begin(),prefix.end());file.insert(file.end(),payload.begin(),payload.end());}
    else{std::fprintf(stderr,"FEX PREPARE batch=%zu unavailable: original FXCC validation refused; own JIT retained\n",begin/Batch);rejected+=end-begin;}
    unlink(raw_path.c_str());
   }
   // Preparation owns this unpublished buffer: no guest/thread can execute it.
   // Keep its original size, clear links through the existing implementation.
   {FEXCore::Allocator::ScopedJITWriteProtect write;auto lock=thread->LookupCache->AcquireWriteLock();thread->LookupCache->ClearCache(lock);ctx.LatestOffset=0;thread->CPUBackend->ClearRelocations();}
   if(checked){
    if(fs::exists(path))Preserve(path);
    Publish(path,file);auto seed_path=fs::path(path.string()+".seed");
    if(fs::exists(seed_path))Preserve(seed_path);
    std::vector<std::byte> record(64);memcpy(record.data(),result.key.data(),32);memcpy(record.data()+32,seeds.data(),32);Publish(seed_path,record);++batches;
   }
  }
  // Empty/unavailable batches get no executable payload. Keep the fixed batch
  // number in the filename/index so subsequent repairs do not shift every ID.
  if(!checked){result.shards.push_back({0,0,{},seeds});}
  else{
   Shard meta{file.size(),checked->code_bytes,BbCpuCache::Hash(file),seeds};EnsureCatalog(output,meta,identity,sections,image,file,&*checked);result.shards.push_back(meta);
   for(auto& b:checked->blocks){published.try_emplace(b.guest,id);for(auto page:b.pages)if(!used_pages.contains(page)){
    Require(page<image&&BbCacheValidation::Inside({image,sections,GIT_HASH},page),"FEX cached source page unavailable");const auto n=std::min<uint64_t>(4096,image-page);
    used_pages.emplace(page,Page{page,n,BbCpuCache::Hash({reinterpret_cast<const std::byte*>(base+page),size_t(n)})});
   }}
  }
  std::printf("{\"event\":\"compile-progress\",\"done\":%zu,\"total\":%zu,\"active\":1,\"capacity\":1,\"backend\":\"fex\"}\n",end,entries.size());std::fflush(stdout);
  begin=end;
 }
 for(auto [guest,shard]:published)result.entries.push_back({guest,shard});for(auto& [_,page]:used_pages)result.pages.push_back(page);
 if(fs::exists(output/"directory.bin"))Preserve(output/"directory.bin");
 Publish(output/"directory.bin",Encode(result));
 std::printf("{\"event\":\"done\",\"backend\":\"fex\",\"requested\":%zu,\"compiled\":%llu,\"rejected\":%llu,\"reused_shards\":%llu,\"compiled_shards\":%llu,\"cached_entries\":%zu,\"shards\":%zu,\"guest_executed\":false}\n",entries.size(),(unsigned long long)compiled,(unsigned long long)rejected,(unsigned long long)reused,(unsigned long long)batches,result.entries.size(),result.shards.size());
 return result.entries.size();
}
}
