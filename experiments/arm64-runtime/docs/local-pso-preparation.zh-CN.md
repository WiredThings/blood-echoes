# 本机库与最终 Metal 管线的提前准备

2026-10-26。以下是隔离 ARM64 开发源码的进展，正式应用和 Release 未更新。默认本机准备仍按正式功能推进；平均 FPS 不提高不构成关闭理由。用户自己的游戏输入和编译产物始终留在用户自己的 Mac。

## 已接入的链路

原 ShaderCompiler 与原 PsoCache 冷工厂共用一套独立产物接口：

1. 当前编译器生成真正成功的最终函数，记录实际有效 MSL、入口、编译选项、本机设备/系统/编译器、实际 Source 或 LocalMetallib 来源和模块 ABI。原程序 hash、permutation 槽号和指针不替代这些身份。
2. 用实际完整 native descriptor 和函数身份形成管线键，包括顶点布局、附件格式、采样数、混合、细分及相关状态。无法完整表达的非空 linked/preloaded-library 等状态明确拒绝准备，原运行路径保留。
3. 准备期用新归档编译、序列化，再以 `FailOnBinaryArchiveMiss` 验证当前 descriptor，成功后才原子发布独立产物及校验记录。失败保留已有成功产物，不向已加载主归档追加，也不清旧缓存。
4. 运行时使用自己的实际函数与 descriptor 独立计算键，只查对应产物；缺失或不匹配交回原工厂。已有内存管线命中不重新做磁盘校验、序列化或驱动创建。

辅助管线将稳定的 discovery 身份与最终产物身份分开。前者跨 record/load 阶段保持一致；后者保留真正成功的本机库或源码回退来源，不能把全局“请求 load”当作成功加载。已接入 11 个 HostKernels 家族，包括新增 UpscaleMerge、Presenter 后处理和相机速度图工厂；其它辅助管线的最终 PSO 仍逐项记录，不能声称全部完成。

本机 MSL 构建请求改为前两位摘要分片，旧 flat 请求继续读取；标准库 SQLite 磁盘索引每页最多 256 key、最多 2 个最终文件 pin。两个 native 发布者不再因全进程达到 4,096 次而停止记录。成功校验过的条目在保护中断后不重编，孤立和不完整请求保持明确状态。

## 无窗口游戏配方准备器

`src/game_pso_preparer.mm` 与 `tools/prepare_game_psos.py` 使用既有完整 cache-local 配方，按 record → 本机 Metal Toolchain → prepare 串行执行。复用原 Module、RectList 和完整 Tess 变体链；每次重新翻译均使用新 source scratch，不读取旧运行时 MSL，不向游戏运行时导入旧 Meta/SPV。

原输入按 owned-read/SHA/stat 冻结；只有 bounded 审计后的 spec 区间和 SPV 传给 native。Info、flat/SRT/walker 尾部不进入 native reader。旧编译器来源未知的配方仅提供候选输入，不能因此获得运行时缓存兼容授权。最终原生产物只有当前游戏自己生成相同完整身份时才可能命中。

独立 host 服务提供真实 monotonic 时钟、CNTFRQ 和 TLS。guest/VM/TCB/重启接口明确退出原 unsupported 80，不是假成功，也不使用测试 runtime stubs。准备保护、中断和失败不会使用启动器的 75/76 重启状态。

原启动器 final-exec 入口现自动从实际 --app0 的原 SFO reader 取得 TITLE_ID，并只盘点当前选定 QA GPU 根；不要求开发者手写配方目录。worker、实际加载的渲染库、布局与最小头文件闭包随隔离包冻结。CLI 仍用于定向验证，必须显式提供这些完整身份。

默认每页 16 份、单次 600 秒，遍历整个 manifest，不再以 16 MiB、4,096 条或单次 16 份截断总目录。进度与候选放磁盘 SQLite 索引，内存只保留有界页；保护或时间预算中断后明确 partial 并可继续。ready hint 只影响调度，后续仍用当前 native 严格命中核验，不能代替产物准入。4,111 条/约23.7 MiB的作者队列控制已完整遍历，不能把它说成4,111份真实GPU配方已完成。worker 退出不等于准备成功，另核对实际产物事件、成功计数和最终 GPU 加载边。

## 早期闭包的实际验证（保留版本边界）

