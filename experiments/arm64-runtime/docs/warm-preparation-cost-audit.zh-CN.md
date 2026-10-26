# 暖启动本机 Metal 库与 PSO 准备成本

2026-10-26。核对当前 `tools/` 与 `vendor/metal_api/` 源码，以及既有十四库默认启动的冷/暖证据；没有启动游戏、GPU 或新的长测。除下述获授权的 worker 等待修复外，其余只作建议。源码身份与去内容结果见 `evidence/warm-preparation-cost-audit-20261026/summary.json`。

**主要重复在游戏 PSO 准备控制器，不在十四类平铺库本身。** 稳定暖启动已经全部 strict 命中，但仍对 424 个 recipe 各启动一次原生 worker；同一次 final-exec 内还存在多轮本机 builder、工具链与组件身份检查。

## 已有测量能说明什么

下表来自既有冻结的十四库包，非本次当前源码重新计时，也不是旧新 A/B：

| 项目 | 冷输出目录 | 首次暖启动 | 稳定暖启动 |
|---|---:|---:|---:|
| 十四库与八固定 PSO 准备 | 8.856 秒 | 0.281 秒 | 0.178 秒 |
| 游戏 PSO 准备 | 349.756 秒 | 169.491 秒 | 90.483 秒 |
| 完成 recipe | 404 | 424 | 424 |
| 实际 worker 子进程结果数 | 1212 | 848 | 424 |
| 记录源码 recipe | 404 | 0 | 0 |
| builder 时间总计 | 128.069 秒 | 16.728 秒 | 14.767 秒 |
| 准备时间扣除 builder 后的剩余 | 221.687 秒 | 152.763 秒 | 75.716 秒 |

稳定暖轮的 424 项全部首遍 strict 命中、每项普通 compile_attempts=0、`warm_fast_path=true`；它只跳过第二轮构建/准备，并没有跳过这 424 次子进程验证。75.716 秒包含启动、DSO 加载、Metal 设备/缓存初始化、输入冻结、校验、监督与日志，**尚不能分配成各自准确耗时**。

该轮每个 worker 结束后，`verify_loaded_gpu()` 重新读取散列同一个 18,745,312 字节 DSO；424 次合计 **7,948,012,288 逻辑字节**。这不是 7.95 GB 实际 SSD 读取量，也不是已测出的纯散列 CPU 时间：系统文件缓存可命中。进程内存峰值、场景 FPS 也不能由这些计数推导。

## 当前默认源码调用链

`launcher_local_aot._prepared()` 在启动前依次执行：

1. actual GPU + game/patch/settings/profile/options 的 cohort 身份计算。
2. 第一次 `local_prepare()`。
3. `launcher_fixed_helpers.prepare()`：原生探测十四库与八类固定 PSO；完整暖探测直接结束，冷路径才增量记录、build、prepare。
4. `launcher_game_pso.prepare()`：核验组件与选中的 GPU、读取原 SFO、重新生成当前 cache-local candidates，再经 queue 和 native worker 验证。
5. game_report 有 preparation 时，再一次 `local_prepare()` 取得最终 exec 身份。

在“请求目录已存在、工具链可用、游戏 PSO 全暖”的当前分支中，外层开始、游戏 queue 内 initial、外层结束会各调用一次 builder，合计三次。这是源码分支推导，不是三段新的分别实测。当前 `PreparationSession` 已能在**一次** `prepare_game_psos.prepare()` 内缓存经原全量验证且环境不变的结果，不能说项目完全没有复用；但这个 session 没有跨外层 initial/fixed/game/final 共享。稳定暖证据中的内部 session 只有一次 full_build，外层另外两次不计入其 14.767 秒。

## 可消除的重复与优先级

**已修复：去掉每个游戏 PSO worker 的固定 100 ms 睡眠。** 原 `prepare_game_psos.execute()` 在 poll 仍运行后无条件 `sleep(.1)`，即使子进程马上退出也要等下一次检查。现在原监督与 finally 清理移到 `wait_owned_worker()`，只把等待换成 `proc.wait(timeout=.1)`，与既有 `FixedRunner` 一致；保留相同全局期限、check 调用、日志上限、TERM → 等三秒 → KILL、异常传播。Python 超时 wait 仍有内部调度成本，不宣称完全零延迟，也不把理论每项最多接近 100 ms 的固定等待当作实测总收益。

