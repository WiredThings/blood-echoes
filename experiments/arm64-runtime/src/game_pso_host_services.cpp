// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone preparer support, not a game runtime or IPC guest view.
// Clocks follow runtime_kernel.c's monotonic ns/us and ARM CNTFRQ contracts.
// Every guest/VM/restart/thread-into-guest interface is an explicit unsupported
// boundary (80). No empty-success VM calls, fake mapped memory, TCB or address table.
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <setjmp.h>
#include "runtime_exit_codes.h"
#include "runtime_read_arm.h"

namespace {
[[noreturn]] void Forbidden(const char* name){std::fprintf(stderr,"PSO preparer: unsupported guest/runtime interface %s\n",name);std::_Exit(BB_ARM_EXIT_UNSUPPORTED_IMPORT);}
uint64_t Nanoseconds(){
 struct timespec now{};if(clock_gettime(CLOCK_MONOTONIC,&now))Forbidden("CLOCK_MONOTONIC");
 return uint64_t(now.tv_sec)*1000000000+uint64_t(now.tv_nsec);
}
const uint64_t process_start=Nanoseconds();
}
extern "C" {
__thread sigjmp_buf* runtime_fault_recover=nullptr;
// There is no guest/toggle-file mutator in this process. ShaderCompiler's actual
// source options remain the inherited BB_MTL_* environment and are identity-bound.
uint64_t runtime_disabled_optimizations=0,runtime_experiment_bits=0;
volatile int runtime_restarting=0;
uint64_t runtime_process_time_counter(){return Nanoseconds()-process_start;}
uint64_t runtime_process_time_us(){return runtime_process_time_counter()/1000;}
uint64_t runtime_tsc_frequency(){
#if defined(__aarch64__)
 uint64_t n;__asm__ volatile("mrs %0, cntfrq_el0":"=r"(n));if(!n)Forbidden("CNTFRQ");return n;
#else
 Forbidden("native ARM CNTFRQ required");
#endif
}
}
using Range=void(*)(uintptr_t,uint64_t);
#define FENCE(ret,name,args) extern "C" ret name args {Forbidden(#name);}
FENCE(int32_t*,runtime_errno,(void))
FENCE(int,runtime_file_translate,(const char*,char*,size_t))
FENCE(uint64_t,runtime_heap_growths,(void))
FENCE(void,runtime_sleep_stats,(uint64_t*,uint64_t*))
FENCE(void,runtime_wait_report,(double))
// Original attach_host explicitly creates a guest TCB/TLS. It is not a generic
// pthread naming service and must not be silently "supported" by this worker.
FENCE(void,runtime_thread_attach_host,(const char*))
FENCE(void,bb_arm_native_host_thread_detach,(void))
FENCE(void,runtime_restart,(void))
FENCE(void,runtime_memory_set_guest_chunk_whole,(int))
FENCE(void,runtime_memory_set_guest_chunk_allocator,(int(*)(uint64_t,uint64_t)))
FENCE(int,runtime_memory_snapshot_v1,(uint64_t*,uint64_t))
FENCE(uint64_t,runtime_memory_clamp,(uintptr_t,uint64_t))
FENCE(int,runtime_memory_region,(uintptr_t,uintptr_t*,uintptr_t*,int*))
FENCE(const uint64_t*,runtime_memory_generation,(void))
FENCE(int,runtime_memory_vma_info,(uintptr_t,int*,int*,uintptr_t*))
FENCE(int,runtime_memory_direct_phys,(uintptr_t,uint64_t*,uintptr_t*))
FENCE(int,runtime_memory_is_mapped,(uintptr_t,uint64_t))
FENCE(int,runtime_memory_write_backing,(uintptr_t,const void*,uint64_t))
FENCE(void,runtime_memory_read_backing,(uintptr_t,void*,uint64_t))
FENCE(void,runtime_memory_gpu_protect,(uintptr_t,uint64_t,int,int))
FENCE(void,runtime_memory_trap,(uintptr_t,uint64_t,unsigned,int))
FENCE(unsigned,runtime_memory_trap_reasons,(uintptr_t))
FENCE(void*,runtime_memory_backing_pointer,(uint64_t))
FENCE(uintptr_t,runtime_memory_resolve,(const char*))
FENCE(void,runtime_memory_set_gpu_hooks,(Range,Range,Range))
FENCE(void,runtime_memory_set_note_write_hook,(Range))
FENCE(void,runtime_memory_set_cpu_write_hook,(Range))
FENCE(void,runtime_memory_set_write_watch,(uintptr_t,uint64_t,int))
FENCE(int,runtime_memory_host_pool,(uint64_t,int*,uint64_t*,uint64_t*))
FENCE(void,runtime_memory_set_mirror_hook,(void(*)(int,uintptr_t,uint64_t,uint64_t,int,int,int,int)))
FENCE(int,runtime_memory_lock_held,(void))
FENCE(int,runtime_memory_exclude,(uintptr_t,uintptr_t))
FENCE(int,runtime_memory_reclaim_if_unaliased_v2,(uintptr_t,uint64_t,int(*)(void*),void*))
FENCE(int,runtime_memory_arm_read,(const RuntimeReadArmArgs*,RuntimeReadArmReceipt*))
FENCE(int,runtime_memory_finish_read_arm,(uint64_t,int,RuntimeReadArmReceipt*))
FENCE(int,runtime_memory_live_fault_can_delegate,(void))
FENCE(int,runtime_memory_try_live_mapping,(uintptr_t,uint64_t,RuntimeLiveMapping*))
FENCE(void,runtime_memory_end_live_mapping,(void))
FENCE(int,runtime_memory_try_live_read_protection,(uintptr_t,uint64_t,unsigned))
FENCE(void,runtime_memory_end_live_read_protection,(void))
FENCE(int,runtime_memory_try_live_read_retirement,(uintptr_t,uint64_t))
FENCE(int,runtime_memory_retire_live_read_protection,(uintptr_t,uint64_t,RuntimeLiveRetirement*))
FENCE(unsigned,runtime_memory_host_fault_pages,(uintptr_t,int,uintptr_t*,unsigned))
FENCE(int,runtime_memory_host_access_now_allowed,(uintptr_t,int))
#undef FENCE
