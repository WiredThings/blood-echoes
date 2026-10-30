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
- 5组CTest通过：CPU状态/线程/TLS/FP/七参数、共享HLE/SIMD/结构体/嵌套回调、5种准确退出74的拒绝反例、生效映像/原补丁算法控制、FEX与共同HLE服务合同。共同服务的正常返回保持小对象，不在热路径额外复制完整错误记录。19项原构建计划/生成器控制通过，包括独立CPU选路与同一真实renderer输入。
- 实际作者静态流程：自制ELF+真实unwind表→原BB boot/启动补丁算法的有效映像→原scanner与bbrecomp→系统clang ARM64库→CPU兼容身份/有效映像/摘要/Mach-O准入→固定fd实际dlopen→至少8192次静态原生调用。3个函数覆盖算术、硬件TSC及静态C→自身译码器→HLE→所属Run停止；后者重复9000次，不执行后续UD2。约2.33秒、库33488字节仅是小型方法控制，不外推全游戏首编/磁盘/FPS。
- CPU改动后旧函数被拒绝并由bbcpu得到新结果；错误ABI、溢出/未映射函数范围、错误CPU合同、坏库摘要、错误Mach-O架构及实际游戏映像改变均被拒绝。文件保留；固定fd加载经过真实执行验证，避免摘要检查后再次按可变路径打开库。
- 原HLE与Metal使用硬件计数器，上游CPU默认1.6GHz。此分支为CPU选择相同硬件时钟域并使用独立ABI5；生成库真实入口检查ABI，解释/动态/静态计时对齐已验证。未改变共享GPU/HLE时钟。
- 有效映像使用原`BbStartup::Apply`、`BbLauncherPatch::Apply`，主段/unwind与BB boot严格对应；映像指针按实际相对值规范化，导入保持动态，代码重定位槽明确列出。代码Mod改变身份，恢复原代码恢复原身份；部分重定位写入、错误guard、布局不匹配和链接输入均拒绝。来源路径/完整源文件摘要仅追踪，不冒充代码等价。导出按64KiB流式处理，私有映射结束释放，不保留第二份完整游戏映像。
- 线程清理反例曾抓到Darwin先销毁TLV导致栈指针丢失，已把所有权放进pthread key持有的CPU堆对象。四个测试线程的1MiB隐式栈都实际释放，活上下文恢复1；重入只恢复寄存器/执行前缀，保留期间新建的缓存/帧所有权，避免丢失或恢复旧指针。
- 主要本机证据：`/private/tmp/blood-echoes-bbcpu-aot-20261030-08/result.json`、`/private/tmp/blood-echoes-bbcpu-game-build-20261030-08.log`、`/private/tmp/blood-echoes-bbcpu-shared-plan-tests-20261030-02.log`。作者目录不进入GitHub；仓库只收方法与结果摘要。
- 正式应用仍0.2.59 / 5b186b81，偏好/存档/已有缓存未写入，临时纹理诊断保持关闭；两份原未提交FEX/unwind候选原样保留。未做游戏FPS、白模、F3、死亡或联网验收，也未更改默认CPU或Release。

## 当前真实剩余工作

1. 将已接线的本机增量生产器、原前端选择和单会话意图封装到当前源码载荷的隔离QA包；不把旧原型GPU/CPU的结果冒充当前正式路线。现有正常源码包准入、原QA命名空间/种子、源码闭包写入和CPU工具封装复用，不创建新前端或恢复协议。
2. 检查实际启动中的静态覆盖、运行期自身兜底原因/次数/耗时和资源释放；共享线程、F2/F3、手柄、汉化/联网/菜单集中回归仍未完成。组件正确不等于游戏兼容。
3. 在同一隔离数据与当前画质条件下做FEX vs默认静态bbcpu集中功能/性能/内存/启动对照，另记首编/磁盘/兜底原因及开销。没有单独纯动态bbcpu性能组。确认后才考虑改默认；正式安装和Release仍不在本轮范围。

## 2026-10-30 增量生产器与执行钩子

