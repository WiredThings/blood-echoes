# 真实 GCN 输入与独立 CPU 重新翻译

2026-10-26，隔离开发源码。此处处理可编译输入的完整性，不是一次游戏性能测试，也没有更换正式应用。

## 实际链路

`static_program_links.py` 将固定 1.09 资源中的真实代码、实际硬件阶段和来源关联；`game_module_inputs.py` 从经过原有 owned audit 的运行记录冻结当前 profile、spec-only、16 个 UD、flat 和 data-only SRT triplets，再独立重读 SFO、容器、条目及 GCN 摘要。不将旧 Info、原生对象地址或可执行 walker 交给消费者。

`src/game_module_preparer.mm` 使用现有 CompileSnapshot/翻译器，在 CPU 进程中重新发现资源和 SRT，并确认生成的布局与冻结输入相同；重新建立的特化向量长度和内容也必须相同。输出 SPIR-V 还必须与已观察结果逐字节相同，之后才发布 `module.spv` 和 completed receipt。没有 Metal Device、guest 读取或运行时缓存导入。

这份输入包是**受信任的本地冻结器的输出**。包内自报的游戏版本与 SHA 不是独立的来源认证；调用方必须重新核对原来源和 profile，不能接受任意目录发来的包。原生端另外落实同一 owned canonical 根、无链接、有界长度、前后 stat、文件 SHA 和独立输出目录等检查。

未知 guest program base 不默认为真实的零地址。新增 `TranslateProgramWithoutProgramBase()` 在 ResourceDiscover 后、Flatten/ResourcePatching 前拒绝 OffsetByProgramBase 依赖；原 `TranslateProgram()` 的调用和行为保留。定向夹具覆盖每个资源的两个 sharp 位置和全部现有 post-op。需要 program base 的输入保留明确缺口。

## 原生安全边界

- 总输入 2 MiB，GCN 512 KiB，输出 SPIR-V 512 KiB，沿用 Snapshot 的 flat/walker 上限。
- Profile 所有 bool 与 RuntimeInfo 活跃 union 的 bool、枚举、数组数量先按 raw bytes 检验，再调用原 spec reader；完整 spec 区间之外的旧 Info/SRT 尾不允许进入。
- fetch、motion 和 tess 当前仍不在此消费者准入范围；GS 只经独立 schema，先清零旧 span，再从同源静态 copy 代码重绑定 owned span。空 SRT 必须是 `walker_bytes=0` 且 walker 为 JSON null；非空必须提供完整引用。
- 特化向量比较前检查五类向量长度一致，避免继承的比较器在截短的合法序列化向量上越界。每个文件引用强制非空完整 SHA256。
- 缺读、不同 SRT、不同特化和 SPIR-V 不一致均拒绝；失败不产生 completed receipt，也不覆盖原目录。诊断构建仅额外保存 `rejected-generated.spv`，不能当已准备模块。
- 已支持的普通 CS、FS、VS 以及 ES/Vertex 采用各自真实 hw/sw 对；不能把裸 hash 或 BinaryInfo 文件类型转换成硬件阶段。

## 当前验证及边界

初始 31 个独立程序包中，18 CS、8 FS、2 VS、2 ES 已重新翻译为逐字节相同的 SPIR-V；一个 CS 的 SPIR-V 不一致仍拒绝。空 walker 的实际模块已通过，35 类原生反例干净拒绝且没有成功输出。另将 51 条物理 GS 记录去重成 14 个独立 GS 输入，14 个均经真实 main+copy 重新翻译为逐字节相同的 SPIR-V；独立 GS 的 28 个原生反例也已拒绝，输出为空；最后构建只增量复核 1 GS、1 普通正例和 1 混种反例，不重复整场长测。合计 45 个真实输入态，44 个通过，不等于 45 个不同程序或完整 PSO。

CS `42f2a521` 的 SRT 和特化重新生成相同，但 SPIR-V 有新增 subgroup ballot/lane 指令，不能当作仅 ID 重排。历史缓存也存在不同输出；旧格式只记录布局版本、profile/spec/Info，没有原 GCN 摘要和编译器提交身份，暂不能证明它们来自同一生产链。保留全部旧缓存，不以新输出强制替换。新库／缓存消费需要同时证明生产者身份。

这些验证证明的是独立真实输入可以重复翻译；**不代表全游戏最终 PSO 已齐备，也不代表 Metal 像素、帧率或内存 A/B 已通过**。完整 PSO、缺失 fetch 的实际 V#、motion 常量、GS copy/tess 及附件状态的边界见 `pipeline-input-completion.zh-CN.md` 和 `vertex-fetch-inputs.zh-CN.md`。固定游戏的静态全集与已观测组合分别计数，不混用。fetch 持久化契约见 `fetch-register-store.zh-CN.md`，当前纯助手并不代表运行时已自动捕获；真实 producer 另行接入和验证。

## 开发命令

`python3 tools/build_game_module_consumer.py --project /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`

该工具是**本机定向构建**：只编消费者与新增翻译入口，链接摘要固定的先前完整 native DSO，依赖路径统一后签名。它没有重新编译全后端，不能冒充干净检出的可移植交付。正式整合前仍须统一构建。所有实际 GCN、SPIR-V、输入包和编译结果仅在本机隔离目录；Git 中只放工具、源码、作者夹具及去内容摘要。每位用户的库和管线由其自己的游戏资源在本机产生，不分发我们的游戏派生产物。
