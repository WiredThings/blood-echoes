# 正式角色代码包的隔离 CLI 验收入口

`tools/run_qa_launch_benchmark.py --formal-stage` 在原 receipt/clone/scene driver 上增加代码选择，不新增启动配置，不启动正式 ID 的 GUI。默认 QA 路径和 `qa_launch_conditions.load` 的全部 Shared、账号、原脚本、配置与来源校验保持不变。

输入：`--stage` 仍是原 QA engine；`--receipt` 仍是该 QA 原 Swift 最终环境出口。额外 `--formal-stage` 必须是固定开发项目 `P/evidence/` 内 canonical owned 的新 staging `.app`，不是 `/Applications`。默认执行 `RuntimeProfile.bundle` 完整核外层正式 ID、production FEX 清单、8 个实际角色与所有工具/原 loader/原脚本闭包。再用既有 native verifier 分别检查 top CPU、nested CPU 到 host 的 ARM64/签名/实际声明加载边，以及 GPU 到对应 DSO；两份 CPU 身份不相互冒充。源 QA receipt 的 16 项 effective script SHA 必须与 staging 完全相同。

此模式强制 empty cache：省略 `--cache-mode` 会选 empty，显式 seeded 会拒绝。`qa_launch_clone.create` 仍只从已准入 QA source 复制正常存档与三个配置，创建 fresh Shared benchmark user/data/GPU 根；不会读取正式 Defaults、存档或旧正式 cache。原 bootstrap 自行从这个空 base 建立 `arm64-native-v1`，driver 不追加 namespace、不导入 QA shader/local-metal cache。原 QA 文件保留，正常 source savedata 前后核验继续执行。

执行选择只改 staged engine 的 cwd、原 shell 脚本绝对位置和 Python wrapper 代码路径，以及显式记录的 `BB_GPU_PROCESS`。原质量/语言/特效/4 项保护沿用 receipt；既有采样器的隔离路径、静音、pad/frame telemetry、采样时 quicksave=0 控制仍单独记录，F3 功能测试沿用原 quicksave=1 前置条件及公开声明的统计控制。F3 监测按两条真实 CPU 文件路径收集 PID，原 `run.sh` 负责选择 nested，driver 不伪造 top→nested 映射。

`native_identity` 保留 source QA 的准入；`formal_native_identity` 独立记录正式 staging 的实际代码身份。receipt 与 clone 来源仍明确是 QA，绝不参与正式用户进度选择。只读 package identity 或场景 camera 不证明整个正式 GUI/游戏已验收。

后续由主任务调用，例如（这里只展示接口，本任务未运行）：

```sh
python3 experiments/arm64-runtime/tools/run_qa_launch_benchmark.py \
  --receipt /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/gui-root-TAG/optimized-launcher/out/launch-conditions.json \
  --stage '/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/apps/QA.app/Contents/Resources/bbport' \
  --formal-stage '/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/local-stage-TAG/Blood Echoes.app' \
  --cache-mode empty --runs 2
```

本轮验证：新增 `tests/test_qa_benchmark_formal_stage.py` 的 14 项作者控制通过；使用真实 Python bundle byte reader，注入 native identity verifier，并明确禁止 Popen。覆盖完整闭包、outer ID、production、文件更改、缺 nested、脚本不等、错误 CPU/GPU edge/签名/架构、路径链接/越界、强制 empty、原环境与 source clone 身份保留、画质更改拒绝及 F3 前置。没有启动 native/GPU/game，也没有修改正在运行的旧 driver 或正式数据；真正 staged-role 运行由主任务统一安排。
