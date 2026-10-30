# 上游定向整合（2026-10-30，进行中）

本轮授权为并行移植值得采用的上游更新，先完成源码审阅和定向验证，再运行隔离游戏。bloodborne-recomp 暂不接入；不更改本机正式应用、存档、设置、缓存或 Release。当前基线为开发分支 `00385145`，本机仍为 0.2.58 / `a7087799`。既有两个 FEX/unwind 未提交改动保留，不能混入本轮提交及干净构建。

## 整合范围

| 来源 | 本轮处理 |
|---|---|
| shadPS4 [#5570](https://github.com/shadps4-emu/shadPS4/pull/5570) | 修复 LRU bool 回调提前停止；不改变缓存容量和回收预算。 |
| shadPS4 [#5577](https://github.com/shadps4-emu/shadPS4/pull/5577)，尚开放 | 对缓存写队列共享计数使用原子同步；保留原队列、IO、关闭协议及本地抓取锁。 |
| shadPS4 [#5524](https://github.com/shadps4-emu/shadPS4/pull/5524) | mip/layer 逐维包含判断，并补齐审阅发现的深度重建逐维联合 extent。 |
| FEX [#6074](https://github.com/FEX-Emu/FEX/pull/6074) | 六类本地尚缺的 guest load 副作用标记；已有两类保持。真实 FEX 归档摘要决定 CPU 缓存身份，不伪造磁盘格式版本。 |
| DiAvisoo [8e678b6f](https://github.com/DiAvisoo/bbport-mac-optimized/commit/8e678b6f64112accd5af80c64526b4a14b9c7357) | 可选 UI 补丁目的地检查仅在实际安装／恢复时要求；活动与中断恢复仍严格检查，不误阻普通 Play。 |
| Huax [77a4bf4d](https://github.com/Huax-1839/bloodborne-macos/commit/77a4bf4d6ab5d06c426014322dadd5fed724cb14) | 仅普通光栅顶点的 contraction + Position Invariant，接完整编译选项、schema3 本机库、捕获与准备身份；默认关闭，并在原前端标为实验性。游戏画面／速度图／性能尚未验收。 |

## 审阅发现与当前边界

1. 深度重建不能继续对整个 extent 使用 `std::max`：更多 mip 却更少 layer 会仍建出缺层对象。已改为逐维联合并增加生产表达式原生反例。
2. Canonical Profile 末字节扩展为有界 feature flags 后，低层 store 仍按 bool 拒绝2/3。已修正实际 codec，普通 bool 仍只接受0/1，未知 feature 位拒绝，实际 Store／typed companion 写读有正反例。
3. 旧 raw Profile 的74偏移原为 padding。已采用明确116字节的新 feature envelope，默认关闭仍使用旧96字节格式，旧88／96只读取原有命名字段。实际 WarmUp 判定前缀验证256种旧 padding 均复用且 Clear／Save 为0；截断、删前缀、错位、未知位拒绝。独立复核已关闭此问题，不能把新模式当作旧 padding 静默归零。
4. Metal 跨颜色／深度复制的多 mip、增长层数能力限制原已存在；旧合法成功域在逐维联合后仍满足原断言。本轮不扩写转换 scratch 或伪造支持。

## 不直接移植的候选

- shadPS4 #5571 的异步读回在本地会改变 staging 分配策略，且 priority CPU 写回不由 `PopPendingOperations` 排空。直接减少 `Finish` 会破坏返回前写回保证，或增加峰值与多余等待。
- Metal 上游异步池夹带 `AsyncSkippable` 的短暂跳绘制路径，不符合本轮画质要求。
- bbport 0.7 的布料需接 live dt 与混合浮点 hook；ring 去重、专用复制过滤和 Vulkan query intervals 均不是当前 Metal 生命周期的直接补丁。点光源逐面元数据核心本地已有。具体差异保留在本轮专项说明中，不整仓升级。

## 当前验证状态

缓存、纹理范围、FEX IR 与 UI 的定向控制已运行；主代理实跑可选 UI 的20项 Python测试、全部127项 Swift（跳过1、失败0）及本轮51项 Python控制通过。Metal agent 实跑 authored 离线编库、不同进程 Local 命中、暖准备零编译及1024深度等值像素控制通过；这些不代替真实游戏、速度图、帧率或持续内存验收。

源码审阅后的整合提交为 `5c0f2b72`；干净工作树完成正常 ARM64 CPU、renderer、模块／管线准备器、网络库、GUI 和同构 layout 构建。真实 FEX fixture 已链接这次新归档执行：正常读取／后续写入通过；故障用例先由 guest gate 证明已进入执行，再保护自有页，确认异常地址匹配且后续写入未发生。首次 fault 用例被映射准入提前挡住的运行不计为通过，记录已保留。

正常组包暴露的两个既有合同缺口另以 `214c843a` 修复：打包专用能力对象补真实冻结 native 身份查询，不取得游戏数据权限；Python provider 在构建与组包之间按同一实际解释器路径和摘要规范到 Framework。135项相关控制通过；父代理另复跑其中84项。新GUI准确标记214c，9470项 native 源与5c构建源逐文件一致，复用七份未变正常构建产品；原始 manifest 不改写，失败暂存包保留。隔离包为0.2.59，尚未安装或发布。

QA采样已修正冻结CSV仍被当作完成的问题：以整段场景源时钟与宿主wall时钟核对，必须观察到截止时间后的真实推进；保留中间停顿，允许有界的原日志批刷新延迟。57项相关控制通过，父代理统一执行本轮87项控制通过。它们都不代替实际游戏结果。

隔离游戏对照及 F3／死亡回归尚未完成。游戏回归须复用原 Swift 冻结启动条件和现有 QA 控制器；若脚本字节改变，只允许经过审阅的一对精确源投影，不能伪造 receipt 或放宽所有脚本校验。性能采样不使用 Cua、截图或录屏，保持静音、原画质与缓存容量，保留隔离 QA 保护。
