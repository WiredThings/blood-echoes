// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <atomic>
#include <cstdint>
#include <chrono>
#include <thread>
#include <functional>
#include <exception>
#include "bbport_image_provenance.h"
#include "../../../bb_runtime/src/runtime_live_mapping.h"
#include "../../../bb_runtime/src/runtime_read_arm.h"
namespace BbRemote {
// Independent versioned protocol. Never use LifetimeLease.age_ms/Released state.
inline constexpr std::uint32_t LivePayloadVersion=6;
enum LiveStage:std::uint32_t {LiveIdle,LiveRequest,LiveClaimed,LiveGranted,
                             LiveComplete,LiveFinished,LiveRejected,LiveAborted,LiveRetired,LiveRetirementSynced};
enum LivePurpose:std::uint32_t {LiveValidateOrigin=1,LiveCaptureCreation=2,LiveWritebackRetire=3};
struct LiveOwner {std::uint64_t address{},size{},generation{};std::uint32_t domain{},reserved{};};
static_assert(sizeof(LiveOwner)==32);
struct LivePayloadRetirement {std::uint64_t address{},size{};std::uint32_t applied{},reserved{};};
static_assert(sizeof(LivePayloadRetirement)==24);
struct LivePayloadShared {
 alignas(64) std::atomic<std::uint32_t> stage{LiveIdle};
 std::uint32_t version=LivePayloadVersion;
 std::uint32_t purpose{},reserved{},required_traps{},granted_traps{};
 LiveOwner expected{},granted{};
 std::uint64_t address{},size{},journal_frontier{};
 RuntimeLiveMapping mapping{},expected_mapping{};
 std::uint32_t completion_success{},retirement_sync_success{};
 LivePayloadRetirement retirement{};
};
static_assert(sizeof(LivePayloadShared)<=256);
struct LivePayloadReceipt {
 BbResourceLifetime::Tag owner{};
 RuntimeLiveMapping mapping{};
 std::uint64_t address{},size{},journal_frontier{};
 std::uint32_t protected_reasons{};
};
// No timeout after grant can legally release the payload while a peer may still
// encode. The frontend only releases on Complete or confirmed backend death.
// Client must use the receipt synchronously inside body; never retain it as a lease.
template<class PeerGone,class Body>
bool WithLivePayloadRequest(LivePayloadShared& s,LivePurpose purpose,BbPayload::Origin origin,
                     std::uint64_t address,std::uint64_t size,PeerGone peer_gone,Body body,
                     std::uint32_t required_traps=0,LivePayloadRetirement* retirement=nullptr,
 const std::function<bool(const LivePayloadRetirement&)>& sync_retirement={}) {
 if(retirement)*retirement={};
 const bool retire=purpose==LiveWritebackRetire;
 const auto expected=origin.owner;
 if((required_traps!=0&&required_traps!=32)||(purpose==LiveCaptureCreation&&required_traps))return false;
 if((retire&&(!origin||required_traps))||(purpose==LiveValidateOrigin&&!origin)||(purpose==LiveCaptureCreation&&origin)||
    (purpose!=LiveValidateOrigin&&purpose!=LiveCaptureCreation&&!retire))return false;
 if(s.stage.load(std::memory_order_acquire)!=LiveIdle||s.version!=LivePayloadVersion||
    !address||!size||size>(8ull<<20)||(expected&&expected.domain!=BbResourceLifetime::Domain::Heap))return false;
 s.purpose=purpose;s.expected_mapping=origin.mapping;
 s.completion_success=0;s.retirement_sync_success=0;s.retirement={};
 s.required_traps=required_traps;s.granted_traps=0;
 s.expected={expected.address,expected.size,expected.generation,std::uint32_t(expected.domain),0};
 s.address=address;s.size=size;s.granted={};s.mapping={};s.journal_frontier=0;
 s.stage.store(LiveRequest,std::memory_order_release);
 const auto start=std::chrono::steady_clock::now();std::uint32_t stage;
 for(;;){
  stage=s.stage.load(std::memory_order_acquire);
  if(stage==LiveGranted||stage==LiveRejected||stage==LiveAborted)break;
  if(stage==LiveRequest&&(peer_gone()||std::chrono::steady_clock::now()-start>std::chrono::milliseconds(250))){
   std::uint32_t value=LiveRequest;if(s.stage.compare_exchange_strong(value,LiveIdle,std::memory_order_acq_rel))return false;
  }
  if(peer_gone())return false; // confirmed peer death, process-local locks only
  std::this_thread::sleep_for(std::chrono::microseconds(50));
 }
 if(stage!=LiveGranted){s.stage.store(LiveIdle,std::memory_order_release);return false;}
 const auto g=s.granted;
 const LivePayloadReceipt receipt{{g.address,g.size,g.generation,BbResourceLifetime::Domain(g.domain)},
                                  s.mapping,address,size,s.journal_frontier,s.granted_traps};
 bool ok=false;std::exception_ptr error;
 try{ok=body(receipt);}catch(...){error=std::current_exception();}
 s.completion_success=ok&&!error?1u:0u;
 s.stage.store(LiveComplete,std::memory_order_release);
 while((stage=s.stage.load(std::memory_order_acquire))!=LiveFinished&&stage!=LiveAborted){
  if(retire&&stage==LiveRetired){
   // Frontend STILL holds its original owner/mapping/trap lease. The local
   // mirror must be synchronized before a later native reassert can run.
   try{s.retirement_sync_success=(!sync_retirement||sync_retirement(s.retirement))?1u:0u;}
   catch(...){s.retirement_sync_success=0;if(!error)error=std::current_exception();}
   s.stage.store(LiveRetirementSynced,std::memory_order_release);
  }
  if(peer_gone())return false;
  std::this_thread::sleep_for(std::chrono::microseconds(50));
 }
 const auto retired=s.retirement;
 if(retirement)*retirement=retired;
 s.stage.store(LiveIdle,std::memory_order_release);
 if(error)std::rethrow_exception(error);return ok&&stage==LiveFinished&&(!retire||(retired.applied==1&&s.retirement_sync_success==1));
}
template<class PeerGone,class Body>
bool WithLivePayload(LivePayloadShared& s,BbPayload::Origin origin,std::uint64_t address,
                     std::uint64_t size,PeerGone gone,Body body) {
 return WithLivePayloadRequest(s,LiveValidateOrigin,origin,address,size,gone,body);
}
template<class PeerGone,class Body>
bool CaptureLivePayload(LivePayloadShared& s,std::uint64_t address,
                        std::uint64_t size,PeerGone gone,Body body) {
 return WithLivePayloadRequest(s,LiveCaptureCreation,{},address,size,gone,body);
}
template<class PeerGone,class Body>
bool WithProtectedLivePayload(LivePayloadShared& s,BbPayload::Origin origin,
                     std::uint64_t address,std::uint64_t size,PeerGone gone,Body body) {
 return WithLivePayloadRequest(s,LiveValidateOrigin,origin,address,size,gone,body,32);
}
// Retire-only after successful commit, inside the original frontend lease.
// Validate is scalar-only: no memcpy, GPU wait, trap mutation or RPC. It must
// prove no GPU-only bytes remain and source epoch is unchanged. Missing 32 is
// permitted for idempotent retries; this does NOT grant protected_reasons=32.
// Partial 4 KiB edge pages remain protected; no unowned neighbouring byte is retired.
template<class PeerGone,class Body>
bool RetireReadProtection(LivePayloadShared& s,BbPayload::Origin origin,
 std::uint64_t address,std::uint64_t size,PeerGone gone,Body body,LivePayloadRetirement* out=nullptr,
 const std::function<bool(const LivePayloadRetirement&)>& sync_retirement={}){
 return WithLivePayloadRequest(s,LiveWritebackRetire,origin,address,size,gone,body,0,out,sync_retirement);
}
} // namespace BbRemote
