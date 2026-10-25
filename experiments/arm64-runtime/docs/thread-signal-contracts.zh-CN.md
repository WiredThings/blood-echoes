# 线程与信号上下文剩余导入审查（2026-10-25）

本轮只读审查 `__pthread_cxa_finalize`、`_is_signal_return`、`sigreturn`、`__Ux86_64_setcontext`，另做不执行游戏的自造结构控制。四项仍未接入，不能计作兼容性完成。没有改公共源码、重建共享目标、启动游戏、调用宿主上下文恢复函数或触碰正式数据。`sceKernelMlock` 由另一专项负责。

## 实际调用结构

在固定本地 1.09 链接映像中找到四项各一个 libc 直接调用或尾跳入口。通过导入重定位定位 GOT，再检查相对分支及其参数。原映像 SHA、地址和已核对的源码 SHA 放在同目录 `private-callsite-facts.json`，不导出游戏字节或反汇编。扫描只证明这些直接分支存在，不是所有间接路径的枚举，更不证明最近游玩时已调用。

| 导入 | NID | 已观察的输入与用途 |
|---|---|---|
| `__pthread_cxa_finalize` | `kbw4UHHSYy0#p#J` | libc 模块终结流程末尾，传入与模块地址查询、地址匹配相同的输出缓冲区；不是 DSO 指针，也不是当前线程句柄。 |
| `_is_signal_return` | `crb5j7mkk1c#p#J` | 传入 unwind cursor 的保存指令地址；返回 1 和 2 分别进入两种信号帧路径，0 保持普通帧。 |
| `sigreturn` | `mo0bFmWppIw#p#J` | unwind 恢复信号帧时，取保存的信号帧地址加 0x40；若调用返回，后续进入终止路径。 |
| `__Ux86_64_setcontext` | `OjWstbIRPUo#p#J` | 普通 unwind 恢复分支传入保存的上下文指针；经 libc 尾跳进入该导入，与信号帧的恢复分支分开。 |

`__pthread_cxa_finalize` 输入指向本项目已验证的 424 字节 Orbis 模块信息。虽然 FreeBSD 同名函数接收 `dl_phdr_info *`，不能把那种结构传入或直接套用宿主函数。三项信号/上下文导入属于 unwind 和控制流恢复；它们不是可以用普通 C 包装器返回 0 的查询函数。

## `__pthread_cxa_finalize`：可先实现，不能误作线程退出

