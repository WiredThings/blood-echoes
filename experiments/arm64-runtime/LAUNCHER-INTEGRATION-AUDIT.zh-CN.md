# 2026-10-26 最新对账

以[当前UI功能审计](docs/launcher-feature-audit.zh-CN.md)为准。原35 Metal开关都有代码消费，新补recent staging实验开关。已修正FEX必需宿主、无Rosetta原生包准入和不支持的Vulkan入口、自动准备说明与原System菜单零副作用失败语义；本轮Swift40/stage14/菜单7定向控制通过，最新整包及真实功能集中验收尚未完成。下面是历史记录，不将其中旧“未接入”状态当当前结论。

# 原启动器功能接入审查（2026-10-24）

> 续进状态：System 菜单 FEX 调用桥已补齐并编译，116 项无真实游戏回归通过；游戏内 UI、真实手柄和新构建性能仍待验收。以本节及 `evidence/game-menu-bridge-status.json` 为当前状态，下面的“未接入”描述保留为本轮审查时发现的问题。

## System 菜单调用桥续进

复用 `vendor/metal_api/shim/bbport_game_menu.cpp`，仅增加架构边界：客体函数调用通过现有 FEX；20 个宿主回调在启动前发布为 HLE veneer，游戏只看到客体入口；游戏长期引用的文本与数值放进已有低地址堆；临时文本/工厂/值列表在原生命周期结束后释放。两处入口同时完成版本字节、范围、重叠、跳转可达性和容量检查后才修改镜像。游戏缓存身份在安装后排除修改的代码页，并纳入全部 CPU 前端头文件和实际候选库身份。

原菜单 `DefineTexts` 与 `Fill` 函数逐字不变；`Poll` 除固定数组改指针后的显式长度表达式外不变。没有重写页面、汉化、存档或联网策略，也没有新增 GPU 资源。原 CPU 侧 Metal 库保留原 SHA；候选库新增 SetGuestBridge，一个原有导出符号都未移除。当前 CPU 显式链接私有 `libbbgpu.menu-candidate.dylib`，GPU 子进程继续用已验证的原后端，消息协议不变。

自有实际 FEX 用例：2 页原菜单，20 轮页面构建，240 行，1,654 次调用及嵌套回调；临时分配无累积，持续持有客体数据负载 5,318 字节（包含测试对象，**不是实际游戏 RSS 增量**）。关闭、代码不匹配和过短镜像均在安装/分配前拒绝。另覆盖8种双钩子原子计划反例及错误虚表/值列表边界。实际 FEX 测试安装与游戏相同的 Darwin 异常分派；初版测试未安装时 SIGBUS 失败，记录保留，没有靠忽略未知信号通过。

最终完整回归 `evidence/suite-1792851238099660000`：116项全部通过，不传 --metal，不加载真实游戏、不创建游戏窗口。新增四项菜单用例已接入 `tests/run_prototype.py`。定向证据 `evidence/game-menu-boundaries-final-1792850996192928000`。这些证明调用桥和自有对象生命周期，不能代替真正的游戏 System UI、手柄或性能验收。

构建命令：先 `python3 tools/build_menu_candidate.py` 构建只替换原菜单对象的私有库，再在原 FEX 构建中选择 `-DBB_ARM_GPU_LIBRARY=<原型目录>/backend/bbport/bin/gpu-metal-arm64/libbbgpu.menu-candidate.dylib`，`ninja -C build/fex bb-game-arm64 test_game_menu_fex -j1`。原型构建依赖既有冻结 GPU 对象，不是正式仓库源码发布/自包含构建验收；本轮不涉及正式发布。

本轮按用户要求停止真实游戏测试，先审查原启动器到 ARM64 CPU/FEX 组件的接入链。结论：**当前原型还不能称为原启动器的完整替代；确实存在初始化遗漏和未接入功能。** 能启动、脚本输入可用、113 项旧测试通过，不代表真实手柄或全部前端功能已经验收。

范围为 Shared 隔离开发树。正式应用、存档、设置、缓存不动，未发布。下面的“源码接通”只指调用关系与配置消费已找到；与“本轮实机验证通过”分开。历史游戏实测均属于旧 CPU 构建，不能冒充本轮修复后验收。

## 已确认问题与修正

