# 上下文接口：普通恢复、信号 PC 分类与剩余恢复合同

2026-10-26；仅隔离源码候选，没有更新正式应用、正式数据或 Release。原有六项接口、Metal 回写/资源生命周期、Mlock 和 module-finalizer 未被旧 overlay 覆盖。

本轮完成了两个可供整合的有界候选：普通私有 libunwind 的 `__Ux86_64_setcontext` 恢复、`_is_signal_return` 的实际 0/1/2 分类。**`sigreturn` 尚未完成，不能把三个接口或全部九项写成已完成。** 普通恢复支持已证实的展开语义，不冒充完整信号恢复。

## 普通恢复的实际推进

源码在 `src/guest_ordinary_unwind.h`、`ordinary_unwind.asm`、`ordinary_unwind_bytes.h`，以及 `vendor/aether/src/core/fex/fex_guest_engine.{h,cpp}` 的最小边界查询。作者汇编与字节数组重新组装比对一致，第三方顺序参考的许可保存在 `ordinary_unwind_LICENSE.txt`。

- 只接受固定 libc `_Ux86_64_getcontext` 的 166 字节身份，SHA-256 为 `e679c2718887d1942a2858930726956ae4a1d8ef113ff95cbd0d7abfdc362272`；实际 FP 起点为 Ucontext `+0x120`。
- 在真正已经 spill 的 HLE 边界确认 thread owner、RSP 和 invocation 深度。新增 `AtHleBoundary` 的 RAII 标记，避免“有 FEX invocation”被错当成“寄存器已安全落盘”。只允许根调用 depth=1；嵌套 host callback 明确拒绝，不跳过 C++ 析构。
- 输入来自当前线程拥有的 stack scope，整个 0x500 字节受保护复制，核对长度、格式、FP owner、扩展位、MXCSR、只读 guest RIP 和同栈向上恢复目标。采用冻结的只读 alias，客体不能把它重新设为可写。每线程首次需要时才用一个 16 KiB 物理页，无新大缓存、无每帧成本。
- 验证通过后先正常返回 FEX，再由 guest x86 veneer 完成普通寄存器/FXRSTOR/栈与 RIP 转移；没有调用 Darwin setcontext/sigreturn，也没有 host longjmp 跨越 C++。

普通分支保留当前 guest mask、TLS 和 alt-stack；不承诺恢复信号语义、完整 RFLAGS 或未证明的 AVX 扩展。固定 `_Ux86_64_getcontext` 没有写 uc_sigmask，因此不能从未初始化区恢复 mask。新查到的 fpPS4 setcontext 却先调用 SIG_SETMASK，这部分与本地 producer 不一致，**没有照搬**。

反例测试还发现并修复了一个恢复叶问题：`sigsetjmp(...,0)` 在一次 SIGBUS/SEGV 后可能留下屏蔽位，使第二次坏输入无法进入恢复。两个 POD 复制叶改用 `sigsetjmp(...,1)` 恢复进入前的宿主信号掩码，并恢复原来的 recovery 槽；不修改 guest signal mask。

## 新找到的两类返回 PC 规则

两份公开源码提供了相同的实际实现：

