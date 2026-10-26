# 私有 QA 的原 Swift 启动条件出口

`launcher-macos/Sources/BBLauncher/QALaunchConditions.swift` 由原 `LauncherModel.start` 在 `.game` 的 `Process.run()` 前调用，直接接收该次真实的 `arguments`、`repo`、`env`。没有复制 `LauncherModel.environment()`、`OptimizedProfile` 或联网/语言/资源决策，不修改原字典、argv、工作目录、Process 环境和退出语义。receipt 是原 Swift 已决定的启动条件，不是 preferences 的重建，也不证明子进程已经成功启动或场景已进入（字段 `launch_boundary=before-Process.run`、`game_started_verified=false`）。

## 唯一启用条件

正式 bundle 与没有 opt-in 的 QA bundle直接返回，不查写数据目录、不产生日志。必须同时满足：

- Bundle ID 精确为 `io.github.bbport.optimized.options-qa.arm64.gui.<非空数字>`，Info.plist 显式设置字符串 `BBQALaunchReceipt`。
- `BB_ONLINE=0`；`BBPreviewDataPath` 精确为 `P/state/gui-root-<相同数字>`，原环境 `BB_DATA_DIR` 精确为其 `optimized-launcher`，receipt 是该数据目录已存在 `out` 内的非隐藏 `.json`。`P=/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。
- root/data/out 与代码输入均 canonical、当前用户拥有，拒链接；repository 必须是本 bundle 自己的 `Contents/Resources/bbport` 且位于 `P/apps`。原 arguments 必须仍是 `["tools/macos/run_optimized.sh"]`。
- 只选择原环境的 `BB_`、`MTL_` 大写键，不写 PATH/HOME/PYTHON 等继承环境。候选键若包含 `TOKEN/SECRET/PASSWORD/CREDENTIAL/AUTH/BEARER/PRIVATE_KEY/PAIRING_CODE`，拒绝整份 receipt，诊断不包含键值。不得给联网启动写凭据快照。

## 内容与写入

JSON schema1 记录原 Swift producer、launch UUID、时间、launcher PID、bundle、原 shell/argv/repo、选中环境，以及 launcher、两个有效 shell脚本、SOURCE-PARITY、QA manifest 和原 INI/mod/patch 配置的 SHA256。配置只能在本次 QA data 目录内。摘要读取用 `O_NOFOLLOW` 与前后 inode/长度/时间核对，流式 64KiB；单文件上限64MiB。

JSON 最大256KiB。临时文件通过该 out 的目录 fd、`O_EXCL|O_NOFOLLOW`、0600 创建，完整写入/fsync 后原子rename。已有目标必须是同路径owned普通文件且只有一个硬链接；目标/父目录变化、链接或目录均拒绝，不跟随或删除。失败只追加 QA 诊断，继续原启动；不会把错误改成新的游戏退出码，也不会擦除先前有效记录。使用者必须核对新的 launch_id/时间/源PID与本次 launch，不能把拒绝后留下的旧receipt当新证据。

## 对照程序如何消费

组包器只需为新 QA 的 Info.plist 设置 `BBQALaunchReceipt=<该包私有data>/out/launch-conditions.json`，UI不新增开关。对照程序应核这份receipt的bundle/脚本/config/manifest身份和本次启动时间，保持全部现有画质、Metal、语言与四项保护开关；只改新的隔离 user/data/cache/config/nonce 与显式 benchmark遥测。不得用旧 `launcher_environment` 或preferences再覆盖原Swift环境。renderer实际加载身份仍用已有严格组件校验；此出口不替代它，也不把单ARM测量变成ABBA。

共享出口已接到当前 QA 组包器的显式 Info.plist opt-in；尚未修改 `run_launcher_engine_abba.py`，不能把接口存在当成已完成匹配对照。`QALaunchConditionsTests.swift` 新增14项作者控制，覆盖正式bundle/未启用/错误后缀、原环境和身份、online及8类凭据键、数据/receipt越界、链接与硬链接、原子替换权限、错误repo/argv、配置逃逸、摘要链接、超限且保留旧记录。helper typecheck 通过后，已从干净 Shared 副本用 Xcode／原生 ARM64 Release 执行完整 114 项 Launcher XCTest：1项真实 iCloud 集成按原规则跳过，0失败；新增14项全部执行通过。首次显式 Xcode Debug 测试链接失败、首次 Release 副本漏掉原 quicksave.py／ultrawide_ui.py／Bloodborne.xml 三个夹具均保留日志；补齐原精确源码后整套重跑通过，没有改测试条件。证据位于 P/evidence/launcher-final-environment-20261027-01/。未启动游戏或 GPU。
