# 自动准备组件的 coherent 构建

`tools/build_game_preparation_component.py` 将 GPU DSO、CPU 模块消费者和最终 PSO worker 的实验构建收口到一个入口。它复用已准备好的 Ninja／第三方依赖阶段，**当前不是 clean checkout 构建证明**；缺失的原始 macOS build-deps、测试目录仍须独立补齐，不能以本工具的本机成功替代源码自包含验收。

先只生成可审查计划：

```sh
python3 tools/build_game_preparation_component.py --plan \
  --project /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype \
  --output /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/coherent-preparation-唯一名称
```

源代码仍在修改时不要编译。确认 capture producer 等源码冻结后，用新目录重新生成计划，再执行：

```sh
python3 tools/build_game_preparation_component.py --build-plan /上述新目录/plan.json
```

计划固定 `gpu-build-inputs.json` 的 SHA，默认使用已审查的 230 单元 prepared baseline；重新映射到当前源码，加入 capture 的 5 个 cpp，共 **235 个唯一 GPU 编译单元**。源码和头文件先形成完整 SHA 闭包，真正编译前逐文件复制到新的 owned snapshot，任何源漂移都会拒绝。现有 Finder 式 ` 2` 副本只在有原名对应时过滤，不删除，也不误过滤没有原名的合法文件。prepared header／link 输入另外绑定实际字节摘要；编译完成后记录实际 depfile 头文件摘要。

GPU 单元统一使用该 snapshot 与 `BB_DEV_FETCH_CAPTURE=1`，`ninja -j1`。CPU 模块消费者和 PSO worker 只编译各自 main 与生产 `game_pso_host_services.cpp`，链接同一新 GPU DSO，**不再覆盖 recompiler／translator／SPIR-V 等局部对象**。未支持的 guest 接口仍显式失败，没有空成功的测试桩。

构建完成后核验 ARM64、严格签名、capture 的真实导出 `_bb_fetch_companion_configure_v1`，以及两个执行文件的实际相同 GPU load edge。`result.json` 包含 source identity／闭包、235 单元数、worker/module-consumer/library 路径与 SHA、实际依赖、coherent 标志和 `clean_checkout_build_proven:false`。组件 freeze/load 与打包器还会重新校验这些产物；此 manifest 本身不能授权 runtime 缓存导入。

本轮已完成真实 235 TU coherent build，耗时 320.3 秒，压力 2→2，swap 7902→7886 MiB；复用 prepared dependencies，仍不是 clean checkout 证明。typed companion 原生控制暴露主函数移动 JSON 错误对象后掩盖异常的问题；已以独立 native-only repair 重编 main，保留同 DSO／host 与已核 ABI/header 源，原完成结果和只读 snapshot 不变。这不是局部 translator/emitter 覆盖，也未重编 235 单元。10 项 typed 原生控制通过，包括实际 tiny authored GCN 重译及已修复的清楚拒绝路径；未进行游戏／GPU／性能测试。

原构建结果为 Shared 开发根 `evidence/coherent-game-preparation-20261026-02/result.json`；修复身份为 `evidence/coherent-game-module-repair-20261026-01/repair.json`。未来同类 main-only 修复可使用 `--repair-native-main <base-result> --output <新目录>`；它核验全部原 GPU 源和已编译头文件 SHA，记录新 main／consumer SHA、实际 load edge，拒绝原 DSO／header 漂移。由组件打包器基于该 repair 身份生成新 manifest，不能覆盖原结果假装旧组件未变。
