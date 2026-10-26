# QA08 → 本机正式应用：路径与身份准入审计

2026-10-27；仅开发审计，不是发布说明，也不表示已经替换正式应用。

本次只读源码、应用 Info.plist、组件清单和动态链接元数据；未读取正式存档内容，未更改正式设置或缓存，未运行游戏、GPU 或编译。审核对象是 `Blood Echoes ARM64 GUI QA 1793033361727444000.app`。以下行号为本次审计时源码位置。

## 结论

不能把 QA08 目录直接覆盖到 `/Applications/Blood Echoes.app` 后宣称兼容。二进制直接依赖已相对打包，但实际启动仍依赖 Shared 开发根、QA session marker、未打包的模块元数据脚本及 Python 开发入口。正式启动还可能优先选中旧的嵌套 Game.app。最低可行改动是保留原启动链和用户路径，增加有身份约束的正式启动作用域；不能单纯删除 `Shared` 检查或设置 `production=true`。

## 既有正式语义必须保留

| 项目 | 已核实语义 | 正式整包要求 |
| --- | --- | --- |
| 启动器 ID | 正式 `io.github.bbport.optimized`；QA08 为 `io.github.bbport.optimized.options-qa.arm64.gui.1793033361727444000` | 正式 ID 原样保留，不能导入 QA Defaults |
| 正式默认数据 | `~/Library/Application Support/bbport/optimized-launcher` | 保留，不改为 Shared |
| 正式默认 user | `~/Library/Application Support/bbport/user`；用户已选择目录则遵循 `prefs.userFolder` | 保留现有选择；本次未读取实际偏好/存档，不能推断当前具体 user 路径 |
| 登录凭据 | Keychain service 为 `bundleIdentifier + .huntersdream` | 保留正式 ID/正式凭据命名空间，不借 QA 凭据 |
| QA 选择器 | QA08 有 `BBPreviewDataPath`、`BBPreviewPreferencesPath`、`BBQALaunchReceipt` | 正式 Info 删除这些项；不能带上 QA seed/标题目录适配 |
| 重开 | 原 supervisor 解释 75 为菜单重开，76 为离线快速恢复 | 原语义原样保留，退出/回收完成后返回原状态 |

权威源码为 `launcher-macos/Sources/BBLauncher`，不是旧 `D/launcher-gui-source`。`LauncherModel.swift:151–178,592–627` 定义数据/user/原环境；`Settings.swift:543–558` 仅对 QA ID 接受 preview 根；`Online.swift:19–20` 定义 Keychain service。`LauncherRuntime.swift` 的 NATIVE 解析只是 UI 能力选择，不是完整身份认证。`OptimizedProfile.swift:82–85` 可由 FEX 能力推导 ARM64 Metal，但不会设置 `BB_ARM_LAUNCHER_COMPAT`；目前 QA 的兼容开关和 session/restart nonce 来自测试 extraEnv，正式链必须自行建立会话合同，不能拷贝 QA 的固定值。

## 本次确认的实际阻塞

表中的 `D` 指 `experiments/arm64-runtime`，`P` 指 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。

