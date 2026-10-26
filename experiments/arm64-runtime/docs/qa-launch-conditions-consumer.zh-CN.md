# 原 Swift QA receipt 的严格 CPU 读取器

`tools/qa_launch_conditions.py` 只消费原 Swift 启动出口，不读取 preferences 后重建前端决策，不启动原生程序、不调用 GPU/Mach-O loader、不复制存档/缓存、不写任何源 QA 文件。入口：

```python
from qa_launch_conditions import load
conditions = load(receipt_path, stage=packaged_bbport_root, project=shared_project)
env = dict(conditions['environment'])  # 后续克隆显式派生一份；原快照只读
ini = conditions['configs']['BB_CONFIG']
# ini.path / ini.data（不可变bytes） / ini.sha256
```

这是原 `LauncherModel.start` 在 `Process.run` 前决定的条件。读取成功不证明游戏启动、进入场景、通过功能回归或性能提高；返回值保留 `game_started_verified=False`。后续运行器仍须进行现有二进制/真实加载边准入及各自的锁屏、内存、计时保护。

## 严格准入

- project 必须是固定 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。stage 必须是该根 `apps/Blood Echoes ARM64 GUI QA <数字>.app/Contents/Resources/bbport` 的owned canonical目录。receipt必须是同数字 `state/gui-root-<数字>/optimized-launcher/out` 直接子级的非隐藏JSON。
- 读本 bundle 的Info，核对私有数字Bundle ID、原 `bbport` executable、`BBPreviewDataPath` 和显式 `BBQALaunchReceipt`。receipt不超过256KiB；所有读取文件拒绝symlink、额外hardlink、非当前用户所有、读取期间变化，JSON拒绝重复字段。
- receipt schema/producer/before-Process.run边界、offline/unchanged/formal数据标记、原 `/bin/bash` 和单一 `tools/macos/run_optimized.sh` argv必须匹配。原 source_pid必须与本数字 `evidence/launcher-gui-run-<数字>/guard-ready.json` 的PID及launcher路径一致；不通过同名进程猜来源，不要求历史launcher仍存活。
- 完整保留原 `BB_`/`MTL_` 字典，拒绝非法键、非字符串值、NUL和8类凭据关键字；不混入继承环境。当前验收cohort明确要求四项 `PAYLOAD_GATE/RAW_PREWRITE/READ_TRAPS/LIFETIME=1`，不以缺项默认值补齐。原offline/native Metal选择和两个nonce格式必须有效。
- 原user严格匹配case和本数字 `state/gui-user-<数字>`；cache严格匹配manifest指定的 `state/gui-gpu-local-aot-<数字>`。game及可选社区翻译目录必须是Shared兄弟 `assets` 下真实owned canonical子目录。data/config/mods/patches均绑定同QA数据根。未使用的原mods或patches目录允许**精确预期路径的叶子不存在**，返回 `(path, False)`，不创建、不称已准入存在的目录；存在时仍完整核目录与链接。
- 核receipt记录的launcher、两个effective脚本、SOURCE-PARITY、QA manifest和三个配置摘要。16个original/effective脚本集合完整一致，original须匹配现存原baseline，effective须匹配当前stage；复用 `prepare_local_aot_stage.HOOK/runtime_log_revision` 验证只有准许的两项精确变化，第三脚本变化或同脚本额外行为均拒绝。
- 三配置返回frozen `ConfigSnapshot(path, data:bytes, sha256)`，其余环境/身份映射使用 `MappingProxyType`。返回前重新核验42个实际输入的SHA，避免读取期间换内容。运行时7项文件的路径/声明摘要会返回给已有严格native verifier；本CPU读取器不把普通文件存在冒称Mach-O、签名或真实DSO加载已验证。

## 返回合同

- `environment`：原完整只读字典，不插入、删减或重写任何选项。
- `configs`：键为 `BB_CONFIG`、`BB_MODS_CONFIG`、`BB_PATCHES_CONFIG` 的不可变字节快照。
- `identity`、`input_sha256`、`original_scripts`、`effective_scripts`：已核摘要和provenance。
- `runtime`、`runtime_sha256`：包内精确路径及其manifest声明摘要，供后续既有native准入。
- `receipt/stage/app/project/data_root/user_root/cache_root/game_root`、`optional_directories`、`launch_id/source_pid/created_unix_ns`、原 `arguments/executable`。

后续bench只在新的隔离副本里派生路径/nonce/遥测；本接口没有克隆或执行功能。不能再用旧 `launcher_environment()`、语言fixture或preferences覆盖这份原环境。

## 本轮验证

`tests/test_qa_launch_conditions.py`：34项作者控制，0.464秒，全部通过。只在系统临时目录构造普通作者文件；测试单独patch固定project常量指向该fixture，不增加生产绕过参数；`subprocess.Popen/run/check_output` 全部被设置为一调用即失败。覆盖正路径/不可变结果/无写入、固定根、receipt链接/hardlink/ownership/超限/重复字段、producer/边界/argv/Bundle/Info/guard/PID/时间、非法及凭据键、四项保护、资产/配置/user/cache逃逸、配置和身份变化、两受控脚本的额外修改、返回前再次变更等。

同时对真实QA08做了纯Python只读准入：

- receipt：`P/state/gui-root-1793033361727444000/optimized-launcher/out/launch-conditions.json`。
- stage：`P/apps/Blood Echoes ARM64 GUI QA 1793033361727444000.app/Contents/Resources/bbport`。
- launch_id `5E9746DB-429A-4975-8EA1-5E3678539F8D`，source_pid81826；92个原环境键，四保护全部1。
- INI/mods/patches快照分别544/46/48bytes，42项输入摘要完整重核，未写回源数据。

完整原Swift Release测试由主任务已完成：114项、1项真实iCloud跳过、0fail，其中新增14项出口控制全过。此结果与本轮CPU读取器测试是两类证据；均不冒充游戏场景或匹配性能验收。本轮不改runner、package、Swift、正式应用/存档/设置/缓存或Release。
