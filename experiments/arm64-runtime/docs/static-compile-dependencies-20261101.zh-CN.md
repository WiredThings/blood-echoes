# 静态输入：按编译阶段区分缺口

## 本轮确认

2026-11-01 只读处理当前 QA 已登记的 `static-program-index.jsonl`，没有重新读取游戏包、启动游戏或修改已有缓存。共 8,915 个代码／硬件阶段身份及 8,915 个头变体：

|硬件阶段|数量|
|---|---:|
|Fragment|6,573|
|Vertex|1,865|
|Export|177|
|Local|131|
|Compute|68|
|Geometry|62|
|Hull|39|

原 `launcher_game_catalog.build_static()` 给每项统一写 `dynamic-compile-input-required` 和三个缺项。这只是实现没有构造完整输入，不能证明每项都必须运行后才能确定。尤其 Compute 不需要图形阶段配对、颜色附件、混合和深度状态。

另有 31 个 Fragment 和 2 个 Vertex 的头没有 usage 或 SRT 声明。这仍不证明指令没有读取资源／用户数据，不能据此填空特化。当前没有生成新的完整静态编译输入。

## 已改动

复用 `static_program_links` 的已校验记录，增加 `compile_dependencies()`，接到原静态准备输出：

- 模块编译缺项与最终 PSO 缺项分开；Compute 不再被列入图形依赖。
- Fragment 的输入、颜色导出和采样状态同时可能影响模块编译，不能全部推迟到 PSO 阶段。
- Vertex／Export／Local 保留有效逻辑阶段、fetch 或无 fetch 的证明要求；Geometry 保留已有 owned GS copy 及实际几何状态要求；Hull 单列细分状态。
- 每个头变体分别引用原静态索引，不逐字段拼成不存在的组合。文件默认寄存器仍只是默认值，不冒充实际 draw／dispatch 状态。
- gap 改为 `unresolved-compile-inputs`，明确 `dynamic_requirement_proven=false`、`static_compilation_ready=false`。这是依赖报告，不能送去当作编译输入或运行时准入证明。

只在正常静态登记／更新时生成报告；没有增加逐帧工作、改变编译算法、放宽完整键、移除校验或修改缓存预算。旧登记和旧产物不做迁移或覆写，尚未更新 QA 应用。

## 原生入口的实际边界

`src/game_module_preparer.mm` 的现有 owned module／companion 路径采用已捕获的完整特化、运行前后状态和参考 SPIR-V，重新编译后严格对照。这条已验证路径继续保留。

底层 `Shader::CompileOwnedToSpirv`／`TranslateProgramWithoutProgramBase` 已能复用，并不在算法上要求游戏先运行。缺的是**将静态证据构造成完整 owned 编译输入的入口**，不能靠删除现有参考 SPIR-V 对照来补。

最短后续路径：

1. 从上述小集合先做实际指令读取／字段依赖证明，复用原生解码与编译实现；不能把空声明当无依赖，也不能写一套 Python 指令翻译器。
2. 对所有编译相关字段都有依据的类别，添加静态 owned 输入构造，交给现有编译、持久化和运行时匹配流程；未知字段明确阻止这一类别构造。
3. 启动器批量处理满足同一条件的条目；确实依赖运行状态的部分继续由正常路径自动捕获，下一次启动提前准备。

此处不声称已有完整静态 PSO、全游戏首启覆盖或性能提升。不能把修正 gap 分类作为完成自动编译集成的替代。

## 验证范围

- `test_static_program_links.py`：24 项通过，覆盖七种硬件阶段、空声明反例、SRT、变体不混合、未知阶段和坏摘要。
- `test_launcher_game_catalog.py`：16 项通过，含原静态入口生成阶段依赖并完整回读 READY 的集成检查。
- 对现有 8,915 条登记执行新分类，全部完成；单次约 0.11 秒。这只是元数据报告耗时，不是游戏性能或本机编译耗时。
- 没有运行性能测试、Cua、截图或录屏；正式应用、存档、设置、已有缓存及 Release 未修改。

独立的草动画周期跳变仍未归因，不能与已改善的旧亚楠出门首次编译卡顿混算。
