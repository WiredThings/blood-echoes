/* bbcpu interpreter: SSE/AVX instructions. Legacy SSE forms merge into the destination and keep
 * bits 128-255; VEX forms take a separate first source and zero the bits above their width. */
#include "cpu_internal.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define M(name) ZYDIS_MNEMONIC_##name

static BbVec vread(BbCpu *cpu, const BbInsn *in, const BbOp *op) {
    BbVec v;
    memset(&v, 0, sizeof(v));
    if (op->type == OP_REG && op->kind == RK_VEC) return cpu->v[op->reg];
    if (op->type == OP_REG && op->kind == RK_GPR) { v.q[0] = bbcpu_read(cpu, in, op); return v; }
    if (op->type == OP_MEM) {
        memcpy(v.b, (const void *)bbcpu_addr(cpu, in, op), op->size);
        bb_fence_load();
        return v;
    }
    if (op->type == OP_IMM) { v.q[0] = (uint64_t)op->disp; return v; }
    bbcpu_fatal(cpu, in, "vector operand");
}

/* Writes `bytes` of v; VEX register destinations get zeros above them. */
static void vwrite(BbCpu *cpu, const BbInsn *in, const BbOp *op, const BbVec *v, int bytes) {
    if (op->type == OP_REG && op->kind == RK_VEC) {
        BbVec *d = &cpu->v[op->reg];
        memcpy(d->b, v->b, bytes);
        if (in->vex && bytes < 32) memset(d->b + bytes, 0, 32 - bytes);
        return;
    }
    if (op->type == OP_MEM) {
        bb_fence_store();
        memcpy((void *)bbcpu_addr(cpu, in, op), v->b, op->size);
        return;
    }
    if (op->type == OP_REG && op->kind == RK_GPR) { bbcpu_write(cpu, in, op, v->q[0] & bb_mask(op->size)); return; }
    bbcpu_fatal(cpu, in, "vector destination");
}

static int width(const BbInsn *in) { return in->vex && in->vl ? in->vl : 16; }

/* dst, first source, second source and immediate of a two/three-source form. */
typedef struct { const BbOp *dst, *s1, *s2, *imm; } Ops;
static Ops ops3(const BbInsn *in) {
    Ops o = {&in->op[0], NULL, NULL, NULL};
    if (in->vex && in->count >= 3 && in->op[2].type != OP_IMM) {
        o.s1 = &in->op[1]; o.s2 = &in->op[2];
        if (in->count >= 4 && in->op[3].type == OP_IMM) o.imm = &in->op[3];
    } else {
        o.s1 = &in->op[0]; o.s2 = &in->op[1];
        if (in->count >= 3 && in->op[2].type == OP_IMM) o.imm = &in->op[2];
    }
    return o;
}
/* dst, source and immediate of a one-source form (the last register/memory operand). */
static Ops ops1(const BbInsn *in) {
    Ops o = {&in->op[0], NULL, NULL, NULL};
    int last = in->count - 1;
    if (in->op[last].type == OP_IMM) { o.imm = &in->op[last]; --last; }
    o.s2 = &in->op[last];
    o.s1 = &in->op[0];
    return o;
}

static float fmin_x86(float a, float b) { return a < b ? a : b; }
static float fmax_x86(float a, float b) { return a > b ? a : b; }
static double dmin_x86(double a, double b) { return a < b ? a : b; }
static double dmax_x86(double a, double b) { return a > b ? a : b; }

static int32_t cvt_f2i32(double x, int truncate, uint32_t mxcsr) {
    if (x != x || x >= 2147483648.0 || x < -2147483648.0) return INT32_MIN;
    if (truncate) return (int32_t)x;
    switch ((mxcsr >> 13) & 3) {
    case 0: return (int32_t)nearbyint(x);
    case 1: return (int32_t)floor(x);
    case 2: return (int32_t)ceil(x);
    default: return (int32_t)x;
    }
}
static int64_t cvt_f2i64(double x, int truncate, uint32_t mxcsr) {
    if (x != x || x >= 9223372036854775808.0 || x < -9223372036854775808.0) return INT64_MIN;
    if (truncate) return (int64_t)x;
    switch ((mxcsr >> 13) & 3) {
    case 0: return (int64_t)nearbyint(x);
    case 1: return (int64_t)floor(x);
    case 2: return (int64_t)ceil(x);
    default: return (int64_t)x;
    }
}
static double round_mode(double x, int imm, uint32_t mxcsr) {
    const int mode = (imm & 4) ? (int)((mxcsr >> 13) & 3) : imm & 3;
    switch (mode) {
    case 0: return nearbyint(x);
    case 1: return floor(x);
    case 2: return ceil(x);
    default: return trunc(x);
    }
}

/* cmpps/cmpss predicates (0-31). */
static int fcompare(double a, double b, int predicate) {
    const int unordered = a != a || b != b;
    int r;
    switch (predicate & 7) {
    case 0: r = !unordered && a == b; break;
    case 1: r = !unordered && a < b; break;
    case 2: r = !unordered && a <= b; break;
    case 3: r = unordered; break;
    case 4: r = unordered || a != b; break;
    case 5: r = unordered || !(a < b); break;
    case 6: r = unordered || !(a <= b); break;
    default: r = !unordered; break;
    }
    if (predicate & 8) { /* the _UQ/_OQ/... variants of predicates 8-15: */
        switch (predicate & 7) {
        case 0: r = unordered || a == b; break;  /* EQ_UQ */
        case 1: r = unordered || !(a >= b); break; /* NGE */
        case 2: r = unordered || !(a > b); break;  /* NGT */
        case 3: r = 0; break;                       /* FALSE */
        case 4: r = !unordered && a != b; break;   /* NEQ_OQ */
        case 5: r = !unordered && a >= b; break;   /* GE */
        case 6: r = !unordered && a > b; break;    /* GT */
        default: r = 1; break;                      /* TRUE */
        }
    }
    return r;
}

static void comis(BbCpu *cpu, double a, double b) {
    uint64_t f = cpu->flags & ~(uint64_t)F_ARITH;
    if (a != a || b != b) f |= F_ZF | F_PF | F_CF;
    else if (a < b) f |= F_CF;
    else if (a == b) f |= F_ZF;
    cpu->flags = f;
}

static uint16_t f32_to_f16(float value) {
    uint32_t x;
    memcpy(&x, &value, 4);
    const uint32_t sign = (x >> 16) & 0x8000;
    int32_t exp = (int32_t)((x >> 23) & 0xff) - 127 + 15;
    uint32_t mant = x & 0x7fffff;
    if (((x >> 23) & 0xff) == 0xff) return (uint16_t)(sign | 0x7c00 | (mant ? 0x200 : 0));
    if (exp >= 31) return (uint16_t)(sign | 0x7c00);
    if (exp <= 0) {
        if (exp < -10) return (uint16_t)sign;
        mant |= 0x800000;
        const int shift = 14 - exp;
        uint32_t half = mant >> shift;
        const uint32_t rest = mant & ((1u << shift) - 1), mid = 1u << (shift - 1);
        if (rest > mid || (rest == mid && (half & 1))) ++half;
        return (uint16_t)(sign | half);
    }
    uint32_t half = ((uint32_t)exp << 10) | (mant >> 13);
    const uint32_t rest = mant & 0x1fff;
    if (rest > 0x1000 || (rest == 0x1000 && (half & 1))) ++half;
    return (uint16_t)(sign | half);
}
static float f16_to_f32(uint16_t h) {
    const uint32_t sign = (uint32_t)(h & 0x8000) << 16;
    uint32_t exp = (h >> 10) & 0x1f, mant = h & 0x3ff, x;
    if (exp == 0) {
        if (!mant) x = sign;
        else {
            exp = 127 - 15 + 1;
            while (!(mant & 0x400)) { mant <<= 1; --exp; }
            x = sign | (exp << 23) | ((mant & 0x3ff) << 13);
        }
    } else if (exp == 31) x = sign | 0x7f800000 | (mant << 13);
    else x = sign | ((exp - 15 + 127) << 23) | (mant << 13);
    float f;
    memcpy(&f, &x, 4);
    return f;
}

static int16_t sat16(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }
static uint16_t usat16(int32_t v) { return v > 65535 ? 65535 : v < 0 ? 0 : (uint16_t)v; }
static int8_t sat8(int32_t v) { return v > 127 ? 127 : v < -128 ? -128 : (int8_t)v; }
static uint8_t usat8(int32_t v) { return v > 255 ? 255 : v < 0 ? 0 : (uint8_t)v; }

