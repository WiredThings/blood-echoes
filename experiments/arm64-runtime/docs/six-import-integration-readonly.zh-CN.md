# 原六项导入：当前源码只读收口

2026-10-26。这里只读核对当前源码和已有定向证据，没有新增接口、重跑游戏或宣称新包回归。三项信号／上下文扩展已冻结，不作为这六项或 shader 工作的交付阻塞。六项已经接入隔离开发源码，不代表本轮已更新正式应用。

| 六项 | 当前接线 | 已有证据／准确范围 |
|---|---|---|
| `__progname` | `guest_process_parameters.h` 在原参数页写入客体字符串和可写客体指针槽；`game_runtime.cpp` 按 DATA kind/NID/caps 准入，非函数 veneer、非宿主 char**。 | `guest-progname-1792902485760350000` 的真实 FEX 控制；当前初始化／路由／参数页权限仍在。 |
| `sceKernelGetModuleInfoFromAddr` | 原准备助手生成并验证固定模块 metadata；loader registry Ready 后注册 `module_query`，完整校验输出范围、flags 和包含该地址的模块。 | `module-info-fex-1792902643716403000`，8 组真实 FEX、73 项原生反例及助手失败语义。没有另写一般动态加载器。 |
| `__elf_phdr_match_addr` | 与同一个 registry 同时接入，复制完整 424 字节客体描述后按执行段首含尾不含匹配；不解引用段描述中的地址。 | 同一模块控制；非法描述／指针与非执行段仍拒绝。 |
| `__pthread_cxa_finalize` | registry Ready 后注册 `module_finalize`；完整描述须与 canonical registry 一致，按 owner 清 key destructor，保留 key/值；native `__cxa_finalize(dso)` 在原 atexit 流程后复用同一模块清退。 | `module-finalizer-fex-1792919451466464000`、`module-finalizer-native-1792920145997216000`。原 key generation、锁外回调、in-flight 等待、关闭期注册拒绝及重入死锁拒绝都保留。 |
| `msync` | `runtime_memory.c` 原 resolver/生成 native wrapper；显式客体 flags 转换，VMA 或 published State 映射租约跨真实宿主 syscall；wired INVALIDATE 联动同一 Mlock 账本。 | `msync-contract-1792904311857877000`，18 组真实 FEX；不是 GPU fence、dirty 或缓存清空。 |
| `sceKernelMlock` | 原 resolver 的名字与已核对 NID 均连到实际 `kernel_mlock`；VMA／published-native 范围租约后调用同一固定账本和 Darwin mlock/munlock，映射成功提交后才退账。 | `mlock-contract-1792916322032111000`；真实单页 wired-count、10 组 FEX、失败回滚／MAP_FIXED/unmap 生命周期。单次 16 MiB、总 64 MiB，只可下调。 |

以上证据均位于 Shared `arm64-runtime-prototype/evidence/`。六项统一的已有汇总是 `current-runtime-regression-1792919473613013000`，摘要在 `docs/module-finalizer-validation.json`：56 个构建目标、121 个基础控制及各专项通过；这些是该历史 CPU 的验证，不冒充当前全部改动已重新打包验证。

## 只读审查后仍需保留的实际边界

- 模块 registry 是当前固定加载模块的描述，不是通用 dlopen/dlclose 支持。模块 key 回调清退安全不等于其它普通执行／网络／atexit 都已具备热卸载租约；当前模块保持到进程退出。这是已知能力边界，本轮没有新增热卸载需求。
- Mlock 的真实游戏请求分布、是否触达上述预算尚未动态覆盖。超预算须明确失败；不能把合成单页／预算反例解释为任意 PS4 内存锁定都等价，也不能为了未见需求无限锁页。
- Finalizer 的 get/set 互斥和 per-thread 元数据、模块准备助手的临时峰值已在原文档披露，尚待最后统一的匹配性能／内存和整包回归。该验证安排不要求先补齐继承的 signal 子系统。
- 模块查询／finalizer 在 registry 未 Ready 时继续 missing。正常原启动器会给实际 app0 走原模块准备流程；特殊无 app0／自造 fixture 不应被误记成已支持任意模块元数据。
- 当前只读核对未发现六项路由因本轮 context 冻结而丢失，也没有证据要求新增功能。不能把只读审查写成全游戏正确性证明。

## 三项冻结口径

默认仅上述六项有当前实现；`BB_ARM_CONTEXT_IMPORTS=1` 才显式启用 `_is_signal_return` 分类和普通 `__Ux86_64_setcontext` 两项有界候选，未知 ABI 仍不准入。`sigreturn` 不注册。三项没有本轮真实血源运行命中证据；27 场景／282 断言只是作者定向控制。正式 Rosetta／bbport 的 inherited missing 边界与 shadPS4 用户提供调查，分别按 `rosetta-signal-boundary.zh-CN.md` 的证据范围说明，不删缺失统计、不空成功。
