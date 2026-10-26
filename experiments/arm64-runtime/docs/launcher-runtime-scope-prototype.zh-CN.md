# 集中式运行角色作用域：纯数据原型

本轮只新增 `tools/launcher_runtime_scope.py` 和作者控制，没有把正式目录接入FEX、GPU、Swift或原启动脚本，没有改现有Shared门。它是后续三个实现共用**数据合同**的候选，不是正式准入已完成；不会把 `production=true` 或一个环境flag当授权。

## 与原实现的关系

已先核对 `src/session_state.h`、`tools/qa_launch_conditions.py`、`tools/launcher_local_aot.py` 和 `docs/qa08-local-promotion-admission.zh-CN.md`：

- 原 `BbSession::Prepare` 接受Shared state直接子目录并使用精确ARMQA1 marker；原QA路径不改。
- 原GUI receipt只描述 `Process.run` 前环境，还没有原shell合并mod之后的boot/content/patches与最终argv，不能把它单独升级成native正式票据。
- 原 local preparation wrapper已有正确的最终exec边界和原参数保留语义，作用域必须在这里接收**实际参数和原选择**，不能重做前端策略。
- 当前源码的模块metadata helper已经开始按实际CPU角色定位随包helper；该QA改进不等于正式数据/root门已接通。本原型不退回旧P helper，也不替代另一任务的helper收口。

## 有限接口

```python
validate(document, *, selection, observed, final_argv, session, uid) -> PrototypeScope
load_shared(descriptor, *, project, **same_facts) -> PrototypeScope
PrototypeScope.classify(actor, role, path, operation) -> dict
```

`validate` 是纯数据算法，连formal目录的stat/resolve都不做。独立参数含义：

- `selection`：原GUI与最终shell已确定的code/data/out/user/gpu/game/mod/work等准确路径、原home、三个配置路径；来源标明 `original-launcher-final-exec`。
- `observed`：调用者持有的实际bundle、组件SHA、CPU/GPU/helper实际加载边、逐节点canonical/uid/no-link/nlink及文件SHA事实。此参数不从同一descriptor中取出冒充外部证明。
- `final_argv`：实际将交给CPU的完整原native argv；含原boot、--content-profile、--patches、最终--app0、--user和原可选开关。
- `session`：原supervisor另持有的scope UUID、session/restart nonce，避免拿descriptor自报的旧nonce认可自己。

当前结果**恒为** `authorization=False`、`native_integration=False`、`filesystem_verified_by_this_reader=False`；`classify` 只给 `structural_candidate`，叶子解析仍明确未验证，不返回open/exec授权。没有生产启用参数。`load_shared` 只读固定Shared开发 `state/evidence` 内owned canonical无额外hardlink的≤256KiB descriptor，拒绝重复JSON字段；不会读取descriptor里formal路径的文件。任意正式目录descriptor在打开前即拒绝。

## schema1的主要约束

闭合字段集合、显式 `prototype_only=true/production=false`，profile只有 `qa-shared` 和 `formal-prototype`。

| 角色 | 原型约束 |
| --- | --- |
| code | 实际 `.app/Contents/Resources/bbport`，launcher在本app `Contents/MacOS/bbport`；代码访问只能匹配清单内精确组件文件，不能因同目录而接任意程序 |
| data / out | 原选择一致，out严格为data/out；读取data仅三个已绑定配置，不准整个data当泛目录授权 |
| user | 默认或原自选目录精确保留，不能是整个HOME、其祖先或与out/cache/work重叠；不给正式user写QA marker/module metadata |
| gpu | 原GUI选择的GPU根，formal不向其旧cache namespace写入 |
| gpu_namespace | formal严格为原gpu下 `native-arm64-<compatibility-id>` 新命名空间；QA保持原Shared根，不改现有cache策略 |
| game / mod | game是原选择，mod是同game或原out直属 `mod-game-*` 最终合并视图；不能拿未经mod的game替代最终--app0 |
| work | 精确为out/runtime-sessions/<session nonce>，formal的session/module-info/restart临时文件只对应此角色，progress仍是user |

所有role根必须有独立owned/canonical/no-link目录事实；文件必须owned、nlink=1、SHA与实际观察一致。正式ID仍是 `io.github.bbport.optimized`，不能复用QA bundle或QA可写根。Shared profile仍强制P/apps、P/state和私有assets边界，不打开原QA authored/development入口。

CPU可明确选顶层或原嵌套Game.app，两者只能绑定**此次真实选中路径**。CPU/host、GPU/DSO、PSO/module worker、metadata helper为必需组件，Python/net module可明确列入；每个角色只能用其准许的bundle相对位置。CPU→host、GPU/两个worker→GPU DSO的实际加载边必须与独立观察一致。bundle launcher与NATIVE-METAL清单本身也绑定SHA。

最终argv逐项完全一致并绑定版本化摘要；重复、未知或角色不符的原参数拒绝。boot/content/patches及三个配置分别要求实际文件SHA观察。对原timeout、strict-imports/cpu-only仅接受现有CLI语义，不另造启动器。

兼容namespace的id由source closure SHA、driver SHA、compiler/profile SHA和device ID组成。摘要格式跨语言可实现：UTF-8域名+NUL，再逐字符串 `uint32 little-endian字节长度 + UTF-8字节`。argv域为 `BB-FINAL-ARGV1`，cache域为 `BB-CACHE-SCOPE1`；后者顺序为source/driver/profile/device。source/profile实际含哪些ABI/编译键应由现有coherent manifest与profile reader提供，不能拿任意字符串冒充实际身份。

