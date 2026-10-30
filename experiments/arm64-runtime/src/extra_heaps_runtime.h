// SPDX-License-Identifier: GPL-2.0-or-later
namespace ArmExtraHeaps {
using U=uint64_t;
std::array<std::atomic<U>,5> completed{},before{},after{};
std::array<std::array<std::atomic<U>,5>,3> classified{};
bool FreshEnabled(){static const bool on=[] {const auto* value=getenv("BB_ARM_EXTRA_HEAPS_FRESH");return value&&value[0]=='1';}();return on;}
void ReportClasses(){for(unsigned type=0;type<3;++type)printf("ARM EXTRA classification memory=%s sizes<256,<4K,<64K,<512K,large=%llu,%llu,%llu,%llu,%llu\n",type==0?"type0":type==1?"type3":"other",classified[type][0].load(),classified[type][1].load(),classified[type][2].load(),classified[type][3].load(),classified[type][4].load());}
std::atomic<U> aba_skipped{};U fixture_during{};
U Completed(U allocator,U size,U alignment,U address,U kind){
 (void)allocator;(void)alignment;check(kind==0||kind==2||kind==3,"invalid extra heap completion kind");++completed[kind];
 if(BbResourceLifetime::Enabled()&&ArmResourceRuntime::GpuRange(address,size)){
  if(getenv("BB_ARM_EXTRA_HEAP_DIAG")){int prot{},type{};uintptr_t end{};runtime_memory_vma_info(address,&prot,&type,&end);unsigned category=type==0?0:type==3?1:2,bucket=size<256?0:size<4096?1:size<65536?2:size<524288?3:4;U n=++classified[category][bucket];if((n&16383)==0)ReportClasses();}
  BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Heap,address,size);
  // Ownership publication is not a CPU write. Existing loader/write hooks
  // remain responsible for source invalidation and asset promotion. Sending
  // extra fresh notifications is a separate legacy diagnostic control.
  if(FreshEnabled())BbGuestHooks::FexFreshRange(address,size);
 }
 return address;
}
U Before(U allocator,U address,U kind){
 (void)allocator;check(kind==1||kind==4,"invalid extra heap free kind");++before[kind];
 if(!BbResourceLifetime::Enabled()||!BbGuestHooks::FexGpuScope(address))return 0;
 return BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,address).generation;
}
void After(U address,U generation,U kind){
 check(kind==1||kind==4,"invalid extra heap released kind");++after[kind];if(!generation)return;
 auto current=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,address);
 if(current.generation==generation)BbResourceLifetime::Get().Release(current);
 else ++aba_skipped; // A replacement is never released by the old completed free.
}
void FixtureDuring(U allocator,U address){
 ++fixture_during;U flag{};check(ArmResourceRuntime::ReadWord(allocator+32,flag),"extra fixture flag");
 if(ArmResourceRuntime::GpuRange(address,128)){
  auto current=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,address);check(bool(current)&&BbResourceLifetime::Get().Status(current)==BbResourceLifetime::State::Live,"extra free published before original body");
  if(flag)BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Heap,address,128);
 }
}
U FixtureCheck(){
 U allocations{},frees{};if(!ArmResourceRuntime::ReadWord(active->image+0x4408,allocations)||!ArmResourceRuntime::ReadWord(active->image+0x4410,frees))return 1;
 auto s=BbResourceLifetime::Get().Read();printf("ARM EXTRA fixture originals=%llu/%llu completed=%llu/%llu/%llu before=%llu/%llu after=%llu/%llu aba=%llu during=%llu live=%llu\n",allocations,frees,completed[0].load(),completed[2].load(),completed[3].load(),before[1].load(),before[4].load(),after[1].load(),after[4].load(),aba_skipped.load(),fixture_during,s.domains[1].live_ranges);
 return allocations==7&&frees==6&&completed[0]==2&&completed[2]==2&&completed[3]==1&&before[1]==2&&before[4]==3&&after[1]==2&&after[4]==3&&aba_skipped==1&&fixture_during==6&&s.domains[1].allocations==6&&s.domains[1].releases==4&&s.domains[1].live_ranges==1?0:1;
}
std::unique_ptr<Mapping> Install(State& state,Mapping& image,const Boot& boot,bool authored){
 const auto sites=authored?BbExtraHeaps::FixtureOffsets:BbExtraHeaps::GameOffsets;
 for(auto site:sites)check(boot.covered(site,BbExtraHeaps::Prefix.size(),true),"extra heap hook outside executable segment");
 if(!authored){for(auto site:sites)check(boot.covered(site,BbExtraHeaps::ProfileBytes,true),"extra heap profile outside executable segment");BbExtraHeaps::ValidateProfiles(std::span(static_cast<const unsigned char*>(image.ptr),size_t(boot.size)));}
 auto mapping=std::make_unique<Mapping>(16384);
 std::array<std::shared_ptr<HleCallAdapter>,3> adapters={MakeHleCallAdapter(Completed),MakeHleCallAdapter(Before),MakeHleCallAdapter(After)};
 std::array<U,3> targets{};for(unsigned i=0;i<3;++i){if(authored&&i==2)adapters[i]=std::make_shared<ArmResourceRuntime::ClobberInline>(adapters[i]);targets[i]=veneer(state,state.registry.Register(framed(adapters[i]),"resource.extra_heap."+std::to_string(i)));}
 auto plan=BbExtraHeaps::Prepare(std::span(static_cast<const unsigned char*>(image.ptr),size_t(boot.size)),image.at(),mapping->at(),mapping->size,sites,targets);
 memcpy(mapping->ptr,plan.stubs.data(),plan.stubs.size());check(!mprotect(mapping->ptr,mapping->size,PROT_READ),"extra heap stub permission failed");state.ranges.push_back({mapping->at(),mapping->size,true,false});
 for(auto& p:plan.patches){
#ifdef BB_CPU_BACKEND_BBCPU
  bbcpu_note_patch(p.offset,p.bytes.size(),"extra-heaps");
#endif
  memcpy(static_cast<unsigned char*>(image.ptr)+p.offset,p.bytes.data(),p.bytes.size());
 }
 if(authored){BbGuestHooks::ConfigureFexHooks(true);BbGuestHooks::Install([](U,U){});}
 printf("ARM EXTRA 5/5 actual heap hooks installed, stub=%zu bytes; original guest calls once, completed frees use captured generation, no cache budget change\n",plan.stubs.size());return mapping;
}
}
