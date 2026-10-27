## 2026-10-27 最新：按变化复用已交付，白模专项诊断暂缓

本机原子替换0.2.55（源码579df61d），Bundle ID不变，旧0.2.54代码留档；985文件一致且deep/strict签名通过。没有启动正式GUI、修改正式存档/设置/现有缓存，Release不更新。351项定向控制、原/候选共十次静音隔离进场完成。最终两轮暖启动84.01/79.20秒，对照本轮0.2.54稳定193.83秒，平均缩短57.9%；源/快照/目录各28852项事件历史复用且读取内容0，231库built=0、持久审计命中。场景FPS约50基本不变，footprint平均5915.28MiB对照5907.73MiB；短时未见显著持续增加，不等于长期跑图证明。

残余如实保留：最终模块准备约26秒、完整PSO约21秒。真实headless验证前后仅startup-entry.json字节改变，严格调度证明仍不发布，不宣称PSO预热已跳过，不为提高命中放宽验证；typed companion的不同provenance/integrity字段未证明可安全合并。它们后续另评估，不持续盲测。正式布局当前包未重做菜单/联网/死亡/F2/F3与视觉验收，本轮仅启动改动，不把整体目标标完成。

用户最新明确：本版本不重点推进新增白模/缺图日志或自动视觉诊断；已有保护保留，真正复现再保留现场debug。原三个signal/context接口仍是上游既有兼容缺口，不恢复整批前置门槛。详情docs/change-driven-startup.zh-CN.md与evidence/change-driven-startup-20261027。下方“当前”均为历史记录，以本段为准。

## 2026-10-27 最新进度：日常启动增量校验已交付

按本轮授权，两项增量快路径已默认接入原启动器，272项定向测试、原/新各冷暖四轮真实进场验证通过；本机已更新0.2.54（源码780afcd0）。暖启动349.89→277.17秒，资源检查160.89→100.18秒；源/快照/目录各28852项内容读取为0，原完整元数据/映射/产物/最终管线键校验保留。冷启动444.71→465.38秒，不宣称首启收益；暖场景FPS基本相同，footprint未增加。正式存档、设置及原有缓存不变，旧代码有备份，Release不更新。详docs/startup-preparation-progress-and-reuse.zh-CN.md及incremental-startup-20261027证据。

本次只完成启动快路径任务，不把整个ARM迁移目标标成完成。历史三个signal/context接口已取消整批交付前置条件，需求驱动且保持明确诊断；下面历史门槛不再适用。后续用户手动跑图/战斗白模和功能体验不以本轮站桩替代。剩余元数据耗时及本机编译阶段可另行评估，本轮不扩大优化范围。

## 2026-10-27 双增量校验实现，进入集中冷暖对照

资源视图和静态目录均接入默认文件摘要提示，正常重启只核完整集合/原mod映射/文件身份和mtime/ctime，变化文件才读SHA；快照仍readonly且完整集合验证，代码准入/格式/编译兼容身份/runtime完整键不变。坏提示完整校验，坏快照/索引保留并另建retry。新模块已加入正式frozen工具闭包，不留漏打包。旧解析器迁移仅精确已知SHA+九函数及边界常量语义+其余parser/enum字节不变。定向反例覆盖同大小同mtime重写、替换inode、增删、损坏提示、链接/权限、晚变更及解析器变化拒绝旧代。

当前272项Python控制通过；此时尚未完成实际冷暖/内存对照，不改本机安装包或Release，正式数据和旧缓存保持。接下来只做一次集中原0.2.53/候选0.2.54的独立冷暖Play，无Cua和截图，使用相同QA配置/存档副本。不是用同supervisor F3证明独立Play快路径。

## 2026-10-27 阶段进度条本机交付（当前）

0.2.53（59a261a7）已原子替换本机，保留0.2.52代码备份；982项payload一致、整包deep/strict签名通过。原SwiftGUI全套124项/跳过1/失败0，新增8项进度检查，实际冷启+F3日志回放见两次完成/两次native开始/一次重置。只重编GUI及同源组包74.49秒，原生9463文件源闭包和CPU/GPU/双worker/net产物摘要未变，压力1、swap不增，无游戏/正式GUI、存档/设置/缓存访问。0.2.53尚未实际打开GUI或运行游戏，不能把0.2.52的实际游戏结果冒称53重验。代码同步开发分支，Release和公共发布文档仍不更新。

本轮实际实现仅阶段百分比。日常资源视图/静态索引的元数据失效和增量摘要快路径尚未实施，仍保留在docs/startup-preparation-progress-and-reuse.zh-CN.md，不能宣称启动耗时已下降。

## 2026-10-27 启动准备进度与重复校验评估

按用户建议，在原launcher-macos PlayBar接入既有准备日志的阶段百分比，失败不冒充完成、F3重置、100仅代表准备结束，三语言已接。8项Swift定向测试通过，无游戏/Cua或正式数据访问。正在完成原GUI统一编译与同源代码组包；未变渲染/准备算法和缓存。

日常启动无需重复全量读取的候选已记录docs/startup-preparation-progress-and-reuse.zh-CN.md：先完整文件清单/身份/元数据失效检查，变动才重算SHA，保留readonly快照、完整路径/格式/最终资源与管线键校验，必要时复检。当前source-view已有快照命中仍读原文件两遍及快照一遍；static-catalog命中前仍全游戏SHA。该增量方案尚未实现，不能声称本轮已缩短启动时间，也不扩展到新长期方向。

## 2026-10-27 本轮自动回归完成，等待人工游玩（最新状态）

当前本机保持0.2.52（8f6302e0），退出码74修复包的全新GPU缓存首启/F2/F3已完整通过：521.27秒，410.06秒进入场景，新建检查点、原supervisor离线重启、自动继续及后续场景均实测；swap净增164.81MiB，最高压力2，结束压力1，源QA存档和前后整包deep/strict封口不变。先前的保护停止仍保留失败记录，没有放宽保护。暖轮同链310.56秒、swap+84.94MiB；私有SetHp两次死亡卸载/重生103.11秒、swap+524.63MiB，也已通过。全部无Cua、截图或盲走攻击，静音，自动游戏进程已清理。

209项Python3.11定向控制、17个当前正常FEX guest退出用例、6个原supervisor协议、17目录控制、原travel钩子/状态及私有SetHp契约检查通过。测试叠层不进入正式包。实际全新首启仍慢：资源视图132.93秒、静态目录247.81秒；首次F3本机库准备62.51秒，game preparation严格调度复用0.73秒。记录为后续启动耗时残余，不在此次功能回归中扩大优化，也不声明平均FPS收益。

下一步由用户在Applications的0.2.52验收实际Play、菜单/中文/Dock、跑图加载和战斗贴图；最新THD和长期探索内存本轮未重测。正式存档、设置和缓存未动，无Release或公共文档更新；开发分支同步内部测试证据。整项目尚未宣布完成。下方“冷轮复查运行中”均为历史中间状态，详情evidence/preparation-automatic-regressions-20261027/summary.json。

## 2026-10-27 自动回归收尾（当前）

退出码74修复后的实际0.2.52包已在隔离CLI完成F2新检查点→原supervisor离线F3→新CPU/GPU→原自动继续→场景；310.56秒，swap净增84.94MiB，最高压力2，源QA存档及整包前后封口不变。不是物理键盘输入测试。当前在后台应用已退出后，用全新GPU缓存再跑一轮首启/F3，先前冷轮保护停止保留incomplete，不用暖轮代替。

自动死亡工具复用原FlipperUpdate钩子及旧工具已核验的1.09 SetHp入口，用FEX guest thunk显式清零XMM0传浮点参数，只存在开发测试源叠层；不改正式CPU/游戏文件，不直接写血量。两次均实测HP归零、world卸载、loading以及新角色满血稳定；103.11秒，swap+524.63MiB，最高压力2、退出后1，源QA存档未变。无Cua/捕获/盲走攻击，不宣称战斗白模或联网验收。开始的两次控制器错误（漏原shell默认项、读未刷出的CSV头）保留失败回执，修复后通过，未改正式渲染逻辑。

11套Python3.11回归188项、源码组包8项、闭包13项共209项通过；17个当前正常FEX退出码fixture、6个原supervisor协议、17目录门控及原travel钩子/状态测试通过。初次系统Python3.9不满足3.11要求的失败另存，不放宽兼容条件。最新真实首启、菜单/中文/Dock、跑图/战斗贴图留给用户；最新THD与长期探索内存未在本轮重验。当前本机0.2.52（8f6302e0），未发Release或改公共文档。详情evidence/preparation-automatic-regressions-20261027/summary.json。

## 2026-10-27 退出码74实际启动故障修复与自动验收重开

用户实际正式启动在fixed-helpers阶段失败，原生原因是“fresh empty Shared source scratch required”，外层被误报为结果记录数量。先前最终布局测试仍使用Shared/state，因此没有覆盖正式数据路径；此前“没有新源码阻塞”不能覆盖这次已证实故障。`8f6302e0`补齐PSO/模块/typed companion的真实bundle角色→正式work/GPU namespace数据路由，保留原QA门控，错误报告保留原生原因。17原生目录反例+7错误协议+17响应+19正式profile控制通过。

真实0.2.52代码在开发专用P/state之外的Application Support布局完成2次原生调用，8固定管线及14平铺库完整准备，11.62秒，压力1、swap不变；模块在通过目录/nonce后继续拒绝缺少kind的非法包，不伪造shader输入。增量只重编两worker，正常2a050545构建的CPU/GPU/net主产物摘要未变；不是声称与旧安装包原始字节完全相同。0.2.52已原子替换本机，保留旧代码备份，正式存档/设置/缓存未动，无Release/公共文档更新。

用户已进一步授权所有无需Cua连续操作的自动测试，含测试指令触发的死亡回归；菜单、跑图和战斗白模留给用户。当前启动/F2/F3冷轮在400.84秒、进入场景前触发5秒>1GiB swap保护，未通过；同隔离进度暖轮已启动，保护不放宽。用户关闭飞书后当前压力1。死亡测试将复用原有SetHp游戏线程、签名与双次上限，通过FEX调用原游戏函数，不能拿旧Rosetta工具结果替代FEX。详情evidence/formal-preparation-path-fix-20261027/summary.json。

## 2026-10-27 干净源码构建与纯源码组包完成（当前）

最终逐项审计见`evidence/completion-audit-20261027/requirements.json`（18项）。六项实际路由、三个保留缺口默认状态、本机准备与runtime命中、源码打包均按证据核对；19项F3失效/复用与20项本机准备作者控制复跑通过，无新游戏/GUI。没有发现新的确定源码阻塞，但不宣布整体完成：最新包连续死亡、THD及完整菜单/中文/Dock/视觉和长探索内存仍未在本夜全面重验，短时静态对照不替代这些证据。等待用户早晨体验后集中补验及按授权发布，不重新启动同源长测或扩展长期任务。

原`tools/macos/package.sh`现已接入正常ARM64构建回执，不再默认调用旧Rosetta producer。组包前后核对真实source snapshot、7类原始产物、GUI/layout摘要和明确依赖根；21项反例控制通过。实际原入口用已完成的2a050545回执在全新目录组包45.97秒，真实schema2/8角色/整包签名通过，压力1→1、swap8904→8904MiB，无游戏/GUI/正式数据。构建机须使用Python3.11+，可通过BB_PACKAGE_PYTHON显式选择；系统3.9会在读取输入前明确拒绝。此项仅修正开发打包入口，未再次替换本机包或更新Release。

