# 去除历史 GPU wrapper 模板依赖：原算法复用与实际接线

2026-10-27 更新：已从新的空 build 实际构建正常 `bb-gpu`、235 TU renderer 和两 helper（317.49秒），配对 CPU/FEX（70.04秒）及完整 layout／stager 也通过。renderer raw SHA 仍为 `ec7e9309…af2d`。`prepare_gpu_candidate_app.py --normal-build-result … --source-repository …` 已实际使用正常 main 与原 `create_native_gpu_bundle` 生成新 wrapper，严格加载边／签名通过，manifest 记录 `historical_app_template_used=false`；新 app 尚未运行场景或验证 F3。11项原 helper mock＋4项原包装回归、7项新 main 来源反例通过。旧历史模式保留用于旧实验兼容，新模式不读取历史 app。

## 结论

可以直接使用现有正常 `bb-gpu` CMake target，再复用原GPU应用打包段产生fresh隔离wrapper。无需继续依赖 `P/evidence/first-upload-gpu-app.json`，无需另造前端，也无需重新实现图标或子进程协议。

准确的可复用GPU打包实现在 **R/tools/macos/native_metal.py**；`game_app.create_game_app` 是CPU游戏进程的bundle算法，固定 `bb-probe-metal`，不能原样改名当GPU应用。

## 初始只读检查点（在正常 bb-gpu 构建之前）

- D/vendor/metal_api/CMakeLists.txt:189–192：`add_executable(bb-gpu EXCLUDE_FROM_ALL bb_gpu/bb_gpu_main.cpp)`，链接当前 `bbgpu`，并导出动态符号。193–197把原macOS icon实现链接到DSO。
- 正常clean-source目录为 `P/evidence/normal-clean-source-1793023825033571000`；其build/CMakeCache声明arm64/Release，source为本次快照vendor/metal_api。compile_commands只有一条实际 `bb_gpu_main.cpp` 规则；build.ninja:2939将该主程序链接同build/libbbgpu.dylib。
- 该build已有libbbgpu.dylib（19,346,656B），**尚无bb-gpu输出**。EXCLUDE_FROM_ALL解释了它没有随共享库默认目标产生，不能把“target有规则”称为已构建或可启动。
- 正常快照main与当前D main字节相同，SHA256 `3da09b327a103f7a947cf76dbb133efd928d5a6d5409bb9f311f4cd4def60a3e`。
- 现有 prepare_gpu_candidate_app.py:22/32核验并复制first-upload旧app，随后仅修改其直接libbbgpu依赖。这能证明选中了新DSO，却无法自行证明wrapper来自本次正常源码构建。

下一阶段待root运行结束后，可明确构建正常target（本轮**没有执行**）：

```sh
cmake --build <normal-clean-source.../build> --target bb-gpu -j1
```

需要记录真实main/header依赖摘要、target SHA、架构和实际DSO加载边；不能因为CMake仍有旧对象就省略来源验证。

## 原helper的准确抽取边界（抽取前行号）

`native_metal.stage_arm64_metal` 中：

- **93–106行**：从已重定位的host/bb-gpu复制到 `Bloodborne Metal.app/Contents/MacOS/bb-gpu`，按新可执行位置改写原loader-relative依赖。
- **107–121行**：根据实际依赖最低OS生成GPU专用Info.plist，复制原GAME_ICON，再先签可执行文件、后签bundle。这个区间连同93–106是最小可抽GPU bundle helper。
- **122–128行**：原CPU前端若存在，设置 `LSUIElement=true`。这是CPU/Dock角色策略，不能由独立candidate wrapper helper修改外部前端；只在owned外层staging包原打包流程执行。
- **129–136行**：写NATIVE-METAL能力manifest、执行整个闭包校验。不能原样搬给FEX：该旧函数renderer_architecture仍声明游戏使用Rosetta，当前FEX能力字段应由现有新packager生成。

现有helper签名为 `create_native_gpu_bundle(engine, source_executable, *, bundle=None, version, minimum_os_major=27, bundle_id='io.github.bbport.optimized.metal-game')`，返回新bundle内实际executable。它校验owned/canonical engine、源普通薄ARM64 MH_EXECUTE、fresh目标、loader-relative依赖解析仍在engine；全部准入检查在创建bundle前完成。只生成wrapper，不选游戏、存档、GPU配置或重启策略。默认正式ID与现有formal GPU一致；隔离测试ID由当前QA包策略明确传入。目的必须fresh且owned，不能沿用原函数开头的rmtree去清理已有应用。

`game_app.py:16–27` 的 metadata-free temporary copy→签binary→签bundle→strict verify→copy-back策略可复用，但它目前把执行文件名写死为bb-probe-metal；若抽通用签名helper，只需从明确参数/Info.plist确定bb-gpu，不创建另一套应用算法。

**不能直接全量调用 stage_arm64_metal 然后沿用旧DSO摘要。** 它前半部会复制/重定位/重签DSO和依赖，文件摘要会改变。现有candidate合同要求selected DSO byte-equal，因此应只抽wrapper段；如果最终正式打包确需重定位整个闭包，则沿用原最终freeze流程，重新验证所有最终DSO/worker/receipt身份。

