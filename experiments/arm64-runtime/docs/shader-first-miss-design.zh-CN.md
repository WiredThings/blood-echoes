# 首见 shader / 新特化实际提前准备：只读设计与源码草稿

2026-10-25。根代理正在串行重建GPU库。本 worker没有修改正在构建的15个公共源码文件，没有编译/执行测试或游戏、Cua或录屏。此目录仅存作者设计/草稿，不含游戏shader、目录原文、缓存payload或凭据。第一阶段snapshot接口仍未接scheduler；本设计不把拒绝接口或搬线程称作优化完成。

## 真实提前量入口和冻结边界

- `vk_draw_prep.cpp:247 Build`复制命令，`:404 WorkerLoop`重放Submission.commands及register delta，`:474`调用PrepareGraphicsPipeline。`liverpool.cpp:2760 SubmitGfx`在queue锁前做拷贝和DrawPreparation::Build，同一锁中编号/Enqueue。Submission及其shared owner是真实可复用的命令快照，不拥有shader/fetch/SRT指向的数据。
- `pipeline_cache_core.cpp:405 PrepareGraphicsPipeline`目前拒绝HS/tess，随后RefreshGraphicsKey；`:874 RefreshGraphicsStages`内AmdGpu::GetParams会在GetProgram的sigsetjmp之前读取客体code/header。新候选必须在这个调用点就换安全owned参数读取，不能只改GetProgram miss块。
- `GetProgram(sel.worker)`当前有371项缺失观测，完整pipeline尚不能ready时返回Unavailable；可在这里额外发布一个完整owned shader job票据。不要把PreparedDraw::Ready用于表示“仅有后台任务”，保留原已完成模块/管线的含义。
- 准备线程的regs/UD是本提交副本，但嵌套表不是。原native SubmitGfx命令复制和ShaderParams的code span都不能当作通用资源freeze。复制数据必须在实际capture时记录来源，之后worker只读owned数据；GPU采用时以当前顺序命令状态作权威重验。
- GPU当前`Metal::Rasterizer::Draw`先GetGraphicsPipeline再PostDraw；DrawPipe的DrawPacket只有已完成pipeline指针，并依赖pipeline->GetStages获取资源。故“在GPU首次命中才投递然后马上等”不能去掉唯一新VS+PSO临界路径。真正提前量必须来自现有DrawPreparation或另行拥有完整DrawRecipe的更大改造。本方案优先前者。

## 客体读取穷举与必须保持的正常顺序语义

| 实际入口 | 当前读取 | 提前捕获需要拥有/验证的东西 |
| --- | --- | --- |
| AmdGpu::SearchBinaryInfo/GetParams (`regs_shader.h:224/243`) | code[0/1]、token指向的BinaryInfo或最多0x4000字搜索、length/hash与程序span | 先读有界header，再验证加法/长度/位置；有界复制实际code，保留原program_base标量和真实code字节身份。不能让code[1]逃出预算或仅验shader_hash |
| BuildRuntimeInfo GS (`pipeline_cache_core.cpp` stage Geometry) | VS copy的GetParams/code span | 同一安全参数读取器复制完整VS copy；RuntimeInfo只保留owned span，另核对copy完整bytes和hash |
| Info.user_data / ProgramBase | 用户寄存器及inline buffer地址fixup | 16 DWORD来自同次regs副本；原基址只作数值，不在worker解引用。所有source span需指owned存储 |
| Gcn::GetFetchShaderCode/ParseFetchShader (`fetch_shader.cpp:47/56`) | UD中的代码指针；无界到S_SETPC_B64的解码；thread_local按地址缓存会memcmp原code | 新有界fetch-span解码器，捕获实际终止指令内全部字节及FetchShaderData；拒绝无终止、截断、>32属性；不能对临时arena地址复用原按地址cache |
| VertexAttribute::GetSharp → Info::ReadUdReg | sgpr指针指向的V#，加inst_offset/格式覆盖 | 记录每个原始V#，后处理在owned值上执行；大小/offset与同地址重读须一致。正常GPU路径在采用时再读当前V# |
| FlattenExtendedUserdataPass → GenerateSrt → RefreshFlatBuf | 根据SRT bytecode访问任意层嵌套表，动态offset也依赖先前flat值 | 临时owned bytecode+严格有界解释器；每个4/8字节读取都经checked reader，记录来源/bytes；栈/bytecode长度/flat offset/步数全部有界。capture模式不注册永久walker、不把缺页零填当成功 |
| HullShaderTransform/StageSpecialization/BuildRuntimeInfo LS → ReadTessConstantBuffer | V#、base_address常量结构 | 读取两层均需owned；现有DrawPreparation本来拒绝tess，第一版保持拒绝，不能称tess leadtime已实现 |
| ResourcePatching/SharpFetch/InfoCollection | flattened表和program-base fixup；最后InfoCollection还能添加BDA/FaultBuffer、UD mask和paged属性 | 必须运行真实pass顺序，不能在ResourceDiscover刚返回时宣称Info或bindings完整。所有descriptor读取转owned flat；采用时核对最终资源列表和精确binding end |
| EmitContext::DefineInputs/EmitSPIRV | 再次ParseFetchShader/V#；MotionVectors全局BDA | fetch/V#从owned输入复用；motion暂拒绝，后续显式捕获session地址并重验；不能漏掉emitter再读 |
| GPU PostDraw/CollectRingBindings | 后续按顺序复制常量、V#及pending read/write标记 | 采用后绑定当前权威UD/flat及V#快照，继续原PostDraw/等待策略。新shader准备不能借机使用陈旧backing bytes或跳过pending GPU write |

