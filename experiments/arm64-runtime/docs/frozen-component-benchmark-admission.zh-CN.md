# 当前 GUI 组件进入性能入口前的合同核对

2026-10-27，只读源码/已存在 QA manifest 审计；未启动 GUI、原生工具、游戏、GPU 或编译。QA08 正由主任务组包，本文不给它预填二进制摘要。当前可读的最近已封口 GUI 包是 `P/apps/Blood Echoes ARM64 GUI QA 1793029386377677000.app`（QA07），`P=/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。QA08 应使用自己最终 manifest，不能沿用该包或旧 `P/build/fex` 身份。

## 确定会拒绝或改变条件的现状

| 位置 | 现状与影响 |
| --- | --- |
| `tools/run_launcher_engine_abba.py:68-80` | frozen 分支先证明 `original_scripts` 等于旧 baseline，但随后按这组摘要核对**实际 effective stage**，再把实际文件复制给 Rosetta 并再次要求 original 摘要。两个受控变化 `run.sh` final-exec hook、`tools/macos/run_optimized.sh` FEX 日志修正都会触发拒绝。不能删掉所有脚本校验解决。 |
| 同文件 `:32` | `--component-stage` 只允许 `order=['arm64']`，禁 lifecycle/memory snapshot。它目前是单 ARM smoke 的合同，不是新包 ABBA 或死亡/F3 入口。 |
| 同文件 `:44,103-107,125` | cache、INI/save seed、游戏路径和语言仍来自旧固定私有条件；没有复用当前 GUI 实际选择。`metal_environment()` 还会主动覆盖 archive/relaxed_math/strict_float 为 0（`launcher_environment.py:20-29`），不能把它称为当前 GUI 完整设置。 |
| 同文件 `:107,125-137` | runner 环境不读新 GUI 的 extraEnv。CPU 默认会补部分 hook，但不补 `BB_ARM_PAYLOAD_GATE`、`BB_ARM_RAW_PREWRITE` 或 `BB_LAYER_READ_TRAPS`；后两项在 renderer 中默认关闭。`BB_GPU_LIFETIME` 恰好被旧 policy 加入不代表其取值与本次 GUI 一致。 |
| 同文件 `:267-280` | 时间从 shell 开始算，90–180 秒包含自动静态目录/建库准备；不能用“未及时入场”认定 runtime 失败或稳态性能下降。默认首启准备可能长于整个旧 runner 预算。保护观察可持续覆盖准备与游戏，但性能采样计时应从场景条件满足后开始。 |
| 同文件 `:228,262` | 扩展诊断仍精确认旧 CPU/GPU 绝对路径。当前 component 限制挡住其中一些分支；不能只解除限制而不更新实际角色识别。 |

QA07 manifest 记录的两个摘要确实不同：run.sh original `3d3ad017...9d46` / effective `fe9ed3fc...5b44d`；run_optimized.sh original `ba021134...c67b` / effective `8be77302...ff7`。该包 extraEnv 四项保护均为 1，是 `package_local_arm64_qa.py:166-167` 明确加入的，不是推测的默认值。

## 最短复用办法

1. **原 GUI 决策仍是唯一设置来源。** 权威链是 `LauncherModel.environment():571-629`（extraEnv→路径→原 `OptimizedProfile.applyingEnvironment`）→`startPreparedGame():658-685`（联网信息/保存原 INI、mod/patch profile）→`start():699-702`。preferences.plist 是输入，不能由 Python 再实现一遍 Swift 决策。当前未发现已经提供的最终 GUI 环境 JSON 出口；若需自动对照，应在原私有 QA 启动边界取同一个 `env` 和 argv 的受限、无凭据记录/交接，而不是继续扩展旧手填 env。联网 token 不得落盘或复制到离线对照条件。
2. **脚本 provenance 与执行字节分开。** 用原 baseline 核对 `original_scripts`；用 `effective_scripts` 核对当前 stage。再用现有 `prepare_local_aot_stage.HOOK` 与 `runtime_log_revision()` 验证仅两个已准许变化、其余 14 脚本相同（其生成点 `prepare_local_aot_stage.py:143-153,194-200`）。Rosetta ref 如果需要 original 字节，必须从 original baseline 复制；如果两组共用 effective 脚本，须按 effective 摘要复制，并保持 Rosetta 的 ARM hook guard 未启用，报告不能再写“全部 original 字节”。先只修单 ARM smoke，不自动扩成混合 ABBA。
3. **只替换测试专用路径与控制。** 从同一 GUI 决策生成的配置/INI/mod/patch profile 克隆到全新 Shared state；保留原脚本做 mod 合并、社区汉化 last-mod、内容/补丁生成与最终 argv。每个 case 生成新 user/data/nonces/session marker；原文件不改。cache 使用显式 QA cache 的新克隆，不能写回 GUI 在用 cache，也不能复用不兼容 renderer 身份的产物冒称暖缓存。
4. **显式保留当前保护组合。** ARM `run_env` 保留 `BB_ARM_PAYLOAD_GATE=1`、`BB_ARM_RAW_PREWRITE=1`、`BB_LAYER_READ_TRAPS=1`、`BB_GPU_LIFETIME=1`，以及原 UI 决定的资源/Metal选项。不能随后用旧 `metal_environment()` 或语言 fixture 覆盖。`guest_resource_runtime.h:106-107` 证明 payload gate 独立准入且依赖 lifetime；`buffer_cache.cpp:3567-3568` 证明 raw prewrite 还要求 read traps；`buffer_cache_layer.cpp:1321-1325` 证明 read traps 独立默认关闭。缺其中一项就不是当前已修保护路径。
5. **实际产物取新包合同。** CPU 是 `<stage>/bin/bb-probe-metal`，CPU host 是 `bin/gpu-metal/libbbgpu.dylib`，独立 GPU 是 `bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu` 与 `bin/gpu-metal-arm64/libbbgpu.dylib`。它们来源分别是最终 manifest 的 `cpu_runtime_manifest/upstream_inputs`，运行身份是 `frozen_runtime`；重定位/签名后摘要允许不同于 raw normal-build 摘要。必须精确验证实际加载边，不能用原始 SHA 直接替代包内身份、不能用 `P/build/fex` name-match。CPU 样本里 `bb-probe-metal` 角色可继续仅对已准入 frozen CPU 映射为 ARM CPU。
6. **保留原最终执行和退出语义。** 原 `run_optimized.sh`→`supervise_restart.sh`→`run.sh`→当前 local startup wrapper，最终 `launcher_local_aot.py:169-183` 仍 exec 同一 executable/argv。F3 76、回菜单75由原 supervisor消费；不要改成 runner 自己模拟恢复。性能 case 只把自动输入交给已存在 `--benchmark` 路径，功能验收单独保留真实键鼠/手柄与 quicksave。
7. **音频和计时差异要明示。** GUI QA 当前 `BB_AUDIO=none`；旧 benchmark 则要求两条真实音频 stream muted 日志（`:284`），不能原样继承前者又沿用后者断言。纯功能验收可维持 timer sink；性能对照若保留原音频 cadence，应只在两个对照组一致地去掉 timer-sink override、启用 `BB_BENCHMARK_MUTE=1`，作为显式 benchmark-only 条件。不得发声。冷准备、暖准备、入场与最后稳态窗口分别计时，锁屏/严重压力/快速 swap 增长保护仍完整生效，不能在采样开始时清掉累计增长基线。

## 修改前应有的短定向控制

- 双 hook effective stage 通过；原 baseline 被改、第三脚本被改、hook 重复/缺失、有效摘要伪报均拒绝；Rosetta ref 复制所声明字节而非混用两套摘要。
- 当前 GUI 原 env 的四项保护及其余选项逐项保持；缺项、错误值、旧 fixture 试图覆盖、renderer identity 与 cache 身份冲突均拒绝/清楚标明不适用，不能静默降级成旧保护路径。
- 新 CPU path 被实际角色识别，旧 CPU 路径或额外同名进程不误认；包内 SHA/实际 DSO 错配拒绝；raw normal SHA 与封口 SHA 分开记录。
- 全新 case 仅克隆 QA 文件，不改原 user/data/cache；拒 symlink、非 Shared 路径、已有输出和逃逸；nonce/boot/content/base-game session identity 与原 FEX 检查一致。
- 模拟长准备后才出第一帧：准备保护不中断，性能窗口不提前耗尽；模拟准备退出74、75、76，runner 不把它们误当成功入场或自行重启。
- 声音 timer-sink 与零增益真实流两种模式断言分开；两组相同音频条件，保持静音；不得拿一次单 ARM smoke 写成 ABBA、死亡、在线或长期内存验收。

这些是接入前已证实的源码问题与最小方案；本文没有执行任何新增反例或实际性能对照，也没有证明 QA08 的最新场景与性能。
