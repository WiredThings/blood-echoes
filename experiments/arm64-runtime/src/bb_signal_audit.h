// SPDX-License-Identifier: MIT
#pragma once
#include "bb_stutter_audit.h"
// Only PrepareThread runs ordinary libc / trace initialization. The signal path
// uses pre-bound TLS, lock-free atomics and POSIX async-signal-safe clock_gettime.
// https://pubs.opengroup.org/onlinepubs/9699919799/functions/V2_chap02.html
namespace BbSignalAudit {
inline std::atomic<unsigned> requested{};
struct ThreadState {BbAudit::Buffer* data{};const std::atomic<uint32_t>* gate{};uint64_t tid{};bool prepared{};};
inline ThreadState& Thread(){static thread_local ThreadState state;return state;}
inline void PrepareThread(){
 static const bool enabled=[] {const char*p=std::getenv("BB_UNALIGNED_AUDIT");return p&&p[0]=='1';}();
 if(!enabled)return;
 auto& t=Thread();if(t.prepared)return;t.prepared=true;
 auto& s=BbAudit::Get();if(!s.data)return;
 uint64_t tid{};if(pthread_threadid_np(nullptr,&tid)||!tid)return;
 // Resolve clock/TLS entry points in ordinary execution, before any guest signal.
 if(!BbAudit::Now()||!BbAudit::Now(CLOCK_THREAD_CPUTIME_ID))return;
 t.data=s.data;t.gate=s.gate;t.tid=tid;requested.store(1,std::memory_order_release);
}
constexpr bool Valid(uint64_t start,uint64_t cpu,uint64_t end,uint64_t used){
 if(!start||!cpu||!end||!used||end<start||used<cpu)return false;
 const auto wall=end-start,delta=used-cpu;
 return delta<=wall || delta-wall<=1000000; // finite clock quantization allowance
}
class Scope {
 BbAudit::Buffer* data{};uint64_t tid{},start{},cpu{},arg{},minimum{};uint32_t kind{};
public:
 Scope(uint32_t k,uint64_t a,uint64_t min=0)noexcept:arg(a),minimum(min),kind(k){
  if(!requested.load(std::memory_order_acquire))return;
  const auto& t=Thread();if(!t.data||!t.tid||(t.gate&&t.gate->load(std::memory_order_relaxed)!=1))return;
  const int saved=errno;start=BbAudit::Now();cpu=BbAudit::Now(CLOCK_THREAD_CPUTIME_ID);errno=saved;
  data=t.data;tid=t.tid;
 }
 void Rejected()noexcept{kind=BbAudit::UnalignedRejected;}
 void End()noexcept{
  if(!data)return;auto*b=data;data=nullptr;
  const int saved=errno;const auto end=BbAudit::Now(),used=BbAudit::Now(CLOCK_THREAD_CPUTIME_ID);errno=saved;
  if(!Valid(start,cpu,end,used)){b->h.dropped.fetch_add(1,std::memory_order_relaxed);return;}
  if(end-start>=minimum)BbAudit::RecordPrepared(b,kind,tid,start,end-start,used-cpu,arg);
 }
 ~Scope(){End();}
};
static_assert(std::atomic<unsigned>::is_always_lock_free && std::atomic<uint64_t>::is_always_lock_free);
}