同一头/源码闭包的 230 个 GPU 单元完整重建，库 SHA `527592556231ffc5707059a77e127856e56aef4e85bc9460ef83f10210da4bd7`。执行程序和状态都在 Shared，未启动游戏、窗口、录屏或 Cua。

- 自编写 VS/FS 的 7 个原生进程、681 项断言：Source 和 LocalMetallib 分别跨进程严格命中；fresh 与 MSL disk hit 函数身份一致；像素 oracle 正确；descriptor 改变、缺少来源、损坏作者校验记录和缺失本机库时正确回退。暖命中没有普通 PSO 编译，重复内存命中不再访问产物工厂。
- 真实 FillU32 原工厂的 7 个进程、2,138 项断言：record/load 使用相同 discovery/请求/descriptor；实际缺 entry 的本机库退回 Source，不能误用 Local 产物；恢复作者库后原 Local 产物仍严格命中。真实 GPU 填充值及边界哨兵通过。
- 三种真实游戏完整配方：compute 1 条、ordinary graphics 1 条、完整 tessellation 5 条原生 PSO。冷阶段 7 条全部发布；独立暖进程在禁止 PSO 编译时 7/7 严格命中、0 次普通 PSO 编译，函数身份、descriptor 和归档 SHA 一致，没有源码库回退。冷/暖约 11.36/1.64 秒，仅代表这三份输入，不是游戏启动时间。
- 上述原生控制最高内存压力均 1、swap 增量均 0。没有据此声称整游戏内存不变或获得 FPS 收益。
- 当前布局重新由 CPU emitter 生成，217 个标量 ABI、49 个 Profile 字段、7 类原 Writer fixture 与旧版一致；当前源码 SHA 正常验证，没有放宽校验。旧 producer 兼容性仍未知。
- 公开 Python builder/recovery/readiness 40 项控制通过；新增分页 22 项控制通过，其中 4,101 个作者请求在第 300 项保护停止后续建 3,801 项，跳过已成功 300 项，全暖再跳过 4,101 项。该项使用假工具，不能冒充真实 Metal 性能测量。

汇总见 `evidence/local-pso-native-validation-20261026/summary.json`。只公开作者输入、源码和汇总，不提交游戏源码、Meta/SPV/MSL、完整私人配方、AIR、metallib、PSO 或游戏运行日志。

## 早期检查点的未完成项（下文已更新）

自动接到原启动器 final-exec/加载准备、完整配方的有界遍历与覆盖账本、原游戏运行时实际消费这些产物、RectList 专项原生准备及其余辅助 PSO，以及同设置下游戏冷/暖启动、现场编译、帧时间尖峰和内存对照。原生小样本通过不能替代这些验收。

另有去除函数 ABI 摘要中两项冗余重写计数的独立补丁：相同最终 MSL 不应因诊断计数不同而分裂身份。当前正常 fresh/disk/memo/local 路径和本轮原生控制均一致；它不是当前命中错误。该补丁尚未进入上述库，须下一次同源重建后再验证，不能混用新头和旧库。


## 2026-10-26：启动整合与原工厂补齐

- 无可用主归档时，已校验的独立产物可阻止创建会挡住命中的空主归档；真正加载成功的旧主归档仍优先。startup-entry只是经校验后的启动偏好，实际函数/descriptor仍独立形成键，游戏期不写产物。
- Geometry两处手写辅助函数补实际成功来源和完整有效编译选项，最终ABI绑定在字段设完之后；不借此放开旧GS元数据中的SRT/span。函数ABI身份移除已由最终MSL表达的两个重写统计标志，域更新为v2，不清缓存。
- UpscaleMerge、Presenter后处理和Camera接入同一冷工厂；原pipeline槽与画质保持。Camera只在构造时使用主HostKernels，不存借用。
- 真实验证发现原Camera把108B MotionConstants直接上传，但MSL要求112B。Metal调用仅补4B零尾，不改公共结构、字段offset、矩阵计算或shader。
- 全230单元构建9a353538后，只有相机.mm需要尾部修补。精确源/头差分及229个对象SHA验证通过后重编该单元，最终原生库c2ec6e05；不是再次重编230单元。该库11进程冷暖/坏输入控制通过，实际CameraMotion::RecordMotion 7进程与后处理7进程的像素/guard、Source/本机库/独立strict命中及缺entry回退均通过，压力1/swap无增长。断言多为像素与guard逐字节检查，不能视为同等数量的独立管线。
- 启动器、worker与游戏精确保留实际Metal validation选项和BB_GS_DEBUG（包括字符串0及缺席），不强开验证或改变特效。请求集合增加不改变工具链build_id，已通过真实builder/身份代码的增量控制；只有工具链/系统等实际身份变化才换build。

