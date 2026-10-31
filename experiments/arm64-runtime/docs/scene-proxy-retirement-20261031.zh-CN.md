# 已销毁源图的 Metal 场景副本回收（0089）

本轮只检查现有 GPU/兼容层内存，不改 CPU 后端，不动正式应用、存档、设置或已有缓存。没有游戏长测或场景内存收益结论。

## 已确认的保留缺口

`SceneTargetsCore::Get` 为实际 `image_uid + mip` 建立独立低分辨率纹理与视图。此前，条目只有在分辨率真正变化的 `SetSize` 或对象销毁时才释放；`ResolveAll` 遇到 `lookup(id, uid) == nullptr` 只是跳过。原 `TextureCache::FreeImage → UnregisterImage → DeleteImage` 已正常延迟删除源 Image，但场景副本的 `entries` 仍持有旧 UID 的纹理，固定分辨率下没有相应清理路径。

这证明原图失效后仍有 CPU 所有权，不证明先前某次游戏的内存增长全部来自这里，也不把仍有合法源图的缓存称为泄漏。

## 窄范围修改与线程/寿命证明

- `SceneTargetsCore::CollectUnavailable` 使用原 lookup，另核对返回 Image 的真实 UID，只移除确实失效或槽位已复用的旧 UID。所有 live、dirty、被采样或待绘制源图都保留；没有按年龄、大小、预算淘汰。
- 删除前清除 `recent` 原始指针；删除对应 `tracked` UID；仅实际删除时推进 `generation`。不向已失效原图写回，不调用 `Finish`，不申请新纹理或改变任何缓存容量。
- 接在 Metal `Upscaler::OnFrameStart`。`RasterizerCore::DrawRecord` 在此次 `BindResources` 之前调用它；上次 `BindResources` 中的 `bind_helper.Fork` 已经由原有 `bind_helper.Join` 收尾，下一次 Fork 尚未发生。没有新加线程或 Join 等待；Vulkan 调用路径不变。
- **lookup 失效本身不是 GPU 完成证明。** `SceneTargets::Proxy::~Proxy` 将 native view 强引用交给原 `Scheduler::DeferOperation`；`UniqueImage` 析构经 `Runtime::DestroyImage` 将 native texture 保留引用和 residency 删除操作交给同一队列。
- 该队列绑定当时 `CurrentTick`。`Scheduler::PopPendingOperations` 只处理 `gpu_tick <= retired_tick`；后者由 Metal command-buffer completed handler 推进，而非仅由 shared-event signal 推进。因此旧命令仍使用的 unretained native 对象继续存活，直到原调度器确认 command buffer 退休。CPU 条目移除与实际 Metal 对象销毁是两个时点。

既有首次 CPU 上传摘要、CPU/GPU 内容归属、速度图、遮挡、两级页表、汉化和联网路径未改动。

## 内存分类边界

| 当前实现 | 是否新增一份独立 payload | 已有依据/统计 |
|---|---|---|
| `MemoryMirror` 的 `PROT_NONE` 地址预留 | 否 | 无 backing 的虚拟地址空间，不是 RAM 容量 |
| CPU/GPU 两进程 `MAP_SHARED` 同一 backing fd | 否 | 两种映射；footprint 可能重复覆盖共享页 |
| `GuestMemory::AllocChunk` 的 `newBufferWithBytesNoCopy` | 否 | 直接包装已有 backing；`guest-alias-capacity` 不是新增显存 |
| `Runtime::ImportBuffer` 的 native retain | 否 | 引用同一 chunk MTLBuffer，不再次分配 chunk payload |
| MTL texture view /格式视图 | 通常否 | `parentTexture` 引用，未按独立纹理计入 `MemoryStats::Track` |
| `CreateBuffer(newBufferWithLength)` | 是 | 即便为 Shared，也分配新的 buffer；Kind Buffer/Staging |
| `CreateImage(newTextureWithDescriptor)`、场景副本 | 是 | 唯一原生分配 Kind Texture/Target/SceneProxy |
| usage promotion /格式转换目标 | 是 | 拷贝期间可同时存在；旧 backing、视图按 tick 退休 |
| 跨块 gather | 是，但沿用已有 stream ring | `read_snapshots` 只记录可复用区间，不保留另一份完整资源缓存 |
| staging | 是 | Metal 当前拥有自己的 `StagingPool`；环与 dedicated span 受现有 fence/held 生命周期管理 |

