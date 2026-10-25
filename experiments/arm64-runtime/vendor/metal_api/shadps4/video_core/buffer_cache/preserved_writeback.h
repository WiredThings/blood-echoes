// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <utility>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <optional>
#include "bbport_image_provenance.h"
#include "bblayer_gpu_memory.h"
namespace VideoCore {
// GPU command/cache owner only; caller retains the existing cache serialization.
// Bounded provenance metadata, not a new resource cache or retained GPU snapshot.
class PreservedWritebackLedger {
public:
 struct Snapshot {BbPayload::Origin origin;BbLayer::StableMirrorSnapshot mirror;
                  std::uint64_t address{},size{},tick{},buffer_identity{};};
 struct Ticket {std::size_t slot{};std::uint64_t serial{};bool conversion{};};
 static constexpr std::size_t Capacity=64;
 class Scope {
 public:
  Scope(Scope&&o)noexcept:ledger(std::exchange(o.ledger,nullptr)),outer(o.outer){}
  Scope(const Scope&)=delete;Scope&operator=(const Scope&)=delete;
  ~Scope(){if(!ledger)return;if(owner!=ledger)std::abort();if(outer){owner=nullptr;ledger->owner_flag.clear(std::memory_order_release);}}
 private:friend class PreservedWritebackLedger;Scope(PreservedWritebackLedger*l,bool o):ledger(l),outer(o){};PreservedWritebackLedger*ledger{};bool outer{};
 };
 std::optional<Scope> TryAcquire()noexcept {
  if(owner==this)return Scope{this,false};
  if(owner||owner_flag.test_and_set(std::memory_order_acquire))return {};
  owner=this;return Scope{this,true};
 }

