// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include "remote/bb_live_payload.h"
namespace BbRemote {
// This is a synchronous COMMIT primitive, not a GPU completion provider. Caller
// obtains readback bytes from actual finished work outside every live lease and
// keeps the existing readback allocation/lock alive through this function.
// Failed admission never authorizes discard of outstanding GPU-only contents.
enum class LiveWritebackResult {Committed,NotReady,Retry,CommittedUnacknowledged};
struct FrozenWritebackSpan {
 std::uint64_t guest_address{};
 void* destination{}; // backend's pre-existing unprotected same-VA view
 const void* completed_source{}; // stable private readback pool, never guest bytes
 std::uint64_t bytes{};
};
struct FrozenWriteback {
 static constexpr std::size_t Capacity=16;
 std::array<FrozenWritebackSpan,Capacity> spans{};
 std::size_t count{};
 std::uint64_t address{},bytes{},tick{},content_epoch{};
};
inline bool AppendWriteback(FrozenWriteback& b,std::uint64_t guest,const void* completed,
                             std::uint64_t bytes) noexcept {
 if(!bytes||!completed||!b.bytes||b.bytes>(8ull<<20)||b.address>UINT64_MAX-b.bytes||
    guest<b.address||guest-b.address>b.bytes||bytes>b.bytes-(guest-b.address)||
    b.count>=b.Capacity)return false;
 for(std::size_t i=0;i<b.count;++i){const auto&s=b.spans[i];
  if(guest<s.guest_address+s.bytes&&s.guest_address<guest+bytes)return false;
 }
 b.spans[b.count++]={guest,reinterpret_cast<void*>(guest),completed,bytes};return true;
}
template<class Completed,class Lease,class StillCurrent>
LiveWritebackResult CommitLiveWriteback(const BbPayload::Origin& origin,
 const FrozenWriteback& batch,Completed completed,Lease with_lease,StillCurrent still_current) {
 using R=LiveWritebackResult;
 if(!origin||!batch.count||batch.count>batch.Capacity||!batch.bytes||batch.bytes>(8ull<<20)||
    batch.address>UINT64_MAX-batch.bytes)return R::Retry;
 // No locks/lease yet. Production uses the real readback/queue completion, not
 // a caller timer, Record return value, retained native pointer or fake fence.
 if(!completed(batch.tick))return R::NotReady;
 bool copied=false;
 const bool acknowledged=with_lease(origin,batch.address,batch.bytes,[&](const LivePayloadReceipt&r){
  if(r.owner!=origin.owner||!BbPayload::SameMappingForRange(r.mapping,origin.mapping,batch.address,batch.bytes)||
     r.address!=batch.address||r.size!=batch.bytes||r.protected_reasons!=32)return false;
  // Entire destination list checked BEFORE first store. No calls to
  // TryWriteBacking/Covered: they would reacquire the held backend VMA lock.
  for(std::size_t i=0;i<batch.count;++i){const auto&s=batch.spans[i];
   if(!s.bytes||!s.completed_source||s.destination!=reinterpret_cast<void*>(s.guest_address)||
      s.guest_address<batch.address||s.guest_address-batch.address>batch.bytes||
      s.bytes>batch.bytes-(s.guest_address-batch.address))return false;
   for(std::size_t j=0;j<i;++j){const auto&t=batch.spans[j];
    if(s.guest_address<t.guest_address+t.bytes&&t.guest_address<s.guest_address+s.bytes)return false;
   }
  }
  // Already-held cache/recording ownership + no-wait scalar epoch verification.
  // It must neither allocate nor RPC, wait for GPU or touch protected guest data.
  if(!still_current(batch.content_epoch))return false;
  for(std::size_t i=0;i<batch.count;++i){const auto&s=batch.spans[i];
   std::memcpy(s.destination,s.completed_source,std::size_t(s.bytes));
  }
  std::atomic_thread_fence(std::memory_order_release);copied=true;return true;
 });
 if(copied)return acknowledged?R::Committed:R::CommittedUnacknowledged;
 return R::Retry;
}
} // namespace BbRemote
