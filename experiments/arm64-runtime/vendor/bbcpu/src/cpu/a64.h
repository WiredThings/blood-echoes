/* bbcpu: a small arm64 instruction emitter for the JIT (jit_arm64.c). Encodings follow the Arm
 * Architecture Reference Manual (A64); only what the translator uses. Registers are 0-31 (31 is
 * xzr/wzr or sp depending on the instruction). */
#ifndef BB_A64_H
#define BB_A64_H
#include <stdint.h>
#include <string.h>

typedef struct {
    uint32_t *start, *at, *end;
} A64;

static inline void a64_emit(A64 *a, uint32_t insn) {
    if (a->at < a->end) *a->at = insn;
    ++a->at;
}
static inline uint32_t *a64_here(const A64 *a) { return a->at; }

enum { XZR = 31, SP = 31 };
/* Condition codes. */
enum { CC_EQ, CC_NE, CC_HS, CC_LO, CC_MI, CC_PL, CC_VS, CC_VC, CC_HI, CC_LS, CC_GE, CC_LT, CC_GT,
       CC_LE, CC_AL };

/* sf: 1 for 64-bit (x), 0 for 32-bit (w). */

/* mov (wide immediates): movz/movk sequences. */
static inline void a64_movz(A64 *a, int sf, int rd, uint16_t imm, int shift) {
    a64_emit(a, (uint32_t)sf << 31 | 0x52800000u | (uint32_t)(shift / 16) << 21 | (uint32_t)imm << 5 | (uint32_t)rd);
}
static inline void a64_movk(A64 *a, int sf, int rd, uint16_t imm, int shift) {
    a64_emit(a, (uint32_t)sf << 31 | 0x72800000u | (uint32_t)(shift / 16) << 21 | (uint32_t)imm << 5 | (uint32_t)rd);
}
static inline void a64_movn(A64 *a, int sf, int rd, uint16_t imm, int shift) {
    a64_emit(a, (uint32_t)sf << 31 | 0x12800000u | (uint32_t)(shift / 16) << 21 | (uint32_t)imm << 5 | (uint32_t)rd);
}
static inline void a64_mov_imm(A64 *a, int rd, uint64_t value) {
    if (value == 0) { a64_movz(a, 1, rd, 0, 0); return; }
    if (~value <= 0xffff) { a64_movn(a, 1, rd, (uint16_t)~value, 0); return; }
    int first = 1;
    for (int shift = 0; shift < 64; shift += 16) {
        const uint16_t part = (uint16_t)(value >> shift);
        if (!part) continue;
        if (first) { a64_movz(a, 1, rd, part, shift); first = 0; }
        else a64_movk(a, 1, rd, part, shift);
    }
}

