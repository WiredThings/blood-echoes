# 固定 Metal 内核首启准备与整批建库

本次仅更新隔离 ARM64 开发分支。正式应用、正式数据、既有缓存和 Release 不变；已有一次暖场景采样，仍缺匹配的性能和内存对照。

## 默认启动路径

原启动器仍负责语言、补丁、联网、F2/F3 和退出码。只在原 final-exec 边界增加默认准备步骤，不另写游戏启动流程。

固定工厂中可直接确定完整输入的八类内核（kind 4、5、6、9、10、11、12、13）先由原 HostKernels 的 Describe/Replay 收集本机建库请求，再调用既有本机 Metal Toolchain 策略，最后用实际本机函数与最终设备管线身份完成严格准备。它们包括 Fill、inline update、UpscaleMerge、Presenter、速度图与格式转换。依赖实际调用常量的 tiling、fault 和 MSAA 家族仍从真实调用输入收集，不能用猜测常量伪造覆盖。

无窗口 worker 不构造 guest Runtime、Storage 或整个渲染器，不修改 profile/h2 历史。Presenter 格式取自原 C++ 提供者。Source 回退明确记录为 Source，不能因请求 load 或读到旧归档就宣称本机建库完成。没有额外纹理、常驻 GPU 缓存或新的资源淘汰预算。

组件能力由**实际重定位后的 worker**执行纯标量查询取得；旧 worker 的 usage 64 不获得新能力。查询最多捕获 4097 字节，超过 4096 拒绝；最长工作 10 秒，清理并 wait 自建进程组。真实 dyld 加载记录复用既有 PID、完整路径和 owned-file SHA 校验，支持应用路径含空格。

## 游戏管线目录：整批收集后统一建库

目录仍按有界页读取，阶段进度保存在隔离磁盘。第一遍收集全目录缺失请求，之后统一本机建库，第二遍用实际 native 严格验证。一个完整调用至多一次可靠首审、一次收集后的最终 build；不是每页都重审并重建。首审没有请求时可以进入 record，不伪造成功。

同一次调用中，若首遍全部严格命中且没有新 record，只在结束时确认工具、SDK、系统、请求、库和环境身份不变后提交，不重复遍历第二次。磁盘阶段记录与 ready hint 只是调度信息，不能代替当前 native 准入。中断可续作；晚出现的新建库请求保留为 partial/下次继续，不能无限循环或跳过绘制。

## 本轮实际证据与限制

- 同源 230 个 GPU 单元、30 个 CPU 单元、两个 AvPlayer 桥和 GPU 窗口主进程均已重建。
- 11 个真实原生进程验证 Source/Local、跨进程归档、坏记录回退、原主归档共存与 GPU 像素。
- 八类固定内核：空目录 probe 为 0 ready/0 普通 PSO 编译；本机建出八份库，prepare 为 8 ready；独立暖进程为 8 strict hits/0 普通 PSO 编译。缺失 build 的反例回到 Source，0 虚假 ready。该组内存压力 1，交换增量 0，不等于整游戏内存结论。
- 真实原入口中八类固定内核约 5.26 秒完成；当前目录 404/404 游戏配方完成最终验证，收集阶段 404 条、首审一次、最终建库一次，共约 385.63 秒。准备期间压力 1、交换增量 0。
- 随后的游戏加载触发既有 5 秒内交换增长超过 1 GiB 保护，未取得有效场景帧率。本轮不能宣称 FPS、加载速度或整游戏内存已经改善。暖重启与场景测量另外记录。
- 首轮整合暴露含空格 dyld 路径截断，现已复用原解析器修复；七项定向控制（五个真实 Popen 作者进程）通过。错误 PID、错误 SHA 和第二份不同 GPU 库仍会拒绝。

404 是本轮可准入目录，不代表全游戏所有管线状态。静态程序清单也不等于完整 PSO；全游戏覆盖、三个 context/signal 接口及性能对照仍是未完成项。白模调查保持已确认风险的收口边界，不因本轮结果扩大猜测。

## 可复现的定向控制

从 `experiments/arm64-runtime` 执行以下命令；测试不携带游戏代码或编译产物：

```sh
python3 tools/test_game_pso_cross_page.py
python3 tools/test_local_builder_validation_anchor.py
python3 tests/metal_preparation_regressions/fixed_helpers/test_controller.py --source-root "$PWD"
python3 tests/metal_preparation_regressions/fixed_helpers/test_source_contracts.py --source-root "$PWD"
python3 tests/metal_preparation_regressions/fixed_helpers/test_injected_capability.py --source-root "$PWD"
mkdir -p /tmp/blood-echoes-helper-cpu-controls
python3 tests/metal_preparation_regressions/fixed_helpers/test_bounded_capability.py --source-root "$PWD" --work-root /tmp/blood-echoes-helper-cpu-controls
python3 tests/metal_preparation_regressions/fixed_helpers/test_fixed_helper_dyld.py --source-root "$PWD" --work-root /tmp/blood-echoes-helper-cpu-controls
```

CPU 作者事件控制验证控制流与准入边界，不能冒充真实 GPU 加载、全游戏预编译或性能验证。原生控制使用本机编译器和 Metal Toolchain；其输出留在本机隔离目录，不分发 AIR、metallib、PSO 或游戏配方。

## 后续暖场景测量

同一个新 QA 包和隔离缓存暖重启，416/416 配方准备完成，record 0；首审一次、最终 build 一次，其中 builder 完整审核一次、复用一次。首遍已有404项命中、12项只有本机函数输入，因而需要第二遍，不能把这次称为“全 strict 单遍快路径”。准备175.27秒，进入场景248.82秒；它不是匹配 A/B 的加速结论。

随后90秒固定相机观察完成：48.935 FPS，p95 21.13 ms、p99 21.57 ms，GPU平均19.186 ms。两个进程相加的 footprint 中位数5851.3 MiB，不是去重后的物理内存。整个运行 swap 增长856.625 MiB，最高压力4曾短暂出现，但未持续到5秒；所有保护保持原值。不能声称“不增加内存压力”已证明。

实际API CSV总计35列，其中17项Metal统计，5836个完整行；计划终止时仅最后一行不完整，中间无坏行。GPU ready之后共4次源码建库API、3.035 ms；7次本机库加载API；8/8 strict PSO命中；普通render/compute工厂分别9/6次，合计6.024 ms，无失败。最后30秒这些API均0。GPU ready涵盖菜单和加载，不能等同进入可操作场景；API walltime不是纯编译/GPU执行时间，也未统计newFunction特化、MetalFX内部或所有驱动编译。

这些结果验证了原入口可使用新准备产物，但没有证明稳定FPS收益、全游戏无现场编译或内存达标。原先约2%的跨版本帧率差异仍未归因。本机测试包保持host-local QA性质；不是可分发正式安装包。
