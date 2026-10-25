# 补充：在 raw 写入之前汇合 image 权威数据

这比事后 layout 转换再合并更晚 raw 写更容易给出顺序保证：`旧image最新逻辑数据 → full raw基线（padding保留） → partial GPU raw写 → 标记所有重叠image需从raw刷新`。后一旦成立，GpuDirty的整mip刷新从raw读取的是已经合并后的正确内容，而非“CPU旧基线+少量GPU新字节”。本设计只读，尚无实现/运行证明。

## 精确接入位置

| 生产路径 | 当前顺序 | 受限插入点 |
|---|---|---|
| 普通shader writable buffer | rasterizer_core.cpp:2343 ObtainBuffer → :2362 InvalidateMemoryFromGPU → 后续draw/dispatch | 在Obtain之后、Invalidate之前，传明确raw binding与GPU image ID；这时部分写还未录入 |
| DMA CopyBuffer | rasterizer_core_memory.cpp:219 Invalidate → :225 Obtain源(read,texel) → :231 Obtain目的(write,texel) → :238 backend.CopyBuffer | 必须先捕获/准备有效旧image，完成全图raw准备/导出后再失效；现顺序早置GpuDirty导致SafeToDownload不再允许导出，不能原样挂接 |
| DMA FillBuffer | :179 Obtain(write) → :181 FillBuffer；:157仅ClearMeta | 新预写/失效流程需覆盖此处；只修Copy会漏Fill |
| paged writable shader | :2327–2334 LayerPagedRecord后invalidate，绑定记录没有单一raw Buffer | 首版拒绝自动导出；不能把record buffer当image数据目标 |
| CPU快速Copy/Fill | :202–216/:162–172可直接写CPU backing | 不是GPUraw写闭环；若目的地有GPU-only image，仍有CPU部分写与GPU逻辑混合问题。不得声称新GPU钩子覆盖这些分支；后续须显式转GPU路径或保留已定义的CPU同步 |
| command writes/时间戳等 | UpdateBuffer/timestamps/occlusion各有自己的记录路径 | 不能只改ObtainBuffer声称全覆盖；先枚举已知可命中image的生产写命令并分开准入 |

## 完整图空间，而非部分binding空间

例如写`base+4096,64`，获取的mirror可能虽有整张图，但不代表整图已经同步；Obtain只对查询范围SynchronizeMemory。必须检查：

- raw绑定guestAddress与其nativeoffset的映射可逆；`imageOffset = bindingOffset - (writeAddress-imageBase)`需要先检查顺序及下溢，不能拿partial offset直接当imageBase。
- imageOffset+guest_size在同一个真实buffer容量内，且整个guest image当前解析到**同一代的这个mirror/chunk**，不是“容量够大但实际另一半在其他mirror”。
- CPU dirty全图raw基线已准备，padding不能来自未初始化或旧staging；image的CpuDirty/MaybeCpuDirty意味着不具备直接导出资格，不能用全图CPU上传后覆盖image作为混合修复。
- LayerMode部分Mirror/Mixed或跨物理chunk首版拒绝。raw可写目的不允许使用只读gather stream，也不允许GetArena大稀疏分配兜底。
- 请求全图会改变residency，不能在已有bound_buffers/native descriptor录入后再换buffer而不重建绑定。可行受限版本是在真正BindBufferSet前准备，若mirror generation发生变化则重新取得并校验binding；或直接只允许稳定已经完整覆盖的mirror/chunk。

## 私有helper形状（设计）

建议明确 `PrepareImageAliasesForRawWrite(guestWriteRange, RawBinding)`，返回 Prepared/Unsupported/Failed，不放入普通texel只读回调；避免在 `ObtainBuffer` 的每个早返回分支偷偷递归调用同一个Obtain(fullrange)。若用内部整图raw准备函数，应显式suppress-image-sync并保持RMW语义，而非用is_texel_buffer=true重新按地址选image。

