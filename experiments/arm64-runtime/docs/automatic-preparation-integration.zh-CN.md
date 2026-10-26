# 原启动器自动本机准备：当前接线与验收边界

2026-10-26。当前仅隔离开发源码；未替换正式应用、未改正式存档/设置/缓存、未更新 Release。

交付路径是原启动器 final-exec → 自动扫描用户游戏文件 → 自动转换可证明输入 → 同机编译并持久化 → 原运行时实际命中。人工补下一批输入不再是主线或集成前置条件。

## 已有组件与复用位置

| 环节 | 原有实现 | 本轮接线 |
|---|---|---|
| 游戏文件与模型资源扫描 | `game_shader_catalog.inventory`、`gnm_shader_metadata.census`、`full_game_catalog.Scanner` | `launcher_game_catalog.prepare` 默认调用；子进程完成后释放扫描临时内存 |
| 精确代码/硬件阶段关联 | `static_program_links.collect` | 原严格关联算法不变；SELF 先经原 loader 重构 ELF，明确分离两个 offset 域 |
| 原 final-exec/cohort | `launcher_local_aot`、`warm_archive_manifest` | 保留原参数、语言、联网、F3 和退出码语义 |
| 旧观察输入转换 | 原 canonical metadata/key/profile reader、owned module consumer、fetch projection、owned GS | 通用 module controller 自动分类；不能证明的组合记缺口，不能猜描述符 |
| Metal 本机库/PSO | 原 local builder、`prepare_game_psos`、`game_pso_queue`、runtime `ShaderCompiler`/`PsoCache` | GS 使用同一队列、共享 Geometry 算法、相同实际缓存目录与 cohort |
| 动态补齐 | 原 Parse/GetSharp/StageSpec/CompileModule 已发生的读取 | 新编译与暖命中已接单 blob／真实stage-permutation封口，CPU及实际TU控制通过；typed消费原生10项通过；尚未验证真实游戏命中 |

## 静态入口的增量与保护

`launcher_game_pso.prepare` 在已验证实际 renderer/worker 同 DSO及隔离 GPU 根后，复用原 Mod 合并算法核对实际 final app0，再用 APFS clonefile 产生只读普通文件视图。TITLE_ID 用原 SFO reader读取同字节视图；游戏 final exec仍用原 app0。接着执行默认静态入口，再枚举原观察缓存。空缓存也会扫描，而不是直接返回空目录。

新增源视图严格核对原 Mod 选择/优先级、社区汉化last-mod、实际虚拟文件名与canonical来源；不会盲随未知链接，不用hardlink，不在CoW失败时默默全量copy。冷发布前重核完整源内容读集，暖采用也拒绝快照新增/缺失文件。原CLI相对路径和默认`../CUSA03173`按原脚本语义规范化，严格reader不放宽。源视图16项作者控制包含真实APFS clonefile、原mods合并、并发源修改/新增文件反例；不等于真实游戏运行验证。

每代索引以实际游戏文件内容摘要和扫描算法身份寻址；文件新增、删除、内容修改以及扫描器变更都会失效。原 native enum 与原 SELF loader 也纳入算法身份。编译器、设备、补丁、选项变化由原 cohort 独立管理，不把静态索引身份充作运行时管线键。

暖启动仍对输入和成功输出重新核验，不采用仅凭 mtime 的信任捷径。当前完整文件摘要扫描的耗时尚未实测，不能宣称暖启动已很快；后续验收必须单独报告摘要验证、解析、模块准备和 Metal 编译耗时。

成功代最后原子发布 READY；中断代不准入，重试成功后指针指向完整新代，原缓存不删除。所有新元数据位于所选隔离 GPU 根的 `automatic-game-inputs`。原有严格路径、链接和来源校验保留。扫描子进程受同一压力、锁屏、swap 增长和超时保护；无额外游戏/GPU 并发。

13 项作者控制已通过，包含真实既有 DCX/BND4/GNM/ELF reader 处理自造输入、空安装、冷暖调度、内容改动（即使 size/mtime 不变）、增删、损坏输出、链接拒绝、失败重试、保护停止和原文件不变。它们证明调度/解析契约，不证明真实游戏已运行命中。

## 尚未闭合

1. typed companion原生10项正反例已通过（含实际自造GCN重译与完整特化/SPIR-V逐字节对照），helper异常处理已修复。新编译/暖命中封口已接源码并通过CPU与实际TU控制，但不能据此宣称游戏闭环通过。
2. 通用controller、GS同队列接口和自动capture环境已接；实际同DSO组件及整包入口仍需验收。候选scan非安全deferred时保留已验证capture环境，不伪造PSO完成。
3. 当前235GPU TU及两helper coherent构建已完成，320.3秒，压力2→2、swap 7902→7886MiB；同snapshot ABI已生成。consumer异常路径移用JSON对象问题已修复；仅重建main，完整共享头和原DSO不变，原生10项通过。新repair组件用于后续QA，旧consumer及旧结果保留但不采用。未重建整套FEX CPU，也不是clean-checkout证明。
4. 新隔离code stage已通过原打包入口生成，36准备模块与2原parser冻结，原16脚本仅run.sh新增已准入final-exec hook。仍需完整新包、空缓存首启/冷暖/失效、真实Local/PSO命中、编译/长帧、启动及峰值/常驻内存统一验收。

静态扫描并不假称已有完整 PSO：确实依赖绘制时描述符、附件、特化等状态的字段列入缺口，由正常运行自动采集，后续启动准备。静态 identity、captured input 完整性摘要与原 runtime 语义 key 分开。

## 并行的上下文接口工作

按用户最新调查修订：三个接口是上游与正式 Rosetta 已有的兼容性缺口，不是 ARM 迁移漏接的已有功能，也不再是本主线交付前置条件。`_is_signal_return` 与普通 `__Ux86_64_setcontext` 保留隔离门控实现及真实 FEX 27 场景/282 断言，默认关闭；这些测试不代表血源真实命中。完整 `sigreturn` 暂停扩展，未知路径继续明确诊断。后续只在正常回归真实命中后据现场处理，不继续理论覆盖长测。

此前六项为 `sceKernelMlock`、`sceKernelGetModuleInfoFromAddr`、`__elf_phdr_match_addr`、`__pthread_cxa_finalize`、`msync`、`__progname`，各自按正确性和集成状态收尾，不回退，也不被三项缺口捆绑阻塞。取消捆绑门槛不等于自动闭环、coherent构建、实际运行命中、集中回归和交付已完成。

## 实际全量静态入口（2026-10-26）

本机真实用户游戏的28852文件经原Mod算法核对后，用APFS CoW生成只读视图并完成自动目录生成，总390.9秒（目录247.2秒，其余主要为源视图与内容核验）。扫描得8915个代码/硬件阶段身份；静态完整PSO仍为0，不能据此声称静态输入已具备动态附件/描述符。一个可选about SELF段保持明确unsupported记录，未猜解、未隐藏。系统压力2→2、swap净增75.44MiB；未运行游戏、GPU或编译器，未证明持续内存等价或实际缓存命中。成功代元数据由READY封口，后续暖采用继续严格核验。摘要见`evidence/automatic-source-entry-20261026/summary.json`。

新的隔离GPU应用已绑定同源DSO并通过实际ARM64/签名/加载边核验；准备器新main绑定还会核对全部编译头。SDKSettings.json是当前Xcode SDK中的root-owned系统输入，独立匹配当前xcrun SDK后做稳定摘要，其余组件/头仍要求user-owned，不放宽游戏或缓存目录校验。完整新CPU/GPU包和真实运行验收未完成。
