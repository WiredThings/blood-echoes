# 未接入的最小修复设计

本文件及 layout_candidate.h 只是供根代理审查的候选；没有修改共享源码，也没有构建/运行修复后的真实库。

## 1. 精确匹配与合法别名分开

精确同址/同 extent/同资源数匹配必须再满足 SameGuestByteLayout(a,b)。候选谓词比较 guest_size、extent、mip/layer 数、pitch、bits、samples、tile/array、bank_swizzle、alt_tile、volume/block/tiled/pow2、micro_tiled_mips 与实际使用的 mip size/pitch/height/offset。未使用 mip 的未初始化字节不比较。地址、逻辑类型、typed format、depth aspect 不是这个谓词本身的职责，仍由现有调用者检查。

因此 sRGB/UNORM twin 可继续共享物理 backing；1D/2D 在 #5133 已统一的 2D backing 上、相同字节布局仍能共享。depth/color 转换继续走现有 ResolveDepthOverlap，不把“字节数相同”当成完整视图兼容证明。

FindImage exact loop 的最小检查差异为：

    if (!SameGuestByteLayout(cache_image.info, info)) continue;

但这一个改动不能单独启用：memo 和 overlap 同样需要修正，否则仍选错，或丢失 GPU-only 数据。

## 2. memo 必须记住请求布局，而非只比较选中图

现有 cache entry 不保存请求的 pitch/tile/array/swizzle 等。同维度的 CompatibleDimensionalLayout 无条件为 true。still_exact 也因此把 generation 改变后的相同大小误判为精确匹配。

同址精确 memo 可使用候选谓词对选中 backing 再检验；合法 mip/slice view 与 backing 本就不完全同布局，不能一律要求它们与 backing 完全相等。稳妥方案是给现有 1024 项 entry 的“请求键”补齐紧凑布局输入（而非新建全量 ImageInfo/缓存或把裸结构体 padding 当键），并在命中时保留现有 subresource/type 检查。必要时对非精确 alias 重算 overlap；不要清缓存、不要缩容量。新增元数据应先静态算清字节量，可限制在约数十 KiB，而不是复制整张图/完整表。

## 3. 同址 overlap 在“<=size 可作视图”之前处理布局改变

ResolveOverlap 当前在检查 tile_mode 不同之前已经用 guest_size <= cached.guest_size 返回旧图。因此相同大小的 tiled/linear 变化绕过后面的地址复用分支。必须在这个快捷分支之前区分：

- 同布局合法 typed view、mip/slice alias：复用现有路径。
- 真正新分配、CPU/raw-buffer 最新：按新描述从权威 raw bytes 上传，不从旧像素 CopyImage；只有满足未绑定、GPU 完成和原生命周期条件才能淘汰旧缓存。
- 旧图 GPUModified、raw bytes 已过时：不能直接弃旧图、不能直接 CopyImage（那是像素拷贝，改变了 PS4 同地址字节重解释的意义），不能设置干净或返回空纹理冒充成功。
- 部分 CPU/GPU 写入：不能用单个 GpuDirty 布尔值决定整图来源；保持现有未启用的范围实验关闭，先要求实际写入范围/子资源/代际证明。

项目并非完全没有 retile：TileManager::TileImage（tile_manager.cpp:168）已有 backend.DownloadTiledImage 快路与 staging+DispatchTiling 备用；DetileImage 可按新布局解释同 raw bytes。可复用既有暂存池做 GPU→raw→新图，有界、流内有序、按 mip/layer/支持格式逐项验证。不要把 TextureCache::DownloadImageMemory（只直接 download mip0 linear）当作任意 tiled 全资源写回。

CPU-only 新资源可先做受限修复，但必须保证 GPU-only/部分写入路线明确保持原数据，不能把它们悄悄落入 CPU 上传。本文不提供一个会默认丢失 GPU-only 内容的可应用补丁。

## 4. 后续最小测试矩阵

已有 real-Metal 七项：相同布局、sRGB alias、合法一行 1D/2D alias、Unmap 后新 UID、memo/exact/generation 三种布局错选。候选谓词 26 项通过，仅证明分类。

修复前还需：CPU 完整重写后 layout 换址/同址、旧 GPU 非均匀图案且 CPU bytes 过时、mip/layer 子资源、部分 GPU buffer 写、保留/更换分配代际、同时绑定两种合法视图、proxy/velocity/depth 受保护角色、待完成 GPU tick 与 usage promotion。以旧/新 raw-word oracle 比较所有像素，不能用统一色块掩盖 detile 错误。

没有实测修复库、没有真实游戏白模现场、没有性能或内存收益数据，因此不应默认启用修复或称白模已解决。

## 可审查的最小 patch

layout-selection-diagnostic.patch 是未应用、未编译的最小观测差异：复用同一候选谓词，只在 BB_ARM_LAYOUT_AUDIT=1 时、相同 shape/字节数而布局不一致的选择中记录最多64条；没有改变资源选择、dirty、上传、复制或回收。用于先确认真实场景是否命中。它不是完整修复，不能宣称消除像素错误。实际行为修复必须完成前述 CPU/GPU 权威源分流，否则我不会给出可能丢 GPU-only 数据的“修复”补丁。
