// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_backend.h"
#include "bbcpu.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <sys/mman.h>
#include <atomic>
#include <thread>
using namespace Core::GuestCpu;
extern "C" {
uintptr_t bb_image_base;
int bbcpu_is_guest_code_span(uint64_t,size_t);
void* runtime_low_map(size_t size,int prot){
 auto* p=mmap(reinterpret_cast<void*>(0x6000000000ull),size,prot,MAP_PRIVATE|MAP_ANON,-1,0);
 return p==MAP_FAILED?nullptr:p;
}
}
struct Pair{uint64_t a,b;};
template<>struct Core::GuestCpu::GuestAggregate<Pair>{
 static constexpr bool enabled=true;
 static constexpr auto fields=std::tuple{GuestField<uint64_t,offsetof(Pair,a)>{},GuestField<uint64_t,offsetof(Pair,b)>{}};
};
namespace {
BbCpu::BbcpuBackend* backend;
uintptr_t sum_entry;
uint64_t Seven(uint64_t a,uint64_t b,uint64_t c,uint64_t d,uint64_t e,uint64_t f,uint64_t g){return a+b+c+d+e+f+g;}
uint64_t Nested(uint64_t n){const uint64_t args[]={n,5};auto value=backend->CallGuest(sum_entry,args);assert(std::holds_alternative<Core::GuestExecutionState>(value));return std::get<Core::GuestExecutionState>(value).Gpr[0]+7;}
Pair Swap(Pair p){return{p.b,p.a};}
double Add(double a,double b){return a+b;}
unsigned pointer_calls;
std::atomic<unsigned> queries{},sites{};
unsigned stop_destructors;
uintptr_t stop_entry;
uint64_t Stop(){struct Guard{~Guard(){++stop_destructors;}} guard;assert(bbcpu_request_execution_stop(stop_entry));return 77;}
void ForbiddenHost(){std::_Exit(99);}
uint64_t Read(const uint64_t* p){++pointer_calls;return *p;}
bool Validate(void* ctx,uintptr_t p,size_t n,bool write){
 if(!p||!n||p>UINTPTR_MAX-n)return false;
 for(const auto& r:*static_cast<std::vector<Core::GuestExecutionRange>*>(ctx))
  if(p>=r.Begin&&n<=r.Size&&p-r.Begin<=r.Size-n&&(!write||r.Writable))return true;
 return false;
}
std::optional<Core::GuestExecutionRange> Executable(void* ctx,uintptr_t p){
 ++queries;
 for(auto r:*static_cast<std::vector<Core::GuestExecutionRange>*>(ctx))
  if(r.Executable&&p>=r.Begin&&p-r.Begin<r.Size)return r;
 return{};
}
bool Site(void* ctx,uintptr_t p,size_t n){
 ++sites;
 auto r=static_cast<HleVeneerAllocator*>(ctx)->QueryExecutableRange(p);
 return r&&n<=r->Size&&p-r->Begin<=r->Size-n;
}
}
int main(int argc,char**argv){
 assert(argc==1||argc==2);
 auto* memory=static_cast<unsigned char*>(runtime_low_map(32768,PROT_READ|PROT_WRITE));assert(memory);
 bb_image_base=reinterpret_cast<uintptr_t>(memory);sum_entry=bb_image_base;
 const unsigned char sum[]={0x48,0x89,0xf8,0x48,0x01,0xf0,0xc3};std::memcpy(memory,sum,sizeof(sum));
 memory[64]=0x48;memory[65]=0xb8;auto native=argc==2&&std::string_view(argv[1])=="--unmapped-host"?UINT64_C(0x700000000020):reinterpret_cast<uint64_t>(ForbiddenHost);std::memcpy(memory+66,&native,8);memory[74]=0xff;memory[75]=0xe0;
 const unsigned char unowned[]={0x49,0x89,0xca,0x48,0xb8,1,0,0,0,0,0,0,0,0x0f,0x05,0xc3};std::memcpy(memory+96,unowned,sizeof(unowned));
 const unsigned char tail_code[]={0xff,0x25,0xfa,0x3f,0,0};std::memcpy(memory+0x100,tail_code,sizeof(tail_code));
 const unsigned char call_code[]={0xff,0x74,0x24,8,0xff,0x15,0xb6,0x3f,0,0,0x48,0x8d,0x64,0x24,8,0xc3};std::memcpy(memory+0x140,call_code,sizeof(call_code));
 const unsigned char import_stub[]={0xff,0x25,0x82,0x3f,0,0};std::memcpy(memory+0x180,import_stub,sizeof(import_stub));
 const unsigned char bad_slot[]={0xff,0x25,0xff,0xff,0xff,0x7f};std::memcpy(memory+0x1c0,bad_slot,sizeof(bad_slot));
 memory[512]=0xf4;assert(mprotect(memory,16384,PROT_READ)==0);
 HleCallRegistry registry;HleVeneerAllocator veneers;
 auto add=[&](auto fn,const char* name){auto a=registry.Register(MakeHleCallAdapter(fn),name);auto v=veneers.Allocate(*a);assert(std::holds_alternative<uint64_t>(v));return std::pair{a,std::get<uint64_t>(v)};};
 const auto seven=add(Seven,"authored.seven"),nested=add(Nested,"authored.nested"),swap=add(Swap,"authored.swap"),fp=add(Add,"authored.float"),read=add(Read,"authored.read"),stop_hle=add(Stop,"authored.stop");
 const std::string_view mode=argc==2?argv[1]:"";
 const bool positive=mode.empty()||mode=="--mutable-overlap"||mode=="--many-ranges";
 const auto owned=veneers.GetExecutableRanges();auto ranges=owned;ranges.push_back({bb_image_base,16384,true,false});ranges.push_back({bb_image_base+16384,16384,false,true});
 if(mode=="--mutable-overlap")ranges.push_back({bb_image_base,16384,false,true});
 if(mode=="--many-ranges")while(ranges.size()<257)ranges.push_back({bb_image_base,16384,true,false});
 HleCallService service(registry,Validate,&ranges,nullptr,nullptr,Executable,&ranges,"BBCPU_HLE");
 setenv("BB_RECOMP_LIB",BBCPU_TEST_LIBRARY,1);
 BbCpu::BbcpuBackend cpu(service,Site,&veneers,ranges,owned);backend=&cpu;
 bbcpu_recomp_load(bb_image_base);
 assert(bbcpu_recomp_functions()==2);
 Core::GuestExecutionRequest request;request.Rsp=bb_image_base+32768-24;request.Rflags=2;request.MappedRanges=ranges;
 const uint64_t stop=bb_image_base+512;std::memcpy(reinterpret_cast<void*>(request.Rsp),&stop,8);
 stop_entry=stop;
 auto run=[&](uint64_t entry){std::memcpy(reinterpret_cast<void*>(request.Rsp),&stop,8);request.Rip=entry;auto result=cpu.Run(request);assert(std::holds_alternative<Core::GuestExecutionState>(result));return std::get<Core::GuestExecutionState>(result);};
 auto compiled=[&](uint64_t target,bool call=false){std::memcpy(memory+0x4100,&target,8);return run(bb_image_base+(call?0x140:0x100));};
 if(!positive){
  if(mode=="--raw-host"||mode=="--unmapped-host")run(bb_image_base+64);
  else if(mode=="--unowned-syscall")compiled(bb_image_base+96);
  else if(mode=="--bad-pointer"){request.Gpr[7]=1;compiled(read.second);}
  else if(mode=="--bad-slot")compiled(bb_image_base+0x1c0);
  else if(mode=="--wrong-operation"){request.Gpr[0]=UINT64_MAX;run(seven.second+13);}
  else if(mode=="--invalidate"){bbcpu_invalidate(bb_image_base,1);}
  else return 2;
  return 98; // every refusal must terminate with the original HLE fault status
 }
 request.Gpr[7]=1;request.Gpr[6]=2;request.Gpr[2]=3;request.Gpr[1]=4;request.Gpr[8]=5;request.Gpr[9]=6;
 request.Rflags=0x246;request.Gpr[3]=0x123456;request.Gpr[5]=0x56789;request.Gpr[12]=0xabcdef;
 const uint64_t last=7;std::memcpy(reinterpret_cast<void*>(request.Rsp+8),&last,8);
 const auto before_queries=queries.load(),before_sites=sites.load();
 for(bool call:{false,true}){auto value=compiled(seven.second,call);assert(value.Gpr[0]==28&&value.Rflags==request.Rflags&&value.Gpr[3]==0x123456&&value.Gpr[5]==0x56789&&value.Gpr[12]==0xabcdef&&value.Gpr[10]==4);}
 std::memcpy(memory+0x4108,&seven.second,8);assert(compiled(bb_image_base+0x180).Gpr[0]==28);
 request.Gpr[7]=23;assert(compiled(nested.second,true).Gpr[0]==35);
 request.Gpr[7]=101;request.Gpr[6]=202;auto pair=compiled(swap.second);assert(pair.Gpr[0]==202&&pair.Gpr[2]==101);
 const double a=1.25,b=2.5;std::memcpy(request.Xmm[0].data(),&a,8);std::memcpy(request.Xmm[1].data(),&b,8);
 auto f=compiled(fp.second,true);double result;std::memcpy(&result,f.Xmm[0].data(),8);assert(result==3.75);
 uint64_t value=909;std::memcpy(memory+16384,&value,8);request.Gpr[7]=bb_image_base+16384;assert(compiled(read.second).Gpr[0]==909&&pointer_calls==1);
 assert(compiled(stop_hle.second).Gpr[0]==77&&stop_destructors==1);
 const bool reuse=!getenv("BB_BBCPU_RANGE_REUSE")||strcmp(getenv("BB_BBCPU_RANGE_REUSE"),"0");
 if(reuse&&mode.empty())assert(queries==before_queries&&sites==before_sites);
 else assert(queries>before_queries);
 const auto old_contexts=bbcpu_live_thread_contexts();std::atomic<unsigned> bad_results{};
 std::memcpy(memory+0x4100,&seven.second,8);
 std::vector<std::thread> workers;
 for(unsigned index=0;index<2;++index)workers.emplace_back([&,index]{
  auto local=request;local.Rip=bb_image_base+0x140;local.Rsp=bb_image_base+0x7008+index*0x80;
  local.Gpr[7]=1;local.Gpr[6]=2;local.Gpr[2]=3;local.Gpr[1]=4;local.Gpr[8]=5;local.Gpr[9]=6;
  for(unsigned repeat=0;repeat<64;++repeat){
   std::memcpy(reinterpret_cast<void*>(local.Rsp),&stop,8);std::memcpy(reinterpret_cast<void*>(local.Rsp+8),&last,8);
   auto result=cpu.Run(local);if(!std::holds_alternative<Core::GuestExecutionState>(result)||std::get<Core::GuestExecutionState>(result).Gpr[0]!=28)++bad_results;
  }
 });
 for(auto& worker:workers)worker.join();assert(!bad_results&&bbcpu_live_thread_contexts()==old_contexts);
 assert(!bbcpu_is_guest_code_span(bb_image_base+16380,8));
 assert(!bbcpu_is_guest_code_span(UINTPTR_MAX-4,8));
 assert(!bbcpu_is_guest_code_span(bb_image_base+16384,1));
 // A later HLE page is deliberately absent from the immutable snapshot. Both
 // uses must keep consulting the authoritative owner; never cache transient misses.
 uint64_t late{};for(unsigned i=0;i<2048&&!late;++i){const auto fresh=registry.Register(MakeHleCallAdapter(Seven),"authored.late");auto v=veneers.Allocate(*fresh);assert(std::holds_alternative<uint64_t>(v));const auto address=std::get<uint64_t>(v);bool known=false;for(const auto& r:owned)known|=address>=r.Begin&&address-r.Begin<r.Size;if(!known)late=address;}
 assert(late);for(const auto& r:veneers.GetExecutableRanges())if(late>=r.Begin&&late-r.Begin<r.Size)ranges.push_back(r);
 request.Gpr[7]=1;request.Gpr[6]=2;request.Gpr[2]=3;request.Gpr[1]=4;request.Gpr[8]=5;request.Gpr[9]=6;
 for(unsigned i=0;i<2;++i){const auto old=queries.load();assert(compiled(late).Gpr[0]==28);assert(queries>old);}
 HleCallFrame bad{};bad.operation=read.first->Operation();bad.gpr[7]=1;bad.rsp=request.Rsp;
 auto refused=service.Invoke(bad);assert(std::get<int>(refused)==EFAULT&&pointer_calls==1);
 bad.operation=UINT64_MAX;assert(std::get<int>(service.Invoke(bad))==ENOSYS);
 request.Rip=bb_image_base;request.Rsp=1;assert(std::holds_alternative<Core::GuestExecutionFailure>(cpu.Run(request)));
 assert(std::holds_alternative<Core::GuestExecutionFailure>(cpu.CallGuest(1,{})));
 puts("BBCPU registered code + real static call/tail HLE: seven args, callbacks, pair, SIMD, flags, return stack, RAII stop, bounded lookup and late-page fallback passed");
 return 0;
}
