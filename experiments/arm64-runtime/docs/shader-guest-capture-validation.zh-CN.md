# 安全 shader 输入捕获基础：已构建和定向验证（2026-10-25）

仅隔离 ARM64 Metal 库。为现有 lookup-only DrawPreparation 的程序头读取/GS copy 参数加入 bounded POD leaf，防止裸读不可访问 shader 地址。原 GPU 线程参数路径保持原逻辑；尚未接后台编译 scheduler、完整首次读集与GPU采用。

## 已接入

- TryCopy 是独立 noinline POD leaf，保存前一个 fault recover；sigsetjmp(...,1) 恢复信号mask。C++异常在leaf退出后才抛，上层owner正常析构。
- BinaryInfo快路径长度/offset/地址溢出预检；fallback最多0x4000 DWORD，程序代码最多512 KiB。不可读/截断返回Unavailable，现有GPU正常路径仍负责有序执行。
- CaptureProgramBytes提供调用者持有的code/16UD副本，固定256-byte再检查缓冲；无额外shader堆分配。双读相等不是原子snapshot或allocation/content身份租约，尚不授权GPU采用。
- GcnCodeSlice可显式启用checked bounds，覆盖literal/64-bit扩展读取；普通调用默认原模式，readu64用memcpy避免未对齐类型解引用。

## 实际验证

230个GPU编译单元完整串行重建，arm64/签名核验通过。新库 SHA-256：ec51ec6c939d3407bef32fd183f940d422e45bff9c303573f13efde9763d38f1。

- 真实POD copy成功/PROT_NONE/跨页/溢出/前recover恢复；1000次连续fault后signal mask与C++ vector/owner析构检查通过。
- 合法/fallback/所有DWORD边界截断、长度/offset/search预算、owned code容量及源毒化控制通过。
- 真实 PipelineCacheCore::RefreshGraphicsStages 与GS BuildRuntimeInfo不可读参数负例通过：Unavailable且不进入compiler/backend。
- 真实GCN decoder的literal与64-bit截断拒绝通过。
- 已有owned GCN/SPIR-V/Metal、独立context、源毒化、fetch/V#/GS/tess字段拥有、预算/motion拒绝控制通过。
- 原phase off/on输出同一SPIR-V，且与保存的自造参考摘要623deccc一致；不是全游戏shader等价证明。
- 24项真实Metal字节保全/布局/alias/mip/padding控制符合预期，原c607逻辑边界候选无回退。

首轮capture测试被exit78拒绝：脚本错误使用evidence/private作GPU user目录。根代理改为既有Shared/state私有子目录并预创建，保护实现未改；第二轮通过。首轮失败证据保留。

## 剩余与边界

旧GetProgram prep的RefreshFlatBuf/StageSpecialization仍有大sigsetjmp guard与guest fetch/SRT读取，需要进一步拆成leaf和有界owned读集，不能把当前基础说成已完成。
没有接入任务发布/完整特化身份核验/后台编译/GPU采用；首次程序miss仍回原同步路径。
未启动本轮游戏，未测实际prep CPU成本、FPS、白模或长期内存。sigsetjmp保存mask与代码双读有成本，不能称零开销或提帧。
原本机QA ZIP、正式应用/存档/设置/缓存、Release均未更新。前一轮正式保护文件11671项SHA均与基线一致。

复跑：先用 tools/build_first_upload_test.py --suite guest-capture --library <新库>，再 tests/test_shader_guest_capture.py；owned、phase和24项保全脚本见validation.json。
