# 启动准备与缓存身份的定向重设计

2026-10-27。执行既有ARM64/FEX目标的启动部分修订，不重建目标，不回退已有效成果，不扩展新的长期方向。当前工具卡仍显示历史blocked，不能据此宣布无工作可做或整体完成；执行现状和授权以本文件与GOAL/CURRENT-PLAN顶部为准。

## 当前实际状态

本机已安装0.2.56/684cc230，仅交付此前通过隔离验证的局部复用与临时纹理入口；986文件一致及deep/strict签名检查通过，旧0.2.55代码保留。下面的新身份/依赖重设计仍仅在开发源码中，没有随此次安装交付。此前393项定向控制和2项exec接线补充控制通过；新隔离同路径旧版暖启动88.48/79.31秒，候选69.14/48.76秒，后者PSO调度实际命中，complete-pso21.57→1.07秒。模块native编译0次但仍有约16秒核验，完整设计未完成。候选场景footprint6190/6019MiB，基线6179/5801MiB，不能宣称稳定内存等同或长期验收通过；压力最高均2。首次全量登记后的进场在5秒swap增量>1GiB时保护停止，曾见压力4，该轮排除。用户退出Safari后正常继续，不放宽保护。

现有临时逐绘制纹理入口已接到原最终exec。独立隔离原supervisor F3、自动继续及三帧批次记录解析通过：1295/1332/1338绘制、7536/7582/7790绑定，无截断/解析警告。首次检查器误把批次协议当逐行协议，保留原失败记录，改用已有分析器扩展后确认，没有为修检查器再跑游戏。未捕获到白模或读取纹理像素，不能证明白模修复。用户下一次启动的临时抓取请求已单独开启，等待其复现通知后再触发。它不是新设计完成的证据，不与性能采样混用。正式存档、设置及缓存保持。

## 本轮已接入源码，尚未集中游戏验收

1. 原资源视图的registered-selection快路径已经接入：仍运行原Mod合并器，只比较实际稀疏链接树，不沿指向原版目录的链接遍历全部文件；已可靠登记的来源和readonly快照由连续FSEvents与READY收据摘要确认。变化记录缺失/丢失、链接结构变化、选择变化或收据损坏回到原完整路径。定向例覆盖暖启动不调用mapping/plain_tree、不hash游戏叶文件、A→A+B→A、源文件变化和异常链接。它解决无变化及回到已登记组合的调度，不代表新Mod组合的静态目录已改为逐资源增量。
2. 同supervisor F3的本机准备复用已前置：原完成准备收据只保存五个Local环境字段与库/请求目录的连续变化记录；当前会话、nonces、原脚本/代码、原参数和关键输入仍重验。命中后先走原game-preparation的resume-only分支，跳过普通整批库/固定helper/源扫描/模块/PSO路径。缺少收据、产物或历史变化则普通准备；不恢复账号环境，不改退出码。当前快路径沿用旧binding，覆盖相同离线条件；在线→离线的F3语义保持原流程，跨模式快路径要等兼容域迁移，不冒称已提速。
3. 模块源证明复用已接入实际existing-module分支：只有原完整源码核验与输出校验成功、同源快照历史连续、完整产品字节/权限集合相同的通用模块，才省掉再次解包原始shader容器。冻结输入的全部SHA/长度/路径、原结果/SPIR-V/自动准入仍检查；GS保留原完整读取器。新owned capture与拒绝项继续走原路径，没有为了命中冻结8项缺口。只略去规定的顶层报告、关联日志和临时inventory目录；模块输入/输出不能引用被略去的区域。该层不是整模块阶段调度完成，也没有消除当前全部捕获枚举。
4. PSO schedule不再绑定全部frozen Python工具，而使用实际PSO输入、解析、队列及变化记录协议代码；整包全部源码准入仍先执行，worker/DSO、布局ABI、完整manifest、具体编译选项、工具链/SDK环境及真实产物证明保持。UI、临时纹理入口及无关新增工具不再单独作废这个调度证明；相关协议变化仍重验。最终完整函数/PSO键不变。
5. 实际manifest producer已输出LaunchSelection与CompilationCompatibility两个域；只有4个经读取点审计的执行开关分离，其余未知开关明确保守保留。原native cohort暂未整体切换，两个manifest域目前用于精确记录，不把新摘要当作旧产物的准入。
6. Metal库的内容产品复用已接入真实builder循环：先验证原native derive_key，按完整有效MSL、v1入口/v2完整库协议、语言/math/FP32与SDK/工具链build身份查内容索引。候选必须重验原完整请求键、原MSL与当前逐字节相同、原build.json、原库收据/实际库字节，才为当前原生请求发布正常库收据，并单独记录原请求和原库SHA；原请求、键和产物不改名或覆盖。实际native编译算法与load准入不变，当前key仍由原算法产生。仅来源cohort不同不需要再执行metal/metallib。坏候选保留并回退只编当前项；不拥有完整SDK证明的旧schema1 build不冒称兼容。该路径先解决当前原生工厂已经给出的真实请求，不代表旧PSO跨cohort迁移也已完成。
7. 实际NativeToolchain.build_id已升级为schema2，把默认SDK的SDKSettings.json/plist完整内容摘要/长度纳入产品兼容；编译前后重新确认实际SDK元数据、选择路径及安全身份。SDK位置/时间仅为来源或本次固定条件，跨次仅时间戳/同SDK换目录不制造新产品，内容变化才产生新build目录，旧schema1目录保留。完整stamps继续用于准备调度。新增SDK变化、时间戳、目录迁移、编译中变化及V1/V2跨cohort A→B→A控制。元数据版本身份按本机工具链发布契约；这不是全SDK每个header的内容快照，也不宣称兼容用户手工篡改系统SDK。

