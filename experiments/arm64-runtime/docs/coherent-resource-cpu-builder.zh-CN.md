# 同源 CPU／生产宿主构建入口

`tools/build_resource_cpu_runtime.py` 将已有 `evidence/tiling-default-startup-20261026/cpu-v2` 框架参数化。它不启动游戏，不构建 GPU，不使用准备器专用的 `game_pso_host_services.cpp` 替代游戏宿主，也不改旧执行包。

## 输入与顺序

1. 提供实际已完成的 coherent GPU `plan.json`、`result.json`；核对二者摘要、完整源码快照、235 个对象、库、实际编译头和外部链接依赖。
2. 从原 FEX Ninja 编译数据库选择原来的 30 个单元：23 native、4 FEX embedding、1 GPU adapter、1 AvPlayer adapter、1 game frontend。每个单元显式绑定当前仓库原始来源摘要；生成桥仅做可记录的绝对 include 路径重写。
3. 私有目录保留原前端 `../vendor` 相对 include 语义，使用普通文件副本。当前 `vendor/aether/src` 全部冻结，禁止悄悄退回旧 Shared FEX engine。共享 bb_runtime 源必须与 GPU 快照同摘要；LibAtrac9 等 CPU 专用头来自当前仓库。
4. 复用准备好的 FEXCore 静态依赖，核对可用 vendor API 头与当前仓库一致。该流程仍是 prepared-dependency 构建，不声称 clean checkout 构建已经成立。
5. 串行编译 29 个前置 CPU 单元、形成原两个私有 archive；沿用实际 GPU 链接闭包，仅将两个 AvPlayer 对象换成原 CPU 完成桥版本，链接生产 CPU host。
6. host 完成后，`cpu_cache_build_identity.py` 以原 `BE-common-runtime-build-v1`／`BE-common-bridge-build-v1` 域生成真实身份，再编译 game frontend，最后链接、签名。GPU／AvPlayer adapter 身份引用本次实际对象，不能引用旧 FEX build 中的同名对象。

所有阶段保留不运行游戏、锁屏、严重压力、交换增长与有界超时保护。输出只能是新的 `P/evidence/resource-cpu-execution-<数字>`，完成后供现有 `package_local_arm64_qa.py --cpu-runtime-manifest` 使用。

## 命令

在 `experiments/arm64-runtime` 上级仓库执行，替换相应绝对路径与唯一数字：

```sh
python3 experiments/arm64-runtime/tools/build_resource_cpu_runtime.py \
  --plan \
  --project /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype \
  --gpu-plan /absolute/coherent/plan.json \
  --gpu-result /absolute/coherent/result.json \
  --output /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/resource-cpu-execution-<数字>

python3 experiments/arm64-runtime/tools/build_resource_cpu_runtime.py \
  --build-plan /absolute/resource-cpu-execution-<数字>/plan.json
```

`--plan` 不运行编译器；它会建立新的私有输入目录并执行只读 Ninja／Mach-O 查询。旧路径不可覆盖。`--build-plan` 前后重新核全部输入摘要，原 GPU 对象不重新编译。

## 本轮验证边界

作者测试覆盖完整 235 输入身份、修改／新增源码、错误 GPU 来源、实际 GPU 编译头变化、旧输出、身份域分离、链接路径、当前 FEX／生成桥优先级，以及私有相对头文件布局。

原始失败计划保留：首轮缺少前端相对 vendor 头视图；补齐后暴露 GPU 快照不含 CPU 所需 LibAtrac9 头。这些均为准备阶段失败，不计成功。随后补齐当前仓库私有依赖视图。实际编译结果和摘要以独立 `result.json`／`cpu-runtime-manifest.json` 为准；构建成功不等于游戏功能或性能验证通过。

## 2026-10-26 实际构建结果

