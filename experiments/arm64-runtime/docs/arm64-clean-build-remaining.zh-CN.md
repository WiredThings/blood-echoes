# ARM64 干净检出构建：剩余交付清单

2026-10-26 更新：提交 `8ec54a30` 的干净 Git 归档已通过正常 renderer（235 TU）、双 helper、FEX/CPU/cache-generator 实际构建，完整 layout 与实际同 DSO 加载验证也已通过；见 [`normal-source-build-checkpoint.zh-CN.md`](normal-source-build-checkpoint.zh-CN.md)。后续有界 batch main 另有原生对照，不能冒充整树已包含该改动。外部依赖仍来自明确的既有前缀，最终正常产物 QA 组包、空依赖前缀构建、集中回归与可移植目录合同仍未完成。正式应用、存档、设置和缓存未改，三个上下文接口不构成交付前置。

下面保留旧 prepared 构建与路径问题的具体定位；其中“未实际构建”是当时检查点，不能覆盖本节新证据。正常入口已经摆脱旧对象及 generated 输入，但现有旧 QA 包尚未全部切换至正常产物。

## 当前已经覆盖的范围

[`build_game_preparation_component.py`](../tools/build_game_preparation_component.py) 从同一源码快照重建 **235 个 GPU TU**（230 个既有 TU＋5 个输入捕获 TU），再编译 production host services、PSO worker 和 module consumer；两个 helper 必须真实加载同一新 DSO。**该构建不包含 FEX 游戏 CPU 主进程。** 它明确记录 `prepared_dependencies_reused=true`、`clean_checkout_build_proven=false`，不能称为完整 ARM64 应用的干净检出构建证明。

现已补齐 renderer CMake 的邻接源码布局：从已初始化的原 `engines/metal` 复用 **65 个测试／fixture 源文件**（覆盖 61 个显式测试源及其传递依赖），`vendor/macos/build-deps.sh` 的 SPIRV-Cross 版本元数据来源和原 GPL 许可证。精确允许清单、上游 revision、原始／最终摘要见 [`vendor/metal-build-source-closure.json`](../vendor/metal-build-source-closure.json)；只改了六个测试文件共七条 quoted include 的 `gpu/`→`metal_api/` 路径，没有删测试 target、添加游戏资源或编译产物。使用说明见 [`vendor/METAL-BUILD-SOURCES.md`](../vendor/METAL-BUILD-SOURCES.md)。

