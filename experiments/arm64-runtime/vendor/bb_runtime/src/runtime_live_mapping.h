/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
 /* generation is the durable identity of this actual VMA insertion. */
 uint64_t begin,end,physical,generation;
 int32_t prot,type;
} RuntimeLiveMapping;
/* Value-only slice equivalence. Does not acquire a lock or authorize a write. */
static inline int runtime_live_mapping_same_slice(const RuntimeLiveMapping*a,const RuntimeLiveMapping*b,
                                                  uint64_t at,uint64_t size){
 if(!a||!b||!size||at>UINT64_MAX-size||!a->generation||a->generation!=b->generation||
    a->prot!=b->prot||a->type!=b->type||at<a->begin||at<b->begin||at>=a->end||at>=b->end||
    size>a->end-at||size>b->end-at||a->physical>UINT64_MAX-(at-a->begin)||
    b->physical>UINT64_MAX-(at-b->begin))return 0;
 return a->physical+(at-a->begin)==b->physical+(at-b->begin);
}

/* Dedicated frontend worker only. Nonblocking one shared direct VMA and no
 * current physical alias. The successful lease belongs to the calling thread.
 * No nested runtime/protection/RPC operations until end. Never touches payload. */
int runtime_memory_live_fault_can_delegate(void);
int runtime_memory_try_live_mapping(uintptr_t,uint64_t,RuntimeLiveMapping*);
void runtime_memory_end_live_mapping(void);
/* Only after a live mapping lease, on that worker. Try-lock the authoritative
 * trap owner; require VramData on every page inside that same lease; hold through
 * end. No RPC/mprotect. Frontend must opt in with BB_LAYER_READ_TRAPS=1. */
int runtime_memory_try_live_read_protection(uintptr_t,uint64_t,unsigned);
void runtime_memory_end_live_read_protection(void);
/* Retire-only lock. Same mapping/trap exclusion, but already-retired 32 is valid.
 * Does not authorize memcpy or claim any read protection exists. */
int runtime_memory_try_live_read_retirement(uintptr_t,uint64_t);
/* Successful writeback only: calling worker still owns live mapping + trap lease.
 * Retire reason32 only on whole 4 KiB pages inside the exact committed slice.
 * No public trap call, nested lock, RPC, or ownership inference. */
typedef struct {uint64_t address,size;} RuntimeLiveRetirement;
int runtime_memory_retire_live_read_protection(uintptr_t,uint64_t,RuntimeLiveRetirement*);
#ifdef __cplusplus
}
static_assert(sizeof(RuntimeLiveMapping)==40);
#else
_Static_assert(sizeof(RuntimeLiveMapping)==40,"runtime live mapping ABI");
#endif
