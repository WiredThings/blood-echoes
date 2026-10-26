; SPDX-License-Identifier: MIT
; Restore sequencing follows the MIT-licensed libunwind x86_64 setcontext
; implementation; see ordinary_unwind_LICENSE.txt. Offsets/fixture are local work.
; Authored guest routine: matches observed normal libunwind subset.
; This is the final guest restore sequence, not sigreturn; its adapter must own a validated,
; immutable, 16-byte-aligned context and writable target stack before entry.
; No game bytes copied. Offsets are specific to the observed local libc ABI.
bits 64
default rel
cmp qword [rdi+0x118], 0x20001
jne .gprs
cmp qword [rdi+0x110], 0x10002
jne .gprs
fxrstor [rdi+0x120]
.gprs:
mov r8,  [rdi+0x68]
mov r9,  [rdi+0x70]
mov rbx, [rdi+0x80]
mov rbp, [rdi+0x88]
mov r12, [rdi+0xa0]
mov r13, [rdi+0xa8]
mov r14, [rdi+0xb0]
mov r15, [rdi+0xb8]
mov rsi, [rdi+0x50]
mov rdx, [rdi+0x58]
; Stage both values before changing RSP: context can overlap the old stack.
mov rcx, [rdi+0xf8]
mov rax, [rdi+0xe0]
mov [rcx-8], rax
mov rax, [rdi+0x48]
mov [rcx-16], rax
mov rax, [rdi+0x78]
mov rcx, [rdi+0x60]
mov rsp, [rdi+0xf8]
sub rsp, 16
pop rdi
ret
