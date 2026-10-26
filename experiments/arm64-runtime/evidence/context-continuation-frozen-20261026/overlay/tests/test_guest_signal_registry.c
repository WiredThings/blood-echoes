/* SPDX-License-Identifier: MIT
 * Actual runtime C state, native accessor test. No guest handler is executed.
 */
#include "../vendor/bb_runtime/src/runtime_kernel.c"
#include <assert.h>
static _Thread_local int32_t authored_errno;
int32_t *runtime_errno(void){return &authored_errno;}
static void *other_thread(void *unused){
 (void)unused;uintptr_t selected=0;uint32_t mask[4]={1,2,3,4};
 assert(runtime_guest_signal_snapshot(30,&selected,mask)==0&&selected==0x12345678);
 assert(mask[0]==0&&mask[1]==0&&mask[2]==0&&mask[3]==0);
 uint32_t changed[4]={5,6,7,8};assert(runtime_guest_signal_restore_mask(changed)==0);
 assert(runtime_guest_signal_snapshot(30,&selected,mask)==0&&memcmp(mask,changed,sizeof(mask))==0);
 return NULL;
}
int main(void){
 uintptr_t selected=0xfeed;uint32_t mask[4]={1,2,3,4},original[4]={0x100,0x200,0x400,0x800};
 assert(runtime_guest_signal_snapshot(0,&selected,mask)==EINVAL&&selected==0xfeed&&mask[0]==1);
 assert(runtime_guest_signal_snapshot(128,&selected,mask)==EINVAL);
 assert(runtime_guest_signal_snapshot(30,NULL,mask)==EINVAL);
 assert(runtime_guest_signal_restore_mask(NULL)==EINVAL);
 assert(guest_signal(30,0x12345678)==0);
 assert(guest_sigprocmask(3,(const GuestSigset*)original,NULL)==0);
 assert(runtime_guest_signal_snapshot(30,&selected,mask)==0&&selected==0x12345678&&memcmp(mask,original,sizeof(mask))==0);
 pthread_t thread;assert(pthread_create(&thread,NULL,other_thread,NULL)==0&&pthread_join(thread,NULL)==0);
 assert(runtime_guest_signal_snapshot(30,&selected,mask)==0&&memcmp(mask,original,sizeof(mask))==0);
 uint32_t restored[4]={0x11,0x22,0x44,0x88};assert(runtime_guest_signal_restore_mask(restored)==0);
 GuestSigset got;assert(guest_sigprocmask(0,NULL,&got)==0&&memcmp(got.bits,restored,sizeof(restored))==0);
 assert(guest_signal(30,1)==0x12345678);assert(runtime_guest_signal_snapshot(30,&selected,mask)==0&&selected==1);
 puts("{\"runtime_registry\":true,\"thread_local_mask\":true,\"second_registry\":false,\"host_signal_forwarded\":false}");return 0;
}