- [fpPS4：ps4_signal.pas，固定提交 04cefd43](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/kernel/ps4_signal.pas#L67)
- [psOff：libkernel entry.cpp，固定提交 a36de91](https://github.com/SysRay/psOff_public/blob/a36de91aa9c87fcadc28e22a0468b4e535380446/modules/libkernel/entry.cpp#L70)

它们将 PC 小于 4096 的特殊值直接分类为 1；PC 起点的完整 19 字节为 `lea rdi,[rsp+0x40]; push 0; mov rax,0x1a1; syscall; hlt; jmp` 时为 1；否则 PC 前 5 字节为 `mov r10,rcx; syscall` 时为 2；其余为 0。低地址分支是两个上游共同的显式 sentinel 规则，包含零，不是让坏地址绕过内存检查并读取。

`src/guest_signal_return_classifier.h` 独立表达这些指令特征。与上游裸指针读取不同，本候选还要求 loader 已准入固定 ABI；除低地址 sentinel 外，PC 和所读整段必须属于单个永久、不可写 guest 可执行映射。类型 1/2 两边界都检查溢出与范围；实际复制异常为 EFAULT；未知 ABI 为 ENOTSUP。若没有足够已拥有字节证实“不匹配”，不会草率返回普通类型 0。没有新增代码扫描缓存。

[FreeBSD 9 sigtramp.S](https://github.com/freebsd/freebsd-src/blob/0d9d75b124880ee5a3aeb5c2af92a04340c02bef/sys/amd64/amd64/sigtramp.S#L41) 也解释了第一类来源：内核把 signal trampoline 放入用户地址空间，调用 handler 后以 frame 内 ucontext 执行 sigreturn。它能解释模式来源，不能单独证明 PS4 所有结构偏移。

## 为什么 sigreturn 仍不能安全登记

有界调查已覆盖 fpPS4、psOff、GPCS4 和原 ps4delta（现在为 prosperity）的相关公开 kernel/signal 源码，另复核当前 Aether 实现。没有把“搜索结果为空”写成项目从未实现的证明。实际新发现是：

1. [fpPS4 signal.inc](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/sys/signal.inc#L240) 的 signal Mcontext 包含 LBR/aux 字段，FP 位于 Ucontext +0x140；同项目的普通 setcontext 却访问 +0x120。其 [Windows signal 分发](https://github.com/red-prig/fpPS4/blob/04cefd43e6fddd1ab033e7980cd356d14c964905/sys/sys_signal.pas#L981) 最终用保存的 Windows CONTEXT/NtContinue，不能据此得到可直接移植的 PS4 内核 sigreturn。
2. 当前 Aether `exception.h` 的 Ucontext 是 0x500，Mcontext 是 0x480，FP 也在 +0x140；RFLAGS +0xf0、FS/GS base +0x480/+0x488、stack +0x4c8、uc_flags +0x4e0。这些尺寸和关键偏移已由定向测试中的编译期断言核实；完整边界列于 `evidence/context-interfaces-integration-20261026/signal-layout-boundary.json`，明确标记为**当前头文件布局，并非已准入的游戏 signal ABI**。
3. 当前 `FlushPendingOrbisSignal()` 是 Unity 场景的继承实现：有些寄存器会先清零，可能用临时/虚构 RIP、RSP，只填部分上下文，然后经嵌套 callback 调 handler；没有产生可恢复的完整 PS4 signal frame、完整 FP/flags/mask、原始 onstack 状态或相应 retcode。当前 Bloodborne 路径亦无已证明的调用 producer。FEX 自身 AArch64 hlt 返回槽不是 PS4 guest retcode。

因此仅凭“头文件存在”接一个较小 subset 也不安全：还不能证明传入的是哪种布局，连合法输入集合都未证实；未知 FP、flags、stack 或 mask 都必须明确拒绝，不能空成功。当前不注册 `sigreturn`。

剩余工作已经缩到明确合同，不要求先建设一般 POSIX signal 系统：

| 最小前置 | 必须拿到的事实/实现 |
|---|---|
| 输入身份 | 真正使用的 signal frame producer、布局版本，以及 return PC 对应的 frame/ucontext 地址；compact +0x120 与 signal +0x140 必须区分 |
| 完整状态 | 同一安全边界内真实 spill 的全部 GPR、RFLAGS、FP/xstate、FS/GS；不能从仍含 host SRA 的状态拼接 |
| 生命周期 | 同线程有效 frame、拥有的栈/alt-stack、嵌套次序和一次消费边界；不接受另一线程或过期 context |
| guest 软件状态 | producer 保存的 128-bit mask、onstack/stack 与支持的扩展；不支持的扩展在改变任何状态前拒绝 |
| 非返回采用 | 以结构化 FEX continuation 一次采用；所有 native 锁和 RAII 先正常释放，不以宿主 longjmp 越过 callback |

[FreeBSD 9 sys_sigreturn](https://github.com/freebsd/freebsd-src/blob/0d9d75b124880ee5a3aeb5c2af92a04340c02bef/sys/amd64/amd64/machdep.c#L424) 对 flags/CS、FP、TLS、onstack 和 mask 的完整验证及 `EJUSTRETURN`，正好说明普通寄存器 veneer 不等价于内核信号恢复。这里借用合同检查范围，不照抄数值 ABI。

## 定向验证与整合位置

执行 `python3 tests/test_ordinary_unwind_fex.py --project /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。整合前最后结果：**16 个普通恢复场景、11 个分类场景、282 断言，真实 FEX 与实际 HLE adapter；3.73 秒**。另一个进程验证 depth=2 的真实嵌套 callback 拒绝，退出 74，未发启动器 75/76 重启语义。分类覆盖完整类型 1/2、篡改、普通 PC、低地址临界、PC−5 段边界、段末不足 19 字节、可写数据和溢出；两个恢复叶先后遇到真实保护页错误仍可安全拒绝。

新可执行、状态与编译输出只放在 fresh Shared evidence/state，准备好的 FEX archive 未被修改；没有游戏、GUI、GPU 或长 benchmark。最终证据为 Shared `evidence/ordinary-unwind-current-1792996763297790000/result.json`；仓库 `evidence/context-interfaces-integration-20261026/summary.json` 保存精简结果和当前源码 SHA。较早一次 Python 报告字段不匹配，以及随后暴露连续信号错误的超时测试均保留为未通过记录，不算成功。

### 本轮源码整合与 sigreturn 收口复审

两项候选现在已接入 `src/game_runtime.cpp`，**仅 `BB_ARM_CONTEXT_IMPORTS=1` 生效**；host/guest-linked 优先、未知 ABI 显式缺项、`sigreturn` 不注册。普通恢复目标和分类器读取共用 `immutable_executable`，只使用启动前发布的永久范围，拒绝可写重叠和运行中可释放的 VMA。Mlock/finalizer 的原实现和路由保留。最终路由可直接核对 `src/game_runtime.cpp` 的开发提交；较早的 proposal 和本机生成 patch 仅作历史草案，不参与构建。

继续核对真实调用约束后，完整 signal 恢复可以收窄为一个候选入口，**无需先实现通用 POSIX 系统**：先只考虑根 invocation、当前线程拥有的普通栈、已证明的 legacy FP schema 和当前 TLS；嵌套 callback、alt-stack、变更 TLS、未知 xstate 明确不支持。它可以沿用“冻结快照 → 正常返回 FEX → guest veneer”的普通恢复架构，另恢复 R10/R11、RFLAGS 和正确的信号 FP 区；不需要宿主长跳或立即改共享 HLE ABI。但是，当前还不能把这个设计当可登记的正实现，具体缺口如下：

1. **已有直接调用只证明指针转发。** 固定 libc 的一条已解码 `sigreturn` call 在 +0x547bf，参数来自 cursor +0x138 指向的 frame 再 +0x40；若返回便转到终止路径。这证明“信号 frame 内的 Ucontext”，不证明它是 `getcontext` 创建的 compact buffer，也不证明真实 FP 在 +0x140。这次有限扫描不能排除间接调用。
2. **现有 handler callback 在返回后覆盖目标。** `FlushPendingOrbisSignal()` 的 `HandleCallback` 返回后，无条件复制 `saved_gprs`、`saved_rip` 和 `saved_rsp`，所以即便在 handler 内填好恢复状态，也会被外层覆盖；handler 内非返回跳到外层还会绕过 native callback 的 RAII。根深度限制可以明确拒绝它，而不能把它当已经可用的完整 signal-return 通路。
3. **当前 HLE frame 没有完整状态提交合同。** `HleCallFrame` 只有 GPR、XMM 和 RSP，没有 RFLAGS、FS/GS、x87/xstate 或“采用 continuation”标记；`HandleSyscall` 也只复制 GPR/XMM。直接给某个 native `frame.rip` 赋值不能证明 OS_GENERIC 的返回分派会采用它。普通 guest veneer 避开了这个问题；完整恢复仍要在一个支持的真实输入上证明对应字段和提交序列。
4. **最小真实前置尚缺。** 仍需要同一版本的一份真实 signal producer/返回入口证据，确认 frame+0x40 的布局、保存 flags/FP/TLS/mask 的来源和所有权。尤其 +0x120 在继承 signal 头中是 LBR 字段，在普通 producer 却是 FXSAVE，不能靠字段数值“像合法”自动猜布局。拿到这一事实后，根/同栈 subset 才能编写正控；之前只能检验拒绝，不应造一个永远 ENOTSUP 的导入冒充完成。

本轮没有改这些公共 FEX/HLE 状态字段，也没有为信号另建无限生命周期表。`sigreturn-constraint-audit.json` 把调用点、已有提交字段和上述根深度方案分别记录；这是精确的剩余边界，不是全游戏实现承诺。

整合后当前 game_runtime.cpp 通过 `-fsyntax-only`（使用当前 D 源码/shim 头和已有准备依赖）；已有 27 场景、282 断言实际 FEX 控制再通过，用时 3.36 秒；Shared `evidence/ordinary-unwind-current-1792997378879745000/result.json` 保存本轮最终证据。完整游戏功能、性能/内存匹配与最终包仍留给统一收尾，不能用语法通过或夹具通过替代。
