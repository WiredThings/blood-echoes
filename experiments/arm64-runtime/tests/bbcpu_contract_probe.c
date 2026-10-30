// SPDX-License-Identifier: GPL-2.0-or-later
// Authored guest instructions only. No game, save, preferences or renderer.
#include "bbcpu.h"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/mman.h>

void *runtime_low_map(size_t size, int prot) {
    void *p=mmap((void *)UINT64_C(0x6000000000),size,prot,MAP_PRIVATE|MAP_ANON,-1,0);
    if(p==MAP_FAILED)return NULL;
    if((uintptr_t)p>=UINT64_C(0x10000000000)){munmap(p,size);return NULL;}
    return p;
}
static uintptr_t code;
uintptr_t bb_image_base;
static _Atomic unsigned released_stacks;
static uint64_t owned_stop;
static void release_stack(void *stack,size_t bytes){assert(munmap(stack,bytes)==0);atomic_fetch_add(&released_stacks,1);}
static int hle_syscall(BbGuestRegs *regs) {
    if(regs->rip==code+261){
        assert(regs->r[0]==37&&owned_stop);
        assert(!bbcpu_request_execution_stop(owned_stop+16));
        assert(bbcpu_request_execution_stop(owned_stop));
        regs->r[0]=0xabcddcba;return 1;
    }
    if(regs->rip!=code+205||regs->r[0]!=37)return 0;
    BbTrapState state;assert(bbcpu_trap_state(&state));
    assert(*state.mxcsr==0x1f80);
    regs->r[0]=regs->r[7]+regs->r[6];
    const uint64_t result=91;memcpy(state.ymm[0],&result,8);
    return 1;
}
static uint64_t nested(uint64_t x) {
    const uint64_t args[]={x,9};
    return bbcpu_call(code,2,args)+11;
}
static void *worker(void *value) {
    uint64_t tcb[2]={(uint64_t)(uintptr_t)value,0};
    bbcpu_set_thread_pointer((uintptr_t)tcb);
    assert(bbcpu_call(code+64,0,NULL)==tcb[0]);
    const uint64_t args[]={17,19};
    assert(bbcpu_call(code,2,args)==36);
    return NULL;
}
static uint64_t native_counter(void){uint64_t n;__asm__ volatile("mrs %0,cntvct_el0":"=r"(n));return n;}
int main(int argc,char **argv) {
    assert(argc==1||(argc==3&&(!strcmp(argv[2],"static")||!strcmp(argv[2],"changed")||!strcmp(argv[2],"wrong-abi")||!strcmp(argv[2],"bad-span"))));
    bbcpu_set_stack_release(release_stack);
    uint8_t *p=runtime_low_map(16384,PROT_READ|PROT_WRITE);
    assert(p);code=(uintptr_t)p;bb_image_base=code;
    // add(rdi,rsi); a nested guest->host->guest call; load fs:[0].
    const uint8_t sum[]={0x48,0x89,0xf8,0x48,0x01,0xf0,0xc3};
    const uint8_t tls[]={0x64,0x48,0x8b,0x04,0x25,0,0,0,0,0xc3};
    const uint8_t seventh[]={0x48,0x8b,0x44,0x24,0x08,0xc3};
    const uint8_t syscall_code[]={0x48,0x89,0xf8,0x0f,0x05,0xc3};
    memcpy(p,sum,sizeof(sum));memcpy(p+64,tls,sizeof(tls));
    memcpy(p+4096,sum,sizeof(sum));
    const uint8_t tsc[]={0x0f,0x31,0x48,0xc1,0xe2,0x20,0x48,0x09,0xd0,0xc3};
    memcpy(p+4112,tsc,sizeof(tsc));
    const uint8_t static_exit[]={0x48,0x89,0xf8,0x48,0x83,0xec,0x08,0xbf,0x25,0,0,0,0xff,0xd0,0x0f,0x0b};
    const uint8_t exit_syscall[]={0x48,0x89,0xf8,0x0f,0x05,0x0f,0x0b};
    memcpy(p+4160,static_exit,sizeof(static_exit));memcpy(p+256,exit_syscall,sizeof(exit_syscall));
    const int changed=argc==3&&!strcmp(argv[2],"changed");
    const int wrong_abi=argc==3&&!strcmp(argv[2],"wrong-abi");
    const int bad_span=argc==3&&!strcmp(argv[2],"bad-span");
    if(changed)p[4100]=0x29; /* same function changed from ADD to SUB */
    memcpy(p+128,seventh,sizeof(seventh));memcpy(p+200,syscall_code,sizeof(syscall_code));
    const uint8_t enter[]={0x48,0x83,0xec,0x08,0x48,0xb8};
    const uint8_t leave[]={0xff,0xd0,0x48,0x83,0xc4,0x08,0xc3};
    memcpy(p+32,enter,sizeof(enter));uint64_t host=(uintptr_t)nested;memcpy(p+38,&host,8);
    memcpy(p+46,leave,sizeof(leave));
    assert(mprotect(p,16384,PROT_READ)==0);
    bbcpu_add_guest_code(code,16384);
    bbcpu_set_syscall_handler(hle_syscall);
    if(argc==3){
        assert(setenv("BB_RECOMP_LIB",argv[1],1)==0);
        bbcpu_recomp_load(code);
        assert(bbcpu_recomp_functions()==(wrong_abi?0:bad_span?1:changed?2:3));
        const uint64_t input[]={70,19};
        for(int i=0;i<9000;++i)assert(bbcpu_call(code+4096,2,input)==(changed?51:89));
        extern uint64_t bbcpu_recomp_calls;
        assert((changed||wrong_abi||bad_span)?bbcpu_recomp_calls==0:bbcpu_recomp_calls>=8192);
        puts(bad_span?"BBCPU invalid function spans rejected; own translator retained":wrong_abi?"BBCPU wrong ABI rejected; own translator retained":changed?"BBCPU mismatch rejected; own translator returned changed result":"BBCPU static native code actually executed (at least 8192 calls)");
    }
    const uint64_t before=native_counter(),tick=bbcpu_call(code+4112,0,NULL),after=native_counter();
    assert(before<=tick&&tick<=after);
    const uint64_t t0=native_counter(),runtime_tick=bbcpu_tsc(),t1=native_counter();
    assert(t0<=runtime_tick&&runtime_tick<=t1);
    assert(bbcpu_enabled());
    uint64_t args[]={23,29};assert(bbcpu_call(code,2,args)==52);
    assert(bbcpu_call(code+32,1,args)==43);
    const uint64_t seven[]={1,2,3,4,5,6,7007};assert(bbcpu_call(code+128,7,seven)==7007);
    uint8_t *stack=runtime_low_map(16384,PROT_READ|PROT_WRITE);assert(stack);
    const uint64_t stop=UINT64_C(0x700000001234);
    BbExecutionFrame frame={0};frame.rip=code+200;frame.flags=2;frame.mxcsr=0x1f80;frame.fcw=0x37f;
    frame.stack_top=(uintptr_t)stack+16384;frame.r[4]=frame.stack_top-8;
    memcpy((void *)frame.r[4],&stop,8);frame.r[7]=37;frame.r[6]=3;
    assert(bbcpu_execute_frame(&frame,stop)==0&&frame.r[0]==40&&frame.rip==stop);
    uint64_t vector_result;memcpy(&vector_result,frame.ymm[0],8);assert(vector_result==91);
    // Static C -> own translator -> completed HLE callback -> owned Run stop.
    // Neither following UD2 nor another host/native frame may execute.
    owned_stop=stop;assert(!bbcpu_request_execution_stop(stop));
    extern uint64_t bbcpu_recomp_calls;const uint64_t calls_before_stop=bbcpu_recomp_calls;
    const unsigned stops=argc==3&&!wrong_abi?9000:1;
    for(unsigned i=0;i<stops;++i){
        frame.rip=code+4160;frame.r[4]=frame.stack_top-8;frame.r[7]=code+256;
        memcpy((void*)frame.r[4],&stop,8);
        assert(bbcpu_execute_frame(&frame,stop)==0&&frame.rip==stop&&frame.r[0]==0xabcddcba);
    }
    if(stops==9000)assert(bbcpu_recomp_calls>=calls_before_stop+8192);
    assert(!bbcpu_request_execution_stop(stop));owned_stop=0;
    BbTrapState absent;assert(!bbcpu_trap_state(&absent));
    assert(bbcpu_execute_frame(NULL,stop)==EINVAL);
    frame.rip=code;frame.mxcsr=0x10000;assert(bbcpu_execute_frame(&frame,stop)==EINVAL);
    frame.mxcsr=0x1f80;frame.r[4]=0;assert(bbcpu_execute_frame(&frame,stop)==EINVAL);
    pthread_t threads[4];
    for(uintptr_t i=0;i<4;++i)assert(pthread_create(&threads[i],NULL,worker,(void *)(i+100))==0);
    for(int i=0;i<4;++i)assert(pthread_join(threads[i],NULL)==0);
    fprintf(stderr,"BBCPU thread lifecycle: live=%llu released_stacks=%u\n",(unsigned long long)bbcpu_live_thread_contexts(),atomic_load(&released_stacks));
    assert(bbcpu_live_thread_contexts()==1&&atomic_load(&released_stacks)==4);
    uint32_t mxcsr;uint16_t fcw;bbcpu_get_fp_control(&mxcsr,&fcw);
    assert(mxcsr==0x1f80&&fcw==0x37f);
    bbcpu_set_fp_control(0x9fc0,0x37f);bbcpu_get_fp_control(&mxcsr,&fcw);assert(mxcsr==0x9fc0);
    bbcpu_report();
    puts("BBCPU arithmetic, nested callbacks, seventh SysV argument, full frame, HLE syscall, invalid frames, thread TLS and FP control passed");
    return 0;
}