### 1. 原图形启动入口没有进入主线程事件循环（已修源码、已编译，待真实手柄验证）

- 原 `src/probe.c:main` 将游戏放到工作线程，主线程执行 `bbgpu_main_thread_loop()`。
- ARM 原型之前只看 `--graphics`。原启动器使用位置参数 `boot.bin --user ... --app0 ...`，不带该标志，所以错误地把游戏放在主线程。
- 原 GPU 前端把 `SDL_InitSubSystem(SDL_INIT_GAMEPAD)`、`SDL_PumpEvents()`、Cocoa run loop 和 CPU 进程 Dock 隐藏投递到这个队列。队列未运行，键盘仍可经远端共享状态更新，真实手柄却失效。不是设备白名单。
- 已用与 `game_main` 相同的 `BbLauncherCli::Parse` 判断图形模式，进入原有事件循环；保留 CPU-only 和无图形自有测试入口。
- 当前旧进程的一秒线程采样 `/private/tmp/bb-human-input-main-thread.sample.txt` 验证了主线程正在执行 FEX，而不是事件泵。修复后尚未启动真实游戏，不宣称硬件手柄或 Dock 已实测恢复。

### 2. 工作线程默认栈不再匹配原加载器（已修、定向回归通过）

- 原 macOS 加载器显式申请 64 MiB 工作线程栈；原型图形路径使用默认 `std::thread` 栈。
- 已复用原线程布局：用 pthread 属性申请同样的 64 MiB 虚拟栈。自有线程测试验证工作线程与主线程不同，并读取实际 pthread 栈大小。
- 这是虚拟保留，不是预先占用 64 MiB RAM；实际游戏内存和性能变化仍待完整接入后对照，不能据此保证零开销。

### 3. 原诊断参数与游戏标题不完全一致（已修、已编译）

- 原 CLI 的 `--strict-imports` 之前被判为未知参数。已支持并在发现未解析导入时拒绝进入客体代码；默认解析策略不变。
- GPU 配置的窗口标题原先硬编码为 ARM64 Prototype；已使用原 SFO 的 TITLE。分辨率初始值 1920×1080 与原加载器一致，实际渲染/输出仍由原设置、补丁和 Metal 场景目标决定，未降低画质。
- 无参数、畸形原启动参数的当前 CPU 进程测试均受控返回 1；不会因入口解析抛出未捕获异常而制造 134。

### 4. 原界面可选组合超出原型组件能力（已补源码和单元测试，未替换正在打开的 QA GUI）

- 原型 CPU 只链接了现有独立 ARM64 Metal 组件，不能关闭该宿主后退回旧 x86 进程内 Metal，也未接入 Vulkan CPU 替换路径。
- 当前游戏元数据接入仅验收 CUSA03173；原启动器接受六个区域 ID。因此不能把其它区域的前端检查通过等同于原型已支持。
- 已增加最小 CPU 组件准入检查：Metal + ARM64 宿主以外的组合、未验收区域，在原 `play()` 的存档准备之前明确报错；不改用户选择，不偷偷切渲染器，不新增另一套存档/联网策略。旧 x86 组件保持原行为。
- 这只是提前说明真实能力，不等于把 Vulkan、其它区域的兼容工作做完；源头功能不能被删除后声称无缝兼容。

## 明确未接完：游戏 System 菜单设置页

原加载器在补丁后调用 `bbgpu_patch_image()`，其中 `BbGameMenu::PatchImage()` 安装消息查询和新增页面钩子。ARM CPU 入口没有调用，也没有等效的 FEX 接入。`BB_GAME_MENU` 传到了环境里，但这一步没有发生，所以它是**确实未接入**，而非普通“还没实测”。F1 浮层由 GPU 自己处理，是另一功能。

不能直接补一行调用：原菜单实现会生成 x86 跳转到宿主函数，并从宿主直接调用游戏函数；在 ARM CPU 下必须通过现成 FEX HLE 入口/回调桥，菜单的文本、工厂函数和被客体引用的数据也需要正确地址与生命周期。这些地方不处理，直接开钩子反而可能跳入 ARM 地址作为 x86 执行。

