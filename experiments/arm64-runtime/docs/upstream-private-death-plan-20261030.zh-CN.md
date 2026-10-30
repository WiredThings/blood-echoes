# 本轮新 ARM64 构建的隔离两次死亡叠层

状态：私有单元编译/链接/签名、准确独立Bridge常量和正常输入核对已完成；关闭与开启顶点优化的两次死亡重生均已通过。原R0脚本错误、R1保护中止及后续修正的历史证据继续保留，不能算作通过。正常包不含SetHp注入；用户授权的本机代码更新已完成，正式存档、设置及缓存不变。以下准备/待审段落记录执行时序，最终结果以文末和游戏回归记录为准。

当前可用新CPU：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/upstream-private-death-cache-20261030/code/bb-game-arm64`，SHA256 `f1b5179750caa42f75fba486e7eda65d069c9456dfeba7fdca15ac21be8de042`。`verify_cpu`、实际仅ARM64、strict签名、当前renderer load edge及21个normal输入摘要全部通过。构建约6.11秒，原MemoryPolicy守护期间pressure始终1，swap前后均4585.3125MiB，无增长；开编前确认无游戏、未锁屏。

同目录`static-cache-identity-proof.json`记录真实Mach-O符号对应的文件偏移及32字节常量：Runtime=`888a438b199b74943138b7cf69f152f4bdc54bf1e45bcd259e68316dddd0ae18`，Bridge=`67cb77dd0d495cbc799a356b6d67bef3a68c18663c400fef56f4e1c30527a671`，均精确匹配新计划，证明实际编译采用私有cache身份。证据含5份源码/header、对象、最终二进制和编译/链接命令SHA链。没有现成无游戏身份CLI；此为编译常量辅证，不能冒充语义probe。正常编译数据库命令不含`-MD/-MF`，因此未产生.d，也没有编造或另跑一次编译。

已保留的旧私有可执行文件：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/upstream-private-death-20261030/code/bb-game-arm64`。SHA256为 `871918f0cbbca990f513789de39eda9329e465339875530219105a9086c07f33`，仅ARM64，ad-hoc签名strict验证通过。21份正常输入（原正常CPU、原game_runtime对象、GPU adapters/avplayer对象、新FEX/embedding/runtime归档、renderer及Homebrew动态库）构建前后摘要不变。`@rpath/libbbgpu.dylib`与本轮 `build/renderer` rpath保留。**该文件不能运行：缺少新QA cache身份，执行入口明确拒绝它。**

旧、新私有目录均有 `plan.json`、`build-result.json`、`compile-private-game-runtime.log`、`link-private-game-runtime.log`、`private-architecture.log`、`private-libraries.log`及`private-load-commands.log`。旧CPU保留且再次核对摘要不变，执行入口只接受新准确身份版本。

## 复用依据与具体差异

基线是 `5c0f2b72bb2f1153513ce018f2a26814d8c61e7e` 的干净 source。旧成功证据位于 Shared 开发根的 `evidence/private-death-native-1793068526046053000/run-1793068777443668000/death-result.json`：两次重生、耗时约103秒。旧二进制及旧对象绝不可复用。

新适配 `tests/run_upstream_death_regression.py` 固定四份完整原文件 SHA，调用点变化可完整反向还原：

1. `game_runtime.cpp` 首部 include 现有 `private_death_contract.h`。
2. `dev_travel_runtime.h` 的 `Tick` 前 include 现有 `private_death_runtime.h`，仍位于 `ArmTravel` 内。
3. `Tick` 排除瞬态读取失败后调用 `ObserveDeath(now,world,loading,player)`。
4. 写好 travel stub 后，在 `!authored` 条件内调用 `PrepareDeath`，随后仍执行原 mprotect。

现有两个测试头按字节复用；没有重新实现游戏死亡、直接改 HP、拓宽 travel 门控或添加退出码。SetHp 仍核对1.09的11字节签名，并用15字节 **guest x86 thunk** 清零浮点参数后经 `bb_arm_call`/本轮FEX调用。最多两次命令，每次要求健康状态有效、world有效、非loading且player稳定至少5次观察。

## 可执行准备命令（父任务审阅后）

以下命令从仓库根执行。`--plan` 当前已运行，只有读文件；不会偷偷启动编译器或游戏。

```sh
python3 experiments/arm64-runtime/tests/test_upstream_death_plan.py
python3 experiments/arm64-runtime/tests/run_upstream_death_regression.py --plan
```

