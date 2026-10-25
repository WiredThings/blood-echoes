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
