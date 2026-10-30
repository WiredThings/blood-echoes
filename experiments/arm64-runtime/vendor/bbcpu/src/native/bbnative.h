/* Native versions of the game's functions (docs/RECOMPILATION.md): the interface between the
 * program (the game, tools/recomp/replay.c) and a library of native functions (BB_NATIVE_LIB).
 *
 * The library exports BB_NATIVE_INIT. A native function has the game function's SysV signature
 * (integer and pointer arguments, then float and vector ones, in order): the program calls it
 * through the guest -> host call bridge, as it calls its own functions from the game. It reaches
 * the game's globals at api->image_base + offset and calls game functions through api->call. */
#ifndef BB_NATIVE_H
#define BB_NATIVE_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BB_NATIVE_API_VERSION 1u
#define BB_NATIVE_INIT "bb_native_init"

/* A call of a game function: SysV integer arguments (rdi rsi rdx rcx r8 r9) and vector ones
 * (xmm0-7, 16 bytes each), and how many of each the call passes (the replay compares those); its
 * results (rax, rdx, xmm0, xmm1). */
typedef struct { uint64_t gpr[6]; uint8_t xmm[8][16]; uint32_t gpr_count, xmm_count; } BbCallIn;
typedef struct { uint64_t rax, rdx; uint8_t xmm0[16], xmm1[16]; } BbCallOut;

typedef struct {
    uint32_t version;
    uint64_t image_base;
    /* Calls the game function at address `fn`. */
    void (*call)(uint64_t fn, const BbCallIn *in, BbCallOut *out);
} BbNativeApi;

/* What a native function returns, for the replay's comparison. */
enum { BB_RETURNS_NOTHING, BB_RETURNS_I32, BB_RETURNS_I64, BB_RETURNS_F32, BB_RETURNS_F64 };

typedef struct {
    uint64_t offset;      /* image offset of the game function */
    const void *function;
    uint32_t returns;     /* BB_RETURNS_* */
    const char *name;
} BbNativeFunction;

/* The library's entry point: keeps `api`, returns its functions. */
typedef const BbNativeFunction *(*BbNativeInit)(const BbNativeApi *api, size_t *count);

/* ---- for native code ---- */

extern const BbNativeApi *bb_api;

/* The game's memory at an image offset. */
#define BB_GAME(type, offset) ((type)(uintptr_t)(bb_api->image_base + (uint64_t)(offset)))

/* Calls of game functions (image offsets) with integer arguments; returns rax. */
static inline uint64_t bb_call(uint64_t offset, int count, const uint64_t *args) {
    BbCallIn in;
    memset(&in, 0, sizeof(in));
    for (int i = 0; i < count && i < 6; ++i) in.gpr[i] = args[i];
    in.gpr_count = (uint32_t)count;
    BbCallOut out;
    bb_api->call(bb_api->image_base + offset, &in, &out);
    return out.rax;
}
/* A virtual call: `fn` is the address read from the object's table. */
static inline uint64_t bb_call_address(uint64_t fn, int count, const uint64_t *args) {
    BbCallIn in;
    memset(&in, 0, sizeof(in));
    for (int i = 0; i < count && i < 6; ++i) in.gpr[i] = args[i];
    in.gpr_count = (uint32_t)count;
    BbCallOut out;
    bb_api->call(fn, &in, &out);
    return out.rax;
}
#define BB_CALL(offset, ...) bb_call((offset), (int)(sizeof((uint64_t[]){__VA_ARGS__}) / 8), (uint64_t[]){__VA_ARGS__})
#define BB_CALL0(offset) bb_call((offset), 0, NULL)
#define BB_VCALL(fn, ...) bb_call_address((fn), (int)(sizeof((uint64_t[]){__VA_ARGS__}) / 8), (uint64_t[]){__VA_ARGS__})

#ifdef __cplusplus
}
#endif
#endif