下一阶段必须复用原 `bbport_game_menu.cpp` 的菜单定义、文本、设置读取和保存逻辑，只抽出游戏函数调用、回调、detour 和客体可读数据这几个架构边界。不要再写一套菜单。先自有正反例，再检查原游戏 System 页；在此之前停止追加 FPS、跑图和重复死亡实测。

## 全功能核对表

| 功能 | 当前接入与证据 | 仍需完成/限制 |
|---|---|---|
| 前端偏好、语言、目录 | 原 Settings/Localization/LauncherModel；仅 QA plist 与 Shared 路径重定向 | QA 从不参与正式进度选择；本轮未运行迁移 |
| 启动前文件/版本检查 | 原 Checks、game_check.py、1.09 合并结构检查 | 原型仅 CUSA03173；已在原 play 前补明确准入 |
| 准备/链接/模块初始化/TLS | 原 prepare/link 脚本及 BBPROBE5；原型加载器/FEX 桥 | 早前自有模块/TLS/线程回归通过；不是所有 PS4 接口均实现的承诺 |
| 正常图形入口 | 原 CLI + 原主线程任务队列 | 本轮已修，真实手柄验证待做 |
| 键盘、按键重绑、快捷键选择 | 原 runtime_pad.c 与 runtime_quicksave_bindings.h；改动仅堆统计回调 | 原先键盘可用；真实物理 F2/F3 要补，脚本按钮通过不代表物理键通过 |
| 手柄枚举、GUID/名称选择、热插拔 | 原 current_gamepad()/BB_GAMEPAD + SDL 主线程泵 | 没有白名单；入口修复后需实机验证连接/断开/重连 |
| 手柄震动、触摸板 | 原 SDL_RumbleGamepad/触摸板映射 | 实机未验收；原运行库的传感器 stub 是上游原有，不宣称新增陀螺仪 |
| 音频/解码/设备输出 | runtime_audio.c、runtime_ajm.c 与原版逐字相同；SDL 音频队列/节拍保持 | 本轮按要求静音；真实声音、爆音和 FineTune 应用归属没有新验收 |
| 窗口/全屏/多显示器 | 原 GPU window.cpp 消费 BB_FULLSCREEN/BB_DISPLAY | 默认宽高与原加载器一致；多显示器/全屏切换尚未逐项实机验收 |
| VSync、帧率上限、帧率解锁 | 原 OptimizedProfile + patches.py + Metal presenter | 启动器设置仍为唯一来源；本轮无帧率测量 |
| MetalFX/预设/输出尺寸 | 原设置、原补丁生成与现有 Metal 场景目标 | 1.25/1.5/1.7/2/3 映射不另实现；速度图等继续走同一 GPU 构建 |
| LOD/特效/跳过开场/默认离线 | 原 bbport.ini、patches.py 生成启动补丁 | 已找到脚本消费；未通过改默认值伪装兼容 |
| 原版中文与社区汉化 | 原 ChineseTranslation、mods.py、run.sh 的英文槽覆盖与语言映射 | 原 GUI 旧构建曾进入中文场景；本轮不新增字体要求，不重写汉化逻辑 |
| 普通 Mod、排序、禁用、临时覆盖视图 | 原 Mods 界面与 scripts/mods.py | 原 run.sh 持有视图/转发 TERM/退出清理；未改游戏目录 |
| 外置 XML 补丁/宽屏 UI | 原发现/选择/准备，BBPATCH2 适配器保留顺序覆盖/指针重定位 | 定向格式/负例通过；宽屏、所有第三方组合未实机全验收 |
| 正常存档与存档命名空间 | 原 runtime_savedata.c；TITLE_ID 与 INSTALL_DIR_SAVEDATA 分离 | 不把 CUSA03173 错当 CUSA00207 存档空间；本轮未改任何进度 |
| 存档管理/备份/导出/保留数量 | 原 SaveStore/SaveRetention/SaveManagerView | 源码一致；本轮未操作文件选择框或执行恢复/清理 |
| iCloud 最新正常存档同步 | 原 CloudSaves/CloudSaveSettings，原同步触发点 | QA cloudSaves=false；没有用正式云目录做验收，也不宣称本轮上传成功 |
| F2 新检查点 | 原 SaveRequest + 原 quicksave.py；游戏函数经 bb_arm_call | 旧 CPU 原 GUI 脚本路径已通过；物理键仍待补验 |
| F3 永远离线恢复 | 原 supervise_restart.sh + 原状态 75/76；ARM 只桥接退出与子进程回收 | 旧 CPU 原 GUI 脚本路径已通过；修复主线程后的实机 F3 待验 |
| 自动继续/快速恢复输入 | 原 runtime_quicksave 自动确认；原 quickload pending | 不改正式快照，不引入第二份恢复逻辑 |
| 在线/离线共用存档与归档 | 原 SaveStore.prepareMode 与原 GUI 路径 | 只使用 QA 副本，未参与正式选择 |
| THD 配对/验证码复制/钥匙串 | 原 Online.swift 逐字相同、已授权 QA 凭据 | 不创建新配对系统；不读取正式账号 |
| THD 路由/账号传入 | 原 HuntersDream.environment、online.py、原启动脚本 | THD SDK/回调采用 ARM typed bridge；原 GUI 旧 CPU 登录及 HTTP 成功已有证据 |
| 留言/圣杯 | 原 THD 网络模块、GPU 文本输入 API | 当前 ARM 原 GUI 尚无完整业务验收；草稿/菜单打开不算发出留言，不套用正式 Rosetta 成绩 |
| 合作/入侵 | 原 THD matching/signaling，调用客体用 CallGuest | 无第二客户端；未验收，不宣称全面联网完成 |
| 游戏 System 菜单设置页 | 原实现加 FEX 架构桥，实际 FEX 自有回归通过 | 真实游戏 UI 待验收；与 F1 区分 |
| F1 设置/输入捕获 | 原 GPU 浮层与共享设置/键盘状态 | 原 GPU 代码保留；这不验证上一行 |
| Dock 图标/隐藏 CPU 图标 | 原 GPU 图标与进程隐藏逻辑 | 隐藏操作原先因队列未跑而漏执行；入口已修，真实图标数量/过渡待验 |
| 停止/退出/子进程回收 | 原 GUI Stop + 原 shell 信号转发；原 Remote::BeforeRestart/owned child reaping | 原 GUI 旧 CPU F3 回收有证据；本轮不杀其它应用/进程 |
| 日志、FPS、诊断开关 | 原 GUI environment、GPU 消费位置 | 没有持续录屏/视觉采样；本轮没有新性能结论 |
| 36 个 Metal 优化开关 | 每个均找到 GPU/运行库/脚本消费；原 profile 依赖保持 | 文本匹配不等于运行时实际命中，不重新宣称收益 |
| 二级页表/速度图/遮挡/纹理回收 | 复用同一现有 Metal 二进制和资源生命周期桥 | 本轮未改 GPU、预算、分辨率、缓存容量；主线程修正后仍需一次综合验收 |
| CPU 缓存/软件 TSO | 原型默认策略保持，软件排序继续启用 | 硬件 TSO 仍未获系统准入；旧编译缓存身份随构建变化，不删缓存 |
| QA 自动输入 | 人工接管前已从该 QA extraEnv 删除 BB_ARM_QA_PAD_FILE；原 run_optimized 清除普通自动输入 | 保留显式开发测试入口，但本轮未再注入任何输入 |
| Vulkan/进程内 Metal/其它区域 | 原前端相关能力仍在 | ARM CPU 原型未接完整，只能提前明确拒绝；不能据此删掉源头功能 |

