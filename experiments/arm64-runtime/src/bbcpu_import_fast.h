// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Embedding-only API. Generated game code does not include this header and
// keeps its existing Rc/BbCpu layout, complete function checks and ABI.
#include "bbcpu.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint64_t syscall_rip,operation; } BbOwnedImport;
// Pure resolver, set before guest workers start: 0 is unsupported, 1 proves an
// owned MOV/MOV/SYSCALL/RET sequence, negative is malformed and fatal. The CPU
// then uses the EXISTING syscall handler and requested-stop protocol. The
// resolver never executes an HLE or changes guest state. No raw host jumps.
void bbcpu_set_owned_import_resolver(int (*resolve)(const BbGuestRegs*,BbOwnedImport*));
#ifndef __cplusplus
struct BbCpu;
int bbcpu_try_owned_import(struct BbCpu*,uint64_t target);
#endif
#ifdef __cplusplus
}
#endif
