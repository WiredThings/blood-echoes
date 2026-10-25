# 私有平铺参数与GPU逻辑texel边界候选（2026-10-25）

## 结论与范围

已实现默认关闭的隔离候选：retile只回写GPU实际纹理覆盖的逻辑texel，保留原始字节填充区；保留原物理pitch、height、padded寻址和层跨度，没有逐像素CPU mask、新图像副本或扩大缓存。先前Thin13 64×60与七级small-mip的真实GPU-only失败均消除。尚未改FindImage/memo/exact/overlap布局选择，也未证明用户战斗白模被修复。候选未进入正式应用或普通本机QA包。

## 实现与ABI

- 新src/bb_tiler_bounds.h定义私有ParamsV1：旧TilingInfo前缀460字节，16×12字节logical(width,height,slices)尾部，共652；静态断言offset/size/standard-layout。公开TilingInfo、TilingDispatch、KernelBuffer以及原函数签名不变。
- TileImage仅在BB_ARM_TILER_BOUNDS精确为1、完整canonical颜色导出时上传652；其它情况以及DetileImage保留460。拒绝partial mip prefix、非零origin、非canonical row/offset、部分layer、volume、block、depth/stencil和MSAA。本轮没有修这些未覆盖形状。
- GPU新常量HAS_LOGICAL_BOUNDS使用13（12保留给既有TextureTilingScale）；HostKernels只在is_tiler且params.range精确652时显式设true，不用>=。Generic和直接texture两条路径都设置常量，完整缓存键新增独立bool，不挤占物理字段/原12个寻址常量。
- GLSL仅在tiler且常量为true时读tail；裁剪x/y/slice和count，不改线性物理stride或宏/微tile地址计算。每次增加192字节参数payload；若下一次按256对齐，分配间距典型512→768、多256字节，仍使用现有固定stream buffer。未增加逐draw等待；测试显式Finish只属于校验控制。
- 新MakeTilingInfo入口num_mips断言和HostKernels num_constants<=12断言，防止新的复制路径形成越界读。
- TextureTilingSource从生成的InputBuf/OutputBuf参数导出匿名资源名，继续严格限定替换次数；不再依赖SPIR-V的_788/_794编号。形状不符仍显式退回原staging路径。保留原RGBA8位精度、RGBA16F位模式/proxy NaN与MetalFX缩放规则。
- 不能直接ninja重生旧B：其规则仍指向R上游源码。本轮从P候选显式生成全部12份SPV+12份MSL header，放在独立include优先路径；没有覆盖B中的旧对象/header。
- 当前隔离两对象热链接只替换tile_manager和mtl_runtime_kernels，inline TextureTilingSource在后者。subagent实际Clang记录布局：HostKernels old/new均208/align8，14成员偏移一致；Runtime::Impl均536/align8，11成员偏移一致。所有两map访问和HostKernels构造/析构都在kernels TU，节点类型不跨TU。头中另有old/new map sizeof/align断言。常规源码构建仍应按头依赖重编8TU；若改成员顺序/大小，本结论立即失效。

## 可复跑控制

1. generate_tiler_bounds_shaders.py：12组来自当前候选GLSL，均成功。实际32/64位MSL重写、数字资源改名和非法输入拒绝控制通过。生成头含常量13；direct路径不能只看fallback通过。
2. test_texture_layout_preservation.py --full --candidate evidence/tiler-bounds-candidate-current.json --bounds：24控制符合预期。21正例全部0 raw/pixel mismatch，包括原64×60与七级small mip失败；另3负例分别拒绝陈旧CPU、像素CopyImage、未GPU生成。Macro32 direct开关正例日志确认真正命中，mip/sRGB不支持形状仍fallback。原CPU源/目标仍保持旧0x11/0x22，GPU新图案不是由CPU expected值喂入。
3. 同候选关闭bounds的4控制：紧齐正例通过，两已知padding缺陷仍exit7、known_defect=true、all_preservation_paths_correct=false，未篡改期望像素凑绿。是同库代码开关的缺陷对照，非性能A/B。
4. test_tiler_bounds_cache.py：Metal debug layer开启，同进程460/652交替六次，旧参数tail刻意为零，旧模式写padding/新模式保留padding，所有raw与guard通过；19类copy形状拒绝、14个参数长度/方向控制通过。说明新旧管线缓存未混用；不把非法range当成可读元数据去执行。

