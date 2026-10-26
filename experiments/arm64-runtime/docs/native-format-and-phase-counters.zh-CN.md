# 固定格式转换工厂与实际 Metal API 阶段计数

2026-10-26，隔离 ARM64 开发版。正式应用、正式存档/设置、既有缓存和 Release 保持不动。这里区分原生回归、实际游戏整合与尚未覆盖的输入；没有把小样本当作白模根因或全游戏预编译证明。

## FormatConvert 复用原工厂

RGB/Depth 两个固定计算管线接入 HostKernels 与原独立 PSO 产物，新增 kind 12/13。原 MSL 2783 字节、Metal 3.1/Safe、零特化常量、双 entry 验证、buffer 参数和转换算法保持。Runtime 显式传入原主 kernels；Converter 仍是两个管线槽，温态命中不增加查找。借用不跨 owner 寿命，原 Finish 与成员析构顺序保持。无新增纹理、资源池或常驻缓存。

真实原 Converter::Encode 的7个独立进程通过：9种RGB、D16/D24/X8D24双向字节/float-bit oracle，非对齐offset、row/slice padding及全buffer guard；同一个Converter交替RGB→Depth→RGB，证明两个槽复用。Source/本机库/严格归档两条PSO均验证；仅保留RGB、移除Depth的库必须连RGB也退回Source，恢复后原产物SHA不变。断言大多为逐字节检查，不是十万条独立管线。

## 计数范围和成本

35个当前实际编入的原生API位置包含：源码建库、metallib data加载、render/compute PSO创建，以及binary archive addRender/addCompute。默认ImGui Metal的render创建也覆盖，它是原230TU中的unit228；Metal4后端未编入，不能把其源码位置算成已测运行路径。

只在这些冷工厂的原直接调用前后计数，保留原参数、NSError、返回值和ARC new-family直接赋值。归档BOOL调用可经有界小包装；禁止对象指针经该包装返回。计数数据为8类×2阶段×4个u64 atomic，共512字节加小状态/锁；不持有GPU对象或源代码，不增加每draw hash/资源查询。真实API执行时不持统计锁。

阶段以原远端GPU-ready事件的**入场时间**划分；ready前在途调用完成后输出一次settled汇总。ready后包括菜单和场景加载，不等于已经可操作。非远端模式若未经过该事件，`metal_gpu_ready_seen=0`，不能把其runtime零读成无编译。

17个追加CSV字段记录source/local成功失败、strict尝试与命中、ordinary render/compute、archive add和各自API wall time。既有列名不改。墙钟包含驱动等待，不能当成GPU执行或纯编译时间。newFunction常量特化、archive open/serialize及MetalFX内部驱动工作不在这组计数内；不能据这些字段宣布所有Metal内部编译为零。帧CSV额外快照/格式化的整场景开销仍需匹配对照，不以纯CPU微测冒充免费。

## 当前验证

原生源/头一致的230TU构建为 `a4d1ddf1…`，258秒；新30CPU单元、2个AvPlayer保留单元及1个匹配GPU main均配对构建，协议007/Live6不变。Format7、Camera实际RecordMotion7、PostProcess7及既有核心冷暖/坏输入11，共32独立进程通过。均用新Shared可执行文件和状态、每PID实际加载指定库；压力1，构建/小控制swap无增长。没有测这些进程的峰值RSS，也不把它们称游戏性能提升。

纯C++阶段/并发/异常控制和3组ARC结果SSA对照已执行；原作者总报告器在断言后因局部变量覆盖退出1，失败日志保留，修报告器后未重跑整套，不声称那个总进程exit0。新原生构建、32进程控制及下述游戏/CSV是独立验证。

首次新cohort原入口游戏通过，但600秒只准备336/404项，不能称全目录完成；游戏进入场景约632秒，稳态48.64FPS、p95/p99 21.35/21.93ms，合计footprint约5940MiB（共享页重复计数），最高压力2，swap增约460MiB。与全部本机库已暖的前轮相比，不是匹配的FPS/RAM收益对照。

实际CSV有5785个完整行、17个新字段、ready_seen全1。唯一不完整行为计划143退出打断的最后一行，原benchmark的complete_rows已过滤；原CSV未改，没有中间坏行。ready前96次source、451次local data、406次strict成功、98次ordinary PSO；ready后11次source和35次ordinary PSO，失败0，最后事件在CSV t=6.015秒，最后30秒这些API事件为0。零尾段只证明本静态场景的已计数API，没有覆盖跑图或所有驱动阶段。

汇总见 `evidence/native-format-and-phase-validation-20261026/summary.json`。只提交项目源码、作者fixture与去游戏内容的汇总；不提交Meta/SPV/MSL、AIR、metallib、PSO、游戏/存档/凭据或完整游戏日志。

## 仍在推进

冷准备目前逐页record会使本机审核会话反复失效，本轮42次full占约430秒；正在改为有界跨页收集，再集中建库/末遍验证，保留暖态严格快捷和原保护。尚未把该新草稿计入上述实测。

首次用户没有历史h2时，固定helper仍需从原工厂收集；不能把13个已接kind或当前404项说成首启已预编译全部。8个静态可确定kind的首启入口在独立私有草稿中；需要实际dispatch/format/sample输入的动态kind不猜默认。其余helper最终PSO、全游戏完整状态目录、匹配冷暖尖峰/内存和现有未完接口仍不等于完成。死亡/联网/菜单由用户稍后验收，白模不扩大新根因假设。


### 同包暖重启结果

第二轮已正常结束：424/424当前可准入配方准备完成，仍有74条需要record（其余350条直接strict），7次full/26次session复用，准备241.31秒、进场324.55秒。运行时538条本机MSL命中/0源码事件；实际API统计也确认ready前后source调用均0，ready后13次strict全命中，另2次ordinary PSO总约0.963ms，warm最后30秒这些API为0。5785完整CSV行及仅终止末行截断的结构再次通过。

场景48.54FPS、p99 22.67ms、GPU19.41ms，合计进程footprint5967MiB，最高压力2、swap+9MiB。先前c2ec包约49.70FPS/5808MiB；当前差约2%和159MiB只是跨轮观察，尚未匹配A/B归因。89项环境对照仅9个隔离路径/nonce不同；3个严格同program/stage/完整特化顶点样本的SPIR-V/MSL/离线有效源码逐字相同，不能外推全片元/compute。没有确认是新代码性能退化或内存增长，也没有把差异当噪声抹掉。两轮原生游戏整合通过不等于性能/峰值RAM验收；相关原始报告保留。

最新原生测试包为工作区 `artifacts/arm64-qa-20261026-format-counters/Blood-Echoes-ARM64-Local-QA.zip`，SHA256 `0d86536fad07767d68ab938e2950e307012c79c878a97ea2e6bdd5f74d46e9f5`，依赖本机Shared开发树，不是可移植安装包，未上传Release。它包含本节已测a4原生功能与上一版逐页工具，不包含仍在私有验证的两遍队列/首启种子。
