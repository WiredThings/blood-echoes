// SPDX-License-Identifier: GPL-2.0-or-later
// Authored structural controls only. This neither implements nor invokes any
// guest/native sigreturn, setcontext, thread finalizer, or game code.
#include <cstddef>
#include <cstdio>
#include <signal.h>
#include "core/libraries/kernel/threads/exception.h"
#include "guest_module_info.h"
using Libraries::Kernel::Mcontext;
using Libraries::Kernel::Ucontext;
static_assert(sizeof(BbGuestModules::Info)==424);
static_assert(offsetof(Ucontext,uc_mcontext)==0x40);
static_assert(offsetof(Mcontext,mc_rip)==0xa0);
static_assert(offsetof(Mcontext,mc_rsp)==0xb8);
static_assert(offsetof(Mcontext,mc_len)==0xc8);
static_assert(offsetof(Mcontext,mc_fpstate)==0x100);
static_assert(offsetof(Mcontext,mc_fsbase)==0x440);
static_assert(offsetof(Ucontext,uc_mcontext)+offsetof(Mcontext,mc_rsp)==0xf8);
// Local libc's unwinder reads saved RSP at sigframe+0x138, then passes
// sigframe+0x40 to the sigreturn import. Check the observed subset only.
static_assert(0x40+offsetof(Ucontext,uc_mcontext)+offsetof(Mcontext,mc_rsp)==0x138);
static_assert(sizeof(Mcontext)==0x480 && sizeof(Ucontext)==0x500);
// Darwin's native ucontext stores a machine-context pointer; Orbis embeds it.
static_assert(offsetof(ucontext_t,uc_mcontext)!=offsetof(Ucontext,uc_mcontext));
static_assert(sizeof(ucontext_t)!=sizeof(Ucontext));
int main(){
 std::printf("{\"structural_assertions\":12,\"orbis_mcontext_size\":%zu,\"orbis_ucontext_size\":%zu,\"darwin_ucontext_size\":%zu,\"native_context_invoked\":false,\"full_guest_abi_verified\":false}\n",sizeof(Mcontext),sizeof(Ucontext),sizeof(ucontext_t));
}
