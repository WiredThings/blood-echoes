# 新特化 owned 编译接口：源码候选，尚未编译或启用

2026-10-25。本 worker 未编译、未执行测试、未运行游戏、未调用 Cua、未改正式应用/存档/设置/缓存；验证交根代理串行执行。该阶段不是性能修复完成，不是 WarmUp 或离线 Toolchain，不重复已有特化预载来称新优化。

## 实际修改

- `CompileSnapshot::Capture`：从已翻译过、已顺序刷新资源的 Info 复制程序字节、16 DWORD UD、完整 flattened UD、SRT 字节码、Profile、RuntimeInfo、起始 bindings、fetch 已解析结构与每个 attribute 的原始 V#、GS copy 字节和 tess 常量。GS span 在存储对象中清空，使用时才绑定本对象拥有的 vector；程序基址只保留标量做 inline descriptor fixup，不解引用。
- 单快照总载荷上限2 MiB，单code/GS copy各512 KiB、flat1 MiB、walker256 KiB、fetch32属性。先预算再复制。它是 **ordered GPU 调用接口**，不宣称读前后memcmp构成资源生命周期锁或原子代际证明。
- `Info` 显式 owned 模式：ReadUdReg 只读已经捕获的 register record 或直接UD；未捕获读取抛拒绝。ParseFetchShader返回owned解析结构；需要原始fetch代码的dump入口拒绝。Tess常量直接来自owned值。普通 Info 的路径保持原实现。
- ARM SRT生成在owned模式下重新生成当前IR的bytecode并与捕获布局/flat大小逐字节比较；不一致抛拒绝；一致后使用快照owned walker和flat，完全不执行guest walker，不向永久全局walker arena注册推测项。x86 capture拒绝，未修改x86 SRT生成。
- `CompileContext` 每编译线程拥有独立Pools。原CompileModule只把原Pools成员包进context；`CompileOwnedToSpirv`复用真实TranslateProgram和EmitSPIRV，结果保留shared snapshot生命周期。
- `PipelineCacheCore::PrepareOwnedShader`复用上述真实前端和后端hook；当前profile不匹配、dump/collect/patch或无后端支持则拒绝。Metal用独立ShaderCompiler返回detached模块；Build/Insert分开，准备线程不改program_cache/modules_/PSO表。Metal owned编译只读MSL缓存，不写新项、不删拒绝项，成功资源由RAII持有；adoption接口仍须在GPU线程且完成完整重验后调用。
- 原ShaderCompiler默认仍可正常写缓存；只读构造参数默认true保留既有行为。后台Metal调用有autoreleasepool。

## 已明确拒绝和未完成

1. **没有scheduler接线，没有默认开启的runtime候选。** GetProgram 的worker分支仍只读；当前正常路径不会自动调用Capture/PrepareOwnedShader，也没有多线程等待或画质/容量变化。单快照预算不等于已经证明IR/Metal临时峰值。
2. **初次程序miss未完成。** 尚无已生成的SRT布局和fetch/资源发现时不能构造完整快照；不能将worker观察到的hash当完整特化。需要把真实TranslateProgram的资源发现/flatten规划拆出为有界捕获阶段，或从权威固定目录拿到完整且与实际指令一致的方案，再使用同一owned编译接口。现接口针对已知程序的新spec，不能宣称覆盖317个程序miss。
3. **draw worker安全capture未完成。** 原sigsetjmp分支会跳过部分C++析构；不能直接在guard内新增动态快照。需要预分配有界capture arena、在recover点外保持owner，并对guest code/UD/SRT/fetch/V#/tess的代际或冻结句柄提供一致性准入。Capture现在只允许调用者已拥有稳定源的ordered GPU上下文；直接在drawworker调用不被本补丁授权。
4. **GPU adoption的完整身份重验未接。** 不能只按hash/permutation号接收。须核对代码字节/阶段/Profile/runtime+start、完整资源特化、fetch/GS copy/tess输入与source代际；再决定当前permutation号并按原RegisterShaderBinary/RegisterShaderMeta落缓存。不能提前分配cache-local permutation或在worker写meta文件。现detached接口为此分离，但GetProgram尚未采纳结果。
5. **motion vectors拒绝。** emitter仍从MotionVectors读取全局BDA标量；捕获VS/FS motion=true返回nullopt。要支持需将这组scalar显式送入emitter并在GPU采用时重验session。dump依赖原fetch代码也拒绝。GS/tess拥有字段不代表其实际GCN编译已测；Metal普通GS/tess模块没有可用plain shader，detached后台hook保守返回空，原路径继续。
6. **PSO提前准备未接。** 当前Detached接口只准备shader模块。新PSO必须有完整GraphicsPipelineKey、Metal序列化顶点属性、各阶段完整key/模块及动态状态快照，且需解决同一管线并发归属；不能伪造Metal dynamic-input能力、跳绘或排无界队列。后续建议单worker/单active/单pending、总owned输入不超2 MiB，busy/失配即原同步路径；drawworker不得等待GPU采用结果。
7. CompilerContext可独立拥有Pools，但调用者仍须保证同context不被并发重用、pipeline cache/backend比后台job存活更久。现在没有新线程生命周期，因此没有声称shutdown/cancellation已验证。

## 定向测试源码与根代理执行计划

新增 `tests/metal/test_shader_owned_snapshot.mm`，`build_first_upload_test.py --suite owned-shader --library <新隔离GPU库>`支持构建。该fixture包含自造GCN经真实Translate/Emit的原/owned SPIR-V逐字节对照、源code/UD/flat毒化后异线程独立context编译、未捕获UD读取和bounds拒绝、SRT布局/大小拒绝、fetch解析+V#拥有、GS copy span拥有、tess常量拥有、超预算/motion拒绝、真实只读Metal库构建，以及真实PipelineCacheCore→detached Metal hook准备前后modules计数不变。

这些是 **编写完成但未运行的控制**。根代理先重建隔离GPU库（CMake RECOMPILER glob需收进新.cpp），再构建执行fixture，确保关闭游戏且测试进程使用新独立Shared QA user/cache目录。随后重跑现有shader-phases真实GCN/Metal回归和必要完整GPU编译，确认原路径SPIR-V/资源布局不变。通过后才考虑有界capture/scheduler和实际探索新spec门控对照。

测试未覆盖：真实游戏新spec、全shader目录、首见程序、完整PSO后台、GPU资源代际锁、取消/析构时后台job、IR/Metal峰值、首次探索p95/p99与内存、白模。现阶段不得打包默认启用或宣称338ms尖峰已缓解。

## 根代理后续验证（不替换以上worker交付记录）

见 owned-shader-verified-checkpoint-20261025。230个GPU编译单元完整重建、owned实际GCN/Metal控制、原同步路径probe off/on与24个raw保全控制已通过；修复OwnedSpirv RuntimeInfo{}初始化与overlay复用原shortcut头的路径。第一程序捕获、完整采用和有界scheduler仍未接入，不能声称已修复探索长帧。场景脚本被用户观察到砍墙，只计启动动作smoke。
