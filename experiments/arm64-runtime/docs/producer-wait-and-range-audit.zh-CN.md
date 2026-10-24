# 工作线程依赖与完整纹理范围诊断（2026-10-25）

本轮只改隔离原型及诊断工具。正式应用、存档、设置、缓存和 Release 均不更新。所有自动动作静音，未用 Cua/录屏；诊断数据不是性能收益验收。死亡重生、联网、菜单实测按用户要求暂缓。

## 修正此前诊断盲区

显式 render condition 模式压制了重复的 generic condition HLE scope，但此前只纳入 render waiter。producer 自己进入 cond_wait 时没有对应记录。因此旧报告里“没有 HLE 等待行”不能排除 producer 在等游戏任务。新增默认关闭的 BB_PRODUCER_WAIT_AUDIT，只跟踪已向合法 render wait 通知过的 producer，不靠名字猜角色，不解引用 guest 条件变量参数。

- producer wait >=20ms 才发固定行；时间/CPU回退按不完整诊断处理，不变更原 adapter 返回值。
- 固定8槽、30位不复用 token；记录与同一参数槽匹配的活动 wait 的 signal/broadcast。包含返回码、operation、线程号和原生线程名。
- 关联只表示候选通知：不假定某次广播唯一导致唤醒，不解析 guest pthread 对象别名；未匹配不代表没有通知。
- 默认关闭时没有这些计时或 registry 访问；不改变调度、条件变量、内存预算或渲染策略。

完整重建55个被基础套件调用的二进制后，119项基础回归通过；另外7项缺失data导入、19项libc跨度、17项真实FEX退出+6组原监督器、ABI与时钟校验通过。依据 current-runtime-regression-1792878475597841000。当前CPU SHA256 5c273a497e2e5a9b036a9b9c4062400b1d35512eba3edbd61696431ddc918ff7。新增策略测试包含默认关闭零时钟、阈值、异常退出、满槽、token耗尽、ABA及4000次并发租约；真实pthread集成例保持调用/结果。

## 三轮实际游戏记录

1. launcher-engine-abba-1792877811372103000：162.517ms帧，GPU1.52ms；render condition159.693ms，producer condition86.664ms，producer该调用CPU仅0.009209ms。同一参数槽另有26.84ms等待。新记录证明其中有真实 producer HLE cond_wait，不能继续全部归为翻译/绘制。该版尚无worker关联，不声称原因完全查明。
2. launcher-engine-abba-1792878675038582000：完整记录，没有>=20ms producer wait或>=40ms帧；没有可归因通知。
3. launcher-engine-abba-1792878936530243000：producer condition21.115ms、CPU0.006375ms。同PID同token9852同参数槽的 runtime.posix_cond_broadcast 在结束前约18微秒开始，返回0；原生线程名 CSChrThread2。该重叠区间没有记录到FEX编译或Metal编译。是游戏工作线程依赖的线索，不足以证明它造成之前全部长帧，也不能从名字推断具体算法。

原CLI sample 12秒/5ms运行成功但输出15,287,767字节，超过8MiB诊断预算，所以 launcher-engine-abba-1792877031948712000 整案维持失败，未改成通过。raw栈里能看到 producer 与 CSChrThread2 的原生 pthread_cond_wait，但JIT展开存在大量未知/重复帧，不能据此统计精确执行占比。工具现限制为6秒/10ms（8MiB与30秒上限保持），自造busy/wait进程1秒/10ms测试通过；尚未用新默认再采游戏。无GUI、只允许本轮拥有且路径匹配的PID，采样仅用于诊断，不接受为FPS对照。

## 纹理探针扩大可见范围，未更改像素逻辑

原局部写入探针也沿用了函数自身的 first-byte 查询，会漏掉“写入起点在纹理前，范围穿过该纹理”的情况。新诊断单独在锁内遍历完整声明范围，原 first-byte 标记循环原样保留。门控关闭时不走额外范围；最多64条、溢出安全、无payload拷贝。记录只是潜在GPU写绑定，不能当作已完成写入。

partial-audit-candidate-1792879325111918000 仅替换一个诊断对象，不打包为默认渲染器。

first-upload-regression-1792879347321383000 首次直接运行缺少强制私有GPU状态路径，6例均由构造期保护以78拒绝；未读取默认缓存，不算像素结果。随后 test_partial_audit_controls.py 为每例新建 Shared/state 私有目录：partial-audit-controls-1792879413531232000 六组诊断控制通过。

- 零长度、只声明无实际写入、实际首像素/内部/跨开头局部写：各准确记录一条，保留旧像素失败exit7。
- 真实整图覆盖：无partial日志，像素exit0。
- 这证明观察范围扩展正确、原行为没被偷偷修掉，**不是白模已修复**。

新范围探针的游戏轮 launcher-engine-abba-1792879424293034000 使用另一个既有QA seed engine-user-1792844166685513000 的新副本，源savedata摘要不变；17.19秒出现真实scene camera，门控启用，partial entries仍为0。该轮有128.383ms帧，GPU13.78ms、compiles0、FEX block并集0.649ms、render wait112.968ms；没有>=20ms producer condition。说明 producer 长等待只是部分卡顿来源，不能通用于所有长帧。没有新增截图或独立确认敌人命中，不称为玩家白模场景复现。

## 下一步

优先追踪 CSChr 工作依赖的活跃时间与阻塞，以及另一类未落入producer condition的低CPU长间隔；同时等待真实白模现场或更确定的战斗隔离路线。不要为了让定向负例变绿而整图丢弃GPU新内容、全量读回或扩大副本驻留。保留首次上传hash修复；范围校验候选继续默认关闭且不打包。没有新安全优化收益证据，不修改同步语义或默认画质。

血源专用FEX/Metal协同长期目标保持：按实际热点和资源代际/写入范围减少重复工作，未知条件回退；删除未用x86处理器不等于热路径更快。原专项研究见 docs/fex-specialization.zh-CN.md。


## 最新本机 QA 包与只读验收

本机应用：apps/Blood Echoes ARM64 GUI QA 1792879623833144000.app。压缩包：工作区 artifacts/arm64-qa-20261025-producer-checkpoint/Blood-Echoes-ARM64-Local-QA.zip，42,813,803字节，SHA256 6690a935bc2f0f960ca92eb69144d5ccf39e29b487cd00a9a297043e4d4dda0a。仅本机可用，仍引用Shared私有游戏/汉化/依赖资源；不是独立安装器。CPU来源5c273a49，GPU仍为first-upload修复库，未包含full-range诊断库；诊断和QoS默认关闭、离线静音、保留物理输入。

解压后严格签名、四个真实FEX自造退出例、arm64 Python与手动输入策略通过。冻结组件实际90秒场景加载 launcher-engine-abba-1792879680245033000 通过：17.53秒scene camera，末30秒48.66FPS、p99 22.69ms、GPU19.25ms；聚合footprint5817MiB（含共享重复计数），压力最高warning2、swap增量0.81MiB。单次冒烟，不是改动收益A/B或白模/死亡/联网/菜单验收。

正式应用/存档/设置/缓存保护快照11671文件与目标起点完全相同，注意不是11671份存档；临时GPU别名恢复原路径。限额最新剩余46%，未触及15%暂停阈值，无Release更新。
