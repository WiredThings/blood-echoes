# 管线持久化与加载期重复读取：隔离验证（2026-10-25）

## 实际改动

主路线遵循完整组合持久化与加载期预热优先，未知首见异步仅兜底。本轮没有增加异步调度器，也没有恢复已移除的 Metal Toolchain 实验。

1. 显式开启的 ARM64 启动归档实验，使用实际选定执行文件的唯一 libbbgpu 加载边、架构、签名和 SHA 校验；真实库、boot、已应用 startup patches、配置与 profile 共同产生兼容 token。PsoCache 再加入实际 GPU registry/name、系统 build 及源代码相关选项，形成 v3 独立文件名。旧 v2/legacy 文件不导入、不清理、不覆盖。原 Rosetta 实验仍走旧路径；OFF 不解析新库、不新增文件散列。
2. 仍使用现有 WarmUp → Flush → Release 生命周期；加载时的归档只查找，刷新使用既有完整 descriptor ledger 重建，先释放旧 archive 再建新对象，不额外常驻两份。成功写入同目录唯一临时文件后才原子替换。管线和库的进程内缓存预算未缩小，绘制命中路径未新增磁盘检查。
3. 真实 LoadPipelineStage 先查已加载的完整特化及原 permutation index，命中复用现存模块；仅需创建模块时才读 SPIR-V 并验证头。不同特化和不一致索引不会错误复用。第一条模块编译成功才发布 program，失败不留下空条目。
4. 归档刷新前检查父目录仍为目录。作者负例把父目录变为普通文件时，AGX 原先会在内部临时 metallib 处理中中止；新检查返回失败，保留旧文件。它不在逐帧热路径；也不能保证抵御外部进程在检查后的删除竞态。

## 执行证据

- 最终候选由已验证的 230 单元一致对象集只重建两个实现单元，公共头和类布局不变；库 SHA-256：9f0573ad466ddb2468391c2769ab234c306b830a60c79cd230e25b524d9b8771。
- 15 个独立原生进程通过：计算与渲染第二次启动必须实际命中 FailOnBinaryArchiveMiss；源代码改变正确 miss；附件格式改变验证正确像素；归档释放后仍实际 dispatch/draw 并读回；成功文件在发布冲突和 ENOTDIR 失败后保持；OFF、缺失/错误 token，以及游戏、补丁、选项和翻译器身份变化不加载旧归档。逐进程 dyld 加载边均核对实际候选。
- GPU/系统不匹配只做纯身份 helper 负例，未声称跨硬件或跨系统实测。系统驱动缓存没有清除；测试结果以严格命中及输出为准，不用运行快慢代替命中证明。
- 真实 Storage 注册/保存/读取、Info/spec/meta 序列化及 LoadPipelineStage 路径通过：相同模块移走/损坏 SPIR-V 后仍复用，区别特化、旧 index、短头、坏 magic、首次缺失、后端拒绝及有效重试均验证。仅 CreateShaderModule 是计数 mock，这项不证明真实 Metal 编译结果。
- 9 项实际 producer 函数分支通过，散列真实作者文件；只注入原生 resolver，不冒充原生身份验证。Rosetta 和 OFF 明确断言不调用新散列器/resolver，原环境不变。
- 真实 owned GCN/SPIR-V/Metal 控制、阶段探针开关后相同 SPIR-V、24 类纹理 GPU 保留/格式/布局/部分覆盖控制均通过。仍是作者输入定向测试，不是游戏白模已修复。
- 全轮串行保护监督约 18.81 秒，压力 2 → 2、swap 4159.25 → 4159.25 MiB；仅证明这轮没有新增 swap，未量化实际游戏峰值/常驻内存。正式数据 11,671 个既有文件逐个大小与 SHA 核对不变。

## 失败历史与限制

最初原生夹具误写 CompiledShader 的 const 身份、局部变量 id 遮蔽 Objective-C id，Clang 正确拒绝；仅修测试。Storage 夹具首次遗漏 bbgpu.h 的 include 路径，仅修构建参数。原 ENOTDIR 注入确实使驱动中止，失败记录保留；先用发布目标冲突证明旧文件保护，再增加真实父目录检查，并把原失败条件加入最终 15 项回归。未放宽任何输出、严格命中或格式校验断言。

MSL 源仍不等同于 metallib，binary archive 命中也不意味着省掉 newLibraryWithSource。游戏中新增组合在现有 ReleaseWarmArchive 后不会即时写入该归档，仍须下一次 WarmUp 才覆盖；这是后续覆盖率工作。启动 manifest 保守包含整个设置，音量/窗口等也会导致不必要的失效，散列自身有一次启动成本；未宣称零开销。候选尚未在实际游戏比较冷/暖启动、帧时间、编译次数及内存，因此不默认启用，不替换普通手动 QA 包或正式应用，不更新 Release。

## 复跑

先按现有隔离构建环境建立完整 GPU 基线，再执行：

```sh
python3 tools/build_warm_archive_candidate.py --warm-stage
python3 tools/run_authored_controls_guard.py warm-persistence-checkpoint-supervision python3 tools/run_warm_persistence_regressions.py
```

这是需要本机基线构建元数据的增量候选工具，不冒充全新 checkout 自包含 clean-build 验证。工具只使用新建的 Shared 私有 state/evidence 和作者输入；不能将上述命令直接指向正式缓存。
