// Real FEX compiler/dispatcher/relocator and real existing HLE bridge. Authored
// x86 bytes only. Generation and consumption are separate macOS processes.
#include AETHER_ENGINE_SOURCE
#include "core/guest_cpu/fex_hle_bridge.h"
#include "cpu_cache_identity_inputs.h"
#include <FEXCore/HLE/SourcecodeResolver.h>
#include <cassert>
#include <thread>
using namespace Core::GuestCpu;
namespace fs=std::filesystem;
static unsigned calls;
static uint64_t sum(uint64_t a,uint64_t b){++calls;return a+b;}
struct Fixture{
 uintptr_t base,stack;size_t stack_bytes;FEXCore::ExecutableFileInfo file;
 std::array<std::pair<uint64_t,uint64_t>,2> sections{{{0,4096},{8192,12288}}};
 static bool Valid(void* ctx,uintptr_t a,size_t n,bool writable){auto& f=*static_cast<Fixture*>(ctx);return n&&((a>=f.stack&&a-f.stack<f.stack_bytes&&n<=f.stack_bytes-(a-f.stack))||(!writable&&a>=f.base&&a-f.base<32768&&n<=32768-(a-f.base)));}
 static std::optional<Core::GuestExecutionRange> Executable(void* ctx,uintptr_t at){auto& f=*static_cast<Fixture*>(ctx);if(at>=f.base&&at-f.base<32768)return Core::GuestExecutionRange{f.base,32768,true,false};return {};}
 static std::optional<FEXCore::ExecutableFileSectionInfo> Source(void* ctx,uintptr_t at){auto& f=*static_cast<Fixture*>(ctx);for(auto [lo,hi]:f.sections)if(at>=f.base+lo&&at<f.base+hi)return FEXCore::ExecutableFileSectionInfo{f.file,f.base,f.base+lo,f.base+hi};return {};}
};
static std::shared_ptr<BbFexDisk::VerifiedShard> SelectedCatalog(const fs::path& root,const BbFexDisk::Shard& meta,const BbFexDisk::Digest& key,const BbCacheValidation::Policy& policy,uint64_t guest){
 auto bytes=BbFexDisk::Read(root/BbFexDisk::CatalogFilename(meta.catalog_hash),16u<<20);
 if(!meta.catalog_root_bytes)return BbFexDisk::DecodeCatalog(bytes,meta,key,policy);
 auto proof=BbFexDisk::DecodePagedRoot(std::span(bytes).first(meta.catalog_root_bytes),meta,key,policy);const auto i=proof->Select(guest);const auto& range=proof->catalog_ranges[i];return BbFexDisk::DecodePagedUnit(std::span(bytes).subspan(range.offset,range.bytes),*proof,i,meta,key,policy);
}
int main(int argc,char**argv){
 setvbuf(stdout,nullptr,_IONBF,0);assert(argc==4);const std::string mode=argv[1];const bool generate=mode=="generate"||mode=="generate-legacy"||mode=="generate-flat"||mode=="upgrade"||mode=="upgrade-metadata-only",load=mode=="load"||mode=="load-legacy"||mode=="load-flat";
 fs::path root=argv[2];auto address=uintptr_t(std::strtoull(argv[3],nullptr,0));
 auto acquired=AetherPS4::Fex::FexConfigLease::Acquire();assert(!std::holds_alternative<AetherPS4::Fex::EngineFailure>(acquired));
 FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLECODECACHINGWIP,generate?"1":"0");FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLELAZYCODECACHINGWIP,"0");FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLECODECACHEVALIDATION,"0");
 auto ctx=FEXCore::Context::Context::CreateNewContext(FEX::FetchHostFeatures());assert(ctx);
 void* memory=mmap(reinterpret_cast<void*>(address),32768,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);if(memory==MAP_FAILED||uintptr_t(memory)!=address)std::fprintf(stderr,"authored mapping requested=%p actual=%p errno=%d\n",reinterpret_cast<void*>(address),memory,errno);assert(memory!=MAP_FAILED&&uintptr_t(memory)==address);
 AetherPS4::Fex::Mapping stack(16384,PROT_READ|PROT_WRITE);assert(stack.IsValid());
 Fixture f{address,uintptr_t(stack.Get()),16384};f.file.FileId=0x1234;f.file.Filename="authored-fex-prepared-blocks";
 HleCallRegistry registry;auto operation=registry.Register(MakeHleCallAdapter(sum),"prepared.sum");
 HleGuestBridge bridge(registry,Fixture::Valid,&f,nullptr,nullptr,Fixture::Executable,&f,Fixture::Source,&f);
 AetherPS4::Fex::BridgeSignalDelegator signals(0);auto handler=fextl::make_unique<AetherPS4::Fex::BridgeSyscallHandler>(bridge);
 ctx->SetSyscallHandler(handler.get());ctx->SetSignalDelegator(&signals);ctx->EnableExitOnHLT();assert(ctx->InitCore());
 auto* impl=static_cast<FEXCore::Context::ContextImpl*>(ctx.get());
 auto* code=static_cast<unsigned char*>(memory);memset(code,0xf4,32768);
 std::vector<unsigned char> first{0xbf,7,0,0,0,0xbe,5,0,0,0,0x48,0xb8};uint64_t op=operation->Operation();auto size=first.size();first.resize(size+8);memcpy(first.data()+size,&op,8);first.insert(first.end(),{0x0f,0x05});const size_t resume=first.size();first.insert(first.end(),{0x48,0x83,0xc0,10,0xf4});memcpy(code,first.data(),first.size());
 const unsigned char second[]={0x48,0x8d,0x05,25,0,0,0,0x8b,0,0xf4};memcpy(code+8192,second,sizeof(second));uint32_t data=57;memcpy(code+8192+32,&data,4);
 // This target is intentionally absent from the prepared catalogue. Cached
 // source block +600 jumps to it, exercising ordinary live direct backpatches.
 const unsigned char source[]={0xe9,0xfb,0x5f,0,0};memcpy(code+0x600,source,sizeof(source));const unsigned char target[]={0xb8,85,0,0,0,0xf4};memcpy(code+0x6600,target,sizeof(target));
 const unsigned char prepared_source[]={0xe9,0xfb,0xfe,0xff,0xff};memcpy(code+0x700,prepared_source,sizeof(prepared_source)); // independently moved unit -> another cached unit in SAME shard
 BbCpuCache::Identity identity;identity.runtime_build=CacheFixture::Text("actual-FEX-fixture-v1");identity.patched_code=BbCpuCache::Hash({reinterpret_cast<const std::byte*>(code),32768});identity.module_set=CacheFixture::Text("authored-main");identity.bridge_abi=CacheFixture::Text("prepared.sum/u64/u64/u64/"+std::to_string(op));identity.host_features=CacheFixture::Features(impl->HostFeatures);identity.codegen_config=CacheFixture::ContextConfig(*impl);assert(BbCpuCache::Valid(identity));
 const uint64_t rsp=f.stack+f.stack_bytes-16;auto*t=ctx->CreateThread(address,rsp);assert(t);
 AetherPS4::Fex::CallRetStack returns;assert(returns.IsReserved()&&!std::holds_alternative<AetherPS4::Fex::EngineFailure>(returns.MakeWritable()));returns.Initialize(t);AetherPS4::Fex::GuestSegmentState segments;segments.Initialize(t->CurrentFrame->State);
 std::vector<Core::GuestExecutionRange> ranges{{address,32768,true,false},{f.stack,f.stack_bytes,false,true}};assert(!std::holds_alternative<AetherPS4::Fex::EngineFailure>(handler->RegisterThread(t,ranges)));
 if(generate){
  std::vector<uint64_t> seeds{0,resume,0x600,0x700,8192};std::vector<std::byte> plan(16+seeds.size()*8);memcpy(plan.data(),"BFXPLAN1",8);uint64_t count=seeds.size();memcpy(plan.data()+8,&count,8);memcpy(plan.data()+16,seeds.data(),seeds.size()*8);
  if(fs::exists(root/"plan.bin"))assert(BbFexDisk::Read(root/"plan.bin",4096)==plan);else BbFexDisk::Publish(root/"plan.bin",plan);
  std::map<fs::path,BbCpuCache::Digest> prior;for(auto& p:fs::directory_iterator(root))if(p.path().extension()==".becc"&&mode!="upgrade-metadata-only")prior.emplace(p.path(),BbCpuCache::Hash(BbFexDisk::Read(p.path(),BbCpuCache::MaxPayload+4096)));
  auto compiled=BbFexDisk::Generate(*impl,t,root/"plan.bin",root,identity,f.sections,address,32768,f.file);assert(compiled&&calls==0&&impl->LatestOffset==0);
  for(const auto& [p,hash]:prior)assert(BbCpuCache::Hash(BbFexDisk::Read(p,BbCpuCache::MaxPayload+4096))==hash);
  if(mode=="generate-legacy"||mode=="generate-flat"){
   auto d=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);
   if(mode=="generate-flat")for(auto& meta:d.shards){auto file=BbFexDisk::Read(root/BbFexDisk::Filename(meta.seeds),BbCpuCache::MaxPayload+4096);auto checked=BbCacheValidation::Validate(std::span(file).subspan(4096),{32768,f.sections,GIT_HASH});assert(checked);auto proof=BbFexDisk::MakeCatalog(file,*checked);auto data=BbFexDisk::EncodeCatalog(proof,meta,d.key,32768,GIT_HASH);meta.catalog_bytes=data.size();meta.catalog_root_bytes=0;meta.catalog_hash=BbCpuCache::Hash(data);BbFexDisk::Publish(root/BbFexDisk::CatalogFilename(meta.catalog_hash),data);}
   auto raw=BbFexDisk::Encode(d);const auto stride=mode=="generate-flat"?120:80;std::vector<std::byte> old(raw.begin(),raw.begin()+72);old[7]=std::byte(mode=="generate-flat"?'2':'1');
   for(size_t i=0;i<d.shards.size();++i)old.insert(old.end(),raw.begin()+72+128*i,raw.begin()+72+128*i+stride);
   old.insert(old.end(),raw.begin()+72+128*d.shards.size(),raw.end()-32);auto sum=BbCpuCache::Hash(old);auto bytes=std::as_bytes(std::span(sum));old.insert(old.end(),bytes.begin(),bytes.end());BbFexDisk::Preserve(root/"directory.bin");BbFexDisk::Publish(root/"directory.bin",old);
  }
  std::printf("FEX_PREPARED authored generated entries=%llu guest_executed=0\n",(unsigned long long)compiled);
 }else{
  if(mode=="reject-key")identity.bridge_abi[0]^=1;
  if(mode=="load-bad-catalog"||mode=="load-missing-catalog"||mode=="load-other-metadata-corrupt"){
   auto d=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);const auto name=root/BbFexDisk::CatalogFilename(d.shards.front().catalog_hash);
   if(mode=="load-other-metadata-corrupt"){
    auto bytes=BbFexDisk::Read(name,16u<<20);const auto& meta=d.shards.front();auto root_proof=BbFexDisk::DecodePagedRoot(std::span(bytes).first(meta.catalog_root_bytes),meta,d.key,{32768,f.sections,GIT_HASH});const auto target=root_proof->Select(0x600);assert(target!=root_proof->Select(0));const auto at=root_proof->catalog_ranges[target].offset;const int fd=open(name.c_str(),O_RDWR|O_NOFOLLOW);assert(fd>=0);unsigned char byte=0;assert(pwrite(fd,&byte,1,at)==1&&!fsync(fd));close(fd);
   }else if(mode=="load-missing-catalog")fs::rename(name,fs::path(name.string()+".preserved-for-test"));
   else{const int fd=open(name.c_str(),O_RDWR|O_NOFOLLOW|O_CLOEXEC);assert(fd>=0);unsigned char value=0;assert(pwrite(fd,&value,1,0)==1&&!fsync(fd));close(fd);}
  }
  bool admitted=true;try{handler->PreparedDisk=std::make_unique<BbFexDisk::Loader>(root,identity,f.sections,address,32768);}catch(const std::exception&){admitted=false;}
  if(mode=="reject-key"||mode=="reject-index")assert(!admitted);
  else assert(admitted);
  assert(impl->LatestOffset==0&&t->LookupCache->FindBlock(t,address)==0); // no eager payload copy
  if(load){
   // Descriptor-bound range reads retain the original ownership/no-link rules
   // and detect writes between admission and consumption.
   const auto name="range-io-"+std::to_string(getpid())+".bin";const auto link=name+".link";std::array<std::byte,8> bytes{};BbFexDisk::Publish(root/name,bytes);BbFexDisk::OwnedDirectory dir(root);
   auto reject=[](auto operation){bool caught=false;try{operation();}catch(const std::exception&){caught=true;}assert(caught);};
   reject([&]{dir.Open("../escape",16);});fs::create_symlink(root/name,root/link);reject([&]{dir.Open(link,16);});fs::remove(root/link);
   fs::create_hard_link(root/name,root/link);reject([&]{dir.Open(name,16);});fs::remove(root/link);
   {auto file=dir.Open(name,16);auto old=file.OpenedStamp();const int fd=open((root/name).c_str(),O_WRONLY);assert(fd>=0);unsigned char value=1;assert(pwrite(fd,&value,1,0)==1&&!fsync(fd));close(fd);reject([&]{file.ReadRange(0,8,old);});}
   fs::remove(root/name);
   BbFexDisk::ShardProofs bounded;for(size_t i=0;i<512;++i)bounded.Put(i,std::make_shared<BbFexDisk::VerifiedShard>());assert(bounded.Bytes()<=BbFexDisk::ShardProofs::MaxBytes&&bounded.Churn()[0]>0);
   assert(!bounded.Find(0,{}));auto newest=bounded.Find(511,{});assert(newest);auto changed=newest->stamp;changed.value.st_size=1;assert(!bounded.Find(511,changed)&&bounded.Churn()[1]==1);
   // Hold the real JIT write lock: complete disk read + checksum + structural
   // admission must finish on another thread before it can publish any code.
   auto* second_thread=ctx->CreateThread(address+8192,rsp);assert(second_thread);
   std::unique_lock invalidation{ctx->GetCodeInvalidationMutex()};std::unique_lock held{impl->CodeBufferWriteMutex};std::atomic<bool> finished{},second_finished{};uintptr_t prepared{},second_prepared{};
   std::thread io([&]{prepared=handler->PreparedDisk->Load(t,address);finished=true;});
   const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(3);
   while(!handler->PreparedDisk->ProofBytes()&&!finished&&std::chrono::steady_clock::now()<until)std::this_thread::sleep_for(std::chrono::milliseconds(1));
   const bool verified_before_writer=handler->PreparedDisk->ProofBytes()>0&&!finished;
   const auto first_proof_bytes=handler->PreparedDisk->ProofBytes();
   std::thread other([&]{second_prepared=handler->PreparedDisk->Load(second_thread,address+8192);second_finished=true;});
   const auto second_until=std::chrono::steady_clock::now()+std::chrono::seconds(3);
   while(handler->PreparedDisk->ProofBytes()<=first_proof_bytes&&!second_finished&&std::chrono::steady_clock::now()<second_until)std::this_thread::sleep_for(std::chrono::milliseconds(1));
   const bool second_verified_before_writer=handler->PreparedDisk->ProofBytes()>first_proof_bytes&&!second_finished;
   held.unlock();invalidation.unlock();io.join();other.join();assert(verified_before_writer&&second_verified_before_writer&&prepared&&second_prepared);ctx->DestroyThread(second_thread);
  }
  auto execute=[&](uint64_t off,uint64_t expected){
   AetherPS4::Fex::BridgeSyscallHandler::InvocationState call;AetherPS4::Fex::BridgeSyscallHandler::InvocationScope invoke(*handler,call,t);AetherPS4::Fex::FexExecutionSignalScope signal(*ctx,t,&signals,handler.get());
   t->CurrentFrame->State.rip=address+off;t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSP]=rsp;ctx->ExecuteThread(t);assert(!handler->FailureResult(call)&&t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RAX]==expected);
  };
  execute(0,22);execute(8192,57);assert(calls==1);
  if(mode=="load-other-metadata-corrupt"){assert(handler->PreparedDisk->Hits()==2);execute(0x600,85);assert(handler->PreparedDisk->Hits()==2);}
  if(mode=="load-bad-catalog"||mode=="load-missing-catalog")assert(handler->PreparedDisk->Hits()==1&&handler->PreparedDisk->WholeReads()==0);
  if(mode=="load-file-changed"){
   auto d=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);
   auto e=std::find_if(d.entries.begin(),d.entries.end(),[](auto e){return e.guest==8192;});assert(e!=d.entries.end());auto name=root/BbFexDisk::Filename(d.shards[e->shard].seeds);
   const auto& meta=d.shards[e->shard];auto proof=SelectedCatalog(root,meta,d.key,{32768,f.sections,GIT_HASH},8192);const auto& unit=proof->units[proof->Select(8192)];
   const int fd=open(name.c_str(),O_RDWR|O_NOFOLLOW|O_CLOEXEC);assert(fd>=0);unsigned char byte{};const auto at=off_t(4096+proof->metadata.code_offset+unit.begin+sizeof(FEXCore::CPU::CPUBackend::JITCodeHeader));assert(pread(fd,&byte,1,at)==1);byte^=1;assert(pwrite(fd,&byte,1,at)==1&&!fsync(fd));close(fd);
   {FEXCore::Allocator::ScopedJITWriteProtect write;ctx->ClearCodeCache(t);}execute(8192,57);assert(handler->PreparedDisk->WholeReads()==0);
   std::puts("FEX_PREPARED changed-file invalidates-proof=1 unit-revalidation=1 safe-own-JIT=1");
  }
  if(load){
   // Validate the original emitted unit boundaries, not inferred guest spans.
   const auto directory=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);
   for(const auto& shard:directory.shards){
    auto file=BbFexDisk::Read(root/BbFexDisk::Filename(shard.seeds),BbCpuCache::MaxPayload+4096);auto raw=std::span<const std::byte>(file).subspan(4096);
    auto checked=BbCacheValidation::Validate(raw,{32768,f.sections,GIT_HASH});assert(checked);auto bytes=raw.subspan(checked->code_offset,checked->code_bytes);
    auto units=BbFexDisk::CompiledUnits(bytes,*checked);assert(!units.empty());
    if(shard.catalog_bytes){
     auto full=BbFexDisk::MakeCatalog(file,*checked);auto catalog=BbFexDisk::EncodeCatalog(full,shard,directory.key,32768,GIT_HASH);auto flat=shard;flat.catalog_bytes=catalog.size();flat.catalog_hash=BbCpuCache::Hash(catalog);flat.catalog_root_bytes=0;auto proof=BbFexDisk::DecodeCatalog(catalog,flat,directory.key,{32768,f.sections,GIT_HASH});assert(proof->units.size()==units.size());
     auto reject_catalog=[&](std::vector<std::byte> data,bool rehash){auto meta=flat;if(rehash){meta.catalog_bytes=data.size();meta.catalog_hash=BbCpuCache::Hash(data);}bool caught=false;try{BbFexDisk::DecodeCatalog(data,meta,directory.key,{32768,f.sections,GIT_HASH});}catch(const std::exception&){caught=true;}assert(caught);};
     auto changed=catalog;changed.back()^=std::byte{1};reject_catalog(changed,false);
     changed=catalog;changed[8]^=std::byte{1};reject_catalog(changed,true); // incompatible full identity
     changed=catalog;changed[72]^=std::byte{1};reject_catalog(changed,true); // compiler identity, even with a valid outer digest
     changed=catalog;uint64_t outside=32768;memcpy(changed.data()+148,&outside,8);reject_catalog(changed,true); // guest entry outside registered code
     changed=catalog;changed.push_back(std::byte{});reject_catalog(changed,true);changed=catalog;changed.resize(147);reject_catalog(changed,true);
    }
    if(shard.catalog_root_bytes){
     auto file=BbFexDisk::Read(root/BbFexDisk::CatalogFilename(shard.catalog_hash),16u<<20);auto prefix=std::span(file).first(shard.catalog_root_bytes);auto routing=BbFexDisk::DecodePagedRoot(prefix,shard,directory.key,{32768,f.sections,GIT_HASH});
     auto reject_root=[&](std::vector<std::byte> data,bool rehash){auto meta=shard;if(rehash){meta.catalog_root_bytes=data.size();meta.catalog_hash=BbCpuCache::Hash(data);}bool caught=false;try{BbFexDisk::DecodePagedRoot(data,meta,directory.key,{32768,f.sections,GIT_HASH});}catch(const std::exception&){caught=true;}assert(caught);};
     auto changed=std::vector<std::byte>(prefix.begin(),prefix.end());changed.back()^=std::byte{1};reject_root(changed,false);
     for(size_t field:{size_t(8),size_t(72)}){changed.assign(prefix.begin(),prefix.end());changed[field]^=std::byte{255};reject_root(changed,true);}
     changed.assign(prefix.begin(),prefix.end());uint64_t outside_guest=32768;memcpy(changed.data()+148,&outside_guest,8);reject_root(changed,true);
     changed.assign(prefix.begin(),prefix.end());uint64_t bad=file.size()+8;const auto descriptors=148+16*routing->catalog_routes.size();memcpy(changed.data()+descriptors+24,&bad,8);reject_root(changed,true);
     changed.assign(prefix.begin(),prefix.end());changed.push_back(std::byte{});reject_root(changed,true);
     for(size_t i=0;i<routing->units.size();++i){auto range=routing->catalog_ranges[i];auto raw=std::span(file).subspan(range.offset,range.bytes);auto loaded=BbFexDisk::DecodePagedUnit(raw,*routing,i,shard,directory.key,{32768,f.sections,GIT_HASH});assert(loaded->units.size()==1);
      auto bytes=std::vector<std::byte>(raw.begin(),raw.end());bytes.back()^=std::byte{1};bool caught=false;try{BbFexDisk::DecodePagedUnit(bytes,*routing,i,shard,directory.key,{32768,f.sections,GIT_HASH});}catch(const std::exception&){caught=true;}assert(caught);
      if(routing->units.size()>1){const auto other=(i+1)%routing->units.size();auto wrong=*routing;wrong.catalog_ranges[i]=routing->catalog_ranges[other];const auto& ext=wrong.catalog_ranges[i];caught=false;try{BbFexDisk::DecodePagedUnit(std::span(file).subspan(ext.offset,ext.bytes),wrong,i,shard,directory.key,{32768,f.sections,GIT_HASH});}catch(const std::exception&){caught=true;}assert(caught);}
     }
    }
    auto reject=[&](std::vector<std::byte> changed,BbCacheValidation::Validated metadata){bool caught=false;try{(void)BbFexDisk::CompiledUnits(changed,metadata);}catch(const std::exception&){caught=true;}assert(caught);};
    auto changed=std::vector<std::byte>(bytes.begin(),bytes.end());uint32_t outside=0xfffffff8;memcpy(changed.data(),&outside,4);reject(changed,*checked);
    changed.assign(bytes.begin(),bytes.end());size_t huge=bytes.size()+1;memcpy(changed.data()+units[0].code_end,&huge,sizeof(huge));reject(changed,*checked);
    auto metadata=*checked;metadata.blocks[0].host=units[0].code_end;reject({bytes.begin(),bytes.end()},metadata);
    metadata=*checked;metadata.relocs.push_back({units[0].end-4,0,0,0});reject({bytes.begin(),bytes.end()},metadata);
    changed.assign(bytes.begin(),bytes.end());changed[units[0].code_end+offsetof(FEXCore::CPU::CPUBackend::JITCodeTail,SingleInst)]=std::byte{2};reject(changed,*checked);
   }
   assert(handler->PreparedDisk->Hits()>=2);const auto before=impl->LatestOffset;
   assert(handler->PreparedDisk->CopiedBytes()<4096); // no whole-shard payload inflation
   assert(!t->LookupCache->FindBlock(t,address+0x600)); // same shard, unused unit stays unloaded
   const auto warm_reads=handler->PreparedDisk->ReadBytes(),warm_catalogs=handler->PreparedDisk->CatalogReads();
   execute(0,22);execute(8192,57);assert(impl->LatestOffset==before&&handler->PreparedDisk->ReadBytes()==warm_reads&&handler->PreparedDisk->CatalogReads()==warm_catalogs);
   const bool paged=directory.shards.front().catalog_root_bytes;const auto reads_before_unit=paged?handler->PreparedDisk->NativeBytes():handler->PreparedDisk->ReadBytes();execute(0x700,85);assert((paged?handler->PreparedDisk->NativeBytes():handler->PreparedDisk->ReadBytes())-reads_before_unit==216); // two 108-byte units, not two full shards
   execute(0x600,85);const auto linked=impl->LatestOffset;execute(0x700,85);execute(0x600,85);assert(impl->LatestOffset==linked);
   // Existing whole-buffer clear removes both cached and newly JIT'd links.
   {FEXCore::Allocator::ScopedJITWriteProtect write;ctx->ClearCodeCache(t);}assert(!t->LookupCache->FindBlock(t,address)&&!t->LookupCache->FindBlock(t,address+0x6600));
   code[0x6601]=102;execute(0x600,102);
   // Existing precise invalidation must not resurrect old source-page bytes.
   {std::scoped_lock lock{ctx->GetCodeInvalidationMutex()};ctx->InvalidateCodeBuffersCodeRange(address,16384);ctx->InvalidateThreadCachedCodeRange(t,address,16384);}
   code[1]=9;execute(0,24);
   // Model an almost-full ORIGINAL buffer without changing its real capacity:
   // a 136-byte unit fits while its 4096-byte shard does not. Then verify an
   // actually insufficient remainder returns to the original JIT/rotation path.
   assert(!t->LookupCache->FindBlock(t,address+8192));auto buffer=impl->GetLatest();impl->LatestOffset=buffer->UsableSize()-256;
   const auto copied=handler->PreparedDisk->CopiedBytes();assert(handler->PreparedDisk->Load(t,address+8192));assert(handler->PreparedDisk->CopiedBytes()-copied==136);execute(8192,57);
   {FEXCore::Allocator::ScopedJITWriteProtect write;ctx->ClearCodeCache(t);}buffer=impl->GetLatest();impl->LatestOffset=buffer->UsableSize()-32;
   assert(!handler->PreparedDisk->Load(t,address+8192));execute(8192,57);
   assert(handler->PreparedDisk->WholeReads()==(mode=="load-legacy"?2:0)&&handler->PreparedDisk->ProofBytes()<=BbFexDisk::ShardProofs::MaxBytes);
   std::puts("FEX_PREPARED authored normal-budget=1 relocated=1 actual-HLE=1 demand-copy=1 warm-no-codegen=1 backpatch=1 clear=1 changed-page-own-JIT=1 unit-boundary-negatives=1 unit-fits-shard-does-not=1 own-JIT-budget-fallback=1");
  }else std::printf("FEX_PREPARED authored refusal-safe-own-JIT=1 mode=%s\n",mode.c_str());
 }
 handler->UnregisterThread(t);ctx->DestroyThread(t);ctx.reset();munmap(memory,32768);AetherPS4::Fex::FexConfigLease::Release();
}
