# 两类派生 PSO：真实 Metal / 跨进程暖启动作者回归

结论：冻结的 portable-derived-key 两处成功登记逻辑，在自造输入上已通过实际 Metal 渲染与下一进程严格二进制归档命中。这里没有运行《血源》，也不代表已测出游戏卡顿或帧率收益。

## 使用的生产实现

- 主代理一致构建的候选库：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/derived-pso-gpu-1792949840875543000/libbbgpu.derived-pso-1792949840901813000.dylib`。
- SHA-256：`d23894136ecd8df8109850d92bfc82399d0939dd2fbed1ce666dde6366ea26be`。两个进程的实际 DYLD 加载行均只出现这一份 GPU 库。
- 同源头文件与构建树：`D/evidence/pso-derived-integration/local/overlay`（`D` 是本 experiments/arm64-runtime 目录）。主代理构建包含冻结 patch `6992ce6a873101ef105670bd97510243b8d28080810322cee8dbbe5426c7b86a`。
- 实际 `Metal::PipelineCache`、`PipelineCacheCore::WarmUp`、`LoadGraphicsPipeline`、`LoadPipelineStage`、Meta/SPIR-V/GraphicsSerialization、`Metal::ShaderCompiler`、`PsoCache`、Metal factory、GPU 命令和真实磁盘 Storage；没有计数 mock 工厂，没有内存版 Storage。
- 两个 GLSL 文件均为作者自造，通过本机 glslang 编译、spirv-val 校验。没有从游戏复制着色器或游戏资产。

`make_branch_controls.py` 从冻结 proposed 文件逐字提取两处新成功 block，记录块摘要。作者 wrapper 复现原来 `try_emplace` 的首次成功路径，并调用真实 `GetRenderPipeline`。仅将 block 的 `RegisterPipelineData` 名称宏替换为透明观察函数：递增调用计数，再将相同实参转发真实 DSO 函数，仍由真实 Storage 异步保存。这个控制验证新登记合同；没有构造整个 Rasterizer/Upscaler/速度图捕获系统，不能用它声称游戏中的 viewport/packing/read-only-stage 等 eligibility 已做实景验证。

## 实际通过的控制

| 控制 | 实际结果 |
| --- | --- |
| Seed 进程 | PID 88854，退出 0，260 项断言 |
| 独立 Warm 进程 | PID 88857，退出 0，192 项断言 |
| 真正 WarmUp | 从新建磁盘 Storage 恢复恰好两条派生 key；没有在 warm 作者路径手动调用登记器或重造原 PSO |
| Metal 严格归档 | Warm 中实际 `archive_hits=2`；真实 factory 的成功调用均带 `MTLPipelineOptionFailOnBinaryArchiveMiss`，没有接受回退普通编译作为命中 |
| 无深度派生 descriptor | 原 key 是 `Z32Float + Stencil8`；派生后的实际 Metal descriptor 深度和模板均为 `MTLPixelFormatInvalid` |
| 速度派生 descriptor | 原 VS/FS 未替换；实际颜色附件 0 为 RGBA16Float，原深度/模板格式保持 Depth32Float_Stencil8 |
| 顶点元数据 | 实际 descriptor 恢复 location 2/7、slot 30/29、Float2/Float4、per-vertex / per-instance(divisor 3)、动态 stride、sample 1 |
| 原始输出 | GPU 渲染 8×8，全部像素验证；无深度分支与原始 RGBA8 像素逐字节相同 |
| Half 速度输出 | 全部 64 像素的原 VS/FS 输出 `(-0.375, 0.15625, 0.5625, 1)` 精确为 `b600 3100 3880 3c00`（half bits）；没有专用替代速度 shader |
| 跨进程输出 | warm 的 RGBA8 和 RGBA16Float 全图字节均等于 seed 文件 |
| 重复命中 | 16 次重复取得同一 native PSO，无新 native factory、无新成功 block 的 Register 调用，实际 key 文件内容及纳秒修改时间不变 |
| 失败拒录 | 给作者负例的属性格式置 Undefined；真实 PsoCache 在 driver 前拒绝，返回 nullptr，不登记新 key |
| motion-key 拒录 | 使用实际原阶段创建带 motion flag 的作者 key；新成功 block 没有登记它。这不是对真实注入 shader 正确性的测试 |
| 注入 motion Meta 拒载 | 实际 `LoadShaderMeta` 对 `runtime_info.hw.vs.motion_vectors=1` 的 Meta 返回 false，在 native factory 前拒绝 |
| 保存数量 | 最终真实 Storage 恰好 2 个 `.key`；失败/motion 负例没有加入 portable key |
| 释放归档后使用 | 释放暖归档后，仍使用已创建的 native PSO 得到正确像素和 half 输出 |

descriptor 观察是作者进程内短期 Objective-C IMP 包装，始终以未改变的 descriptor/options/reflection/error 参数调用原系统 factory，结束时还原。它只旁路读取、固定最多记录 24 次，没有替换驱动返回值。

`Save` 结论的精确边界：新增登记 block 的调用计数是透明转发的真实 `RegisterPipelineData` 次数，结合真实 key 文件内容/纳秒 mtime 验证重复命中没有重写 key；没有为所有任意 Storage::Save 设置全局观察钩子。WarmUp 的两条实际加载、实际 native factory/归档计数和磁盘文件均是真实实现。

## 隔离与保护

- 成功运行的可执行文件、对象、运行所读着色器与全部状态都在新 Shared 树，未放在 Documents；Documents 中只保存源码、构建/运行脚本、日志和报告。
- 执行根：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/derived-pso-native-1792950118685264000-attempt2`。
- 状态根：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/derived-pso-native-1792950118685264000-attempt2`。
- `MTL_DEBUG_LAYER=1`、私有 HOME/TMPDIR/BB_GPU_USER_DIR、`require_no_games()`、原 NativeObserver / MemoryPolicy 均保留；没有放宽保护。
- 0.70 秒内存压力始终 1，swap 从 7386.6875 MiB 到 7386.6875 MiB，增量 0；这个短控制不能当作游戏长期内存或帧率测量。
- 全新数据库首次读取不存在的 profile 后，真实 WarmUp 按原行为 Clear **0** 个文件。日志中的这条预期信息只涉及此次新建目录，没有清理旧缓存。
- 未启动游戏、GUI、录屏或已有 cachewalker；实际 `WarmUp` 只枚举此次自造的两条 key。未改 O/P 公共源码、正式应用、存档、设置或既有缓存。
- 本测试显式 `BB_MTL_LOCAL_AOT_MODE=off`，验证的是 source-library 路径的严格 PSO binary archive，不是本机离线 `.metallib` 建库全覆盖。

## 首次失败与修正

第一次 seed（PID 88747）在作者夹具的 `LoadMemoryKey` 内错误地将写入游标已在末尾的 Archive 直接交给 Reader，触发 `Serialization::CorruptData`；没有到达新增 native PSO。修正只涉及作者夹具：`TakeOff()` 后重建读取 Archive。`attempt1/` 保留原日志、validation、构建记录和旧执行文件；原 state 没有重用。更早的作者单 TU 语法错误保留在 `syntax-attempt1.log`。成功结果来自全新 `-attempt2` 目录，不覆盖失败样本。

## 复现

1. 由主代理完成同源库构建，提供库路径、SHA 与完整头文件树；本脚本不编 GPU 230 个 TU。
2. 在 Shared 下分别新建空 `P/evidence/<fresh>`、`P/state/<fresh>`（P 为上述 arm64-runtime-prototype），协调无其它 GPU 测试。
3. 在本证据目录运行：

```sh
python3 make_branch_controls.py
python3 build_native.py \
  --source-root "$SOURCE_ROOT" \
  --library "$LIBRARY" --library-sha256 "$LIBRARY_SHA256" \
  --execution-root "$NEW_SHARED_EXECUTION_ROOT"
python3 run_native.py --state-root "$NEW_SHARED_EMPTY_STATE_ROOT"
```

`build.json` 保存实际单 TU compile/link、依赖重定向、签名后可执行文件 SHA 和输入 SPIR-V 摘要；`validation.json` 保存两个子进程 PID、实际库路径、全部保护采样和断言汇总。成功可执行文件 SHA：`3f59a7dcecd0475fd847a445d237c3f62737ae850cf29756e07adc69b335161f`。

剩余实景工作：确认本游戏实际 no-depth/velocity eligibility 的命中率、加载期准备覆盖率及游戏内编译尖峰变化；本作者回归没有提供这些场景收益数据。
