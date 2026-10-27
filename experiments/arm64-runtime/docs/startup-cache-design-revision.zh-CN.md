# 启动准备与缓存身份的定向重设计

2026-10-27。执行既有ARM64/FEX目标的启动部分修订，不重建目标，不回退已有效成果，不扩展新的长期方向。当前工具卡仍显示历史blocked，不能据此宣布无工作可做或整体完成；执行现状和授权以本文件与GOAL/CURRENT-PLAN顶部为准。

## 当前实际状态

本机已安装0.2.56/684cc230，仅交付此前通过隔离验证的局部复用与临时纹理入口；986文件一致及deep/strict签名检查通过，旧0.2.55代码保留。下面的新身份/依赖重设计仍仅在开发源码中，没有随此次安装交付。此前393项定向控制和2项exec接线补充控制通过；新隔离同路径旧版暖启动88.48/79.31秒，候选69.14/48.76秒，后者PSO调度实际命中，complete-pso21.57→1.07秒。模块native编译0次但仍有约16秒核验，完整设计未完成。候选场景footprint6190/6019MiB，基线6179/5801MiB，不能宣称稳定内存等同或长期验收通过；压力最高均2。首次全量登记后的进场在5秒swap增量>1GiB时保护停止，曾见压力4，该轮排除。用户退出Safari后正常继续，不放宽保护。

现有临时逐绘制纹理入口已接到原最终exec。独立隔离原supervisor F3、自动继续及三帧批次记录解析通过：1295/1332/1338绘制、7536/7582/7790绑定，无截断/解析警告。首次检查器误把批次协议当逐行协议，保留原失败记录，改用已有分析器扩展后确认，没有为修检查器再跑游戏。未捕获到白模或读取纹理像素，不能证明白模修复。用户下一次启动的临时抓取请求已单独开启，等待其复现通知后再触发。它不是新设计完成的证据，不与性能采样混用。正式存档、设置及缓存保持。

## 当前源码集成：进入集中隔离验收前的封口

下面均为开发源码；不表示已安装、已在真实游戏命中或已证明性能/内存收益。

1. **真实编译域已启用。** 原 manifest 仍完整保留 boot/content_profile/patches/settings、选项及旧 selection token；实际传给原生工厂的 cohort 已改用 CompilationCompatibility。原生 MSL、全部特化、函数/管线完整字段和键算法未删减。7 个已审查的执行控制与编译身份分离（residency、barrier、staging、timing、归档持久化/释放、弱库 memo），其余未知控制显式保守保留；two-level BDA 明确保留为编译控制。UI/日志工具不进入 GPU 编译域。原生 DSO、ABI、真正编译选项或 SDK 兼容变化仍需失效。
2. **本体与各 Mod 独立登记。** RegisteredSources 为本体保留完整文件集，包括当前被遮住的原版文件；每个 Mod 有独立 hash/journal。原 mods.py 继续决定启用、覆盖顺序、大小写、目录/文件冲突及社区汉化最后覆盖。新 Mod 组合不会作废本体登记；已登记组合直接采用 registered-selection，变化历史缺失才回到相应来源的完整登记。遍历 RAM 中的旧清单不等于重新遍历本体文件系统。
Mod组合变化但最终物理来源映射完全相同（例如下层覆盖被汉化再次遮住），可直接从旧完整选择证明登记本次组合，无本体扫描或新视图物化；实际选中源、只读视图变化记录或完整映射任一不符即回退。旧混合源摘要仅作为当前逐文件stat匹配后的字节证明，不冒充本体完整登记；隐藏原版文件仍实际发现并检查。

