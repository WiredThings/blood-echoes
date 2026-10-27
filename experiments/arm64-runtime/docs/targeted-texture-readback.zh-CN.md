# 定向采样视图与纹理读回（诊断功能）

## 目的与证据边界

白模和彩点曾出现在多种敌人及角色身上，不视为狗模型特有缺陷。本轮狗的可见／出画面各三帧，用六组网格索引数与静态模型目录交叉匹配，缩小到共用 BC 贴图和两组动态可写纹理。未发现空绑定、失效分配代号；这不能证明纹理像素正确，尚未确认根因。

此前入口只保存绑定元数据。新增入口复用同一抓取目录、触发和帧边界，不改游戏、设置、存档或缓存算法。选择器是运行时有界 JSON，不在代码中写死狗的模型、纹理 UID 或地址；其他敌人可以使用相同方法先定位绘制，再更换选择器。

## 使用方法

仅明确启用的 Shared 私有纹理诊断目录内接受 `texel-request.json`：

```json
{"schema":1,"vs":1038787955,"ps":[3793627194,1891320495],"indices":[19620,6378,58497,834,8109,6246]}
```

这只是当前狗样本的选择器示例，不是普遍白模识别规则。文件必须为当前用户的 0600 普通单链接文件，最大 4096 字节，目录为当前用户私有目录；不跟随请求／输出符号链接，不覆盖已有读回文件。与原有 `trigger` 一起请求，正常游玩没有 GPU 读回。

首个抓取帧消费请求，对匹配绘制的实际反射片元纹理绑定，记录统一槽位、MSL 槽位、Metal view／parent、实际格式、类型、mip／slice 范围、swizzle、usage 和 storage。完整纹理键和原有绑定、渲染 memo 不变。暂不支持的形状／格式明确记录 skip，不伪造内容。

在同一调度器内、相应绘制采样前，编码 texture→buffer 原始块复制。使用现有 BlitEncoder 的排序／通道结束逻辑；如果切开已有同一渲染通道，恢复为 load，避免再次执行已消费的 clear。保留 view 和 buffer 至 GPU tick 完成，适配该调度器的 unretained command references。帧边界 Finish 后核对 tick，再写私有文件并释放临时资源。

每次最多 32 个不同 view、16 MiB 总读回、每纹理最多 12 mip；同一 view 只记录该次请求的首次采样快照，限制命中会显式记录。BC1/BC4/BC5/BC7 和已支持的 32 位非压缩格式按原始块读回，不做颜色或 shader 转换；文件含 256 字节对齐行，`texture_probe_plane` 给出每层偏移和块布局。

`texture_probe_saved` 必须同时 `gpu_complete=true`、`written=true` 才是成功内容证据。原有 `texture_capture.contents_read=false` 仍指绑定元数据记录，不代表附加的 texel 文件。捕获会引入通道切换和 GPU 等待，不能拿这一帧做性能结论。

## 如何归因

先关联同帧的实际 native view，再核对其格式、父纹理及 mip／slice／swizzle。静态 BC 纹理的原始块可与对应游戏源和已知解平铺结果比较；动态可写纹理必须追其生成与复制，不能拿初始 CPU 数据当成权威。原始块正确也不单独证明采样正确，仍需核对 shader 输入及最终像素。单帧结束后才读原纹理，可能混入后续写入，因此本入口在采样前先编码独立快照。

若采样前快照导致问题暂时消失，要保留同步敏感的事实，不能宣称已修好。导出、命中计数、定向测试通过不等于真实白模已解决；不扩大新的故障假设，不以诊断为由回退既有优化。

## 验证

`tests/metal/test_texture_probe.mm` 调用同一个 reader 模板及真实 Metal Scheduler，使用自制数据，不加载游戏或正式缓存：整数→UNORM view、相对 mip、swizzle 元数据、多 mip 原始块、后续 GPU 写入不改变先前快照、BC1/BC4/BC7 尾 mip（Metal 验证层），以及禁用／非捕获帧／无匹配／无效 schema／请求和输出链接反例。GPU 测试数据必须位于原运行时准入的 Shared 隔离 state 目录，不放宽原目录校验。

