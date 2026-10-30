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
namespace {
std::atomic<BbCpu::BbcpuBackend*> owner{};
std::once_flag initialize;
bool contains(const Core::GuestExecutionRange& range,uintptr_t p,size_t bytes,bool write){
 return p&&bytes&&p>=range.Begin&&bytes<=range.Size&&p-range.Begin<=range.Size-bytes&&(!write||range.Writable);
}
}
namespace BbCpu {
BbcpuBackend* BbcpuBackend::Owner(){return owner.load(std::memory_order_acquire);}
int BbcpuBackend::Query(uintptr_t address){
 auto* self=Owner();if(!self)return 0;
 auto range=self->service.QueryExecutableRange(address);
 return range&&range->Executable&&contains(*range,address,1,false);
}
int BbcpuBackend::QuerySpan(uintptr_t address,size_t bytes){
 auto* self=Owner();if(!self)return 0;
 auto range=self->service.QueryExecutableRange(address);
 return range&&range->Executable&&contains(*range,address,bytes,false);
}
BbcpuBackend::BbcpuBackend(Core::GuestCpu::HleCallService& value,HleSiteValidator site,void* ctx)
 :service(value),hle_site(site),context(ctx){
 if(!site)throw std::invalid_argument("bbcpu requires an owned HLE-site validator");
 bool installed=false;
 std::call_once(initialize,[&]{
  owner.store(this,std::memory_order_release);
  bbcpu_set_guest_code_query(Query);
  bbcpu_set_guest_code_span_query(QuerySpan);
  bbcpu_set_host_call_validator([](uintptr_t){return 0;});
  bbcpu_set_fatal_handler([](const char*,uint64_t){std::_Exit(BB_ARM_EXIT_HLE_FAULT);});
  bbcpu_set_syscall_handler([](BbGuestRegs* regs){return Syscall(regs);});
  installed=true;
 });
 if(!installed)throw std::runtime_error("bbcpu host may initialize only one backend per process");
}
BbcpuBackend::~BbcpuBackend(){
 if(running.load(std::memory_order_acquire))std::abort();
 owner.store(nullptr,std::memory_order_release);
}
int BbcpuBackend::Syscall(void* input){
 auto* self=Owner();auto* regs=static_cast<BbGuestRegs*>(input);
 if(!self||regs->rip<15||!self->hle_site(self->context,regs->rip-15,15))return 0;
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