## 威胁模型与尚缺的真实接线

按本机同一用户安全边界设计：主要防止路径/角色混淆、遗留QA选择、错组件/错缓存、过期会话和不小心扩大目录。nonce用于作用域隔离，不被描述成防同用户恶意程序的密码签名。能改写本用户程序、组件或直接操控进程的恶意同用户不在本原型可保证的边界内。

外部observed事实在纯数据测试里是作者输入，不是密码学证明，也不是本reader已经检查真实文件。后续接线须先由已有native签名/加载边/组件封口提供实际事实，在启动器发起的会话内经继承fd或等价owned精确handoff传递；不能让任意env flag自行mint formal scope。GPU全局构造器必须在第一次目录访问前取得这份已核合同，main之后才检查过晚。

目录根有no-link事实不意味着所有后代解析安全。尤其原mod view内部有已知来源链接；必须复用原 source-view/overlay映射校验，逐叶子确认受限实际来源，而不是用本原型的字符串前缀匹配跟随任意链接。最终文件打开/写入还须目录fd、O_NOFOLLOW、fstat前后、精确角色归属；nonce+旧stat不解决TOCTOU。当前 `classify` 因此始终标记 `leaf_resolution_verified=False`。并发F3准备要保留原supervisor先回收再重开；每实例work不可串用，关闭离线建库也不能跳过组件/作用域准入。

原型没有mkdir正式新namespace/work，也未证明其父目录的原子安全创建；仅结构上要求已观察目录。尚需实现跨Swift/native/Python的同schema reader、实际事实采集与handoff、初始化前消费、按fd的最终操作保护，并用隔离数据验证后才能接正式路径。这不是本轮完成项，不能把当前文件直接当作正式准入开关。

### 最短生产接线顺序（尚未实施）

1. **选定来源，保持原参数。** 原 `LauncherModel.environment/startPreparedGame` 产生初始选择；复用已完成的Swift receipt出口获取同一原env，但只能把它当选择记录。原 `run.sh` 完成mods/中文last-mod/boot/link/patch后，在现有 `launcher_local_aot.prepare_launch(executable, arguments, env)` 位置取得真正final argv与原文件。不要在Swift提前猜最终overlay，也不改变原 `--user`。
2. **在bundle边界获得独立身份。** 复用 `game_pso_component.load(stage)`、`warm_archive_manifest.resolve_gpu_identity`、`gpu_candidate_identity.verify` 的组件SHA/签名/精确DSO边/完整layout检查逻辑，适配明确formal code role而不是删除其旧QA根门。`game_pso_owned_audit.load_canonical_layout` 保留原严格ABI/source校验；不能把组件manifest自己的SHA字段直接抄成observed。当前几个loader的production/root合同仍有限制，复用算法不表示它们已经接受formal。
3. **采集确实打开的节点与源视图。** `qa_launch_conditions.Reader` 已有owner/O_NOFOLLOW/前后stat/stream SHA可复用实现，但其固定QA `load` 不扩到formal。新的受限适配须先绑定原GUI的准确角色，再按这些角色打开所需文件；原source视图继续走 `launcher_source_view` 的最终选择/overlay/source一致性检查。原配置和boot等实际打开后的摘要、实际组件观测，与descriptor候选分开保有。不要把同一份JSON的自报owner/SHA再传一次就称独立观测。
4. **先隔离工作文件和兼容cache。** 原user继续保存真实进度/快速存档，不把它改成work，也不把全部 `State` 语义指向work。为module-info/session/restart等单独传准确work destination；QA仍旧分支。按原GPU根安全创建新compat namespace与session work，创建/父目录fd核验在正式启用前还需定向控制；原旧namespace不试读清空。`BbSession::Prepare` 与 `native_restart.c` 的formal分支必须消费同一作用域和原75/76协议，不能另造恢复语义。
5. **在第一次文件访问前分发同一事实。** 增加原supervisor持有的有界继承fd或等价owned精确handoff，明确把允许的scope/nonce交给CPU、独立GPU和helper；包内Python与native reader使用同一schema。GPU `UserPaths` 静态初始化前、helper工作目录创建前就检查，不能在main晚补。实际CPU角色继续用已有 `BbGuestModules::ActualHelper/SelectHelper` 的真实exe定位方法；formal扩展也须绑定code manifest，不能回退P。自动建库关闭时仍先做组件/作用域准入。
6. **再做隔离后才推广。** 使用作者临时目录模拟正式默认与自选user、嵌套/顶层CPU、scope传递丢失、错角色/边/nonce、F3原语义、overlay来源及cache保留反例；随后在隔离数据里做真实初始化/功能验收。只有这些实际消费接线完成且验证后，才讨论正式应用替换。当前25项纯数据控制不覆盖这些生产步骤。

## 本轮定向控制

`tests/test_launcher_runtime_scope.py` 25项全过（末轮0.023秒）：默认/自选user、Shared与formal隔离、整HOME/祖先拒绝、生产flag/未知字段、bundle/清单/组件SHA与实际边、显式嵌套CPU、owner/symlink/hardlink、来源/profile/cache身份、旧nonce、user/work分离、最终argv与输入摘要、out/mod旁路写、不可变且不可授权结果、有界Shared JSON读取等。formal候选正例中对 `Path.stat/lstat/resolve`、`os.open` 和native subprocess设置调用即失败，证明验证只用了作者数据；Shared reader测试只读作者临时目录文件。

未启动native/GPU/游戏，未编译运行时，未读写正式存档/设置/cache，未替换本机应用，未发布Release。
