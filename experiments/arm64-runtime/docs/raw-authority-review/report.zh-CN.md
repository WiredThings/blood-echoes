# 布局重解释时 raw 权威来源与流内排序（只读审查）

结论：可以设计一个有界的 `旧 GPU image → 既有 raw backing（只覆盖逻辑 texel）→ 新 layout image` 闭环；现有 TileImage 的边界候选补齐了“不要破坏 raw padding”这一必要条件，**尚不足以直接把 FindImage 所有布局冲突替换为自动转换**。最大缺口是判定 raw 与 image 的最后写者、严格锁定旧对象身份/映射代际，以及明确传输是否成功。当前 image Dirty/GpuModified 与 buffer 页状态不足以单独证明所有重叠写顺序。

本目录 source-identity.json 和 reviewed-excerpts.txt 固定实际源码与行号。本次不修改共享源码、不编译、不运行游戏；c607候选24GPU控制通过是根代理已给出的前提，不是本审查新增运行结果。

## 1. 三种“内存”必须分开

1. 游戏 CPU backing：可能仍是旧值；统一内存不保证 CPU 虚拟地址中的副本等于 Metal mirror，更不等于 GPU image。CPU hash 也只能说明这个 backing 的字节。
2. BufferCache raw：可为 in-place guest chunk、VRAM mirror、arena、跨 chunk gather 或临时 staging。只有取得对应最新的实际raw范围，才有保留 padding 的意义。
3. Metal image：可能含只有 GPU 写过的 texel，可能还有 scene proxy 比 native 更新。GpuModified 表示 image 曾被 GPU写；GpuDirty 表示 raw buffer 的GPU内容要求重传 image，语义不同，不能混用。

image.h:29–35 明确 Dirty=MaybeCpuDirty|CpuDirty|GpuDirty；SafeToDownload(:136–137)仅在 GpuModified 且无任一 Dirty 时成立。CPU和GPU历史标记并非完整“每字节最后写者”。

## 2. 真实生产调用链

### buffer消费者要求image导出

rasterizer_core.cpp:2343 以 `ObtainBuffer(base,size,is_written,is_formatted)` 取得shader buffer。texel只读路径可在 LayerBind(:313/:388)、in-place ObtainBuffer(:1032)、普通 SynchronizeMemory(:3104)、跨chunk gather准备每片(:967)触发 `SynchronizeMemoryFromImage`。

后者 buffer_cache.cpp:3190–3272：先处理HTile元数据；按registry generation缓存miss；`FindImageFromRange(device_addr,size)`；BeforeImageAccess；按旧image所有mip建canonical copy；TileImage写既有目标；DropShadows；某些模式NoteGpuWrite。

**不应直接复用此函数作为布局转换事务**：

- FindImageFromRange默认ensure_valid=true，过滤SafeToDownload；它按基址选择。只有一个候选时可忽略size(:873–876)，多候选才优先精确guest_size；完全不匹配布局/UID/代际。转换时须显式旧ImageID/UID，不能再次按地址猜来源。
- arena容量可导致mip前缀break(:3226)，不是全链保证。当前bounds仅接受完整canonical链，前缀退旧路径。因此转换应拒绝任何不完整范围，而不能把true当完整导出证明。
- TileImage/DownloadImage返回void，SynchronizeMemoryFromImage即使下载未发生也可返回true。须有私有明确success/unsupported结果或严格已证明格式前置检查。
- DropShadows和NoteGpuWrite不是完整的 `memory_tracker/gpu_modified_ranges/WriteTicks` 替代。NoteGpuWrite(:2474)需3/60帧才提升策略，不是立即原始内容有效标记。

### 为image取得raw输入

RefreshImage(:1157)调用ObtainBufferForImage→DetileImage→UploadImage。ObtainBufferForImage(:1159–1242)先尝试LayerBind镜像/in-place；再处理GPUmodified→ObtainBuffer(false)；preupload arena；最后可直接异步CPU staging。

所以 `ObtainBufferForImage` 不能作为“existing raw含最新GPU内容”的无条件保证，更不能在旧image导出后盲目重取一次：后一次可能选到CPU staging，丢掉刚写入另一mirror的内容。最小闭环应把第一次取得的**同一个 buffer+offset+range**明确传给后续DetileImage，不再走地址重新解析。

本代码中 BufferCache 没有通用 GetBuffer(addr,size)入口；真正路径是GetArena/LayerBind/LayerInPlace/ObtainBuffer。这些不同所有权不能用“GetBuffer”一个名字掩盖。

## 3. raw padding从哪里来，怎样保全