`2a050545` 的新Gitless源码归档已完整执行正常构建driver：renderer/双准备helper/独立GPU、自动生成CPU桥/FEX、独立bbnet、原Swift启动器、实际metadata封口和架构/签名核验，503.41秒。8类目标全部薄ARM64，压力始终1，swap8920→8904MiB；全程无游戏。外部依赖是明确选择的既有前缀，未宣称全部依赖从零重建。

该回执直接送入纯源码assembler，57.75秒完成最终app；真实标量schema2、8个冻结角色、CPU/GPU同源库、完整ARM64/相对加载边和deep/strict签名通过，982个payload条目。组包没有旧QA/正式app、游戏、存档、缓存或账号输入。另移动新目录仅使用包内Python/SQLite/原生hashlib与真实worker复查，代码普通文件摘要和签名不变；没有GUI或游戏启动。此前`5dacb719`同样通过73.43秒独立组包及1.32秒迁移标量检查，两轮不混称游戏验收。

实际失败均留证：Python独立wrapper身份、裸LC_RPATH、共享子图重复遍历、合法SDK误拒绝、外壳签名校验顺序，以及额外Xcode构建模式生成通用GUI；全部修复后重验，未降低来源/摘要/薄ARM64/加载边/目录准入门槛。33构建、31依赖、8组包、11封口、19正式profile及13代码闭包定向检查通过；它们是定向检查，不代替游戏测试。详evidence/clean-source-delivery-20261027/{summary,normal-driver-receipt}.json。

本机保持已通过游戏与F2/F3验收的340e9c86，不再替换为仅组包验收的新产物。正式存档、设置、缓存、钥匙串仍不动；原生代码构建/组包这项残余已收口。目标仍active，下一步等用户体验反馈，再按授权更新Release与公共使用/构建文档；届时公开构建说明应指向本次正常ARM64构建器与package.sh回执入口。现在不再安排同源重复游戏长测，不扩大长期优化。下方旧缺口以此段为准。

## 2026-10-27 源码交付收口（不改变已安装包）

本机仍为已验收并安装的340e9c86，未再启动游戏或修改应用；Release与公共README/发布文档仍待用户确认。源码构建修正4d634fc3仅移除编译时对旧QA运行目录“必须存在”的误要求，原默认路径字面量和运行时隔离保持；18项控制通过。

精确Git归档在全新目录完成正常renderer236 TU、双helper及独立GPU主程序312.05秒，FEX/CPU及缓存生成器71.73秒，独立ARM64 bbnet73.09秒。全部为当前跟踪源码重新编译；依赖包与监控工具复用，不把普通外部依赖当作源代码缺失，也不声称全依赖从零重建。三段内存压力均1，未运行任何游戏/网络客户端或读取账号。

源码闭包写入函数aac9cdca复用原HOOK/日志投影、API/import闭包与冻结算法。13项新作者控制+原QA15项回归通过；另从新Git归档实际生成61文件（16脚本、40工具、2 loader、bootstrap、2 metadata），拦截旧Shared QA目录全部读取，尝试数0。仅代码候选，不造NATIVE/GAME-PSO能力或已准备成功声明。

**仍有真实源码交付缺口，目标未完成：**现有最终组装入口仍依赖旧QA app/旧正式app；需将纯代码闭包、正常native/layout、原GUI壳/Python依赖和原重定位/8-role封口串成纯构建输入入口。另需用现有正式worker scratch完成真实标量能力查询，避免freeze_inputs回到旧P/state；把已单独构建的ARM64 bbnet接入正常编排。不得伪造game/boot或引入QA存档。详情见evidence/clean-source-delivery-20261027/summary.json。完成这些源码接线后只集中验证最终包，不重新逐小改做长游戏测试。

## 2026-10-27 本机交付检查点（当前状态，覆盖下方历史状态）

最终源码 `340e9c86` 已组入正式目录布局，并在隔离 CLI 跑完原条件冷暖、F2新检查点、离线F3 supervisor、自动继续、整包pre/post签名检查。空缓存首轮F3从343.24秒降至84.69秒，其中game preparation严格调度复用0.82秒。源QA存档摘要不变；测试全部静音，无Cua、录屏或盲走攻击。实际包内106个Mach-O均为ARM64。

自动本机准备确有运行时复用：完整逐帧delta求和（不能取末行当累计）显示初轮GPU-ready后390次编译/237次源建库，暖轮6次/4次，Local11次且失败0、strict PSO13/13命中；最终F3 replacement为0次编译/0次源建库、Local11次、strict13/13。GPU-ready计数包含加载/菜单，并非全为可操作场景；固定场景末30秒compiles=0，不外推全游戏零编译。

同镜头同1367 draw对照：Rosetta48.81/48.02FPS、合计footprint5716/5647MiB；ARM48.88/46.55FPS、5828/5940MiB。暖ARM前/中/后30秒49.34/48.76/46.55FPS，后段有波动，无稳定提速声明。两边最高压力2；ARM两轮swap净变化+499/-5MiB，短时未见持续累积，不代表长跑图内存证明。完整准备首轮443.20秒、暖轮345.23秒，核验等待仍存在。

按用户夜间条件授权，已先保留旧应用代码，再原子替换 `/Applications/Blood Echoes.app`；源码标识340e9c86，Bundle ID保持。987文件逐项一致、安装后deep/strict封口通过；正式GUI未启动，正式存档、设置、钥匙串与缓存未访问/更改，QA进度未采用。旧QA GUI与测试进程均已退出。另生成本地预览zip，SHA72ca0f48…dc8e1，不含游戏、存档、缓存、账号或本机metallib。详evidence/night-acceptance-20261027/{summary,matched-performance}.json。

本轮自动游戏测试已结束，下一步等待用户早晨体验；**目标未宣布完成**。战斗/新区域/联网全功能本夜未重新验收，完整依赖仍复用既有前缀。Release、标签及对外发布文档未更新，必须等待用户确认后再执行；开发分支源码/内部进度继续同步。下文旧QA、未替换和未推送状态均属历史。

## 2026-10-27 05:25 当前验收状态（覆盖下方历史状态）

源码接线与正常 ARM64 构建已完成；`78c63859` 已推送开发分支，后续字节码修复与整包验收提交待一并同步。实际正式目录布局的隔离 CLI 已完成新 F2 检查点、原离线 F3 supervisor、新 nested ARM64 CPU、自动继续和回到场景。实际运行前后 whole-app deep/strict 签名均通过；模块 helper `-I` 忽略环境变量造成应用内新增 pyc 的问题，已用 `-I -B` 修复并实际回归。原 QA 源存档摘要未变，正式数据未访问。

本轮首个空缓存场景419.18秒就绪，F3到场景343.24秒，压力最高2、swap净增599MiB。首轮 game PSO 输入为空，未发布完整准备调度提示，第一次 F3 因而重做准备；这是已观察到的启动残余，不隐去。先前已具备完整输入的 QA10 同 supervisor F3 41.11秒、game preparation0.76秒。两种条件不可混称性能改善。

现在进行原0.2.51 Rosetta与当前正式布局 ARM64 的同条件冷暖/内存对照，不使用 Cua、录屏、盲走或攻击。参考仅复制正式应用代码，使用 QA 存档/配置的新副本；两处 XML Note 说明文字与未签名 CLI 的临时签名仅在参考副本统一，实际补丁指令与原版代码不变，变更单独留证。尚未替换本地应用，Release和对外文档均未更新。无问题后按用户最新授权只替换本机，待早晨人工体验后再发布。目标仍active。

## 2026-10-27 正常构建已完成，进入整包验收

原生接入源码已提交 `0cbd1368`，复审保护修复 `7b9dea01`；尚未推送此批，也未更新 Release。正常构建从 tracked Git archive 加三个明确的已提交修正文件生成，未混入并行 Python 工作树。236 个 GPU TU 与 normal main/双 helper 编译 318.02 秒通过，CPU/FEX 与 cache generator 73.46 秒通过；两段压力均为 1、swap 没有增长。实际 ABI/layout、薄 ARM64、加载边、strict component 及 scalar schema2 封口已通过，重定位/签名后原输入不变。依赖仍复用现有前缀，不能称全部依赖从零构建。

Python 正式路径与默认准备链正在最后收口，随后使用当前原 Swift（116项/1真实iCloud跳过/失败0）组一个统一隔离包。Formal ID 的 staged GUI 会读原正式 Defaults 并执行存档初始化，因此隔离正式角色验证只从精确 CLI/复制的 QA 进度启动，绝不开 staged formal GUI。正式应用仍未替换；实际 F3 快路径、冷暖/内存、原条件与正式角色动态验收尚未完成。

## 2026-10-27 03:40 集中验收补证与最小正式接入

QA09 原始自动驱动回执保留 incomplete：旧末尾日志读取器漏掉了 F3 完成标记；独立复核实际新检查点→原 supervisor 离线重启→新 CPU→原自动继续→后续场景帧已证实路径运行。新增量读取器重放该完整实际日志通过，但尚待新包完整实测，不把重放当作新实测。实测另发现 F3 重做 source-view/static 准备数分钟；同 supervisor 会话调度复用候选保留每次 Local/组件/metadata SHA 校验，现已补不同目录同名脚本、CWD及 SOURCE-PARITY 反例。

正式接入只需真实外层 bundle/已知 native role + 已有冻结摘要/实际加载边，以及原 CLI/数据根映射；不要把巨大 runtime-scope 原型所有字段或新票据系统增加为交付门槛。QA 原保护分支保留。工作元数据移至 data 独立目录，原进度和旧缓存不迁移；正式 GPU 使用原根下新增 arm64-native-v1 命名空间。当前正在集中接源码，尚未替换 /Applications，Release 和对外发布文档未更新。

原生 session-work 与 GPU namespace 的作者文件系统控制实际编译运行通过；Swift 两 nonce 每次 Play 建立并原 supervisor 内继承的 6 项定向测试通过。均不替代整包验收。

## 2026-10-27 夜间授权与当前验收

夜间实测补证：使用原Swift最终配置和新隔离副本的第一次启动706.95s时因5秒swap新增>1GiB停止，未到场景。按既有授权重启约1464MiB chronod后，同一QA副本续测325.30s见场景并采90s，末30s48.87FPS、GPU19.22ms、p95 21.21ms；合计footprint中位5809MiB（共享页可能重复），swap净增1550MiB，严重压力仅1次观测，未持续。5931帧中现场compiles/source_calls=0、strict8/8、11Local调用；源QA存档摘要保持不变。仅该静态场景采样通过，不是匹配Rosetta/旧新版、长稳态内存或全功能验收。正式应用尚未替换，Release/发布文档未更新。包内metadata闭包源码与作用域纯数据候选已完成143项Python控制，native/正式作用域接线仍未完成。

用户最新指令：自主继续静音测试验收，全部必要检查无问题后可替换本机应用；暂不发布 GitHub Release，待用户早晨体验确认后再更新 Release 和对外文档。此条覆盖下方历史中的“禁止替换正式应用”，但不放宽正式存档、设置、缓存及 QA/正式进度隔离。目标仍 active，未宣布完成。

当前真实场景测试由原 Swift 最终环境 receipt 驱动，使用新复制的 QA 存档、配置和缓存；不使用 Cua 或录屏。原显式 benchmark 入口要求快速存档关闭，已单列为测试控制差异，不冒称 F2/F3 功能验收。场景与冷暖/内存验收尚未完成。