所有游戏纹理、截图、捕获原始记录只留在私有诊断目录，不提交 GitHub、不进入 Release。真实游戏读回与白模归因尚待下一次现场验收。

## 2026-10-28：上一代 backing 的生成和复制链

已在用户确认故障仍存在时保存持武器村民的实际 view 和 44 个成功快照（两批，含重复 view）。一张 256×256、九层 mip 的共享材质纹理呈彩噪，两轮全部 mip 摘要稳定；view 范围、swizzle、用途标志暂未观察到准入异常。原因仍未确认。

物理对象升级用途后会复制旧内容，并从新对象重新计数。因此当前对象 upload=0 / copy=1 **不能**证明整个生命周期从未上传或生成，也不能据此选定 ExpandImage 或 promotion 为根因。

新的临时入口在明确启用的捕获目录内要求另一个当前用户 0600、空普通单链接文件 `lineage-enable`，启动时检查。仅针对已观察到的资源类别（256×256、单层、单采样、九个 mip 的 RGBA8 Uint/Unorm/sRGB），并记录必要的复制来源。正常游玩没有这个标记，入口不产生快照。

记录复用现有 Scheduler、MemoryStats 和资源身份；物理 uid、出生时间、分配 generation 与游戏 image_uid 分开，进程 pid 加 epoch 避免 F3 后编号或指针复用混淆。原有 ContentSnapshot 返回类型保持不变，不引入 ABI、退出码或存档流程变化。

覆盖上传 GPU 缓冲区在解平铺前／后各 128 字节、上传结果、用途提升的源／结果、CopyImage/CopyMip/经缓冲区复制的调用入口／返回、清除结果和可反射 storage texture 的计算写入结果。复制入口／返回不冒充某个 GPU 分支确实成功编码。实际 view 抓取同时携带物理 backing uid，供本次采样与历史关联。

纹理历史只取 mip0 左上、中心、右下各 8×8 的原始块，非完整内容。每进程最多 4096 项、同时 64 个小 staging buffer（最多约 384 KiB），payload 上限约 24 MiB，JSONL 再有 8 MiB 限制；只写私有诊断文件。资源在编码 unretained GPU 命令前已有完成回调持有，GPU 退休后异步写出并释放，不保留旧纹理作历史，不增加等待或缓存容量。诊断分配失败会停用并标记缺口，不应把诊断异常变成游戏渲染改动。

GPU 完成信号和 command-buffer retirement 是不同边界：定向测试等待后者并执行现有延迟回收，确认旧 backing 的 weak 引用最终归零，避免以零计数冒充已释放。上限、缺少源对象身份、不支持的来源或诊断失败均作为缺口报告。

原完整读回也补上了退休引用租约：仅 `Finish()` 等到 GPU 信号还不能马上销毁 unretained 命令使用的对象。快照容器预留容量，资源先持有再编码，并核对全部 mip 已编码才准入文件。此为验证层捕获的诊断工具寿命问题的修正，不是游戏白模根因结论。日志达到上限的反例继续验证原清除与复制像素正确，诊断失败不会转成渲染修改。

当前历史不穷举 render attachment 写入或 BDA/原始缓冲区写入。遇到 held/hoisted pass 的 storage 写入只报缺口，不能为了抓取改变 hoist 顺序。原始 CPU guest 内存不额外读取，CPU 摘要仅使用引擎已经计算的值。局部样本干净不等于整张纹理或全部 mip 正确；需结合现场完整读回确认。若抓取改变同步而使故障消失，只能记为同步敏感，不能当成修复。

`run_texture_probe_test.py --lineage --compile-commands <renderer/compile_commands.json> --library <built-library>` 复用原测试入口。`test_texture_lineage.mm` 通过真实 TextureCache、上传、清除、promotion 和复制验证已知字节、父子身份和释放，包含坏输入、pending 上限、关闭和链接标记反例。`analyze_texture_lineage.py` 只接纳已完成且成功保存的样本，并保留缺口，不输出“根因已证实”。
