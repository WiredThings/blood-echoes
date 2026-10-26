# ARM64 正式组包的依赖资产层

`tools/native_package_assets.py` 是构建时共用层，不参与游戏运行，不从 QA、正式应用或用户数据目录取默认资产。组包方显式提供原生构建产物和 Framework Python 3.11。

接口：

```python
selection = python_provider(provider)
relocator = Relocator(engine, search_dirs=(selection['framework_root'], *dependency_roots),
                      bindings={selection['library']: engine / 'python/lib/Python'},
                      minimum_os=(27, 0), library_subdir='deps-metal-arm64/lib')
relocator.copy(source, destination)
report = embed_python(engine, provider, relocate=relocator)
relocator.sign_copied()
```

`machos` 是已记录的目标 Path 列表；`inputs` 保留目标相对路径、原始 canonical 来源、重定位前 SHA 和目前目标 SHA。`closure(destination)` 返回该已记录目标的递归依赖目标。`sign_copied(paths=None)` 只签本实例已记录、当前字节仍与记录相符的 owned 普通文件，并更新共享 SHA；不会扫描目录签名。

同一 engine 的两个 Relocator 可在同一 Python 进程复用公共依赖：要求 canonical 原始来源、原始 SHA、完整依赖目标计划及当前已复制字节 SHA 全部相同。允许显式将同一个 GPU 原始 DSO 映射到两个不同目标；不同来源碰撞同一目标则拒绝，公共依赖的绑定计划有歧义也拒绝。未知已有目标仅在与原始输入字节完全相同时接管；未知已重定位目标不能依靠文件名或签名复用。共享 provenance 不写磁盘，不是跨次构建缓存。

依赖解析保留 `@rpath` 的完整后缀，并考虑 loader、加载祖先及实际 executable 的 LC_RPATH 和显式 search roots。绝对引用只能使用原精确文件，不按 basename 猜版本；多个不同 canonical 候选拒绝。合法 `../` 必须仍在已声明的来源根或精确 binding 来源内。目标始终限制 engine 内，拒绝链接、硬链接和不同来源碰撞。只接受 thin ARM64 和不高于明确目标版本的最低系统要求。重定位复用原 `package_optimized.py` 的 copy/loads/commands/minimum/sha，只修改新副本的 load command、RPATH 与 dylib ID。

Python provider 仅接受 3.11 Framework root、其 `bin/python3.11` 或 Resources 内真实 Python executable 三种声明布局；返回的 `framework_root` 可供组包方建立显式来源根与 library binding。stdlib 复用原白名单，排除第三方包、测试、字节码及 GUI 工具；非系统依赖的扩展交给原策略省略。随后原 `complete_packaged_python.complete` 分别补齐并验证 sqlite3/hashlib，保留其解释器级 `-I -B`。为执行这两项检查，只对已跟踪的 Python 闭包新副本临时签名；其余最终签名和冻结角色 SHA 仍由外层组包方完成。

本轮验证：20 项纯 Python 作者测试，所有 Mach-O 工具、签名和 completion 执行均 mock，包含递归闭包、继承 RPATH、歧义、精确绝对引用、跨实例已签字节复用、碰撞、外改、薄架构/系统版本、输出链接/逃逸、Python 布局、白名单和只签 Python 闭包。没有读取 QA/正式应用资产，没有启动 native/GPU/游戏。真实依赖复制与实际 Python 能力检查由统一组包阶段执行；这组作者测试不代替该验收。
