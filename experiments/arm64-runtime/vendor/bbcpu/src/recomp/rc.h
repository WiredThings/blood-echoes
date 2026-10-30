/* The runtime interface of recompiled game code (docs/RECOMPILATION.md): what the C written by
 * tools/recomp/bbrecomp.c includes, and what the program (the game, tools/recomp/replay.c) gives it.
 *
 * A recompiled function takes the guest CPU state at the function's entry (its return address on
 * top of the guest stack) and returns with cpu->rip at the address it returned to, as the guest
 * code does. Guest registers live in locals inside; the CPU flags are computed lazily (RcFlags):
 * clang drops what no instruction reads. */
#ifndef BB_RECOMP_RC_H
#define BB_RECOMP_RC_H
#include "../cpu/cpu_internal.h"

#ifdef __cplusplus
extern "C" {
#endif

#include "abi.h"

typedef struct {
    uint32_t version;
    uint64_t image_base;
    /* Runs the guest instruction at cpu->rip (the interpreter); returns the next rip. */
    uint64_t (*step)(BbCpu *cpu);
    /* A call of guest `target` returning to `next` (nothing pushed yet); cpu->rip is `next` after. */
    void (*call)(BbCpu *cpu, uint64_t target, uint64_t next);
    /* A tail call of cpu->rip (the function's return address on top of the stack); cpu->rip is
     * where it returned to after. */
    void (*tail)(BbCpu *cpu);
    /* The rest of the function from cpu->rip in the translator (its return address at entry_rsp);
     * cpu->rip is where it returned to after. */
    void (*bail)(BbCpu *cpu, uint64_t entry_rsp);
    /* Code generated with BB_RECOMP_TRACE=1 calls this before each instruction (its image
     * offset, the guest registers): tools/recomp/replay.c --lockstep. NULL elsewhere. */
    void (*trace)(uint64_t offset, const uint64_t r[16], const BbCpu *cpu);
} RcApi;

#ifndef BB_CPU_TRACE_H
typedef void (*RcFn)(BbCpu *cpu);
#endif
/* A recompiled function: its image offset and size, and the hash of the code it was generated
 * from (bbcpu_recomp_hash): a function the game's patches changed is left to the translator.
 * `direct`: the slot the library's own calls of it go through, NULL until the program sets it to
 * `fn` (the functions it runs); empty, they go through RcApi.call. */
typedef struct { uint64_t offset, size, hash; RcFn fn; RcFn *direct; } RcFunction;
/* The library's entry point: keeps `api`, returns its functions. */
typedef const RcFunction *(*RcInit)(const RcApi *api, size_t *count);

/* ---- for generated code ---- */

/* Lazy flags: the last flag-setting operation and its operands, or FK_CPU (in cpu->flags). */
enum { FK_CPU, FK_ADD, FK_SUB, FK_LOGIC, FK_INC, FK_DEC };
static inline uint64_t rc_mask(int size) { return size >= 8 ? ~UINT64_C(0) : (UINT64_C(1) << (size * 8)) - 1; }

/* RFLAGS from the lazy state (as the interpreter computes them). */
static inline uint64_t rc_flags(int kind, uint64_t a, uint64_t b, uint64_t r, int size, int saved_cf,
                                uint64_t cpu_flags) {
    if (kind == FK_CPU) return cpu_flags;
    const uint64_t m = rc_mask(size), sign = UINT64_C(1) << (size * 8 - 1);
    a &= m; b &= m; r &= m;
    uint64_t f = cpu_flags & ~(uint64_t)F_ARITH;
    int cf = 0, of = 0, af = 0;
    switch (kind) {
    case FK_ADD: cf = r < a; of = ((a ^ r) & (b ^ r) & sign) != 0; af = ((a ^ b ^ r) & 0x10) != 0; break;
    case FK_SUB: cf = a < b; of = ((a ^ b) & (a ^ r) & sign) != 0; af = ((a ^ b ^ r) & 0x10) != 0; break;
    case FK_INC: cf = saved_cf; of = r == sign; af = ((a ^ 1 ^ r) & 0x10) != 0; break;
    case FK_DEC: cf = saved_cf; of = r == sign - 1; af = ((a ^ 1 ^ r) & 0x10) != 0; break;
    default: break; /* FK_LOGIC: CF, OF, AF clear */
    }
    if (cf) f |= F_CF;
    if (of) f |= F_OF;
    if (af) f |= F_AF;
    if (!r) f |= F_ZF;
    if (r & sign) f |= F_SF;
    if (!__builtin_parity((unsigned)(r & 0xff))) f |= F_PF;
    return f;
}

/* Condition code cc (0 o, 1 no, 2 b, 3 nb, 4 z, ... 15 nle) of the lazy state. */
static inline int rc_cond(int cc, int kind, uint64_t a, uint64_t b, uint64_t r, int size, int saved_cf,
                          uint64_t cpu_flags) {
    const uint64_t f = rc_flags(kind, a, b, r, size, saved_cf, cpu_flags);
    const int cf = !!(f & F_CF), zf = !!(f & F_ZF), sf = !!(f & F_SF), of = !!(f & F_OF), pf = !!(f & F_PF);
    int v;
    switch (cc >> 1) {
    case 0: v = of; break;
    case 1: v = cf; break;
    case 2: v = zf; break;
    case 3: v = cf || zf; break;
    case 4: v = sf; break;
    case 5: v = pf; break;
    case 6: v = sf != of; break;
    default: v = zf || sf != of; break;
    }
    return (cc & 1) ? !v : v;
}

/* Guest memory: x86 ordering between threads (bb_load/bb_store: acquire/release). The stack too:
 * other threads read and write structures on it (the game's jobs report into their creator's
 * stack), so plain accesses there let the compiler reorder or merge what another thread sees. */
static inline uint64_t rc_ld(uint64_t address, int size) { return bb_load(address, size); }
static inline void rc_st(uint64_t address, int size, uint64_t value) { bb_store(address, size, value); }
static inline uint64_t rc_lds(uint64_t address, int size) { return bb_load(address, size); }
static inline void rc_sts(uint64_t address, int size, uint64_t value) { bb_store(address, size, value); }

/* One atomic compare-and-exchange (lock prefixes; the interpreter's atomic_cas): *seen gets the
 * value the operation found. */
static inline int rc_cas(uint64_t address, int size, uint64_t expected, uint64_t desired, uint64_t *seen) {
    int ok;
    switch (size) {
    case 1: { uint8_t e = (uint8_t)expected; ok = __atomic_compare_exchange_n((uint8_t *)address, &e, (uint8_t)desired, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); *seen = e; break; }
    case 2: { uint16_t e = (uint16_t)expected; ok = __atomic_compare_exchange_n((uint16_t *)address, &e, (uint16_t)desired, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); *seen = e; break; }
    case 4: { uint32_t e = (uint32_t)expected; ok = __atomic_compare_exchange_n((uint32_t *)address, &e, (uint32_t)desired, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); *seen = e; break; }
    default: { uint64_t e = expected; ok = __atomic_compare_exchange_n((uint64_t *)address, &e, desired, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); *seen = e; break; }
    }
    return ok;
}

/* SZP of a result into flags f (the interpreter's set_szp). */
static inline uint64_t rc_szp(uint64_t f, uint64_t r, int size) {
    r &= rc_mask(size);
    f &= ~(uint64_t)(F_ZF | F_SF | F_PF);
    if (!r) f |= F_ZF;
    if (r & (UINT64_C(1) << (size * 8 - 1))) f |= F_SF;
    if (!__builtin_parity((unsigned)(r & 0xff))) f |= F_PF;
    return f;
}

/* Flags after adc (sub = 0) or sbb (sub = 1) of a and b with carry giving r (the interpreter's). */
static inline uint64_t rc_carry_flags(uint64_t f, int sub, uint64_t a, uint64_t b, uint64_t carry, uint64_t r, int size) {
    const uint64_t m = rc_mask(size), sign = UINT64_C(1) << (size * 8 - 1);
    a &= m; b &= m; r &= m;
    f &= ~(uint64_t)F_ARITH;
    if (sub ? (carry ? a <= b : a < b) : (carry ? r <= a : r < a)) f |= F_CF;
    if (sub ? ((a ^ b) & (a ^ r) & sign) : ((a ^ r) & (b ^ r) & sign)) f |= F_OF;
    if ((a ^ b ^ r) & 0x10) f |= F_AF;
    return rc_szp(f, r, size);
}

/* Float to integer conversions (the interpreter's cvt_f2i32/64): out of range, the "integer
 * indefinite"; rounding from mxcsr unless truncating. */
static inline double rc_round_mxcsr(double x, uint32_t mxcsr) {
    switch ((mxcsr >> 13) & 3) {
    case 0: return __builtin_nearbyint(x);
    case 1: return __builtin_floor(x);
    case 2: return __builtin_ceil(x);
    default: return x;
    }
}
static inline int32_t rc_f2i32(double x, int truncate, uint32_t mxcsr) {
    if (x != x || x >= 2147483648.0 || x < -2147483648.0) return INT32_MIN;
    return (int32_t)(truncate ? x : rc_round_mxcsr(x, mxcsr));
}
static inline int64_t rc_f2i64(double x, int truncate, uint32_t mxcsr) {
    if (x != x || x >= 9223372036854775808.0 || x < -9223372036854775808.0) return INT64_MIN;
    return (int64_t)(truncate ? x : rc_round_mxcsr(x, mxcsr));
}

/* Flags after shl/shr/sar of `a` by `count` (masked, non-zero) giving `r` (the interpreter's). */
static inline uint64_t rc_shift_flags(uint64_t f, int kind, uint64_t a, unsigned count, uint64_t r, int size) {
    const int bits = size * 8;
    const uint64_t sign = UINT64_C(1) << (bits - 1);
    int cf;
    f &= ~(uint64_t)(F_CF | F_OF);
    if (kind == 0) { /* shl */
        cf = count <= (unsigned)bits && ((a >> (bits - count)) & 1);
        if (((r & sign) != 0) != cf) f |= F_OF;
    } else if (kind == 1) { /* shr */
        cf = (int)((a >> (count - 1)) & 1);
        if (a & sign) f |= F_OF;
    } else { /* sar: a sign-extended */
        cf = (int)(((int64_t)a >> (count - 1)) & 1);
    }
    if (cf) f |= F_CF;
    return rc_szp(f, r, size);
}

/* Vector operands (the interpreter's vread/vwrite): memory loads and stores with x86 ordering;
 * a register destination gets `bytes`, VEX forms zeros above them. */
static inline BbVec rc_vld(uint64_t address, int size) {
    BbVec v;
    memset(&v, 0, sizeof(v));
    memcpy(v.b, (const void *)address, (size_t)size);
    bb_fence_load();
    return v;
}
static inline void rc_vst(uint64_t address, int size, BbVec v) {
    bb_fence_store();
    memcpy((void *)address, v.b, (size_t)size);
}
static inline void rc_vset(BbVec *d, BbVec v, int bytes, int vex) {
    memcpy(d->b, v.b, (size_t)bytes);
    if (vex && bytes < 32) memset(d->b + bytes, 0, (size_t)(32 - bytes));
}
static inline BbVec rc_vgpr(uint64_t value) {
    BbVec v;
    memset(&v, 0, sizeof(v));
    v.q[0] = value;
    return v;
}

/* cmpps predicates (the interpreter's fcompare). */
static inline int rc_fcompare(double a, double b, int predicate) {
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
    if (predicate & 8) {
        switch (predicate & 7) {
        case 0: r = unordered || a == b; break;
        case 1: r = unordered || !(a >= b); break;
        case 2: r = unordered || !(a > b); break;
        case 3: r = 0; break;
        case 4: r = !unordered && a != b; break;
        case 5: r = !unordered && a >= b; break;
        case 6: r = !unordered && a > b; break;
        default: r = 1; break;
        }
    }
    return r;
}

/* roundss/roundps (the interpreter's round_mode): the mode from imm, or mxcsr with imm bit 2. */
static inline double rc_round_mode(double x, int imm, uint32_t mxcsr) {
    switch ((imm & 4) ? (int)((mxcsr >> 13) & 3) : imm & 3) {
    case 0: return __builtin_nearbyint(x);
    case 1: return __builtin_floor(x);
    case 2: return __builtin_ceil(x);
    default: return __builtin_trunc(x);
    }
}

/* Saturations of the pack and saturating add/sub instructions (the interpreter's). */
static inline int16_t rc_sat16(int32_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }
static inline uint16_t rc_usat16(int32_t v) { return v > 65535 ? 65535 : v < 0 ? 0 : (uint16_t)v; }
static inline int8_t rc_sat8(int32_t v) { return v > 127 ? 127 : v < -128 ? -128 : (int8_t)v; }
static inline uint8_t rc_usat8(int32_t v) { return v > 255 ? 255 : v < 0 ? 0 : (uint8_t)v; }

/* rdtsc: the guest's 1.6 GHz counter, as bbcpu_tsc computes it (interp.c). */
#if defined(__aarch64__)
static inline uint64_t rc_tsc(void) {
#ifdef BBCPU_NATIVE_TSC
    uint64_t counter;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(counter));
    return counter;
#else
    static uint64_t num, den;
    if (!__atomic_load_n(&den, __ATOMIC_ACQUIRE)) {
        uint64_t hz;
        __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(hz));
        uint64_t x = BBCPU_TSC_HZ, y = hz ? hz : 24000000;
        while (y) { const uint64_t r = x % y; x = y; y = r; }
        num = BBCPU_TSC_HZ / x;
        __atomic_store_n(&den, (hz ? hz : 24000000) / x, __ATOMIC_RELEASE);
    }
    uint64_t counter;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(counter));
    return counter * num / __atomic_load_n(&den, __ATOMIC_ACQUIRE);
#endif
}
#else
#include <time.h>
static inline uint64_t rc_tsc(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return ((uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec) * 8 / 5;
}
#endif

/* comiss/ucomiss flags (the interpreter's comis). */
static inline uint64_t rc_comis(uint64_t f, double a, double b) {
    f &= ~(uint64_t)F_ARITH;
    if (a != a || b != b) f |= F_ZF | F_PF | F_CF;
    else if (a < b) f |= F_CF;
    else if (a == b) f |= F_ZF;
    return f;
}

#ifdef __cplusplus
}
#endif
#endif
