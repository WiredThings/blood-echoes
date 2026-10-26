# ARM64 干净检出构建：剩余交付清单

2026-10-26，源码审查与有界构建闭包修复后的清单。已运行一次 fresh CMake configure 和九项短定向控制，没有构建 renderer／FEX targets 或启动游戏，也没有修改正式应用、存档、设置或缓存。这是当前目标的剩余交付工作，不是新增长期优化；冻结的三个上下文接口不构成交付前置。

## 当前已经覆盖的范围

[`build_game_preparation_component.py`](../tools/build_game_preparation_component.py) 从同一源码快照重建 **235 个 GPU TU**（230 个既有 TU＋5 个输入捕获 TU），再编译 production host services、PSO worker 和 module consumer；两个 helper 必须真实加载同一新 DSO。**该构建不包含 FEX 游戏 CPU 主进程。** 它明确记录 `prepared_dependencies_reused=true`、`clean_checkout_build_proven=false`，不能称为完整 ARM64 应用的干净检出构建证明。

现已补齐 renderer CMake 的邻接源码布局：从已初始化的原 `engines/metal` 复用 **65 个测试／fixture 源文件**（覆盖 61 个显式测试源及其传递依赖），`vendor/macos/build-deps.sh` 的 SPIRV-Cross 版本元数据来源和原 GPL 许可证。精确允许清单、上游 revision、原始／最终摘要见 [`vendor/metal-build-source-closure.json`](../vendor/metal-build-source-closure.json)；只改了六个测试文件共七条 quoted include 的 `gpu/`→`metal_api/` 路径，没有删测试 target、添加游戏资源或编译产物。使用说明见 [`vendor/METAL-BUILD-SOURCES.md`](../vendor/METAL-BUILD-SOURCES.md)。

`tools/macos/setup_deps.sh` 的 Rosetta 检查现仅适用于 `BB_ARCH=x86_64`；`arm64` 不再需要 Rosetta，非法架构仍拒绝。九项 [`test_metal_build_source_closure.py`](../tests/test_metal_build_source_closure.py) 控制通过，包含无 Rosetta 的 ARM、缺 Rosetta 的 x86 拒绝、SHA 变更／symlink 反例和完整测试源布局。fresh ARM64 CMake configure 已成功生成（约 1.8 秒，未构建任何 target），证据在 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/metal-source-closure-configure-1793014775457008000/`。该 configure **复用了 prepared dependencies**，不能替代下述 clean-checkout 构建证明。

[`vendor/metal_api/cmake/GamePreparation.cmake`](../vendor/metal_api/cmake/GamePreparation.cmake) 现提供正常 `game-pso-preparer`、`game-module-preparer`、`metadata-layout` targets，显式源码根绑定正在编译的同一 renderer；两个 consumer 只链接同一 `bbgpu` 与 production host-services 对象，metadata 保持无 renderer 链接。六项配置控制与四个 main／host TU 的 `-fsyntax-only` 检查通过，未生成对象或构建 targets。完整 ARM 应用入口仍需显式启用这些构建 targets 并完成实际链接／打包；使用方法及证据见 [`native-preparation-cmake.zh-CN.md`](native-preparation-cmake.zh-CN.md)。

## 尚未闭合的具体输入

- prepared builder 使用 `evidence/resource-writeback-integration/gpu-build-inputs.json`、`evidence/tiling-library-integration-20261026/native/build.json`，以及固定 `/private/tmp/blood-echoes-arm64-metal-20261023/build`。仍复用该目录生成的 host-shader headers、sirit／FSR archives、SPIRV-Cross archives、Homebrew headers／libraries 和既有 DSO load-edge 来源。下一步必须由新依赖前缀与新 CMake build 生成这些输入，不能复制旧清单冒充 fresh 构建。`BB_FONT_PATH` 是 `.incbin` 的编译输入，应绑定当前源码字体；不是运行时外置字体要求。
- [`configure.py`](../configure.py) 现已改用显式配置入口，不再重放 `evidence/configure.json`；两生成器可使用独立源码／输出目录，`targets.cmake` 可显式选择 generated、依赖／SDL、正常 renderer build／artifact／RPATH，并保持固定 Shared 开发运行根。新入口从当前正常 CMake File API／compile commands 提取头字段；原 `generated/*` 仍保留历史绝对路径，**不得作为新入口的生成输入复用**。十项作者控制、八项身份根控制、真实桥生成与 FEX configure 已通过，实际 target 构建／身份生成／链接仍未完成。正常同源 bbgpu 的 CPU-host 复用有旧对象等字节依据，但新产物仍需实际 native 加载验证。具体命令和未闭合事项见 [`explicit-native-configuration.zh-CN.md`](explicit-native-configuration.zh-CN.md)。

## 最短落地顺序

1. 初始化仓库及递归子模块；renderer 邻接 `macos`／`tests` 源闭包和 dependency prefix／build／GPU target／生成输入的显式参数入口已补齐，尚未执行完整新前缀构建。
2. 复用仓库根 `tools/macos/setup_deps.sh`（`BB_ARCH=arm64`）、`tools/macos/setup_metal_deps.sh`（`BB_METAL_ARCH=arm64`）和 `tools/macos/build_metal_arm64.sh` 的 ARM 依赖／CMake配置，在新前缀重建依赖及 host shaders。纯 ARM setup 的 Rosetta 硬依赖已修复。`vendor/macos/build-deps.sh` 保留字节相等的旧元数据源；它与实际原入口 `engines/metal/macos/build.sh` 均硬写 x86_64／Rosetta／MoltenVK，不能原样当作纯 ARM 入口；该树没有 `build_macos.sh`。
3. 从当前源码构建 renderer、两个 helper、实际 layout exporter 和 FEX CPU，重新生成 typed bridges、flags 与身份清单。helper／layout 正常 CMake targets 已接入，但完整 ARM 构建入口与最终清单尚须切到它们，完成实际链接后的同 DSO／纯系统 metadata 加载边验证，才能摆脱历史 baseline 命令清单。
4. 复用仓库根 `tools/macos/build_from_source.sh`、`package_optimized.py`、`package_metal.py` 的“源码生成基础包再组装”骨架，接入上述 ARM CPU／helpers。现有原入口尚不构建本实验的 FEX CPU，不能仅调用它便宣布本轮交付完成。
5. 使用新检出、新依赖前缀、空 build 目录完成真实构建，再对迁移后的包检查实际 ARM64 加载边、同 DSO、目录准入及最终功能／性能／内存。此前不得将 prepared 构建成功升级为 clean-checkout／可移植交付成功。

## Shared 护栏属于开发隔离，不能直接删除

[`src/game_runtime.cpp`](../src/game_runtime.cpp) 的 `private_path`、[`src/game_pso_preparer.mm`](../src/game_pso_preparer.mm) 的 `Shared`／state 准入、[`prepare_local_aot_stage.py`](../tools/prepare_local_aot_stage.py) 的 `PRIVATE_PROJECT` 与生成 bootstrap、[`package_local_arm64_qa.py`](../tools/package_local_arm64_qa.py) 都明确依赖 `/Users/Shared/BloodEchoesDevelopment`。这是当前正确的开发隔离边界，现 QA 包也明确为 host-local。

正式包应由包内代码身份及原启动器选定的数据根建立同等严格的 canonical／ownership／范围准入，分别处理代码、状态、原游戏／mod 来源和缓存路径。不能简单删掉 Shared 检查，不能让迁移后的程序读取开发 QA 或正式数据的未授权目录。原存档、设置和缓存保持不变。
