# 本机离线库与 PSO 归档联用：实测、修复及特化键负例

2026-10-25。只操作 Shared 隔离开发版；静音，无Cua/桌面录屏、无角色动作。正式应用、数据和Release不更新。交付本机建库源码与作者测试，不分发任何游戏MSL/AIR/metallib/PSO归档。

## 同候选五轮

前置capture及A1/B1/B2/A2各75秒，固定同一QA检查点、空输入、原设置与画质、启动补丁、镜头、CPU与GPU候选。实测GPU为a24a0569985495d79a78d02502052037517232f2ef92c4629916bd264e0e0f38；不是后续修复候选。四轮没有保护中止，压力最高2。

| 轮次 | 库路径 | 模块库累计ms | PSO累计ms | PSO归档命中/520 | 最后30秒FPS | footprint中位MiB |
|---|---|---:|---:|---:|---:|---:|
| A1 | 源码 | 821.877 | 23.812 | 500 | 48.86 | 5918.1 |
| B1 | 本机离线库首载 | 851.869 | 34.135 | 0 | 49.06 | 5891.2 |
| B2 | 本机离线库暖载 | 857.471 | 23.290 | 500 | 49.05 | 5916.6 |
| A2 | 回源码 | 814.108 | 36.260 | 0 | 48.58 | 6164.7 |

两次离线库均538/538模块命中，零模块源码回退。初次本机编译428个库；下一轮built0/skipped428。所有轮次末30秒无新编译。离线库平均约49.05FPS、源码48.72，约0.68%差别不足以宣称稳定收益。末轮footprint较高，不能据均值把离线库说成省137MiB；这只是系统/顺序相关波动，合计footprint也会重复计共享页。未覆盖新场景或白模。

同一真实GPU库/补丁/settings/archive token下，归档命中500→0→500→0。源码生成函数和离线metallib生成函数没有共享驱动归档命中；原代码却使用同一归档路径，下一次Flush替换上一种路径的结果。不能把驱动源码缓存本轮很热、PSO只需数十ms，当成此问题不存在；更早首载IR曾耗5.84秒，但本轮不能把该旧数值归因于此单个原因。

## 有界修复与真实验证

私有WarmArchiveLibraryOptions在实际PsoCache构造中加入已验证本机建库build_id。off/record仍为原source身份；有效load独立，toolchain build_id改变再隔离。每次仅加载当前一套归档，WarmUp后沿用原释放逻辑；没有加常驻库、缓存容量或逐绘制文件检查。多一套磁盘归档是预期，旧成功归档不删不迁移；本修复不保证所有20个剩余PSO都可被驱动归档命中。

修复GPU SHA 3e6d7e83df299f146ea4c61857651122095c9da345a5166b7f20050075cbec9a，仍从230单元一致基线只重建3实现单元，公共ABI不变。19个真实原生子进程控制通过：原source seed/load；真实source接受后本机record并调用metal/metallib生成作者库；从Data实际加载后local seed/load严格归档命中；再次回source仍严格命中。两种归档内容同时保持，真实compute整数读回与render/附件格式读回、ReleaseWarmArchive后执行、身份失配/发布失败等原负例继续通过。

另外重新运行真实4作者库生成/16项准入与GPU输出、实际GCN→BuildLibrary、二次verified-skip；九组回归及24项纹理保留通过。

### 回归证据修订

本轮发现旧run_warm_persistence_regressions虽然接受--candidate，纹理preservation一项却固定指向warm-stage旧候选。因此以前“该轮24项通过”只能证明旧候选，不能证明当轮新AOT候选。现在传入真实args.candidate，核对唯一新结果、实际library_sha256、bounds/24项结果；本轮确认24项实际加载3e6d7e83df299f146ea4c61857651122095c9da345a5166b7f20050075cbec9a。其它原生回归本已有actual load检查。旧记录保留，不把历史误指向说成当时已覆盖新库。

## 额外查实：RuntimeInfo特化谓词遗漏（尚未并入）

独立作者测试直接调用真实Hw/RuntimeInfo/StageSpecialization和ranges::find，不载入Metal/SDL或执行GPU/游戏，不访问任何guest指针。当前header28项中17项失败；只修geometry后剩4项compute失败；shadow header同时修GS与compute后28项通过。实际vendor header完全未改，修复候选仍不含此改动。

GS把num_invocations写成双方非零判断，导致1/2等价、0不自反，且忽略in/out_vertex_data_size。Compute忽略shared_memory_size。实际编译消费者使用这些字段：GS Invocations/输入接口/ring输出映射；compute共享数组长度、LDS转storage时workgroup步长、wave64 scratch起点等。不能把现有谓词当完整跨线程编译身份。

已保留copy hash及原语义、相同内容不同host地址仍命中，当前未消费mode不新增变体。现有快照没发现同GS程序改变这些寄存器的见证，所以不能认定这是白模根因。需要实际GCN/IR/SPIR-V输出负例、LoadStage/运行路径及完整一致GPU重建，再考虑纳入隔离候选；不通过清缓存掩盖问题。

第一次作者probe错误地沿用了GPU fixture链接依赖，触发Shared状态保护78且有重复SDL加载；该次不作证据。修正为只链接系统C++运行库后，三种header的28项结果如上，不绕过生产状态保护。

## 剩余

新候选已完成75.23秒source模式隔离游戏smoke，进入场景且按预算正常退出，无保护中止，压力最高2、swap净变化-8MiB；这不是新候选完整离线库游戏对照，不能把旧a24测量改标3e6d7e83df299f146ea4c61857651122095c9da345a5166b7f20050075cbec9a。主路线继续完整已知组合/加载期准备；其它直接Source API、首次未知输入、峰值进程内存、GUI、live payload以及first-miss scheduler仍未完成。实验AOT保持关闭；死亡重生/联网/菜单留用户统一验收，整个目标未完成。

本轮结束再次逐个核对11671个正式既有文件的大小及SHA，全部不变。