本机替换前仍有源码阻塞：QA08 的 Shared-only 运行作用域、原入口会话建立和模块元数据 helper 的包内闭包。不能直接把 QA bundle 覆盖为正式 app，也不能仅删除路径校验。详 docs/qa08-local-promotion-admission.zh-CN.md；有限 helper 闭包修正并行处理中。若未收口，保留旧应用可用，不发布不稳定替换。

# 当前生效计划：自动闭环收口，三项上游缺口不阻塞交付（2026-10-26）

已读取当前系统目标、源码和最新进度。2026-10-26本轮发现系统卡残留blocked；已按用户继续指令点击原卡“恢复目标”，API回读active，正文未改。源码已有进展不应被旧状态阻断。用户最新要求取消“三项全完成后九项才可整批接入”的门槛；未重建/清空目标，也未标记完成。下方历史中的该门槛不再执行。

最新收口（本轮）：Git提交`8ec54a30`的干净归档已实际完成正常renderer235TU+双helper构建（311.83s）、全新FEX/CPU/cache-generator构建（69.81s）及同源码完整layout封口；两个helper实际加载同一DSO且能力通过。正常构建仍使用既有依赖前缀，尚未证明全新依赖构建，正常产物已通过原schema1 CPU清单及严格组件准入，并装入新QA包07；原GUI的实际准备/运行验证正在进行。详docs/normal-source-build-checkpoint.zh-CN.md。

同App暖启动有界运行520.93s，约304.88s进入原生主进程；538次Local/28次helper hit、0miss、0fatal。压力最高2，swap总增加516.06MiB；可操作场景与FPS未验证，不能叫内存验收通过。测试已正常结束，仅保留一个QA启动器。暖PSO分项409个recipe均为严格archive hit且0编译，不能把305s全部归GPU worker。新最多16项共享Device候选已实际编译并测17条真实recipe/29个artifact：函数/descriptor/key/archiveSHA完全一致，暖batch4.19s vs single4.42s仅单对，RSS峰+7.33MiB/foot+4MiB为临时进程开销；原压力/swap不变，三个无Device失败反例通过。GS不在本轮样本，完整409/501仍待集中验证。37工具闭包已接候选模块，旧组件仍单项fallback；新QA07已接入经过实际原生对照的batch main，正式应用未改。

最新唯一QA为 `Blood Echoes ARM64 GUI QA 1793029386377677000.app`（包07，SHA `4bd168b4a406b4414f7668a2fe8430490a660f97ace7473e2118efdada525878`）。旧06已正常退出。新包保留原UI与脚本，使用正常构建CPU/renderer/module及单列的batch-main delta；原加载边、签名、layout和组件loader均通过。本轮原GUI启动验证已结束：914.88秒见原生主进程、915.66秒见GPU；409项准备完成（297.49秒），538 Local/11 helper hit、0 Local miss、无Fatal日志。全程1128.17秒由root结束；swap净增249.56MiB，曾严重压力约0.51秒，未触发持续保护。没有可操作场景/FPS验证，不能称性能或内存验收通过。下方package05/06段落均为历史实测，不是当前运行状态。

下一收口：真实cold调度发现844个worker进程，已以最多16项元数据页改为先strict再record，保留1227次实际recipe验证；8项新增及69项既有纯CPU控制通过，实际native收益待集中验证。正常GPU wrapper去历史模板接线中：复用原native_metal的GPU专用封面/metadata/签名helper，不另造前端；新正常bb-gpu target已从全新build实际构建（317.49秒），配对CPU/FEX（70.04秒）、layout和终版stager整段亦通过，正常wrapper已实际生成；尚未组成含当前Swift条件出口的新QA整包。临时依赖VMA config缺失造成一次重配置失败已保留证据，已从固定3.4.0源码在新开发前缀重建header-only package，原头SHA一致；依赖仍未全量从零构建。

2026-10-27 最新QA08已组包（SHA `6ee1bd8f…098be`，47,510,788字节），旧07已退出，唯一08为 `1793033361727444000.app`。Swift ARM64 Release全套114项/跳过1真实iCloud/失败0，新增14项QA出口全过。原UI已真实写出92个BB/MTL条件及4个保护开关全1，producer/sourcePID/launchID与本次case对应；84.12秒条件采集由root明确结束，未声称游戏/性能通过。原UI条件的CPU消费器与自动bench接线仍未完成，不能继续用旧固定fixture取代它；之后统一场景、冷暖、性能/内存和功能回归。Shared可移植准入及全部依赖fresh构建仍是实际源码/交付残余。当前无游戏，仅08启动器idle；目标active。

验收前新增：完成全启动器UI→配置→脚本→当前ARM/Metal消费对账，修复原生必需宿主误作可选、Rosetta误门控、未支持renderer入口、菜单零副作用不匹配差异和旧GUI组包；详docs/launcher-feature-audit.zh-CN.md。先收口这些源码，再组当前隔离包集中验收。

1. 三项归上游与正式 Rosetta 既有缺口：保留两个默认关闭的门控候选及测试；完整sigreturn停止扩展。27/282不是游戏命中证据；未知导入仍明确诊断，不能隐藏统计或零返回。接口代理已停止 owned producer/417 专项，收口其未启用成果，不回退六项。系统目标正文现已由用户原地更新并回读active。
2. 原六项独立核对正确性和集成状态。主线仍是原启动器兼容与默认自动扫描→本机增量编译持久化→runtime复用，动态缺口正常捕获，不手工逐批补输入。
3. 已接原 final-exec 自动静态入口、模块controller与GS同队列接口；新 Mod/汉化 source-view 复用原合并算法，实际 APFS clonefile 作者正反例通过，不放宽原严格reader。36个准备工具与2个原parser已纳入冻结闭包。
4. 新编译/暖命中companion封口已接源码并有CPU/实际TU控制。235 GPU TU与两helper已同源-j1构建（320.3秒，无game/GPU，swap未增长），ABI与36工具/2原parser的隔离code stage亦已生成。typed消费原生10项通过，异常路径JSON移动后再次抛错已修复；仅重建main、保持同一DSO/ABI并生成新组件清单，旧consumer不用于最终QA。原入口真实全量静态扫描已完成（28852文件、390.9秒、无游戏/GPU），不代表完整PSO或runtime命中。FEX CPU/生产host已按当前D重建，菜单修复也进入真实depfile与cache身份（47.09秒、无swap增长）。Swift40/stage14/菜单7定向控制通过，最新整包已生成（arm64-ui-audit-20261026-06），41项Swift/14stage/7菜单控制及包内代码/资源/原生组件冻结核对通过；正在使用原GUI验证默认准备与运行时闭环。当前真正未收尾是整包源与产物核验、原入口首启/冷暖/失效/真实命中及长帧/启动/内存验收；随后集中匹配性能、最新包功能回归、干净检出构建和交付。此coherent构建不含FEX CPU，不冒称clean checkout或可移植交付。

本轮QA实际启动补证：原UI的UserDefaults与Shared数据目录准入已对齐，41项Swift及7项偏好隔离控制通过。当前整包为`arm64-ui-audit-20261026-05`；原入口日志确认1.09、快速存档、六文件社区汉化和FEX/Metal设置均按QA偏好消费。默认首启已实际进入完整资源扫描/关联，游戏进程尚未启动，不冒称runtime命中或性能验收完成。Cua已停止，单份隔离运行由原严重压力/异常swap/锁屏保护监控。

首启实测修订：package05在1500秒时间预算内成功发布静态READY，尚未启动游戏即由测试保护器停止；压力最高警告级、swap未增长，不能叫游戏崩溃或完整闭环通过。已确认包内Python缺`_hashlib`导致慢SHA实现；复用SQLite闭包补齐受限原生哈希模块，9控制和新副本原生加载/摘要正控通过，64MiB微测约450→2215–2903MiB/s，额外临时峰值约2.3MiB。源码`f18bbdd0`、`dd6a9640`已推送；package06实际重新跑原GUI首启，完整启动提速/runtime命中仍待验证。见docs/packaged-python-hashing.zh-CN.md。

package06完整准备已通过原GUI自动执行，409个既有QA输入实际native-ready，65次严格archive命中；渲染器启动初始化538次Local库命中、11次helper库命中。随后游戏加载在5秒内swap新增>1GiB，保护器停止该次运行（期间短时pressure4），不是格式崩溃；不冒称空shader缓存、场景/FPS或内存验收完成。用户已关闭其它App并授权继续，旧05 QA已正常退出，仅留06；当前同缓存暖启动在保护器下继续，Cua已停止。最新证据evidence/original-gui-prepared-runtime-20261026/summary.json。

正常构建源码入口也已参数化，真实Clang桥生成及FEX配置通过；18项配置/身份根控制通过。正常metadata target实际构建运行，215原生ABI字段一致且无renderer加载边；完整layout来源/fixture封口、全新依赖与CPU/GPU/helper实际构建/新加载边和最终打包仍待收口，不能叫只剩验收。

`aad1668f`及其前置源码检查点已推送开发分支；本轮继续正常CMake构建入口和隔离真实运行验收，收口后同步新增源码。不更新正式应用/Release，不动正式存档、设置和缓存。画质、持续内存及保护规则不变，目标保持 active。

以下为历史进度；相冲突的三接口捆绑门槛、旧额度停止线和旧暂停状态均以本节为准。

## 2026-10-26 后续源码收口：真实入口与剩余交付

本轮属于实际进展：开发主批 `3e2141ba` 已推送；等待修复 `c0815c81` 已提交，等待下一批一起推送。目标回读为 active，不受旧15%线限制。

- 基本输入为 31 个普通模块态、14 个 GS 态，44/45 已逐字节重译，1 个旧 CS 来源不明继续拒绝。输出槽额外边界3/4、真实VS/GS增量验证通过。
- 新的显式 fetch 语义投影不合成 V#：完整扫描3201条有fetch记录，其中1152条转换能唯一证明，2049条有真实歧义。首批4VS+2Local均逐字节SPIR-V一致，25原生反例和14项API控制通过；不是1152条已全部原生验证。
- 原始fetch采集现在有真实CompileModule入口，观察原Parse/GetSharp，不额外读V#；模式OFF/ON的实际TU均编译通过。Mailbox丢唤醒和两组实际mask身份已经修复并定向验证。它是开发采集，正式默认宏OFF，无collector符号/线程；十四库默认本机准备没有被关闭。
- 采集侧car本身仍不足以独立恢复完整编译输入。继续保存同一次编译的完整owned companion及实际SPIR-V，单blob原子发布、最多两项且总4MiB在途，不能以“已写V#”冒充准备器已可消费。
- GS最终PSO新增独立schema2；原Geometry算法共享、运行时仅委托，47个CPU合同控制和语法通过。77条实际GS recipe仍待生成严格模块计划/receipt绑定与统一native验证；普通schema1仍拒绝GS。
- 上下文两项有界候选已接隔离门控；完整sigreturn在正式Rosetta与上游同样未实现，仍未伪造实现或宣布九项整体完成。
- 暖准备仅落最小等待修复，6项真实子进程控制通过。跨helper/session复用、有限批处理仍只是审计建议，没有以理论时间冒充启动实测。

接下来先完成这三条来源链（fetch投影、完整companion、GS最终PSO输入），再重新生成当前ABI/layout及整套coherent native后端与工具，最后统一冷暖、性能/内存、F2/F3/死亡/THD/菜单中文Dock回归和干净检出交付。正式应用、正式存档、设置、缓存和Release未改变。长期TSO/FEX/CPU-GPU协同仍仅在文档，不扩大白模调查。

