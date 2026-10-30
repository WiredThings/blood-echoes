/* bbcpu: recorded calls of game functions, for checking decompiled ones (docs/RECOMPILATION.md).
 *
 * BB_RECORD=OFFSET:SIZE[,OFFSET:SIZE...] (image offsets and sizes from tools/recomp/scan.c): calls of
 * these functions are run by the tracer below, one instruction at a time, and written to
 * BB_RECORD_DIR/<offset>.rec (default out/recomp/records) in the format of record.h. BB_RECORD_CALLS
 * calls per function (default 50), one of every BB_RECORD_EVERY (default 1).
 *
 * The tracer (bbcpu_trace) reads the memory each instruction accesses before and after it runs:
 * the first read of each byte in an epoch is an observation, a changed byte a write. Calls the
 * function makes run at full speed (callbacks); tools/recomp/replay.c answers them from the
 * record instead. */
#include "trace.h"
#include "record.h"
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

#define M(name) ZYDIS_MNEMONIC_##name

/* ---- the tracer ---- */

typedef struct { uint64_t address; uint32_t length; } Access;

static int is_string_op(const BbInsn *in) {
    switch (in->mnemonic) {
    case M(MOVSB): case M(MOVSW): case M(MOVSQ): case M(STOSB): case M(STOSW): case M(STOSD):
    case M(STOSQ): case M(LODSB): case M(LODSW): case M(LODSD): case M(LODSQ): case M(CMPSB):
    case M(CMPSW): case M(CMPSQ): case M(SCASB): case M(SCASW): case M(SCASD): case M(SCASQ):
        return 1;
    case M(MOVSD): case M(CMPSD): /* also SSE instructions: the string forms have no XMM operand */
        return in->count == 0 || !(in->op[0].type == OP_REG && in->op[0].kind == RK_VEC);
    default:
        return 0;
    }
}

static int string_size(int m) {
    switch (m) {
    case M(MOVSB): case M(STOSB): case M(LODSB): case M(CMPSB): case M(SCASB): return 1;
    case M(MOVSW): case M(STOSW): case M(LODSW): case M(CMPSW): case M(SCASW): return 2;
    case M(MOVSD): case M(STOSD): case M(LODSD): case M(CMPSD): case M(SCASD): return 4;
    default: return 8;
    }
}

/* The memory an instruction accesses (computed before it runs); -1 when the tracer cannot say. */
static int accesses(const BbCpu *cpu_c, const BbInsn *in, Access *out) {
    BbCpu *cpu = (BbCpu *)cpu_c;
    switch (in->mnemonic) {
    case M(NOP): case M(PREFETCHNTA): case M(PREFETCHT0): case M(PREFETCHT1): case M(PREFETCHT2):
    case M(PREFETCHW): case M(PREFETCH): case M(CLFLUSH): case M(CLDEMOTE): case M(LEA):
        return 0;
    default:
        break;
    }
    int n = 0;
    for (int o = 0; o < in->count; ++o) {
        const BbOp *op = &in->op[o];
        if (op->type != OP_MEM) continue;
        if (op->size == 0 || op->size > 64) return -1; /* fxsave and the like */
        out[n++] = (Access){bbcpu_addr(cpu, in, op), op->size};
    }
    const uint64_t rsp = cpu->r[RSP];
    switch (in->mnemonic) {
    case M(PUSH): {
        const uint32_t size = in->op[0].size == 2 ? 2 : 8;
        out[n++] = (Access){rsp - size, size};
        break;
    }
    case M(POP):
        if (in->op[0].type == OP_MEM) return -1; /* addressed with the incremented rsp */
        out[n++] = (Access){rsp, in->op[0].size == 2 ? 2 : 8};
        break;
    case M(PUSHFQ): out[n++] = (Access){rsp - 8, 8}; break;
    case M(POPFQ): out[n++] = (Access){rsp, 8}; break;
    case M(LEAVE): out[n++] = (Access){cpu->r[RBP], 8}; break;
    case M(RET): out[n++] = (Access){rsp, 8}; break;
    case M(MASKMOVDQU): case M(VMASKMOVDQU): out[n++] = (Access){cpu->r[RDI], 16}; break; /* the masked bytes */
    case M(ENTER): return -1;
    default: break;
    }
    return n;
}

