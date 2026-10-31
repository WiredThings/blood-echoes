# 原启动器的 FEX 本机磁盘准备（隔离开发版）

状态：源码集成、定向正反例已完成；真实游戏与静态 bbcpu 的匹配性能／内存验收仍在进行。没有更新正式应用或 Release，也不分发游戏代码、扫描输出或生成的 ARM 缓存。

## 实际接线

`launcher_local_aot` 沿用原前端的目录、覆盖选择、最终映像、补丁与 supervisor 参数。在选中 FEX 且隔离包带有 `FEX-COMPONENT.json` 时，自动调用 `fex_launch.prepare`；bbcpu 分支仍独立，不混合调用。未选中的 CPU 引擎不会被加载。

1. 登记原启动器已产生的 boot、ELF、有序补丁及真实 CPU/scanner 组件。复用既有文件身份／摘要登记；少数实际 FEX 配置文件、环境选项和系统身份变化触发重新登记。UI、音量、帧率显示不作为 CPU 编译输入。
2. 同一个 FEX 游戏宿主运行原导入、补丁与原 resource/heap/menu 安装器，在执行 guest 前导出实际最终映像。复用 `BbCpuImage::Export`，不发明补丁或另写游戏载入器。FEX 源页身份在安装器完成后建立；原小缓存实验的 pre-hook 行为仍保留在独立旧入口。
3. 使用既有独立 Zydis scanner。新增 `entries.tsv` 只输出真实解码的直接目标、条件分支及 call/syscall 继续点，复用原函数／实际 detour resume 目录；未知间接目标由原正常 code-map 捕获。没有通过 FEX/ARM 反猜描述符或执行未验证旧产物。
4. 原 FEX `CompileRIP`、可缓存性判定和 FXCC serializer 处理入口，绝不执行 guest。每个 4 KiB 源页内最多 64 个请求一批；完成分片写入后清理临时 lookup／relocation 记录，保留原代码缓冲区预算。内容不变的分片按种子摘要复用，中断后可继续；旧代产物不删除。
5. `directory.bin` 仅保存兼容键、入口、分片与源页摘要。单个 FXCC payload 上限 16 MiB；目录上限 400 万入口、65,536 分片、131,072 页、96 MiB。读写使用相同常量，百万条以上回归防止再出现两端预算不一致。
6. 运行时只在原 FEX lookup miss 下读取所需分片，核对完整实际代码／模块／桥 ABI／host／codegen／内存顺序身份、文件摘要、FXCC 结构、relocations 和实际源页；随后复制到**原有 MAP_JIT 缓冲区**并发布原 lookup/page 映射。文件暂存随调用释放，不能全量装入另一份可执行缓存。

首次全静态控制流目录包含大量不同入口，FEX块存在重叠，磁盘展开明显大于337MB级bbcpu整库；上述12GiB是磁盘文件，不是启动常驻RAM，也不是已经证明值得默认交付的成本。

因此它不是“完全无额外 RAM”：常驻入口／分片索引仍需空间，按需验证还有短暂文件缓冲和校验成本；实际值以匹配测量为准。好处是 ARM 代码沿用原失效、回填、buffer rotation、safepoint 与回收，不额外常驻完整代码库。

## 校验、增量与自身兜底

- 新/未知/拒绝的入口、损坏分片、变更的源页、满／过期的 JIT buffer，继续原 FEX 编译路径，不切到 bbcpu。精确失效之后再次读取磁盘也必须重新核对源页，不能复活旧代码。
- 分片损坏留下有界 repair 请求，后续准备处理相关分片。新的 code-map 含实际 source companion，来源路径与内容身份分开；全新文件只含已知入口时不再次启动整个 native producer。
- 完成后的未变暖启动不启动 exporter、scanner 或 native compiler。新进程仍须进行实际输入准入、读取索引，以及按需建立当前进程的 ARM code/lookup 对象，不能把后者描述成重新编译。
- 保留既有 F2/F3、自动继续、在线恢复转离线与错误码；准备进程结束沿用原 owned GPU reaping，不引入 75 等新语义。
- 当前原型继承的 `BbCacheBuild` 仍包含较宽的宿主／桥构建摘要；不能据此宣称所有纯调度或宿主修改都已精确局部失效。后续缩窄必须先证明真实生成代码／ABI依赖，不能删字段冒充兼容。

## 已完成的验证边界

- 原 launcher hook 的冷／暖、真实补丁切换和切回、无关设置、FEX 配置变化、损坏索引／repair、scanner 中断或损坏恢复、组件拒绝等 authored 控制通过；未变暖启动 exporter/scanner/compiler 均为 0。
- 实际 FEX 生成与消费在不同 macOS 进程、不同 guest 基址运行：真实 HLE 调用、RIP 重定位、首次按需复制、暖执行不产生新 code、直接链接回填、整缓冲清理、修改源页后的原 JIT 结果均正确。
- 错误兼容键、损坏目录与单个损坏 payload 均拒绝缓存，原 FEX JIT 仍产生正确结果。格式边界通过 14 项拒绝及 1,000,001 条入口写读正例。
- 首次真实自动流程扫描出 2,633,548 个入口，完成 46,930 个分片请求批次（其中 45,111 个非空文件，约 12,491 MiB 逻辑磁盘容量）；没有 guest 执行。该轮在最后写索引时暴露旧 writer 的百万条上限，故**不是完整游戏／性能通过**。预算现已集中修正，旧分片保留，后续重启按实际兼容身份处理。
- 该准备轮的黑窗来自 GPU 初始化被准备进程复用，不是已经进入场景后的黑屏。`bb_preparation_window` 只对明确 exporter/native-preparer 角色隐藏新窗口并禁用其 fullscreen，普通带缓存的游戏不隐藏；不省略原 GPU 初始化、共享内存或实际 patch 安装。完整新 renderer 中的窗口行为仍需验收。

`FEX DISK totals` 中的 load 时间包含磁盘读取／验证／等待，fallback-path 时间包含原编译及其等待，不是 GPU 时间或纯 codegen。命中和定向测试不能替代 FPS、长帧、启动时间与内存对照；两个进程 footprint 合计也不是物理 RAM 净占用。

## 并行 GPU／内存工作

另见 [GPU 屏障续接审查](gpu-execution-efficiency-20261031.zh-CN.md) 与 [失效场景副本退休](scene-proxy-retirement-20261031.zh-CN.md)。0088/0089 已有源码依赖和定向控制，完整 renderer 与整场景收益由主 agent 集中验收；不把短 Metal 探针释放的原生唯一容量换算成物理内存收益。
