# 白模资源路径并行审查（2026-10-25）

## 结论

发现一个可独立重现的真实纹理选择错误：**相同地址、extent、总字节数，但 linear/tiled 字节布局不同的合法纹理请求，仍会复用旧纹理；实际读回像素错误。** 这是具体正确性缺陷，尚不能认定它导致了用户战斗中的偶发白模。没有复现用户症状，也没有修改/验证修复后的运行库。

原正式工作树 engines/metal 中同类 exact/memo/overlap 条件也缺少这些布局字段；不是只在此次 ARM 桥新增的逻辑。这里只读对照源码，未执行或修改正式应用，不能替代已安装二进制归因。

## 实际控制

专属目录：本报告所在 white-model-resource-audit-1792904829213120000。独立测试 layout_identity.mm 复用现有自造映射/真实 TextureCache/Metal 夹具，链接既有 libbbgpu.avplayer-1792894220118812000.dylib，SHA256 a5005c5f807c861eddd50a31fcf0e64295a463aeff9d33e5097cddf504ab2783。不重建公共库，不启动游戏或窗口，不读游戏资源/现存缓存。HOME 私有，BB_GPU_USER_DIR 为 state 下全新同名测试目录。使用 8 MiB 合成地址空间、16 KiB 图像及小型 readback，结束 unmap；未新增常驻游戏缓存。

七项最终控制全部按预期通过（results-v4.json）；其中后三项“通过”是确认现有缺陷确实存在，而不是修复通过：

| 控制 | 结果 |
|---|---|
| 同布局重新查找 | 同 image，真实 bytes 相同 |
| UNORM→合法 sRGB typed alias | 同 backing，原始 bytes 相同 |
| 高度1的逻辑2D→1D、相同布局 | 同 backing/视图可创建，bytes 相同，保持 #5133 的合法用法 |
| 显式 Unmap 再查找 | 新 image_uid，重新上传，bytes 相同 |
| memo 命中，linear→Thin1DThin | 错选旧 uid1 / tile8，11904/16384 bytes 与正确独立 tiled 参考不同 |
| 关闭 FindImageCache，走 exact loop | 同样错选，11904/16384 不同 |
| 无关图注册改变 registry generation | still_exact 仍接受旧图，11904/16384 不同 |

故不能仅关闭 memo 解决。测试纹理为64×64 RGBA8、单 mip/layer，linear和Thin1DThin的实际 guest_size 都为16384。输入为非均匀原创 pattern，参考是在另一个地址用相同 bytes 按新 tiled 描述真正上传的图；Metal 读回逐字节比较，并确认参考不同于旧 linear 图，不只是比较描述字段。

候选 layout_candidate.h 的26项纯谓词正负例通过。没有接入任何缓存，不构成修复后的真实像素通过。构建、源文件 SHA 和执行时间保存在 build-v4.json、source-identity.json、results-v4.json。

初次运行因 GPU_USER_DIR 不在 state 子目录，被既有构造保护以78拒绝；第二次固定合成地址已占用，夹具 assert 中止。随后仅改自造夹具为 MAP_FIXED_NOREPLACE 寻找空闲区。失败日志保留，不改变保护，不把这两次当像素结果或游戏崩溃。

## 代码路径

以下均为 vendor/metal_api/shadps4 下相对路径；确切源码 SHA 已保存：

- texture_cache/texture_cache.cpp:57–73：SameDimensionalLayout 比较布局，但 CompatibleDimensionalLayout 对非1D↔2D直接返回 true。
- 同文件:711–729：memo/still_exact 未记请求 pitch/tile/array/swizzle；generation 改变仍可经 still_exact 放行。
- 同文件:752–783：exact loop 只检查地址/大小/extent/format/type，未检查完整 byte layout。
- 同文件:401–478：ResolveOverlap 同址路径，guest_size<=cached 分支先返回，后面的 tile_mode 地址复用检查对同大小情况来不及执行。
- image_info.cpp:154–209：真正上传大小/排布取决于 pitch/array/tile/pow2/mips 等，这些不是可随意忽略的描述文字。
- texture_cache.cpp:1139–1149：实际 detile 使用选中 image.info，因此选错 image 后即使 CPU 重新上传，也可能按旧布局读取。

## 代际与生命周期核对

1. 注册/注销增 registry_generation（texture_cache.cpp:1174–1202）；光明正大 Unmap 会先注销、后延迟销毁，已有控制显示新 UID，未发现这一基本路径的 ABA 复用。
2. DeleteImage:1802 起在原 GPU defer tick 之后擦除 view/image slot；仅凭延迟销毁存在，不能称泄漏或立即释放。
3. 原纹理绑定/target memo 均检查 registry generation；view memo还检查 backing，usage promotion切换 backing 并延迟释放旧 view（mtl_runtime.mm:2601–2707）。本次未证实 view memo 失效/释放过早。
4. CollectReleasedGuestImages:1550 起等待 GPU 完成、保护 target/proxy/写入角色，并在真正回收时以分配代际锁复核；没有依据缩缓存/放宽回收。
5. AssociateGuestImage:1539 只关联 owner tag；collector发现新 owner时重关联，并不自动证明像素内容已更新。地址代际与像素权威源必须分别观察，不能把分配tag变化当上传完成。

## 与已有修复的关系

0038 是推测资源格式“未就绪”拒绝与严格有序路径校验，本缺陷的两份格式都是合法 RGBA8，不需要弱化或移除0038。0039/#5133保留合法1D/2D统一2D backing，本次相同布局跨维正控通过；修复布局键不能退回强制每次维度变化重建。0039针对跨维的 SameDimensionalLayout 很严格，但尚未覆盖同维布局变化。先前首次GPU上传hash修复与本次问题独立；不能用任一离线控制宣称白模根因已定。

## 最小观测与安全推进

现有 CaptureTextureBinding（texture_cache.cpp:1673–1704）只接收 requested 地址，输出 selected format/width/height/uid/owner；**没有请求的 tile/pitch/mip/layer、selected同字段及选择路径**。因此“地址覆盖完整、partial GPU intent=0”仍不能排除本次选错布局。建议只在隔离诊断中、已有门控内记录最多64条不一致事件：owned T# snapshot及资源限定、请求/选中布局签名、uid/backing、lookup path、分配tag/当前owner、dirty flags、GPU tick；命中时再关联完整材质/程序特化。不要重新读取可能竞争的客体描述符，不默认逐draw落盘，不抓帧参与FPS统计。

候选最小差异与 CPU/GPU 权威源分流在 candidate-plan.zh-CN.md。**不能仅加一个比较后任由 overlap 走CPU刷新，更不能删掉旧GPU-only图。** 现有 TileManager::TileImage:168 和 DetileImage 可作为 raw 字节重解释基础，但需要全subresource与部分写入测试。当前没有已验证、不损画质/数据的完整修复，不建议直接改默认回收或图像复制策略。

下一步优先用现有实际现场门控确认是否出现本类布局不一致；并在独立候选库中完成 CPU-only 与 GPU-only 非均匀像素转换控制，再决定集成。没有新增真实游戏性能测试、FPS或节省内存结论。正式应用、存档、设置、缓存未读写/清理；源码未提交，由根代理选择整合。
