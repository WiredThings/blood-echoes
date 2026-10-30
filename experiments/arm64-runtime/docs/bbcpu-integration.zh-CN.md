# 独立 bbcpu 接入与静态准备（2026-10-30，执行中）

按用户最新要求修订原目标：FEX保留，新增独立bbcpu进程/构建，在原启动器选择、下次启动生效。一份游戏进程只链接所选引擎；**不建设FEX调用静态重编译库的桥，不用FEX兜底**。bbcpu默认用户本机静态重编译，继续使用它自身的动态翻译/解释器。共享游戏宿主已编译，静态准入已接线，但尚未完成自动增量编译、启动器选择或实际游戏验证，不宣称用户已经能使用此后端；正式应用仍0.2.59 / 5b186b81，不修改正式数据或Release。

## 来源与复用

| 组件 | 固定来源 / 处理 |
|---|---|
| CPU、扫描和生成工具 | [bloodborne-recomp](https://github.com/bloodborne-native/bloodborne-recomp/tree/56f8f91a31b311c01233a00ba572545d4c3f4ecb)，44份工具/源码约713KB纳入`vendor/bbcpu`；保留GPL-2.0-or-later及上游文件SHA，修改另外登记。没有游戏资产、生成游戏C或游戏库。 |
| CPU接入参考 | [bloodborne-runtime](https://github.com/bloodborne-native/bloodborne-runtime/tree/1117ddaa286636a8807920c05bc67ff12a4bd1b5)，其recomp子模块也正是上述revision。参考线程、TLS、动态代码、静态加载，不替换本地整套系统库或renderer。 |
| 分发/准备参考 | [bloodborne-mac](https://github.com/bloodborne-native/bloodborne-mac/tree/54a2b3ed680167fb67b5249edf7e4c9c4433f6ae)，参考本机建库流程，不引入其前端。 |
| 继续复用 | 现有Swift启动器、原prepare/Mod/汉化选择、BB boot/模块链接、当前Metal跨进程框架及其内存/速度图/遮挡/两级页表补丁、THD、手柄、F2/F3、自动继续、存档管理、原退出码。 |
| 验证基线 | `upstream-game-regression-20261030.zh-CN.md`的已交付FEX包及完整条件。顶点开关约1.53%的同包收益不是bbcpu收益；上游相对其自己译码器的14%也不是相对FEX。 |

## 必须适配的边界

1. **CPU/HLE**：原`game_runtime.cpp`直接持有FEX类型。先把现有验证、参数解码、宿主范围发布/撤销分离为共同`HleCallService`；FEX保留原wrapper及错误语义，bbcpu仅调用共同HLE服务、不链接FEX。其调用可触达GPU宿主库，执行层不能绕过原指针校验或把任意非guest地址当作ARM函数。
2. **入口、回调、线程、TLS和FP**：上游公开调用最多六个整数参数，而当前联网/媒体回调测试会用七个。保留SysV第七参数栈位、完整GPR/SIMD和调用者持有的guest栈、当前TCB、重入状态；不能用统一返回零代替。已经补上CPU上下文、JIT查询表、隐式栈和静态兜底帧的线程退出清理；实际游戏线程生命周期仍待集中回归。静态C→自身译码器→HLE退出可在HLE作用域完成后回到所属Run，保留原线程退出和进程退出码语义。
3. **动态代码**：本地已有只读HLE veneer、启动资源/菜单detour和现场补丁。bbcpu原16段静态列表不足以替代当前实时可执行查询；首次接入提供权威查询和独立syscall handler，INT3保持上游原语义。原decoded失效代码明确会遗留旧块，native JIT链接/线程cache与静态direct slot失效也必须审查，不能只删decoded条目就声称安全。正常运行仍保留必要完整资源准入。
4. **真实静态输入**：上游`build_library.py`从原始eboot生成；我们须接现有生效Mod、链接/稳定启动补丁，分开稳定代码改动与运行时detour。不得拿原始eboot摘要冒充已生效代码；运行时原有逐函数hash检查和bbcpu动态回退保留，实际不匹配逐类记录。
5. **产物身份**：代码/函数边界/发现种子、完整生成选项、rc.h/BbCpu布局、真实头依赖、编译器与目标/链接兼容信息进入身份；目录、覆盖来源、UI和日志只作追踪。上游对象指纹仅以生成C及编译器版本字符串加盐，需补真实依赖，避免头变化误复用；其整个Git revision/annotations也不能让无关更新使所有兼容产物失效。
6. **打包和选择**：现有八角色准入和CPU嵌套App不能把新exe冒充旧FEX角色。新后端需明确独立角色、原会话监督、CPU选择的冻结意图及准备/运行一致证明，不能另建启动器或新退出码。F3继续原会话后端并恢复离线，不在中途读取刚改的UI切换引擎。

## 分阶段交付

1. 独立CPU/工具构建，作者代码的原生翻译、完整帧、七参数、重入、TLS、SIMD/聚合、坏指针、未知HLE及实际静态调用/不匹配回退；共同HLE对FEX原合同回归。没有游戏资产进入测试源码。
2. 同一游戏宿主的独立bbcpu目标，复用原系统接口与Metal宿主库。先完成生命周期、动态代码、退出/回调审查与定向控制，再进行游戏启动，避免边测边补漏。
3. 在原准备流程接生效代码导出、全扫描自动生成、用户本机限并发增量编译、持久产物及运行时准入；无变化复用，稳定代码Mod增量，坏单项局部修复、旧产物保留。随后接原Swift选择和原F3准备证明。默认准备，失败明确诊断并仅退回bbcpu，不静默切FEX。开发机先用实际系统工具链；无需把上游70MB下载器和新前端一起移入。
4. 集中回归后对照当前FEX与默认静态bbcpu：同场景/路线/画质/Mod/存档隔离副本与明确准备状态，平均FPS、长帧/探索卡顿、普通启动/F3、峰值/持续内存；首次编译时间、磁盘用量、静态覆盖及回退原因/次数/耗时单列。正常CPU初始化、GPU对象加载不能误算成重编译。不要另排纯动态bbcpu性能组，不用Cua/截图/录屏，不把保护中止算完成。
5. 只有兼容与整体表现更合适才改默认、暂停FEX推进。否则保持FEX默认并保留bbcpu成果与明确结论；本轮不推广正式应用或Release。

## 已验证（隔离组件；不等于游戏验收）

- 独立`bbcpu/CMakeLists.txt`构建完整CPU、scanner、C生成器、共享HLE、有效代码导出及准入控制工具。共享`game_runtime.cpp`也已编译为独立`bb-game-bbcpu`，沿用实际选中的Metal宿主库及其真实头/编译配置；架构和依赖/全局符号核对为ARM64且没有FEX执行引擎。FEX目标仍使用原入口与返回类型。
- 5组CTest通过：CPU状态/线程/TLS/FP/七参数、共享HLE/SIMD/结构体/嵌套回调、4种准确退出74的拒绝反例、生效映像/原补丁算法控制、FEX与共同HLE服务合同。共同服务的正常返回保持小对象，不在热路径额外复制完整错误记录。19项原构建计划/生成器控制通过，包括独立CPU选路与同一真实renderer输入。
- 实际作者静态流程：自制ELF+真实unwind表→原BB boot/启动补丁算法的有效映像→原scanner与bbrecomp→系统clang ARM64库→CPU兼容身份/有效映像/摘要/Mach-O准入→固定fd实际dlopen→至少8192次静态原生调用。3个函数覆盖算术、硬件TSC及静态C→自身译码器→HLE→所属Run停止；后者重复9000次，不执行后续UD2。约2.33秒、库33488字节仅是小型方法控制，不外推全游戏首编/磁盘/FPS。
- CPU改动后旧函数被拒绝并由bbcpu得到新结果；错误ABI、溢出/未映射函数范围、错误CPU合同、坏库摘要、错误Mach-O架构及实际游戏映像改变均被拒绝。文件保留；固定fd加载经过真实执行验证，避免摘要检查后再次按可变路径打开库。
- 原HLE与Metal使用硬件计数器，上游CPU默认1.6GHz。此分支为CPU选择相同硬件时钟域并使用独立ABI5；生成库真实入口检查ABI，解释/动态/静态计时对齐已验证。未改变共享GPU/HLE时钟。
- 有效映像使用原`BbStartup::Apply`、`BbLauncherPatch::Apply`，主段/unwind与BB boot严格对应；映像指针按实际相对值规范化，导入保持动态，代码重定位槽明确列出。代码Mod改变身份，恢复原代码恢复原身份；部分重定位写入、错误guard、布局不匹配和链接输入均拒绝。来源路径/完整源文件摘要仅追踪，不冒充代码等价。导出按64KiB流式处理，私有映射结束释放，不保留第二份完整游戏映像。
- 线程清理反例曾抓到Darwin先销毁TLV导致栈指针丢失，已把所有权放进pthread key持有的CPU堆对象。四个测试线程的1MiB隐式栈都实际释放，活上下文恢复1；重入只恢复寄存器/执行前缀，保留期间新建的缓存/帧所有权，避免丢失或恢复旧指针。
- 主要本机证据：`/private/tmp/blood-echoes-bbcpu-aot-20261030-08/result.json`、`/private/tmp/blood-echoes-bbcpu-game-build-20261030-08.log`、`/private/tmp/blood-echoes-bbcpu-shared-plan-tests-20261030-02.log`。作者目录不进入GitHub；仓库只收方法与结果摘要。
- 正式应用仍0.2.59 / 5b186b81，偏好/存档/已有缓存未写入，临时纹理诊断保持关闭；两份原未提交FEX/unwind候选原样保留。未做游戏FPS、白模、F3、死亡或联网验收，也未更改默认CPU或Release。

## 当前真实剩余工作

1. 把已验证的有效代码/产物准入接上完整本机增量生产器：真实头/预处理输入、工具依赖、编译器/选项、对象与链接身份、旧对象复用及局部修复。原vendor脚本的弱对象盐不能直接作为本启动器最终缓存策略。
2. 完成已识别的运行期代码失效审查：decoded unlink、JIT直连/间接查询和静态direct slot，登记真实会变化的代码范围；不能把仅删除decoded条目当作全缓存安全。之后才启动真实游戏。
3. 原前端选择、独立包角色/依赖及supervisor意图冻结；普通启动选择一次、F3沿用当前引擎与原恢复离线语义，共享同一准备/语言/联网/输入/存档流程。暂不显示不能实际启动的开关。
4. 在同一隔离数据与当前画质条件下做FEX vs默认静态bbcpu集中功能/性能/内存/启动对照，另记首编/磁盘/兜底原因及开销。没有单独纯动态bbcpu性能组。确认后才考虑改默认；正式安装和Release仍不在本轮范围。

本机复现（只编工具与作者fixture，不启动游戏）：

```sh
cmake -S experiments/arm64-runtime/bbcpu -B /private/tmp/blood-echoes-bbcpu-build \
  -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_PREFIX_PATH=/opt/homebrew
cmake --build /private/tmp/blood-echoes-bbcpu-build --parallel 1
ctest --test-dir /private/tmp/blood-echoes-bbcpu-build --output-on-failure --timeout 30
python3 experiments/arm64-runtime/tests/run_bbcpu_authored_static.py \
  --build-dir /private/tmp/blood-echoes-bbcpu-build \
  --work-dir /private/tmp/blood-echoes-bbcpu-authored-new
```

作者work目录必须全新，结束不删除旧证据。真实游戏的准备、静态命中、F3、联网、死亡、内存与FEX对照均尚未验收。当前目标卡旧blocked状态不能由现有目标工具恢复/改正文，Codex界面访问又被安全限制拒绝；仅修订原执行目标文件，未重建或标完成。
