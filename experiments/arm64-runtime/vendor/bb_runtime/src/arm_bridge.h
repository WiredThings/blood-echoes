// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
uint64_t bb_arm_call(uintptr_t entry,const uint64_t* arguments,size_t count);
void *bb_arm_tcb(void);
void bb_arm_guest_sites(uint64_t out[3]);
void bb_arm_set_stack_size(uint64_t);
void runtime_thread_tls_cleanup(void);
int bb_arm_range(uintptr_t,size_t,int);
void bb_arm_require_callback(const char*,uintptr_t);
void bb_arm_require_range(const char*,uintptr_t,size_t,int);
void bb_arm_require_string(const char*,const char*,size_t);
void bb_arm_image_info(uintptr_t*,uintptr_t*,uintptr_t*,uint64_t*);
void bb_arm_runtime_register(void);
void bb_arm_add_native(const char* name,void* original,void* wrapper,const char* signature);
void bb_arm_low_heap_range(uintptr_t* begin,uint64_t* size);
int bb_arm_host_owned(uintptr_t address,uint64_t size);
/* Numeric VM operation over published native-owned guest ranges. Called only
 * outside the VMA lock; returns a host errno, not a guest pointer or status. */
int bb_arm_msync_native(uintptr_t address,uint64_t size,int host_flags);
int bb_arm_mlock_native(uintptr_t address,uint64_t size,int unlock);
/* Native State lease or runtime VMA lease must already be held. These functions
 * acquire only the bounded wiring ledger; they never acquire a mapping lease. */
int runtime_memory_mlock_native_leased(uintptr_t address,uint64_t size,int unlock);
int runtime_memory_msync_native_leased(uintptr_t address,uint64_t size,int flags);

#ifdef __cplusplus
}
#endif