- `bbcpu_static_library.py`复用原scanner、C生成器与编译选项，但对象键改为真实预处理字节、编译器/动态依赖、选项和native-TSC合同。最终产物另绑定实际链接器/SDK输入。就绪产物复用不依赖保留中间C/对象；坏最终库用已验证对象局部重链，保留旧代与坏产物。作者Mod A→B→A及无关资源变化的控制通过。
- `bbcpu_launch.py`位于原`launcher_local_aot.main`最终exec之前；原资源/映像/Metal准备不被重写，不启动FEX作为辅助执行器。使用原`eboot.elf`与实际linked boot/稳定patches，仅变化时重新导出。CPU工具子进程剥离BB_/BBCPU_/DYLD_变量，包括联网令牌；最终游戏环境保留原联网/恢复语义。失败仍由原CLI映射退出74，不创造新码。
- 原Swift增加下次Play生效的CPU偏好，仅显式双CPU包展示；F3仍继承同一supervisor的环境。执行角色缺失/非ARM64预检报错，旧单FEX包不宣称支持bbcpu。131项Swift测试通过，一项既有测试跳过；八项新调度控制及原工具控制通过。
- ABI5合同现在还绑定实际Zydis/Zycore头。隔离kit复制这些真实头和重定位后的依赖，全部重新签名；不依赖用户碰巧安装的头。CPU宿主只读合同查询沿用原标量临时作用域，保留Metal DSO构造时的数据路径准入，不为查询放宽它。
- 运行期修改审查未找到正常路径调用`bbcpu_invalidate`；原运行时拒绝对所属image/stub的重映射/保护变更，菜单detour在CPU建立前完成，相关特效代码变更约定下次重启。保留这些边界；若未来真实命中不完整的CPU-only invalidation，明确诊断并退出74，不能把decoded unlink或空成功冒充JIT/static同步失效。新增该拒绝反例后五组CTest仍通过，拒绝模式现为五种。
- `/private/tmp/blood-echoes-bbcpu-component-20261030-07/result.json`：实际冻结的CPU/工具/源码/头闭包→原执行钩子→本机静态库→C++准入→固定fd实际静态执行通过。三个作者函数的冷准备约0.72秒、暖准备约0.089秒，暖准备生成/预处理/编译/链接均为零；**不是《血源》启动时间或性能数据**。早期01—05封装控制分别发现真实rpath边界、工具部署目标、重定位签名、复制后依赖根和外部头缺口，均保留证据并按实际依赖修复，没有放宽准入。
- 正式应用、正式数据、已有缓存、默认FEX与Release保持原状。仍需隔离真实游戏与集中对照，不能将本节标为总体完成。

## 全游戏首轮准备（2026-10-31，尚未进入游戏）

- 已复用当前正常源码版载荷生成独立QA包：原FEX/Metal/联网可执行文件和库的实际字节保持不变，只叠加原Swift选择、代码闭包和独立bbcpu工具。`package_bbcpu_source_qa.py`复用原QA命名空间/进度种子，不读正式进度；原前端仍是启动条件唯一来源。
- 旧QA种子一度带着不同画质选项，首次尝试在游戏创建前停止。后续只读复制正式当前INI及画质偏好到新QA副本；路径/存档/网络凭据不从正式偏好导入。原Swift收据确认：bbcpu、Metal、1080p、MetalFX off、离线、静音。正式配置未写入。
- 首轮当前源码QA结果为`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/bbcpu-source-qa-20261030-03/result.json`。原Swift启动收据经过真实只读验证；源载荷目录越界、少/错基础角色、额外执行角色和虚假原脚本身份五种反例被拒绝，旧启动收据34项测试仍通过。
- 生成器分析241817函数、11062868条指令，输出384个C单元；其中约99%已分析指令表示为C，9245个间接跳转仍未静态解析。**这是生成器统计，不是运行时覆盖率，更不是相对FEX的收益。** 正在系统clang串行编译，首编时间、磁盘、峰值内存待完整结束后记录。
- 首次离线准备与实际游戏采样分别计时，避免旧30分钟总时限中止首次建库。监护保留原swap基线、严重压力/快速增长/锁屏规则；准备最多2小时，实际游戏开始后最多10分钟。保护仍只在隔离QA工具，未带到正式应用。Cua仅用于准备前点击原Play并已停止，未用于运行性能采样。
- 完整首编另暴露执行钩子的旧3600秒子进程上限：349/384对象已完成时超时，未运行游戏，也不是内存保护退出。对象与日志保留；以同一冻结生产器、实际输入和对象键在独立有界CPU准备监护下续编。源码将静态首编上限改为7200秒，实际游戏采样预算与内存规则不变。原冻结QA签名和收据不被就地改写。
- 原benchmark的CPU准入原先写死FEX角色和旧QA构建清单；现已复用原Swift收据，明确校验所选bbcpu、其实际重定位host库及原Metal进程。source-built QA沿用自己的完整源码/角色证明，不伪造旧QA的upstream记录，也不扩大旧角色数量准入。真实只读验收通过，未知CPU和以FEX冒充所选bbcpu均被拒绝；原34项收据、20项克隆、20项formal-stage和11项reference-stage控制通过。这些属于测试接线，不是FPS或游戏兼容结论。

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


