# 本机 Metal 离线库：隔离实现与实测（2026-10-25）

## 交付与实际实现

交付建库代码，不分发开发机的游戏MSL、AIR、metallib、PSO归档。所有请求和产物保留在用户本机隔离GPU缓存下。当前仅显式开发CLI启用，普通QA包、正式应用和Release不更新；新checkout自包含clean-build尚未验证，工具依赖已有隔离构建元数据和本机已安装Metal Toolchain。

实际BuildLibrary在现有复制合并/顶点数学/片元uniform等重写后，对完整有效MSL、entry、metal3.1、Safe/Relaxed数学和Fast FP32产生SHA键，并绑定兼容cohort。原native编译接受源码后记录请求；非渲染线程外部工具串行执行固定xcrun metal→AIR→metallib命令。编译器/链接器/xcrun实际二进制SHA、版本与OS构成build_id。无任意输入flags或shell。首用缺少完整有效源时明确收集未知项，不谎称全游戏已离线准备。

加载核对key/cohort/source/options/build_id、严格整数schema/size和库SHA；失败仍走原源码路径，不跳绘、不删特效。现有每模块function、binding/reflection及PSO身份独立。运行期不启动CLI编译器，命中普通draw缓存不扫描/散列库。默认OFF不做AOT文件IO。无缩小缓存或新增强引用驻留memo。

启动入口--local-aot record/load要求显式验证过的私有ARM GPU候选。load在游戏启动前准备已知库；失败/锁屏/严重压力不启动游戏。编译阶段和游戏阶段均用原MemoryPolicy；仅重置短突增历史，累计swap基线取两阶段较小值，防止分阶段扩大原2GiB总增长预算。首次不存在requests只标收集，不能把没有输入当成功预编译。

## 已执行

首个实机性能候选GPU SHA 1ba32725a5521b7828d023c144b73831ce0e5cd0fe3961b4680ecdc1493d07e1，由一致230单元基线只重建3个实现单元，公共头/类布局不变。

- 26项实际文件/伪compiler/Popen控制：固定argv、路径/身份、OFF零工作、timeout/保护、原子发布、成功pair不重写、失败保留，真实非零命令诊断保存在私有日志；不是GPU/工具执行证明。
- 实际xcrun本机生成4个作者库，随后独立进程加载；16项原生库准入/真实GPU整数与精确float读回通过，包含错误schema、source/entry/math/build/digest/size、缺失/损坏库与正确源码回退。真实GCN→IR/SPIR-V→BuildLibrary记录/采用通过，原SPIR-V 623deccc摘要保持。第二次准备4个verified-skip，确实不重编。
- 6项实际启动策略控制，包括两阶段总内存保护；旧9组着色器/缓存/归档与24类纹理GPU保留控制通过。

## 真正游戏数据（仅本机）

固定同一隔离存档、空输入、镜头、候选、启动补丁及最终设置；native1920×1080与现有特效不变，SDL零增益、无Cua/录屏。四轮各75秒完成，无保护中止。

游戏模块538次调用收集为428个不同有效源/选项请求，源共6,602,992字节。本机初次串行建库约379.6秒（manifest到最后产物mtime范围），metallib合计3,901,743字节。此时间不含初始工具身份查询，不把它当整段启动或精确编译CPU时间。第一批编译实际峰值进程内存没有录入，仅有连续系统保护和阶段前后记录，不能宣称内存峰值零增加。

| 阶段 | 记录源码 | 首次IR载入 | 关闭AOT源码对照 | 暖IR载入 |
| --- | ---: | ---: | ---: | ---: |
| 模块库累计ms | 1311.405 | 863.163 | 821.081 | 856.158 |
| AOT模块实际命中 | 0 | 538 | 0 | 538 |
| 最终PSO累计ms | 147.144 | 5841.210 | 227.795 | 206.193 |
| 最后30秒FPS | 49.31 | 49.02 | 51.49 | 49.48 |
| 合计footprint中位MiB | 5928.20 | 6150.58 | 5792.54 | 5882.40 |
| swap变化MiB | +248.875 | +90.875 | +387.25 | 0 |

记录轮含捕获IO，不能当启动时延基线；首载受驱动缓存冷热影响，不是受控清缓存。两次load均538/538模块命中、零模块source fallback；暖轮428 verified-skip、零重新离线编译。全部末30秒无新编译。结果仅短站桩和稀疏次序，未证明稳定FPS/卡顿/内存收益，保持实验关闭。合计footprint会重复计共享页，不能当独占物理RAM。未进行新区域/白模、死亡/在线/菜单验收。

