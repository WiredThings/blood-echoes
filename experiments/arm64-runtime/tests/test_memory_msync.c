/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <sys/mman.h>
#include <assert.h>
#include <stdatomic.h>
static int host_sync(void*,size_t,int);
#define msync host_sync
#define BB_LOW_HEAP_IMPL
#include "../vendor/bb_runtime/src/runtime_memory.c"
#undef msync
static _Thread_local int32_t guest_error;
int32_t* runtime_errno(void){return &guest_error;}
int32_t runtime_guest_errno(int error){return error;}
static unsigned calls;static uintptr_t last_address;static size_t last_size;static int last_flags,forced_error;
static atomic_int gate,entered,released,writer_entered;
static int host_sync(void* address,size_t size,int flags){
 ++calls;last_address=(uintptr_t)address;last_size=size;last_flags=flags;
 if(atomic_load(&gate)){assert(read_depth);atomic_store(&entered,1);while(!atomic_load(&released))usleep(100);}
 if(forced_error){errno=forced_error;return -1;}return msync(address,size,flags);
}
static uintptr_t native_begin;static size_t native_size;
int bb_arm_msync_native(uintptr_t at,uint64_t size,int flags){
 assert(!read_depth&&!exclusive_depth);
 if(at<native_begin||at-native_begin>=native_size)return EINVAL;
 if(!size){at=native_begin;size=native_size;}
 if(size>native_size-(at-native_begin))return EINVAL;
 return host_sync((void*)at,size,flags)?errno:0;
}
static const uintptr_t base=UINT64_C(0x7000000000);
static void* reader(void*unused){(void)unused;assert(!posix_msync(base,16384,1));return NULL;}
static void* writer(void*unused){(void)unused;while(!atomic_load(&entered))usleep(100);write_lock();atomic_store(&writer_entered,1);vmas[0].prot=1;write_unlock();return NULL;}
int main(int argc,char**argv){
 assert(argc==2);char path[4096];assert(snprintf(path,sizeof(path),"%s/sync-XXXXXX",argv[1])<(int)sizeof(path));int fd=mkstemp(path);assert(fd>=0&&!ftruncate(fd,65536));
 unsigned char*data=mmap((void*)base,65536,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);assert(data==(void*)base);
 unsigned char*alias=mmap(NULL,65536,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);assert(alias!=MAP_FAILED);
 static Vma records[3];vmas=records;vma_count=vma_capacity=2;
 records[0]=(Vma){.start=base,.end=base+32768,.kind=KIND_FLEXIBLE,.prot=3};records[1]=(Vma){.start=base+32768,.end=base+65536,.kind=KIND_DIRECT,.prot=3};
 memset(data,0x5a,65536);guest_error=777;
 for(int flags=0;flags<=2;++flags){assert(!posix_msync(base+4096,8192,flags));assert(last_address==base&&last_size==16384&&last_flags==((flags&1?MS_ASYNC:MS_SYNC)|(flags&2?MS_INVALIDATE:0)));assert(guest_error==777&&data[4096]==0x5a&&alias[0]==0x5a);}
 unsigned before=calls;assert(!posix_msync(base,65536,0)&&calls==before+1&&last_size==65536);assert(!posix_msync(base,0,0)&&last_size==32768);assert(!posix_msync(base+1,0,1)&&last_size==16384);
 for(int flags=4;flags<=16;flags*=2){assert(!posix_msync(base,16384,flags));assert(last_flags==MS_SYNC&&data[0]==0x5a);}
 before=calls;assert(posix_msync(base,16384,3)==-1&&guest_error==22&&calls==before);assert(posix_msync(UINTPTR_MAX-16,32,1)==-1&&guest_error==22&&calls==before);
 records[0].kind=KIND_RESERVED;assert(posix_msync(base,1,1)==-1&&guest_error==22&&calls==before);records[0].kind=KIND_FLEXIBLE;
 records[1].start+=16384;assert(posix_msync(base,65536,1)==-1&&guest_error==22&&calls==before);records[1].start-=16384;
 records[0].host_owned=1;assert(posix_msync(base,1,1)==-1&&calls==before);records[0].host_owned=0;
 assert(!mprotect(data,32768,PROT_NONE));records[0].prot=0;assert(!posix_msync(base,16384,1));assert(!mprotect(data,32768,PROT_READ|PROT_WRITE));records[0].prot=3;assert(data[0]==0x5a);
 forced_error=EBUSY;assert(posix_msync(base,1,1)==-1&&guest_error==16);forced_error=ENOMEM;assert(posix_msync(base,1,1)==-1&&guest_error==22);forced_error=0;
 native_begin=(uintptr_t)alias;native_size=65536;assert(!posix_msync(native_begin+4096,8192,1));assert(!posix_msync(native_begin,0,0)&&last_size==65536);assert(posix_msync(native_begin+65536,1,1)==-1&&guest_error==22);
 atomic_store(&gate,1);pthread_t a,b;assert(!pthread_create(&a,NULL,reader,NULL));assert(!pthread_create(&b,NULL,writer,NULL));while(!atomic_load(&entered))usleep(100);usleep(100000);assert(!atomic_load(&writer_entered));atomic_store(&released,1);assert(!pthread_join(a,NULL)&&!pthread_join(b,NULL));assert(atomic_load(&writer_entered));
 unsigned char value=0;assert(pread(fd,&value,1,0)==1&&value==0x5a);assert(!munmap(data,65536)&&!munmap(alias,65536)&&!close(fd)&&!unlink(path));
 puts("PASS real shared-file/alias sync, legacy flags, spans, zero length, PROT_NONE, holes, errno, host-owned rejection, native callback outside VMA lock and concurrent writer lease; no page wiring");
}
