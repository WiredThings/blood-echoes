## 实机父进程格式补证（2026-10-27）

root 使用临时自造 shell（没有游戏/GPU）实际调用当前 `supervisor()`，发现 macOS `lsof -Fpn` 仍强制输出 `fcwd` 字段；原两行 mock 导致安全回退，优化不会生效。已严格改为 `p<精确PID> / fcwd / n<唯一canonical目录>` 三字段并保留 4096 字节/2 秒界限。缺 fd、错 fd、错 PID、多 cwd 及超限反例继续拒绝；18 项测试通过，修正后真实 ps+lsof 作者正控通过。该结果只证明观测格式与选定阶段关系，不替代新包的 F3 命中/耗时实测。

# 同一原监督器内 F3 的启动准备调度复用

2026-10-27，源码候选。针对 QA09 实测 F3 第二轮 source-view 仍约 120 秒的问题；本轮未改冻结 QA09，未读/改 Shared 游戏源或正式数据，未启动 native、GPU、游戏或编译器。没有实际快路径耗时结论。

## 只跳提前工作，票据不授权 artifact

原启动链/16 脚本和退出码保持不变。只有完整提前准备完成、Local 最终为 prepared、且最终保护检查通过后，才向所选 GPU 的 `automatic-game-inputs` 原子发布一个最大 16 KiB 的调度票据。PSO runner 必须返回 completed 且 `full_manifest_preparation_verified=true`，并已正常配置 capture；partial/failed/stopped/无 capture 不生成可复用 context。票据失败不使已经完成的原启动失败，保护异常仍传播。

下一轮必须同时满足：原 `BB_QUICKLOAD_PENDING=1`、`BB_RESTART_SUPERVISED=1`、ARM 兼容选择、显式 `BB_ONLINE=0`、同 session/restart 两个 32 hex nonce，以及本轮重新计算的全部身份相同。冷启动和菜单重开没有 quickload 标志，始终走原完整提前流程。

身份包括原归档策略本轮重新 SHA 得出的完整 boot/content_profile/patches/settings/cohort，实际 CPU SHA/路径、4 份最终组件/源清单 SHA、原 CLI（仅临时 app0 overlay 名替为角色占位）、原 mod recipe/config SHA 与准确 selected GPU 根。保存 nonce 的摘要，不保存账户/凭据环境。

supervisor 不能简单等于 Python 的直接 PPID：有 mod overlay 时 `run.sh` 自己 wait Python，每轮 F3 的该中间父进程不同。新 helper 从 `getppid()` 起最多两级，只承认 bash 的原 run.sh→supervise_restart.sh 或直接 supervise_restart.sh，核同 uid，并绑定原监督器 PID 与实际 `ps lstart` 起始身份。查询使用固定 `/bin/ps`、干净 PATH、2 秒 timeout、有界输出；不记录完整进程命令行。完成与恢复时再确认同一监督器。原 nonce 与原监督器 quickload 状态共同限定这一会话，不把 PID 单独当授权。

根审查后已强化脚本来源：独立以本次 stage 为期待值，`script` 参数只能是原代码里的精确 `run.sh` / `tools/macos/supervise_restart.sh` 相对形式或 stage 内对应绝对路径；同名不同目录、basename-only、其它相对拼法都不能冒充原脚本。每个观察父 PID 用固定 `/usr/sbin/lsof -a -p PID -d cwd -Fpn` 只取 cwd，必须是准确 PID 的唯一 canonical owned stage 目录；最多 4 KiB 输出、2 秒 timeout、干净 PATH。再核该 stage SOURCE-PARITY 的冻结 effective_sources 脚本 SHA，且 SOURCE-PARITY 自身 SHA 必须仍等于绑定值；观察后重读同 PID/start/script 防止期间进程变化。ps/lsof 不可观测则只走完整准备，不猜测放宽。

命中后只略过重复 source-view、全游戏扫描、候选/模块准备和提前完整 PSO 预热。Local 和固定 helper 的原步骤照常执行；原正常运行时 Local/module/PSO 的完整键、函数、descriptor、archive、实际 DSO/driver/source 校验一行不改。返回显式 `artifact_authority=false`、`runtime_cache_import_allowed=false`、`actual_final_read_admission=false`、`full_final_pso_ready=false`；调度票据不是 native-ready 证据。

## Capture 重新确认，不继承旧 env

恢复先由原 controller 重新 `component.load`、验证实际选中 DSO、原工具链和压力策略。随后核 owned canonical context 只在 selectedGPU/automatic-game-inputs 的既有目录内：

- 源快照目录仍 readonly，源快照 READY 的完整 SHA 与已完成 context 相同。
- 原 `read_ready` 重新核静态 metadata READY、**全部 metadata 文件集合和全部文件 SHA**，不是仅检查 receipt 存在或 mtime。
- 调用原 `configure_capture`，重新核 catalog/index/backend/SFO、原静态代码身份表并发布原 receipt；返回的 capture root/game root 必须恰为所选路径。旧 `BB_FETCH_CAPTURE_*` 仍在原启动入口清空，绝不直接继承。

不重新读取整个游戏目录来证明它此刻与旧静态 registry 完全一致，因此结果明确带 `static_registry_rescanned=false` 和动态捕获缺口。正常 runtime 仍使用原真实 app0；producer 的 `Allows(hash,stage,codeSHA,bytes)` 只接受 registry 中精确匹配代码，新增/改变的代码正常走原 runtime 编译，但可能不被这份旧静态 registry 捕获，不能默称全游戏静态覆盖已更新。下一次普通完整启动会更新静态来源。

源快照/metadata/身份/票据缺失、损坏、变更或 capture 配置不成立，一律保留旧内容并回到原完整准备。运行时安全异常不降格成 cache miss。本轮没有用 mtime 代替任何 SHA，也没有新增常驻线程或内存缓存。

## 作者控制

新增 `test_quick_restart_preparation.py` 18 项纯 CPU 控制，所有 process 查询 mock：完整同会话 F3、冷/菜单/在线/未监督/坏 nonce、换监督器 start、mod 中间父层、boot/settings/cohort/CPU/component/recipe 变化、坏 catalogue/index/READY/额外 metadata、坏 source snapshot READY、坏调度票据、capture 拒绝/错误 env/旧 env 不继承、保护异常、路径摘要篡改、原 controller 路由不扫描、只在完整准备与最终保护之后发布；并覆盖同名脚本不同目录/错 script 参数、cwd 更换或不可观测、脚本/SOURCE-PARITY 变化，以及 lsof PID/输出界限。

同时 9 项计时、7 项原自动游戏准备、6 项 Local 代表合同共 40 项通过；另 15 项原打包闭包控制通过。`prepare_local_aot_stage.TOOLS` 显式新增 `quick_restart_preparation.py`，当前 import 闭包校验通过。没有执行 subprocess/signal/实际 exec 的旧 Local 测试，也没有运行实际 ps/lsof。

本轮仅：`tools/quick_restart_preparation.py`、`tools/launcher_local_aot.py`、`tools/launcher_game_pso.py`、`tools/prepare_local_aot_stage.py`、新增 tests 和本开发记录。下一次统一打包后仍需实际原监督器 F3 验证命中及耗时；不能把作者路径通过当作已部署或已提速。