仍需完成的交付阻塞：按资源内容/解码器维护base独立登记及Mod依赖差分；当前required模块产品索引；旧PSO的保留式兼容迁移及runtime共用语义键；现有键位置自身损坏后的保留式局部修复（跨cohort坏候选已有局部补编，不等于当前损坏库对已能迁移）；完成上述后统一包和集中验收。当前源码定向控制不能证明整场景峰值/持续内存或实际启动秒数。

## 复用现有实现

- 原Mod加载和社区汉化覆盖顺序仍由scripts/mods.py及原启动链拥有。它已按层记录替换路径/owners；扩展其实际输出收据，避免再重放并逐文件比较整个app0。
- startup_change_journal/incremental_file_hashes提供连续变化记录、根身份与异常回退；不另建常驻监听服务。
- 静态扫描、容器/ELF/GCN解码、完整source_code核对、显式字段接口、GS准备、owned capture与原生consumer继续使用。资源登记改为按内容/解码器的分项索引，而非每个Mod组合重建全游戏目录。
- LocalAot/LocalLibrary使用真实有效MSL、concrete Metal options和实际entry factory；builder保存实际编译器/OS build收据及SDK准备环境记录。新SDK产品身份已绑定实际版本元数据；旧schema1不能因存在库就冒称有SDK证明。继续共用实际编译器，不能另做推测性MSL或伪造descriptor。
- game_pso_queue保留每条真实输入和ready状态、现有批次worker；native PsoArtifactCreate保留完整descriptor与FailOnBinaryArchiveMiss验证，运行时完整函数/PSO键不变。
- quick_restart_preparation与原supervise_restart协议继续使用，保留75/76含义、F2、F3自动继续、恢复强制离线。

## 已确认的身份混杂

| 当前字段/位置 | 实际用途 | 处理范围 |
| --- | --- | --- |
| warm_archive_manifest.make_manifest.game：boot/content_profile/patches/settings全文件SHA | 启动选择及来源证明 | 从编译兼容身份分离，继续保存在启动清单；真正改变编译的代码/状态进入各自产品键 |
| make_manifest.options：BB_MTL_/BB_LAYER_/BB_PC_/BB_GUEST_大前缀集合 | 混合编译、调度、内存/复制及诊断开关 | 对照实际读取点逐项分类；未分类可能影响编译的项先明确保留，不静默删字段 |
| translator.gpu_library实际SHA | 当前编译器/ABI的保守兼容证明 | 保留实际代码准入；编译兼容与UI/Python代码身份分离，原生编译器变化需显式兼容/失效 |
| 原生Shader::Profile、metadata/layout ABI与完整StageSpecialization | 真正影响翻译/布局 | 保留全部语义字段与精确反例，不以程序短hash替代完整字段 |
| LocalAot v1 Key(recipe,cohort,sourceSHA,entry,language,math,fp32) | 真正MSL输入混入全局选择cohort | cohort改为编译兼容域；source、v1 entry、全部具体options保留 |
| LocalLibrary v2 Key(recipe,cohort,sourceSHA,language,math,fp32) | 完整库，entry由真实factory单独准入 | 同上，不擅自给v2添加/删除函数常量语义；最终函数键仍完整 |
| builder build_id(manifest)及preparation_environment | schema2绑定Metal工具/版本/OS及SDK版本元数据；完整SDK路径/stamps另为来源/本次固定条件 | SDK内容变更正确失效，时间戳与路径不制造新产品；旧schema1保留，SDK证明不足不伪装兼容 |
| launcher_game_modules.compiler.catalog=全目录身份 | 模块存储/调度绑定当前选择 | 从产品键分离，完整来源收据保留；按实际代码、完整目标spec、owned输入和ABI查找兼容产物 |
| module_input_identity中的完整metadata、profile及projection proof | 混合真实特化/捕获完整性/原PSO证明 | 逐字段审计；保留原完整包。只有既有原生证明的精确目标可复用，不能随意投影字段冒充新输入等价 |
| capture producer header中的catalog/index摘要 | 当前程序来源登记/捕获准入 | 独立程序登记语义与选择来源；不把文本Mod造成的目录摘要变化当作所有着色器变化。旧格式保留并明确迁移 |
| pso_startup_schedule.code_identity=全部runtime_tools | 整包代码准入混入调度失效 | 严格整包准入继续；调度使用真实编译兼容和相关验证协议，UI/日志/helper无关改动不整体作废GPU产物 |
| PSO discovery/preparation、函数origin、完整descriptor/constants | 真正函数/管线产品与设备兼容 | 保留真实算法和字段；建立资源/模块→库/函数→PSO依赖关系，单产物坏只修相应依赖 |
| FunctionIdentity::Context/Make/MemoNamespace | context.cohort混合选择域；完整options、源码、entry、device/OS/compiler与成功local库身份是真输入/兼容 | 原native字段目前保留；迁移应替换为真实兼容域，不可直接清空cohort或把Source与Local路由视作等价 |
| LocalLibrary::Origin::Canonical | source SHA、build ID与实际library SHA是产品证明；原cohort/key为来源/当前native定位 | 当前库收据按原完整键发布并留存旧来源；后续PSO语义键须保留实际库/工具链和函数常量，不能仅省略原cohort字符串 |
| HostPso::ArtifactIdentityBytes与Recipe::preparation | descriptor、所有显式函数常量和入口是真正PSO输入；全选择cohort进入两处preparation | 旧二进制归档必须由当前完整descriptor严格命中后才建立新语义索引，保留旧receipt/archive；不能复制ready标志完成迁移 |
| quick_restart.binding和resume | 同supervisor会话复用 | 判断前置到普通库/模块/PSO批次之前，仅保留会话、代码和关键启动条件检查 |