## 源码与二进制证据

本轮源码清单工具 `tools/audit_launcher_integration.py` 只读取源文件，不执行游戏、不读存档或凭据。覆盖原 GUI/测试/本地化文件、16 个原启动文件、25 个运行库文件；生成逐文件 SHA 与实际 diff，不能把脚本相同就推断全部功能已接入。

- 首轮清单：`evidence/launcher-source-inventory-1792848719320519000`：36 个 GUI/测试/本地化文件中 31 个一致、无缺文件；16 个原启动文件全部一致；25 个运行库文件中 16 个一致；36 个 Metal 开关均有文本消费位置。增加本轮 QA 准入后以最新清单为准。
- 原 System 菜单没有安装的证据：`src/game_runtime.cpp` 缺 `bbgpu_patch_image`/FEX 等效；对照 `R/src/probe.c` 的补丁后调用与 `vendor/metal_api/shim/bbport_game_menu.cpp` 的直接双向代码调用。对它未做不安全的直接启用。
- 当前 CPU 编译与定向测试：`evidence/launcher-integration-audit-1792848704932774000`。C++ 参数/线程/补丁/SFO 正反例通过；实际 CPU 的两项错误入口测试受控退出 1；Swift CPUComponentTests 3 项通过。初次错误入口测试在没有设置私有 GPU 目录时被构造器以 78 拒绝，这是 QA 隔离保护，不是回归成功，保留原始失败记录。
- 当前架构复核：`evidence/x86-boundary-audit-1792848922803696000`，103 个显式宿主/非系统依赖图像都有 ARM64 slice，未发现未解析文件依赖。源码扫描 270 个编译单元/417 个候选 include；这不是“所有 ABI 均已证明正确”。x86 字节为 FEX 客体/生成代码；硬件计数器用 ARM CNTVCT/CNTFRQ，保留的软件 TSO 非 Rosetta。
- 源码扫描发现的 shadNet 原始直接回调仍在 vendor 目录，但当前 native loader 只准入 thd；`bbnet_register()` 的 THD 分支只注册 THD+HTTP/SSL 并返回，THD 回调使用 CallGuest。未注册的 shadNet 分支不能作为可支持的替代服务器路径开放。THD 的 `webapi_reg_push` stub 与合作邀请事件未送达提示为继承的模块限制，不冒充完整平台能力。
- 正式快照：`/private/tmp/blood-echoes-arm64-metal-followup-20261023/launcher-integration-audit-formal.json` 与 `native-runtime-goal-current.json` 的 11,671 项逐项一致（其中绝大多数是缓存文件，**不是 11,671 个存档**）。

