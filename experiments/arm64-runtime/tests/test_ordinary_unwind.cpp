// SPDX-License-Identifier: GPL-2.0-or-later
#define BB_ORDINARY_UNWIND_AUTHORED_TEST 1
#include "guest_ordinary_unwind.h"
#include "guest_signal_return_classifier.h"
#include "runtime_exit_codes.h"
#include "core/guest_cpu/fex_guest_cpu.h"
#include "core/guest_cpu/fex_hle_bridge.h"
#include "core/libraries/kernel/threads/exception.h"
#include <sys/mman.h>
#include <mach/mach.h>
#include <signal.h>
#include <thread>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <map>
// Proves the CURRENT inherited header boundary, not the game's signal ABI.
using CurrentSignalContext=Libraries::Kernel::Ucontext;
using CurrentSignalMachine=Libraries::Kernel::Mcontext;
static_assert(sizeof(CurrentSignalContext)==0x500&&sizeof(CurrentSignalMachine)==0x480);
static_assert(offsetof(CurrentSignalContext,uc_mcontext)==0x40);
static_assert(offsetof(CurrentSignalMachine,mc_fpstate)+0x40==0x140);
static_assert(offsetof(CurrentSignalMachine,mc_rflags)+0x40==0xf0);
static_assert(offsetof(CurrentSignalMachine,mc_fsbase)+0x40==0x480);
static_assert(offsetof(CurrentSignalMachine,mc_gsbase)+0x40==0x488);
static_assert(offsetof(CurrentSignalContext,uc_stack)==0x4c8&&offsetof(CurrentSignalContext,uc_flags)==0x4e0);
extern "C" {__thread sigjmp_buf* runtime_fault_recover{};}
namespace U=BbOrdinaryUnwind;
using Core::GuestExecutionRequest;using Core::GuestExecutionState;using Core::GuestExecutionFailure;
using namespace Core::GuestCpu;
using u64=std::uint64_t;
static unsigned checks,cases;
static void check(bool ok,const char*why){++checks;if(!ok)throw std::runtime_error(why);}
struct Map{size_t size;void* ptr;
 explicit Map(size_t n=16384):size(n),ptr(mmap(nullptr,n,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0)){check(ptr!=MAP_FAILED,"mmap");}
 ~Map(){munmap(ptr,size);}uintptr_t at(size_t n=0)const{return reinterpret_cast<uintptr_t>(ptr)+n;}
};
static void word(uintptr_t p,size_t at,u64 n){memcpy(reinterpret_cast<void*>(p+at),&n,8);}
static u64 word(uintptr_t p,size_t at){u64 n;memcpy(&n,reinterpret_cast<void*>(p+at),8);return n;}
static void fault(int sig,siginfo_t*info,void*ctx){
 if(runtime_fault_recover){auto* p=runtime_fault_recover;runtime_fault_recover=nullptr;siglongjmp(*p,1);}
 if(AetherPS4::Fex::HandleGuestSignal(sig,info,ctx))return;
 write(2,"unhandled authored fault outside read leaf\n",42);
 _exit(99);
}
struct Ranges{std::vector<Core::GuestExecutionRange> list;};
static bool executable(void*ctx,uintptr_t p){
 for(auto r:static_cast<Ranges*>(ctx)->list){if(r.Executable&&!r.Writable&&p>=r.Begin&&p-r.Begin<r.Size)return true;}
 return false;}
static bool immutableExecutable(void*ctx,uintptr_t p,size_t n){
 if(!p||!n||p>UINTPTR_MAX-n)return false;bool found=false;
 for(auto r:static_cast<Ranges*>(ctx)->list){
  if(r.Writable&&p<r.Begin+r.Size&&r.Begin<p+n)return false;
  if(r.Executable&&!r.Writable&&p>=r.Begin&&n<=r.Size&&p-r.Begin<=r.Size-n)found=true;
 }return found;}
