/* bbcpu: running one guest function instruction by instruction with its memory accesses seen
 * (record.c): the recorder of BB_RECORD and tools/recomp/replay.c. */
#ifndef BB_CPU_TRACE_H
#define BB_CPU_TRACE_H
#include "cpu_internal.h"
#include "record.h"

/* Memory accesses: PRE before the instruction runs (its operands), POST after it (the same
 * operands: changed bytes are writes); READ and WRITE after a string instruction. */
enum { BB_TRACE_PRE, BB_TRACE_POST, BB_TRACE_READ, BB_TRACE_WRITE };
/* How the function leaves its code: a call, a tail call (jump out of [start, end)), an int3 hook. */
enum { BB_TRACE_CALL, BB_TRACE_TAIL, BB_TRACE_TRAP };

typedef struct BbTrace BbTrace;
struct BbTrace {
    BbCpu *cpu;
    uint64_t start, end; /* the function's code */
    uint64_t entry_rsp;
    uint64_t instructions;
    uint64_t limit; /* stop after this many instructions (0: none); `stopped` is then set */
    int stopped;
    void (*access)(BbTrace *t, int kind, uint64_t address, uint32_t length);
    /* Runs the call (CALL: cpu->rip is the return point; TAIL: the function's return address is
     * on the stack; TRAP: cpu->rip is the int3) and leaves the cpu where the function resumes. */
    void (*call)(BbTrace *t, const BbInsn *in, uint64_t target, int how);
    /* An instruction whose accesses the tracer cannot list (the record is not usable). */
    void (*unsupported)(BbTrace *t, const BbInsn *in);
    /* Optional: before each instruction of the function (replay --lockstep). */
    void (*before)(BbTrace *t, uint64_t rip);
};

/* Runs the function at cpu->rip (its return address on top of the stack) until it returns; with
 * t->entry_rsp set, from the middle of it (its return address at entry_rsp). */
void bbcpu_trace(BbTrace *t);
void bbcpu_trace_regs(const BbCpu *cpu, BbRecRegs *regs);
void bbcpu_trace_set_regs(BbCpu *cpu, const BbRecRegs *regs);

/* BB_RECORD (record.c): whether `rip` starts a function being recorded (the JIT leaves its first
 * instruction to bbcpu_step), whether this call is to be recorded, and the recording itself
 * (returns the rip to continue at, after the function returned). */
extern int bbcpu_record_armed;
int bbcpu_record_target(uint64_t rip);
int bbcpu_record_wants(uint64_t rip);
uint64_t bbcpu_record_call(BbCpu *cpu);

/* Native versions of game functions (native.c): the host function replacing `address`, or NULL. */
extern int bbcpu_native_count;
extern uint64_t bbcpu_native_calls; /* calls run natively (statistics) */
const void *bbcpu_native_at(uint64_t address);
/* Runs native function `fn` for a guest call about to be made (its arguments in the registers,
 * no return address pushed; interp.c). */
void bbcpu_call_native(BbCpu *cpu, const void *fn);

/* Probes (native.c): run the probes at cpu->rip, if any (bbcpu_step, and translated code at the
 * start of a probed block; decode.c starts a block at each probed address). */
extern int bbcpu_probe_count;
int bbcpu_probe_at(uint64_t address);
void bbcpu_run_probe(BbCpu *cpu);
/* A guarded call's return lands on BB_GUARD_RETURN (not guest code): the dispatchers call
 * bbcpu_guard_return, which runs the guard's leave and continues at the real return address. */
#define BB_GUARD_RETURN UINT64_C(0xbb6a7d0000000000)
void bbcpu_guard_return(BbCpu *cpu);

/* Recompiled game functions (recomp.c, src/recomp/rc.h): the one at `address`, or NULL. */
typedef void (*RcFn)(BbCpu *cpu);
extern int bbcpu_recomp_count;
extern uint64_t bbcpu_recomp_calls;
/* Counts a call of a recompiled function: per thread (in its BbCpu, not TLS: a lookup per call),
 * added to bbcpu_recomp_calls in batches (one shared counter costs as much as a short function
 * when every thread calls them). */
static inline void bbcpu_recomp_counted(BbCpu *cpu) {
    if (++cpu->recomp_pending == 4096) {
        __atomic_add_fetch(&bbcpu_recomp_calls, cpu->recomp_pending, __ATOMIC_RELAXED);
        cpu->recomp_pending = 0;
    }
}
RcFn bbcpu_recomp_at(uint64_t address);
/* Whether this thread runs recompiled functions (BB_RECOMP_THREADS, diagnostics). Without a
 * thread filter (bbcpu_recomp_load sets the flag) only the flag is read. */
extern int bbcpu_recomp_threads_filtered;
int bbcpu_recomp_thread_named(void);
static inline int bbcpu_recomp_thread_ok(void) {
    return !bbcpu_recomp_threads_filtered || bbcpu_recomp_thread_named();
}
/* The hash of the code at [start, start + size) the recompiler goes by (recomp.c): instruction
 * bytes, the loader's TCB loads (mov reg, fs:[0], rewritten as gs loads) as one token. */
uint64_t bbcpu_recomp_hash(uint64_t start, uint64_t size);
/* Whether `in` loads the TCB pointer: mov reg, fs:[0] or the loader's gs form of it. */
int bbcpu_is_tcb_load(const BbInsn *in);
/* One instruction in the interpreter, without bbcpu_step's hooks (record, native, recompiled). */
uint64_t bbcpu_step_insn(BbCpu *cpu, const BbInsn *in);

#endif
