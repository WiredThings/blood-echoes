/* bbcpu interpreter: x87 instructions, with values held as doubles (the game uses x87 in a few
 * plain computations and its libc's printf/strtod). 80-bit memory operands are converted. */
#include "cpu_internal.h"
#include <math.h>

#define M(name) ZYDIS_MNEMONIC_##name

static double *st(BbCpu *cpu, int i) { return &cpu->st[(cpu->top + i) & 7]; }
static void fpush(BbCpu *cpu, double v) { cpu->top = (cpu->top - 1) & 7; *st(cpu, 0) = v; }
static double fpop(BbCpu *cpu) { const double v = *st(cpu, 0); cpu->top = (cpu->top + 1) & 7; return v; }

static double load80(uint64_t address) {
    uint64_t mant;
    uint16_t se;
    memcpy(&mant, (const void *)address, 8);
    memcpy(&se, (const void *)(address + 8), 2);
    const int sign = se >> 15, exp = se & 0x7fff;
    double v;
    if (exp == 0 && mant == 0) v = 0.0;
    else if (exp == 0x7fff) v = (mant << 1) ? NAN : INFINITY;
    else v = ldexp((double)mant, exp - 16383 - 63);
    return sign ? -v : v;
}
static void store80(uint64_t address, double v) {
    uint64_t mant = 0;
    uint16_t se = signbit(v) ? 0x8000 : 0;
    if (isnan(v)) { mant = UINT64_C(0xC000000000000000); se |= 0x7fff; }
    else if (isinf(v)) { mant = UINT64_C(0x8000000000000000); se |= 0x7fff; }
    else if (v != 0.0) {
        int exp;
        const double frac = frexp(fabs(v), &exp); /* v = frac * 2^exp, frac in [0.5, 1) */
        mant = (uint64_t)ldexp(frac, 64);
        se |= (uint16_t)(exp - 1 + 16383);
    }
    memcpy((void *)address, &mant, 8);
    memcpy((void *)(address + 8), &se, 2);
}

static double load_mem(BbCpu *cpu, const BbInsn *in, const BbOp *op, int integer) {
    const uint64_t address = bbcpu_addr(cpu, in, op);
    if (integer) return (double)bb_sext(bb_load(address, op->size), op->size);
    if (op->size == 4) { float f; memcpy(&f, (const void *)address, 4); return f; }
    if (op->size == 8) { double d; memcpy(&d, (const void *)address, 8); return d; }
    return load80(address);
}

static double operand(BbCpu *cpu, const BbInsn *in, const BbOp *op, int integer) {
    if (op->type == OP_REG && op->kind == RK_X87) return *st(cpu, op->reg);
    return load_mem(cpu, in, op, integer);
}

static void set_compare(BbCpu *cpu, double a, double b, int eflags) {
    if (eflags) {
        uint64_t f = cpu->flags & ~(uint64_t)F_ARITH;
        if (a != a || b != b) f |= F_ZF | F_PF | F_CF;
        else if (a < b) f |= F_CF;
        else if (a == b) f |= F_ZF;
        cpu->flags = f;
    } else {
        uint16_t sw = cpu->fsw & ~0x4500; /* C0 0x100, C2 0x400, C3 0x4000 */
        if (a != a || b != b) sw |= 0x4500;
        else if (a < b) sw |= 0x100;
        else if (a == b) sw |= 0x4000;
        cpu->fsw = sw;
    }
}

static int64_t to_int(BbCpu *cpu, double v, int truncate) {
    if (truncate) return (int64_t)v;
    switch ((cpu->fcw >> 10) & 3) {
    case 0: return (int64_t)nearbyint(v);
    case 1: return (int64_t)floor(v);
    case 2: return (int64_t)ceil(v);
    default: return (int64_t)v;
    }
}

/* Arithmetic: kind 0 add, 1 mul, 2 sub, 3 subr, 4 div, 5 divr. */
static double arith(int kind, double a, double b) {
    switch (kind) {
    case 0: return a + b;
    case 1: return a * b;
    case 2: return a - b;
    case 3: return b - a;
    case 4: return a / b;
    default: return b / a;
    }
}

static int arith_kind(int m) {
    switch (m) {
    case M(FADD): case M(FADDP): case M(FIADD): return 0;
    case M(FMUL): case M(FMULP): case M(FIMUL): return 1;
    case M(FSUB): case M(FSUBP): case M(FISUB): return 2;
    case M(FSUBR): case M(FSUBRP): case M(FISUBR): return 3;
    case M(FDIV): case M(FDIVP): case M(FIDIV): return 4;
    case M(FDIVR): case M(FDIVRP): case M(FIDIVR): return 5;
    default: return -1;
    }
}

