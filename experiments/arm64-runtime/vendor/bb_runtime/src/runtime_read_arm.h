/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include "runtime_live_mapping.h"
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* One cold transaction, bounded to an unaligned 8 MiB image, never per-address metadata. */
#define RUNTIME_READ_ARM_PAGES 2049u
#define RUNTIME_READ_ARM_WORDS 33u
typedef struct {RuntimeLiveMapping mapping;uint64_t address,size;} RuntimeReadArmArgs;
typedef struct {uint64_t token,address,size;uint32_t pages,ok;uint64_t actual32[RUNTIME_READ_ARM_WORDS];} RuntimeReadArmReceipt;
int runtime_memory_arm_read(const RuntimeReadArmArgs*,RuntimeReadArmReceipt*);
int runtime_memory_finish_read_arm(uint64_t token,int commit,RuntimeReadArmReceipt*);
#ifdef __cplusplus
}
static_assert(sizeof(RuntimeReadArmArgs)==56&&offsetof(RuntimeReadArmArgs,address)==40);
static_assert(sizeof(RuntimeReadArmReceipt)==296&&offsetof(RuntimeReadArmReceipt,actual32)==32);
#else
_Static_assert(sizeof(RuntimeReadArmArgs)==56&&offsetof(RuntimeReadArmArgs,address)==40,"arm args ABI");
_Static_assert(sizeof(RuntimeReadArmReceipt)==296&&offsetof(RuntimeReadArmReceipt,actual32)==32,"arm receipt ABI");
#endif
