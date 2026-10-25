# 固定 1.09 的材质、程序与已观测特化关联

2026-10-25。仅在隔离开发 evidence 下写分析脚本和元数据；未修改 runtime、正式应用、游戏、存档、设置或缓存，未启动游戏/GPU，也未宣称性能收益。游戏目录与缓存内容仍只留本机，不应把下面的完整本地 JSON 上传 GitHub。

## 新确认的事实

1. 对固定目录中 `allmaterialbnd.mtdbnd.dcx` 做有界、结构化只读解析，304/304 MTD 精确消费到 EOF：共 72 个不同 ShaderPath、3,911 参数和 1,946 纹理槽。解析文件/子块长度、Shift-JIS 字符串、标记、参数类型/元素数、v3/v5 纹理项；不从任意可打印字串猜格式。输入压缩包 SHA256 为 `813e5767744884ec813f1ef557fa3848f0f05d33a334f31dc633be7b5f07055b`，读取前后相同。
2. MTD 确实提供 SPX shader 家族路径，但没有完整硬件 draw/PSO 特化。按照路径 basename + `_` / `@` 边界匹配非 debug shader 条目，273 个材质可关联到候选家族，31 个无候选；候选合计覆盖 9,554 条 shader entry、2,023 个程序 hash。**这是命名候选，不是已证实绑定**。不能把缺少候选的31个说成无用资源，也不能为前273个创建笛卡尔积。
3. 新的本地身份账本将两份已解析 QA 缓存连到静态代码候选、完整缓存 blob 摘要、Profile 语义摘要、阶段/逻辑阶段、cache-local permutation、原始特化摘要和实际管线文件摘要；重新核验所有引用的 profile/meta/SPIR-V/key 源文件 SHA，均仍与原 inventory 相同。没有合并或重写源缓存。
4. 主 QA：508 个 metadata、409 个管线；505 份 metadata 对应单一静态代码摘要候选，3 份有歧义，0 份静态无候选；233 份有材质家族候选；409/409 管线本地引用完整。5 个管线引用歧义 GS/session pointer。
5. 热点 QA：327 个 metadata、259 个管线；324 份对应单一代码候选、3 份歧义、0 份无候选；135 份有材质候选；259/259 本地引用完整。3 个管线引用歧义 GS/session pointer。两份缓存的歧义都落在 GS `1635154c`、`8e3f8dc4` 的 main/copy 同 hash 情况；账本保留多个 codeSHA，不做猜选。
6. 已定位的长编译顶点 `37a8af3d` 可在静态目录关联到10个 shader entry，均相同代码摘要；属于 Gbuffer、instanced、`@[cl]` 一组。MTD 命名候选有4个，不能从此确定当时到底用了哪个材质。主 QA 不含该特化，热点 QA 的 vertex permutation0 才含它。这给优先调查方向，仍不能把同 hash 当成相同完整 pipeline。

## “一次性梳理”现在做到哪里

| 层 | 本轮结果 | 仍缺少什么 |
| --- | --- | --- |
| 固定包/程序代码 | 现有非 debug 15,323 shader entries、8,853 hash、8,311 codeSHA；新增 MTD 全解析 | SELF 压缩段未全面展开，不能称绝对全游戏枚举 |
| 材质 | 304/304 结构解析，72 ShaderPath，纹理槽及参数可查询 | FLVER 中实际材质实例、SPX 内部选择逻辑、效果 pass 的权威绑定 |
| 已观测特化 | 508/327 meta 及409/259 key可完整定位到现有 blob | 不代表全游戏 runtime state，permutation 序号只在本缓存代际内有效 |
| 跨运行可复用身份 | 已保存 code 候选、Profile语义、阶段、blob和key摘要 | RuntimeInfo/session地址、fetch/descriptor内容及实际指令字节的所有权和版本；不能拿序列化裸字节当跨 session 语义键 |
| 安全提前编译 | 目录可用来排序、统计漏项 | 未建立独立 compiler context、不可变完整快照、所有权、失效和有界队列，故未启用 |

