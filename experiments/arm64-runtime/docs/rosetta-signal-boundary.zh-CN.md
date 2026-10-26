# 正式 Rosetta 的 signal/sigreturn 功能边界

2026-10-26，只读核对；未启动游戏，未改代码、正式数据、缓存或设置。结构化证据在 `evidence/context-interfaces-integration-20261026/rosetta-signal-boundary-audit.json`。

**正式 Rosetta runtime 也没有实现 PS4 `sigreturn` 导入或 PS4 syscall 417。** 当前剩余信号接口不是 ARM 桥漏接了正式版已有的完整功能，不能把它们全称作“去除 Rosetta 必须先修复的功能回归”。正式版能正常启动，依靠的是未解析项按需失败，以及日常场景没有走到那些不支持的路径。

## 导入调用实际如何处理

正式来源 `WiredThings/blood-echoes` 的固定版本 [`3e2419b5`](https://github.com/WiredThings/blood-echoes/tree/3e2419b5f1b1f291521d0b61ffcd99d3fcaf881a)，以及本地当前正式源码，均有以下行为：

1. [`runtime_resolve()`](https://github.com/WiredThings/blood-echoes/blob/3e2419b5f1b1f291521d0b61ffcd99d3fcaf881a/src/runtime.c#L204) 逐一查询 runtime 子模块和 GPU 导出。`sigreturn`、`_is_signal_return`、`__Ux86_64_setcontext` 没有对应实现。`import_names.inc` 中存在名称仅用于诊断，不代表能执行。
2. [`probe.c` 的导入重定位](https://github.com/WiredThings/blood-echoes/blob/3e2419b5f1b1f291521d0b61ffcd99d3fcaf881a/src/probe.c#L567) 先尝试 host resolver，再尝试已链接 guest export；均失败则保留 `unresolved` stub。真正调用时才打印未支持符号、返回地址并 `_exit(20)`。**它没有因为 import table 里存在这几项就禁止游戏启动。** `--strict-imports` 在原 probe 中会禁用 native-libc fallback，也不等于证明所有导入均已实现。
3. [`runtime_kernel.c`](https://github.com/WiredThings/blood-echoes/blob/3e2419b5f1b1f291521d0b61ffcd99d3fcaf881a/src/runtime_kernel.c#L183) 的 `signal()` 仅记录 handler，日志明确说明不转发 host signals；`sigprocmask()` 维护 guest TLS mask。没有 PS4 signal frame、retcode、alt-stack 生产者。probe 的宿主 fault 路径只处理 GPU tracking/受保护读取或诊断退出，不会把 Darwin ucontext 转成 PS4 signal frame 继续运行。

当前安装包 `bb-probe-metal` 只读检查为 Mach-O x86_64；`nm` 可见 `guest_signal`、`guest_sigprocmask`、`unresolved` 和 `fault`，没有上述三个导入的实现。没有运行该程序来探测异常 syscall。

## syscall 417 是另一条入口，但也没有实现

PS4 sigtramp 的 417（0x1a1）来自内核/guest signal return stub；它与调用 libc 的 `sigreturn` PLT 是两个入口，最终都需要恢复 PS4 context。

正式 `probe.c` 没有 PS4 syscall 号分派、syscall 417 特殊处理或将 syscall 指令转换成 sigreturn 的代码；prepare/link_modules 也不生成这一类信号桩。Rosetta 翻译 CPU 指令，不会凭空提供 PS4 内核 ABI。当前 SDK 的 Darwin `SYS_sigreturn` 是 184，而 417 是 `SYS_poll_nocancel`；因此不能把 PS4 417 转发 Darwin，后果也不能按 PS4 语义解释。本次没有执行未知号 syscall，亦不猜测 Rosetta 内部对此的具体异常结果。

已下载的固定 libc `4378b47f…` 在可执行区经原有 Capstone 线性 skip-data 解码，观察到 **0 个 syscall 指令**；它是导入 sigreturn，而不是自带 firmware 的 417 retcode。这个静态事实有解码覆盖边界，不是对所有间接执行的完整证明。

## 当前 boot 为什么不能提供另一套恢复实现

只读 BBPROBE5 元数据：boot SHA-256 `b5cb1da3a15bb853c2adbc9ef03d7d8058f729b247a0646370cf61f056398e1d`；共有 762 个导入、238 个 linked bindings，客体模块仅 libc 和 Fios2。

| 索引 | 导入 | linked binding |
|---|---|---|
| 728 | `_is_signal_return` | 无 |
| 729 | `sigreturn` | 无 |
| 730 | `__Ux86_64_setcontext` | 无 |

libc SHA-256 仍为 `4378b47f46f1d856824a6f971db1a0c3f41833b77f49d2fba9538f667a139166`。它们都是外部导入，没有藏在已链接模块中；本地模块亦不含 libkernel。旧的 libc call 点已证实是 frame 指针转发，不能补出不存在的 producer。

## 源头和固定提交核查范围

以下实际源码均没有三个接口的实现或 syscall 417 分派，signal handler 也仍是记录型；probe 均保留 lazy unsupported 退出。不是仅根据 GitHub 搜索结果下结论。

- 本地所有已存在的 DiAvisoo 上游分支（macos-0.3/0.4/0.5/0.51、native、KK、passes、optimized）和固定 [`8289f836`](https://github.com/DiAvisoo/bbport-mac-optimized/tree/8289f836e79e2ddd46228f58b69d0898555abb69)。逐文件/commit 记录在 JSON。
- [bmy `4de5fd1340` 的 runtime](https://github.com/bmy/bbport-mac/blob/4de5fd1340c8184dd88687fc07631ee2425cb587/src/runtime_kernel.c#L186) 与 [probe](https://github.com/bmy/bbport-mac/blob/4de5fd1340c8184dd88687fc07631ee2425cb587/src/probe.c#L132)。
- 当前 Metal 引擎 pin [Huax `11992198` 的 runtime](https://github.com/Huax-1839/bloodborne-macos/blob/11992198df6306dacf6ec37de57ee920db1acc11/src/runtime_kernel.c#L181) 与 [probe](https://github.com/Huax-1839/bloodborne-macos/blob/11992198df6306dacf6ec37de57ee920db1acc11/src/probe.c#L130)。它不是一个可直接复用的完整版 PS4 signal kernel。
- 本次在线核对的 [bbport 源头固定 `8185b88d`](https://github.com/deadinside28/bloodborne_pc/tree/8185b88d80573fa300595d5c54450183b4109935)，对应 runtime.c/runtime_kernel.c/probe.c 也没有这些实现。仅代表本次检查的该提交及文件，不扩展成所有历史版本都不存在的断言。

因此，较准确的兼容结论是：**普通恢复与分类器是在补强原版未覆盖的罕见上下文路径；完整 sigreturn 是原版同样缺少的能力。** 原版能玩不能证明这条路径完整，也不能保证以后触发异常/信号展开时不退出。ARM 当前默认亦保留 lazy missing，只有显式 `--strict-imports` 才会按未解析项提前拒绝；不能把该诊断模式造成的门槛与原版实现差异混淆。

这不撤销完成其余兼容工作的要求，但应把“恢复正式版已支持语义”和“新增原版没有的信号恢复能力”分清。没有可复用且版本已证明的 signal producer 时，继续拒绝未知 sigreturn 比猜布局、转发 Darwin 或伪造成功更准确。
