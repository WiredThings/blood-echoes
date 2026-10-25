/* SPDX-License-Identifier: GPL-2.0-or-later
 * Bounded guest user-wiring ledger. Does NOT validate VMA ownership: callers
 * must retain a mapping-lifetime lease through every operation. Lock ordering:
 * VMA write/read lease OR published-native State read lease -> ledger -> syscall.
 * Never acquire either mapping lease from a backend callback or ledger owner.
 * Guest duplicate locks are a set, not Darwin's nested wire reference count.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#define BB_MLOCK_PAGE UINT64_C(16384)
#define BB_MLOCK_CALL_HARD (UINT64_C(16)<<20)
#define BB_MLOCK_TOTAL_HARD (UINT64_C(64)<<20)
#define BB_MLOCK_SLOTS 4096u
/* This private result is never a guest errno. Caller must fail-stop with the
 * existing HLE-fault exit contract, not return success or restart code 75/76. */
#define BB_MLOCK_POISONED (-1)
typedef int (*BbMlockHostOp)(void*,uintptr_t,uint64_t,int);
typedef int (*BbMlockHostSync)(void*,uintptr_t,uint64_t,int);
typedef struct {uintptr_t address;unsigned char used;} BbMlockPage;
typedef struct {
    pthread_mutex_t gate;
    BbMlockPage pages[BB_MLOCK_SLOTS];
    uint64_t call_limit,total_limit,high_water,calls,unlocks,duplicate_pages,failures;
    unsigned page_limit,count;int policy_ready,policy_error,poisoned;
} BbMlockLedger;
#define BB_MLOCK_LEDGER_INIT { .gate=PTHREAD_MUTEX_INITIALIZER }
/* Host callbacks return 0 or a positive host errno. On failure their own page
 * status must be unchanged, matching the single-range host syscall contract. */
static inline int bb_mlock_normalize(uintptr_t address,uint64_t size,uintptr_t*start,uint64_t*bytes) {
    const uint64_t limit=UINT64_C(1)<<40;
    if(address>=limit||size>limit-address)return EINVAL;
    const uint64_t offset=address&(BB_MLOCK_PAGE-1);
    *start=address-offset;*bytes=(size+offset+BB_MLOCK_PAGE-1)&~(BB_MLOCK_PAGE-1);
    if(*bytes>limit-*start)return EINVAL;
    return 0;
}
static inline int bb_mlock_env_number(const char*name,uint64_t maximum,uint64_t fallback,uint64_t*out) {
    const char*text=getenv(name);*out=fallback;if(!text)return 0;
    if(!*text)return EINVAL;
    uint64_t n=0;for(const unsigned char*p=(const unsigned char*)text;*p;++p){
        if(*p<'0'||*p>'9'||n>(maximum-(*p-'0'))/10)return EINVAL;
        n=n*10+(*p-'0');
    }
    if(!n||n>maximum)return EINVAL;*out=n;return 0;
}
static inline int bb_mlock_policy_locked(BbMlockLedger*l) {
    if(l->policy_ready)return l->policy_error;
    uint64_t call=16,total=64,pages=4096;
    int e=bb_mlock_env_number("BB_MLOCK_MAX_CALL_MIB",16,16,&call);
    if(!e)e=bb_mlock_env_number("BB_MLOCK_MAX_TOTAL_MIB",64,64,&total);
    if(!e)e=bb_mlock_env_number("BB_MLOCK_MAX_PAGES",4096,4096,&pages);
    l->call_limit=call<<20;l->total_limit=total<<20;l->page_limit=(unsigned)pages;
    l->policy_error=e;l->policy_ready=1;return e;
}
static inline int bb_mlock_find_locked(const BbMlockLedger*l,uintptr_t address) {
    for(unsigned i=0;i<BB_MLOCK_SLOTS;++i)if(l->pages[i].used&&l->pages[i].address==address)return(int)i;
    return -1;
}
static inline unsigned bb_mlock_empty_locked(const BbMlockLedger*l) {
    for(unsigned i=0;i<BB_MLOCK_SLOTS;++i)if(!l->pages[i].used)return i;
    return BB_MLOCK_SLOTS;
}
static inline void bb_mlock_add_locked(BbMlockLedger*l,unsigned slot,uintptr_t address) {
    l->pages[slot]=(BbMlockPage){address,1};++l->count;
    const uint64_t wired=(uint64_t)l->count*BB_MLOCK_PAGE;if(wired>l->high_water)l->high_water=wired;
}
static inline void bb_mlock_remove_locked(BbMlockLedger*l,unsigned slot) {
    l->pages[slot].used=0;--l->count;
}
/* Lock requests are at most 1024 pages. Unlock scans at most 4096 existing
 * slots even for a huge span; rollback scratch is bounded to 8 KiB. No malloc
 * or guest writes. */
