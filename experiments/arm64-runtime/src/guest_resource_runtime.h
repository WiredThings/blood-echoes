// SPDX-License-Identifier: GPL-2.0-or-later
// Included inside game_runtime.cpp's anonymous namespace. Guest jumps enter
// HLE veneers; every call of the original x86 body returns through FEX.
namespace ArmResourceRuntime {
bool payload_gate_enabled{};

using namespace BbArmHooks;
std::array<U,3> originals{};
thread_local unsigned heap_depth{};
std::atomic<bool> metadata_complete{true};
std::array<std::atomic<U>,unsigned(Kind::Count)> calls{};
bool GpuRange(U address,U size){
 if(!address||!size||size>=(U(1)<<32)||address>=(U(1)<<40)||size>(U(1)<<40)-address)return false;
 if(!BbGuestHooks::FexGpuScope(address))return false;
 U at=address,end=address+size;
 while(at<end){int prot{},type{};uintptr_t next{};if(!runtime_memory_vma_info(at,&prot,&type,&next)||next<=at||type<0||!(prot&0x30))return false;at=std::min<U>(next,end);}
 return true;
}
void LoseCoverage(){
 if(metadata_complete.exchange(false)){BbPayload::Get().Configure(false);BbGuestHooks::ConfigureFexHooks(false);fputs("ARM RESOURCE invalid metadata; lifetime reclamation disabled\n",stderr);}
}
bool ReadWord(U address,U& value){if(!active||!active->valid(address,8,false)){LoseCoverage();return false;}memcpy(&value,reinterpret_cast<const void*>(address),8);return true;}
bool NodeRange(U node,U& start,U& size){
 if(!node)return false;
 if(node>UINT64_MAX-0x20||!active->valid(node,0x20,false)){LoseCoverage();return false;}
 U next{},end{};if(!ReadWord(node+8,start)||!ReadWord(node+0x18,next))return false;
 if(!next)return false;
 if(next>UINT64_MAX-16||!ReadWord(next+8,end))return false;
 if(end<=start||end-start>=(U(1)<<32)||start>=(U(1)<<40)||end>(U(1)<<40)){LoseCoverage();return false;}
 size=end-start;return true;
}
void RangeAllocated(U node){
 ++calls[unsigned(Kind::RangeAlloc)];U start{},size{};if(!NodeRange(node,start,size))return;
 if(BbResourceLifetime::Enabled())BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Range,start,size);
 BbGuestHooks::FexFreshRange(start,size);
}
void RangeReleased(U node){
 ++calls[unsigned(Kind::RangeFree)];U start{},size{};if(!NodeRange(node,start,size)||!BbResourceLifetime::Enabled())return;
 auto tag=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Range,start);
 if(!tag||tag.size!=size)tag={start,0,0,BbResourceLifetime::Domain::Range};
 BbResourceLifetime::Get().Release(tag);
}
void HeapReturned(U address,U size){
 ++calls[unsigned(Kind::HeapReturn)];
 if(address&&size&&size<(U(1)<<32))BbGuestHooks::FexFreshRange(address,size);
 // All heap entry points are covered before this path is enabled. These
 // wrapper return sites only notify writes; never double-count allocations.
}
U HeapAllocated(Kind kind,U allocator,U size,U alignment=0,U completed_result=0){
 ++calls[unsigned(kind)];const bool top=heap_depth++==0;
 const U args[]={allocator,size,alignment};const auto index=unsigned(kind)-unsigned(Kind::HeapFree);
 U address=completed_result?completed_result:bb_arm_call(originals[index],args,kind==Kind::HeapPlain?2:3);--heap_depth;
 if(top&&BbResourceLifetime::Enabled()&&GpuRange(address,size))BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Heap,address,size);
 return address;
}
U HeapPlain(U allocator,U size,U completed_result){return HeapAllocated(Kind::HeapPlain,allocator,size,0,completed_result);}
U HeapAligned(U allocator,U size,U alignment){return HeapAllocated(Kind::HeapAligned,allocator,size,alignment);}
void HeapFreed(U allocator,U address){
 ++calls[unsigned(Kind::HeapFree)];const bool track=BbResourceLifetime::Enabled()&&BbGuestHooks::FexGpuScope(address);
 auto tag=track?BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,address):BbResourceLifetime::Tag{};
 if(track&&!tag)tag={address,0,0,BbResourceLifetime::Domain::Heap};
 const U args[]={allocator,address};bb_arm_call(originals[0],args,2);
 if(track)BbResourceLifetime::Get().Release(tag);
}
// Separate adapters selected ONCE at install. Ordinary wrappers have no new
// per-allocation branch, lock, counter or host transition.
U HeapPlainProtected(U allocator,U size,U completed_result){
 check(completed_result==0,"payload gate cannot observe an already-run leaf allocation");
 BbPayload::Gate::Mutation mutation{BbPayload::Get()};return HeapPlain(allocator,size,0);
}
U HeapAlignedProtected(U allocator,U size,U alignment){
 BbPayload::Gate::Mutation mutation{BbPayload::Get()};return HeapAligned(allocator,size,alignment);
}
void HeapFreedProtected(U allocator,U address){
 BbPayload::Gate::Mutation mutation{BbPayload::Get()};HeapFreed(allocator,address);
}
U Copy(U dst,U src,U size){
 ++calls[unsigned(Kind::Copy)];
 if(!size)return dst;
 bb_arm_require_range("resource.copy.source",src,size,0);bb_arm_require_range("resource.copy.destination",dst,size,1);
 runtime_memory_note_write(dst,size);memcpy(reinterpret_cast<void*>(dst),reinterpret_cast<const void*>(src),size);runtime_memory_note_write(dst,size);return dst;
}
class ResourceTimed final:public HleCallAdapter {
 std::shared_ptr<HleCallAdapter> inner;unsigned kind;
public:ResourceTimed(std::shared_ptr<HleCallAdapter> value,unsigned k):inner(std::move(value)),kind(k){}
 HleCallResult Invoke(HleCallFrame& f)const override{
  // Created ONLY for explicit diagnostics. Normal installs keep the original
  // adapter and incur neither this virtual dispatch nor per-call timing.
  BbAudit::Scope timer{33,U(kind)|(U(heap_depth)<<8),0};
  return inner->Invoke(f);
 }
};
class ClobberInline final:public HleCallAdapter {
 std::shared_ptr<HleCallAdapter> inner;
public:explicit ClobberInline(std::shared_ptr<HleCallAdapter> value):inner(std::move(value)){}
 HleCallResult Invoke(HleCallFrame& f)const override{
  auto result=inner->Invoke(f);if(std::holds_alternative<HleCallFailure>(result))return result;
  for(unsigned i:{0,1,2,6,7,8,9,10,11})f.gpr[i]=0xfeedface;
  for(auto& x:f.xmm)x={0,0};return result;
 }
};
std::unique_ptr<Mapping> Install(State& state,Mapping& image,const Boot& boot,bool authored){
 BbPayload::Get().Configure(false);
 BbGuestHooks::ConfigureFexHooks(false);
 for(const char* key:{"BB_FREE_CHECK","BB_LABEL_TRAP","BB_GUEST_HOOK_TRAPS"})if(const char* e=getenv(key);e&&e[0]=='1')throw std::runtime_error("resource hooks conflict with guest diagnostic patch");
 payload_gate_enabled=[] {const char* e=getenv("BB_ARM_PAYLOAD_GATE");return e&&e[0]=='1';}();
 check(!payload_gate_enabled||BbResourceLifetime::Enabled(),"payload gate requires lifetime metadata");
 const bool fast_dispatch=[] {const char* e=getenv("BB_ARM_RESOURCE_FAST");return e&&e[0]=='1';}();
 const bool leaf_dispatch=[] {const char* e=getenv("BB_ARM_RESOURCE_LEAF");return e&&e[0]=='1';}();
 check(!leaf_dispatch||fast_dispatch,"leaf dispatch requires guarded dispatch");
 if(leaf_dispatch&&!authored)check(boot.covered(PlainLeafOffset,PlainLeafSize,true)&&MatchCodeDigest(std::span(static_cast<const B*>(image.ptr)+PlainLeafOffset,size_t(PlainLeafSize)),PlainLeafDigest),"resource leaf profile digest mismatch");
 if(fast_dispatch&&!authored){
  // Full prefix proves the small-alignment path only returns failure or
  // tails to the plain entry; no observed allocation can bypass both hooks.
  constexpr unsigned char proof[]={0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x31,0xc0,0xc4,0xe2,0xf0,0xf3,0xca,0xf,0x85,0xcc,0x2,0x0,0x0,0x31,0xc0,0x48,0x39,0x77,0x18,0xf,0x82,0xc0,0x2,0x0,0x0,0x48,0x83,0xfa,0x10,0x77,0xe,0x5b,0x41,0x5c,0x41,0x5d,0x41,0x5e,0x41,0x5f,0xe9,0x9a,0xfc,0xff,0xff};
  check(boot.covered(0x2085a60,sizeof(proof),true)&&!memcmp(static_cast<const B*>(image.ptr)+0x2085a60,proof,sizeof(proof)),"resource aligned tail proof mismatch");
 }
 auto sites=authored?AuthoredSites():GameSites();
 for(auto&site:sites){bool code=false;for(auto seg:boot.segments)if((seg.flags&1)&&site.offset>=seg.address&&site.expected.size()<=seg.size&&site.offset-seg.address<=seg.size-site.expected.size())code=true;check(code,"resource hook outside guest executable segment");}
 auto mapping=std::make_unique<Mapping>(16384);
 std::array<std::shared_ptr<HleCallAdapter>,7> adapters{MakeHleCallAdapter(Copy),MakeHleCallAdapter(RangeAllocated),MakeHleCallAdapter(HeapReturned),MakeHleCallAdapter(RangeReleased),MakeHleCallAdapter(HeapFreed),MakeHleCallAdapter(HeapPlain),MakeHleCallAdapter(HeapAligned)};
 if(payload_gate_enabled){adapters[4]=MakeHleCallAdapter(HeapFreedProtected);
  adapters[5]=MakeHleCallAdapter(HeapPlainProtected);adapters[6]=MakeHleCallAdapter(HeapAlignedProtected);}
 std::array<U,7> targets{};
 for(unsigned i=0;i<targets.size();++i){if(std::getenv("BB_STUTTER_AUDIT"))adapters[i]=std::make_shared<ResourceTimed>(adapters[i],i);if(authored&&(i==1||i==2||i==3))adapters[i]=std::make_shared<ClobberInline>(adapters[i]);targets[i]=veneer(state,state.registry.Register(framed(adapters[i]),"resource.hook."+std::to_string(i)));}
 auto plan=Prepare(std::span(static_cast<const B*>(image.ptr),size_t(boot.size)),image.at(),mapping->at(),mapping->size,sites,targets,false,fast_dispatch,leaf_dispatch,payload_gate_enabled);
 memcpy(mapping->ptr,plan.stubs.data(),plan.stubs.size());check(mprotect(mapping->ptr,mapping->size,PROT_READ)==0,"resource stub read permission failed");
 originals=plan.originals;
 state.ranges.push_back({mapping->at(),mapping->size,true,false});
 for(auto& patch:plan.patches){
#ifdef BB_CPU_BACKEND_BBCPU
  bbcpu_note_patch(patch.offset,patch.bytes.size(),"resource-hooks");
#endif
  memcpy(static_cast<B*>(image.ptr)+patch.offset,patch.bytes.data(),patch.bytes.size());
 }
 BbGuestHooks::ConfigureFexHooks(true);
 if(payload_gate_enabled)BbPayload::Get().Configure(metadata_complete.load());
 if(authored)BbGuestHooks::Install([](U,U){});
 printf("ARM RESOURCE 10/10 startup FEX detours; stub-bytes=%zu; no native x86 traps; reclaim waits for remote callback\n",plan.stubs.size());
 printf("ARM RESOURCE leaf-dispatch=%d; guarded original executes once; GPU result gets native lifetime observation\n",leaf_dispatch&&!payload_gate_enabled);
 printf("ARM RESOURCE dispatch-filter=%d; outside-arena frees stay guest; small alignment keeps original validation and hooked plain tail\n",fast_dispatch&&!payload_gate_enabled);
 printf("ARM PAYLOAD gate=%d requested-fast=%d requested-leaf=%d effective-fast=%d effective-leaf=%d; full host wrapper spans original allocator through metadata publication\n",payload_gate_enabled,fast_dispatch,leaf_dispatch,fast_dispatch&&!payload_gate_enabled,leaf_dispatch&&!payload_gate_enabled);
 return mapping;
}
std::thread payload_fixture_reader;
std::atomic<bool> payload_fixture_held{},payload_fixture_verified{};
BbResourceLifetime::Tag payload_fixture_tag{};
U FixturePayloadArm(){
 check(payload_gate_enabled&&BbPayload::Get().Ready(),"payload fixture gate not ready");
 U address{};check(ReadWord(active->image+0x4380,address),"payload fixture address unreadable");
 payload_fixture_tag=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,address);
 check(bool(payload_fixture_tag),"payload fixture lacks live original tag");
 payload_fixture_reader=std::thread([address]{
  const bool ok=BbPayload::Get().TryWithLiveHeap(BbResourceLifetime::Get(),payload_fixture_tag,address,128,[&](auto current){
   check(current==payload_fixture_tag,"payload fixture owner changed");payload_fixture_held=true;
   const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
   while(!BbPayload::Get().Read().waiting&&std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
   check(BbPayload::Get().Read().waiting>0,"actual guest free did not reach mutation gate");
   U original_frees{};check(ReadWord(active->image+0x4428,original_frees)&&original_frees==0,"original free ran under live payload read");
   payload_fixture_verified=true;return true;
  });check(ok,"payload fixture read lease rejected");
 });
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
 while(!payload_fixture_held&&std::chrono::steady_clock::now()<deadline)std::this_thread::yield();
 check(payload_fixture_held,"payload fixture reader did not acquire");return 0;
}
U FixturePayloadCheck(){
 if(payload_fixture_reader.joinable())payload_fixture_reader.join();
 const auto stats=BbPayload::Get().Read();
 printf("ARM PAYLOAD actual-fex entered=%llu completed=%llu held=%llu waiting=%llu readers=%llu original-free-blocked=%u\n",stats.entered,stats.completed,stats.held,stats.waiting,stats.readers,unsigned(payload_fixture_verified.load()));
 return payload_fixture_verified&&stats.entered>0&&stats.entered==stats.completed&&!stats.held&&!stats.waiting&&!stats.readers&&BbResourceLifetime::Get().Status(payload_fixture_tag)!=BbResourceLifetime::State::Live?0:1;
}
U FixtureBadCheck(){return !metadata_complete.load()&&!BbResourceLifetime::ReclaimReady()?0:1;}
U FixtureCheck(){
 auto s=BbResourceLifetime::Get().Read();BbResourceLifetime::Report();
 for(unsigned i=0;i<calls.size();++i)printf("ARM RESOURCE fixture kind=%u calls=%llu\n",i,calls[i].load());
 U original_frees{};check(ReadWord(active->image+0x4428,original_frees),"fixture free counter unreadable");
 const bool fast=!payload_gate_enabled&&std::getenv("BB_ARM_RESOURCE_FAST")&&std::getenv("BB_ARM_RESOURCE_FAST")[0]=='1';
 const bool leaf=!payload_gate_enabled&&std::getenv("BB_ARM_RESOURCE_LEAF")&&std::getenv("BB_ARM_RESOURCE_LEAF")[0]=='1';
 U cpu_frees{},odd_frees{},gpu_plain{},cpu_plain{},odd_plain{};
 if(!ReadWord(active->image+0x4728,cpu_frees)||!ReadWord(active->image+0x4768,odd_frees)||!ReadWord(active->image+0x4420,gpu_plain)||!ReadWord(active->image+0x4720,cpu_plain)||!ReadWord(active->image+0x4760,odd_plain))return 1;
 if(original_frees!=7||cpu_frees!=2||odd_frees!=1||gpu_plain!=2||cpu_plain!=2||odd_plain!=1||calls[4]!=(fast?6:10)||calls[5]!=(leaf?3:5)||calls[6]!=(fast?1:3))return 1;
 return (calls[0]>=2&&calls[1]>=1&&calls[2]>=3&&calls[3]>=1&&calls[4]>=2&&calls[5]>=1&&calls[6]>=1&&s.domains[0].allocations>=1&&s.domains[0].releases>=1&&s.domains[1].allocations>=2&&s.domains[1].releases>=2&&s.domains[1].reuses>=1&&metadata_complete&&BbResourceLifetime::ReclaimReady())?0:1;
}
} // namespace ArmResourceRuntime