3. **新视图复用已验证快照。** 当前来源/完整 SHA/长度相同，且旧 readonly 文件仍匹配完整 inode/security/stamp 时，使用 Darwin fclonefileat 的原子 CoW 数据等价契约，从该精确 inode 建立新文件。无硬链接、无软链接、无大复制回退，源/目标 inode 不同、0400/nlink=1/长度/mtime 及源 fd 前后身份均检查。目标完整集合、权限和后续变化仍检查；新增/变化项及自定义 clone 回调继续实际字节验证。借用的是旧文件真实字节证明，单列 sealed_kernel_clones，不虚报新 hash 数。全新组合仍须物化一套满足原 canonical reader 的文件名/CoW 元数据；这属于当前视图格式的剩余成本，不是游戏必然需要全量检查。已完成的正常启动/F3不重复这一物化。
4. **静态目录复用同一份快照证明。** source-view 已做完的全量校验通过其 READY、完整集合与连续变化记录传给 catalogue，不再重复读取同一批字节。既有 package、GNM 与 full-resource 三层解析器共享逐资源 metadata products：完整文件内容、路径/解析语义、解析器/loader/enum 身份，以及 BXF 头→数据体等依赖进入键。未知文件照旧扫描；变化资源重新解析，未变化的包/头/模型结果复用，并保持输出行顺序、字段顺序和全局去重/计数。实际端到端作者样本比对所有非计时索引字节一致。SQLite mmap=0、2MiB页缓存、单记录4MiB/库512MiB边界只约束这个新 metadata 加速层；超界按原解析器处理，不减少游戏/渲染缓存或覆盖范围。退出 worker 后关闭/释放。
静态目录的物理来源与选择同样分开：两个 readonly 视图的完整文件集/所有SHA/长度相同，才复用原目录所绑定的物理根；保留本次 selected_game_root，禁止只改 catalog.game_root 字符串冒充新来源。旧物理根损坏或历史丢失则另建目录代，旧产物保留。此边界有正例和损坏反例，未启动游戏。

5. **模块按当前 required 输入查询。** 编译器命名空间不再含整个 catalogue ID；完整选择来源另存不可变收据。typed companion 的 required 索引在枚举历史捕获前，核对当前完整 GCN SHA、Profile、全部 canonical spec、实际 SPIR-V、原 packet/nonce/cohort 及 native receipt；可跨两份已登记只读源视图复用。全部覆盖则不启动 inventory/native 编译。通用模块的每 UID 源证明免去重复解包，全部冻结输入、结果/SPIR-V 准入保持；GS 沿用原完整源码读取器。未覆盖/拒绝不能写成成功，正常运行 owned capture 继续补全。
6. **PSO 按 required recipe 复用，随后聚合暖启动提示。** 在原队列中接入 Recipes 调度证明，不伪造 worker row：只接受真实 native 事件、真实 Local 请求/库/收据和每个实际 archive 的完整字节证明，并等待原 prepare phase 的稳定环境校验通过才提交。复用键保留完整当前 unit、原输入 identity、worker/DSO、布局、编译选项、SDK/local 环境与相关准入协议。改变不相干 cache/library/archive 文件不作废旧 recipe；单依赖损坏只使关联 recipe 进入原原生队列。已覆盖部分不调用库准备或验证 worker。全当前集合再次确认且事件历史连续后，保存聚合 schedule，后续可跳过整批流程。scheduled_reused、required_preparation_complete 与本次 full_manifest_preparation_verified 分开，不借前次 native 证明伪报本次原生重验。未知/owned geometry PSO 协议仍用其原完整路径，不能以普通 recipe 证明冒充覆盖。
7. **当前坏产物可保留式局部修复。** Metal 库/receipt 或中断 orphan，先完成并验证替代品，再以 NOREPLACE 迁移旧对到 .retained-invalid-v1，保留实际旧 SHA/stamps/清单，最后发布新对；失败保留原对，危险链接不移动。兼容的其他 cohort 库可无编译采用。坏模块的旧 index entry 和 case 保留，只重做同 UID；临时修复失败不变成永久输入拒绝。依赖该模块的 GS recipe 由实际 packet/receipt/SPIR-V 摘要局部失效。诊断 adoption 来源独立于活动 artifact root，避免自己触发全体失效。
8. **F3 已前置，原在线→离线语义保持。** 同 supervisor 的 Local 选择与 resume-only 在普通整批库/helper/资源/模块/PSO准备之前。在线运行可以登记完成的 GPU 准备，但只有原 supervisor 的离线 F3 child 可以采用；不会恢复 token/账号或在线状态。当前实际 boot/patch/settings、选择、代码、nonces、SDK/local 目录及上下文仍匹配；真实变化就回退原准备。原 F2、自动继续和退出码完全沿用。
9. **本机库兼容身份与保留式采用。** actual NativeToolchain schema2 绑定 Metal/metallib/xcrun 的代码/版本、OS build 和默认 SDKSettings.json/plist 内容。SDK 路径/时间另作来源/本次固定条件，同版本换目录/仅时间变化不换产品。真实跨 cohort 库采用须原 native derive_key 有效，原 MSL 与当前逐字节相同，完整 options/build/receipt/library 字节通过。v1 entry 与 v2 整库语义保持。旧 schema1 未记录 SDK，及旧 PSO 仅有不可逆 recipe digest 的情况，不能伪造兼容或重贴 ready；保留旧产品，必要时一次性为当前真实输入重建。未来 Mod 变化不再因此整体更换 compiler cohort。

