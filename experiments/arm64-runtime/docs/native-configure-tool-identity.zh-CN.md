# 正常 CPU 配置驱动的显式工具身份

2026-10-27。仅源码修正与纯 Python 合同验证；没有运行 CMake、Ninja、编译器、native/GPU 或游戏，没有改动正在进行的 S2 构建及旧 receipt。

## 问题与修正

`configure_native_build.plan()` 原本把裸 `cmake` 放在 configure argv[0]。构建驱动使用受限 PATH 时找不到它，即使已核正常 renderer 的 CMakeCache 记录了真实 CMake/Ninja。

现在沿用该 CMakeCache 的 `CMAKE_COMMAND`、`CMAKE_MAKE_PROGRAM` 两个明确选择：两者必须是非空绝对路径，所选实体必须存在、为可执行普通文件。缓存中的 Homebrew 符号链接可解析，但真正 configure 命令只使用 canonical 实体路径，不搜索 PATH，也不静默寻找另一工具。renderer 输入身份新增 `build_tools.{cmake,ninja}`，各自冻结 cache key、缓存选择路径、canonical 实体路径和 SHA256；原 cache/compile_commands/target SHA 门保留。

原 renderer 必须明确使用 `CMAKE_GENERATOR=Ninja`。CPU configure argv[0] 使用该 CMake 实体，同时显式传 `-G Ninja` 与 `-DCMAKE_MAKE_PROGRAM=<已核Ninja实体>`。原 source、arm64、preparation/source root、独立输出目录、AvPlayer/SDL/current-target 及既有配置选项不变。

`prepare()` 原本在 generator 前后完整重读 renderer 输入；新工具身份是这份完整输入的一部分，因此工具内容改变或选中 symlink 改指另一实体，即使文件名或内容恰好相同，也会使既有计划失效。配置过程不执行历史命令，不把新的工具来源改为 PATH 探测。

## 纯 CPU 作者结果

`tests/test_native_build_configuration.py` 的 14 项 plan/mock 控制通过：7 项原 source/arm64/preparation/artifact/runtime/output/显式配置门，加 7 项新控制覆盖 canonical symlink 选择、缺少/空/相对工具、缺文件/目录/不可执行文件、错误 generator、计划后工具内容变化、symlink 目标变化，以及 mock generation 期间变化。新 mock 只替代 subprocess.run，不运行实际工具；本轮未执行另 3 项生成器 subprocess 测试。

成功结果：`/private/var/folders/gc/42dvg5q93_z0zz0rfgjx0h0w0000gn/T/native-build-author-controls-r60xjyz2/tool-path-contract-result.json`。

最初作者输出放在 D/evidence，触发原“构建输出不可位于 source 内”门；随后仅把作者临时输出改到 canonical 系统临时目录，未放宽原门。该首次失败结果保留在 `D/evidence/native-build-author-controls-1793037545774849000/tool-path-contract-result.json`。

本轮文件仅 `tools/configure_native_build.py`、原 `tests/test_native_build_configuration.py` 和本开发说明。源码合同通过不代表本轮真实 CPU 构建已运行，下一次统一构建才验证真实工具调用。
