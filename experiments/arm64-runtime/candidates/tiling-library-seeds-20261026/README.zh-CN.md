# 未启用候选：十四类平铺转换库首启准备

基线 `15bcac22`。两个补丁按0001、0002顺序应用；它们**未应用到当前运行时代码、QA包或正式应用**。当前源码仍运行schema1、八类固定PSO的已验证入口。

候选沿原 `TilingShader`、`TextureTilingSource`、`CompileOptions` 与 `LocalLibrary` 收集十四份有限源码库，不创建纹理、guest表、最终tiling PSO或新的运行时缓存。八类固定PSO就绪与十四库实际Local就绪分别统计，二者均完成才声明整体完成。原热map命中路径保持原样。

内部能力、packet和consumer同时升级schema2。精确旧能力1与旧usage64返回unavailable，不能被误读为新能力；其它错误输出拒绝。库与PSO ready不混用。0002保留空源/nil库立即返回和原nil缓存语义；原`function ? MakeCompute : nil`保护始终存在，不属于已发生nil工厂调用的崩溃修复。

已验证：组合补丁的21项纯CPU响应、旧/新能力和源码控制通过；使用实际Apple编译器及已准备的外部头文件闭包，对`mtl_runtime_kernels.mm`和`game_pso_preparer.mm`做`-fsyntax-only`均成功。仅有原有`auto`推导`id`警告。**没有产生对象或链接新库，不能称完整构建、Metal编译、像素或游戏通过，也不是干净源码构建证明。** 摘要见VALIDATION.json。

在独立隔离源码树应用两补丁后，可运行：

```sh
python3 candidates/tiling-library-seeds-20261026/test_library_contract.py --source-root "$PWD"
python3 candidates/tiling-library-seeds-20261026/test_delta.py --source-root "$PWD"
```

上述命令假定当前目录为`experiments/arm64-runtime`。不要直接对正式安装目录应用补丁。

下一验收必须包括：同源完整GPU/CPU/worker构建；真实重定位能力2和旧1拒绝；全新空QA缓存十四种源码身份收集、原本机builder、独立进程Local14/14；原八类PSO严格命中；缺库/entry/坏摘要回退；32/64、proxy、bounded原TextureTiling像素与guard；同场景冷/暖内存和API对照。没有通过前保持未启用。

库阶段有十秒预算，极慢机器可能分多次才收齐请求；现有一次builder策略未必当轮收齐晚请求，需沿已有两遍增量机制补齐，不能将partial当完成。十四份源码库也不等于全部最终函数常量组合或全游戏PSO覆盖。

这是默认首启准备的后续正式方向；正确性及内存验收通过后应默认接入，不以站桩FPS收益作为默认关闭的理由。当前未启用只因原生验收尚缺。