## 有界的下一步

优先做完整输入已知的新模块 job，而非把 8,311 个代码逐个盲编译：

- 在实际 draw 首次取得完整 specialization 与 pipeline state 时，复制**只属于该 job**的程序字节、fetch 数据、运行时标量、资源特化和必要绑定；不能把 guest 指针或 GS copy span 借给后台线程。复制前后验证资源/代码代际，GPU 命令线程按序消费时再次验证；失配正常退回原有编译。
- 使用独立的翻译 pools、Info/runtime 对象和 backend 编译上下文，不让现有 draw preparation worker 在 `programs_mutex` 共享锁内修改 program/modules。当前 `GetProgram` worker 明确只读；修改为 CompileModule 直接复用公共 pools 不安全。
- 元数据目录只做磁盘索引，按当前场景已知材质/已见管线排序；最多一个后台 job，至多4个排队快照、合计不超过8 MiB（提议上限，尚未实测）。超过预算直接走现有同步路径；不新增大缓存、不减少现有缓存、不长持 guest/Metal 资源。单个已观测 meta 最大10,338 bytes、SPIR-V 最大265,308 bytes，但翻译 IR/Metal 临时峰值未知，因此必须测峰值后决定是否准入。
- 已有 SPIR-V/完整key 的预载路径本来就存在：`LoadPipelineStage` 会 CreateShaderModule；重复实现这件事不能算新优化。新实验应只统计“首次出现且有足够提前量”的完整键，分别量准备提前量、主线程编译减少、首次进入 p95/p99、启动延迟和 footprint。碰到 GS pointer、未捕获 descriptor、无完整 PSO state 时明确拒绝，而不是按 hash 猜。
- 冷目录条目可先做不生成 GPU 对象的头部/哈希/布局校验。它能提前发现坏输入，但不把它宣称为消除 MSL/PSO 编译卡顿。

该方案没有改变画质或现有缓存策略。是否最终能够降低卡顿，需要首次遇到新键的真实路线对照；当前数据只能证明目录和关联已建立。

## 验证与成本

- `test_material_inventory.py` 10项自造测试通过：基础/扩展纹理、float3参数、**逐字节所有截断位置**、尾部垃圾、嵌套块逃逸、错误marker、字符串预算、版本错和家族边界/大小写。全部不含游戏payload。
- `material_inventory.py` 实际304项通过精确边界解析，前后源包SHA相同；`link_observed.py` 实际全部缓存引用SHA匹配。
- 本轮离线分析约3.02秒；`time -l` 最大 RSS197,574,656 bytes，peak footprint123,388,600 bytes。无游戏运行和常驻增加。分析主要同时持有现有大静态 catalog；未来生产目录应流式或单独进程，不能每次把完整 JSON 读到游戏进程。
- 重复关联改成共享 entry 索引：本地 `materials.json` 8,248,450 bytes，身份账本1,782,098 bytes；都只含本地元数据、不含shader原始代码。完整路径/材质名目录仍不应自动进入源码导出。
- 只读格式参考：[SoulsFormatsNEXT MTD 实现](https://github.com/soulsmods/SoulsFormatsNEXT/blob/master/SoulsFormats/Formats/MTD.cs)。解析为独立实现；未拷贝其代码进项目。

## 本地交付

- `material_inventory.py`：独立有界解析候选，尚未集成 tools。
- `test_material_inventory.py`：自造控制。
- `link_observed.py`：源 SHA 校验及本地身份账本。
- `materials.json` / `observed-identity-ledger.json`：仅本机，禁止源导出。

这份报告可由主代理摘录/发布；其中计数和限定不包含游戏payload。若将脚本纳入源码，要由主代理统一审查后移动，并保留原格式参考及测试；本 agent 未修改公共代码或提交 Git。
