# 上游纹理范围修复与批量读回评估（2026-10-30）

本项只做源码集成和无游戏定向控制。没有启动游戏、GPU 设备或 Cua，没有访问正式存档、设置和缓存，没有修改本机应用。整合后的正常构建及隔离游戏验证由主任务集中执行。

## 已移植：shadPS4 #5524

来源：[PR #5524](https://github.com/shadps4-emu/shadPS4/pull/5524)，已合并；merge commit `ca36c7e31e9edb958db8d2b94a0721e58eca9daf`，PR head `e6ab8c6b407878eca1a42721c02cdefdceb7035b`。2026-10-30 重新读取实际 diff 与状态后移植。

`SubresourceExtent` 的默认比较先比较 mip 数，只有相等时才比较 array layer 数，不能回答一张图是否覆盖请求的全部子资源。例如缓存 `(8 mips, 1 layer)` 在字典序上大于请求 `(4 mips, 6 layers)`，实际却缺少五层。

与上游一致，增加 `Contains(other)`，要求 mip 数和层数均足够，并修正三个实际决策：

1. `ResolveDepthOverlap`：任一维度不足就进入原有重建路径。
2. `ResolveOverlap` 同地址、同图像类型的扩展决策：使用缓存是否覆盖请求，不再使用请求是否字典序更大。
3. `FindImage` 最终选择校验：拒绝任一维度不足的候选。

独立代码审阅还发现本地重建路径仍使用整体 `std::max(requested.resources, cached.resources)`。对于 `(8,1)` 与 `(4,6)`，它仍生成 `(8,1)`，与新准入条件矛盾。这是本地集成必须配套处理的遗漏，已将合并改为两字段分别取最大值，结果 `(8,6)` 同时包含两份输入。限定纹理链检索没有发现第二处整体 extent 的 max/min；复制中的标量 mip/layer min 是交集上限，保留。

额外只将本地详细错误日志的 `Old resources <= new` 改为 `Old resources cover new`，使用相同的包含关系，避免日志仍给出错误的字典序结论。

保留本地 `u32 levels/layers`，不照抄上游当前 `u16` 字段，不改变结构尺寸、偏移、默认值、等值或排序运算。方法为 `const noexcept`，无分配或新常驻状态，不调整缓存预算。以前错误命中不足纹理的情况现在会走已有扩展/新建流程；仍需实机记录实际分配情况，不能仅凭无新成员宣称真实内存占用已验证不变。

## 与现有路径的关系

- `Contains` 只判断 extent，不假装验证非零 base offset。现有右/左重叠路径的 `MipOf`、`SliceOf` 对地址、pitch、mip/slice 的匹配保持原样。
- `ResolveOverlap` 的 1D/2D 同布局准入、0039 的地址复用拒绝仍在扩展判断之前；0038、完整格式和类型校验不动。
- `ExpandImage` 的 CPU 权威/GPU 有效内容判断不动；Metal `CopyImage` 已将复制 mip 数限制为两图最小值，并由 `SanitizeCopyLayers` 处理层数。此次没有新增跨布局复制算法。`CopyColorAndDepth` 同 depth-kind 时走上述路径；color↔depth 时走 `CopyImageWithBuffer`，后者仍要求层数相等及只有一个共同 mip。这是已有的跨格式限制，纯 extent 控制不证明该路径可处理任意扩展，须在整合审阅/定向 GPU 验证中单独界定。
- `FindImageMemo`、首次上传摘要/白模修复、CPU dirty/GPU-written 所有权、延迟销毁、速度图、两级页表和内存回收代码均不修改。
- 没有证据把该 bug 认定为之前彩噪的根因；既有彩噪修复保留。

## 定向控制

运行：

```sh
python3 experiments/arm64-runtime/tests/test_upstream_texture_extents.py
```

结果：4 项 unittest 通过，最近一次约 3.5 秒。实际运行五个 clang 编译产物：正例包含完整正式 `types.h`，按正常构建顺序使用实际 `shim/common/logging/log.h`、正式 SlotVector/基础类型及本机 fmt 依赖，同时抽取并编译 `ResolveDepthOverlap` 中实际的 `new_info.resources` 合并赋值；以独立坐标集合核对 4,096 组有效 extent 的包含关系、最小联合矩形、合并后同时覆盖两输入及交换顺序，并执行 15 个命名边界/ABI 检查。三个方法负例直接抽取生产完整 extent 声明和正式 `common/types.h`，分别改为旧字典序、两维 OR、严格大于；第四个负例保留正确 Contains，却恢复原整体 std::max 合并表达式。四个临时产物均被同一原生测试以退出码 7 拒绝。临时产物仅在自动删除的私有临时目录中。

独立的接线检查覆盖三个生产分支和日志，没有把接线字符串检查当成原生行为验证。没有提供日志或 SlotVector 假头文件。完整 `types.h` 和依赖链已通过编译，真实 `TextureCache` 分支、GPU 像素和持续内存尚未由本测试执行，须由集中正常构建/隔离验证覆盖。

## 本轮不直接移植：shadPS4 #5571

来源：[PR #5571](https://github.com/shadps4-emu/shadPS4/pull/5571)，2026-10-30 状态仍开放，head `43e04b83c665bd67d12e7e44e22b750198200375`。上游修改是每张图异步排队，循环后统一 `scheduler.Finish()` 和 `PopPendingOperations()`。

本地共用接口经过 bbport/Metal 改造，存在三个已核实的合同差异：

1. **CPU 写回完成不是同一条队列。** 本地 `DownloadImageMemory(..., false)` 将 `Invalidate`、`TryWriteBacking`、必要的缓冲失效及 staging 释放放入 `DeferPriorityOperation`。Metal 调度器有独立 `priority_pending_ops` 和 `priority_thread`；`PopPendingOperations()` 只处理普通 `pending_ops`。统一 GPU `Finish()` 再调用普通 `PopPendingOperations()`，不能据此证明函数返回前 priority CPU 写回已完成，可能破坏原先每张同步读回在 fence 前完成的合同。
2. **会改变 staging 分配方式与峰值。** `StagingRequest(download_size, HostCached, 16, !sync)` 在异步时传 `deferred=true`。实际共用的 `StagingBufferPool::Request` 对任何 deferred 请求都进入 `RequestLarge`，即使请求很小；每份 dedicated allocation 的 `held` 标志直到 priority 回调才释放。原同步小读回使用 16 MiB ring。直接批量异步会同时保留多份 dedicated span，并进入约 3,000 帧闲置缓存生命周期，不只是把等待次数由 N 减为 1，不能宣称内存峰值不增加。
3. **in-place 路径本来不需要等待。** `GuestInPlace()` 成功且 `IsInPlace` 为真时，仅记录按 GPU 流顺序的写回就返回，不申请 staging，也不 `Finish`。全队列均命中该路径时新增统一 `Finish()` 反而强制 GPU 等待。

相关实现位置：`texture_cache.cpp::DownloadImageMemory/ProcessDownloadImages`；`renderer_vulkan/vk_staging_buffer_pool.cpp::Request/RequestLarge/FreeDeferred`（Metal 同样通过该共用 staging pool 分配）；`renderer_metal/mtl_runtime.mm` 的接口转发；`renderer_metal/mtl_scheduler.mm::Finish/DeferPriorityOperation/PopPendingOperations`。

因此本轮保持 #5571 原代码不动。将来只有真实观测到同一批次多次 fallback 读回等待时，才值得设计有界批次、按需等待并显式确认 CPU 回写完成的 Metal 实现；这不是本次 #5524 的前置条件，也不需要为了证明理论收益专门启动长测。

## bbport 0.7 只读筛查

固定来源：`deadinside28/bloodborne_pc` commit `0fc2921c45f8ff3900f6bfa43a299e9a4f532af4`。以下没有修改运行时代码，不强行并入本次小范围修复。

| 项目 | 上游位置与做法 | 本地判断 |
|---|---|---|
| >30 FPS 布料 | `gpu/shim/bbport_frame_rate.cpp::ClothStiffnessFactor/HookClothStiffness`，eboot+`0x1167a80` 函数尾调用替换，按 `live->dt * 30` 调整类型相关的 Havok stiffness；类型 5/8/10 保留原特殊语义 | 本地没有上游新增的 live-frame-rate 模块。需接真实 dt、混合整数/浮点参数及返回值的 FEX 原生 hook，不适合当作只替换常量的小修复；后续独立评估，不新增本轮接口。 |
| 点光源 shadow cube 每面清除 | `gpu/render/raster_targets.cpp::PrepareDepthTarget` 使用 view base layer，并对 `IsMetaCleared/TouchMeta` 逐 slice 处理；`gpu/textures/texture_cache.h` 保持 per-slice clear mask | 本地 `rasterizer_core.cpp` 的 depth target 和 `texture_cache.h` 已有这组核心逻辑。尚未定位出其他可单独移植的缺口，不声称已经完整验证所有 shadow cube 行为，也不重复加 clear。 |
| 遮挡 intervals | `gpu/render/vk_occlusion.cpp::Counting/NoteInterval`，仅查询对打开时累计 segment，地址形态异常时恢复旧累计模式 | 本地 Metal 使用自己的 visibility slot + GPU 有序 snapshot，没有上游 Vulkan 的 query-result CPU 读回/等待路径。因此上游节省的约 0.8ms 不是本机已存在的同一开销；如要减少无查询时 counting，需要适配现有录制线程、query 配对、分辨率缩放、保守历史和失配回退，不直接套上游类。 |
| Constant ring 去重 | `gpu/render/raster_pipe.cpp::CopyRingDeduplicated`、`vk_constant_ring.h::Reuse`，两张 8,192-entry TLS 表、128-bit 摘要、暂存字节、monotonic ring holds/retirement | 本地 Stage A `CollectRingBindings` 暂无同等跨 packet 复用；但已有 in-place 与跨块读取快照复用。新算法仍读取/哈希原字节并增加每线程表，AMD 上游单项约 0.7%，需要在统一内存上独立测量；移植必须覆盖环回与生命周期，不能只加哈希表。 |
| Copy skip same | `gpu/render/raster_dispatch.cpp`，专用 CopyFilter 线程、Stage A job 输入、目标写入历史和过滤后的 workgroup control list | 本地无相同 CopyFilter；已有部分复制/快照和通道优化，并非等价于按控制列表过滤。新实现涉及消费者忙碌反馈、source/destination 失效、两级页表与跨进程写入顺序，不能作为几行低风险跳过绘制合入。 |

本机依赖路径只读核验：`evidence/night-source-build-0cbd1368/build/CMakeCache.txt` 及 `compile_commands.json` 是现存正常构建记录，但仍引用已消失的 `/private/tmp/blood-echoes-arm64-metal-20261023/deps` 和 `/private/tmp/bbport-perf-deps`。现存 VMA prefix `evidence/normal-vma-source-20261027-01/prefix` 只有 `vk_mem_alloc.h`；`/opt/homebrew` 有 fmt/Vulkan/xxhash/miniz，未发现 SPIRV-Cross prefix。正常构建本来使用项目 `shim/common/logging/log.h` 替代 spdlog，不需要为该头恢复 spdlog。SPIRV-Cross 如需恢复，应沿用 `vendor/macos/build-deps.sh` 的固定版本 `vulkan-sdk-1.4.363.0`，不要拿其他版本的库冒充原依赖。本筛查没有重新配置或启动构建。
