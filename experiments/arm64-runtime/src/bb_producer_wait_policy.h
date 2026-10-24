// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
namespace BbProducerWait {
using U=uint64_t;
template<class Clock,class Sink>class Call {
 Clock& clock;Sink& sink;U operation{},condition{},mutex{},start{},cpu{};bool active{};
public:
 Call(Clock& c,Sink& s,U op,U key,U m,bool enabled,bool known_producer,bool condition_wait):clock(c),sink(s),operation(op),condition(key),mutex(m){
  if(enabled&&known_producer&&condition_wait){start=clock.Mono();cpu=clock.Cpu();active=true;}
 }
 U Start()const{return start;}
 bool End(uint32_t result,bool adapter_success=true){
  if(!active)return false;active=false;const U end=clock.Mono(),used=clock.Cpu();
  if(!adapter_success||!start||!cpu||end<start||used<cpu||used-cpu>end-start+1000000){sink.Invalid();return false;}
  if(end-start>=20000000){sink.Wait(start,end-start,used-cpu,operation,condition,mutex,result);return true;}
  return false;
 }
 ~Call(){if(active)End(UINT32_MAX,false);}
};
}

#include <array>
#include <atomic>
#include <optional>
namespace BbProducerWait {
struct Registry {
 static constexpr unsigned Capacity=8;static constexpr U MaxToken=(U{1}<<30)-1;
 struct Slot{std::atomic<U>key{},start{},token{};};
 struct Lease{unsigned index{};U key{},token{},start{};};
 std::array<Slot,Capacity>slots{};std::atomic<U>next{},rejected{};std::atomic<bool>exhausted{};
 static_assert(std::atomic<U>::is_always_lock_free);
 std::optional<Lease> Acquire(U key,U start){
  if(key<=1||!start||exhausted.load(std::memory_order_relaxed)){++rejected;return {};}
  for(unsigned i=0;i<Capacity;++i){U zero=0;if(!slots[i].token.compare_exchange_strong(zero,UINT64_MAX,std::memory_order_acq_rel))continue;
   U token=next.fetch_add(1,std::memory_order_relaxed)+1;
   if(!token||token>MaxToken){exhausted=true;++rejected;slots[i].token.store(0,std::memory_order_release);return {};}
   slots[i].key.store(key,std::memory_order_relaxed);slots[i].start.store(start,std::memory_order_relaxed);slots[i].token.store(token,std::memory_order_release);return Lease{i,key,token,start};
  }++rejected;return {};
 }
 bool Release(Lease lease){if(lease.index>=Capacity||!lease.token||lease.token>MaxToken)return false;U expected=lease.token;return slots[lease.index].token.compare_exchange_strong(expected,0,std::memory_order_acq_rel);}
 bool Active()const{for(auto&s:slots){auto t=s.token.load(std::memory_order_acquire);if(t&&t<=MaxToken)return true;}return false;}
 std::array<U,Capacity> MatchLong(U key,U now)const{
  std::array<U,Capacity>r{};if(key<=1)return r;
  for(unsigned i=0;i<Capacity;++i){auto&s=slots[i];U t=s.token.load(std::memory_order_acquire),start=s.start.load(std::memory_order_relaxed);
   if(t&&t<=MaxToken&&s.key.load(std::memory_order_relaxed)==key&&start&&now>=start&&now-start>=20000000&&s.token.load(std::memory_order_acquire)==t)r[i]=t;
  }return r;
 }
};
template<class Clock,class Sink>class Link {
 Registry& registry;Clock& clock;Sink& sink;std::optional<Registry::Lease>lease;std::array<U,Registry::Capacity>tokens{};
 U key{},operation{},start{},cpu{};unsigned stage{};bool active{};
public:
 // stage 1=wait, 2=signal, 3=broadcast, matching the existing condition classifier.
 Link(Registry&r,Clock&c,Sink&s,unsigned kind,U condition,U op,bool enabled,bool known_producer,U wait_start):registry(r),clock(c),sink(s),key(condition),operation(op),stage(kind){
  if(!enabled)return;
  if(stage==1){if(!known_producer||!wait_start)return;start=wait_start;lease=registry.Acquire(key,start);active=true;}
  else if((stage==2||stage==3)&&registry.Active()){
   const U now=clock.Mono();tokens=registry.MatchLong(key,now);bool found=false;for(U t:tokens)found|=bool(t);if(!found)return;
   active=true;start=now;cpu=clock.Cpu();
  }
 }
 void End(uint32_t result,bool success,bool recorded_wait=false){
  if(!active)return;active=false;
  if(stage==1){if(lease&&!registry.Release(*lease))++registry.rejected;if(recorded_wait)sink.Token(start,lease?lease->token:0);return;}
  const U end=clock.Mono(),used=clock.Cpu();
  if(!success||!start||!cpu||end<start||used<cpu||used-cpu>end-start+1000000){sink.Invalid();return;}
  for(U token:tokens)if(token)sink.Wake(start,end-start,used-cpu,token|(U(stage)<<30)|(U(result)<<32),key,operation);
 }
 ~Link(){if(active)End(UINT32_MAX,false);}
};
}