/* Packed/scalar floating-point binary operations. kind: 0 add 1 sub 2 mul 3 div 4 min 5 max. */
static void fbinary(BbCpu *cpu, const BbInsn *in, int kind, int dbl, int scalar) {
    const Ops o = ops3(in);
    BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2), r = a;
    const int w = width(in);
    const int lanes = scalar ? 1 : w / (dbl ? 8 : 4);
    for (int i = 0; i < lanes; ++i) {
        if (dbl) {
            const double x = a.fd[i], y = b.fd[i];
            r.fd[i] = kind == 0 ? x + y : kind == 1 ? x - y : kind == 2 ? x * y : kind == 3 ? x / y
                    : kind == 4 ? dmin_x86(x, y) : dmax_x86(x, y);
        } else {
            const float x = a.f[i], y = b.f[i];
            r.f[i] = kind == 0 ? x + y : kind == 1 ? x - y : kind == 2 ? x * y : kind == 3 ? x / y
                   : kind == 4 ? fmin_x86(x, y) : fmax_x86(x, y);
        }
    }
    vwrite(cpu, in, o.dst, &r, scalar ? 16 : w);
}

static void bitwise(BbCpu *cpu, const BbInsn *in, int kind) {
    const Ops o = ops3(in);
    const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
    BbVec r;
    for (int i = 0; i < 4; ++i)
        r.q[i] = kind == 0 ? a.q[i] & b.q[i] : kind == 1 ? ~a.q[i] & b.q[i]
               : kind == 2 ? a.q[i] | b.q[i] : a.q[i] ^ b.q[i];
    vwrite(cpu, in, o.dst, &r, width(in));
}

/* Packed integer arithmetic by element size. */
static void ibinary(BbCpu *cpu, const BbInsn *in) {
    const Ops o = ops3(in);
    const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
    BbVec r;
    memset(&r, 0, sizeof(r));
    const int w = width(in), m = in->mnemonic;
    switch (m) {
#define LANES(T, field, expr) for (int i = 0; i < w / (int)sizeof(T); ++i) r.field[i] = (T)(expr)
    case M(PADDB): case M(VPADDB): LANES(uint8_t, b, a.b[i] + b.b[i]); break;
    case M(PADDW): case M(VPADDW): LANES(uint16_t, w, a.w[i] + b.w[i]); break;
    case M(PADDD): case M(VPADDD): LANES(uint32_t, d, a.d[i] + b.d[i]); break;
    case M(PADDQ): case M(VPADDQ): LANES(uint64_t, q, a.q[i] + b.q[i]); break;
    case M(PSUBB): case M(VPSUBB): LANES(uint8_t, b, a.b[i] - b.b[i]); break;
    case M(PSUBW): case M(VPSUBW): LANES(uint16_t, w, a.w[i] - b.w[i]); break;
    case M(PSUBD): case M(VPSUBD): LANES(uint32_t, d, a.d[i] - b.d[i]); break;
    case M(PSUBQ): case M(VPSUBQ): LANES(uint64_t, q, a.q[i] - b.q[i]); break;
    case M(PADDSW): case M(VPADDSW): LANES(int16_t, sw, sat16(a.sw[i] + b.sw[i])); break;
    case M(PADDUSW): case M(VPADDUSW): LANES(uint16_t, w, usat16(a.w[i] + b.w[i])); break;
    case M(PSUBSW): case M(VPSUBSW): LANES(int16_t, sw, sat16(a.sw[i] - b.sw[i])); break;
    case M(PSUBUSW): case M(VPSUBUSW): LANES(uint16_t, w, usat16(a.w[i] - b.w[i])); break;
    case M(PADDSB): case M(VPADDSB): LANES(int8_t, sb, sat8(a.sb[i] + b.sb[i])); break;
    case M(PADDUSB): case M(VPADDUSB): LANES(uint8_t, b, usat8(a.b[i] + b.b[i])); break;
    case M(PSUBSB): case M(VPSUBSB): LANES(int8_t, sb, sat8(a.sb[i] - b.sb[i])); break;
    case M(PSUBUSB): case M(VPSUBUSB): LANES(uint8_t, b, usat8(a.b[i] - b.b[i])); break;
    case M(PMULLW): case M(VPMULLW): LANES(uint16_t, w, a.sw[i] * b.sw[i]); break;
    case M(PMULHW): case M(VPMULHW): LANES(uint16_t, w, (a.sw[i] * b.sw[i]) >> 16); break;
    case M(PMULHUW): case M(VPMULHUW): LANES(uint16_t, w, ((uint32_t)a.w[i] * b.w[i]) >> 16); break;
    case M(PMULLD): case M(VPMULLD): LANES(uint32_t, d, (uint32_t)((int64_t)a.sd[i] * b.sd[i])); break;
    case M(PMULUDQ): case M(VPMULUDQ): LANES(uint64_t, q, (uint64_t)a.d[i * 2] * b.d[i * 2]); break;
    case M(PMULDQ): case M(VPMULDQ): LANES(uint64_t, q, (uint64_t)((int64_t)a.sd[i * 2] * b.sd[i * 2])); break;
    case M(PMADDWD): case M(VPMADDWD): LANES(uint32_t, d, a.sw[i * 2] * b.sw[i * 2] + a.sw[i * 2 + 1] * b.sw[i * 2 + 1]); break;
    case M(PAVGB): case M(VPAVGB): LANES(uint8_t, b, (a.b[i] + b.b[i] + 1) >> 1); break;
    case M(PAVGW): case M(VPAVGW): LANES(uint16_t, w, (a.w[i] + b.w[i] + 1) >> 1); break;
    case M(PMINSD): case M(VPMINSD): LANES(int32_t, sd, a.sd[i] < b.sd[i] ? a.sd[i] : b.sd[i]); break;
    case M(PMAXSD): case M(VPMAXSD): LANES(int32_t, sd, a.sd[i] > b.sd[i] ? a.sd[i] : b.sd[i]); break;
    case M(PMINUD): case M(VPMINUD): LANES(uint32_t, d, a.d[i] < b.d[i] ? a.d[i] : b.d[i]); break;
    case M(PMAXUD): case M(VPMAXUD): LANES(uint32_t, d, a.d[i] > b.d[i] ? a.d[i] : b.d[i]); break;
    case M(PMINSW): case M(VPMINSW): LANES(int16_t, sw, a.sw[i] < b.sw[i] ? a.sw[i] : b.sw[i]); break;
    case M(PMAXSW): case M(VPMAXSW): LANES(int16_t, sw, a.sw[i] > b.sw[i] ? a.sw[i] : b.sw[i]); break;
    case M(PMINUW): case M(VPMINUW): LANES(uint16_t, w, a.w[i] < b.w[i] ? a.w[i] : b.w[i]); break;
    case M(PMAXUW): case M(VPMAXUW): LANES(uint16_t, w, a.w[i] > b.w[i] ? a.w[i] : b.w[i]); break;
    case M(PMINUB): case M(VPMINUB): LANES(uint8_t, b, a.b[i] < b.b[i] ? a.b[i] : b.b[i]); break;
    case M(PMAXUB): case M(VPMAXUB): LANES(uint8_t, b, a.b[i] > b.b[i] ? a.b[i] : b.b[i]); break;
    case M(PMINSB): case M(VPMINSB): LANES(int8_t, sb, a.sb[i] < b.sb[i] ? a.sb[i] : b.sb[i]); break;
    case M(PMAXSB): case M(VPMAXSB): LANES(int8_t, sb, a.sb[i] > b.sb[i] ? a.sb[i] : b.sb[i]); break;
    case M(PCMPEQB): case M(VPCMPEQB): LANES(uint8_t, b, a.b[i] == b.b[i] ? 0xff : 0); break;
    case M(PCMPEQW): case M(VPCMPEQW): LANES(uint16_t, w, a.w[i] == b.w[i] ? 0xffff : 0); break;
    case M(PCMPEQD): case M(VPCMPEQD): LANES(uint32_t, d, a.d[i] == b.d[i] ? ~0u : 0); break;
    case M(PCMPEQQ): case M(VPCMPEQQ): LANES(uint64_t, q, a.q[i] == b.q[i] ? ~UINT64_C(0) : 0); break;
    case M(PCMPGTB): case M(VPCMPGTB): LANES(uint8_t, b, a.sb[i] > b.sb[i] ? 0xff : 0); break;
    case M(PCMPGTW): case M(VPCMPGTW): LANES(uint16_t, w, a.sw[i] > b.sw[i] ? 0xffff : 0); break;
    case M(PCMPGTD): case M(VPCMPGTD): LANES(uint32_t, d, a.sd[i] > b.sd[i] ? ~0u : 0); break;
    case M(PCMPGTQ): case M(VPCMPGTQ): LANES(uint64_t, q, a.sq[i] > b.sq[i] ? ~UINT64_C(0) : 0); break;
    case M(PSADBW): case M(VPSADBW):
        for (int i = 0; i < w / 8; ++i) {
            uint32_t s = 0;
            for (int j = 0; j < 8; ++j) s += (uint32_t)abs(a.b[i * 8 + j] - b.b[i * 8 + j]);
            r.q[i] = s;
        }
        break;
    case M(PHADDD): case M(VPHADDD):
        for (int h = 0; h < w / 16; ++h) {
            r.d[h * 4 + 0] = a.d[h * 4] + a.d[h * 4 + 1]; r.d[h * 4 + 1] = a.d[h * 4 + 2] + a.d[h * 4 + 3];
            r.d[h * 4 + 2] = b.d[h * 4] + b.d[h * 4 + 1]; r.d[h * 4 + 3] = b.d[h * 4 + 2] + b.d[h * 4 + 3];
        }
        break;
    case M(PHADDW): case M(VPHADDW):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 4; ++i) {
                r.w[h * 8 + i] = (uint16_t)(a.w[h * 8 + i * 2] + a.w[h * 8 + i * 2 + 1]);
                r.w[h * 8 + 4 + i] = (uint16_t)(b.w[h * 8 + i * 2] + b.w[h * 8 + i * 2 + 1]);
            }
        break;
    case M(PACKSSDW): case M(VPACKSSDW):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 4; ++i) {
                r.sw[h * 8 + i] = sat16(a.sd[h * 4 + i]);
                r.sw[h * 8 + 4 + i] = sat16(b.sd[h * 4 + i]);
            }
        break;
    case M(PACKUSDW): case M(VPACKUSDW):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 4; ++i) {
                r.w[h * 8 + i] = usat16(a.sd[h * 4 + i]);
                r.w[h * 8 + 4 + i] = usat16(b.sd[h * 4 + i]);
            }
        break;
    case M(PACKSSWB): case M(VPACKSSWB):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 8; ++i) {
                r.sb[h * 16 + i] = sat8(a.sw[h * 8 + i]);
                r.sb[h * 16 + 8 + i] = sat8(b.sw[h * 8 + i]);
            }
        break;
    case M(PACKUSWB): case M(VPACKUSWB):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 8; ++i) {
                r.b[h * 16 + i] = usat8(a.sw[h * 8 + i]);
                r.b[h * 16 + 8 + i] = usat8(b.sw[h * 8 + i]);
            }
        break;
    case M(PUNPCKLBW): case M(VPUNPCKLBW): case M(PUNPCKHBW): case M(VPUNPCKHBW):
    case M(PUNPCKLWD): case M(VPUNPCKLWD): case M(PUNPCKHWD): case M(VPUNPCKHWD):
    case M(PUNPCKLDQ): case M(VPUNPCKLDQ): case M(PUNPCKHDQ): case M(VPUNPCKHDQ):
    case M(PUNPCKLQDQ): case M(VPUNPCKLQDQ): case M(PUNPCKHQDQ): case M(VPUNPCKHQDQ): {
        const int high = m == M(PUNPCKHBW) || m == M(VPUNPCKHBW) || m == M(PUNPCKHWD) ||
                         m == M(VPUNPCKHWD) || m == M(PUNPCKHDQ) || m == M(VPUNPCKHDQ) ||
                         m == M(PUNPCKHQDQ) || m == M(VPUNPCKHQDQ);
        const int es = (m == M(PUNPCKLBW) || m == M(VPUNPCKLBW) || m == M(PUNPCKHBW) || m == M(VPUNPCKHBW)) ? 1
                     : (m == M(PUNPCKLWD) || m == M(VPUNPCKLWD) || m == M(PUNPCKHWD) || m == M(VPUNPCKHWD)) ? 2
                     : (m == M(PUNPCKLDQ) || m == M(VPUNPCKLDQ) || m == M(PUNPCKHDQ) || m == M(VPUNPCKHDQ)) ? 4 : 8;
        const int per = 16 / es;
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < per / 2; ++i) {
                const int src = h * 16 + (high ? 8 : 0) + i * es;
                memcpy(r.b + h * 16 + i * 2 * es, a.b + src, es);
                memcpy(r.b + h * 16 + i * 2 * es + es, b.b + src, es);
            }
        break;
    }
    case M(PSHUFB): case M(VPSHUFB):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 16; ++i) {
                const uint8_t c = b.b[h * 16 + i];
                r.b[h * 16 + i] = (c & 0x80) ? 0 : a.b[h * 16 + (c & 15)];
            }
        break;
    case M(PABSD): case M(VPABSD): LANES(uint32_t, d, b.sd[i] < 0 ? -(uint32_t)b.sd[i] : (uint32_t)b.sd[i]); break;
    case M(PABSW): case M(VPABSW): LANES(uint16_t, w, b.sw[i] < 0 ? -b.sw[i] : b.sw[i]); break;
    case M(PABSB): case M(VPABSB): LANES(uint8_t, b, b.sb[i] < 0 ? -b.sb[i] : b.sb[i]); break;
