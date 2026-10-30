/* bbcpu: native versions of game functions (docs/RECOMPILATION.md). A call of a replaced function
 * reaches the dispatcher (the JIT leaves its first instruction untranslated), and bbcpu_step calls
 * the native function through the guest -> host bridge instead. */
#include "trace.h"
#include <stdio.h>
#include <stdlib.h>

enum { BUCKETS = 4096 };
typedef struct Native { uint64_t address; const void *fn; struct Native *next; } Native;
static Native *buckets[BUCKETS];
int bbcpu_native_count;
uint64_t bbcpu_native_calls;

static uint32_t bucket(uint64_t address) { return (uint32_t)((address * 0x9e3779b97f4a7c15ull) >> 52); }

void bbcpu_set_native(uint64_t address, const void *fn) {
    Native *n = malloc(sizeof(*n));
    if (!n) abort();
    *n = (Native){address, fn, buckets[bucket(address)]};
    buckets[bucket(address)] = n;
    ++bbcpu_native_count;
}

const void *bbcpu_native_at(uint64_t address) {
    for (const Native *n = buckets[bucket(address)]; n; n = n->next)
        if (n->address == address) return n->fn;
    return NULL;
}

/* Probes and guards: like natives, a hash of addresses; the probe (or a guard's enter) runs and
 * the guest code continues. */
typedef struct Probe {
    uint64_t address;
    BbProbe fn;
    BbGuardEnter enter;
    BbGuardLeave leave;
    uint32_t tag;
    struct Probe *next;
} Probe;
static Probe *probes[BUCKETS];
int bbcpu_probe_count;

static void add_probe(Probe p) {
    Probe *n = malloc(sizeof(*n));
    if (!n) abort();
    *n = p;
    n->next = probes[bucket(p.address)];
    probes[bucket(p.address)] = n;
    ++bbcpu_probe_count;
}

void bbcpu_set_probe(uint64_t address, BbProbe fn, uint32_t tag) {
    add_probe((Probe){.address = address, .fn = fn, .tag = tag});
}

void bbcpu_set_guard(uint64_t address, BbGuardEnter enter, BbGuardLeave leave, uint32_t tag) {
    add_probe((Probe){.address = address, .enter = enter, .leave = leave, .tag = tag});
}

static const Probe *probe_at(uint64_t address) {
    for (const Probe *p = probes[bucket(address)]; p; p = p->next)
        if (p->address == address) return p;
    return NULL;
}

int bbcpu_probe_at(uint64_t address) { return probe_at(address) != NULL; }

/* Guarded calls in progress on this thread: their real return addresses. */
enum { GUARD_DEPTH = 64 };
typedef struct { uint64_t ret, cookie; BbGuardLeave leave; } GuardFrame;
static _Thread_local GuardFrame guard_frames[GUARD_DEPTH];
static _Thread_local int guard_depth;

size_t bbcpu_guard_cookies(BbGuardLeave leave, uint64_t *cookies, size_t capacity) {
    size_t count = 0;
    for (int i = 0; i < guard_depth && count < capacity; ++i)
        if (guard_frames[i].leave == leave) cookies[count++] = guard_frames[i].cookie;
    return count;
}

void bbcpu_run_probe(BbCpu *cpu) {
    /* Several entries may share the address (a probe and a guard): all run. */
    for (const Probe *p = probes[bucket(cpu->rip)]; p; p = p->next) {
        if (p->address != cpu->rip) continue;
        if (p->fn) {
            p->fn(cpu->r, cpu->rip, p->tag);
            continue;
        }
        if (guard_depth == GUARD_DEPTH) continue; /* too deep: this call runs unguarded */
        const uint64_t cookie = p->enter(cpu->r, p->tag);
        if (!cookie) continue;
        uint64_t *slot = (uint64_t *)(uintptr_t)cpu->r[RSP];
        guard_frames[guard_depth++] = (GuardFrame){*slot, cookie, p->leave};
        *slot = BB_GUARD_RETURN;
    }
}

void bbcpu_guard_return(BbCpu *cpu) {
    if (guard_depth == 0) {
        fprintf(stderr, "STOP: bbcpu: a return to the guard marker with no guarded call\n");
        abort();
    }
    const GuardFrame f = guard_frames[--guard_depth];
    cpu->rip = f.ret;
    f.leave(f.cookie);
}