`tools/macos/setup_deps.sh` 的 Rosetta 检查现仅适用于 `BB_ARCH=x86_64`；`arm64` 不再需要 Rosetta，非法架构仍拒绝。九项 [`test_metal_build_source_closure.py`](../tests/test_metal_build_source_closure.py) 控制通过，包含无 Rosetta 的 ARM、缺 Rosetta 的 x86 拒绝、SHA 变更／symlink 反例和完整测试源布局。fresh ARM64 CMake configure 已成功生成（约 1.8 秒，未构建任何 target），证据在 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/metal-source-closure-configure-1793014775457008000/`。该 configure **复用了 prepared dependencies**，不能替代下述 clean-checkout 构建证明。

[`vendor/metal_api/cmake/GamePreparation.cmake`](../vendor/metal_api/cmake/GamePreparation.cmake) 现提供正常 `game-pso-preparer`、`game-module-preparer`、`metadata-layout` targets，显式源码根绑定正在编译的同一 renderer；两个 consumer 只链接同一 `bbgpu` 与 production host-services 对象，metadata 保持无 renderer 链接。六项配置控制与四个 main／host TU 的 `-fsyntax-only` 检查通过，未生成对象或构建 targets。正常配置入口现已显式启用这些 targets 并实际链接，最终 QA 打包与回归继续推进；使用方法及证据见 [`native-preparation-cmake.zh-CN.md`](native-preparation-cmake.zh-CN.md)。

## 尚未闭合的具体输入

- prepared builder 使用 `evidence/resource-writeback-integration/gpu-build-inputs.json`、`evidence/tiling-library-integration-20261026/native/build.json`，以及固定 `/private/tmp/blood-echoes-arm64-metal-20261023/build`。仍复用该目录生成的 host-shader headers、sirit／FSR archives、SPIRV-Cross archives、Homebrew headers／libraries 和既有 DSO load-edge 来源。下一步必须由新依赖前缀与新 CMake build 生成这些输入，不能复制旧清单冒充 fresh 构建。`BB_FONT_PATH` 是 `.incbin` 的编译输入，应绑定当前源码字体；不是运行时外置字体要求。
- [`configure.py`](../configure.py) 现已改用显式配置入口，不再重放 `evidence/configure.json`；两生成器可使用独立源码／输出目录，`targets.cmake` 可显式选择 generated、依赖／SDL、正常 renderer build／artifact／RPATH，并保持固定 Shared 开发运行根。新入口从当前正常 CMake File API／compile commands 提取头字段；原 `generated/*` 仍保留历史绝对路径，**不得作为新入口的生成输入复用**。十项作者控制、八项身份根控制、真实桥生成与 FEX configure 已通过，其后已在上述干净归档中完成正常 target 构建、CPU 身份生成和链接，双 helper 的实际 native 加载也已验证；不能把此前 configure-only 检查当作最新进度。具体命令和未闭合事项见 [`explicit-native-configuration.zh-CN.md`](explicit-native-configuration.zh-CN.md)。

## 最短落地顺序

1. 初始化仓库及递归子模块；renderer 邻接 `macos`／`tests` 源闭包和 dependency prefix／build／GPU target／生成输入的显式参数入口已补齐，尚未执行完整新前缀构建。
2. 复用仓库根 `tools/macos/setup_deps.sh`（`BB_ARCH=arm64`）、`tools/macos/setup_metal_deps.sh`（`BB_METAL_ARCH=arm64`）和 `tools/macos/build_metal_arm64.sh` 的 ARM 依赖／CMake配置，在新前缀重建依赖及 host shaders。纯 ARM setup 的 Rosetta 硬依赖已修复。`vendor/macos/build-deps.sh` 保留字节相等的旧元数据源；它与实际原入口 `engines/metal/macos/build.sh` 均硬写 x86_64／Rosetta／MoltenVK，不能原样当作纯 ARM 入口；该树没有 `build_macos.sh`。
3. 正常 renderer、两个 helper、layout、FEX CPU 与 typed bridges／身份清单已从干净归档实际构建，加载边和完整 layout 已验证。接下来将这些正常产物接入最终 QA 清单和包；有界 batch main 的 source delta 单独记录，继续完成全部 family 及整体回归，不能靠旧产物名称冒充已切换。
4. 复用仓库根 `tools/macos/build_from_source.sh`、`package_optimized.py`、`package_metal.py` 的“源码生成基础包再组装”骨架，接入上述 ARM CPU／helpers。现有原入口尚不构建本实验的 FEX CPU，不能仅调用它便宣布本轮交付完成。
5. 使用新检出、新依赖前缀、空 build 目录完成真实构建，再对迁移后的包检查实际 ARM64 加载边、同 DSO、目录准入及最终功能／性能／内存。此前不得将 prepared 构建成功升级为 clean-checkout／可移植交付成功。

## Shared 护栏属于开发隔离，不能直接删除

[`src/game_runtime.cpp`](../src/game_runtime.cpp) 的 `private_path`、[`src/game_pso_preparer.mm`](../src/game_pso_preparer.mm) 的 `Shared`／state 准入、[`prepare_local_aot_stage.py`](../tools/prepare_local_aot_stage.py) 的 `PRIVATE_PROJECT` 与生成 bootstrap、[`package_local_arm64_qa.py`](../tools/package_local_arm64_qa.py) 都明确依赖 `/Users/Shared/BloodEchoesDevelopment`。这是当前正确的开发隔离边界，现 QA 包也明确为 host-local。

正式包应由包内代码身份及原启动器选定的数据根建立同等严格的 canonical／ownership／范围准入，分别处理代码、状态、原游戏／mod 来源和缓存路径。不能简单删掉 Shared 检查，不能让迁移后的程序读取开发 QA 或正式数据的未授权目录。原存档、设置和缓存保持不变。