#undef LANES
    default:
        return;
    }
    vwrite(cpu, in, o.dst, &r, w);
}

static int is_ibinary(int m) {
    switch (m) {
    case M(PADDB): case M(VPADDB): case M(PADDW): case M(VPADDW): case M(PADDD): case M(VPADDD):
    case M(PADDQ): case M(VPADDQ): case M(PSUBB): case M(VPSUBB): case M(PSUBW): case M(VPSUBW):
    case M(PSUBD): case M(VPSUBD): case M(PSUBQ): case M(VPSUBQ): case M(PADDSW): case M(VPADDSW):
    case M(PADDUSW): case M(VPADDUSW): case M(PSUBSW): case M(VPSUBSW): case M(PSUBUSW):
    case M(VPSUBUSW): case M(PADDSB): case M(VPADDSB): case M(PADDUSB): case M(VPADDUSB):
    case M(PSUBSB): case M(VPSUBSB): case M(PSUBUSB): case M(VPSUBUSB): case M(PMULLW):
    case M(VPMULLW): case M(PMULHW): case M(VPMULHW): case M(PMULHUW): case M(VPMULHUW):
    case M(PMULLD): case M(VPMULLD): case M(PMULUDQ): case M(VPMULUDQ): case M(PMULDQ):
    case M(VPMULDQ): case M(PMADDWD): case M(VPMADDWD): case M(PAVGB): case M(VPAVGB):
    case M(PAVGW): case M(VPAVGW): case M(PMINSD): case M(VPMINSD): case M(PMAXSD):
    case M(VPMAXSD): case M(PMINUD): case M(VPMINUD): case M(PMAXUD): case M(VPMAXUD):
    case M(PMINSW): case M(VPMINSW): case M(PMAXSW): case M(VPMAXSW): case M(PMINUW):
    case M(VPMINUW): case M(PMAXUW): case M(VPMAXUW): case M(PMINUB): case M(VPMINUB):
    case M(PMAXUB): case M(VPMAXUB): case M(PMINSB): case M(VPMINSB): case M(PMAXSB):
    case M(VPMAXSB): case M(PCMPEQB): case M(VPCMPEQB): case M(PCMPEQW): case M(VPCMPEQW):
    case M(PCMPEQD): case M(VPCMPEQD): case M(PCMPEQQ): case M(VPCMPEQQ): case M(PCMPGTB):
    case M(VPCMPGTB): case M(PCMPGTW): case M(VPCMPGTW): case M(PCMPGTD): case M(VPCMPGTD):
    case M(PCMPGTQ): case M(VPCMPGTQ): case M(PSADBW): case M(VPSADBW): case M(PHADDD):
    case M(VPHADDD): case M(PHADDW): case M(VPHADDW): case M(PACKSSDW): case M(VPACKSSDW):
    case M(PACKUSDW): case M(VPACKUSDW): case M(PACKSSWB): case M(VPACKSSWB): case M(PACKUSWB):
    case M(VPACKUSWB): case M(PUNPCKLBW): case M(VPUNPCKLBW): case M(PUNPCKHBW):
    case M(VPUNPCKHBW): case M(PUNPCKLWD): case M(VPUNPCKLWD): case M(PUNPCKHWD):
    case M(VPUNPCKHWD): case M(PUNPCKLDQ): case M(VPUNPCKLDQ): case M(PUNPCKHDQ):
    case M(VPUNPCKHDQ): case M(PUNPCKLQDQ): case M(VPUNPCKLQDQ): case M(PUNPCKHQDQ):
    case M(VPUNPCKHQDQ): case M(PSHUFB): case M(VPSHUFB): case M(PABSD): case M(VPABSD):
    case M(PABSW): case M(VPABSW): case M(PABSB): case M(VPABSB):
        return 1;
    default:
        return 0;
    }
}

