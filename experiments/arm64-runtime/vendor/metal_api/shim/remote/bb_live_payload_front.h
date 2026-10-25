// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdlib>
#include "remote/bb_live_payload.h"
#include "bbport_payload_gate.h"
#include "remote/bb_lifetime.h"
namespace BbRemote {
template<class PeerGone>
bool ServeLivePayload(LivePayloadShared& s,LifetimeJournal& journal,
                      BbResourceLifetime::Tracker& tracker,PeerGone peer_gone){
 std::uint32_t requested=LiveRequest;
 if(!s.stage.compare_exchange_strong(requested,LiveClaimed,std::memory_order_acq_rel))return false;
 bool granted=false,aborted=false;
 const auto e=s.expected;
 const BbResourceLifetime::Tag expected{e.address,e.size,e.generation,BbResourceLifetime::Domain(e.domain)};
 const bool capture=s.purpose==LiveCaptureCreation;
 const auto* trap_flag=std::getenv("BB_LAYER_READ_TRAPS");
 const bool traps_enabled=trap_flag&&trap_flag[0]=='1';
 const bool retire=s.purpose==LiveWritebackRetire;
 const bool traps_valid=retire?(traps_enabled&&!s.required_traps):
  (s.required_traps==0||(s.required_traps==32&&traps_enabled));
 const bool validate=(s.purpose==LiveValidateOrigin||retire)&&bool(expected)&&expected.domain==BbResourceLifetime::Domain::Heap;
 const bool empty_owner=!e.address&&!e.size&&!e.generation&&!e.domain;
 const bool empty_mapping=BbPayload::SameMapping(s.expected_mapping,RuntimeLiveMapping{});
 if(traps_valid&&(!capture||!s.required_traps)&&(capture||validate)&&(!capture||(empty_owner&&empty_mapping))&&s.version==LivePayloadVersion&&!journal.lost.load(std::memory_order_acquire)&&!peer_gone()){
  // Lock order: payload(read try) -> Tracker(try) -> VMA(read try). Allocator
  // mutations take payload(write) before calling any VMA or Tracker operation.
  BbPayload::Get().TryWithLiveHeap(tracker,expected,s.address,s.size,[&](auto actual){
   RuntimeLiveMapping mapping{};
   if(!runtime_memory_try_live_mapping(s.address,s.size,&mapping))return false;
   struct ReleaseMapping{~ReleaseMapping(){runtime_memory_end_live_mapping();}} hold;
   if(validate&&!BbPayload::SameMappingForRange(mapping,s.expected_mapping,s.address,s.size))return false;
   const unsigned required=s.required_traps;
   if(retire?!runtime_memory_try_live_read_retirement(s.address,s.size):
      (required&&!runtime_memory_try_live_read_protection(s.address,s.size,required)))return false;
   struct ReleaseReadProtection{bool held;~ReleaseReadProtection(){if(held)runtime_memory_end_live_read_protection();}} protect{bool(required)||retire};
   if(journal.lost.load(std::memory_order_acquire)||!BbPayload::Get().Ready()||peer_gone())return false;
   s.granted={actual.address,actual.size,actual.generation,std::uint32_t(actual.domain),0};
   s.mapping=mapping;s.granted_traps=required;s.journal_frontier=journal.written.load(std::memory_order_acquire);
   granted=true;s.stage.store(LiveGranted,std::memory_order_release);
   while(s.stage.load(std::memory_order_acquire)!=LiveComplete){
    if(peer_gone()){aborted=true;break;}
    std::this_thread::sleep_for(std::chrono::microseconds(50));
   }
   if(!aborted&&retire&&s.completion_success==1){
    RuntimeLiveRetirement retired{};
    if(runtime_memory_retire_live_read_protection(s.address,s.size,&retired)){
     s.retirement={retired.address,retired.size,1,0};
     s.stage.store(LiveRetired,std::memory_order_release);
     while(s.stage.load(std::memory_order_acquire)!=LiveRetirementSynced){
      if(peer_gone()){aborted=true;break;}
      std::this_thread::sleep_for(std::chrono::microseconds(50));
     }
    }
   }
   return !aborted;
  });
 }
 s.stage.store(aborted?LiveAborted:(granted?LiveFinished:LiveRejected),std::memory_order_release);
 return true;
}
} // namespace BbRemote
