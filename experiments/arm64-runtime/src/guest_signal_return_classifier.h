// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "core/guest_cpu/hle_call_adapter.h"
#include <array>
#include <atomic>
#include <cerrno>
#include <csetjmp>
#include <cstdint>
#include <cstring>
extern "C" __thread sigjmp_buf* runtime_fault_recover;

// Independently expressed instruction signatures, corroborated by fpPS4
// 04cefd43/kernel/ps4_signal.pas and psOff_public a36de91/libkernel/entry.cpp.
// Admission belongs to the loader's pinned libc ABI. This is only the unwind
// PC classifier: it neither creates a signal frame nor implements sigreturn.
namespace BbSignalReturn {
inline constexpr std::array<unsigned char,19> SignalTail{
  0x48,0x8d,0x7c,0x24,0x40, // lea rdi,[rsp+0x40]
  0x6a,0x00,               // fake return address
  0x48,0xc7,0xc0,0xa1,0x01,0x00,0x00,0x0f,0x05,0xf4,0xeb,0xfd};
inline constexpr std::array<unsigned char,5> SyscallTail{0x49,0x89,0xca,0x0f,0x05};
// Only POD lives in this recovery leaf; a prior handler is always restored.
[[gnu::noinline]] inline bool Read(uintptr_t p,unsigned char* out,size_t n){
 sigjmp_buf recovery;auto* previous=runtime_fault_recover;
 if(sigsetjmp(recovery,1)){runtime_fault_recover=previous;return false;}
 runtime_fault_recover=&recovery;std::atomic_signal_fence(std::memory_order_seq_cst);
 memcpy(out,reinterpret_cast<const void*>(p),n);
 std::atomic_signal_fence(std::memory_order_seq_cst);runtime_fault_recover=previous;return true;
}
class Classifier final:public Core::GuestCpu::HleCallAdapter {
public:
 // Must prove the WHOLE span is in one published immutable guest executable
 // mapping, never a writable overlap, temporary callback stack or host image.
 using ImmutableExecutable=bool(*)(void*,uintptr_t,size_t);
 Classifier(bool observed,ImmutableExecutable executable,void* context)
  :observed_(observed),executable_(executable),context_(context){}
 Core::GuestCpu::HleCallResult Invoke(Core::GuestCpu::HleCallFrame& frame)const override{
  using Core::GuestCpu::HleCallFailure;
  if(!observed_||!executable_)return HleCallFailure{ENOTSUP,"signal PC classifier ABI"};
  auto pc=frame.gpr[7];
  // Both corroborating implementations deliberately classify this sentinel as
  // type 1, including zero. No dereference takes place in this branch.
  if(pc<4096){frame.gpr[0]=1;return true;}
  if(!executable_(context_,pc,1))return HleCallFailure{EFAULT,"signal PC not immutable executable"};
  std::array<unsigned char,19> bytes{};
  bool forward=pc<=UINTPTR_MAX-bytes.size()&&executable_(context_,pc,bytes.size());
  if(forward){if(!Read(pc,bytes.data(),bytes.size()))return HleCallFailure{EFAULT,"signal PC forward read"};
   if(bytes==SignalTail){frame.gpr[0]=1;return true;}}
  bool backward=pc>=SyscallTail.size()&&executable_(context_,pc-SyscallTail.size(),SyscallTail.size());
  if(backward){if(!Read(pc-SyscallTail.size(),bytes.data(),SyscallTail.size()))return HleCallFailure{EFAULT,"signal PC backward read"};
   if(memcmp(bytes.data(),SyscallTail.data(),SyscallTail.size())==0){frame.gpr[0]=2;return true;}}
  if(!forward||!backward)return HleCallFailure{EFAULT,"signal PC immutable range"};
  frame.gpr[0]=0;return true;
 }
private:
 bool observed_;ImmutableExecutable executable_;void* context_;
};
}
