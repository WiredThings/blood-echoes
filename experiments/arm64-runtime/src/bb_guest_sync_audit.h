// SPDX-License-Identifier: MIT
#pragma once
#include "bb_guest_sync_policy.h"
#include "bb_stutter_audit.h"
#include "bb_producer_wait_policy.h"
namespace BbGuestSync {
inline bool Requested(){static const bool b=[] {const auto*p=std::getenv("BB_GUEST_SYNC_AUDIT");return p&&p[0]=='1';}();return b;}
inline Registry& Get(){static Registry r;return r;}
inline State& ThreadState(){static thread_local State s;return s;}
inline void NoteRole(unsigned role){
 static thread_local unsigned seen{};if(seen&role)return;seen|=role;
 char name[64]{};uint64_t tid{};const int error=pthread_getname_np(pthread_self(),name,sizeof(name));pthread_threadid_np(nullptr,&tid);
 std::fprintf(stderr,"ARM CONDITION role=%u tid=%llu name=%s name_error=%d\n",role,(unsigned long long)tid,name,error);
}
struct Sink{
 void WaitSpan(U start,U wall,U cpu,U token,U key,U mutex,uint32_t result,U op){
  NoteRole(1);
  BbAudit::Record(BbAudit::GuestConditionWait,start,wall,cpu,token);
  BbAudit::Record(BbAudit::GuestConditionIdentity,start,0,0,key);
  BbAudit::Record(BbAudit::GuestConditionMutex,start,0,0,mutex);
  BbAudit::Record(BbAudit::GuestConditionResult,start,0,0,result);
  BbAudit::Record(BbAudit::GuestConditionOperation,start,0,0,op);
 }
 void WakeSpan(U start,U wall,U cpu,U arg){NoteRole(2);BbAudit::Record(BbAudit::GuestConditionWake,start,wall,cpu,arg);}
 void ProducerWindow(U start,U wall,U cpu,U key){BbAudit::Record(BbAudit::GuestProducerCpuWindow,start,wall,cpu,key);}
 void Status(U end,U calls,U completed,U invalid,U rejected){
  auto sat=[](U a){return std::min<U>(a,UINT32_MAX);};
  BbAudit::Record(BbAudit::GuestConditionStatus,end,0,0,sat(calls)|(sat(completed)<<32));
  BbAudit::Record(BbAudit::GuestConditionErrors,end,0,0,sat(invalid)|(sat(rejected)<<32));
 }
};
}

namespace BbProducerWait {
inline Registry& Get(){static Registry r;return r;}
inline bool Requested(){static const bool b=[] {const auto*p=std::getenv("BB_PRODUCER_WAIT_AUDIT");return p&&p[0]=='1';}();return b;}
struct Sink{
 void Token(U start,U token){BbAudit::Record(BbAudit::ProducerConditionToken,start,0,0,token);}
 void Wake(U start,U wall,U cpu,U arg,U key,U operation){
  BbGuestSync::NoteRole(4);
  BbAudit::Record(BbAudit::ProducerNotification,start,wall,cpu,arg);
  BbAudit::Record(BbAudit::ProducerNotificationIdentity,start,0,0,key);
  BbAudit::Record(BbAudit::ProducerNotificationOperation,start,0,0,operation);
 }
 void Invalid(){if(auto* b=BbAudit::Get().data)b->h.dropped.fetch_add(1,std::memory_order_relaxed);}
 void Wait(U start,U wall,U cpu,U operation,U condition,U mutex,uint32_t result){
  BbAudit::Record(BbAudit::ProducerConditionWait,start,wall,cpu,operation);
  BbAudit::Record(BbAudit::ProducerConditionIdentity,start,0,0,condition);
  BbAudit::Record(BbAudit::ProducerConditionMutex,start,0,0,mutex);
  BbAudit::Record(BbAudit::ProducerConditionResult,start,0,0,result);
 }
};
}
