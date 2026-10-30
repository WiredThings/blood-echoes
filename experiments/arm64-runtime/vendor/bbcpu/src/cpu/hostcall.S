/* bbcpu_hostcall(fn, BbHostArgs *a): calls host function fn with a guest call's arguments.
 * BbHostArgs (cpu_internal.h): gpr[8] @0, stack[16] @64, xmm[8] (32-byte slots) @192,
 * rax @448, rdx @456, xmm0 @464, xmm1 @496. */
#if defined(__APPLE__)
#define SYMBOL(name) _##name
#else
#define SYMBOL(name) name
#endif

#if defined(__x86_64__)
    .text
    .globl SYMBOL(bbcpu_hostcall)
SYMBOL(bbcpu_hostcall):
    push %rbp
    mov %rsp, %rbp
    push %rbx
    push %r12
    mov %rsi, %r12
    mov %rdi, %r11
    sub $128, %rsp
    xor %ecx, %ecx
1:  mov 64(%r12,%rcx,8), %rdx
    mov %rdx, (%rsp,%rcx,8)
    inc %ecx
    cmp $16, %ecx
    jne 1b
    movdqu 192(%r12), %xmm0
    movdqu 224(%r12), %xmm1
    movdqu 256(%r12), %xmm2
    movdqu 288(%r12), %xmm3
    movdqu 320(%r12), %xmm4
    movdqu 352(%r12), %xmm5
    movdqu 384(%r12), %xmm6
    movdqu 416(%r12), %xmm7
    mov 0(%r12), %rdi
    mov 8(%r12), %rsi
    mov 16(%r12), %rdx
    mov 24(%r12), %rcx
    mov 32(%r12), %r8
    mov 40(%r12), %r9
    mov $8, %eax /* vector registers used, for variadic callees */
    call *%r11
    mov %rax, 448(%r12)
    mov %rdx, 456(%r12)
    movdqu %xmm0, 464(%r12)
    movdqu %xmm1, 496(%r12)
    add $128, %rsp
    pop %r12
    pop %rbx
    pop %rbp
    ret

#elif defined(__aarch64__)
    .text
    .p2align 2
    .globl SYMBOL(bbcpu_hostcall)
SYMBOL(bbcpu_hostcall):
    stp x29, x30, [sp, #-32]!
    mov x29, sp
    str x19, [sp, #16]
    mov x19, x1
    mov x16, x0
    sub sp, sp, #112          /* stack[2..15]: SysV stack arguments past the 8 AAPCS registers */
    add x10, x19, #80          /* &stack[2] */
    mov x9, #0
1:  ldr x11, [x10, x9, lsl #3]
    str x11, [sp, x9, lsl #3]
    add x9, x9, #1
    cmp x9, #14
    b.ne 1b
    ldr q0, [x19, #192]
    ldr q1, [x19, #224]
    ldr q2, [x19, #256]
    ldr q3, [x19, #288]
    ldr q4, [x19, #320]
    ldr q5, [x19, #352]
    ldr q6, [x19, #384]
    ldr q7, [x19, #416]
    ldp x0, x1, [x19, #0]
    ldp x2, x3, [x19, #16]
    ldp x4, x5, [x19, #32]
    ldp x6, x7, [x19, #48]
    blr x16
    str x0, [x19, #448]
    str x1, [x19, #456]
    str q0, [x19, #464]
    str q1, [x19, #496]
    add sp, sp, #112
    ldr x19, [sp, #16]
    ldp x29, x30, [sp], #32
    ret
#else
#error bbcpu_hostcall: unsupported host architecture
#endif