1. 在准备线程完成后按已知锁顺序扫描writeRange的**全部overlap**，收集明确ImageID+UID。不是FindImageFromRange，因为后者要求等基址，且可忽略size。
2. 只准入一个可证明raw权威、单sample exact color、全mip canonical、无Dirty、非proxy/target/VO/depth/当前storage别名等初始子集；相同raw多张GPU-authoritative image先拒绝，不能由扫描顺序决定最后写者。
3. 旧image仍保持GpuModified且clean时完成bounded TileImage到**full-image raw**。raw此前只是padding/未image写区域基线；导出不是在partialWrite范围内裁剪，而是把旧image逻辑内容汇入完整raw。
4. 使用backend正常Transit/IsBufferAccessed/AccessBuffer、EndRendering和FlushBarriers在GPU流中建立image→raw写后再到partial raw读写的依赖；不Finish、不CPU回读。
5. 然后对**所有重叠image**设置GpuDirty（可先设以阻止重复导出，但须确保导出命令已成功录入），继续原partial Copy/Fill/draw写。后续RefreshImage不能再从CPUstaging错误重取，应由BufferCache正确GPUdirty raw来源取得完整合并图。
6. image/raw/backing/stream寿命都保持到当前tick结束。预写导出不释放image，无需layout转换时制造新的临时旧/新对象；可直接避免“更晚raw局部写被旧image全幅retile覆盖”。

## 原有同步的不足与可重用处

SynchronizeMemory(arena,addr,size,is_written,is_texel)只在 `is_texel && !is_written` 时导出image；**写绑定当前不会预先汇合**。因此把invalidate改成全部overlap仍不够：那只会触发整image从“未合并raw”上传，丢失未被partialwrite覆盖的GPU-only texel。

普通arena/Mirror写绑定同步CPUdirty页并记录GPUmodify/WriteTick已有，可复用RMW基础；in-place只读路径有现成导出，但未证明partial writable完整图覆盖。导出后应准确记录完整raw导出范围的GPU有效状态，而不只把partialWrite范围记为GPUmodified。否则后续ObtainBufferForImage可能将整图判成CPU-only，走staging，再丢掉刚导出的GPU图。已有SynchronizeMemoryFromImage只DropShadows/条件NoteGpuWrite，不足以直接提供跨所有模式的明确full-image有效状态。

## 成功、锁与失败处理

Runtime::DownloadImage:1855缺backing直接return；:1872 CopyCompatible失败可不执行复制，仍记录buffer access；UploadImage:1837也可能跳过实际上传，却:1850无条件清Dirty。TileImage也是void。新helper必须预检native/source格式、sampleCount、copy shape和返回明确录入结果，**不能把AccessBuffer事件/void return当实际传输成功**；只有成功才允许把旧image整体交给raw权威，unsupported保持旧路径且可诊断，失败不得“标dirty后悄悄继续错误raw”。

TextureCache使用普通std::mutex，InvalidateMemoryFromGPU自己加锁；因此锁住cache再调用Invalidate会死锁。应在单个明确Locked内部函数设置flags，或释放后重验UID/generation。FindImage/UpdateImage也自己加锁，不能在locked预写helper中重入Refresh/FindImage。BeforeImageAccess只调用image_access_hook；它是准备线程汇合，不是GPU完成Fence，也不是防guestCPU写锁。不要在cache锁内等待可能需要同锁的准备线程或allocation/mapping互斥；复用已核实的DrainDrawPipe→cache lock顺序。

由于GPUraw实际写未发生，image仍clean是一次性导出的关键。若某一旧资源此前已GpuDirty，说明raw可能有更晚内容，必须先沿raw→image路径取得完整当前图或拒绝；不能为“先合并”再导出旧image。多次写绑定首次汇合后置GpuDirty，使第二次跳过旧图导出，在同一提交内也能保持顺序，无需依赖tick。但同draw中同时使用image写与raw写、多个stage可见性、render反馈等应保持首版拒绝。

## 应追加的定向控制

在根代理现有矩阵上加入：先cleanGPUimage，绑定部分raw write前预导出，后写新raw矩形，然后GpuDirty刷新整图；untouched GPUtexel、later rawtexel、padding均应精确。Mirror容量足但fullrange解析Mixed、imageBase早于rawOffset、GPUbuffer内部offset、同submit两次partialwrite、多imageoverlap、缺backing和unsupported-format全部负控。记录实际native handle/offset及命令顺序，证明没有CPU roundtrip/新大buffer/额外长wait。

这个受限预写接入口比事后用tick猜测字节最后写者更可信，但仍须完整枚举producer并补full-raw有效标记，不能只添加一个统一Invalidate就称问题修复。
