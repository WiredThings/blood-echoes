# 启动准备分阶段计时

2026-10-27，开发记录。只增加原 Python 启动调用边界日志，不改变准备算法、完整输入校验、缓存/归档准入、调用次数、返回结构、异常或退出码。现有 QA08 应用未修改，本轮没有运行 native、GPU、游戏或编译器。

## 输出合同

每个实际进入的阶段只输出 start/end 两行；用 `time.monotonic()` 差值记录秒数，不依赖墙上时钟。固定前缀 `Metal preparation phase: ` 后为 JSON：

```json
{"event":"start","phase":"module-inputs"}
{"event":"end","outcome":"returned","phase":"module-inputs","seconds":1.25}
```

`outcome=returned` 表示原调用正常返回；`raised` 表示该调用抛出，包括中断。**正常返回不等于库/管线已经准备完成**：source-fallback、unavailable、deferred 等原结果仍由原报告判定，不把日志当 ready 证据。日志不包含返回 payload、异常文本、路径、环境变量、cohort、凭据或 shader 标识。阶段名只接收内部固定枚举，未知名字在开始前拒绝。写日志失败不代替原调用结果或原异常。

阶段如下；没有执行的分支不伪造 start/end：

| 枚举 | 原调用边界 |
| --- | --- |
| archive-policy | 现有归档兼容身份策略 |
| local-libraries | 首轮本机 Metal 库准备 |
| fixed-helpers | 现有固定 helper 准备 |
| game-preparation | 整个原游戏准备调用，含下面嵌套阶段 |
| source-view | 原 mod 最终视图核验与源快照 |
| static-catalog | 全游戏静态目录准备 |
| capture-receipt | 正常路径捕获配置票据 |
| candidate-scan | 原缓存/完整输入候选扫描 |
| module-inputs | 原模块输入准备与关联 |
| geometry-inputs | 存在 resolver 时的原 GS 输入关联 |
| complete-pso | 原完整 PSO runner 调用 |
| local-finalize | 候选收集完成后的原 Local 最终身份复核 |

`game-preparation` 是父区间，不能与其子阶段直接相加。此轮不增加 draw 热路径计时、后台线程、常驻缓存或新工具模块；`launcher_game_pso` 复用已有工具闭包中的 `launcher_local_aot.preparation_phase`。

## 作者验证

新增 `test_startup_phase_timing.py` 共 9 项纯 CPU 控制：可预测 monotonic 时长/仅两行、阶段枚举、异常与 KeyboardInterrupt 对象身份、不泄漏异常内容、BrokenPipe 不改变结果、Local 各原调用/参数/凭据保持、mock 游戏全链阶段顺序、source-view 失败的原 fallback、模块失败阻止后续 runner、无组件/显式 off 不制造阶段。

另 13 项已有代表性控制通过：6 项 Local 启动合同（保留参数/凭据、off、capture 环境准入、总 swap 边界、严重/锁屏保护）与 7 项自动游戏准备原测试。只使用作者文件和 mock callable，没有运行旧测试中的 subprocess/signal/实际 exec 项。`prepare_local_aot_stage.validate_import_closure` 对当前工具集检查通过，不新增工具闭包依赖。

尚无本轮真实启动耗时数据，不能据此宣称启动提速；下一份统一打包后读取这些区间，才可判断数分钟实际耗在哪一阶段。
