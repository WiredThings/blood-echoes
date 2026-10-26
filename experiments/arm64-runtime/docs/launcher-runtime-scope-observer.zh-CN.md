# Shared运行作用域的真实文件观测层

`tools/launcher_runtime_scope_observer.py` 是前一版纯数据scope的独立文件事实生产者。本轮只在作者临时夹具执行，未接正式路径、未改既有QA门、未启动native工具/GPU/游戏或编译。

```python
observed = observe(document, selection=original_selection, final_argv=actual_final_argv,
                   session=supervisor_session, uid=current_uid, project=fixed_shared_project,
                   native_results=already_obtained_loader_results)
candidate = launcher_runtime_scope.validate(document, selection=original_selection,
    observed=observed, final_argv=actual_final_argv, session=supervisor_session, uid=current_uid)
```

返回的observed可直接交给现有纯数据validator；scope仍 `authorization=False/native_integration=False`。它不是正式目录放行器，也不做open/exec授权。

状态策略现已与original-launcher-final-exec源码同步：Shared QA也将运行时module metadata/工作marker指向work，user中旧QA来源marker仅只读验证；非launcher authored/legacy的旧state语义不在此profile内。观察器仍不创建work或写user，不因为这项源码接线就升级为native授权。

## 限界与实际读取

在任何resolve/stat/open之前，先只按词法检查 `qa-shared/prototype_only/production=false`、原selection相同、P/apps内实际code角色、P/state的data/out/user/gpu/work、P兄弟assets内game及精确最终overlay。formal profile、形式正确但指向正式目录的role/组件均先拒绝。原final argv解析复用 `launcher_runtime_scope._final_inputs`，没有另写近似前端策略。

只采指定的9个role根、实际Info/launcher/NATIVE、清单的7–9个精确组件和6个最终输入；不递归扫user、cache、game或整个HOME。目录采当前owned canonical/no-link/inode事实；文件继承 `qa_launch_conditions.Reader` 的O_NOFOLLOW、前后fstat/lstat、64KiB流式SHA与有界JSON/重复字段检查。新增 `StreamReader` 只记录逐文件预算并复用相同算法：组件最多512MiB且不保留内容，launcher/final输入最多64MiB，Info/NATIVE最多1MiB。

所有文件摘要现场计算，**不把document.expected SHA交给首次读取**；bundle ID从真实Info读取，launcher和NATIVE的摘要从实际文件获得。完成后重核所有内容摘要及文件/role目录的dev/ino/uid/mode/nlink/size/mtime/ctime，拒绝期间变化或同字节inode替换，再调用原validator比较预期与真实观察。所有role叶子事实均来自实际stat，不把descriptor的canonical/owner字段抄回。

## 独立原生结果的有限接口

observer不执行新的 `otool/lipo/codesign` 或helper探测。调用者先通过已有准入获得：

| native_results键 | 真实生产者与核对字段 |
| --- | --- |
| cpu / gpu / pso_worker / module_worker | 已有 `warm_archive_manifest.resolve_gpu_identity` / `gpu_candidate_identity.verify` 返回字典；精确target/library路径、target/library SHA、ARM64、strict_signatures和load_edge_verified，全部须与本轮实际文件观察吻合 |
| preparation | 已有 `game_pso_component.load(stage)` 的真实结果；enabled、isolated profile、worker/module/library路径及三个SHA须与本轮实际组件相同 |
| compatibility | 调用者独立采集的source closure SHA、driver SHA、compiler/profile SHA、device ID；由原prototype的同一算法计算id，不能从document复制期望值 |

CPU→host、GPU/两个worker→GPU DSO的observed边仅来自上述独立结果。缺结果、旧摘要、错目标、签名/架构失败、准备组件不一致均拒绝。这里的“注入”是现有同进程调用者交入已有验证结果，不是一个能防恶意调用者造假的密码证明；不能让任意未校验JSON填写几个true来替代原生loader。作者测试的native结果明确是夹具，未实际执行签名/Mach-O工具。

## 准确剩余缺口

1. 本模块尚没有接真实final-exec边界，也没有采集QA08/新包的完整最终scope。原GUI的启动receipt仍只是初始选择，final boot/patch/content与mod view要由原shell完成后提供。
2. compatibility的source/driver/profile/device四字段需一个来自现有coherent/profile/driver事实的**明确适配器**。已有verify结果本身只证明二进制/签名/边，并不单独提供driver与profile closure；不能据其缺字段而复制descriptor或填占位摘要。本轮对缺完整记录明确拒绝，未冒称该适配器已经存在。
3. `game_pso_component.load` 等旧loader的QA/production限制未修改。正式路径需要前一文档所列真实角色准入与初始化前handoff，不能直接把此observer的Shared限制删除。
4. 当前统计前后比较能拒绝作者测试中的替换/变化，但它不是长期持有directory fd并为后续所有文件操作代开fd的授权层。路径父级恶意并发替换与未来操作之间仍有TOCTOU边界；同用户恶意进程不在这里可保证的安全模型内。未来真正formal消费必须使用已有/新增的锚定目录fd、逐叶子受限解析和准确overlay来源映射，不能将字符串前缀或一次stat当永久权限。

## 定向结果

`tests/test_launcher_runtime_scope_observer.py` 18项全过（末轮0.256秒），真实打开作者临时普通文件并生成独立observed，再交原validator核对。覆盖错误expected SHA不会变成observed、Info/组件真实摘要、固定Shared边界在IO前拒绝、symlink/hardlink、原生注入缺失/过期/失败、真实准备receipt配对、compat不自报、复用重复JSON拒绝、读取中变化、同字节inode替换、role目录替换、stream重核后晚替换及不使用read_bytes。subprocess三个入口全部被设置为调用即失败。

没有native/GPU/游戏实测，没有正式存档/设置/cache读写，没有替换本机app或发布Release。
