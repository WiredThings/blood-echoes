# 固定游戏阶段头与编译输入（2026-10-26）

## 本轮实际补齐的静态来源

以前的程序目录读取了GCN与OrbShdr身份，但忽略GCN之前的阶段头。实际游戏文件还保存了`Shdr` v7.1、公共信息、阶段寄存器、usage表以及输入/输出语义。自有工具`tools/gnm_shader_metadata.py`现把它们与原code_offset、BinaryInfo和code SHA相互验证，单独生成只留本机的JSONL；不提取或分发游戏代码与编译资产。

公开格式参考：[OpenGNM shaderbinary](https://github.com/PS4-OpenGNM/opengnm/blob/4b295ca54c82c83acf308d1c646a2dfa9ae57350/include/gnm_shaderbinary.h)、[寄存器与语义结构](https://github.com/PS4-OpenGNM/opengnm/blob/4b295ca54c82c83acf308d1c646a2dfa9ae57350/include/gnm_shader.h)。游戏实际v7.1的ES/LS/CS/GS/HS有额外字段，不能把公开最小struct长度直接套上去：本工具保存未知字段为未解释的整数，用真实usage数量、完整表长度、相对code指针及独立OrbShdr校验边界，不臆测字段语义。

- 非调试7包共15,323条文件，包含17,024份阶段头；另1调试包明确不计。
- 原SELF loader重建的8个模块再得到5,495份头；总计22,519条程序记录全部关联成功，无拒绝、无遗留未关联程序。与已有完整文件扫描相结合，提高静态程序来源覆盖；不是全特化/PSO完成声明。
- GS文件内有1,701份嵌入复制VS，其code relocation相对外层GS公共头。实际复制VS表从GS固定56B及usage表之后开始，并以其自身usage/语义表结束；每条仍要求独立kind6 BinaryInfo与代码跨度吻合，不能误用GS主程序身份。
- 模块中的头未必按全局4B对齐；padding按公共头相对位置验证。此差异已补定向测试，最初全局对齐导致的5,139个拒例保留在本机中间结果，最终不再误拒。
- 解码覆盖8,915个`program_hash × hw_stage`对；stage必须保留，不能把同hash不同阶段合并。

各阶段寄存器可提供RSRC1/2、FP模式与默认用户寄存器数等；VS另有输出配置/位置格式/clip-cull控制，PS有输入ena/addr/control、颜色/Z export及shader mask，CS有线程维度和LDS设置。ES/LS的额外字与HS/GS扩展值尚只按原值保留，不能宣称已全部转为RuntimeInfo字段。

## 与现有完整缓存的只读对照

`tools/compare_gnm_runtime_metadata.py`只分析已由规范布局解码的JSON，不运行原生反序列化、SRT walker、GPU或游戏。48份逐目录解码输出中，31份实际游戏缓存共8,036条metadata，全部有静态阶段头匹配；克隆记录仍按实体计数，不夸大为8,036个不同特化。

这次比较仅针对`num_user_data`、`num_input_vgprs`、`num_allocated_vgprs`三项，CS按原BuildRuntimeInfo只比较两项：

- 5,809条与某一静态默认值组合一致。
- 2,227条需要修改默认值；差异全部是输入VGPR数：vertex 2,083、export 48、local 96。按已序列化spec摘要去重分别141、3、6种，不是2,227个新程序。
- 没有发现其它两项寄存器数量不一致；没有据此声称整个RuntimeInfo、resource specialization或PSO一致。
- [OpenGNM实际fetch设置实现](https://github.com/PS4-OpenGNM/opengnm/blob/4b295ca54c82c83acf308d1c646a2dfa9ae57350/src/shader.c)会修改SGPR及VGPR_COMP_CNT。比较证实文件默认值不能直接冒充draw时的最终输入；该差异不是本轮发现的运行时bug。
- 8036条匹配记录的序列化spec去重为523种，这只是在本次三个数量字段比较范围内标记重复记录；不能用该数替代不同PSO、不同原始metadata文件或语义等价类数。

原完整key/meta/SPIR-V引用图已另完成只读盘点；各cache本地permutation索引不能直接跨目录复制。曾记录的长编译VS `37a8af3d`已有一个完整graphics配方，基线409键中没有；这应通过正确的完整配方准入与加载期准备解决，不应重新用基础hash列表代替。

## 用法与内存边界

先用已有`game_shader_catalog.py`在用户本机生成来源目录；输出均选择新的私有目录。

```sh
python3 tools/gnm_shader_metadata.py \
  --game-root /absolute/path/to/CUSA03173-1.09 \
  --prior-catalog /absolute/path/to/catalog.json \
  --output /absolute/path/to/new-private-header-directory

python3 -m unittest discover -s tests -p test_gnm_shader_metadata.py -v
```

SELF/ELF重建前检查输出范围上限128MiB，沿用原loader；usage/语义表、程序跨度、候选数量和JSON行均有界。输出使用逐条/分块JSON写入。本轮仅是独立、游戏未运行的静态工具，不加入启动器，不增加逐draw工作或常驻游戏缓存。

实际扫描耗时、峰值RSS和输入摘要见`evidence/gnm-header-census/validation-summary.json`。RSS包括原loader复制、解码及Python对象，不能把独立扫描成本隐去，更不能把它写成游戏内存改善；若后续集成准备阶段，必须独立进程串行运行并重新测启动峰值。

14项作者测试覆盖各stage、完整截断、stage/relocation不匹配、语义越界、重复身份、GS复制身份、相对对齐、ELF预分配预算和修改/歧义/克隆比较。它们不运行真实着色器或证明最终画面。

## 下一步与未完成字段

将本轮真实静态寄存器/usage/语义，与已经收集的材质/顶点layout、真实完整配方及fetch设置关联。仍需确切取得附件格式/采样/混合与深度模板状态、资源描述符特化、fetch实例率以及GS/Tess运行配置；不能对这些字段静默填默认值，也不能将基础程序目录叫完整PSO预编译。

本机离线建库继续按默认启用的正式方向推进，覆盖全部静态建库入口；原生最终PSO、typed function constants及helper准备也必须闭环。未知组合异步只兜底。正式应用、存档、设置和旧缓存不动；源代码/摘要文档只推开发分支，不更新Release。
