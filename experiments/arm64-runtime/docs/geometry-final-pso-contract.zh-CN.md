# 独立 GS 最终管线准备合同

2026-10-26；当前源码接点，未启动 GPU/游戏。源 GCN→SPV 的实际重译与 producer 准入由原模块准备控制器负责；本轮 CPU 作者测试只检查合同，不冒充真实重译或 Metal 管线完成。

## 共用运行时算法

`mtl_pipeline_cache.mm::GeometryShaders` 的既有实现搬入 `mtl_cached_variants.h::CachedVariants::Geometry`。运行时只把 handle 转成 native module 数组并委托，原有 SPV 分析、primitive 匹配、三种 index-type ES、GS kernel 反射、FS inputs、clip-space 和 pass-through 生成顺序不变。与修改前 HEAD 正文进行归一比对，除 handle 转换、`VariantShader` 对既有 `Variant` 的委托和传入既有 pass-through cache 外一致；没有另写 GS 算法或改变绘制政策。

独立 worker 用同一 helper 生成 ES index 0/1/2、GS kernel、pass-through，可选 FS 沿原 BuildModule。GS 的 plain ES 与运行时一致，不作为 variants-only 的成功前提；三个实际 ES kernel 仍须全部成功。最终调用同一 `PsoCache::GetGeometryPipeline`，不创建完整 GPU Runtime、资源表、纹理或 draw。

## 新旧 schema 分离

普通 `packet_schema=1` 继续拒绝 GS key/stage，没有删除旧 gate。新的独立入口必须同时满足：

```
packet_schema: 2
manifest_schema: 2
geometry_pso_schema: 1
kind: owned-gcn-geometry-pso-input
candidate_only: true
runtime_cache_import_allowed: false
native_info_srt_allowed: false
walker_bytes_passed: 0
```

仍使用原 record/prepare CLI、canonical key reader、cohort/Local build、Shared fresh scratch、完整 artifact/root、当前 flags、源文件 SHA/大小与整体 8 MiB 预算。所有 file refs 必须是同一冻结 packet 目录内的 owned canonical regular file，SHA 为 64 位小写 hex；不能拿旧外部缓存目录充当本次 source scratch。

新 packet 公共字段沿原协议保留 `key`、`layout`、`original_cache`、`current_cohort`、`cache_id`、`recipe_blob_identity`、`frozen_input_set_identity`、`artifact_cache_dir`、`allow_compile`。新增 `retranslation_profile` 文件 ref，要求现有 profile ABI header/长度正确。

阶段集合必须是 logical vertex（hardware export 或 vertex）+ logical/hardware geometry，可选 logical/hardware fragment；禁止 compute、tessellation、motion。每个 stage hash 与完整 key 严格对应，没有省略模块。缺失原 vertex attributes 的 GS key 仍拒绝。

## 每阶段的明确来源

每个 stage 保留原 `program_hash`、`permutation`、`permutation_hash`、`logical_stage`、`hardware_stage`、`spec` ref（source_offset=24、spec-only）和 `spirv` ref，另提供：

- `module_input_packet_sha256`：实际成功 CPU module-preparer 的完整输入 packet 摘要。
- `retranslation_receipt`：该次成功 result.json 的冻结文件 ref。
- `source_provenance`：实际来源 title_id=CUSA03173、app_ver=01.09、code_prefix_verified=true，以及 code/container/entry SHA。

worker 在 typed spec 读取及任何 Metal Device 构造前核对 receipt 的 `module_retranslation_schema=1`、完成状态、原安全 flags、输入 packet/code/spec/profile SHA、program/stage、输出 SPV SHA、`expected_spirv_equal`、`current_generated_srt_verified` 和 unknown-program-base 拒绝事实。

**生产者兼容不是由这些 JSON 布尔值自行建立的。** Python 控制器必须先核验实际 CPU worker、固定 compiler DSO 的真实加载与 SHA，执行原 owned GCN 重译及结果检查，再冻结这些 refs；不能拿旧 cache 的 profile/spec 看似一致、历史 receipt 或手填成功标记代替该步骤。此 worker 负责验证这条已准入结果链与当前 PSO 输入完全绑定，不会重新读取 Info/SRT 或执行旧 walker。

## GS span 与 copy 单独绑定

GS stage 另外提供：

```
spec_rebinding:
  original_spec_sha256: <原 captured spec 摘要>
  runtime_span_offset: <当前 ABI 的 RuntimeInfo.hw.gs.vs_copy 偏移>
  bytes: 16
  serialized_span_zeroed: true
  retired_pointer_exported: false
gs_copy:
  code: <owned copy GCN file ref>
  program_hash: <16 位 hash>
  source_provenance: <原同 container/entry 的 copy 来源>
```

在原 StageSpecialization reader **之前**逐字节确认 serialized span 的 16 字节都是零；任何一字节非零、偏移或长度错误均拒绝。没有把旧 session 指针重新绑定进 native PSO worker。

GS receipt 还必须证明 `owned_geometry_copy_rebound=true`，其 `gs_copy_sha256`、`gs_copy_source_provenance` 与冻结 copy 完全匹配；copy 与 main 同一来源 container/entry，code ref SHA 与来源 SHA 相同。copy 的 GCN prefix、dword extent、program hash 与 spec 的 `vs_copy_hash` 匹配。copy bytes 在这里仅作身份核验，既不执行也不传给 Info/SRT；实际 copy 重绑定发生在原 CPU module preparer。

## 结果与尚未验证的范围

结果列出所有实际 PSO functions：三个 `geometry-export-index-N`、`geometry-kernel`、`geometry-pass-through`，可选 `fragment`；若 BB_GS_DEBUG 沿原 PsoCache 替换 FS，还额外记录 `geometry-actual-render-fragment` 的真实来源，不修改该 flag。各自必须有真实 persistent identity 和 source/local route。报告 index variants=3、预期最终 artifacts=5，以及原 PsoCache 全部 attempts/hits/built/compile/deferred/failed/critical/unavailable、strict archive hits。记录阶段不宣称最终 PSO 完成。

当前语法检查覆盖 worker 与运行时 TU。47 项无 GPU 作者控制覆盖新旧 schema、旧 key 的 GS 拒绝、类型/版本、GS key domain、16 字节 span 各自非零、截断、receipt 缺失或字段错配、profile/spec/SPV/GCN 摘要、禁止的旧 walker/guest/GPU/Info flags，以及正常 ES/GS 合同。作者 JSON 仅验证 gate，不是实际来源凭证。

后续由统一 native 构建验证真实 77 条 GS PSO 的 record→本机建库→prepare、实际 function route、五个最终 artifact 和暖 strict 命中。**本轮没有完成或宣称这 77 项已经可用**，也没有更新正式应用、缓存、存档、设置或 Release。

最终短验证证据：Shared `evidence/geometry-pso-source-final-1793003873799047000/result.json`；公开去内容摘要在 `evidence/geometry-final-pso-source-20261026/summary.json`。CPU控制可执行只链接 Foundation/CoreFoundation、libobjc、libc++、libSystem，没有链接 Metal。
