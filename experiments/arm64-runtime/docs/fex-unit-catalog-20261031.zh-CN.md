# FEX 首次单元范围读取与非倒退验收

当前是隔离源码候选；不是正式应用已生效或游戏帧率已恢复。既有CPU/Metal/启动器目标保留，不因定向测试通过而完成整个目标。

## 已定位并修改

旧格式只以整分片摘要建立可信边界，首遇分片需要全读，并对BECC、FXCC和各单元做验证。2MiB证明LRU淘汰还会使后续读取重走该流程。已移出FEX锁不等于没有IO/CPU成本。

生产器现在使用原FXCC结构验证器和真实FEX header/tail边界，生成独立的完整单元目录：兼容键、FEX编译版本、分片摘要、文件/代码范围、完整block/pages/relocations、单元范围和摘要。BFXDIR02以精确摘要绑定目录；运行时核对目录完整性、身份、边界，随后只读取所需完整单元并核对其摘要。发布前原源页、JIT预算、缓冲/已有映射、重定位与I-cache规则保留。

这是将必要元数据登记前移，而非取消对实际采用代码的校验。未采用的代码及旧容器填充无需为当前执行再次读取。旧BFXDIR01继续可读，准备器可从旧分片生成新目录，保存原index且不重编/改写机器码；缺失/损坏目录明确退回自身JIT并请求修复。不新增常驻代码库，不扩大原JIT/证明缓存；索引每分片增加40字节，临时读取及借出证明另计。

旧格式首次读取门控也已修正：选择后只保留所需单元，完整输入释放并解门控后才等待失效/JIT发布锁。独立并发控制确认：主线程持有两把锁时，两个FEX线程均可完成不同分片准入，再等待发布，不会让完整读门控被一个发布等待占住。

## 定向验证

原生真实FEX控制12次正常返回0，包含跨进程/不同基址生成及执行、HLE/RIP重定位、链接/清理/源页变更与容量自身JIT、目录缺失/损坏回退、旧格式执行及升级。目录兼容键、FEX版本、越界guest、截断/尾随和摘要反例均拒绝。暖执行断言读取与目录计数不增长。另目录格式14个拒绝及百万条writer/reader回归、8项launcher增量控制通过。

同一自造字节序列旧格式read_bytes=25960、hash_bytes=43472、whole_reads=2；新格式分别3340、3340、0，减少读取约87.1%、摘要字节约92.3%。新旧装入1176字节机器码，证明缓存1560字节不变。升级compiled=0、compiled_shards=0、reused_shards=2且原分片摘要不变。这些是定向计数，不是整场景加速比例。

证据：`/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/evidence/unit-catalog-controls-1793443357659773000/result.json`。可重复：构建原`test_fex_prepared_disk`目标后，运行`python3 experiments/arm64-runtime/tests/run_fex_unit_catalog.py --binary /absolute/path/test_fex_prepared_disk`。测试固定使用Darwin embedding分配区域内基址；前面选区外hint的失败保留，未用MAP_FIXED覆盖映射。

## 稳态差异尚不能归因于校验

上一轮暖采样中，JIT与预编译组均无GPU现场编译。frame_ms均值20.924/21.294，gpu_ms均值18.293/18.765；GPU时间差已经大于总帧差，不能从约1.7%的平均FPS差直接推出CPU校验是唯一原因。当前校验在L3缺失后的装入路径；已发布代码的重复执行不进行磁盘/摘要检查，控制已验证这一点。预编译顺序与运行时顺序可能影响代码布局，但尚无证据把实测差异归到该因素，不据此扩大特化。

后续集中验收须采用同源GPU/同设置、交错ABBA及独立长帧事件场景，检查平均FPS非倒退、P99/最大帧、FEX读取/编译、GPU模块/PSO次数与时间、峰值/持续内存；诊断与性能分轮。暂未通过，不宣称本候选已解决转视角、探索或战斗卡顿。

## 减少突发卡顿的其它既有路径

GPU提前准备优先：已有真实338ms长帧中Metal建库约232ms、PSO约84ms，同帧FEX block约0.407ms，见shader-phase-and-uniform-range.zh-CN.md。复用已有静态目录、完整特化捕获、本机库和binary archive，先核对加载期准备与运行时的真实键/命中，不能重复建设或跳过draw。Apple文档：https://developer.apple.com/documentation/metal/metal-binary-archives 。

资源转换/上传提前量和同步等待其次：复用原后台准备、内容代次、生命周期和fence，只将可证明的工作移出首次绘制等待；有界临时内存，不常驻另一套资源，不缩缓存、不回退白模修复。静态bbcpu仍是独立CPU备选，但它不能消除Metal编译、资源读取或GPU同步引发的长帧。

正式应用、存档、设置、已有缓存及Release均不动。