## 后续顺序与完成条件

1. 先完成原 System 菜单的 FEX 双向调用/数据/回调边界，补针对这些边界的自有正反例。保留菜单定义和原设置策略。
2. 完成源码接线后，再用同一个原 QA 启动器集中检查真实手柄热插拔、物理 F2/F3、Dock、全屏和设置；只验证缺项，不重复已成立的测试。
3. 原 GUI 登录继续复用 QA 凭据，补留言和圣杯实际业务；无搭档的合作/入侵明确保留未验证。
4. 最后才在相同画质/分辨率下复测 Rosetta/ARM 性能和去重内存。旧 0ea CPU 对照不自动继承给本轮 CPU。

当前没有重新启动游戏，没有替换正式版或当前 QA GUI 应用包；新的 CPU 源码和二进制位于隔离树，stage 的组件符号链接仍指向它。目标保持 active，完整兼容 gate 未通过。

最终源码清单：`evidence/launcher-source-inventory-1792849317084893000`，原有36项中28项逐字相同、8项为明确的 QA/架构准入及本地化差异，无缺文件。16项原启动文件仍全部一致；25项运行库中16项一致；36个Metal开关均找到消费位置。另补原监督协议自有回归通过。机器状态：`evidence/launcher-integration-status.json`；`ready_for_game_tests=false`。

本轮最终状态：`evidence/game-menu-bridge-status.json`。116项通过；104项宿主/依赖图像均含ARM64且无未解析文件依赖；正式11,671项指纹再次一致。可进入当前Metal/CUSA03173配置的集中功能QA，性能与正式推广gate仍未通过。原bbgpu_patch_image另含旧Gnm census/observe开发诊断detour，尚未移植；普通启动配置不启用它们，不将其宣称为已支持。

## 实机续进：由用户接管输入

同一个隔离原启动器已完成打包更新（Bundle ID 不变）；证据 `evidence/launcher-menu-functional-1792851784403004000`。当前 CPU 和菜单候选库身份与上面的116项测试一致。中文主菜单静态截图已核对；一秒线程采样显示主线程进入原 bbGPU/Cocoa loop，日志识别 Xbox One S Controller。以上只证明标题画面、事件循环和设备枚举，尚不证明真实按钮、System 新页或 F3 验收通过。当前 QA 包基本签名通过，严格 bundle 校验因已有 Resources/bbport 指向 Shared stage 的符号链接失败，不是可分发安装包。