## 最短集成顺序

1. 明确两类数据：LaunchSelection（目录、Mod开关/顺序/覆盖映射/来源）与CompilationCompatibility/ProductIdentity（真实代码/完整特化和状态/编译选项、编译器、ABI、工具链和设备）。先把现有字段表和定向矩阵接入，不能仅新增一套未被实际调用的“正确key”。
2. 复用已有源登记/事件提示与原Mod合并，保存base独立登记与每资源内容/解码器记录；Mod变化只更新差异覆盖和编译依赖。恢复原版查询仍保留的原资源产物。历史完整目录/快照保留，作为可验证的登记/来源，不清空迁移。
3. 模块、库、PSO按对应语义键持久化查询。已覆盖的当前required products不再枚举所有历史捕获或启动原生编译；新/缺/坏部分进入现有增量队列。已有8条拒绝不能写成成功，也不能冻结未来自动捕获可补足的输入；缺口须单列并继续正常捕获。
4. 缓存身份迁移必须可验证：旧请求/库保留，按完整MSL/options/build身份验证后只发布新收据/兼容引用；管线跨旧身份须由实际当前descriptor严格命中后才登记新别名。不能只改目录名、改cohort字符串或复制ready标志。一次登记验证与每次重复验证区分清楚。
5. F3在同supervisor会话前置复用刚完成的准备环境/required products，避免重复整批库检查；代码/关键条件真不一致按相应范围回退。原运行时库加载、创建本进程Metal对象依然必要，计数单独统计，不能算成离线预编译失败。
6. 全部源码集成后再统一构建/集中验收；不因局部修改立刻开长测。临时纹理诊断独立收尾，性能测试不启用它。

本轮28套、430项定向检查通过，见evidence/startup-cache-redesign-20261027/directed-result.json。实际Xcode SDK元数据读取检查通过（JSON7948字节、plist5538字节），没有调用编译器或游戏。先前失败记录保留：旧增量提示的测试需显式选择下层回退路径；新SDK产品需要新建库后再完成稳定审计，旧“两次即可memo”假设已修正；新源证明测试的重复seed与拒绝返回约定已纠正。初次沙箱内变化记录不可用及SDK路径查询混入xcodebuild系统告警，退出沙箱重验通过，没有放宽原解析或路径校验。一次旧live Metal测试因缺少它要求的旧构建manifest，在启动任何GPU/游戏前失败，留待集中包验收，不记通过。430项通过不包含此项，也不构成真实Metal库迁移或游戏性能验收。

## 定向及最终验收矩阵

必须覆盖：连续普通暖启动；A→A+B→A；关闭Mod后回到已缓存原版；UI/日志/无关设置变化；真实代码、完整特化/管线状态、编译环境变化；单个产物损坏局部修复；F3同会话。每类先做代表性正/反例，再自动批量调度；不回人工搜集全游戏组合。

最终记录：阶段/到场景耗时、模块编译子进程和管线验证worker次数、运行期MSL源编译/管线创建/二进制命中、长帧、启动峰值和场景持续footprint/系统swap。必要的进程内Metal对象创建独立列出。已覆盖、未变化部分应没有重复编译或整批验证。保护中止、只命中计数、单元测试均不代替完整验收。

约束：保留画质、分辨率、缓存容量与全部既有补丁；不增加持续内存压力，不删清覆盖旧缓存，不改正式存档/设置，QA进度不参与正式选择。保持严重压力、异常swap增长和锁屏保护。代码/内部文档同步现有GitHub开发分支，先不发Release。
