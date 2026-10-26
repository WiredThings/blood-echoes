# 完整管线输入关联与独立模块输入

本轮不启动游戏或 GPU、不修改旧 QA 缓存和正式数据。目标是把已经具备条件的目录条目转换为原准备器实际能消费的输入，并给尚未具备完整绘制状态的静态程序建立独立模块层；不把哈希列表、同名材质配对或默认寄存器当作完整 PSO。

## 已落地的输入

`tools/complete_pipeline_inputs.py` 对显式选择的 35 个隔离 QA 缓存运行原 `cache_records`、`audit_owned_stage`、`audit_owned_key_profile`、`metadata_sections` 和真实 `freeze`，将原 schema 2 单元原样保存到新清单。实际 6,837 条记录中，5,032 条完整原始输入完全重复；剩余 1,728 套输入全部通过 owned packet 审计：349 计算、1,309 普通图形／矩形链、70 已有三阶段曲面链。77 条 GS 链继续由原准入规则拒绝，没有取消限制。各阶段均关联最终静态索引的唯一 `(program_hash, hardware_stage, code_sha256)`，不会假设 hash 全局唯一。

这里的“通过”是**实际可交给已有 worker 的候选输入**，不是 1,728 个已成功生成的 Metal PSO。旧生产者和当前编译器的兼容身份仍须由原准备链确认；完整的 Metal PSO 编译、加载与匹配性能验证尚未在本轮执行。候选保持 `source_provenance.enabled=false`，禁止当运行时缓存直接导入。

原始读取集合和 worker 实际读取集合分开去重。原始集合保留完整 profile/key/meta/SPIR-V 摘要；worker 集合精确覆盖当前 ABI、profile、key、各阶段 spec/SPIR-V 以及排列编号等标量。未被 worker 读取的 opaque Info/flat/walker 差异只保留来源，不创造重复编译任务；不改写旧排列编号，不对 padding 或语义做猜测性归一化。本次实际语料中第二层没有额外合并，仍为 1,728 项。定向测试证明仅 opaque flat 不同可合并，而真正 spec、SPIR-V、profile 或排列编号不同不能合并。

实际私有结果：

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/pipeline-input-completion-final-1792996779341182000/completed/
  completed-inputs.jsonl
  input-links.jsonl
  module-inputs.jsonl
  gaps.jsonl
  summary.json
```

输入审计约 16 秒，输出约 21.7 MiB；前后内存压力均为 1，swap 未增长。审计输出是新证据，不是新增运行时缓存。

## 静态模块层：确实已有与仍需补齐的输入

最终静态索引共 8,915 个程序身份：6,573 fragment、1,865 vertex、177 export、131 local、68 compute、62 geometry、39 hull。它们均保留 GNM header 原始寄存器、usage、阶段语义、code SHA/大小、来源包／条目／位置以及每个默认状态 variant；相同代码的不同默认状态不能拼字段。`program_low/high` 是文件重定位字段，不当作实际 guest program base。

| 输入 | 静态可确定的内容 | 不能据此省略的实际构造依赖 |
|---|---|---|
| GCN 程序 | 原始 code span、独立 BinaryInfo hash/SHA、硬件阶段与来源归属 | 源码版本、Profile、特殊化和实际资源描述符 |
| 基础 RuntimeInfo | 文件默认的 VGPR/UD 数、浮点模式；CS 本地线程组、TGID/LDS 等默认值 | 运行时寄存器可重写，不能将文件默认等同于所有 draw 的最终值 |
| VS／export | 输入／输出语义、输出寄存器等默认值 | fetch 实际寄存器读取、顶点格式／步进、VS/ES 逻辑阶段关系、motion session 地址 |
| FS | 输入语义、PS 输入地址和使能、默认导出格式 | 实际附件颜色/数字格式、采样数、swizzle/blend/depth 状态及额外 motion 附件 |
| 资源访问 | usage/SRT 声明、资源需要何种寄存器或表 | 实际 UD/flattened sharp 内容、资源特殊化；`uses_srt=false` 不表示无资源 |
| GS | 静态主程序和 copy 程序可以分别唯一识别 | owned copy span 的原生构造与完整 GS 链支持；旧 session pointer 不可读取 |
| Tessellation | 静态阶段程序、寄存器／语义和来源 | 实际 tess 常量、LS/HS/TES 配对及 draw 配置 |
| 最终 PSO | 已观察的规范 key/spec/SPIR-V 可组成现有 worker 输入 | 仅静态同名文件或 hash 不能补出附件、资源布局、阶段配对 |

68 个静态 CS 身份全部有非零 usage，默认 UD 数为 16（42 项）、12（18 项）或 13（8 项）。因此没有把整个静态域统一丢弃，也没有把空 UD、未知资源或未知附件硬填成合法输入：静态 code 和部分 RuntimeInfo 可独立复用，资源发现与特殊化则需要真实、拥有所有权的数据。

观察到的 2,200 套模块状态中，1,308 套具备当前有界 GCN consumer 模板所需的完整输入；892 套有缺失依赖，原因可重叠：807 fetch、85 tess 常量、14 geometry-copy。对应 `module-inputs.jsonl` 已逐条保留确切原因和静态来源，不能将这些模板数说成最终 PSO 数。

## 可执行的 owned 模块包

`tools/game_module_inputs.py` 将模板冻结为 schema 1 `owned-gcn-module-input`。它从本机游戏重新验证 SFO、原始容器和条目 SHA，独立解析 BinaryInfo、GCN 前缀与 code SHA，然后复制完整当前 Profile、spec、原始 code、flattened words 和旧 walker **数据**到新目录。旧 Info/SRT 不进入原生反序列化；walker 数据只允许与当前重新生成的 SRT 比较，绝不注册或执行。flat 至少包含 16 个真实 UD words，长度与 SRT 声明精确一致。

`tools/freeze_game_modules.py` 负责有界批量冻结，每阶段最多 64、总计最多 128，输出必须是新隔离 evidence 目录。真实批次成功冻结 **19 CS + 8 FS + 2 vertex + 2 export，共 31 个不同程序**；没有 source/shape 拒绝。批次清单：

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/game-module-batch-1792996896480772000/manifest.json
```