static bool valid(void*ctx,uintptr_t p,size_t n,bool write){if(!p||!n||p>UINTPTR_MAX-n)return false;
 for(auto r:static_cast<Ranges*>(ctx)->list)if(p>=r.Begin&&n<=r.Size&&p-r.Begin<=r.Size-n&&(!write||r.Writable))return true;return false;}
struct Recorder:HleCallAdapter{
 std::shared_ptr<U::Adapter> inner;mutable uintptr_t snapshot{};mutable unsigned ok{},failed{};uintptr_t protectPage{};
 explicit Recorder(std::shared_ptr<U::Adapter>a):inner(std::move(a)){}
 HleCallResult Invoke(HleCallFrame&f)const override{
  if(protectPage)check(mprotect(reinterpret_cast<void*>(protectPage),16384,PROT_NONE)==0,"protect source after FEX bootstrap");
  auto r=inner->Invoke(f);
  if(protectPage)check(mprotect(reinterpret_cast<void*>(protectPage),16384,PROT_READ|PROT_WRITE)==0,"restore source after guarded read");
  if(std::get_if<bool>(&r)){snapshot=f.gpr[7];++ok;}else ++failed;return r;
 }
};
struct Nested:HleCallAdapter{
 Core::FexGuestCpuBackend*cpu{};uintptr_t entry{};mutable unsigned rejected{};
 HleCallResult Invoke(HleCallFrame&f)const override{
  std::array<u64,1>a{f.gpr[7]};auto r=cpu->CallGuest(entry,a);
  if(auto*failure=std::get_if<GuestExecutionFailure>(&r)){if(failure->Error==ENOTSUP)++rejected;return HleCallFailure{failure->Error,"nested ordinary rejection"};}
  return HleCallFailure{EINVAL,"nested ordinary unexpectedly succeeded"};
 }
};
static void initial(uintptr_t context,uintptr_t target,uintptr_t stack){
 memset(reinterpret_cast<void*>(context),0,0x500);
 for(auto [off,value]:std::array<std::pair<size_t,u64>,6>{{{0x48,0x1111},{0x78,0x1234abcddcba4321ULL},{0xe0,target},{0xf8,stack},{0x108,0x480},{0x110,0x10002}}})word(context,off,value);
 word(context,0x118,0x20001);word(context,0x120,0x037f);word(context,0x138,0x1f80);
 for(size_t i=0;i<16;++i){word(context,0x1c0+i*16,0x0102030405060708+i);word(context,0x1c8+i*16,0x8899aabbccddeeff-i);}
}
static void mutate(void*,uintptr_t input){word(input,0xe0,0);word(input,0x138,0x80000000);word(input,0x78,0xbad);}
static void report(void* context,const HleCallFailure&failure){
 if(!*static_cast<bool*>(context))return;
 uintptr_t owner{},rsp{};unsigned depth{};
 if(failure.error==ENOTSUP&&AetherPS4::Fex::PrototypeQueryUnwindBoundary(&owner,&depth,&rsp)&&depth==2){
  constexpr char evidence[]="{\"case\":\"nested_callback\",\"depth\":2,\"error\":45,\"restoration_rejected\":true,\"explicit_failure_stop\":true,\"launcher_restart_emitted\":false}\n";
  write(1,evidence,sizeof(evidence)-1);_exit(BB_ARM_EXIT_HLE_FAULT);
 }
 _exit(91);
}
int main(int argc,char**argv){try{
 check(argc==2||(argc==3&&std::string_view(argv[2])=="--nested-only"),"need local boot path");
 bool nested_only=argc==3;
 struct sigaction action{};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO;sigemptyset(&action.sa_mask);
 check(sigaction(SIGBUS,&action,nullptr)==0&&sigaction(SIGSEGV,&action,nullptr)==0,"fault installation");
 uintptr_t owner{},rsp{};unsigned depth{};
 check(!AetherPS4::Fex::PrototypeQueryUnwindBoundary(&owner,&depth,&rsp),"outside FEX no boundary");
 // Read-only identity guard consumes local bytes in memory, never exports them.
 std::ifstream boot(argv[1],std::ios::binary);check(bool(boot),"boot");boot.seekg(8);u64 image;boot.read(reinterpret_cast<char*>(&image),8);boot.seekg(0,std::ios::end);auto file=u64(boot.tellg());
 std::array<std::byte,166> observed;boot.seekg(file-image+0x5733cd0);check(bool(boot.read(reinterpret_cast<char*>(observed.data()),observed.size())),"observed ABI input");
 check(U::AdmitObservedAbi(observed),"actual ABI admission");observed[0]^=std::byte(1);check(!U::AdmitObservedAbi(observed),"changed ABI rejection");observed[0]^=std::byte(1);
 Map code,stack(65536),foreign(65536),output;
 Ranges ranges;HleCallRegistry registry;HleVeneerAllocator veneers;
 auto inner=std::make_shared<U::Adapter>(true,executable,&ranges);auto record=std::make_shared<Recorder>(inner);
 auto adapter=registry.Register(record,"authored.ordinary_unwind");check(bool(adapter),"registry");U::Entry entry(adapter->Operation());
 auto nested=std::make_shared<Nested>();auto na=registry.Register(nested,"authored.nested_unwind");
 auto nr=veneers.Allocate(*na);check(std::holds_alternative<u64>(nr),"nested veneer");
 auto classifier=registry.Register(std::make_shared<BbSignalReturn::Classifier>(true,immutableExecutable,&ranges),"authored.signal_pc_classifier");
 auto classifierEntry=veneers.Allocate(*classifier);check(std::holds_alternative<u64>(classifierEntry),"classifier veneer");
 // call entry; fxsave [r10]; hlt. Success must branch directly to code+256.
 auto emitCaller=[&](size_t offset,uintptr_t callee){unsigned char body[]={0x48,0xb8,0,0,0,0,0,0,0,0,0xff,0xd0,0x41,0x0f,0xae,0x02,0xf4};
  memcpy(body+2,&callee,8);memcpy(reinterpret_cast<void*>(code.at(offset)),body,sizeof(body));};
 emitCaller(0,entry.Address());emitCaller(512,std::get<u64>(nr));*reinterpret_cast<unsigned char*>(code.at(256))=0xf4;
 {unsigned char body[]={0x48,0xb8,0,0,0,0,0,0,0,0,0xff,0xd0,0xf4};
  auto callee=std::get<u64>(classifierEntry);memcpy(body+2,&callee,8);memcpy(reinterpret_cast<void*>(code.at(1024)),body,sizeof(body));}
 memcpy(reinterpret_cast<void*>(code.at(1536)),BbSignalReturn::SignalTail.data(),BbSignalReturn::SignalTail.size());
 memcpy(reinterpret_cast<void*>(code.at(1600)),BbSignalReturn::SyscallTail.data(),BbSignalReturn::SyscallTail.size());
 memcpy(reinterpret_cast<void*>(code.at(1664)),BbSignalReturn::SignalTail.data(),BbSignalReturn::SignalTail.size());
 *reinterpret_cast<unsigned char*>(code.at(1664+10))=0xa2; // wrong syscall number
 memcpy(reinterpret_cast<void*>(code.at(1728)),BbSignalReturn::SyscallTail.data(),BbSignalReturn::SyscallTail.size());
 *reinterpret_cast<unsigned char*>(code.at(1728+4))=0x06; // wrong syscall instruction
 check(mprotect(code.ptr,code.size,PROT_READ)==0,"code seal");
 ranges.list={{code.at(),code.size,true,false},entry.Range(),{stack.at(),stack.size,false,true},{foreign.at(),foreign.size,false,true},{output.at(),output.size,false,true}};
 for(auto r:veneers.GetExecutableRanges())ranges.list.push_back(r);
 HleCallFrame cf{};cf.gpr[7]=0;BbSignalReturn::Classifier denied(false,immutableExecutable,&ranges);
 auto deniedResult=denied.Invoke(cf);check(std::get_if<HleCallFailure>(&deniedResult)&&std::get<HleCallFailure>(deniedResult).error==ENOTSUP,"unknown ABI cannot classify low sentinel");
 cf.gpr[7]=code.at(1536);BbSignalReturn::Classifier guarded(true,immutableExecutable,&ranges);
 check(mprotect(code.ptr,code.size,PROT_NONE)==0,"classifier unreadable published code");
 sigjmp_buf previousClassifier;runtime_fault_recover=&previousClassifier;auto unreadable=guarded.Invoke(cf);
 check(runtime_fault_recover==&previousClassifier,"classifier restores previous fault guard");runtime_fault_recover=nullptr;
 check(mprotect(code.ptr,code.size,PROT_READ)==0,"restore classifier code");
 check(std::get_if<HleCallFailure>(&unreadable)&&std::get<HleCallFailure>(unreadable).error==EFAULT,"real classifier read fault rejected");
 HleGuestBridge bridge(registry,valid,&ranges,report,&nested_only);auto made=Core::FexGuestCpuBackend::Create(bridge);
 check(std::holds_alternative<std::unique_ptr<Core::FexGuestCpuBackend>>(made),"FEX create");auto cpu=std::move(std::get<std::unique_ptr<Core::FexGuestCpuBackend>>(made));nested->cpu=cpu.get();nested->entry=entry.Address();
 ranges.list.push_back(cpu->ReturnRange());ranges.list.push_back(cpu->CallbackReturnRange());
 GuestExecutionRequest request;request.Rip=code.at();request.Rsp=stack.at(stack.size-0x1008);request.Rflags=2;request.Gpr[1]=output.at();request.MappedRanges=ranges.list;
 for(size_t i=0;i<16;++i)request.Xmm[i]={0x77+i,0x99-i};
 const auto context=stack.at(0x1000),targetStack=stack.at(stack.size-8);request.Gpr[7]=context;
 auto reset=[&]{initial(context,code.at(256),targetStack);memset(output.ptr,0,output.size);};
 auto execute=[&](GuestExecutionRequest req,int error,const char*label,uintptr_t scope=0,size_t scopeBytes=0){++cases;std::cerr<<"CASE "<<label<<"\n";
  ScopedGuestStack owned(scope?scope:stack.at(),scopeBytes?scopeBytes:stack.size);
  auto result=cpu->Run(req);
  if(error){auto*f=std::get_if<GuestExecutionFailure>(&result);check(f&&f->Error==error,label);
   for(size_t i=0;i<16;++i)check(word(output.at(),160+i*16)==request.Xmm[i][0],"failure must not partly restore FP");
  }else{auto*s=std::get_if<GuestExecutionState>(&result);check(s&&s->StopReason==Core::GuestStopReason::Halted,label);
   check(s->Gpr[0]==0x1234abcddcba4321ULL&&s->Rsp==targetStack,"nonreturn ordinary transfer");
   check(s->Xmm[0][0]==0x0102030405060708ULL,"immutable FP snapshot used");}
 };
 if(nested_only){reset();auto req=request;req.Rip=code.at(512);execute(req,ENOTSUP,"real nested callback rejected");throw std::runtime_error("nested failure reporter must stop before returning");}
 reset();execute(request,0,"valid normal context");
 check(record->snapshot&&record->snapshot!=context,"snapshot has distinct immutable alias");
 vm_address_t region=record->snapshot;vm_size_t bytes{};vm_region_basic_info_data_64_t vm{};mach_msg_type_number_t count=VM_REGION_BASIC_INFO_COUNT_64;mach_port_t object{};
 check(vm_region_64(mach_task_self(),&region,&bytes,VM_REGION_BASIC_INFO_64,reinterpret_cast<vm_region_info_t>(&vm),&count,&object)==KERN_SUCCESS,"snapshot native mapping");
 if(object)mach_port_deallocate(mach_task_self(),object);
 check(vm.protection==VM_PROT_READ&&vm.max_protection==VM_PROT_READ,"guest snapshot cannot regain write access");
 reset();inner->SetAfterSnapshotForAuthoredControl(mutate,nullptr);execute(request,0,"source mutation after capture uses frozen bytes");inner->SetAfterSnapshotForAuthoredControl(nullptr,nullptr);
 for(auto [offset,value,error,label]:std::array<std::tuple<size_t,u64,int,const char*>,8>{{
  {0x108,1,ENOTSUP,"bad length"},{0x110,0,ENOTSUP,"wrong format"},{0x118,0,ENOTSUP,"wrong owner"},{0xd0,1,ENOTSUP,"unsupported extension flags"},
  {0x138,0x80000000,EINVAL,"corrupt MXCSR"},{0xe0,output.at(),EFAULT,"data is not executable"},
  {0xf8,foreign.at(0x8000),EFAULT,"another thread stack target"},{0xf8,request.Rsp-8,EFAULT,"downward target"}}}){
  reset();word(context,offset,value);execute(request,error,label);
 }
 for(auto [source,label]:std::array<std::pair<uintptr_t,const char*>,4>{{{1,"bad pointer"},{context+1,"unaligned context"},{stack.at(stack.size-256),"truncated context"},{foreign.at(0x1000),"foreign input"}}}){
  reset();auto req=request;req.Gpr[7]=source;execute(req,EFAULT,label);
 }
 reset();auto faultReq=request;faultReq.Gpr[7]=stack.at(16384-256);
 record->protectPage=stack.at(16384);
 sigjmp_buf previous;runtime_fault_recover=&previous;execute(faultReq,EFAULT,"full span unreadable fault rejected");check(runtime_fault_recover==&previous,"previous recovery restored");runtime_fault_recover=nullptr;
 record->protectPage=0;
 reset();std::exception_ptr problem;std::thread other([&]{try{
  auto req=request;req.Rsp=foreign.at(foreign.size-0x1008);execute(req,EFAULT,"foreign context rejected on another real FEX thread",foreign.at(),foreign.size);
 }catch(...){problem=std::current_exception();}});other.join();if(problem)std::rethrow_exception(problem);
 unsigned classifierCases=0;
 for(auto [pc,result,error,label]:std::array<std::tuple<uintptr_t,u64,int,const char*>,11>{{
  {0,1,0,"zero sentinel"},{4095,1,0,"low sentinel"},{4096,0,EFAULT,"low boundary is not sentinel"},
  {code.at(1536),1,0,"type1 full signal tail"},{code.at(1605),2,0,"type2 syscall tail"},
  {code.at(1664),0,0,"mutated type1 is ordinary"},{code.at(1733),0,0,"mutated type2 is ordinary"},
  {output.at(100),0,EFAULT,"writable host data rejected"},{code.at(2),0,EFAULT,"type2 PC-minus-five segment boundary"},
  {code.at(code.size-2),0,EFAULT,"type1 segment-end span"},{UINTPTR_MAX,0,EFAULT,"address overflow rejected"}}}){
  ++classifierCases;std::cerr<<"CLASSIFIER "<<label<<"\n";auto req=request;req.Rip=code.at(1024);req.Gpr[7]=pc;auto actual=cpu->Run(req);
  if(error){auto* f=std::get_if<GuestExecutionFailure>(&actual);check(f&&f->Error==error,label);}
  else{auto* s=std::get_if<GuestExecutionState>(&actual);check(s&&s->Gpr[0]==result,label);}
 }
 std::cout<<"{\"cases\":"<<cases<<",\"classifier_cases\":"<<classifierCases<<",\"assertions\":"<<checks<<",\"real_fex\":true,\"actual_hle_adapter\":true,\"signal_imports_registered\":false,\"game_started\":false}\n";
 return 0;
}catch(const std::exception&e){std::cerr<<"safe ordinary controls failed: "<<e.what()<<"\n";return 1;}}