正常 `evidence/upstream-refresh-20261030/build/cpu` 现已构建完成，可读取实际命令图：

```sh
python3 experiments/arm64-runtime/tests/run_upstream_death_regression.py --plan --inspect-link
```

这个参数只读 `compile_commands.json` 和 `ninja -t commands bb-game-arm64`。实际库含正常和old-stack-snapshot两个同源文件记录；只选 `CMakeFiles/bb-game-arm64.dir`，不能误用快照target。若尚未生成数据库，计划明确报告 pending，不能用旧命令拼凑。只生成4份源码（不编译）可用：

```sh
python3 experiments/arm64-runtime/tests/run_upstream_death_regression.py --write-overlay --inspect-link
```

它只在新的 `Shared/.../evidence/upstream-private-death-20261030` 写4份源码及计划；目录必须全新，不能覆盖现有目录。以后重跑应以 `--output` 指定另一个独立 `upstream-private-death-*` 直接子目录。

**`--write-overlay`仍不执行编译。** 复现本轮已授权的最小私有构建，应另选全新目录，直接用下列互斥模式（不能先在同目录write-overlay再build-private）：

```sh
python3 experiments/arm64-runtime/tests/run_upstream_death_regression.py \
  --build-private \
  --output /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/upstream-private-death-20261030-rebuild
```

它只执行计划内的一个编译单元和链接命令，然后进行ad-hoc签名及架构/依赖核对，没有游戏运行入口。保留正常目标所有 defines、generated headers、GPU adapters和avplayer对象、同一新 `bb_fex_embedding`/FEX/`bb_native_runtime` 与同一新 renderer CPU-host dylib；只替换 `game_runtime.cpp.o`，输出 obj/dep/链接信息/可执行文件均移到新私有目录。额外未审旁路输出拒绝。真实依赖SHA构建前后必须一致；不会调用ninja build或改写正常对象。

## 运行输入沿用原启动器

不改 Swift/receipt，不另做前端，也不使用旧 raw CPU。由已审的正常 app/harness 建立私有case并完成原准备流程，复用冻结QA输入；本轮不接THD，不读取正式账号。

旧成功实验发现，手写少量 env 会漏掉真实默认值。但此前的 `policy_environment_projection` 也仅捕获 `run_optimized.sh` defaults，**不是实际CPU环境**：`run.sh`仍计算DMEM/渲染参数，社区汉化可能创建并清理merged app0，本机准备还会补runtime环境。因此保留该函数只作边界控制，执行入口不再用其导出结果启动CPU，也不能复用已清理的旧mod view或拿原dump代替。

使用原准备产物构造原 argv：`boot-linked.bin --content-profile content.bin --patches patches.bin --app0 实际合并资源路径 --user 私有user --timeout 0`。必须保留本次真正的合并app0，不能假定是未合并原始目录。

- 新CPU为这次单元叠层的 raw ARM64 executable，`BB_GPU_PROCESS` 为已准入新normal app的嵌套GPU；CPU-host dylib同属这次正常构建。
- `BB_AUDIO=none`、`BB_ONLINE=0`、`BB_QUICKSAVE=0`、`BB_PAD_FILE_ONLY=1`，原 `BB_ARM_LAUNCHER_COMPAT=1`/`BB_RESTART_SUPERVISED=1` 语义保留。本轮不与F3混测。
- 创建仅属于本case的 `user/world-warper`，`BB_DEV_WORLD_WARPER_DIR` 必须等于其canonical路径；32字符session nonce与安全普通文件 `user/.private-world-warper` 一致。
- 场景稳定后写 `world-warper/armed` 为同nonce，复用原有限Flipper hook。只发 `death-command.txt` 的 `1`、`2`；不写 travel `command.txt`、不盲走、不攻击。
- 读取 `death-status.json`，每轮记录 `death_sequence`、HP归零、world卸载/loading，再回到健康HP>0、world有效且非loading、stable>=5。不能只看到新player地址就宣称完成；地址允许重用。缺任一证据时判未完成。
- 原600秒session上限、锁屏/严重压力/swap保护、源QA摘要与仅终止本case拥有进程的规则均保留。对照性能/F3由原正常包另轮进行，不能用这个带逐tick诊断的叠层测性能。

## 控制覆盖与尚待执行

