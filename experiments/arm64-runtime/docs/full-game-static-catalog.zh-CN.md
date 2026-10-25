# 全游戏静态目录工具

这些工具只读取自备、已明文的《血源》1.09文件与已有metadata。不会解密、启动游戏/GPU、执行shader或生成游戏资产。入口默认在当前源码树寻找`tools/game_shader_catalog.py`和`vendor/blood_echoes/loader/prepare.py`，不依赖开发机Shared树；`--prototype`仍可显式指定另一份完整源码树。

```sh
python3 tools/full_game_catalog.py --game-root /绝对规范路径/游戏目录 --prior-catalog /绝对路径/catalog.json --out /绝对路径/新的扫描目录
python3 tools/derive_bindings.py --scan /扫描目录 --materials /materials.json --catalog /catalog.json --out /新的关联目录
python3 tools/retry_scan_errors.py --game-root /游戏目录 --prior-catalog /catalog.json --scan /扫描目录 --out /新的重试目录
python3 tools/summarize_scan.py --scan /扫描目录 --recovery /重试目录 --prior-catalog /catalog.json --bindings /关联目录/binding-summary.json --out /新的汇总目录
```

`prior-catalog`来自现有`game_shader_catalog.py`，`materials`来自`material_inventory.py`。没有recovery可省略该参数，多轮recovery可重复传入。所有输出都是新目录，不覆盖原扫描；关联/汇总省略`--out`会创建唯一兄弟目录。全扫描输出必须在游戏目录之外。重试工具只针对历史`unsupported DCX profile`物理输入，先验证其原始SHA，不是自动忽略所有错误的工具。

测试无需游戏、GPU或原生模块：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/test_full_game_catalog.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/run_authored_census.py
```

作者census使用临时DCX/FLVER/BND/BHF字节；`--out /新的报告.json`可额外保存摘要。不生成源码树内固定结果文件。

## 有界元数据

历史已生成metadata的流式测量覆盖15,371模型、120,726去重split条目。以下是记录中的最大规模，未重扫游戏：

| 项目 | 实测最大 | 默认累计预算/边界 |
|---|---:|---:|
| 单模型输出行 |88,469B|另有32MiB估算元数据预算|
| 材料 / mesh |88 /88|分配的材料、mesh、VB、faces、layout合计32,768记录|
| layout表 / VB表 / faces表 |10 /99 /374|仍受源表span及格式计数上界约束|
| layout成员总引用 |已引用成员展开547|解析layout成员累计16,384；mesh展开成员65,536|
| mesh VB / face引用 |99 /374|分别8,192|
| 材料字符串累计UTF-8 |5,209B|1MiB逻辑引用|
| 单BHF条目数 |31,232|100,000|
| 单BHF名称累计UTF-8 |1,453,712B|16MiB逻辑引用|
| 最长BHF名称 |53B|仍有逐字符串8KiB源字节边界|
| BHF物理header |4,379,880B|读取前拒绝超过128MiB|

FLVER原记录没有保留dummy/bone/texture未展开表的最大值和全部物理offset，不能声称测到了它们。其原有100,000计数上界和输入span校验保留，不因未知统计新增较小阈值。10个layout表记录、每表格式上界128成员，也意味着已知模型每个最多1280个表成员，仍低于16,384。

预算逐**逻辑引用**计费；共享同一个offset、成员表、字符串不会绕过累计限制。同时复用已验证的layout/string对象，减少重复分配；流式JSON编码避免再创建一份完整展开字符串。这些计数与估算不是整个Python进程RSS或所有递归解压峰值的精确承诺。

超限抛`MetadataBudgetError`，在`errors.jsonl`标记`coverage_status=budget-limited`并在`gaps.jsonl`记录字段、已用量、请求量和上限。失败对象不计作已解析模型，也不输出伪造的部分成功。摘要保留预算策略/超限次数。必须保留这类缺口，不能删除错误后宣称覆盖完成。

## 覆盖边界

旧全目录统计中的8853程序hash/8311代码摘要只表示已识别静态程序集合；不表示完整PSO，更不表示所有场景已经预编译。FLVER、MTD和同名shader family关联是候选关系，不是完整stage选择。附件格式、动态状态、资源特化、fetch/runtime上下文等还需其它静态证据或有界运行期记录。现在`gnm_shader_metadata.py`已提供默认静态寄存器、usage和semantic tables，`compare_gnm_runtime_metadata.py`可关联现有8036份cache meta；这部分不再笼统标为“缺少寄存器”。但fetch会调整input VGPR等运行时输入，默认头仍不是实际draw的完整PSO，本工具不重复实现该分析。

新扫描会给BND/BHF header显式资源账目。汇总按原资源路径+摘要去重，不把多轮重试的parse attempts相加伪装成不同容器；旧扫描缺header账目时明确保留该限制。加密/不支持格式、可选ELF段和预算缺口都必须报告。全部工具的完整静态PSO数保持0。

不要把游戏字节、游戏metadata/完整JSONL、缓存、MSL或编译库提交到仓库。源码、作者夹具生成器和摘要说明可公开。

格式参考沿用独立parser来源：[SoulsFormats Binder/FLVER定义](https://github.com/soulsmods/SoulsFormats/tree/master/SoulsFormats/Formats)及本仓库vendored loader与shadPS4头。本实现不是复制第三方反序列化器，也不执行其原生对象构造。

## 当前源码的实际全目录重扫

2026-10-26，23项作者控制与端到端夹具通过后，使用本工具重新扫描全部28,852个物理文件、31,438,478,315B。逐文件SHA与上一轮台账完全一致（新增/缺失/改变均0），所有输出进入新Shared目录，原数据和结果未改。

- 15,371份模型、133份split header和120,726条body记录完成解析；新增累计预算拒绝0。
- 仍为8,853程序hash、8,311代码摘要，无新增或遗失程序；80,451个mesh→材质引用中80,296精确关联，155未精确关联。
- 规范化asset draw schema仍为350，同包同stem多阶段候选3,442；原始55,420个含索引数等的partial签名不冒充PSO。
- 唯一不支持的物理输入仍是系统about目录的`right.sprx`；可选ELF段和材质缺口保持明确记录。
- 耗时184.43秒，独立Python进程峰值RSS642.91MiB；监控最高压力1，系统swap净变化+0.8125MiB。没有游戏运行，不代表游戏内存改善或整个进程严格受32MiB估算预算约束。

结果摘要见`evidence/static-catalog-final-replay/validation-summary.json`。完整文件/模型/程序JSONL留在Shared，不上传GitHub。完整PSO、预编译成果和实际游戏卡顿改善仍须后续实现与验证。