ObtainBuffer(is_written=true)不是“整段discard”。普通arena路径先EnsureResident与SynchronizeMemory(:1089)上传CPU modified页，再加入gpu_modified_ranges与WriteTicks；Mirror LayerBind(:335–340)同样先同步CPU页再记录GPU写；in-place直接指向guest chunk，并记录write tick。

当没有CPU新写、没有更新的raw GPU buffer写，现有raw保留原始padding；bounds版TileImage仅覆盖旧布局逻辑texel的地址，padding保持raw中的旧权威字节，然后新layout Detile读取同一raw。因此能够保全“新layout把旧padding变可见”的数据；CPU backing不必为了GPU texel回读而更新。

但获取目标自身有副作用：Mirror/arena可能换residency，mixed/chunk可能copyback/demote。Metal不支持旧4GiB sparse arena fallback；可写范围跨chunk不能用read gather冒充完整可写raw。首版应只接受一个完整稳定mirror或一个完整in-place chunk；不隐式扩缓存、不创建第二份永久raw，不使用只读stream snapshot作为可写目标。

padding必须基于最新raw，而非零填或CPU全量覆盖。若padding也有后续GPUbuffer写，必须保留该写；若logical texel的raw有晚于旧image的GPU写，则不能再让旧image覆盖它。现有bounds只区分位置，不含写入时间；这两种权威问题不能混为一谈。

## 4. CPU部分写、hash与GPUraw写的顺序缺口

- CPU fault使重叠image CpuDirty或MaybeCpuDirty，并untrack(:243–273)。MaybeDirty的检查只hash最多8×8样本，不能充当完整内容/代际证明。
- RefreshImage对GpuModified的每个mip记录CPU backing hash；未GpuDirty且hash相同跳过上传(:1123–1129)，首上传hash已补齐。hash不同则**整个mip**从raw来源重传，非按CPU修改texel合并。无法仅凭该hash拆分“GPU image改了A、CPU仅改了B”双方数据。
- Buffer MemoryTracker按页决定上传；Twins可在buffer路径保留CPU未改变的GPU bytes(:3025–3055)，但没有给image+raw建立每texel双写合并语义。不能宣称Twins自动解决image别名。
- InvalidateMemoryFromGPU(:293–317)只查看写入基址并要求image.guest_address==address；max_size不用于完整overlap判断，源码TODO为subresource。rasterizer raw storage写和DMA copy均调用此入口，但内部offset/后续mip写可能未使image GpuDirty。因此 `GpuDirty==0` 不能证明“无更新raw写”。
- BufferWriteEpoch每次AccessBuffer写或AccessGlobal shader写递增(:977–1037)，能防止事务内后续未预期写/复用，但image没有记录一个可与之比较的完整raw-range writer epoch；全局epoch变化也可能无关，不能直接按新旧数值排image/data最后写者。
- source identity(UID)、registry generation、allocation generation、mapping generation各有不同用途。registry只随register/unregister变化，不能证明同地址不是新allocation；Owner tag查找也可能未知或被collector重新关联(:1585)，不能把tag当前相等自动当旧GPU内容属于当前payload。

首版准入应拒绝CpuDirty/MaybeCpuDirty/GpuDirty；拒绝未知/更换/释放/重用的allocation或mapping；还需要覆盖范围的GPUraw写证明，至少带有image最近写时刻与可重叠raw写序列的有界记录。若没有这种记录，先限明确由控制建立的无raw写case，不应声称一般运行时安全。

## 5. 流内有序最小闭环（设计，不是现有实现）

A. 先BeforeImageAccess/DrainDrawPipe完成绘制准备，再按现有锁顺序抓旧ID+UID+backing、requested/old精确layout、allocation/mapping标签、flags/roles、range writer stamp。FindImage当前已持普通std::mutex(:712，header:449)，不能再调用会锁同一mutex的UpdateImage，不能在锁内等待需此mutex的helper。需要显式 `...Locked` 内部helper或外层两阶段事务，避免重入。

B. 保留旧image，取得一个完整真实可写raw目标，执行正常CPU-page同步，但 `is_texel_buffer=false` 避免再次按地址FindImage/递归导出。再次核验flags/UID/owner/mapping；任何变化拒绝。is_written=true是RMW访问而非discard，但仍必须在写前证明buffer capacity和最新padding来源。若获取路径触发promotion/mixed fallback，首版拒绝或完成其现有copyback再重验，不能使用未经验证的“GetArena兜底”。

C. EndRendering，在当前GPU stream明确 `old image native/proxy coherence → Download/TileImage(bounds) → AccessBuffer(raw,write) → FlushBarriers/compute transition`。仅接受已证实exact copy-compatible single-sample canonical color；旧image只读，不立即Free。Bounded参数须明确确认启用，不能不知情退legacy全raw写。

