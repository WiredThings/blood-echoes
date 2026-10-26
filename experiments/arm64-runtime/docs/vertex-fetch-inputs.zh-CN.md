# 静态顶点输入与已捕获 fetch 的可用范围

本轮仅分析既有全游戏目录和既有 QA 缓存。没有重新扫描游戏文件，没有启动游戏或 GPU，没有修改现有缓存、正式版、存档或设置。

## 能补齐的字段

`tools/vertex_fetch_inputs.py` 输出三个独立来源域，不把它们按名称或 program hash 拼成一条虚构 draw：

| 输出 | 可确证内容 | 不代表什么 |
| --- | --- | --- |
| `static-vertex-program-inputs.jsonl` | 1,938 个静态 program identity 的原始 GNM 输入语义表、usage 和逐变体源引用 | 尚未解释四个字节各自语义，没有重建游戏 CPU 生成的 fetch 程序 |
| `asset-vertex-layouts.jsonl` | 已有 350 个 asset schema 中的 42 种精确 FLVER 顶点流布局；保留 stream、stride、member byte offset、原始 type/semantic/index 和所有 asset schema 引用 | FLVER semantic 数字不是 fetch semantic 序号；没有未经证明的 VkFormat 或 program/pass 对应 |
| `observed-vertex-fetch-inputs.jsonl` | 原 metadata 的精确 cache、文件 SHA、特化 SHA、native stage/code identity；fetch 的九个 u8 字段；同序号已序列化的 divisor、num_class、dst_select；同缓存精确 vertex permutation 对应的实际 key formats/attributes | 不是完整 V#，不授权模块编译或正式缓存导入 |

fetch 的九个字段为 `semantic, dest_vgpr, num_elements, sgpr_base, dword_offset, instance_data, inst_offset, data_format, num_format`。工具同时检查当前 C++ 声明顺序及编译器生成的 `sizeof(VertexAttribute)==9`，因此不会根据猜测的结构体偏移解码。原始九字节完整保留，typed-load override 与 instruction offset 也不遗漏。

实际 key 关联使用同一个缓存的 **vertex stage permutation**，并重新读取、检查原 key 文件 SHA 及规范 decoder 结果；不能跨缓存拿 permutation 数字相同当作相同输入，也不从 program hash 推导 draw。`vertex_formats` 是后端格式枚举，不能反推完整原始 `AmdGpu::Buffer` 描述符。每条记录保留 key 源文件字节范围与 SHA。

## 为什么现有 flat 表补不上 V#

现有 8,036 条 metadata 中，3,201 条带 fetch，关联到 155 个唯一静态 program identity，共 13,962 个属性：

- 2,013 条有非空 portable walker，只使用 `PushPtrImm / CopyImm / PopPtr / End`；其根指针全部为 SGPR6。
- 1,188 条 walker 为空。
- 全部 13,962 个 fetch 属性的 V# 表根指针为 SGPR2。
- 10,598 个属性的四字描述符未包含在非空 walker 的来源路径中，另 3,364 个对应空 walker。**没有一个属性能从已有 flat 快照证明其完整 V#。**

这不是目录没对齐：`frontend/fetch_shader.h::VertexAttribute::GetSharp()` 通过 `ReadUdReg<AmdGpu::Buffer>(sgpr_base,dword_offset)` 读取 V#；`video_core/pipeline_cache_core.cpp::RefreshVertexInputFormats()` 也明确注明它解引用 guest table，**不是 flattened snapshot**。普通 shader SRT 捕获 SGPR6 不能代替 fetch 对 SGPR2 的读取。现有 `SrtSharpReservation` 只有类型声明，没有序列化的 descriptor 内容。

工具对 portable walker 只做**符号来源解释**：记录根指针索引、常量路径和每个 `CopyImm` 到 flat word 的映射，不计算真实地址，不读取 userdata 中的指针，也不注册或执行旧 walker。只有同一路径、同一 word offset 下四个完整且一致的词都有证据时，才产生 `exact-flat-descriptor-provenance`；这个状态本身仍不授权 native consumer。动态偏移、覆盖目的槽、冲突副本、部分词和嵌套路径均有拒绝或显式缺口测试。

本轮共建立 5,251 条实际 key 顶点输入关联，但它们不能消除上面的四字 V# 缺口。现有 `game_module_inputs.py` 和 native module consumer 的 fetch gate 保持不变。

## 下一层需要的输入

若要让这些 vertex/local 模块进入离线 GCN 编译，应该复用已有 `CompileSnapshot::Capture()` 在有序 GPU 线程捕获的 `RegisterRead { pointer_index, dword_offset, buffer }`，以独立版本化、限长、带完整来源身份的格式保存**实际读到的四字描述符**，随后使用 owned reader，不能调用旧 Info/SRT 反序列化或重新解引用保存的地址。现有 transient snapshot 明确不应直接整体序列化；需要独立持久化契约及来源/有效性校验，而不是把 C++ 对象原样写盘。

纯静态全游戏资产侧仍缺：游戏 draw 对哪组 shader/pass 的权威选择，FLVER 成员至 fetch 属性的实际映射，V# 原始 descriptor 与实例步进状态；完整 PSO 还需要附件格式/样本数、混合和深度模板状态，以及真实资源特化。已有 GNM 原始输入语义和 42 种 FLVER 布局已经结构化保留，可作为这些权威关联的输入，不能直接标成 PSO。

## 验证和私有证据

13 项作者夹具定向测试通过，命令：

```sh
python3 -m unittest discover -s experiments/arm64-runtime/tests -p 'test_vertex_fetch_inputs.py' -v
```

实际 CPU 分析约 2.4 秒、输出约 22 MiB；输出仅为私有元数据。私有目录通过 `evidence/vertex-fetch-inputs-current.json` 定位；公开证据只包含聚合计数和摘要，不包含游戏布局/语义表或缓存内容。没有运行性能 benchmark，也没有宣称帧率或内存占用改善。
