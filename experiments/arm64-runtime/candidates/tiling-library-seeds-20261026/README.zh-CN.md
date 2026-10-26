# 未启用候选：十四类平铺转换库首启准备

基线 `15bcac22`。两个补丁按0001、0002顺序应用；它们**未应用到当前运行时代码、QA包或正式应用**。当前源码仍运行schema1、八类固定PSO的已验证入口。

候选沿原 `TilingShader`、`TextureTilingSource`、`CompileOptions` 与 `LocalLibrary` 收集十四份有限源码库，不创建纹理、guest表、最终tiling PSO或新的运行时缓存。八类固定PSO就绪与十四库实际Local就绪分别统计，二者均完成才声明整体完成。原热map命中路径保持原样。

内部能力、packet和consumer同时升级schema2。精确旧能力1与旧usage64返回unavailable，不能被误读为新能力；其它错误输出拒绝。库与PSO ready不混用。0002保留空源/nil库立即返回和原nil缓存语义；原`function ? MakeCompute : nil`保护始终存在，不属于已发生nil工厂调用的崩溃修复。

初始检查点已验证：组合补丁的21项纯CPU响应、旧/新能力和源码控制通过；使用实际Apple编译器及已准备的外部头文件闭包，对`mtl_runtime_kernels.mm`和`game_pso_preparer.mm`做`-fsyntax-only`均成功。仅有原有`auto`推导`id`警告。**当时没有产生对象或链接新库，不能称完整构建、Metal编译、像素或游戏通过，也不是干净源码构建证明。** 摘要见VALIDATION.json。

在独立隔离源码树应用两补丁后，可运行：

```sh
python3 candidates/tiling-library-seeds-20261026/test_library_contract.py --source-root "$PWD"
python3 candidates/tiling-library-seeds-20261026/test_delta.py --source-root "$PWD"
```

上述命令假定当前目录为`experiments/arm64-runtime`。不要直接对正式安装目录应用补丁。

下一验收必须包括：同源完整GPU/CPU/worker构建；真实重定位能力2和旧1拒绝；全新空QA缓存十四种源码身份收集、原本机builder、独立进程Local14/14；原八类PSO严格命中；缺库/entry/坏摘要回退；32/64、proxy、bounded原TextureTiling像素与guard；同场景冷/暖内存和API对照。没有通过前保持未启用。

库阶段有十秒预算，极慢机器可能分多次才收齐请求；现有一次builder策略未必当轮收齐晚请求，需沿已有两遍增量机制补齐，不能将partial当完成。十四份源码库也不等于全部最终函数常量组合或全游戏PSO覆盖。

这是默认首启准备的后续正式方向；正确性及内存验收通过后应默认接入，不以站桩FPS收益作为默认关闭的理由。当前未启用只因原生验收尚缺。

## 后续原生检查点（额度停止前）

同源230 GPU单元已完整重建，配对worker与作者控制链接通过；真实能力查询2。11核心原生进程通过。全新目录实际建22库；随后14/14 tiling库Local、8/8原固定PSO就绪，独立暖进程14 Local和8 strict、普通PSO编译0。缺失build反例回Source、0虚假完成。没有准备最终tiling PSO全集。

四个原纹理/代理回归进程（逻辑边界0/1）均通过，普通两组各执行18例32/64位纹理。但日志local-library-hit计数均0，尚未证明这些像素比较走Local库，不能将像素通过当作Local路径已验证。另缺14库缺entry/坏摘要反例、完整CPU/App配对与原入口冷暖；仍未启用。作者HLE桩不验证实际游戏系统接口。详native summary。

初次作者脚本漏改packet schema1被新worker以65正确拒绝；修正为2后通过，原始失败保留。额度已到14%，开发测试按用户规则停止；没有更新QA应用或Release。


## 2026-10-26：授权定向复测已完成，Local 像素归因补齐

用户仅对本轮目录修正与像素复测取消15%额度停止限制。旧作者脚本的 AOT root 位于另一个 GPU user 目录；只修正测试配置，将缓存克隆到每例自己的 gpu/local-metal，未改任何运行时目录校验。

九个最终进程通过：bounds0/1普通纹理各Source0/Local4、18例输出相同；代理各Source0/Local6、2例输出相同。代理额外的原SceneTargets CopySource库由现有builder仅在新测试克隆补建，不混入14种平铺库统计。错误目录仍以load模式运行并回退Source4/Local0，输出相同，证明未绕过目录准入。新增脚本从实际DSO读取API统计，避免作者executable重复inline变量造成假零。详evidence/tiling-local-pixel-validation-20261026。

28.16秒、426系统样本、最高压力2、swap减少8MiB；原fixture92文件散列清单不变。未运行游戏、未改正式数据/既有缓存、未改运行时代码、未重编QA App或发布Release。这些是作者GPU正确性控制，不是游戏性能/峰值内存或全部最终PSO覆盖证明。

Local像素归因待办已完成；仍缺14库缺entry/坏摘要反例、配对CPU/App及原入口冷暖和其它原目标。候选继续未启用，最新游戏QA包仍e3689f86。本轮授权测试已结束，整个旧目标仍保持原暂停状态，不凭本轮额度例外自动恢复其它开发。
