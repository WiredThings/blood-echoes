/* Authored microcontrol. At most one 16 KiB mapping is ever wired. No game. */
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
static int wiring(void *p) {
    mach_vm_address_t a=(mach_vm_address_t)p;mach_vm_size_t n=0;
    vm_region_basic_info_data_64_t info={0};mach_msg_type_number_t count=VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object=MACH_PORT_NULL;
    kern_return_t r=mach_vm_region(mach_task_self(),&a,&n,VM_REGION_BASIC_INFO_64,(vm_region_info_t)&info,&count,&object);
    if(object!=MACH_PORT_NULL)mach_port_deallocate(mach_task_self(),object);
    return r==KERN_SUCCESS ? info.user_wired_count : -1;
}
int main(void) {
    const long page=sysconf(_SC_PAGESIZE);struct rlimit limit;
    if(page!=16384||getrlimit(RLIMIT_MEMLOCK,&limit))return 2;
    unsigned char *p=mmap(NULL,(size_t)page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    if(p==MAP_FAILED)return 3;
    memset(p,0x5a,(size_t)page);int before=wiring(p),first=-1,second=-1,unlocked=-1,code=0,lock_errno=0,final_wire=-1,removed=0;
    errno=0;
    if(mlock(p,(size_t)page)) {lock_errno=errno;code=4;goto cleanup;}
    first=wiring(p);
    /* Duplicate lock touches the SAME one page, never a second physical range. */
    if(mlock(p,(size_t)page)) {lock_errno=errno;code=5;goto cleanup;}
    second=wiring(p);
    if(munlock(p,(size_t)page)) {lock_errno=errno;code=6;goto cleanup;}
    unlocked=wiring(p);
    for(long i=0;i<page;i++)if(p[i]!=0x5a){code=7;break;}
cleanup:
    /* Best effort explicit unlock; munmap is authoritative lifetime cleanup even
       if a syscall failed. No touching replacement or another mapping. */
    (void)munlock(p,(size_t)page);
    final_wire=wiring(p);
    if(munmap(p,(size_t)page))code=8;else removed=1;
    printf("{\"page_bytes\":%ld,\"memlock_soft\":%llu,\"memlock_hard\":%llu,\"wire_before\":%d,\"wire_after_first\":%d,\"wire_after_duplicate\":%d,\"wire_after_one_unlock\":%d,\"host_errno\":%d,\"exit\":%d,\"actual_mapped_and_max_locked_bytes\":16384,\"wire_after_cleanup_unlock\":%d,\"mapping_removed\":%s}\n",page,(unsigned long long)limit.rlim_cur,(unsigned long long)limit.rlim_max,before,first,second,unlocked,lock_errno,code,final_wire,removed?"true":"false");
    return code;
}
