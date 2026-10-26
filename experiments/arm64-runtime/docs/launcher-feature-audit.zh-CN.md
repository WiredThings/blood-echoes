# 启动器功能与当前 ARM64 运行时对账（2026-10-26）

本轮只改开发分支和隔离构建；正式应用、存档、设置、缓存、Release未动。集中游戏验收之前先收口此表。审计采用当前源代码，逐项追UI→偏好/ini→环境/脚本→ARM/Metal分支；“对应”不等于最新包实际游玩、像素或收益已验收。

## 确认问题及修正

| 项目 | 发现 | 开发分支处理 |
|---|---|---|
| ARM64宿主开关 | FEX必须独立GPU进程，旧可关闭开关会让脚本清除必需GPU_PROCESS；旧文字仍称游戏用Rosetta | 包内NATIVE-METAL显式声明`game_runtime=fex-arm64`。UI隐藏该可选开关，显示必需能力；Profile强制必需宿主flag，但不改旧偏好。旧Rosetta包仍保留原选项 |
| Rosetta安装检查 | 原`checkOptimizedEngine`对所有正式布局包都要求Rosetta，不能在无Rosetta的新Mac使用FEX包 | FEX声明之外还检查CPU/host/GPU和准备器实际薄ARM64 Mach-O类型；通过才不要求Rosetta。旧包检查保留，错误架构/不完整包仍明确阻止 |
| Vulkan兼容入口 | 当前隔离FEX组件只有Metal路线，原UI仍可选择Vulkan并尝试启动 | 按所选组件能力禁用不支持的入口并解释；不修改已存renderer值，不假装支持Vulkan |
| 自动本机准备 | 新流程没有UI信息，易与旧二进制归档开关混淆 | 显示默认自动准备的只读说明；不增加关闭开关。缺Toolchain仍由原准备代码明确回退，不声称每个组合已提前建好 |
| 最近staging复用 | QA extraEnv已启用，UI原无该实验选项 | 新增`BB_MTL_HOT_STAGING_REUSE`默认关闭实验开关，仅在引擎显式声明recent-staging-reuse时显示并传递；旧引擎隐藏但保留偏好。原迁移逻辑导入已有明确开发值，显式UI值优先，不改原容量/fence |
| System扩展菜单 | 原PatchImage在prologue不符时关闭扩展继续，ARM无stubs即整次退出 | 只准入原算法正常返回、零callback/零分配尝试/零安装入口的无副作用路径。其余异常/部分工作仍失败；不复制原prologue判断 |
| 日志架构文字 | 原脚本在FEX仍输出Rosetta game | 仅在ARM兼容模式输出FEX game runtime；旧分支原输出保留。原16脚本冻结准入仅新增这一精确日志修正，不放开任意脚本改动 |
| QA使用旧GUI | 原QA生成器固定复制历史GUI产物 | 组包要求显式指定本次`--launcher-products`，检查ARM64并复制本次UI/三语资源，记录可执行SHA；不把后端更新当UI也已更新 |

## 渲染开关核对

原35项：21默认开、14实验默认关，全部找到真实消费者。新增recent staging后为36项（21/15）；旧可选ARM宿主按包能力单独出现，FEX不显示它。共同链为`MetalOptimizations.id`→`prefs.metalOptimizations`（未存采用defaultOn）→`OptimizedProfile.environment`→`LauncherModel.environment`→原run_optimized保留显式0→下表原分支。额外变量不能覆盖已显示控件是设计；5个旧*_FILE对照入口会被清除。

消费者路径以下相对`vendor/metal_api/shadps4/video_core`；shim另注明。

