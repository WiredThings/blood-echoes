/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored native ordering control; actual candidate runtime.c is included. */
#define BB_LOW_HEAP_IMPL 1
#include "runtime.c"
#include <assert.h>
static unsigned log_items[32],n,module_calls;
static void record(unsigned item){assert(n<32);log_items[n++]=item;}
static void plain(void){record(1);}
static void a(void *arg){assert(arg==(void*)0x1234);record(2);}
static void b(void *arg){assert(arg==(void*)0x4567);record(3);}
static void unknown(void *arg){assert(arg==(void*)0x89ab);record(4);}
static void recursive(void *arg){assert(arg==(void*)0xbeef);record(5);assert(!guest_atexit(plain));runtime_finalize(NULL);}
uint64_t bb_arm_call(uintptr_t fn,const uint64_t *args,size_t count){if(count){assert(count==1);((void(*)(void*))fn)((void*)(uintptr_t)args[0]);}else ((void(*)(void))fn)();return 0;}
int bb_arm_finalize_module_from_dso(uintptr_t dso){assert(dso);++module_calls;record(dso==0x111?10:dso==0x222?20:30);return 0;}
int main(void){
 assert(!guest_atexit(plain));assert(!guest_cxa_atexit(a,(void*)0x1234,(void*)0x111));assert(!guest_cxa_atexit(b,(void*)0x4567,(void*)0x222));
 runtime_finalize((void*)0x111);assert(n==2&&log_items[0]==2&&log_items[1]==10&&module_calls==1);
 runtime_finalize((void*)0x111);assert(n==3&&log_items[2]==10&&module_calls==2);
 runtime_finalize(NULL);assert(n==5&&log_items[3]==3&&log_items[4]==1&&module_calls==2);runtime_finalize(NULL);assert(n==5&&module_calls==2);
 assert(!guest_cxa_atexit(unknown,(void*)0x89ab,(void*)0x333));runtime_finalize((void*)0x333);assert(n==7&&log_items[5]==4&&log_items[6]==30);
 assert(!guest_cxa_atexit(recursive,(void*)0xbeef,(void*)0x111));runtime_finalize(NULL);assert(n==9&&log_items[7]==5&&log_items[8]==1&&module_calls==3);
 puts("candidate actual C atexit controls: global NULL, DSO callbacks before module substep, one-shot and recursive registration passed");
}
