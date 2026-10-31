// Real FEX compiler/dispatcher/relocator and real existing HLE bridge. Authored
// x86 bytes only. Generation and consumption are separate macOS processes.
#include AETHER_ENGINE_SOURCE
#include "core/guest_cpu/fex_hle_bridge.h"
#include "cpu_cache_identity_inputs.h"
#include <FEXCore/HLE/SourcecodeResolver.h>
#include <cassert>
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
 BbCpuCache::Identity identity;identity.runtime_build=CacheFixture::Text("actual-FEX-fixture-v1");identity.patched_code=BbCpuCache::Hash({reinterpret_cast<const std::byte*>(code),32768});identity.module_set=CacheFixture::Text("authored-main");identity.bridge_abi=CacheFixture::Text("prepared.sum/u64/u64/u64/"+std::to_string(op));identity.host_features=CacheFixture::Features(impl->HostFeatures);identity.codegen_config=CacheFixture::ContextConfig(*impl);assert(BbCpuCache::Valid(identity));
 const uint64_t rsp=f.stack+f.stack_bytes-16;auto*t=ctx->CreateThread(address,rsp);assert(t);
 AetherPS4::Fex::CallRetStack returns;assert(returns.IsReserved()&&!std::holds_alternative<AetherPS4::Fex::EngineFailure>(returns.MakeWritable()));returns.Initialize(t);AetherPS4::Fex::GuestSegmentState segments;segments.Initialize(t->CurrentFrame->State);
 std::vector<Core::GuestExecutionRange> ranges{{address,32768,true,false},{f.stack,f.stack_bytes,false,true}};assert(!std::holds_alternative<AetherPS4::Fex::EngineFailure>(handler->RegisterThread(t,ranges)));
 if(generate){
  std::vector<uint64_t> seeds{0,resume,0x600,8192};std::vector<std::byte> plan(16+seeds.size()*8);memcpy(plan.data(),"BFXPLAN1",8);uint64_t count=seeds.size();memcpy(plan.data()+8,&count,8);memcpy(plan.data()+16,seeds.data(),seeds.size()*8);BbFexDisk::Publish(root/"plan.bin",plan);
  auto compiled=BbFexDisk::Generate(*impl,t,root/"plan.bin",root,identity,f.sections,address,32768,f.file);assert(compiled&&calls==0&&impl->LatestOffset==0);
  std::printf("FEX_PREPARED authored generated entries=%llu guest_executed=0\n",(unsigned long long)compiled);
 }else{
  if(mode=="reject-key")identity.bridge_abi[0]^=1;
  bool admitted=true;try{handler->PreparedDisk=std::make_unique<BbFexDisk::Loader>(root,identity,f.sections,address,32768);}catch(const std::exception&){admitted=false;}
  if(mode=="reject-key"||mode=="reject-index")assert(!admitted);
  else assert(admitted);
  assert(impl->LatestOffset==0&&t->LookupCache->FindBlock(t,address)==0); // no eager payload copy
  auto execute=[&](uint64_t off,uint64_t expected){
   AetherPS4::Fex::BridgeSyscallHandler::InvocationState call;AetherPS4::Fex::BridgeSyscallHandler::InvocationScope invoke(*handler,call,t);AetherPS4::Fex::FexExecutionSignalScope signal(*ctx,t,&signals,handler.get());
   t->CurrentFrame->State.rip=address+off;t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RSP]=rsp;ctx->ExecuteThread(t);assert(!handler->FailureResult(call)&&t->CurrentFrame->State.gregs[FEXCore::X86State::REG_RAX]==expected);
  };
  execute(0,22);execute(8192,57);assert(calls==1);
  if(mode=="load"){
   assert(handler->PreparedDisk->Hits()>=2);const auto before=impl->LatestOffset;
   execute(0,22);execute(8192,57);assert(impl->LatestOffset==before);
   execute(0x600,85);const auto linked=impl->LatestOffset;execute(0x600,85);assert(impl->LatestOffset==linked);
   // Existing whole-buffer clear removes both cached and newly JIT'd links.
   {FEXCore::Allocator::ScopedJITWriteProtect write;ctx->ClearCodeCache(t);}assert(!t->LookupCache->FindBlock(t,address)&&!t->LookupCache->FindBlock(t,address+0x6600));
   code[0x6601]=102;execute(0x600,102);
   // Existing precise invalidation must not resurrect old source-page bytes.
   {std::scoped_lock lock{ctx->GetCodeInvalidationMutex()};ctx->InvalidateCodeBuffersCodeRange(address,16384);ctx->InvalidateThreadCachedCodeRange(t,address,16384);}
   code[1]=9;execute(0,24);
   std::puts("FEX_PREPARED authored normal-budget=1 relocated=1 actual-HLE=1 demand-copy=1 warm-no-codegen=1 backpatch=1 clear=1 changed-page-own-JIT=1");
  }else std::printf("FEX_PREPARED authored refusal-safe-own-JIT=1 mode=%s\n",mode.c_str());
 }
 handler->UnregisterThread(t);ctx->DestroyThread(t);ctx.reset();munmap(memory,32768);AetherPS4::Fex::FexConfigLease::Release();
}
