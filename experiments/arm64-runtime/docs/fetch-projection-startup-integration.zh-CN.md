# 本机首启自动准备的接入契约

入口是现有 `launcher_local_aot → launcher_game_pso`，复用原参数、正式启动语义、队列、MSL 本机 builder 和最终 PSO worker，不建立第二套启动器。静态扫描由 `launcher_game_catalog.prepare` 产生 immutable catalog/index；`launcher_game_modules.prepare` 接在原 candidate builder 之后、原 PSO queue 之前。

## 可调用 API

```python
module_inputs = launcher_game_modules.prepare(
    static_context, project=project, caches=selected_original_caches,
    component=component, cohort=cohort, check=existing_safety_guard,
    environment=original_environment, selected_renderer=original_gpu,
    backend_root=original_component_root,
    candidate_manifest={"path": str(original_manifest), "sha256": manifest_sha},
    page_size=16, seconds=600)
```

`static_context` 提供 `catalog/index/game_root/source_root/cache_root/identity/cohort/gpu_root`。仅当前有效的原始游戏／最终 mod 视图参与扫描，游戏文件、存档、设置与原缓存不被修改。带 SHA 的 `candidate_manifest` 复用刚完成的原始 cache inventory，不重复全扫描；逐行读取，校验原来源目录、所有权、非链接、前后文件身份和完整 SHA。

消费者组件必须是 coherent 非诊断产物，并包含 `module_consumer/module_consumer_sha256/library_sha256/native_layout/backend_root`。Python 自报标志不构成证明：实际 consumer 和本次选定 renderer 的唯一 Mach-O GPU load edge 必须指向同一 owned ARM64 DSO，严格签名、路径与 SHA 都通过；native `--capability` 必须真实返回版本化 automatic-root／nonce／cohort 能力。每次 CPU 子进程又以 dyld 的实际 PID 记录校验加载 DSO。

当前 6 TU override consumer 是定向证据，不能仅改 manifest 标志冒充 coherent 发布组件。尚无合格打包组件时返回明确 `unavailable`，既不调用旧 targeted DSO冒充，也不更改原游戏启动参数。

## 自动输入与持久化

控制器从原 candidate readset 解码真实 metadata，按 `(program_hash, native hardware stage, code SHA)` 静态事实关联。完整 plain、独立 owned GS-copy、可唯一证明的 fetch projection 分别走原冻结函数；不再依赖手工 CLI sidecar 或“下一批”清单。模糊转换、动态资源／基址、tessellation 等未闭合输入明确记入 gap，不能使用静态默认值硬凑完整管线。

`module_input_identity` 绑定完整 raw Profile／metadata／参考 SPIR-V、静态代码、游戏、ABI 和逻辑／硬件阶段。fetch 额外绑定本次真实 PSO key 与唯一转换证明；无需 fetch 的模块不因其它附件状态重复编译。同一完整模块可以关联多个原 PSO。此身份不是原 `HashCombine(program_hash, permutation)`：后者仅用于当前 cache 的 stage/key 关联。编译器／组件 DSO／cohort 是独立的生产者身份，不能只凭旧 Profile/spec 一样就认可历史 SPIR-V。

默认在同一次首启中连续处理 16 项一页，**16 不是整个启动的上限**。总时间、系统压力或锁屏保护触发才停止；稳定 index 可在下次启动自动续作。新输入、native receipt、SPIR-V、stage→input 与 recipe→module 关联保存在选定 GPU 的 `automatic-game-inputs/<compiler-identity>/`，不会进入原保存目录。关联账本流式写入并有总量界限；不建立大型常驻源码缓存。原生处理单次总输入仍 ≤2 MiB、每子进程 ≤30 秒且串行执行。

原生 CPU consumer 的证据根门仍保留。自动模式仅允许 `BB_GPU_USER_DIR` 是隔离 `Shared/.../state` 下 owned canonical 子目录，输入／输出各自位于它的 `automatic-game-inputs` 后代并互不重叠。packet 的 nonce 和完整 cohort 必须与本次授权环境精确相等；state 根、正式目录和链接均不准入。没有把 evidence 伪装成 GPU user dir，也没有绕过原 DSO 构造器。

只有完整重建的 SRT、实际特化与逐字节 SPIR-V 全部相等才发布模块 receipt。旧 producer 无法证明或字节不同继续拒绝。GS 完整结果经 `game_pso_geometry_adapter.freeze_recipe` 再独立绑定原 Profile／metadata／stage／copy，返回 resolver 交回原 PSO queue。CPU 模块成功本身不等于 Metal library／最终 PSO 已准备，也不授权旧 cache 的 runtime import。

## 正常游玩中的缺口