六项真实自造子进程控制通过，用时约 0.43 秒：已退出/退出码、短退出、较慢退出的周期检查、取消、原全局期限超时、日志上限。所有子进程已回收，无游戏或 Metal；没有跑 424 项长对照，所以**本修复收益尚未做完整启动实测**。`launcher_fixed_helpers` 也有同类 sleep，但稳定暖只一个探测进程、已有 0.178 秒，本轮没有顺手改它。

其余建议暂未修改：

- **一次启动共享现有 PreparationSession。** 保留首个完整 source/receipt/library 验证，后续相同 cohort/build/root 使用当前 session 的 stat、工具选择、SDK/OS 和环境复核。record、新请求、构建产物变化、保护中止立即失效；只在本次启动有效，不把上次进程的“完成”布尔值当授权。可先消掉 outer 初/末两次完整扫库，避免增加常驻对象。
- **一次启动复用组件准入。** fixed 与 game 分别 `game_pso_component.load()`、`resolve_gpu_identity()`，会重复 DSO 散列、otool/lipo/codesign、12 份 layout 源身份和 title/SFO 检查；available_toolchain 也被多层再次调用。可共享已经验证的只读身份对象，并在使用边界重验路径、inode、ctime/mtime/大小/链接数及实际选择器，变化即回完整验证。不能永久缓存一个路径字符串。
- **有界多 recipe worker。** 稳定暖仍启动 424 个进程，每次初始化 Device/PsoCache、加载 DSO、Local library/function 和最终 PSO。未来可用固定数量的串行、有界 batch 共用一次进程准入与 Metal 设备，每个 recipe 仍走原严格 native descriptor/archive 路径，及时释放临时对象；不得让完整目录的库/PSO 在内存无限累积。只是 page_size=16 当前不等于一次 worker 处理16项，当前 execute 仍逐项 Popen。
- **只读扫描合并。** `game_pso_candidates.cache_records()` 先列目录并 lstat；`inventory()` 再遍历、读并散列所有文件；`reference()` 随后又按 profile/key/stage 重新打开核验，复用 stage 的多个 key 还会重复读/解码。可沿同一 owned read-set、有限 page 或磁盘 ledger 合并部分扫描；不得为省 IO 把所有 shader/MSL 留在 Python 内存，也不能放松读取前后身份检查。这里尚无独立耗时，优先级低于已证实的逐进程成本。
- **混合暖启动少量新增时的二遍验证。** 既有首次暖轮 424 项中首遍 404 strict hit，最终仍跑了第二遍424项。将来可保留已经取得的真实 native 结果并以完整未变环境/该项 artifact 身份证明复用；不能只凭 queue.ready_hint 跳过。新增库、flags 或原归档发生变化时必须重新验证，不能把此优化当成不检查的增量模式。

## 不能为提速移除的工作

以下严格检查仍必要，优化应共享已验证输入或减少次数，不删除合同：

| 边界 | 必须保留 |
|---|---|
| 目录与文件 | 实际 GPU root、cohort、artifact_cache_dir 一致；canonical、owner、regular/no-symlink、链接数、边界大小及读取前后身份。不能再把测试产物目录当运行时目录 |
| 选中代码 | 实际 worker PID 的单一 GPU dyld 路径；选中 DSO 的内容身份、签名、架构、load edge，与 component/layout 对应。7.95 GB 逻辑重散列可研究一次启动的有界复用，PID/实际路径验证不可取消 |
| 本机库 | source、compile options、recipe、cohort、toolchain/build_id、receipt/library bytes 摘要；实际 newLibraryWithData 和 typed entry 成功。缺库、缺 main0、坏摘要仍逐项回原 Source |
| 工具环境 | 当前 metal/metallib/xcrun 选择与二进制身份、版本、OS、SDK；同一次准备中改变必须使复用失效 |
| 最终 PSO | 完整 native descriptor、附件/布局/特化、实际严格归档结果与 function 来源；program_hash、旧 ready hint、单独14库“完成”均不能替代 |
| 进程与内存 | 锁屏、严重压力、交换增长、全局时限、取消、日志上限和所有自有子进程清理；准备完成不应留下后台 Metal 对象 |

十四库已是默认准备路径，不能因稳定暖耗时较小就删除或默认关闭；它的目的是减少游戏中现场建库尖峰。八类固定 PSO、十四份完整库和完整游戏 PSO 目录仍分别报告，不能混称一项完成。

本轮只落了等待修复；跨 helper/session、批处理、扫描合并均留给源码目录收口后的最小分步实现与一次统一验证，没有推进长期 FEX/TSO 工作。