## 2026-10-26 本轮源码推进（状态补充）

- quota 已重置且用户撤销旧 15% 停止线，系统目标 active；不兑换 reset。
- 8,915 个真实硬件阶段/代码身份已关联；跨 35 份隔离缓存冻结 1,728 套完整候选输入，不冒充全游戏最终 PSO。
- 已落独立 CPU GCN 重新翻译器，实际普通 CS/FS/VS/ES 共 30 个与旧观察 SPIR-V 逐字节相同，35 类原生反例通过。独立 GS-copy 输入又有 14 个真实正例通过（51 条物理记录去重）；GS 反例继续收口。发现一个大 CS 不一致，严格拒绝，不覆盖旧缓存。
- 真实 fetch 读取使用 SGPR2，而旧 SRT 只保存 SGPR6；补齐来源后开始为已有 owned RegisterRead 做独立有界持久化，不从静态格式反造描述符。
- 普通 setcontext 与 PC 分类器已按隔离环境门控接线，27 场景/282 断言真实 FEX 回归通过。sigreturn 仍缺实际完整帧与非返回提交合同，不能登记空实现；新审计证实正式Rosetta和上游同样没有实现，不能写成ARM桥接遗漏。九项尚未按原定门槛整体完成。
- 先做源码、CPU/FEX 定向测试，再统一长测/内存/启动器回归与可移植构建。正式应用、数据、缓存、Release 不动。

# 当前计划：先目录与三个上下文接口，最后统一验收

## 2026-10-26 当前执行顺序（覆盖下方历史安排）

用户已取消15%额度停止线，额度重置后继续推进；不兑换reset。严重内存压力、异常swap增长、锁屏及正式数据保护保持。当前不逐小改运行长时间游戏测试。

1. **优先完整可编译输入目录。** 在已完成的全量物理资源扫描上继续关联程序代码/阶段、材质/模型、静态寄存器/usage/semantic、fetch/顶点布局、资源特化、附件和最终管线状态。可确定的生成原worker能消费的canonical输入；缺字段逐项给出来源和前置条件，不按hash猜选或枚举笛卡尔积。
2. **同步收口三个上下文接口。** 原九项中六项已实现并定向验证，当前剩 `_is_signal_return`、`sigreturn`、`__Ux86_64_setcontext`。复用已有普通unwind候选和真实FEX控制，不从头重复研究；严格区分普通恢复与完整signal恢复，不绑定Darwin同名API、空成功或长跳跨RAII。三项做完后，九项作为同一批兼容性工作准备正式接入，不能先单独推广六项或带不完整三项冒充完成。
3. **暖启动重复成本随后处理。** 保留现有严格准入，减少可证明重复的扫描、进程和加载工作；不放宽目录/摘要/入口校验，不缩缓存、不降低画质、不增加大常驻索引。
4. **最后统一验收与交付。** 完成前述源码和定向测试后，再集中做同配置Rosetta/旧新性能与内存对照、最新组合F3/死亡/单客户端联网/菜单/中文/Dock回归、干净检出构建和可移植包。正式应用接入在九项完整、批量验收通过后进行；当前不修改正式存档、设置或缓存，不刷新Release。

开发验证分层：纯数据/解析/契约先CPU自造夹具；只有控制流/内存/ABI变化才做有界原生/FEX正负例。每个关键错误验证一次，不把相同静态场景长测当日常编译步骤。当前允许并行静态目录、可编译输入consumer和上下文接口，主代理统一共享契约与整合，不同时启动多份游戏。

**长期优化暂存：** 硬件TSO签名/逐线程准入、CPU/GPU深度协同、新增血源专用FEX热点特化只记录在docs/fex-specialization.zh-CN.md及待办，不占当前迭代，等上述工作收尾后再推进。现有软件排序和安全回退保持。

已完成支线：十四类Metal源库+原八类固定PSO已隔离默认准备，Local像素、56失败反例和同包三轮冷暖通过；详docs/tiling-default-startup.zh-CN.md。424项仅当前QA目录，不是全游戏完整管线全集。白模已确认风险的回写链路已收口，不再扩大新假设。

---

以下保留历史检查点，遇到旧接口计数/额度或优先级以顶部当前计划为准。

# 当前目标与状态（2026-10-25）

隔离ARM64/FEX无缝复用原启动器；先查白模、长编译、性能。死亡重生、在线和菜单live验收暂缓，留待用户醒后。保持静音、画质、分辨率、缓存容量和现有补丁；不改正式应用、存档、设置、缓存与Release。

## 已完成的安全修复

- 75/76只供原监督器验证过的菜单/F3重启；错误退出独立，17项FEX退出+6组监督器回归。
- 显式Clang ABI marker接回宏漏掉的包装器；344包装器不是全量兼容证明。完整libc跨度校验，未知data导入不可读guard页，同包离线/联网模块准入。
- 首次GpuModified上传hash遗漏有真实Metal old/fixed像素对照；修复用于隔离QA。实战白模未复现，不能视作同一根因；ABBA未证明可靠提帧。
- 原16个启动脚本字节不变；手动QA默认不屏蔽物理输入。正式版未替换。
- 当前56个套件目标全部重建后121基础回归及专项通过；最新CPU67db33e2有额外msync/模块正负例，手动QA契约包已冻结67db33e2，旧09af包保留历史。

## 最新发现

- 新探索的唯一shader/PSO同步编译确实可达200–326ms；磁盘MSL是源码，不是metallib。暖场景也有非编译长帧，不能混为一谈。
- 162ms帧中producer有86.7ms cond_wait。已补此前被显式render模式漏掉的producer等待记录；默认关闭，不改同步。
- 另一轮21.1ms producer wait关联到CSChrThread2广播；没有重叠编译记录。只是候选唤醒线索，不证明所有卡顿原因。
- 全范围纹理诊断补齐first-byte盲区，六组控制通过；新QA游戏轮partial0，128ms帧无新编译/长producer cond_wait。白模与多类长帧仍待追踪。
- 未对齐信号诊断在实际游戏捕获47次，合计0.318ms、最高0.030ms；本轮42–52ms长帧不重叠。不是该轮主要瓶颈，不改访存语义。详见docs/unaligned-signal-audit.zh-CN.md。
- QoS默认关闭：实际静态ABBA47.50→47.08FPS，无收益。保留实验，不默认启用。部分纹理范围候选存在漏更新负例，继续不打包。

## 尚未完成

- 用户战斗白模现场归因及安全局部数据同步修复；不以合成测试替代。
- CSChr工作依赖、低CPU长间隔及实际首次编译尖峰的可重复优化。
- 762 导入中 16 个 AvPlayer 已桥接原实现；原9个边缘接口已有4项真实桥接，5项仍待完成。
- 原型独立clean-checkout构建尚未完成，README明确准备过的对象/依赖stage约束。正式构建不变。
- 用户醒后统一验收死亡、联网和菜单，不重复宣称历史通过等于本轮通过。

## 长期目标与交付

版本绑定、完整通用回退的Bloodborne FEX/Metal协同：有界热点/指令普查、资源代际+精确范围通知、快照去重、完整shader/PSO键下的有界准备。删未用x86实现不自动提帧；硬件TSO未准入，保留软件排序。

开发分支dev/arm64-runtime-audit。源码检查点仅包含源码/自造测试/选定非敏感说明，不含游戏、存档、缓存、账号或画面。最新本机测试包在artifacts/arm64-qa-20261025-contract-checkpoint，CPU67db33e2/正确a500宿主/原first-upload GPU冻结，仍依赖本机Shared资源，不是可分发安装器。详见NIGHT-HANDOFF和docs/。

限额最后可读剩余41%；阶段边界继续检查，低于15%就停止开发测试，完成交接与最新QA包，推代码，不更新Release。严重压力、快速swap增长、锁屏保护未改。用户随后授权仅测试前使用Cua，已在无游戏时收起Codex并验证服务停止；之后三轮不用Cua或桌面录屏。

最新冻结包功能冒烟通过（47.88FPS），但单轮聚合内存6143MiB且加载时短暂critical一个采样，不能当作内存等价验收；退出后压力1，原5秒保护未改。详见signal报告。

后续冻结CPU-only ABBA输入完全匹配：旧47.353/新47.466FPS，无提帧结论；本组旧6109/新5829MiB，与此前单轮方向相反。未见稳定新增内存压力，不将下降归因于探针，长期/实战白模仍未验收。下一步应补独立正确性游戏画面观察，与FPS对照分开。

## 原生游戏图像与真实战斗补证

已复用原生present读回并经18张真实Metal控制校准，新增有界owned输入/图像诊断，全程无桌面录屏。一次外部控制因300秒超时未完成，失败保留；后两轮主动结束通过。最新旧亚楠确有敌人受击，5帧见衣物/毛发/血液效果，没有复现整身白模。战斗门控没有新Metal编译，唯一140.7ms长帧与抓图请求相交，禁止当性能证据。正式/手动QA包未捆绑诊断GPU。详见docs/native-game-image-diagnostic.zh-CN.md。

下一步将已找到的真实敌人区域用于短门控战斗，图片放在测量门控外，再关联纹理绑定和首次编译；保留原有保护，不能把未复现当修复。

追加门控分离短窗口：CPU2657/GPU577条完整，最高帧间隔27.02ms，FEX block最高1.216ms，未对齐17次共0.095ms，未出现新library编译；抓图在门控关闭1秒后。最后前景遮挡且无敌人接触，不称作实战复现，不从本轮推FPS收益。下轮必须重走已校准路线，不假定普通QA存档保留战斗位置。

## 组合实战诊断和装载身份准入

已完成full-range+原生帧组合诊断：一次装载错旧库的失败由native格式证明抓出；补齐实际Mach-O load edge/SHA/ARM64/严格签名准入，11个真实Mach-O控制通过，覆盖所有现有GPU候选和冻结包。私有基线Info签名/1个FinderInfo属性已备份修复，代码段/库字节不变；正式app和正常QA包不改。

成功路线1792887238427365000：角色染血、回响141，三个无抓图计时窗口，CPU7256/GPU1725行完整，最高31.662ms；新library/PSO scope为0，full-range局部intent为0。未复现白模，不称已修复，不拿诊断当提帧。详见docs/combined-combat-and-load-identity.zh-CN.md。

下一步优先补GCN→IR、IR→SPIR-V和完整模块计时，分开冷/暖内容。不要重复同一暖路线零编译站桩来宣称已查清长编译，也不先放松失效/同步。新保存的有界路线工具需要在该下一轮正跑中验证入口，源码已拒绝非QA目录。

收尾冻结QA90秒无抓图冒烟通过：48.39FPS/p99 22.73ms/GPU19.45ms，聚合5828MiB、最高warning、swap约+105MiB。只验证新实际装载准入不破坏原启动路径，不归因提帧；正常zip仍未改。

补充准入回归gpu-candidate-identity-1792888744088525000：最终12项真实Mach-O控制通过。新增代码页篡改后重新声明正确文件哈希仍被严格签名拒绝的负例；签名失败现在带出有界实际原因，避免只见笼统CalledProcessError。未改游戏执行或封装包。

## 最新：真实长编译已拆到具体阶段

GCN→IR/IR→SPIR-V/后端的默认关闭计时已接入，7项分析负例、7项原生trace控制、真实自造GCN→Metal开关同SPIR-V正控均通过。两轮GPU环完整，CPU早期记录有覆盖，不能据此给整个启动排名。冷缓存264模块最长57.8ms（IR34.2ms/后端23.2ms/实际驱动1.1ms）；暖路线新顶点37a8af3d模块246.8ms，其中Metal驱动231.8ms，再接PSO83.7ms，同帧338.3ms、Gnm提交等待337.3ms、FEX编译0.407ms。不是所有慢编译同因；该次明显是新库/管线阻塞。