CompileModule 捕获只覆盖新编译，warm cache 命中可能不会经过它。正常 `GetProgram` 在已有 `RefreshFlatBuf/StageSpecialization` 读取期间可被动旁观，随后用实际 `perm_idx/perm_hash` 关联；准备 worker 的 lookup-only 分支不能作权威来源。最终 graphics/compute key 在原函数中建立完整 stage 映射。原始 SPIR-V 的真实 storage 来源、SHA、producer/patch 身份仍需绑定，不能把按 hash 找到的旧文件当当前编译器输出，也不能为了采集清缓存。这部分由正常路径 owned companion producer 提供，控制器不重新读取 guest 指针。

静态目录完整并不代表每个条目已经有真实资源、fetch 和附件状态。报告分别列出静态身份数、已观察到完整输入的身份、尚缺实际编译态的身份，以及本轮 native 完成／拒绝／预算待续数；不会用候选数冒充最终可执行覆盖。

## 本轮验证边界

- fetch 15 项 Python 控制、typed API 15 项 C++ 控制、25 项原生反例；4 VS +2 Local 使用实际翻译／发射 TU 后逐字节匹配。
- controller 10 项无私有素材作者控制，含 33 个假造状态在同次首启连续三页、原 manifest 复用、其它 cache／改 SHA 与不同 DSO 拒绝。
- 新 automatic-root native 门：1 个此前已验证的实际 compute 模块自动根正例、6 个路径／nonce／cohort反例通过。state 根及别名被原构造器 code 78 先拒绝，与 main 中的 code 65 拒绝明确区分。
- 已完成 coherent 构建和 native-only 主函数修复，但尚未进行 GPU、游戏或性能对照，也未更新正式发布。首轮 admission 脚本误以为所有拒绝都有 main JSON，被既有构造器挡住后停止；该失败证据保留，修正控制脚本后在新目录通过。

私有证据：`evidence/game-module-native-1793007502946128000/build.json`、`evidence/module-automatic-admission-20261026-02/result.json`（均在 Shared 开发根）。公开只保留去内容摘要。coherent 组件构建、真实默认首启和最终 Metal 产物复用的整体回归由集成阶段统一验收，不能由这些定向控制代替。

## 完整 owned companion 分支

默认控制器现还消费 `static_context.capture_root`，不要求用户手工导出下一批。它由同一 coherent module consumer 的独立 typed inventory kind 调用 `Store::Keys/Read`，逐 profile 校验真实 game/compiler/ABI／code registry／association；Python 不解析裸 blob 头、不恢复 guest 指针。随后每个完整 key 进入独立 typed compile kind，调用原 `CompileOwnedToSpirv`，核验 runtime-after、完整 canonical StageSpecialization 和实际 observed SPIR-V。

此分支的 `program_base` 来自真实 owned Snapshot，可在纯数值寻址修复中使用；receipt 用 `program_base_from_owned_snapshot` 表示来源，**不复用旧 unknown-program-base 拒绝标志**。完整 input integrity、磁盘 blob locator 和真实 runtime `program_hash/permutation/permutation_hash` 各自保存，不能互换。

已有原 PSO 的 raw specialization 只进入原有有界 spec-only 读取器（不含 Info/SRT tail），再与 companion 的完整 canonical spec 比较，包含 fetch 全 9 字段；原 stage/perm、实际参考 SPIR-V、Profile 和静态 code SHA 也必须一致，才把该模块关联回原 PSO 队列。无匹配 PSO 的完整模块可先准备并持久化，但不会声称最终 PSO 就绪。GS 专属 adapter 保持独立校验，typed fetch receipt 不伪装成旧 GS receipt。

这次新增 9 项 typed receipt／通用调度作者控制，已通过：真实 base 与 unknown-base 合同分离、runtime permutation 不可替换为 input ID、缺失 runtime/spec 等值门拒绝、不同 compiler／packet、改 SPIR-V、漏 target spec 均拒绝。实际 tiny authored GCN 的原生重译及负例需使用全量 coherent DSO，由 `run_game_companion_contracts.py` 一次执行；该测试不含实际游戏数据，也不能代替全游戏覆盖和最终 runtime 回归。已在同一 coherent DSO + main-only repair 上通过 10 项：native inventory、带完整 target-spec 校验的 actual GCN→SPIR-V byte equality，以及 8 个实际反例。

本轮实际过程中，旧 codec 的两字 sentinel 不是完整执行 shader，作者执行测试现使用三字独立结束指令。原 `Archive.TakeOff()` 返回的容量包含 fetch serializer 的未写预留，作者导出按实际写入 cursor 截取，未放宽 spec-only 检查。多余尾字节反例会干净返回 65，输出事务仅回收本次独占创建且 inode 未变的未提交文件；保留 75/76 原语义。失败的 01/02/03 私有证据保留，最终通过记录是 `typed-companion-native-20261026-04/result.json`。

通用 typed converter 另以 21 个作者状态验证默认遍历不会退化成 4/2 代表样本限制；第二次使用同一稳定 index 时 21 项全部重核后复用，不重复 native module 编译。这是调度控制，不计入实际游戏／原生 shader 覆盖数量。
