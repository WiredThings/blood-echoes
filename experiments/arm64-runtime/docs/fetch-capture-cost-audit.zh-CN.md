# 自动捕获的成本边界（源码审计）

本轮仅审查源码及定向 CPU 契约，不启动游戏、不做整机帧率/内存测量。以下字节数是代码预算或 ARM64 ABI 布局推导，不能当作实测 RSS。

## 正常暖绘制

原 `GetProgram` 的 `RefreshFlatBuf`、StageSpecialization 构造、资源读取和原缓存查找继续存在，不能把它们原有的全部成本算给 collector。

新增 `ObserveWarm` 以原 program 的 last_used、attempted 和一个 retry bool 判定：已尝试的同一模块即使还有一条从不再使用的旧 permutation，也不构造 CompileScope。因此不复制 Snapshot/Runtime/Profile/fixed观察数组，不 hash userdata/code/StageSpec，不建大 vector，不做磁盘 I/O、不进入配置 mutex；正常已有的 parser/GetSharp hook 只看空 TLS observer。

发现一次真实的新 target，但本次没有 scope 时，延后一用并置 retry；下一次原读取观察到未处理 target 才封口。retry 不会被中间一次已处理 A 清掉，避免 A/B 交替永远错过 B。未处理 target 暂时不再出现时，retry 观察窗口可能持续：仍有固定数组观察、64B UD hash、74B Profile编码和Runtime复制，但不为已处理 target复制整套输入/全 hash shader code。该剩余窗口需要最终运行计数才能量化，不能据此宣称绝对零开销。

只有采集状态真正改变时才 any_of 扫描原 modules，不因未访问旧 variant 每 draw 做线性扫描。单独的 seen hash table 不存在。配置完成后 `Producer::Get()` 只读 acquire 原子指针；配置 mutex、receipt/DSO/SFO 摘要仅初始化时执行。未配置时 CompileModule 也不额外计算 SPV proof SHA。

## 真正处理新输入

成功取得两个 lease 中的一个后才复制 owned code/UD/flat/walker、构造小 fetch/read vectors；StageSpec canonical、原始 SPV复制和完整输入摘要检查也只在真实待采集 target 上执行，不是每 draw 的缓存验证。暖输入的 SPV读取/哈希/复合落盘在 utility worker执行。code身份的全hash延迟到实际采集，不在每次 warm scope begin执行。

Busy/队列满/缓存暂未打开保持待处理，不能当永久拒绝。没有可信原SPV来源或不可接受的SPV大小有明确 `NoReference/ReferenceBudget` 拒绝计数，不计 written success；后台读取失败另计 failed。`capture_attempted` 只表示本会话已处理一次尝试，绝不等同于磁盘保存成功。下次启动仍会检查重新捕获；跨启动 prepared accepted目录尚需由统一consumer闭环给出实际命中证据。

每条已接收输入的完整 canonical/Validate 会做多轮有界hash；这部分CPU工作并非免费，且磁盘256MiB/8192文件预算扫描在后台每次写入执行。应在最终批次分别观察已seen场景、首个待处理target和后台落盘，不能用作者夹具推算FPS收益。

## 额外内存

- receipt allowlist最多32768个 ProgramProof。ARM64布局推导每个56B，总约1.75MiB；实际数量由精确静态条目决定。
- Snapshot、SPV、metadata和IO reserve单项≤2MiB；building/queued/writing合计最多两个lease，所以≤4MiB是**在途逻辑预算**，不包含utility线程栈、allowlist和下述原缓存结构增量。
- BinaryProof为32B摘要、size_t和bool，ARM64 ABI推导48B。它内嵌于原 Module；原 small_vector inline容量8并非最大permutation数，因此每个Program约增加8×48+8=392B；外溢后还增加约48B×外部module capacity。总增量近似 `392×program_count + 48×external_module_capacity`，没有新建独立无界seen集合，但这项依原缓存规模线性增长，不能宣称collector全部常驻严格≤6MiB。8853个Program仅内嵌部分约3.31MiB，是目录规模假设，不是实测常驻。
- 原 raw store/GPU缓存容量没有缩小或驱逐；新companion store256MiB是磁盘预算，不是常驻内存。

CPU73断言和宏OFF/ON四个实际TU编译控制仅验证上述分支/契约。最终游戏中该观察窗口出现频次、后台存储负担和内存压力仍由统一性能批次检查。