13组Python测试覆盖：完整4输入及反向还原、任一文件多1字节拒绝、缺/多输入、缺/重复/已注入anchor、保留新FEX/renderer和编译参数、旧source/build/object/embedding/renderer/target拒绝、未知编译/链接旁路输出拒绝、shell只投影两处终端exec/错误字节及正式路径拒绝、plan不创建目录/不编译/不启动、正式或normal源码/build输出拒绝、同源old-stack-snapshot目标和重复/错误output字段拒绝、缺实际命令时构建拒绝。

旧 `test_private_death_contract.cpp` 已验证的健康上下界、签名和两序列门控代码原样保留，**本轮没有重新编译该C++测试**。这个构建适配本身没有游戏运行入口；新增独立执行入口见下节，尚待父任务审阅和真实两次卸载/重生验证。不能把旧私有CPU构建或Python控制当成游戏回归结果。

## 最新：完整原流程与单点QA转交（待审，未执行）

`tests/run_upstream_death_case.py` 默认`--plan`。它通过正常harness的准入、冻结Swift receipt、`qa_launch_clone.create(empty)`、`execution_environment`、原`benchmark_changes`取得新隔离case；在原检查之后，单独记录仅用于该case的WorldWarper目录、原有QA pad和独立CPU cache路径三项变化。原离线、静音、quicksave0不变。

3份私有shell投影绑定本轮Reviewed.app完整实际SHA，只改cwd和下一跳位置：

```text
private run_optimized → private supervisor → private run.sh
                                         → QA handoff.py
                                         → 原真实local_metal_startup.py
                                         → 原完整prepare_launch/最终runtime env
                                         → 单独准入的私有QA CPU
```

原16脚本、原app/manifest/receipt均不变。原mod创建、后台子进程wait及trap清理逻辑完整保留，merged app0在QA CPU退出前持续存在。原75/76监督语义不变；本轮不发F3。`--benchmark`使用原允许的自动化门控，不把QA变量泄入正常Play。

handoff执行原bootstrap的真实`__file__`，完整原工具摘要、imports、角色和准备校验照常。只替换`launcher_local_aot`模块自己的`os`视图，不修改全局os；一次精确`execve`代理只接受已准入normal CPU及其真实argv，随后独立复核QA CPU/source/21依赖和新DSO。仅替换exec目标及argv[0]，argv尾与最终env原样；记录真实DMEM/渲染环境和实际merged app0。不是把QA CPU冒称normal role。child dispatcher代码复制到新Shared case，避免QA进程读取文稿目录引发TCC弹框。

CPU cache：现有`make_cpu_cache_build.combine`算法先回算验证normal完整parts，Runtime保留真实同newFEX身份`888a438b…`；Bridge替换QA game/travel摘要，加入两个QA头，得到`67cb77dd0d495cbc799a356b6d67bef3a68c18663c400fef56f4e1c30527a671`。新增私有`source/bb_cpu_cache_build.h`由quoted include优先取用；测试另用全新CPU cache路径，不继承/更改A/B cache。

执行仍调用原`driver.sample`来复用NativeObserver、原MemoryPolicy、输入、源存档摘要、app前后seal和owned cleanup。DeathPolicy先委托保护，再处理最多2条命令；要求HP0、world down、loading、稳定有效恢复四类证据。允许地址复用及≤5秒命令ack滞后；拒绝时钟回退、错误顺序、CPU换代/重叠、缺证据和超时。使用独立完成原因结束，因此不会伪造`sampled`/FPS结果；只增加`functional_death.passed`，且必须源存档不变、原app seal前后有效。

父任务完成独立审阅、且主线游戏测试结束后，才可批准新身份QA单元重编，再执行：

```sh
python3 experiments/arm64-runtime/tests/test_upstream_death_plan.py
python3 experiments/arm64-runtime/tests/test_upstream_death_case.py
python3 experiments/arm64-runtime/tests/run_upstream_death_case.py --plan
# 未来单独放行以后才执行：
python3 experiments/arm64-runtime/tests/run_upstream_death_regression.py --build-private
python3 experiments/arm64-runtime/tests/run_upstream_death_case.py --execute --reviewed-code
```

执行入口审阅后，父任务已单独批准并完成新身份的一单元native重编；首次执行及其帧表头中止、后续严格续用准备见下节。

## R0帧表头修正与严格续用（最新）

父任务R0：`P/evidence/upstream-private-death-run-1793349284558147000`。错误是`missing bounded frame header`：CPU转交已发生，文件刚创建而CSV头尚未完整发布，DeathPolicy比原driver更早读取而抛异常。修复只将未armed时的`read_tail ValueError`视为暂无完整rows；原保护和总预算不变，零字节及partial header用真实文件反例验证。