- 新执行根：`P/evidence/resource-cpu-execution-1793008000000000004`。
- `plan.json` SHA：`2017c9aef0ff3d4fc627210d0d5bed7580a57b5dd9baf02dc3e70f0193be7957`。
- `cpu-runtime-manifest.json` SHA：`8f37fc81ec0a06f554f8cc1d630c7160a1ca58b93bdeaa2e1e74b998ae11a25d`。
- game SHA：`3d7869b3d2a5f3ca85847fcfaa3cae9311e0962373171482ad0be7d96bca33fc`。
- production host SHA：`305f8b3187a78d348f328e6b47d8bd58e7e10d138f1354a618bd50943b4476b2`。
- 对应 GPU SHA：`df2719a07631f0316ce4ed85578663fe7c88993d4f7aa6d5115a1316fdca0cbc`，source identity `850529ad963627bacf91e2c8222b1d1765dbd005bdfd65a02c53575a5508b653`。
- 30/30 CPU 单元有当前仓库原始来源摘要，812 个准备依赖的 vendor API 头与当前仓库一致。235 个 GPU 对象只读复用，重编数量为零。
- 14 项作者测试通过；实际构建 47.83 秒，压力 1→1，交换占用 7833.625→7833.625 MiB。严格签名、ARM64、游戏→本次生产 host 加载路径及原 AvPlayer 完成符号均通过。
- 状态严格是 `built-not-run`。未启动游戏、未测 FPS，未改正式应用、存档、设置、缓存或旧执行包。

## 当前 CPU 包装层更新与 GPU 身份分离

CPU `src`／`generated` 的选择顺序是准备目录基线 → 已冻结 GPU 快照 → 当前仓库；当前 CPU-only 头文件修复必须进入私有 stage，不能被旧快照反向覆盖。共享门仅由 **235 个 GPU TU 各自真实 depfile＋源文件**产生，逐项与原编译摘要、当前仓库一致性核验并冻结 depfile SHA，不把准备器 helper main 或它的私有头当成 renderer ABI。实际 GPU 依赖变更仍拒绝；不在该集合中的 CPU-only 包装层允许更新。

计划与新 manifest 保留独立 `gpu_source_identity`（同既有 `source_identity`）及 `cpu_input_sha256`，后者覆盖本轮全部冻结 CPU／准备依赖输入。新增五项定向测试（总计 19 项）覆盖：比 snapshot 新的 CPU-only 头实际被采用、GPU 真共享头变更拒绝、GPU TU 变更拒绝、helper-only main 修复不误拒绝、depfile 目标对象错配拒绝。旧 `...0004` 是此前成功构建历史，不代表后续 CPU-only 菜单修复已编入；新一轮以新的 manifest／depfile／cache identity 证据为准。

### 当前菜单修复最终包（supersedes `...0004` for QA）

- 执行根：`P/evidence/resource-cpu-execution-1793008000000000005`，plan SHA `4883edb238189787e1b05aa65734ee17414fcfb010e4e1a35e23615b056af368`。
- CPU manifest SHA `0873f863b4a5957a1aba7d74bc6d54d02ab49314448de0bb7c1dcd526a097084`；game SHA `adaca80df8877e7ff0e5b39960f059f37a8df6a79fc27b97a0f417630c1e976a`；production host SHA `c6608270e148489e6ad42aa2c326d971d5639e094246bdf6058011f3167f572d`。
- GPU 仍为 `df2719…`／source identity `850529…`，GPU 重编数量 0。CPU input SHA 独立为 `5bd32f55d4b3f81edeecc5aac75c7c431c7ee8b158b7e3cae084df3229d55440`。
- `game_menu_runtime.h` 当前 SHA `f38cbf183b3b00e126b89a4cdfedc4b9d5426319f6ede1c4a7c9761e5e054c55`，已实际出现在游戏 frontend `objects/27.d` 中（该 depfile SHA `f5156908542e38a479aef245c7cfbfb81d024bfd7c260102c72b349641e6f969`）；`cpu_cache_build.json` 的 `bridge_parts.frontend-header-game_menu_runtime.h` 同摘要。
- 235 GPU depfile→4236 个实际依赖→642 个 snapshot／当前仓库共享输入；不把 helper main 或 CPU-only 菜单包装层误计为 GPU 共享依赖。
- 19 项作者测试通过；实际构建 47.09 秒，压力 1→1，交换占用 7825.625 MiB 不变。签名、ARM64、实际生产 host 加载路径和 AvPlayer 完成符号通过。依然仅 `built-not-run`，本轮未启动游戏或 GPU。