这批文件随后交给独立 CPU 原生 consumer 做真实 GCN→SPIR-V 翻译：**30/31 个程序逐字节匹配观察 SPIR-V**，包括 18 CS、8 FS、2 VS、2 export。每项均重新发现 SRT，核对重新生成的 walker 数据、flat 长度和完整资源 spec；1 项 CS 差异继续拒绝，详情见下节。这里仍未创建最终 Metal PSO。

consumer 检查当前 Profile、规范 spec、重新生成的 SRT/walker 等价、当前资源特殊化、无未捕获 fetch/motion/tess/GS 依赖。metadata 不包含真实 `program_base`，所以包显式为 `known=false`；准备专用翻译入口在资源发现后、GetSharp 资源读取前遇到 `OffsetByProgramBase` 就拒绝，不能让未知 base 被当作真实 0。完整成功前不发布 SPIR-V，不写原运行时缓存。

来源认证的边界必须区分：原生消费者检查包内 SHA/大小和代码前缀，但不重读原游戏的 SFO／容器／BinaryInfo。**包内自报 title、版本和出处不是认证**。`run_game_module_contracts.py` 的可信冻结 wrapper 会在每轮原生调用前独立重读原 SFO、包、条目、BinaryInfo 和 GCN code，再验证全部 frozen 文件摘要。此接口只给该 wrapper 使用，结果保持 candidate-only，禁止运行时导入。诊断版本偶尔输出的 `rejected-generated.spv` 明确不能视为成功产物；普通版本拒绝时不写输出。

本轮审查实际找到并修复了准备器自身的边界问题：空 walker 必须以 null 表达且不调用 JSON 标量 dump；raw Profile／RuntimeInfo／图片 bool 和枚举、数组范围必须在原反序列化前检查；资源向量长度必须在调用原 `StageSpecialization::operator==` 前相同；每个文件 reference 的摘要必须非空且为完整 SHA256。合法 export/vertex 阶段对采用正确的 SGPR base，仍不开放 GS/Tess。上述是独立 CPU 准备接口的修正，没有扩大正式游戏运行入口。

静态 GS copy 的来源证明由 `static_program_links.geometry_copy_provenance` 完成：48 条观察元数据均能将主程序、copy hash、长度、同容器／条目和 outer header 对齐。它证明可建立 owned copy 来源，**没有证明旧 worker 支持 GS**，因此本轮仍不计入可编译 PSO。

## 定向验证与后续边界

```
python3 experiments/arm64-runtime/tests/test_pipeline_input_completion.py
python3 experiments/arm64-runtime/tests/test_game_module_inputs.py
```