首轮因系统 swap 从 7316.875 MiB 增至 9424.1875 MiB，触发2 GiB增量保护而终止；终止时 pressure=1，不能记作游戏崩溃或严重压力。之后为用户手动测试重新建立同策略后台保护，证据 `evidence/launcher-human-functional-1792852349084325000`。用户接管期间不发送游戏按键、不切换窗口、不录屏；只读日志。物理手柄、System设置页、F2/F3自动继续和Dock仍待结果，不将旧构建证据迁移为新构建结论。

### 用户手动功能验收结果

当前 CPU `1a650135…`，原 QA GUI `f4c6610d…`，CPU菜单候选库 `dbcbf3ff…`，GPU后端仍为原 `2da2c735…`。证据 `evidence/launcher-human-functional-1792852349084325000`。用户确认：真实手柄操作正常；中文和 Dock 正常；System 的 Display 与 Game effects 两页均能正常打开。两次 F3 均在原监督器内替换 CPU/GPU 子进程，旧进程不再存活；日志确认离线恢复和角色就绪，用户确认自动继续正常。

普通 F2 两次未产生保存请求；Fn+F2 随后成功产生“Saving checkpoint / requested a fresh character and profile save / Quicksaved - checkpoint ready”完整链。用户移动后 F3 恢复至这次新保存位置，已排除“ARM 快照仍固定恢复初始位置”的假设；本轮没有修改保存实现。按键/屏蔽/请求状态机对照原代码一致，实际调用客体 SaveRequest 通过已有 FEX 桥；Fn 的具体系统设置未改变。以 `f2-f3-acceptance.json` 为此项验收。

System 两页“可打开”不等于全部设置均已传播验证；目前请用户只切换显示FPS再恢复，避免改画质。正在等待新构建的连续两次正常死亡重生；旧 CPU 的死亡和性能证据不自动套用。单客户端剩余 THD 业务及新构建性能/去重内存仍未完成。正式版未更新、未发布。


## 2026-10-25 睡前目标后的契约复核

本段覆盖本轮实测与新增源码，早期段落的“已通过”不能自动覆盖新二进制。

- 错误退出码75与诊断退出码76原先侵占原监督重启契约，属于本项目接入遗漏。现由src/runtime_exit_codes.h集中定义，restart头转发原库；原run_optimized/supervise_restart等16文件字节不改。真实FEX的exit/_exit低字节冲突被转为82，GPU异常透传冲突转为83。17个人工FEX用例、6组原监督脚本，以及原重启拥有子进程回收测试通过；真正菜单/F3的用户新一轮验收按要求暂缓。
- 包装器生成器漏掉DIALOG宏的16个已有实现，现生成343个包装器，并由人工guest调用初始化/状态/打开/结束流程验证；117项无窗口回归通过。本轮实际游戏imports.tsv未解析数由41降为25。剩余16个AvPlayer接口需要正式guest callback/handle桥，另9项libkernel/libc/数据符号需继续逐项分类，不能用返回成功或隐藏导入替代。
- void* ABI默认仅检查首字节。libc复制/填充/比较现新增完整span校验后仍调用原实现、原CPU写通知；19个有效/越界/只读/重叠/超大长度真实FEX用例通过。
- 完整构建的旧线程启动对照目标仍链接旧GPU宿主库，现统一BB_ARM_GPU_LIBRARY；仅修复开发构建依赖。
- 候选构建保护曾依赖12077/12080旧PID；现按当前游戏宿主进程清单拒绝并行编译，避免错误认为游戏已退出。
- GUI的8个差异逐项为：ARM组件准入与CUSA03173约束、隔离Defaults/数据路径、原生宿主说明精简及三语言准入提示。没有重写手柄、存档、F3、在线服务选择或图形选项。36个Metal开关都有消费者只证明源码接线，不证明每项性能收益。
- 架构/动态依赖图和回调ABI测试保留；未解析接口仍是明确未完成项。本轮不声称完整兼容通过。

原固定场景2026-10-25对照：ARM45.95 FPS、Rosetta45.77 FPS，GPU均约20.5ms；ARM主进程CPU约221%、Rosetta约126%。这是一组AB，不能证明普遍提帧。聚合footprint约5743/5395MiB，包含共享页双重计数，不是物理RAM差异。正式11671项哈希与既有基线一致。
