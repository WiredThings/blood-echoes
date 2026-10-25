# ARM64/FEX 夜间进展与交接（2026-10-25）

## 范围与边界

目标是替换 Rosetta CPU 组件，复用 Blood Echoes 原启动器、Metal 后端与现有补丁。正式应用、存档、设置和缓存保持不变。仅使用 Shared 隔离配置、已有 QA 进度的新副本和私有 GPU 缓存；不降低分辨率/特效，不缩小缓存。

按用户最新指令，死亡重生、在线和菜单实测本轮暂缓，醒后统一验收；不把历史通过当作本构建通过。所有游戏测试静音，无持续录屏/Cua。严重压力、快速 swap 增长和锁屏保护未放宽。源码只推送 `dev/arm64-runtime-audit`，不更新 Release。用量任一相关可读窗口剩余低于15%即停止开发/测试，整理交接和最新测试包；最近可读窗口剩余41%，其它窗口不可用。

## 已修复并有定向证据

1. **退出协议冲突**：保留原75/76语义，仅显式监督重启路径能发出。缺失导入80、提交前诊断81、保留码误透传guest82/backend83。包括低8位折叠的331/332和负数都已防护。17项自制x86/FEX退出样例、6组原监督脚本，以及子进程回收回归通过。没有重写原监督器。
2. **ABI 生成器漏接原接口**：从 PAIR/DIALOG 宏名字白名单改成 Clang 显式语义 marker；普通、未知宏、嵌套宏可识别，host helper、无关注解、仅声明不会误接。16个原有DIALOG接口复用现有实现。当前343个包装器签名无unsupported；不等于全部导入已支持。
3. **libc完整跨度**：memcpy/memmove/memset/memcmp 在调用现有实现前证明全部访问范围，保留原写入准备/通知。19项真实FEX正反例通过。仅对已明确完整证明的参数去掉重复首字节证明，没有取消范围校验。
4. **真实纹理一致性错误**：GpuModified图像首次GpuDirty上传时没记mip hash，之后邻页写入保守置CPU-dirty时，会将未变的CPU旧数据覆盖GPU结果。真实Metal像素回读证实旧基线红像素255被覆盖成0x11；候选保住GPU结果，真实CPU改变和GpuDirty强制刷新仍生效。三项定向和既有四项扩展回归全通过。采用上游同策略，不增加hash存储或降低缓存容量。[上游实现](https://github.com/deadinside28/bloodborne_pc/blob/8185b88/gpu/textures/texture_cache_upload.cpp)
5. **手动QA入口**：发现新生成入口又带着BB_ARM_QA_PAD_FILE，重新屏蔽物理输入。生成脚本已默认移除，新增拒绝空值/0值等自动化变量的3项回归；自动benchmark仍独立使用文件输入。此处是打包配置修复，不是重新实现手柄。
6. **打包依赖路径**：冻结CPU/宿主库/GPU时，复制后的SDL和旧rpath会同时加载；已统一包内依赖路径，解压后自制FEX样例无重复Objective-C类警告。外链严格签名问题也已改为复制运行依赖。

## 验证与性能

最新开发CPU SHA256：`88da6e9e425efcd51e89a3ef269cc25904b06f249775e92914ffdb1bbfa06344`。

- `suite-1792864806185609000`：117项无窗口回归通过。
- `libc-spans-1792864806283513000`：19项跨度回归通过。
- `exit-contract-1792864806537078000`：17项FEX退出样例、6组原监督器和回收回归通过。
- `first-upload-regression-1792863091096195000`：旧基线像素错误被确定复现。
- `first-upload-regression-1792863287573965000`、`1792863342008165000`：修复3项、扩展4项通过。
- `first-upload-abba-1792864209177601000`：旧46.85FPS，新47.38FPS（约+1.1%），仍在波动内，**不宣称提帧**；相同boot/content/patch/settings/camera。
- `first-upload-memory-1792864532896586000`：内核去重快照5626.93→5639.40MiB，约+12.47MiB；无更高压力/swap增长。先前聚合footprint多算了共享页。单对快照不是长期不增长验收。
- `launcher-engine-abba-1792864949670891000`：最新整合CPU+纹理修复固定场景48.9995FPS、GPU19.19ms、聚合footprint5797MiB、swap+76.6MiB；这是单轮，不能归因给span去重，也不能与早期不同二进制的ABBA混成因果结论。
- `frozen-qa-package-1792865967795256000`：最新zip解压后严格签名通过，4项自制FEX退出样例通过，包内Python运行架构arm64，无实际游戏启动。

## 白模：发现了相关真错误，尚未证明用户症状解决

两次血子弹特效纹理metadata捕获完整，选中图像地址全部覆盖请求；单次静态画面HUD+5证实动作触发，未见整体白角色。但用户的探索/战斗偶发白模尚未复现，不能宣称已修复。

已修正旧观察器自身在memo-hit时解引用被清空slot的错误；捕获只读owned prepared快照，不重新读取guest描述符。性能候选与捕获版严格分开，纹理修复候选只替换一个对象，不带捕获开销。

上游另有mirror trap重装/解锁竞态，但本路径Metal统计0镜像/0陷阱命中，未盲目移植为白模修复。旧PS4固定guest基址假设同样记录为休眠镜像路径风险，不因此启用镜像。

## 长编译与探索长帧：已分开两类证据

原人工探索曾有唯一新shader/PSO编译：VS最长326.6ms、FS209.7ms、PSO188.4ms。MSL源码缓存命中仍要newLibraryWithSource，不能把它叫预编译metallib。弱library复用减少110/538次API调用，但未证明累计时间或进场收益，继续保持实验选项、不默认启用。没有恢复此前已撤销的离线Toolchain功能。

本轮新建空**应用**缓存，再复用它跑相同的有界动作请求。原有QA/正式缓存和系统驱动缓存完全保留；这是空应用缓存+暖系统缓存，不是真正冷驱动缓存。动作按时长请求，不是确定性路线，不作FPS对照。

| 诊断 | 空应用缓存 | 随后复用缓存 |
|---|---:|---:|
| 场景camera首次出现 | 17.51s | 17.31s |
| 首次动作之后新增shader | 37 | 0 |
| 动作后shader总scope | 201.21ms | 0 |
| 动作后library总scope | 125.20ms | 0 |
| 动作后PSO数量/总scope | 40 / 19.06ms | 0 / 0 |

暖缓存启动前预载275个shader（全源码命中），但仍调用275次Metal library编译，总scope约469.8ms。冷应用轮有一个192.1ms的PSO发生在camera出现之后、动作之前，不能把camera出现当作完全可操作/完全加载。shader scope包含library，表内类别不能相加当帧延迟。

证据：`launcher-engine-abba-1792865327869171000`、`1792865415904408000`。后者仍出现130.48ms帧：GPU20.41ms、绘制记录6.83ms、FEX编译重叠0.792ms（CPU仅0.019ms）、没有Metal编译。这说明还存在非编译长尾，不能归咎于长编译本身。普通cond_wait覆盖这段只说明有等待，不证明等待本身是根因。

补开有界producer/guest提交记录后，`1792865593487382000`未重现：完整trace无丢失/覆盖、1970帧最长26.58ms。**原因未定，不能当已解决。** 后续重点记录同一依赖链的生产者CPU、提交、资源页/失效等待，先复现再修改同步。

## 原启动器兼容性还剩什么

最新显式imports元数据审计 `launcher-source-inventory-1792865790099149000`：16个启动/汉化/存档/联网脚本全部逐字节一致；GUI36文件中28相同、8是已审阅隔离/架构准入/文案差异；36项Metal选项均有消费者，但消费者存在不等于收益已测。

实际762个所需导入仍有25项unresolved：16个AvPlayer（原后端实现存在，但guest回调/句柄桥未接）以及9项kernel/libc边缘接口。旧报告41项已过时，工具现在能显式指定新运行的imports元数据并记录摘要。不能拿343个可包装接口称完整兼容。

AvPlayer须保留原实现并正确桥接callback、allocator、opaque handle和线程退出；不能直接将guest地址当host函数，也不能用返回成功空壳绕过。剩余9项须区分原本也未实现、guest模块替代和真正漏接。闲置菜单触发缺失AvPlayer现在安全报80，不再重启循环，但完整播放兼容仍未完成。

## 长期研究结论

已按用户授权完成FEX特化subagent研究，见 `docs/fex-specialization.zh-CN.md`。FEX已经按需翻译；删掉未用ISA处理器主要减少冷代码/磁盘体积，不会自动缩短已经生成的热代码。没有证据证明观察过的指令集合覆盖血源所有场景。

长期目标：**版本绑定、保留完整通用回退的Bloodborne FEX/Metal协同层**。先有界统计热PC/指令/编译与执行成本；优先完整范围和资源代际下的重复通知/快照消除，以及完整shader/PSO键已知后的有界准备队列。不降低浮点/访存语义，不跳绘，不增大archive或缓存。

硬件TSO仍未通过系统签名/profile准入，保留软件TSO；用户同意不能代替Apple provisioning。每个实际执行guest的宿主线程都要成功准入，才能停用软件排序。

## 最新本机测试包

`artifacts/arm64-qa-20261025/Blood-Echoes-ARM64-Local-QA.zip`，约42.81MB，SHA256 `50d4823d60a7c22ef09d6212da52c9436b3835b03e8da51cbfc5671b9079ae8d`。

这是**本机隔离QA包，不是跨机器安装包**。CPU、CPU宿主库、GPU候选、原启动脚本和基础依赖已冻结在包内；游戏/汉化/QA配置路径和当前原型硬编码根仍在本机Shared。无游戏、存档、缓存、账号凭据、截图。默认静音、离线、物理输入，不持续采样。

源码快照同样不是原型的clean-checkout独立构建：候选仍依赖既有对象/依赖stage。已在README明确，不能拿正式构建自包含的结论套在此开发原型上。正式版本构建流程没有修改。


## 后续检查点（数据导入与最终包）

- 发现ARM原型把未知数据导入也当作Missing函数分配可读跳板，而原probe.c为数据导入保留PROT_NONE页。自制BBPROBE5样例证实旧CPU可把跳板字节读成数据，正常返回0。这处差异由ARM导入循环引入，源头原实现已正确区分。
- 现在每个未实现数据导入只保留一页不可访问VA，不预填/提交payload，不加入可读/可写guest范围。启用前发布只读范围表，信号路径仅write+_Exit80，原有显式恢复域的EFAULT语义保留。未使用导入不影响进场；NoExec仍通过FEX原错误路径返回非零，不产生75/76。
- 新7例：未使用、首/尾读取、写入、执行、合法data及普通missing函数；旧冻结CPU的读取错误控制仍可复现。新CPU正常场景进场16.97s、固定尾段48.19FPS，无保护停止。这是加载/功能回归，不是提帧A/B。
- `final-cpu-checks-1792868995161926000`：最终88da构建117无窗口、7项data、19项span、退出17+监督6、同包net路径10例全部通过。
- 冻结包网络模块已是arm64。原加载器只接受P/build，复制进包后本会被拒绝。现在额外允许与真实当前CPU处于同一`Contents/Resources/bbport/bin`组件中的模块；复用原启动器原位置，继续要求原ABI3入口，offline不dlopen。仅路径定向测试通过，真实登录/留言/圣杯/合作未重测。
- 最终包：`Blood Echoes ARM64 GUI QA 1792869097396418000.app`，zip SHA `be2a9dc1d2e0e41094c9906c8218ed473a3075f8b5567c402ac963d418a006b7`。`frozen-qa-package-1792869217393211000`验证解压签名/4项FEX/Python；`missing-data-regression-1792869425373087000`验证冻结CPU七例。冻结后的Mach-O摘要因相对依赖/重签不同于构建输入，两个摘要均在manifest保存。
- 新imports审计 `launcher-source-inventory-1792869426120406000`仍为25个未实现：24函数+1受保护data；“有保护”不是“实现了接口”。16脚本逐字节不变、36选项均有消费者。

## 新抓到的非编译长帧

`launcher-engine-abba-1792866665169598000` 在81s内抓到162.305ms长帧，两个trace完整，无丢弃/覆盖。GPU31.36ms、记录13.03ms；没有新Metal编译；FEX块重叠18.731ms但线程CPU仅0.035ms。IPC MsgWriteFault(4)调用67.456ms（CPU0.044ms），同时GPU IPC线程处理八个MsgSubmitGfx(20)，合计50.239ms、线程CPU0.210ms；不是这50ms都在跑代码。

周围系统出现大量compression/decompression，swap总量在该窗口不变（整个运行+378.9MiB，压力最高warning）。已新增只读VM/CPU全局计数，rev1结构152字节由本机SDK实际编译证明，Mach host端口有对应释放；计数是全系统，不能归因给某个进程。现有采样未给出每次缺页等待时间，不能单凭pageins平稳确认或排除内存活动造成的延迟。SDK新ri_page_wait_time_mach字段的当前内核语义/可用性尚未核实，没有将0当作无等待的证据。

下一步分开记录提交快照复制、DrawPreparation::Build、queue.m_access和submit_mutex。MsgWriteFault可能排在这些提交之后；只有缩短已确认的阻塞源才是优化，不宜单纯减少校验或随意合并fault。固定1MiB trace/64KiB CSV尾部，有>=80ms帧后自动结束并保留上下文，未提高缓存或保护阈值。

另记录上游对部分GPU覆盖的完整image-size条件。本地尚缺，但部分GPU写入的前缀也必须正确更新，不能只验证剩余texel不被覆盖就宣称整体正确；先构造全覆盖/部分覆盖/真实修改与保守标记的定向测试，再决定是否移植。当前未启用该改动。

源码导出worktree突然出现9180个带“ 2”后缀的未跟踪源码副本，约93.83MiB，来源未确认（部分内容与较早检查点相同）。没有删除、覆盖或加入Git；后续只能按SOURCE-EXPORT明确清单暂存，不能广泛git add -A，也不能宣称worktree整体干净。此处是源码副本，不是游戏存档。测试运行与构建仍在Shared。


最终冻结CPU/图形主体的原入口场景验证：`launcher-engine-abba-1792869508968741000`，17.24s出现camera，固定尾段48.80FPS、GPU19.25ms、聚合footprint5814MiB、最高pressure2，无保护中止。仍是单轮，不作严格提帧A/B。该记录及上一冻结包记录的旧gpu_variant字段写成baseline，但frozen_component已经包含真实固定二进制摘要；保留原记录，另有metadata-clarification解释为冻结的first-upload修复版，后续工具已改名frozen-component，不能混作旧基线。


最新测试包入口更新为 `Blood Echoes ARM64 GUI QA 1792869948191017000.app`，zip SHA `50d4823d60a7c22ef09d6212da52c9436b3835b03e8da51cbfc5671b9079ae8d`。其CPU/CPU宿主库/GPU/GPU库四个主体与48.80FPS实测包逐字节相同；仅把arm64联网模块也纳入fmt路径归一化/冻结清单，避免日后联网加载第二份fmt。`frozen-qa-package-1792870055920459000`再次验证严格签名、4项FEX与arm64 Python。没有新增真实联网测试。GUI基础版本仍0.2.51，身份以QA时间戳和manifest摘要为准；不是正式应用替换。


## 最新：局部GPU覆盖与宿主CPU干扰


## 真实Metal像素对照

无游戏数据、无窗口，64×64 RGBA8。CPU初始0x11，先用Metal把缓存图像清为红色；实际写入用完成后的Metal shared-buffer fill 0x77。只通知测试目标纹理失效路径，不伪造CPU纹理写入。BB_GUEST_IN_PLACE=0，故不能直接推定正式in-place路径也相同。

| 输入 | 当前首次上传修复库 | 仅增加完整范围校验的实验库 |
| --- | --- | --- |
| 零字节标记，无写入 | 错误重传旧CPU内容 | 保持红色 |
| 4字节保守写绑定，实际无写入 | 错误重传旧CPU内容 | 保持红色 |
| 真实首4字节写入 | 首像素正确，剩余GPU内容丢失 | 首像素漏更新 |
| 真实整图写入 | 正确 | 正确 |
| 真实内部4字节写入 | 漏更新 | 漏更新 |

当前库：66d554bde2f1692034983e4437e2ef9acb9171498a9b9ac82c9a1db3c1f3d596。
范围实验库：872c2442edea28cf5437e80c026ae9fedcfeb0545fc773cd1662528483948c5d。
证据first-upload-regression-1792873009080464000、1792873163425242000。完整覆盖的正控制均通过。第一次直接执行因缺少强制私有状态变量被78拒绝，未接触正式缓存；补上新建Shared私有目录后才取得上述像素证据。

结论：单一GpuDirty布尔值不能表达局部最新数据，整张刷新与整张保留都不是完整解法。范围实验只作负控制，不启用、不打包。下一步须区别“声明可能写入”与“已完成实际写入”，在保留代际和子资源的前提下确定合法数据源，避免未经证明的全图读回、扩大缓存或增加驻留副本。用户实战白模尚未复现，不能称其根因已确认。

## 非编译长帧

新增timing-only候选拆开提交快照、DrawPreparation、队列及通知锁，未改调度或同步行为。案例1792872081454932000在89.7秒内抓到94.053ms帧，GPU12.95ms，无新Metal编译；trace完整。附近一次1.06秒的粗粒度进程CPU间隔中Codex Renderer达223.5%，同时游戏进程有实际CPU活动。这是明显干扰线索，不能仅凭时间相关认定每次卡顿来自Codex，也不能把等待时间全算作FEX翻译。

单次ps每秒只记录top12的PID、可执行文件名及CPU增量，不采集命令参数、窗口、内容。新建/退出进程会遗漏，系统VM计数仍是全局，均不作精确每帧归因。非绝对路径kernel_task未列入。

此前1792870954720662000的一条HLE记录出现无符号负23微秒，解析器现在拒绝越界终止时间并标记trace不完整，不把它画成巨大等待。Scope::End源代码也增加回退/失败时钟保护并计入dropped；原生注入起点测试与正常/重复End测试通过。此writer guard尚未重建进CPU/GPU二进制；已冻结QA包诊断默认关闭，现有读取器仍防御旧记录。不能把解析修复当作游戏性能提升。

## 验收与边界

自动审批拒绝了启动临时Cua后台以最小化Codex窗口，理由为与性能测试不用Cua约束冲突。服务未启动、窗口未改。已提出仅测试前使用并停止服务的授权问题，等待答复；没有换AppleScript或其他GUI通道绕过。无窗口源码/Metal测试继续进行。

最新可玩QA仍为1792869948191017000，zip SHA50d4823d60a7c22ef09d6212da52c9436b3835b03e8da51cbfc5671b9079ae8d；范围实验未进入此包。正式应用/存档/设置/缓存不变；Release不更新。死亡、联网、菜单实测继续按用户要求暂缓。当前剩余额度约50%，低于15%停止开发测试并交接。


### 受管理缓冲区模型的补充边界

额外尝试把外部shared-buffer写入改为BufferCache::ObtainBuffer返回的真实缓存写绑定。现有夹具固定BB_GUEST_IN_PLACE=0，ObtainBuffer会走Metal不支持的sparse arena：4个非零范围用例在创建buffer时以23拒绝，完整覆盖控制同样未通过，因此这组不是有效的像素对照，不能推导游戏路径内容错误。证据first-upload-regression-1792873420825650000，失败版本留在证据，未作为支持的测试入口导出。前述shared-backing五例只证明特定生命周期模型，不等于实际in-place游戏路径。要关联实战白模，应先在真实隔离游戏记录该类局部重叠是否发生，或补齐真实共享chunk生命周期的夹具，再谈回收/合并修复。


# in-place 纹理与生产线程调度检查点

## 实际导入路径的像素证据

新增 `tests/metal/test_texture_inplace_gpu.mm`。它调用真实 Metal GuestMemory::Install 和 chunk allocator，先通过 gpuAddress/buffer 双路径自检，再断言 BufferCache::ObtainBuffer 返回的对象确为导入 chunk 的 MTLBuffer。测试只用自制8MiB backing；guest VMA/physical translation为夹具，BB_IMAGE_TRAPS=0，不验证跨进程页保护/映射退役。没有游戏文件、窗口或正式缓存。

现有首次上传修复库下，零长度/无实际写入/首像素部分写/内部部分写仍未通过，整图实际写入控制通过。范围校验候选下，零长度/无实际写入/整图写入通过，两个真实部分写入仍漏更新。证据分别为 first-upload-regression-1792873733110023000、1792873772538688000。这比上一轮外部shared-buffer模型更接近当前Metal路径，但仍不是游戏战斗白模复现。

最初未关闭夹具不支持的image trap时，五例在stub处SIGABRT；完整覆盖控制也失败，不能算像素证据。补齐实际chunk、禁用未模拟trap并完成正控制后，才取得上述结果。未将范围校验候选启用。

## 有界游戏日志

`build_partial_audit.py` 创建独立签名的日志候选，保持失效条件、遍历范围和渲染行为。只在显式诊断gate开启时打印，最多64条。它记录**原函数当前首字节查询能看见的**GpuModified局部重叠，不是全地址空间、全写范围的完整别名审计。

- launcher-engine-abba-1792873922454962000：93s，原固定QA副本，进场17.79s；日志确认启用，0条候选命中。压力normal，swap较起点+1254MiB，无保护中止。
- launcher-engine-abba-1792874232663345000：此前手动QA正常存档另建副本，89.61s，进场16.83s；同样0条候选命中，压力normal，swap -32MiB。CPU/GPU trace完整，无覆盖/丢弃/时钟拒绝。

两轮都没有新截图/录屏，也没有独立确认敌人受击或白模，不能宣称视觉正确。自定义seed仅放行Shared/state内部的诊断/功能测试，拒绝savedata中的符号链接；实际源QA文件摘要保持不变。正式存档和设置从未参与选择。

第二轮抓到178.386ms帧：GPU22.06ms，记录5.732ms，compiles=0；FEX lookup/compile总窗口仅1.908ms，CPU0.039ms。渲染线程condition wait165.559ms；对应生产线程168.275ms区间CPU19.225ms。同期Codex Renderer约109% CPU。这说明不是该帧有178ms编译或GPU绘制，尚不能唯一归因于调度/内存或某个锁。

该暖缓存轮整个运行282次shader scope共515.17ms，其中Metal library共450.23ms、PSO共53.47ms，类别嵌套不能直接相加。最长单次shader14.645ms，全部在第一个动作请求前；动作阶段没有新library/shader/PSO。真实新场景的全新shader复杂度仍需另测，不能拿暖缓存替代。

## 只读调度计数

新增 process_schedule_audit.py，低频读取已归属QA的进程rusage v4。SDK编译证明结构296字节，关键字段偏移16/160/248/288；自进程CPU循环与实际API计数通过，保留原始累计值，不把缺失/失败当0，也不把全进程runnable值直接算成某个线程的off-CPU时长。公开XNU的task_power_info_locked也是累计任务/线程runnable计数：[Apple XNU](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/task.c)。SDK新增page_wait_time字段未找到足够公开语义，本轮没有用它排除内存等待。

launcher-engine-abba-1792874960290559000再次抓到95.37ms帧：GPU18.21ms，compiles=0，无FEX编译scope重叠；condition wait85.839ms、生产线程92.51ms区间CPU26.655ms。周围进程累计显示游戏CPU主要是legacy QoS，Codex Renderer约210%、backupd约21%。仍是相关性证据，不是唯一因果证明。

## QoS实验：保留、默认关闭

新增C/C++共用qa_cpu_qos.h，只允许inherit/user-initiated。默认不调用调度API。只在私有loader session和显式BB_ARM_QA_CPU_QOS_EXPERIMENT=1下接受选项；游戏工作线程及guest pthread入口显式准入，原生网络/音频callback池不改，不修改guest优先级字段或原启动脚本。错误不会返回75/76。

原生测试发现pthread_attr_init后的普通新线程没有自动沿用父线程user-initiated，故不能只在main设一次；现在测试父/子显式准入、默认无操作、非法输入和EPERM失败，以及同头文件C/C++编译。先发现并修正夹具/准入假设，再进行真实游戏实验。

CPU fbbe99cbbd38031e5bb355d1dbd8a6ea86e47f7502a42157a94d20cadcc0bbb5，cpu-qos-regression-1792875472222356000：117无窗口、7数据导入、19libc跨度、17退出码+6原监督器，以及QoS/调度计数测试全通过。源端时钟下溢保护也已随该CPU构建，不再仅是解析器修复。

launcher-engine-abba-1792875547709454000实际游戏准入：CPU约97.9%时间进入user-initiated，进场17.58s，两轮动作请求正常结束，未触发保护。但仍有140.108ms长帧，不能称卡顿已修。

随后同二进制、同GPU、同boot/content/patch/settings摘要及镜头做纯静态ABBA，关闭额外诊断/host sampler/Cua/录屏。每轮90s，取末30s，compiles=0。证据 launcher-engine-abba-1792875683322786000：

| 策略 | FPS | p99 ms | 聚合footprint MiB |
| --- | ---: | ---: | ---: |
| inherit A1 | 48.015 | 22.86 | 5841.23 |
| user-initiated B1 | 46.537 | 23.36 | 6128.78 |
| user-initiated B2 | 47.624 | 22.67 | 5823.28 |
| inherit A2 | 46.989 | 23.95 | 6091.84 |

A均值47.502、B47.081，B约-0.89%；未证明收益。两组聚合内存均值5966.54/5976.03MiB，差9.49MiB，但轮间波动约300MiB且聚合含共享页重复计费，不能认为精确物理RAM增加9.49MiB。四轮最高warning，未保护中止，无画质/分辨率/效果/缓存预算变化。静态场景不替代探索的低帧结论；保留实验入口供后续CPU受限场景研究，日常QA配置不启用。

复测命令（仅准备好的本机隔离树）：
```
python3 tools/run_launcher_engine_abba.py --order arm64,arm64,arm64,arm64 --seconds 90 --gpu-variant first-upload --cpu-qos-order inherit,user-initiated,user-initiated,inherit
```

## 下一步

优先把生产线程低CPU的长间隔拆清楚：游戏任务依赖/宿主调度/页等待，避免继续把lookup cache hit称为长编译。局部纹理模型已有确定证据，但在现有两轮游戏中未关联实际白模；需要实际命中/白模现场或更接近敌人交互的隔离场景，再改局部数据权威源和合并。

死亡重生、在线、菜单继续按用户要求暂缓。硬件TSO仍未准入，软件排序不变。Cua最小化审批未获新答复，未启动或绕过。正式版/Release不更新。剩余额度约48%，低于15%停止并交接。


## 最新可玩隔离包

应用：`apps/Blood Echoes ARM64 GUI QA 1792876149743662000.app`。归档：工作区`artifacts/arm64-qa-20261025-current/Blood-Echoes-ARM64-Local-QA.zip`，42,809,121字节，SHA256 `d3a1e3e2f7876e2016cfe9108262cbf19e3c025e084fdaef86839e609f530987`。原包保留。仍依赖本机Shared树，不是便携安装器，不含游戏/存档/缓存/凭据。原GUI版本号仍0.2.51，身份由QA时间戳与manifest确定。

CPU输入fbbe99，冻结后因依赖改写/重签为593f1e219e25988d0eec5bd7cb2440e89ee590c10629d44bbd9fc67fd108e10a。frozen-qa-package-1792876282546725000证明解压严格签名、4个真实FEX退出fixture、arm64 Python与手动物理输入配置；偏好中未含QoS实验变量，online=false、cloudSaves=false（离线）。随后launcher-engine-abba-1792876359653644000实际冻结组件进场21.38s，90秒正常结束，末段49.08FPS、GPU19.16ms、聚合5837MiB、最高warning、swap+196.5MiB。单轮只作可启动与功能回归，不归因性能提升，也未验证中文视觉、F3/死亡/在线/菜单。

qos-checkpoint-20261025正式11671文件快照与目标开始基准完全一致；ABBA源QA正常存档摘要亦一致。Release没有更新。


## 生产线程等待和完整纹理范围检查点


本轮只改隔离原型及诊断工具。正式应用、存档、设置、缓存和 Release 均不更新。所有自动动作静音，未用 Cua/录屏；诊断数据不是性能收益验收。死亡重生、联网、菜单实测按用户要求暂缓。

## 修正此前诊断盲区

显式 render condition 模式压制了重复的 generic condition HLE scope，但此前只纳入 render waiter。producer 自己进入 cond_wait 时没有对应记录。因此旧报告里“没有 HLE 等待行”不能排除 producer 在等游戏任务。新增默认关闭的 BB_PRODUCER_WAIT_AUDIT，只跟踪已向合法 render wait 通知过的 producer，不靠名字猜角色，不解引用 guest 条件变量参数。

- producer wait >=20ms 才发固定行；时间/CPU回退按不完整诊断处理，不变更原 adapter 返回值。
- 固定8槽、30位不复用 token；记录与同一参数槽匹配的活动 wait 的 signal/broadcast。包含返回码、operation、线程号和原生线程名。
- 关联只表示候选通知：不假定某次广播唯一导致唤醒，不解析 guest pthread 对象别名；未匹配不代表没有通知。
- 默认关闭时没有这些计时或 registry 访问；不改变调度、条件变量、内存预算或渲染策略。

完整重建55个被基础套件调用的二进制后，119项基础回归通过；另外7项缺失data导入、19项libc跨度、17项真实FEX退出+6组原监督器、ABI与时钟校验通过。依据 current-runtime-regression-1792878475597841000。当前CPU SHA256 5c273a497e2e5a9b036a9b9c4062400b1d35512eba3edbd61696431ddc918ff7。新增策略测试包含默认关闭零时钟、阈值、异常退出、满槽、token耗尽、ABA及4000次并发租约；真实pthread集成例保持调用/结果。

## 三轮实际游戏记录

1. launcher-engine-abba-1792877811372103000：162.517ms帧，GPU1.52ms；render condition159.693ms，producer condition86.664ms，producer该调用CPU仅0.009209ms。同一参数槽另有26.84ms等待。新记录证明其中有真实 producer HLE cond_wait，不能继续全部归为翻译/绘制。该版尚无worker关联，不声称原因完全查明。
2. launcher-engine-abba-1792878675038582000：完整记录，没有>=20ms producer wait或>=40ms帧；没有可归因通知。
3. launcher-engine-abba-1792878936530243000：producer condition21.115ms、CPU0.006375ms。同PID同token9852同参数槽的 runtime.posix_cond_broadcast 在结束前约18微秒开始，返回0；原生线程名 CSChrThread2。该重叠区间没有记录到FEX编译或Metal编译。是游戏工作线程依赖的线索，不足以证明它造成之前全部长帧，也不能从名字推断具体算法。

原CLI sample 12秒/5ms运行成功但输出15,287,767字节，超过8MiB诊断预算，所以 launcher-engine-abba-1792877031948712000 整案维持失败，未改成通过。raw栈里能看到 producer 与 CSChrThread2 的原生 pthread_cond_wait，但JIT展开存在大量未知/重复帧，不能据此统计精确执行占比。工具现限制为6秒/10ms（8MiB与30秒上限保持），自造busy/wait进程1秒/10ms测试通过；尚未用新默认再采游戏。无GUI、只允许本轮拥有且路径匹配的PID，采样仅用于诊断，不接受为FPS对照。

## 纹理探针扩大可见范围，未更改像素逻辑

原局部写入探针也沿用了函数自身的 first-byte 查询，会漏掉“写入起点在纹理前，范围穿过该纹理”的情况。新诊断单独在锁内遍历完整声明范围，原 first-byte 标记循环原样保留。门控关闭时不走额外范围；最多64条、溢出安全、无payload拷贝。记录只是潜在GPU写绑定，不能当作已完成写入。

partial-audit-candidate-1792879325111918000 仅替换一个诊断对象，不打包为默认渲染器。

first-upload-regression-1792879347321383000 首次直接运行缺少强制私有GPU状态路径，6例均由构造期保护以78拒绝；未读取默认缓存，不算像素结果。随后 test_partial_audit_controls.py 为每例新建 Shared/state 私有目录：partial-audit-controls-1792879413531232000 六组诊断控制通过。

- 零长度、只声明无实际写入、实际首像素/内部/跨开头局部写：各准确记录一条，保留旧像素失败exit7。
- 真实整图覆盖：无partial日志，像素exit0。
- 这证明观察范围扩展正确、原行为没被偷偷修掉，**不是白模已修复**。

新范围探针的游戏轮 launcher-engine-abba-1792879424293034000 使用另一个既有QA seed engine-user-1792844166685513000 的新副本，源savedata摘要不变；17.19秒出现真实scene camera，门控启用，partial entries仍为0。该轮有128.383ms帧，GPU13.78ms、compiles0、FEX block并集0.649ms、render wait112.968ms；没有>=20ms producer condition。说明 producer 长等待只是部分卡顿来源，不能通用于所有长帧。没有新增截图或独立确认敌人命中，不称为玩家白模场景复现。

## 下一步

优先追踪 CSChr 工作依赖的活跃时间与阻塞，以及另一类未落入producer condition的低CPU长间隔；同时等待真实白模现场或更确定的战斗隔离路线。不要为了让定向负例变绿而整图丢弃GPU新内容、全量读回或扩大副本驻留。保留首次上传hash修复；范围校验候选继续默认关闭且不打包。没有新安全优化收益证据，不修改同步语义或默认画质。

血源专用FEX/Metal协同长期目标保持：按实际热点和资源代际/写入范围减少重复工作，未知条件回退；删除未用x86处理器不等于热路径更快。原专项研究见 docs/fex-specialization.zh-CN.md。


## 最新本机 QA 包与只读验收

本机应用：apps/Blood Echoes ARM64 GUI QA 1792879623833144000.app。压缩包：工作区 artifacts/arm64-qa-20261025-producer-checkpoint/Blood-Echoes-ARM64-Local-QA.zip，42,813,803字节，SHA256 6690a935bc2f0f960ca92eb69144d5ccf39e29b487cd00a9a297043e4d4dda0a。仅本机可用，仍引用Shared私有游戏/汉化/依赖资源；不是独立安装器。CPU来源5c273a49，GPU仍为first-upload修复库，未包含full-range诊断库；诊断和QoS默认关闭、离线静音、保留物理输入。

解压后严格签名、四个真实FEX自造退出例、arm64 Python与手动输入策略通过。冻结组件实际90秒场景加载 launcher-engine-abba-1792879680245033000 通过：17.53秒scene camera，末30秒48.66FPS、p99 22.69ms、GPU19.25ms；聚合footprint5817MiB（含共享重复计数），压力最高warning2、swap增量0.81MiB。单次冒烟，不是改动收益A/B或白模/死亡/联网/菜单验收。

正式应用/存档/设置/缓存保护快照11671文件与目标起点完全相同，注意不是11671份存档；临时GPU别名恢复原路径。限额最新剩余46%，未触及15%暂停阈值，无Release更新。


## 未对齐信号路径检查点


范围仍为隔离原型；不改正式应用、存档、设置、缓存或Release。静音、原画质和保护规则保留。没有启用硬件TSO、放松软件访存顺序或删减x86语义。

## 诊断接线

新增显式 BB_UNALIGNED_AUDIT=1，仅隔离 action+producer 诊断准入。利用既有固定1MiB trace映射，未增加资源缓存或图像副本。各宿主guest执行线程在普通执行路径预先绑定trace、gate、原生TID并触达时钟/TLS；信号路径不调用getenv、malloc、stdio初始化或pthread名字查询，只用预绑定状态、锁自由原子和clock_gettime。clock_gettime在POSIX信号安全接口表中：[POSIX.1-2017 §2.4](https://pubs.opengroup.org/onlinepubs/9699919799/functions/V2_chap02.html)。默认关闭时不进入新增计时。

- kind73：早期信号/ESR/PC检查之后，包含代码缓冲区查询、写保护切换、修补、日志和寄存器恢复。
- 74修补、75成功日志写入、76缓存/上下文发布，只有>=0.1ms时发明细；73不设时长阈值。
- 77：原处理函数拒绝的路径，仍返回原值，不伪装恢复。
- 不覆盖内核异常投递和进入函数最初的过滤成本；不能把记录当全系统缺页总耗时。
- 复用同一格式的预绑定写入函数，不在handler中触发Store懒初始化；保留errno，时钟回退/失败计为dropped。

## 回归与失败记录

current-runtime-regression-1792880711073384000：56个实际使用目标全部重建，121基础回归通过，另外data导入、libc跨度、17项FEX退出和6组原监督器、ABI、时钟和解析专项通过。CPU SHA256 09af59d2403b3937b5eaba9ae2227e3101342c97e7be54118f0057a1e99b769f。

原生信号测试：513次真实信号、5个预绑定线程、2052条完整记录；gate关闭无记录、默认关闭不创建trace、errno保留。初轮测试错误地要求所有极短区间wall>0，实际749条量化为0微秒且全部发布、无丢行；修正断言并增加异常时钟/边界静态检查。失败证据 current-runtime-regression-1792880323875880000 保留，未改成通过。

实际FEX夹具同时验证开关前后读取结果正确、错误数据返回非零。首轮错误使用嵌套state路径，被原保护拒绝；改成既有Shared/state直接子目录。单线程/已知地址读取没有触发SIGBUS，多线程TLS夹具也未触发，均明确actual_signal_exercised=false，不冒充信号路径覆盖。错误数据的夹具返回3按原运行器约定映射进程退出2；只修正测试预期，运行时退出语义未变。最终 signal-audit-fex-1792880732801700000 与完整回归中的同项通过。

## 实际游戏：该路径成本很小

launcher-engine-abba-1792880757871433000，两个有界移动/攻击请求周期，私有QA进度的新副本。16.64秒观察到scene camera；未录屏、未使用Cua/stack profiler。CPU14153行、GPU3651行，均完整，无overflow、dropped或时钟拒绝。

- 开门控期间47次真实未对齐修正，kind73 wall合计0.318ms，CPU合计0.308ms，单次最高0.030ms。
- 没有>=0.1ms的修补/日志/发布分项，没有拒绝处理记录。
- 完整覆盖的51.836、49.859、41.519ms长帧都没有与未对齐scope重叠；GPU分别26.92、18.44、16.62ms。
- 全程日志3864个修正包含启动阶段；没有测到的启动部分不能冒称0.318ms总成本。因此结论限定为该轮已加载场景的门控区间，不能泛化所有新地图。
- 21.761ms producer条件等待关联到CSChrThread3广播，返回0。仍仅为同槽/token候选通知，非唯一唤醒因果证明。
- 全范围partial探针已启用，依然0命中；没有新视觉白模复现。

这轮证据不支持为了流畅度修改未对齐修正或压缩其错误检查，也不支持把暖场景长帧归给FEX新编译。所有新计时默认关闭。下一步区分CSChr任务实际CPU工作、它自己的等待、producer上的多次短等待和宿主失去CPU的时间；不要盲目加frames-in-flight或扩大缓存。探索冷内容的200–326ms同步shader/PSO编译仍是另一个已确认来源。


## 冻结包与内存验收边界

最新本机QA：apps/Blood Echoes ARM64 GUI QA 1792881296726369000.app；工作区artifacts/arm64-qa-20261025-signal-checkpoint/Blood-Echoes-ARM64-Local-QA.zip（42,816,059字节，SHA256 1281d4de2238dfac49c86615b2db6d421fdefe0aa2c88858fb542be2c47791a6）。仅本机准备过的Shared环境可用。CPU09af59d2、GPU仍为既有first-upload修复，未捆绑诊断GPU库；BB_UNALIGNED_AUDIT等新增诊断默认关闭，离线静音、保留物理输入，不含游戏/存档/账号/缓存。

解压严格签名、四项FEX自造退出、ARM64 Python和手动输入策略通过。冻结组件90秒实际加载 launcher-engine-abba-1792881363563803000 通过：17.28秒出现scene camera，末30秒47.88FPS、p99 27.84ms，GPU19.71ms。此为功能冒烟，非性能改动收益验收。

本轮聚合footprint6143MiB、swap+219MiB；17.54秒仅一个采样为critical4，未持续到原5秒停止阈值，退出后压力恢复1。对照较早5c包单轮5817MiB，本轮staging对象多7个/98MiB、RT多92个/70.2MiB，故不能宣称内存严格等价，也不能用非匹配单轮认定新诊断代码泄漏。它没有改缓存预算/寿命/资源分配，新计时已确认默认关闭；聚合数还重复计算共享页。内存差异仍需匹配比较，未用此结果启用任何性能候选。


## 冻结 CPU 版本匹配内存 ABBA

为排查单轮内存差异，frozen-cpu-memory-abba-1792881779846842000 按旧5c/新09af/新09af/旧5c，各90秒。两者除CPU外的冻结runtime文件SHA全部相同；16个原脚本、boot/content/patch/settings摘要、镜头位置匹配。静音、无诊断/录屏/Cua、没有修改缓存容量或正式配置。

| 顺序 | CPU | FPS | 聚合footprint MiB | critical采样数 |
|---|---|---:|---:|---:|
| A1 | 旧5c | 47.619 | 6104.27 | 3 |
| B1 | 新09af | 47.401 | 5819.40 | 0 |
| B2 | 新09af | 47.530 | 5839.22 | 0 |
| A2 | 旧5c | 47.086 | 6114.18 | 0 |

均值47.353→47.466FPS（约+0.24%，无显著提帧证据），聚合footprint6109.22→5829.31MiB（本组少279.92MiB）；旧RT344/345个，新RT254个，暂存也有差别。与之前单轮方向相反，不能将下降归功于探针或宣称精确物理RAM节省，更不能据四轮排除长期泄漏/白模。至少本次匹配测试没有呈现新CPU额外内存压力，所有guard按原策略运行。

已将可重复的比较入口整理为tools/run_frozen_component_abba.py，显式传入--baseline与--candidate的Shared/apps私有冻结包；首先校验只有CPU不同、脚本和输入一致。不是正式应用更新/安装器，也不更新Release。

下一步优先获取独立正确性诊断的游戏画面，帮助自动定位实际敌人与白模；该种抓图必须单独于性能对照，不拿抓取帧或录屏期间的FPS判断收益。继续追踪CSChr依赖和冷shader/PSO同步编译，不放松资源校验或访存语义。


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


# 组合实战诊断与实际装载身份（2026-10-25）

## 真实受击路线与覆盖范围

复用既有full-range局部GPU写入探针（最多64条metadata，不改变失效逻辑）和原生present读回，组合成单独的ARM64 QA GPU库/子应用。仍只改隔离诊断，不捆绑普通手动QA、不改正式应用或Release。机器码缓存、画质、分辨率、效果和缓存容量策略未改变。

完整成功案例：launcher-engine-abba-1792887238427365000。从原始QA seed的新副本走已观察旧亚楠灯点外广场路线；使用原Player No Dead，离线静音，不验收死亡、联网或菜单。按原pad-file接口分短步调度，不承诺碰撞/AI确定性。两个原生帧均在计时门控外：11秒和142秒请求；三个门控约28–40、100–112、130–140秒。源QA存档哈希未变。

- 最终可见角色明显染血、回响141，角色衣物和血液效果有细节，没有整身均匀白模。起点与路径对应前轮已实际看到敌人攻击的广场，强于对墙挥刀。最终帧没有活敌人特写，不能宣称覆盖所有敌人材质或捕获了白模的瞬间。
- CPU7256/GPU1725条，完整无dropped/overwritten/clock reject。
- 1690个记录帧间隔，最高31.662ms，没有>=40ms长帧；这只是诊断窗口，无A/B收益结论。
- FEX block177项合计wall46.160ms/CPU27.635ms，单项最高1.475ms；低于阈值的事件仍省略，不能将scope总和当全进程CPU总量。
- 未对齐修正18次共0.113ms，最高0.029ms。三个窗口没有新的Metal library/PSO scope，完整运行无Slow compile；暖路线不等于新地图无首次编译。
- 已有PARTIAL_IMAGE_AUDIT_ACTIVE full_range=1，局部GpuModified写入intent匹配0条。这仅排除该轮门控内的这类通知，不能排除CPU写失效、其它时间窗或所有别名问题。不据此启用范围实验，不缩缓存。
- 系统pressure1→1，swap约+550MiB，未触发保护；系统swap不能作为候选常驻RAM增长的归因，也不是deduplicated memory对照。

路线算法保存为tools/run_observed_combat_probe.py：显式--save-seed，只允许Shared私有state，启动与路线同一驱动按场景就绪时钟接管，最多180秒，最多2秒单步、图片放在门控外，原监督器保留全部保护和owned收尾。源入口已做拒绝外部/正式目录控制；成功live由同算法的临时驱动执行，保存入口的新正跑留到下一轮编译分项诊断，不能混称全部入口已验收。

## 失败没有作通过处理

- launcher-engine-abba-1792886710184051000：启动与控制分开导致衔接晚于8秒误差限制，主动finish、未抓图，完整验收保持失败。
- launcher-engine-abba-1792886808498953000：路线请求已完成，但得到旧1920×1080 physical dump，无新格式/方向证明，验收失败。最终原因是组合库正确编出，复制的QA子应用仍链接旧first-upload库：install_name_tool -change使用了partial库的名字，复制源实际链接的是first-upload库；未命中的替换会返回成功。此轮没有运行范围探针，不用于0命中或画面正确性结论。
- image-range-controls-1792886648767300000中的7个真实Metal首次上传/扩展正例和6个范围控制是直接链接组合库的夹具，结果有效，但不证明当时子应用已装载它。部分写入像素的预期失败仍保留，不称已修好。
- 新冻结包验证器最初只懂@loader_path，未解析正常包的@executable_path，曾拒绝合法包；补全解析和实际正控制后通过，原包未修改。

## 将实际装载关系纳入准入

新增gpu_candidate_identity.py：只接受指定私有root下的owned regular文件、原生ARM64主执行文件和库、声明SHA256、严格签名以及真正的Mach-O直接GPU依赖边。解析absolute、@loader_path、主执行文件的@executable_path；未知@rpath不猜测。共享库冒充主程序、错库、静默替换未命中、哈希改变、外部路径、symlink和x86宿主残留均拒绝。它只证明所选GPU主程序/直接库，不冒充完整传递依赖或guest ABI证明。

实际Mach-O控制gpu-candidate-identity-1792887682165169000：11项通过，包括执行真实未命中install_name_tool后仍被拒绝，正确absolute/loader-relative/executable-relative装载均可通过，且验证不执行x86微型程序。全部自造源，无游戏数据。

已有first-upload、submit、partial、present-image四候选与旧texture-candidate均复核通过。新runner把校验接到这些候选、基线、冻结组件路径，记录子程序和实际库SHA；修正present-image只记录固定旧库路径的artifact遗漏。候选装载不符在切换alias/启动游戏前失败；无效参数的负控制已确认finally恢复alias。原16个启动脚本和游戏退出语义不改。

## 私有基线签名封装修复

只读审查另发现Shared/backend的原始开发基线Info.plist与签名不一致。正式应用、普通冻结QA包不受影响。已完整备份到baseline-gpu-envelope-1792887996363472000/before.app，再修复该私有基线封装。第一次保留metadata/普通重签都被FinderInfo阻塞，Python os.listxattr在这套macOS Python不可用，未产生属性修改；最终用系统xattr备份并移除唯一com.apple.FinderInfo，保留fileprovider/provenance等其它属性，再以原本ad-hoc方式签名。

最后严格签名和实际load edge通过，__TEXT,__text摘要与备份完全相同、GPU库SHA完全相同。只更新签名封装，不是游戏渲染/性能修复。Finder属性备份与完整原app都留在私有evidence，均不上传Git。

## 最新正常QA包仍未变

frozen-qa-package-1792887683191489000：解压严格签名、实际native GPU主程序→@executable_path库路径/SHA/ARM64身份、四项真实FEX退出（含75/76错误→82）、ARM64 Python与保留手动输入策略均通过。归档仍为signal-checkpoint的1281d4de…47791a6；未带本轮组合诊断，没为诊断重打包。

后续90秒冻结组件无抓图冒烟结果待本报告收尾追加。不能把包验收等同当前白模、F3、菜单或在线全面验收。

## 对下一步的约束

本轮已确认组合探针实际接入且能覆盖受击相关路线，但仍未复现用户的偶发白模，不再把同一暖路线零编译重复测当作解决长编译。下一步补齐现有缺失的GCN→IR、IR→SPIR-V与模块总计时，与已存在MSL/Metal-library/PSO和FEX计时关联，使用有界门控和自造shader控制；之后仅私有新应用缓存比较冷/暖新内容，不清现存缓存、不常驻全量归档。

FEX相同guest PC的跨线程IR重复已有上游CompileCode的二次LookupCache检查和明确减少全局锁竞争注释，不能凭三条重复PC就另造共享JIT/大锁。保留完整回退的游戏特化与资源代际/写入范围协同仍为长期目标；不全局裁ISA或松TSO/页保护。

25个未接导入、完整ARM clean-checkout构建和用户醒后验收仍未完成。目标继续active，正式app/数据不改，代码只同步dev/arm64-runtime-audit，不同步Release。剩余额度最近42%，低于15%停止并完成交接/最新本机QA包。


### 冻结组件收尾冒烟

launcher-engine-abba-1792888283578587000：新装载准入接入后的原冻结包90秒静音、无Cua/抓图冒烟通过，17.51秒出现scene camera，末段48.39FPS、p99 22.73ms、GPU19.45ms，聚合footprint5828MiB，最高warning2，系统swap约+105MiB。实际GPU direct load edge和签名/SHA均记录在result，没改封装包或设置。这是启动路径/新验证器不破坏正常入口的回归，不是新增性能收益、去重RAM或完整游戏正确性验收。

补充准入回归gpu-candidate-identity-1792888744088525000：最终12项真实Mach-O控制通过。新增代码页篡改后重新声明正确文件哈希仍被严格签名拒绝的负例；签名失败现在带出有界实际原因，避免只见笼统CalledProcessError。未改游戏执行或封装包。

## 当前优先交接：分段编译证据

最新实测和回归见docs/shader-phase-and-uniform-range.zh-CN.md：已真实抓到顶点37a8af3d的232ms驱动建库+84ms PSO，与338ms长帧和337ms提交阻塞吻合，FEX仅0.407ms；GCN重编译与驱动阻塞已经可分开。另有冷启动compute的34ms IR转换，不宜只修驱动。新小优化只消除片元源码后缀复制，生成文本/实际24像素用例不变，不能当FPS收益。

诊断候选present-image-candidate-1792890975879404000（新增mtl_shader对象）已运行隔离路线通过；正常手动QA仍使用冻结的signal-checkpoint包，未加诊断库。第三轮同hash预加载0.206ms，但AI/内容偏差与缓存变暖不允许归因小优化。正式快照shader-phase-finish-20261025.json与native-runtime-goal-current.json完全相等。Cua仅按用户追加授权用于测试前收起Codex，服务关闭后才跑三轮；无需再启动Cua。限额41%，目标保持进行中。

## 最新 AV 接入交接

见 docs/avplayer-bridge.zh-CN.md：16 原实现薄桥、真实 FEX 回调与 TCB 收尾、旧宿主错配准入及静态 ABBA 已完成。新 CPU602795df，普通手动包仍09af59d2。未宣称白模修复/提帧；未重测死亡/在线/菜单，不更新正式/Release。剩余9导入、完整特化编译准备及白模归因继续。当前剩余额度38%，低于15%才按要求停工交接。

## 准备提前量与用户新增目标

GOAL.zh-CN.md 已新增九接口收口及固定游戏着色器/特化目录，两者是完成条件。见 docs/shader-preparation-opportunity.zh-CN.md 的真实数据：提前机会存在但多数属于启动，部分实际新片元没有提前量；尚未增加异步编译或常驻缓存。实际图片未见整身白模，也未确认敌人接触，不称实战复现。下一步先静态枚举已找到的八个 shaderbnd/DCX（含debug包，尚未解包计数），并继续九接口对照，不要重复纯站桩代替。

## 静态目录和已有特化交接

见 docs/fixed-shader-catalog-and-imports.zh-CN.md。只读盘点固定1.09：15,323个非调试shader条目，含内嵌后8,853哈希/8,311已验证代码摘要；实测221哈希全部找到，但不称全游戏特化覆盖。现有QA缓存508份meta/SPIR-V配对、391程序、409管线键；约12.8MiB，不改缓存。20+6自造控制通过。原resolver源码与ARM副本逐字节相同、旧GPU宿主九NID均缺失（16AV正控），故为原有支持缺口，仍需逐项契约/调用点收口；未执行正式CPU或冒称九项已实现。白模、提前准备与材质/完整键解码继续。

## 已有特化输入已解码

见 docs/semantic-cache-inputs.zh-CN.md。主QA508份meta/409键和热点缓存327份meta/259键均完整解析、阶段引用一致；仅只读字节，不注册walker。速度图顶点拒载计数均0，排除该假设；热点37a8af3d在后者，不在前者。Profile raw差异仅6字节padding，49字段语义相同，经原生operator==正控确认；不以此自动合并缓存。18项canonical Writer/布局/引用/字段语义控制通过。未新增运行时优化，未测FPS收益。下一步继续材质/未覆盖特化关联及九接口，不重复预载已知键冒充新优化。最后可读额度剩余36%。

## 最新：程序名数据导入已实现，剩余八项继续

见 docs/guest-process-data-contracts.zh-CN.md。`__progname` 已改为已有客体参数页内的可写指针槽，实际FEX读取/改写/新进程重置及错误函数声明负例通过，不增加映射/内存池。新CPU442a746f通过121基础回归、17退出/6监督器和6实际AV控制；普通手动QA包未更新，仍09af59d2。正式11671既有保护文件逐字节不变。

实际libc调用点证明 ModuleInfoFromAddr 的同一输出缓冲区随后传给 __elf_phdr_match_addr，不能照搬FreeBSD dl_phdr_info同名函数。这两项需成对补完整模块元数据，另外线程析构、mlock/msync和三项信号上下文仍未完成。F3检查点是普通游戏存档文件而非RAM快照，未改其格式/协议；本轮无真实游戏验收/性能结论。最后可读用量剩余36%。

## 最新：模块查询和地址匹配已成对实现

见 docs/module-info-contracts.zh-CN.md。复用原SELF解码器，在FEX/GPU启动前准备SHA绑定的元数据；原16个启动准备源码不变。运行时3描述共1272字节，不扩大缓存；助手约302MB临时峰值/0.38秒，退出后才创建FEX/GPU，不能隐瞒此启动开销。新CPUbf4338e5通过8组真实FEX、73个原生负例、6个生成器测试、两个编译布局、121基础及程序名/退出/AV回归。实际无窗口libc/Fios2初始化成功约1.04秒，压力仍2、swap无增长；没有场景/FPS结论。原九项已有三项实现，六项继续。助手75/76和空成功均以CPU错误1失败，原F3协议不变。原手动QA包和正式应用/Release未改，最后可读额度剩余35%。

对齐专项补证：max_align_t假设被原生测试拒绝，已共用现有低地址堆常量；实际FEX→TLS分配验证32字节对齐/零初始化，64字节不保证的负例仍拒绝。最终CPUbf4338e5的121基础、退出/AV/程序名复测通过；实际模块冒烟0.856秒，不当成启动性能A/B。分配算法和容量未改。

## 最新：msync 与 VM 数值地址契约

详见 docs/msync-and-vm-address-contracts.zh-CN.md。msync 已真实桥接，显式转换 FreeBSD/PS4 标志，防止误触 Darwin KILLPAGES；映射校验和同步受生命周期读租约保护，不增加锁页或缓存。实际 FEX PROT_NONE 测试发现六类 VM 地址被误当可读缓冲区，已只修地址槽，输出指针负例仍拒绝。18组真实FEX和原生文件/并发控制、121基础及模块/程序名/AV/退出专项通过；新CPU67db33e2的无窗口原模块初始化0.852秒、swap无增长，不是FPS结果。原九项已有四项实现、五项继续；正式11671既有文件与原16脚本不变。普通QA包仍09af59d2，未更新正式应用或Release。

已并行分派 mlock 契约、白模资源身份、shader/材质完整键三项审查；候选不改公共代码，根代理统一整合，不并行启动游戏。最后可读用量剩余34%。

## 2026-10-25 并行专项审查收尾

- sceKernelMlock：固定镜像默认锁页配置关闭，锁定目标是libc flexible arena整块。实际Darwin同一16KiB页重复lock计数0→1→2，一次unlock仅降1，最终已清零/unmap。22项有界模型测试通过；单次16MiB/总64MiB/4096页仅候选上限，不是游戏需求或已接实现。须联动msync INVALIDATE的wired EBUSY、VMA租约和成功覆盖后退账，继续计入5个未完成接口。见docs/mlock-contract-review.zh-CN.md。
- 白模：真实TextureCache/Metal七项控制确认同址同尺寸但linear/tiled布局变化会在memo/exact/generation路径误选旧图，11904/16384字节与正确参考不同；合法sRGB和1D/2D等正控通过。26项布局谓词控制仅证明分类；未修运行库，未归因为用户白模。GPU-only需raw→retile→detile保存字节语义，不能直接CopyImage或弃图。见docs/white-model-resource-audit.zh-CN.md及docs/texture-layout-candidate.zh-CN.md。
- 材质/特化：304个MTD完整结构解析，72 ShaderPath；273有命名候选、31未关联，非实际绑定。主/热点缓存505/508、324/327唯一静态代码候选，余为GS歧义；10项自造控制通过。只导出解析器、控制和摘要，完整材质/身份账本留本机。见docs/shader-material-inventory.zh-CN.md。
- 线程/上下文：finalize是模块注册清理，不是线程TLS清理；须补析构回调in-flight生命周期。三项上下文不能调Darwin sigreturn或空成功；12个ARM64/x86结构控制只验证子集，不证明完整PS4 ABI。见docs/thread-signal-contracts.zh-CN.md。

主线程复跑22+10自造控制通过；这些新增候选不接resolver、不启用锁页/布局修复/后台编译，不产生新FPS结论。独立审查/自造测试可并行，公共执行状态与GPU行为修复集中整合，真实游戏/性能对照串行；所有正式数据继续不动，不发布Release。最新可读用量剩余32%。

## 最新：布局诊断与GPU原始字节保全

见docs/layout-observation-and-raw-preservation.zh-CN.md及两份GPU-only报告。默认关闭的64条饱和布局观测已编译，6原生helper/13实际Metal控制通过；不改选择/dirty/回收。原retile的padding有两个明确像素失败，GPU覆盖合并语义正控将错误降0，但CPU oracle mask不是生产方案；整合13控制仍保留2项known_defect，不能冒称全保全已修。下一步应让tiler按源逻辑texel写入并保留raw其余字节，先审TilingInfo ABI和所有消费者。

两次加载被swap增长保护停止；按既有授权重启chronod后仍不能通过，用户关闭其它应用后第三轮实际场景17.07秒/两轮请求完成，布局错选0条；没有白模或FPS收益结论。短暂critical5采样、swap+963MiB不能省略。保护未变，alias和源QA存档已核验。正式既有11671文件及原16脚本不变。

新本机QA契约包已冻结CPU67db33e2/正确a500宿主/原66d554bd renderer，修复打包器固定旧menu库的路径；两个包内FEX正负例和CRC/内容检查通过。artifacts/arm64-qa-20261025-contract-checkpoint，SHA c22970cc38eb2a90946327a4a5f80e7525f12ddd22d9b5504a116615badb5032。未启用布局转换/诊断到普通包，未开新GUI、未更新正式应用/Release。旧09af包仍保留历史。最后可读用量剩余30%。

## 最新：GPU逻辑texel回写边界候选

详见docs/tiler-logical-coverage.zh-CN.md。私有652字节参数保留旧460前缀和公开ABI，specialization13/完整cache key区分新旧；原物理寻址不变。24真实GPU控制全部符合预期（原两padding缺陷消除）、19形状拒绝/14参数控制、同进程六次新旧管线交替通过。32/64直接源重写不再硬编码SPIR-V编号，保留现有proxy/MetalFX语义。候选默认关闭，未改FindImage选择。

单次无Cua静音隔离探索进入场景18.05秒、66.41秒完成，未崩溃、无保护中止；pressure4短暂约0.261秒，swap+1.73GiB。此轮不是FPS/内存对照，未证明白模解决。普通本机QA包、正式app/数据与Release不变。下步继续raw权威来源与布局选择修复，以及匹配性能/内存验证，不降低画质或缩缓存。

最终c607候选补上省略常量默认值规范化与完整key，4新增控制和24 GPU/六次交替复测通过；最终构建也完成70.42秒隔离探索，swap+360.06MiB、起止压力1、短暂严重不足1秒，不是性能/内存A/B。源码继续只推dev分支，普通QA包和正式应用/Release不变。

## 最新：GPU raw写入重叠与顺序诊断

详见 docs/raw-write-order.zh-CN.md。两组19个实际Metal控制保留四种已知失效缺陷；GPU-only同submit顺序证明预写导出能保住旧/新数据，但尚未接入实际BufferCache。修正限额后的真实隔离场景17.01秒进入，16条潜在漏失效记录、最多4条/probe、swap终值+57.5MiB，无严重压力。binding覆盖不是实际逐字节写入或白模根因证明。正式11671既有文件/16脚本不变；不降画质、不改缓存预算、不更新正式包或Release。Mlock和owned-shader仍为独立未接入草稿；前者发现部分映射失败状态一致性门槛，不能当作已支持。

## 最新：owned shader基础验证，下一步接入

230个GPU单元完整构建，owned源毒化/异线程GCN和detached Metal、原同步phase与24raw控制通过；普通QA仍旧包，正式版不变。最后短场景用户观察到一直砍墙，只能算启动动作smoke，后续不再跑盲目攻击路线。scheduler/首见程序capture和完整GPU采用仍未接；Mlock二审补丁待串行应用和C/FEX验证。详见本机evidence/owned-shader-verified-checkpoint-20261025/report.zh-CN.md。

## 最新：有界 Mlock 接入通过无窗口回归

见 evidence/mlock-integrated-checkpoint-20261025/report.zh-CN.md。CPU854294e0，56目标重建/121基础及14专项通过；真实Mach单页重复去重和MAP_FIXED清账通过。原九项已有五项有界实现，四项继续。未跑游戏、不称提帧或白模修复；普通QA包仍CPU67db。新owned-shader基础a8bd4776已推dev，捕获与raw预导出草稿尚未接入。正式数据/Release不变。用量本轮剩余约93%。

## 最新：POD shader捕获基础通过真实控制

见 evidence/guest-capture-verified-checkpoint-20261025/report.zh-CN.md。230GPU单元重建为ec51ec6c，1000故障恢复/owner析构、有界参数/真实prep入口/GCN截断通过；owned、原phase输出623deccc及24字节保全回归通过。未开游戏、未接scheduler，旧SRT/fetch大guard仍待修。源码仅dev同步；普通QA/正式数据/Release不变。Mlock已推c2c5bdc1；当前用量剩余约93%。

## 最新：bounded SRT/fetch准备读集和mirror整库回归

见 evidence/shader-readset-verified-checkpoint-20261025/report.zh-CN.md。230单元重建a36c7bd3，真实lookup/nested SRT/fetch/V#及旧capture/owned/phase/24保全通过；mirror 46项在独立源码和实际动态库均通过。成功64次小读微测约12us，非FPS。尚未接firstmiss discovery/worker/adoption，白模仍未解决；R2/finalizer/context独立草稿未混入。普通QA/正式数据/Release不变。

## 最新：模块finalizer实际回归及策略更新

见 evidence/module-finalizer-verified-checkpoint-20261025/report.zh-CN.md。CPU7adf4d5e，真实C两组/11FEX/121基础及完整专项通过；原九缺口已有六项有界实现，三context仍未接。不称实景性能已验证，普通QA包仍67db/66d554bd。新增目标以完整组合持久化/加载预热为主，未知异步兜底，测逐draw校验累计成本；GOAL末尾保留细则。R2/live payload/context/firstmiss仅私有草稿，正式数据/Release不变。当前账户约87%剩余。

## 2026-10-25：持久化与加载期读取验证

见 docs/warm-persistence-validation.zh-CN.md（Shared 原始证据 evidence/warm-persistence-verified-checkpoint-20261025）。v3 归档实际身份隔离、原子成功发布、父目录故障保护及 LoadPipelineStage 精确命中免重读已在两单元候选实现，15 原生子进程、9 producer 分支、真实 Storage 路径、owned/phase 与 24 GPU 保留控制通过。无游戏启动、无新 FPS/白模成功主张，尚未默认启用、未更新普通手动 QA 包/正式应用/Release。下一步须在隔离场景核对完整组合覆盖与冷/暖启动编译、帧时间及内存，尤其 newLibraryWithSource 仍会发生，不能用 archive 命中代替所有层命中。完整设置散列保守且有启动成本；新原生候选必须用实际绑定此库的私有 GPU 执行文件测试，不能把旧 GPU app 的 v2 结果当成 v3。
