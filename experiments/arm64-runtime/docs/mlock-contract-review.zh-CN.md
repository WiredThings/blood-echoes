# sceKernelMlock 独立契约审查（2026-10-25）

## 结论和范围

**不要空成功，也不要直接把客体调用无限制转给 Darwin mlock。** 这是一项真实“页驻留”契约，不是内存建议或 Metal 资源保留提示。本轮完成只读调用路径审查、可执行有界算法模型及原生单页正控；没有安装到运行时，没有启动游戏，没有修改正式应用、存档、设置或缓存，没有 Git 提交/推送。

对固定 `boot-linked.bin` 的静态证据表明：当前游戏默认 libc 锁页选项关闭，**不能据此宣称动态调用次数/锁定量为零或接口不必实现**。需要正式挂接时的有界计数和实际 FEX 负例，才能关掉这个支持缺口。

## 固定镜像中的调用范围

`static-contract.json` 绑定 boot SHA-256 `b5cb1da3a15bb853c2adbc9ef03d7d8058f729b247a0646370cf61f056398e1d`，只存元数据、不存游戏代码。分析在标量读取时应用 BBPROBE5 kind0 重定位，避免将原始文件里的零指针误判为空配置。

- 唯一 `sceKernelMlock` 导入 NID `3k6kx-zOOSQ#p#J`；唯一导入重定位 GOT `0x5798748`，PLT `0x56e0360`。
- 唯一静态直接调用 `0x5703ed9`，位于 libc 的映射包装器 `0x5703e70`。包装器先调用 `sceKernelMapNamedFlexibleMemory`（`0x56e0350`），保护值 3、flags 0；成功后若锁页标志非零，用**整块映射地址与原始请求长度**调用 Mlock。Mlock 返回非零时立即 `sceKernelMunmap` 同址同长，然后返回 -1。不能吞掉锁页失败，否则会绕过 libc 自己的失败处理。
- 包装器的两个已验证直接调用点：初始化 `0x5703d44`、主 arena 增长 `0x5704047`。初始化大小来自 libc 配置/默认值；增长通常将需求加分配元数据后按至少 64 KiB 粒度取整，且存在配置控制的特殊分支。**不是每张纹理单独锁页，也不是固定只锁 16 KiB。** 未测运行时请求尺寸分布，不能拿测试上限冒充游戏需求。
- 锁页全局 `0x579fcbc` 初始 0，libc 可执行段中只找到初始化写入 `0x5703e56` 与包装器读取 `0x5703ec9`。
- 初始化通过真实 `sceKernelGetProcParam`，读主参数 `+0x38` 的 libc 参数。固定镜像重定位后指向 `0x513b320`（size `0x78`、version 9、type 1）。只有版本至少 5 且 libc 参数 `+0x50` 指向非零 int 时，才将全局标志设为 1。**本镜像这个 `+0x50` 指针为 NULL。** 现有 runtime_set_procparam 原样保存指针；没有发现为其制造锁页开关的现有代码。
- 固定导入表没有 `munlock` / `sceKernelMunlock`。这不证明没有间接/动态调用，也不应省略将来的解锁支持。整个镜像的直接相对分支候选经过已知 libc 指令边界核对；不声称完成了自修改代码、任意间接函数指针的可达性证明。

## 解锁和释放路径

libc 对 `sceKernelMunmap` 有八个直接调用点：`0x5702bc2`、`0x57030d5`、`0x5703ee9`、`0x5704009`、`0x570494b`、`0x5704e57`、`0x5704f6d`、`0x57066a7`。其中 `0x5703ee9` 明确是上面的映射/锁页失败清理，另有 arena/块回收上下文。并非每次普通 free 都 munmap：小块通常回到 libc 自己的空闲结构，**仍映射的页继续锁定属于调用者契约，不等于 Metal 资源泄漏**。

当前兼容层实际释放入口在 `vendor/bb_runtime/src/runtime_memory.c`：

