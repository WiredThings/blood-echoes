# 有界准备读集与稳定镜像身份：整库回归（2026-10-25）

已在隔离GPU库实际接入既有 GetProgram lookup-only 准备路径的有界读取，移除跨C++ owner的大sigsetjmp，以及单独的V#读取guard。故障恢复只留在POD leaf；解析/资源对象经正常异常析构。普通同步GPU路径保留原SRT walker/解码模式，不启用本组推测解释器。

## 具体读集和边界

- 真实GetProgram每次入口最多2MiB被计费字节、16384次读，flat<=1MiB、walker<=256KiB、fetch<=32KiB、属性<=32。flat/初始化元数据及walker副本也计费；模板flat复制前先限额。
- portable SRT interpreter支持动态offset和nested pointer，检查指针/值栈和实际flat初始化；无终止/未知op/超限/不可读失败回原GPU路径，不注册新的推测walker，不以零填代表成功。
- fetch逐指令有界叶拷贝后checked decode，覆盖literal/SDWA/DPP/64-bit扩展与opcode表越界；V#读取也经leaf。
- 每个vertex attribute另有独立的小额读预算，通常一个16B sharp；不能把上述2MiB称为整个draw的统一预算。
- worker私有Info的scoped reader异常后复原；不发布新program/module，不改正常缓存容量。
- stable mirror snapshot/try-read lease现在完整包含在同一整库中，仍只护注册表，不能冒充payload或native对象生命周期租约。

## 实际执行证据

230个GPU单元完整串行重建，签名和arm64身份验证。库SHA-256：a36c7bd3cdfad5924a50a6b165ec9940b16af032855b90c7c505f4f8a52b91ad。
真实prep lookup、nested dynamic SRT、未初始化flat/栈/预算拒绝、bounded fetch/V#、C++ owner正常释放定向控制全部通过。
既有guest-capture（含1000次fault恢复）、owned GCN/SPIR-V/Metal、phase开关同输出及24真实GPU字节保全均通过；自造正常SPIR-V仍为623deccc参考。

镜像控制扩为46项，另补“同当前无效owner/handle完全匹配的伪造值”也必须拒绝。独立真实Layer源码和动态链接本次实际GPU库均通过同一控制；后者验证新方法符号与整库ABI一致，无Metal设备或游戏启动。

成功读取CPU微测5组：leaf中位数约185.754ns，实际64次bounded Copy阶段中位数约11.907us。是作者host数组的成功拷贝CPU成本，不是游戏帧率，也不证明整体CPU/内存改善。逐DWORD保护可能放大成本，后续应合并无依赖连续读取，保持同样拒绝语义。

fixture首轮有Clang拒绝：const vector的SRT bytecode句柄不能直接转函数指针；仅测试中补显式const_cast用于opaque portable walker句柄（不执行或写入code），生产库未因此更改。真实库Layer wrapper首轮头路径不对，改用项目相对路径后重编及重跑；两次失败记录均保留。

## 未完成

未启用first-program miss/new specialization后台发现、单worker pending队列或GPU采用；纯shader module输出不写旧payload，所需身份复验与raw image写回租约需分开审，不应因后者缺口永久拒绝前者。
首次TranslateProgram推测编译还须检查断言/UNREACHABLE是否能伤及整个进程，以及noexcept链的异常安全。尚不能称长编译或白模已修复。
PreparedExactExport R2、模块finalizer及上下文探针仍为独立未编译草稿，未混入本库。
本轮未开游戏；不动正式app/存档/设置/缓存/Release。普通QA包仍旧CPU67db/renderer66d554bd，不含本组候选。

复跑入口和证据目录见validation.json；使用独立Shared/state目录。
