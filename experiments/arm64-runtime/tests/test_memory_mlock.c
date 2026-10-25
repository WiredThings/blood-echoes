/* SPDX-License-Identifier: GPL-2.0-or-later
 * Actual runtime_memory.c integration with authored host-wiring failure backend.
 * mmap/mprotect/msync are real owned mappings; ZERO pages physically mlocked.
 * Native production callback is replaced only by an explicit published-range
 * test owner. The C++ State publisher still requires separate actual FEX QA.
 */
#include <sys/types.h>
#include <sys/mman.h>
#include <assert.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
static int host_pin(const void*,size_t);
static void*host_realloc(void*,size_t);
static void mapping_failure_observer(const char*);
static int host_unpin(const void*,size_t);
static int host_sync(void*,size_t,int);
static void*host_map(void*,size_t,int,int,int,off_t);
#define BB_MLOCK_MAPPING_FAILURE_OBSERVER(stage) mapping_failure_observer(stage)
#define realloc host_realloc
#define mlock host_pin
#define munlock host_unpin
#define msync host_sync
#define mmap host_map
#define BB_LOW_HEAP_IMPL
#include "../vendor/bb_runtime/src/runtime_memory.c"
#undef mlock
#undef munlock
#undef msync
#undef mmap
#undef realloc
#undef BB_MLOCK_MAPPING_FAILURE_OBSERVER
static _Thread_local int32_t guest_error;
int32_t*runtime_errno(void){return &guest_error;}
/* Link the actual runtime_kernel.c errno converter as a separate TU. */
static const uintptr_t base=UINT64_C(0x7000000000);
static uintptr_t native_begin,fail_map;static uint64_t native_bytes;
static int forced_lock_error,force_realloc_failure;
static void*host_realloc(void*p,size_t n){if(force_realloc_failure){errno=ENOMEM;return NULL;}return realloc(p,n);}
static _Thread_local unsigned native_lease;
static uintptr_t pin_pages[32];static unsigned pin_count,host_pins,host_unpins,host_syncs;
static atomic_int gate,entered,released,writer_done;
int bb_arm_host_owned(uintptr_t at,uint64_t size){return at<base||at>=USER_MAX||!size||size>USER_MAX-at;}
static int pin_find(uintptr_t a){for(unsigned i=0;i<pin_count;++i)if(pin_pages[i]==a)return(int)i;return-1;}
static int host_pin(const void*addr,size_t bytes){
 if(forced_lock_error){errno=forced_lock_error;return-1;}
 const uintptr_t at=(uintptr_t)addr;assert(bytes==PAGE&&!(at&(PAGE-1)));assert(read_depth||exclusive_depth||native_lease);assert(pin_find(at)<0&&pin_count<32);
 if(atomic_load(&gate)){atomic_store(&entered,1);while(!atomic_load(&released))usleep(100);}
 ++host_pins;pin_pages[pin_count++]=at;return 0;
}
static int host_unpin(const void*addr,size_t bytes){
 assert(bytes==PAGE);const int i=pin_find((uintptr_t)addr);assert(i>=0);++host_unpins;pin_pages[i]=pin_pages[--pin_count];return 0;
}
static int host_sync(void*addr,size_t bytes,int flags){++host_syncs;return msync(addr,bytes,flags);}
static void*host_map(void*addr,size_t bytes,int prot,int flags,int fd,off_t offset){
 if((flags&MAP_FIXED)&&(uintptr_t)addr==fail_map){errno=ENOMEM;return MAP_FAILED;}
 void*result=mmap(addr,bytes,prot,flags,fd,offset);
 if(result!=MAP_FAILED&&(flags&MAP_FIXED))for(unsigned i=0;i<pin_count;){const uintptr_t a=pin_pages[i];if(a>=(uintptr_t)addr&&a-(uintptr_t)addr<bytes)pin_pages[i]=pin_pages[--pin_count];else ++i;}
 return result;
}
static int native_owned(uintptr_t at,uint64_t n){return native_bytes&&at>=native_begin&&at-native_begin<native_bytes&&n<=native_bytes-(at-native_begin);}
int bb_arm_mlock_native(uintptr_t at,uint64_t n,int unlock){
 assert(!read_depth&&!exclusive_depth);if(!native_owned(at,n))return ENOMEM;++native_lease;const int e=runtime_memory_mlock_native_leased(at,n,unlock);--native_lease;return e;
}
int bb_arm_msync_native(uintptr_t at,uint64_t n,int flags){
 assert(!read_depth&&!exclusive_depth);if(!native_owned(at,n))return EINVAL;
 if(!n){at=native_begin;n=native_bytes;}++native_lease;const int e=runtime_memory_msync_native_leased(at,n,flags);--native_lease;return e;
}
static void publish(uintptr_t at,uint64_t bytes,int prot){
 write_lock();vma_count=0;assert(!vma_insert(0,(Vma){.start=at,.end=at+bytes,.kind=KIND_DIRECT,.prot=prot,.phys=0}));write_unlock();
}
static void restore(uint64_t bytes){
 assert(host_map((void*)base,bytes,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE|MAP_FIXED,-1,0)==(void*)base);
 bb_mlock_forget_committed(&mlock_ledger,base,bytes);publish(base,bytes,3);
}
/* Oracle observes actual CPU page protections/bytes AND old metadata before
 * the fail-stop; the parent then requires the production exit contract 74. */