/* After a string instruction: the memory it read and wrote, from the registers it moved. */
static void string_accesses(BbTrace *t, const BbInsn *in, uint64_t rcx, uint64_t rsi, uint64_t rdi) {
    const BbCpu *cpu = t->cpu;
    const int m = in->mnemonic, size = string_size(m);
    const uint64_t count = (in->rep || in->repne) ? rcx - cpu->r[RCX] : 1;
    if (!count) return;
    const int down = (cpu->flags & F_DF) != 0;
    const uint64_t bytes = count * (uint64_t)size;
    const uint64_t src = down ? rsi - bytes + (uint64_t)size : rsi;
    const uint64_t dst = down ? rdi - bytes + (uint64_t)size : rdi;
    for (uint64_t done = 0; done < bytes; done += 64) { /* in pieces: Access lengths are small */
        const uint32_t piece = (uint32_t)(bytes - done < 64 ? bytes - done : 64);
        switch (m) {
        case M(MOVSB): case M(MOVSW): case M(MOVSD): case M(MOVSQ):
            t->access(t, BB_TRACE_READ, src + done, piece);
            t->access(t, BB_TRACE_WRITE, dst + done, piece);
            break;
        case M(STOSB): case M(STOSW): case M(STOSD): case M(STOSQ):
            t->access(t, BB_TRACE_WRITE, dst + done, piece);
            break;
        case M(LODSB): case M(LODSW): case M(LODSD): case M(LODSQ):
            t->access(t, BB_TRACE_READ, src + done, piece);
            break;
        case M(CMPSB): case M(CMPSW): case M(CMPSD): case M(CMPSQ):
            t->access(t, BB_TRACE_READ, src + done, piece);
            t->access(t, BB_TRACE_READ, dst + done, piece);
            break;
        default: /* scas */
            t->access(t, BB_TRACE_READ, dst + done, piece);
            break;
        }
    }
}

void bbcpu_trace(BbTrace *t) {
    BbCpu *cpu = t->cpu;
    if (!t->entry_rsp) t->entry_rsp = cpu->r[RSP]; /* preset: the rest of a function, from its middle */
    for (;;) {
        const BbBlock *block = bbcpu_block(cpu->rip);
        for (uint32_t i = 0; i < block->count; ++i) {
            const BbInsn *in = &block->insn[i];
            const uint64_t rip = cpu->rip, next = rip + in->length;
            const int m = in->mnemonic;
            if (t->before) t->before(t, rip);
            Access acc[8];
            const int n = accesses(cpu, in, acc);
            if (n < 0) t->unsupported(t, in);
            for (int a = 0; a < n; ++a) t->access(t, BB_TRACE_PRE, acc[a].address, acc[a].length);
            if (++t->instructions > t->limit && t->limit) {
                t->stopped = 1;
                return;
            }
            if (m == M(CALL) || m == M(JMP)) {
                const BbOp *op = &in->op[0];
                const uint64_t target = op->type == OP_IMM ? rip + (uint64_t)op->disp : bbcpu_read(cpu, in, op);
                /* Out of the function with the stack as at its entry: a tail call. With the frame
                 * still in use it is code of this function elsewhere (a part the compiler moved
                 * out): traced on. */
                const int leaves = (target < t->start || target >= t->end || !bbcpu_is_guest_code(target)) &&
                                   (cpu->r[RSP] == t->entry_rsp || !bbcpu_is_guest_code(target));
                if (m == M(CALL)) {
                    cpu->rip = next;
                    t->call(t, in, target, BB_TRACE_CALL);
                    break;
                }
                if (leaves) { /* a tail call: the function ends when it returns */
                    t->call(t, in, target, BB_TRACE_TAIL);
                    return;
                }
                cpu->rip = target;
                break;
            }
            if (m == M(INT3)) { /* a hook patched into the game: host code, like a call */
                t->call(t, in, rip, BB_TRACE_TRAP);
                break;
            }
            const int ends = m == M(RET) && cpu->r[RSP] == t->entry_rsp;
            const uint64_t rcx = cpu->r[RCX], rsi = cpu->r[RSI], rdi = cpu->r[RDI];
            const uint64_t to = bbcpu_step(cpu, in);
            for (int a = 0; a < n; ++a) t->access(t, BB_TRACE_POST, acc[a].address, acc[a].length);
            if (is_string_op(in)) string_accesses(t, in, rcx, rsi, rdi);
            cpu->rip = to;
            if (ends) return;
            if (in->branch || to != next) break;
        }
    }
}

