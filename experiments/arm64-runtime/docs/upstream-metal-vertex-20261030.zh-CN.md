# 上游 Metal 顶点候选与本机库兼容

来源：[Huax-1839/bloodborne-macos `77a4bf4d6ab5`](https://github.com/Huax-1839/bloodborne-macos/commit/77a4bf4d6ab5d06c426014322dadd5fed724cb14)。只提取普通光栅顶点运算的候选，不整文件覆盖本地 renderer。

## 当前采用边界

- 正常启动器默认开启，并传入`BB_MTL_RELAX_VERTEX=1`；底层仍只接受精确值1，未传入时保持原关闭路径。显式关闭会清除继承值并省略该变量，保留旧false缓存身份；已有明确关闭偏好继续生效。Strict float模式仍优先关闭此优化，现有严格顶点helper内联继续保留。
- 仅 SW Vertex + HW Vertex 且原片元/计算松绑 profile 开启时，移除浮点运算的 NoContraction，并在同一判定下装饰 Position Invariant。ES、LS、GS、TCS、TES 和深度输出不扩展。
- Metal 编库的 `preserveInvariance` 从实际 SPIR-V 的 Position/Invariant 装饰关联推导；读取捕获输入或 MSL 磁盘缓存时使用同一算法，不依赖一个环境变量冒充输入等价。
- 上游报告的帧率变化不是本机收益。尚未完成本游戏的画面、深度、速度图和内存 A/B；不得默认启用或声明已采用。

## ABI 与持久化

- `Shader::Profile` 仍为80字节；`relaxed_fragment_compute_float` offset72、`two_level_bda` offset73 不变；新增 bool 使用原 padding 的 offset74。实编译静态断言验证。
- 74字节的 canonical Profile 不扩容。最后一个字节明确定义为有界 feature flags：bit0 为原 two_level_bda，bit1 为新 invariant vertex；0/1 逐字保留旧编码，2/3 是新编码，其它位拒绝。其它布尔字节继续只接受0/1。旧 reader 会拒绝2/3，不会把新选项误读为普通 true。
- 核心 ShaderProfile 缓存另有明确的 wire 版本：默认off仍写旧96字节（16版本头+80 Profile），旧88字节短格式继续可读。旧格式的byte74始终是padding，0..255都不冒充新增bool。新true只写116字节显式feature envelope；实际新增bool及其flags严格一致，并还原全部命名字段。
- 新wire在payload首词放固定marker，真实viewport宽度保存在20字节feature envelope。去掉整个envelope、截断到旧长度或错位都不能伪装旧格式。Metal实际viewport上限16384，模块原有准入上限65536，marker与合法viewport区间不交。
- core、模块准备、geometry准备gate和开发fixture共用同一native codec；Python准备投影与同一wire对照。实际C++ WarmUp判定前缀（内存Storage stub）验证旧padding全部256取值：reused、Clear=0、Save=0且旧blob字节保留。新true放默认false则拒绝；没有为了复用任意将真实新true归零。
- 其它真正不兼容／损坏缓存的原有Clear路径本轮不扩展重构；测试使用内存stub和新私有数据，未拿正式缓存试清理。
- 本机 game-AOT 增加 schema3、`bb-metal-local-aot-invariant-v1`、显式 `preserve_invariance: true` 和独立键。实际离线命令增加 `-fpreserve-invariance`；收据、源摘要、工具身份和库摘要仍严格绑定。
- schema1/2 原请求与键不变。完整 helper-library wrapper 继续拒绝 preserveInvariance，未把 helper 的支持范围一并扩展。
- 启用时使用独立核心缓存标签和 `vertex-invariant-v1` PSO 子目录；关闭时保留原目录/标签。旧缓存不删除、不清空、不覆盖；实际 MSL/选项相同的产物仍走已有跨 cohort 验证复用。

## 工具链选择修复

本机 `/usr/bin/xcode-select -p` 已指向 Xcode，但隐式 `xcrun metal`/SDK 查询仍选到 CLT，报 missing Metal Toolchain。统一通过 `/usr/bin/xcrun --sdk macosx ...` 查询实际 SDK、查找工具、取版本以及执行 metal/metallib 后正常。

这不接收任意工具路径或环境覆盖。FixedRunner 继续清除 DEVELOPER_DIR、TOOLCHAINS 和 DYLD 环境；实际 SDK 元数据、工具 SHA、OS 与 BuildId 仍核验。现有工具登记已绑定 local_metal_builder.py 规则源码，规则变化只触发重新登记，不直接把源码 hash 当成编译产物 BuildId。SDK/工具真的不同仍正确失效。

## 验证与复现

- 新7项 AOT 控制：旧键逐字一致；same-source 新旧选项键/产品不同；拒绝 false/数值/字符串/缺字段/错 schema/错 recipe/坏摘要/额外字段；真实 builder 命令协议和收据不匹配；跨 cohort 保留复用。工具 runner 注入部分为控制测试，不冒充 GPU 实验。
- 实编译真实 CanonicalProfile/DecodeProfile：ABI、旧字节、4种 feature flags 往返、252种非法 flags、普通 bool 反例和真实 SW/HW 全阶段条件通过。
- 独立审阅后追加 core Profile wire 回归：实编译生产 WarmUp 的真实判定前缀与内存Storage stub，旧padding的256种取值均复用、无Clear/Save；新116字节native writer/reader与Python投影一致，全部截断长度、删feature prefix、错位、未知版本/位、raw/flags冲突均拒绝。低层store实际556断言、typed companion61断言通过。
- 原 metadata reader 的当前fixture指针在本工作树缺失，未伪造该指针或将导入失败算通过；另显式选择既有coherent作者fixture只读运行原18项reader控制通过（旧layout，不冒充最新layout重建），证据 `evidence/upstream-vertex-regressions-20261030/decoded-legacy-coherent-layout.json`。新feature layout另由实际C++ offsetof/sizeof发出并与Python校验。
- 无窗口实际 Metal probe：两个作者自制顶点库分别编译，先写深度再以 Equal 绘制，32×32 的1024像素全部匹配。新 schema 请求由实际 native helper 记录。
- 通过现有严格 NativeToolchain/FixedRunner 实际离线编库2份，启动独立 probe 进程实际 Local 命中并得到同样深度像素；再次 builder `built=0`，再启动进程仍 Local 命中。证据 `evidence/upstream-vertex-native-current.json`，首次 BuildId `5db87511a6c59568d2597d160853ff354749a52a4a84802c441a7556de6c83ab`。

命令：

```sh
python3 -m unittest discover -s experiments/arm64-runtime/tests -p 'test_upstream_vertex_*.py' -v
python3 experiments/arm64-runtime/tests/run_upstream_vertex_native.py
```

第二条需要主机 Metal 访问权限，默认沙箱 MTLCreateSystemDefaultDevice 可能返回 nil。只使用新建私有测试目录及作者自制数据，不启动游戏、不使用 Cua/截图/录屏，不改正式存档、设置、缓存或应用。

本机交付：0.2.59 / `5b186b81`已默认开启；明确关闭仍使用原OFF身份并保留旧缓存。同包ABBA约+1.53%，开启后的F3和两次死亡回归通过，持续场景视觉仍由后续正常游玩观察，不据此承诺全场景收益。详见[实际游戏回归](upstream-game-regression-20261030.zh-CN.md)。Release未发布。

## 没有移植的异步池

审阅了 [上游 `694a52252154`](https://github.com/Huax-1839/bloodborne-macos/commit/694a52252154f99421bac3bc14733ff09e85ded1)。当前默认本机准备已避免已覆盖输入在游戏中建库；新池只能补动态遗漏的并行编译，不替代提前准备。

该提交还把 Metal 接入核心 AsyncSkippable：上游注释明确允许每帧绘制的通道在管线尚未完成时跳过一两帧。它与“不降低画质、不跳绘制”约束不符。本地现有路径不因此解锁跳绘制；本轮不移植该池。若以后单独提取纯等待式编译并行，仍须 owned 输入、固定线程/队列预算、完整结果准入、析构/取消/资源寿命验证，不能直接叠加默认2–8线程或无界环境线程数。
