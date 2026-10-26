# 首启准备的 Python 哈希闭包

2026-10-26，实际原 GUI 首启在 25 分钟测试上限内完成静态目录，但未进入游戏；停止原因是时间预算，不是严重压力或异常 swap。详细结果见 `evidence/original-gui-first-preparation-20261026/summary.json`。不把生成目录当作 runtime 命中或完整验收。

排查发现包内 Python 3.11.15 缺少 `_hashlib`，`hashlib.sha256` 使用 `_sha256`。同版本 Homebrew Python 具有 OpenSSL 原生扩展。64 MiB 内存数据的微测中，原副本约 449–451 MiB/s，补齐后的全新副本约 2,215–2,903 MiB/s，四次摘要完全一致；进程峰值约从 14.0 MiB 到 16.2–16.3 MiB。这不是完整启动时间或 FPS 的收益证明。

`complete_packaged_python.py` 复用原 SQLite 的原生依赖封口算法，扩展明确允许清单至 `_sqlite3` 和 `_hashlib`，不允许任意模块。默认调用仍处理 SQLite。新 QA 组包额外显式处理 `_hashlib`：完整 Python 版本、架构、SOABI、扩展后缀须精确相同；只补全新副本缺失的模块，递归重定位非系统依赖，重新签名并核验加载边；不改 provider、旧 QA 包或正式数据。已存在的模块同样先核对全部依赖被限制在副本内，才运行功能探针。

实际新增两文件 `_hashlib` 与 `libcrypto.3.dylib`，约 4.70 MiB 磁盘占用。空字符串、`abc` 标准向量、增量输入及 context copy 均验证；SQLite 的持久读写探针也重新通过。9 项作者控制涵盖不匹配 ABI、x86、目录逃逸、未解析依赖、未知模块和保留既有副本。实际重定位及重复准入结果见 `evidence/packaged-hashlib-closure-20261026/summary.json`。

该修复不删减文件/目录校验，不改变 SHA-256、不跳过失效检查，也不改变游戏画质或缓存容量。`arm64-ui-audit-20261026-06` 已纳入闭包，原 GUI 默认首启仍待完成；目前不能宣称整轮准备加速比例或持续内存变化已测定。