## 最短接线

1. 保留正常同源target＋现有selected candidate DSO合同；候选manifest明确包含本次GPU executable及其main/header/二进制身份，拒绝未声明历史app fallback。
2. 如DSO仍在evidence，照原prepare工具byte-for-byte复制到fresh私有backend目录并核SHA。
3. 把本次正常target复制进fresh GPU app；所有非系统直接依赖指向现有同一闭包。只改wrapper加载边，保持已验证DSO字节不变。
4. 用上述原GPU helper写Info.plist和同一GAME_ICON，按原签名策略重签wrapper；原 `gpu_candidate_identity.verify` 再核薄arm64主程序、唯一真实GPU edge、实际DSO SHA和严格签名。
5. 输出新的candidate-app manifest，供当前package_local_arm64_qa复用。原launcher、supervisor、argv、F3、联网、输入和存档路径全部沿原链，不增加启动器。

当前正常target还直接声明fmt/SDL3/miniz/Zydis/FFmpeg等依赖和build/Homebrew RPATH。只替换libbbgpu edge并不等于依赖闭包已冻结：要复用原 `native_metal` 的loads/relocation/validate规则，检查wrapper和selected DSO的SDL等指向同一份，避免曾出现过的重复Objective-C类。现有gpu_candidate_identity只证明直接GPU edge，不冒充完整传递依赖验证。

## 图标和进程合同

- GPU可执行名 `bb-gpu`，GPU正式bundle ID `io.github.bbport.optimized.metal-game`，CFBundleIconFile=AppIcon，NSPrincipalClass=NSApplication，NSHighResolutionCapable=true。GPU应用不设LSUIElement。
- 资源复用 `R/tools/macos/resources/BloodborneGame.icns`，SHA256 `ad65ced1300409f0ce8564fde829c9f0ea9f4edefe1cf921a31abc5791cf075f`。原launcher继续猎人印记；CPU前端仍隐藏。不能从正在运行游戏或正式游戏目录另取资源来重新设计图标。
- D/vendor/metal_api/shim/window.cpp:103仍由原 `BbPort::SetGameDockIcon()`（同shim/macos_app_icon.mm）在SDL主线程设置同样圆角/留白的运行时游戏封面。bundle默认封面负责SDL初始化前和F3拆窗时，不能丢掉它。
- bb_gpu_main.cpp的main只调用 `BbRemote::Back::Main(argc,argv)`。remote_back.cpp:810校验原pool-fd/pool-bytes/control-offset；:716/876的原WatchParent监听父进程EXIT/EXEC并退出；:1059的RequestRestart仍给CPU发送MsgRestart。必须直接exec/spawn该GPU主程序，不能用 `open -a` 导致父子关系改变，也不能把CPU supervisor的75/76语义移植给GPU。

## 历史wrapper是否有额外代码

只读解析现存first-upload target和其original_target的Mach-O，没有运行它们：

- 两者 `__TEXT,__text` 均200B，摘要均 `d900bd4b0a4b9e73bda8fce6d061a698bfa769c3856c7ed23a9faaf53f93623f`，相同。
- 35个公开main/runtime_*代码入口与当前正常main源码齐全，未发现额外业务入口或缺失；另一个text类型符号只是Mach-O `__mh_execute_header`。
- 现有反汇编main为tail-call Back::Main，bridge stub行为对应当前源码。未发现first-upload特有extra代码delta。
- 旧first-upload/原baseline的AppIcon也与上述GAME_ICON逐字节相同。其bundle ID分别是历史QA专用ID和prototype ID，版本仍0.2.51/0251；这些metadata不应当成当前新包版本模板。

这不是本次新CMake executable的机器码比较：正常bb-gpu尚未生成，所以不能宣称新target已bitwise等同或已验证F3/退出。已知的风险是来源与包装依赖，不是发现隐藏的first-upload运行功能。

## 下一阶段定向检查

只需收口以下差异，不再扩展新的性能课题：fresh创建拒绝旧路径、main/header source身份、ARM64/MH_EXECUTE、唯一精确DSO edge、所有非系统依赖闭包与min OS、原icon SHA/Info.plist角色、签名metadata卫生、失败不覆盖旧app。后续由root在隔离运行中验证父退出/exec和一次F3不驻留旧Dock项；本轮mock测试覆盖原stage调用helper、默认/QA元数据、原icon合同、源/DSO不变、依赖越界、symlink、旧目标拒绝、非ARM/假dylib和签名命令顺序。它们没有真正执行lipo/install_name_tool/codesign，更不能代替实际验收。

当前实际正常构建／配对／wrapper清单见 `evidence/normal-source-build-20261027/summary.json`。正常模式逐项核当前源／plan／完整二进制 SHA／同 DSO；只重定位 wrapper 副本，DSO 保持原字节。原直接依赖按实际 Mach-O edge 复制；最终 QA 仍必须经原 package freeze 把 CPU、GPU、worker 的依赖合并到同一份闭包，不能把源 wrapper 的单一 GPU edge 检查当作完整包传递依赖验收。
