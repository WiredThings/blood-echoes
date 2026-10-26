# isolated 模块 helper 在解释器启动时禁止 pyc

2026-10-27；最小 CPU 侧修正，不改隔离、模块解析或数据路径，不改已运行的 QA10/正式包。

根任务的 QA10 实测：F3 功能通过，7 项 native SHA 未变，但 Python stdlib 新增 pyc 导致 strict codesign 失败。`prepare_guest_module_info.h` 原 argv 是 `python -I helper ...`；`-I` 包含忽略 Python 环境变量，因此 `PYTHONDONTWRITEBYTECODE=1` 不生效。helper 顶层已经 import pathlib 等，后来才设置 `sys.dont_write_bytecode` 也来不及保护这些导入。

现在 argv 为 **`python -I -B helper ...`**。`-I` 保留，`-B` 位于 script 参数之前，由解释器启动时处理；无需等待 helper 执行，也不依赖环境变量。此头只影响 CPU helper 启动，GPU/Metal 算法与 ABI 未变，GPU 无需为此重编。

## 定向控制

使用此前已存在 QA08 内的 bundled Python 3.11，仅导入全新 scratch 内的微小作者模块：

- `-I -S` + env `PYTHONDONTWRITEBYTECODE=1`：实测 ignore_environment=1、isolated=1，dont_write_bytecode=False，作者模块产生 pyc。
- `-I -S -B` + 相同 env：isolated 仍为 1，dont_write_bytecode=True，没有 pyc。
- header 源码控制确认真实 spawn argv 同时含 `-I`、`-B`，且二者均在 helper 路径之前。

基线另用解释器级 `-X pycache_prefix=<全新作者scratch>/authored-cache`，将包括潜在启动期 stdlib cache 在内的全部输出限定于该 scratch；这是保护测试环境的措施，不是产品修复。没有向原 bundle Python 目录写测试 pyc，也没有修改 QA10。3 项定向测试通过，另 14 项原 metadata bundle 选择合同通过，diff-check 通过。没有启动 game/GPU/native helper 或编译器。

## 活动调用审查

检查原启动器脚本、Swift、CPU src 与运行工具中的 Python isolated 调用：产品的遗漏点是本头。`complete_packaged_python.py` 的 3 处现有解释器 probe 已为 `-I -B`。没有发现另一处活动产品 Python `-E`。C/C++ 编译器的 `-I/-E` 不属于解释器选项；`test_fixedrunner_wakeup.py` 的 `-I -S` 是未随产品运行的开发作者控制，本轮不改它或历史实验工具。

本轮仅 `src/prepare_guest_module_info.h`、新增 `tests/test_module_helper_bytecode.py` 与本说明。QA10 新增 pyc 的证据、signed zip 原字节恢复及 QA11 真实 F3 后 codesign 验证由根任务处理；本轮作者测试不替代该实物验证。
