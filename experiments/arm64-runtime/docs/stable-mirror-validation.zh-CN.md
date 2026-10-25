# 稳定 single-mirror 注册表身份（2026-10-25）

新增按值 StableMirrorSnapshot、AddMirror/RemoveMirror 的 source_version 和非阻塞 WithStableMirror。
锁内同时校验完整范围、all-valid、owner/handle/device_address/base/size/kind、source和valid两项代际；callback仅在try shared lock成功且所有条件匹配后执行。callback的Span/source只活到返回，不允许重新入表、protection RPC、GPU等待或冷编译。

这只保护镜像注册表。**不保护表外native对象或游戏分配器payload，不是BufferCache生产准入许可。** 原Resolve与现有调用不改；没有接入新raw写回或扩大失效范围，没有新GPU分配/缓存。

44项断言控制通过：合法完整镜像、跨界/零/溢出、局部valid、invalid→valid、同地址同owner/handle删除重建ABA、更换owner、10类快照身份变异、双镜像范围拒绝、null owner/handle、callback false/异常析构、真实并发RemoveMirror读租约、500次替换/valid变化与并发读。
真实Layer源码以clang++ arm64 -O2 -Werror编译执行，不是单独的算法模型；未启动GPU或游戏，无guest payload分配。具体控制日志只代表这组作者输入。

首次测试发现提案漏校验 expected.source.kind：只检查当前表中kind不能拒绝已篡改的期望快照。根代理补齐该负例，第二次44项通过；失败证据 stable-mirror-controls-1792917677532638000 保留。
通过证据 stable-mirror-controls-1792917709586974000。复跑：python3 tests/test_stable_mirror_lease.py。

新字段改变GpuMemory类布局；**尚未重建GPU完整依赖闭包，当前ec51ec6c库与普通QA包均不含此API**。
后续整合PreparedExactExport时必须完整GPU重建并重新跑owned/phase/24保全回归，不可把本组纯Layer测试说成整库ABI或白模验证。
不修改正式应用、存档、设置、缓存和Release。只将作者源代码/控制/说明同步开发分支。
