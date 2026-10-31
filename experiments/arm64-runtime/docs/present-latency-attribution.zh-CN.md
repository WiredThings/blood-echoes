# 呈现长帧分项诊断（隔离候选）

沿用原启动器条件和run_qa_launch_benchmark，不另建前端。--runs3 --present-diagnostic-last 在同一QA数据根依次做冷/暖无诊断采样、最后一次诊断；最后一轮明确performance_sample=false，不能混算平均FPS。每轮仍保留严重压力、交换异常和锁屏保护，静音，不降画质/分辨率，不用Cua/截图/录屏，不注入移动/战斗输入。先完成代码/定向验证，再统一正常构建和游戏。

新增默认关闭BB_PRESENT_LATENCY_DIAGNOSTIC；闭合的4字节门控在正常场景摄像机被既有benchmark识别后才打开，避免加载期占满预算。只记录>=50ms阶段，每进程最多512条加一条overflow，溢出/错误/缺少frame origin均不作为完整诊断。无常驻ring或payload；normal原分支保留，完成计时合入原回调、位于最终退出计数前（8faf644d），只在诊断开启时读取时钟。诊断运行有时钟/门控读取/输出开销，不能作为性能结果。

request-residence=PrepareFrame后入requests至Flip入口；present-dispatch=RunPresenter调用前至异步lambda执行（包括派发锁/队列/调度，不是纯队列等待）；present-host含SetHDR/Present主机调用；next-drawable单独测CAMetalLayer资源等待；submit-to-completion为提交至完成回调，包括队列/GPU/回调调度，非纯GPU。CPU时间仅同线程scope可知，跨线程记录-1而不是零。每请求用ready_ns关联，原frame CSV steady origin用于时钟关联；token0是未关联重绘，不能强行归到游戏帧。

本候选没有删除/合并等待，不改变帧池/fence/附件/查询/原子内容归属或队列行为，兼容既有两级页表/速度图/遮挡与内存补丁。C++原生控制通过默认off、门控、token、真实慢scope、512预算、异常路径；Python验证诊断必须显式准入且仅batch最后一轮开启、已有benchmark控制保持。实际集中结果见下文；未证明整游戏性能收益或旧尖峰根因。


实际3485包五轮集中结果和8faf收尾修正边界见[fex-precompiled-present-validation-20261031.zh-CN.md](fex-precompiled-present-validation-20261031.zh-CN.md)。第三轮origin/门控有效，但无>=50ms事件，不能视为旧长帧已归因。新源候选的正常构建检查与旧包实际运行分开记录。
