# GPU覆盖范围合并语义正控（2026-10-25）

## 结论与边界

仅在新的自造测试中增加GPU端覆盖合并，两个旧失败均消失：

| 控制 | 未合并raw错误 | 未合并目标错误 | 合并后raw错误 | 合并后目标错误 |
|---|---:|---:|---:|---:|
| Thin13 64×60→linear | 1024 | 512 | 0 | 0 |
| Thin13 64×64七级mip→linear | 684 | 156 | 0 | 0 |

expected padding仍为原权威raw的0x11，没有改成0，也没有向客体CPU源写入GPU图案。两个新布局分别会把512/160字节旧padding当有效像素读取；合并后这些像素均正确。全部GPU定义texel、padding和尾部guard错误均为0。

这证明所需的**覆盖合并语义**，不是完成生产修复。mask由独立CPU地址oracle在小型测试中生成，不能直接作为逐像素CPU mask生成的生产方案。没有修改公共GPU代码、FindImage行为、现有测试基线或任何正式数据；没有运行游戏/GUI、扩展格式矩阵或测性能收益。

## 实际路径

1. 真实Metal compute在private MTLBuffer生成非均匀pattern，GPU blit写源texture各逻辑mip/layer。源GPU读回先校验，禁止未生成就继续。
2. 客体CPU源/目标在两种布局最大raw跨度分别始终为0x11/0x22；前后全跨度逐字节assert。源CPU保持旧内容，不能供给新GPU图案。
3. 原a500私有库 TileImage 产生raw候选；独立CPU tiling地址oracle生成“哪些原始字节属于源实际逻辑texel”的mask，只提供覆盖位置，不向GPU提供expected图案值。
4. 新GPU compute执行 `authoritative=(produced & mask)|(authoritative & ~mask)`。authoritative raw先保留原raw 0x11、尾部guard 0xCD。只用实际retile输出替换被源GPU texel覆盖的字节，padding原样留下。
5. 用既有DetileImage按新布局解释合并raw，再UploadImage并逐subresource真实Metal读回，比较独立raw地址oracle预测。

合并核函数处理32位word的每字节mask；本测试逻辑texel为完整RGBA8，mask为0或全1。没有CPU像素合并，没有把expected像素写入GPU。原私有库中间输出仍有1024/684错误，日志before_raw_mismatch保留，随后才合并为0，未掩盖原错误。

同步采用隔离测试明确的GPU完成边界：既有retile完成后执行独立merge command buffer并等待完成，随后再detile。中间span用原StagingRequest池；同buffer不同span断言不重叠，未扩大池配置。此同步仅是正确性控制，不能作为游戏每次转换都CPU等待的性能设计。

## 九项最终控制

results-final.json 全部符合预期：

- 合并64×60与七级小mip：通过。
- 紧齐Thin13→linear与linear→Thin13：通过。
- 两项unmerged：仍以7返回，原1024/512与684/156错误完全复现。
- 陈旧CPU raw：合并无法重建缺失GPU图案，仍错16329字节，以7拒绝。
- CopyImage像素捷径：raw正确但新布局目标仍错12064字节，以7拒绝。
- 跳过GPU生成：源oracle观察0x11111111而非预期pattern，以9拒绝。

旧目录 texture-layout-roundtrip-1792905925397597000 的源码/失败日志未改。新目录只复制为独立merge.mm；新测试同样保留未合并负例。最初本轮results.json的unmerged诊断误打印固定gpu_merge=1（同时merged=0正确），已只修测试标签并重新编译复跑，最终results-final对应gpu_merge=0；保留原日志，避免把日志标签当执行事实。

## 内存与隔离

每项8MiB合成VA。限定本轮四种小布局，最大raw跨度22784字节；raw输出与authoritative各最多23040字节（含256 guard），mask最多22784字节，均低于344064限制。GPU生成buffer按mip/层小图逐次执行，不存在大型常驻表。全部中间span复用既有池、不改池容量；测试结束进程释放，无残留进程。

mask/原始地址expected/readback仅为离线正确性控制。未测生产转换的峰值内存或帧率，不宣称额外资源免费。实际整合应优先研究让retile核只写有效逻辑texel而保留原raw其余部分，并保持物理padded_pitch/padded_height及bank寻址；不应在游戏线程每帧构建逐像素CPU mask。

生产前仍须确认原raw基线确为最新（可能来自CPU、GPU buffer或镜像）、逻辑texel/子资源覆盖、GPU写入顺序、分配代际及旧view/backing生命周期。不能把CPU值或GpuModified整图布尔值当任意场景权威源；也不能仅据这两个case就放行部分CPU/GPU混合写、压缩/深度/MSAA/3D。

实际库 libbbgpu.avplayer-1792894220118812000.dylib，SHA256 a5005c5f807c861eddd50a31fcf0e64295a463aeff9d33e5097cddf504ab2783。load-identity证明指定GPU和单份packaged SDL，测试Mach-O依赖统一到该库自己的依赖，不改共享库。HOME为本目录private-home，GPU_USER_DIR为Shared/state中新建同名子目录。无游戏资源、存档、正式设置、既有缓存访问或清理，无Git提交。

## 复现与交付

在准备过的本机隔离环境：`python3 prepare_test.py`，然后 `python3 run_controls.py`。本工具复用现有fixture、CPU地址oracle、准备好的库和依赖路径，不是全新checkout独立构建声明。build-v2.json记录编译命令、source/binary SHA和装载边；source-identity.json记录未改动的原基线/相关实现。源代码与自造控制可由根代理择机整合，不上传本机二进制或私有state。