void bbcpu_trace_regs(const BbCpu *cpu, BbRecRegs *regs) {
    memset(regs, 0, sizeof(*regs));
    memcpy(regs->r, cpu->r, sizeof(regs->r));
    regs->rip = cpu->rip;
    regs->flags = cpu->flags;
    regs->fs_base = cpu->fs_base;
    regs->gs_base = cpu->gs_base;
    regs->tcb = cpu->tcb;
    regs->mxcsr = cpu->mxcsr;
    for (int i = 0; i < 16; ++i) memcpy(regs->v[i], cpu->v[i].b, 32);
}

void bbcpu_trace_set_regs(BbCpu *cpu, const BbRecRegs *regs) {
    memcpy(cpu->r, regs->r, sizeof(cpu->r));
    cpu->rip = regs->rip;
    cpu->flags = regs->flags;
    cpu->mxcsr = regs->mxcsr;
    for (int i = 0; i < 16; ++i) memcpy(cpu->v[i].b, regs->v[i], 32);
}

/* ---- the recorder ---- */

extern uint64_t bb_image_base __attribute__((weak));
static uint64_t image_base(void) { return &bb_image_base ? bb_image_base : 0; }

enum { MAX_TARGETS = 1 << 16, TARGET_SLOTS = 1 << 17, LOG_LIMIT = 64 << 20 };
/* The return address of the calls the recorded function makes (never guest code). */
#define SENTINEL UINT64_C(0x0000700000000f00)

typedef struct {
    uint64_t offset, size;
    uint64_t seen, recorded, racy, failed;
} Target;
static Target *targets;
static int target_count, max_calls = 50, every = 1;
/* Target index + 1 by image offset (open addressing). */
static uint32_t *slots;
static const char *directory = "out/recomp/records";
int bbcpu_record_armed;
static _Thread_local int in_record;
static pthread_mutex_t file_lock = PTHREAD_MUTEX_INITIALIZER;

static uint32_t slot_of(uint64_t offset) { return (uint32_t)((offset * 0x9e3779b97f4a7c15ull) >> 47) & (TARGET_SLOTS - 1); }

static void add_target(uint64_t offset, uint64_t size) {
    if (target_count == MAX_TARGETS) return;
    targets[target_count] = (Target){.offset = offset, .size = size};
    uint32_t i = slot_of(offset);
    while (slots[i]) i = (i + 1) & (TARGET_SLOTS - 1);
    slots[i] = (uint32_t)++target_count;
}

/* BB_RECORD=OFFSET:SIZE,... or @FILE (lines "OFFSET SIZE" or "OFFSET:SIZE"). */
__attribute__((constructor)) static void record_init(void) {
    const char *spec = getenv("BB_RECORD");
    if (!spec || !*spec) return;
    targets = calloc(MAX_TARGETS, sizeof(*targets));
    slots = calloc(TARGET_SLOTS, sizeof(*slots));
    if (!targets || !slots) abort();
    if (spec[0] == '@') {
        FILE *f = fopen(spec + 1, "r");
        if (!f) { perror(spec + 1); return; }
        char line[256];
        while (fgets(line, sizeof(line), f)) {
            char *end;
            const uint64_t offset = strtoull(line, &end, 0);
            if (end == line) continue;
            while (*end == ':' || *end == ' ' || *end == '\t') ++end;
            const uint64_t size = strtoull(end, NULL, 0);
            if (size) add_target(offset, size);
        }
        fclose(f);
    } else {
        for (const char *p = spec; *p;) {
            char *end;
            const uint64_t offset = strtoull(p, &end, 0);
            if (end == p) break;
            uint64_t size = 0;
            if (*end == ':') size = strtoull(end + 1, &end, 0);
            if (size) add_target(offset, size);
            else fprintf(stderr, "BB_RECORD: %#llx needs its size (OFFSET:SIZE)\n", (unsigned long long)offset);
            p = *end == ',' ? end + 1 : end;
        }
    }
    const char *env = getenv("BB_RECORD_CALLS");
    if (env) max_calls = atoi(env);
    env = getenv("BB_RECORD_EVERY");
    if (env && atoi(env) > 0) every = atoi(env);
    env = getenv("BB_RECORD_DIR");
    if (env && *env) directory = env;
    bbcpu_record_armed = target_count > 0;
    if (target_count > 16) printf("Record: %d functions\n", target_count);
}