注意：GcnCodeSlice::readu32/readu64/at没有边界检查。只给fetch-parser一个短span仍不足以安全拒绝末尾半条指令；草稿要求检查编码长度/literal扩展后才解码，或让该slice在capture模式抛可恢复边界异常。不能依赖assert/越界后fault来声明输入有效。

## 不让siglongjmp跨越C++所有者

`capture-boundary-api.draft.h`给出具体POD leaf：每次原始copy在独立noinline叶函数内setjmp/memcpy/恢复previous handler；它的上层保持slab/lease/Info/Pools/CFG/vector所有者。fault在leaf内被恢复并返回false，**叶函数返回后**才通过普通C++异常传播CaptureUnavailable。这样不需要把TranslateProgram/StageSpecialization包在一个大setjmp里，也不会跳过这些对象的析构。

新slab和read-receipt owner在WorkerLoop外服务/slot对象中、任何recover点之前创建；publication只移动owner，不复制到第二份vector。现有GetProgram大guard不应承载新的capture所有者。现有ReadSpeculativeVertexSharp已使用近似POD叶边界，可复用同一恢复指针语义，但不能直接在它外面挂一个会被longjmp跳过的vector。

安全copy只解决故障恢复，不等于内容冻结：runtime_memory_generation仅覆盖map/unmap/protect，Tracker::Owner/Status是有界元数据查询，均不是活跃allocation pin或content lock。read_backing会对gap填零且可能绕过GPU read trap，不能用来授权完整shader快照。新provider必须区分tracked allocation/immutable image/unknown；before/after确认同一owner和映射，记录完整read-set，对重复/重叠读取见到不同字节直接拒绝。若缺少权威owner或遇到GPU no-access/pending write，不可在prep worker等待GPU读回，应回原顺序GPU路径。成功的provisional字节仍需GPU当前顺序读取逐项验证才可采用。

**正常路径中所有guest读取仍以ordered GPU为权威。** 提前线程的读取只是可丢弃的快照候选，不赋予它执行/资源提交权；不修改原guest数据。若要求跨读取完全原子视图，现有接口还缺content/allocator epoch与live-read lease，必须先新增真实provider并验证锁顺序；不能把两次memcmp当成该保证。本阶段明确没有实现这层provider，亦没有声称已知命中率或覆盖所有首见程序。

## 首见程序如何复用真实编译而非总是拒绝