| 环境键 | 默认 | 实际分支/条件 |
|---|---|---|
| BB_MTL_TWO_LEVEL_BDA | 开 | renderer_metal/mtl_runtime.mm / mtl_pipeline_cache.mm；pt2/flat分兼容命名空间，保留旧缓存 |
| BB_MTL_CLEAR_DISCARD | 开 | mtl_scene_targets.mm，全清除前load |
| BB_MTL_OVERWRITE_DISCARD | 开 | mtl_scene_targets.mm，完整覆盖 |
| BB_MTL_PROXY_CLEAR | 开 | mtl_scene_targets.mm，现有场景副本clear |
| BB_MTL_VERTEX_MATH | 开 | mtl_vertex_math.h / mtl_shader.mm源改写 |
| BB_MTL_PROXY_LEAN | 开 | mtl_scene_targets.mm，关联FULL_USAGE=0 |
| BB_MTL_UPSCALE_ALPHA | 开 | mtl_scene_targets.mm；仅effective MetalFX |
| BB_TEXTURE_INVALID_RECLAIM | 开 | texture_cache/texture_cache.cpp；需lifetime且collector未禁用 |
| BB_MTL_IDLE_STAGING_DISCARD | 开 | mtl_runtime.mm；GPU完成/闲置backing |
| BB_MTL_RELEASE_WARM_ARCHIVE | 开 | mtl_pipeline_cache.mm；释放编译期对象，不丢活动PSO |
| BB_MTL_BATCH_CLEARS | 开 | mtl_scheduler.mm |
| BB_MTL_DIRECT_COPY | 开 | mtl_scene_targets.mm |
| BB_MTL_ADAPTIVE_STAGING | 开 | mtl_runtime.mm |
| BB_MTL_TEXTURE_TILING | 开 | mtl_runtime_kernels.mm |
| BB_MTL_PRECISE_RESIDENCY | 开 | mtl_scheduler.mm |
| BB_MTL_COALESCE_BARRIERS | 开 | mtl_render_list.mm |
| BB_MTL_QUERY_COMPUTE_CHAIN | 开 | mtl_occlusion.mm / mtl_runtime.mm |
| BB_MTL_PRECISE_COMPUTE | 开 | mtl_rasterizer.mm；依赖PRECISE_RESIDENCY |
| BB_MTL_TEXTURE_TILING_HALF | 开 | mtl_runtime_kernels.mm / mtl_scene_targets.mm；依赖TEXTURE_TILING |
| BB_MTL_FOLD_CLEARS | 开 | mtl_scheduler.mm；依赖BATCH_CLEARS |
| BB_MTL_CONTINUE_CLEARS | 开 | mtl_render_list.mm |
| BB_MTL_DEPTH_LAYOUT | 关 | mtl_pass_continuation.h |
| BB_LIFETIME_HEAP_HISTORY_PRIORITY | 关 | shim/bbport_resource_lifetime.cpp |
| BB_MTL_NATIVE_CLEAR_BATCH | 关 | mtl_runtime.mm；依赖BATCH_CLEARS |
| BB_MTL_MERGE_OVERLAY | 关 | mtl_presenter.mm |
| BB_MTL_FILL_UPDATES | 关 | mtl_runtime.mm；重复字节fill优先 |
| BB_LAYER_USE_CACHE | 关 | buffer_cache/buffer_cache.cpp |
| BB_MTL_COPY_PAIR | 关 | mtl_shader.mm |
| BB_MTL_RENDER_UPDATES | 关 | mtl_runtime.mm / mtl_render_list.mm；依赖INLINE_UPDATES |
| BB_MTL_GATHER_REUSE | 关 | buffer_cache/buffer_cache.cpp；单draw/dispatch，写/epoch失效 |
| BB_MTL_FRAGMENT_UNIFORMS | 关 | mtl_shader.mm |
| BB_MTL_LIBRARY_REUSE | 关 | mtl_shader.mm |
| BB_MTL_INLINE_UPDATES | 关 | mtl_runtime.mm |
| BB_MTL_RELAXED_MATH | 关 | mtl_shader.mm；关联STRICT_FLOAT=0；结果可变，仍实验 |
| BB_MTL_PERSIST_WARM_ARCHIVE | 关 | mtl_pso_cache.mm / mtl_pipeline_cache.mm；必须有效ARCHIVE_COMPATIBILITY_ID |
| BB_MTL_HOT_STAGING_REUSE | 关 | mtl_runtime.mm；DeviceLocal recent复用，原容量/完成fence |

旧binary archive不控制新本机`.metallib`准备或正常路径owned capture。关闭它不会关闭新默认流程，也不会删除旧文件。TEXTURE_RELEASED_STORAGE仍在CPU明确禁用，未包装成可开功能。硬件TSO和三个context缺口仍不作为可用产品开关。

