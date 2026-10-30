// SPDX-License-Identifier: GPL-2.0-or-later
// Authored instructions and unwind records; no game or user data.
#define main scanner_main
#include "../vendor/bbcpu/tools/recomp/scan.c"
#undef main
#include <assert.h>

static void put32(unsigned char *p,uint32_t value){memcpy(p,&value,4);}
static void make_image(void){
    data_size=8192;data=calloc(1,(size_t)data_size);assert(data);
    Elf64_Ehdr *eh=(void*)data;eh->e_phoff=64;eh->e_phentsize=56;eh->e_phnum=2;
    Elf64_Phdr *ph=(void*)(data+64);ph[0].p_type=PT_LOAD;ph[0].p_filesz=8192;
    ph[1].p_type=PT_GNU_EH_FRAME;ph[1].p_vaddr=0x200;ph[1].p_filesz=20;
    nloads=1;loads[0]=ph;
    // push rbp; mov rbp,rsp; conditional branch. CFA proves all paths.
    const unsigned char code[]={0x55,0x48,0x89,0xe5,0x74,0x02,0x90,0x90,0xc3};
    memcpy(data+0x1000,code,sizeof(code));
    unsigned char *h=data+0x200;h[0]=1;h[1]=0x1b;h[2]=3;h[3]=0x3b;
    put32(h+8,1);put32(h+12,0x1000-0x200);put32(h+16,(uint32_t)(0x140-0x200));
    const unsigned char cie[]={1,'z','R',0,1,0x78,16,1,0x1b,0x0c,7,8,0x90,1};
    put32(data+0x100,4+sizeof(cie));memcpy(data+0x108,cie,sizeof(cie));
    const unsigned char fde[]={0,0x41,0x0e,16,0x43,0x0d,6};
    put32(data+0x140,12+sizeof(fde));put32(data+0x144,0x144-0x100);
    put32(data+0x148,0x1000-0x148);put32(data+0x14c,sizeof(code));
    memcpy(data+0x150,fde,sizeof(fde));
}
static int program(const unsigned char *bytes,size_t size,PrefixCfa *state){
    PrefixCursor c={bytes,bytes+size,1};PrefixCfa initial=*state;
    return prefix_cfi_program(c,0,UINT64_MAX,1,-8,state,initial);
}
int main(void){
    make_image();int reg;int64_t delta;
    assert(prefix_frame(0x1000,0x1004,&reg,&delta)&&reg==4&&delta==8);
    assert(!prefix_frame(0x1000,0x1006,&reg,&delta));
    assert(prefix_cfi(0x1000,0x1001,&reg,&delta)&&reg==4&&delta==8);
    assert(prefix_cfi(0x1000,0x1006,&reg,&delta)&&reg==5&&delta==8);
    // Exact range and record admission; no invented unwind state.
    assert(!prefix_cfi(0x1000,0x1009,&reg,&delta));
    data[0x200+8]=2;assert(!prefix_cfi(0x1000,0x1006,&reg,&delta));data[0x208]=1;
    data[0x104]=1;assert(!prefix_cfi(0x1000,0x1006,&reg,&delta));data[0x104]=0;
    data[0x110]=0x1c;assert(!prefix_cfi(0x1000,0x1006,&reg,&delta));data[0x110]=0x1b;
    data[0x152]=0xff;assert(!prefix_cfi(0x1000,0x1006,&reg,&delta));data[0x152]=0x0e;
    PrefixCfa state={.known=1,.reg=7,.offset=8,.return_offset_known=1,.return_offset=-8};
    const unsigned char underflow[]={11},unknown[]={0x2f},bad_expr[]={15,127};
    assert(!program(underflow,sizeof(underflow),&state));
    assert(!program(unknown,sizeof(unknown),&state));
    assert(!program(bad_expr,sizeof(bad_expr),&state));
    const unsigned char val_offset[]={20,16,1};
    assert(program(val_offset,sizeof(val_offset),&state)&&!state.return_offset_known);
    const unsigned char huge[]={17,16,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0};
    assert(!program(huge,sizeof(huge),&state));
    unsigned char deep[17];memset(deep,10,sizeof(deep));assert(!program(deep,sizeof(deep),&state));
    const unsigned char bad_leb[]={0x80,0x80,0x80,0x80,0x80,0x80,0x80,0x80,0x80,0x02};
    PrefixCursor c={bad_leb,bad_leb+sizeof(bad_leb),1};(void)prefix_sleb(&c);assert(!c.ok);
    c=(PrefixCursor){bad_leb,bad_leb+sizeof(bad_leb),1};(void)prefix_uleb(&c);assert(!c.ok);
    assert(!at(UINT64_MAX,8)&&!at(8190,8));
    free(data);puts("Continuation return slots: straight-line and real CFA rows, bounded malformed-input refusals passed");
    return 0;
}