/* psll/psrl/psra by an immediate or by the low quadword of a vector; pslldq/psrldq. */
static void vshift(BbCpu *cpu, const BbInsn *in) {
    const int m = in->mnemonic, w = width(in);
    const BbOp *dst = &in->op[0];
    const BbOp *src = in->vex ? &in->op[1] : &in->op[0];
    const BbOp *cnt = in->vex ? &in->op[2] : &in->op[1];
    const BbVec a = vread(cpu, in, src);
    const uint64_t count = cnt->type == OP_IMM ? (uint64_t)cnt->disp & 0xff : vread(cpu, in, cnt).q[0];
    BbVec r;
    memset(&r, 0, sizeof(r));
    switch (m) {
    case M(PSLLW): case M(VPSLLW): for (int i = 0; i < w / 2; ++i) r.w[i] = count > 15 ? 0 : (uint16_t)(a.w[i] << count); break;
    case M(PSLLD): case M(VPSLLD): for (int i = 0; i < w / 4; ++i) r.d[i] = count > 31 ? 0 : a.d[i] << count; break;
    case M(PSLLQ): case M(VPSLLQ): for (int i = 0; i < w / 8; ++i) r.q[i] = count > 63 ? 0 : a.q[i] << count; break;
    case M(PSRLW): case M(VPSRLW): for (int i = 0; i < w / 2; ++i) r.w[i] = count > 15 ? 0 : (uint16_t)(a.w[i] >> count); break;
    case M(PSRLD): case M(VPSRLD): for (int i = 0; i < w / 4; ++i) r.d[i] = count > 31 ? 0 : a.d[i] >> count; break;
    case M(PSRLQ): case M(VPSRLQ): for (int i = 0; i < w / 8; ++i) r.q[i] = count > 63 ? 0 : a.q[i] >> count; break;
    case M(PSRAW): case M(VPSRAW): for (int i = 0; i < w / 2; ++i) r.sw[i] = (int16_t)(a.sw[i] >> (count > 15 ? 15 : count)); break;
    case M(PSRAD): case M(VPSRAD): for (int i = 0; i < w / 4; ++i) r.sd[i] = a.sd[i] >> (count > 31 ? 31 : count); break;
    case M(PSLLDQ): case M(VPSLLDQ):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 16; ++i) r.b[h * 16 + i] = (uint64_t)i >= count ? a.b[h * 16 + i - count] : 0;
        break;
    case M(PSRLDQ): case M(VPSRLDQ):
        for (int h = 0; h < w / 16; ++h)
            for (int i = 0; i < 16; ++i) r.b[h * 16 + i] = i + count < 16 ? a.b[h * 16 + i + count] : 0;
        break;
    default: bbcpu_fatal(cpu, in, "vector shift");
    }
    vwrite(cpu, in, dst, &r, w);
}

static int is_vshift(int m) {
    switch (m) {
    case M(PSLLW): case M(VPSLLW): case M(PSLLD): case M(VPSLLD): case M(PSLLQ): case M(VPSLLQ):
    case M(PSRLW): case M(VPSRLW): case M(PSRLD): case M(VPSRLD): case M(PSRLQ): case M(VPSRLQ):
    case M(PSRAW): case M(VPSRAW): case M(PSRAD): case M(VPSRAD): case M(PSLLDQ): case M(VPSLLDQ):
    case M(PSRLDQ): case M(VPSRLDQ):
        return 1;
    default:
        return 0;
    }
}

/* movss/movsd (SSE): register forms merge one lane, loads zero the rest. */
static void move_scalar(BbCpu *cpu, const BbInsn *in, int bytes) {
    const BbOp *dst = &in->op[0];
    if (dst->type == OP_MEM) {
        const BbVec v = vread(cpu, in, &in->op[in->count - 1]);
        bb_fence_store();
        memcpy((void *)bbcpu_addr(cpu, in, dst), v.b, bytes);
        return;
    }
    BbVec r;
    if (in->count == 3) { /* VEX register form: lane from src2, the rest from src1 */
        r = vread(cpu, in, &in->op[1]);
        const BbVec b = vread(cpu, in, &in->op[2]);
        memcpy(r.b, b.b, bytes);
        vwrite(cpu, in, dst, &r, 16);
        return;
    }
    const BbOp *src = &in->op[1];
    if (src->type == OP_MEM) {
        memset(&r, 0, sizeof(r));
        memcpy(r.b, (const void *)bbcpu_addr(cpu, in, src), bytes);
        bb_fence_load();
        if (in->vex) vwrite(cpu, in, dst, &r, 16);
        else memcpy(cpu->v[dst->reg].b, r.b, 16);
        return;
    }
    r = cpu->v[dst->reg];
    memcpy(r.b, cpu->v[src->reg].b, bytes);
    vwrite(cpu, in, dst, &r, 16);
}