片元源码每次读取复制全后缀已改为原字符串区间regex，367份输入输出字节一致，24组真实Metal采样像素一致，主机微测约1.2%下降，不是FPS提升。新诊断库编译/真实GCN正控及门控探索冒烟通过，后者同hash预载0.206ms，无新模块门控记录；缓存变暖和AI偏差不能当新优化A/B。正式11671文件完全一致，正常QA包/Release不变。详见docs/shader-phase-and-uniform-range.zh-CN.md。

下一步应查完整特化已知的管线准备时机：现有DrawPreparation worker只选择已有模块，不编译，不可简单多开线程/跳绘。白模仍未明确复现，剩余 9 导入和独立 clean-checkout 仍待完成，不再重复同一暖站桩来宣称收益。

## 最新 AV 桥接检查点

16 个原 AvPlayer 实现已接入真实 FEX 回调、句柄及有界暂存，线程清理控制从 5/0 到 5/5；真实 FEX 关闭后 TCB 回到 1/1、对象归零。121 基础回归及新布局、AV、退出专项通过。最终 CPU602795df 的静态 ABBA 旧48.492/新48.623FPS，持平，不称提帧或内存下降。剩余 9 导入需分类。白模未修复、长编译未缓解；普通手动 QA 包仍为旧 CPU09af59d2，正式应用未动。详见 docs/avplayer-bridge.zh-CN.md。最后可读用量剩余38%。

## 用户新增两项任务

完整范围与验收见 GOAL.zh-CN.md 的 2026-10-25 补充：收口剩余九个接口，建立固定 1.09 着色器和完整特化目录。不能按“能启动/尚未调用”跳过前者，不能把理论笛卡尔积或基础 hash 清单称为后者已完成。

## 新的准备提前量证据

默认关闭、有界且不改变绘制的 probe 已接入。真实 GCN 正控及七项原生预算/门控控制、十二项关联分析控制通过。实际隔离路线 GPU6859条完整，371项缺失发现、195项 hash/阶段的潜在关联；269次模块编译全部分项完整，但多数发生于启动。进入场景一秒后13次模块编译中仅5个模块有较早发现，约20ms的两项片元没有提前记录；不能宣称已有完整特化可供后台安全编译。CPU早期环覆盖仍明确报告，不能做全启动CPU排名。详见 docs/shader-preparation-opportunity.zh-CN.md。

## 最新静态目录与九接口对照

见 docs/fixed-shader-catalog-and-imports.zh-CN.md。只读盘点固定1.09：15,323个非调试shader条目，含内嵌后8,853哈希/8,311已验证代码摘要；实测221哈希全部找到，但不称全游戏特化覆盖。现有QA缓存508份meta/SPIR-V配对、391程序、409管线键；约12.8MiB，不改缓存。20+6自造控制通过。原resolver源码与ARM副本逐字节相同、旧GPU宿主九NID均缺失（16AV正控），故为原有支持缺口，仍需逐项契约/调用点收口；未执行正式CPU或冒称九项已实现。白模、提前准备与材质/完整键解码继续。

## 已有特化输入已解码

见 docs/semantic-cache-inputs.zh-CN.md。主QA508份meta/409键和热点缓存327份meta/259键均完整解析、阶段引用一致；仅只读字节，不注册walker。速度图顶点拒载计数均0，排除该假设；热点37a8af3d在后者，不在前者。Profile raw差异仅6字节padding，49字段语义相同，经原生operator==正控确认；不以此自动合并缓存。18项canonical Writer/布局/引用/字段语义控制通过。未新增运行时优化，未测FPS收益。下一步继续材质/未覆盖特化关联及九接口，不重复预载已知键冒充新优化。最后可读额度剩余36%。

## 最新：程序名数据导入已实现，剩余八项继续

见 docs/guest-process-data-contracts.zh-CN.md。`__progname` 已改为已有客体参数页内的可写指针槽，实际FEX读取/改写/新进程重置及错误函数声明负例通过，不增加映射/内存池。新CPU442a746f通过121基础回归、17退出/6监督器和6实际AV控制；普通手动QA包未更新，仍09af59d2。正式11671既有保护文件逐字节不变。

实际libc调用点证明 ModuleInfoFromAddr 的同一输出缓冲区随后传给 __elf_phdr_match_addr，不能照搬FreeBSD dl_phdr_info同名函数。这两项需成对补完整模块元数据，另外线程析构、mlock/msync和三项信号上下文仍未完成。F3检查点是普通游戏存档文件而非RAM快照，未改其格式/协议；本轮无真实游戏验收/性能结论。最后可读用量剩余36%。

## 最新：模块查询和地址匹配已成对实现

见 docs/module-info-contracts.zh-CN.md。复用原SELF解码器，在FEX/GPU启动前准备SHA绑定的元数据；原16个启动准备源码不变。运行时3描述共1272字节，不扩大缓存；助手约302MB临时峰值/0.38秒，退出后才创建FEX/GPU，不能隐瞒此启动开销。新CPUbf4338e5通过8组真实FEX、73个原生负例、6个生成器测试、两个编译布局、121基础及程序名/退出/AV回归。实际无窗口libc/Fios2初始化成功约1.04秒，压力仍2、swap无增长；没有场景/FPS结论。原九项已有三项实现，六项继续。助手75/76和空成功均以CPU错误1失败，原F3协议不变。原手动QA包和正式应用/Release未改，最后可读额度剩余35%。

对齐专项补证：max_align_t假设被原生测试拒绝，已共用现有低地址堆常量；实际FEX→TLS分配验证32字节对齐/零初始化，64字节不保证的负例仍拒绝。最终CPUbf4338e5的121基础、退出/AV/程序名复测通过；实际模块冒烟0.856秒，不当成启动性能A/B。分配算法和容量未改。

## 最新：msync 与 VM 数值地址契约

详见 docs/msync-and-vm-address-contracts.zh-CN.md。msync 已真实桥接，显式转换 FreeBSD/PS4 标志，防止误触 Darwin KILLPAGES；映射校验和同步受生命周期读租约保护，不增加锁页或缓存。实际 FEX PROT_NONE 测试发现六类 VM 地址被误当可读缓冲区，已只修地址槽，输出指针负例仍拒绝。18组真实FEX和原生文件/并发控制、121基础及模块/程序名/AV/退出专项通过；新CPU67db33e2的无窗口原模块初始化0.852秒、swap无增长，不是FPS结果。原九项已有四项实现、五项继续；正式11671既有文件与原16脚本不变。普通QA包仍09af59d2，未更新正式应用或Release。

已并行分派 mlock 契约、白模资源身份、shader/材质完整键三项审查；候选不改公共代码，根代理统一整合，不并行启动游戏。最后可读用量剩余34%。

## 2026-10-25 并行专项审查收尾

- sceKernelMlock：固定镜像默认锁页配置关闭，锁定目标是libc flexible arena整块。实际Darwin同一16KiB页重复lock计数0→1→2，一次unlock仅降1，最终已清零/unmap。22项有界模型测试通过；单次16MiB/总64MiB/4096页仅候选上限，不是游戏需求或已接实现。须联动msync INVALIDATE的wired EBUSY、VMA租约和成功覆盖后退账，继续计入5个未完成接口。见docs/mlock-contract-review.zh-CN.md。
- 白模：真实TextureCache/Metal七项控制确认同址同尺寸但linear/tiled布局变化会在memo/exact/generation路径误选旧图，11904/16384字节与正确参考不同；合法sRGB和1D/2D等正控通过。26项布局谓词控制仅证明分类；未修运行库，未归因为用户白模。GPU-only需raw→retile→detile保存字节语义，不能直接CopyImage或弃图。见docs/white-model-resource-audit.zh-CN.md及docs/texture-layout-candidate.zh-CN.md。
- 材质/特化：304个MTD完整结构解析，72 ShaderPath；273有命名候选、31未关联，非实际绑定。主/热点缓存505/508、324/327唯一静态代码候选，余为GS歧义；10项自造控制通过。只导出解析器、控制和摘要，完整材质/身份账本留本机。见docs/shader-material-inventory.zh-CN.md。
- 线程/上下文：finalize是模块注册清理，不是线程TLS清理；须补析构回调in-flight生命周期。三项上下文不能调Darwin sigreturn或空成功；12个ARM64/x86结构控制只验证子集，不证明完整PS4 ABI。见docs/thread-signal-contracts.zh-CN.md。

主线程复跑22+10自造控制通过；这些新增候选不接resolver、不启用锁页/布局修复/后台编译，不产生新FPS结论。独立审查/自造测试可并行，公共执行状态与GPU行为修复集中整合，真实游戏/性能对照串行；所有正式数据继续不动，不发布Release。最新可读用量剩余32%。

## 最新：布局诊断与GPU原始字节保全

见docs/layout-observation-and-raw-preservation.zh-CN.md及两份GPU-only报告。默认关闭的64条饱和布局观测已编译，6原生helper/13实际Metal控制通过；不改选择/dirty/回收。原retile的padding有两个明确像素失败，GPU覆盖合并语义正控将错误降0，但CPU oracle mask不是生产方案；整合13控制仍保留2项known_defect，不能冒称全保全已修。下一步应让tiler按源逻辑texel写入并保留raw其余字节，先审TilingInfo ABI和所有消费者。

两次加载被swap增长保护停止；按既有授权重启chronod后仍不能通过，用户关闭其它应用后第三轮实际场景17.07秒/两轮请求完成，布局错选0条；没有白模或FPS收益结论。短暂critical5采样、swap+963MiB不能省略。保护未变，alias和源QA存档已核验。正式既有11671文件及原16脚本不变。

新本机QA契约包已冻结CPU67db33e2/正确a500宿主/原66d554bd renderer，修复打包器固定旧menu库的路径；两个包内FEX正负例和CRC/内容检查通过。artifacts/arm64-qa-20261025-contract-checkpoint，SHA c22970cc38eb2a90946327a4a5f80e7525f12ddd22d9b5504a116615badb5032。未启用布局转换/诊断到普通包，未开新GUI、未更新正式应用/Release。旧09af包仍保留历史。最后可读用量剩余30%。

## 最新：GPU逻辑texel回写边界候选

详见docs/tiler-logical-coverage.zh-CN.md。私有652字节参数保留旧460前缀和公开ABI，specialization13/完整cache key区分新旧；原物理寻址不变。24真实GPU控制全部符合预期（原两padding缺陷消除）、19形状拒绝/14参数控制、同进程六次新旧管线交替通过。32/64直接源重写不再硬编码SPIR-V编号，保留现有proxy/MetalFX语义。候选默认关闭，未改FindImage选择。

单次无Cua静音隔离探索进入场景18.05秒、66.41秒完成，未崩溃、无保护中止；pressure4短暂约0.261秒，swap+1.73GiB。此轮不是FPS/内存对照，未证明白模解决。普通本机QA包、正式app/数据与Release不变。下步继续raw权威来源与布局选择修复，以及匹配性能/内存验证，不降低画质或缩缓存。

最终c607候选补上省略常量默认值规范化与完整key，4新增控制和24 GPU/六次交替复测通过；最终构建也完成70.42秒隔离探索，swap+360.06MiB、起止压力1、短暂严重不足1秒，不是性能/内存A/B。源码继续只推dev分支，普通QA包和正式应用/Release不变。

