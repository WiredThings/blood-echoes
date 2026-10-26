# 有界 PSO page worker（源码候选）

## 定位到的重复工作

`launcher-gui-warm-1793022717819775000` 对应的 `state/game-pso-worker-1793022960430042000/result.json` 显示：409 个 recipe、409 次严格 archive 命中、0 次编译，PSO 准备阶段为 64.78 秒，其中 Local 库验证约 18.41 秒。409 个进程产生 501 个实际 artifact event（包括 tessellation／geometry 内部多管线），不能把 409 当成 artifact 数，也不能把整个启动时间归到这一步。

原 `warm_fast_path` 只是在逐条原生验证之后免掉第二次 Local 建库和最终准备遍历；`ready_hint` 仍只是排程提示。候选不改变这个原则，也不添加磁盘 ready receipt 跳过机制。

## 候选行为

- 原单项 CLI 和固定 helper CLI 保留。只有实际选中 executable 返回精确 `--game-pso-batch-capability`，controller 才用新接口；旧组件继续原单项 CLI。
- `--batch record|prepare` 在同一进程中顺序执行最多 16 个原 packet。phase、allow_compile 或有效环境变化就结束旧页；record/load/build/snapshot/verify 不共用旧进程环境。
- 原 worker 算法抽为 `RunRecipe`，全键、原读集摘要、Profile／spec／SPIR-V、Local 函数路由、descriptor、实际 archive、driver namespace 和严格原生创建全部继续执行。普通、rectlist、tessellation、owned GS 路径不换算法。
- 跨 recipe 仅保留 `Metal::Device`（其原命令队列／residency set）。每条独立创建并释放 `ShaderCompiler`、module/function、variant、`PsoCache`、owned 读集及 autorelease pool。最多一份原读集在途，未预留 16 份输入，也不持有 409 份 PSO。
- 原 runtime `WarmUp()`／`Load*Pipeline()`／`PsoCache::Get*()`／`TryArtifact()` 路径完全不变；progress 数据库仍不能授权跳过 native 验证。

## 协议和失败规则

每条 frame 带 sequence、request identity、packet SHA、明确 phase／policy。native 重新核 packet SHA，并输出 BEGIN、原 `GAME_PSO_RESULT`／artifact events、END。controller 只接受一个属于该 frame 的原生结果，仍逐条核 actual loaded GPU DSO 的路径与字节摘要。

单 recipe 拒绝为 65，并在整页结束时以 partial／65 如实报告；不能靠进程正常通讯把某条失败升级为成功。70／79 为保护或中断，75／76 不属于该协议。进程异常退出、错标识、缺失／重复结果、错事件数量和终态计数均不能算 ready。

锁屏、压力和全准备 deadline 继续由原 supervisor 检查；每条发送和等候时检查同一个全局起点。停止只清理由本 controller 新建的页进程和临时输入，既有缓存保留。

## 当前验证边界

源码候选尚未编译 native，也未运行 Metal 或游戏，不声称已测得提速或内存不变。31 项纯 CPU 作者控制已通过，包括帧错配／缺失／重复、16 项上界、17 项切为两页、phase／policy 换页、旧组件单项 fallback、部分失败、保护退出和全局 deadline。原 strict-first 16 项、queue 19 项、readiness 5 项也通过。transport／controller 测试使用作者替身，不是原生成功证据。

root 后续需要：将 `game_pso_batch.py` 加入既有工具冻结闭包；统一 native 构建；确认新旧单项／批次的每条 descriptor 和 501 个 artifact 身份一致；验证受损 archive、源码／环境变化、partial／critical 等反例；再用同 app 的冷暖准备时间和峰值 RSS／交换增长对照。页进程上界控制显式对象生命周期，但 driver 内部保留量仍需实测。

## 文件

- 修改 `src/game_pso_preparer.mm`、`tools/prepare_game_psos.py`。
- 新增 `tools/game_pso_batch.py`、`tools/test_game_pso_batch.py` 和本文。
- 不改 queue、CMake、host services、layout、根构建计划或在途源码快照。

### capability 的初始化数据根

DSO 的 `UserPaths` 在 main／capability 分支之前初始化，scalar query 同样需要 canonical `P/state` 子目录。探测复用 `game_pso_component._run_bounded_capability`，为每次探测创建并移除全新的私有 state 临时目录；环境仅含固定 PATH 和该临时目录对应的 HOME、TMPDIR、BB_GPU_USER_DIR，不继承账号或普通启动秘密环境。前后继续原 check，70／79 保持保护停止，64 回旧 CLI；78 即使输出看似合法 capability 文本也不能准入。新增六项作者控制验证数据根／干净环境、78／64、70／79、探测后中断和超时清理、linked state 拒绝。没有为这些控制启动原生进程。

## 2026-10-26 正常构建组件的原生定向对照

本节是后续完成的实际原生验证，前述“源码候选尚未 native 编译”保留为源码阶段的历史边界。

- 使用 root 正常构建的 worker `6c24946f…`、renderer `ec7e9309…` 和正常目标 layout `9998823a…`。实际 private-state capability 已通过，非 manifest 布尔准入。
- 只复制 1439 个 raw `.bin/.meta/.spv/.key`，共 13,410,376 字节；拒绝 links，忽略 11 个非 raw 文件。未复制旧 `.hostpso`、MSL、archive 或 31 GB source-view。旧 raw 源 SHA 前后完全一致。
- 新 state、新 cohort、新 Local 库及 native archive，从零执行 17 条 cold batch，随后同输入 warm batch／强制 single CLI。实际 family 为 compute 4、ordinary graphics 7、rectlist 3、tessellation 3；5 条 GS 仍因 session span 未准入，没有猜静态来源。
- 三阶段每 recipe 的函数、完整 descriptor、primary／fragment／route、native key 和全部 **29 个 artifact archive SHA**完全一致，均为原 native-ready 判定。cold 是 4 个页进程（record 2＋prepare 2）；warm batch 是 2 个页进程，single 是 17 个。
- cold 16.807 秒；warm batch 4.193 秒；warm single 4.420 秒。这只有一对 17 条样本，约差 0.227 秒，不能外推为 409 条或游戏 FPS 提升。两轮 Local 验证分别约 1.927／1.952 秒。
- 50 ms 内核采样得到 warm batch RSS／footprint 峰值 54.14／38.44 MiB，single 46.81／34.44 MiB，分别约增加 7.33／4.00 MiB。它们是采样峰值，不保证抓到退出前的绝对峰值；没有采样错误。全程压力 1，swap 9193.875 MiB 不变。
- 另一个 0.46 秒原生协议控制在 lazy Device 之前验证了错误 packet SHA、packet policy 不符、坏 JSON：三条各返回 65，整页 partial／65，未出现 Device 初始化日志或 artifact event；不能升级为成功。

主对照总时长 111.46 秒，未启动游戏、窗口、音频或 Cua，未修改正式数据。源工具 `run_game_pso_batch_native.py`／`run_game_pso_batch_protocol_native.py` 可复现本轮专用步骤；私有完整证据位于 `P/evidence/native-pso-batch-control-20261026-01` 和 `P/evidence/native-pso-batch-protocol-20261026-01`。公开去内容摘要在 `evidence/game-pso-batch-native-20261026/summary.json`。