`--resume-from`只接受这个R0及该精确pre-scene异常。只读重建固定ALLOWED_CHANGES的after值，再由原`execution_environment`和`benchmark_changes`逐字复现旧changes/allowed；不补写旧证明。旧完整八角色身份来自死亡启动前已存在的真实`N/package-reviewed-result.json`，核对214c组包/5c原生身份、八角色SHA和arm64、SOURCE-PARITY SHA及记录早于旧handoff，不能声称旧death result保存过这些字段。

还要求三root同tag、canonical/owner/700模式、原两个nonce及session marker一致、旧pad空且无pending、frames已归档、3config与冻结源SHA一致、mods/patches结构及字节一致、全部私有/source savedata字节相同、world-warper目录为空且原私有marker匹配、没有旧SetHp命令。旧handoff必须对应当前真实CPU/hash/Bridge及未改argv。新run根、新CPU cache namespace及新MemoryPolicy基线，既有world目录只在这个明确resume分支复用。

每周期仍从SetHp请求起限180秒：第一次完整重生后连续稳定5秒再发第二次；第二次连续稳定10秒，必须同一CPU仍活且帧时钟在确认截止点之后真实前进一步。稳定期加载/死亡/health失效、CPU重叠/换代或消失即拒绝；已armed后CPU消失不等旧status更新。

只有exact COMPLETED、两周期及5/10秒证据、第二次真实帧推进、源存档不变、双seal和真实handoff全部成立，才设顶层`functional-checked`，并保留`raw_driver_status`。保护中止或旧失败永远不转成通过，也不生成FPS结论。

检查结果：deathCase18、deathResume11、deathPlan14、既有normalGameResume23，共66项通过；本修订阶段没有编译或执行游戏。冻结入口SHA：`79e369316c9886e4aeb7bb855a4b55a0fff7481286f6db59037ee9754fc3d1ee`。后续父任务命令：

```sh
python3 experiments/arm64-runtime/tests/run_upstream_death_case.py \
  --execute --reviewed-code \
  --resume-from /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/upstream-private-death-run-1793349284558147000
```

最新QA语义校正：R1在未scene/未death时被原swap增长保护停下，不计通过，也不放宽保护。死亡case现在保持来源CPU cache模式：来源缺省/空值分别保持缺省/空值，仅来源原本非空时才改用新run的独立私有Bridge命名空间；绝不以字符串`0`代替关闭。最终handoff同时约束并记录实际presence/value，准确`67cb…` Bridge/header保留。旧R0 on-mode证明仍只读完整校验，再允许新run明确恢复source off；R0/R1证据不改。此为测试语义对齐，**不是已证实的内存修复**：已有静态反证表明该路径只尝试读取不存在的缓存并保留普通JIT，哈希峰值约1.75MiB，不能据此归因为GiB内存增长。最新相关Python检查68项通过（20＋11＋14＋23），未编译或运行游戏，保护不变。

父任务已完成OFF死亡回归R2（`upstream-private-death-run-1793352135669906000`）：两次真实重生通过，稳定窗口约5.09/10.96秒，pressure1、swap零增长，源与seal检查通过。为验证用户授权的顶点优化默认开启，新增`--vertex-on`：原环境先经完整原准入，再仅将`BB_MTL_RELAX_VERTEX`缺省/0改为1，记录唯一用户授权变化，最终handoff精确要求1。不带flag保持来源；与`--resume-from`互斥，必须创建新隔离case。CPU cache来源语义及所有保护不变，无native重编需求。新增相关检查通过48项（23＋11＋14），ON游戏验证由父任务在新GUI组包后单独进行，不能把OFF结果冒充ON兼容性。

ON验证已完成：`upstream-private-death-run-1793354249971597000`，正常包0.2.59 / `5b186b81`，显式`--vertex-on --seconds 1200`。两次HP归零、world卸载/loading及健康恢复均有证据，稳定5.15/10.06秒，第二次期限后帧推进、同CPU存活；pressure1、swap净增-1090.31MiB、源QA存档不变、正常包前后签名通过。未放宽原内存保护，完整耗时448.27秒包含新case冷准备。实际CPU cache缺省语义与来源一致，私有诊断未进入正式包。随后本机代码已更新，Release未发布；详见[实际游戏回归](upstream-game-regression-20261030.zh-CN.md)。
