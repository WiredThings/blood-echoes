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

后续90秒冻结组件无抓图冒烟通过，数值和边界见本报告末尾。不能把包验收等同当前白模、F3、菜单或在线全面验收。

## 对下一步的约束

本轮已确认组合探针实际接入且能覆盖受击相关路线，但仍未复现用户的偶发白模，不再把同一暖路线零编译重复测当作解决长编译。下一步补齐现有缺失的GCN→IR、IR→SPIR-V与模块总计时，与已存在MSL/Metal-library/PSO和FEX计时关联，使用有界门控和自造shader控制；之后仅私有新应用缓存比较冷/暖新内容，不清现存缓存、不常驻全量归档。

FEX相同guest PC的跨线程IR重复已有上游CompileCode的二次LookupCache检查和明确减少全局锁竞争注释，不能凭三条重复PC就另造共享JIT/大锁。保留完整回退的游戏特化与资源代际/写入范围协同仍为长期目标；不全局裁ISA或松TSO/页保护。

25个未接导入、完整ARM clean-checkout构建和用户醒后验收仍未完成。目标继续active，正式app/数据不改，代码只同步dev/arm64-runtime-audit，不同步Release。剩余额度最近42%，低于15%停止并完成交接/最新本机QA包。


### 冻结组件收尾冒烟

launcher-engine-abba-1792888283578587000：新装载准入接入后的原冻结包90秒静音、无Cua/抓图冒烟通过，17.51秒出现scene camera，末段48.39FPS、p99 22.73ms、GPU19.45ms，聚合footprint5828MiB，最高warning2，系统swap约+105MiB。实际GPU direct load edge和签名/SHA均记录在result，没改封装包或设置。这是启动路径/新验证器不破坏正常入口的回归，不是新增性能收益、去重RAM或完整游戏正确性验收。

补充准入回归gpu-candidate-identity-1792888744088525000：最终12项真实Mach-O控制通过。新增代码页篡改后重新声明正确文件哈希仍被严格签名拒绝的负例；签名失败现在带出有界实际原因，避免只见笼统CalledProcessError。未改游戏执行或封装包。
