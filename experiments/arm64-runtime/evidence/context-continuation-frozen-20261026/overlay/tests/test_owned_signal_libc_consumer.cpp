// SPDX-License-Identifier: GPL-2.0-or-later
// Runs fixed libc consumer instructions read from the existing private boot.
// No original bytes are stored in this source or exported by this test.
#define main OrdinaryUnwindFixtureMain
#include "test_ordinary_unwind.cpp"
#undef main
#include "owned_signal_libc_contract.h"
namespace L=BbOwnedSignalLibc;
struct OwnedRead final:HleCallAdapter{
 Ranges*ranges{};mutable unsigned reads{};
 HleCallResult Invoke(HleCallFrame&f)const override{
  check(valid(ranges,f.gpr[6],8,false)&&valid(ranges,f.gpr[2],8,true),"real libc bounded memory callback");
  memcpy(reinterpret_cast<void*>(f.gpr[2]),reinterpret_cast<const void*>(f.gpr[6]),8);++reads;f.gpr[0]=0;return true;
 }
};
int main(int argc,char**argv){try{
 struct sigaction action{};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO;sigemptyset(&action.sa_mask);check(sigaction(SIGBUS,&action,nullptr)==0&&sigaction(SIGSEGV,&action,nullptr)==0,"FEX fault handling");
 check(argc==2,"existing private boot argument");std::ifstream boot(argv[1],std::ios::binary);check(bool(boot),"boot opened");
 auto get=[&]{u64 value;check(bool(boot.read(reinterpret_cast<char*>(&value),8)),"bounded boot word");return value;};
 boot.seekg(8);auto image_size=get();boot.seekg(96);auto modules=get();check(modules>=1&&modules<=9,"bounded module table");auto libc_base=get(),libc_size=get();check(libc_size==878112,"known libc module extent");boot.seekg(0,std::ios::end);auto image_offset=u64(boot.tellg())-image_size;
 Map libc(1024*1024),code,stack(65536),data;check(libc_size<=libc.size&&libc_base<=image_size&&libc_size<=image_size-libc_base,"module inside image");boot.seekg(image_offset+libc_base);check(bool(boot.read(reinterpret_cast<char*>(libc.ptr),libc_size)),"read only existing linked libc image");
 auto image=std::span(reinterpret_cast<const std::byte*>(libc.ptr),libc_size);check(L::Admit(image),"exact fixed libc consumers admitted");
 Ranges ranges;HleCallRegistry registry;HleVeneerAllocator veneers;auto reader=std::make_shared<OwnedRead>();reader->ranges=&ranges;auto read_op=registry.Register(reader,"authored.fixed-libc-memory-read");auto read_entry=veneers.Allocate(*read_op);check(std::holds_alternative<u64>(read_entry),"reader veneer");
 auto classifier=registry.Register(std::make_shared<BbSignalReturn::Classifier>(true,immutableExecutable,&ranges),"authored.fixed-libc-PC-classifier");auto classify_entry=veneers.Allocate(*classifier);check(std::holds_alternative<u64>(classify_entry),"classifier veneer");
 // The original PLT jump is unchanged; relocate only its declared imported GOT
 // slot to the existing real HLE classifier, just as the real loader does.
 word(libc.at(),755752,std::get<u64>(classify_entry));*reinterpret_cast<unsigned char*>(code.at())=0xf4;memcpy(reinterpret_cast<void*>(code.at(256)),BbSignalReturn::SignalTail.data(),BbSignalReturn::SignalTail.size());
 check(mprotect(libc.ptr,libc.size,PROT_READ)==0&&mprotect(code.ptr,code.size,PROT_READ)==0,"consumer and stub sealed");
 ranges.list={{libc.at(),libc.size,true,false},{code.at(),code.size,true,false},{stack.at(),stack.size,false,true},{data.at(),data.size,false,true}};for(auto range:veneers.GetExecutableRanges())ranges.list.push_back(range);
 HleGuestBridge bridge(registry,valid,&ranges);auto made=Core::FexGuestCpuBackend::Create(bridge);check(std::holds_alternative<std::unique_ptr<Core::FexGuestCpuBackend>>(made),"real FEX create");auto cpu=std::move(std::get<std::unique_ptr<Core::FexGuestCpuBackend>>(made));ranges.list.push_back(cpu->ReturnRange());ranges.list.push_back(cpu->CallbackReturnRange());
 const auto frame=stack.at(4096),uc=frame+0x40,cursor=data.at(),accessors=data.at(1024);GuestExecutionRequest req;req.Rsp=stack.at(stack.size-8);req.MappedRanges=ranges.list;req.Rflags=0x202;word(req.Rsp,0,code.at());
 unsigned actual_calls=0;
 for(size_t reg=0;reg<L::RegisterOffsets.size();++reg){std::cerr<<"ACTUAL_LIBC_REGISTER "<<reg<<"\n";req.Rip=libc.at(0x546c0);req.Gpr[7]=uc;req.Gpr[6]=reg;auto result=cpu->Run(req);auto* state=std::get_if<GuestExecutionState>(&result);check(state&&state->Gpr[0]==uc+L::RegisterOffsets[reg],"actual fixed libc register field mapping");++actual_calls;}
 word(cursor,8,accessors);word(cursor,0x18,code.at(256));word(accessors,0x18,std::get<u64>(read_entry));req.Rip=libc.at(0x54510);req.Gpr[7]=cursor;auto result=cpu->Run(req);auto* state=std::get_if<GuestExecutionState>(&result);check(state&&state->Gpr[0]==2&&*reinterpret_cast<uint32_t*>(cursor+0x130)==2,"actual fixed libc classified original signal tail");++actual_calls;
 word(cursor,0x10,frame);auto* context=reinterpret_cast<L::U*>(uc);context->uc_mcontext.mc_rsp=stack.at(stack.size-128);context->uc_mcontext.mc_rip=code.at();req.Rip=libc.at(0x54570);result=cpu->Run(req);state=std::get_if<GuestExecutionState>(&result);check(state&&state->Gpr[0]==0&&reader->reads==1,"actual fixed libc signal-step callback");check(word(cursor,0x10)==context->uc_mcontext.mc_rsp&&word(cursor,0x138)==frame,"actual frame owner pointer and saved RSP");
 for(size_t reg=0;reg<L::RegisterOffsets.size();++reg)check(word(cursor,0x48+reg*8)==uc+L::RegisterOffsets[reg],"actual fixed libc signal-step all register locations");++actual_calls;
 std::cout<<"{\"actual_fixed_libc_calls\":"<<actual_calls<<",\"registers\":17,\"real_fex\":true,\"classification_and_unwind\":true,\"owned_signal_producer_tested\":false,\"sigreturn_registered\":false,\"game_started\":false}\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<"\n";return 1;}}
