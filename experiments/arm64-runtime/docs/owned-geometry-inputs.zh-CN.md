# GS copy 的独立 owned 输入闭合

本轮仅增加独立 CPU 准备接口，不修改普通 packet、原 core reader 或旧 worker 的 GS 拒绝规则，不启动游戏／GPU，不修改正式存档、设置和已有缓存。

## 实际覆盖

显式选择的 35 个隔离缓存共 8,544 个 metadata。51 个物理 GS 记录中，`8e3f8dc4` 有 35 条、`1635154c` 有 16 条；按完整 raw profile/meta/SPIR-V 去重后为 **14 套输入**，另 37 套完全重复。先前静态 sidecar 报告的 48 条来自稍小的缓存语料，本轮增加了最新 QA 缓存，计数口径不同。

14 套均为真实 `HwGeometry/SwGeometry`、无 fetch：两个程序的 copy 分别为 90 words（360 B）和 43 words（172 B）。copy 与主程序的 program hash 相同，但原生硬件阶段和 code SHA 不同，不能仅凭 hash 拿主程序当 copy。静态最终索引把 main/copy 绑定到相同 package、entry、source SHA 和 outer header；冻结前再从原始 1.09 包独立提取、核对两段 GCN。

`tools/owned_geometry_inputs.py` 冻结了 14 套新输入，不包含原 session span。包清单：

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-inputs-1792999098582447000/manifest.json
```

## 退休 span 的替换

新 packet 使用独立 kind `owned-gcn-geometry-module-input`。普通 source/profile/spec/flat/walker 形状继续复用，但 `spec.bin` 内 `RuntimeInfo.hw + HwGeometryRuntimeInfo.vs_copy` 的 **16 字节整体清零**，包括旧 pointer 和 length；原 pointer 的数值不写进 packet，旧 RuntimeInfo/Info/SRT 不进入原生反序列化。旧 spec 的 SHA、copy words/hash 只作为标量证据保存。

`gs_copy.code` 是独立的新文件，具有 code SHA/长度和同条目来源证明。consumer 先检查原始 span 确实全零，再通过原 spec-only reader；随后把 copy 读入 `CompileSnapshot.gs_copy`，重绑定 actual 与 expected runtime 到本进程拥有所有权的 span。它只允许读取这段 owned code，绝不跟随原来的 pointer。

完整 Profile、资源 spec、重新生成的 SRT/walker 数据和 flat 长度均核对；program base 未知时拒绝依赖；最后生成的 SPIR-V 必须逐字节等于观察模块。GS 的 `in/out_vertex_data_size` 和 `num_invocations` 额外显式比较，避免依赖原 Geometry `operator==` 中省略的字段或仅判断非零的比较。

实际 GS 状态范围为：num_outputs=0，outputs 全零，num_invocations=1，output_vertices 为 8 或 40，in_vertex_data_size 为 28 或 60，out_vertex_data_size 为 12 或 28，输入 PointList，四项输出 TriangleStrip，模式 ScenarioG。独立 consumer 本轮只接入这条有证据的阶段子集，仍不放开任意 GS 场景或完整 PSO 链。

## 原生结果与定向验证

针对性 CPU consumer `game-module-native-1792999112833083000` 对 **14/14 套输入全部通过**：重新翻译真实 main+copy、重建 SRT、比较资源 spec，并与对应观察 SPIR-V 逐字节相同。运行约 1.5 秒；前后内存压力均为 1，swap 没有增长。冻结与扫描约 3.2 秒。这不是完整 renderer 的 coherent 重建，也没有创建最终 Metal PSO。

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-native-positive-1792999330898388000/result.json
```

结果按 14 套 cache 状态计算，涉及两个真实 GS 程序；不能称为 14 个不同的全游戏程序或 14 个最终 PSO。运行时导入仍禁止。

```
python3 experiments/arm64-runtime/tests/test_owned_geometry_inputs.py
```

12 项作者自造夹具测试通过，覆盖真实 bounded metadata/source reader、整 span 清零、原 pointer 改变不影响 sanitized spec、原始文件不改、copy hash/长度/阶段/来源配对、同 hash 多 copy 代码歧义、GS 字段枚举／边界、canonical permutation、新输出不覆盖，以及必须取得显式 native owned-copy rebound 证明。

最终 policy CPU 构建 `game-module-native-1792999422135049000` 仅增量复核 1 GS 和 1 普通 CS，二者均通过；GS 结果明确 `owned_geometry_copy_rebound=true`、`geometry_copy_bytes=360`。没有为 policy 增量重跑全量正例。

同一最终构建的 **27 个原生 GS 反例全部通过**：非零 retired pointer/length 在 typed reader 前拒绝，copy SHA/空 SHA/长度/hash/stage、同源 container/entry、rebinding 和 ownership policy 不匹配均拒绝；GS 枚举／字段范围及 malformed original-spec SHA、非 null unknown program base 也在预期门拒绝。每项退出 65、输出目录空。所有输入变更只作用于新克隆，各子进程独立 `BB_GPU_USER_DIR`、30 秒限制和内存保护。

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-policy-positive-1792999647098778000/result.json
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-negatives-verified-1792999700510143000/result.json
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/module-final-policy-ordinary-1792999702284386000/result/result.json
```

最后的纵深门构建 `game-module-native-1793000307708837000` 只补做三项：1 个混种 kind/stage 反例在**已有 RuntimeEnvelope 前置门**以 `geometry schema and stage must match` 拒绝；1 GS 和 1 普通 CS 仍通过完整 source/SRT/spec/SPIR-V 校验。原来就有该前置保护，不能把本次新增的反序列化后同义门描述为修复了可触达错误 union。最终计 27 个 GS 反例 + 1 个混种反例；没有再次运行全量 14/31 正例。

```
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-last-kind-negative-1793000521061996000/result.json
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/owned-geometry-last-policy-GS-1793000522131645000/result.json
/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/module-last-policy-ordinary-1793000522367783000/result/result.json
```
