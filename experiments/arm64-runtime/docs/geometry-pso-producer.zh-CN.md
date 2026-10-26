# GS 最终管线输入生产者

2026-10-26。`tools/complete_geometry_psos.py` 是独立的 GS 生产链；普通 schema1 的 GS 拒绝规则不变。没有启动 GPU、游戏，也没有修改正式应用、存档、设置、缓存或 Release。

## 实际目录闭合

对 cross35 的 GS gaps 逐条以 **实际 cache 路径 + 完整 recipe_blob_identity** 定位，再复核同 cache 的 profile/key/全部阶段 metadata/SPV 的字节数、SHA 与整组 frozen identity。GS 由已有 `owned_geometry_inputs.freeze_geometry` 重新核对原 GCN 及同 entry 的 copy GCN；ES/FS 由原 `game_module_inputs.freeze_module` 核对原 GCN。没有按 program hash 粗配不同特化、permutation、profile 或其它 cache。

实际计划结果为 **77/77 recipe，0 拒绝、0 缺失**，生成 **44 个精确 CPU module 输入：14 GS、14 ES export、16 FS**。每个 module 保留所有实际 recipe/stage 使用关系；去重包含完整 profile/metadata/SPV/spec 摘要、hardware/logical stage、permutation/hash 和 serialization ABI。

Shared 证据：

- `evidence/geometry-exact-plan-1793005400000000000/plan.json`
- `evidence/geometry-current-layout-1793005172605317000/result.json`

layout 是本轮从当前头文件重新编译并运行的小型 exporter，只有 system C++ 依赖、没有 renderer。12 个当前源文件 SHA 均经原 `load_canonical_layout` 核验；与旧 cache 的数值 sizeof/offsetof ABI 完全相同，未放宽 source identity 校验。

**计划成功不是 CPU 重译通过或最终 PSO ready。** 这 44 项必须由当前实际 module-preparer 和固定 compiler DSO 执行后才能进入下一步。

## 默认启动链的接点

主路径继续使用原 `launcher_game_pso.prepare` → 原 candidates → 原增量 queue → 原 `prepare_game_psos` 的 record／本机建库／prepare，不要求用户或开发者逐批手动跑 GS。新的 `game_pso_geometry_adapter` 只转换已经由通用 source controller 生成并准入的 GS 输入，不另造启动器。

内部调用约定：`launcher_game_pso.prepare(..., prepared_inputs=context)`，其中 `context['geometry_resolver'](unit, layout, check)` 返回稳定的 owned schema2 packet ref 或 `None`，`context['cache_root']` 是 controller 的隔离持久目录。该 resolver 是程序内 callable，不从外部 JSON 执行代码。源目录扫描、模块 CPU 重译及其增量索引由通用 controller 负责。adapter 在原候选扫描后严格校验完整 recipe/cache/profile/stages、receipt、copy/span，合入同一 manifest，保留原 input identity。原 GS gate 与其它排除条件不放宽。

`prepare_game_psos.execute` 识别 `owned_geometry_pso` 后，只把已核验 refs 克隆进本次新 scratch，并注入当前 cohort、原选定 `artifact_cache_dir` 与本次 allow_compile，随后执行同一 worker。队列及 actual strict hit 标准不变；resolver 缺失或返回 None 时保持原 GS 排除，并报告 not-provided／missing，不宣称已经完成 GS 自动覆盖。

CPU compiler 的 `BB_GPU_USER_DIR` 必须位于新的 `P/state/geometry-module-consumer-*` 子目录；evidence 仅放日志、冻结输入和结果。已修正先前 evidence 路径导致 constructor 78 的开发控制器错误，没有修改 native 严格目录守卫。反例覆盖 evidence 路径、state 根本身及软链接拒绝。

## 开发控制器命令记录

以下是生产者底层 API 的可复现实验记录，供自动 controller 复用；不是用户首启流程，也不作为继续逐批手工跑 44 项的主线。

以下 `D` 为当前 `experiments/arm64-runtime`，`P` 为 `/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype`。`BUILD` 必须是本轮非 diagnostic 的 module-consumer build manifest；不能用历史结果或仅 profile/spec 相同的旧 binary 代替。

```sh
python3 "$D/tools/complete_geometry_psos.py" run-cpu \
  --project "$P" \
  --plan "$P/evidence/geometry-exact-plan-1793005400000000000/plan.json" \
  --build "$BUILD" \
  --output "$P/evidence/geometry-cpu-FRESH"

python3 "$D/tools/complete_geometry_psos.py" freeze \
  --project "$P" \
  --plan "$P/evidence/geometry-exact-plan-1793005400000000000/plan.json" \
  --results "$P/evidence/geometry-cpu-FRESH/result.json" \
  --cohort "$CURRENT_VERIFIED_PSO_COHORT" \
  --gpu-root "$ISOLATED_GPU_ROOT" \
  --output "$P/evidence/geometry-pso-packets-FRESH"
```

`run-cpu` 不建 Metal Device：每个子进程重新核对实际 game GCN 来源、固定输入文件 SHA，执行原 owned CPU compiler；记录实际 PID 的 DYLD image 并验证唯一 compiler DSO 的真实路径、SHA、owned inode/extent。所有成功输出还须满足现有 SPV 完全字节相等、当前 generated SRT、未知 program-base 拒绝，以及新 receipt 的 packet/code/spec/profile/program/stage/copy 绑定。每个 child 30 秒上限，继续保留原内存/锁屏监控和 owned process-group 清理。

`freeze` 重新验证 build/binary/DSO 和全部 receipt/输出文件；只为所需各阶段都通过的 recipe 生成独立 schema2 GS packet。span 16 字节先确认全零，旧指针不会传入 native reader，copy 仅作为来源身份核对。每个 stage 的 module metadata 与原 recipe 必须完全相同，GS 的 original spec hash 和 ordinary spec hash 分别严格匹配。缺一模块便保留拒绝原因，不伪造成功。schema2 packet 的所有文件 ref 位于各自新冻结目录，沿用原 key/layout/flags/目录合同。

`freeze` 仍然输出 `native_PSOs_ready=false`。后续统一 native batch 要实际验证 record → 本机库 → prepare、三个 ES variants、GS kernel/pass-through/FS、五个最终 artifact 与 strict archive 暖命中；本工具不运行该 GPU batch。

## 作者短测试

`tests/test_complete_geometry_psos.py` 的 8 组作者控制覆盖逐项缺失／错配 receipt、GS copy extent、profile/spec/metadata/SPV 混配、相同 program 不同 permutation/ABI、跨 cache stage identity、空模块计划、伪造 ready 标记与严格 state 目录。另有 `tests/test_game_pso_geometry_adapter.py` 的 7 组作者控制覆盖原队列输入身份不变、完整阶段／permutation、schema1 拒绝、跨 recipe／目录／链接拒绝、resolver miss 保持排除，以及本次 scratch refs／cohort／artifact／compile 策略注入。其小型 buffer 均为自造，canonical 二进制解码在该作者测试中仅替换为已有 input_identity 校验，不把它冒充真实 GCN／Metal 证明；原 native 47 项合同测试另行保留。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 "$D/tests/test_complete_geometry_psos.py" -v
```