## 最新：GPU raw写入重叠与顺序诊断

详见 docs/raw-write-order.zh-CN.md。两组19个实际Metal控制保留四种已知失效缺陷；GPU-only同submit顺序证明预写导出能保住旧/新数据，但尚未接入实际BufferCache。修正限额后的真实隔离场景17.01秒进入，16条潜在漏失效记录、最多4条/probe、swap终值+57.5MiB，无严重压力。binding覆盖不是实际逐字节写入或白模根因证明。正式11671既有文件/16脚本不变；不降画质、不改缓存预算、不更新正式包或Release。Mlock和owned-shader仍为独立未接入草稿；前者发现部分映射失败状态一致性门槛，不能当作已支持。

## 最新：独立shader编译基础已构建，暂停盲目攻击脚本

见本机evidence/owned-shader-verified-checkpoint-20261025/report.zh-CN.md。230 GPU单元完整重建、owned真实GCN/Metal/毒化控制、原phase off/on及24字节保全通过。scheduler/首见捕获/采用仍未接入，不称卡顿已缓解。最后场景仅启动动作smoke；用户观察到一直砍墙，不是探索/战斗验收，后续不再启动该盲目脚本。新源码待精确export/push，dev当前94de6bb5；正式应用/数据及普通QA/Release不变。

## 最新：有界 Mlock 接入通过无窗口回归

见 evidence/mlock-integrated-checkpoint-20261025/report.zh-CN.md。CPU854294e0，56目标重建/121基础及14专项通过；真实Mach单页重复去重和MAP_FIXED清账通过。原九项已有五项有界实现，四项继续。未跑游戏、不称提帧或白模修复；普通QA包仍CPU67db。新owned-shader基础a8bd4776已推dev，捕获与raw预导出草稿尚未接入。正式数据/Release不变。用量本轮剩余约93%。

## 最新：POD shader捕获基础通过真实控制

见 evidence/guest-capture-verified-checkpoint-20261025/report.zh-CN.md。230GPU单元重建为ec51ec6c，1000故障恢复/owner析构、有界参数/真实prep入口/GCN截断通过；owned、原phase输出623deccc及24字节保全回归通过。未开游戏、未接scheduler，旧SRT/fetch大guard仍待修。源码仅dev同步；普通QA/正式数据/Release不变。Mlock已推c2c5bdc1；当前用量剩余约93%。

## 最新：bounded SRT/fetch准备读集和mirror整库回归

见 evidence/shader-readset-verified-checkpoint-20261025/report.zh-CN.md。230单元重建a36c7bd3，真实lookup/nested SRT/fetch/V#及旧capture/owned/phase/24保全通过；mirror 46项在独立源码和实际动态库均通过。成功64次小读微测约12us，非FPS。尚未接firstmiss discovery/worker/adoption，白模仍未解决；R2/finalizer/context独立草稿未混入。普通QA/正式数据/Release不变。

## 最新：模块finalizer实际回归及策略更新

见 evidence/module-finalizer-verified-checkpoint-20261025/report.zh-CN.md。CPU7adf4d5e，真实C两组/11FEX/121基础及完整专项通过；原九缺口已有六项有界实现，三context仍未接。不称实景性能已验证，普通QA包仍67db/66d554bd。新增目标以完整组合持久化/加载预热为主，未知异步兜底，测逐draw校验累计成本；GOAL末尾保留细则。R2/live payload/context/firstmiss仅私有草稿，正式数据/Release不变。当前账户约87%剩余。

## 2026-10-25：持久化与加载期读取验证

见 docs/warm-persistence-validation.zh-CN.md（Shared 原始证据 evidence/warm-persistence-verified-checkpoint-20261025）。v3 归档实际身份隔离、原子成功发布、父目录故障保护及 LoadPipelineStage 精确命中免重读已在两单元候选实现，15 原生子进程、9 producer 分支、真实 Storage 路径、owned/phase 与 24 GPU 保留控制通过。无游戏启动、无新 FPS/白模成功主张，尚未默认启用、未更新普通手动 QA 包/正式应用/Release。下一步须在隔离场景核对完整组合覆盖与冷/暖启动编译、帧时间及内存，尤其 newLibraryWithSource 仍会发生，不能用 archive 命中代替所有层命中。完整设置散列保守且有启动成本；新原生候选必须用实际绑定此库的私有 GPU 执行文件测试，不能把旧 GPU app 的 v2 结果当成 v3。

## 2026-10-25：实际归档BAAB及用户本地离线库要求

见 docs/warm-archive-game-validation.zh-CN.md。显式候选选择入口已补，不再用旧GPU app冒充新库。用户关闭部分后台后4×75秒原生1080p同设置静音站桩完成：PSO开启500/520实际命中，平均创建23ms对关闭178ms；仍538次Metal建库约0.8s。FPS趋势48.82对46.58（仅单组），内存平均差+20MiB但两配对方向相反，未证明稳定增减；继续实验、不默认启用。两轮先前保护中止保留，不清缓存/不放宽保护。缓存508完整特化/409键按当前布局解码无错，但不是全游戏覆盖。正式11671文件不变、普通QA和Release不变。

用户新增要求已写入GOAL末尾：交付代码中的本机离线建库流程，各用户自己构建，不分发开发机游戏metallib/归档。当前只有本机PSO归档，新的本机离线库构建器尚未实现；不可把两者混称。后续以实际重写后MSL和完整兼容身份做准备期构建/命中验证，不直接恢复已撤销Toolchain实验。几何runtime特化等价问题由已有subagent只读审查，未应用草稿或声称白模修复。

## 2026-10-25：本机离线库已有隔离代码和实测，未启用

见 docs/local-metal-aot-validation.zh-CN.md 与JSON。当前GPU1ba32725（3实现/公共ABI不变），本机428请求→metallib约6分20/3.9MB，真游戏两轮538/538模块命中，暖prepare零重编。26builder/6policy/16native、真实BuildLibrary以及旧9专项/24纹理通过；11671正式文件不变。源码/产物不外传，只推dev，不更新普通QA/正式应用/Release。

无确定收益：暖IR库856ms对source821ms，FPS49.48对51.49仅稀疏样本；首IR仍PSO5.84s。保留实验路径、默认OFF，不把record轮/驱动冷热当startup gain。后续先应用独立entry准入/原source回退/发布及根边界/中断恢复审查，再处理18处API覆盖（现在只接1处模块入口），重点推进最终PSO二进制和新场景完整组合。并行GS等价修复仍草稿；不得将它或白模问题标已解决。整轮记录和父进程启动时序已存在，首次编译真实峰值RSS尚缺。

## 2026-10-25：本机AOT安全修正验证完毕，仍实验关闭

见docs/local-metal-aot-validation.zh-CN.md末节。当前GPU a24a0569985495d79a78d02502052037517232f2ef92c4629916bd264e0e0f38，已补entry实际成功后准入/记录、memo失败绕过artifact回source、RENAME_EXCL、根相等拒绝、逐层创建前检查及只读中断恢复。26builder+9恢复+6policy、actual BuildLibrary假函数/路径负例、4真库/16真GPU负例、旧9专项/24保留通过；最终默认OFF游戏75秒进入场景正常退出。旧1ba32725性能表保留原版本，未冒充当前版本AOT性能。所有游戏编译产物留本机、旧428库保留。剩余：其它17个source API/defaults/constants、最终PSO和新场景覆盖、初次编译峰值记录、GUI与最终AOT验收；不可标整个目标完成。正式与普通QA、Release不变。

## 2026-10-25：归档来源冲突修正与特化谓词负例

见docs/local-metal-aot-archive.zh-CN.md。旧a24同候选ABBA的PSO归档命中500→0→500→0，确认source与本机离线库互相替换归档。3e6d7e83df299f146ea4c61857651122095c9da345a5166b7f20050075cbec9a按本机build_id隔离，只保留当前一套内存归档，19原生子进程严格命中/双文件保留/真实GPU输出通过；四库16准入及九组回归通过。修正preservation固定旧candidate的测试入口，本次24纹理实际载入3e6d7e83df299f146ea4c61857651122095c9da345a5166b7f20050075cbec9a。旧历史24项不能证明当时新AOT候选。AOT默认关，旧ABBA约0.68%差异不是收益证明。实际特化key28项作者负例确认GS invocations/ring尺寸及compute shared memory遗漏，仅shadow修复通过，vendor未改、白模根因未证。下一步独立实际IR输出/LoadStage/一致GPU重建；不清缓存。正式数据/应用/普通QA/Release不变。

归档修复候选3e6d7e83的75.23秒source模式游戏smoke已完成，无保护中止；正式11671文件重验全不变。完整新候选AOT游戏性能仍待，尚未默认开启。

## 2026-10-25：特化字段正确性修复已进隔离源码

见docs/runtime-key-correctness.zh-CN.md。真实IR/GS copy/SPI emitter证明四字段改变编译；旧库实际Storage/ordered/worker检出六个错误，新库21检查零失败。ec7742a66c5d3ef4ce4858414aecc558ea9f7b2141c596f6e61e287b3fe6aa46完整230GPU单元重建，编译器布局不变，不清缓存。既有9回归、19原生归档及实际SHA绑定24纹理通过。ABBA+BA六轮站桩帧率基本持平，501份MSL与核心缓存计数一致，无新增变体、无保护中止/正向swap增长；B1内存偏高未持续，不能声称整游戏零成本或白模已修好。历史compute38条LDS全0、GS已知调用数全1，所以仍无live根因见证。正式11671文件全不变，普通QA/正式app/Release不更新。下一步：live payload原始身份/安全写回的实际生命周期，首次未知组合和异步采用；用户验收项继续暂缓。

本机AOT回归新增显式--candidate以测试全量Ec7742a6库，四库/16准入和真实输出/GCN/skip4通过。当前header变化用完整230构建；旧三单元工具会正确拒绝，不绕过。


## 2026-10-25 用户范围修订：默认本机建库、全调用覆盖、白模收束

本节优先于此前“无平均帧率收益则离线建库默认关闭”的实验决策。

1. 本机离线 Metal 建库按默认启用的正式功能推进；主要验收游戏中源码编译次数及帧时间尖峰、冷暖启动成本和内存，而非要求平均 FPS 提升。用户在自己的 Mac 上构建自己的库，不分发游戏 MSL/AIR/metallib/PSO。整合完成前不把当前不完整候选改名为已发布功能。
2. 静态定位到的全部 Metal 建库调用必须建立覆盖台账并接入；不同语言版本、math/optimization、宏、函数常量及入口必须保持原语义，不能把默认 options 或 specializations 强套现有 recipe。编译工具可用性与失败恢复也属于正式功能完整性。
3. 扫描全游戏着色器、材质及引用，关联已记录完整 graphics/compute key；目标为启动/加载期提前准备完整已知管线。程序目录不等于 PSO 目录；不能静态确定的运行时附件/布局/特化值必须逐项列明来源和需补的收集方法，不能以笼统“未知组合”作为停止覆盖的理由，亦不能虚称扫描已证明所有运行时状态。
4. 白模任务本轮收束：只修复已发现且有证据的缓存键、纹理布局、生命周期和协议风险，补定向测试后结束本轮调查。不继续扩展白模根因追踪或新生产 raw-write 系统；不能以这些风险修补宣称实际白模已解决。
5. 仍不改正式应用/正式存档/设置/既有缓存；开发分支代码与文档同步、不更新 Release。死亡重生、在线和菜单最新验收继续暂缓。

