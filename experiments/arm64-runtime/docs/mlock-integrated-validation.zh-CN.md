# 有界 Mlock 运行时接入验证（2026-10-25）

仅隔离 ARM64/FEX。不是空成功：实际调用 Darwin mlock/munlock，显式转换 PS4/SCE errno。
单次锁定最多 16 MiB、总量最多 64 MiB、4096 个 16 KiB 页；环境只允许降低上限。
这是安全实现上限，不是已证明足够的全游戏需求。游戏默认 libc 锁页配置静态为关闭，动态请求分布仍待实际覆盖；不宣称 FPS/白模收益。

## 生命周期和失败边界

VMA 或 published-native State 的映射租约在先，账本锁在后，实际 syscall 最后。
重复/重叠客体锁定只给新页加一次宿主 wiring，解锁不是 Darwin 的叠加引用语义。
MS_INVALIDATE 与账本原子检查，wired 范围返回 EBUSY；未改 GPU fence、退休/dirty 顺序。
成功 MAP_FIXED/unmap 后才退旧代际账，失败前不丢账。不可逆的部分映射提交、GPU unmap 通知后失败、VMA 元数据发布失败采用原 HLE fault 74 停止，不用 75/76。
物理块释放失败不继续清零/释放未解除映射的块。空账本退账和 msync 检查有常数快路径。
固定账本约 64 KiB、回滚 scratch 8 KiB；没有扩大缓存或增加大型动态池。

## 已执行证据

- 3 组原生控制：有界账本/回滚/并发；实际 runtime_memory.c 的 mmap、mprotect、msync 及五类故障提交 fail-stop；真实 Darwin 单页锁定。
- 独立 Mach user_wired_count：0→首次1→重复1→重叠1→解锁0；MAP_FIXED 内核先清零后账本退账，同 VA 再锁1、最终清理0。只实际锁定一个 16 KiB 页。
- 10 个真实 FEX 正负例：flex-set、readonly、none-lock、none-unlock、image、stack、unpublished、overflow、budget、freed。均 exit 0，最终账本 wired 0。17 MiB 超限在 syscall 前拒绝。
- 全部 56 目标重新构建，121 基础测试通过；另 14 组专项全部 exit 0，包括原退出协议、AV、程序名、模块查询、msync 及 Mlock。
- 新增 munlock/sceKernelMunlock/mlock 的 NID 用本地 shadPS4 aerolib 及 StringToNid 算法双重核验。原游戏导入未更改。

曾有一次完整回归被旧 data 负例拦住：测试拿已实现的 __progname 当未知导入。改用明确自造的 bb.prototype.unknown.data 后重跑通过；未放宽运行时校验。首次 Mlock fixture 的纯名 munlock 导入也被拒绝；改用核实 NID，而非放宽 resolver。

源码中原九个未接接口已有五个具备有界实现和定向回归，仍有四个：模块清理 finalizer 与三项上下文契约。
该计数不表示整个启动器兼容审计结束，也不包含未覆盖的实际游戏路径。

## 未完成与保持范围

未启动本轮游戏；未验证实战请求尺寸/频率、长期压力或性能，不把合成测试当游戏验收。
正式应用/存档/设置/缓存与 Release 不改。普通手动 QA ZIP 仍使用旧 CPU67db，不含本轮锁页/owned-shader 候选。
只将源码与作者测试导出 dev 分支；不包含游戏代码、截图、凭据、二进制和缓存。

复跑：`python3 tests/test_mlock_fex.py` 与 `python3 tools/run_current_regressions.py`。
完整证据：evidence/mlock-contract-1792916322032111000、evidence/current-runtime-regression-1792916582170640000。
CPU SHA-256：854294e0ef42da5becdb99f24e68b90812cbc0b869753262670d576dea6e7bc5
