# Bloodborne 专用 FEX 特化与 CPU/GPU 协同评估

2026-10-26优先级更新：本报告作为长期待办暂存，硬件TSO、CPU/GPU深度协同与新增游戏热点特化暂不实施。先完成完整管线输入目录、三个上下文接口及统一验收交付，再回到本方向。原软件排序/通用回退保留。

日期：2026-10-25。范围：只读源码、保留诊断和上游一手资料；未启动游戏、未编译、未修改源代码、正式应用、设置、存档或缓存。本报告中的收益均为待验证假设，未新增性能实测。

## 结论

**适合加入长期目标，但应定义为“保留完整回退的血源热点特化”，不应定义为“裁掉其它 x86 指令就会加速”。** 当前游戏和模块是有限二进制集合，其实际指令集合当然有边界；但尚未完成可证明覆盖的指令普查，不能声称只使用了某个具体百分比的 x86 指令，也不能把探索过的场景当作全部路径。

FEX 目前已经只翻译实际遇到的代码块；它没有为了支持其它应用，在每帧依次模拟所有 x86 指令。删除未使用的 AVX/x87/其它模式实现，主要可能减少编译产物与冷代码体积，不会自动减少已经生成的 ARM64 热代码的访存、flags 或同步指令。当前64位模式、原生 HLE 和 ARM64 Metal 后端也已经缩小了翻译范围，应复用而非再实现。

**更有价值的专用方向**：准确识别游戏热块、减少确定冗余的转换/校验与跨进程往返、改进热块代码生成、在授权和逐线程准入成功后使用硬件 TSO。任何专用假设不满足时，必须回退到现有正确路径，而不是返回空纹理、跳过绘制、降低精度或静默继续。

## 当前实现已经做了什么