当前并行分工：生命周期与 peer-exit 保护的独立作者验证；全部建库调用/完整组合覆盖及私有补丁；剩余 context/signal ABI 审查。主代理负责合并、串行全量编译及真实 GPU 对照；同机不并行运行游戏或性能测试。


## 用户纠正：现有纹理导出/完整回写必须完成

不扩大白模调查不等于搁置现有 R2 与回写闭环。继续整合并证明内容、分配/映射代际和真实 GPU 完成顺序；53 项组件控制不代表完整调用链验证。停止新增白模假设，不停止已在手中的工程与回归。见 docs/resource-risk-closure.zh-CN.md 最新节。


### 此链路收尾结果（2026-10-26）

上述现有工程已完成有限准入的安全写回整合：10项真实Metal/跨进程C前端控制、9项真实FEX分配器/故障回调控制，以及干净主进程3项复测通过。实际游戏原入口固定场景ABBA四轮通过；关闭/开启平均49.77/49.69FPS，未确认帧率或RAM收益，未观察到候选更高的内存占用。没有把原53项组件测试或静态场景结果当作用户白模根因/长期稳定证明。

新的本机隔离QA包开启受保护资源路径和默认本机建库，包含一致007/Live6的干净CPU、宿主与新编GPU main；正式应用/数据/Release不动。该资源链路不继续扩展白模猜测；其余着色器/管线完整输入和加载期准备、剩余ABI接口及CPU/GPU协同目标仍active，不把阶段收尾当整个目标完成。

## 2026-10-26：补齐静态阶段头与现有完整配方的来源

见docs/static-shader-register-inputs.zh-CN.md。新自有有界reader补读GCN前GNM文件头、寄存器/usage/语义表，以及GS嵌入复制VS；非调试包加8个原loader重建模块的22,519条程序记录全部关联成功，旧全文件扫描已识别的代码不再只有hash。14项作者控制通过，未运行GPU、walker或游戏。8,036条既有cache元数据都能关联静态头；三项寄存器数量对照的2,227条差异仅为fetch设置会修改的输入VGPR数，不能用文件默认值直接预编译。附件、资源特化和完整PSO状态仍需精确接入，不能把阶段头覆盖说成预编译完成。

完整配方只读盘点另得基线同profile域额外1,016个逐blob配方，其中1,006无已知motion/GS-span拒载标记，但只增加5个程序/阶段对，不能作为1,006个语义不同或已准入的PSO。长编译37a8af3d的完整引用已定位；保留原cache-local permutation命名空间，不直接合并索引或改旧缓存。

两处portable派生PSO缺少RegisterPipelineData已形成小补丁，正在私有新cohort完整重建并准备原生descriptor/像素/跨进程archive回归；此时尚未合入公共运行时或更新QA。其它helper的typed constants和最终PSO准备另有私有草稿，未编译验证，不能用旧模块AOT测试为它背书。正式应用、存档、设置、缓存和Release均不变。

### 派生key原生回归续进

上述新cohort完整230TU已重建为d2389413；真实Metal/Storage/原WarmUp在seed及独立warm进程分别通过260/192项断言，strict archive命中2/2，native无深度附件与原VS/FS的RGBA16Float像素/速度值读回正确。重复16次不登记、不新建、不重写key；失败/motion拒录保留。详见docs/derived-pipeline-warmup.zh-CN.md。两处12行持久化成功分支已纳入开发源码，未更新QA/正式应用；整场景命中率、启动/卡顿和内存对照仍待。其它helper草稿只在私有目录修复，尚未合入。

### 全目录工具已公开并完成有界实现的实际重扫

四个静态工具及两份作者测试已整理进tools/tests，默认当前源码树vendor，独立最小树入口与23项作者控制通过。FLVER/BHF增加逻辑引用累计预算、共享offset收费及结构化budget缺口，输出逐块编码。重新读完28,852文件/31.4GB，全部SHA与旧台账一致；15,371模型及120,726拆包条目完整，新增预算拒绝0，旧8853/8311程序/代码集合完全一致。184.4秒，独立扫描进程峰值642.9MiB、最高压力1，完整数据只留新Shared目录。唯一right.sprx格式缺口及155材质引用未解决，静态完整PSO仍0，不把目录交付说成所有组合已预编译。见docs/full-game-static-catalog.zh-CN.md与evidence/static-catalog-final-replay/validation-summary.json。


### 2026-10-26：真实本机库和最终 PSO 的准备/读取链路已贯通

详见 docs/local-pso-preparation.zh-CN.md。helper v2 的稳定 discovery 与实际 Source/Local 来源已分开；成功函数身份和原游戏冷工厂采用独立严格验证产物，新增无窗口 spec-only 准备器及两个分片发布者/分页 builder。230TU一致重建为52759255…；自编写 VS/FS 7进程681断言、真实FillU32 7进程2138断言通过。三族真实游戏配方生成7条PSO，独立暖进程禁止PSO编译时7/7 strict hit、0 ordinary PSO compile，完整Tess为5条；冷/暖最高压力1、swap+0。当前正常fresh/disk/memo/local身份一致，另有去冗余诊断计数的小补丁尚未合入，不混头/库。

217项当前layout数值、49 Profile字段和7类Writer字节与旧版相等；新的source SHA校验未绕过，旧producer仍unknown。旧输入只作候选重译，游戏不导入Meta/SPV/SRT。公开40项Python控制和22项分页控制通过；4101作者请求可保护停止后续建、跳过已成功项。所有游戏输入/库/PSO和完整配方仅留Shared；源码、作者输入与汇总同步开发分支，Release/正式应用/正式数据不变。

未完成：原启动器自动编排与完整配方分页覆盖、原游戏实际消费、RectList专项、其它helper最终PSO、真实游戏冷暖启动/卡顿/内存对照。当前实际QA包仍是资源回写检查点，不能把新源码说成已安装新QA，更不能标整体目标完成。死亡/在线/菜单仍按用户安排暂缓；白模不扩新假设。


## 2026-10-26：原启动入口准备、相机常量ABI与整包失败定位

原final-exec现自动取得实际TITLE_ID和选定QA缓存，按有界磁盘页遍历完整目录；取消总16MiB/4096和单次16条截断，预算未完明确partial。原实际Metal验证选项和BB_GS_DEBUG保留，工具代码从冻结闭包导入，数据全局显式绑定Shared。新包冻结worker/实际renderer/lib边及最小布局源，不依赖Documents运行输入。

UpscaleMerge、Presenter后处理和Camera共享原冷工厂，原owner槽/算法保持。真实Camera验证抓到108B常量对112B的MSL ABI错误：只补零尾4B，不改公共字段/矩阵/shader；修补后实际RecordMotion三帧静态/移动/抖动的7进程控制与后处理7进程通过，另11原生冷暖/回退进程通过，无swap增长。230单元9a闭包后仅相机.mm变化，验证229对象和全部头/源未变再重编一单元得c2ec，不把增量说成重建230。

实际首包缺_sqlite3，已补齐包内同ABI依赖并用真正包内Python验证。再测进入真实worker，发现dyld行按空格拆路径会误拒正确DSO，已停止这轮无效准备并保留日志，修补与整包验收继续；不能声称本轮游戏启动/FPS成功。公开源码和文档待此检查点整理同步dev，不更新Release。详见docs/local-pso-preparation.zh-CN.md。

全游戏目录/完整PSO和其它辅助工厂仍未全完成，死亡/联网/菜单暂缓，正式数据及旧缓存保持不动。最新额度剩余32%，未用reset；整体目标仍active。

带空格dyld解析已修为完整记录/PID/唯一库/实际SHA核验：13解析与真实日志、3supervisor控制通过，公开12项便携控制。新包原入口首批16真实配方完成，正在继续分页；尚未以中间状态作整游戏消费或性能验收。


## 2026-10-26：整包消费与增量进度已验证

四轮原final-exec入口游戏测试已通过；SQLite和带空格dyld路径修补已获整包验证。最新v2暖启429条中424项可准入全部完成（另5GS session/span拒绝），419条已有hint保留而仍严格重验，0record，原生492hit/4built/0failed。准备471.10秒；游戏527最终管线Hit状态日志、538本机MSL命中/0源码事件，49.08FPS，p99 21.88ms，最高压力2。不是全游戏覆盖、匹配FPS收益或零现场Metal编译证明；CSV遗漏WarmUp/辅助工厂。冷启短暂严重压力和swap增长留在汇总中。

增量域v2、原子索引激活、编译子进程退出唤醒、仅QA cache/local-metal克隆已合入开发源，55项CPU控制通过；测试包为artifacts/arm64-qa-20261026-incremental，SHA bd7e75fa...611505，依赖本机Shared、不便携、不上传Release。准确汇总见evidence/startup-preparation-validation-20261026/game-integration-aggregate.json；其余旧段落是历史检查点。

当前继续：消除准备阶段每页重复全扫描/工具探测（原native准入不能省），补其余固定helper最终PSO，推进静态完整目录与尚未完成的接口/翻译协同。全部源与对应文档仅同步dev，正式数据不动。死亡/联网/菜单仍由用户稍后验收，白模不扩大新假设。目标active。


### 2026-10-26：重复审核已消除，整包启动复测通过

最新工具包原生组件仍c2ec/f894，没有混入尚未验证的Format/counter。真实448请求克隆审核16.328秒→0.401/0.398秒，源cache不动、pressure1/swap0。整包0hint新进度域先实际strict探测，424/424完成、0record，496nativehit/0built/0failed；27builder中1full/26reuse，整体准备103.72秒、同场景进场155.91秒，90秒49.70FPS、p99 21.43ms、maxpressure2。不同冷暖/旧轮曾补4PSO，所以不称匹配帧率/RAM收益。原生严格准入及全部保护不变。

最新本机QA zip是artifacts/arm64-qa-20261026-session-startup，SHA bf4f9ae5...580e05（host-local）。源/对应文档同步dev，Release不变。接下来FormatConvert原工厂与Metal实际API阶段计数需同源重建/真实原生验收，仍不能将其作者控制视为已启用；其它固定helper/new-user种子及全游戏完整状态仍未完。目标active。


## 2026-10-26：原生Format工厂与真实阶段计数

同源230TU为a4d1，Format7/Camera7/Post7/核心11共32进程通过；匹配30CPU+2AvPlayer+main、007/Live6已配对。原RGB/Depth字节算法不变，主kernels共享原两slot；统计35个active raw API点（含unit228 ImGui，Metal4 inactive），17新CSV列，512B计数数据不含小状态。无draw hash/资源查询/新GPU对象。

首次新cohort原入口通过但600秒只准备336/404，42full占430秒，进场632秒，48.64FPS/最高压力2。实际5785完整CSV行ready1，唯一末尾143打断行按原reader过滤；ready后11source/35ordinary，全在前6.015CSV秒，tail30这些API0，不等于所有驱动零编译。无匹配FPS/RAM收益声明，常量特化/open/serialize/MetalFX内部未计。详见docs/native-format-and-phase-counters.zh-CN.md及去内容汇总。新包0d86536f当前只完成冷整合，同包暖轮正在测；E7/bf4f是先前完整424准备的包。

继续最小有界两遍（可靠首审至多1+收集后build至多1）消除cold每页重扫，并保留全strict暖快捷；另无历史h2首启固定8kind收集是未验证私有草稿。它们尚未替换上述实测工具/原生包，不能当已经合入。正式app/data/旧cache/Release不变，未完全目录/接口/翻译协同目标继续active。


### a4同包暖轮已完成

