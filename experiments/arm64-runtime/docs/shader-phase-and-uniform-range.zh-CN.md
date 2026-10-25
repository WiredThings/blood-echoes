# 着色器分段计时与源码后缀复制（2026-10-25）

## 范围

仅隔离 ARM64/FEX 原型、静音、原画质和设置。正式应用、存档、设置、缓存与 Release 不变。用户明确授权只在游戏未运行时使用一次 Cua 收起 Codex；AX 复核窗口已最小化/隐藏后服务停止，三轮游戏不使用 Cua、桌面截屏或录屏。仅原生 present 两次读回，放在测量门关闭时。内存、快速交换增长及锁屏保护保持原规则。系统驱动缓存没有人为清除，所谓冷缓存只指全新私有应用缓存。

## 新证据

- 冷缓存 `launcher-engine-abba-1792890413212958000`：19.63 秒进入场景。GPU 5874 行完整无丢失；264 个模块的完整身份/返回/三阶段区间均可证明。GCN→IR 合计177.61ms、IR→SPIR-V12.44ms、模块后端799.25ms；这些累计量跨模块，不是单帧耗时。另25个后端调用没有GCN模块包围（如预加载），独立列示。
- 冷缓存最长模块57.831ms：GCN→IR34.217ms，SPIR-V0.407ms，后端23.159ms；其中驱动建库1.117ms。不能把全部慢编译都归因驱动。
- 同一私有缓存下一轮 `launcher-engine-abba-1792890613514995000`：18.61秒进入场景。GPU3535行完整，2个新模块、311个没有GCN模块包围的后端调用。后者包含预加载，不等于311次重新翻译。
- 热缓存路线相对ready+135.53秒出现新片元`37a8af3d`，模块246.757ms：GCN→IR2.981ms、SPIR-V1.433ms、后端241.926ms。其中驱动新库231.764ms，随后同线程PSO83.669ms。它与338.253ms帧完整相交，CSV当帧GPU17.92ms、compiles=2；游戏Gnm提交等待337.288ms，同帧FEX block合计仅0.407ms。无图像读回交叠。这是本轮首次内容编译造成长帧的强时间证据；不代表所有卡顿同因，也不能把嵌套区间相加。
- 两轮CPU环均因启动记录覆盖早期历史（8748/7937），不得据此对整个启动CPU排名；GPU未覆盖。模块统计只对可证明的完整发布区间成立。
- 两轮最高内存压力warning、结束normal，系统swap分别+1016.63/+410.19MiB，未触发停止。系统swap变化不等于游戏新增内存或泄漏，不能用于宣称内存收益。
- 路线涉及AI和碰撞，不是确定性回放。独立原生帧看见材质/毛发/血液与战斗，但未获得用户所述整身白模的清晰复现；仍不能宣布白模已修复。

## 诊断实现和验证

`BB_SHADER_PHASE_AUDIT=1`默认关闭：记录模块完整程序hash、stage、当次permutation以及GCN→IR、IR→SPIR-V、后端区间。模块准入后子阶段锁存记录，避免门关闭使耗时变成假零；异常不标记正常返回，句柄非零不等于画面正确。沿用每进程固定1MiB环，不记录shader载荷、不扩缓存。BBAUDIT枚举78–83不是进程退出码，原75/76协议不变。

- 7项分析负例（丢阶段、错线程、重复锚点、交叠等）通过。
- 7项真实原生trace控制通过，覆盖门控切换、默认关、无文件、异常和非法元数据。
- 自造三条GCN字的真实 PipelineCacheCore→IR→SPIR-V→Metal库正控：探针开关的SPIR-V逐字节一致，两个permutation均完整，实际库/架构/签名准入通过。可重跑 `tests/test_actual_shader_phases.py`，之前漏设私有GPU目录的失败保留，补齐私有state后通过。
- 编译阶段探针只重建诊断库对象；正常CPU仍09af59d2，正常QA包未加入诊断。

## 小优化：片元参数改写不再逐访问复制源码后缀

只将锚定regex的输入从`code.substr(position)`改为原字符串同一迭代器区间，不改表达式、flags、地址空间准入、4KiB边界和生成源码；没有新增常驻缓存。对同一后缀的所有组捕获仍保持原始内容。

`fragment-uniform-range-controls-1792890891012685000`：35份自造正负例和332份独立诊断MSL共367份，约5.63MB；105份适用，全部输出与原实现逐字节相同。ABBA主机微测原677.901/682.966ms、新672.867/671.552ms，约1.2%降低，只是源码处理成本，不是FPS收益。真实Metal沿用原有24组像素测试（线性/sRGB、mip过滤、repeat/mirror、偏移及非法源回退）逐字节一致。

诊断候选`present-image-candidate-1792890975879404000`已串行编译，额外重建mtl_shader对象；真实GCN→Metal开关正控再次通过。它保持两级地址表、速度图、遮挡、生命周期、0038/0039/5133、原网络/launcher代码；本轮不把未执行的速度图/在线场景称作live验收。正式/正常QA包均未替换。

## 下一步

新诊断库兼容冒烟 `launcher-engine-abba-1792891081374792000` 已通过：21.83秒进场、GPU3812行完整，门控中0个新模块/321次无GCN包围的后端调用。同一程序hash `37a8af3d` 在启动预加载中后端总scope为0.206ms；这不是完整特化键相等的单独证明。ready之后保留的1912个门控frame中最高87.538ms发生在ready+0.13秒，之后门控最高27.424ms。没有再次观察到该338ms游戏内尖峰，不等于所有长帧已修复。最高warning、结束normal，系统swap+384.06MiB；源QA种子未改。最终原生图仍能辨认衣物、毛发、血液和环境纹理，未获得明确整身白模现场。新旧两轮同时缓存变暖且AI路线有偏差，禁止当优化FPS A/B。正式11671个文件快照与保护基线完全一致。

下一步依据已明确的`37a8af3d`首次模块+PSO长帧，核对有界管线准备的时机、完整键、资源快照和同步归属。现有DrawPreparation仅选择已存在模块，源码明确workers不创建/编译；不能简单多开线程或跳过draw掩盖首次开销。优先借已有持久化记录与完整特化条件提前准备，不缩缓存、不扩并行内存，不放松校验。

白模、25个未接导入和独立clean-checkout仍未完成；不以本次编译诊断替代这些验收。硬件TSO仍未获得可用准入，保留软件排序。

## 可复跑入口（仅准备好的隔离开发主机）

```sh
python3 tools/build_present_image_candidate.py --range-audit --shader-phases --uniform-range
# 先用 build.json 中的 candidate 链接 fixture，再验证实际装载
python3 tools/build_first_upload_test.py --suite shader-phases --library /absolute/private/candidate.dylib
python3 tests/test_actual_shader_phases.py
python3 tests/test_shader_phase_analysis.py
python3 tests/test_shader_phase_audit.py
python3 tests/test_fragment_uniform_range.py --pixels
# --cache 只接受 state/gpu-diagnostic-* 直接子目录，不允许正式缓存
```

源码导出不含私有缓存/MSL/GCN或画面；微测和像素正控本身只需自造输入。实际库、签名和Mach-O装载边均受准入核对。正常本机QA包仍为冻结CPU09af59d2/首次上传修复GPU，未把诊断库默认塞给用户。
