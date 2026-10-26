# 原启动器兼容路径的work与progress分离

本轮是实际运行时源码接线，尚未编译或启动native游戏，不是formal路径已启用。现有Shared输入门、QA源user直接子目录门及75/76重开协议不改。

## 源码行为

`game_runtime.cpp` 仍从原最终 `--user` 得到 `state`，先调用原 `BbSession::Prepare(...,resume=true)` 只读验证user内原ARMQA1来源marker；不移动、重写或补建该marker。随后仅launcher-compatible分支调用：

```cpp
BbSession::PrepareLauncherWork(data, prototype_state_root,
                              original_config, boot, content, nonce, identity)
```

返回准确 `BB_DATA_DIR/out/runtime-sessions/<原session nonce>`。data必须是同一固定private state根内owned canonical子目录；config、boot和content须精确是该data的 `bbport.ini/out/boot-linked.bin/out/content.bin`。nonce为32个小写hex，完整identity是调用者刚由boot/content/base game/nonce形成并通过原user marker核验的ARMQA1字节串，最大16KiB。base game而非每次重建的临时mod overlay绑定身份，因此原supervisor的F3重新合并overlay仍可合法复用同一work。

路径创建通过owned root目录fd逐层 `openat(O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)`；out必须已经存在。runtime-sessions与nonce目录新建0700，已有目录必须owned且恰好0700，其他祖先不得group/other writable。新work marker用0600、O_EXCL/O_NOFOLLOW创建并完整写入/fsync；已有work绝不自动补marker，必须读取完整同一identity、EOF及前后dev/ino/owner/nlink/mode/长度/时间一致。marker链接、hardlink、错owner/权限/identity、已有半成品或路径替换均拒绝，不通过重写旧对象“修复”。异常只留下本次新work，不动来源user。

`runtime_work` 对非launcher authored/legacy仍等于原state。兼容分支仅把以下临时/诊断写入work：

- `.arm-module-info.bin`（仍走已在QA09验收的实际CPU codeRoot helper选择及原Python metadata算法）；
- `imports.tsv`；
- profiling启用时的 `cpu-code-manifest.json` / `cpu-code-<pid>.map` / `cpu-cache-environment.json`。

新日志 `ARM LAUNCHER runtime work=<path>; progress=<原user>; existing QA source marker retained` 给外部诊断工具定位。旧user中历史metadata/诊断文件不删除、不迁移、不再用作新输出。诊断工具若读这些路径需改用日志中的work；此路径变化不应被误解成存档目录变化。

`runtime_file_configure(app0,state)`、savedata标题、正常存档与F2/F3、原INI选择、实际磁盘CPU缓存文件及其预算保持。`native_restart.c` 未修改：继续原user门、nonce、backend先回收及原75/76状态。没有往formal user写QA marker，也没有启用任何formal目录。

## 原型策略同步

`launcher_runtime_scope` 的original-launcher-final-exec现在对QA/formal-prototype都把运行marker与module metadata描述为work；user的来源marker只读，不能让metadata helper借user角色写临时文件。非launcher legacy不属于这个profile。`launcher_runtime_scope_observer` 的作者夹具沿用同一策略，但scope结果仍非native授权，正式范围仍拒绝。

## 本轮验证与下一步

- 新 `tests/test_launcher_work_contract.py` 9项源码接线控制通过（0.004秒）：先验证原source再建work、原--user/save wiring不改、临时文件新路径、helper真实codeRoot保留、旧restart门/状态语义保留等。它们不是C++文件系统执行证据。
- 同步后的scope纯数据25项（0.022秒）、文件观测18项（0.243秒）均通过。均只Python与作者临时夹具，未改正式数据。
- 原bundled module helper选择器14项作者控制仍全部通过（0.044秒），实际exe/codeRoot、原parser闭包与Shared门保留。
- 已扩展既有 `tests/test_session_state.cpp` / 原CMake `test_session_state` target，包含首次创建、0700、同identity重用/metadata保留、source marker/进度不变、boot/base game/content变化拒绝、非法nonce/错角色、模式、marker symlink/hardlink/缺失及session目录链接反例。按本轮“不编译native”的要求，这些真实C++控制**尚未编译执行**。

主任务后续应一次统一构建运行该原生小测试，再重编当前CPU并在隔离QA做启动/F3，确认新work日志、metadata注册、原savedata保持和正常退出。可能的兼容变化是此前不一致的BB_DATA_DIR/config/boot路径或不安全目录权限现在明确拒绝；不能拿源码控制通过替代这些实测。最终操作仍有路径式helper与诊断writer的并发边界，formal作用域/初始化前消费/实际fd操作保护仍未接线；不据本改动宣称本机正式替换已准备完成。