 std::optional<Ticket> Reserve(Snapshot s) noexcept {AssertOwner();
  if(!Valid(s))return {};
  std::size_t slot=Capacity;
  for(std::size_t i=0;i<Capacity;++i){auto&e=entries[i];
   if(e.state==State::Free){if(slot==Capacity)slot=i;continue;}
   if(!Overlap(s.address,s.size,e.live.address,e.live.size))continue;
   if(e.state!=State::Recorded||!SameDestination(e.live,s))return {};
   if(slot!=Capacity&&entries[slot].state!=State::Free)return {};
   slot=i;
  }
  if(slot==Capacity)return {};
  auto&e=entries[slot];const bool conversion=e.state==State::Recorded;
  if(sequence==UINT64_MAX)std::abort();e.serial=++sequence;e.pending=s;
  e.state=conversion?State::Conversion:State::Reserved;
  if(!conversion){e.live=s;++active;}
  return Ticket{slot,e.serial,conversion};
 }
 void CommitRecorded(Ticket t) noexcept {AssertOwner();
  auto&e=Checked(t);if(e.state!=(t.conversion?State::Conversion:State::Reserved))std::abort();
  e.live=e.pending;e.pending={};e.state=State::Recorded;
 }
 void Cancel(Ticket t) noexcept {AssertOwner();
  auto&e=Checked(t);if(e.state!=(t.conversion?State::Conversion:State::Reserved))std::abort();
  if(t.conversion){e.pending={};e.state=State::Recorded;}else{e={};--active;}
 }
 std::optional<Ticket> ClaimFirst(std::uint64_t at,std::uint64_t bytes) noexcept {AssertOwner();
  if(!bytes||at>UINT64_MAX-bytes)return {};
  for(std::size_t i=0;i<Capacity;++i){auto&e=entries[i];
   if(e.state==State::Recorded&&Overlap(at,bytes,e.live.address,e.live.size)){
    e.state=State::Claimed;return Ticket{i,e.serial,false};
   }
  }
  return {};
 }
 // A cold wait/flush may run an in-order callback that consumes or replaces
 // this entry. Reacquire only the exact recorded serial; a stale ticket is not
 // an error and MUST NOT be fed to Checked()/Retry()/Complete().
 bool ClaimExact(Ticket t) noexcept {AssertOwner();
  if(t.slot>=Capacity)return false;auto&e=entries[t.slot];
  if(e.serial!=t.serial||e.state!=State::Recorded)return false;
  e.state=State::Claimed;return true;
 }
 Snapshot Claimed(Ticket t) const noexcept {AssertOwner();
  const auto&e=Checked(t);if(e.state!=State::Claimed)std::abort();return e.live;
 }
 void Retry(Ticket t) noexcept {AssertOwner();auto&e=Checked(t);if(e.state!=State::Claimed)std::abort();e.state=State::Recorded;}
 void Complete(Ticket t) noexcept {AssertOwner();auto&e=Checked(t);if(e.state!=State::Claimed)std::abort();e={};--active;}
 bool Overlaps(std::uint64_t at,std::uint64_t bytes) const noexcept {AssertOwner();
  if(!active||!bytes||at>UINT64_MAX-bytes)return false;
  for(const auto&e:entries)if(e.state!=State::Free&&Overlap(at,bytes,e.live.address,e.live.size))return true;
  return false;
 }
 // Only a confirmed current-owner invalidation event may discard provenance.
 // Generic failed lease, unrelated mapping change and busy locks must use Retry.
 void ForgetConfirmed(std::uint64_t at,std::uint64_t bytes) noexcept {AssertOwner();
  if(!bytes||at>UINT64_MAX-bytes)return;
  for(auto&e:entries)if(e.state==State::Recorded&&at<=e.live.address&&e.live.address-at<=bytes&&e.live.size<=bytes-(e.live.address-at)){e={};--active;}
 }
 std::size_t Count() const noexcept {return active.load(std::memory_order_acquire);}
 static bool SameMirror(const BbLayer::MemorySource&a,const BbLayer::MemorySource&b)noexcept {
  return a.kind==b.kind&&a.base==b.base&&a.size==b.size&&a.handle==b.handle&&
   a.device_address==b.device_address&&a.owner==b.owner;
 }
private:
 enum class State {Free,Reserved,Conversion,Recorded,Claimed};
 struct Entry {Snapshot live{},pending{};std::uint64_t serial{};State state=State::Free;};
 std::array<Entry,Capacity> entries{};std::uint64_t sequence{};std::atomic<std::size_t> active{};std::atomic_flag owner_flag=ATOMIC_FLAG_INIT;
 inline static thread_local PreservedWritebackLedger*owner{};
 void AssertOwner()const noexcept {if(owner!=this)std::abort();}
 static bool Overlap(std::uint64_t a,std::uint64_t n,std::uint64_t b,std::uint64_t m)noexcept{return a<b+m&&b<a+n;}
 static bool Valid(const Snapshot&s)noexcept {
  const auto&m=s.mirror.source;const auto&t=s.origin.owner;
  return bool(s.origin)&&s.buffer_identity&&s.size&&s.size<=(8ull<<20)&&s.tick&&s.address<=UINT64_MAX-s.size&&
   s.address>=t.address&&s.address-t.address<=t.size&&s.size<=t.size-(s.address-t.address)&&
   s.mirror.va==s.address&&s.mirror.size==s.size&&m.kind==BbLayer::MemorySource::Kind::Mirror&&
   m.owner&&m.handle&&m.size&&m.base<=UINT64_MAX-m.size&&m.base<=s.address&&s.address-m.base<=m.size&&s.size<=m.size-(s.address-m.base);
 }
 static bool SameDestination(const Snapshot&a,const Snapshot&b)noexcept {
  return a.address==b.address&&a.size==b.size&&a.buffer_identity==b.buffer_identity&&a.origin.owner==b.origin.owner&&
   BbPayload::SameMapping(a.origin.mapping,b.origin.mapping)&&SameMirror(a.mirror.source,b.mirror.source);
 }
 Entry& Checked(Ticket t)noexcept {
  if(t.slot>=Capacity||entries[t.slot].serial!=t.serial||entries[t.slot].state==State::Free)std::abort();return entries[t.slot];
 }
 const Entry& Checked(Ticket t)const noexcept {
  if(t.slot>=Capacity||entries[t.slot].serial!=t.serial||entries[t.slot].state==State::Free)std::abort();return entries[t.slot];
 }
};
} // namespace VideoCore