static Target *find_target(uint64_t rip) {
    const uint64_t base = image_base();
    if (rip < base) return NULL;
    const uint64_t offset = rip - base;
    for (uint32_t i = slot_of(offset); slots[i]; i = (i + 1) & (TARGET_SLOTS - 1))
        if (targets[slots[i] - 1].offset == offset) return &targets[slots[i] - 1];
    return NULL;
}

int bbcpu_record_target(uint64_t rip) { return bbcpu_record_armed && find_target(rip) != NULL; }

/* Shadow memory of the current epoch: the bytes the function saw or wrote, and their values. */
typedef struct Page {
    uint64_t page;
    uint32_t epoch;
    uint8_t known[512];
    uint8_t value[4096];
    struct Page *next;
} Page;

typedef struct {
    BbTrace trace;
    Target *target;
    uint8_t *log;
    size_t log_size, log_capacity;
    uint32_t events, calls, epoch, racy, failed;
    Page *pages[1024];
} Recorder;

static Page *page_of(Recorder *r, uint64_t address) {
    const uint64_t number = address >> 12;
    Page **slot = &r->pages[(number * 0x9e3779b97f4a7c15ull) >> 54];
    for (Page *p = *slot; p; p = p->next) {
        if (p->page != number) continue;
        if (p->epoch != r->epoch) {
            memset(p->known, 0, sizeof(p->known));
            p->epoch = r->epoch;
        }
        return p;
    }
    Page *p = calloc(1, sizeof(*p));
    if (!p) abort();
    p->page = number;
    p->epoch = r->epoch;
    p->next = *slot;
    *slot = p;
    return p;
}

static void log_bytes(Recorder *r, const void *data, size_t size) {
    if (r->failed) return;
    if (r->log_size + size > LOG_LIMIT) {
        r->failed = BBREC_TOO_BIG;
        return;
    }
    if (r->log_size + size > r->log_capacity) {
        r->log_capacity = (r->log_size + size) * 2;
        r->log = realloc(r->log, r->log_capacity);
        if (!r->log) abort();
    }
    memcpy(r->log + r->log_size, data, size);
    r->log_size += size;
}

static void log_event(Recorder *r, uint8_t kind, uint8_t tail, uint64_t address, const void *data,
                      uint32_t length) {
    const BbRecEvent e = {.kind = kind, .tail = tail, .length = length, .address = address};
    log_bytes(r, &e, sizeof(e));
    log_bytes(r, data, length);
    ++r->events;
}

static void log_regs(Recorder *r, uint8_t kind, uint8_t tail, uint64_t address) {
    BbRecRegs regs;
    bbcpu_trace_regs(r->trace.cpu, &regs);
    log_event(r, kind, tail, address, &regs, sizeof(regs));
}

static void record_access(BbTrace *t, int kind, uint64_t address, uint32_t length) {
    Recorder *r = (Recorder *)t;
    if (r->failed) return;
    uint8_t now[64];
    memcpy(now, (const void *)address, length);
    int unknown = 0, changed = 0;
    for (uint32_t i = 0; i < length; ++i) {
        Page *p = page_of(r, address + i);
        const uint32_t at = (uint32_t)((address + i) & 4095);
        const int known = (p->known[at >> 3] >> (at & 7)) & 1;
        if (!known) unknown = 1;
        else if (p->value[at] != now[i]) {
            changed = 1;
            if (kind == BB_TRACE_PRE || kind == BB_TRACE_READ) ++r->racy;
        }
        p->known[at >> 3] |= (uint8_t)(1u << (at & 7));
        p->value[at] = now[i];
    }
    if ((kind == BB_TRACE_PRE || kind == BB_TRACE_READ) && unknown)
        log_event(r, BBREC_OBSERVE, 0, address, now, length);
    else if ((kind == BB_TRACE_POST && changed) || kind == BB_TRACE_WRITE)
        log_event(r, BBREC_WRITE, 0, address, now, length);
}

