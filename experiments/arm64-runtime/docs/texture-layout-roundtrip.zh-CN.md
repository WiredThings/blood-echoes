# GPU-only 字节布局保全控制（2026-10-25）

## 结论

现有私有Metal库的 TileManager::TileImage → 同一原始字节 → DetileImage → UploadImage **在已测的紧齐RGBA8子集可保全GPU-only内容**。但不能直接用于任意布局：GPU只改逻辑texel时，当前retile会覆盖仍属于旧raw backing的padding；新布局把这些字节变成可见像素后会出错。没有修改 FindImage 或公共GPU代码，没有宣称用户白模已修复。

本目录：texture-layout-roundtrip-1792905925397597000。源码roundtrip.mm，完整实际库结果results-v3.json，额外归因padding-audit.json和最终padding-final.json，编译/依赖build-v3.json，装载证明load-identity.json，准备脚本prepare_test.py。均为自造数据，不包含游戏代码、素材、存档或既有缓存。

## 数据来源与独立oracle

源纹理由真实Metal compute在 storageModePrivate 的MTLBuffer中生成非均匀32位pattern，再由GPU blit逐mip/layer写入texture。该buffer没有CPU contents路径，不是给陈旧guest内存填值后冒充GPU写。先逐像素读回源并校验GPU公式，才能继续。

客体源/目标CPU映射在两种布局最大raw跨度内分别初始化为0x11/0x22，转换前后逐字节检查完整跨度始终保持原值；不把源guest_size以外但新布局可能读取的已映射raw字节留作未初始化基线。因此源CPU无法提供已生成图案，目标也不能从CPU上传得到正确结果。没有CPU图像数据填回客体。

独立原始地址oracle复用原项目测试 tiling_cpu_reference.h 的逐texel地址函数，与TileManager所用GPU work distribution不同；GPU纹理的每个逻辑texel在expected raw中只写它真正覆盖的四字节，未写区域保持原raw的0x11，尾部256字节guard为0xCD。检查映射injective/in-bounds，先比整个retile raw与guard，再按目标布局地址推算每个目标像素并真实Metal读回比较。

## 最终结果

去重依赖后24个控制：19次保全正例通过、2项边界保全失败、3项故意错误来源按预期拒绝。19包含3次开启direct开关的重复控制，不能说19种布局。

通过的子集：

- 64×64单mip/layer，linear(8)↔Thin1DThin(13)、Display1DThin(9)↔Thin13。
- 256×256 Macro14↔Thin13，以及Macro14→Thin13的3级mip（256/128/64）。
- 64×64、3级mip到16×16、2layer的Thin13↔linear；2layer的Thin13→Display9。
- Thin13的UNORM↔sRGB，以及Macro14 sRGB→Thin13；比较原始8bit通道，不依赖浮点颜色近似。
- 高度1、相同linear布局的逻辑1D/2D双向，保留 #5133 所允许的统一2D backing用法。
- linear→Thin13的64×60目标padding边界也通过，但不能用这个方向替代相反方向测试。

开启 BB_MTL_TEXTURE_TILING=1：Macro14 RGBA8单mip/layer实际记录“Metal texture tiling”并通过；mip链和sRGB源正确回退原路径且通过，不伪称direct支持它们。原始关闭开关时同例也通过。

### 必须保留的失败

| 现有路径控制 | raw错误 | GPU逻辑texel错误 | 仅原raw padding错误 | guard错误 | 新布局可见像素错误 |
|---|---:|---:|---:|---:|---:|
| Thin13 64×60→linear | 1024 | 0 | 1024 | 0 | 512 |
| Thin13 64×64、7级小mip→linear | 684 | 0 | 684 | 0 | 156 |

