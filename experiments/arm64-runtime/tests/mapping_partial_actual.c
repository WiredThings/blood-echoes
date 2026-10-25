/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "writeback_mapping_actual.c"
int main(void){
 setenv("BB_LAYER_READ_TRAPS","1",1);WritebackTestSetup();RuntimeLiveMapping original={},survivor={};
 assert(runtime_memory_try_live_mapping(UINT64_C(0x7000000000),0x8000,&original));runtime_memory_end_live_mapping();
 WritebackTestSplit();write_lock();vma_erase(0,1);write_unlock();
 assert(!runtime_memory_try_live_mapping(UINT64_C(0x7000000000),128,&survivor));
 assert(runtime_memory_try_live_mapping(UINT64_C(0x7000004000),128,&survivor));
 assert(runtime_live_mapping_same_slice(&original,&survivor,UINT64_C(0x7000004000),128));
 assert(runtime_memory_try_live_read_protection(UINT64_C(0x7000004000),128,32));runtime_memory_end_live_read_protection();runtime_memory_end_live_mapping();
 WritebackTestSamePhysRemap();assert(runtime_memory_try_live_mapping(UINT64_C(0x7000004000),128,&survivor));
 assert(!runtime_live_mapping_same_slice(&original,&survivor,UINT64_C(0x7000004000),128));runtime_memory_end_live_mapping();
 WritebackTestClear();WritebackTestClose();puts("ACTUAL PARTIAL MAPPING: removed left rejects, protected original right slice survives, same physical remap rejects");return 0;
}
