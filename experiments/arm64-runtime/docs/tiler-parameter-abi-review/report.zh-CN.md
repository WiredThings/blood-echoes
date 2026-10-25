# Tiler 参数 ABI 与逻辑覆盖范围审查（源码交付）

结论：当前候选采用 **460 字节原前缀 + 192 字节逻辑 mip 尺寸 = 652 字节**的私有参数，公开 TilingInfo/TilingDispatch 不变，方向与精确 range 决定 specialization 13；这是比复用 pitch、打包不透明位或让旧调用者读取尾部更清晰的接入。实际代码审查未发现此方案新的 ABI 阻断项。它修复的是 retile 导出覆盖 padding 的已复现缺陷，**不是用户偶发白模的根因结论，也不是完整布局错选修复**。

## 审查对象与证据

本目录 source-identity.json 固定此次查看的实际 P 源码 SHA256。abi-contract.json 是归一化字节布局；layout_contract.cpp 对真实头文件做编译断言，已有 ARM64/x86_64 编译证据。host-class-layouts.json 是 Clang 前后实际布局输出（原输出单位为 bit，abi-contract.json 已换为 byte）。existing-spirv-layouts.json 和 candidate-spirv-layouts.json 反射实际旧/新 12 个 SPIR-V，不是手写布局推测。未在此次收尾启动任何游戏、GUI、编译或 GPU 测试。

## 生产与消费契约

- src/bb_tiler_bounds.h:11–17 定义 ParamsV1，logical 位于 460，大小严格 652；Enabled 仅接受 is_tiler 且 range==652，**不是 >=652**。460/651/653 等保持 legacy specialization。没有修改公共结构，不应将未采用的 656 字节“内嵌版本号”草案误认为实际参数。
- tile_manager.cpp:183–203 仅 TileImage 在开关 BB_ARM_TILER_BOUNDS=1 且 Describe 成功时上传 652。DetileImage 仍上传旧 460。direct TextureTiling 与 fallback DispatchTiling 接收同一 blob。线性导出早返回不受影响。
- tiling.comp:18/112–119/622–627 保留 scalar 前缀，LogicalMipBounds stride12、数组16、尾部偏移460；13 是默认 false 的 bool。仅 tiler 且 true 才读尾部。循环进度先于 continue 更新；x 越界先拒绝，width-x 不会下溢；count 裁至剩余逻辑宽度，防 CopyWholeRow 跨过逻辑边界。原物理 pitch、height、padding、bank、tile 地址公式均不改。
- mtl_runtime_kernels.mm:163–216 两条消费者都显式设置 13，并将其放入**独立完整 cache-key bool**。TextureTiling 的 proxy scale 仍用 12。公共 constants[12] 没有被越界写入；局部数组扩到14供 specialization。新加 num_constants<=12 断言挡住 copy_n 越界。
- MakeTilingInfo 在循环之前新增 num_mips 上界断言，避免 Describe 之前已越界。不得把“之后会拒绝形状”当作提前越界的保护。
- mtl_texture_tiling.h:9–34 从实际 InputBuf/OutputBuf 声明导出标识符、严格验证出现次数；重写失败返回空并走既有 fallback。新 GLSL 改变临时编号，不能继续假定旧 _788 等固定编号。实际 direct 路径命中需要日志单独证明，像素通过本身不能证明走了快路。

## 实际 copy 形状与拒绝范围

唯一生产 TileImage 调用在 buffer_cache.cpp 的 SynchronizeMemoryFromImage。它从 mip0 起逐级构造 canonical copy，但 arena 容量检查可提前 break，形成**完整前缀而非完整 mip 链**。因此不能假设所有生产调用都适用新裁剪。

Describe 只接完整 canonical color exports：mip 顺序/数量、每级原始 offset、物理 rowLength/imageHeight、零 imageOffset、Color aspect、对应 mipLevel、baseArrayLayer0、全部 layers、逻辑 extent 与 depth1 都必须匹配；逻辑宽高及层数不得超过物理参数。partial mip/offset、重排、局部层、裁剪矩形等拒绝；block、volume、depth/stencil、MSAA 拒绝并保留旧路径。失败时局部 out 已写入的尺寸不会上传，安全。拒绝不表示这些路径的旧 padding 问题也已修复。

## 公开 ABI、内部 map 与重编范围

实测 TilingMip=28、TilingInfo=460、KernelBuffer=24、TilingKey=16、TilingDispatch=160。前后 HostKernels **208 字节/align8**、Runtime::Impl **536 字节/align8**，全部成员偏移一致，详见归一化 JSON。HostKernels 新 key 仅改变 std::map 动态节点的类型；map 本体大小/对齐前后相同，源码另有 old/new map 静态断言。不能据此断言节点内存完全相同。

所有 tiling/texture_tiling map 访问和 HostKernels 构造/析构都集中在 mtl_runtime_kernels.mm，TextureTilingSource 是该 TU 的内联头。因此本次限定热链接候选需要重编 **tile_manager.cpp.o、mtl_runtime_kernels.mm.o**，并明确从候选源生成12个 MSL 头。没有第三个独立 TextureTilingSource TU。其余内联 Runtime::Impl/FaultParser 访问的偏移已核对稳定。

常规干净源码构建仍应让头依赖重编8个 Metal TU：upscaler、runtime_kernels、camera_motion、scene_targets、runtime、rasterizer、presenter、occlusion；既有构建带 ThinLTO，布局相同不是普遍 ODR 安全证明。本次局部候选的依据是实际布局、唯一缓存访问 TU 和运行控制，不应推广到任意后续 header 改动。

