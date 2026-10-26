# 正常运行自动捕获完整 fetch 编译输入

原 `CompileSnapshot::Capture()` 没有生产调用，而且会额外读取 V#。现在使用原解析、`GetSharp` 和 SPIR-V 发射路径的被动观察，不能把作者夹具中的 Capture 当作真实接入。

## 现有启动流程配置

ARM64 macOS 的 CMake `BB_DEV_FETCH_CAPTURE` 默认 ON，五个实现单元为 observer、producer、canonical、companion_store、register_store。构建开关仅用于隔离控制，不增加面向用户的复杂选项。本轮没有替换正式应用。

启动器原准备流程调用 `prepare_fetch_capture_receipt.configure_capture(root,game_root,catalog,index,backend,runtime_source_root)`，得到 `capture_environment`，传给原 exec；打包时 `runtime_source_root` 必须使用组件实际 `source_root`，不能假定工具旁有源码。后端也提供 `bb_fetch_companion_configure_v1(root,game)`，供同一进程的原启动 owner 一次性配置。相同配置幂等，运行中换 cohort 被拒绝。没有配置或可靠 receipt 时不创建线程、队列和 owned 副本，也不增加 guest 读取。

receipt 用真实 SFO、独立 catalog、精确 static index、最终实际加载 DSO 生成；包含 SFO/后端/index/catalog 摘要和精确 `(native hardware stage,hash,codeSHA,bytes)` allowlist。后端校验 `dladdr` 对应 backing-file SHA；这不是执行映像逐页认证。准备器复用 `VerifyPreparationReceipt(root,game,selected_backend)`，不把准备器自己的可执行文件误当成 DSO。所有根、marker、receipt、store 继续严格 canonical/owner/权限/单链接校验。

## 新编译与暖命中

- 正常新编译：`CompileScope` 包围原 `CompileModule`。原 parser 返回完整 fetch9 字段，原 `GetSharp` 在 override 前提供真实16字节 descriptor。成功模块在 SPV move 前复制实际 SPV 和完整 owned 输入，暂存两条总预算中的一个 lease。`GetProgram` 原 StageSpec 构造后才用真实 permutation/index/hash 封口发布。
- 暖命中：只在仍有待记录模块时，围绕原 `GetProgram` 已有 StageSpec 构造的 parser/GetSharp 读取被动观察；draw-preparation speculative worker 不发布。原 warm-load 保存实际读到的 SPV SHA 和大小。背景有界读取原文件；ZIP 复用现有中央目录并同锁保护读写、模式转换和关闭，校验 generation/path，避免复制整张目录。SHA 或大小变化拒绝。原始缓存不清空、不改写。
- 暖路径的 code SHA 延迟到确实要记录所选模块时计算，已记录模块不每 draw 扫描 shader code。只有正常已有读取，绝不为了采集再次取 guest V#。队列忙/满拒绝，未接受的暖记录可在后续 lookup 重试；每个模块已尝试后停止 observer。

新编译冻结并重验 code、UD、完整 canonical Profile、两个实际 live toggle mask，保存翻译前后不同的 active RuntimeInfo。暖采集的 code 身份在所选模块实际捕获时核对独立 allowlist，不能冒称是旧缓存生成时冻结的源；后续重译与实际 SPV 逐字节一致才是采用门槛。descriptor、code、userdata、flat、portable walker、原始地址数值都成为 owned 数据；Info 指针只在栈 scope 比较，绝不写盘或进入队列。

StageSpec canonical 编码显式包含 active runtime、Bindings、bitset、fetch9、vertex attributes、buffers、images、fmasks、samplers；不使用带 padding/host pointer 的 native Serialize。unused buffer 字段依原相等语义置零。0038 的 image type0 哨兵被保留，其余 image type 仅8–15。实际 `permutation_hash == HashCombine(program_hash,permutation)` 在 writer/reader 都校验，不能把完整输入摘要当 permutation hash。

未覆盖阶段、speculative/owned 再编译、shader patch/dump/collect、速度图的未捕获全局地址继续明确拒绝记录，不改变原绘制。两级页表、速度图、遮挡剔除、联网与现有回收路径没有因记录失败被更改。

## 完整复合 blob 与有界后台

详见 [完整编译 companion](fetch-compiler-companion.zh-CN.md)。每条包含完整 owned Snapshot、原 raw Record、翻译后 runtime、实际 SPV、StageSpec 和真实 stage/permutation 关联，单次原子提交。快照+SPV+metadata+IO reserve合计≤2MiB；building/queued/writing 总共最多两个 lease（≤4MiB）。SPV 在后台转移所有权，不能先复制成两份大向量。服务只有一个 utility worker，无轮询。

256MiB/8192文件新复合 store 达预算只拒写，不驱逐；原64MiB raw store 接口和目录保持原样。Push try-lock，不阻塞编译。predicate、Push、Close 同 mutex，解除锁后通知；writer 在锁外工作。

## CPU 验证边界

`test_fetch_compile_producer.py` 含12项 Python 控制，目前原生73断言；包含保护原 V# 页面后新/暖捕获、暖源 SPV 摘要改变拒绝、准备器严格读取、打包 layout-source 路径、完整 mask/Profile/source 校验。`test_fetch_companion_store.py` 原生56断言，覆盖 typed roundtrip、StageSpec、缺读/冲突、projection 互斥、篡改、总内存预算、2 lease、实际 mailbox 的受控丢唤醒窗口、失败保留旧文件、无淘汰配额和大文件流式读写。

`check_fetch_producer_tus.py` 对 fetch_shader、pipeline_cache_core、pipeline_cache_core_serialization、cache_storage 四个实际 TU 做宏 OFF/ON 编译。宏 OFF 的 hooks/helper 被预处理删除，无 collector 未定义引用；临时对象不保留。这些是 CPU/源码控制，不是完整 renderer/gameplay、性能或真实采集覆盖率证明。最终 coherent 构建、准备器重译/实际运行命中由统一批次验证。

成本边界与残余观察窗口见 [短成本审计](fetch-capture-cost-audit.zh-CN.md)。4MiB仅指在途预算，不能覆盖线程栈、allowlist和原small_vector结构增量。