证据：tiler-bounds-generated-1792909511202564000、tiler-bounds-candidate-1792909632293352000、texture-preservation-controls-1792909678150489000、tiler-bounds-cache-controls-1792909843819025000、关闭texture-preservation-controls-1792909846433227000。测试依赖原已准备的独立构建stage，不宣称clean checkout可独立编译本原型。

## 单轮真实游戏冒烟（非性能结论）

用户关闭其它应用后，启动时系统pressure1，swap6838.75MiB。launcher-engine-abba-1792909936367393000使用同CPU67db、独立GPU候选和原16启动脚本，BB_ARM_TILER_BOUNDS=1、静音、隔离QA存档的新副本，没有Cua或桌面/游戏帧抓取，也未测试死亡/在线/菜单。

18.05秒出现场景camera，两个有界move/attack输入请求；66.41秒以private action trace completed完成。trace两进程无overflow/clock reject，源QA存档SHA不变；exit143是监督器SIGTERM正常收尾，不是闪退。GPU子程序实际LC_LOAD_DYLIB/ARM64/签名严格验证，结束后alias恢复原目标，无残留游戏进程。日志已有6条既有Metal texture tiling计数；没有逐次bounds命中统计，所以不能把它算作所有新导出条件已覆盖的证据。

峰值pressure4只有两个采样，首末约0.261秒，未持续至原5秒阈值；结束pressure1。swap6838.75→8609.9375MiB，+1771.1875MiB（约1.73GiB）。没有保护中止，但不能以此宣称内存无新增、已消除压力，亦不能与前一不同启动环境的+963MiB混成匹配A/B。没有帧率、长期跑图、白模视觉或原生分辨率跨配置验收结论。

## 未完成与下一步

继续证明资源切换前raw权威来源与生命周期，再改错选路径；复杂形状不能直接丢GPU写回。需要默认无诊断的匹配性能/内存对照和真实战斗白模现场。当前候选只在isolated opt-in测试，普通QA包仍c22970cc（CPU67db/宿主a500/renderer66d），正式app与Release不更新。Mlock及4项线程/上下文接口、完整特化提前准备、硬件TSO授权目标仍开放。

## 默认常量兼容修正与最终控制

审阅还发现：新局部数组若直接补0并把count设为14，会覆盖旧调用省略参数时的shader非零默认值；旧补0缓存键还可能合并省略/default1与显式0。已改为从实际shader.spec_constants填默认值、覆盖provided，再设13，并用同一规范化前12构成缓存键。直接TextureTiling已有num_constants==12门槛，保持其完整12项键；未来放宽时必须同步规范化。未改公开ABI或对象布局。

最终候选c6076a5fb543bf50d2bc1506001c75717311c23f77a7c6141d4c697479f29e64：tiler-bounds-candidate-1792910436214291000；六次交替、19拒绝、14range和新增4默认值控制通过（tiler-bounds-cache-controls-1792910447740115000）；24 GPU正反控制复测通过（texture-preservation-controls-1792910451228633000）。生成器现在冻结GLSL源输入，12份新生成MSL与已测前一候选逐字节一致；严格重写器控制复跑通过（tiler-bounds-rewriter-1792910275299800000）。前文66秒游戏冒烟对应前一aa7e4357候选，不能当成最终c607构建的游戏验收。

## 最终构建真实场景复测

最终c607候选也已通过一次独立静音探索：launcher-engine-abba-1792910530822646000，真实加载边匹配上述SHA。场景camera 21.91秒；两轮有界输入请求，70.42秒以private action trace completed结束，监督器143非崩溃。源QA存档SHA不变，两份trace无overflow/clock reject，GPU alias恢复且无残留游戏进程。压力1开始、1结束，短暂4的两个采样首末0.257秒；swap 8297.9375→8658.0MiB，+360.06MiB。仍是诊断兼容冒烟，不是白模视觉验收、FPS或内存A/B，尤其不能把第二轮较低swap增长归因于新补丁；应用/驱动缓存与起始VM状态不同。
