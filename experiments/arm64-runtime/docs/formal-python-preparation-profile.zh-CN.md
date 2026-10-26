# 原启动器 Python 准备链的正式包作用域

2026-10-27。源码/作者合同收口；未替换正式应用、未启动真实游戏/GPU/编译器。下一步由根任务用实际新 native 组件统一组包，再做隔离 CLI 接线验证。本记录不是 Release 说明。

## 正式身份与原路径

新增 `launcher_runtime_profile.py`，只从实际外层 `<app>.app/Contents/Resources/bbport` 读取 Info 的 `io.github.bbport.optimized`、NATIVE 的 FEX/production=true、已冻结运行角色字节、LOCAL 的 production=true 和工具/原 parser/bootstrap 闭包、SOURCE-PARITY 精确原 16 脚本。已知角色最多为原 6 项加 nested CPU/net 两项；未知角色、坏 SHA、code 链接/未知 owner/group-world writable 均拒绝。支持原顶层及 nested CPU，不靠 env 布尔提升 QA。

QA/未打包 profile 为 None，继续原 Shared/P/state 门；正式损坏直接失败，不能回到 QA/P 代码。正式 profile 的代码读取可显式允许 root/current UID；普通 GPU/input/game/config 数据仍仅 current UID。`owned_audit`、`gpu_identity` 的 code-owner 参数只由已验 scope 的代码路径调用，默认数据门未放宽；layout 的相对 source 名禁止逃逸。

原 CLI 和偏好不变：config 仍原 BB_DATA_DIR/bbport.ini；boot/content/patch 仍原 out；app0 仍原 game 或原 out/mod-game-*；user 参数不改、不读存档内容、不写 QA marker。正式包的 old runtime_project/source_repository 只属开发来源，不作运行依赖。作者 bootstrap 正控在不存在 P 的条件下，只从冻结 bundle/native-runtime-tools 导入并工作。

| 用途 | 正式路径 |
| --- | --- |
| 原 GPU base | 原 BB_GPU_USER_DIR，以 BB_ARM_GPU_BASE_DIR 显式保留；不扫描或清除旧缓存 |
| 本轮 effective GPU | `<base>/arm64-native-v1`；所有实际 shader/Local/PSO/capture 输入限定此新增 namespace |
| 准备作业/日志/临时 packet | 原 BB_DATA_DIR/arm64-runtime-work，直接使用，不伪造 P/state |
| scalar capability | work/capabilities 下每次全新 owned 0700 child；HOME=TMPDIR=BB_GPU_USER_DIR=child，仅固定 PATH 和 BB_DATA_DIR |
| CPU 模块 metadata | 仍单独是原 data/out/runtime-sessions/session_nonce/.arm-module-info.bin，与 Python work 分开 |

profile 作为可选对象沿原 Local→固定 helper→source-view→catalog→module→完整 PSO 路径传递；已有 project 变量仅承载真实 work 路径，不从 work/tools 导入。代码始终来自已冻结包。AOT OFF 也先完成正式包身份、实际 CPU/GPU 角色及 namespace 选择，然后才跳过准备。

## 子进程与现有校验

普通 fixed/PSO/module worker 继续使用 effective GPU namespace，清环境后显式恢复 BB_ARM_GPU_BASE_DIR 与 BB_DATA_DIR，原 packet/artifact/cache 根关系不改。只有纯 scalar 能力查询使用新 capability child，绝不继承用户 HOME 或拿旧 GPU 根查询。offline Metal Toolchain 的 FixedRunner 仍是原受限系统工具环境，不加载游戏 GPU DSO。

没有重写 mods/中文层、catalogue/parser、模块翻译器、compiler、PSO 算法或 archive/driver 完整键校验。F3 调度 hint 仍不是 artifact authority；正式包代码 owner 参数与严格原监督器来源验证接到同一作用域，原 source registry 未重扫时的动态捕获缺口继续可见。保留 APFS clonefile 唯一路径；跨卷或不支持 CoW 时仍按原逻辑 source-fallback，没有 copy/hardlink fallback。本机同卷测试不能证明任意用户磁盘均支持。

## 定向证据与边界

19 项新的正式 profile 作者控制通过，涵盖 8-role 布局、顶层/nested、production/Info/闭包和未知 role 拒绝、AOT OFF、精确 config/out/app0/user/work 关系、旧 cache 与 link 拒绝、root/current 只读 code 与数据 owner 分离、普通 fixed/PSO/module 的实际构建 env、三 scalar capability 的新私有 env、原 Local 调用路由，以及 bundled metadata parser 和独立 session 输出。另有真实 Python 作者 bootstrap 子进程正控，只运行记录结果的 fake CLI，不 exec 假 CPU，不启动游戏或 GUI。

联合 QA/controller 回归执行 231 项、Local policy/builder 作者回归 32 项、固定 helper controller 14 项，总计 277 项通过；其中包含新 19 项，并非额外再加 19。工具 import 闭包检查通过。旧 Local fixture 需要 state 时，仅将作者 P/PROJECT 显式注入全新临时根，不创建/使用实际 Shared state。

最初新 fixed-env 作者控制遗漏 mock require_no_games，尝试系统 ps 被 sandbox 在创建进程时拒绝；补齐作者 seam 后通过。未放宽运行时 require_no_games。没有运行 native/GPU/game/编译测试，也没有读写正式存档、设置或旧缓存。

当前这些通过的是 source/调用/环境合同，不是实际本机正式包成功启动的证明。真实 code load edge、native main 前 UserPaths、完整 preparation/capture 与 F3 仍需根任务的新隔离包验收；不据此宣称已安装或性能提升。
