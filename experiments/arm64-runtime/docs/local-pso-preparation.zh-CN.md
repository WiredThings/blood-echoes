# 本机库与最终 Metal 管线的提前准备

2026-10-26。以下是隔离 ARM64 开发源码的进展，正式应用和 Release 未更新。默认本机准备仍按正式功能推进；平均 FPS 不提高不构成关闭理由。用户自己的游戏输入和编译产物始终留在用户自己的 Mac。

## 已接入的链路

原 ShaderCompiler 与原 PsoCache 冷工厂共用一套独立产物接口：

1. 当前编译器生成真正成功的最终函数，记录实际有效 MSL、入口、编译选项、本机设备/系统/编译器、实际 Source 或 LocalMetallib 来源和模块 ABI。原程序 hash、permutation 槽号和指针不替代这些身份。
2. 用实际完整 native descriptor 和函数身份形成管线键，包括顶点布局、附件格式、采样数、混合、细分及相关状态。无法完整表达的非空 linked/preloaded-library 等状态明确拒绝准备，原运行路径保留。
3. 准备期用新归档编译、序列化，再以 `FailOnBinaryArchiveMiss` 验证当前 descriptor，成功后才原子发布独立产物及校验记录。失败保留已有成功产物，不向已加载主归档追加，也不清旧缓存。
4. 运行时使用自己的实际函数与 descriptor 独立计算键，只查对应产物；缺失或不匹配交回原工厂。已有内存管线命中不重新做磁盘校验、序列化或驱动创建。

辅助管线将稳定的 discovery 身份与最终产物身份分开。前者跨 record/load 阶段保持一致；后者保留真正成功的本机库或源码回退来源，不能把全局“请求 load”当作成功加载。已接入 8 个 HostKernels 家族，不能据此声称其它辅助管线的最终 PSO 全部完成。

本机 MSL 构建请求改为前两位摘要分片，旧 flat 请求继续读取；标准库 SQLite 磁盘索引每页最多 256 key、最多 2 个最终文件 pin。两个 native 发布者不再因全进程达到 4,096 次而停止记录。成功校验过的条目在保护中断后不重编，孤立和不完整请求保持明确状态。

## 无窗口游戏配方准备器

`src/game_pso_preparer.mm` 与 `tools/prepare_game_psos.py` 使用既有完整 cache-local 配方，按 record → 本机 Metal Toolchain → prepare 串行执行。复用原 Module、RectList 和完整 Tess 变体链；每次重新翻译均使用新 source scratch，不读取旧运行时 MSL，不向游戏运行时导入旧 Meta/SPV。

原输入按 owned-read/SHA/stat 冻结；只有 bounded 审计后的 spec 区间和 SPV 传给 native。Info、flat/SRT/walker 尾部不进入 native reader。旧编译器来源未知的配方仅提供候选输入，不能因此获得运行时缓存兼容授权。最终原生产物只有当前游戏自己生成相同完整身份时才可能命中。

独立 host 服务提供真实 monotonic 时钟、CNTFRQ 和 TLS。guest/VM/TCB/重启接口明确退出原 unsupported 80，不是假成功，也不使用测试 runtime stubs。准备保护、中断和失败不会使用启动器的 75/76 重启状态。

CLI 当前用于开发验证，必须显式提供 worker、实际库 SHA、完整 manifest、当前源码生成的 layout SHA、cohort、选定 GPU 根和精确 artifact cache_dir。默认单次 16 配方/600 秒，可显式增大，但有 16 MiB 队列预算。预算、缺口及未完成项必须报告；这不是整个游戏准备完成的承诺。worker 执行结束也不等于 PSO 已准备，supervisor 另核对实际产物事件和成功计数，失败/延后标为 partial。

## 已完成的实际验证

同一头/源码闭包的 230 个 GPU 单元完整重建，库 SHA `527592556231ffc5707059a77e127856e56aef4e85bc9460ef83f10210da4bd7`。执行程序和状态都在 Shared，未启动游戏、窗口、录屏或 Cua。

- 自编写 VS/FS 的 7 个原生进程、681 项断言：Source 和 LocalMetallib 分别跨进程严格命中；fresh 与 MSL disk hit 函数身份一致；像素 oracle 正确；descriptor 改变、缺少来源、损坏作者校验记录和缺失本机库时正确回退。暖命中没有普通 PSO 编译，重复内存命中不再访问产物工厂。
- 真实 FillU32 原工厂的 7 个进程、2,138 项断言：record/load 使用相同 discovery/请求/descriptor；实际缺 entry 的本机库退回 Source，不能误用 Local 产物；恢复作者库后原 Local 产物仍严格命中。真实 GPU 填充值及边界哨兵通过。
- 三种真实游戏完整配方：compute 1 条、ordinary graphics 1 条、完整 tessellation 5 条原生 PSO。冷阶段 7 条全部发布；独立暖进程在禁止 PSO 编译时 7/7 严格命中、0 次普通 PSO 编译，函数身份、descriptor 和归档 SHA 一致，没有源码库回退。冷/暖约 11.36/1.64 秒，仅代表这三份输入，不是游戏启动时间。
- 上述原生控制最高内存压力均 1、swap 增量均 0。没有据此声称整游戏内存不变或获得 FPS 收益。
- 当前布局重新由 CPU emitter 生成，217 个标量 ABI、49 个 Profile 字段、7 类原 Writer fixture 与旧版一致；当前源码 SHA 正常验证，没有放宽校验。旧 producer 兼容性仍未知。
- 公开 Python builder/recovery/readiness 40 项控制通过；新增分页 22 项控制通过，其中 4,101 个作者请求在第 300 项保护停止后续建 3,801 项，跳过已成功 300 项，全暖再跳过 4,101 项。该项使用假工具，不能冒充真实 Metal 性能测量。

汇总见 `evidence/local-pso-native-validation-20261026/summary.json`。只公开作者输入、源码和汇总，不提交游戏源码、Meta/SPV/MSL、完整私人配方、AIR、metallib、PSO 或游戏运行日志。

## 仍需完成

自动接到原启动器 final-exec/加载准备、完整配方的有界遍历与覆盖账本、原游戏运行时实际消费这些产物、RectList 专项原生准备及其余辅助 PSO，以及同设置下游戏冷/暖启动、现场编译、帧时间尖峰和内存对照。原生小样本通过不能替代这些验收。

另有去除函数 ABI 摘要中两项冗余重写计数的独立补丁：相同最终 MSL 不应因诊断计数不同而分裂身份。当前正常 fresh/disk/memo/local 路径和本轮原生控制均一致；它不是当前命中错误。该补丁尚未进入上述库，须下一次同源重建后再验证，不能混用新头和旧库。
