# Apple GPU 的完整片元屏障：0090

## 问题与范围

旧 `CmdBarrier` 和 Scheduler raw-render 分支以 `Buffers|Textures`、`after=Vertex|Fragment` 调用 render memory barrier。Apple GPU 不支持 after-fragment 的通道内屏障；开启 API validation 后，原有 fragment atomic case 在第一次真实调用即终止。这不是 0088 同 encoder 去重引入的参数变化。旧 baseline 的触发结论来自源码与同设备约束；没有把它另行跑过游戏或全套 GPU 回归。

一手依据：Apple [GPU feature tables](https://developer.apple.com/metal/Metal-Feature-Set-Tables.pdf) 的 render memory barrier 限制；[WWDC22 同步说明](https://developer.apple.com/videos/play/wwdc2022/10101/) 明确要求 after-fragment 依赖采用通道间 fence。修复不删除 fragment 阶段、不关闭 validation，不用相邻命令、纹理名称或缓存命中推断依赖。

## 实现

- Apple family 的 Scheduler 为现有 RenderList 安装 owner 回调。普通全阶段屏障在其**实际执行点**关闭旧物理 render encoder，复用原 fence ring：after-fragment update → 下一段 before-vertex wait。计算屏障、窄顶点更新屏障和非 Apple 设备原有合法路径不变。
- 拆段位于同一 command buffer 和 tick，不调用 EndEncoder、Flush、JoinReplay、提交或回收操作。因而可在真实 replay 线程执行；提交前原有 held/replay/render-tail 排空规则不变。
- 由原 pass descriptor 保存最终 Store／resolve、清除、深度、模板、MSAA 和其他附件属性。创建 encoder 时选择 deferred Store；中间段保存附件，续段 Load，不重做 Clear。最终段执行调用者原来的 Store／resolve。带 resolve target 的中间段必须 `StoreAndMultisampleResolve`，不能只 Store；真实 validation 反例已确认这一 API 约束。
- RenderList 保存的是**已经执行的 prefix** 的完整 setter 状态，不是 recording 末尾的过滤表。新 encoder 重绑管线、深度/模板、固定状态、viewport/scissor、两阶段 buffer/texture/sampler、SetBytes、动态 stride 和 tessellation factor。offset-only 设置先物化为完整 buffer/stride 绑定；不重放 draw、依赖或 signpost。局部 debug groups 在外层 Scheduler 组关闭前弹出，随后重建。
- 续段保持实际已声明的间接资源集合，包含 RequireGuestRender 对 bound-only pass 的升级；不把不可变的 guest/host 查询标签当作当前 residency 集合。继续使用现有 UseGuestChunks/BDA leaves 和 fence 机制，没有新增 GPU 纹理或缓冲区副本。
- 一个逻辑查询只分配一次 slot。macOS 26 的 [visibilityResultType](https://developer.apple.com/documentation/metal/mtlvisibilityresulttype) 允许 Apple7+ 在续段向同一 buffer/offset accumulate 原始样本数，原来的缩放/舍入只处理一次。不能把多个分段分别缩放后求和，也不能让新 encoder reset 掉上一段。
- 0088 的去重覆盖仍仅在同一个实际 encoder 上继承。barrier 后的真实 draw 仍清空覆盖；0090 在执行回调中更新实际 encoder 身份。0089 的 completed/retired tick 回收逻辑没有修改。

## 明确边界

项目生产 raw host pass 的已审计调用点没有在原生绑定/draw 中途请求 generic fragment barrier；主绘制使用 RenderList。未知 raw 状态无法从 Metal 反查，不能静默换 encoder 丢掉绑定。该情况保持明确诊断；只有调用者明确证明为空 pass 才能使用 `MemoryBarrier(true)`。测试里的原 raw empty-pass case 已写明此合同。没有增加 Objective-C 代理或另一套状态拦截层。

当前生产附件为可存储的普通资源。memoryless 附件不能跨段保留内容，若遇到则明确拒绝，不能以 DontCare、丢样本或降低画质蒙混通过。这里不引入 tile shader/imageblock 保存协议；现有 RenderList 没有此类命令。查询续段需要 macOS 26 / Apple7+；本次在 M4、macOS 27 的现有部署条件验证。

状态快照只保存非 retaining 的原对象指针和参数字节，生命周期仍由原 tick/deferred destruction 管理。NativeState 有 382 个 vector 槽：32 个固定状态预留、62 个两阶段 buffer 槽、256 个 texture 槽、32 个 sampler 槽。在当前 64-bit libc++ 下 vector 对象为 24 字节，连组容器约 9,192 字节元数据/已分配列表。各 buffer 槽的逻辑内容最多 Header(8)+CmdBytes(12)+4,096=4,116 字节；texture/sampler 约 24 字节/有效槽，另有 viewport/scissor/固定状态。全部填满时每列表逻辑暂存约 273 KB，主列表+4个replay槽约1.3MiB，不含已有诊断标签字符串。实际分配受 vector capacity 增长、malloc 取整影响，不能把逻辑字节数冒充物理内存上界；组深度限64，当前诊断名称来自既有路径。

Apple owner 回放和 Direct 都在首次实际执行 setter/组/绘制时懒分配 NativeState。**即使该 pass 最终没有 generic barrier，也会保存已经执行的状态**：Direct 不能预知后续屏障，Replay 后还可在同一 encoder 继续 Direct，不能凭录制中没有屏障就丢掉所需 prefix。已被原冗余过滤器丢弃的 setter 不再拷贝；SetBytes 会额外保存一次实际字节。这是本轮正确性方案的明确 CPU/暂存代价，没有用微测宣称可忽略。End 时释放这些暂存，不能据此宣称 malloc 页面必然已归还系统。整场景峰值/持续内存和 CPU 提交/额外 setter-copy 成本由主 agent 的匹配测试验证；不为省这部分而删掉必要状态。

## 构建与定向验证

canonical Scheduler 已有真实 `BbAudit::Scope` 与 `BbCpu::Pause`；0090 没有删除或伪造它们。CMake 让 bbgpu/独立 scheduler test 都引用现有真实 runtime src include，使用现有 shim 的 ARM yield 实现；该 include 不再错误依赖 shader capture 开关。测试未启用 audit 环境。

入口：

```
python3 experiments/arm64-runtime/tests/run_fragment_pass_barrier.py \
  --renderer-build <configured-renderer-build> \
  --qa-state /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state \
  --full --mutations
```

使用原 capability_environment 和目录准入，数据为 QA state 下临时子目录。不得为此放宽目录校验。需要允许 Metal device 的执行环境；沙箱中 nil device 不算 GPU 验证。

正例覆盖：direct / held / moved-list / 真 replay、完整 prefix setter 重绑、offset/stride/两阶段 SetBytes、调试组、所有 draw 控制边界；真实 fragment/vertex atomic producer-consumer 像素；1x/4x MSAA、深度/模板、untracked BDA、query accumulate、tick 不变及 deferred destruction；升级 residency；原完整 scheduler 控制。所有真实 GPU 轮次保留 MTL_DEBUG_LAYER=1，无窗口、游戏、Cua、截图或录屏。

反例：旧 0088 history reset 与禁用状态重绑由 fake encoder 控制检出；query Reset、重复 Clear 的私有编译变体必须被真实像素/计数断言检出；未知 raw 中途 barrier 必须诊断退出。变体只改临时源/对象，不覆盖 configured 对象或现有缓存。

具体最终日志路径和结果由本轮主 agent 记录。短定向通过不等于整场景验收。0090 纠正原先非法的同步表达，额外物理通道和 MSAA 中间 resolve 可能有代价；不能提前承诺提升 FPS。主 agent 统一审阅、隔离打包后，再测 GPU 时间、通道数、帧时间与唯一分配/峰值/持续内存。

### 2026-10-31 本轮最终结果

- 真实 GPU：`/private/tmp/blood-echoes-fragment-barrier-82wxaa21/result.json`，14项均符合预期：11项正常返回0（含原完整 scheduler 的 HOLD=1 前置条件）、未知 raw 状态1项预期 SIGABRT、两种私有错误变体各1项预期断言失败。所有轮次 API validation 开启。
- fake encoder：`/private/tmp/blood-echoes-render-continuation-t1kebnj0/result.json`，无 Metal device，coalescing 开/关通过；history-reset 与删除 native 状态恢复的变体均被检出。
- 全套旧 TestHeldPass/TestGpuQueue 本来要求 HOLD=1；误设 HOLD=0 产生的失败没有算成0090生产回归，也未改旧断言来放过。Direct 由独立真实GPU矩阵验证。
- 生命周期正例先确认拆段未提交/回收；Finish 的 event signal 仍可能早于 completed handler，随后使用既有有界 poll 等待 retired 回调。没有为测试改动生产 retired_tick 规则。
- 0090补丁从前置已提交源码应用后，与7个对应 canonical GPU/test 文件逐字节一致；CMake include 和Python入口为 canonical 集成配套，不伪装成上游通用补丁。
- 本轮没有启动游戏或运行场景 benchmark。所有 GPU 探针已停止，场景性能、CPU提交成本和峰值/持续内存留给主 agent 统一验收。

主agent随后完成统一整包及八轮固定场景对照，见[本轮集中验收](combined-fex-metal-validation-20261031.zh-CN.md)。上节“本轮没有启动游戏”指subagent短probe阶段，不代表主agent未测；正式应用和Release仍未更新。
