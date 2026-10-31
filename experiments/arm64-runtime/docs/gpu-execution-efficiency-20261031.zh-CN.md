# GPU 执行效率审查：同一物理通道的屏障覆盖

本轮保留 CPU 后端和既有 Metal 优化，只实现一个有完整局部顺序依据的候选。尚未替换正式应用、运行游戏或取得新 GPU/FPS/内存收益，不能将以下控制测试当作整场景验收。

主 agent 随后进行的真实 GPU 定向测试被既有 full-stage barrier 的设备约束中止，见文末新增核查；0088 继续保持“控制通过、真实 GPU/整场景待验收”，不能据此宣称完整 GPU 测试通过。

## 已确认的问题

`Scheduler::ReleaseHeldPass()` 在等待已有 replay、处理 hoisted compute 和原有 fence 后，用 `impl->render` 创建实际 render encoder；同一个 encoder 依次传给 `commands.Replay()` 和 `commands.ContinueDirect()`。二者之间只有屏障计数采集，没有结束 encoder、执行新的 GPU 任务或更换附件。

原 `ContinueDirect()` 调用 `BeginDirect()`，使 `barrier_since_draw` 无条件变回 false。若 Replay 最后已经执行全范围屏障，且之后尚无 draw，那么续接后的第一个相同屏障会被再次提交。这里丢失的是去冗余证明，不是需要恢复的绘制或资源写入。

覆盖仅限于已有固定命令：`Buffers|Textures`、`afterStages=Vertex|Fragment`、`beforeStages=Vertex|Fragment`。它是所有这些资源/阶段的全局屏障；是否换了纹理名称或 buffer 绑定不作为依赖证明。绑定/标签本身不执行 shader，真实 draw 则会清除覆盖。

## 实际修改

- `mtl_render_list.mm` 记录最后真正执行 Replay 的非持有 encoder 指针；只有后续 `ContinueDirect()` 指向同一非空 encoder，才继承末尾的屏障覆盖。
- 新的 recording 命令、TakeRecording 转移、End、BeginRecording 和 BeginDirect 都清除 encoder 证明。没有新 Replay 的空列表不能借用先前记录。
- 所有原 draw 家族（包括零计数、indexed、indirect、patch，以及小参数更新使用的真实 DrawPrimitives）仍清除覆盖。较窄的 VertexBarrier 不纳入新删除规则。
- 原 fence、HasBarrier、附件 Load/Store、颜色清除、内容代次、速度图、遮挡和两级页表均未修改。replay 段计数已由 scheduler 采集，因此 direct 段仍重新计数，避免重复累计。
- 不增加 GPU 对象、资源副本或缓存。只增加一个非持有指针（64 位主机为 8 字节字段，结构填充取决于 ABI），不会延长 encoder 生命周期。

默认启用条件沿用已有 `BB_MTL_COALESCE_BARRIERS=1`，无需新增前端开关；关闭该既有选项仍提交全部屏障。

## 定向验证

沿用真实 RenderList 和原 fake Objective-C encoder，新增 `--controls-only`，在创建 Metal device 之前退出。它不创建窗口、GPU device、管线或游戏，不与主 agent 的 GPU 采样竞争。

```sh
python3 experiments/arm64-runtime/tests/run_render_barrier_continuation.py \
  --renderer-build /absolute/path/to/configured/renderer
```

当前实测输出：`/private/tmp/blood-echoes-render-continuation-2rxijaq0/result.json`。测试入口只复用所选 renderer 的实际编译和 File API 链接参数，不隐式选择另一份库或重建 renderer。

已通过：过滤开/关、直接/记录/移动列表、同 encoder 跨模式、不同资源与不同绑定阶段的命令保留、所有五种 draw 在 Replay 前后打断覆盖、新 encoder、同指针新 pass、未经过 Replay、小参数 vertex 写入、转移后不得借用旧 replay。改变既有覆盖恢复语句、重新制造原来的 history-reset 行为后，正控按预期失败 4 个断言，其它边界不被误判为收益。

新 0088 patch 可应用到该提交前的两份原文件，得到的字节与 canonical vendored 实现/测试一致。源闭包清单的共同更新由主 agent 统一执行。

这些结果只证明命令过滤与边界，不证明实际像素、GPU 时间或游戏收益。后续由主 agent 集中测匹配场景，诊断轮单独记录真实 requested/dropped 屏障、附件和通道；关闭诊断后记录 GPU 时间、帧时尖峰及峰值/持续内存。

## 其余检查的边界