1. `sceKernelMunmap` / `munmap` → `direct_unmap` → `unmap_locked`，先保持现有 GPU unmap 通知，再在 VMA 写锁内用 `MAP_FIXED` 的 `PROT_NONE` 匿名映射替换原映射，保留客体 VA 所有权，最后 `drop_range` / `trap_forget` / MIRROR_UNMAP。
2. `sceKernelReleaseFlexibleMemory` 复用同一路径；`drop_range` 释放 flexible offset bitmap 并减少 flexible_bytes。共享 backing view 存活不代表原 VA 的锁页义务存活。
3. `place` 的成功固定地址覆盖也替换映射并 drop 旧 VMA；失败不能先丢锁页账本。
4. `sceKernelReleaseDirectMemory` 遍历物理区间的客体别名，调用 `unmap_locked`，然后释放 Block、清零物理存储；每个被移除 VA 的锁页账目应分别归还。不同 VA 的物理别名不能简单按一个地址解锁全部。
5. F3 是退出子进程再启动，不是保留宿主 mlock 计数的 RAM 镜像；内核退出清理会解除该进程 wirings。账本必须进程内重建，不能写入快速存档或磁盘缓存。

**集成账本时应在真实 host munmap/MAP_FIXED 成功后退账，不再对旧地址调用 munlock。** 否则可能解锁已经占据相同 VA 的新映射。失败替换保留原账目，不能无条件清账。GPU 已有退休/镜像顺序不能因此改变。

## PS4 / FreeBSD / Darwin 语义分级

PS4 私有内核的完整行为没有公开一手证明。这里用固定游戏的真实 ABI/调用检查决定入口，再以它所沿用的 FreeBSD 9 实现作为兼容基线；**不能把 FreeBSD 或社区实现当作 PS4 硬件已验证结果**。