首见程序并非只有“无Info就永久拒绝”一条路。独立capture-mode前端从owned code和regs开始，真实EmitFetch读钩子拥有fetch/V#；同一ResourceDiscover生成SRT计划，临时有界解释器读并拥有SRT/flat；后续真实ResourcePatching、最后InfoCollection及EmitSPIRV完成最终Info、binding end和SPIR-V。其背景Metal阶段只接owned SPIR-V/完整recipe。这里不需要已有program_cache模板；已知program的新spec可省掉重复布局发现并验证SRT字节。可支持普通VS/FS首见/新spec；tess、无法稳定读取、超预算等仍正常回退。

真实实现需要两个输入模式：`CaptureReader`（只允许在有保护的捕获相位访问guest，记录read-set）与`FrozenInputs`（完全禁止guest读取）。不能把当前CompileSnapshot里“已有SRT必须相等”删掉让未知布局无条件通过；应新增明确capture-plan分支，生成并持有临时bytecode，再冻结到FrozenInputs。背景worker只运行后者。`integration.draft.md`给出了首见/已知程序两路、API及具体GetProgram调用序。

为了不重复真实前端工作，优先在现有prep线程上跑一次受控的Translate+Emit，专用worker只做真实Metal驱动编译。整个服务一次仅一个捕获者、一个专用Metal worker；不是让全部原prep workers都并发重编译。若将IR后半段也转交worker，必须heap-stable拥有CFG、block pools、IR::Program的Info引用和ResourceDiscoveryList的IR指针；不能返回引用局部CFG的半成品。精确end bindings必须采用真实Emit返回值，不能只用Info.AddBindings猜测：InfoCollection会增资源，且Emit的image NumBindings可不等于images.size。

## 一worker、一pending、输入合计2 MiB

- 总共至多两个job lease：一个active/ready结果，一个pending/capturing；只有一个专用Metal线程。pending包括正在捕获的entry，防止5个旧prep worker同时分配快照。另一个capture frontend上下文仅有一份、串行使用，不借GPU线程的Pools。
- 所有输入slab实际reservation capacity合计<=2 MiB，包括read receipts、code/fetch/GS/SRT/UD/flat、Info元数据和SPIR-V。必须在alloc/grow之前扣款，失败直接回退；不能先分配2 MiB scratch再复制成另一份2 MiB CompileSnapshot。可按已知header/模板尺寸分阶段reservation；owner转移零复制。内存账本按capacity计数，不能只计vector.size。
- `Empty→Capturing→Queued→Compiling→Ready/Failed→Adopting/Discarding→Empty`，slot token含单调generation防ABA。nearest submission/draw优先；busy直接Unavailable，不建第三项、不淘汰现有shader cache。
- 正在Capturing时GPU到达：GPU不等不完整recipe，标记cancel并正常同步；捕获完检查cancel立即丢弃。Compiling时只有完整当前recipe已验相同才可无锁等待，避免和同步CompileModule重复driver工作。其它spec/key不等直接原路径。
- prep worker从不等GPU采用、DrawPipe或readback。专用worker不持programs_mutex/Storage/PSO表锁，不调用GPU任务；GPU等待前释放全部相关锁。Stop先禁止新capture，通知cancel、停止/加入prep workers，再join专用compiler，最后销毁maps/backend/device。
- 2 MiB是owned输入总量，不是整个编译器/Metal峰值；IR Pools/CFG与driver临时分配仍需实测。只保留最多两项detached结果，绝不写未采用缓存。若需硬IR上限，应为此独立context的ObjectPool加字节/节点预算并可恢复拒绝，不能修改原同步编译器的额度或画质。

## GPU完整身份重验及提交

