// SPDX-License-Identifier: GPL-2.0-or-later
// Real shared-memory protocol across forked processes; actual Tracker/Gate,
// actual C runtime VMA read lock and actual mirrored VmaTable. No GPU/game/assets.
#ifdef NDEBUG
#error "Live payload fixture requires assertions"
#endif
#include <cassert>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <new>
#include <cstdlib>
#include <csignal>
#include <thread>
#include <stdexcept>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include "remote/bb_live_payload_front.h"
#include "remote/bb_vma_table.h"
extern "C" {void LiveTestSetup(void);void LiveTestRemap(void);void LiveTestAlias(int);int LiveTestProtectSetup(void);void LiveTestProtectArm(void);void LiveTestProtectClear(void);void LiveTestProtectTeardown(void);void LiveTestTrapRange(uint64_t,uint64_t,unsigned,int);}
using namespace BbRemote;
using namespace BbResourceLifetime;
using namespace std::chrono_literals;
constexpr uint64_t Address=0x7000000000ull,Bytes=128;
struct Shared {
 LivePayloadShared protocol;
 LifetimeJournal journal;
 std::atomic<unsigned> phase{},free_count{},mutation_done{},mapping_done{},writer_blocked{};
};
static void Until(const std::function<bool()>& predicate) {
 const auto deadline=std::chrono::steady_clock::now()+5s;
 while(!predicate()){assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}
}
static int Child(Shared& s) {
 auto gone=[] {return getppid()==1;};
 BbPayload::Origin origin{};
 Tracker mirror;const auto first=mirror.Allocate(Domain::Heap,Address,Bytes);
 VmaTable table;table.Map(Address,Address+0x4000,{Address+0x4000,MapDirect,0x33,3,0x10000});
 assert(CaptureLivePayload(s.protocol,Address,Bytes,gone,[&](const auto& r){
  assert(r.owner==first&&r.mapping.physical==0x10000);
  assert(table.TryWithLiveDirect(r.mapping,Address,Bytes,[&](uint64_t){
   return mirror.TryWithLiveHeap(r.owner,Address,Bytes,[&](auto t){return t==r.owner;});
  }));
  origin={r.owner,r.mapping};return true;
 }));
 bool called=false;
 // Default read traps remain off, even though ordinary live identity is usable.
 assert(!WithProtectedLivePayload(s.protocol,origin,Address,Bytes,gone,[&](auto&){called=true;return true;}));
 assert(!called);
 assert(!WithLivePayload(s.protocol,BbPayload::Origin{},Address,Bytes,gone,[&](auto&){called=true;return true;}));
 assert(!called);
 assert(WithLivePayload(s.protocol,origin,Address,Bytes,gone,[&](const auto& r){
  assert(r.owner==origin.owner&&BbPayload::SameMapping(r.mapping,origin.mapping));
  s.phase.store(2,std::memory_order_release);
  Until([&]{return s.writer_blocked.load(std::memory_order_acquire)!=0;});
  assert(s.free_count.load(std::memory_order_acquire)==0);return true;
 }));
 Until([&]{return s.mutation_done.load(std::memory_order_acquire)!=0;});
 assert(!WithLivePayload(s.protocol,origin,Address,Bytes,gone,[&](auto&){called=true;return true;}));
 assert(!called);
 assert(mirror.Release(first));const auto second=mirror.Allocate(Domain::Heap,Address,Bytes);
 assert(CaptureLivePayload(s.protocol,Address,Bytes,gone,[&](const auto& r){
  assert(r.owner==second);origin={r.owner,r.mapping};return true;
 }));
 s.phase.store(3,std::memory_order_release);
 Until([&]{return s.mapping_done.load(std::memory_order_acquire)!=0;});
 assert(!WithLivePayload(s.protocol,origin,Address,Bytes,gone,[&](auto&){called=true;return true;}));
 assert(!called);
 // The backend physical mirror must also match, even for explicit capture.
 assert(CaptureLivePayload(s.protocol,Address,Bytes,gone,[&](const auto& r){
  assert(r.mapping.physical==0x14000);
  assert(!table.TryWithLiveDirect(r.mapping,Address,Bytes,[](auto){return true;}));
  table.Map(Address,Address+0x4000,{Address+0x4000,MapDirect,0x33,3,0x14000});
  assert(table.TryWithLiveDirect(r.mapping,Address,Bytes,[](auto){return true;}));
  return true;
 }));
 return 0;
}
int main() {
 unsetenv("BB_LAYER_READ_TRAPS");
 auto* shared=static_cast<Shared*>(mmap(nullptr,sizeof(Shared),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANON,-1,0));
 assert(shared!=MAP_FAILED);new(shared)Shared{};auto& s=*shared;
 LiveTestSetup();Tracker front;auto old=front.Allocate(Domain::Heap,Address,Bytes);
 BbPayload::Get().Configure(true);
 const auto child=fork();assert(child>=0);if(!child)_exit(Child(s));
 std::thread writer([&]{
  Until([&]{return s.phase.load(std::memory_order_acquire)>=2;});
  {BbPayload::Gate::Mutation mutation{BbPayload::Get()};
   s.free_count.fetch_add(1,std::memory_order_release);assert(front.Release(old));
   assert(front.Allocate(Domain::Heap,Address,Bytes).generation==2);
  }
  s.mutation_done.store(1,std::memory_order_release);
  Until([&]{return s.phase.load(std::memory_order_acquire)>=3;});
  LiveTestRemap();s.mapping_done.store(1,std::memory_order_release);
 });
 std::thread observer([&]{Until([&]{return BbPayload::Get().Read().waiting!=0;});s.writer_blocked.store(1,std::memory_order_release);});
 int status{};bool dead=false;
 auto gone=[&]{if(!dead){const auto r=waitpid(child,&status,WNOHANG);assert(r>=0);dead=r==child;}return dead;};
 const auto deadline=std::chrono::steady_clock::now()+10s;
 while(!gone()) {ServeLivePayload(s.protocol,s.journal,front,gone);assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}
 writer.join();observer.join();assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
 // A confirmed backend death while Granted releases frontend payload, Tracker,
 // and actual VMA locks without requiring Complete or a restart status.
 s.protocol.stage.store(LiveIdle);s.phase=0;
 const auto dying=fork();assert(dying>=0);
 if(!dying){CaptureLivePayload(s.protocol,Address,Bytes,[]{return false;},[&](const auto&){s.phase=5;_exit(0);return true;});_exit(2);}
 dead=false;status=0;
 auto gone2=[&]{if(!dead){const auto r=waitpid(dying,&status,WNOHANG);assert(r>=0);dead=r==dying;}return dead;};
 while(!gone2()) {ServeLivePayload(s.protocol,s.journal,front,gone2);std::this_thread::yield();}
 assert(s.phase==5&&s.protocol.stage.load()==LiveAborted);
 {BbPayload::Gate::Mutation mutation{BbPayload::Get()};}
 RuntimeLiveMapping map{};assert(runtime_memory_try_live_mapping(Address,Bytes,&map));runtime_memory_end_live_mapping();
 LiveTestAlias(1);assert(!runtime_memory_try_live_mapping(Address,Bytes,&map));LiveTestAlias(0);
 // This additional arm64 case uses real host mprotect and the authoritative
 // runtime trap table. A trap-clear thread must block until the receipt ends.
 assert(LiveTestProtectSetup());
 // Authoritative 4 KiB coverage, not merely aggregate 16 KiB host denial.
 assert(runtime_memory_try_live_mapping(Address,0x4000,&map));
 assert(runtime_memory_try_live_read_protection(Address,0x4000,32));
 runtime_memory_end_live_read_protection();runtime_memory_end_live_mapping();
 LiveTestTrapRange(Address+0x1000,0x1000,32,0);
 LiveTestTrapRange(Address+0x1000,0x1000,16,1);
 assert(runtime_memory_try_live_mapping(Address,0x4000,&map));
 assert(!runtime_memory_try_live_read_protection(Address,0x2000,32));
 assert(runtime_memory_try_live_read_protection(Address,0x1000,32));
 runtime_memory_end_live_read_protection();runtime_memory_end_live_mapping();
 LiveTestTrapRange(Address+0x1000,0x1000,16,0);
 LiveTestTrapRange(Address+0x1000,0x1000,32,1);
 assert(runtime_memory_try_live_mapping(Address,Bytes,&map));
 assert(!runtime_memory_try_live_read_protection(Address,Bytes,16));
 assert(!runtime_memory_try_live_read_protection(Address+Bytes,Bytes,32));
 assert(runtime_memory_try_live_read_protection(Address,Bytes,32));
 std::atomic<bool> clear_started=false,clear_done=false;
 std::thread clearer([&]{clear_started=true;LiveTestProtectClear();clear_done=true;});
 Until([&]{return clear_started.load();});std::this_thread::sleep_for(10ms);assert(!clear_done.load());
 runtime_memory_end_live_read_protection();runtime_memory_end_live_mapping();clearer.join();assert(clear_done.load());
 assert(runtime_memory_try_live_mapping(Address,Bytes,&map));
 assert(!runtime_memory_try_live_read_protection(Address,Bytes,32));runtime_memory_end_live_mapping();
 // Independent explicit experiment: real protected grant, then backend dies
 // before Complete. Frontend must unwind trap_mutex and VMA too.
 setenv("BB_LAYER_READ_TRAPS","1",1);LiveTestProtectArm();
 s.protocol.stage.store(LiveIdle);s.phase=0;
 const auto protected_child=fork();assert(protected_child>=0);
 if(!protected_child){
  BbPayload::Origin held_origin{};
  assert(CaptureLivePayload(s.protocol,Address,Bytes,[]{return false;},[&](const auto& r){held_origin={r.owner,r.mapping};return true;}));
  // Ordinary callback-thread exception must Complete/Finished before its
  // thread exits; a following request proves no frontend lock was abandoned.
  bool caught=false;
  std::thread callback_thread([&]{try {
   WithProtectedLivePayload(s.protocol,held_origin,Address,Bytes,[]{return false;},[](const auto& r)->bool {
    assert(r.protected_reasons==32);throw std::runtime_error("authored callback exit");
   });
  } catch(const std::runtime_error&){caught=true;}});
  callback_thread.join();assert(caught);
  assert(WithProtectedLivePayload(s.protocol,held_origin,Address,Bytes,[]{return false;},[](const auto& r){return r.protected_reasons==32;}));
  WithProtectedLivePayload(s.protocol,held_origin,Address,Bytes,[]{return false;},[&](const auto& r){assert(r.protected_reasons==32);s.phase=6;_exit(0);return true;});_exit(2);
 }
 dead=false;status=0;
 auto gone3=[&]{if(!dead){const auto r=waitpid(protected_child,&status,WNOHANG);assert(r>=0);dead=r==protected_child;}return dead;};
 while(!gone3()){ServeLivePayload(s.protocol,s.journal,front,gone3);std::this_thread::yield();}
 assert(s.phase==6&&s.protocol.stage.load()==LiveAborted);
 LiveTestProtectClear();
 assert(runtime_memory_try_live_mapping(Address,Bytes,&map));
 assert(!runtime_memory_try_live_read_protection(Address,Bytes,32));runtime_memory_end_live_mapping();
 LiveTestProtectTeardown();
 auto stats=BbPayload::Get().Read();assert(!stats.held&&!stats.waiting&&!stats.readers&&stats.entered==stats.completed);
 munmap(shared,sizeof(Shared));
 puts("LIVE PAYLOAD actual remote: explicit capture, live original, blocked original-free, reuse/map-generation/physical-mirror/alias veto and dead-peer unlock passed");
}
