// SPDX-License-Identifier: GPL-2.0-or-later
/* Blood Echoes: exact affine stack state of an installed continuation.
 * Analysis only, through the same Zydis decoder. Never assumes a prologue. */
static int prefix_gpr(ZydisRegister reg) {
    ZydisRegister full=ZydisRegisterGetLargestEnclosing(ZYDIS_MACHINE_MODE_LONG_64,reg);
    if(full<ZYDIS_REGISTER_RAX||full>ZYDIS_REGISTER_R15)return -1;
    return (int)full-(int)ZYDIS_REGISTER_RAX;
}
static int prefix_frame(uint64_t start,uint64_t resume,int *result_reg,int64_t *result_delta) {
    if(resume<=start||resume-start>512||start>UINT64_MAX-512)return 0;
    int known[16]={0};int64_t bias[16]={0};known[4]=1;
    ZydisDecoder decoder;ZydisDecoderInit(&decoder,ZYDIS_MACHINE_MODE_LONG_64,ZYDIS_STACK_WIDTH_64);
    for(uint64_t pc=start;pc<resume;){
        const unsigned char *bytes=at(pc,resume-pc);if(!bytes)return 0;
        ZydisDecodedInstruction ins;ZydisDecodedOperand ops[ZYDIS_MAX_OPERAND_COUNT];
        if(!ZYAN_SUCCESS(ZydisDecoderDecodeFull(&decoder,bytes,resume-pc,&ins,ops))||!ins.length)return 0;
        if(ins.meta.category==ZYDIS_CATEGORY_CALL||ins.meta.category==ZYDIS_CATEGORY_RET||
           ins.meta.category==ZYDIS_CATEGORY_COND_BR||ins.meta.category==ZYDIS_CATEGORY_UNCOND_BR||
           ins.mnemonic==ZYDIS_MNEMONIC_SYSCALL||ins.mnemonic==ZYDIS_MNEMONIC_INT||
           ins.mnemonic==ZYDIS_MNEMONIC_INT3||ins.mnemonic==ZYDIS_MNEMONIC_HLT)return 0;
        int previous[16];int64_t old[16];memcpy(previous,known,sizeof(known));memcpy(old,bias,sizeof(bias));
        for(unsigned i=0;i<ins.operand_count;++i)if(ops[i].type==ZYDIS_OPERAND_TYPE_REGISTER&&
            (ops[i].actions&ZYDIS_OPERAND_ACTION_MASK_WRITE)){
            const int reg=prefix_gpr(ops[i].reg.value);if(reg>=0)known[reg]=0;
        }
        const int dst=ins.operand_count_visible&&ops[0].type==ZYDIS_OPERAND_TYPE_REGISTER?prefix_gpr(ops[0].reg.value):-1;
        if(ins.mnemonic==ZYDIS_MNEMONIC_PUSH||ins.mnemonic==ZYDIS_MNEMONIC_PUSHFQ){
            if(ins.operand_width!=64)return 0;known[4]=previous[4];bias[4]=old[4]-8;
        }else if(ins.mnemonic==ZYDIS_MNEMONIC_POP||ins.mnemonic==ZYDIS_MNEMONIC_POPFQ){
            if(ins.operand_width!=64||dst==4)return 0;known[4]=previous[4];bias[4]=old[4]+8;
        }else if(dst>=0&&ops[0].size==64&&ins.operand_count_visible>=2){
            if(ins.mnemonic==ZYDIS_MNEMONIC_MOV&&ops[1].type==ZYDIS_OPERAND_TYPE_REGISTER){
                const int src=prefix_gpr(ops[1].reg.value);if(src>=0&&ops[1].size==64){known[dst]=previous[src];bias[dst]=old[src];}
            }else if(ins.mnemonic==ZYDIS_MNEMONIC_LEA&&ops[1].type==ZYDIS_OPERAND_TYPE_MEMORY&&ops[1].mem.index==ZYDIS_REGISTER_NONE){
                const int src=prefix_gpr(ops[1].mem.base);const int64_t disp=ops[1].mem.disp.value;
                if(src>=0&&previous[src]&&disp>=-1048576&&disp<=1048576){known[dst]=1;bias[dst]=old[src]+disp;}
            }else if((ins.mnemonic==ZYDIS_MNEMONIC_ADD||ins.mnemonic==ZYDIS_MNEMONIC_SUB)&&ops[1].type==ZYDIS_OPERAND_TYPE_IMMEDIATE){
                const int64_t n=ops[1].imm.is_signed?ops[1].imm.value.s:(int64_t)ops[1].imm.value.u;
                if(previous[dst]&&n>=-1048576&&n<=1048576){known[dst]=1;bias[dst]=old[dst]+(ins.mnemonic==ZYDIS_MNEMONIC_ADD?n:-n);}
            }
        }
        for(int reg=0;reg<16;++reg)if(known[reg]&&(bias[reg]<-1048576||bias[reg]>1048576))return 0;
        pc+=ins.length;
    }
    /* Prefer RSP; otherwise a proven frame register survives alignment. */
    for(int pick=0;pick<17;++pick){int reg=pick==0?4:pick-1;if(known[reg]){*result_reg=reg;*result_delta=-bias[reg];return 1;}}
    return 0;
}
/* Inline hooks can lie after branches: use the real FDE's CFA row, rather
 * than pretending that bytes from function entry form one straight path. */