## 2026-10-31 有界并发与17项失配专项（源码推进中）

- 用户追加夜间自主静音推进及验收后本机更新授权。仍不修改正式存档/设置/现有缓存，不发布Release；只有集中验收及前端走查通过后才推广，不把单项控制当全部完成。
- 两个较重真实单元（recomp_0012b / recomp_0003d）通过原Producer.object、原编译选项，在全新小型证据目录比较：串行228.53s、双任务136.83s；Clang合计采样峰值footprint 474.80/607.88MiB；对象键与SHA逐字节相同。四次编译只涉及两个代表单元，未清缓存或全量重编；监护压力始终正常、swap净增长0。这不是全游戏首编时间的预测，也不是分配器HWM。
- 源码接入最多两个待执行任务的自适应派发，按free+一半inactive估计余量，每任务预留1GiB、另留768MiB；speculative已包含于free，不能重复计算。警告级最多单任务，严重或余量不足停止派发，已运行任务完成且对象保留；不把QA停止规则带到游戏。原生产器/对象键/编译参数不改，计数与依赖登记加锁。
- 准备进度从生产器流式送回原Swift输出收集器及既有LaunchPreparationProgress，不增加第二套前端。bbcpu选择提前声明CPU阶段，Metal完成只占25%，编译按实际单元进度至95%，实际CPU准备返回后到100%；FEX原进度语义保持。133项Swift测试通过（1项既有跳过），4项派发/真实子进程流式控制、8项CPU调度控制和6组原生CTest通过；尚未集中验收新QA前端。
- 新启动前定向审计先执行实际同一启动器脚本、原补丁与CPU安装器，严格准入后在guest entry前退出，没有加载存档或FPS采样。17项全部实证关联：资源钩子10、额外堆5、菜单2；没有未归属项，编译/运行原始SHA均不相同。旧全局代码身份在runtime detour之前已匹配，因此不能把它们归因于原游戏FPS补丁遗漏或仅凭标签认定不可静态编译。审计记录完整地址/范围、FNV及SHA、阶段和真实变更范围。
- 下一步仍未完成：复用同一CPU启动准备导出实际安装后的固定代码输入，处理跳板返回原函数中部的静态覆盖，保留逐函数与完整身份准入；确属运行期生成部分单独记真实原因/次数/耗时。不能把17个入口计数归零或安全回退本身当作性能问题已解决。之后才集中原前端/增量往返/实际静态执行/启动/F3/功能及FEX性能内存对照。
- 额外走查发现私有联网模块路径还只认CPU顶层角色，源码已覆盖原FEX嵌套与独立bbcpu嵌套角色，并保持原formal delegate拒绝优先；17项原生路径正反例通过，尚不是真实联网验证。
- 私有证据：P/evidence/bbcpu-clang-pair-20261031/result.json、P/evidence/bbcpu-patch-audit-20261031/result.json及startup-patch-audit.jsonl（P为Shared开发根）。真实游戏文件、生成C、对象、库与这些原始内容不上传GitHub。