/* Register moves and arithmetic (shifted register forms). */
static inline void a64_orr(A64 *a, int sf, int rd, int rn, int rm) {
    a64_emit(a, (uint32_t)sf << 31 | 0x2a000000u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_mov(A64 *a, int sf, int rd, int rm) { a64_orr(a, sf, rd, XZR, rm); }
/* mov to/from sp: add rd, rn, #0 */
static inline void a64_add_imm(A64 *a, int sf, int rd, int rn, uint32_t imm12, int shift12) {
    a64_emit(a, (uint32_t)sf << 31 | 0x11000000u | (uint32_t)(shift12 ? 1 : 0) << 22 | (imm12 & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_sub_imm(A64 *a, int sf, int rd, int rn, uint32_t imm12, int shift12) {
    a64_emit(a, (uint32_t)sf << 31 | 0x51000000u | (uint32_t)(shift12 ? 1 : 0) << 22 | (imm12 & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* subs/adds with an immediate (cmp/cmn with rd = xzr) */
static inline void a64_subs_imm(A64 *a, int sf, int rd, int rn, uint32_t imm12) {
    a64_emit(a, (uint32_t)sf << 31 | 0x71000000u | (imm12 & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_adds_imm(A64 *a, int sf, int rd, int rn, uint32_t imm12) {
    a64_emit(a, (uint32_t)sf << 31 | 0x31000000u | (imm12 & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* op: 0 add 1 adds 2 sub 3 subs; shift: 0 lsl 1 lsr 2 asr; amount 0-63. */
static inline void a64_arith(A64 *a, int op, int sf, int rd, int rn, int rm, int shift, int amount) {
    static const uint32_t base[4] = {0x0b000000u, 0x2b000000u, 0x4b000000u, 0x6b000000u};
    a64_emit(a, (uint32_t)sf << 31 | base[op] | (uint32_t)shift << 22 | (uint32_t)rm << 16 | (uint32_t)amount << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_add(A64 *a, int sf, int rd, int rn, int rm) { a64_arith(a, 0, sf, rd, rn, rm, 0, 0); }
static inline void a64_adds(A64 *a, int sf, int rd, int rn, int rm) { a64_arith(a, 1, sf, rd, rn, rm, 0, 0); }
static inline void a64_sub(A64 *a, int sf, int rd, int rn, int rm) { a64_arith(a, 2, sf, rd, rn, rm, 0, 0); }
static inline void a64_subs(A64 *a, int sf, int rd, int rn, int rm) { a64_arith(a, 3, sf, rd, rn, rm, 0, 0); }
/* adc/adcs/sbc/sbcs */
static inline void a64_adc(A64 *a, int sf, int s, int rd, int rn, int rm) {
    a64_emit(a, (uint32_t)sf << 31 | (s ? 0x3a000000u : 0x1a000000u) | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_sbc(A64 *a, int sf, int s, int rd, int rn, int rm) {
    a64_emit(a, (uint32_t)sf << 31 | (s ? 0x7a000000u : 0x5a000000u) | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* Logical shifted register. op: 0 and 1 orr 2 eor 3 ands; n: invert rm (bic/orn/eon/bics). */
static inline void a64_logic(A64 *a, int op, int n, int sf, int rd, int rn, int rm, int shift, int amount) {
    a64_emit(a, (uint32_t)sf << 31 | (uint32_t)op << 29 | 0x0a000000u | (uint32_t)shift << 22 | (uint32_t)(n ? 1 : 0) << 21 | (uint32_t)rm << 16 | (uint32_t)amount << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_and(A64 *a, int sf, int rd, int rn, int rm) { a64_logic(a, 0, 0, sf, rd, rn, rm, 0, 0); }
static inline void a64_ands(A64 *a, int sf, int rd, int rn, int rm) { a64_logic(a, 3, 0, sf, rd, rn, rm, 0, 0); }
static inline void a64_eor(A64 *a, int sf, int rd, int rn, int rm) { a64_logic(a, 2, 0, sf, rd, rn, rm, 0, 0); }
static inline void a64_bic(A64 *a, int sf, int rd, int rn, int rm) { a64_logic(a, 0, 1, sf, rd, rn, rm, 0, 0); }
static inline void a64_mvn(A64 *a, int sf, int rd, int rm) { a64_logic(a, 1, 1, sf, rd, XZR, rm, 0, 0); }

/* Logical immediate with a mask of `length` ones starting at bit `start` (rotations allowed):
 * op 0 and 1 orr 2 eor 3 ands. */
static inline void a64_logic_imm(A64 *a, int op, int sf, int rd, int rn, int start, int length) {
    const int bits = sf ? 64 : 32;
    a64_emit(a, (uint32_t)sf << 31 | (uint32_t)op << 29 | 0x12000000u | (uint32_t)(sf ? 1 : 0) << 22 |
                    (uint32_t)((bits - start) % bits) << 16 | (uint32_t)(length - 1) << 10 |
                    (uint32_t)rn << 5 | (uint32_t)rd);
}
/* Single bits: eor/orr/bic (and with all ones but the bit). */
static inline void a64_eor_bit(A64 *a, int rd, int rn, int bit) { a64_logic_imm(a, 2, 1, rd, rn, bit, 1); }
static inline void a64_orr_bit(A64 *a, int rd, int rn, int bit) { a64_logic_imm(a, 1, 1, rd, rn, bit, 1); }
static inline void a64_bic_bit(A64 *a, int rd, int rn, int bit) { a64_logic_imm(a, 0, 1, rd, rn, (bit + 1) % 64, 63); }

/* Variable shifts: op 0 lslv 1 lsrv 2 asrv 3 rorv. */
static inline void a64_shiftv(A64 *a, int op, int sf, int rd, int rn, int rm) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1ac02000u | (uint32_t)rm << 16 | (uint32_t)op << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* Bitfield moves: ubfm/sbfm/bfm (opc 2/0/1). */
static inline void a64_bfm_op(A64 *a, int opc, int sf, int rd, int rn, int immr, int imms) {
    a64_emit(a, (uint32_t)sf << 31 | (uint32_t)opc << 29 | 0x13000000u | (uint32_t)sf << 22 | (uint32_t)immr << 16 | (uint32_t)imms << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_ubfx(A64 *a, int sf, int rd, int rn, int lsb, int width) { a64_bfm_op(a, 2, sf, rd, rn, lsb, lsb + width - 1); }
static inline void a64_sbfx(A64 *a, int sf, int rd, int rn, int lsb, int width) { a64_bfm_op(a, 0, sf, rd, rn, lsb, lsb + width - 1); }
/* bfi rd, rn, #lsb, #width */
static inline void a64_bfi(A64 *a, int sf, int rd, int rn, int lsb, int width) {
    const int bits = sf ? 64 : 32;
    a64_bfm_op(a, 1, sf, rd, rn, (bits - lsb) % bits, width - 1);
}
/* bfxil rd, rn, #lsb, #width */
static inline void a64_bfxil(A64 *a, int sf, int rd, int rn, int lsb, int width) { a64_bfm_op(a, 1, sf, rd, rn, lsb, lsb + width - 1); }
static inline void a64_lsl_imm(A64 *a, int sf, int rd, int rn, int shift) {
    const int bits = sf ? 64 : 32;
    a64_bfm_op(a, 2, sf, rd, rn, (bits - shift) % bits, bits - 1 - shift);
}
static inline void a64_lsr_imm(A64 *a, int sf, int rd, int rn, int shift) { a64_bfm_op(a, 2, sf, rd, rn, shift, sf ? 63 : 31); }
static inline void a64_asr_imm(A64 *a, int sf, int rd, int rn, int shift) { a64_bfm_op(a, 0, sf, rd, rn, shift, sf ? 63 : 31); }
/* sxtb/sxth/sxtw/uxtb/uxth */
static inline void a64_sxt(A64 *a, int sf, int rd, int rn, int bytes) { a64_bfm_op(a, 0, sf, rd, rn, 0, bytes * 8 - 1); }
static inline void a64_uxt(A64 *a, int rd, int rn, int bytes) { a64_bfm_op(a, 2, 0, rd, rn, 0, bytes * 8 - 1); }

/* Multiply: madd/msub; smulh/umulh; [su]maddl (32x32->64). */
static inline void a64_madd(A64 *a, int sf, int rd, int rn, int rm, int ra) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1b000000u | (uint32_t)rm << 16 | (uint32_t)ra << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_msub(A64 *a, int sf, int rd, int rn, int rm, int ra) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1b008000u | (uint32_t)rm << 16 | (uint32_t)ra << 10 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_mul(A64 *a, int sf, int rd, int rn, int rm) { a64_madd(a, sf, rd, rn, rm, XZR); }
static inline void a64_smulh(A64 *a, int rd, int rn, int rm) { a64_emit(a, 0x9b407c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_umulh(A64 *a, int rd, int rn, int rm) { a64_emit(a, 0x9bc07c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_smull(A64 *a, int rd, int rn, int rm) { a64_emit(a, 0x9b207c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_umull(A64 *a, int rd, int rn, int rm) { a64_emit(a, 0x9ba07c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
/* udiv/sdiv */
static inline void a64_udiv(A64 *a, int sf, int rd, int rn, int rm) { a64_emit(a, (uint32_t)sf << 31 | 0x1ac00800u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_sdiv(A64 *a, int sf, int rd, int rn, int rm) { a64_emit(a, (uint32_t)sf << 31 | 0x1ac00c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd); }
/* clz/rbit/rev */
static inline void a64_clz(A64 *a, int sf, int rd, int rn) { a64_emit(a, (uint32_t)sf << 31 | 0x5ac01000u | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_rbit(A64 *a, int sf, int rd, int rn) { a64_emit(a, (uint32_t)sf << 31 | 0x5ac00000u | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_rev(A64 *a, int sf, int rd, int rn) { a64_emit(a, (sf ? 0xdac00c00u : 0x5ac00800u) | (uint32_t)rn << 5 | (uint32_t)rd); }
static inline void a64_rev16(A64 *a, int sf, int rd, int rn) { a64_emit(a, (uint32_t)sf << 31 | 0x5ac00400u | (uint32_t)rn << 5 | (uint32_t)rd); }

/* Conditional select: csel/csinc (cset = csinc rd, zr, zr, !cc). */
static inline void a64_csel(A64 *a, int sf, int rd, int rn, int rm, int cc) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1a800000u | (uint32_t)rm << 16 | (uint32_t)cc << 12 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_csinc(A64 *a, int sf, int rd, int rn, int rm, int cc) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1a800400u | (uint32_t)rm << 16 | (uint32_t)cc << 12 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_cset(A64 *a, int sf, int rd, int cc) { a64_csinc(a, sf, rd, XZR, XZR, cc ^ 1); }

/* Flags: mrs/msr nzcv (bits 31-28 N Z C V). */
static inline void a64_mrs_nzcv(A64 *a, int rt) { a64_emit(a, 0xd53b4200u | (uint32_t)rt); }
static inline void a64_msr_nzcv(A64 *a, int rt) { a64_emit(a, 0xd51b4200u | (uint32_t)rt); }

/* Loads/stores, unsigned scaled offset: size 0 b 1 h 2 w 3 x. */
static inline void a64_ldr_uoff(A64 *a, int size, int rt, int rn, uint32_t offset) {
    a64_emit(a, (uint32_t)size << 30 | 0x39400000u | ((offset >> size) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_str_uoff(A64 *a, int size, int rt, int rn, uint32_t offset) {
    a64_emit(a, (uint32_t)size << 30 | 0x39000000u | ((offset >> size) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* ldr/str with register offset (rn + rm). */
static inline void a64_ldr_reg(A64 *a, int size, int rt, int rn, int rm) {
    a64_emit(a, (uint32_t)size << 30 | 0x38606800u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_str_reg(A64 *a, int size, int rt, int rn, int rm) {
    a64_emit(a, (uint32_t)size << 30 | 0x38206800u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* Sign-extending loads to 64 bits: ldrsb/ldrsh/ldrsw (size 0/1/2), register offset. */
static inline void a64_ldrs_reg(A64 *a, int size, int rt, int rn, int rm) {
    a64_emit(a, (uint32_t)size << 30 | 0x38a06800u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* ldp/stp x, pre-index / post-index / offset (64-bit pairs). */
static inline void a64_stp_pre(A64 *a, int rt, int rt2, int rn, int offset) {
    a64_emit(a, 0xa9800000u | ((uint32_t)(offset / 8) & 0x7f) << 15 | (uint32_t)rt2 << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_ldp_post(A64 *a, int rt, int rt2, int rn, int offset) {
    a64_emit(a, 0xa8c00000u | ((uint32_t)(offset / 8) & 0x7f) << 15 | (uint32_t)rt2 << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_stp(A64 *a, int rt, int rt2, int rn, int offset) {
    a64_emit(a, 0xa9000000u | ((uint32_t)(offset / 8) & 0x7f) << 15 | (uint32_t)rt2 << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_ldp(A64 *a, int rt, int rt2, int rn, int offset) {
    a64_emit(a, 0xa9400000u | ((uint32_t)(offset / 8) & 0x7f) << 15 | (uint32_t)rt2 << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* SIMD&FP q-register load/store, unsigned offset (scaled by 16). */
static inline void a64_ldr_q(A64 *a, int rt, int rn, uint32_t offset) {
    a64_emit(a, 0x3dc00000u | ((offset / 16) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_str_q(A64 *a, int rt, int rn, uint32_t offset) {
    a64_emit(a, 0x3d800000u | ((offset / 16) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}

/* Atomics (LSE): cas/swp/ldadd/ldclr/ldset/ldeor with acquire+release, size 0-3. */
static inline void a64_casal(A64 *a, int size, int rs, int rt, int rn) {
    a64_emit(a, (uint32_t)size << 30 | 0x08e0fc00u | (uint32_t)rs << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_swpal(A64 *a, int size, int rs, int rt, int rn) {
    a64_emit(a, (uint32_t)size << 30 | 0x38e08000u | (uint32_t)rs << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* op: 0 ldadd 1 ldclr 2 ldeor 3 ldset */
static inline void a64_ldopal(A64 *a, int op, int size, int rs, int rt, int rn) {
    static const uint32_t opc[4] = {0x0000u, 0x1000u, 0x2000u, 0x3000u};
    a64_emit(a, (uint32_t)size << 30 | 0x38e00000u | opc[op] | (uint32_t)rs << 16 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_dmb_ish(A64 *a) { a64_emit(a, 0xd5033bbfu); }

/* Branches. Offsets in instructions. */
static inline void a64_b(A64 *a, int32_t offset) { a64_emit(a, 0x14000000u | ((uint32_t)offset & 0x3ffffff)); }
static inline void a64_bl(A64 *a, int32_t offset) { a64_emit(a, 0x94000000u | ((uint32_t)offset & 0x3ffffff)); }
static inline void a64_bcond(A64 *a, int cc, int32_t offset) { a64_emit(a, 0x54000000u | ((uint32_t)offset & 0x7ffff) << 5 | (uint32_t)cc); }
static inline void a64_cbz(A64 *a, int sf, int rt, int32_t offset) { a64_emit(a, (uint32_t)sf << 31 | 0x34000000u | ((uint32_t)offset & 0x7ffff) << 5 | (uint32_t)rt); }
static inline void a64_cbnz(A64 *a, int sf, int rt, int32_t offset) { a64_emit(a, (uint32_t)sf << 31 | 0x35000000u | ((uint32_t)offset & 0x7ffff) << 5 | (uint32_t)rt); }
static inline void a64_blr(A64 *a, int rn) { a64_emit(a, 0xd63f0000u | (uint32_t)rn << 5); }
static inline void a64_br(A64 *a, int rn) { a64_emit(a, 0xd61f0000u | (uint32_t)rn << 5); }
static inline void a64_ret(A64 *a) { a64_emit(a, 0xd65f03c0u); }
/* adr rd, at + offset (bytes) */
static inline void a64_adr(A64 *a, int rd, int32_t offset) {
    a64_emit(a, 0x10000000u | ((uint32_t)offset & 3) << 29 | (((uint32_t)offset >> 2) & 0x7ffff) << 5 | (uint32_t)rd);
}
/* Patch a branch emitted at `at` to reach `target`. */
static inline void a64_patch_bcond(uint32_t *at, const uint32_t *target) {
    const int32_t offset = (int32_t)(target - at);
    *at = (*at & ~(0x7ffffu << 5)) | ((uint32_t)offset & 0x7ffff) << 5;
}
static inline void a64_patch_b(uint32_t *at, const uint32_t *target) {
    const int32_t offset = (int32_t)(target - at);
    *at = (*at & 0xfc000000u) | ((uint32_t)offset & 0x3ffffff);
}

/* ---- SIMD & FP ---- (dbl: double precision) */
/* Scalar arithmetic: op 0 fmul 1 fdiv 2 fadd 3 fsub (s or d registers). */
static inline void a64_fop_s(A64 *a, int op, int dbl, int rd, int rn, int rm) {
    static const uint32_t opc[4] = {0x0800u, 0x1800u, 0x2800u, 0x3800u};
    a64_emit(a, 0x1e200000u | (uint32_t)(dbl ? 1 : 0) << 22 | opc[op] | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_fsqrt_s(A64 *a, int dbl, int rd, int rn) {
    a64_emit(a, 0x1e21c000u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* Vector arithmetic on 4s (dbl: 2d): op 0 fmul 1 fdiv 2 fadd 3 fsub. */
static inline void a64_fop_v(A64 *a, int op, int dbl, int rd, int rn, int rm) {
    static const uint32_t base[4] = {0x6e20dc00u, 0x6e20fc00u, 0x4e20d400u, 0x4ea0d400u};
    a64_emit(a, base[op] | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* 128-bit logic: op 0 and 1 orr 2 eor 3 bic (rn & ~rm). */
static inline void a64_vlogic(A64 *a, int op, int rd, int rn, int rm) {
    static const uint32_t base[4] = {0x4e201c00u, 0x4ea01c00u, 0x6e201c00u, 0x4e601c00u};
    a64_emit(a, base[op] | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* imm5 of an element index (es: 1, 2, 4 or 8 bytes). */
static inline uint32_t a64_imm5(int es, int index) {
    return es == 1 ? ((uint32_t)index << 1 | 1) : es == 2 ? ((uint32_t)index << 2 | 2)
         : es == 4 ? ((uint32_t)index << 3 | 4) : ((uint32_t)index << 4 | 8);
}
static inline int a64_log2_es(int es) { return es == 1 ? 0 : es == 2 ? 1 : es == 4 ? 2 : 3; }
/* ins vd.<b|h|s|d>[di], vn.<b|h|s|d>[si] */
static inline void a64_ins_elem(A64 *a, int es, int rd, int di, int rn, int si) {
    const uint32_t imm4 = (uint32_t)si << a64_log2_es(es);
    a64_emit(a, 0x6e000400u | a64_imm5(es, di) << 16 | imm4 << 11 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* ins vd.<b|h|s|d>[di], wn|xn (wzr: 31) ; umov wd|xd, vn.<b|h|s|d>[si] */
static inline void a64_ins_gpr(A64 *a, int es, int rd, int di, int rn) {
    a64_emit(a, 0x4e001c00u | a64_imm5(es, di) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_umov(A64 *a, int es, int rd, int rn, int si) {
    a64_emit(a, (es == 8 ? 0x4e003c00u : 0x0e003c00u) | a64_imm5(es, si) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* dup vd.<16b|8h|4s|2d>, vn.<b|h|s|d>[i] ; dup vd, wn|xn */
static inline void a64_dup_elem(A64 *a, int es, int rd, int rn, int index) {
    a64_emit(a, 0x4e000400u | a64_imm5(es, index) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_dup_gpr(A64 *a, int es, int rd, int rn) {
    a64_emit(a, 0x4e000c00u | a64_imm5(es, 0) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}

/* More SIMD (128-bit forms unless named otherwise): opcodes for a64_v3 (vd, vn, vm) and a64_v2
 * (vd, vn); tests/test_a64.c checks each against the assembler. */
enum {
    V_ADD_16B = 0x4e208400, V_ADD_8H = 0x4e608400, V_ADD_4S = 0x4ea08400, V_ADD_2D = 0x4ee08400,
    V_SUB_16B = 0x6e208400, V_SUB_8H = 0x6e608400, V_SUB_4S = 0x6ea08400, V_SUB_2D = 0x6ee08400,
    V_MUL_8H = 0x4e609c00, V_MUL_4S = 0x4ea09c00,
    V_CMEQ_16B = 0x6e208c00, V_CMEQ_8H = 0x6e608c00, V_CMEQ_4S = 0x6ea08c00, V_CMEQ_2D = 0x6ee08c00,
    V_CMGT_16B = 0x4e203400, V_CMGT_8H = 0x4e603400, V_CMGT_4S = 0x4ea03400, V_CMGT_2D = 0x4ee03400,
    V_SMAX_16B = 0x4e206400, V_SMAX_8H = 0x4e606400, V_SMAX_4S = 0x4ea06400,
    V_SMIN_16B = 0x4e206c00, V_SMIN_8H = 0x4e606c00, V_SMIN_4S = 0x4ea06c00,
    V_UMAX_16B = 0x6e206400, V_UMAX_8H = 0x6e606400, V_UMAX_4S = 0x6ea06400,
    V_UMIN_16B = 0x6e206c00, V_UMIN_8H = 0x6e606c00, V_UMIN_4S = 0x6ea06c00,
    V_SQADD_16B = 0x4e200c00, V_SQADD_8H = 0x4e600c00, V_UQADD_16B = 0x6e200c00, V_UQADD_8H = 0x6e600c00,
    V_SQSUB_16B = 0x4e202c00, V_SQSUB_8H = 0x4e602c00, V_UQSUB_16B = 0x6e202c00, V_UQSUB_8H = 0x6e602c00,
    V_URHADD_16B = 0x6e201400, V_URHADD_8H = 0x6e601400,
    V_FADDP_4S = 0x6e20d400, V_FADDP_2D = 0x6e60d400,
    V_FCMEQ_4S = 0x4e20e400, V_FCMEQ_2D = 0x4e60e400, V_FCMGE_4S = 0x6e20e400, V_FCMGE_2D = 0x6e60e400,
    V_FCMGT_4S = 0x6ea0e400, V_FCMGT_2D = 0x6ee0e400,
    V_ZIP1_16B = 0x4e003800, V_ZIP1_8H = 0x4e403800, V_ZIP1_4S = 0x4e803800, V_ZIP1_2D = 0x4ec03800,
    V_ZIP2_16B = 0x4e007800, V_ZIP2_8H = 0x4e407800, V_ZIP2_4S = 0x4e807800, V_ZIP2_2D = 0x4ec07800,
    V_UZP1_4S = 0x4e801800, V_UZP2_4S = 0x4e805800, V_TRN1_4S = 0x4e802800, V_TRN2_4S = 0x4e806800,
    V_TBL_16B = 0x4e000000, V_ORN = 0x4ee01c00,
    /* two registers */
    V_NOT_16B = 0x6e205800, V_CNT_8B = 0x0e205800, V_ADDV_8B = 0x0e31b800,
    V_CMLT0_16B = 0x4e20a800, V_CMLT0_8H = 0x4e60a800, V_CMLT0_4S = 0x4ea0a800, V_CMLT0_2D = 0x4ee0a800,
    V_FSQRT_4S = 0x6ea1f800, V_FSQRT_2D = 0x6ee1f800,
    V_SCVTF_4S = 0x4e21d800, V_FCVTZS_4S = 0x4ea1b800,
    V_FCVTL_2D = 0x0e617800, V_FCVTN_2S = 0x0e616800, /* low two s <-> two d */
    V_SQXTN_4H = 0x0e614800, V_SQXTN2_8H = 0x4e614800, V_SQXTUN_4H = 0x2e612800, V_SQXTUN2_8H = 0x6e612800,
    V_SQXTN_8B = 0x0e214800, V_SQXTN2_16B = 0x4e214800, V_SQXTUN_8B = 0x2e212800, V_SQXTUN2_16B = 0x6e212800,
    V_SXTL_8H = 0x0f08a400, V_SXTL_4S = 0x0f10a400, V_SXTL_2D = 0x0f20a400, /* from the low half */
    V_UXTL_8H = 0x2f08a400, V_UXTL_4S = 0x2f10a400, V_UXTL_2D = 0x2f20a400,
    V_LD1R_4S = 0x4d40c800, V_LD1R_2D = 0x4d40cc00, /* ld1r {vt}, [xn] */
};
static inline void a64_v3(A64 *a, uint32_t op, int rd, int rn, int rm) {
    a64_emit(a, op | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_v2(A64 *a, uint32_t op, int rd, int rn) { a64_emit(a, op | (uint32_t)rn << 5 | (uint32_t)rd); }
/* Shifts by an immediate (es: element bytes; shift 1..bits-1 for the right shifts, 0..bits-1
 * for shl): shl, ushr, sshr, usra (vd += vn >> shift). */
static inline void a64_vshl(A64 *a, int es, int rd, int rn, int shift) {
    a64_emit(a, 0x4f005400u | (uint32_t)(es * 8 + shift) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_vushr(A64 *a, int es, int rd, int rn, int shift) {
    a64_emit(a, 0x6f000400u | (uint32_t)(es * 16 - shift) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_vsshr(A64 *a, int es, int rd, int rn, int shift) {
    a64_emit(a, 0x4f000400u | (uint32_t)(es * 16 - shift) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_vusra(A64 *a, int es, int rd, int rn, int shift) {
    a64_emit(a, 0x6f001400u | (uint32_t)(es * 16 - shift) << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* ext vd.16b, vn.16b, vm.16b, #index */
static inline void a64_ext(A64 *a, int rd, int rn, int rm, int index) {
    a64_emit(a, 0x6e000000u | (uint32_t)rm << 16 | (uint32_t)index << 11 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* movi vd.16b, #imm8 ; movi/mvni vd.4s, #imm8, lsl #(8 * byte) ; fmov vd.4s, #1.0 */
static inline void a64_movi_16b(A64 *a, int rd, uint8_t imm8) {
    a64_emit(a, 0x4f00e400u | (uint32_t)(imm8 >> 5) << 16 | (uint32_t)(imm8 & 31) << 5 | (uint32_t)rd);
}
static inline void a64_movi_4s(A64 *a, int invert, int rd, uint8_t imm8, int byte) {
    a64_emit(a, (invert ? 0x6f000400u : 0x4f000400u) | (uint32_t)(imm8 >> 5) << 16 | (uint32_t)(byte * 2) << 12 |
                    (uint32_t)(imm8 & 31) << 5 | (uint32_t)rd);
}
static inline void a64_fmov_one_4s(A64 *a, int rd) { a64_emit(a, 0x4f03f600u | (uint32_t)rd); }
/* fmov sd, sn / dd, dn: the low 4 or 8 bytes, the rest of the register zeroed */
static inline void a64_fmov_reg(A64 *a, int dbl, int rd, int rn) {
    a64_emit(a, (dbl ? 0x1e604000u : 0x1e204000u) | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* csinv rd, rn, rm, cc (rd = cc ? rn : ~rm) */
static inline void a64_csinv(A64 *a, int sf, int rd, int rn, int rm, int cc) {
    a64_emit(a, (uint32_t)sf << 31 | 0x5a800000u | (uint32_t)rm << 16 | (uint32_t)cc << 12 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* mrs xt, cntvct_el0 (the virtual counter, readable at EL0 on macOS) */
static inline void a64_mrs_cntvct(A64 *a, int rt) { a64_emit(a, 0xd53be040u | (uint32_t)rt); }
/* mov vd.16b, vn.16b */
static inline void a64_vmov(A64 *a, int rd, int rn) { a64_vlogic(a, 1, rd, rn, rn); }
/* movi vd.2d, #0 */
static inline void a64_vzero(A64 *a, int rd) { a64_emit(a, 0x6f00e400u | (uint32_t)rd); }
/* fcmp sn|dn, sm|dm */
static inline void a64_fcmp(A64 *a, int dbl, int rn, int rm) {
    a64_emit(a, 0x1e202000u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rm << 16 | (uint32_t)rn << 5);
}
/* fcsel sd|dd, sn|dn, sm|dm, cc */
static inline void a64_fcsel(A64 *a, int dbl, int rd, int rn, int rm, int cc) {
    a64_emit(a, 0x1e200c00u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rm << 16 | (uint32_t)cc << 12 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* scvtf s|d, w|x ; fcvtzs w|x, s|d ; fcvt between s and d */
static inline void a64_scvtf(A64 *a, int sf, int dbl, int rd, int rn) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1e220000u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_fcvtzs(A64 *a, int sf, int dbl, int rd, int rn) {
    a64_emit(a, (uint32_t)sf << 31 | 0x1e380000u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_fcvt_sd(A64 *a, int to_double, int rd, int rn) {
    a64_emit(a, (to_double ? 0x1e22c000u : 0x1e624000u) | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* Vector min/max are NaN-propagating on arm64; x86 semantics come from fcmgt + bsl. */
/* fcmgt vd.4s|2d, vn, vm ; bsl vd.16b, vn, vm (vd = vd ? vn : vm, bitwise) */
static inline void a64_fcmgt_v(A64 *a, int dbl, int rd, int rn, int rm) {
    a64_emit(a, 0x6ea0e400u | (uint32_t)(dbl ? 1 : 0) << 22 | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_bsl(A64 *a, int rd, int rn, int rm) {
    a64_emit(a, 0x6e601c00u | (uint32_t)rm << 16 | (uint32_t)rn << 5 | (uint32_t)rd);
}
/* SIMD loads/stores, unsigned offset: size 2 s, 3 d, 4 q. */
static inline void a64_ldr_v(A64 *a, int size, int rt, int rn, uint32_t offset) {
    static const uint32_t base[5] = {0x3d400000u, 0x7d400000u, 0xbd400000u, 0xfd400000u, 0x3dc00000u};
    a64_emit(a, base[size] | ((offset >> size) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
static inline void a64_str_v(A64 *a, int size, int rt, int rn, uint32_t offset) {
    static const uint32_t base[5] = {0x3d000000u, 0x7d000000u, 0xbd000000u, 0xfd000000u, 0x3d800000u};
    a64_emit(a, base[size] | ((offset >> size) & 0xfff) << 10 | (uint32_t)rn << 5 | (uint32_t)rt);
}
/* fmov s|d <-> w|x */
static inline void a64_fmov_to_gpr(A64 *a, int sf, int rd, int rn) {
    a64_emit(a, (sf ? 0x9e660000u : 0x1e260000u) | (uint32_t)rn << 5 | (uint32_t)rd);
}
static inline void a64_fmov_from_gpr(A64 *a, int sf, int rd, int rn) {
    a64_emit(a, (sf ? 0x9e670000u : 0x1e270000u) | (uint32_t)rn << 5 | (uint32_t)rd);
}

#endif
