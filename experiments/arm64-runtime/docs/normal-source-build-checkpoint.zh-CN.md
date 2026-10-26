# 正常源码构建检查点

2026-10-26。完整摘要见 `evidence/normal-source-build-20261026/summary.json`。这次验证正常 CMake 构建图，未运行游戏，没有替换正式应用或清缓存。

从已推送提交 `8ec54a3032ceb107bf813a128d6b8bda076f72ac` 导出 10,971 个 Git 条目到全新源码目录。原工作目录有未跟踪的 Finder `buffer_cache 2.cpp` 等副本，递归 GLOB 将其误编入旧尝试；文件全部保留，新的 Git 归档不包含它们。不能用这次成功掩盖旧尝试的失败。

已完成：

- 正常 renderer 的 235 个 TU、`game-pso-preparer`、`game-module-preparer` 构建成功，约 311.83 秒；全程单并发，压力 1→1，交换占用下降 24 MiB。没有复用旧 renderer 对象。
- 同一源码重新生成 348 个 native 签名、75 个 GPU adapters，正常 FEX/CPU 与 cache generator 构建成功，约 69.81 秒；压力 1→1，交换占用不变。没有使用旧 FEX／CPU archive 或旧 generated 输入。
- 同一正常 `metadata-layout` target 实际运行并封口；215 原生 ABI 值、49 个 Profile 字段、12 份源摘要、实际二进制摘要、七种 Writer fixture 完整，原 decoder 与严格 layout loader 均通过。加载边只有系统 libc++／libSystem。
- 两个 helper 都通过 ARM64、签名、私有根、唯一加载边及字节摘要检查；实际 dyld 日志确认加载同一新 DSO，固定 helper schema 和 typed companion capability 均通过。

正常 CMake 使用 `@rpath/libbbgpu.dylib`。身份校验现在仅解析主 executable 自己声明的 LC_RPATH，不查环境、cwd 或隐式目录；有多个实际候选、未知嵌套 RPATH 或目录逃逸就拒绝。解析后仍执行原私有根、owner、薄 ARM64、签名及 SHA 检查。8 项反例和上述真实加载验证通过，未扩大运行时目录授权。

`tools/build_normal_preparation.py` 用当前 CMake 图及配置中的实际 CMake executable 构建三个正常 targets，单并发并保留原内存／锁屏保护。它记录当前源码和工具摘要，不重放历史编译表；6 项短准入控制通过。

仍未完成的边界：

- 此次依赖来自明确的既有前缀，尚未从空依赖前缀重建，不能称完整 clean-dependency 构建。
- 后加入的 PSO 有界批次 main 是单独候选，不能算进本次整树构建；其实际 native 对照另见 `game-pso-bounded-batch.zh-CN.md`。
- 新 CPU／renderer／helpers／layout 已组成第 07 个本机 QA 包，严格组件、加载边、签名及原 GUI 代码身份检查通过；原启动器实际准备／运行正在验证，功能、场景、性能和内存结论尚未成立。正式应用和 Release 未更新。
- 运行时 Shared 隔离合同仍保留，尚不是可移植安装包。

布局组包同时去除开发机 Writer fixture 的绝对路径，保留完整 ABI、源摘要和 fixture 验证事实；重新绑定组包后的 layout SHA，并对复制的头文件再过原严格 loader。开发原始封口和源码目录不修改。

正常产物隔离组包：`artifacts/arm64-ui-audit-20261026-07/`，archive SHA `4bd168b4a406b4414f7668a2fe8430490a660f97ace7473e2118efdada525878`（47,497,427 字节）。source CPU／GPU／helpers 来源绑定新正常构建；PSO main 的独立 batch delta 单列，未声称整个归档都含该改动。依赖、基础 QA shell 与 Shared 根仍是已声明的既有环境。组包不含游戏、存档、缓存、汉化或账号资产。
