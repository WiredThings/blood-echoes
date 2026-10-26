# 静态程序、材质与已有完整输入的关联

本轮复用已完成的全量扫描和现有头/缓存元数据，没有再次扫描31GB游戏，没有启动游戏或GPU。新游戏派生台账仅写Shared新目录；旧输入逐一复核摘要未变。正式应用、存档、设置、缓存和Release未改。

## 程序身份：阶段来源必须准确

新增`tools/static_program_links.py`保留 `(program_hash, hardware_stage, code_sha256)`，每个variant完整保存原寄存器、usage、semantic和file-default属性，禁止把多个variant逐字段拼成不存在的组合。每条代码长度与原catalog的 `(hash, codeSHA, BinaryInfo type)` 精确交叉核对；完整header范围与payload范围也原值保留；原代码偏移保存在逐来源记录中，区分entry字节与重建ELF字节域。没有导出代码。

阶段来自经过独立OrbShdr/代码跨度校验的GNM文件头，不能把BinaryInfo raw type强转成HwStage。工具解析当前`runtime_info.h`的真实枚举顺序，并与规范metadata解码器的HW列表一致性检查；枚举改变时明确拒绝。

| 文件头file_stage | BinaryInfo raw type | 原生HwStage |
| --- | --- | --- |
| 2 | 0 | Fragment=0 |
| 1 | 1/6/8 | Vertex=1 |
| 3 | 5 | Geometry=2 |
| 5 | 2/9 | Export=3 |
| 7 | 7 | Hull=4 |
| 6 | 3 | Local=5 |
| 4 | 4 | Compute=6 |

结果：22,519来源引用归为8,915个程序身份及8,915份完整静态variant。原62个hash对应多个代码的情况，在已有**真实硬件阶段**参与后，每个 `(hash, HwStage)` 都只剩一份代码。该结论不授权仅凭logical stage或文件扩展名选代码；将来遇到同硬件阶段多代码时仍返回ambiguous。

程序寄存器中的`program_low/high`保留原始32位，但明确标注为文件重定位值，不能当guest指针或随意烘焙成常量。file-default RuntimeInfo字段与实际fetch/draw最终属性分开；此前2,227条输入VGPR需要修改的事实没有被默认值覆盖。未知stage words、aux、GPU-mode及剩余语义字段均原值保留且标记未解释；解析器以后新增的未知字段会显式拒绝，不能静默丢弃。

## GS复制程序：能补来源，尚未解禁旧consumer

新增`geometry_copy_provenance`只读核对实际缓存已保存的copy hash和word数量。必须满足：唯一Vertex copy身份、BinaryInfo type6、真实embedded-GS-copy标记、字数×4等于代码长度，并与Geometry主程序处于相同package/entry/sourceSHA及outer header。

已对8,036条真实规范解码metadata完成关联，全部具有唯一静态程序身份。其中48条GS-copy（`1635154c`15条、`8e3f8dc4`33条）全部找到上述完整来源；其copy大小分别172B/360B，恰为43/90 words，而Geometry主代码分别8668B/5976B。48个不同阶段代码的负向查找全部拒绝。

此处没有读取、修补或解引用任何序列化session指针。输出只是可供下一步建立owned-code span的来源描述，不是原缓存SPIR-V实际来源的反向证明，也没有授权现有worker反序列化旧span。真正接入时仍须校验当前游戏/包/entry字节与代码SHA，在编译边界构造进程内owned copy，并保留其它runtime/spec/附件字段的校验。

## 资产与材质：缺口有明确归属

`derive_bindings.py`保留原350份asset schema与3,442份同stem stage-family候选，同时新增80,451条mesh→材质引用追踪（模型资源及SHA、mesh/material索引、原MTD路径等）及304份独立MTD参数/纹理槽事实。shader家族候选仍不当权威pass配对。来自真实阶段头的17,024个entry→program身份已经精确关联进候选记录。

80,296条引用有唯一的归一化MTD basename匹配。其余155条并非笼统“解析失败”：139条指定的MTD名称不在现有304条材质定义内，16条MTD路径为空；按资产类别为chr49、map46、obj60。保留所有原引用，不做近似名映射，也不将这些模型宣布为无用。若未来出现重复归一化basename，明确返回ambiguous，不能由字典最后一项覆盖。

全扫描的物理及逻辑资源中没有独立`.spx`文件；MTD内ShaderPath是家族引用，不提供权威多阶段pass选择。80,099条mesh材料还带有尚未解释的GX偏移。BlendMode等MTD参数的原值可查询，但没有把它们猜测转换为Vulkan/Metal混合、深度和附件状态。因此静态完整PSO数仍为0，不能把350条asset schema冒充350份可编译PSO。

## 输出契约与复用

程序索引JSONL每条含`identity`、`variants`、逐来源`evidence_refs`、`complete_pso=false`。variant中的facts保留完整原字段，`code_bytes`独立交叉校验；同code的不同默认属性不会拼接。entry索引也明确`stage_pairing_authority=false`。

```sh
python3 tools/static_program_links.py --headers /本机/headers.jsonl --catalog /本机/catalog.json --out /新的绝对目录/programs
python3 tools/derive_bindings.py --scan /本机/scan --materials /本机/materials.json --catalog /本机/catalog.json --static-program-index /新目录/programs/static-program-index.jsonl --out /新的绝对目录/bindings
python3 tools/audit_static_program_links.py --index /新目录/programs/static-program-index.jsonl --decoded /本机/decoded目录 --out /新的绝对目录/observed
```

以上在experiments/arm64-runtime运行。新目录不允许覆盖旧输出；输入要求规范普通文件，符号链接不绕过检查。程序索引有100,000引用、65,536 variant、256MiB逻辑估算上限，usage/semantic表及原始字受独立结构预算限制；这是元数据估算，不是完整进程RSS上限。没有为游戏增加常驻缓存或逐draw工作。

15项静态身份/GS-copy定向测试、4项材质关联定向测试及23项原全扫描作者回归均通过；全部纯CPU、耗时小于一秒。实际三次元数据处理的耗时/峰值RSS及去内容计数见`evidence/catalog-semantic-linking-validation-20261026/summary.json`。完整JSONL保留本机，不上传GitHub。
