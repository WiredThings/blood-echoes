# 已捕获 fetch RegisterRead 的独立磁盘契约

这是隔离开发路径的可调用源码，尚未接入共享 queue、游戏热路径、启动器或正式缓存。它补的是现有 SRT 磁盘数据没有保存的 fetch V#，不从 VkFormat 推算 Buffer，不重新读取 guest 内存。

## 接口与来源

- `src/fetch_register_snapshot.h::FromSnapshot()` 接受原生、已经完整拥有输入的 `Shader::CompileSnapshot`，逐字段复制 fetch 和实际 `register_reads`。每个 `AmdGpu::Buffer` 的原始 16 字节完整保留；它们的 guest 地址位仍是描述符标量，不是允许解引用的 host 指针。
- `WriteCapturedFetch()` 是可交给已有后台工作项的 writer 包装。它不调用 `Capture()`、`ReadUdReg()`、`MakeInfo()`，也不执行旧 Info/SRT 反序列化或 walker。
- `Store::Read(trusted_expected)` 必须提供调用者独立持有的完整身份，不列举目录猜选条目。它验证版本、长度、文件内容 SHA 和所有期望字段。
- `RestoreSnapshotReads()` 再对照已有 owned companion snapshot，全部通过后才赋值；失败保留原有 reads。reader 从不直接把磁盘 C++ 对象映射进运行时。

身份包含：游戏内容、当前完整编译器 build、持久化/编译 ABI、完整 canonical 编译输入集四个非零摘要；native 硬件/逻辑阶段、程序 hash、code 字节数和 SHA、owned 词数组/程序基址标量的上下文摘要、原始 descriptor 内容摘要、全部 fetch 字段、绑定起点，以及完整逐字段 Profile。Profile 覆盖六个 u32、`buffer_copy_shader_hash` 和全部 42 个 bool，包含 `relaxed_fragment_compute_float`、`two_level_bda`。不保存 Profile 的 C++ padding。

`Environment.compile_inputs` 需要由输入集 owner 根据完整 canonical runtime、特化、资源描述符和配方生成；不能只填 program hash、上游 git revision 或临时路径的摘要。该字段不由 store 猜测。实际 RegisterRead 的四字内容还会**自动**参与独立摘要与文件键；不能改 Buffer 内容却沿用旧键。

同一 `(pointer_index,dword_offset)` 若重复且原始 16 字节一致，writer 将它们规范化为一次读；冲突重复拒绝。每个 fetch 属性必须有完整真实读，缺读和无关额外读均拒绝。磁盘中的 reads 必须唯一且按目标排序。fetch 最多 32 个属性，原 snapshot 仍受现有单项和总计 2 MiB 限制；不为收集复制一份大型快照。

## 目录、原子写入与预算

必须显式提供 canonical、本人所有的 `0700` 开发根，及本人所有、单链接 `0600` 文件 `fetch-register-development.marker`，内容严格为：

```text
Blood Echoes isolated fetch input storage v1
```

marker 由隔离测试配置创建；store 不自动生成它，也不从环境变量寻找正式 GPU 缓存。唯一写入子目录为 `fetch-register-inputs-v1`，使用 pinned directory FD、`openat`/`O_NOFOLLOW`、所有者/权限/regular-file/单链接检查。规范路径中存在 symlink、非私有目录/marker、symlink 或 hardlink 目标都会被拒绝。

格式 `BEFETCH1` v1 使用固定宽度 little-endian 字段：48 字节文件头（magic、版本、payload 长度、SHA256），随后为显式字段、九字节属性和每次读的两个 u32 目标与四个原始 u32。不写 C++ vector/span/pointer 对象、旧 Info、可执行 walker 或宿主地址。

单条硬上限 16 KiB，整个开发 store 硬上限 64 MiB、4,096 个条目，可配置更小的开发预算。预算包含写入期间旧文件与 pending 文件同时存在的瞬时空间。达到预算只拒绝新写入，**不驱逐有效条目、不缩 GPU 缓存、不清任何既有缓存**。每次有界扫描统计目录，无常驻条目 map；锁为非阻塞，忙时拒绝，不等待游戏线程。

复用现有 `BbCpuCache::Hash`。发布沿用已有 Metal artifact 的“独占临时文件 → 完整写入 → fsync/close → 原子 rename”模式，并增加原始字节读回及严格 FD 检查。任何 commit 前失败/取消均清理本次 pending，保留旧文件；不会先截断目标。取消在创建前和 fsync/读回后检查。该协议保证完整文件的原子可见性，不宣称断电后的目录项持久化保证。

## 建议接点

1. 输入 owner 已在有序线程完整捕获快照后，将**同一个** `shared_ptr<const CompileSnapshot>` 交给既有有界工作项。不新增一次 live guest 读取，不在普通 draw/cache-hit 上调用 writer。
2. 在后台/加载期已通过编译输入身份校验的工作项中调用 `WriteCapturedFetch()`。写成功后才发布相应 manifest 的 trusted Expected/内容引用；预算不足或取消只记明确缺口，不影响原同步编译。
3. 本机准备器先按 manifest 的独立期望身份读取 store，再为同一 owned code/UD/flat/runtime companion 调用 `RestoreSnapshotReads()`，继续使用现有 owned GCN 编译与 regenerated SRT 一致性校验。此契约本身不授权最终 PSO 或正式运行时导入。

当前源码核对中，`PrepareOwnedShader()` 和 `CompileSnapshot::Capture()` 已有实现，但本分支 `video_core/shim/src` 未见生产调用 `Capture()`；实际调用目前见于作者测试。因此本轮交付的是可直接接入的 writer/reader，不冒称现在游戏已在自动保存 RegisterRead。共享 queue/runtime 的具体 owner 接线由根任务统一进行。

## 验证

```sh
python3 -m unittest discover -s experiments/arm64-runtime/tests -p 'test_fetch_register_store.py' -v
```

两项 Python 测试包含当前 Profile 字段完整性审计，以及使用**真实原生 CompileSnapshot/Buffer 类型**的 43 项 CPU 断言。覆盖原始 16 字节往返、全部九个 fetch 字段、重复/缺失/冲突读、snapshot 尺寸、身份和 Profile 变化、摘要篡改、额外尾部、读回恢复、开发目录准入、symlink/hardlink、字节/条目预算、fsync 后取消、pending 名称碰撞时不删除他人文件及旧文件保留。测试不链接 renderer/GPU 库，不启动游戏，不读取正式数据。