## 新QA包 Python 闭包与整包验收

新分页工具使用sqlite3。实际首包暴露原Python3.11缺_sqlite3，已在新包复制同版本、SOABI、arm64扩展和实际SQLite依赖，改为包内相对加载并签名；旧Python和包不改。有效已带SQLite的闭包经验证原样保留，不查询不需要的provider；无效existing拒绝覆盖。修补时可显式传 --python-provider；版本/ABI不匹配拒绝。运行时不依赖Homebrew SQLite路径。真正包内Python已通过18工具导入、SQLite落盘读写及队列/分页控制。

带空格应用路径的 dyld 日志曾使正确 DSO 被误拒；现保留完整路径、绑定 worker PID 并对唯一实际库做 owned 流式 SHA 核验。原失败日志保留；修补后的原入口游戏已通过四轮，不再将中间批次当作完成证据。

## 增量目录与暖缓存重启的实测

原目录身份包含整个 inventory 摘要，游戏新增20项时会让原409项的进度身份全部改变。现在 inventory 摘要只用于扫描审计；稳定域保留实际缓存根、Profile语义和序列化ABI，每项仍保留完整原键、特化、源引用及其摘要。工具、库、布局、选项等上下文改变仍分域，逐项输入变化仍失效。全量重建 pending 索引并复核最终 manifest 后才原子激活，中途退出不能执行新旧混合目录。旧 v1 进度保留，首次 v2 迁移正常重验，不导入旧 hint。

进度提示只影响调度：即使已标 ready，仍要本次 freeze 和实际 native 严格准入。工具链本地编译子进程结束后立即唤醒，仍保留超时、输出界限、内存/锁屏保护和最终文件检查。

原入口静音、1920×1080和原效果，未录屏、未用Cua、未操控角色。下表是整合/覆盖验证，冷暖条件不同，不能作为 FPS 收益对照：

|轮次|准备完成/当前可准入|准备耗时|游戏最终管线 Hit 状态日志|本机 MSL 命中/源码事件|场景 FPS|
|---|---:|---:|---:|---:|---:|
|旧全目录身份首轮|304/404，预算未完|600秒预算|381|442/96|48.87|
|旧身份新增20项后|368/424，预算未完|600秒预算|458|538/0|48.36|
|v2首次迁移|419/424，预算未完|600秒预算|523|538/0|49.29|
|同包同缓存v2暖启|424/424，完成|471.10秒|527|538/0|49.08|
|会话审核复用与strict-first新包|424/424，完成|103.72秒|527|538/0|49.70|

v2 暖启实际执行424次 prepare、0次 record；419份沿用调度提示但仍通过本次验证，剩余5份补齐。准备器内原生计数为492次严格命中、4次新建、0失败；这与配方数不同，一份配方可生成多条管线。总目录429条，另5条几何着色器 session/span 链路因原安全限制排除；424/424仅代表当前缓存的可准入输入，不能称全游戏完整预编译。

同包暖启场景 p95/p99 为21.16/21.88ms，合计进程 footprint约5826MiB（共享页重复计数），最高压力2，系统swap净减2516MiB；不能据此宣称节省RAM。旧首轮加载swap曾增1848MiB、短暂严重压力约0.26秒；旧第二轮也有约0.51秒严重压力，均未持续到5秒保护阈值。全部加载风险与原始报告保留。core CSV 编译计数遗漏 WarmUp/辅助工厂，不能把CSV零或源码库日志零说成所有Metal现场编译已消失。

仅源代码、自编写控制和去游戏内容的汇总公开，见 `evidence/startup-preparation-validation-20261026/game-integration-aggregate.json`。本轮公开CPU控制55项通过（增量11、队列19、退出唤醒8、QA缓存种子17）；作者控制不代替游戏或GPU测试。

## QA 包缓存种子与当前边界