1. 附件通常仍为 Store，且未清除时为 Load。已有 clear folding 已在队列完整排空、后继完整覆盖得到证明的窄条件下省去往返；不能再凭相邻 pass 或同名 texture 推断死写。没有找到额外完整后继访问/别名证明，本轮不再删 Store/Load 或合并其它 pass。
2. `Scheduler::MemoryBarrier()` 的接口不携带完整的资源/阶段依赖，且存在 hoisted producer、fence 环和 render-tail。已有 coalescing 只删除无 intervening draw 的同范围屏障；本轮不将它扩成任意 compute/render 等待删除。0085 保留的 hoisted producer 顺序不得回退。
3. 片元参数特化、两级页表和现有代码优化已存在。未取得热点 shader 上重复采样的完整纹理视图、采样器、LOD/导数、偏移及读取可变性等价证明；不改采样或寻址结果，也不因文本相似删除加载。
4. `mtl_gpu_profile.h` 的 `CpuEncode()` 是 CPU 编码时间；command buffer 的 `GPUEndTime-GPUStartTime` 及 stage counters 才是相应 GPU 测量。调用减少或 CPU 提交缩短均不能直接当作 GPU/FPS 收益。

## 新发现：既有 full-stage 屏障不符合本机设备能力

主 agent 的实际日志（2026-10-31 14:57:49）：

`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/metal-combined-directed-20261031-02/mtl-render-list-test.log`

在 `MTL_DEBUG_LAYER=1` 与原 capability environment 下，两组 fake-encoder 控制均通过。之后真实 API 报错：`afterStages (0x3) can not contain MTLRenderStageTile or MTLRenderStageFragment on this device`，进程 abort；后面的 GPU case 没有通过。

### 精确调用路径和基线归属

- 当前 `vendor/tests/test_mtl_render_list.mm:340` 在两组 fake controls 后先调用 `TestBarrierAtomicOrdering(f,false)`，再测试 vertex 版本。该函数 `:272` 的 mode=0 使用 BeginDirect；`:277`、draw=0 的第一个 `l.MemoryBarrier()` 是该路径遇到的第一个真实 render memory-barrier API。
- 此时尚未调用 DrawPrimitives，也没有调用 Replay 或 ContinueDirect。原过滤器保留每个物理通道的第一个屏障；0088 不改变这条路径。
- 当前 `mtl_render_list.mm:336–339` 的 `CmdBarrier` 原样调用 Buffers|Textures、afterStages=Vertex|Fragment(0x3)、beforeStages=Vertex|Fragment。`Scheduler::MemoryBarrier()` 的 render direct fallback `mtl_scheduler.mm:982–984` 也已有相同参数；held/commands-active 路径经 RenderList 走同一个 API。
- 在 0088 之前的提交 `6a4748f1`，`mtl_render_list.mm:336–339` 已有完全相同的 API 参数；旧测试 `:266` 先调用 fragment atomic case，`:209` 已包含相同的首个 barrier。该 mode=0 case 不进入 0088 的新续接路径。

因此，这次实测失败归属于既有 API/设备兼容缺口，而不是 0088 引入新的阶段或范围。**“相同设备和 validation 条件下，旧版本同一调用也会被拒绝”目前是源码与设备约束推断，尚未另跑旧版本 GPU 实测。** 子 agent 只读核查，没有启动新 GPU 探针或游戏。

### 官方依据与最窄合法策略

Apple 当前功能表将 M4 列为 Apple9；表的注释 3 明确说明 Apple3–Apple11 不支持在该 render memory-barrier API 的 after 参数中包含 fragment/tile，也不支持 scope 中的 renderTargets。这与本机报错一致。[Metal Feature Set Tables，2026-10-23，表及注释3](https://developer.apple.com/metal/Metal-Feature-Set-Tables.pdf)

Apple 对 Metal 3 的说明要求，片元阶段完成后的资源依赖改用 pass 之间的 fence；不能靠同一 render pass 的 after-fragment memory barrier 表达。[Go bindless with Metal 3，Synchronization](https://developer.apple.com/videos/play/wwdc2022/10101/)

最窄处理边界是先按真实 producer/consumer 分开：已证明只由 vertex 写入的既有 VertexBarrier 可保留合法的 vertex→vertex/fragment 形式；真正需要 fragment 完成的依赖，在受限设备上必须经受支持的通道边界和 fence 实现。不能统一删去 Fragment 位：本 atomic case 正是 fragment 写入后由后续 fragment 读取，单改成 vertex-after 并不提供所需顺序。也不能关掉 validation 并把“不再 abort”当成修复。

若后续实现该兼容性处理，必须由有 pass descriptor、重绑定和 fence 所有权的 scheduler 执行；不能在只收到一个 encoder 的 `Apply(CmdBarrier)` 内偷偷换通道。至少须证明：前通道所有附件正确保存，续接不重复 Clear 而按 Load 恢复；MSAA/深度/模板、visibility query 和已录制命令顺序不变；pipeline/bindings 在新 encoder 完整恢复；fence 覆盖原片元写及同队列提交顺序；hoisted producer/render-tail 和未知间接别名仍被覆盖。GPU 反例需包含 fragment buffer 写读、vertex 写读、纹理/附件别名、recorded/moved/direct 和两种设备能力路径，并在 validation 开启时检查像素与状态。

本轮不修改全局屏障 API、不删除阶段或操作、不扩展实现范围；以上是精确的兼容性记录和后续必须满足的证明边界。0088 的控制结果保留，GPU/FPS/持续内存及整场景验收仍未完成。
