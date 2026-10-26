// SPDX-License-Identifier: GPL-2.0-or-later
.text
.globl _CheckGuestHookState
_CheckGuestHookState:
push %rbp
push %rbx
push %r12
push %r13
push %r14
push %r15
sub $24,%rsp
mov %rdi,%r15
mov %rsi,%r12
mov %rdx,%r13
vmovdqu 0(%r12),%ymm0
vmovdqu 32(%r12),%ymm1
vmovdqu 64(%r12),%ymm2
vmovdqu 96(%r12),%ymm3
vmovdqu 128(%r12),%ymm4
vmovdqu 160(%r12),%ymm5
vmovdqu 192(%r12),%ymm6
vmovdqu 224(%r12),%ymm7
vmovdqu 256(%r12),%ymm8
vmovdqu 288(%r12),%ymm9
vmovdqu 320(%r12),%ymm10
vmovdqu 352(%r12),%ymm11
vmovdqu 384(%r12),%ymm12
vmovdqu 416(%r12),%ymm13
vmovdqu 448(%r12),%ymm14
vmovdqu 480(%r12),%ymm15
mov $0x13579,%rax
mov $0x13579,%rcx
mov $0x13579,%rdx
mov $0x13579,%rdi
mov $0x13579,%rsi
mov $0x13579,%r8
mov $0x13579,%r9
mov $0x13579,%r10
mov $0x13579,%r11
stmxcsr (%rsp)
stc
pushfq
pop %r14
call *%r15
pushfq
pop %rbx
cmp %r14,%rbx
jne 1f
cmp $0x13579,%rax
jne 1f
cmp $0x13579,%rcx
jne 1f
cmp $0x13579,%rdx
jne 1f
cmp $0x13579,%rdi
jne 1f
cmp $0x13579,%rsi
jne 1f
cmp $0x13579,%r8
jne 1f
cmp $0x13579,%r9
jne 1f
cmp $0x13579,%r10
jne 1f
cmp $0x13579,%r11
jne 1f
vmovdqu %ymm0,0(%r13)
vmovdqu %ymm1,32(%r13)
vmovdqu %ymm2,64(%r13)
vmovdqu %ymm3,96(%r13)
vmovdqu %ymm4,128(%r13)
vmovdqu %ymm5,160(%r13)
vmovdqu %ymm6,192(%r13)
vmovdqu %ymm7,224(%r13)
vmovdqu %ymm8,256(%r13)
vmovdqu %ymm9,288(%r13)
vmovdqu %ymm10,320(%r13)
vmovdqu %ymm11,352(%r13)
vmovdqu %ymm12,384(%r13)
vmovdqu %ymm13,416(%r13)
vmovdqu %ymm14,448(%r13)
vmovdqu %ymm15,480(%r13)
stmxcsr 4(%rsp)
mov (%rsp),%eax
cmp 4(%rsp),%eax
jne 1f
mov $1,%eax
jmp 2f
1:
xor %eax,%eax
2:
vzeroupper
add $24,%rsp
pop %r15
pop %r14
pop %r13
pop %r12
pop %rbx
pop %rbp
ret
.globl _ClobberGuestHookState
_ClobberGuestHookState:
vzeroall
sub $8,%rsp
stmxcsr (%rsp)
xorl $0x2000,(%rsp)
ldmxcsr (%rsp)
add $8,%rsp
xor %rax,%rax
xor %rcx,%rcx
xor %rdx,%rdx
xor %rdi,%rdi
xor %rsi,%rsi
xor %r8,%r8
xor %r9,%r9
xor %r10,%r10
xor %r11,%r11
clc
ret
