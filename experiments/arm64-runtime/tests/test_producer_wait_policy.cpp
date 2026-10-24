#include "bb_producer_wait_policy.h"
#include <cassert>
#include <thread>
#include <vector>
using U=uint64_t;
struct Clock {U mono=1000000000,cpu=1000000,reads=0;U Mono(){++reads;return mono;}U Cpu(){++reads;return cpu;}};
struct Sink {unsigned waits=0,invalid=0,wakes=0,tokens=0;U token{},wake_arg{},wake_key{},wake_operation{};void Token(U,U t){++tokens;token=t;}void Wake(U,U,U,U a,U k,U op){++wakes;wake_arg=a;wake_key=k;wake_operation=op;}U key{},mutex{},operation{},wall{},cpu{};uint32_t result{};void Invalid(){++invalid;}void Wait(U,U w,U c,U op,U k,U m,uint32_t r){++waits;key=k;mutex=m;operation=op;wall=w;cpu=c;result=r;}};
int main(){Clock c;Sink s;
 {using R=BbProducerWait::Registry;R r;assert(!r.Acquire(0,1));std::vector<R::Lease>ls;
  for(unsigned i=0;i<R::Capacity;++i){auto l=r.Acquire(0x4000,100);assert(l);ls.push_back(*l);}assert(!r.Acquire(0x4000,100));
  for(U t:r.MatchLong(0x4000,20000099))assert(!t);unsigned matches=0;for(U t:r.MatchLong(0x4000,20000100))matches+=bool(t);assert(matches==R::Capacity);
  for(auto l:ls)assert(r.Release(l));auto fresh=r.Acquire(0x4000,100);assert(fresh&&fresh->token!=ls[0].token&&!r.Release(ls[0]));assert(r.Release(*fresh)&&!r.Active());
  R exhausted;exhausted.next=R::MaxToken;assert(!exhausted.Acquire(0x4000,1));exhausted.next=0;assert(!exhausted.Acquire(0x4000,1));
 }
 {BbProducerWait::Registry r;Clock c;Sink s;
  for(unsigned i=0;i<1000;++i){BbProducerWait::Link l(r,c,s,1,0x4000,123,false,true,c.mono);}assert(!c.reads&&!r.Active());
  BbProducerWait::Call wait(c,s,123,0x4000,0x5000,true,true,true);BbProducerWait::Link l(r,c,s,1,0x4000,123,true,true,wait.Start());
  c.mono+=19999999;{BbProducerWait::Link early(r,c,s,2,0x4000,124,true,false,0);early.End(0,true);}assert(!s.wakes);
  ++c.mono;{BbProducerWait::Link wrong(r,c,s,2,0x6000,124,true,false,0);wrong.End(0,true);}assert(!s.wakes);
  {BbProducerWait::Link wake(r,c,s,2,0x4000,124,true,false,0);c.mono+=1000;c.cpu+=100;wake.End(0,true);wake.End(0,true);}
  c.mono+=1000;c.cpu+=100;bool emitted=wait.End(0);l.End(0,true,emitted);l.End(0,true,true);
  assert(s.waits==1&&s.wakes==1&&s.tokens==1&&s.token==(s.wake_arg&BbProducerWait::Registry::MaxToken)&&s.wake_key==0x4000&&s.wake_operation==124&&!r.Active());
 }
 {BbProducerWait::Registry r;std::atomic<unsigned>done{};std::vector<std::thread>workers;
  for(unsigned i=0;i<4;++i)workers.emplace_back([&,i]{for(unsigned n=0;n<1000;++n){std::optional<BbProducerWait::Registry::Lease>l;while(!(l=r.Acquire(0x10000+i,1)))std::this_thread::yield();bool found=false;for(U t:r.MatchLong(l->key,20000001))found|=t==l->token;assert(found&&r.Release(*l));++done;}});
  for(auto&w:workers)w.join();assert(done==4000&&!r.Active());
 }

 for(unsigned i=0;i<1000;++i){BbProducerWait::Call a(c,s,123,0x4000,0x5000,false,true,true);}assert(c.reads==0);
 {BbProducerWait::Call a(c,s,123,0x4000,0x5000,true,false,true);}assert(!c.reads);
 {BbProducerWait::Call a(c,s,123,0x4000,0x5000,true,true,false);}assert(!c.reads);
 {BbProducerWait::Call a(c,s,123,0x4000,0x5000,true,true,true);c.mono+=19999999;c.cpu+=1000;a.End(7);}assert(!s.waits);
 {BbProducerWait::Call a(c,s,123,0x4000,0x5000,true,true,true);c.mono+=20000000;c.cpu+=1000;a.End(0x8002003c);a.End(0);}assert(s.waits==1&&s.key==0x4000&&s.mutex==0x5000&&s.operation==123&&s.result==0x8002003c&&s.wall==20000000&&s.cpu==1000);
 {BbProducerWait::Call a(c,s,1,2,3,true,true,true);c.mono-=1;a.End(0);}assert(s.invalid==1);
 {BbProducerWait::Call a(c,s,1,2,3,true,true,true);}assert(s.invalid==2&&s.waits==1);
}
