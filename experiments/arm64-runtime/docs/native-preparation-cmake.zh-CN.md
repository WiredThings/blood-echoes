# 本机准备组件的正常 CMake targets

2026-10-26，源码接入已完成；当前证据是配置／目标图控制，不是完整新依赖构建或可运行安装包验收。

`vendor/metal_api/CMakeLists.txt` 新增编译时选项：

```sh
cmake -S "$runtime_source/vendor/metal_api" -B "$renderer_build" \
  -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=26.0 \
  -DCMAKE_PREFIX_PATH="$dependency_prefix;$header_prefix" \
  -DBB_BUILD_GAME_PREPARATION=ON \
  -DBB_GAME_PREPARATION_SOURCE_ROOT="$runtime_source"

cmake --build "$renderer_build" --parallel 1 \
  --target game-pso-preparer game-module-preparer metadata-layout
```

变量分别为当前 ARM runtime 源码根、全新 build 目录和明确的依赖前缀；SPIRV-Cross 等原依赖仍按 renderer 原 CMake 查找，可继续显式传入其原 `SPIRV_CROSS_*` 参数。`BB_GAME_PREPARATION_SOURCE_ROOT` 必须含正在构建的同一个 `vendor/metal_api`，只用于选择编译源码，不能指定游戏／存档／缓存目录，也不会改变两个 consumer 内原有的 Shared 运行输入校验。

`BB_BUILD_GAME_PREPARATION` 默认 OFF，便于单独配置 renderer；**ARM 应用的完整构建入口必须显式 ON**。这不是运行时实验开关，不代表关闭默认的本机首启准备。当前默认开启的 capture producer 仍须具备其源码闭包；完整构建将其与两个 consumer 一起从同一个源码根编译。

新增正常 targets：

- `game-pso-preparer`：原 PSO main＋production `game_pso_host_services.cpp` 对象，链接同一个正常 `bbgpu` target。
- `game-module-preparer`：原 module main＋同一个 host-services 对象，链接同一个 `bbgpu`；显式启用 `BB_WITH_OWNED_COMPANION_CONSUMER=1`，因此构建时拒绝关闭同 renderer 的 capture producer。
- `metadata-layout`：既有 `tests/metadata_layout.cpp`，使用同一 canonical renderer headers 和明确的平台／标准／宏字段。`profile_fields.inc` 从当前 `Profile` 头按声明顺序生成，头变化会触发重新配置。它不链接 renderer、host-services 或第三方 DSO，继续保持独立 ABI 元数据工具的合同。

两个 consumer 不再通过历史 evidence 的 compile/link 命令拼装，不向 main 加入替代 compiler、guest runtime 或覆盖 TU。renderer 的 host-shader headers、sirit／FSR archives、SPIRV-Cross 和系统依赖，继续由正常 CMake target graph／明确依赖前缀提供；不是从旧 prepared build 目录复制对象。host 服务仍对不支持的 guest／VM／重启操作明确拒绝。

定向配置证据：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/game-preparation-cmake-1793018964949195000/result.json`。六个配置场景通过；CMake File API 核对了两 consumer 的同 renderer／host 对象边、无覆盖 TU、metadata 的无 renderer 链接，以及当前 49 个 Profile 字段的完整顺序。使用了本机现有依赖进行查找，未构建 targets、启动 GPU／游戏；这些证据不能替代实际链接后的 DSO identity、纯系统 metadata 加载边和首启功能验证。

同目录 `syntax-1793019076096583000/result.json` 记录从新 CMake `compile_commands.json` 直接导出的四项 `-fsyntax-only` 检查：production host-services、PSO main、module main、metadata emitter 全部通过。保留原显式源码／include／宏／平台／ARC 字段，仅去掉对象输出参数；未生成 `.o`，没有编译 235 个 renderer TU 或 FEX。

剩余工作是将完整 ARM 构建入口与包清单接到这些 targets，完成新依赖前缀、全新 build、FEX CPU 及最终同 DSO／运行验证。此前 prepared 235 TU 结果仍不等于 clean-checkout 构建。
