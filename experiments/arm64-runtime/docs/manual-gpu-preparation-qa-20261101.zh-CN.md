# 手动 QA：缓存命名空间接线错误

用户要求接入最新 GPU 提前准备并亲自体验。已从实际验证采用的 `69cb146e` 包更新隔离 GUI、Metal 和准备工具，保留独立 FEX/bbcpu，后续 `b150e080` 没有运行时代码变化。新 QA 后缀为 `1793531250044454000`，旧 QA 保留，正式应用/存档/设置/原有缓存和 Release 未改。

## 原因与实际结果

私有打包脚本复制了正常来源包测试的整个 GPU base，其实际产物在 `base/arm64-native-v1`。正常来源包会选择该子目录，隔离 GUI QA 则直接使用前端 `BB_GPU_USER_DIR`。本次 Swift 实际 receipt 指向 base，因此最新代码没有读到复制的已有产物。这是 QA 复制接线错误，不能当成静态输入无法覆盖或必须削弱目录校验的证据。

用户进入场景后日志：

- 整批准备在 `receipt-file` 未命中；资源视图 70.39 秒、静态目录 244.27 秒、game-preparation 316.28 秒。源/快照摘要各读取约31.4 GB，不是正常暖启动测量。
- `game_pso=empty`，只建22个通用库。本机库 hit=0、源码事件至少364、静态派生采用0；至少35条慢编译，最长约62 ms。源码事件不等于驱动纯编译次数。
- `ArtifactState` 顺序为 Unavailable/Hit/Built/Deferred/Failed/Critical；`state=3` 是 Deferred，不是命中。
- FEX 准备本次 compiler=0、scanner=0、export=1，4.48秒；内嵌 producer 的历史 compiled 计数不能当成本次重新编译。

本轮不计为提前准备生效或探索/性能/内存验收。已向用户说明，并请求正常退出 QA；退出前不改当前设置、缓存或进程。

## 修正和验收边界

保留本轮新增及全部原有缓存，从经过采用验证的正确 namespace 根复制为独立 QA 缓存根，不能再多嵌套一层。只改本次隔离 QA 的选择和来源记录，不覆盖原缓存、不伪造 ready 凭据、不放宽摘要/完整特化准入。

启动前核对实际前端选择根、Local 库/静态记录/完整输入位置及 GPU 编译身份；启动后确认原准备报告与真实 Local/静态派生/完整 PSO 命中。worker 准备和游戏运行采用分别报告，未触发的目标不记为命中。物理输入开放，无录屏或纹理抓取。

Shared 证据：`evidence/manual-static-prewarm-1793531250044454000`，以及本 QA data 的 `out/last-run.log`。当前为原因确认，等待退出后修正，尚未完成修正验收。
