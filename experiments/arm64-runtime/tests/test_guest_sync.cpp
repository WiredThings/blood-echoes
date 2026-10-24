#include "bb_guest_sync_audit.h"
#include "bb_guest_frame_audit.h"
#include "core/guest_cpu/fex_hle_bridge.h"
#include <cassert>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <chrono>
#include <vector>
#include <string>
using namespace BbGuestSync;
static_assert(BbAudit::ProducerConditionWait==65&&BbAudit::ProducerConditionIdentity==66&&BbAudit::ProducerConditionMutex==67&&BbAudit::ProducerConditionResult==68);
struct Clock{U mono=1000000000,cpu=100000000,reads{};U Mono(){++reads;return mono;}U Cpu(){++reads;return cpu;}};
struct TestSink{unsigned waits{},wakes{},reports{},windows{};U window_wall{},window_cpu{};void ProducerWindow(U,U w,U c,U){++windows;window_wall=w;window_cpu=c;}U token{},key{},mutex{},result{},arg{};void WaitSpan(U,U,U,U t,U k,U m,uint32_t r,U){++waits;token=t;key=k;mutex=m;result=r;}void WakeSpan(U,U,U,U a){++wakes;arg=a;}void Status(U,U,U,U,U){++reports;}};
static std::mutex lock;static std::condition_variable event;static bool ready{},go{};static std::atomic<unsigned>wait_calls{},wake_calls{};
static int32_t real_wait(uint64_t condition,uint64_t mutex){assert(condition==0x4000&&mutex==0x5000);++wait_calls;std::unique_lock l(lock);ready=true;event.notify_all();event.wait(l,[]{return go;});return 0;}
static int32_t real_signal(uint64_t condition){assert(condition==0x4000);++wake_calls;std::lock_guard l(lock);go=true;event.notify_all();return 0;}
int main(int argc,char**argv){
 assert(Classify("runtime.cond_wait")==Wait&&Classify("runtime.posix_cond_wait")==Wait&&Classify("runtime.cond_signal")==Signal&&Classify("runtime.posix_cond_broadcast")==Broadcast);
 assert(!Classify("runtime.cond_waitX")&&!Classify("other.cond_wait")&&!Classify("runtime.mutex_lock"));
 Registry r;assert(!r.Acquire(0));std::vector<Registry::Lease>leases;
 for(unsigned i=0;i<Registry::Capacity;++i){auto l=r.Acquire(0x4000);assert(l);leases.push_back(*l);}assert(!r.Acquire(0x4000));unsigned matches=0;for(auto t:r.Match(0x4000))matches+=t!=0;assert(matches==Registry::Capacity);for(auto l:leases)assert(r.Release(l));auto fresh=r.Acquire(0x4000);assert(fresh&&fresh->token!=leases[0].token&&!r.Release(leases[0]));assert(r.Release(*fresh));
 Registry limit;limit.next=Registry::MaxToken;assert(!limit.Acquire(0x4000));assert(!limit.Acquire(0x4000));limit.next=0;assert(!limit.Acquire(0x4000));
 Registry concurrent;std::vector<std::thread>workers;std::atomic<unsigned>done{};
 for(unsigned id=0;id<4;++id)workers.emplace_back([&,id]{for(unsigned j=0;j<1000;++j){std::optional<Registry::Lease>l;while(!(l=concurrent.Acquire(0x10000+id)))std::this_thread::yield();bool found=false;for(auto n:concurrent.Match(l->key))found|=n==l->token;assert(found);assert(concurrent.Release(*l));++done;}});
 for(auto&w:workers)w.join();assert(done==4000);for(auto&slot:concurrent.slots)assert(slot.token==0);
 Clock c;TestSink s;State t;Registry rr;
 for(unsigned i=0;i<10000;++i){Call a(rr,c,s,t,Wait,0x4000,0x5000,8,false,true);}assert(c.reads==0&&rr.next==0&&t.calls==0);
 {Call a(rr,c,s,t,Wait,0x4000,0x5000,8,true,false);}assert(c.reads==0&&rr.next==0);
 {Call a(rr,c,s,t,Signal,0x4000,0,9,true,false);}assert(c.reads==0);
 {Call a(rr,c,s,t,Wait,0x4000,0x5000,8,true,true);c.mono+=60000000;c.cpu+=1000000;{Call wake(rr,c,s,t,Signal,0x4000,0,9,true,false);c.mono+=1000000;c.cpu+=1000;wake.End(42);}c.mono+=40000000;c.cpu+=100000;a.End(7);a.End(7);}
 assert(s.waits==1&&s.wakes==1&&s.result==7&&s.key==0x4000&&s.mutex==0x5000&&s.token==(s.arg&Registry::MaxToken)&&(s.arg>>32)==42&&((s.arg>>30)&3)==Signal&&t.calls==1&&t.completed==1);for(auto v:rr.Match(0x4000))assert(!v);
 {Call a(rr,c,s,t,Wait,0x4000,0x5000,8,true,true);c.mono+=1000;c.cpu+=10000000;a.End(0);}assert(t.invalid==1&&rr.rejected==1);for(auto v:rr.Match(0x4000))assert(!v);
 {Call a(rr,c,s,t,Wait,0x4000,0x5000,8,true,true);}assert(t.invalid==2); // unwinding is not a successful wake
 {Registry r;Clock c;TestSink s;State t;{Call a(r,c,s,t,Wait,0x4000,0x5000,1,true,true);c.mono+=19000000;c.cpu+=1000000;a.End(0);}assert(!s.waits);{Call a(r,c,s,t,Wait,0x4000,0x5000,1,true,true);c.mono+=20000000;c.cpu+=1000000;a.End(0);}assert(s.waits==1);}
 assert(!SuppressGeneric(None,true)&&!SuppressGeneric(Wait,false)&&SuppressGeneric(Wait,true)&&SuppressGeneric(Signal,true));
 {Registry r;Clock c;TestSink s;State t;auto lease=r.Acquire(0x4000);assert(lease);
  {Call a(r,c,s,t,Broadcast,0x4000,0,1,true,false);a.End(0);}assert(!s.windows);
  c.mono+=150000000;c.cpu+=1000000;auto reads=c.reads;{Call a(r,c,s,t,Broadcast,0x4000,0,1,true,false);a.End(0);}assert(c.reads==reads+4&&s.windows==1&&s.window_wall==150000000&&s.window_cpu==1000000);
  c.mono+=150000000;c.cpu+=149000000;{Call a(r,c,s,t,Broadcast,0x4000,0,1,true,false);a.End(0);}assert(s.windows==2&&s.window_cpu==149000000);
  reads=c.reads;{Call a(r,c,s,t,Broadcast,0x4000,0,1,false,false);}assert(c.reads==reads&&!t.producer_anchor);
  c.mono+=50000000;{Call a(r,c,s,t,Broadcast,0x4000,0,1,true,false);a.End(0);}assert(s.windows==2);
  auto other=r.Acquire(0x6000);assert(other);c.mono+=50000000;{Call a(r,c,s,t,Broadcast,0x6000,0,1,true,false);a.End(0);}assert(s.windows==2);
  c.mono+=50000000;c.cpu-=1;{Call a(r,c,s,t,Broadcast,0x6000,0,1,true,false);a.End(0);}assert(t.invalid==1&&s.windows==2);assert(r.Release(*lease)&&r.Release(*other));
 }
 const bool producer=argc>1&&std::string_view(argv[1])=="--producer";const bool disabled=argc>1&&!producer;setenv("BB_PRODUCER_WAIT_AUDIT",producer?"1":"0",1);std::string root="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/guest-sync-unit-"+std::to_string(getpid());setenv("BB_STUTTER_AUDIT",root.c_str(),1);unsetenv("BB_STUTTER_GATE");if(disabled)unsetenv("BB_GUEST_SYNC_AUDIT");else setenv("BB_GUEST_SYNC_AUDIT","1",1);assert(BbAudit::Enabled());
 using namespace Core::GuestCpu;HleCallRegistry registry;auto w=registry.Register(MakeHleCallAdapter(real_wait),"runtime.cond_wait");auto sig=registry.Register(MakeHleCallAdapter(real_signal),"runtime.cond_signal");assert(w->guest_sync_stage==Wait&&sig->guest_sync_stage==Signal);HleGuestBridge bridge(registry,[](void*,uintptr_t,size_t,bool){return false;},nullptr);
 std::thread thread([&]{BbGuestFrame::ThreadState().markers=1;HleCallFrame f{};f.operation=w->Operation();f.gpr[detail::SysVIntegerArgs[0]]=0x4000;f.gpr[detail::SysVIntegerArgs[1]]=0x5000;auto z=bridge.Invoke(f);assert(std::get<bool>(z)&&f.gpr[0]==0);});
 {std::unique_lock l(lock);assert(event.wait_for(l,std::chrono::seconds(2),[]{return ready;}));}std::this_thread::sleep_for(std::chrono::milliseconds(60));HleCallFrame f{};f.operation=sig->Operation();f.gpr[detail::SysVIntegerArgs[0]]=0x4000;auto z=bridge.Invoke(f);assert(std::get<bool>(z)&&f.gpr[0]==0);thread.join();assert(wait_calls==1&&wake_calls==1);
 if(producer){
  assert(BbGuestSync::ThreadState().known_producer); // admitted by the real render wake above
  {std::lock_guard l(lock);ready=false;go=false;}
  std::thread notifier([&]{
   {std::unique_lock l(lock);assert(event.wait_for(l,std::chrono::seconds(2),[]{return ready;}));}
   std::this_thread::sleep_for(std::chrono::milliseconds(60));HleCallFrame f{};f.operation=sig->Operation();f.gpr[detail::SysVIntegerArgs[0]]=0x4000;auto z=bridge.Invoke(f);assert(std::get<bool>(z));
  });
  HleCallFrame f{};f.operation=w->Operation();f.gpr[detail::SysVIntegerArgs[0]]=0x4000;f.gpr[detail::SysVIntegerArgs[1]]=0x5000;auto z=bridge.Invoke(f);assert(std::get<bool>(z)&&f.gpr[0]==0);notifier.join();assert(wait_calls==2&&wake_calls==2);
  unsigned spans=0,keys=0,mutexes=0,results=0;U producer_token=0,notified_token=0;
  for(const auto&r:BbAudit::Get().data->rows)if(r.sequence){
   if(r.kind==65){++spans;assert(r.wall>=40000000&&r.cpu<r.wall&&r.arg==w->Operation());}
   if(r.kind==66){++keys;assert(r.arg==0x4000);}
   if(r.kind==67){++mutexes;assert(r.arg==0x5000);}
   if(r.kind==68){++results;assert(r.arg==0);}
   if(r.kind==69)producer_token=r.arg;
   if(r.kind==70){notified_token=r.arg&BbProducerWait::Registry::MaxToken;assert(r.arg>>32==0);}
   if(r.kind==71)assert(r.arg==0x4000);
   if(r.kind==72)assert(r.arg==sig->Operation());
  }assert(spans==1&&keys==1&&mutexes==1&&results==1&&producer_token&&producer_token==notified_token);
 }
 U wait_token=0,wake_token=0;bool identity=false,mutex=false,result=false,generic=false;for(const auto&row:BbAudit::Get().data->rows)if(row.sequence){generic|=row.kind==BbAudit::HLE;if(row.kind==BbAudit::GuestConditionWait){wait_token=row.arg;assert(row.wall>=40000000&&row.cpu<row.wall);}if(row.kind==BbAudit::GuestConditionWake){wake_token=row.arg&Registry::MaxToken;assert(row.arg>>32==0);}identity|=row.kind==BbAudit::GuestConditionIdentity&&row.arg==0x4000;mutex|=row.kind==BbAudit::GuestConditionMutex&&row.arg==0x5000;result|=row.kind==BbAudit::GuestConditionResult&&row.arg==0;}
 assert(generic==disabled);assert(bool(wait_token)==!disabled&&bool(wake_token)==!disabled&&wait_token==wake_token&&identity==!disabled&&mutex==!disabled&&result==!disabled);assert(!BbAudit::Get().data->h.dropped);unlink((root+"-"+std::to_string(getpid())+".bin").c_str());puts(disabled?"sync tracing off: original condition/signal calls and results retained":"sync tracing: exact slots, bounds, ABA, disabled clocks, real threaded wait/wake passed");
}