handle15658 terminal passed：P/evidence/launcher-engine-abba-1792975355830020000，worker1792975405838075000。424/424、record74、strict350，准备241秒、进场325秒；source API0，ready后13strict全命中、2ordinary，tail30 API0；17列实际结构再次通过。48.54FPS/合计footprint5967MiB/maxpressure2/swap+9。与c2ec包49.70/5808的跨轮差异未归因，不能承诺性能/内存无退化；3匹配顶点样本字节同/有效env同也不证明全缓存等价。详见原生文档。

原生23源文件、项目自有Format fixture和计数/ARC作者源及准确汇总已整理，源仅同步dev；正式应用/数据/Release保持。新测试包0d86536f已两轮原入口通过，未包含当前独立两遍/8fixed种子草稿。当前无游戏运行，原agents正在仅CPU验证后两项，冻结E6不可修改。目标active。


## 2026-10-26 固定内核与整批建库整合

已实现并默认接到隔离原 final-exec：8类可确定完整输入的固定内核；真实能力查询；全目录先收集再统一建库；当前native/环境准入后提交，不以磁盘hint授权。230 GPU/30 CPU/2AvPlayer/1窗口main同源构建，11核心原生进程和4固定种子进程过，固定暖8/8 strict，0普通compile。原入口路径含空格解析错误已修并补7定向控制；capability stdout运行中4097B/10s有界。

真实冷准备404/404、record404、首审1/最终build1、385.63秒，准备swap0；随后的加载触发既有5秒swap增长保护，未取得冷场景结果。暖准备416/416、record0、175.27秒；原入口暖场景90秒通过，48.935FPS/p95 21.13/p99 21.57ms，合并footprint5851.3MiB（不去重），全程swap+856.625MiB、短暂压力4但不持续。GPU ready后Source4/3.035ms、最后30秒计数0；不是全驱动编译0，也不是已证FPS提升/内存达标。详docs/fixed-helper-startup.zh-CN.md和公开summary/warm-scene。

剩余：匹配性能/峰值内存对照和瞬态严重压力归因、全游戏完整PSO状态目录和静态可确定调用的持续覆盖、3个signal/context入口、干净源码ARM构建、CPU/GPU及FEX专用化、硬件TSO准入。白模已确认风险的回写生命周期收口不再扩大；死亡/在线/菜单实测继续暂缓。正式app/data/cache/Release不动。整个目标仍active，不能以本轮准备通过标完成。

## 2026-10-26 残余建库和加载压力只读归因

四个新增schema2 helper库请求已与实际完整generated header及原TextureTilingSource变换逐source SHA匹配：macro16、macro64、texture macro32/64。首次GPUready后Source4/3.035ms为聚合计数，缺逐API key/time，不能将269.29ms整帧归因为3ms编译。十二条collected-local-inputs首次函数已Local且无source fallback，第二遍Built、随后原WarmUp同key Hit；缺的是最终PSO，非十二份MSL库。详docs/tiling-library-coverage.zh-CN.md。源码可确定14个tiling库有限域，下一实现应逐条沿原工厂默认首启收集，不靠历史、不硬编码本次hash；最终typed constants目录另行合法过滤和去重，不能把理论上界充作游戏全集。尚未改C++。

加载压力首次出现均在原生GPU游戏宿主启动后，非已完成的目录准备阶段。旧/新开头两组八类资源全部字段、metal/owned/alias/unclassified统计完全相同。现有数据不显示新大块类别保留，但没有全系统VM/其它进程同刻明细，不能称已排除回归/泄漏或锁定后台原因。不得因此缩缓存或改回收；下一内存诊断复用NativeVMObserver。详docs/startup-memory-attribution.zh-CN.md。只读重算暖态1%low旧40.98、新45.39（各最慢15帧均值倒数），非匹配系统ABBA，不声明收益。

新QA包中央目录854条，未包含AIR/metallib/binaryarchive/SPV/游戏payload或savedata；仍host-local。当前在同一QA包做下一暖重启，只让已有builder消费4新请求，验证Source4→0；保持原保护、静音、无Cua/录屏/角色动作。该结果后补，不以意图当通过。

以上最小复测已完成：Shared launcher-engine-abba-1792984355921423000，原builder453条/449 verified skipped/4 built与既知四key逐一相符；目录424/424/record0。GPUready后Source4→0、Local7→11，strict13/13、ordinaryrender/compute各1。90秒站桩48.510FPS/p95 21.36/p99 21.72，1%low44.20；combinedfootprint5816.26MiB，maxpressure2、swap+373.06。无匹配系统A/B，不说FPS或内存收益；14库静态首启入口仍未实现。只改了新QA缓存中的正常建库产物，没有运行时代码或正式数据改动。

## 2026-10-26 VM诊断与14库候选交接

开发控制器新增默认off的--memory-timeline，SDK 24字段/152B验证通过；同增量用于私有原入口诊断完成，946/946 VM样本，performance_evidence=false，maxpressure2/swap+181.1875。GPU前wired约3998MiB，GPU+5秒8958MiB，scene-ready10124MiB；系统compressed物理约3402MiB。系统IO累计swapout229.2MiB不能当占用增量，也不能归到单进程。该轮424/424首遍strict、record0、final local0，warm_fast_path=true、94.84秒，实际证明单遍分支，不声称性能收益。Shared case launcher-engine-abba-1792985185717589000，旧test包未重编。

14库后续候选只保存在candidates/tiling-library-seeds-20261026两patch；未应用当前运行时或QA包。组合21项CPU控制过，两个实际ObjC++ TU的fsyntax-only过（依赖已备外部头，不是clean build、无objects/link/GPU）。schema2区分8PSO与14库，旧1/64安全unavailable，恢复原nil库短路；原三元保护一直存在，不称崩溃修复。下一必须同源全闭包构建+真实14库Local/8PSO/pixel与负例，再接默认入口。不因无FPS收益默认关闭，只因尚缺原生验收保持未启用。

最新已测QA包仍e3689f86，全部正式数据/Release不变。当前余量15%，不再开新大批构建或游戏；低于15停止开发测试、保留目标未完并按交接续做。不得把此条当目标完成。

## 额度停止线已触发：余14%

十四库E10已完成230GPU闭包/配对worker链接；11核心+4固定准备原生进程过，22库建成，暖14Local/8strict/普通PSO0，缺build正确Source回退。四像素进程过但Local hit0，未证明Local像素路径；应先核对测试的AOT root与GPU user命名空间，再做定向验证，不能直接改生产逻辑。没有配对CPU游戏/App、未启用候选，最新QA包仍e3689f86。详公开native summary与HANDOFF末节。停止开发测试，完成交接同步；不把额度停止当整体完成。


## 2026-10-26：授权定向复测已完成，Local 像素归因补齐

用户仅对本轮目录修正与像素复测取消15%额度停止限制。旧作者脚本的 AOT root 位于另一个 GPU user 目录；只修正测试配置，将缓存克隆到每例自己的 gpu/local-metal，未改任何运行时目录校验。

九个最终进程通过：bounds0/1普通纹理各Source0/Local4、18例输出相同；代理各Source0/Local6、2例输出相同。代理额外的原SceneTargets CopySource库由现有builder仅在新测试克隆补建，不混入14种平铺库统计。错误目录仍以load模式运行并回退Source4/Local0，输出相同，证明未绕过目录准入。新增脚本从实际DSO读取API统计，避免作者executable重复inline变量造成假零。详evidence/tiling-local-pixel-validation-20261026。

28.16秒、426系统样本、最高压力2、swap减少8MiB；原fixture92文件散列清单不变。未运行游戏、未改正式数据/既有缓存、未改运行时代码、未重编QA App或发布Release。这些是作者GPU正确性控制，不是游戏性能/峰值内存或全部最终PSO覆盖证明。

Local像素归因待办已完成；仍缺14库缺entry/坏摘要反例、配对CPU/App及原入口冷暖和其它原目标。候选继续未启用，最新游戏QA包仍e3689f86。本轮授权测试已结束，整个旧目标仍保持原暂停状态，不凭本轮额度例外自动恢复其它开发。


## 2026-10-26：十四类库已接隔离默认入口，冷暖验证完成

用户授权本轮不受15%停止线限制，仅推进十四库反例、隔离默认入口与冷暖/内存。56项失败反例+1正常对照均过；每次仅目标Source，其余13Local，原8PSO strict且普通编译0。目录/摘要/真实入口校验未改。源码已合候选，额外补齐十秒预算后晚请求的有界增量处理；纯CPU进展/无进展/取消等控制通过。

沿已核验的ffbb230GPU闭包构建30CPU+2AvPlayer+窗口main，打包同源schema2组件。新QA应用默认准备14库与8PSO；不是正式应用。冷404/404，约395秒进场；首次暖补齐运行中产生的20项最终host PSO，424/424；稳定暖424首遍strict、record0、warm_fast_path=true，约141秒进场。每轮固定场景90秒。最高压力均2；场景footprint合计中位数5974.5/5970.1/5944.3MiB，swap+20.3/+124.8/-24MiB；准备进程合计峰值约630–634MiB。共享页可能重复计入，不声明旧新性能/峰值RAM收益或长期无泄漏。

已保留首次CPU私有脚本旧overlay校验拒绝和首次冷fixture漏profile.bin的失败；修正目录配置后用另一个新目录预检404项完整，才接受上述三轮。全体测试结束、无遗留游戏/worker。源QA输入清单重新散列不变，正式存档/设置/缓存、正式应用和Release均未改。

最新本机包artifacts/arm64-qa-20261026-fourteen-libraries/Blood-Echoes-ARM64-Local-QA.zip，SHA256 b6f44b74023a8e276aa612716e6e11a1b3150232c5df4a6aa7adcac21987e304；44,752,772字节、854项，host-local非独立安装器。详docs/tiling-default-startup.zh-CN.md及evidence/tiling-default-startup-20261026/summary.json。

本轮范围已完成；整个原目标仍未完成并保持原暂停状态，本轮额度例外不自动授权其它开发。完整游戏目录、匹配旧新性能/内存、三个signal/context入口、硬件TSO授权和CPU/GPU及FEX特化等仍保留；死亡/联网/菜单仍待用户验收，白模不扩大。
## 2026-10-26 最新执行口径：自动闭环与上下文接口并行

以用户最新要求及 GOAL.zh-CN.md 顶部为准：交付原启动器默认自动扫描→可编译输入→本机编译持久化→runtime复用，不再人工逐批寻找输入，不要求集成前手工找齐动态组合。静态可确定的自动覆盖；动态缺口由正常路径采集并供下次启动准备，不能猜描述符。

`launcher_game_catalog` 已复用原扫描、原SELF loader与严格static linking，接到原game preparation入口，空缓存也会进入；13个作者正反例通过，其中一项运行真实既有reader而非mock。完整companion新编译/暖命中封口、通用module controller和GS同队列接线仍在收口。尚未完成当前全部源码coherent构建和真实运行时命中验收；不以定向consumer成功冒充交付。详见 docs/automatic-preparation-integration.zh-CN.md。

三个上下文接口仍是并行要求：两项受限候选已隔离门控，27场景/282断言真实FEX通过；sigreturn尚未完成。接口代理已从GS接线返回其producer/完整状态/结构化采用工作。此前六项不单独推广，整批正式接入门槛不变。

验收改为新安装自动准备、冷暖复用、兼容身份失效、Local/PSO实际命中、现场编译与长帧、启动耗时和峰值/持续内存；正式应用、Release和正式数据继续不动。
