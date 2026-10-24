// SPDX-License-Identifier: MIT
#pragma once
#include "bb_guest_frame_policy.h"
#include <array>
#include <atomic>
#include <optional>
namespace BbGuestSync {
using U=uint64_t;
enum Stage:uint8_t {None,Wait,Signal,Broadcast};
constexpr Stage Classify(std::string_view n){
 if(n=="runtime.cond_wait"||n=="runtime.posix_cond_wait"||n=="runtime.cond_timedwait"||n=="runtime.posix_cond_timedwait"||n=="runtime.cond_abs_timedwait"||n=="runtime.posix_cond_abs_timedwait")return Wait;
 if(n=="runtime.cond_signal"||n=="runtime.posix_cond_signal")return Signal;
 if(n=="runtime.cond_broadcast"||n=="runtime.posix_cond_broadcast")return Broadcast;
 return None;
}
// Opaque argument-slot identities only. Never dereference guest memory.
// Slot aliases are not resolved: absence of a match is not proof of no wake.
struct Registry {
 static constexpr unsigned Capacity=8;static constexpr U MaxToken=(U{1}<<30)-1;
 struct Slot{std::atomic<U>key{},token{};};
 struct Lease{unsigned index{};U key{},token{};};
 std::array<Slot,Capacity>slots{};std::atomic<U>next{},rejected{};std::atomic<bool>exhausted{};
 static_assert(std::atomic<U>::is_always_lock_free);
 std::optional<Lease> Acquire(U key){
  if(key<=1||exhausted.load(std::memory_order_relaxed)){++rejected;return {};}
  for(unsigned i=0;i<Capacity;++i){U empty=0;if(!slots[i].token.compare_exchange_strong(empty,UINT64_MAX,std::memory_order_acq_rel))continue;
   U token=next.fetch_add(1,std::memory_order_relaxed)+1;
   if(!token||token>MaxToken){exhausted=true;++rejected;slots[i].token.store(0,std::memory_order_release);return {};}
   slots[i].key.store(key,std::memory_order_relaxed);slots[i].token.store(token,std::memory_order_release);return Lease{i,key,token};
  }++rejected;return {};
 }
 bool Release(Lease l){
  if(l.index>=Capacity||!l.token||l.token>MaxToken)return false;
  U expected=l.token;return slots[l.index].token.compare_exchange_strong(expected,0,std::memory_order_acq_rel);
 }
 std::array<U,Capacity> Match(U key)const{
  std::array<U,Capacity>r{};if(key<=1)return r;
  for(unsigned i=0;i<Capacity;++i){auto&a=slots[i];U t=a.token.load(std::memory_order_acquire);if(t&&t<=MaxToken&&a.key.load(std::memory_order_relaxed)==key&&a.token.load(std::memory_order_acquire)==t)r[i]=t;}
  return r;
 }
};
struct State{BbGuestFrame::Limiter status{1000000000},producer{40000000};U calls{},completed{},slow{},invalid{},producer_mono{},producer_cpu{},producer_key{};bool producer_anchor{},known_producer{};};
inline bool SuppressGeneric(Stage stage,bool requested){return stage!=None&&requested;}
inline U WakeArg(U token,Stage stage,uint32_t result){return token|(U(stage)<<30)|(U(result)<<32);}
template<class Clock,class Sink>class Call {
 Registry&registry;Clock&clock;Sink&sink;State&state;std::optional<Registry::Lease>lease;std::array<U,Registry::Capacity>tokens{};Stage stage{};U key{},mutex{},op{},start{},cpu{};bool active{};
public:
 Call(Registry&r,Clock&c,Sink&s,State&t,Stage k,U condition,U m,U operation,bool enabled,bool render):registry(r),clock(c),sink(s),state(t),stage(k),key(condition),mutex(m),op(operation){
  if(!enabled){state.producer_anchor=false;state.known_producer=false;state.producer.Reset();return;}
  if(!stage)return;
  if(stage==Wait){if(!render)return;++state.calls;lease=registry.Acquire(key);if(!lease){++state.invalid;++state.completed;return;}}
  else {tokens=registry.Match(key);bool any=false;for(auto x:tokens)any|=x!=0;if(!any)return;}
  active=true;start=clock.Mono();cpu=clock.Cpu();
 }
 void End(uint32_t result,bool success=true){
  if(!active)return;active=false;const U end=clock.Mono(),used=clock.Cpu();
  if(lease){if(!registry.Release(*lease))++registry.rejected;++state.completed;}
  const bool valid=success&&start&&cpu&&end&&used&&end>=start&&used>=cpu&&used-cpu<=end-start+1000000;
  if(!valid){++state.invalid;++registry.rejected;state.producer_anchor=false;state.producer.Reset();}
  else if(lease){if(end-start>=20000000){++state.slow;sink.WaitSpan(start,end-start,used-cpu,lease->token,key,mutex,result,op);}}
  else {
   state.known_producer=true; // this thread just notified an admitted render wait
   for(auto token:tokens)if(token)sink.WakeSpan(start,end-start,used-cpu,WakeArg(token,stage,result));
   if(state.producer_key!=key){state.producer_anchor=false;state.producer.Reset();state.producer_key=key;}
   if(state.producer.Due(end)){
    if(state.producer_anchor){
     if(end>state.producer_mono&&used>=state.producer_cpu&&used-state.producer_cpu<=end-state.producer_mono+1000000)
      sink.ProducerWindow(state.producer_mono,end-state.producer_mono,used-state.producer_cpu,key);
     else{++state.invalid;++registry.rejected;}
    }
    state.producer_anchor=true;state.producer_mono=end;state.producer_cpu=used;
   }
  }
  if(lease&&end&&state.status.Due(end))sink.Status(end,state.calls,state.completed,state.invalid,registry.rejected.load());
 }
 ~Call(){if(active)End(UINT32_MAX,false);}
};
}