共 42 项作者自造夹具／结果合约测试通过（16 输入关联 + 11 source 冻结 + 15 结果合约与变体审计），不依赖游戏私有数据或 GPU。覆盖完整 owned packet 路径、去重不能改写 spec/SPIR-V/排列、hash/stage 歧义拒绝、源 SHA/SFO/code/offset 不匹配、符号链接／路径穿越拒绝、新输出不覆盖、缺 profile 显式失败、内存保护传播、禁止遗漏依赖和 opaque Info/SRT 传递；即使 profile/spec/SPIR-V 全部相同，也不能据此追认历史 producer 兼容。

另用真实原生 CPU 消费者运行 **35 个反例**，均在预期门拒绝、退出 65、保持输出目录空；覆盖 raw bool、阶段／FP／图片枚举、FS 输入数组、样本数、寄存器数、CS 线程维度、bitset／向量分配炸弹、fetch 缺口、禁止尾部、misaligned flat、SPIR-V 差异，以及更短的 buffer/image/sampler 向量。两个追加反例确认空摘要和非法 vertex SGPR 范围被拒绝。每个子进程独立 `BB_GPU_USER_DIR`、30 秒限制及内存保护。31 正例批次前后压力均为 1、swap 无增长。

```
.../evidence/game-module-contracts-positive-final-1792998079861298000/result.json
.../evidence/game-module-contracts-negatives-verified-1792998148766487000/result.json
.../evidence/game-module-contracts-exports-1792998562267420000/result.json
.../evidence/game-module-contracts-extra-negatives-1792998563184502000/result.json
```

上述 `...` 均为 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。去内容摘要保存于 `evidence/pipeline-input-completion-20261026/summary.json`；游戏派生 code、spec、SPIR-V、完整清单只留在私有 Shared 目录。

构建证据有明确分层：这些 native consumer 是针对性 CPU 程序，链接已固定摘要的既有库并单独重编准备入口；**没有做整个 renderer 的 coherent 重建**。先后几轮修复分别验证 raw 边界、空 walker、向量形状、合法 ES 和空 SHA。最终普通版基线使用 `game-module-native-1792997968715095000` 跑 31 项和 33 个反例，再用普通版 `game-module-native-1792998384406981000` 只增量验证 2 ES、空 SHA 和非法 SGPR。`1792997878522051000` 是诊断版，仅用于保留明确拒绝的差异 SPIR-V，不是最终准备器。最早的空 walker 拒绝和 JSON null dump 崩溃证据保留，不能算 pass。

## CS42 差异与历史来源缺口

程序 `0000000042f2a521` 是实际游戏 `CS_VFX_SparkParticle.cpo`，静态源代码为 23,244 字节。当前消费者重建 SRT 和资源 spec 一致，但 SPIR-V 与历史缓存不同；新结果增加了 Ballot／lane builtin，不能当作单纯编号变化，更不能盲目采用为优化。

`tools/audit_game_module_variants.py` 对 35 缓存已有规范模块记录做有界只读核对：该程序有 6 套原始状态，同一 serialized spec；其中 5 套语义 Profile 相同，却存在两种历史 SPIR-V（265,308 和 266,576 字节），另一个不同 strict Profile 对应 304,692 字节。当前生成摘要也不等于这三种。已证实的是“记录的 profile/spec 不足以唯一说明历史产物”，不是证明任一种缓存损坏或当前翻译错误。

旧 `.meta` 保存 serialization 版本、permutation、spec 和 Info，SPIR-V 是原始 words；没有持久化原始 GCN SHA、完整编译器身份或 patch 身份。`CompileModule` 的外置 SPIR-V patch 用于创建后端模块，但 `RegisterShaderBinary` 保存的仍是未替换的生成 SPIR-V，所以不能直接归因为外置 SPIR-V patch 被写入旧缓存。元数据的 hash 关联也不是观察时实际 GCN 字节的密码学证明。本轮没有找到足以把旧产物归属到某个确定编译器版本的凭据，根因保持未确认。

私有来源审计位于 `.../evidence/game-module-42-provenance-1792998423692991000/summary.json`。后续新建库回执应把调用时拥有所有权的 GCN SHA、编译器／选项 cohort、Profile、RuntimeInfo/spec、patch 选择以及生成 SPIR-V 摘要绑定起来；不修改或追认老缓存。本轮 CS42 保持拒绝，其他 30 个已经取得的逐字节等价证据有效。

下一步衔接已证明输入到当前本机构建／预热准备链，保留未确认项的拒绝与原因。最后统一进行匹配性能、内存和整包功能回归，不在本轮重复启动游戏长测。
