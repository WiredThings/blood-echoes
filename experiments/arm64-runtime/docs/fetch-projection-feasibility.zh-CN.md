# Fetch 编译投影：已知字段与真实歧义

**旧缓存虽然没有 V#，其中 1,152/3,201 条记录仍可证明编译所需的 fetch 语义；其余 2,049 条存在转换歧义，继续拒绝。** 这不是从 backend format 反造 Buffer。实际 13,962 个属性全部 `data_format=0`，没有 typed-load override；`num_format=0` 在这种情况下不代表原 V# 的数字格式。

| CompileModule 内调用点 | 实际编译依赖 | 唯一来源／等价证明 |
|---|---|---|
| `Translator::EmitFetch` | `GetNumberConversion()`、有效 `DstSelect()`；目标 VGPR／语义 | 后者已在 vs_attribs 保存；前者由完整同 cache key 格式的枚举等价类证明；fetch 全 9 字段原样核对 |
| SPIR-V `DefineInputs` | `GetNumberClass(GetNumberFmt())`，输入固定 vec4 | vs_attribs 已保存 class；新增 class 接口，不伪装原 NumberFormat |
| `StageSpecialization` | nonnull、divisor、class、有效 swizzle | 原 spec 的非默认语义字段证明 nonnull，不能看 key 猜；divisor 与原 RuntimeInfo 步进核对 |
| 其它普通资源访问 | 完整 sharp／owned flat，可能有程序基址依赖 | 保留原路径和缺失读取拒绝，projection 不能替代它们 |

Fetch 的 `base_address += inst_offset` 不参与以上编译输出；stride、base、num_records 仍是实际绘制／`GetVertexInputs` 的真实资源输入，**不填 0、不合成描述符**。投影只替换三个明确的编译读取点；其它 `ReadUdReg` 缺项直接抛错。原 fetch 相等运算漏比 `inst_offset/data_format/num_format`，新接口逐一比较全部 9 字段。

物理 V# 的 data_format 为 4 bit、num_format 为 3 bit。原生消费者遍历原枚举域 16×8，调用原 `RemapDataFormat`、`RemapNumberFormat`、`TrySurfaceFormat`、`TryMapNumberConversion`，要求该属性所有实际 key format 和 class 的转换结果唯一。可证明的当前格式为 `{37,91,100,103,106,109}`，class 均为 Float、conversion 均为 None；对应 **1,056 Vertex + 96 Local/逻辑 Vertex** 记录。Local 的 ls_stride 另从原 RuntimeInfo 验证。计数是字段可投影状态，不是已准备的最终 PSO，也不是全游戏覆盖。

真实歧义例：程序 `000000000073ecc9`，metadata SHA `01454ec3…eac0e4`，key `g_0xeb12ffb892a97319.key`，属性 1 为 backend `41/R8G8B8A8Uint`、class=Uint、RGBA swizzle。原始 `Format8_8_8_8 + Uint` 和 `Format8_8_8_8 + Uscaled` 都产生上述相同字段，但转换分别为 **None / UintToUscaled**；现有缓存无法区分。类似歧义还存在于观察到的格式 96、82、14。静态 FLVER 布局没有到实际 draw 的权威关联，不能借其猜掉原格式。

实现使用独立 `owned-gcn-fetch-projection-module-input` 和 typed `FetchCompileProjection`，只持有 class/conversion/swizzle/divisor/nonnull 与完整 fetch9 字段，无 Buffer、地址或 stride。CompileSnapshot 尾部新增 optional；动态预算计入 SizeBytes，与 raw RegisterRead 互斥。默认正常分支保持原 GetSharp，不增加 live 读取或无关 NumberConversion 计算。

验证：当前翻译／发射相关 6 个 TU 均实际 override 编译；**4 VS + 2 Local 逐字节匹配观察 SPIR-V**，普通／GS 各 1 增量回归通过。15 项 Python 证明控制、15 项 C++ API 控制、25 个原生反例通过，后者包含上述真实歧义源包的拒绝。上述 6 项为分项对照；随后已完成 235 TU coherent 重建及 typed companion CPU 控制，详见首启接入说明。始终没有启用 GPU 或测帧率；正式存档、设置、缓存与应用保持不变。

私有证据均位于 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/`：`fetch-projection-inputs-final-1793003750058817000/manifest.json`、`fetch-projection-native-final-1793005658704803000/result.json`、`fetch-projection-negatives-20261026-01/result.json`、`fetch-projection-api-final-1793005656777813000/result.json`。原游戏派生输入不提交，公开仅保留去内容摘要。

通用 converter 的 `scan()` 默认遍历全部可证明条目；旧 4 VS／2 Local 限额仅是作者定向对照，不是启动策略。`input_identity()` 对完整 raw metadata／Profile／参考 SPIR-V、所有 key 证明、静态代码身份、游戏身份和 ABI 做稳定字节摘要，路径和时间不参与；它不是旧 runtime permutation key，也不证明旧 producer 兼容。默认启动入口与剩余 coherent 集成边界见 [首启接入说明](fetch-projection-startup-integration.zh-CN.md)。