1. 快速过滤submission token、draw ordinal、register checksum、hash/阶段；这些都不是采用证明。
2. 当前code完整字节/原program base、Profile语义、真实runtime各字段、start/end binding、SRT字节/flat、UD及完整read receipts、fetch结构和原V#、GS copy/tess、resource specialization均须匹配。不把session pointer序列化或padding raw hash当语义键。
3. 不单凭现有RuntimeInfo::operator==：Compute比较没有shared_memory_size；GS比较num_invocations使用非零判断并省略若干字段。新receipt需显式完整字段，不能沿用这个宽松等价充当全key。StageSpecialization也是按已知资源布局设计的查找谓词，不能替代原code/资源结构一致性的证明。
4. 在GPU线程重新选择/核验当前Program::modules索引，permutation序号只在当前cache代际有效。必要时把临时SRT bytecode注册一次，Info清掉transient compile_snapshot/source_reader指针，UD/flat/base重绑当前权威状态；spec.info必须重绑到Program::info，不能留下job中被销毁的Info地址。
5. AdoptOwnedShaderModule之后按原顺序AddPermut/创建info_template、RegisterShaderBinary/Meta。失败/同项已同步编译则丢弃detached对象，不污染program/modules/PSO表。shader采用后继续原GraphicsPipelineKey构建，PSO仍可同步；不能称已解决83.7msPSO。
6. PSO提前准备需捕获完整非shader GraphicsPipelineKey与Metal顶点序列化属性、各阶段完整recipe、fetch/geometry/tess变体依赖和Profile。后台recipe不能提前把guest hash伪造成最终stage_hashes；GPU确定perm索引后映射到现有key。现有DrawPreparation在Metal仍可能生成prepared key，只是缺模块时不可用；扩展此分支须验证静态vertex input，不能伪造dynamic能力或跳绘。

## 定向测试计划（此worker未执行）

- leaf recovery：mmap两页后第二页PROT_NONE，精确copy成功/跨边界失败；fault前后previous runtime_fault_recover一致；上层slab/lease/CFG/vector析构计数完整，重复一万次allocated-live归零。
- first-program控制：缓存为空，自造包含fetch和嵌套SRT的GCN在真实DrawPreparation replay发现后生成owned job；完整program/table源毒化或mprotect之后，真实前端/Metal只读取frozen数据；GPU当前exact输入采用，代码/表/owner变化拒绝并原路径输出与baseline一致。
- parser完整负例：BinaryInfo offset/length溢出、所有code/fetch截断、缺S_SETPC、末尾半条64bit/literal指令、>32属性、SRT未知opcode/无End/超步数/栈溢出/越flat/dynamic offset溢出。都可恢复拒绝，不abort、不注册永久walker、不写cache。
- leadtime真实性：用latch让prep frozen job先开始driver工作，再让GPU抵达；记录发现、capture结束、编译开始/结束、GPU采用、完整recipe id。负例同hash不同code/runtime/fetch/Profile/descriptor/owner均不采用。单new shader与双new stage分开，不能拿同draw小重叠替代早发现收益。
- 边界预算：2 MiB恰好、+1、两job合计、reserved capacity大于size、capture失败/取消/异常/完成未采用全部归还；第三job立即回退。并发只一个Metal compile active和一个pending/capturing，Ready结果也占slot。
- 死锁/退出：GPU等待时无programs/cache锁；compiler不调用GPU；source free/reuse、commandwrite/pendingGPUwrite、frame/F3 shutdown每个状态interrupt控制；不改变原同步绘制数/颜色/图像/分辨率。
- shader键：compute shared_memory_size、GS invocations/mode/primitive等被旧operator省略的字段，完整recipe变化必须拒绝；permutation插入重排/已有同步项/旧Info地址均有负例。
- 最后才是真实首次探索同路线门控对照：新spec/newprogram成功采用率、capture开销与提前量消耗、driver时间被隐藏多少、PSO余下时间、p95/p99、启动与去重footprint。当前设计没有这些结果。

## 额外可实施草稿

- `owned-program-params.draft.h`：用真实BinaryInfo结构生成checked/receipted的owned程序参数；包括offset/length/整数溢出和512KiB上限。Reader必须由真实slab/provider实现，不提供假成功；header搜索与code读取都受同一预算。它替代prep-selector进入GetProgram之前的raw GetParams，而不改正常GPU路径。
- `owned-gcn-slice-bounds.draft.patch`：仅针对owned capture显式开启的GCN slice边界异常草稿，覆盖u32/u64/literal读取截断前置检查；未应用、未编译，仍需配合有界fetch span和非fatal非法opcode处理，不能单独当完整修复。