static int oracle_fd=-1,oracle_phase=-1;
static int actual_protection(uintptr_t target){
 mach_vm_address_t at=target;mach_vm_size_t size=0;mach_port_t object=MACH_PORT_NULL;
 vm_region_basic_info_data_64_t info={0};mach_msg_type_number_t count=VM_REGION_BASIC_INFO_COUNT_64;
 kern_return_t result=mach_vm_region(mach_task_self(),&at,&size,VM_REGION_BASIC_INFO_64,(vm_region_info_t)&info,&count,&object);
 if(object!=MACH_PORT_NULL)mach_port_deallocate(mach_task_self(),object);
 return result==KERN_SUCCESS&&at<=target&&target-at<size?info.protection:-1;
}
typedef struct {int phase,first_prot,second_prot,first_vma,second_vma,first_vma_prot,second_vma_prot;unsigned wired,host_wired;uint64_t first_vma_phys,second_vma_phys;int first_byte,second_byte,physical_block_live;} Oracle;
static void mapping_failure_observer(const char*stage){
 assert(oracle_fd>=0&&oracle_phase>=0&&stage);Oracle o={.phase=oracle_phase};
 o.first_prot=actual_protection(base);o.second_prot=actual_protection(base+PAGE);
 int p=0,t=0;uintptr_t end;o.first_vma=runtime_memory_vma_info(base,&p,&t,&end);o.first_vma_prot=p;p=0;o.second_vma=runtime_memory_vma_info(base+PAGE,&p,&t,&end);o.second_vma_prot=p;
 o.first_vma_phys=UINT64_MAX;o.second_vma_phys=UINT64_MAX;runtime_memory_direct_phys(base,&o.first_vma_phys,&end);runtime_memory_direct_phys(base+PAGE,&o.second_vma_phys,&end);
 o.wired=mlock_ledger.count;o.host_wired=pin_count;
 o.first_byte=o.first_prot>=0&&(o.first_prot&PROT_READ)?*(unsigned char*)base:-1;o.second_byte=o.second_prot>=0&&(o.second_prot&PROT_READ)?*(unsigned char*)(base+PAGE):-1;
 o.physical_block_live=blocks[0].used;
 if(oracle_phase==0){assert(o.first_prot==(PROT_READ|PROT_WRITE)&&o.second_prot==(PROT_READ|PROT_WRITE));assert(o.first_vma&&o.second_vma&&o.wired==2&&o.host_wired==2);}
 else if(oracle_phase==1){assert(o.first_prot==(PROT_READ|PROT_WRITE)&&o.second_prot==(PROT_READ|PROT_WRITE));assert(o.first_byte==0xa1&&o.second_byte==0x5a);assert(o.first_vma_prot==3&&o.second_vma_prot==3&&o.first_vma_phys==0&&o.second_vma_phys==PAGE);assert(o.first_vma&&o.second_vma&&o.first_vma_prot==3&&o.second_vma_prot==3&&o.wired==1&&o.host_wired==1);assert(pin_find(base)<0&&pin_find(base+PAGE)>=0);}
 else if(oracle_phase==2){assert(o.first_prot==PROT_NONE&&o.second_prot==(PROT_READ|PROT_WRITE)&&o.second_byte==0x5a);assert(o.first_vma&&o.second_vma&&o.first_vma_prot==3&&o.second_vma_prot==3&&o.wired==1&&o.host_wired==1);assert(pin_find(base)<0&&pin_find(base+PAGE)>=0);}
 else if(oracle_phase==3){assert(o.first_prot==(PROT_READ|PROT_WRITE)&&o.second_prot==PROT_NONE&&o.first_byte==0x5a);assert(o.first_vma&&!o.second_vma&&o.wired==1&&o.host_wired==1);assert(pin_find(base)>=0&&pin_find(base+PAGE)<0);}
 else if(oracle_phase==4){assert(o.first_prot==PROT_NONE&&o.second_prot==(PROT_READ|PROT_WRITE)&&o.second_byte==0x5a);assert(!o.first_vma&&o.second_vma&&o.wired==1&&o.host_wired==1);assert(o.physical_block_live&&live_bytes==2*PAGE&&backing_base[0]==0x5a&&backing_base[PAGE]==0x5a);}
 else assert(0);
 assert(write(oracle_fd,&o,sizeof(o))==(ssize_t)sizeof(o));
}
static void stop_child_case(int phase){
 int pipefd[2];assert(!pipe(pipefd));pid_t pid=fork();assert(pid>=0);
 if(!pid){
  close(pipefd[0]);oracle_fd=pipefd[1];oracle_phase=phase;
  if(phase==0){fail_map=base;(void)direct_unmap((void*)base,PAGE);}
  else if(phase==1){fail_map=base+PAGE;write_lock();void*at=(void*)base;(void)place(&at,2*PAGE,3,MAP_FIXED_FLAG,PAGE,KIND_DIRECT,0,CHUNK-PAGE);write_unlock();}
  else if(phase==2){fail_map=base+PAGE;write_lock();vmas[0].end=base+PAGE;assert(!vma_insert(1,(Vma){.start=base+PAGE,.end=base+2*PAGE,.kind=KIND_DIRECT,.prot=3,.phys=PAGE}));write_unlock();(void)direct_unmap((void*)base,2*PAGE);}
  else if(phase==3){write_lock();free(vmas);vmas=calloc(1,sizeof(*vmas));assert(vmas);vma_count=vma_capacity=1;vmas[0]=(Vma){.start=base,.end=base+4*PAGE,.kind=KIND_DIRECT,.prot=3};force_realloc_failure=1;void*at=(void*)(base+PAGE);(void)place(&at,PAGE,0,MAP_FIXED_FLAG,PAGE,KIND_RESERVED,0,0);write_unlock();}
  else if(phase==4){fail_map=base+PAGE;write_lock();vma_count=0;assert(!vma_insert(0,(Vma){.start=base,.end=base+PAGE,.kind=KIND_DIRECT,.prot=3,.phys=0}));assert(!vma_insert(1,(Vma){.start=base+PAGE,.end=base+2*PAGE,.kind=KIND_DIRECT,.prot=3,.phys=PAGE}));write_unlock();(void)direct_release(0,2*PAGE);}
  _Exit(99); /* Returning with inconsistent mappings is an explicit failure. */
 }
 close(pipefd[1]);Oracle oracle;ssize_t got=read(pipefd[0],&oracle,sizeof(oracle));close(pipefd[0]);int status=0;assert(waitpid(pid,&status,0)==pid);assert(got==(ssize_t)sizeof(oracle)&&oracle.phase==phase&&WIFEXITED(status)&&WEXITSTATUS(status)==BB_ARM_EXIT_HLE_FAULT);
 printf("PASS fail-stop oracle phase=%d host_prot=%d/%d vma=%d/%d vma_prot=%d/%d vma_phys=%llu/%llu wired=%u/%u bytes=%d/%d block_live=%d exit=74\n",phase,oracle.first_prot,oracle.second_prot,oracle.first_vma,oracle.second_vma,oracle.first_vma_prot,oracle.second_vma_prot,(unsigned long long)oracle.first_vma_phys,(unsigned long long)oracle.second_vma_phys,oracle.wired,oracle.host_wired,oracle.first_byte,oracle.second_byte,oracle.physical_block_live);
}
static void*reader(void*unused){(void)unused;assert(!kernel_mlock(base,PAGE));return NULL;}
static void*writer(void*unused){(void)unused;while(!atomic_load(&entered))usleep(100);assert(!direct_unmap((void*)base,PAGE));atomic_store(&writer_done,1);return NULL;}
int main(int argc,char**argv){
 assert(argc==2);assert(!setenv("BB_MLOCK_MAX_CALL_MIB","16",1)&&!setenv("BB_MLOCK_MAX_TOTAL_MIB","64",1)&&!setenv("BB_MLOCK_MAX_PAGES","4096",1));
 unsigned char*data=mmap((void*)base,4*PAGE,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);assert(data==(void*)base);
 publish(base,4*PAGE,3);memset(data,0x5a,4*PAGE);guest_error=999;
 assert(!kernel_mlock(base+1,PAGE)&&mlock_ledger.count==2&&host_pins==2);assert(!kernel_mlock(base,2*PAGE)&&host_pins==2);
 assert(posix_msync(base,2*PAGE,2)==-1&&guest_error==16&&!host_syncs);assert(!posix_msync(base,2*PAGE,0)&&host_syncs==1&&data[0]==0x5a);
 assert(!kernel_munlock(base,2*PAGE)&&!mlock_ledger.count&&!pin_count&&host_unpins==2);assert(!kernel_munlock(base,2*PAGE)&&host_unpins==2);
 const int host_errors[]={EPERM,ENOMEM,EAGAIN,EINVAL,EACCES};const unsigned guest_errors[]={1,12,35,22,13};
 for(unsigned i=0;i<sizeof(host_errors)/sizeof(host_errors[0]);++i){forced_lock_error=host_errors[i];assert(kernel_mlock(base,PAGE)==(int32_t)(UINT32_C(0x80020000)|guest_errors[i]));assert(posix_mlock(base,PAGE)==-1&&guest_error==(int32_t)guest_errors[i]&&!mlock_ledger.count&&!pin_count);}forced_lock_error=0;
 unsigned before=host_pins;assert(kernel_mlock(UINTPTR_MAX-1,4)==(int32_t)UINT32_C(0x80020016)&&host_pins==before);
 assert(posix_mlock(base,BB_MLOCK_CALL_HARD+PAGE)==-1&&guest_error==12&&host_pins==before);
 vmas[0].host_owned=1;assert(kernel_mlock(base,PAGE)==(int32_t)UINT32_C(0x8002000c));vmas[0].host_owned=0;
 vmas[0].kind=KIND_RESERVED;assert(kernel_mlock(base,PAGE)==(int32_t)UINT32_C(0x8002000c));vmas[0].kind=KIND_DIRECT;
 assert(!kernel_mprotect((void*)base,PAGE,0));assert(kernel_mlock(base,PAGE)==(int32_t)UINT32_C(0x8002000c));assert(!kernel_mprotect((void*)base,PAGE,3));
 assert(!kernel_mlock(base,PAGE));assert(!kernel_mprotect((void*)base,PAGE,0));assert(!kernel_munlock(base,PAGE));assert(!kernel_mprotect((void*)base,PAGE,3));
 assert(!kernel_mlock(base,2*PAGE));stop_child_case(0);stop_child_case(2);stop_child_case(3);assert(mlock_ledger.count==2&&pin_count==2);
 assert(!direct_unmap((void*)base,PAGE)&&mlock_ledger.count==1&&pin_find(base)<0&&pin_find(base+PAGE)>=0);
 restore(4*PAGE);assert(!kernel_mlock(base,2*PAGE));write_lock();void*at=(void*)base;assert(!place(&at,PAGE,0,MAP_FIXED_FLAG,PAGE,KIND_RESERVED,0,0));write_unlock();assert(mlock_ledger.count==1&&pin_find(base)<0);restore(4*PAGE);
 char path[4096];assert(snprintf(path,sizeof(path),"%s/mlock-XXXXXX",argv[1])<(int)sizeof(path));pool_fd=mkstemp(path);assert(pool_fd>=0&&!ftruncate(pool_fd,(off_t)(CHUNK+PAGE)));
 static int chunk[64];for(unsigned i=0;i<64;++i)chunk[i]=-2;chunk_fds=chunk;whole_chunks=0;
 unsigned char file_page[16384];memset(file_page,0xa1,sizeof(file_page));assert(pwrite(pool_fd,file_page,sizeof(file_page),(off_t)(CHUNK-PAGE))==(ssize_t)sizeof(file_page));
 memset(data,0x5a,4*PAGE);assert(!kernel_mlock(base,2*PAGE));stop_child_case(1);assert(mlock_ledger.count==2&&pin_count==2&&data[0]==0x5a&&data[PAGE]==0x5a);chunk_fds=NULL;restore(4*PAGE);
 /* A validation failure is recoverable; a post-notification host failure is not. */
 static unsigned char backing[4*16384];backing_base=backing;memset(backing,0x5a,sizeof(backing));blocks[0]=(Block){0,PAGE,0,1};live_bytes=PAGE;
 assert(!kernel_mlock(base,PAGE));vmas[0].host_owned=1;assert(direct_release(0,PAGE)!=0&&blocks[0].used&&live_bytes==PAGE&&mlock_ledger.count==1&&backing[0]==0x5a);vmas[0].host_owned=0;
 assert(!direct_release(0,PAGE)&&!blocks[0].used&&!live_bytes&&!mlock_ledger.count&&!pin_count&&backing[0]==0);restore(4*PAGE);
 memset(data,0x5a,4*PAGE);memset(backing,0x5a,sizeof(backing));blocks[0]=(Block){0,2*PAGE,0,1};live_bytes=2*PAGE;assert(!kernel_mlock(base,2*PAGE));stop_child_case(4);assert(blocks[0].used&&live_bytes==2*PAGE&&mlock_ledger.count==2&&pin_count==2);
 assert(!direct_release(0,2*PAGE)&&!blocks[0].used&&!live_bytes&&!mlock_ledger.count&&!pin_count&&backing[0]==0&&backing[PAGE]==0);restore(4*PAGE);
 native_begin=(uintptr_t)runtime_low_map(PAGE,PROT_READ|PROT_WRITE);assert(native_begin);native_bytes=PAGE;
 assert(!kernel_mlock(native_begin,PAGE)&&mlock_ledger.count==1);assert(posix_msync(native_begin,PAGE,2)==-1&&guest_error==16);
 native_bytes=0;runtime_low_release((void*)native_begin,PAGE);assert(!mlock_ledger.count&&!pin_count);assert(kernel_mlock(native_begin,PAGE)==(int32_t)UINT32_C(0x8002000c));
 atomic_store(&gate,1);pthread_t a,b;assert(!pthread_create(&a,NULL,reader,NULL)&&!pthread_create(&b,NULL,writer,NULL));while(!atomic_load(&entered))usleep(100);usleep(50000);assert(!atomic_load(&writer_done));atomic_store(&released,1);assert(!pthread_join(a,NULL)&&!pthread_join(b,NULL)&&atomic_load(&writer_done));assert(!mlock_ledger.count&&!pin_count);
 assert(!munmap(data,4*PAGE)&&!close(pool_fd)&&!unlink(path));free(vmas);puts("PASS authored VM Mlock integration controls: SCE/POSIX, protection/ownership, lifecycle leases, duplicate set, wired msync, exact partial MAP_FIXED retire, failed/success physical release, native-owned cleanup; zero physical wiring");
}