| 入口 | 当前门/依赖 | 最短修正边界 |
| --- | --- | --- |
| `D/src/game_runtime.cpp:391–412` | 兼容开关、原 supervisor、Shared config/game/patch/app0；实际 `--user` 被交给 `BbSession::Prepare(...,P/state,ARMQA1...)` | 分离正式 user 与运行时工作/session 根；只放行本次原启动器准确选择的读写角色，QA 门不变 |
| `D/src/session_state.h` | state 必须为 declared root 的直接子目录；resume 必须有精确 QA marker | 正式不往现有 user 塞 QA marker；会话票据放独立工作根 |
| `D/src/native_restart.c:19–34` | 即使 launcher-compatible，仍先要求 user 为 `P/state` 直接子目录 | 改为同一角色作用域验证；保持 nonce、原 supervisor 与先回收 backend 再退出 |
| `D/src/prepare_guest_module_info.h:16–20` | 无条件从 `P/tools/prepare_module_metadata.py` 生成 user 下 `.arm-module-info.bin` | 随包带原 parser/helper 闭包，由实际 CodeRoot 定位；输出移至运行时工作根 |
| `D/tools/prepare_module_metadata.py:47` | boot/game/output/loader 都限定 Shared；默认 loader 在 P/vendor | 保留原有界 PT_LOAD/TLS/EH 与 SHA 算法；只参数化经认证 CodeRoot/输入/输出来源 |
| `D/src/game_runtime.cpp:485,618` | GPU 子程序、联网 module 仍要求 Shared | 校验实际组件清单对应角色/加载边，不扩大到任意同目录文件 |
| `D/src/bb_private_net_module_path.h` | 已允许顶层 bundled `bin/bb-probe-metal` 的 gpu-net；不匹配嵌套 Game.app | 与实际选中 exec/组件关系一致，不依赖新的任意环境路径 |
| `D/vendor/metal_api/shadps4/common/path_util.cpp:89–102` | DSO 全局 `UserPaths` 在 main 前只准 `P/state`；其它路径直接 78 | 必须在全局初始化前获得正式作用域；main 内晚设 user_dir 不够；不能用无认证环境 flag 绕过 |
| `D/src/game_pso_preparer.mm:133–136,182–183` | GPU/artifact/scratch/packet 限 P/state | 保留 GPU 内准确 artifact 关系、nonce、完整输入/管线/归档门；准入经认证的正式 helper 工作子树 |
| `D/src/game_module_preparer.mm:239–241,291–308` | 自动包仅在 selectedGPU/automatic-game-inputs 且 P/state；证据模式 P/evidence | 正式仅自动作用域，不打开开发 evidence 模式；保持 cohort/nonce、所有来源/逐字节门 |
| `D/tools/prepare_local_aot_stage.py:6,66–75` | bootstrap 写死 P，清单要求 runtime_project=P、host_local_only | 正式 mode 使用 bundle 相对工具和当前准确选择的运行目录；不再依赖开发仓库 |
| `D/tools/launcher_local_aot.py:35–46,78–95` | 原启动输入在 project.parent，GPU 在 project/state | 用同一作用域提供各角色根；仍解析原最终参数、原 patch/mod 结果并 exec 同一程序 |
| `D/tools/game_pso_component.py:194,244` | freeze/load 只接 `production is False` | 独立明确正式 profile；不能仅改 bool；仍核最终 SHA、actual load edge、layout/source/capture 能力 |

其余同源路径门应一起走作用域 accessor，避免逐个临时开洞：`launcher_game_pso.py:11,13,41`、`local_aot_policy.py:26`、`local_metal_builder.py:80–94`、`launcher_fixed_helpers.py:76`、`launcher_game_catalog.py:189–191`、`launcher_source_view.py:129`、`launcher_game_modules.py:145,175`、`complete_pipeline_inputs.py:62`、`prepare_game_psos.py:206–207`；capability 探测在 `game_pso_batch.py` / `game_pso_component.py` 也需使用正式声明的私有 scratch 子树。`game_companion_modules.py` 的 capture-under-selectedGPU 相对关系、producer/store 的 ownership/mode/no-link 门本身可以保留。

不应扩大 authored runtime、CPU cache generator、fixture peer、固定 seed、QA pad 等开发入口的 Shared 门。`launcher_local_aot` 的显式关闭准备分支也不能绕过 CPU/GPU 正式组件身份门。

## 实际选中执行文件与清单

