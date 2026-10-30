// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "core/guest_cpu/hle_call_adapter.h"
#ifdef BB_CPU_BACKEND_BBCPU
#include "bbcpu.h"
#else
#include "core/fex/fex_guest_engine.h"
#endif
#include "static_range_index.h"
#include "bb_darwin_veneer_slab.h"
#include "bb_cpu_cache_identity.h"
#include "ordinary_unwind_bytes.h"
#include <array>
#include <atomic>
#include <type_traits>
#include <cerrno>
#include <csetjmp>
#include <cstring>
#include <span>
#include <system_error>
extern "C" __thread sigjmp_buf* runtime_fault_recover;
namespace BbOrdinaryUnwind {
inline constexpr size_t ContextBytes=0x500,FP=0x120;
struct alignas(16) Snapshot {std::array<unsigned char,ContextBytes> bytes;};
static_assert(std::is_trivially_copyable_v<Snapshot> && sizeof(Snapshot)==ContextBytes);
inline uint64_t Word(const Snapshot&s,size_t at){uint64_t v;memcpy(&v,s.bytes.data()+at,8);return v;}
inline uint32_t Dword(const Snapshot&s,size_t at){uint32_t v;memcpy(&v,s.bytes.data()+at,4);return v;}
// Only POD locals may live across this leaf's setjmp. Caller RAII scopes remain
// outside the recovery boundary. Restore a previously installed recovery slot.
[[gnu::noinline]] inline bool ReadSnapshot(uintptr_t source,Snapshot* out){
 sigjmp_buf recovery;sigjmp_buf* previous=runtime_fault_recover;
 // Restore the pre-read host mask as well: otherwise a caught SIGBUS/SEGV
 // can remain blocked and a second bad input cannot reach this recovery leaf.
 if(sigsetjmp(recovery,1)){runtime_fault_recover=previous;return false;}
 runtime_fault_recover=&recovery;
 std::atomic_signal_fence(std::memory_order_seq_cst);
 memcpy(out->bytes.data(),reinterpret_cast<const void*>(source),ContextBytes);
 std::atomic_signal_fence(std::memory_order_seq_cst);
 runtime_fault_recover=previous;return true;
}
inline bool AdmitObservedAbi(std::span<const std::byte> code){
 constexpr BbCpuCache::Digest expected{0xe6,0x79,0xc2,0x71,0x88,0x87,0xd1,0x94,0x2a,0x28,0x58,0x93,0x07,0x26,0x95,0x6a,0xe4,0xa1,0xd8,0xef,0x11,0x3f,0xf9,0x5c,0xbd,0x0d,0x7a,0xbf,0xdc,0x36,0x22,0x72};
 return code.size()==166&&BbCpuCache::Hash(code)==expected;
}
class Adapter final:public Core::GuestCpu::HleCallAdapter {
public:
 using Executable=bool(*)(void*,uintptr_t);
 Adapter(bool observed,Executable executable,void*context):observed_(observed),executable_(executable),context_(context){}
 static constexpr size_t SnapshotResidentBound=16384;
#ifdef BB_ORDINARY_UNWIND_AUTHORED_TEST
 using AfterSnapshot=void(*)(void*,uintptr_t);
 void SetAfterSnapshotForAuthoredControl(AfterSnapshot hook,void* context){after_=hook;after_context_=context;}
#endif
 Core::GuestCpu::HleCallResult Invoke(Core::GuestCpu::HleCallFrame&frame)const override{
  using Core::GuestCpu::HleCallFailure;
  uintptr_t owner{},rsp{};unsigned depth{};
  if(!observed_)return HleCallFailure{ENOTSUP,"ordinary unwind boundary"};
#ifdef BB_CPU_BACKEND_BBCPU
  const bool boundary=bbcpu_query_boundary(&owner,&depth,&rsp)!=0;
#else
  const bool boundary=AetherPS4::Fex::PrototypeQueryUnwindBoundary(&owner,&depth,&rsp);
#endif
  if(!observed_||!boundary||!owner||depth!=1||rsp!=frame.rsp)
   return HleCallFailure{ENOTSUP,"ordinary unwind boundary"};
  const auto source=frame.gpr[7];
  // The existing scope is tied to the owning host thread and stack mapping.
  // Supporting a heap/signal/callback context needs a stronger owner protocol.
  if(source%16||!ScopedGuestStack::Contains(source,ContextBytes)||!ScopedGuestStack::Contains(rsp,8))
   return HleCallFailure{EFAULT,"ordinary unwind owned input"};
  Snapshot pending;
  if(!ReadSnapshot(source,&pending))return HleCallFailure{EFAULT,"ordinary unwind unreadable input"};
#ifdef BB_ORDINARY_UNWIND_AUTHORED_TEST
  if(after_)after_(after_context_,source);
#endif
  const auto target=Word(pending,0xe0),stack=Word(pending,0xf8);
  // This ordinary libunwind path is bound to the observed FXSAVE producer.
  // No guessed AVX extension, alternate ABI, or uninitialised signal mask.
  if(Word(pending,0x108)!=0x480||Word(pending,0x110)!=0x10002||Word(pending,0x118)!=0x20001||Dword(pending,0xd0))
   return HleCallFailure{ENOTSUP,"ordinary unwind saved format"};
  // FEX SaveMXCSRState publishes 0xFFC0 as its authoritative supported mask.
  if(Dword(pending,FP+24)&~uint32_t(0xffc0))return HleCallFailure{EINVAL,"ordinary unwind invalid MXCSR"};
  if(!executable_||!target||!executable_(context_,target))return HleCallFailure{EFAULT,"ordinary unwind executable target"};
  if(stack%8||rsp>UINTPTR_MAX-8||stack<rsp+8||stack<16||!ScopedGuestStack::Contains(stack-16,16))
   return HleCallFailure{EFAULT,"ordinary unwind upward owned stack"};
  // A per-host-thread read-only alias freezes validated input across the return
  // to FEX. No native lock or VMA lease survives syscall return. Same-thread
  // root execution cannot enter another HLE before this short guest stub has
  // consumed its snapshot; nested callbacks are rejected above.
  if(!snapshot_){auto made=BbDarwinVeneer::Slab::Create(16384);
   if(auto* error=std::get_if<int>(&made))return HleCallFailure{*error,"ordinary unwind snapshot allocation"};
   snapshot_=std::move(std::get<std::unique_ptr<BbDarwinVeneer::Slab>>(made));}
  memcpy(snapshot_->Writer(),pending.bytes.data(),ContextBytes);
  frame.gpr[7]=reinterpret_cast<uintptr_t>(snapshot_->Reader());frame.gpr[0]=0;return true;
 }
private:
 bool observed_;Executable executable_;void* context_;
#ifdef BB_ORDINARY_UNWIND_AUTHORED_TEST
 AfterSnapshot after_{};void* after_context_{};
#endif
 inline static thread_local std::unique_ptr<BbDarwinVeneer::Slab> snapshot_;
};
class Entry final {
 std::unique_ptr<BbDarwinVeneer::Slab> slab_;
public:
 explicit Entry(uint64_t operation){
  auto made=BbDarwinVeneer::Slab::Create(16384);
  if(auto* error=std::get_if<int>(&made))throw std::system_error(*error,std::generic_category(),"ordinary unwind entry");
  slab_=std::move(std::get<std::unique_ptr<BbDarwinVeneer::Slab>>(made));
  // mov r10,rcx; movabs rax,operation; syscall; test rax,rax; jnz fail;
  // restore... ; fail:ret. Error never interprets the original pointer as a
  // validated context, even when a test reporter returns rather than exits.
  std::array<unsigned char,22> prefix{0x49,0x89,0xca,0x48,0xb8,0,0,0,0,0,0,0,0,0x0f,0x05,0x48,0x85,0xc0,0x0f,0x85,0,0};
  // Use a six-byte near conditional jump to avoid an instruction-size limit.
  std::array<unsigned char,24> full{};memcpy(full.data(),prefix.data(),20);
  memcpy(full.data()+5,&operation,8);uint32_t distance=OrdinaryUnwindCode.size();memcpy(full.data()+20,&distance,4);
  memcpy(slab_->Writer(),full.data(),full.size());
  memcpy(slab_->Writer()+full.size(),OrdinaryUnwindCode.data(),OrdinaryUnwindCode.size());
  slab_->Writer()[full.size()+OrdinaryUnwindCode.size()]=0xc3;
 }
 uintptr_t Address()const{return reinterpret_cast<uintptr_t>(slab_->Reader());}
 Core::GuestExecutionRange Range()const{return{Address(),slab_->Size(),true,false};}
};
}