本轮43套、669项定向检查（含系统真实 CoW/文件历史控制）通过；另有独立真实 FSEvents+fclonefileat 作者数据端到端证明，源/目标字节另行完整比对，新 Mod 只 hash 1 个文件并复用 2 个已验证只读 clone。见 evidence/startup-cache-redesign-20261027/integration-directed-result.json 与 real-cow-admission.json。早期430项/393项及先前游戏数字是不同源码阶段，保留为历史，不拿来冒充当前包结果。

下一步交付阻塞：冻结并同步当前源码 → 干净 Git 归档组隔离包并核 native 载荷/闭包/签名 → 实际本机库与 native worker 命中及迁移 → 一次集中启动/Mod 往返/坏项局部修复/F3验收，记录 worker/编译次数、阶段耗时、长帧、峰值和场景持续内存。尚未运行当前完整设计的游戏或更新正式应用；不能据定向通过宣称这些已完成。若实际日志暴露未覆盖协议/缺少事件，则按具体输入修复，不能把 ready 计数补成成功。

## 首轮实际集中采样与残余修正

76e208d5隔离包真实进场：旧0.2.56暖启动51.49秒，首次新身份登记479.64秒，随后53.90/25.40秒；场景48.02→48.23/48.86 FPS，不宣称渲染加速。source-view从9.72秒降0.58秒，内容读取0；PSO从整批进入每recipe复用0.82秒，最终Local231项built=0且persistent audit实际命中。场景合计footprint旧6136.78MiB，新6167.53/6147.84MiB，压力最高均2，无持续内存等同结论。首轮更换解析/SDK命名空间一次性登记单列，不算暖启动收益。

现场发现：87模块已完整采用，但93个typed候选中6个没有唯一静态源关联，仍触发历史inventory。修正typed required集合只含原算法真正能准备的唯一静态源；原完整stage循环与missing-or-ambiguous-static-source缺口保留，另外显式报告unresolved_static_stage_records，不删除导入/统计或冒称全部准备。新定向反例通过，待最终包合并进后续Mod/F3集中矩阵。尚未替换正式应用，Release不更新。

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

早期1956c367里程碑28套、430项定向检查通过，见evidence/startup-cache-redesign-20261027/directed-result.json（当前669项见上文）。实际Xcode SDK元数据读取检查通过（JSON7948字节、plist5538字节），没有调用编译器或游戏。先前失败记录保留：旧增量提示的测试需显式选择下层回退路径；新SDK产品需要新建库后再完成稳定审计，旧“两次即可memo”假设已修正；新源证明测试的重复seed与拒绝返回约定已纠正。初次沙箱内变化记录不可用及SDK路径查询混入xcodebuild系统告警，退出沙箱重验通过，没有放宽原解析或路径校验。一次旧live Metal测试因缺少它要求的旧构建manifest，在启动任何GPU/游戏前失败，留待集中包验收，不记通过。430项通过不包含此项，也不构成真实Metal库迁移或游戏性能验收。

## 定向及最终验收矩阵

必须覆盖：连续普通暖启动；A→A+B→A；关闭Mod后回到已缓存原版；UI/日志/无关设置变化；真实代码、完整特化/管线状态、编译环境变化；单个产物损坏局部修复；F3同会话。每类先做代表性正/反例，再自动批量调度；不回人工搜集全游戏组合。

最终记录：阶段/到场景耗时、模块编译子进程和管线验证worker次数、运行期MSL源编译/管线创建/二进制命中、长帧、启动峰值和场景持续footprint/系统swap。必要的进程内Metal对象创建独立列出。已覆盖、未变化部分应没有重复编译或整批验证。保护中止、只命中计数、单元测试均不代替完整验收。

约束：保留画质、分辨率、缓存容量与全部既有补丁；不增加持续内存压力，不删清覆盖旧缓存，不改正式存档/设置，QA进度不参与正式选择。保持严重压力、异常swap增长和锁屏保护。代码/内部文档同步现有GitHub开发分支，先不发Release。
