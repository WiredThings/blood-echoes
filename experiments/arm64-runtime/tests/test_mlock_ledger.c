/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored deterministic host backend: no physical page is wired by this test.
 * Separate native_one_page.c is the explicitly bounded actual syscall control.
 */
#include "../vendor/bb_runtime/src/bounded_mlock.h"
#include <assert.h>
#include <stdio.h>
#include <stdatomic.h>
#include <unistd.h>
#define BASE UINT64_C(0x7000000000)
typedef struct {
    uintptr_t pinned[32];unsigned count,lock_calls,unlock_calls,sync_calls;
    uintptr_t fail_lock,fail_unlock;int lock_error,unlock_error;
    atomic_int pause,entered,released;
} Backend;
static int find(Backend*b,uintptr_t a){for(unsigned i=0;i<b->count;++i)if(b->pinned[i]==a)return(int)i;return-1;}
static int host(void*ctx,uintptr_t a,uint64_t size,int unlock){
    Backend*b=ctx;assert(size==BB_MLOCK_PAGE);assert(!(a&(BB_MLOCK_PAGE-1)));
    if(unlock){++b->unlock_calls;if(a==b->fail_unlock)return b->unlock_error?b->unlock_error:ENOMEM;int i=find(b,a);assert(i>=0);b->pinned[i]=b->pinned[--b->count];}
    else {++b->lock_calls;if(atomic_load(&b->pause)){atomic_store(&b->entered,1);while(!atomic_load(&b->released))usleep(100);}
        if(a==b->fail_lock)return b->lock_error?b->lock_error:EAGAIN;assert(find(b,a)<0&&b->count<32);b->pinned[b->count++]=a;}
    return 0;
}
static int host_sync(void*ctx,uintptr_t a,uint64_t size,int flags){Backend*b=ctx;(void)a;(void)size;(void)flags;++b->sync_calls;return 0;}
static void init(BbMlockLedger*l,Backend*b){
    memset(l,0,sizeof(*l));assert(!pthread_mutex_init(&l->gate,NULL));memset(b,0,sizeof(*b));
    l->policy_ready=1;l->call_limit=4*BB_MLOCK_PAGE;l->total_limit=6*BB_MLOCK_PAGE;l->page_limit=6;
}
static void finish(BbMlockLedger*l){assert(!pthread_mutex_destroy(&l->gate));}
static int apply(BbMlockLedger*l,Backend*b,uintptr_t a,uint64_t n,int unlock){return bb_mlock_apply(l,a,n,unlock,host,b);}
static void normalized(void){
    uintptr_t a;uint64_t n;assert(!bb_mlock_normalize(BASE+1,BB_MLOCK_PAGE,&a,&n)&&a==BASE&&n==2*BB_MLOCK_PAGE);
    assert(!bb_mlock_normalize(BASE,0,&a,&n)&&n==0);assert(!bb_mlock_normalize(BASE+1,0,&a,&n)&&n==BB_MLOCK_PAGE);
    assert(bb_mlock_normalize(UINT64_C(1)<<40,0,&a,&n)==EINVAL);assert(bb_mlock_normalize((UINT64_C(1)<<40)-1,2,&a,&n)==EINVAL);
    assert(!bb_mlock_normalize((UINT64_C(1)<<40)-BB_MLOCK_PAGE,BB_MLOCK_PAGE,&a,&n)&&n==BB_MLOCK_PAGE);
}
static void policy(void){
    const char*names[]={"BB_MLOCK_MAX_CALL_MIB","BB_MLOCK_MAX_TOTAL_MIB","BB_MLOCK_MAX_PAGES"};
    char*saved[3];for(unsigned i=0;i<3;++i){const char*s=getenv(names[i]);saved[i]=s?strdup(s):NULL;assert(!unsetenv(names[i]));}
    BbMlockLedger*l=calloc(1,sizeof(*l));assert(l&&!pthread_mutex_init(&l->gate,NULL));assert(!bb_mlock_policy_locked(l));assert(l->call_limit==BB_MLOCK_CALL_HARD&&l->total_limit==BB_MLOCK_TOTAL_HARD&&l->page_limit==4096);
    const char*bad[]={"", "0", "-1", "+1", "1x", "9999999999999999999999999", "17"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i){l->policy_ready=0;assert(!setenv(names[0],bad[i],1));assert(bb_mlock_policy_locked(l)==EINVAL);}
    assert(!setenv(names[0],"1",1)&&!setenv(names[1],"2",1)&&!setenv(names[2],"16",1));l->policy_ready=0;assert(!bb_mlock_policy_locked(l)&&l->call_limit==(UINT64_C(1)<<20)&&l->total_limit==(UINT64_C(2)<<20)&&l->page_limit==16);
    finish(l);free(l);for(unsigned i=0;i<3;++i){if(saved[i]){assert(!setenv(names[i],saved[i],1));free(saved[i]);}else assert(!unsetenv(names[i]));}
}
static void duplicate_overlap(void){
    BbMlockLedger*l=calloc(1,sizeof(*l));Backend b;assert(l);init(l,&b);
    assert(!apply(l,&b,BASE,2*BB_MLOCK_PAGE,0)&&l->count==2);assert(!apply(l,&b,BASE,2*BB_MLOCK_PAGE,0)&&b.lock_calls==2);
    assert(!apply(l,&b,BASE+BB_MLOCK_PAGE,2*BB_MLOCK_PAGE,0)&&l->count==3&&b.lock_calls==3);
    assert(!apply(l,&b,BASE+BB_MLOCK_PAGE,BB_MLOCK_PAGE,1)&&l->count==2&&b.unlock_calls==1);
    assert(!apply(l,&b,BASE+BB_MLOCK_PAGE,BB_MLOCK_PAGE,1)&&b.unlock_calls==1);assert(l->duplicate_pages==3);
    assert(!apply(l,&b,BASE,0,0)&&b.lock_calls==3);finish(l);free(l);
}
static void limits(void){
    BbMlockLedger*l=calloc(1,sizeof(*l));Backend b;assert(l);init(l,&b);
    assert(apply(l,&b,BASE,5*BB_MLOCK_PAGE,0)==ENOMEM&&!b.lock_calls);assert(apply(l,&b,BASE+1,BB_MLOCK_PAGE,0)==EINVAL&&!b.lock_calls);
    assert(apply(l,&b,UINT64_MAX&~(BB_MLOCK_PAGE-1),BB_MLOCK_PAGE,0)==EINVAL&&!b.lock_calls);
    assert(!apply(l,&b,BASE,4*BB_MLOCK_PAGE,0)&&!apply(l,&b,BASE+2*BB_MLOCK_PAGE,4*BB_MLOCK_PAGE,0)&&l->count==6);
    unsigned before=b.lock_calls;assert(apply(l,&b,BASE+6*BB_MLOCK_PAGE,BB_MLOCK_PAGE,0)==ENOMEM&&b.lock_calls==before);assert(l->high_water==6*BB_MLOCK_PAGE);
    assert(!apply(l,&b,BASE,8*BB_MLOCK_PAGE,1)&&!l->count&&!b.count);
    finish(l);free(l);
    l=calloc(1,sizeof(*l));assert(l);init(l,&b);l->page_limit=1;assert(apply(l,&b,BASE,2*BB_MLOCK_PAGE,0)==ENOMEM&&!b.lock_calls);finish(l);free(l);
}
static void rollback(void){
    BbMlockLedger*l=calloc(1,sizeof(*l));Backend b;assert(l);init(l,&b);
    assert(!apply(l,&b,BASE,BB_MLOCK_PAGE,0));b.fail_lock=BASE+2*BB_MLOCK_PAGE;
    assert(apply(l,&b,BASE,3*BB_MLOCK_PAGE,0)==EAGAIN&&l->count==1&&b.count==1&&find(&b,BASE)>=0&&!l->poisoned);
    b.fail_lock=0;assert(!apply(l,&b,BASE+BB_MLOCK_PAGE,2*BB_MLOCK_PAGE,0));b.fail_unlock=BASE+BB_MLOCK_PAGE;
    assert(apply(l,&b,BASE,3*BB_MLOCK_PAGE,1)==ENOMEM&&l->count==3&&b.count==3&&!l->poisoned);
    finish(l);free(l);
    l=calloc(1,sizeof(*l));assert(l);init(l,&b);b.fail_lock=BASE+BB_MLOCK_PAGE;b.fail_unlock=BASE;
    assert(apply(l,&b,BASE,2*BB_MLOCK_PAGE,0)==BB_MLOCK_POISONED&&l->count==1&&b.count==1&&l->poisoned);unsigned before=b.lock_calls;
    assert(apply(l,&b,BASE,BB_MLOCK_PAGE,0)==BB_MLOCK_POISONED&&b.lock_calls==before);finish(l);free(l);
    l=calloc(1,sizeof(*l));assert(l);init(l,&b);assert(!apply(l,&b,BASE,2*BB_MLOCK_PAGE,0));b.fail_unlock=BASE+BB_MLOCK_PAGE;b.fail_lock=BASE;
    assert(apply(l,&b,BASE,2*BB_MLOCK_PAGE,1)==BB_MLOCK_POISONED&&l->count==1&&b.count==1&&find(&b,BASE)<0);finish(l);free(l);
}
static void retire_and_sync(void){
    BbMlockLedger*l=calloc(1,sizeof(*l));Backend b;assert(l);init(l,&b);assert(!apply(l,&b,BASE,2*BB_MLOCK_PAGE,0));
    assert(bb_mlock_sync(l,BASE,2*BB_MLOCK_PAGE,2,1,host_sync,&b)==EBUSY&&!b.sync_calls);
    assert(!bb_mlock_sync(l,BASE,2*BB_MLOCK_PAGE,0,0,host_sync,&b)&&b.sync_calls==1);
    /* Successful first MAP_FIXED chunk implicitly unwired only that page. */
    int i=find(&b,BASE);b.pinned[i]=b.pinned[--b.count];bb_mlock_forget_committed(l,BASE,BB_MLOCK_PAGE);assert(l->count==1&&!b.unlock_calls);
    /* A later chunk failure must not retire its untouched old mapping. */
    assert(bb_mlock_sync(l,BASE+BB_MLOCK_PAGE,BB_MLOCK_PAGE,2,1,host_sync,&b)==EBUSY&&l->count==1);
    assert(!apply(l,&b,BASE,BB_MLOCK_PAGE,0)&&b.lock_calls==3&&l->count==2);assert(!apply(l,&b,BASE,2*BB_MLOCK_PAGE,1)&&!l->count);
    assert(!bb_mlock_sync(l,BASE,2*BB_MLOCK_PAGE,2,1,host_sync,&b)&&b.sync_calls==2);finish(l);free(l);
}
typedef struct {BbMlockLedger*l;Backend*b;} Job;
static void*run(void*ctx){Job*j=ctx;assert(!apply(j->l,j->b,BASE,BB_MLOCK_PAGE,0));return NULL;}
static void concurrent(void){
    BbMlockLedger*l=calloc(1,sizeof(*l));Backend b;assert(l);init(l,&b);atomic_store(&b.pause,1);Job j={l,&b};pthread_t one,two;
    assert(!pthread_create(&one,NULL,run,&j));while(!atomic_load(&b.entered))usleep(100);assert(!pthread_create(&two,NULL,run,&j));usleep(20000);atomic_store(&b.released,1);
    assert(!pthread_join(one,NULL)&&!pthread_join(two,NULL));assert(b.lock_calls==1&&l->count==1);finish(l);free(l);
}
int main(void){normalized();policy();duplicate_overlap();limits();rollback();retire_and_sync();concurrent();puts("PASS bounded wiring normalization/policy/set semantics/limits/rollback/fail-stop/partial retire+reuse/MS_INVALIDATE/concurrency controls; zero physical pages pinned");}
