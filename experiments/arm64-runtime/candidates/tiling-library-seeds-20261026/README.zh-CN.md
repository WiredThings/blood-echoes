# 十四类平铺库：已接入隔离版默认准备

0001、0002保留最初基线15bcac22的迁移补丁。当前experiments/arm64-runtime源码已经合入；不要对当前源重复应用。正式应用和Release未变。

复用原TilingShader、TextureTilingSource、CompileOptions及LocalLibrary，保留nil短路、热map和既有缓存预算。14份完整源码库与8类固定最终PSO分别就绪才算完成；它们不代表22个最终PSO或全游戏全部变体。schema2能力、packet及consumer一起升级，旧能力1和usage64明确不可用。

230GPU单元冻结构建、配对30CPU+2AvPlayer+GPU窗口main、真实Local14/14与8/8 strict均通过。9进程Local像素/guard控制通过；56项缺库、缺入口、坏摘要和损坏内容反例逐类通过，没有放宽目录/身份检查。控制器另补了晚请求的有界增量建库，不会因8PSO先完成就误报14库完成。

同包冷、暖、稳定暖均进入相同QA场景并运行90秒。稳定暖424/424首遍strict，源库记录0、warm_fast_path=true，约141秒进入场景；最高压力2，场景footprint合计中位数约5.80GiB，swap减少24MiB。不是匹配旧新性能/内存收益，也不是完整游戏画面或长期生命周期验收。

范围、失败保留、详细冷暖内存与限制见[默认准备文档](../../docs/tiling-default-startup.zh-CN.md)和VALIDATION.json。最新本机包为artifacts/arm64-qa-20261026-fourteen-libraries，依赖现有Shared开发树；不分发用户本机编译结果。其它原目标继续保留。

CPU合同控制（在experiments/arm64-runtime运行）：

```sh
python3 candidates/tiling-library-seeds-20261026/test_library_contract.py --source-root .
python3 candidates/tiling-library-seeds-20261026/test_delta.py --source-root .
```

原生定向工具为tools/run_tiling_library_negatives.py和tools/run_tiling_local_pixels.py；要求事先准备同源native manifest、作者fixture和本机Metal Toolchain，不能把它们当作干净源码构建证明。