已有 `MemoryStats` 的 allocated/retired/released/peak 是原生对象分配容量；discarded-capacity 不是驻留量。`ImportedBytes` 和 imported-free-retained 也是容量。`currentAllocatedSize`、graphics ledger、系统 VM、进程 footprint 都各有口径，不能直接相加为物理净占用。尤其不能把两个进程 footprint 之和作为本补丁节省量。

## 定向控制与实际 native 释放

CPU 控制使用完整生产头文件和真实 `SceneTargetsCore`，复用既有 undefined-format 夹具，不发出 GPU 命令。正例覆盖 live/dirty 保留、失效多 mip、固定分辨率、UID 槽位复用、recent 清理、稳定 generation 及复制期间不重入回收。恢复旧“不回收”、删除 live、不核对 UID、漏删 tracked、无条件推进 generation 五个变异，均被同组断言以退出码 7 拒绝；不用系统崩溃作为反例结果。

```sh
python3 experiments/arm64-runtime/tests/test_scene_proxy_retirement.py \
  --compile-commands /path/to/renderer/compile_commands.json \
  --library /path/to/verified/libbbgpu.dylib
```

实际 Metal 单项复用现有 `test_mtl_scene_targets.mm`，入口 `BB_TEST_RETIRED_PROXY_ONLY=1`。使用自制源图和 sRGB view，在 command buffer 尚未提交时编码红色 clear 与一像素读回，然后销毁源图并清理旧 UID。结果：

1. 清理没有提交命令或等待；GPU 未退休时 backing/view 的弱引用仍有效，已计入的唯一分配容量保留。
2. 完成并通过原退休队列释放后，两个弱引用均变 nil，读回 RGBA 为 `(255,0,0,255)`；Metal 验证层开启，没有验证错误。
3. SceneProxy 唯一原生分配容量实际释放 **2,244,608 bytes**，对象数和容量回到用例基线。这是自制用例的 native deallocation 证据，**不是物理 RAM 净节省，也不是游戏场景收益**。

本次探针将新 `CollectUnavailable` 核心对象与已冻结的真实 Metal 库链接，验证原有 deferred 释放机制；加载库摘要为 `eff61895e8d4f9f7070891ea4e99df577dd7eec7972c132d96ebcd2fce93cca1`。原始证据只存本机 `/private/tmp/blood-echoes-proxy-0089-sva25cvj` 和 `/private/tmp/blood-echoes-proxy-0089-gpu-9enc4586`，汇总见 `evidence/scene-proxy-retirement-20261031/summary.json`。

**尚未验收：** 完整新 renderer 的正常构建、真实帧首接线、和其他候选合并后的画面/功能回归、匹配场景峰值与持续内存、GPU 时间及帧时间尖峰。由主代理集中执行；不能用清理条目计数或本定向测试冒充上述验收。

## 本轮不改的范围

没有实际证据说明普通 sampled-texture 缓存、held staging、共享 guest chunk 或 GPU-only 内容存在新的可回收缺口，均保持现有所有权/fence/代次逻辑。Imported wrapper 的集合是否会遇到同一 native 对象多重 wrapper，只找到 API 层理论风险；当前 `chunk_buffers` 为每 chunk 单槽复用，未找到游戏中的重复 wrapper 路径，不据此改实现。

### 0090 合并后的生命周期边界

最终只读审阅确认0090仅同command buffer/tick拆encoder，提交前仍排空held/replay/render-tail；状态快照不retain GPU payload，原view/backing/sampler继续当前tick的deferred retirement。另有既有安全空tick分支：Submit已排空工作、没有command buffer且前序全部退休时，可直接退休空tick；0090拆段已有command buffer，不经过这个分支。不能将“通常由completed handler退休”误写成唯一分支。
