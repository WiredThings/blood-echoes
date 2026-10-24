# 原生游戏帧与战斗正确性诊断（2026-10-25）

## 做了什么

复用renderer既有BB_PRESENT_DUMP_TRIGGER/BB_DUMP_DIR读回，不新增桌面录屏、GUI自动化或游戏退出协议。将原读回提取为可测试helper，保留默认旧physical-buffer模式；仅显式BB_PRESENT_DUMP_FINAL=1时读已完成的post-process present纹理。沿原scheduler完成tick等待，独立blit结束后读回，32MiB上限、2D单采样/支持格式/错误状态检查。行256字节对齐后去掉padding，日志发布实际Metal格式与垂直方向。

之前直接读physical显示缓冲会漏掉present view的swizzle：HP栏错误预览为蓝色。这是诊断颜色错误，不是游戏新出现蓝色血条。新模式读现有post-process结果并按present垂直方向预览，实际HP栏红色、原生中文“旧亚楠”可见。原始raw保留，PNG只作正确性观察，不含桌面像素，不作色度学或FPS证据。

普通冻结QA包GPU仍是既有first-upload修复，未捆绑此诊断GPU。只替换私有candidate中的一个presenter对象，正常应用/正式运行设置未改。诊断一次readback最多32MiB GPU staging，4K PNG预览有短暂Python内存；不是常驻缓存，也不能声称抓图无开销。

## 真实Metal与输入控制测试

present-dump-controls-1792885493630450000：18张真实Metal纹理，RGBA8/BGRA8及sRGB、RGB10A2/BGR10A2六格式各有CPU transfer、独立MSL render、翻转render三个控制。65×17奇数行，逐像素检查颜色、sRGB灰阶、方向与alpha忽略；nil queue/不支持格式/尺寸溢出/短文件/重复或缺失格式证明/符号链接拒绝。全部通过。未使用游戏材质做合成单测。

新增qa_image_action.py只接受私有owned ARM64 case，PID路径+进程birth/UUID校验、离线/不快速存档条件、owner/regular/link检查、独占输入锁；复用原pad-file接口，每步最多2秒，finally释放，最多8张帧。父监督器最多300秒且保留原压力/swap/锁屏保护。finish沿已有finish-request结束，原exit143为监督器终止结果，未引入新语义退出码。4项API边界测试、10项原内存规则与3项手动QA输入策略通过。普通手动QA包仍保留实体键鼠/手柄。

## 失败与完整性

- launcher-engine-abba-1792883537047823000：旧physical buffer模式3帧，颜色未校准。证实以前的turn/move攻击路线主要冲墙，不能称作实战。
- launcher-engine-abba-1792884078186688000：新final模式3帧，完成校准，但未见敌人，不算战斗复现。
- launcher-engine-abba-1792884779748910000：新外部短步控制导出3帧，到达外广场；没有及时finish，300秒时限退出。完整验收assert保持失败，没有放宽条件或改写passed。owned进程已清理，alias恢复；失败材料保留。
- launcher-engine-abba-1792885264374959000：3帧，短步控制+主动finish，全链路通过；未见敌人。门控始终关，trace0行是预期，不能称作完成编译诊断。

## 首次真正覆盖受击/攻击

launcher-engine-abba-1792885544308077000：从同一私有旧亚楠QA进度副本出发，共5帧，主动finish，通过。真实敌人已接触并击中角色，HP降到低位；No Dead仅此隔离测试配置启用。f003/f004分别见敌人攻击及后续血液效果，敌人毛发/身体轮廓、角色衣物与血液效果可辨，未观察到整身均匀白模。请求R1/R2并不等于已独立证明每次攻击命中敌人，但真实受击由图像确认。没有执行死亡重生、菜单或联网验收。

战斗门控CPU949/GPU201行，全部发布、无overwritten/dropped/clock reject。72个FEX block scope合计26.588ms wall/7.556ms CPU，最高11.999ms wall仅0.100ms CPU；这不足以把全部wall称作FEX编译CPU。窗口没有新的Metal library/PSO scope（低于探针阈值的操作仍可能存在）。唯一140.661ms长帧明确与最后一次图像请求相交，必须归为读回扰动候选，不作为真实游玩卡顿或性能回退证据。

进程正常清理，未发生重生；source QA seed哈希未变，正式数据未参与。此轮起始pressure1，退出pressure1，系统swap约+321MiB；只是诊断运行的系统变化，不代表常驻内存收益或增长归因。

## 尚未解决与下一步

没有复现用户的偶发白模，不能宣称first-upload hash修复已经解决白模。新诊断能避免再次把墙边攻击当作战斗，并可在出现异常时保留实际画面证据。下一轮应从本轮私有副本附近做有界真实战斗门控，图片放在门控之外；需要白模现场关联具体texture绑定/资源代际/有效跨度。不要为无复现而放松同步或清缓存。

长编译仍有历史200–326ms首次shader/PSO证据；此暖战斗窗口没有重现这些长编译。不能因此否定探索首次编译，也不能把本轮抓图的140ms算作新的编译尖峰。后续优先对同一私有路线拆首次与暖重走，现成弱library memo可作独立开关对照，不直接改默认或常驻全量归档。

所有raw/PNG、state、游戏资源、缓存和账号均不导出Git。源码checkpoint只含工具/自造测试与本报告。正式app和Release不更新。最近正常QA包仍是CPU09af59d2/GPUfirst-upload的signal-checkpoint，图像诊断不混入人工验收包。


## 后续：门控与读回严格分离的短窗口

launcher-engine-abba-1792886056747722000 使用上一轮私有正常存档的副本；不能假定游戏退出后存档会精确保留战斗位置。11.448秒门控结束1.072秒后才请求原生帧，最后沿原finish结束，44.18秒退出。无重生、联网或菜单验收，source seed未变。

CPU2657/GPU577条，完整无丢弃/覆盖/时钟拒绝。564帧间隔最高27.020ms，没有>=40ms长帧。FEX block192项合计45.023ms wall/34.343ms CPU，最高1.216ms；未对齐修正17次合计0.095ms，最高0.015ms。Metal没有library build，只有0.149ms单次PSO。这说明这个暖窗口没有数百毫秒长编译或信号修正尖峰，不代表新地图已消除首次编译。部分相同guest PC在不同线程分别生成IR，尚不能在不了解线程本地链接/失效语义时共享机器码。

最后图像前景被植物遮挡、没有明确敌人接触，不能将本轮称为成功重复上一轮实战或白模回归。静止攻击请求不等于实际击中；此处只证明门控与抓图时间已隔离、暖窗口的诊断完整。全进程whole-window CPU、嵌套scope和frame interval不可简单相加，也不作A/B性能增益结论。系统pressure1→1，swap约-16MiB。

新的自动化要求：从原有已校准路线重走到敌人区域，保留实际受击/接触证据；不得假定新的普通QA存档已经位于敌人附近。正式包/数据仍不变。
