# 原启动器 ARM 运行时的包内准入与 CPU 接线

当前为开发源码候选，尚未替换正式应用，未读写正式存档、设置或缓存。不是新前端或授权票据系统；同用户可修改应用代码本来属于信任边界，清单也不宣称抵御同用户恶意重签。三个冻结的信号/上下文接口不属于本改动。

## 代码身份

`src/bundled_runtime_admission.{h,mm}` 的 `Actual()` 从 `_NSGetExecutablePath` 取真实目标；`TryAdmit(path)` 是作者夹具入口。按五个已知相对角色定位外层 CodeRoot：top/nested CPU、nested GPU、PSO worker、module worker。使用系统 plist/JSON parser，核外层实际 Info ID `io.github.bbport.optimized`、`NATIVE-METAL.json` 的 production/FEX身份与全部 frozen runtime SHA、本机准备工具/原loader/bootstrap/SOURCE-PARITY闭包。非正式 ID 返回空 optional，由原调用者保留 Shared 门；已找到正式包但角色、清单、SHA坏会拒绝，不能退 QA。

包内文件允许 root 或当前用户所有，普通文件无符号/硬链接、不可被组/其他用户写；流式 SHA 前后检查完整 stat。实际执行路径先 canonical，角色取真实目标而非链接名字；包内声明路径本身仍必须无链接。解析薄 ARM64 Mach-O 的已声明 load commands，精确检查 CPU→host、GPU/worker→GPU DSO；`@rpath` 只允许唯一包内解析。返回 `static_load_edges_verified=true`，不冒称 dyld 动态观测或 codesign 验收。没有数据目录 IO，也没有环境变量身份后门。

## CPU 与原目录关系

`game_runtime.cpp` 仅在实际包准入后接受原启动器 argv（仍由原 `BbLauncherCli::Parse` 处理）；保留原监督器、语言、汉化、mod合并、联机和存档逻辑。`launcher_bundled_paths.h` 把 config/boot/content/patch 精确绑定到选中的 data 与 data/out；user 保持原 `--user`，app0保持原游戏或原 `out/mod-game-*` 合并视图。这里只核角色根，不递归拒绝原mod视图内的资源链接。

正式分支不调用 `BbSession::Prepare(user,...)`，不向存档目录写或要求 ARMQA1 marker。模块元数据与诊断沿已分离的 `data/out/runtime-sessions/<session nonce>`；复用原 fd/openat/O_NOFOLLOW/0700 写入与完整身份核验。work marker 使用 ARMLAUNCH1，包含 boot/base game/content/session nonce。QA 分支仍核原 P/state marker 和 Shared 输入。

原 UI 的 session nonce 和 restart nonce 是两份独立值。`BundledLauncherSession` 分开保存：前者只用于 work 身份，后者用于 F3/重开准入。`native_restart.c` 通过实际 CPU callback 精确复核 user、restart nonce 和 work marker；正式失败立即拒绝，不退旧 P 门；仍使用原 `runtime_supervised_restart_status()` 的 75/76，先回收 owned backend。不改原监督器的离线恢复语义。模块 selector 从实际包准入结果使用包内 `prepare_module_metadata.py`；Python入口由准备链同批接线，不能仅凭 native selector 认为端到端已过。

网络 C gate 用三态：-1沿原 QA 规则，0正式拒绝且不可fallback，1只允许实际已准入CPU角色对应包内 net DSO。离线仍不加载网络库。原GPU/联网组件路径也在 CPU 的启动/缓存身份输入处匹配包内角色。

## 原设置与 ARM 实现默认

原 `tools/macos/run_optimized.sh:23` 已为 Metal 默认 `BB_GPU_LIFETIME=${BB_GPU_LIFETIME:-1}`。`ConfigureExecutionDefaults` 只在图形模式、原 lifetime 与 ARM resource hooks 开启时，对 unset 的 `BB_ARM_PAYLOAD_GATE`、`BB_LAYER_READ_TRAPS`、`BB_ARM_RAW_PREWRITE` 默认 1。它们分别使 allocator/live payload lease、GPU写后CPU可见性和alias raw写前保护使用本轮已测路径；不选择画质或调整缓存。显式 0、非图形、原 lifetime=0 或 hooks=0 均保留。原前端独立渲染优化不被覆盖。

## 有界验证与剩余

`P/evidence/bundled-admission-author-1793043555537024000/result.json`：29项原生作者夹具通过，合成 Mach-O 只用于读元数据，不执行 fixture game/GPU。

`P/evidence/bundled-cpu-consumer-author-1793044033860933000/result.json`：本轮原生 paths/work/双nonce/缺失进度叶子 20控制、原session、launcher inputs/default覆盖、29项代码准入、6项纯C网络gate控制、8组mock backend restart 分发均通过；当前 `game_runtime.cpp` 用同一已配置头/定义做 `-fsyntax-only` 通过。第一次测试器没创建空fixture根而失败，历史结果保留；建正确根后重跑通过，不隐藏该错误。以上不等于正式动态加载或游戏验收。

仍需统一正常 CPU/GPU/worker 构建及封装清单、隔离正式角色 CLI/native加载、原GUI最终条件与 F3/联网/性能回归。未通过前不安装。读取后到原 loader 使用期间仍存在普通同用户进程可替换文件的 TOCTOU 边界；现门检查稳定读取和精确路径，不是系统沙箱，也没有新增可覆盖整个 HOME 的授权。
