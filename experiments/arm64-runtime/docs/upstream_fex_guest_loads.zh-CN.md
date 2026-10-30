# 2026-10-30：FEX #6074 guest load 副作用移植

来源：[FEX #6074](https://github.com/FEX-Emu/FEX/pull/6074)，已合并，合并提交 `46acbde3c3f9eee57b9113c0c691f69bc571c7f7`。

## 窄范围修改

本地六项 IR guest load 加入 `HasSideEffects: true`：LoadMem、LoadMemTSO、VLoadVectorMasked、VLoadVectorGatherMasked、VLoadVectorGatherMaskedQPS、VLoadVectorElement。VLoadNonTemporal 已经有此保护，保持不变，LoadMemPair 的既有保护也保留。没有照搬较新上游 gather 的新参数，也没有修改其它指令、访存顺序、寄存器或异常桥。

原因是 guest 读取即使其值和条件码均未使用，仍可能产生可观察的访存异常；原 `EliminateDeadCode` 仅保留有 uses 或生成表标为有副作用的节点，缺失标记会使载入和死 flags 一起消失。此修复恢复异常语义，不能解释为已定位到血源某次崩溃或已取得性能收益。

## 缓存兼容与构建

本 snapshot 没有上游 DiskCache.h，不伪造对应的 38→39 格式版本，也不新增清缓存逻辑。现有 `targets.cmake` 通过真实 `$<TARGET_FILE:FEXCore>`、embedding、Common 等产物生成 CPU runtime build 身份；`make_cpu_cache_build.py` 对实际归档字节摘要，再组合进 CPU 缓存准入身份。因此重编后的 FEXCore 产物会自动产生不同的 CPU 身份，无需触碰原缓存或 GPU 编译身份。

FEXCore/Source/CMakeLists.txt 的现有自定义命令依赖 IR.json 和 json_ir_generator.py，修改会触发现有 IR 表生成与 FEXCore 重建。完整构建后仍须核对新生成表及实际归档摘要；本小测试不冒充已经编译整个 FEX。

## 已完成的无游戏验证

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 experiments/arm64-runtime/tests/test_upstream_fex_guest_loads.py
```

7 项测试通过，约 1.3 秒，全部只用 `/private/tmp/be-upstream-fex-loads-*`，运行后清理。

- 执行实际 json_ir_generator.py，编译它生成的 opcode enum、HasSideEffects 表与函数。
- 原样编译生产 `EliminateDeadCode` 方法体，最小 node/emitter 容器只提供其 uses 和 Remove 接口。
- 六个新增 load 均验证 uses=0/1 不删除；已有 non-temporal／paired load 仍保留。
- 纯算术的 dead 节点仍删除，有 uses 的节点仍保留，不把所有 DCE 禁用。
- 用旧 IR 标记重新生成、编译，六个 load 的零 uses 反例均实际失败，确认覆盖缺口。
- opcode 编号、其它表、dispatcher 输出与旧标记版本一致，未扩大到 IR ABI／参数变更。
- 使用实际 CPU 身份脚本和自造归档输入，验证相同输入稳定复用、仅 FEX 输入变化时 runtime 身份变化、bridge 身份不变，并保留旧缓存 fixture 字节。

## 真实 FEX 额外 fixture：已在新归档执行

`tests/test_upstream_fex_guest_load_fault.cpp` 是不含游戏字节的实际 FexGuestCpuBackend fixture，供集中构建后链接到新的 FEX/embedding：

- `valid` 模式执行手写 `cmp rax,[rdi]; add dword ptr [rsi],1; mov eax,42; ret`，确认返回且计数变为1。
- `fault` 模式要求 SIGBUS/SIGSEGV 的实际 `si_addr` 等于输入页首地址，且后续计数仍为0；预期故障由 fixture 自身处理后退出，不用系统 crash report 作为成功依据。

首次 fixture 时序不正确：在 `Run` 前将映射声明为 writable 的页设为 PROT_NONE，被实际 mapping 准入以 `BACHATA_FEX_MAPPING_FAIL reason=host_write_permission ... host_protection=0` 拒绝，counter仍0。此结果只证明没有进入 guest，不是 #6074 实现失败。

修正版让输入页在映射准入时保持 RW。guest 先写 gate0，再用有界 `ECX=0x02000000` 的 PAUSE 循环等待 gate1；native helper 仅在看见 gate0后对自有输入页 `mprotect(PROT_NONE)`，随后 release 发布 gate1=1。helper 等待10秒超时或 mprotect 失败发布2，guest返回明确错误值，不假成功。不引入 HLE 回调、重新准入或运行时生产代码变更。普通返回均 join helper；预期故障路径只用 async-safe write／_exit，随 fixture 整个进程退出回收 helper，不从信号处理栈执行 C++ 清理或 longjmp。handler同时核验两个gate为1、实际故障地址以及counter0。

2026-10-30 已使用集中构建 `5c0f2b72bb2f1153513ce018f2a26814d8c61e7e` 的真实 Ninja compile／link 参数，仅替换 fixture 源文件、obj／dep／输出位置，保留 `-UNDEBUG` 和全部新归档依赖。未修改 clean source、生产 build、manifest或本机应用：

- FEXCore SHA256：`ab1734d381994e944705f8ab8fce92d3a5be3fcddb04b5359580123f8b13d06c`。
- embedding SHA256：`d108aaa265508e313fbbdaa068286bb3870bddbeb56a56f13a7b914981591aaa`。
- compile／link退出均0，约0.969／0.155秒。
- valid退出0，约0.584秒：真实guest返回、后续store发生。
- fault退出0，约0.0076秒：已进入guest、保护在入场后发布、目标读取fault、后续store未发生。

证据目录：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/upstream-fex-load-fault-20261030/`，`plan.json` 保存原始与替换后的命令、15个真实输入的大小与SHA256；`result.json` 保存各次退出、耗时、完整stdout／stderr与JSON断言。执行后输入摘要全部未变。该测试只覆盖手写dead-flags guest read和正常路径，未运行游戏、未证明所有七类load的Darwin fault，也不把上游Linux sigtest当成本机验证。
