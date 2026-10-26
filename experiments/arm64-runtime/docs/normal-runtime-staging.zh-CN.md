# 正常构建产物接入隔离 QA 合同

`tools/stage_normal_runtime.py` 将正常 CMake 构建的 CPU、同源 Metal DSO、两个准备工具和完整封口 layout 接入已有隔离打包合同。它不编译、不启动游戏、不创建 GPU Device，不读取历史 QA 二进制作为模板。原生执行限于签名、Mach-O/符号检查及原组件合同要求的有界标量 capability 查询；不能称为“完全不执行原生代码”。

## 准入与复制

- 所有输入是固定 Shared 开发根 `P=/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype` 下 `evidence` 内当前用户拥有的 canonical 普通文件，拒绝链接、额外硬链接和路径逃逸。结果、plan、源 TU、File API、CMake cache、实际产物及封口 layout 都核 SHA；结束前再次核对输入。
- renderer 与 CPU 使用同一正常 source/build receipt，核实 ARM64、原协议版本、实际唯一 DSO 加载边。源清单和 layout 继续通过原严格 reader，未放宽目录或 identity 校验。
- 输出必须是不存在的 `P/evidence/resource-cpu-execution-<数字>`。CPU 与 host 都先复制进此根；复制前后 SHA 相同。只修改 CPU **副本** 的精确加载边：先把其 `@rpath` 解到原正常 DSO，再改为根内 host 副本的绝对路径并签名。host 保持原正常 DSO 字节。原构建目录、正式应用、存档、设置及缓存不改。
- 生成原 `schema=1` 的 `cpu-runtime-manifest.json`，由现有 `load_cpu_runtime_manifest` 准入。另生成 `GAME-PSO-COMPONENT.source.json`；原 `freeze_inputs` 将 worker/module 副本重定位到相同 host，复制 layout 所需源头并生成相对路径 runtime component，最后由原 `load` 严格核验。

可选的 main-only worker repair 必须同时提供 result 与 plan。工具核对实际 DSO、全部编译 flags/headers 与链接输入，允许差异仅为明确的 worker main 源、该 object 与输出位置；拒绝 shader compiler override、local quoted-header shadow 及错误 source SHA。receipt 同时保留原 main 和修复 main 摘要，不能声称两份 main 源相同。省略这两个参数时使用正常原 worker，保留其旧单项回退行为。

## 当前可执行入口

下列命令只在没有游戏运行时使用；Shared 执行根的既有保护不变：

```sh
P=/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype
python3 tools/stage_normal_runtime.py \
  --renderer-build "$P/evidence/normal-clean-source-1793023825033571000/build" \
  --cpu-build "$P/evidence/normal-clean-source-1793023825033571000/cpu" \
  --gpu-result "$P/evidence/normal-clean-targets-1793023825033571000/result.json" \
  --cpu-result "$P/evidence/normal-clean-cpu-build-1793025288111560000/result.json" \
  --layout-result "$P/evidence/normal-clean-layout-1793023825033571000/result.json" \
  --worker-repair-result "$P/evidence/normal-batch-main-build-1793026612098121000/result.json" \
  --worker-repair-plan "$P/evidence/normal-batch-main-build-1793026612098121000/plan.json"
```

此命令在 `experiments/arm64-runtime` 下执行。日期命名路径是本次真实输入示例，并非运行时查找旧历史 plan 的算法。新构建应显式提供自己的正常 receipts。

## 已验证范围与剩余边界

1. 实际 staging 与原 CPU manifest loader 已通过：`P/evidence/resource-cpu-execution-1793029304846779000/result.json`。该次 raw GPU SHA 为 `ec7e930967edc24423f3e4fcf8a5635f784cb86ef729802d2bef6af63109af2d`，已核验 main-only repair。CPU 副本签名/重定位后摘要改变，原 normal 产物未改。
2. 随后为工具加入原 `freeze_inputs` 与 runtime `load` 封口；为避免与已运行 QA07 争抢资源，尚未单独重跑新整段 stager。主任务 QA07 已使用上述实际 source component 完成原打包器的真实 worker/module freeze，不能把该事实写成新 stager 整段再次执行。
3. `tests/test_stage_normal_runtime.py` 的 12 项纯作者控制全过：`P/evidence/normal-stage-controls-1793029820916391000/result.json`。覆盖输出名/已有输出/链接/越界、错误和变化 SHA、编译与链接归一化边界、唯一实际 DSO、精确重定位 argv。此组 mock 控制未运行原生进程，不能替代真实 Mach-O 加载检查或游戏功能测试。

依赖仍复用本次正常构建明确声明的 prepared prefixes。manifest 明确记录 `prepared_dependencies_reused=true`、`clean_checkout_build_proven=false`；本工具不证明第三方依赖重新从零构建、不证明可移植分发，也不把正常 source 构建与 prepared 依赖混称为完整 clean-checkout 验证。