以下路径均以 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype` 为根。

| 机制 | 当前源码证据 | 对“再裁指令”的含义 |
|---|---|---|
| 按需译码/JIT 与块缓存 | `vendor/aether/runtime/sources/fexcore-darwin/FEXCore/Source/Interface/Core/Core.cpp:573–818` 的 GenerateIR；`:891–1010` 的 CompileBlock 先查 LookupCache，再编译并发布 | 未执行的指令处理器不会持续进入热路径；优先优化已生成块 |
| 固定64位 guest 模式 | `vendor/aether/src/core/fex/fex_guest_engine.cpp:451–469` | 已消除部分“支持所有应用”的运行时自由度，不能重复计收益 |
| 死 flags 消除 | `.../FEXCore/Source/Interface/IR/PassManager.cpp:84–91` 添加 `CreateDeadFlagCalculationEliminination`；对应 `Passes/RedundantFlagCalculationElimination.cpp` 有读写与CFG分析 | flags 优化已存在；下一步应找未消除的具体热点，不应全局忽略 PF/AF/溢出等 |
| 原生 HLE 与 GPU 桥 | `src/game_runtime.cpp:349–392`；`vendor/aether/src/core/guest_cpu/hle_call_adapter.cpp` 已有 published lookup 与 packed veneers | GPU 驱动不是整段通过 FEX 执行；已有桥的快速路径应先审计和测量 |
| 宿主特征检测 | `.../Source/Common/HostFeatures.cpp:165–201,573–592` 检测 AFP/RPRES/FlagM/原子等，后续做 errata 与覆盖 | 核实有效检测和实际发射，不要假造 M4 不支持的扩展；Apple Silicon 无 SVE 不能靠强开得到 SIMD 收益 |
| 代码缓存身份 | `src/bb_cpu_cache_identity.h:19–56`、`bb_cpu_cache_inputs.h:25–89` | 覆盖本地 translator、已补丁代码、模块、ABI、宿主特征、有效配置和 TSO；任何新特化都应进入身份 |

公开 shadPS4 设计文档说明常规 x86 宿主直接执行 guest CPU 代码、在系统调用和 GPU 命令处接管。这不提供“血源只有极少指令”的证据，也不是现成的 ARM 专用译码器。[shadPS4 设计说明](https://github.com/shadps4-emu/shadPS4/wiki/VI.-Emulator-Design-%5BDevs%5D)

## 现有实测证据决定先优化哪里

1. `evidence/slow-compile-investigation-1792854369607244000/report.zh-CN.md`：最后一代14个 GPU 慢项累计1597.3 ms，分布在探索过程中。最长 VS326.6 ms、FS209.7 ms、PSO188.4 ms；并非单帧总和。新 shader/特化/PSO 同步编译目前是明确的尖峰来源。`.metal` 命中仍调用 `newLibraryWithSource`，并不是 metallib 命中。
2. `evidence/launcher-engine-abba-1792858077113256000/input-attribution.json`：ARM 最长122.657 ms 帧中 GPU3.01 ms，重叠 FEX compile0.691 ms，Metal compile0。它靠近首次请求 L2，而非已证实的“卖血事件”；输入请求不等于游戏动作成功。另一77.007 ms帧含24.183 ms IPC reply 重叠，GPU40.73 ms。分类有嵌套，不能把它们相加当帧时间。
3. `evidence/launcher-engine-abba-1792858314945503000/0-arm64`：再次采集最差46.85 ms，123 ms尖峰未复现。40.545 ms帧内所选 guest condition wait 并集37.394 ms；不能由此认定等待本身可删。没有新增优化就不能宣称尖峰已修好。
4. 前述日志有每代约4千个不同 host PC 的一次性未对齐修正，证明存在首次修正路径，尚无独立耗时比例；不能用次数判断它比 Metal 编译更贵。

因此，**现在没有证据支持为了提高帧率而先删大批 x86 指令语义。** 应先完成白模正确性与编译/producer/同步归因，再对有成本证据的 CPU 热块下手。

## 建议纳入长期目标的分阶段方案

### A. 指令和热点普查：作为分析工具，不把缺失覆盖当“未使用”

- 在现有 decoder 的已译码块处，按 module hash+相对PC、指令类、向量宽度、LOCK/访存、flags 读写、跨页/未对齐类别累计；只保存有界计数和代码摘要，不落游戏字节、资源或存档。
- 区分“静态可译码”“运行中曾翻译”“运行中经常执行”。JIT译码次数和代码映射文件都不是执行频率。优先采用有界抽样PC映射到已编译块，避免每条指令插桩拖慢游戏。
- 静态反汇编只能作上界近似：可执行段中可能混数据、间接跳转未知、guest veneer/加载器stub是运行时生成的；绝不能把线性扫到的垃圾opcode或未扫到的目标当硬证据。
- 未来覆盖初始化、探索、战斗、特效、死亡、菜单、联网回调、模块启动及语言补丁；**本轮按用户要求先跳过死亡、在线和菜单 live 测试**，因此当前普查只能标部分覆盖。
- 记录eBoot/guest模块、补丁集、桥ABI、host features与TSO策略摘要。发现新模块/代码页/修改代际，旧热点配置自动失效或回退通用JIT。

预期：极少固定元数据、无需增大代码缓存；其收益是确定后续目标，不应宣称普查本身提帧。

### B. 最小且可回退的专用候选

| 候选 | 合理实验 | 主要边界 |
|---|---|---|
| 多块编译/块长度配置 | 同一编译版、同一路线冷/热两遍，分开记录 JIT CPU/wall、执行CPU、尖峰和JIT字节；一次只变一个参数 | 小块可降低首次编译停顿却增加dispatch；大块可能反向增加停顿和代码内存。上游默认 Multiblock=true、MaxInst=5000，但需记录本进程有效值 |
| flags/标量SSE/向量热点改进 | 抽取合成指令片段，对原始与优化后的全部可观测结果做差分；寻找地址计算、重复扩展、临时寄存器/flags搬运 | 不降低浮点精度，不假定 flags/NaN/有符号零/溢出永远无人观察；信号与回调退出也要能恢复正确guest状态 |
| 已识别 libc/小循环原生替代 | 仅按完整版本/代码摘要匹配，复用原有 memcpy/memset/HLE，验证跨页、重叠、页保护与资源写入通知 | 当前原生路径已替换部分函数；不可跳过 memory_prepare_cpu_write/note_cpu_write 或把 token 当可访问地址 |
| 统一翻译与HLE入口 | 审计现有 fast lookup、packed veneer、回调调用桥的实际热成本 | 不再写平行协议；保留原启动器退出码/重启/错误语义，先修当前集成缺口 |
| 硬件TSO | 进程初始准入成功、每个实际执行guest的宿主线程准入成功后，单独比较 mode0/mode1/mode2 | 见下一节，不能直接关软件TSO代替硬件准入 |

**不建议**：全局 O0/关闭 flags、全局松访存、缩缓存、关SMC/代码页失效保护、降低x87精度、遇到未支持指令直接忽略、跳绘或只运行“已知好”的特化。它们可能把偶发白模/崩溃变成长期数据损坏，违背本轮目标。

当前 vendored FEX 配置文件自己明确提示 Multiblock 可能造成 JIT 停顿、关闭TSO可能破坏多线程程序、低精度x87可能造成渲染问题；无需为试验重复造配置。[上游当前配置](https://github.com/FEX-Emu/FEX/blob/main/FEXCore/Source/Interface/Config/Config.json.in)

### C. 硬件 TSO 的正确位置

`src/bb_hardware_tso.h` 已有 mode0软件、mode1硬件准入但保留软件、mode2硬件准入后停软件的策略；`fex_guest_engine.cpp:2072–2078` 只有选中2才调用 `SetHardwareTSOSupport(true)`。逐线程失败时不能继续共享按硬件假设生成的JIT，当前选择终止190。授权不可用时保留软件策略。

HyperBridge 的固定版本补丁同样围绕逐宿主线程身份、native→guest入口、寄存器/FP状态保全和失败保护构建；它不是一个“忽略x86内存模型”的游戏专用hack。[HyperBridge 0055](https://raw.githubusercontent.com/t0b1kent/hyperbridge/ac49813a48908d7a4a94f0e5583fedbe8fbc71e0/fex/patches/0055-HW-TSO-R2.patch)

当前报告未重新探测签名/profile与系统授权，**不能宣称硬件TSO已可用或已启用**。后续签名与准入解决后，再用mode1区分系统模式本身成本，mode2测减少屏障的净收益。TSO不代替Metal fence、资源代际或CPU/GPU完成同步。[FEX 对访存模拟成本及硬件支持的说明](https://fex-emu.com/FEX-2406/)

## CPU/GPU 深度联动：可行的不是把两套指令随意混合

FEX 翻译x86游戏逻辑；Metal翻译AMD着色器/PM4，两者中间有真实的数据依赖和GPU调度。控制两者有利于共享更完整的资源语义，但不能把CPU指令自动搬到GPU或省掉跨硬件可见性。

优先方向：

1. **统一资源代际与写入范围。** 现有 `remote_front.cpp:149–218` 已发布lifetime、fresh/invalidate/note-write事件。未来用相同allocation generation关联FEX/HLE写入和GPU缓存，减少同一批次已知无新写入区间的重复快照/格式转换。必须同时验证free→地址复用、CPU跨页写、GPU写、快照内容版本；生命周期标签活着不代表内容一定新鲜。当前白模调查应首先受益于这套关联，而不是先放松校验。
2. **以完整不可变键提前发现编译任务。** 在命令提交/已完整解码阶段提前发现shader/PSO，给有界队列预备；首次实际draw仍要等待正确PSO，绝不跳绘。Metal vertex descriptor属于PSO，当前 `mtl_pipeline_cache.mm:135` 动态顶点输入能力=false，不能假装支持Vulkan可跳过路径。控制并发与临时IR峰值，建议一个worker/严格队列字节预算先实验，不扩大常驻archive。
3. **跨进程消息合批但保留顺序点。** 重复write通知或只读查询若有相同generation/区间，可在既有提交边界合并，遇到同步查询、CPU读取GPU结果、free/reuse必须flush。不能按“一个frame”无条件合并，因为同帧有GPU写→CPU读→新draw的依赖。
4. **缩短 producer→renderer 的空等。** 现有时序证明有guest条件变量等待及偶发IPC回复长尾；先找唤醒方与依赖，避免盲目加frames-in-flight（会增加资源驻留及输入延迟）。仅把确认只取CPU本地状态的IPC留本地，GPU拥有状态不能缓存成永久值。

这些更接近“用游戏的确定行为减少兼容层重复工作”，可能比删指令支持更有价值；但本报告没有验证某条复制/通道切换已安全冗余。

## 长期目标建议写法与验收门槛

> 建立 Bloodborne 专用、版本绑定、保留通用回退的 FEX/Metal 协同优化层。用有限元数据普查实际热点与依赖，优先减少重复转换、首次编译和同步长尾；仅在语义等价、无画质变化、内存不显著增加且匹配A/B可复现后默认启用。未知指令、模块、资源代际或补丁组合自动回退，保持原启动器完整功能和退出协议。

验收至少分开：合成ISA/ABI差分与异常测试；资源生命周期/内容写入测试；冷/热探索路线；与Rosetta相同配置的无录屏ABBA；aggregate/deduplicated内存、swap增量、JIT字节、峰值临时IR与p95/p99帧时间。不能用静态站桩FPS掩盖探索长尾，也不能用诊断窗口的下降推断补丁退化。死亡/在线/菜单按用户最新要求暂缓，醒后统一验收；通过探索性能不等于这些已通过。

本次只增加本研究报告，不启用任何候选，不同步Release。
