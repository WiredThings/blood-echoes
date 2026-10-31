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
int main(int argc,char**argv){
 setvbuf(stdout,nullptr,_IONBF,0);assert(argc==4);const std::string mode=argv[1];const bool generate=mode=="generate";
 fs::path root=argv[2];auto address=uintptr_t(std::strtoull(argv[3],nullptr,0));
 auto acquired=AetherPS4::Fex::FexConfigLease::Acquire();assert(!std::holds_alternative<AetherPS4::Fex::EngineFailure>(acquired));
 FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLECODECACHINGWIP,generate?"1":"0");FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLELAZYCODECACHINGWIP,"0");FEXCore::Config::Set(FEXCore::Config::CONFIG_ENABLECODECACHEVALIDATION,"0");
 auto ctx=FEXCore::Context::Context::CreateNewContext(FEX::FetchHostFeatures());assert(ctx);
 void* memory=mmap(reinterpret_cast<void*>(address),32768,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(memory!=MAP_FAILED&&uintptr_t(memory)==address);
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
  std::vector<uint64_t> seeds{0,resume,0x600,0x700,8192};std::vector<std::byte> plan(16+seeds.size()*8);memcpy(plan.data(),"BFXPLAN1",8);uint64_t count=seeds.size();memcpy(plan.data()+8,&count,8);memcpy(plan.data()+16,seeds.data(),seeds.size()*8);BbFexDisk::Publish(root/"plan.bin",plan);
  auto compiled=BbFexDisk::Generate(*impl,t,root/"plan.bin",root,identity,f.sections,address,32768,f.file);assert(compiled&&calls==0&&impl->LatestOffset==0);
  std::printf("FEX_PREPARED authored generated entries=%llu guest_executed=0\n",(unsigned long long)compiled);
 }else{
  if(mode=="reject-key")identity.bridge_abi[0]^=1;
  bool admitted=true;try{handler->PreparedDisk=std::make_unique<BbFexDisk::Loader>(root,identity,f.sections,address,32768);}catch(const std::exception&){admitted=false;}
  if(mode=="reject-key"||mode=="reject-index")assert(!admitted);
  else assert(admitted);
  assert(impl->LatestOffset==0&&t->LookupCache->FindBlock(t,address)==0); // no eager payload copy
  if(mode=="load"){
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
   std::unique_lock invalidation{ctx->GetCodeInvalidationMutex()};std::unique_lock held{impl->CodeBufferWriteMutex};std::atomic<bool> finished{};uintptr_t prepared{};
   std::thread io([&]{prepared=handler->PreparedDisk->Load(t,address);finished=true;});
   const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(3);
   while(!handler->PreparedDisk->ProofBytes()&&!finished&&std::chrono::steady_clock::now()<until)std::this_thread::sleep_for(std::chrono::milliseconds(1));
   const bool verified_before_writer=handler->PreparedDisk->ProofBytes()>0&&!finished;held.unlock();invalidation.unlock();io.join();assert(verified_before_writer&&prepared);
  }
  auto execute=[&](uint64_t off,uint64_t expected){
   AetherPS4::Fex::BridgeSyscallHandler::InvocationState call;AetherPS4::Fex::BridgeSyscallHandler::InvocationScope invoke(*handler,call,t);AetherPS4::Fex::FexExecutionSignalScope signal(*ctx,t,&signals,handler.get());
   t->CurrentFrame->State.rip=address+off;t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSP]=rsp;ctx->ExecuteThread(t);assert(!handler->FailureResult(call)&&t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RAX]==expected);
  };
  execute(0,22);execute(8192,57);assert(calls==1);
  if(mode=="load-file-changed"){
   auto d=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);
   auto e=std::find_if(d.entries.begin(),d.entries.end(),[](auto e){return e.guest==8192;});assert(e!=d.entries.end());auto name=root/BbFexDisk::Filename(d.shards[e->shard].seeds);
   const int fd=open(name.c_str(),O_RDWR|O_NOFOLLOW|O_CLOEXEC);assert(fd>=0);unsigned char byte{};const auto at=off_t(d.shards[e->shard].file_bytes-1);assert(pread(fd,&byte,1,at)==1);byte^=1;assert(pwrite(fd,&byte,1,at)==1&&!fsync(fd));close(fd);
   {FEXCore::Allocator::ScopedJITWriteProtect write;ctx->ClearCodeCache(t);}execute(8192,57);assert(handler->PreparedDisk->WholeReads()==3);
   std::puts("FEX_PREPARED changed-file invalidates-proof=1 full-revalidation=1 safe-own-JIT=1");
  }
  if(mode=="load"){
   // Validate the original emitted unit boundaries, not inferred guest spans.
   const auto directory=BbFexDisk::Decode(BbFexDisk::Read(root/"directory.bin",96u<<20),BbCpuCache::Key(identity),32768);
   for(const auto& shard:directory.shards){
    auto file=BbFexDisk::Read(root/BbFexDisk::Filename(shard.seeds),BbCpuCache::MaxPayload+4096);auto raw=std::span<const std::byte>(file).subspan(4096);
    auto checked=BbCacheValidation::Validate(raw,{32768,f.sections,GIT_HASH});assert(checked);auto bytes=raw.subspan(checked->code_offset,checked->code_bytes);
    auto units=BbFexDisk::CompiledUnits(bytes,*checked);assert(!units.empty());
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
   execute(0,22);execute(8192,57);assert(impl->LatestOffset==before);
   const auto reads_before_unit=handler->PreparedDisk->ReadBytes();execute(0x700,85);assert(handler->PreparedDisk->ReadBytes()-reads_before_unit==216); // two 108-byte units, not two full shards
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
   assert(handler->PreparedDisk->WholeReads()==2&&handler->PreparedDisk->ProofBytes()<=BbFexDisk::ShardProofs::MaxBytes);
   std::puts("FEX_PREPARED authored normal-budget=1 relocated=1 actual-HLE=1 demand-copy=1 warm-no-codegen=1 backpatch=1 clear=1 changed-page-own-JIT=1 unit-boundary-negatives=1 unit-fits-shard-does-not=1 own-JIT-budget-fallback=1");
  }else std::printf("FEX_PREPARED authored refusal-safe-own-JIT=1 mode=%s\n",mode.c_str());
 }
 handler->UnregisterThread(t);ctx->DestroyThread(t);ctx.reset();munmap(memory,32768);AetherPS4::Fex::FexConfigLease::Release();
}