static inline int bb_mlock_apply(BbMlockLedger*l,uintptr_t start,uint64_t bytes,int unlock,BbMlockHostOp host,void*ctx) {
    unsigned short changed[BB_MLOCK_SLOTS];unsigned changed_count=0;
    pthread_mutex_lock(&l->gate);++l->calls;if(unlock)++l->unlocks;
    int e=bb_mlock_policy_locked(l);
    if(l->poisoned){pthread_mutex_unlock(&l->gate);return BB_MLOCK_POISONED;}
    if(!e&&((start&(BB_MLOCK_PAGE-1))||(bytes&(BB_MLOCK_PAGE-1))))e=EINVAL;
    if(!e&&!unlock&&bytes>l->call_limit)e=ENOMEM;
    if(!e&&(start>=(UINT64_C(1)<<40)||bytes>(UINT64_C(1)<<40)-start))e=EINVAL;
    unsigned new_count=0;
    if(!e&&!unlock){
        for(uint64_t n=0;n<bytes;n+=BB_MLOCK_PAGE){
            if(bb_mlock_find_locked(l,start+n)<0)++new_count;else ++l->duplicate_pages;
        }
        if(l->count>l->page_limit||new_count>l->page_limit-l->count||(uint64_t)(l->count+new_count)*BB_MLOCK_PAGE>l->total_limit)e=ENOMEM;
    }
    if(!e&&unlock){
        /* Releasing a broad range is bounded by existing entries, not span.
         * A caller can release all accumulated locks in one operation. */
        for(unsigned slot=0;slot<BB_MLOCK_SLOTS;++slot){
            if(!l->pages[slot].used||l->pages[slot].address<start||l->pages[slot].address-start>=bytes)continue;
            e=host(ctx,l->pages[slot].address,BB_MLOCK_PAGE,1);if(e)break;
            changed[changed_count++]=(unsigned short)slot;bb_mlock_remove_locked(l,slot);
        }
    }else if(!e){
        for(uint64_t n=0;n<bytes;n+=BB_MLOCK_PAGE){
            const uintptr_t page=start+n;if(bb_mlock_find_locked(l,page)>=0)continue;
            const unsigned slot=bb_mlock_empty_locked(l);if(slot>=BB_MLOCK_SLOTS){e=ENOMEM;break;}
            e=host(ctx,page,BB_MLOCK_PAGE,0);if(e)break;
            changed[changed_count++]=(unsigned short)slot;bb_mlock_add_locked(l,slot,page);
        }
    }
    if(e){
        ++l->failures;
        while(changed_count){
            const unsigned slot=changed[--changed_count];const uintptr_t page=l->pages[slot].address;
            const int restored=host(ctx,page,BB_MLOCK_PAGE,!unlock);
            if(restored)l->poisoned=1;
            else if(unlock)bb_mlock_add_locked(l,slot,page);else bb_mlock_remove_locked(l,slot);
        }
    }
    if(l->poisoned)e=BB_MLOCK_POISONED;
    pthread_mutex_unlock(&l->gate);return e;
}
/* Call only after each actual successful munmap/MAP_FIXED suboperation, with
 * exclusive mapping ownership. This is not an explicit unlock: the replaced
 * mapping has already implicitly dropped its host wiring. Subsequent failure
 * of a multi-chunk operation must not resurrect these old-generation entries. */
static inline void bb_mlock_forget_committed(BbMlockLedger*l,uintptr_t start,uint64_t bytes) {
    pthread_mutex_lock(&l->gate);
    if(!l->count){pthread_mutex_unlock(&l->gate);return;}
    if(bytes&&start<=UINTPTR_MAX-bytes)for(unsigned i=0;i<BB_MLOCK_SLOTS;++i)
        if(l->pages[i].used&&l->pages[i].address>=start&&l->pages[i].address-start<bytes)bb_mlock_remove_locked(l,i);
    pthread_mutex_unlock(&l->gate);
}
/* Mapping lease + ledger lease stay held through msync so a concurrent Mlock
 * cannot enter between guest MS_INVALIDATE's wired check and the host syscall. */
static inline int bb_mlock_sync(BbMlockLedger*l,uintptr_t start,uint64_t bytes,int flags,int invalidate,BbMlockHostSync host,void*ctx) {
    pthread_mutex_lock(&l->gate);int e=l->poisoned?BB_MLOCK_POISONED:0;
    if(!e&&l->count&&invalidate&&bytes)for(unsigned i=0;i<BB_MLOCK_SLOTS;++i)
        if(l->pages[i].used&&l->pages[i].address>=start&&l->pages[i].address-start<bytes){e=EBUSY;break;}
    if(!e)e=host(ctx,start,bytes,flags);
    pthread_mutex_unlock(&l->gate);return e;
}