64×60的源实际texture只有60行；ImageInfo布局为64行，最后4行没有任何GPU blit，CPU raw仍为0x11。retile却把这些padding字节输出为0；目标linear的有效texel需要读取其中512字节，512全部错误。小mip同理：目标有效像素会读取160字节旧raw padding，156被错误改写。padding-final.json在明确初始化完整最大raw跨度后对两项失败、紧齐正控和反向padding正控再次确认，结果相同。两个失败中，所有GPU实际定义texel均正确、guard也未破坏，问题不是越界图案或整图GPU复制失败，而是把不属于源texture逻辑内容的raw字节也视作可覆盖。

### 反作弊负控

- 不执行GPU生成：源像素oracle立刻拒绝（退出9，观察0x11111111而不是预期pattern）。
- 强制改用陈旧CPU raw：raw/目标均错16329字节，退出7。
- 直接 CopyImage 像素：raw本身正确，但新布局目标错12064字节，退出7。

这确认现有测试不能因“原始CPU字节/直接像素复制”而假阳性通过。

## 代码边界解释

vendor/metal_api/shadps4/video_core/texture_cache/tile_manager.cpp:33起 MakeTilingInfo 把 info.mips_layout 的pitch/height交给tiler；这些字段已经包含物理布局padding。:168起 TileImage备用路径只从源texture下载实际逻辑 imageExtent 到linear staging，再DispatchTiling。padding并不等于有效imageExtent，不能认为整个guest_size都已被源图覆盖。后续DetileImage按新布局解释同一raw时，原padding可能成为有效texel。这是本次控制确证的语义门槛。

正确的保全模型是：原raw最新数据（可能来自CPU或别的GPU buffer），加上源图实际覆盖的texel字节；仅覆盖被证明写入的对应坐标/子资源，不得全raw替换。不能用一个 GpuModified/GpuDirty flag 代表padding也来自图像，不能把CPU旧进度误上传覆盖GPU内容。

当前DownloadTiledImage（mtl_runtime_kernels.mm:375）直读纹理快路仅支持指定格式/单mip/layer等，不能因此推断padding或任意格式已可保全。下轮若修其逻辑尺寸边界，应同时保持物理padded_pitch/padded_height与bank地址规则，不能简单把物理height改小。

## 安全整合门槛

先将转换准入限定在真实控制通过的子集；padding/小mip失败不能隐藏。若整合布局改变修复，需要明确源raw与GPU texel的覆盖合并、同步顺序、旧view/backing延期销毁、分配代际及部分写入。合并后再次执行失败case，必须以真正raw权威源保留padding，而不是把测试expected改成0。

本轮raw中间缓冲取自既有StagingRequest池，fallback及detile也复用原池；没有扩大缓存或添加常驻表。最大逻辑图像/布局344064字节、单控制8MiB合成VA；有界短进程退出即释放。读回与oracle只为定向正确性，不是生产路径设计。尚未测试转换在游戏中的FPS或峰值内存，不能承诺零额外瞬时占用；正式启用前需验证有界队列和既有池的实际峰值。

未覆盖深度/压缩格式/MSAA/3D/thick/部分GPU写/多源重叠权威，也没有实际白模现场、游戏性能、F3/死亡/联网测试。不得泛化本子集。

## 测试依赖核验与隔离

最初独立测试从原链接命令带入Homebrew依赖，和库自身packaged SDL发生双载警告。仅给测试Mach-O执行原工具已有的otool basename依赖重写方法，统一到指定库的实际packaged依赖并重签；24例全量复跑结果相同，失败保留。load-identity记录实际只装载一个packaged SDL及a5005c5f... GPU；没有改共享库或系统依赖。

实际 GPU 库：libbbgpu.avplayer-1792894220118812000.dylib，SHA256 a5005c5f807c861eddd50a31fcf0e64295a463aeff9d33e5097cddf504ab2783。HOME为本目录private-home，GPU_USER_DIR为Shared/state中新建同名目录。无游戏/GUI/音频、无残留测试进程。正式应用、存档、设置、已有缓存未使用或修改，没有公共源码改动和Git提交。