int bbcpu_exec_vec(BbCpu *cpu, const BbInsn *in) {
    const int m = in->mnemonic;
    if (is_ibinary(m)) { ibinary(cpu, in); return 1; }
    if (is_vshift(m)) { vshift(cpu, in); return 1; }
    switch (m) {
    /* ---- moves ---- */
    case M(MOVAPS): case M(MOVUPS): case M(MOVAPD): case M(MOVUPD): case M(MOVDQA): case M(MOVDQU):
    case M(VMOVAPS): case M(VMOVUPS): case M(VMOVAPD): case M(VMOVUPD): case M(VMOVDQA):
    case M(VMOVDQU): case M(LDDQU): case M(VLDDQU): case M(MOVNTPS): case M(VMOVNTPS):
    case M(MOVNTDQ): case M(VMOVNTDQ): case M(MOVNTPD): case M(VMOVNTPD): case M(MOVNTDQA):
    case M(VMOVNTDQA): {
        const BbVec v = vread(cpu, in, &in->op[1]);
        vwrite(cpu, in, &in->op[0], &v, in->op[0].size);
        return 1;
    }
    case M(MOVSS): case M(VMOVSS): move_scalar(cpu, in, 4); return 1;
    case M(MOVSD): case M(VMOVSD): move_scalar(cpu, in, 8); return 1;
    case M(MOVD): case M(VMOVD): case M(MOVQ): case M(VMOVQ): {
        const BbOp *dst = &in->op[0], *src = &in->op[1];
        const int bytes = (m == M(MOVD) || m == M(VMOVD)) ? 4 : 8;
        if (dst->type == OP_REG && dst->kind == RK_VEC) {
            BbVec r;
            memset(&r, 0, sizeof(r));
            const BbVec s = vread(cpu, in, src);
            memcpy(r.b, s.b, bytes);
            memcpy(cpu->v[dst->reg].b, r.b, 16);
            if (in->vex) memset(cpu->v[dst->reg].b + 16, 0, 16);
        } else {
            const BbVec s = vread(cpu, in, src);
            if (dst->type == OP_MEM) { bb_fence_store(); memcpy((void *)bbcpu_addr(cpu, in, dst), s.b, bytes); }
            else bbcpu_write(cpu, in, dst, bytes == 4 ? s.d[0] : s.q[0]);
        }
        return 1;
    }
    case M(MOVHLPS): case M(VMOVHLPS): case M(MOVLHPS): case M(VMOVLHPS): {
        const Ops o = ops3(in);
        BbVec a = vread(cpu, in, o.s1);
        const BbVec b = vread(cpu, in, o.s2);
        if (m == M(MOVHLPS) || m == M(VMOVHLPS)) a.q[0] = b.q[1];
        else a.q[1] = b.q[0];
        vwrite(cpu, in, o.dst, &a, 16);
        return 1;
    }
    case M(MOVLPS): case M(VMOVLPS): case M(MOVLPD): case M(VMOVLPD): case M(MOVHPS):
    case M(VMOVHPS): case M(MOVHPD): case M(VMOVHPD): {
        const int high = m == M(MOVHPS) || m == M(VMOVHPS) || m == M(MOVHPD) || m == M(VMOVHPD);
        const BbOp *dst = &in->op[0];
        if (dst->type == OP_MEM) {
            const BbVec s = vread(cpu, in, &in->op[1]);
            bb_store(bbcpu_addr(cpu, in, dst), 8, s.q[high]);
            return 1;
        }
        BbVec r = vread(cpu, in, in->count == 3 ? &in->op[1] : dst);
        r.q[high] = bb_load(bbcpu_addr(cpu, in, &in->op[in->count - 1]), 8);
        vwrite(cpu, in, dst, &r, 16);
        return 1;
    }
    case M(MOVMSKPS): case M(VMOVMSKPS): case M(MOVMSKPD): case M(VMOVMSKPD): case M(PMOVMSKB):
    case M(VPMOVMSKB): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        const int w = width(in);
        uint64_t mask = 0;
        if (m == M(MOVMSKPS) || m == M(VMOVMSKPS)) for (int i = 0; i < w / 4; ++i) mask |= (uint64_t)(s.d[i] >> 31) << i;
        else if (m == M(MOVMSKPD) || m == M(VMOVMSKPD)) for (int i = 0; i < w / 8; ++i) mask |= (s.q[i] >> 63) << i;
        else for (int i = 0; i < w; ++i) mask |= (uint64_t)(s.b[i] >> 7) << i;
        bbcpu_write(cpu, in, &in->op[0], mask);
        return 1;
    }
    case M(VBROADCASTSS): case M(VBROADCASTSD): case M(VBROADCASTF128): case M(VPBROADCASTD):
    case M(VPBROADCASTQ): case M(VPBROADCASTB): case M(VPBROADCASTW): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        const int es = (m == M(VBROADCASTSS) || m == M(VPBROADCASTD)) ? 4
                     : (m == M(VBROADCASTSD) || m == M(VPBROADCASTQ)) ? 8
                     : m == M(VPBROADCASTB) ? 1 : m == M(VPBROADCASTW) ? 2 : 16;
        BbVec r;
        const int w = width(in);
        for (int i = 0; i < w; i += es) memcpy(r.b + i, s.b, es);
        vwrite(cpu, in, &in->op[0], &r, w);
        return 1;
    }
    case M(VINSERTF128): case M(VINSERTI128): {
        BbVec r = vread(cpu, in, &in->op[1]);
        const BbVec s = vread(cpu, in, &in->op[2]);
        memcpy(r.b + ((in->op[3].disp & 1) ? 16 : 0), s.b, 16);
        vwrite(cpu, in, &in->op[0], &r, 32);
        return 1;
    }
    case M(VEXTRACTF128): case M(VEXTRACTI128): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        memcpy(r.b, s.b + ((in->op[2].disp & 1) ? 16 : 0), 16);
        vwrite(cpu, in, &in->op[0], &r, 16);
        return 1;
    }
    case M(VPERM2F128): case M(VPERM2I128): {
        const BbVec a = vread(cpu, in, &in->op[1]), b = vread(cpu, in, &in->op[2]);
        const int imm = (int)in->op[3].disp;
        BbVec r;
        for (int h = 0; h < 2; ++h) {
            const int sel = (imm >> (h * 4)) & 0xf;
            if (sel & 8) memset(r.b + h * 16, 0, 16);
            else memcpy(r.b + h * 16, ((sel & 2) ? b.b : a.b) + ((sel & 1) ? 16 : 0), 16);
        }
        vwrite(cpu, in, &in->op[0], &r, 32);
        return 1;
    }
    case M(MOVSHDUP): case M(VMOVSHDUP): case M(MOVSLDUP): case M(VMOVSLDUP): case M(MOVDDUP):
    case M(VMOVDDUP): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int w = width(in);
        if (m == M(MOVDDUP) || m == M(VMOVDDUP)) for (int i = 0; i < w / 8; i += 2) r.q[i] = r.q[i + 1] = s.q[i];
        else {
            const int odd = m == M(MOVSHDUP) || m == M(VMOVSHDUP);
            for (int i = 0; i < w / 4; i += 2) r.d[i] = r.d[i + 1] = s.d[i + odd];
        }
        vwrite(cpu, in, &in->op[0], &r, w);
        return 1;
    }
    case M(MASKMOVDQU): case M(VMASKMOVDQU): { /* bytes of op0 where op1's byte has its top bit, to [rdi] */
        const BbVec v = vread(cpu, in, &in->op[0]), mask = vread(cpu, in, &in->op[1]);
        uint8_t *dst = (uint8_t *)cpu->r[RDI];
        for (int i = 0; i < 16; ++i) if (mask.b[i] & 0x80) dst[i] = v.b[i];
        return 1;
    }
    case M(VMASKMOVPS): case M(VMASKMOVPD): {
        const int es = m == M(VMASKMOVPS) ? 4 : 8, w = width(in);
        if (in->op[0].type == OP_MEM) { /* store: op1 mask, op2 data */
            const BbVec mask = vread(cpu, in, &in->op[1]), v = vread(cpu, in, &in->op[2]);
            uint8_t *dst = (uint8_t *)bbcpu_addr(cpu, in, &in->op[0]);
            for (int i = 0; i < w / es; ++i) if (mask.b[i * es + es - 1] & 0x80) memcpy(dst + i * es, v.b + i * es, es);
        } else { /* load: op1 mask, op2 memory */
            const BbVec mask = vread(cpu, in, &in->op[1]);
            const uint8_t *src = (const uint8_t *)bbcpu_addr(cpu, in, &in->op[2]);
            BbVec r;
            memset(&r, 0, sizeof(r));
            for (int i = 0; i < w / es; ++i) if (mask.b[i * es + es - 1] & 0x80) memcpy(r.b + i * es, src + i * es, es);
            vwrite(cpu, in, &in->op[0], &r, w);
        }
        return 1;
    }
    case M(VZEROUPPER): for (int i = 0; i < 16; ++i) memset(cpu->v[i].b + 16, 0, 16); return 1;
    case M(VZEROALL): memset(cpu->v, 0, sizeof(cpu->v)); return 1;
    /* ---- floating-point arithmetic ---- */
    case M(ADDPS): case M(VADDPS): fbinary(cpu, in, 0, 0, 0); return 1;
    case M(SUBPS): case M(VSUBPS): fbinary(cpu, in, 1, 0, 0); return 1;
    case M(MULPS): case M(VMULPS): fbinary(cpu, in, 2, 0, 0); return 1;
    case M(DIVPS): case M(VDIVPS): fbinary(cpu, in, 3, 0, 0); return 1;
    case M(MINPS): case M(VMINPS): fbinary(cpu, in, 4, 0, 0); return 1;
    case M(MAXPS): case M(VMAXPS): fbinary(cpu, in, 5, 0, 0); return 1;
    case M(ADDPD): case M(VADDPD): fbinary(cpu, in, 0, 1, 0); return 1;
    case M(SUBPD): case M(VSUBPD): fbinary(cpu, in, 1, 1, 0); return 1;
    case M(MULPD): case M(VMULPD): fbinary(cpu, in, 2, 1, 0); return 1;
    case M(DIVPD): case M(VDIVPD): fbinary(cpu, in, 3, 1, 0); return 1;
    case M(MINPD): case M(VMINPD): fbinary(cpu, in, 4, 1, 0); return 1;
    case M(MAXPD): case M(VMAXPD): fbinary(cpu, in, 5, 1, 0); return 1;
    case M(ADDSS): case M(VADDSS): fbinary(cpu, in, 0, 0, 1); return 1;
    case M(SUBSS): case M(VSUBSS): fbinary(cpu, in, 1, 0, 1); return 1;
    case M(MULSS): case M(VMULSS): fbinary(cpu, in, 2, 0, 1); return 1;
    case M(DIVSS): case M(VDIVSS): fbinary(cpu, in, 3, 0, 1); return 1;
    case M(MINSS): case M(VMINSS): fbinary(cpu, in, 4, 0, 1); return 1;
    case M(MAXSS): case M(VMAXSS): fbinary(cpu, in, 5, 0, 1); return 1;
    case M(ADDSD): case M(VADDSD): fbinary(cpu, in, 0, 1, 1); return 1;
    case M(SUBSD): case M(VSUBSD): fbinary(cpu, in, 1, 1, 1); return 1;
    case M(MULSD): case M(VMULSD): fbinary(cpu, in, 2, 1, 1); return 1;
    case M(DIVSD): case M(VDIVSD): fbinary(cpu, in, 3, 1, 1); return 1;
    case M(MINSD): case M(VMINSD): fbinary(cpu, in, 4, 1, 1); return 1;
    case M(MAXSD): case M(VMAXSD): fbinary(cpu, in, 5, 1, 1); return 1;
    case M(ANDPS): case M(VANDPS): case M(ANDPD): case M(VANDPD): case M(PAND): case M(VPAND):
        bitwise(cpu, in, 0); return 1;
    case M(ANDNPS): case M(VANDNPS): case M(ANDNPD): case M(VANDNPD): case M(PANDN): case M(VPANDN):
        bitwise(cpu, in, 1); return 1;
    case M(ORPS): case M(VORPS): case M(ORPD): case M(VORPD): case M(POR): case M(VPOR):
        bitwise(cpu, in, 2); return 1;
    case M(XORPS): case M(VXORPS): case M(XORPD): case M(VXORPD): case M(PXOR): case M(VPXOR):
        bitwise(cpu, in, 3); return 1;
    case M(SQRTPS): case M(VSQRTPS): case M(RSQRTPS): case M(VRSQRTPS): case M(RCPPS): case M(VRCPPS):
    case M(SQRTPD): case M(VSQRTPD): {
        const Ops o = ops1(in);
        const BbVec s = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int w = width(in);
        if (m == M(SQRTPD) || m == M(VSQRTPD)) for (int i = 0; i < w / 8; ++i) r.fd[i] = sqrt(s.fd[i]);
        else
            for (int i = 0; i < w / 4; ++i)
                r.f[i] = (m == M(SQRTPS) || m == M(VSQRTPS)) ? sqrtf(s.f[i])
                       : (m == M(RCPPS) || m == M(VRCPPS)) ? 1.0f / s.f[i] : 1.0f / sqrtf(s.f[i]);
        vwrite(cpu, in, o.dst, &r, w);
        return 1;
    }
    case M(SQRTSS): case M(VSQRTSS): case M(RSQRTSS): case M(VRSQRTSS): case M(RCPSS): case M(VRCPSS):
    case M(SQRTSD): case M(VSQRTSD): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const BbVec s = vread(cpu, in, o.s2);
        if (m == M(SQRTSD) || m == M(VSQRTSD)) r.fd[0] = sqrt(s.fd[0]);
        else r.f[0] = (m == M(SQRTSS) || m == M(VSQRTSS)) ? sqrtf(s.f[0])
                    : (m == M(RCPSS) || m == M(VRCPSS)) ? 1.0f / s.f[0] : 1.0f / sqrtf(s.f[0]);
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(HADDPS): case M(VHADDPS): case M(HSUBPS): case M(VHSUBPS): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int sub = m == M(HSUBPS) || m == M(VHSUBPS);
        for (int h = 0; h < width(in) / 16; ++h) {
            const float *x = a.f + h * 4, *y = b.f + h * 4;
            r.f[h * 4 + 0] = sub ? x[0] - x[1] : x[0] + x[1];
            r.f[h * 4 + 1] = sub ? x[2] - x[3] : x[2] + x[3];
            r.f[h * 4 + 2] = sub ? y[0] - y[1] : y[0] + y[1];
            r.f[h * 4 + 3] = sub ? y[2] - y[3] : y[2] + y[3];
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(HADDPD): case M(VHADDPD): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int h = 0; h < width(in) / 16; ++h) {
            r.fd[h * 2] = a.fd[h * 2] + a.fd[h * 2 + 1];
            r.fd[h * 2 + 1] = b.fd[h * 2] + b.fd[h * 2 + 1];
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(ADDSUBPS): case M(VADDSUBPS): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int i = 0; i < width(in) / 4; ++i) r.f[i] = (i & 1) ? a.f[i] + b.f[i] : a.f[i] - b.f[i];
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(DPPS): case M(VDPPS): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int h = 0; h < width(in) / 16; ++h) {
            /* Pairwise order of the hardware: (p0 + p1) + (p2 + p3). */
            float p[4];
            for (int i = 0; i < 4; ++i) p[i] = (imm & (0x10 << i)) ? a.f[h * 4 + i] * b.f[h * 4 + i] : 0.0f;
            const float sum = (p[0] + p[1]) + (p[2] + p[3]);
            for (int i = 0; i < 4; ++i) r.f[h * 4 + i] = (imm & (1 << i)) ? sum : 0.0f;
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(DPPD): case M(VDPPD): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        BbVec r;
        memset(&r, 0, sizeof(r));
        const double sum = ((imm & 0x10) ? a.fd[0] * b.fd[0] : 0.0) + ((imm & 0x20) ? a.fd[1] * b.fd[1] : 0.0);
        r.fd[0] = (imm & 1) ? sum : 0.0;
        r.fd[1] = (imm & 2) ? sum : 0.0;
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(ROUNDPS): case M(VROUNDPS): case M(ROUNDPD): case M(VROUNDPD): {
        const Ops o = ops1(in);
        const BbVec s = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int imm = (int)o.imm->disp;
        if (m == M(ROUNDPD) || m == M(VROUNDPD)) for (int i = 0; i < width(in) / 8; ++i) r.fd[i] = round_mode(s.fd[i], imm, cpu->mxcsr);
        else for (int i = 0; i < width(in) / 4; ++i) r.f[i] = (float)round_mode(s.f[i], imm, cpu->mxcsr);
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(ROUNDSS): case M(VROUNDSS): case M(ROUNDSD): case M(VROUNDSD): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const BbVec s = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        if (m == M(ROUNDSD) || m == M(VROUNDSD)) r.fd[0] = round_mode(s.fd[0], imm, cpu->mxcsr);
        else r.f[0] = (float)round_mode(s.f[0], imm, cpu->mxcsr);
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(VFMADD213PS): case M(VFMADD231PS): case M(VFMADD132PS): case M(VFMADD213SS):
    case M(VFMADD231SS): case M(VFMADD132SS): {
        BbVec d = vread(cpu, in, &in->op[0]);
        const BbVec b = vread(cpu, in, &in->op[1]), c = vread(cpu, in, &in->op[2]);
        const int scalar = m == M(VFMADD213SS) || m == M(VFMADD231SS) || m == M(VFMADD132SS);
        const int lanes = scalar ? 1 : width(in) / 4;
        for (int i = 0; i < lanes; ++i) {
            if (m == M(VFMADD213PS) || m == M(VFMADD213SS)) d.f[i] = fmaf(b.f[i], d.f[i], c.f[i]);
            else if (m == M(VFMADD231PS) || m == M(VFMADD231SS)) d.f[i] = fmaf(b.f[i], c.f[i], d.f[i]);
            else d.f[i] = fmaf(d.f[i], c.f[i], b.f[i]);
        }
        vwrite(cpu, in, &in->op[0], &d, scalar ? 16 : width(in));
        return 1;
    }
    /* ---- compares ---- */
    case M(CMPPS): case M(VCMPPS): case M(CMPPD): case M(VCMPPD): case M(CMPSS): case M(VCMPSS):
    case M(CMPSD): case M(VCMPSD): {
        const Ops o = ops3(in);
        BbVec a = vread(cpu, in, o.s1);
        const BbVec b = vread(cpu, in, o.s2);
        const int predicate = (int)o.imm->disp & 31;
        const int dbl = m == M(CMPPD) || m == M(VCMPPD) || m == M(CMPSD) || m == M(VCMPSD);
        const int scalar = m == M(CMPSS) || m == M(VCMPSS) || m == M(CMPSD) || m == M(VCMPSD);
        const int lanes = scalar ? 1 : width(in) / (dbl ? 8 : 4);
        for (int i = 0; i < lanes; ++i) {
            if (dbl) a.q[i] = fcompare(a.fd[i], b.fd[i], predicate) ? ~UINT64_C(0) : 0;
            else a.d[i] = fcompare(a.f[i], b.f[i], predicate) ? ~0u : 0;
        }
        vwrite(cpu, in, o.dst, &a, scalar ? 16 : width(in));
        return 1;
    }
    case M(COMISS): case M(VCOMISS): case M(UCOMISS): case M(VUCOMISS): {
        const BbVec a = vread(cpu, in, &in->op[0]), b = vread(cpu, in, &in->op[1]);
        comis(cpu, a.f[0], b.f[0]);
        return 1;
    }
    case M(COMISD): case M(VCOMISD): case M(UCOMISD): case M(VUCOMISD): {
        const BbVec a = vread(cpu, in, &in->op[0]), b = vread(cpu, in, &in->op[1]);
        comis(cpu, a.fd[0], b.fd[0]);
        return 1;
    }
    case M(PTEST): case M(VPTEST): case M(VTESTPS): {
        const BbVec a = vread(cpu, in, &in->op[0]), b = vread(cpu, in, &in->op[1]);
        uint64_t and_ = 0, andn = 0;
        const uint64_t lane_mask = m == M(VTESTPS) ? UINT64_C(0x8000000080000000) : ~UINT64_C(0);
        for (int i = 0; i < width(in) / 8; ++i) {
            and_ |= a.q[i] & b.q[i] & lane_mask;
            andn |= ~a.q[i] & b.q[i] & lane_mask;
        }
        cpu->flags = (cpu->flags & ~(uint64_t)F_ARITH) | (and_ ? 0 : F_ZF) | (andn ? 0 : F_CF);
        return 1;
    }
    /* ---- shuffles and blends ---- */
    case M(SHUFPS): case M(VSHUFPS): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int h = 0; h < width(in) / 16; ++h) {
            r.d[h * 4 + 0] = a.d[h * 4 + (imm & 3)];
            r.d[h * 4 + 1] = a.d[h * 4 + ((imm >> 2) & 3)];
            r.d[h * 4 + 2] = b.d[h * 4 + ((imm >> 4) & 3)];
            r.d[h * 4 + 3] = b.d[h * 4 + ((imm >> 6) & 3)];
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(SHUFPD): case M(VSHUFPD): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int h = 0; h < width(in) / 16; ++h) {
            r.q[h * 2] = a.q[h * 2 + ((imm >> (h * 2)) & 1)];
            r.q[h * 2 + 1] = b.q[h * 2 + ((imm >> (h * 2 + 1)) & 1)];
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(PSHUFD): case M(VPSHUFD): case M(VPERMILPS): case M(PSHUFLW): case M(VPSHUFLW):
    case M(PSHUFHW): case M(VPSHUFHW): case M(VPERMILPD): {
        if ((m == M(VPERMILPS) || m == M(VPERMILPD)) && in->op[2].type != OP_IMM) { /* variable form */
            const BbVec a = vread(cpu, in, &in->op[1]), c = vread(cpu, in, &in->op[2]);
            BbVec r;
            memset(&r, 0, sizeof(r));
            for (int h = 0; h < width(in) / 16; ++h) {
                if (m == M(VPERMILPS)) for (int i = 0; i < 4; ++i) r.d[h * 4 + i] = a.d[h * 4 + (c.d[h * 4 + i] & 3)];
                else for (int i = 0; i < 2; ++i) r.q[h * 2 + i] = a.q[h * 2 + ((c.q[h * 2 + i] >> 1) & 1)];
            }
            vwrite(cpu, in, &in->op[0], &r, width(in));
            return 1;
        }
        const Ops o = ops1(in);
        const BbVec a = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        BbVec r = a;
        for (int h = 0; h < width(in) / 16; ++h) {
            if (m == M(PSHUFD) || m == M(VPSHUFD) || m == M(VPERMILPS))
                for (int i = 0; i < 4; ++i) r.d[h * 4 + i] = a.d[h * 4 + ((imm >> (i * 2)) & 3)];
            else if (m == M(VPERMILPD))
                for (int i = 0; i < 2; ++i) r.q[h * 2 + i] = a.q[h * 2 + ((imm >> (h * 2 + i)) & 1)];
            else if (m == M(PSHUFLW) || m == M(VPSHUFLW))
                for (int i = 0; i < 4; ++i) r.w[h * 8 + i] = a.w[h * 8 + ((imm >> (i * 2)) & 3)];
            else
                for (int i = 0; i < 4; ++i) r.w[h * 8 + 4 + i] = a.w[h * 8 + 4 + ((imm >> (i * 2)) & 3)];
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(UNPCKLPS): case M(VUNPCKLPS): case M(UNPCKHPS): case M(VUNPCKHPS): case M(UNPCKLPD):
    case M(VUNPCKLPD): case M(UNPCKHPD): case M(VUNPCKHPD): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int high = m == M(UNPCKHPS) || m == M(VUNPCKHPS) || m == M(UNPCKHPD) || m == M(VUNPCKHPD);
        for (int h = 0; h < width(in) / 16; ++h) {
            if (m == M(UNPCKLPS) || m == M(VUNPCKLPS) || m == M(UNPCKHPS) || m == M(VUNPCKHPS)) {
                const int s = h * 4 + (high ? 2 : 0);
                r.d[h * 4 + 0] = a.d[s]; r.d[h * 4 + 1] = b.d[s];
                r.d[h * 4 + 2] = a.d[s + 1]; r.d[h * 4 + 3] = b.d[s + 1];
            } else {
                r.q[h * 2] = a.q[h * 2 + high]; r.q[h * 2 + 1] = b.q[h * 2 + high];
            }
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(BLENDPS): case M(VBLENDPS): case M(BLENDPD): case M(VBLENDPD): case M(PBLENDW):
    case M(VPBLENDW): case M(VPBLENDD): {
        const Ops o = ops3(in);
        BbVec a = vread(cpu, in, o.s1);
        const BbVec b = vread(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        const int es = (m == M(BLENDPS) || m == M(VBLENDPS) || m == M(VPBLENDD)) ? 4
                     : (m == M(BLENDPD) || m == M(VBLENDPD)) ? 8 : 2;
        for (int i = 0; i < width(in) / es; ++i) {
            const int bit = es == 2 ? (i & 7) : i;
            if (imm & (1 << bit)) memcpy(a.b + i * es, b.b + i * es, es);
        }
        vwrite(cpu, in, o.dst, &a, width(in));
        return 1;
    }
    case M(BLENDVPS): case M(VBLENDVPS): case M(BLENDVPD): case M(VBLENDVPD): case M(PBLENDVB):
    case M(VPBLENDVB): {
        BbVec a, b, mask;
        const BbOp *dst = &in->op[0];
        if (in->vex) { a = vread(cpu, in, &in->op[1]); b = vread(cpu, in, &in->op[2]); mask = vread(cpu, in, &in->op[3]); }
        else { a = vread(cpu, in, dst); b = vread(cpu, in, &in->op[1]); mask = cpu->v[0]; }
        const int es = (m == M(BLENDVPS) || m == M(VBLENDVPS)) ? 4 : (m == M(BLENDVPD) || m == M(VBLENDVPD)) ? 8 : 1;
        for (int i = 0; i < width(in) / es; ++i)
            if (mask.b[i * es + es - 1] & 0x80) memcpy(a.b + i * es, b.b + i * es, es);
        vwrite(cpu, in, dst, &a, width(in));
        return 1;
    }
    case M(PALIGNR): case M(VPALIGNR): {
        const Ops o = ops3(in);
        const BbVec a = vread(cpu, in, o.s1), b = vread(cpu, in, o.s2);
        const int shift = (int)o.imm->disp & 0xff;
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int h = 0; h < width(in) / 16; ++h) {
            uint8_t cat[32];
            memcpy(cat, b.b + h * 16, 16);
            memcpy(cat + 16, a.b + h * 16, 16);
            for (int i = 0; i < 16; ++i) r.b[h * 16 + i] = i + shift < 32 ? cat[i + shift] : 0;
        }
        vwrite(cpu, in, o.dst, &r, width(in));
        return 1;
    }
    case M(INSERTPS): case M(VINSERTPS): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const int imm = (int)o.imm->disp;
        uint32_t value;
        if (o.s2->type == OP_MEM) value = (uint32_t)bb_load(bbcpu_addr(cpu, in, o.s2), 4);
        else value = cpu->v[o.s2->reg].d[(imm >> 6) & 3];
        r.d[(imm >> 4) & 3] = value;
        for (int i = 0; i < 4; ++i) if (imm & (1 << i)) r.d[i] = 0;
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(EXTRACTPS): case M(VEXTRACTPS): case M(PEXTRD): case M(VPEXTRD): case M(PEXTRQ):
    case M(VPEXTRQ): case M(PEXTRW): case M(VPEXTRW): case M(PEXTRB): case M(VPEXTRB): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        const int imm = (int)in->op[2].disp;
        uint64_t v;
        if (m == M(PEXTRQ) || m == M(VPEXTRQ)) v = s.q[imm & 1];
        else if (m == M(PEXTRW) || m == M(VPEXTRW)) v = s.w[imm & 7];
        else if (m == M(PEXTRB) || m == M(VPEXTRB)) v = s.b[imm & 15];
        else v = s.d[imm & 3];
        const BbOp *dst = &in->op[0];
        if (dst->type == OP_REG) {
            BbOp full = *dst;
            if (full.size < 4) full.size = 4;
            bbcpu_write(cpu, in, &full, v);
        } else bbcpu_write(cpu, in, dst, v);
        return 1;
    }
    case M(EXTRQ): case M(INSERTQ): {
        /* SSE4A (AMD Jaguar; inFAMOUS Second Son uses both): a bit field of the low quadword,
         * extracted (zero-extended) or inserted; the upper quadword is zeroed, as AMD's hardware
         * does. Length 0 means 64. Immediate forms: length then index; register forms: the
         * source's bits 5:0 and 13:8 (EXTRQ), or of its upper quadword (INSERTQ). Semantics as
         * in HayatoG/iss_pc port/src/isa_emulate.c, checked there against AMD CPUs. */
        BbVec d = vread(cpu, in, &in->op[0]);
        BbVec s = {0};
        uint64_t control;
        if (m == M(EXTRQ)) {
            if (in->op[1].type == OP_IMM) control = (uint64_t)(in->op[1].disp & 63) | (uint64_t)(in->op[2].disp & 63) << 8;
            else { s = vread(cpu, in, &in->op[1]); control = s.q[0]; }
        } else {
            s = vread(cpu, in, &in->op[1]);
            if (in->count > 2 && in->op[2].type == OP_IMM)
                control = (uint64_t)(in->op[2].disp & 63) | (uint64_t)(in->op[3].disp & 63) << 8;
            else control = s.q[1];
        }
        const unsigned length = control & 63, index = control >> 8 & 63;
        const uint64_t mask = length == 0 ? ~UINT64_C(0) : (UINT64_C(1) << length) - 1;
        if (m == M(EXTRQ)) d.q[0] = d.q[0] >> index & mask;
        else {
            const uint64_t field = mask << index; /* bits past 63 are dropped */
            d.q[0] = (d.q[0] & ~field) | (s.q[0] << index & field);
        }
        d.q[1] = 0;
        vwrite(cpu, in, &in->op[0], &d, 16);
        return 1;
    }
    case M(PINSRD): case M(VPINSRD): case M(PINSRQ): case M(VPINSRQ): case M(PINSRW): case M(VPINSRW):
    case M(PINSRB): case M(VPINSRB): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const uint64_t v = bbcpu_read(cpu, in, o.s2);
        const int imm = (int)o.imm->disp;
        if (m == M(PINSRQ) || m == M(VPINSRQ)) r.q[imm & 1] = v;
        else if (m == M(PINSRW) || m == M(VPINSRW)) r.w[imm & 7] = (uint16_t)v;
        else if (m == M(PINSRB) || m == M(VPINSRB)) r.b[imm & 15] = (uint8_t)v;
        else r.d[imm & 3] = (uint32_t)v;
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(PMOVZXBW): case M(VPMOVZXBW): case M(PMOVZXBD): case M(VPMOVZXBD): case M(PMOVZXWD):
    case M(VPMOVZXWD): case M(PMOVZXDQ): case M(VPMOVZXDQ): case M(PMOVSXBW): case M(VPMOVSXBW):
    case M(PMOVSXWD): case M(VPMOVSXWD): case M(PMOVSXDQ): case M(VPMOVSXDQ): case M(PMOVSXBD):
    case M(VPMOVSXBD): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int w = width(in);
        switch (m) {
        case M(PMOVZXBW): case M(VPMOVZXBW): for (int i = 0; i < w / 2; ++i) r.w[i] = s.b[i]; break;
        case M(PMOVZXBD): case M(VPMOVZXBD): for (int i = 0; i < w / 4; ++i) r.d[i] = s.b[i]; break;
        case M(PMOVZXWD): case M(VPMOVZXWD): for (int i = 0; i < w / 4; ++i) r.d[i] = s.w[i]; break;
        case M(PMOVZXDQ): case M(VPMOVZXDQ): for (int i = 0; i < w / 8; ++i) r.q[i] = s.d[i]; break;
        case M(PMOVSXBW): case M(VPMOVSXBW): for (int i = 0; i < w / 2; ++i) r.sw[i] = s.sb[i]; break;
        case M(PMOVSXWD): case M(VPMOVSXWD): for (int i = 0; i < w / 4; ++i) r.sd[i] = s.sw[i]; break;
        case M(PMOVSXDQ): case M(VPMOVSXDQ): for (int i = 0; i < w / 8; ++i) r.sq[i] = s.sd[i]; break;
        default: for (int i = 0; i < w / 4; ++i) r.sd[i] = s.sb[i]; break;
        }
        vwrite(cpu, in, &in->op[0], &r, w);
        return 1;
    }
    /* ---- conversions ---- */
    case M(CVTSI2SS): case M(VCVTSI2SS): case M(CVTSI2SD): case M(VCVTSI2SD): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const int64_t v = bb_sext(bbcpu_read(cpu, in, o.s2), o.s2->size);
        if (m == M(CVTSI2SS) || m == M(VCVTSI2SS)) r.f[0] = (float)v;
        else r.fd[0] = (double)v;
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(CVTTSS2SI): case M(VCVTTSS2SI): case M(CVTSS2SI): case M(VCVTSS2SI): case M(CVTTSD2SI):
    case M(VCVTTSD2SI): case M(CVTSD2SI): case M(VCVTSD2SI): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        const int truncate = m == M(CVTTSS2SI) || m == M(VCVTTSS2SI) || m == M(CVTTSD2SI) || m == M(VCVTTSD2SI);
        const int dbl = m == M(CVTTSD2SI) || m == M(VCVTTSD2SI) || m == M(CVTSD2SI) || m == M(VCVTSD2SI);
        const double x = dbl ? s.fd[0] : s.f[0];
        const BbOp *dst = &in->op[0];
        bbcpu_write(cpu, in, dst, dst->size == 8 ? (uint64_t)cvt_f2i64(x, truncate, cpu->mxcsr)
                                                 : (uint32_t)cvt_f2i32(x, truncate, cpu->mxcsr));
        return 1;
    }
    case M(CVTSS2SD): case M(VCVTSS2SD): case M(CVTSD2SS): case M(VCVTSD2SS): {
        const Ops o = ops3(in);
        BbVec r = vread(cpu, in, o.s1);
        const BbVec s = vread(cpu, in, o.s2);
        if (m == M(CVTSS2SD) || m == M(VCVTSS2SD)) r.fd[0] = s.f[0];
        else r.f[0] = (float)s.fd[0];
        vwrite(cpu, in, o.dst, &r, 16);
        return 1;
    }
    case M(CVTDQ2PS): case M(VCVTDQ2PS): case M(CVTPS2DQ): case M(VCVTPS2DQ): case M(CVTTPS2DQ):
    case M(VCVTTPS2DQ): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int i = 0; i < width(in) / 4; ++i) {
            if (m == M(CVTDQ2PS) || m == M(VCVTDQ2PS)) r.f[i] = (float)s.sd[i];
            else r.sd[i] = cvt_f2i32(s.f[i], m == M(CVTTPS2DQ) || m == M(VCVTTPS2DQ), cpu->mxcsr);
        }
        vwrite(cpu, in, &in->op[0], &r, width(in));
        return 1;
    }
    case M(CVTPS2PD): case M(VCVTPS2PD): case M(CVTDQ2PD): case M(VCVTDQ2PD): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int out = in->op[0].size / 8;
        for (int i = 0; i < out; ++i) r.fd[i] = (m == M(CVTPS2PD) || m == M(VCVTPS2PD)) ? (double)s.f[i] : (double)s.sd[i];
        vwrite(cpu, in, &in->op[0], &r, in->op[0].size);
        return 1;
    }
    case M(CVTPD2PS): case M(VCVTPD2PS): case M(CVTPD2DQ): case M(VCVTPD2DQ): case M(CVTTPD2DQ):
    case M(VCVTTPD2DQ): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        const int lanes = in->op[1].size / 8;
        for (int i = 0; i < lanes; ++i) {
            if (m == M(CVTPD2PS) || m == M(VCVTPD2PS)) r.f[i] = (float)s.fd[i];
            else r.sd[i] = cvt_f2i32(s.fd[i], m == M(CVTTPD2DQ) || m == M(VCVTTPD2DQ), cpu->mxcsr);
        }
        vwrite(cpu, in, &in->op[0], &r, 16);
        return 1;
    }
    case M(VCVTPH2PS): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int i = 0; i < in->op[0].size / 4; ++i) r.f[i] = f16_to_f32(s.w[i]);
        vwrite(cpu, in, &in->op[0], &r, in->op[0].size);
        return 1;
    }
    case M(VCVTPS2PH): {
        const BbVec s = vread(cpu, in, &in->op[1]);
        BbVec r;
        memset(&r, 0, sizeof(r));
        for (int i = 0; i < in->op[1].size / 4; ++i) r.w[i] = f32_to_f16(s.f[i]);
        vwrite(cpu, in, &in->op[0], &r, in->op[0].type == OP_MEM ? in->op[0].size : 16);
        return 1;
    }
    /* ---- state ---- */
    case M(LDMXCSR): case M(VLDMXCSR):
        cpu->mxcsr = (uint32_t)bb_load(bbcpu_addr(cpu, in, &in->op[0]), 4);
        bbcpu_apply_host_fp(cpu->mxcsr);
        return 1;
    case M(STMXCSR): case M(VSTMXCSR):
        bb_store(bbcpu_addr(cpu, in, &in->op[0]), 4, cpu->mxcsr);
        return 1;
    case M(EMMS): case M(FEMMS):
        return 1;
    default:
        return 0;
    }
}
