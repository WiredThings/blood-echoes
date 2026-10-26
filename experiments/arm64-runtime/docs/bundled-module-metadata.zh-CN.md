# QA 模块元数据 helper 的有限自包含补齐

2026-10-27。仅开发记录；未替换正式应用、未发布 Release，尚未构建或运行本轮 native 修改。

## 原缺口与范围

`prepare_guest_module_info.h` 原来始终运行 `P/tools/prepare_module_metadata.py`，后者默认从 `P/vendor/blood_echoes/loader` 导入原 parser；QA bundle 已有的原 loader 两源码没有被这个入口使用。这里的 P 为 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。

本轮仅修 helper 选择和打包闭包。正式产品作用域、user/session/GPU 私有路径门均不修改，所有游戏/boot/output 数据仍限定原 Shared 树；不声称 QA 已可在正式用户目录运行。

## 新合同

- native 从 `_NSGetExecutablePath` 取得实际可执行文件，不使用环境变量指定 helper/CodeRoot。路径必须 canonical、普通文件；应用内仅承认 `stage/bin/bb-probe-metal` 和 `stage/bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal` 两个原角色，stage 必须是 `P/apps/<name>.app/Contents/Resources/bbport`。
- native 在该根固定选择 `native-runtime-tools/prepare_module_metadata.py`，传 `--code-root` 和 `--runtime-executable`。缺文件、符号链接、未知布局、P/apps 外应用立即失败；不得退回 P/tools。
- helper 在任何游戏解析前核对：自身固定位置、canonical/owned/单链接普通文件、有界清单、`ARM64-LOCAL-PREPARATION` 的 private project/host-local 约束及 helper SHA；两份原 parser 的准确 SUPPORT 集合与各 SHA；`NATIVE-METAL` 的 private FEX 角色，以及实际 exec 在 `frozen_runtime` 的准确角色 SHA。顶层与嵌套角色必须分别声明，不能借另一个角色的 hash。
- bundle 模式不接收 `--loader-scripts` 覆盖；缺任一角色参数不会切回 developer 模式。原 parser 仍由现有 `prepare.parse_self/unpack/span` 提供，额外核对实际导入文件属于所选 loader；未重写 SELF/PT_LOAD/TLS/EH/boot identity 算法与原子输出。
- 仅真正没有 `.app` 路径组件、且实际 exe 位于 P 开发树的 native 入口保留开发 fallback。脚本的该模式也必须是固定 `P/tools/prepare_module_metadata.py`，默认 loader 保持 P/vendor；原有显式开发 loader 仍受 Shared 数据/代码门。
- `prepare_local_aot_stage.TOOLS` 新增 helper 一次，原 `SUPPORT` 的 prepare.py/game_check.py 仍是唯一原 loader 副本。原 bootstrap 的全闭包 SHA 检查自动覆盖新 helper。

这些 owner/SHA 检查沿用当前私有 QA 的同用户信任边界，不是抵御恶意本机账户重写所有清单的独立认证系统。

## 定向结果与剩余验收

只运行 CPU Python 作者控制，没有启动 native、GPU、游戏或编译器：

- `test_module_metadata_bundle.py`：14 项，覆盖两角色、各自 SHA、错角色、出 P/apps、脚本/root/loader override、缺参数、helper/parser/runtime 改动、链接/硬链接/owner、清单作用域、bundle 失败不开发回退、原开发选择，以及数据仍 Shared。header 仅做源码接线控制，不冒充 native 编译验证。
- `test_prepare_local_aot_stage.py`：15 项，含新 helper 精确冻结一次、原两 SUPPORT 不重复、篡改 helper bootstrap 拒绝；原脚本保留/relocation/闭包负例也通过。
- `test_module_metadata.py`：6 项，原有界 header/TLS/EH/segment/原子拒绝回归通过。作者临时目录不再依赖不存在的 D/state，测试内显式替换作者策略根，运行时策略未放宽。

首次新测试因 `/var` 临时路径不是 `/private/var` canonical 形式被正确拒绝；修正作者 fixture 为 canonical 路径后通过，未放宽路径检查。后续由统一 native 构建检查 header/API，再在新 QA 包验证实际 helper 来源；现有冻结 QA08 未改动，不能认为它已经使用新 helper。

本轮允许文件：`src/prepare_guest_module_info.h`、`tools/prepare_module_metadata.py`、`tools/prepare_local_aot_stage.py`、上述三份 tests 与本开发文档。未改 packager/runner/Swift/其它 runtime C 文件或正式数据。