## 其它功能

| UI功能 | 核对结果 |
|---|---|
| MetalFX/比例/输出分辨率 | 原settings与SceneResolution一致：[0,1,2,3,4,5]→[1,1.5,1.7,2,3,1.25]；MTLFXTemporalScaler；原生off保持原生 |
| 速度图/动态模糊 | effective MetalFX才输出METAL_OBJECT_MOTION；原patch保留速度生产，动态模糊仍由Effects控制 |
| 遮挡剔除 | Metal/GuestInPlace/实验位门控，未绑定MetalFX |
| 超宽 | 原Profile固定container1080p+宽屏patch，effective upscaler off/preset0；外层UpscalerSection已禁用，无新缺陷 |
| 两阶段draw/readback/预上传 | DRAW_PIPE及READBACKS有真实消费；Metal不显示PREUPLOAD选项，脚本采用既定策略 |
| F2/F3/自动Continue | 原Scancode/quick-save helper与重启监督器；F3离线策略/75、76来源校验保留 |
| 正常存档/快速检查点/历史备份 | 原SaveStore/SaveRetention/SaveManager及原--user，没有新存档格式或QA选择逻辑 |
| iCloud | 原Swift启动/退出同步，只同步savedata；QA故意使用fake-home，不能替代正式iCloud上传验收 |
| 官方/社区中文 | 官方原语言+区域解锁；社区英文槽+最后mod覆盖；ARM复用原patch/文件视图，不增加官方字体要求 |
| 普通mod/补丁/目录 | 原合并顺序、禁用、last-mod、XML、ULTRA UI路径；新CoW视图只供严格扫描，final exec用原app0 |
| THD/配对码复制 | 原device-flow、Keychain、THD路由及typed桥；离线不加载网络模块，F3清凭据 |
| 手柄/按键/System | 原SDL主线程泵和映射，无新增白名单；System复用原页面/typed bridge，失败边界见上 |
| FPS/日志/诊断 | 原show_fps/限帧/解锁路径；诊断开销与性能测试分开，不通过持续Cua录屏验收 |

## 验证及剩余边界

Swift release的40项定向测试已通过，覆盖运行时能力/无Rosetta的原生包/错架构拒绝、旧包保留Rosetta要求、开关环境、依赖/偏好迁移和三语。原stage的14项准入控制通过，额外只允许架构日志改动，任意额外wrapper修改仍拒绝。System包装层7项实际原菜单源码/作者边界控制通过（非FEX/游戏）。无游戏、无GUI打开；未用源码命中冒充最新包功能验收。

仍需：冻结这轮CPU-only修复后的新CPU包、当前UI与新准备组件组成完整隔离包，核对实际bundle与上述能力声明；随后一次集中功能/启动/缓存/性能/内存验收。可移植/干净检出构建仍有独立源码收尾，不能称只剩验收。正式应用和Release继续不更新。

## 本轮组包要求

`package_local_arm64_qa.py` 新增必填 `--launcher-products <本轮Shared/evidence下Swift原生Products/Release>`，与`--cpu-runtime-manifest`、`--gpu-app-manifest`、`--game-pso-component`一起显式绑定；不能再默认复用QA生成器里的旧GUI。GUI运行能力由包内`NATIVE-METAL.json`的`game_runtime=fex-arm64`及`local_metal_preparation=auto`声明，UI展示与启动器Profile共用，实际启动仍由原native签名/加载边/缓存校验准入。

CPU本轮最终构建47.09秒，30TU+两AvPlayer覆盖，GPU未重编；菜单新头已进入实际游戏depfile和CPU缓存身份，19作者控制通过。此为built-not-run，新包仍需集中实际运行验证。

GUI签名会随QA Bundle ID改变，不能把开发可执行文件的签名后SHA直接等同于组包后的SHA。组包先确认复制前后字节一致，再在两份新临时副本上移除签名，仅规范化残留的`__LINKEDIT.vmsize`，比较其余全部代码/数据，并对最终App验证严格签名；原文件不改。三语资源逐文件比较，防止后端更新却沿用旧UI。
