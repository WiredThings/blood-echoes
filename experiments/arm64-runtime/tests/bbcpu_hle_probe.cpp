// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_backend.h"
#include "bbcpu.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <sys/mman.h>
using namespace Core::GuestCpu;
extern "C" {
uintptr_t bb_image_base;
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
void ForbiddenHost(){std::_Exit(99);}
uint64_t Read(const uint64_t* p){++pointer_calls;return *p;}
bool Validate(void* ctx,uintptr_t p,size_t n,bool write){
 if(!p||!n||p>UINTPTR_MAX-n)return false;
 for(const auto& r:*static_cast<std::vector<Core::GuestExecutionRange>*>(ctx))
  if(p>=r.Begin&&n<=r.Size&&p-r.Begin<=r.Size-n&&(!write||r.Writable))return true;
 return false;
}
std::optional<Core::GuestExecutionRange> Executable(void* ctx,uintptr_t p){
 for(auto r:*static_cast<std::vector<Core::GuestExecutionRange>*>(ctx))
  if(r.Executable&&p>=r.Begin&&p-r.Begin<r.Size)return r;
 return{};
}
bool Site(void* ctx,uintptr_t p,size_t n){
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
 memory[512]=0xf4;assert(mprotect(memory,16384,PROT_READ)==0);
 HleCallRegistry registry;HleVeneerAllocator veneers;
 auto add=[&](auto fn,const char* name){auto a=registry.Register(MakeHleCallAdapter(fn),name);auto v=veneers.Allocate(*a);assert(std::holds_alternative<uint64_t>(v));return std::pair{a,std::get<uint64_t>(v)};};
 const auto seven=add(Seven,"authored.seven"),nested=add(Nested,"authored.nested"),swap=add(Swap,"authored.swap"),fp=add(Add,"authored.float"),read=add(Read,"authored.read");
 auto ranges=veneers.GetExecutableRanges();ranges.push_back({bb_image_base,16384,true,false});ranges.push_back({bb_image_base+16384,16384,false,true});
 HleCallService service(registry,Validate,&ranges,nullptr,nullptr,Executable,&ranges,"BBCPU_HLE");
 BbCpu::BbcpuBackend cpu(service,Site,&veneers);backend=&cpu;
 Core::GuestExecutionRequest request;request.Rsp=bb_image_base+32768-24;request.Rflags=2;request.MappedRanges=ranges;
 const uint64_t stop=bb_image_base+512;std::memcpy(reinterpret_cast<void*>(request.Rsp),&stop,8);
 auto run=[&](uint64_t entry){request.Rip=entry;auto result=cpu.Run(request);assert(std::holds_alternative<Core::GuestExecutionState>(result));return std::get<Core::GuestExecutionState>(result);};
 if(argc==2){
  const std::string_view mode=argv[1];
  if(mode=="--raw-host"||mode=="--unmapped-host")run(bb_image_base+64);
  else if(mode=="--unowned-syscall")run(bb_image_base+96);
  else if(mode=="--bad-pointer"){request.Gpr[7]=1;run(read.second);}
  else if(mode=="--wrong-operation"){request.Gpr[0]=UINT64_MAX;run(seven.second+13);}
  else if(mode=="--invalidate"){bbcpu_invalidate(bb_image_base,1);}
  else return 2;
  return 98; // every refusal must terminate with the original HLE fault status
 }
 request.Gpr[7]=1;request.Gpr[6]=2;request.Gpr[2]=3;request.Gpr[1]=4;request.Gpr[8]=5;request.Gpr[9]=6;
 const uint64_t last=7;std::memcpy(reinterpret_cast<void*>(request.Rsp+8),&last,8);
 assert(run(seven.second).Gpr[0]==28);
 request.Gpr[7]=23;assert(run(nested.second).Gpr[0]==35);
 request.Gpr[7]=101;request.Gpr[6]=202;auto pair=run(swap.second);assert(pair.Gpr[0]==202&&pair.Gpr[2]==101);
 const double a=1.25,b=2.5;std::memcpy(request.Xmm[0].data(),&a,8);std::memcpy(request.Xmm[1].data(),&b,8);
 auto f=run(fp.second);double result;std::memcpy(&result,f.Xmm[0].data(),8);assert(result==3.75);
 uint64_t value=909;std::memcpy(memory+16384,&value,8);request.Gpr[7]=bb_image_base+16384;assert(run(read.second).Gpr[0]==909&&pointer_calls==1);
 HleCallFrame bad{};bad.operation=read.first->Operation();bad.gpr[7]=1;bad.rsp=request.Rsp;
 auto refused=service.Invoke(bad);assert(std::get<int>(refused)==EFAULT&&pointer_calls==1);
 bad.operation=UINT64_MAX;assert(std::get<int>(service.Invoke(bad))==ENOSYS);
 request.Rip=bb_image_base;request.Rsp=1;assert(std::holds_alternative<Core::GuestExecutionFailure>(cpu.Run(request)));
 assert(std::holds_alternative<Core::GuestExecutionFailure>(cpu.CallGuest(1,{})));
 puts("BBCPU shared HLE: seven args, nested callback, pair return, SIMD, pointer refusal and unknown operation passed");
 return 0;
}
