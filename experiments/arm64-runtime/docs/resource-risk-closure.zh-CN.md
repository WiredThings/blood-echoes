# 已发现资源风险的本轮收束

2026-10-25。依用户最新要求，只修复已有证据且可安全局部处理的风险，不扩展白模根因调查或新生产回写系统。本轮不声称已经修复用户实际遇到的白模。

## 已整合：确切的后端死亡确认

原 `remote_front.cpp::WatchThread` 在 `waitpid` 任意非 EINTR 错误后仍发布 `child_exited=true`；错误不能证明后端已停止访问共享资源。现增加 `bb_peer_exit.h`：只接受确切 owned PID 的终止状态；EINTR 重试，其它失败沿用 `BB_ARM_EXIT_HLE_FAULT=74`，不引入 launcher restart 状态。

真实 fork 正常退出、SIGTERM、已收割后的 ECHILD、无状态、错误、非终止状态及 EINTR 重试控制通过；整合后的作者测试重复 5 次全部通过。实际 `remote_front.cpp` 编译单元已编译通过。源码已同步到隔离原型和开发仓库，尚未重新链接、安装到普通 QA 或正式应用。

## 已验证但本轮不接入生产：准备式纹理导出

既有 R2 提案在私有覆盖树完整重编译 230 个 GPU 单元，ARM64 库身份为 `d8ceabc7a71dc57d95a20e0edf21decfa12ec72f179347b4a7fe20a23396a8ff`。使用 Apple libc++ 可用的共享指针原子接口修正草稿编译错误；作者测试改为调用实际 Scheduler::Flush，并在拥有 TextureCache 的作用域内收取已完成的延迟删除。

启用 Metal API Validation，44 个真实 GPU 内容/失效控制及 9 个对象生命周期/尝试次数控制全部退出 0；覆盖 padding、小 mip、macro、多层、调度器插入、raw 写入、dirty、注销、tick、取消、先失效和真实 epoch 竞态，以及同 slot 重用、Runtime 析构/同地址重建和 claim 竞争。独立 CPU oracle 检查 GPU 生成内容、padding、guard；guest CPU 源始终保持旧值。测试实际加载候选库，使用全新隔离状态目录，无游戏文件或正式缓存参与。

必须保留证据边界：epoch 竞态允许 Recorded 或 Stale，并不证明跨线程失效与录入线性化；Record 只编码到独立 mirror，协议 Complete 不表示 GPU 完成。后续 mirror→guest 回写没有全路径的分配/映射代际及完成保护证明。因此本轮不接入 raw factory、不新增全范围 guest 回写，也不靠全局 Finish 掩盖该缺口。

Stage A 的 payload/read-trap 协议核心与真实 runtime_memory.c、Tracker、fork 控制通过，不代表实际 FEX detour、完整适配器或游戏白模已验证；这些组件仍保留独立提案，不随上述局部修补启用。

## 后续优先级

本轮白模追踪在此收束，主工作转向默认本机离线建库的全部调用覆盖、全游戏程序/材质目录和加载期准备。正式应用、正式存档、设置及已有缓存保持不动；不更新 Release。
