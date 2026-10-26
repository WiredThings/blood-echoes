// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Cold native factory boundaries only. API wall time is neither GPU execution
// time nor proof of driver machine-code compilation. No IPC, cache or draw hook.
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <utility>
#include <type_traits>

namespace BbMetalPhase {
enum class Kind:unsigned {SourceLibrary,LocalLibraryData,StrictRender,StrictCompute,Render,Compute,ArchiveRender,ArchiveCompute,Count};
enum class Phase:unsigned {PreGpuReady,Runtime};
inline constexpr unsigned Count=unsigned(Kind::Count);
struct Value {std::uint64_t calls{},ok{},failed{},api_ns{};};
using Snapshot=std::array<Value,Count>;
struct AtomicValue {std::atomic<std::uint64_t>calls{},ok{},failed{},api_ns{};};
inline std::array<std::array<AtomicValue,Count>,2> values{};
inline std::atomic<Phase> phase{Phase::PreGpuReady};
// Protect only admission/phase transition, never a native factory invocation.
inline std::mutex admission;
inline std::atomic<std::uint64_t> startup_inflight{};
inline std::atomic<bool> startup_reported{};
inline bool Reporting(){static const bool enabled=[] {for(const auto*n:{"BB_FRAME_LOG_ONLY","BB_FRAME_STATS","BB_MTL_COMPILE_AUDIT"}){const char*v=std::getenv(n);if(v&&v[0]=='1')return true;}return false;}();return enabled;}
inline Snapshot Read(Phase p){Snapshot out{};for(unsigned i=0;i<Count;++i){auto&v=values[unsigned(p)][i];out[i]={v.calls.load(std::memory_order_relaxed),v.ok.load(std::memory_order_relaxed),v.failed.load(std::memory_order_relaxed),v.api_ns.load(std::memory_order_relaxed)};}return out;}
inline void PrintStartup(){
 if(phase.load(std::memory_order_acquire)!=Phase::Runtime||startup_inflight.load(std::memory_order_acquire))return;
 bool expected=false;if(!startup_reported.compare_exchange_strong(expected,true,std::memory_order_acq_rel))return;
 if(!Reporting())return;
 static constexpr const char*names[]{"source","local_data","strict_render","strict_compute","render","compute","archive_render","archive_compute"};
 const auto s=Read(Phase::PreGpuReady);std::fprintf(stderr,"Metal native API startup: phase=pre_gpu_ready settled=1");
 for(unsigned i=0;i<Count;++i)std::fprintf(stderr," %s=%llu/%llu/%llu/%.3f",names[i],(unsigned long long)s[i].calls,(unsigned long long)s[i].ok,(unsigned long long)s[i].failed,s[i].api_ns/1e6);
 std::fputs(" units=calls/ok/failed/api_wall_ms; not_gpu_or_pure_compile_time\n",stderr);
}
inline void MarkGpuReady(){
 {std::scoped_lock lock{admission};if(phase.load(std::memory_order_relaxed)==Phase::Runtime)return;phase.store(Phase::Runtime,std::memory_order_release);}
 // Any cold call admitted before ready finishes in the startup bucket. Its
 // last completion emits the one settled startup snapshot if still in flight.
 PrintStartup();
}
inline Kind PsoKind(bool compute,bool strict){return strict?(compute?Kind::StrictCompute:Kind::StrictRender):(compute?Kind::Compute:Kind::Render);}
struct Call {
 Kind kind;Phase bucket;std::chrono::steady_clock::time_point start;bool finished{};
 explicit Call(Kind k):kind{k}{
  {std::scoped_lock lock{admission};bucket=phase.load(std::memory_order_relaxed);if(bucket==Phase::PreGpuReady)startup_inflight.fetch_add(1,std::memory_order_relaxed);values[unsigned(bucket)][unsigned(kind)].calls.fetch_add(1,std::memory_order_relaxed);}
  start=std::chrono::steady_clock::now();
 }
 void Finish(bool ok)noexcept{
  if(finished)return;finished=true;const auto end=std::chrono::steady_clock::now();
  auto&v=values[unsigned(bucket)][unsigned(kind)];v.api_ns.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(end-start).count(),std::memory_order_relaxed);
  (ok?v.ok:v.failed).fetch_add(1,std::memory_order_relaxed);
  if(bucket==Phase::PreGpuReady&&startup_inflight.fetch_sub(1,std::memory_order_acq_rel)==1)PrintStartup();
 }
 ~Call(){Finish(false);} // A C++ exception is an unsuccessful API boundary.
};
template<class Function>auto Invoke(Kind kind,Function&& function){using R=decltype(function());static_assert(std::is_integral_v<R>||std::is_enum_v<R>,"ObjC factory results must stay in their original direct new-family assignment");Call call{kind};auto result=std::forward<Function>(function)();call.Finish(bool(result));return result;}
inline void CsvHeader(FILE*f){std::fputs(",metal_gpu_ready_seen,metal_source_calls,metal_source_failed,metal_source_api_ms,metal_local_calls,metal_local_failed,metal_local_api_ms,metal_strict_pso_attempts,metal_strict_pso_hits,metal_strict_pso_api_ms,metal_render_calls,metal_compute_calls,metal_pso_failed,metal_pso_api_ms,metal_archive_add_calls,metal_archive_add_failed,metal_archive_add_api_ms",f);}
inline void CsvDelta(FILE*f,Snapshot&previous){
 const auto now=Read(Phase::Runtime);Snapshot d{};
 for(unsigned i=0;i<Count;++i){d[i]={now[i].calls-previous[i].calls,now[i].ok-previous[i].ok,now[i].failed-previous[i].failed,now[i].api_ns-previous[i].api_ns};}previous=now;
 const auto at=[&](Kind k)->const Value&{return d[unsigned(k)];};
 const auto source=at(Kind::SourceLibrary),local=at(Kind::LocalLibraryData),sr=at(Kind::StrictRender),sc=at(Kind::StrictCompute),r=at(Kind::Render),c=at(Kind::Compute),ar=at(Kind::ArchiveRender),ac=at(Kind::ArchiveCompute);
 std::fprintf(f,",%u,%llu,%llu,%.3f,%llu,%llu,%.3f,%llu,%llu,%.3f,%llu,%llu,%llu,%.3f,%llu,%llu,%.3f",unsigned(phase.load(std::memory_order_relaxed)==Phase::Runtime),(unsigned long long)source.calls,(unsigned long long)source.failed,source.api_ns/1e6,(unsigned long long)local.calls,(unsigned long long)local.failed,local.api_ns/1e6,(unsigned long long)(sr.calls+sc.calls),(unsigned long long)(sr.ok+sc.ok),(sr.api_ns+sc.api_ns)/1e6,(unsigned long long)r.calls,(unsigned long long)c.calls,(unsigned long long)(r.failed+c.failed),(r.api_ns+c.api_ns)/1e6,(unsigned long long)(ar.calls+ac.calls),(unsigned long long)(ar.failed+ac.failed),(ar.api_ns+ac.api_ns)/1e6);
}
} // namespace BbMetalPhase