[FreeBSD 9 的 thr_fork.c](https://raw.githubusercontent.com/freebsd/freebsd-src/releng/9.0/lib/libthr/thread/thr_fork.c) 表明，此接口用于清退归属已终结模块的注册项：移除匹配模块代码地址的 atfork 记录，并调用 TSD 和信号清理辅助函数。它本身不逐一调用当前线程析构函数，也不释放全部 TLS。

[thr_spec.c](https://raw.githubusercontent.com/freebsd/freebsd-src/releng/9.0/lib/libthr/thread/thr_spec.c) 的 `_thr_tsd_unload` 只把属于该模块的 key destructor 指针置空，保留 key 的分配状态和线程值。实际线程退出时才清理对应线程值并按迭代规则运行仍有效的析构。注意：[thr_sig.c](https://raw.githubusercontent.com/freebsd/freebsd-src/releng/9.0/lib/libthr/thread/thr_sig.c) 的 `_thr_sigact_unload` 在该版本内是 `#if 0`，不能把禁用代码宣称为 FreeBSD 9 实际行为。

当前项目已有可复用基础：

- `runtime_kernel.c` 的固定 256 项 key 表、key lock、四轮线程退出清理；`runtime.c` 的模块 TLS 分配/释放；`runtime_thread.c` 已在普通线程退出及桥接工作线程分离时先做 key 清理再释放 TLS。
- `BbGuestModules::Info/Match` 提供真实模块信息和执行段归属；不需要新增模块扫描器或大型常驻表。
- 当前 runtime 没有 atfork 注册实现；现有 `signal()` 只是记录处理函数，明确不转发宿主信号。不能为了清掉这个导入，把缺少的完整信号系统也当已完成。

建议先实现真实的模块终结注册清理：复制并完整验证输入模块描述，持 key lock 只清空匹配执行段的 destructor；不删除 key、不调用 destructor、不释放线程数据或 TLS。无匹配项可自然返回，但函数仍实际查表，不能做无条件空返回。如以后支持 atfork，必须让注册与模块终结共用同一注册表及生命周期锁。是否另外清理本项目 signal registry 应独立记录为兼容层策略，不能归称 FreeBSD 9 原样行为，更不能卸载 FEX/Darwin 的 SIGBUS/SIGSEGV 故障处理器。

并发门槛不能漏：现有线程 key 清理没有持有 key lock 就读取 destructor 并调用；因此只在新增终结函数里上锁还不够，清理端也必须参与同一生命周期协议。模块终结与此并发时，仅将表项置空不足以保证旧回调不再运行。要明确由模块卸载层持有执行租约，或为已取出的回调建立有界 in-flight 计数并等到回调结束，禁止持有 key lock 调回客体。当前程序只静态映射固定模块，但不能借此把未处理的卸载竞态称作安全完成。

集成后应补真实 FEX 控制：只清匹配模块、执行段尾边界、数据段地址不命中、无匹配、重复调用、完整输入跨页不可读、畸形描述零副作用；同时证明未调用 destructor、未删除 key/线程值、线程退出仍能执行另一模块 destructor。并发控制应覆盖终结与线程退出，以及析构内再次注册 key，避免锁内回调和死锁。

## 上下文结构：已确认的子集与未确认的部分

本地 Aether/shadPS4 `exception.h` 的显式客体结构是：`Ucontext` 1280 字节，内嵌 `Mcontext` 1152 字节；`uc_mcontext` 在 +0x40，`mc_rsp` 在机器上下文 +0xb8，故 Ucontext 的 RSP 为 +0xf8。本地 libc 信号展开路径先从信号帧 +0x138 取保存 RSP，恢复时传信号帧 +0x40，二者与该头文件在这个子集上一致。

ARM64 实编译/执行以及 x86_64 交叉语法编译通过 12 个结构断言，见 `context_layout_controls.cpp`。本机 Darwin `ucontext_t` 只有 56 字节，机器上下文是指针，位置也不同；把客体结构交给 Darwin `sigreturn` 或 `setcontext` 必错。测试完全没有调用这些宿主函数。

这只验证布局和本地调用使用的子集，**没有完整验证 PS4 内核 ABI**。尤其是 `_is_signal_return` 返回 2 对应的第二种 trampoline、信号帧完整布局、AVX/XSAVE 扩展标志、机器上下文长度及安全校验规则仍缺直接证据。不能把 FreeBSD 9 的较小 FP 状态结构直接当成 PS4 结构，也不能把 Aether 注释当实机证明。

## 恢复接口不能直接挂普通包装器

[libunwind 的 setcontext.S](https://raw.githubusercontent.com/libunwind/libunwind/master/src/x86_64/setcontext.S) 是可参考的机器状态恢复实现，明确保留恢复的 RAX，而不是把它改成普通函数的返回 0；[Gresume.c](https://raw.githubusercontent.com/libunwind/libunwind/master/src/x86_64/Gresume.c) 也把信号恢复与普通上下文恢复分开。这支持当前 libc 分支的解释，但不能证明 PS4 所有改动与其一致。

当前桥接的实际限制：`HleCallFrame` 只有 GPR、128 位 XMM、RSP 和地址验证回调；没有 RIP、RFLAGS、YMM 高半、x87、MXCSR 或 FS/GS 恢复通道。`BridgeSyscallHandler::HandleSyscall` 按普通 syscall 语义复制 GPR/XMM 后继续返回。仅改 `frame.gpr` 或栈返回地址不能完成完整上下文恢复。

FEX 本身有可复用的 `SetFlagsFromCompactedEFLAGS`、`SetXMMRegistersFromState` 等 API。Linux emulation 的 SignalDelegator 也有信号帧存取流程，可作为状态覆盖和重入的参考；但 Linux 客体帧、主机 signal context 和 PS4 客体结构互不等价，不能直接绑定 Linux rt_sigreturn。

本地 Aether `DeliverGuestOrbisSignal/FlushPendingOrbisSignal` 是另一套延迟回调流程，包含特定平台的地址窗口和简化上下文，只保存部分状态；当前原型没有通过它接入这三个缺失导入。不能因为源码里有同名“signal”能力就称可直接复用完整恢复。

安全实现应先增加显式“客体上下文转移”结果，交给 FEX 执行层在已溢出寄存器的安全边界处理：完整复制并校验客体输入，验证目标 RIP 可执行、RSP 和必要 scratch 写范围可写、长度/标志/FP 特性受支持；全校验完成才一次提交机器状态。普通恢复与 signal mask/alt-stack 恢复分别处理。嵌套客体回调要逐层传播控制流转移，不能用宿主 longjmp 穿过 C++ 锁和析构。失败必须明确报错并保持原状态，不能返回成功后继续错误路径。

后续真实 FEX 测试应使用自造 x86 代码，覆盖：非零 RAX 哨兵、RIP/RSP 跳转、全部 GPR、RFLAGS、XMM/YMM、x87/MXCSR、FS/GS、信号掩码和嵌套回调；失败例覆盖输入截断、目标不可执行/栈不可写、未知扩展、非法标志且无部分状态写入。每项必须验证实际跳到目标及原调用点不再继续，普通 C 模型通过不算完成。

## `_is_signal_return` 的门槛

[libunwind FreeBSD 路径](https://raw.githubusercontent.com/libunwind/libunwind/master/src/x86_64/Gos-freebsd.c) 通过精确 trampoline 识别信号帧，不把所有地址判作普通帧。PS4 libc 额外区分 1、2 两种结果，因此不能直接抄其单一字节模式，也不能永远返回 0。

先查清原版两个 trampoline 范围/签名和返回值映射，再用当前已发布、版本绑定的客体 trampoline 注册描述符实现分类；分类只接受明确的客体可执行身份，不随意探测任意宿主内存。正例要覆盖两类及内部返回 PC，负例覆盖普通函数、近邻、改一字节、不可读/跨页等。若第二类缺直接证据，保留未完成项；不要猜一个函数签名来消掉导入计数。

## 建议顺序与本轮结论

1. 先把 `__pthread_cxa_finalize` 的模块归属清理及回调生命周期补齐，定向测试后集成；不需要新增大缓存。
2. 同时补齐两种 PS4 trampoline 与上下文完整布局的证据，定义 FEX 非返回控制转移接口；这一路修改执行引擎，必须由根代理统一整合。
3. 完整恢复通过自造 FEX 正负例后，再接入 `sigreturn/__Ux86_64_setcontext`，最后做真实游戏验收。

四项都不是本轮已实现接口。本轮控制只证明结构事实，没有游戏/FPS/内存收益结论。正式应用、存档、设置和缓存均未改，Release 未更新。
