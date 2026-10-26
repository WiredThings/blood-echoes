# 正式角色代码包的隔离 CLI 验收入口

`tools/run_qa_launch_benchmark.py --formal-stage` 在原 receipt/clone/scene driver 上增加代码选择，不新增启动配置，不启动正式 ID 的 GUI。默认 QA 路径和 `qa_launch_conditions.load` 的全部 Shared、账号、原脚本、配置与来源校验保持不变。

输入：`--stage` 仍是原 QA engine；`--receipt` 仍是该 QA 原 Swift 最终环境出口。额外 `--formal-stage` 必须是固定开发项目 `P/evidence/` 内 canonical owned 的新 staging `.app`，不是 `/Applications`。在读取运行合同前先执行 `/usr/bin/codesign --verify --deep --strict <app>`，验证整个资源封口；失败不启动任何游戏。随后默认执行 `RuntimeProfile.bundle` 完整核外层正式 ID、production FEX 清单、8 个实际角色与所有工具/原 loader/原脚本闭包。再用既有 native verifier 分别检查 top CPU、nested CPU 到 host 的 ARM64/签名/实际声明加载边，以及 GPU 到对应 DSO；两份 CPU 身份不相互冒充。源 QA receipt 的 16 项 effective script SHA 必须与 staging 完全相同。

此模式强制 empty cache：省略 `--cache-mode` 会选 empty，显式 seeded 会拒绝。`qa_launch_clone.create` 仍只从已准入 QA source 复制正常存档与三个配置，创建 fresh Shared benchmark user/data/GPU 根；不会读取正式 Defaults、存档或旧正式 cache。原 bootstrap 自行从这个空 base 建立 `arm64-native-v1`，driver 不追加 namespace、不导入 QA shader/local-metal cache。原 QA 文件保留，正常 source savedata 前后核验继续执行。

执行选择只改 staged engine 的 cwd、原 shell 脚本绝对位置和 Python wrapper 代码路径，以及显式记录的 `BB_GPU_PROCESS`。原质量/语言/特效/4 项保护沿用 receipt；既有采样器的隔离路径、静音、pad/frame telemetry、采样时 quicksave=0 控制仍单独记录，F3 功能测试沿用原 quicksave=1 前置条件及公开声明的统计控制。F3 监测按两条真实 CPU 文件路径收集 PID，原 `run.sh` 负责选择 nested，driver 不伪造 top→nested 映射。

sample 的 finally 先调用原 owned 进程清理，再重复整包 `codesign --verify --deep --strict`。post seal 失败或超时保留 `raw_run_status`、F3/帧率原结果，另记 `whole_app_seal` 与 `seal_error`，把本轮和汇总状态置 `incomplete`；不能把 F3 成功当作整包验收通过。

`native_identity` 保留 source QA 的准入；`formal_native_identity` 独立记录正式 staging 的实际代码身份。receipt 与 clone 来源仍明确是 QA，绝不参与正式用户进度选择。只读 package identity 或场景 camera 不证明整个正式 GUI/游戏已验收。

后续由主任务调用，例如（这里只展示接口，本任务未运行）：

```sh
python3 experiments/arm64-runtime/tools/run_qa_launch_benchmark.py \
  --receipt /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/gui-root-TAG/optimized-launcher/out/launch-conditions.json \
  --stage '/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/apps/QA.app/Contents/Resources/bbport' \
  --formal-stage '/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/local-stage-TAG/Blood Echoes.app' \
  --cache-mode empty --runs 2
```

本轮验证：新增 `tests/test_qa_benchmark_formal_stage.py` 的 18 项作者控制通过；使用真实 Python bundle byte reader，注入 native identity verifier，并明确禁止 Popen。覆盖整包pre失败不启动、post失败保留raw功能结果并降级、前后通过与超时，以及完整闭包、outer ID、production、文件更改、缺 nested、脚本不等、错误 CPU/GPU edge/签名/架构、路径链接/越界、强制 empty、原环境与 source clone 身份保留、画质更改拒绝及 F3 前置。没有启动 native/GPU/game，也没有修改正在运行的旧 driver 或正式数据；真正 staged-role 运行由主任务统一安排。

## 原 Rosetta 0.2.51 匹配参考

同一 development driver 新增 `--reference-stage <P/evidence/.../Blood Echoes.app>`，与 `--formal-stage` 互斥；不支持 `--functional-f3`，该参考只用于性能采样。其余仍是同一原 QA receipt、`qa_launch_clone.create`、scene sampler 与连续 MemoryPolicy。默认强制 empty，拒绝 seeded；不读正式 Defaults 或正式进度，不运行参考 GUI。

参考入口核真实外层正式 ID 与 `CFBundleShortVersionString=0.2.51`、whole app pre seal、已启用原 ARM64 Metal 后端。参考 16 脚本实际 SHA 对照 source receipt 的 **original_scripts**；FEX正式候选仍对照 **effective_scripts**，不混淆两种来源。脚本本身负责原生后端选择；源条件若显式关闭 ARM64 Metal 会拒绝，driver不修改该设置。

实际 top/nested CPU 与其 host dylib 必须为 thin x86_64；独立 GPU 与 DSO 为 arm64。`reference_image` 读取 Mach-O header、lipo、codesign，复用原 package `loads` 读取依赖；CPU必须只有一个精确 host edge，使用既有 `gpu_candidate_identity.resolve_gpu_edge` 解析。所有非 system 依赖必须为包内相对边，递归闭包最多256映像，并逐个核对应架构、类型、摘要和签名。未实现任意 RPATH/外部驱动/其他版本兼容，遇到这些明确拒绝，不猜目录。

所有执行选择共用 `execution_code_admission` 和原 sampler 的 pre/post 整包封口；参考身份单独写入 `reference_native_identity`，明确 CPU `x86_64`、GPU `arm64`，不能称纯ARM。只替换代码 cwd/shell/Python wrapper 路径和 `BB_GPU_PROCESS`；原 INI、语言、模组和质量条件仍来自源 QA 的独立副本。原参考脚本没有 FEX 准备 hook，所以不会调用新版准备链；driver 不把 QA 的默认准备行为强加给参考。两边从各自新空缓存执行相同 cold/warm 采样后，才由主任务对照场景、帧时间与内存，不把单次均值直接解释成稳定收益。

新增 `tests/test_qa_benchmark_reference_stage.py` 的11项作者控制通过；保留正式入口18项与scene/summary12项控制。测试只用作者文件和注入的 native metadata/seal verifier，禁止进程启动。此修改尚未执行任何参考游戏/原生/helper过程，真正基线复制与匹配运行由主任务安排。
