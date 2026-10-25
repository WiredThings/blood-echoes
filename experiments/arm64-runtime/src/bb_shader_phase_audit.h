// SPDX-License-Identifier: MIT
#pragma once
// BBAUDIT1 metadata kinds, unrelated to process exit codes. No shader payloads.
#include "bb_stutter_audit.h"
#include <limits>
namespace BbShaderPhaseAudit {
inline bool Enabled(){static const bool enabled=[] {const char* v=std::getenv("BB_SHADER_PHASE_AUDIT");return v&&v[0]=='1';}();return enabled;}
struct Module {
 BbAudit::Scope total;
 Module(uint64_t hash,uint32_t stage,uint64_t permutation):total{Enabled()?BbAudit::ShaderModuleCompile:0,hash,0}{
  if(!total.start)return;
  if(stage>6||permutation>UINT32_MAX){if(auto* b=BbAudit::Get().data)b->h.dropped.fetch_add(1,std::memory_order_relaxed);return;}
  BbAudit::Record(BbAudit::ShaderModuleIdentity,total.start,0,0,(uint64_t(stage)<<32)|permutation);
 }
 Module(const Module&)=delete;
 void Returned(bool nonzero_handle){if(total.start)BbAudit::Record(BbAudit::ShaderModuleReturned,total.start,0,0,nonzero_handle?1:0);}
};
struct Phase {
 BbAudit::Scope timing;
 Phase(const Module& parent,uint32_t kind):timing{0,parent.total.arg,0}{
  // Latch at the module boundary: a gate closed mid-compile must not turn a
  // missing subphase into a measured zero. Only the already admitted module finishes.
  if(parent.total.start){timing.kind=kind;timing.start=BbAudit::Now();timing.cpu=BbAudit::Now(CLOCK_THREAD_CPUTIME_ID);}
 }
 Phase(const Phase&)=delete;
 void End(){timing.End();}
};
}