static void record_call(BbTrace *t, const BbInsn *in, uint64_t target, int how) {
    Recorder *r = (Recorder *)t;
    BbCpu *cpu = t->cpu;
    log_regs(r, BBREC_CALL, how == BB_TRACE_TAIL, target);
    if (how == BB_TRACE_TRAP) {
        cpu->rip = bbcpu_step(cpu, in);
    } else {
        /* The callee returns to SENTINEL, where bbcpu_run stops (translated code: full speed). */
        const uint64_t resume = how == BB_TRACE_TAIL ? bb_load(cpu->r[RSP], 8) : cpu->rip;
        if (how == BB_TRACE_TAIL) bb_store(cpu->r[RSP], 8, SENTINEL);
        else { cpu->r[RSP] -= 8; bb_store(cpu->r[RSP], 8, SENTINEL); }
        cpu->rip = target;
        bbcpu_run(cpu, SENTINEL);
        cpu->rip = resume;
    }
    log_regs(r, BBREC_RETURN, how == BB_TRACE_TAIL, 0);
    ++r->calls;
    ++r->epoch;
}

static void record_unsupported(BbTrace *t, const BbInsn *in) {
    Recorder *r = (Recorder *)t;
    (void)in;
    if (!r->failed) r->failed = BBREC_UNSUPPORTED;
}

static void make_directory(const char *path) {
    char buffer[1024];
    snprintf(buffer, sizeof(buffer), "%s", path);
    for (char *p = buffer + 1; *p; ++p) {
        if (*p != '/') continue;
        *p = 0;
        mkdir(buffer, 0755);
        *p = '/';
    }
    mkdir(buffer, 0755);
}

int bbcpu_record_wants(uint64_t rip) {
    if (in_record) return 0;
    Target *target = find_target(rip);
    if (!target) return 0;
    const uint64_t seen = __atomic_fetch_add(&target->seen, 1, __ATOMIC_RELAXED);
    if (seen % (uint64_t)every) return 0;
    return __atomic_load_n(&target->recorded, __ATOMIC_RELAXED) < (uint64_t)max_calls;
}

uint64_t bbcpu_record_call(BbCpu *cpu) {
    Target *target = find_target(cpu->rip);
    in_record = 1;
    Recorder *r = calloc(1, sizeof(*r));
    if (!r) abort();
    r->target = target;
    r->trace = (BbTrace){.cpu = cpu,
                         .start = cpu->rip,
                         .end = cpu->rip + target->size,
                         .access = record_access,
                         .call = record_call,
                         .unsupported = record_unsupported};
    BbRecHeader header = {.magic = BBREC_MAGIC,
                          .version = BBREC_VERSION,
                          .offset = target->offset,
                          .image_base = image_base(),
                          .index = __atomic_load_n(&target->seen, __ATOMIC_RELAXED) - 1,
                          .return_address = bb_load(cpu->r[RSP], 8),
                          .tcb_address = (uint64_t)(uintptr_t)&cpu->tcb};
    BbRecRegs entry;
    bbcpu_trace_regs(cpu, &entry);
    bbcpu_trace(&r->trace);
    log_regs(r, BBREC_EXIT, 0, 0);

    header.instructions = r->trace.instructions;
    header.events = r->failed ? 0 : r->events;
    header.calls = r->calls;
    header.racy = r->racy;
    header.failed = r->failed;
    header.bytes = r->failed ? 0 : r->log_size;
    pthread_mutex_lock(&file_lock);
    const uint64_t recorded = __atomic_add_fetch(&target->recorded, 1, __ATOMIC_RELAXED);
    if (r->racy) ++target->racy;
    if (r->failed) ++target->failed;
    make_directory(directory);
    char path[1024];
    snprintf(path, sizeof(path), "%s/%#llx.rec", directory, (unsigned long long)target->offset);
    FILE *f = fopen(path, recorded == 1 ? "wb" : "ab");
    if (f) {
        fwrite(&header, sizeof(header), 1, f);
        fwrite(&entry, sizeof(entry), 1, f);
        if (!r->failed) fwrite(r->log, 1, r->log_size, f);
        fclose(f);
    } else {
        fprintf(stderr, "Record: %s: %s\n", path, strerror(errno));
    }
    if (target_count <= 16 && (recorded == (uint64_t)max_calls || recorded % 10 == 0))
        printf("Record: %#llx: %llu calls recorded of %llu seen (%llu with other threads' writes, "
               "%llu not usable)\n",
               (unsigned long long)target->offset, (unsigned long long)recorded,
               (unsigned long long)target->seen, (unsigned long long)target->racy,
               (unsigned long long)target->failed);
    pthread_mutex_unlock(&file_lock);

    for (int i = 0; i < 1024; ++i)
        for (Page *p = r->pages[i], *next; p; p = next) {
            next = p->next;
            free(p);
        }
    free(r->log);
    free(r);
    in_record = 0;
    return cpu->rip;
}
