# RuntimeInfo特化键修复：真实编译、缓存与游戏对照

2026-10-25。Shared隔离开发版；静音、无Cua/录屏、无角色动作。正式应用/存档/设置/既有缓存及Release不改。此项是正确性修复，不冒充白模根因或性能提升。

## 已复现的具体问题

HwGeometryRuntimeInfo把num_invocations当双方非零判断，并忽略in_vertex_data_size/out_vertex_data_size；HwComputeRuntimeInfo忽略shared_memory_size。实际编译会消费这些字段，不能用遗漏字段的谓词判断模块可复用。

作者IR经真实InfoCollection/SPIR-V emitter验证：1024/2048字节LDS产生256/512项U32数组；GS Invocations1/2完整保留，Metal Analyze接受1并明确拒绝2；input ring28/32产生6/7个输入location。真实ParseCopyShader解码三个自造buffer-load/EXP映射，RingAccessElimination在output ring4/8时把同一store映射到Param0/Param2；低于同一copy floor的1/2对照保持相同。实际LDS→storage pass的workgroup字节步长也随1024/2048变化。1250次断言包括SPIR-V逐指令span检查，不能叫1250个独立场景；没有在此提交GPU命令。

真实Storage/LoadPipelineStage与ordered/worker GetProgram在旧3e6d7e83库检出六个预期错误：四种合法新规格的磁盘预载误作陈旧索引跳过；ordered CPU共享内存规格变更仍返回旧module；worker把未知共享内存规格也当成已知。LoadStage的索引检查确实挡住了部分错误复用，但代价是合法缓存漏载，且它无法修复有序线程的旧模块误命中。此测试的backend module factory只计数并核对有效SPIR-V头，实际编译消费者由上一组独立证明。

## 修复与兼容

只将上述四个字段纳入精确比较；不新增mode比较，不以session host地址区分同内容copy shader，不重解析guest资源或扩大每次绘制校验。GS0的自反性测试不授权发出非法Invocations0；Metal现有Invocations>1拒绝仍保留，不用错误的旧1模块掩盖未支持功能。

实际vendor header已改。全部230个GPU编译单元在新的evidence目录重建；不混旧对象，不改旧库。固定GPU SHA ec7742a66c5d3ef4ce4858414aecc558ea9f7b2141c596f6e61e287b3fe6aa46；源快照与旧3e6d7e83逐项比较，GPU树中唯一变化为runtime_info.h。编译器导出的所有尺寸、偏移、字段类型均与旧布局一致；SPV10/meta7/key5和既有profile/核心namespace保持，不清缓存，不缩容量。实际GPU库SHA自然进入实验归档兼容身份，不导入开发机产物。

新库实际缓存21项检查零失败：不同spec创建不同module、原spec仍命中、准备线程未知规格只回退且不编译。编译消费者、九组既有回归、19个真实PSO归档子进程及本次SHA核对的24项纹理保留通过。28项三头文件比较是历史独立负例，不替代实际库验证。

上游对照：本次读取的[shadPS4 main runtime_info.h](https://github.com/shadps4-emu/shadPS4/blob/main/src/shader_recompiler/runtime_info.h)已将GS调用数改成精确比较，但仍没比较上述两个ring大小及compute共享尺寸。该main链接是可变引用，不能把它当已钉住的上游commit；没有整文件搬入上游的其它结构/bitfield变化。

## 固定场景对照

六轮A1/B1/B2/A2，再加B3/A3暖态复核，各75秒。A=旧3e6d7e83，B=ec7742a66c5d3ef4ce4858414aecc558ea9f7b2141c596f6e61e287b3fe6aa46；同一QA检查点、镜头、补丁、原1920×1080画质/特效、CPU与SDL零增益音频策略，两种库分别克隆同一个QA缓存。AOT和PSO持久化均关闭；不清系统驱动缓存。对照只证明此站桩条件，未验证探索/战斗或白模。

| 轮次 | FPS | p99 ms | 合计footprint中位MiB | swap净变化MiB |
|---|---:|---:|---:|---:|
| A1 | 49.76 | 21.35 | 5884.0 | -8.0 |
| B1 | 48.99 | 22.01 | 6132.5 | 0.0 |
| B2 | 49.84 | 21.36 | 5921.4 | 0.0 |
| A2 | 48.97 | 21.44 | 5898.4 | -8.0 |
| B3 | 49.55 | 21.46 | 5867.0 | -16.0 |
| A3 | 48.96 | 21.35 | 5858.4 | 0.0 |

平均FPS 49.23→49.46，基本持平，不是收益证明。全部538模块调用、520 PSO创建，末30秒无新编译；501份MSL文件逐个名称/SHA一致，语义解码的核心缓存目录/元数据数也一致。没有为该站桩额外生成着色器变体。初始化PSO最长约75–166ms，新旧都有，不能拿其累计时间当玩家入场时延。

平均合计footprint约5880→5974MiB（+93MiB）。B1偏高，B2/B3回到接近或低于基线；统计范围和平均值保存在JSON，不能只丢掉B1宣称零成本，也不能凭它认定泄漏。六轮没有保护中止或正向swap增长，最高压力2；未发现持续额外压力，但短站桩不证明长时间探索内存中性。合计footprint重复计共享页，不等于独占物理RAM。

## 边界与后续

历史38条compute metadata的LDS尺寸均0；已知3条GS metadata调用数均1、同程序ring尺寸未变化。因此这些真实负例不能证明它们就是偶发白模根因。严格键是必要的复用前提，仍需查原生资源创建身份、释放/重用及live payload读写回保护。没有跳绘、屏蔽格式错误或为了性能伪造正确性。

F3、死亡重生、在线和菜单live验收按用户要求暂缓；前三类context接口、首见捕获/异步采用及完整组合准备、clean-checkout和普通QA包更新仍未完成。正式与普通QA应用不替换，不发布Release。再次逐个核对11671个正式既有文件大小/SHA全不变。

## 复跑入口与兼容性补充

全量候选的本机建库兼容回归也通过：真实生成4个作者库、16项准入/真实GPU输出、GCN→BuildLibrary及二次skip4；核对实际Ec7742a6库，未默默使用旧local-aot候选。新增--candidate显式选择，普通默认保留原有开发工具行为。

```sh
python3 tools/build_owned_shader_candidate.py
python3 tools/run_authored_controls_guard.py runtime-key-regressions python3 tools/run_runtime_key_regressions.py --candidate evidence/owned-shader-gpu-build-current.json
python3 tools/run_authored_controls_guard.py runtime-key-aot python3 tools/run_local_aot_authored.py --candidate evidence/owned-shader-gpu-build-current.json
```

当前header已改变，需要上述完整230单元重建；旧三单元增量工具绑定旧coherent baseline，会按设计拒绝此header变化，不要把它加入增量白名单绕过保护。工具依赖已准备好的本机依赖/构建元数据，不冒充新checkout clean-build验证。所有库从本机用户资产/作者夹具构建；游戏MSL/AIR/metallib/PSO从不导出。
