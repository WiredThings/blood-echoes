/* SPDX-License-Identifier: GPL-2.0-or-later
 * Independent actual Darwin control of the REAL bounded_mlock.h implementation.
 * At most the SAME one 16 KiB VA is ever wired. No game, graphics or FEX.
 * Do not replace the ledger with a model or stub the host syscalls here.
 */
#include <sys/mman.h>
#include <unistd.h>
#include <stdio.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include "../vendor/bb_runtime/src/bounded_mlock.h"

typedef struct {uintptr_t address;unsigned calls,unlocks;int mapped;} OnePage;
/* This is an independent kernel query. It does not consult the wiring ledger. */
static int host_wire_count(uintptr_t target){
    mach_vm_address_t at=target;mach_vm_size_t bytes=0;mach_port_t object=MACH_PORT_NULL;
    vm_region_basic_info_data_64_t info={0};mach_msg_type_number_t count=VM_REGION_BASIC_INFO_COUNT_64;
    const kern_return_t result=mach_vm_region(mach_task_self(),&at,&bytes,VM_REGION_BASIC_INFO_64,(vm_region_info_t)&info,&count,&object);
    if(object!=MACH_PORT_NULL)mach_port_deallocate(mach_task_self(),object);
    if(result!=KERN_SUCCESS||at>target||target-at>=bytes)return -1;
    return info.user_wired_count;
}
static int real_host_op(void*context,uintptr_t at,uint64_t bytes,int unlock){
    OnePage*owned=context;
    /* Fail closed before any syscall if the test accidentally expands scope. */
    if(!owned->mapped||at!=owned->address||bytes!=16384)return EINVAL;
    if(unlock)++owned->unlocks;else ++owned->calls;
    const int result=unlock?munlock((void*)at,(size_t)bytes):mlock((void*)at,(size_t)bytes);
    return result?errno:0;
}
#define REQUIRE(condition) do{if(!(condition)){failed_line=__LINE__;goto cleanup;}}while(0)
int main(void){
    BbMlockLedger ledger=BB_MLOCK_LEDGER_INIT;OnePage owned={0};
    int failed_line=0,error=0,removed=0,cleanup_wire=-1;
    int before=-2,first=-2,duplicate=-2,overlap=-2,unlocked=-2;
    int before_replace=-2,after_replace=-2,reused=-2,reused_overlap=-2,final_unlock=-2;
    uintptr_t rounded=0;uint64_t span=0;void*mapped=MAP_FAILED;
    REQUIRE(sysconf(_SC_PAGESIZE)==16384);
    mapped=mmap(NULL,16384,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    REQUIRE(mapped!=MAP_FAILED);owned=(OnePage){.address=(uintptr_t)mapped,.mapped=1};
    REQUIRE(!(owned.address&16383));memset(mapped,0x5a,16384);
    before=host_wire_count(owned.address);REQUIRE(before==0);
    error=bb_mlock_apply(&ledger,owned.address,16384,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==1);
    first=host_wire_count(owned.address);REQUIRE(first==1);
    error=bb_mlock_apply(&ledger,owned.address,16384,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==1);
    duplicate=host_wire_count(owned.address);REQUIRE(duplicate==1);
    REQUIRE(!bb_mlock_normalize(owned.address+1,8192,&rounded,&span));
    REQUIRE(rounded==owned.address&&span==16384);
    error=bb_mlock_apply(&ledger,rounded,span,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==1);
    overlap=host_wire_count(owned.address);REQUIRE(overlap==1);
    error=bb_mlock_apply(&ledger,owned.address,16384,1,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==0&&owned.unlocks==1);
    unlocked=host_wire_count(owned.address);REQUIRE(unlocked==0);
    error=bb_mlock_apply(&ledger,owned.address,16384,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==2);
    before_replace=host_wire_count(owned.address);REQUIRE(before_replace==1);
    /* Actual replacement is confined to the one mapping this child owns.
     * Inspect the kernel BEFORE the ledger forget, proving that mmap itself
     * removed the old wiring rather than trusting a cleared software count. */
    REQUIRE(mmap(mapped,16384,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED,-1,0)==mapped);
    after_replace=host_wire_count(owned.address);REQUIRE(after_replace==0&&ledger.count==1);
    for(unsigned i=0;i<16384;++i)REQUIRE(((unsigned char*)mapped)[i]==0);
    bb_mlock_forget_committed(&ledger,owned.address,16384);REQUIRE(ledger.count==0);
    memset(mapped,0xa5,16384);
    error=bb_mlock_apply(&ledger,owned.address,16384,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==3);
    reused=host_wire_count(owned.address);REQUIRE(reused==1);
    REQUIRE(!bb_mlock_normalize(owned.address+16383,1,&rounded,&span));
    REQUIRE(rounded==owned.address&&span==16384);
    error=bb_mlock_apply(&ledger,rounded,span,0,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==1&&owned.calls==3);
    reused_overlap=host_wire_count(owned.address);REQUIRE(reused_overlap==1);
    error=bb_mlock_apply(&ledger,owned.address,16384,1,real_host_op,&owned);
    REQUIRE(!error&&ledger.count==0&&owned.unlocks==2);
    final_unlock=host_wire_count(owned.address);REQUIRE(final_unlock==0);
    for(unsigned i=0;i<16384;++i)REQUIRE(((unsigned char*)mapped)[i]==0xa5);
cleanup:
    if(owned.mapped){
        /* No assert/abort after real wiring: always try exact-page cleanup.
         * Four iterations also bound cleanup if a broken implementation
         * accidentally produced the three duplicate host calls above. */
        cleanup_wire=host_wire_count(owned.address);
        for(unsigned attempts=0;attempts<4&&cleanup_wire>0;++attempts){
            if(munlock(mapped,16384)){if(!failed_line)failed_line=__LINE__;break;}
            cleanup_wire=host_wire_count(owned.address);
        }
        if(cleanup_wire!=0&&!failed_line)failed_line=__LINE__;
        if(!munmap(mapped,16384)){removed=1;owned.mapped=0;bb_mlock_forget_committed(&ledger,owned.address,16384);}
        else if(!failed_line)failed_line=__LINE__;
    }
    printf("{\"source\":\"real bounded_mlock.h and Darwin syscalls\",\"maximum_locked_mapping_bytes\":16384,\"wire_before\":%d,\"wire_first\":%d,\"wire_duplicate\":%d,\"wire_overlap\":%d,\"wire_unlocked\":%d,\"wire_before_MAP_FIXED\":%d,\"wire_after_MAP_FIXED_before_ledger_forget\":%d,\"wire_reused\":%d,\"wire_reused_overlap\":%d,\"wire_final_unlock\":%d,\"wire_cleanup\":%d,\"host_lock_calls\":%u,\"host_unlock_calls\":%u,\"ledger_current_pages\":%u,\"ledger_peak_bytes\":%llu,\"last_host_errno\":%d,\"mapping_removed\":%s,\"failed_line\":%d,\"passed\":%s}\n",
        before,first,duplicate,overlap,unlocked,before_replace,after_replace,reused,reused_overlap,final_unlock,cleanup_wire,owned.calls,owned.unlocks,ledger.count,(unsigned long long)ledger.high_water,error,removed?"true":"false",failed_line,failed_line?"false":"true");
    pthread_mutex_destroy(&ledger.gate);return failed_line?1:0;
}
