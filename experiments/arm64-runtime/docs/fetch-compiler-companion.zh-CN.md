# 完整 fetch 编译 companion

`fetch_companion_store.{h,cpp}` 提供单原子 `BEFCOMP1` 复合 blob，避免原来只存16字节 RegisterRead sidecar、却丢掉 ProgramBase/UD/flat/walker 的不可独立还原问题。

内容包含：全部 canonical Profile字段（含 two_level_bda、relaxed float）、翻译前后 active RuntimeInfo、Bindings、hash/native hardware/logical stage、ProgramBase 数值、code/UD/flat/data-only walker、fetch9、原始 RegisterReads、原 raw Record、完整编译环境/masks、实际 SPV、显式 StageSpec 与真实 permutation/index/hash。原地址只保留整数，不解引用；host Info/span/vector/native object dumps 均不存在。

Reader 先验证严格 owned 根和 marker、文件身份/大小及整个 payload 摘要，再按字段解码、域检查、完整输入摘要重算和原 raw Record 对照。walker 仅验证 opcode/stack/flat 覆盖，不执行或注册。raw snapshot 与 semantic projection 严格互斥。SourceRequirement 必须来自已核 receipt/current Profile/exact code allowlist；正式准备消费设置 `require_association=true`。

自动准备消费顺序：`VerifyPreparationReceipt(root,game,selected_backend)` → `SourceForReceipt(receipt,currentProfile)` → `Store::Open(root)` → `Keys(error)` 有界枚举 → `Read(key,source,error)`。结果原样交原 `CompileOwnedToSpirv(input)`，逐字节比较实际 observed_spirv，并比较翻译后 runtime、显式 StageSpec。`DecodeStage` 返回无 Info 指针的 typed StageSpecialization，供原算法对照；消费端不能重读 guest 或把 descriptor 从格式字段反造出来。

`record.expected.environment.compile_inputs` 是完整输入 integrity ID；它与 `association.permutation_hash` 意义不同，后者严格遵循原 HashCombine。blob key 是完整内容 locator，也不替代 runtime module/PSO key。最终图形/计算 PSO 仍必须来自原 key 中真实 stage_hashes，禁止按 program hash 做笛卡尔组合。

新目录为私有根的 `fetch-compiler-companions-v1/{64hex}.befetchc`，原 `fetch-register-inputs-v1` 不变。单项上限2MiB含保留容量和IO reserve；两个 lease 覆盖构造、排队和写入。流式 writer/reader 不同时保留一整份编码副本；memory codec 另计外部编码内存。256MiB/8192文件总预算，满了拒写不淘汰。exclusive temp→摘要→fsync→完整读回→原子 rename；失败或取消保留原有效文件。

作者 CPU 控制证明这些本地契约，不代表游戏已采到全部动态组合或准备器已经采用所有输出。真实闭环仍需最终组件构建、正常采集与准备后实际 runtime hit 证据。
