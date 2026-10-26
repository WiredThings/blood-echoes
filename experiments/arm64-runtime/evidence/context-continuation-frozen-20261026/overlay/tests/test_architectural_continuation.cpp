// SPDX-License-Identifier: GPL-2.0-or-later
// Reuse the existing authored FEX fixture plumbing, not its PS4 context data.
#define main OrdinaryUnwindFixtureMain
#include "test_ordinary_unwind.cpp"
#undef main
namespace A=AetherPS4::Fex;
using Snapshot=BbArchitecturalContinuation::State;
struct Transfer final:HleCallAdapter{
 uintptr_t target{},target_stack{},foreign_stack{},data{};int mode{};
 mutable unsigned destructed{},capture_calls{};mutable Snapshot captured{},queued{};
 HleCallResult Invoke(HleCallFrame&f)const override{
  struct Guard{unsigned&n;~Guard(){++n;}}guard{destructed};
  Snapshot s{};check(A::PrototypeCaptureArchitecturalState(&s)==0,"real spilled capture");++capture_calls;captured=s;
  s.rip=target;s.gpr[0]=0xabcde1234567890ULL;s.gpr[4]=target_stack;s.rflags^=1;
  for(size_t n=0;n<s.gpr.size();++n)if(n!=0&&n!=2&&n!=4)s.gpr[n]=0x12340000+n;
  for(size_t n=0;n<s.xmm.size();++n)s.xmm[n]=(__uint128_t(0x9988776655443322ULL+n)<<64)|(0x1122334455667788ULL+n);
  s.fcw=0x027f;s.mxcsr=0x5f80;s.abridged_ftw=0xff;s.x87_flags.fill(0);s.x87_flags[8]=1;
  for(size_t n=0;n<s.mm.size();++n)s.mm[n]={0x8000000000000000ULL+n,0x3fff};
  int expected=0;
  switch(mode){case 1:s.owner^=1;expected=EINVAL;break;case 2:--s.boundary;expected=EINVAL;break;
   case 3:s.fsbase^=16;expected=ENOTSUP;break;case 4:s.rip=data;expected=EFAULT;break;
   case 5:s.gpr[4]=foreign_stack;expected=EFAULT;break;case 6:s.mxcsr=0x80000000;expected=EINVAL;break;
   case 7:s.rflags^=0x3000;expected=EINVAL;break;case 8:s.avx_supported=!s.avx_supported;expected=ENOTSUP;break;
   case 9:s.x87_flags[11]=8;expected=EINVAL;break;case 10:s.fcw=0xffff;expected=EINVAL;break;case 13:expected=ENOTSUP;break;default:break;}
  queued=s;auto error=A::PrototypeQueueArchitecturalContinuation(&s);check(error==expected,"queue precise result");
  if(!expected)check(A::PrototypeQueueArchitecturalContinuation(&s)==EALREADY,"one pending continuation");
  if(mode==12)check(mprotect(reinterpret_cast<void*>(target_stack-16384),16384,PROT_NONE)==0,"protect queued target after FEX mapping admission");
  if(mode==11)return HleCallFailure{EIO,"authored failure after queued continuation"};
  f.gpr[0]=expected;return true;
 }
};
struct NestedCapture final:HleCallAdapter{
 mutable unsigned denied{};
 HleCallResult Invoke(HleCallFrame&f)const override{
  Snapshot s{};s.rip=0x1234;check(A::PrototypeCaptureArchitecturalState(&s)==ENOTSUP&&s.rip==0x1234,"nested capture does not fabricate snapshot");check(A::PrototypeQueueArchitecturalContinuation(&s)==ENOTSUP,"nested queue rejected");++denied;f.gpr[0]=0;return true;
 }
};
struct InvokeNested final:HleCallAdapter{
 Core::FexGuestCpuBackend*cpu{};uintptr_t entry{};
 HleCallResult Invoke(HleCallFrame&f)const override{auto result=cpu->CallGuest(entry,{});check(std::get_if<GuestExecutionState>(&result)!=nullptr,"nested guest returned normally");f.gpr[0]=0;return true;}
};
static std::optional<Core::GuestExecutionRange> executableQuery(void*ctx,uintptr_t p){
 for(auto r:static_cast<Ranges*>(ctx)->list)if(r.Executable&&!r.Writable&&p>=r.Begin&&p-r.Begin<r.Size)return r;
 return{};
}
int main(){try{
 Snapshot outside{};outside.rip=0x1234;check(A::PrototypeCaptureArchitecturalState(&outside)==ENOTSUP&&outside.rip==0x1234,"outside capture has no mutation");check(A::PrototypeQueueArchitecturalContinuation(&outside)==ENOTSUP,"outside queue rejected");
 Map code,stack(65536),foreign(65536),output;Ranges ranges;HleCallRegistry registry;HleVeneerAllocator veneers;
 auto transfer=std::make_shared<Transfer>();auto op=registry.Register(transfer,"authored.architectural-continuation");auto entry=veneers.Allocate(*op);check(std::holds_alternative<u64>(entry),"veneer");
 auto probe=std::make_shared<NestedCapture>();auto probe_op=registry.Register(probe,"authored.nested-capture");auto probe_entry=veneers.Allocate(*probe_op);check(std::holds_alternative<u64>(probe_entry),"nested probe veneer");auto invoke=std::make_shared<InvokeNested>();auto invoke_op=registry.Register(invoke,"authored.invoke-nested-capture");auto invoke_entry=veneers.Allocate(*invoke_op);check(std::holds_alternative<u64>(invoke_entry),"nested invoker veneer");
 unsigned char caller[]={0x48,0xb8,0,0,0,0,0,0,0,0,0xff,0xd0,0xf4};auto address=std::get<u64>(entry);memcpy(caller+2,&address,8);memcpy(code.ptr,caller,sizeof(caller));
 auto nested_address=std::get<u64>(invoke_entry);memcpy(caller+2,&nested_address,8);memcpy(reinterpret_cast<void*>(code.at(512)),caller,sizeof(caller));
 // Same operation but syscall;NOP;RET is not the proven project RET veneer.
 memcpy(reinterpret_cast<void*>(code.at(1024)),reinterpret_cast<const void*>(address),16);*reinterpret_cast<unsigned char*>(code.at(1039))=0x90;*reinterpret_cast<unsigned char*>(code.at(1040))=0xc3;auto changed_entry=code.at(1024);memcpy(caller+2,&changed_entry,8);memcpy(reinterpret_cast<void*>(code.at(768)),caller,sizeof(caller));
 const unsigned char target[]={0x48,0x0f,0xae,0x02,0xf4};memcpy(reinterpret_cast<void*>(code.at(256)),target,sizeof(target));check(mprotect(code.ptr,code.size,PROT_READ)==0,"seal code");
 ranges.list={{code.at(),code.size,true,false},{stack.at(),stack.size,false,true},{foreign.at(),foreign.size,false,true},{output.at(),output.size,false,true}};for(auto r:veneers.GetExecutableRanges())ranges.list.push_back(r);
 HleGuestBridge bridge(registry,valid,&ranges,nullptr,nullptr,executableQuery,&ranges);auto made=Core::FexGuestCpuBackend::Create(bridge);check(std::holds_alternative<std::unique_ptr<Core::FexGuestCpuBackend>>(made),"FEX create");auto cpu=std::move(std::get<std::unique_ptr<Core::FexGuestCpuBackend>>(made));ranges.list.push_back(cpu->ReturnRange());ranges.list.push_back(cpu->CallbackReturnRange());
 invoke->cpu=cpu.get();invoke->entry=std::get<u64>(probe_entry);
 transfer->target=code.at(256);transfer->target_stack=stack.at(stack.size-8);transfer->foreign_stack=foreign.at(128);transfer->data=output.at();
 GuestExecutionRequest req;req.Rip=code.at();req.Rsp=stack.at(stack.size-128);req.Rflags=0x202;req.Gpr[2]=output.at();req.FsBase=foreign.at();req.GsBase=foreign.at(256);req.MappedRanges=ranges.list;
 for(size_t n=0;n<16;++n)req.Xmm[n]={0x100+n,0x200+n};
 for(int mode=0;mode<11;++mode){transfer->mode=mode;ScopedGuestStack owned(stack.at(),stack.size);auto result=cpu->Run(req);auto* state=std::get_if<GuestExecutionState>(&result);check(state&&state->StopReason==Core::GuestStopReason::Halted,"real FEX halted");
  if(!mode){std::cerr<<"CONTINUATION final rip="<<std::hex<<state->Rip<<" rax="<<state->Gpr[0]<<" rsp="<<state->Rsp<<" target="<<transfer->target<<" expected_rsp="<<transfer->target_stack<<std::dec<<"\n";check(state->Gpr[0]==transfer->queued.gpr[0]&&state->Rsp==transfer->target_stack,"actual nonreturn GPR/RSP transfer");check(state->Xmm[0][0]==0x1122334455667788ULL&&state->Xmm[0][1]==0x9988776655443322ULL,"actual XMM transfer");check((state->Rflags&0xcd5)==(transfer->queued.rflags&0xcd5),"actual flags transfer");uint16_t fcw;memcpy(&fcw,output.ptr,2);check(fcw==0x027f,"actual x87 FCW transfer");}
  else{check(state->Gpr[0]!=0xabcde1234567890ULL&&state->Xmm[0]==req.Xmm[0],"rejection has no partial architectural transfer");}
  if(!mode){for(size_t n=0;n<16;++n){if(n!=4)check(state->Gpr[n]==transfer->queued.gpr[n],"every GPR restored including RCX R10 R11");check(state->Xmm[n][0]==0x1122334455667788ULL+n&&state->Xmm[n][1]==0x9988776655443322ULL+n,"all XMM restored");}uint32_t mxcsr;memcpy(&mxcsr,reinterpret_cast<char*>(output.ptr)+24,4);check(mxcsr==0x5f80,"actual MXCSR restored");check(*reinterpret_cast<unsigned char*>(output.at(4))==0xff,"actual x87 tag restored");check(word(output.at(),32)==0x8000000000000000ULL,"actual x87 register restored");}
 }
 {ScopedGuestStack owned(stack.at(),stack.size);transfer->mode=11;word(transfer->target_stack-8,0,0xdeadbeef);auto result=cpu->Run(req);auto* failure=std::get_if<GuestExecutionFailure>(&result);check(failure&&failure->Error==EIO,"failed HLE cancels queued transfer");check(word(transfer->target_stack-8,0)==0xdeadbeef,"failed HLE never writes transfer target");}
 {ScopedGuestStack owned(stack.at(),stack.size);transfer->mode=12;transfer->target_stack=stack.at(16384);auto result=cpu->Run(req);check(mprotect(stack.ptr,16384,PROT_READ|PROT_WRITE)==0,"restore transfer target slot");auto* failure=std::get_if<GuestExecutionFailure>(&result);check(failure&&failure->Error==EFAULT,"unwritable target stops before architectural commit");}
 {ScopedGuestStack owned(stack.at(),stack.size);auto nested_req=req;nested_req.Rip=code.at(512);auto result=cpu->Run(nested_req);check(std::get_if<GuestExecutionState>(&result)!=nullptr&&probe->denied==1,"real nested callback rejected capture and queue");}
 {ScopedGuestStack owned(stack.at(),stack.size);transfer->mode=13;auto changed=req;changed.Rip=code.at(768);auto result=cpu->Run(changed);auto* state=std::get_if<GuestExecutionState>(&result);check(state&&state->Gpr[0]==ENOTSUP&&state->Xmm[0]==req.Xmm[0],"unproven return veneer rejected before state change");}
 check(transfer->destructed==14,"all native RAII guards returned");std::cout<<"{\"cases\":15,\"assertions\":"<<checks<<",\"real_fex\":true,\"architectural_continuation\":true,\"PS4_signal_frame_tested\":false,\"sigreturn_registered\":false,\"game_started\":false}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
