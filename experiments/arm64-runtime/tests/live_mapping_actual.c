/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Actual runtime VMA implementation, authored table only. No guest mappings. */
#include <assert.h>
#define BB_LOW_HEAP_IMPL
#include "runtime_memory.c"
static Vma authored[2];
void LiveTestSetup(void) {
 write_lock();
 vmas=authored;vma_count=1;vma_capacity=2;
 authored[0]=(Vma){.start=UINT64_C(0x7000000000),.end=UINT64_C(0x7000004000),
                  .kind=KIND_DIRECT,.prot=0x33,.type=3,.phys=0x10000,.live_generation=vma_new_live_generation()};
 write_unlock();
}
void LiveTestRemap(void) {write_lock();authored[0].phys+=0x4000;authored[0].live_generation=vma_new_live_generation();write_unlock();}
void LiveTestAlias(int enabled) {
 write_lock();vma_count=enabled?2:1;
 authored[1]=(Vma){.start=UINT64_C(0x7100000000),.end=UINT64_C(0x7100004000),
                  .kind=KIND_DIRECT,.prot=0x33,.type=3,.phys=authored[0].phys,.live_generation=vma_new_live_generation()};
 write_unlock();
}

int LiveTestProtectSetup(void) {
 void* at=mmap((void*)UINT64_C(0x7000000000),0x4000,PROT_READ|PROT_WRITE,
               MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
 if(at==MAP_FAILED)return 0;
 if(at!=(void*)UINT64_C(0x7000000000)){munmap(at,0x4000);return 0;}
 runtime_memory_trap(UINT64_C(0x7000000000),0x4000,32,1);return 1;
}
void LiveTestProtectArm(void){runtime_memory_trap(UINT64_C(0x7000000000),0x4000,32,1);}
void LiveTestProtectClear(void){runtime_memory_trap(UINT64_C(0x7000000000),0x4000,32,0);}
void LiveTestProtectTeardown(void){munmap((void*)UINT64_C(0x7000000000),0x4000);}

void LiveTestTrapRange(uint64_t at,uint64_t bytes,unsigned reasons,int on){runtime_memory_trap(at,bytes,reasons,on);}
