/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <fcntl.h>
#define BB_LOW_HEAP_IMPL
#include "runtime_memory.c"
static Vma authored[4];
static int shared_fd=-1;
void WritebackTestSetup(void){
 write_lock();vmas=authored;vma_count=0;vma_capacity=4;
 assert(!vma_insert(0,(Vma){.start=UINT64_C(0x7000000000),.end=UINT64_C(0x7000008000),.kind=KIND_DIRECT,.prot=0x33,.type=3,.phys=0x10000}));write_unlock();
 char file[]="/private/tmp/bb-author-writeback-XXXXXX";shared_fd=mkstemp(file);assert(shared_fd>=0);assert(!unlink(file));assert(!ftruncate(shared_fd,0x8000));
 void* got=mmap((void*)UINT64_C(0x7000000000),0x8000,PROT_READ|PROT_WRITE,MAP_SHARED,shared_fd,0);
 assert(got==(void*)UINT64_C(0x7000000000));memset(got,0x11,0x8000);
 runtime_memory_trap(UINT64_C(0x7000000000),0x8000,32,1);
}
void WritebackTestUnrelatedMap(void){
 write_lock();assert(!vma_insert(1,(Vma){.start=UINT64_C(0x7100000000),.end=UINT64_C(0x7100004000),.kind=KIND_DIRECT,.prot=0x33,.type=3,.phys=0x30000}));write_unlock();
}
void WritebackTestSamePhysRemap(void){
 write_lock();Vma old=vmas[0];vma_erase(0,1);old.live_generation=0;assert(!vma_insert(0,old));write_unlock();
}
void WritebackTestClear(void){runtime_memory_trap(UINT64_C(0x7000000000),0x8000,32,0);}
void WritebackTestArm(void){runtime_memory_trap(UINT64_C(0x7000000000),0x8000,32,1);}
void WritebackTestClose(void){munmap((void*)UINT64_C(0x7000000000),0x8000);close(shared_fd);}

int WritebackTestExclusiveProbe(void){write_lock();int allowed=runtime_memory_live_fault_can_delegate();write_unlock();return allowed;}

void WritebackTestSplit(void){write_lock();assert(!split_at(UINT64_C(0x7000004000)));write_unlock();}