int bbcpu_exec_x87(BbCpu *cpu, const BbInsn *in) {
    const int m = in->mnemonic;
    const int kind = arith_kind(m);
    if (kind >= 0) {
        const int integer = m == M(FIADD) || m == M(FIMUL) || m == M(FISUB) || m == M(FISUBR) ||
                            m == M(FIDIV) || m == M(FIDIVR);
        const int pop = m == M(FADDP) || m == M(FMULP) || m == M(FSUBP) || m == M(FSUBRP) ||
                        m == M(FDIVP) || m == M(FDIVRP);
        if (in->count == 2 && in->op[0].type == OP_REG && in->op[1].type == OP_REG) {
            /* fop st(i), st(j): destination op[0] */
            double *d = st(cpu, in->op[0].reg);
            *d = arith(kind, *d, *st(cpu, in->op[1].reg));
        } else if (in->count >= 1 && in->op[in->count - 1].type == OP_MEM) {
            *st(cpu, 0) = arith(kind, *st(cpu, 0), operand(cpu, in, &in->op[in->count - 1], integer));
        } else if (in->count == 1) {
            *st(cpu, 0) = arith(kind, *st(cpu, 0), *st(cpu, in->op[0].reg));
        } else {
            double *d = st(cpu, 1);
            *d = arith(kind, *d, *st(cpu, 0));
        }
        if (pop) fpop(cpu);
        return 1;
    }
    switch (m) {
    case M(FLD): {
        const BbOp *op = &in->op[in->count - 1];
        fpush(cpu, op->type == OP_REG ? *st(cpu, op->reg) : load_mem(cpu, in, op, 0));
        /* fld st(i) reads before the push moved top: */
        if (op->type == OP_REG) *st(cpu, 0) = *st(cpu, op->reg + 1);
        return 1;
    }
    case M(FILD): fpush(cpu, load_mem(cpu, in, &in->op[in->count - 1], 1)); return 1;
    case M(FST): case M(FSTP): {
        const BbOp *op = &in->op[0];
        const double v = *st(cpu, 0);
        if (op->type == OP_REG && op->kind == RK_X87) *st(cpu, op->reg) = v;
        else {
            const uint64_t address = bbcpu_addr(cpu, in, op);
            if (op->size == 4) { const float f = (float)v; memcpy((void *)address, &f, 4); }
            else if (op->size == 8) memcpy((void *)address, &v, 8);
            else store80(address, v);
        }
        if (m == M(FSTP)) fpop(cpu);
        return 1;
    }
    case M(FIST): case M(FISTP): case M(FISTTP): {
        const BbOp *op = &in->op[0];
        const int64_t v = to_int(cpu, *st(cpu, 0), m == M(FISTTP));
        bb_store(bbcpu_addr(cpu, in, op), op->size, (uint64_t)v);
        if (m != M(FIST)) fpop(cpu);
        return 1;
    }
    case M(FXCH): {
        const int i = in->count ? in->op[in->count - 1].reg : 1;
        const double t = *st(cpu, 0);
        *st(cpu, 0) = *st(cpu, i);
        *st(cpu, i) = t;
        return 1;
    }
    case M(FLD1): fpush(cpu, 1.0); return 1;
    case M(FLDZ): fpush(cpu, 0.0); return 1;
    case M(FLDPI): fpush(cpu, M_PI); return 1;
    case M(FLDL2E): fpush(cpu, M_LOG2E); return 1;
    case M(FLDLN2): fpush(cpu, M_LN2); return 1;
    case M(FLDL2T): fpush(cpu, log2(10.0)); return 1;
    case M(FLDLG2): fpush(cpu, log10(2.0)); return 1;
    case M(FCHS): *st(cpu, 0) = -*st(cpu, 0); return 1;
    case M(FABS): *st(cpu, 0) = fabs(*st(cpu, 0)); return 1;
    case M(FSQRT): *st(cpu, 0) = sqrt(*st(cpu, 0)); return 1;
    case M(FSIN): *st(cpu, 0) = sin(*st(cpu, 0)); cpu->fsw &= ~0x400; return 1;
    case M(FCOS): *st(cpu, 0) = cos(*st(cpu, 0)); cpu->fsw &= ~0x400; return 1;
    case M(FSINCOS): { const double v = *st(cpu, 0); *st(cpu, 0) = sin(v); fpush(cpu, cos(v)); cpu->fsw &= ~0x400; return 1; }
    case M(FPTAN): *st(cpu, 0) = tan(*st(cpu, 0)); fpush(cpu, 1.0); cpu->fsw &= ~0x400; return 1;
    case M(FPATAN): { const double x = fpop(cpu); *st(cpu, 0) = atan2(*st(cpu, 0), x); return 1; }
    case M(FSCALE): *st(cpu, 0) = ldexp(*st(cpu, 0), (int)trunc(*st(cpu, 1))); return 1;
    case M(FRNDINT): *st(cpu, 0) = (double)to_int(cpu, *st(cpu, 0), 0); return 1;
    case M(F2XM1): *st(cpu, 0) = exp2(*st(cpu, 0)) - 1.0; return 1;
    case M(FYL2X): { const double x = fpop(cpu); *st(cpu, 0) = *st(cpu, 0) * log2(x); return 1; }
    case M(FYL2XP1): { const double x = fpop(cpu); *st(cpu, 0) = *st(cpu, 0) * log2(x + 1.0); return 1; }
    case M(FPREM): case M(FPREM1): {
        const double a = *st(cpu, 0), b = *st(cpu, 1);
        *st(cpu, 0) = m == M(FPREM) ? fmod(a, b) : remainder(a, b);
        cpu->fsw &= ~0x400;
        return 1;
    }
    case M(FXTRACT): {
        const double v = *st(cpu, 0);
        int e;
        const double f = frexp(v, &e);
        *st(cpu, 0) = (double)(e - 1);
        fpush(cpu, f * 2.0);
        return 1;
    }
    case M(FCOM): case M(FCOMP): case M(FUCOM): case M(FUCOMP): case M(FICOM): case M(FICOMP): {
        const int integer = m == M(FICOM) || m == M(FICOMP);
        const double b = in->count ? operand(cpu, in, &in->op[in->count - 1], integer) : *st(cpu, 1);
        set_compare(cpu, *st(cpu, 0), b, 0);
        if (m == M(FCOMP) || m == M(FUCOMP) || m == M(FICOMP)) fpop(cpu);
        return 1;
    }
    case M(FCOMPP): case M(FUCOMPP):
        set_compare(cpu, *st(cpu, 0), *st(cpu, 1), 0);
        fpop(cpu); fpop(cpu);
        return 1;
    case M(FCOMI): case M(FCOMIP): case M(FUCOMI): case M(FUCOMIP):
        set_compare(cpu, *st(cpu, 0), *st(cpu, in->op[in->count - 1].reg), 1);
        if (m == M(FCOMIP) || m == M(FUCOMIP)) fpop(cpu);
        return 1;
    case M(FTST): set_compare(cpu, *st(cpu, 0), 0.0, 0); return 1;
    case M(FXAM): {
        const double v = *st(cpu, 0);
        uint16_t sw = cpu->fsw & ~0x4700;
        if (signbit(v)) sw |= 0x200;
        if (isnan(v)) sw |= 0x100;
        else if (isinf(v)) sw |= 0x500;
        else if (v == 0.0) sw |= 0x4000;
        else sw |= 0x400;
        cpu->fsw = sw;
        return 1;
    }
    case M(FCMOVB): case M(FCMOVE): case M(FCMOVBE): case M(FCMOVU): case M(FCMOVNB):
    case M(FCMOVNE): case M(FCMOVNBE): case M(FCMOVNU): {
        const uint64_t f = cpu->flags;
        int c;
        switch (m) {
        case M(FCMOVB): c = !!(f & F_CF); break;
        case M(FCMOVE): c = !!(f & F_ZF); break;
        case M(FCMOVBE): c = !!(f & (F_CF | F_ZF)); break;
        case M(FCMOVU): c = !!(f & F_PF); break;
        case M(FCMOVNB): c = !(f & F_CF); break;
        case M(FCMOVNE): c = !(f & F_ZF); break;
        case M(FCMOVNBE): c = !(f & (F_CF | F_ZF)); break;
        default: c = !(f & F_PF); break;
        }
        if (c) *st(cpu, 0) = *st(cpu, in->op[in->count - 1].reg);
        return 1;
    }
    case M(FNSTSW): {
        const uint16_t sw = (uint16_t)((cpu->fsw & ~0x3800) | ((cpu->top & 7) << 11));
        const BbOp *op = &in->op[0];
        if (op->type == OP_REG) cpu->r[RAX] = (cpu->r[RAX] & ~UINT64_C(0xffff)) | sw;
        else bb_store(bbcpu_addr(cpu, in, op), 2, sw);
        return 1;
    }
    case M(FNSTCW): bb_store(bbcpu_addr(cpu, in, &in->op[0]), 2, cpu->fcw); return 1;
    case M(FLDCW): cpu->fcw = (uint16_t)bb_load(bbcpu_addr(cpu, in, &in->op[0]), 2); return 1;
    case M(FNINIT): cpu->fcw = 0x37f; cpu->fsw = 0; cpu->top = 0; return 1;
    case M(FNCLEX): cpu->fsw &= 0x7f00; return 1;
    case M(FNSTENV): { /* 28-byte protected-mode environment; then all exceptions masked */
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        memset((void *)a, 0, 28);
        bb_store(a, 2, cpu->fcw);
        bb_store(a + 4, 2, (uint16_t)((cpu->fsw & ~0x3800) | ((cpu->top & 7) << 11)));
        bb_store(a + 8, 2, 0); /* tag word: all valid (values are kept as doubles) */
        cpu->fcw |= 0x3f;
        return 1;
    }
    case M(FLDENV): {
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        cpu->fcw = (uint16_t)bb_load(a, 2);
        const uint16_t sw = (uint16_t)bb_load(a + 4, 2);
        cpu->fsw = sw & ~0x3800;
        cpu->top = (sw >> 11) & 7;
        return 1;
    }
    case M(FNSAVE): { /* environment, then the 8 registers as 80-bit values; then finit */
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        memset((void *)a, 0, 28);
        bb_store(a, 2, cpu->fcw);
        bb_store(a + 4, 2, (uint16_t)((cpu->fsw & ~0x3800) | ((cpu->top & 7) << 11)));
        for (int i = 0; i < 8; ++i) store80(a + 28 + i * 10, *st(cpu, i));
        cpu->fcw = 0x37f; cpu->fsw = 0; cpu->top = 0;
        return 1;
    }
    case M(FRSTOR): {
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        cpu->fcw = (uint16_t)bb_load(a, 2);
        const uint16_t sw = (uint16_t)bb_load(a + 4, 2);
        cpu->fsw = sw & ~0x3800;
        cpu->top = (sw >> 11) & 7;
        for (int i = 0; i < 8; ++i) *st(cpu, i) = load80(a + 28 + i * 10);
        return 1;
    }
    case M(FXSAVE): case M(FXSAVE64): { /* 512 bytes: x87 state, MXCSR, XMM0-15 */
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        memset((void *)a, 0, 512);
        bb_store(a, 2, cpu->fcw);
        bb_store(a + 2, 2, (uint16_t)((cpu->fsw & ~0x3800) | ((cpu->top & 7) << 11)));
        bb_store(a + 4, 1, 0xff);
        bb_store(a + 24, 4, cpu->mxcsr);
        bb_store(a + 28, 4, 0xffff);
        for (int i = 0; i < 8; ++i) store80(a + 32 + i * 16, *st(cpu, i));
        for (int i = 0; i < 16; ++i) memcpy((void *)(a + 160 + i * 16), cpu->v[i].b, 16);
        return 1;
    }
    case M(FXRSTOR): case M(FXRSTOR64): {
        const uint64_t a = bbcpu_addr(cpu, in, &in->op[0]);
        cpu->fcw = (uint16_t)bb_load(a, 2);
        const uint16_t sw = (uint16_t)bb_load(a + 2, 2);
        cpu->fsw = sw & ~0x3800;
        cpu->top = (sw >> 11) & 7;
        cpu->mxcsr = (uint32_t)bb_load(a + 24, 4);
        bbcpu_apply_host_fp(cpu->mxcsr);
        for (int i = 0; i < 8; ++i) *st(cpu, i) = load80(a + 32 + i * 16);
        for (int i = 0; i < 16; ++i) memcpy(cpu->v[i].b, (const void *)(a + 160 + i * 16), 16);
        return 1;
    }
    case M(FWAIT): case M(FNOP): case M(FFREE): case M(FDECSTP): case M(FINCSTP): return 1;
    default:
        return 0;
    }
}
