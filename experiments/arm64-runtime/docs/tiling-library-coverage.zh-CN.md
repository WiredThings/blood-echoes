# 残余平铺转换建库与最终 PSO 的区别

只读复核了当前公开源码、已有请求元数据、原生准备结果和暖入口日志。没有再次启动游戏、GPU或编译，也没有改缓存。

## 四条新增本机库请求

暖入口在此前builder的449条请求之外新增了四条schema2 helper请求；请求的完整source SHA与本次生成的MSL头及原 `TextureTilingSource` 变换逐字匹配：

|源|变体|源字节数|请求key前缀|
|---|---|---:|---|
|macro16|原库|25241|af6a6bd356a1|
|macro64|原库|25238|5783c2d3c965|
|macro32|texture改写|25789|3931c58866e2|
|macro64|texture改写|26252|2e0449cfb32a|

四者均entry `main0`、Metal3.1/Safe/Fast，使用原helper编译选项。原generated header包含多个相邻raw literal，必须完整拼接，不能只hash第一段。

它们与本次GPU ready之后4次Source API、共3.035ms的时段对应。不过API计数没有逐次key和起止记录，不能据此分配单调用耗时。聚合所在帧269.29ms，也不能把整帧卡顿归因于这3.035ms。

## 十二条“已有本机函数输入”

十二条 `collected-local-inputs` 都是普通graphics配方。首次函数已经是LocalMetallib，`source_fallback=false`；缺的是最终本机PSO。第二遍每项构建一次，随后原游戏WarmUp按同一完整artifact key命中，全部发生在GPU ready之前。

这证明原游戏的准备路径确实消费了产物，不代表已逐draw证明使用。它们与上述四条平铺转换源码库缺口不同；不能将“十二条需第二遍”误判成“十二次MSL库漏编译”。逐项汇总见 `evidence/residual-source-attribution-20261026/public-summary.json`。

## 下一步：从源码有限域准备库，最终 PSO 另算

原 `TilingShader(bits,macro)` 的源码域为bits `{8,16,32,64,96,128}` 与macro/micro，共12份原库；texture路径实际只接纳macro、bits32/64，再加两份原变换，共14份库。当前版本总源约281323字节、最大单源26252字节。它们可以不依赖历史游玩、逐条沿原工厂收集并交给既有本机builder。源码大小不等于编译器或驱动峰值内存。

这是下一项待实现改动，**当前八类固定内核首启入口尚未包含这14份完整库目录**。应复用原source提供者、compile options、entry与身份生成，不复制一份手写MSL，也不硬编码本次四个摘要作为所有用户的编译身份。

最终PSO另外依赖原 `TileManager::MakeDispatch` 的typed constants：samples、micro-tile mode、thickness、方向，以及macro的array/pipe/bank/tile-split等；bounded与texture proxy倍率也会特化。纹理尺寸、guest地址和UBO mip数组是dispatch数据，不应加入编译变体。

因此，14份库不等于14份最终PSO。完整特化应通过原MakeDispatch合法性过滤与原工厂canonical去重，再按现有有界准备流程持久化；不能把理论笛卡尔积当作《血源》真实可达全集，也不能侵占运行时TextureTiling槽位或缩小缓存。

最小验证顺序是：先由原builder消费现有四个请求，验证同场景Source4→0；再加入无历史的14份源码库收集，验证全新目录、实际Local来源及原工厂像素结果；最后处理可审计的最终特化目录。保持画质、缓存预算、正式数据和既有保护不变。

## 原builder消费四条请求的实际复测

没有修改运行时代码或包，仅使用同一隔离QA缓存和原准备入口：保留的首轮builder结果453条，其中449条skipped-verified、4条built；四个built key与上表逐一相同。后续审核453条均跳过验证，游戏目录424/424完成、record0。

相同相机90秒站桩通过：GPU ready之后Source4→0、Local7→11；strict PSO13/13。普通render/compute工厂仍各1次、合计1.162ms，并非整个驱动绝对无编译；最后30秒这些API均0。48.510 FPS、p95 21.36ms、p99 21.72ms，按最慢1%帧均值定义的1%low44.20 FPS；无匹配ABBA，不声称FPS收益。combined footprint5816.26MiB非去重，最高压力2、swap+373.06MiB，不代表内存改进已归因。

此结果支持四条具体库缺口的归因与既有持久化路径；不等于已经实现无历史14库首启收集。后者仍是下一项运行时代码工作。详second-warm-verification.json。
