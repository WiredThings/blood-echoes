# 当前生效计划：自动闭环收口，三项上游缺口不阻塞交付（2026-10-26）

已读取当前系统目标（active）、源码和最新进度。用户最新要求取消“三项全完成后九项才可整批接入”的门槛；未重建/清空目标，也未标记完成。下方历史中的该门槛不再执行。

1. 三项归上游与正式 Rosetta 既有缺口：保留两个默认关闭的门控候选及测试；完整sigreturn停止扩展。27/282不是游戏命中证据；未知导入仍明确诊断，不能隐藏统计或零返回。接口代理已停止 owned producer/417 专项，收口其未启用成果，不回退六项。
2. 原六项独立核对正确性和集成状态。主线仍是原启动器兼容与默认自动扫描→本机增量编译持久化→runtime复用，动态缺口正常捕获，不手工逐批补输入。
3. 已接原 final-exec 自动静态入口、模块controller与GS同队列接口；新 Mod/汉化 source-view 复用原合并算法，实际 APFS clonefile 作者正反例通过，不放宽原严格reader。35个准备工具与原parser已纳入冻结闭包。
4. 当前真正未收尾：完整companion新编译/暖命中封口与typed消费的自动增量接线；同一源码快照的全部235 GPU TU及模块/PSO组件构建与打包（目前只有plan）；原入口新安装/冷暖/失效/真实Local与PSO命中及长帧/启动/内存验收；随后集中匹配性能、最新包功能回归、干净检出构建和交付。

`c0815c81` 已推送开发分支；后续源码仍有未提交批次，收口后同步。不更新正式应用/Release，不动正式存档、设置和缓存。画质、持续内存及保护规则不变，目标保持 active。

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
