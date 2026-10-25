#include "avplayer_bridge.h"
#include "runtime_exit_codes.h"
#include "qa_cpu_qos.h"
#include "bb_cpu_cache_build.h"
#include "runtime_diagnostic_exit.h"
#include "bb_cpu_cache_inputs.h"
#include "bb_fex_map_audit.h"
#include "bb_static_code_files.h"
#include "bb_stutter_audit.h"
#include "startup_patches.h"
#include "launcher_patch_input.h"
#include "launcher_game_metadata.h"
#include "launcher_cli.h"
#include "launcher_thread.h"
#include "launcher_cpu_policy.h"
#include "guest_resource_detours.h"
#include "game_menu_detours.h"
#include "resource_leaf_profile.h"
#include "dev_travel_hooks.h"
#include "resource_query.h"
#include "allocation_probe.h"
#include "extra_heaps.h"
#include "native_memory_inspection.h"
#include <sstream>
#include "../vendor/dev_world_warper/world_warper_state.h"
#include <fcntl.h>
#include <sys/stat.h>
#include "bbport_guest_hooks.h"
#include "bbport_resource_lifetime.h"
#include "static_range_index.h"
#include "session_state.h"
#include "thread_bootstrap_ranges.h"
#include "core/fex/darwin_fault.h"
// SPDX-License-Identifier: GPL-2.0-or-later
#include "boot_image.h"
#include "remote/bb_memory_mirror.h"
#include "core/guest_cpu/fex_guest_cpu.h"
#include "core/guest_cpu/fex_hle_bridge.h"
#include <FEXCore/Core/X86Enums.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <sys/mman.h>
#include <shared_mutex>
#include <thread>
#include <mutex>
#include <atomic>
#include <iostream>
#include <unistd.h>
#include <signal.h>
#define BB_LOW_HEAP_IMPL
extern "C" {
#include "runtime.h"
#include "runtime_quicksave.h"
#include "gpu/bbgpu.h"
int runtime_memory_vma_info(uintptr_t,int*,int*,uintptr_t*);
int runtime_memory_exclude(uintptr_t,uintptr_t);
void runtime_low_release(void*,size_t);
void* bb_arm_net_adapter(uintptr_t);
void runtime_quicksave_begin_auto_continue(void);
void runtime_thread_detach_host(void);
void* bb_low_aligned_alloc(size_t,size_t);
void bb_low_free(void*);
void runtime_memory_note_write(uintptr_t,uint64_t);
void bbprototype_configure_heap(uintptr_t,uintptr_t,uintptr_t,uint64_t(*)(uintptr_t,const uint64_t*,size_t),uintptr_t(*)(void*,unsigned),int(*)(uintptr_t,size_t,int));
}
namespace Libraries::Kernel {uint64_t sceKernelReadTsc();}
namespace BbRemote::Back {uint64_t ReadTsc();}
namespace BbGuestHooks {void Install(void(*)(uint64_t,uint64_t));}
namespace BbHeapSites {void InstallCounters();long long LiveAllocations();}
using namespace Core::GuestCpu;
std::shared_ptr<HleCallAdapter> bb_arm_gpu_adapter(std::string_view,uintptr_t);
using BbBoot::Boot;
namespace fs=std::filesystem;
namespace {
constexpr size_t Page=16384;
size_t prototype_round_page(size_t n){return (n+Page-1)&~(Page-1);}
void check(bool b,const char* m){if(!b)throw std::runtime_error(m);}
struct Mapping {
 void* ptr{};size_t size{};
 explicit Mapping(size_t bytes,int protections=PROT_READ|PROT_WRITE):size(prototype_round_page(bytes)) {ptr=runtime_low_map(size,protections);check(ptr,"low mapping failed");}
 ~Mapping(){if(ptr)runtime_low_release(ptr,size);}
 Mapping(const Mapping&)=delete;
 uintptr_t at()const{return reinterpret_cast<uintptr_t>(ptr);}
};
struct CodeMapOutput final:FEXCore::CodeMapOpener {
 fs::path path;bool opened{};explicit CodeMapOutput(fs::path file):path(std::move(file)){}
 int OpenCodeMapFile() override{if(opened)return -1;opened=true;const int fd=open(path.c_str(),O_CREAT|O_EXCL|O_WRONLY,0600);check(fd>=0,"private CPU code map unavailable");return fd;}
};
struct State {
 HleCallRegistry registry;HleVeneerAllocator veneers;
 // Published once, before guest entry; fault handling only reads this immutable list.
 std::vector<std::pair<uintptr_t,size_t>> missing_data_ranges;std::atomic<bool> missing_data_ready{false};
 std::map<uintptr_t,std::shared_ptr<HleCallAdapter>> native;
 std::unique_ptr<BbStaticCode::Files> code_files;std::unique_ptr<CodeMapOutput> code_output;
 std::unique_ptr<HleGuestBridge> bridge;std::unique_ptr<Core::FexGuestCpuBackend> cpu;
 std::vector<Core::GuestExecutionRange> ranges;std::shared_mutex mutex;
 StaticRangeIndex permanent;size_t permanent_count{};
 const bool fast_ranges=!getenv("BB_ARM_RANGE_FAST")||std::string_view(getenv("BB_ARM_RANGE_FAST"))!="0";
 std::vector<std::pair<uintptr_t,uintptr_t>> protected_host;
 std::thread::id main_thread=std::this_thread::get_id();
 uintptr_t halt{},image{},text_end{},libc{};uint64_t image_size{};
 std::atomic<unsigned> module{};
 std::vector<Core::GuestExecutionRange> bootstrap(uintptr_t stack,size_t size){std::shared_lock lock(mutex);
#ifdef BB_OLD_THREAD_BOOTSTRAP
 return ranges;
#else
 return ThreadBootstrapRanges(ranges,permanent_count,stack,size);
#endif
 }
 bool mapped_valid(uintptr_t a,size_t n,bool write){
  uintptr_t end{};int prot{},type{};auto current=a;
  while(current<a+n&&runtime_memory_vma_info(current,&prot,&type,&end)&&end>current){if(!(prot&1)||(write&&!(prot&2)))return false;current=std::min(end,a+n);}
  return current==a+n;
 }
 bool valid(uintptr_t a,size_t n,bool write){
  if(!a||!n||a>UINTPTR_MAX-n)return false;
  const bool indexed=fast_ranges&&permanent.Ready();
  if(indexed){
   if(ScopedGuestStack::Contains(a,n)||permanent.Contains(a,n,write))return true;
   // Mutable VMAs always use the authoritative current mapping and permission
   // table. Success here avoids a second lock just to reject static ranges we
   // already checked. Temporary stacks not in the VMA table retain the live lock.
   if(mapped_valid(a,n,write))return true;
  }
  {std::shared_lock lock(mutex);for(size_t i=indexed?permanent_count:0;i<ranges.size();++i){auto r=ranges[i];if(a>=r.Begin&&n<=r.Size&&a-r.Begin<=r.Size-n&&(!write||r.Writable))return true;}}
  return indexed?false:mapped_valid(a,n,write);
 }

};
State* active{};
bool graphics_enabled{};
thread_local bool guest_running{};
thread_local unsigned callback_depth{};
thread_local HleCallFrame* current_guest_frame{};
thread_local size_t guest_stack_size=8u<<20;
class GuestFrame final:public HleCallAdapter {
 std::shared_ptr<HleCallAdapter> inner;
public:explicit GuestFrame(std::shared_ptr<HleCallAdapter> a):inner(std::move(a)){}
 HleCallResult Invoke(HleCallFrame& f)const override{
  auto* previous=current_guest_frame;current_guest_frame=&f;
  struct Restore{HleCallFrame*& slot;HleCallFrame* old;~Restore(){slot=old;}} restore{current_guest_frame,previous};
  auto result=inner->Invoke(f);if(auto* failed=std::get_if<HleCallFailure>(&result);failed&&failed->name.empty())failed->name=Name();return result;
 }
};
std::shared_ptr<HleCallAdapter> framed(std::shared_ptr<HleCallAdapter> a){return std::make_shared<GuestFrame>(std::move(a));}
thread_local std::optional<uint64_t> thread_exit_value;
std::shared_ptr<HleCallAdapter> adapter(void* wrapper,std::string_view signature){
#include "native_adapters.inc"
 throw std::runtime_error("unknown native signature");
}
extern "C" void bb_arm_require_range(const char*,uintptr_t,size_t,int);
void footprints();
class StopBeforeSubmit final:public HleCallAdapter {
public:HleCallResult Invoke(HleCallFrame&)const override{footprints();std::puts("ARM DIAG stopped before first GPU submission");std::fflush(nullptr);_Exit(BbArmExit::BeforeGpuSubmit);}
};
class Missing final:public HleCallAdapter {
public:HleCallResult Invoke(HleCallFrame&)const override{std::fprintf(stderr,"ARM UNIMPLEMENTED: %.*s\n",int(Name().size()),Name().data());std::fflush(nullptr);_Exit(BbArmExit::UnsupportedImport);}
};
class ThreadExit final:public HleCallAdapter {
public:HleCallResult Invoke(HleCallFrame& f)const override{
 if(callback_depth)return HleCallFailure{ENOTSUP,"nested guest pthread_exit requires propagation"};
 if(std::this_thread::get_id()==active->main_thread)return HleCallFailure{ENOTSUP,"main thread exit"};
 if(!active->valid(f.rsp,8,true))return HleCallFailure{EFAULT,"thread exit stack"};
 thread_exit_value=f.gpr[7];memcpy(reinterpret_cast<void*>(f.rsp),&active->halt,8);f.gpr[0]=f.gpr[7];return true;
}
};
bool valid(void* c,uintptr_t a,size_t n,bool write){return static_cast<State*>(c)->valid(a,n,write);}
std::optional<Core::GuestExecutionRange> executable(void* c,uintptr_t address){auto& s=*static_cast<State*>(c);if(auto r=s.veneers.QueryExecutableRange(address))return r;std::shared_lock lock(s.mutex);for(auto r:s.ranges)if(r.Executable&&address>=r.Begin&&address-r.Begin<r.Size)return r;return std::nullopt;}
std::optional<FEXCore::ExecutableFileSectionInfo> code_file(void* c,uintptr_t address){auto& s=*static_cast<State*>(c);return s.code_files?s.code_files->Query(address):std::nullopt;}
void failure(void*,const HleCallFailure& f){std::fprintf(stderr,"ARM HLE STOP name=%.*s error=%d\n",int(f.name.size()),f.name.data(),f.error);std::fflush(nullptr);_Exit(BB_ARM_EXIT_HLE_FAULT);}
void module_start(unsigned index){active->module=index;std::printf("ARM MODULE begin=%u\n",index);}
int module_end(int result){std::printf("ARM MODULE end=%u result=%d\n",active->module.load(),result);return result;}
extern "C" size_t bb_arm_host_thread_count(void);
uint64_t fixture_avplayer_boundary(uint32_t end){
 static size_t before{};const auto now=bb_arm_host_thread_count();
 check(BbAvPlayerBridge::ActiveHandles()==0&&BbAvPlayerBridge::CallbackContexts()==0,"AvPlayer contexts retained");
 if(!end)before=now;else{check(now==before,"AvPlayer host TCB retained");std::printf("ARM AV FIXTURE handles=0 contexts=0 host-TCB=%zu/%zu\n",now,before);}return 0;
}
uint64_t fixture_net_nested(uintptr_t entry){
 const uint64_t args[]={11,12,13,14,15,16,17};return bb_arm_call(entry,args,7);
}
void* fixture_native_worker(void* entry){
 check(bb_arm_tcb(),"native worker TCB missing");const uint64_t args[]={1,2,3,4,5,6,7};
 return reinterpret_cast<void*>(bb_arm_call(reinterpret_cast<uintptr_t>(entry),args,7));
}
int fixture_net_workers(uintptr_t entry){
 bb_arm_require_callback("fixture.net_worker",entry);
 for(unsigned batch=0;batch<16;++batch){void* ids[4]{};
  for(auto& id:ids)check(runtime_thread_spawn(&id,fixture_native_worker,reinterpret_cast<void*>(entry),1u<<20,"net-fixture")==0,"native worker spawn failed");
  for(auto id:ids){void* value{};check(runtime_thread_join_spawned(id,&value)==0&&value==nullptr,"native worker guest callback failed");}
 }
 std::puts("ARM NET FIXTURE 64 native workers, low TCB/TLS, seven-argument guest and nested callbacks passed");return 0;
}
void fixture_gpu_hooks(){BbGuestHooks::Install(nullptr);}
void entry_exit(){std::puts("ARM guest entry finalizer reached");}
uintptr_t veneer(State& s,std::shared_ptr<HleCallAdapter> a){auto v=s.veneers.Allocate(*a);check(std::holds_alternative<uint64_t>(v),"veneer failed");return std::get<uint64_t>(v);}
struct Driver {
 std::vector<uint8_t> code;std::vector<size_t> failures;
 void bytes(std::initializer_list<uint8_t> v){code.insert(code.end(),v);}
 void imm(uint64_t n){for(int i=0;i<8;++i)code.push_back(n>>(i*8));}
 void call(uint64_t p){bytes({0x48,0xb8});imm(p);bytes({0xff,0xd0});}
 void rdi(uint64_t n){bytes({0x48,0xbf});imm(n);}
 void rsi(uint64_t n){bytes({0x48,0xbe});imm(n);}
};

#include "guest_resource_runtime.h"
#include "extra_heaps_runtime.h"
#include "allocation_probe_runtime.h"
#include "dev_travel_runtime.h"
#include "game_menu_runtime.h"
void footprints(){task_vm_info_data_t t{};mach_msg_type_number_t n=TASK_VM_INFO_COUNT;if(task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&t,&n)==KERN_SUCCESS)std::printf("ARM MEM footprint=%llu resident=%llu virtual=%llu\n",t.phys_footprint,t.resident_size,t.virtual_size);}
void fault(int sig,siginfo_t* info,void* context){
 // Match the original probe's PROT_NONE data traps, never executable HLE bytes.
 // Keep an explicitly admitted native recovery scope's existing EFAULT behavior.
 if((sig==SIGBUS||sig==SIGSEGV)&&info&&active&&!runtime_fault_recover&&active->missing_data_ready.load(std::memory_order_acquire)){
  const auto address=reinterpret_cast<uintptr_t>(info->si_addr);
  for(const auto& range:active->missing_data_ranges)if(address>=range.first&&address-range.first<range.second){
   constexpr char message[]="ARM UNIMPLEMENTED DATA IMPORT: protected unresolved data access\n";
   write(2,message,sizeof(message)-1);_Exit(BB_ARM_EXIT_UNSUPPORTED_IMPORT);
  }
 }

 if(AetherPS4::Fex::HandleGuestSignal(sig,info,context))return;
 const auto esr=static_cast<ucontext_t*>(context)->uc_mcontext->__es.__esr;
 const bool alignment=sig==SIGBUS&&AetherPS4::Fex::IsDarwinDataAlignmentEsr(esr);
 if(!alignment&&graphics_enabled&&bbgpu_handle_fault(context,info->si_addr))return;
 if((sig==SIGBUS||sig==SIGSEGV)&&runtime_fault_recover){auto* recovery=runtime_fault_recover;runtime_fault_recover=nullptr;siglongjmp(*recovery,1);}
 auto* uc=static_cast<ucontext_t*>(context);auto pc=arm_thread_state64_get_pc(uc->uc_mcontext->__ss);uint64_t rip{};
 if(AetherPS4::Fex::PrototypeQueryLiveGuestRip(pc,&rip)){char line[256];int n=snprintf(line,sizeof(line),"ARM FATAL signal=%d esr=%#x fault=%p host_pc=%#llx live_guest=%#llx offset=%#llx\n",sig,esr,info->si_addr,pc,rip,active?rip-active->image:0);write(2,line,n);}
 char regs[1024];if(AetherPS4::Fex::BachataDumpGuestRegisters(regs,sizeof(regs))){write(2,regs,strlen(regs));write(2,"\n",1);}
 struct sigaction action{};action.sa_handler=SIG_DFL;sigemptyset(&action.sa_mask);sigaction(sig,&action,nullptr);raise(sig);
}
void install_faults(){for(int sig:{SIGBUS,SIGSEGV}){struct sigaction action{};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO|SA_ONSTACK;sigemptyset(&action.sa_mask);check(sigaction(sig,&action,nullptr)==0,"signal setup failed");}}
void protect_host(State& s){
 mach_vm_address_t at=0x7000000000ull;constexpr uintptr_t limit=0xfc00000000ull;
 while(at<limit){mach_vm_size_t bytes{};vm_region_basic_info_data_64_t info{};mach_msg_type_number_t count=VM_REGION_BASIC_INFO_COUNT_64;mach_port_t object{};auto result=mach_vm_region(mach_task_self(),&at,&bytes,VM_REGION_BASIC_INFO_64,(vm_region_info_t)&info,&count,&object);if(object)mach_port_deallocate(mach_task_self(),object);if(result!=KERN_SUCCESS||at>=limit)break;auto end=std::min<uintptr_t>(at+bytes,limit);check(end>at,"bad native VM range");
  check(runtime_memory_exclude(at,end)==0,"native range exclusion failed");s.protected_host.emplace_back(at,end);at+=bytes;
 }
 std::printf("ARM MEMORY protected_host_ranges=%zu\n",s.protected_host.size());
}
}
extern "C" void bb_arm_add_native(const char* name,void* original,void* wrapper,const char* signature){
 check(active,"native registry missing");auto a=active->registry.Register(std::string_view(name)=="thread_exit"?std::shared_ptr<HleCallAdapter>(std::make_shared<ThreadExit>()):framed(adapter(wrapper,signature)),std::string("runtime.")+name);check(bool(a),"native registration failed");active->native[reinterpret_cast<uintptr_t>(original)]=std::move(a);
}
extern "C" int bb_arm_guest_active(void){return guest_running||callback_depth;}
extern "C" void bb_arm_set_stack_size(uint64_t bytes){
 if(bytes>64u<<20){fputs("ARM unsupported guest stack size\n",stderr);_Exit(BB_ARM_EXIT_HLE_FAULT);}guest_stack_size=std::max<uint64_t>(bytes,1u<<20);
}
extern "C" void bb_arm_guest_sites(uint64_t out[3]){
 out[0]=out[1]=out[2]=0;if(!active||!current_guest_frame)return;size_t found=0;
 for(size_t i=0;i<400&&found<3;++i){uintptr_t at=current_guest_frame->rsp+i*8;if(!active->valid(at,8,false))break;uint64_t v;memcpy(&v,(void*)at,8);if(v>=active->image&&v<active->text_end&&executable(active,v))out[found++]=v-active->image;}
}
extern "C" int bb_arm_range(uintptr_t at,size_t bytes,int writable){return active&&active->valid(at,bytes,writable);}
extern "C" void bb_arm_require_callback(const char* name,uintptr_t at){
 if(!active||!executable(active,at)){std::fprintf(stderr,"ARM ABI STOP %s non-executable callback=%#lx\n",name,at);_Exit(BB_ARM_EXIT_HLE_FAULT);}
}
extern "C" void bb_arm_require_range(const char* name,uintptr_t at,size_t bytes,int writable){
 if(bytes&&!bb_arm_range(at,bytes,writable)){std::fprintf(stderr,"ARM ABI STOP %s pointer=%#lx bytes=%zu write=%d\n",name,at,bytes,writable);_Exit(BB_ARM_EXIT_HLE_FAULT);}
}
extern "C" void bb_arm_require_string(const char* name,const char* string,size_t limit){
 uintptr_t at=(uintptr_t)string;size_t remaining=limit;
 while(remaining){const size_t bytes=std::min<size_t>(remaining,Page-(at&(Page-1)));
  bb_arm_require_range(name,at,bytes,0);if(memchr((void*)at,0,bytes))return;
  if(at>UINTPTR_MAX-bytes)break;at+=bytes;remaining-=bytes;
 }
 std::fprintf(stderr,"ARM ABI STOP %s unterminated string\n",name);_Exit(BB_ARM_EXIT_HLE_FAULT);
}
extern "C" void bb_arm_image_info(uintptr_t* base,uintptr_t* end,uintptr_t* libc,uint64_t* size){
 if(base)*base=active?active->image:0;if(end)*end=active?active->text_end:0;if(libc)*libc=active?active->libc:0;if(size)*size=active?active->image_size:0;
}
extern "C" uintptr_t bb_arm_heap_publish(void* target,unsigned kind){
 std::shared_ptr<HleCallAdapter> a;
 switch(kind){
 case 0:a=MakeHleCallAdapter(reinterpret_cast<void*(*)(size_t)>(target));break;
 case 1:a=MakeHleCallAdapter(reinterpret_cast<void(*)(void*)>(target));break;
 case 2:case 4:a=MakeHleCallAdapter(reinterpret_cast<void*(*)(size_t,size_t)>(target));break;
 case 3:a=MakeHleCallAdapter(reinterpret_cast<void*(*)(void*,size_t)>(target));break;
 case 5:a=MakeHleCallAdapter(reinterpret_cast<int(*)(void**,size_t,size_t)>(target));break;
 default:_Exit(BB_ARM_EXIT_HLE_FAULT);
 }
 return veneer(*active,active->registry.Register(framed(a),"runtime.heap_wrapper."+std::to_string(kind)));
}
extern "C" int bb_arm_heap_valid(uintptr_t at,size_t bytes,int writable){return active&&active->valid(at,bytes,writable);}
extern "C" int bb_arm_host_owned(uintptr_t address,uint64_t size){if(address<0x7000000000ull||address>=0xf000000000ull||!size||size>0xf000000000ull-address)return 1;if(!active)return 1;for(auto [a,b]:active->protected_host)if(address<b&&a<address+size)return 1;{std::shared_lock lock(active->mutex);for(auto r:active->ranges)if(address<r.Begin+r.Size&&r.Begin<address+size)return 1;}return 0;}
extern "C" uint64_t bb_arm_call(uintptr_t entry,const uint64_t* args,size_t count){
 if(!active||!active->cpu||count>7){std::fputs("ARM callback unavailable\n",stderr);_Exit(BB_ARM_EXIT_HLE_FAULT);}
 if(!executable(active,entry)){std::fprintf(stderr,"ARM refused non-guest callback entry=%#lx\n",entry);_Exit(BB_ARM_EXIT_HLE_FAULT);}
 Core::GuestExecutionResult result;
 if(guest_running){++callback_depth;result=active->cpu->CallGuest(entry,{args,count});--callback_depth;}
 else {
  Mapping stack(guest_stack_size);Core::GuestExecutionRequest request;request.Rip=entry;request.Rsp=stack.at()+stack.size-(count==7?24:8);request.FsBase=request.GsBase=reinterpret_cast<uintptr_t>(bb_arm_tcb());request.Rflags=2;{std::unique_lock lock(active->mutex);active->ranges.push_back({stack.at(),stack.size,false,true});}request.MappedRanges=active->bootstrap(stack.at(),stack.size);
  const std::array<size_t,6> registers{7,6,2,1,8,9};for(size_t i=0;i<std::min(count,size_t(6));++i)request.Gpr[registers[i]]=args[i];if(count==7)memcpy(reinterpret_cast<void*>(request.Rsp+8),&args[6],8);memcpy(reinterpret_cast<void*>(request.Rsp),&active->halt,8);
  {ScopedGuestStack stack_validation(stack.at(),stack.size);guest_running=true;result=active->cpu->Run(request);guest_running=false;}{std::unique_lock lock(active->mutex);std::erase_if(active->ranges,[&](auto r){return r.Begin==stack.at();});}
 }
 if(auto* f=std::get_if<Core::GuestExecutionFailure>(&result)){if(f->Error==ECANCELED&&thread_exit_value){auto value=*thread_exit_value;thread_exit_value.reset();return value;}std::fprintf(stderr,"ARM callback failed entry=%#lx stage=%d errno=%d\n",entry,int(f->Stage),f->Error);_Exit(BB_ARM_EXIT_HLE_FAULT);}
 if(thread_exit_value){auto value=*thread_exit_value;thread_exit_value.reset();return value;}
 return std::get<Core::GuestExecutionState>(result).Gpr[0];
}
extern "C" uint64_t bb_arm_net_call(uintptr_t entry,const uint64_t*args,size_t count){
 const bool attach=bb_arm_tcb()==nullptr;
 if(attach)runtime_thread_attach_host("net-callback");
 auto result=bb_arm_call(entry,args,count);
 if(attach)runtime_thread_detach_host();
 return result;
}
int game_main(int argc,char** argv){
 try{
  setvbuf(stdout,nullptr,_IOLBF,0);setvbuf(stderr,nullptr,_IOLBF,0);
  install_faults();
  fs::path boot,state,app0,content,seed_user,startup,settings_template;bool modules_only=false,graphics=false,heap_fixture=false,resume=false;
  fs::path launcher_patches,launcher_config;const bool launcher_compat=argc>1&&!std::string_view(argv[1]).starts_with("--");unsigned launcher_timeout=0;bool launcher_strict_imports=false;
  if(launcher_compat){
   check(getenv("BB_ARM_LAUNCHER_COMPAT")&&std::string_view(getenv("BB_ARM_LAUNCHER_COMPAT"))=="1","launcher compatibility requires explicit isolated selection");
   check(getenv("BB_RESTART_SUPERVISED")&&std::string_view(getenv("BB_RESTART_SUPERVISED"))=="1","launcher requires original restart supervisor");
   std::vector<std::string_view> arguments;for(int i=1;i<argc;++i)arguments.emplace_back(argv[i]);auto options=BbLauncherCli::Parse(arguments);
   boot=options.boot;state=options.user;app0=options.app0;content=options.content;launcher_patches=options.patches;graphics=options.graphics;launcher_timeout=options.timeout;launcher_strict_imports=options.strict_imports;resume=true;
  }else
  for(int i=1;i<argc;++i){std::string_view a=argv[i];if((a=="--heap-fixture"||a=="--authored-fixture")){heap_fixture=true;continue;}if(a=="--resume-state"){resume=true;continue;}if(a=="--graphics"){graphics=true;continue;}if(a=="--modules-only"){modules_only=true;continue;}check(i+1<argc,"missing argument");if(a=="--boot")boot=argv[++i];else if(a=="--state")state=argv[++i];else if(a=="--settings-template")settings_template=argv[++i];else if(a=="--startup-patches")startup=argv[++i];else if(a=="--seed-user")seed_user=argv[++i];else if(a=="--app0")app0=argv[++i];else if(a=="--content-profile")content=argv[++i];else throw std::runtime_error("unsupported argument");}
  check(!boot.empty()&&!state.empty()&&state.is_absolute(),"require --boot --state");
  if(launcher_compat){check(getenv("BB_CONFIG"),"original launcher configuration missing");launcher_config=getenv("BB_CONFIG");check(getenv("BB_GAME_DIR")&&fs::is_directory(getenv("BB_GAME_DIR")),"original launcher game root missing");}
  const auto private_path=[](const fs::path& path){auto relative=fs::canonical(path).lexically_relative("/Users/Shared/BloodEchoesDevelopment");return !relative.empty()&&relative!="."&&*relative.begin()!="..";};
  if(launcher_compat)check(private_path(launcher_config)&&fs::is_regular_file(launcher_config)&&!fs::is_symlink(launcher_config),"original launcher config must be a Shared regular file");
  if(heap_fixture)check(fs::canonical(boot.parent_path())==fs::canonical(fs::path(BB_PROTOTYPE_ROOT)/"fixtures"),"heap test hooks only for authored fixtures");
  check((startup.empty()||private_path(startup))&&(launcher_patches.empty()||private_path(launcher_patches))&&private_path(boot)&& (content.empty()||private_path(content)) && (app0.empty()||private_path(app0)),"runtime inputs must stay in Shared private development tree");
  if(launcher_compat){
   if(const char*pad=getenv("BB_ARM_QA_PAD_FILE")){check(private_path(pad)&&!fs::is_symlink(pad)&&fs::is_regular_file(pad),"launcher QA pad must be a private regular file");setenv("BB_PAD_FILE",pad,1);setenv("BB_PAD_FILE_ONLY","1",1);}

  }
  const char* session_nonce=getenv("BB_ARM_SESSION_NONCE");
  check(!resume||(session_nonce&&strlen(session_nonce)==32),"resume requires its private supervisor session");
  const auto session_app0=launcher_compat?fs::path(getenv("BB_GAME_DIR")):app0;
  check(!launcher_compat||private_path(session_app0),"launcher base game outside Shared");
  const std::string identity="ARMQA1\n"+fs::canonical(boot).string()+"\n"+session_app0.string()+"\n"+content.string()+"\n"+(session_nonce?session_nonce:"")+"\n";
  BbSession::Prepare(state,fs::path(BB_PROTOTYPE_ROOT)/"state",identity,resume);
  if(const char* qos=getenv("BB_ARM_QA_CPU_QOS")){
   check(launcher_compat&&getenv("BB_ARM_QA_CPU_QOS_EXPERIMENT")&&std::string_view(getenv("BB_ARM_QA_CPU_QOS_EXPERIMENT"))=="1","CPU QoS experiment requires explicit private test admission");
   int mode=0;check(!bb_qa_cpu_qos_parse(qos,&mode)&&!bb_qa_cpu_qos_apply(mode),"CPU QoS experiment admission failed");
   std::printf("ARM QA CPU QoS=%s; explicit guest-thread admission, native callback pools unchanged\n",qos);
  }
  if(launcher_compat){
   BbLauncherCpu::ConfigureExecutionDefaults(graphics);
   std::printf("ARM LAUNCHER original online admission=%d; original selection retained\n",BbLauncherCpu::ConfigureNetworkAdmission());
  }
  if(resume)std::puts("ARM QA reusing verified private session; seed will not overwrite working progress");
  if(!seed_user.empty()&&!resume){
   check(fs::canonical(seed_user)==fs::canonical("/Users/Shared/BloodEchoesDevelopment/qa-seed-user"),"only fixed QA seed is allowed");
   auto source=seed_user/"savedata";check(fs::is_directory(source)&&!fs::is_symlink(source),"QA seed missing or linked");
   for(auto& file:fs::recursive_directory_iterator(source))check(!file.is_symlink()&&(file.is_directory()||file.is_regular_file()),"QA seed must contain regular files only");
   fs::copy(source,state/"savedata",fs::copy_options::recursive);
   // This fixed QA seed originated in the CUSA00207 test profile. Adapt only
   // the fresh working copy to this prototype's CUSA03173 save namespace.
   auto qa_user=state/"savedata/1";std::vector<std::pair<fs::path,fs::path>> moves;
   if(fs::is_directory(qa_user))for(auto& file:fs::directory_iterator(qa_user)){
    auto name=file.path().filename().string();
    if(name=="CUSA00207"||name=="CUSA00207.memory"){
     auto dest=qa_user/(std::string("CUSA03173")+name.substr(9));check(!fs::exists(dest),"QA target title must be absent");moves.emplace_back(file.path(),dest);
    }
   }
   for(auto [from,to]:moves)fs::rename(from,to);
   std::printf("ARM QA seed copied into fresh state; title namespaces adapted=%zu\n",moves.size());
  }
  if(graphics&&launcher_compat){
   check(fs::is_regular_file(launcher_config)&&!fs::is_symlink(launcher_config),"original private launcher config missing/linked");
   std::puts("ARM LAUNCHER original configuration retained; no template regeneration");
  }else if(graphics){
   check(!settings_template.empty(),"graphics requires the private launcher settings template");
   auto settings=fs::canonical(settings_template);auto allowed=fs::canonical(fs::path(BB_PROTOTYPE_ROOT)/"fixtures");
   check(settings.parent_path()==allowed&&fs::is_regular_file(settings),"settings template outside private fixtures");
   if(!resume)fs::copy_file(settings,state/"bbport.ini");
   else check(fs::is_regular_file(state/"bbport.ini")&&!fs::is_symlink(state/"bbport.ini"),"private resumed settings missing or linked");
   std::printf("ARM CONFIG existing launcher settings copied into fresh QA state: %s\n",settings.c_str());
  }
  setenv("BB_CONFIG",(launcher_compat?launcher_config:state/"bbport.ini").c_str(),1);if(!runtime_net_enabled())setenv("BB_ONLINE","0",1);if(!graphics&& !heap_fixture){setenv("BB_QUICKSAVE","0",1);unsetenv("BB_QUICKLOAD_PENDING");}
  else {const char* enabled=getenv("BB_QUICKSAVE");if(!enabled||strcmp(enabled,"1")){setenv("BB_QUICKSAVE","0",1);unsetenv("BB_QUICKLOAD_PENDING");}}setenv("BB_NATIVE_GPU",graphics?"1":"0",1);setenv("BB_TEXTURE_RELEASED_STORAGE","0",1);
  if(launcher_compat)std::printf("ARM LAUNCHER HLE published lookup=%d; explicit legacy override retained\n",BbLauncherCpu::ConfigurePublishedLookup());
  Boot b(boot);check(b.format=="BBPROBE5"&&b.caps==1,"require linked BBPROBE5");
  std::vector<std::unique_ptr<Mapping>> missing_data_storage; // outlives the FEX State
  State s;active=&s;
  protect_host(s);
  // Process-lifetime VA reservation. No resident pages or backing is touched.
  // MemoryMirror's whole-range destructor is intentionally not run over native Taken spans.
  auto* guest_reservation=new BbRemote::MemoryMirror;std::string_view reservation_error;
  check(guest_reservation->ReserveAround(0x7000000000ull,0xf000000000ull,reservation_error),"guest VA reservation failed");
  std::printf("ARM MEMORY guest_va_reserved=512GiB sparse taken=%zu\n",guest_reservation->Taken().size());
  BbLauncherGame::Metadata game_metadata{"CUSA03173","Bloodborne","01.09","CUSA03173",0};
  if(launcher_compat){game_metadata=BbLauncherGame::Read(app0/"sce_sys/param.sfo");check(game_metadata.serial=="CUSA03173","unsupported launcher title");std::printf("ARM LAUNCHER game=%s saves=%s version=%s\n",game_metadata.serial.c_str(),game_metadata.saves.c_str(),game_metadata.version.c_str());}
  runtime_start(b.caps);if(!content.empty()){std::ifstream input(content,std::ios::binary);char magic[8];uint32_t values[5];check(bool(input.read(magic,8))&&!memcmp(magic,"BBCONT01",8)&&bool(input.read(reinterpret_cast<char*>(values),sizeof(values)))&&input.peek()==EOF,"invalid content profile");runtime_content_configure(values);}bb_arm_runtime_register();if(app0.empty()){app0=state/"empty-app0";fs::create_directory(app0);}runtime_file_configure(app0.c_str(),state.c_str());runtime_savedata_configure(game_metadata.saves.c_str());
  if((runtime_net_enabled()||getenv("BB_DEV_WORLD_WARPER_DIR"))&&getenv("BB_ARM_AUTO_CONTINUE"))runtime_quicksave_begin_auto_continue();
  bbgpu_register_kernel();
  Mapping image(b.size),halt(Page),driver(Page),params(Page);s.image=image.at();s.image_size=b.size;s.libc=image.at()+b.modules.at(0).base;for(auto segment:b.segments)if(segment.flags&1)s.text_end=std::max<uintptr_t>(s.text_end,image.at()+segment.address+segment.size);s.halt=halt.at();b.load(image.ptr);*static_cast<uint8_t*>(halt.ptr)=0xf4;
  uint64_t net_sdk{};memcpy(&net_sdk,static_cast<uint8_t*>(image.ptr)+b.procparam+16,8);runtime_net_configure(app0.c_str(),game_metadata.serial.c_str(),game_metadata.title.c_str(),game_metadata.version.c_str(),static_cast<unsigned char*>(image.ptr),b.size,uint32_t(net_sdk));
  std::vector<uintptr_t> imported(b.names.size());std::vector<uint64_t> kinds(b.names.size());for(auto x:b.relocs)if(x.kind){check(!kinds[x.value]||kinds[x.value]==x.kind,"mixed import kind");kinds[x.value]=x.kind;}
  if(graphics){
   const char* child=getenv("BB_GPU_PROCESS");check(child&&private_path(child),"GPU child must come from the Shared development tree");
   uint64_t sdk{};memcpy(&sdk,static_cast<uint8_t*>(image.ptr)+b.procparam+16,8);BbGpuConfig config{game_metadata.title.c_str(),game_metadata.serial.c_str(),getenv("BB_GPU_USER_DIR"),uint32_t(sdk),game_metadata.attributes,1920,1080};graphics_enabled=true;check(bbgpu_init(&config)==0,"native GPU init failed");std::printf("ARM GPU frontend ready symbols=%u\n",bbgpu_symbol_count());}
  unsigned native=0,linked=0,missing=0;std::ofstream imports(state/"imports.tsv");
  for(size_t i=0;i<b.names.size();++i){
   auto host=runtime_resolve(b.names[i].c_str(),kinds[i]==2);std::unique_ptr<HleCallAdapter> net_adapter(host?static_cast<HleCallAdapter*>(bb_arm_net_adapter(host)):nullptr);auto av_adapter=host&&kinds[i]!=2?BbAvPlayerBridge::Adapter(runtime_import_name(b.names[i].c_str()),host):nullptr;std::string route;
   if(heap_fixture&&b.names[i]=="bb.prototype.extra_heap_check"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(ArmExtraHeaps::FixtureCheck)),"fixture.extra_heap_check"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.extra_heap_during"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(ArmExtraHeaps::FixtureDuring)),"fixture.extra_heap_during"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.travel_check"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(ArmTravel::FixtureCheck)),"fixture.travel_check"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.resource_bad_check"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(ArmResourceRuntime::FixtureBadCheck)),"fixture.resource_bad_check"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.resource_check"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(ArmResourceRuntime::FixtureCheck)),"fixture.resource_check"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.avplayer_boundary"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(fixture_avplayer_boundary)),"fixture.avplayer_boundary"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.net_workers"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(fixture_net_workers)),"fixture.net_workers"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.net_nested"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(fixture_net_nested)),"fixture.net_nested"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.heap_install"){imported[i]=veneer(s,s.registry.Register(framed(MakeHleCallAdapter(&BbHeapSites::InstallCounters)),"fixture.heap_install"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.gpu_hooks"){imported[i]=veneer(s,s.registry.Register(MakeHleCallAdapter(fixture_gpu_hooks),"fixture.gpu_hooks"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.gpu_clock_wrong_role"){imported[i]=veneer(s,s.registry.Register(MakeHleCallAdapter(&BbRemote::Back::ReadTsc),"fixture.gpu_clock_wrong_role"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.gpu_clock"){imported[i]=veneer(s,s.registry.Register(MakeHleCallAdapter(&Libraries::Kernel::sceKernelReadTsc),"fixture.gpu_clock"));++native;route="authored-fixture";}
   else if(heap_fixture&&b.names[i]=="bb.prototype.heap_live"){imported[i]=veneer(s,s.registry.Register(MakeHleCallAdapter(&BbHeapSites::LiveAllocations),"fixture.heap_live"));++native;route="authored-fixture";}
   else if(net_adapter){auto net=std::shared_ptr<HleCallAdapter>(std::move(net_adapter));imported[i]=veneer(s,s.registry.Register(framed(net),std::string("net.")+runtime_import_name(b.names[i].c_str())));++native;route="native-net";}
   else if(av_adapter){
    imported[i]=veneer(s,s.registry.Register(framed(av_adapter),std::string("avplayer.")+runtime_import_name(b.names[i].c_str())));++native;route="native-avplayer";}
   else if(host&&kinds[i]==2){imported[i]=host;route="native-data";auto page=host&~uintptr_t(Page-1);s.ranges.push_back({page,Page,false,true});}
   else if(host&&s.native.contains(host)){imported[i]=veneer(s,s.native.at(host));++native;route="native";}
   else if(host&&bb_arm_gpu_adapter(runtime_import_name(b.names[i].c_str()),host)){auto gpu_adapter=bb_arm_gpu_adapter(runtime_import_name(b.names[i].c_str()),host);
    const std::string_view name=runtime_import_name(b.names[i].c_str());
    if(getenv("BB_ARM_STOP_AT_SUBMIT")&&(name=="sceGnmSubmitCommandBuffers"||name=="sceGnmSubmitAndFlipCommandBuffers"||name=="sceGnmDingDong"||name=="sceVideoOutSubmitFlip"))gpu_adapter=std::make_shared<StopBeforeSubmit>();
    auto a=s.registry.Register(gpu_adapter,std::string("gpu.")+std::string(name));imported[i]=veneer(s,a);++native;route="native-gpu";}
   else if(auto it=b.bindings.find(i);it!=b.bindings.end()){check(it->second.second==kinds[i],"linked kind mismatch");imported[i]=image.at()+it->second.first;++linked;route="guest-linked";}
   else if(kinds[i]==2){
    auto guard=std::make_unique<Mapping>(Page,PROT_NONE);imported[i]=guard->at();
    s.missing_data_ranges.emplace_back(guard->at(),guard->size);missing_data_storage.push_back(std::move(guard));
    ++missing;route="missing-data";
    std::printf("ARM IMPORT protected-data index=%zu address=%#lx virtual_bytes=%zu resident_payload=0\n",i,imported[i],Page);
   }
   else {auto a=s.registry.Register(std::make_shared<Missing>(),std::string("missing:")+runtime_import_name(b.names[i].c_str())+":"+b.names[i]);imported[i]=veneer(s,a);++missing;route="missing";}
   imports<<i<<'\t'<<runtime_import_name(b.names[i].c_str())<<'\t'<<route<<'\t'<<b.names[i]<<'\n';
  }
  imports.close();
  check(!launcher_strict_imports||!missing,"strict-imports: unresolved imports; guest entry refused");
  for(auto x:b.relocs){uint64_t value=x.kind?imported[x.value]+x.addend:image.at()+x.value;memcpy(static_cast<uint8_t*>(image.ptr)+x.target,&value,8);}
  if(!startup.empty())BbStartup::Apply(startup,image.ptr,b);
  if(!launcher_patches.empty())BbLauncherPatch::Apply(launcher_patches,image.ptr,b);
  const bool code_profile=getenv("BB_ARM_CPU_CODEMAP")&&std::string_view(getenv("BB_ARM_CPU_CODEMAP"))=="1";
  const char* cache_path=getenv("BB_ARM_CPU_CACHE");
  const bool cache_identity=code_profile||(cache_path&&*cache_path)||(getenv("BB_ARM_CPU_CACHE_IDENTITY")&&std::string_view(getenv("BB_ARM_CPU_CACHE_IDENTITY"))=="1");
  if(cache_identity){
   s.code_files=std::make_unique<BbStaticCode::Files>(b,image.at(),std::span(static_cast<const std::byte*>(image.ptr),size_t(b.size)));
  }
  std::unique_ptr<Mapping> resource_stubs;
  if(const char* enabled=getenv("BB_ARM_RESOURCE_HOOKS");enabled&&enabled[0]=='1'){
   check(graphics||heap_fixture,"resource detours require graphics or authored fixture");
   resource_stubs=ArmResourceRuntime::Install(s,image,b,heap_fixture);
  }
  std::unique_ptr<Mapping> extra_heap_stubs;
  if(const char* extra=getenv("BB_ARM_EXTRA_HEAPS");extra&&extra[0]=='1'){
   check(heap_fixture||bool(resource_stubs),"extra heaps require original resource hooks");
   extra_heap_stubs=ArmExtraHeaps::Install(s,image,b,heap_fixture);
  }
  std::unique_ptr<Mapping> travel_stubs;
  if(getenv("BB_DEV_WORLD_WARPER_DIR")||(heap_fixture&&getenv("BB_ARM_TRAVEL_FIXTURE"))){
   check(graphics||heap_fixture,"travel requires graphics or authored fixture");
   travel_stubs=ArmTravel::Install(s,image,b,state,heap_fixture);
  }
  std::unique_ptr<Mapping> allocation_probe;
  if((getenv("BB_ARM_ALLOCATION_AUDIT")&&graphics)||(heap_fixture&&getenv("BB_ARM_ALLOCATION_AUDIT_FIXTURE"))){
   check(heap_fixture||bool(travel_stubs),"allocation audit requires private validated travel session");
   allocation_probe=ArmAllocation::Install(s,image,b,heap_fixture);
  }
  std::unique_ptr<Mapping> game_menu_stubs;
  if(graphics&&!heap_fixture)ArmGameMenu::Install(b,image,game_menu_stubs);
  if(s.code_files){
   s.code_files->Finalize(std::span(static_cast<const std::byte*>(image.ptr),size_t(b.size)));
   s.code_files->Report(state/"cpu-code-manifest.json");
   if(code_profile)s.code_output=std::make_unique<CodeMapOutput>(state/("cpu-code-"+std::to_string(getpid())+".map"));
   printf("ARM CPU CODEMAP static_bytes=%zu excluded_mutated_pages=%zu relocated_pages=%zu retained_hash_bytes=%zu; profiling only, JIT budget unchanged\n",s.code_files->EligibleBytes(),s.code_files->ModifiedPages(),s.code_files->RelocationPages(),s.code_files->RetainedHashBytes());
  }
  runtime_quicksave_bind(image.ptr,b.size);
  std::printf("ARM BOOT image=%#lx bytes=%llu native=%u linked=%u missing=%u\n",image.at(),b.size,native,linked,missing);
  for(auto m:b.modules)if(m.tls_module)runtime_set_module_tls(m.tls_module,static_cast<uint8_t*>(image.ptr)+m.tls_address,m.tls_filesz,m.tls_memsz);
  runtime_set_main_tls(static_cast<uint8_t*>(image.ptr)+b.main_tls[0],b.main_tls[1],b.main_tls[2],b.main_tls[3]);runtime_thread_attach_main();runtime_set_procparam(static_cast<uint8_t*>(image.ptr)+b.procparam);
  auto start=veneer(s,s.registry.Register(MakeHleCallAdapter(module_start),"loader.module_start"));auto finish=veneer(s,s.registry.Register(MakeHleCallAdapter(module_end),"loader.module_end"));auto exit=veneer(s,s.registry.Register(MakeHleCallAdapter(entry_exit),"loader.entry_exit"));
  Driver d;d.bytes({0x48,0x83,0xec,0x08});
  for(size_t i=0;i<b.modules.size();++i){d.rdi(i);d.call(start);d.bytes({0x31,0xff,0x31,0xf6,0x31,0xd2});d.call(image.at()+b.modules[i].init);d.bytes({0x89,0xc7});d.call(finish);d.bytes({0x85,0xc0,0x0f,0x85});d.failures.push_back(d.code.size());d.bytes({0,0,0,0});}
  if(!modules_only){
   uint64_t arguments[]={1,params.at()+64,0};memcpy(params.ptr,arguments,sizeof(arguments));strcpy(static_cast<char*>(params.ptr)+64,"/app0/eboot.bin");d.rdi(params.at());d.rsi(exit);std::printf("ARM ENTRY prepared offset=%#llx\n",b.entry);d.call(image.at()+b.entry);
  }
  auto done=d.code.size();d.bytes({0x48,0x83,0xc4,0x08,0xc3});for(auto at:d.failures){int32_t relative=done-at-4;memcpy(d.code.data()+at,&relative,4);}check(d.code.size()<=driver.size,"driver overflow");memcpy(driver.ptr,d.code.data(),d.code.size());
  if(graphics)for(auto segment:b.segments)if(!(segment.flags&1)){
   check(bbgpu_share_range(static_cast<uint8_t*>(image.ptr)+segment.address,prototype_round_page(segment.size),PROT_READ|PROT_WRITE)==0,"image data share failed");
  }
  check(mprotect(image.ptr,image.size,PROT_NONE)==0,"image hide failed");
  for(auto segment:b.segments){check(segment.address%Page==0,"segment needs split host-page support");auto size=prototype_round_page(segment.size);check(size<=image.size-segment.address,"rounded segment overflow");check(!(segment.flags&1&&segment.flags&2),"WX guest segment unsupported");int protection=(segment.flags&4?PROT_READ:0)|(segment.flags&2?PROT_WRITE:0);check(mprotect(static_cast<uint8_t*>(image.ptr)+segment.address,size,protection)==0,"image permissions failed");s.ranges.push_back({image.at()+segment.address,size,bool(segment.flags&1),bool(segment.flags&2)});}
  uintptr_t heap{};uint64_t heap_size{};bb_arm_low_heap_range(&heap,&heap_size);s.ranges.push_back({heap,size_t(heap_size),false,true});
  mprotect(halt.ptr,halt.size,PROT_READ);mprotect(driver.ptr,driver.size,PROT_READ);s.ranges.push_back({halt.at(),halt.size,true,false});s.ranges.push_back({driver.at(),driver.size,true,false});s.ranges.push_back({params.at(),params.size,false,true});
  if(graphics||heap_fixture){
   uintptr_t text_end=image.at();for(auto segment:b.segments)if(segment.flags&1)text_end=std::max<uintptr_t>(text_end,image.at()+segment.address+segment.size);
   bbprototype_configure_heap(image.at(),text_end,image.at()+b.modules.at(0).base,bb_arm_call,bb_arm_heap_publish,bb_arm_heap_valid);
  }
  auto vr=s.veneers.GetExecutableRanges();printf("ARM VENEER layout=%s guest_pages=%zu guest_virtual_bytes=%zu; no native execution of guest stubs\n",s.veneers.LayoutName(),vr.size(),vr.size()*Page);s.ranges.insert(s.ranges.end(),vr.begin(),vr.end());
  s.bridge=std::make_unique<HleGuestBridge>(s.registry,valid,&s,failure,&s,executable,&s,s.code_files?code_file:nullptr,&s,s.code_output.get());auto cpu=Core::FexGuestCpuBackend::Create(*s.bridge);check(std::holds_alternative<std::unique_ptr<Core::FexGuestCpuBackend>>(cpu),"FEX create failed");s.cpu=std::move(std::get<std::unique_ptr<Core::FexGuestCpuBackend>>(cpu));
  if(const char* reserve=getenv("BB_ARM_JIT_RESERVE_MIB")){
   check(std::string_view(reserve)=="16"||std::string_view(reserve)=="128","unsupported startup JIT reserve");
   const uint64_t bytes=std::string_view(reserve)=="128"?128*1024*1024:16*1024*1024;
   auto configured=s.cpu->ConfigureStartupJitReserve(bytes);check(std::holds_alternative<uint64_t>(configured),"startup JIT reserve unavailable");
   printf("ARM JIT startup_virtual_reserve=%llu max_capacity_unchanged=1 relocation_collection=0; virtual reserve is not resident RAM\n",bytes);
  }
  if(s.code_files){
   const auto environment=s.cpu->CacheEnvironment();check(std::holds_alternative<AetherPS4::Fex::CpuCacheEnvironment>(environment),"startup cache environment unavailable");
   const auto env=std::get<AetherPS4::Fex::CpuCacheEnvironment>(environment);
   BbCpuCache::Identity key{};key.runtime_build=BbCacheBuild::Runtime;key.patched_code=s.code_files->Identity();
   std::vector<std::byte> modules;const auto& source=s.code_files->SourceIdentity();BbCacheInputs::Append(modules,"linked-image",std::string_view(reinterpret_cast<const char*>(source.data()),source.size()));
   if(runtime_net_enabled()){
    const char* module=getenv("BB_NET_MODULE");check(module&&private_path(module),"cache identity requires private online module");auto digest=BbCacheInputs::Build(module);
    check(std::any_of(digest.begin(),digest.end(),[](auto b){return b!=0;}),"cache online module digest unavailable");
    BbCacheInputs::Append(modules,"online-module",std::string_view(reinterpret_cast<const char*>(digest.data()),digest.size()));
   }else BbCacheInputs::Append(modules,"online-module","disabled");
   key.module_set=BbCpuCache::Hash(modules);key.bridge_abi=BbCacheInputs::RegistryABI(BbCacheBuild::Bridge,s.registry,sizeof(HleCallFrame),alignof(HleCallFrame),s.veneers.LayoutName());
   key.host_features=env.host_features;key.codegen_config=env.codegen_config;
   check(BbCpuCache::Valid(key),"cache identity incomplete");
   auto usage=s.cpu->InspectStartupCodeCache();check(std::holds_alternative<AetherPS4::Fex::StartupCpuCacheInfo>(usage),"startup JIT metadata unavailable");
   const auto budget=std::get<AetherPS4::Fex::StartupCpuCacheInfo>(usage).current_jit_bytes;
   std::ofstream metadata(state/"cpu-cache-environment.json");
   metadata<<"{\"schema\":1,\"veneer_layout\":\""<<s.veneers.LayoutName()<<"\",\"runtime_build\":\""<<BbStaticCode::Hex(key.runtime_build)<<"\",\"bridge_build\":\""<<BbStaticCode::Hex(BbCacheBuild::Bridge)<<"\",\"patched_code\":\""<<BbStaticCode::Hex(key.patched_code)<<"\",\"module_set\":\""<<BbStaticCode::Hex(key.module_set)<<"\",\"bridge_abi\":\""<<BbStaticCode::Hex(key.bridge_abi)<<"\",\"host_features\":\""<<BbStaticCode::Hex(key.host_features)<<"\",\"codegen_config\":\""<<BbStaticCode::Hex(key.codegen_config)<<"\",\"software_tso\":"<<(env.software_tso?"true":"false")<<",\"normal_jit\":"<<(env.normal_jit?"true":"false")<<",\"initial_jit_bytes\":"<<budget<<"}\n";metadata.flush();check(bool(metadata),"cache environment report failed");
   if(cache_path&&*cache_path){
    bool attempted=false;
    std::error_code cache_error;const auto canonical_cache=fs::canonical(cache_path,cache_error);
    if(!cache_error&&canonical_cache.string().starts_with("/Users/Shared/BloodEchoesDevelopment/")&&fs::is_regular_file(cache_path)&&!fs::is_symlink(cache_path)){
     std::ifstream input(cache_path,std::ios::binary|std::ios::ate);const auto bytes=input.tellg();
     if(bytes>0&&bytes<=BbCpuCache::PrefixBytes+1024*1024){
      std::vector<std::byte> data(static_cast<size_t>(bytes));input.seekg(0);input.read(reinterpret_cast<char*>(data.data()),data.size());
      if(input){
       std::vector<std::pair<uint64_t,uint64_t>> spans;for(auto r:s.code_files->Sections())spans.emplace_back(r.begin,r.end);
       auto section=s.code_files->Query(image.at()+spans.front().first).value();
       auto installed=s.cpu->InstallStartupCodeCache(data,key,spans,image.at(),b.size,section.FileInfo);attempted=true;
       if(auto* detail=std::get_if<AetherPS4::Fex::StartupCpuCacheInfo>(&installed)){
        printf("ARM CPU CACHE installed file_bytes=%llu code_bytes=%llu blocks=%llu initial_jit_bytes=%llu software_tso=1\n",detail->file_bytes,detail->code_bytes,detail->blocks,detail->initial_jit_bytes);
       }else{const auto error=std::get<Core::GuestExecutionFailure>(installed);printf("ARM CPU CACHE rejected stage=%u error=%d; ordinary JIT retained\n",unsigned(error.Stage),error.Error);}
      }
     }
    }
    if(!attempted)puts("ARM CPU CACHE ignored missing/linked/oversized private input; ordinary JIT retained");
   }
  }
  s.ranges.push_back(s.cpu->ReturnRange());s.ranges.push_back(s.cpu->CallbackReturnRange());
  std::sort(s.ranges.begin(),s.ranges.end(),[](auto a,auto b){return a.Begin<b.Begin;});s.ranges.erase(std::unique(s.ranges.begin(),s.ranges.end(),[](auto a,auto b){return a.Begin==b.Begin&&a.Size==b.Size&&a.Executable==b.Executable&&a.Writable==b.Writable;}),s.ranges.end());
  s.permanent.Build(s.ranges);s.permanent_count=s.ranges.size();std::printf("ARM RANGE static-index=%d bytes=%zu; guest VMAs and temporary stacks retain live validation\n",s.fast_ranges,s.permanent.Bytes());
  std::optional<std::jthread> mapping_status_thread;
  if(BbFexMap::Enabled())mapping_status_thread.emplace([](std::stop_token stop){
   for(unsigned i=0;i<600&&!stop.stop_requested();++i){
    BbFexMap::Checkpoint();
    for(unsigned step=0;step<10&&!stop.stop_requested();++step)usleep(100000);
   }
  }); // Owned/joined before FEX state and static diagnostic counters can be destroyed.
  if(getenv("BB_ARM_MEM_DIAG"))std::thread([]{for(;;){usleep(250000);footprints();}}).detach();
  if(launcher_timeout){signal(SIGALRM,[](int){const char msg[]="ARM LAUNCHER watchdog timeout\n";write(2,msg,sizeof(msg)-1);_Exit(BB_ARM_EXIT_WATCHDOG);});alarm(launcher_timeout);}
  s.missing_data_ready.store(true,std::memory_order_release);
  footprints();auto result=bb_arm_call(driver.at(),nullptr,0);std::printf("ARM DRIVER returned=%llu modules_only=%d\n",result,modules_only);footprints();active=nullptr;return result==0?0:2;
 }catch(const std::exception& e){std::fprintf(stderr,"ARM GAME STOP: %s\n",e.what());return 1;}
}

int main(int argc,char** argv){
 setvbuf(stdout,nullptr,_IOLBF,0);setvbuf(stderr,nullptr,_IOLBF,0);
 try {
  std::vector<std::string_view> args;for(int i=1;i<argc;++i)args.emplace_back(argv[i]);
  if(!BbLauncherCli::NeedsMainThread(args))return game_main(argc,argv);
  struct Arguments{int argc;char** argv;} arguments{argc,argv};
  pthread_t worker;
  const int error=BbLauncherThread::Start(&worker,[](void* p)->void* {
   const auto* a=static_cast<const Arguments*>(p);std::exit(game_main(a->argc,a->argv));
  },&arguments);
  if(error){std::fprintf(stderr,"ARM GAME STOP: cannot start game worker: %s\n",strerror(error));return 1;}
  pthread_detach(worker);
  // Original Cocoa/SDL task queue: controller events and process Dock hiding.
  bbgpu_main_thread_loop();return 1;
 }catch(const std::exception& e){std::fprintf(stderr,"ARM GAME STOP: %s\n",e.what());return 1;}
}
