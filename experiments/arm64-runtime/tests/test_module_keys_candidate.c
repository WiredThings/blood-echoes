/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored native control for the actual candidate C key implementation.
 * The callback trampoline/loader ownership are mocks here; the separate FEX
 * fixture must validate actual guest execution and guest-address admission. */
#define BB_LOW_HEAP_IMPL 1
#include "runtime_kernel.c"
#include <assert.h>
static _Thread_local int32_t error_slot;
int32_t *runtime_errno(void){return &error_slot;}
static unsigned calls_a,calls_b,preserve_seen;static uint32_t selected,keep_key;
static pthread_mutex_t test_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t test_event=PTHREAD_COND_INITIALIZER;
static int entered,release_callback,hold_callback,reenter_status,finalizer_done;
static void callback_a(void *value){
 assert(value==(void*)0x11);++calls_a;
 if(hold_callback){pthread_mutex_lock(&test_lock);entered=1;pthread_cond_broadcast(&test_event);while(!release_callback)pthread_cond_wait(&test_event,&test_lock);pthread_mutex_unlock(&test_lock);}
}
static void callback_b(void *value){assert(value==(void*)0x22);++calls_b;if(calls_b<6)assert(key_set(selected,value)==0);}
static void callback_preserve(void *value){assert(value==(void*)0x55);assert(key_get(keep_key)==(void*)0x42);++preserve_seen;}
static void callback_reentrant(void *value){assert(value==(void*)0x11);reenter_status=runtime_pthread_cxa_finalize_module(1);}
int bb_arm_key_destructor_owner(uintptr_t at){return at==(uintptr_t)callback_a||at==(uintptr_t)callback_reentrant?1:at==(uintptr_t)callback_b||at==(uintptr_t)callback_preserve?2:0;}
uint64_t bb_arm_call(uintptr_t at,const uint64_t *args,size_t count){assert(count==1&&bb_arm_key_destructor_owner(at));((void(*)(void*))at)((void*)(uintptr_t)args[0]);return 0;}
static void *cleanup_thread(void *unused){(void)unused;assert(key_set(selected,(void*)0x11)==0);runtime_thread_keys_cleanup();return NULL;}
static void *finalize_thread(void *unused){(void)unused;assert(runtime_pthread_cxa_finalize_module(1)==0);pthread_mutex_lock(&test_lock);finalizer_done=1;pthread_cond_broadcast(&test_event);pthread_mutex_unlock(&test_lock);return NULL;}
int main(void){
 uint32_t a,b,sentinel=0xdead;assert(key_create(&a,callback_a)==0);assert(key_set(a,(void*)0x11)==0);assert(key_delete(a)==0);assert(key_create(&b,callback_b)==0&&a==b&&key_get(b)==NULL);assert(key_delete(b)==0);
 assert(key_create(&sentinel,(KeyDestructor)(uintptr_t)0x1234)==ERR(22)&&sentinel==0xdead);
 assert(key_create(&a,callback_a)==0&&key_create(&b,callback_b)==0);assert(key_set(a,(void*)0x11)==0&&key_set(b,(void*)0x22)==0);
 uint64_t generation=keys[a].generation;assert(runtime_pthread_cxa_finalize_module(1)==0);assert(keys[a].used&&keys[a].generation==generation&&!keys[a].destructor&&key_get(a)==(void*)0x11);assert(keys[b].destructor==callback_b&&calls_a==0&&calls_b==0);assert(runtime_pthread_cxa_finalize_module(1)==0);assert(key_delete(a)==0);
 selected=b;runtime_thread_keys_cleanup();assert(calls_a==0&&calls_b==4&&key_get(b)==NULL);assert(key_delete(b)==0);
 assert(runtime_pthread_cxa_finalize_module(0)==EINVAL&&runtime_pthread_cxa_finalize_module(10)==EINVAL);
 assert(key_create(&a,callback_reentrant)==0);assert(key_set(a,(void*)0x11)==0);runtime_thread_keys_cleanup();assert(reenter_status==EDEADLK&&!key_modules[0].closing&&!key_modules[0].in_flight);assert(key_delete(a)==0);
 assert(key_create(&selected,callback_a)==0);hold_callback=1;pthread_t worker,finalizer;assert(!pthread_create(&worker,NULL,cleanup_thread,NULL));
 pthread_mutex_lock(&test_lock);while(!entered)pthread_cond_wait(&test_event,&test_lock);pthread_mutex_unlock(&test_lock);
 assert(!pthread_create(&finalizer,NULL,finalize_thread,NULL));
 for(unsigned i=0;i<10000;++i){pthread_mutex_lock(&key_lock);int closing=key_modules[0].closing;pthread_mutex_unlock(&key_lock);if(closing)break;assert(i<9999);usleep(1000);}
 pthread_mutex_lock(&test_lock);assert(!finalizer_done);pthread_mutex_unlock(&test_lock);
 sentinel=0xdead;assert(key_create(&sentinel,callback_a)==ERR(16)&&sentinel==0xdead);
 pthread_mutex_lock(&test_lock);release_callback=1;pthread_cond_broadcast(&test_event);pthread_mutex_unlock(&test_lock);
 assert(!pthread_join(worker,NULL)&&!pthread_join(finalizer,NULL));assert(finalizer_done&&!key_modules[0].in_flight&&!key_modules[0].closing&&keys[selected].used&&!keys[selected].destructor);assert(key_delete(selected)==0);
 assert(key_create(&keep_key,NULL)==0&&key_create(&b,callback_preserve)==0&&keep_key<b);assert(key_set(keep_key,(void*)0x42)==0&&key_set(b,(void*)0x55)==0);runtime_thread_keys_cleanup();assert(preserve_seen==1&&key_get(keep_key)==NULL&&key_get(b)==NULL);assert(key_delete(keep_key)==0&&key_delete(b)==0);
 assert(key_create(&a,NULL)==0);assert(key_set(a,(void*)0x42)==0);runtime_thread_keys_cleanup();assert(key_get(a)==NULL);assert(key_delete(a)==0);
 puts("candidate actual C key controls: ownership rejection, ABA, module-only retirement, four rounds, recursion and real in-flight wait passed");
}