关键限制：离线metallib仅省源码建库这一层，仍须最终设备PSO；首次IR的5.84秒PSO代价不能忽略。暖库856ms对源码821ms也没有已证实优势。本机预编译不是分发成品，更不是所有Metal调用已离线化。

## 初次审查记录与后续覆盖项（安全修正见下节）

初次性能候选1ba32725的审查发现：entry创建失败的回退、记录时机、根目录相等拒绝和中断残留恢复需要加固。这些问题已在当前导出候选中修正并完成定向验证，结果见下节；原性能表仍只代表初次候选。没有证据认定它们是实测白模根因。

全Metal树共18处Source API调用，当前只接模块入口。HostKernels/tiling/passthrough/呈现等仍有直接source，某些有function constants或nil/default options，不能擅自改Safe/3.1或无constant函数工厂。GS debug helper仅显式debug开启时使用，不按静态数量推断运行时耗时。后续继续完整组合及最终二进制准备，不能用538模块命中代替全进程零源码调用。

正式11671个既有文件逐个大小/SHA不变。所有游戏源/编译产物都留在Shared私有state，不进入Git、应用包或Release。只同步源码、作者夹具与统计。普通手动QA包仍旧版本，不将这个开发实现冒充已安装更新。

## 开发复跑

```sh
python3 tools/build_warm_archive_candidate.py --local-aot
python3 tools/run_authored_controls_guard.py local-aot-authoritative-supervision python3 tools/run_local_aot_authored.py
python3 tools/run_authored_controls_guard.py local-aot-existing-regressions python3 tools/run_warm_persistence_regressions.py --candidate evidence/local-aot-candidate-current.json
```

真实游戏只通过原隔离run_game参数与已验证--gpu-candidate使用--local-aot record/load；不指向正式缓存。builder可独立显式--enable --root <私有GPU目录/local-metal> --cohort <64hex>；默认不开启，没有扫描或散列。首次工具缺失、源码不完整或失败不冒充成功。


## 安全修正后的当前导出版本

上述性能表使用1ba32725。当前导出GPU SHA a24a0569985495d79a78d02502052037517232f2ef92c4629916bd264e0e0f38，三实现单元重建、公共ABI不变；不能把旧性能表改标新版本。

已应用并验证：磁盘库必须真正newFunction成功才命中，并把该独立函数交回模块；源码也在entry成功后才记录请求。弱memo函数失败直接编译对应有效源码，不重复尝试同artifact；失败重写仍保留原源码回退。发布采用Darwin RENAME_EXCL，既有成功文件不覆盖；拒绝AOT根等于GPU根，并在创建每一级目录前核对规范化/所有权，静态软链接不会先写target再拒绝。

构建器只读辨认受限旧tmp与同内容键族的可核对内部旧hardlink。完整pair继续，orphan保持partial而非虚报passed，外部link仍拒绝；不删改任何请求或成功产物。非敌对并发条件下验证，不冒充目录被恶意并发替换的完整安全证明。

新候选26构建器、9只读恢复、6启动策略通过；实际BuildLibrary的fake-entry/memo失败与根相等/软链接负例通过（真设备能力初始化，函数工厂mock，不提交fake GPU命令）。另重新执行真实本机4库构建/16类原生准入和整数/float GPU读回/真实GCN→BuildLibrary采用/再次verified-skip。旧9专项及24纹理保留均通过。最终候选默认AOT关闭下的75秒隔离游戏进入场景、按时正常退出、无保护中止。

最终加固版AOT游戏性能未重测，不默认启用；428个旧成功游戏产物完整保留。18调用点覆盖表明确还有其它直接source工厂，不擅改nil/default math与function constants。下一步优先最终PSO及完整组合，新IR路径保留实验性；正常QA/正式应用/Release仍不变。

## 后续证据修订

归档联用审计发现旧测试runner的24纹理一项误指warm-stage旧库。上文历史24项不是当时新AOT库的覆盖证明；详见local-metal-aot-archive.zh-CN.md。修正后的本轮24项已核对实际加载归档来源修复库3e6d7e83。旧性能表与其它实际准入测试保持原版本边界。