CMake 为 micro/macro × 8/16/32/64/96/128 产生12变体。完整双后端生成闭包是12个 Vulkan C-SPV头 +12个 binary SPV +12个 MSL头，另有24条独立 tiling_ref 测试规则。B/build.ninja 仍引用旧 R 源，不能仅运行它就声称编到 P 候选；根代理本次已显式用 P 源和私有生成 include 优先级。**当前 Vulkan 消费者没有显式启用13，默认 false，仍是 legacy 行为；不能称 Vulkan 同样修复。** 若扩展 Vulkan，需重编 vk_tile_kernels 并同步 specialization。

远程协议只传 SubmitGfx/Asc 与命令字、映射/生命周期等，GPU 子进程在本地创建 TextureCache/TileManager 和 UBO；bb_protocol.h、remote_front.cpp、remote_back.cpp 无 TilingInfo/Dispatch 序列化。故此私有尾部不需要 IPC 协议升级或 FEX 桥重编，但 GPU 库必须具有一致的新生产/消费端。

## 现有运行证据（读取根代理已完成结果）

- evidence/texture-preservation-controls-1792909678150489000/result.json：bounds_enabled=true，24 cases，control_outcomes_verified/all_preservation_paths_correct 均 true；game_started=false。包含此前失败的64×60和7级小 mip：原始 padding 保持0x11，GPU逻辑内容与重解释像素对照正确。此前未合并失败结果仍保留，未将 oracle 改成零或让 CPU 改写源。
- evidence/tiler-bounds-cache-controls-1792909843819025000/result.json：passed=true、Metal debug layer=true、game_started=false；同一进程 460→652→460→652→652→460 六次 raw/guard 全通过，legacy 提供 poison/零逻辑 tail 仍正确，19种形状拒绝与14个 range/direction 控制通过。
- 根代理说明默认关闭时4控制仍保留两已知失败，此项来源于根代理记录，不把它混入本目录自行运行结果。

## 兼容边界、代价与剩余门槛

颜色/sRGB 合法 alias、1D/2D 规范化、0038/0039/#5133、first-GpuDirty hash、两级页表、回收/同步、速度图、遮挡、THD 的代码语义不由此参数改写。直接 TextureTiling 仍严格受 macro、32/64bit、原生格式/维度/usage、单 mip/layer、对齐等限制；sRGB、数组、多 mip 可走普通 fallback。half/proxy 的格式精确性、NaN bit 和实际快路命中仍要专门保留门槛；proxy 的逻辑 bounds 必须使用原始 image 尺寸，不能误用缩小副本宽高。未来 BC 应使用块单位，不能拿当前颜色 texel bounds 直接套用。

每次适用导出新增192字节 UBO；若下一分配按256对齐，典型460→652会使分配间距512→768，约多256字节，不扩大既有暂存池。内部缓存新增 bounded 变体，direct cap32未改；并非严格零开销。没有新增生产逐像素 CPU mask、锁页、大缓存。没有本次 FPS/内存收益实测，不能声称零性能/内存代价。

仍未证明：实际游戏布局变更时，raw backing/padding 是否总是最新权威源；FindImage 错选旧布局资源时应怎样合并 GPU 覆盖范围与旧 raw；偶发白模的症状因果；长时稳定性和性能。候选只保证“已知完整 canonical 导出中不覆盖非逻辑字节”。根代理正在做隔离探索，本报告不预填其结果，不更新正式版或正式数据。


## 后续补审：省略 specialization 参数的默认值（独立时间点）

记录时间：2026-10-25T06:41:46.184253+00:00。此节对应 source-identity-defaults-followup.json 的3个新哈希，**前文 source-identity.json 不覆盖本次追加修改**。

发现的兼容风险成立：原 Function 对 id>=count 的 specialization 不设置值，让 shader 使用自身默认值。为了显式提供13而把局部数组补零并把count提高到14，会错误覆盖原来省略的非零默认值；原零填缓存键也可能把“省略且默认1”与“显式0”合并。

已只读核对 src/bb_tiler_bounds.h:19–29 与 mtl_runtime_kernels.mm:163–185 的修正：Constants 先按实际选中 shader.spec_constants 填 default_value，再覆盖提供的前缀，最后单独设置13；同一个规范化数组的前12项构成 Tiling key，bounded 保留独立 bool。Function 仍依 metadata 类型设置 BOOL/U32/I32/F32，default_value 的u32原始位值不被数值转换破坏。所有当前调用在形成 span 前断言 num_constants<=12，满足 helper 前置条件；helper 本身不是无条件接受任意长 span 的公开接口。

TextureTiling:189–217 仍使用 raw constants 键，但唯一生产调用 Runtime::DownloadTiledImage:386 明确要求 num_constants==12，全部12项均显式传入，所以此处当前不存在漏参/default碰撞。以后若放宽12项门槛，必须同时规范化其参数与键，不能直接复用现在的断言。此结论不是对任意外部直接调用 HostKernels 的保证。

已查看 tiler_bounds_cache.mm:31–42 新4项定向控制：省略等价显式默认、非零默认保留、显式零区分、独立13覆盖。它们验证 helper/规范化语义；本审阅未编译、未运行GPU，不把根代理正在重跑的24项/缓存控制预先标为通过。此次改动不改变652/460参数布局、HostKernels/Runtime::Impl对象布局或先前ABI数值。
