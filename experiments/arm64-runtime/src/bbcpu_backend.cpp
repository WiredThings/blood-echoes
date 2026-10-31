// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_backend.h"
#include "bbcpu.h"
#include "runtime_exit_codes.h"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <cstdio>
#include <stdexcept>
#include <mutex>
#include <vector>
namespace {
std::atomic<BbCpu::BbcpuBackend*> owner{};
std::once_flag initialize;
bool contains(const Core::GuestExecutionRange& range,uintptr_t p,size_t bytes,bool write){
 return p&&bytes&&p>=range.Begin&&bytes<=range.Size&&p-range.Begin<=range.Size-bytes&&(!write||range.Writable);
}
bool enabled(const char* name){const auto* value=std::getenv(name);return !value||std::strcmp(value,"0");}
void stable_index(StaticRangeIndex& index,std::span<const Core::GuestExecutionRange> ranges,const StaticRangeIndex* parent=nullptr){
 std::vector<Core::GuestExecutionRange> selected;
 // Bound metadata rather than creating a new admission limit: large or
 // uncertain registrations use the existing live query, never an empty success.
 if(ranges.size()<=256){
  for(const auto& r:ranges)if(!r.Size||r.Begin>UINTPTR_MAX-r.Size)throw std::invalid_argument("invalid stable code extent");
  for(const auto& r:ranges)if(r.Executable&&!r.Writable){
   bool overlap=false;for(const auto& other:ranges)if(other.Writable&&r.Begin<other.Begin+other.Size&&other.Begin<r.Begin+r.Size){overlap=true;break;}
   if(!overlap&&(!parent||parent->Contains(r.Begin,r.Size,false)))selected.push_back(r);
  }
 }
 index.Build(selected);
}
}
namespace BbCpu {
BbcpuBackend* BbcpuBackend::Owner(){return owner.load(std::memory_order_acquire);}
int BbcpuBackend::Query(uintptr_t address){
 auto* self=Owner();if(!self)return 0;
 if(self->code_index.Contains(address,1,false))return 1;
 auto range=self->service.QueryExecutableRange(address);
 return range&&range->Executable&&contains(*range,address,1,false);
}
int BbcpuBackend::QuerySpan(uintptr_t address,size_t bytes){
 auto* self=Owner();if(!self)return 0;
 if(self->code_index.Contains(address,bytes,false))return 1;
 auto range=self->service.QueryExecutableRange(address);
 return range&&range->Executable&&contains(*range,address,bytes,false);
}
BbcpuBackend::BbcpuBackend(Core::GuestCpu::HleCallService& value,HleSiteValidator site,void* ctx,
 std::span<const Core::GuestExecutionRange> stable_code,std::span<const Core::GuestExecutionRange> stable_hle)
 :service(value),hle_site(site),context(ctx){
 if(!site)throw std::invalid_argument("bbcpu requires an owned HLE-site validator");
 if(enabled("BB_ARM_RANGE_FAST")&&enabled("BB_BBCPU_RANGE_REUSE")){stable_index(code_index,stable_code);stable_index(hle_index,stable_hle,&code_index);}
 else{code_index.Build(std::vector<Core::GuestExecutionRange>{});hle_index.Build(std::vector<Core::GuestExecutionRange>{});}
 bool installed=false;
 std::call_once(initialize,[&]{
  owner.store(this,std::memory_order_release);
  bbcpu_set_guest_code_query(Query);
  bbcpu_set_guest_code_span_query(QuerySpan);
  bbcpu_set_host_call_validator([](uintptr_t){return 0;});
  // The shared runtime denies remap/protect of its owned image/stubs; menu
  // detours are finalized before this owner is installed and effect code
  // changes apply after restart. No current HLE imports this CPU-only API.
  // Do not pretend upstream's decoded-only unlink also invalidates native
  // direct links or static slots. A new live-code caller needs that work first.
  bbcpu_set_invalidation_handler([](uintptr_t address,size_t bytes){
   std::fprintf(stderr,"STOP: bbcpu: live code invalidation is not admitted by immutable execution contract address=%#lx bytes=%zu\n",address,bytes);
   std::_Exit(BB_ARM_EXIT_HLE_FAULT);
  });
  bbcpu_set_fatal_handler([](const char*,uint64_t){std::_Exit(BB_ARM_EXIT_HLE_FAULT);});
  bbcpu_set_syscall_handler([](BbGuestRegs* regs){return Syscall(regs);});
  bbcpu_set_owned_import_resolver(enabled("BB_BBCPU_HLE_DIRECT")?ResolveImport:nullptr);
  installed=true;
 });
 if(!installed)throw std::runtime_error("bbcpu host may initialize only one backend per process");
 std::printf("BBCPU fast paths: code-index-bytes=%zu hle-index-bytes=%zu owned-HLE=%d; dynamic misses retain original checks\n",code_index.Bytes(),hle_index.Bytes(),enabled("BB_BBCPU_HLE_DIRECT"));
}
BbcpuBackend::~BbcpuBackend(){
 if(running.load(std::memory_order_acquire))std::abort();
 owner.store(nullptr,std::memory_order_release);
}
bool BbcpuBackend::OwnsHle(uintptr_t address,size_t bytes)const{
 return hle_index.Contains(address,bytes,false)||hle_site(context,address,bytes);
}
int BbcpuBackend::ResolveImport(const BbGuestRegs* regs,BbOwnedImport* out){
 auto* self=Owner();if(!self||!regs||!out)return 0;
 uintptr_t target=regs->rip;
 if(!QuerySpan(target,6))return 0;
 const auto* stub=reinterpret_cast<const unsigned char*>(target);
 if(stub[0]==0xff&&stub[1]==0x25){
  int32_t disp{};std::memcpy(&disp,stub+2,4);
  const auto next=target+6;uintptr_t slot{};
  if(disp>=0){if(next>UINTPTR_MAX-uint32_t(disp))return -1;slot=next+uint32_t(disp);}
  else{const auto distance=uint64_t(-int64_t(disp));if(next<distance)return -1;slot=next-distance;}
  if(!self->code_index.Contains(slot,8,false)&&!self->service.ValidateGuestMemory(slot,8))return -1;
  std::memcpy(&target,reinterpret_cast<const void*>(slot),8);
 }
 if(!QuerySpan(target,16))return 0;
 const auto* code=reinterpret_cast<const unsigned char*>(target);
 constexpr unsigned char prefix[]{0x49,0x89,0xca,0x48,0xb8};
 if(std::memcmp(code,prefix,5)||code[13]!=0x0f||code[14]!=0x05||code[15]!=0xc3)return 0;
 if(!self->OwnsHle(target,16))return 0;
 // No speculative return-slot read. If the common guest validator cannot
 // prove this stack, retain the original interpreter path without side effects.
 if(!self->service.ValidateGuestMemory(regs->r[4],8))return 0;
 uint64_t operation{};std::memcpy(&operation,code+5,8);
 *out={target+15,operation};return 1;
}
int BbcpuBackend::Syscall(void* input){
 auto* self=Owner();auto* regs=static_cast<BbGuestRegs*>(input);
 if(!self||regs->rip<15||!self->OwnsHle(regs->rip-15,15))return 0;
 // Accept the existing immutable HLE veneer, not an arbitrary game syscall
 // with an operation number that happens to name a registered host function.
 const auto* code=reinterpret_cast<const unsigned char*>(regs->rip-15);
 constexpr unsigned char prefix[]{0x49,0x89,0xca,0x48,0xb8};
 uint64_t operation{};std::memcpy(&operation,code+5,8);
 if(std::memcmp(code,prefix,5)||code[13]!=0x0f||code[14]!=0x05||operation!=regs->r[0])return 0;
 BbTrapState trap{};if(!bbcpu_trap_state(&trap))return 0;
 Core::GuestCpu::HleCallFrame frame{};frame.operation=operation;frame.rsp=regs->r[4];
 std::copy_n(regs->r,16,frame.gpr.begin());frame.gpr[1]=frame.gpr[10];
 for(size_t i=0;i<16;++i)std::memcpy(frame.xmm[i].data(),trap.ymm[i],16);
 auto result=self->service.Invoke(frame);
 if(auto* failure=std::get_if<int>(&result)){
  std::fprintf(stderr,"BBCPU HLE STOP error=%d\n",*failure);
  return 0;
 }
 if(!std::get<bool>(result))return 0;
 std::copy(frame.gpr.begin(),frame.gpr.end(),regs->r);
 for(size_t i=0;i<16;++i)std::memcpy(trap.ymm[i],frame.xmm[i].data(),16);
 return 1;
}
Core::GuestExecutionResult BbcpuBackend::Run(const Core::GuestExecutionRequest& request){
 using namespace Core;
 auto fail=[](int error)->GuestExecutionResult{return GuestExecutionFailure{GuestExecutionStage::Request,error};};
 if(!Query(request.Rip)||request.Rsp%8||!request.Rsp)return fail(EFAULT);
 uintptr_t stack_top{};
 for(const auto& range:request.MappedRanges){
  if(range.Begin>UINTPTR_MAX-range.Size)return fail(EOVERFLOW);
  if(contains(range,request.Rsp,8,true))stack_top=std::max(stack_top,range.Begin+range.Size);
 }
 if(!stack_top)return fail(EFAULT);
 uint64_t stop{};std::memcpy(&stop,reinterpret_cast<const void*>(request.Rsp),8);
 if(!stop||!Query(stop))return fail(EFAULT);
 BbExecutionFrame frame{};std::copy(request.Gpr.begin(),request.Gpr.end(),frame.r);
 frame.r[4]=request.Rsp;frame.rip=request.Rip;frame.flags=request.Rflags;
 frame.fs_base=request.FsBase;frame.gs_base=request.GsBase;frame.stack_top=stack_top;
 bbcpu_get_fp_control(&frame.mxcsr,&frame.fcw);
 for(size_t i=0;i<16;++i)std::memcpy(frame.ymm[i],request.Xmm[i].data(),16);
 running.fetch_add(1,std::memory_order_acq_rel);
 const int error=bbcpu_execute_frame(&frame,stop);
 running.fetch_sub(1,std::memory_order_acq_rel);
 if(error)return GuestExecutionFailure{GuestExecutionStage::Execute,error};
 GuestExecutionState out{};out.FirstRip=request.Rip;out.Rip=frame.rip;out.Rsp=frame.r[4];out.Rflags=frame.flags;
 std::copy_n(frame.r,16,out.Gpr.begin());
 for(size_t i=0;i<16;++i)std::memcpy(out.Xmm[i].data(),frame.ymm[i],16);
 out.StopReason=GuestStopReason::Halted;return out;
}
Core::GuestExecutionResult BbcpuBackend::CallGuest(uintptr_t entry,std::span<const uint64_t> args){
 using namespace Core;
 if(args.size()>7||!Query(entry))return GuestExecutionFailure{GuestExecutionStage::Request,EINVAL};
 running.fetch_add(1,std::memory_order_acq_rel);
 auto result=bbcpu_call(entry,int(args.size()),args.data());
 running.fetch_sub(1,std::memory_order_acq_rel);
 GuestExecutionState out{};out.FirstRip=entry;out.Gpr[0]=result;out.StopReason=GuestStopReason::Returned;return out;
}
}