`run.sh:251–255` 在 packaged Metal 模式优先 `bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal`。现正式应用仍有这个旧嵌套程序，QA08 没有它；因此只更新顶层 `bin/bb-probe-metal` 可能继续运行旧 CPU。必须明确更新/取消该 wrapper 并核验实际选择结果。顶层与嵌套身份关系、联网 module 查找和图标必须一起验证。正式游戏 ID 原为 `io.github.bbport.optimized.game`；正式 GPU app ID 为 `io.github.bbport.optimized.metal-game`，不能带上 QA GPU ID `...metal-game.qa.normal.1793032681464965000`。

QA08 的 `NATIVE-METAL` 是 schema1/FEX/auto，但仍 `production=false`；`ARM64-LOCAL-PREPARATION` 仍 `host_local_only=true`、`portable_installer=false`、`runtime_project=P`、`source_repository=<开发仓库>`。`GAME-PSO-COMPONENT` 相对 bundle source/layout/worker/library 的设计可复用，最终复制、重定位和签名完成后重新绑定全部 SHA/真实加载边；不能把 UI 识别为 FEX 当成准入。

已只读检查 QA08 的 bin/deps-metal-arm64/python 未见外部符号链接；顶层 CPU 和 GPU DSO 的直接加载边为 bundle 相对路径与系统库。这仅证明这两项直接链接已搬入包，不证明 P helper/bootstrap/编译路径依赖消失。

## 最短正式准入合同

1. **原启动链不重做。** 原 Swift → `run_optimized.sh` → `supervise_restart.sh` → `run.sh` → 原最终 exec；保留原游戏目录、汉化/mod overlay、正式正常/快速存档选择。FEX 组件选择由已核组件推导，不往用户 extraEnv/Defaults 写 QA 值。
2. **分角色根。** 票据绑定实际 bundle/ID、最终 CPU/GPU/helper/module/network 的 SHA 和加载边、ABI/source/driver 身份、准确配置/data/out/user/GPU/game/mod 读写根及最终输入摘要。私有 QA profile 保持；正式 profile 只允许原启动器已经选定的角色，不准整个 HOME，不依赖单个可伪造布尔。
3. **分离工作文件与进度。** 保留实际 `--user`，工作/session/module metadata/导入诊断放原 data/out 下独立 owned 子树；会话 nonce 临时生成且跨 F3 由原 supervisor 管，不把 QA marker、seed、标题重命名带入正式 user。
4. **初始化前验证。** GPU 全局 UserPaths 与 native helper 在写文件/读取缓存前消费上述合同。Python bootstrap、CPU 和 GPU 共用一致角色语义；所有原完整键、input SHA、来源/owner/no-link、Local/PSO/archive、actual DSO 与 nonce 检查仍然成立。
5. **保留现有缓存。** 原 `pipeline_cache_core_serialization.cpp:331+ WarmUp()` 在不兼容/损坏时有 `DataBase::Clear()`。新组件不能直接拿正式旧 namespace 试读后清空；应采用同一原 GPU 根下新增兼容身份 namespace，或经严格证明的非破坏迁移。不得导入 QA cache，不可把 ready_hint/receipt 替代实际归档验证。

## 收口所需控制（尚未执行）

- 正式默认及自选 user 均原样传入；QA 根/正式根互换、路径逃逸/链接、伪造 production、角色错配必须拒绝。
- 实际 exec（包含嵌套选择）与 CPU/GPU/helper/network SHA/load edge 错配拒绝；缺 bundled metadata helper 拒绝，不回退 P。
- GPU main 前接受合法正式票据，拒绝裸 BB_GPU_USER_DIR/env 开关、过期 nonce、QA→正式票据复用。
- F3 仍原 76/离线恢复，菜单重开仍原 75；不新增会话工作文件到正式 savedata/快速存档树。
- 原 overlay/社区汉化最终文件视图与静态扫描源一致；不回退扫描未经 mod 的原游戏目录。
- 新 namespace 写入不改/清旧正式缓存；关闭自动准备不关闭组件和路径校验。

这些是待实现/验收事项，不是已通过结果。本轮没有修改正式应用，没有发布 Release，也没有更新发布文档。
