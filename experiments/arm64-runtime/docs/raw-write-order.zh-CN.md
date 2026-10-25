# GPU raw 写入顺序与纹理失效：诊断检查点

2026-10-25。仅隔离开发版；没有修改正式应用、存档、设置、缓存或 Release。

## 已证实的范围

实际 TextureCache::InvalidateMemoryFromGPU 只扫描写地址首字节，且只标记等基址 image；内部偏移、从前方跨入及覆盖第二张纹理会漏标，零长度反而标记。五个失败用例包含一次重复控制，对应四种缺陷形态，不能说五个独立根因。两组各19个真实 Metal 控制均验证了预期结果，报告保留 target_expected=0/实际exit=7，不把已知缺陷包装成通过。

GPU-only 自造数据验证同一提交中“旧image导出→partial raw写→detile/upload”保住旧texel和新写入。反序晚导出丢255字节；只刷raw丢16073旧GPU字节；旧CPU源和像素级不同layout复制也分别失败。只有最终观察才Finish/读回；这不是实际BufferCache/所有producer已经接入的证明。

## 真实游戏观察

只使用原启动脚本、隔离存档副本、静音、有界pad-file动作和既有保护；无Cua、屏幕/游戏图像抓取或FPS采样。输入请求不等于命中敌人或复现用户白模。

第二轮正确限额候选17.01秒到场景、观察约65.48秒，16条重叠记录、每probe最多4张；12条sampled、2条storage、2条两者兼具，其中4条SafeToDownload。这里记录的是可写binding覆盖范围与旧image状态，不证明shader实际改写每个字节，也不证明这些缓存image仍会被后续采样。仍需GPU生产者/读取者关联，不能直接认定为战斗白模根因。

第二轮pressure样本1级65次、2级178次、没有4级；swap 8806.125→8863.625 MiB，峰值8895.625 MiB。退出143由“private action trace completed”主动收尾，不是闪退。首轮16.72秒到场景、swap+972.5MiB、同样无4级；其每probe限额未兑现（单probe53条），已保留原证据并修正。最终候选采用独立命名有界visitor，增加10-image控制，两组19例及第二轮实际运行均核实最多4条；旧版全局64条/256probe上限仍未超。未充分定位旧限额失效的编译链接原因，不把推测写成已证实根因。

记录上限256 probes/64 rows/4 images/16MiB范围，精确1启用；实际游戏还经过原诊断gate。跳过的大范围、缺少失效通知的Fill等不在本轮覆盖内。正常路径不开启此日志，不保留额外图像或缩小缓存。

## 下一步实现门槛

不能简单把所有overlap设GpuDirty：部分raw可能没有未修改区的最新GPU图像内容。必须在raw写之前汇合旧image，证明完整raw容量、同一镜像/映射代际、padding基线、成功录入、后续fullrange GPU有效性和锁序，再允许所有overlap失效。多张GPU权威别名、paged/mixed/跨chunk、CPU fast copy、storage反馈和unsupported layout先明确拒绝候选路径；不能静默丢数据或扩大分配兜底。详见raw-authority-review/prewrite-design.zh-CN.md。

Mlock helper的独立C控制已编译执行，零物理锁页；实际接入草稿复核发现部分映射失败后元数据一致性缺口，仍未应用。owned shader输入/上下文草稿也尚未构建或调度。本提交只含本诊断与自造控制，不混入这两组未审完的并行草稿。

正式基线列出的11671个既有文件长度/SHA逐项相同（多数缓存，不是11671个存档），16份原启动脚本相同，QA alias已恢复，未残留游戏进程。保护规则不变；该短诊断不能证明长期内存/FPS等价。