开发打包工具可用 `--gpu-cache-seed-manifest /Users/Shared/.../旧QA.app/Contents/Resources/ARM64-QA-MANIFEST.json`，仅从验证过的旧 QA 包克隆整个 `cache/` 与 `local-metal/`，禁止混合不同来源的 permutation 文件。原始上游 GPU SHA 与包内重定位 SHA 分开验证，签名、实际DSO边、CPU provenance 和工具/脚本闭包保持检查。原 QA 的进度、home、trophy、custom_configs 等不从这个种子导入；原基线配置/存档准备逻辑保持。实际本轮克隆4473文件/51,743,475B，源目录不变，正式数据从不参与。此选项只用于保留开发QA的编译进展，不是分发游戏编译产物。

最新已验证本机测试包位于工作区 `artifacts/arm64-qa-20261026-session-startup/Blood-Echoes-ARM64-Local-QA.zip`，SHA256 `bf4f9ae57d11d017a5f08459b85e7178f6f34f496d056e011ef7d51bdc580e05`。它依赖准备好的本机Shared树，不是可移植安装包，未上传Release。

仍需完成：其余辅助工厂最终PSO、全游戏完整状态目录、新用户无历史请求时固定helper首次收集，以及冷暖卡顿与峰值内存的匹配对照。不能把已完成整包消费说成这些项目全部完成。正式应用、正式存档/设置、已有缓存及Release保持不动；死亡/联网/菜单仍暂缓用户统一验收。


## 消除每页重复审核，先严格复用已有产物

重复审核修补已完成整包验证。PreparationSession只在本次prepare调用内保留一次真正`passed`且`built=0`的原完整审核，前后请求/收据/库的完整metadata集合必须稳定。每次复用仍持原builder锁，检查原保护、当前xcrun工具选择、二进制mutation pins和SDK/OS身份；record或任何变更立即失效，失败不缓存。SQLite metadata页512KiB，262144项/64MiB预算只禁止memo、退回原完整审核，不截断编译目录或缩缓存。临时ledger退出清理，不成为跨启动产物authority。SDK设置/工具身份不变的root手工SDK内部篡改不在这项会话metadata检测保证内，原native身份与实际产物准入仍独立保留。

真实448请求/已有库在全新Shared副本上测试，原缓存metadata前后不变，原build_id不变：首次完整审核16.328秒，随后同会话0.401/0.398秒；0编译、压力1、swap不变。这是实际工具/SDK/收据审核时序，不是作者假编译器的微测，也不是游戏FPS结论。

新工具上下文改变仍正常换新进度域，不导入旧hint。调度现在每页先用当前load环境、实际冻结输入和`allow_compile=False`做严格探测，不要求旧hint。必须所有实际函数和工厂内部event均为Local来源、完整身份、严格archive命中、0新建/编译，才能完成；Source回退或任何miss仍走原record/build/prepare，保护停止不吞掉。cold没有requests、环境仍record时不额外起probe。

新包原入口已通过：424配方全部严格探测成功、0record，原生496hit/0built/0failed；27次本机库审核只有1次full、26次复用，审核合计26.66秒，整体准备103.72秒。启动至同一可操作场景155.91秒（前轮519.31秒）；boot/content/patch/settings/camera完全一致，但旧轮仍补过5配方、4PSO，不能把整轮差额全部单独归于memo。场景90秒约49.70FPS、p95/p99 20.94/21.43ms，合计footprint5808MiB不是去重RAM，最高压力2、swap净增205MiB；没有据此宣称帧率或RAM收益。运行时527条Hit、25条Deferred日志与旧暖轮相同，538本机库命中/0源码事件，仍不能推断所有驱动编译零。

18项会话作者控制、16项strict-first、原26项builder及19项queue、11项增量控制通过。源/收据损坏、同字节替换并还原mtime、工具/SDK/OS变更、失败不缓存、预算禁memo、冷无requests、假hint、工厂内部Source回退和保护停止均有反例。完整游戏/审核摘要分别见 `session-reuse-game-aggregate.json` 与 `real-session-audit-aggregate.json`，同目录保留前轮汇总。新FormatConvert和阶段计数仍是独立私有工程，尚未纳入此包。


2026-10-26 更新：默认首启准备八类固定 Metal 内核，游戏目录改为整批收集后统一建库；原生及准备验收已通过，暖场景完成一次采样，匹配性能与内存对照仍未完成。范围、失败记录和复现命令见 [固定内核与整批建库](fixed-helper-startup.zh-CN.md)。