typedef struct {const unsigned char *p,*end;int ok;} PrefixCursor;
static uint64_t prefix_uleb(PrefixCursor *c){
    uint64_t n=0;unsigned shift=0;
    for(unsigned i=0;i<10&&c->p<c->end;++i){unsigned b=*c->p++;if(shift==63&&(b&126)){c->ok=0;return 0;}n|=(uint64_t)(b&127)<<shift;if(!(b&128))return n;shift+=7;}
    c->ok=0;return 0;
}
static int64_t prefix_sleb(PrefixCursor *c){
    uint64_t n=0;unsigned shift=0,b=0;
    for(unsigned i=0;i<10&&c->p<c->end;++i){b=*c->p++;if(i==9&&b!=0&&b!=127){c->ok=0;return 0;}n|=(uint64_t)(b&127)<<shift;shift+=7;if(!(b&128)){if(shift<64&&(b&64))n|=~UINT64_C(0)<<shift;return (int64_t)n;}}
    c->ok=0;return 0;
}
typedef struct {int reg,known,return_offset_known;int64_t offset,return_offset;} PrefixCfa;
static int prefix_cfi_program(PrefixCursor c,uint64_t pc,uint64_t target,uint64_t code_align,int64_t data_align,PrefixCfa *state,PrefixCfa initial){
    PrefixCfa saved[16];unsigned depth=0,steps=0;
    while(c.ok&&c.p<c.end&&++steps<=4096){
        unsigned op=*c.p++;uint64_t a=0,b=0,advance=0;int64_t signed_value=0;
        if((op&192)==64){advance=op&63;op=2;}
        else if((op&192)==128){a=op&63;b=prefix_uleb(&c);if(a==16){if(b>1048576)return 0;state->return_offset_known=1;state->return_offset=(int64_t)b*data_align;}continue;}
        else if((op&192)==192){if((op&63)==16){state->return_offset_known=initial.return_offset_known;state->return_offset=initial.return_offset;}continue;}
        else if(op==2){if(c.p==c.end)return 0;advance=*c.p++;}
        else if(op==3||op==4){unsigned size=op==3?2:4;if((size_t)(c.end-c.p)<size)return 0;memcpy(&advance,c.p,size);c.p+=size;op=2;}
        if(op==2){if(advance>UINT64_MAX/code_align||pc>UINT64_MAX-advance*code_align)return 0;pc+=advance*code_align;if(pc>target)return c.ok;continue;}
        switch(op){
        case 0:break;
        case 5:case 17:case 20:case 21:
            a=prefix_uleb(&c);if(op==17||op==21)signed_value=prefix_sleb(&c);else b=prefix_uleb(&c);
            if(a==16){if(b>1048576||signed_value<-1048576||signed_value>1048576)return 0;state->return_offset_known=op==5||op==17;state->return_offset=(op==17||op==21)?signed_value*data_align:(int64_t)b*data_align;}break;
        case 6:a=prefix_uleb(&c);if(a==16){state->return_offset_known=initial.return_offset_known;state->return_offset=initial.return_offset;}break;
        case 7:case 8:a=prefix_uleb(&c);if(a==16)state->return_offset_known=0;break;
        case 9:a=prefix_uleb(&c);(void)prefix_uleb(&c);if(a==16)state->return_offset_known=0;break;
        case 10:if(depth==16)return 0;saved[depth++]=*state;break;
        case 11:if(!depth)return 0;*state=saved[--depth];break;
        case 12:case 18:
            a=prefix_uleb(&c);if(op==18)signed_value=prefix_sleb(&c);else b=prefix_uleb(&c);
            if(a>=16||b>1048576||signed_value<-1048576||signed_value>1048576)return 0;
            state->reg=(int)a;state->offset=op==18?signed_value*data_align:(int64_t)b;state->known=1;break;
        case 13:a=prefix_uleb(&c);if(a>=16)return 0;state->reg=(int)a;break;
        case 14:case 19:
            if(op==19)signed_value=prefix_sleb(&c);else b=prefix_uleb(&c);
            if(b>1048576||signed_value<-1048576||signed_value>1048576)return 0;
            state->offset=op==19?signed_value*data_align:(int64_t)b;break;
        case 15:state->known=0;b=prefix_uleb(&c);if(b>(uint64_t)(c.end-c.p))return 0;c.p+=b;break;
        case 16:case 22:a=prefix_uleb(&c);b=prefix_uleb(&c);if(b>(uint64_t)(c.end-c.p))return 0;c.p+=b;if(a==16)state->return_offset_known=0;break;
        case 46:(void)prefix_uleb(&c);break; /* GNU_args_size, no CFA change */
        default:return 0;
        }
    }
    return c.ok&&steps<=4096;
}
static int prefix_cfi(uint64_t start,uint64_t resume,int *reg,int64_t *delta){
    const Elf64_Ehdr *eh=(const Elf64_Ehdr*)data;const Elf64_Phdr *frame=NULL;
    for(int i=0;i<eh->e_phnum;++i){const Elf64_Phdr *p=(const Elf64_Phdr*)(data+eh->e_phoff+i*eh->e_phentsize);if(p->p_type==PT_GNU_EH_FRAME)frame=p;}
    if(!frame||frame->p_filesz<12)return 0;const unsigned char *h=at(frame->p_vaddr,frame->p_filesz);
    if(!h||h[0]!=1||h[1]!=0x1b||h[2]!=3||h[3]!=0x3b)return 0;
    uint32_t count;memcpy(&count,h+8,4);if(count>(frame->p_filesz-12)/8)return 0;
    uint64_t address=0;
    for(uint32_t i=0;i<count;++i){int32_t location,relative;memcpy(&location,h+12+8*i,4);memcpy(&relative,h+16+8*i,4);if(frame->p_vaddr+(int64_t)location==start){address=frame->p_vaddr+(int64_t)relative;break;}}
    if(!address)return 0;const unsigned char *f=at(address,16);if(!f)return 0;
    uint32_t length,cie_back,range;int32_t location;memcpy(&length,f,4);memcpy(&cie_back,f+4,4);memcpy(&location,f+8,4);memcpy(&range,f+12,4);
    if(length<13||length>1048576||start!=address+8+(int64_t)location||resume<start||resume-start>=range||cie_back>address+4)return 0;
    f=at(address,4+(uint64_t)length);const uint64_t cie=address+4-cie_back;const unsigned char *c=at(cie,9);if(!f||!c)return 0;
    uint32_t cie_length,cie_id;memcpy(&cie_length,c,4);memcpy(&cie_id,c+4,4);if(cie_id||cie_length<9||cie_length>1048576)return 0;c=at(cie,4+(uint64_t)cie_length);if(!c||c[8]!=1)return 0;
    PrefixCursor cc={c+9,c+4+cie_length,1};const unsigned char *aug=cc.p;while(cc.p<cc.end&&*cc.p)++cc.p;if(cc.p==cc.end)return 0;size_t aug_size=(size_t)(cc.p-aug);++cc.p;
    uint64_t code_align=prefix_uleb(&cc);int64_t data_align=prefix_sleb(&cc);uint64_t ret=prefix_uleb(&cc);
    if(!cc.ok||!code_align||code_align>1024||data_align!=-8||ret!=16||aug_size!=2||memcmp(aug,"zR",2))return 0;
    uint64_t extra=prefix_uleb(&cc);if(!cc.ok||extra!=1||cc.p==cc.end||*cc.p++!=0x1b)return 0;
    PrefixCfa state={0},empty={0};if(!prefix_cfi_program(cc,start,UINT64_MAX,code_align,data_align,&state,empty))return 0;
    PrefixCursor fc={f+16,f+4+length,1};extra=prefix_uleb(&fc);if(!fc.ok||extra>(uint64_t)(fc.end-fc.p))return 0;fc.p+=extra;
    PrefixCfa initial=state;if(!prefix_cfi_program(fc,start,resume,code_align,data_align,&state,initial)||!state.known||!state.return_offset_known||state.return_offset!=-8||state.reg<0||state.reg>=16||state.offset<-1048576||state.offset>1048576)return 0;
    static const int dwarf_to_cpu[16]={0,2,1,3,6,7,5,4,8,9,10,11,12,13,14,15};*reg=dwarf_to_cpu[state.reg];*delta=state.offset-8;return 1;
}
static int prefix_frames(const char *input,const char *output){
    FILE *in=fopen(input,"r"),*out=fopen(output,"w");if(!in||!out){perror("prefix frame files");return 1;}
    char line[256];unsigned count=0;fputs("resume\tregister\tentry_delta\n",out);
    while(fgets(line,sizeof(line),in)){
        if(!strcmp(line,"start\tresume\n"))continue;
        unsigned long long start,resume;char extra;
        if(++count>64||sscanf(line,"%llx %llx %c",&start,&resume,&extra)!=2){fprintf(stderr,"invalid prefix frame input\n");return 1;}
        int reg;int64_t delta;
        if(!prefix_frame(start,resume,&reg,&delta)&&!prefix_cfi(start,resume,&reg,&delta)){fprintf(stderr,"unproven continuation return slot: start=%#llx resume=%#llx\n",start,resume);return 1;}
        fprintf(out,"%#llx\t%d\t%lld\n",resume,reg,(long long)delta);
    }
    int failed=ferror(in)||ferror(out);fclose(in);failed|=fclose(out)!=0;return failed;
}