D. 用**同一raw handle/offset**直接DetileImage(newInfo)。Detile已EndRendering/FlushBarriers并记录raw读和linear staging写(:143–167)。接着UploadImage(new,linear,copies)，其Transit/IsBufferAccessed/FlushBarriers/AccessBuffer处理写后读。无需CPU Finish或全raw CPU回读；所有对象/暂存继续由当前tick deferred生命周期持有。

E. 明确传输成功后提交：新layout写入后的GpuModified属性要保留（数据来自GPU，仅CPU backing旧）；Dirty只在真实上传成功后清；mip_hash记录对应CPU backing基线，不能把GPUraw hash冒充CPUhash；建立新CPUwrite tracking。对不同mip布局重新按newInfo计算baseline，不能复制old mip hashes。避免后续无关CPU dirty把旧CPU内容写回GPU texel。若目前hash无法在并发CPU写下建立一致快照，需保持保护/epoch并重验，失败则不提交。

F. 最后注册新UID、更新所有memo/view/backing/registry引用，按正常Untrack→Unregister→deferred Delete旧对象；old/new应有事务内明确状态，不能让FindImageFromRange在两者同时注册时任选。两阶段过程中旧image不丢，失败不清状态、不返回未初始化新image。

这套闭环应有常数次retile/detile和原池暂存，不能预分配raw全副本/逐像素mask；短暂同时保留old/new backing有内存峰值，需以现有预算准入及一次事务限制。对不支持路径维持原资源/显式错误诊断，不能静默返回空白。

## 6. 第一阶段必须拒绝的身份/角色

| 类别 | 首版准入边界 |
|---|---|
| 合法视图 | sRGB/UNORM bit-compatible typed alias、0039/#5133支持的等价规范化1D/2D继续走现有view，不强制转换/拆对象 |
| layout变更 | 同完整guest范围、可支持的两布局、相同bit宽与可证明raw意义；不同base/subresource/size/不完整mip拒绝 |
| 代际 | UID/backing变化、tag未知/Released/Reused、mapping别名/重映射、owner只覆盖部分、证据历史丢失均拒绝 |
| Dirty | CpuDirty/MaybeCpuDirty/GpuDirty任一存在拒绝混合导出；先各自解决权威数据，不能用旧image全量覆盖 |
| 绑定/写者 | is_bound/is_target/needs_rebind/force_general、当次storage写或待定draw/prep引用，首版拒绝；它们不是简单free条件 |
| 目标/专有副本 | render_target/depth_target/vo_surface、scene_proxy、depth_id/depth_uid、MSAA、多backing选择未定、速度图/历史角色不能只按地址转换 |
| 格式/维度 | BC/depth/stencil/volume、format substitute导致非exact transfer、转换函数无明确结果、部分layer/mip等保留原行为 |
| 多别名 | 同raw范围存在多个GPU-authoritative image或内部GPUraw写顺序不明时拒绝，不能先删其它候选 |

storage/render target历史usage位不一定表示此刻在写；第一版保守拒绝的原因是尚未证明角色的writer顺序，不能把它们判成永久不可转换。后续可专门放开一个类别并补独立控制。

## 7. 最小验证与可安全优化机会

先构造不启动游戏的真实Metal控制，不直接改FindImage：

1. 已知GPU非均匀image，CPU旧0x11，existing raw padding含另一非零模式；同一raw完成old→raw→new，旧padding变可见仍精确，CPU始终旧。
2. retile前后分别插入raw logical/padding写：padding新写必须存活；晚于image的logical写必须拒绝转换或保持新写，不能由旧image覆盖。
3. CPU仅写padding、仅写logical、shared-page无关写分别检验拒绝/重验，hash一致不能替代实际像素验证。
4. 旧/新allocation generation、同VA不同mapping、旧UID已retired、同时两GPUimage：全部负控；合法sRGB/1D2D仍应复用。
5. 明确形状/格式不支持、backend void no-op模拟、对象内存不足：事务不能提交或销毁旧对象。
6. 记录同流事件序列及真实nativebuffer标识，验证没有ObtainBufferForImage二次取CPUstaging、没有Finish/CPU raw回读、不扩大pool。

最近的可安全优化是限制转换只发生在**真实布局变化**，合法view不拆，绑定准备memo仍保留；避免整CPU回读；仅使用现有raw和有界暂存。当前并没有证据允许把全部布局错选自动转换为这一流程，也不应宣称已修白模或提高帧数。