- [FreeBSD 9 sys_mlock/sys_munlock](https://github.com/freebsd/freebsd-src/blob/releng/9.0/sys/vm/vm_mmap.c)：起点下取整、终点上取整，溢出 EINVAL；缺页/洞或 wire 失败 ENOMEM，容量另有系统/进程约束。手册建议对齐，但实现实际处理未对齐范围，不能只凭手册加拒绝条件。
- [FreeBSD 9 vm_map_wire/unwire](https://github.com/freebsd/freebsd-src/blob/releng/9.0/sys/vm/vm_map.c)：`MAP_ENTRY_USER_WIRED` 是客体用户锁页集合状态；对已 user-wired 的重叠页不再加 wired_count，munlock 清该用户标记。**不能把客体每次重复 Mlock 都累加成必须逐次 Munlock 的引用数。** 内核/其他 owner 的 wiring 与这个用户标记分开。
- 同一 FreeBSD 实现的 `vm_map_sync` 在 `MS_INVALIDATE` 遇到 user-wired 区间时拒绝；sys_msync 映成 EBUSY。将来接账本时要与现在的 msync 补丁一起校验。
- [Apple XNU kern_mman.c](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_mman.c) 将 mlock 转为 user wire，返回 EINVAL/EAGAIN/EPERM/ENOMEM；munlock 注释说明“应移除全部，当前仅移除一次”。宿主 errno 要转换到 PS4/SCE 错误码，不应把 Darwin 原值直接作为 sceKernel 返回值。
- 本机原生实测 `native-one-page.json`：只分配并锁定同一 **16 KiB 页**；Mach `user_wired_count` 为 **0 → 第一次 mlock 1 → 重复 mlock 2 → 一次 munlock 1 → 清理后 0**，随后 munmap 成功。RLIMIT_MEMLOCK soft/hard 均 `9223372036854775807`。所以直接绑定既不能依赖系统软上限兜底，也会引入重复 wiring 的语义差异。

## 有界实现提案

`bounded_mlock_model.py` 是独立、可执行的**契约模型**，没有接入 runtime resolver，不是生产 C 实现；正例一定调用显式后端 lock/unlock，错误注入只存在于测试后端，不提供“无动作成功”路径。

建议先接如下最小策略，再做 FEX/真实调用验证：

- **显式硬上限**：候选单次 16 MiB、总驻留 64 MiB、最多 4096 个 16 KiB 页面条目。它们是安全实验上限，**不是已证明足够的游戏需求，也不是性能优化结论**。超限返回真实 SCE ENOMEM，不拆成无限多次请求、不中途成功、不静默改成 madvise。现在默认 flag 关闭不受这项策略影响；若将来某配置确需更大，应基于分布重新评估或报告不支持，不能默认扩大到 GB。
- 页面集合按 **VA + 映射代际** 管理。重复/重叠 guest 请求只对新页面执行一次 host mlock；按新页数计预算，物理别名按不同 VA 保守计费。不要每次客体调用累加 guest refcount。若宿主另有独立锁页 owner，必须有显式 owner 集合/共享 broker，不能和 Metal 或其他代码的 raw mlock 混账。当前模型只覆盖唯一 guest owner。
- **生命周期租约**：验证整段是可锁定的客体已提交 VMA，排除 hole/reserved/native-owned；在真实 syscall 完成前阻止同区间解除/覆盖。统一顺序 VMA 生命周期锁 → 账本锁 → syscall，不能拿账本锁再等 State/VMA 锁。当前模型测试了账本并发去重，外部 VMA 租约是集成前置条件，不能把模型当现有 C 锁序已验证。
- 预检溢出、取整、单次/总预算、元数据容量后才调用 host；固定大小 C 账本可控制在约 64 KiB，避免每帧分配、无界日志或与纹理数量成比例增长。页粒度参考模型便于精确回滚；实际 C 版本可合并连续新页，但必须保留同样事务边界并测 syscall 次数。
- host 部分失败时只回滚本次新增 wirings，保留旧 guest 页集合。munlock 部分失败要恢复已移除部分。若回滚本身失败，仍准确保留实际 pinned/已解锁状态、占用预算，进入显式错误退出，**不得继续客体执行且隐藏驻留**；错误退出用正常故障码，不用原监督器的 75/76 重启码。
- 解除/固定覆盖成功后再移除对应代际账目；局部解除保留其余页。mprotect 不释放锁页。退出清账不保留跨进程状态。
- 不将 mlock 当 GPU fence，不变更两级页表、GPU dirty/protection、速度图、遮挡查询、资源退休和纹理缓存预算；它不能替代这些同步逻辑，也不应清纹理缓存。离线模式没有新增网络状态。
- 增加默认关闭、固定大小诊断计数：调用/失败数、原始与取整大小、当前/峰值 wired bytes、重复命中、各错误原因；只保留少量首条失败样本，不用全量日志刷屏。实际调用为零也要明确报告“该轮未覆盖”，不能记实现通过。

## 定向验证结果和后续门槛

- **22 项 authored 模型测试全部通过**：真实后端调用要求、重复/重叠去重、取整/零长度、单次/总量/条目界限、空洞/未发布地址/溢出、锁页失败局部回滚、回滚失败准确保留状态并禁止继续、解锁失败恢复、unmap 成功退账/失败保账、地址复用、别名保守记账、并发重复、绝对上限。
- 原生 **1 页**正控通过，计数/字节内容/最终清理如上。最初误用 EXTENDED_INFO（没有 user_wired_count）被本机 SDK 编译拒绝，已改 BASIC_INFO_64 后重新编译运行；没有用错误结构猜布局。
- **未完成**：runtime 公共代码接入、实际 FEX sceKernel ABI/errno 正负例、C VMA 租约/地址复用竞态、GPU 镜像生命周期联合测试、固定游戏运行时真实请求分布、长期内存和 FPS。上述全部通过前不能把 Mlock 从“缺失”清单删掉，也不能启用到正式版。

## 复跑

在本目录执行：

```sh
python3 analyze_mlock.py --boot /Users/Shared/BloodEchoesDevelopment/assets/boot-linked.bin --import-names /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/vendor/bb_runtime/src/import_names.inc --output static-contract.json
python3 test_bounded_mlock_model.py
clang -arch arm64 -Wall -Wextra -Werror -O2 native_one_page.c -o native_one_page
./native_one_page
```

只需复制四个 authored 源码/测试及本说明供审查；不要把本机二进制或任何游戏输入放进 Git。所有输出留在这个隔离 evidence 目录。
