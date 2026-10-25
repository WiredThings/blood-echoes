// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <atomic>
#include <chrono>
#include <thread>
#include <stdexcept>
#include <cstdio>
#include <sys/wait.h>
#include <unistd.h>
#include "bbport_payload_gate.h"
using namespace std::chrono_literals;
using namespace BbResourceLifetime;
void Until(auto f){auto deadline=std::chrono::steady_clock::now()+5s;while(!f()){assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}}
int main(){
 BbPayload::Gate gate;Tracker tracker;auto tag=tracker.Allocate(Domain::Heap,0x7000000000,128);gate.Configure(true);
 std::atomic<unsigned> phase{},competitor{},reader{},resumed{},entered{};
 std::thread competing([&]{Until([&]{return phase>=1;});BbPayload::Gate::Mutation mutation{gate};++competitor;assert(resumed==1);});
 std::thread server([&]{Until([&]{return phase>=2;});assert(gate.TryWithLiveHeap(tracker,tag,tag.address,128,[&](auto same){assert(same==tag);assert(competitor==0);reader=1;return true;}));});
 {
  BbPayload::Gate::Mutation original_body{gate};++entered;
  {BbPayload::Gate::Mutation nested_original{gate};assert(BbPayload::Gate::CurrentMutation());}
  phase=1;Until([&]{return gate.Read().waiting!=0;});
  try{
   BbPayload::Gate::FaultPause rpc_wait{gate};assert(rpc_wait.Active());
   {BbPayload::Gate::FaultPause nested_wait{gate};assert(nested_wait.Active());}
   phase=2;Until([&]{return reader==1;});assert(competitor==0);
   throw std::runtime_error("authored synchronous RPC exception");
  }catch(const std::runtime_error&){
   // FaultPause restored this thread's writer before exception returns to body.
   assert(competitor==0);std::atomic<bool> denied{};
   std::thread check([&]{denied=!gate.TryWithLiveHeap(tracker,tag,tag.address,128,[](auto){return true;});});check.join();assert(denied);
  }
  resumed=1;assert(entered==1&&gate.Read().held==1);
 }
 competing.join();server.join();assert(competitor==1);auto s=gate.Read();assert(s.entered==2&&s.completed==2&&!s.held&&!s.waiting&&!s.readers);
 // No mutation on the ordinary off path: pause is a no-op and creates no work.
 BbPayload::Gate off;off.Configure(false);{BbPayload::Gate::FaultPause none{off};assert(!none.Active());}assert(!off.Read().entered);
 // An original allocator must never execute a nested mutation while paused.
 const auto child=fork();assert(child>=0);if(!child){BbPayload::Gate g;BbPayload::Gate::Mutation original{g};BbPayload::Gate::FaultPause paused{g};BbPayload::Gate::Mutation illegal_resume{g};_exit(3);}int status=0;assert(waitpid(child,&status,0)==child&&WIFSIGNALED(status)&&WTERMSIG(status)==SIGABRT);
 puts("PAYLOAD FAULT PAUSE: serialized competing original body blocked; shared service succeeds; nested/exception restore and off/no-op pass; illegal resumed mutator aborts");
}
