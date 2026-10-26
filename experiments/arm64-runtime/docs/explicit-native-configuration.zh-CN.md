# 显式 ARM 源码配置入口

2026-10-26：已补源码／独立生成目录／依赖／正常 renderer build 接线，实际桥生成与 FEX configure 已通过；尚未完成完整构建或实际 ABI 验收，不是 clean-checkout 构建完成声明。

`configure.py` 现在调用 `tools/configure_native_build.py`，不再读取和替换 `evidence/configure.json` 的历史命令。正常用法先按 [`native-preparation-cmake.zh-CN.md`](native-preparation-cmake.zh-CN.md) 配置当前源码的 renderer，并在配置前申请 CMake File API：

```sh
mkdir -p "$renderer_build/.cmake/api/v1/query"
touch "$renderer_build/.cmake/api/v1/query/codemodel-v2"
# 然后运行 renderer 原 CMake configure，显式启用 preparation targets。

python3 "$runtime_source/configure.py" \
  --source-root "$runtime_source" \
  --build-dir "$cpu_build" --generated-dir "$generated_output" \
  --renderer-build "$renderer_build" \
  --dependency-prefix "$native_dependency_prefix" \
  --dependency-prefix "$header_dependency_prefix" \
  --plan
```

`--plan` 只读取当前 CMake 配置并输出新计划，不创建生成目录，不编译目标。去掉它会先运行两个既有生成器，再进行 FEX 的 CMake **配置**；不会执行 renderer／FEX target build。`--prepare-only` 仅生成桥及当前 GPU 头字段，不运行 FEX configure。依赖前缀可以重复传入；若需要原生 Homebrew 提供的明确依赖，应显式加入其前缀，而不是由新入口暗中选择。

源码根、CPU build、generated output 和 renderer build 相互区分。新入口要求 generated output 首次不存在或为空，且 CPU build／生成目录位于源码树之外，避免覆盖仓库已有 `generated/`。`--runtime-root` **仍严格限定** `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`；这是当前 host-local 开发隔离，不是新的用户数据选择入口。`src/game_runtime.cpp` 和准备 consumer 的 Shared 准入未修改。

## 复用的算法与新字段

- `generate_native.py` 保持原 Clang AST、opaque/callback/span 判定、exit 映射和签名生成算法；新增 `--source-root`、`--output-dir`、`--include-dir`、`--compiler` 参数。
- `generate_gpu.py` 保持原声明 `decltype`／equeue 声明扫描与 adapter 生成；新增独立源码／输出／header manifest 参数。既有导出名称表作为源码配置存放在 `config/gpu-adapter-headers.json`，不再要求从历史 evidence 读取。它只是名称表，真实 C++ 声明仍来自当前 renderer 头，生成文本本身不代表 ABI 编译通过。
- 新入口要求当前 renderer 的 CMake cache、codemodel 和 `compile_commands.json` 共同指向当前源码、单一 ARM64 `bbgpu` 及开启的准备组件。只提取该 target 的当前 include／definition 字段、真实 SDL 路径和 artifact 路径，写入独立 `gpu_flags.cmake`；不执行其编译命令，也不借用历史对象、flags 表或 DSO。
- `targets.cmake` 新增独立 generated、runtime-root、dependency includes、SDL library、renderer build／target 参数。GPU adapters 使用当前头的 C++23 要求；RPATH来自所选 renderer artifact 所在目录。正常 renderer 外部构建边调用 `cmake --build <renderer-build> --target bbgpu --parallel 1` 并声明其产物，只有后续显式构建 CPU target 时才可能执行。
- `bb_fex_embedding` 同时保留 `CMAKE_BINARY_DIR/generated`（FEX／CPU身份内部生成物）和独立 `BB_ARM64_GENERATED_DIR`（native bridges／adapters）；`game_runtime.cpp` 的 `native_adapters.inc` 消费后者。native runtime 的 17 个 generated C 源直接来自独立目录，无须靠旧源码 `generated/` 寻找。
- `make_cpu_cache_build.py` 的输入读根现显式分成 source、CPU build、renderer build；仅 CPU build 内可写身份头和 manifest。所有根仍须 canonical／owned，输入须 regular／bounded／nonlinked，并通过 `O_NOFOLLOW` 和读取前后 inode／size／mtime 校验；输出先共同校验范围，再原子替换。没有放宽任何运行时 Shared 目录准入。
- 旧 `targets.cmake` 的默认路径仍保留给旧开发 build 的兼容性；新入口会显式覆盖它们。不要把旧默认 candidate DSO 路径视为正常新构建路径。

## 已验证与剩余工作

十项短作者控制覆盖显式 plan、错误源码／架构／未启用组件／artifact越界拒绝、runtime-root不迁移、生成输出不落回源码、GPU头范围校验和原 native 算法的合成 AST 输入。它们没有构建或运行 renderer／FEX／游戏，合成 AST 控制不代表真实游戏 ABI 通过。

控制记录：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/native-build-author-controls-1793022310961384000/result.json`，十项通过，约 1 秒。另有八项合成身份根控制通过，覆盖输入／输出逃逸、文件和父目录链接、独立根正例以及坏 manifest 路径不得先写 header：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/cpu-cache-build-roots-1793022310844558000/result.json`。它们不代表实际链接产物的身份已生成。

随后使用真实正常 renderer File API 的当前 19 个 include／8 个 definition 字段执行新入口，实际 Clang 桥生成和 FEX configure 在约 8.26 秒内返回 0：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/normal-native-configure-1793022002861853000/`。`graph-check.json` 确认独立 generated 路径、17 个 native generated C 源、348 条 native signature 记录、保留的 FEX 内部 generated 目录、正常 DSO 候选链接边和显式身份读根；没有构建 renderer／FEX targets，也没有生成实际链接身份或启动游戏。

## CPU 侧 host 与 renderer 的关系

不能只因为旧 builder 有 `host_overrides` 就断言必须再建不同语义的库。对既有 coherent02 和 `resource-cpu-execution-1793008000000000005` 的有界核对表明：`avplayer_state.cpp`／`avplayer_source.cpp` 两个 host compile command 仅输出／depfile 路径不同；对应实际对象逐字节一致，SHA-256 分别为 `7247a91ec9847bc93262fec63a816768b5b152ba2e8ef31f308e9d0bb2a00229`、`5ad9d99195350af380b8d22be3cabcb95c2ab7a850d9eb97278b9a75dc1352cc`。将两处等字节对象及输出名归一化后，完整 **301 字段链接命令一致**；当前两源文件与 completion thread 头也与该 coherent 输入相同。

精确摘要见 [`normal-cpu-host-equivalence.json`](normal-cpu-host-equivalence.json)。这说明该轮 host overrides 是历史组装中的重复编译，不是额外的 ABI 变体。因此新入口明确把正常同源 `bbgpu` 作为 **CPU-host 复用候选**，不另造重复 235 TU 库；配置时显式传 `BB_ARM64_CPU_HOST_LIBRARY`，不会隐含采用旧 candidate 路径。新的实际产物是否正确导出 revision1、保留 attach／detach 绑定并通过 native 加载／播放／回调验证，仍未完成。旧对象等字节的证据不能替代新产物的这些验证。

仍须用新依赖前缀完成正常 renderer／CPU／helper 构建，执行真实身份生成，验证 adapter 编译、CPU-host 复用和实际加载边，再接入最终包清单。当前未证明新入口的完整链接、运行 ABI、可移植安装包或 clean-checkout 构建。运行时私有目录合同仍固定 Shared，应继续作为单独、明确的交付边界处理。
