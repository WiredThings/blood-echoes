# 原 GUI 条件派生的独立 benchmark 数据副本

`tools/qa_launch_clone.py` 提供两个CPU接口：

```python
create(conditions, cache_admission, *, project, tag, cache_mode='seeded')
inventory_savedata(path)  # dict: savedata相对文件名 -> SHA256
```

`conditions` 来自原 Swift receipt 的严格 `qa_launch_conditions.load`；`cache_admission` 必须由调用者先执行已有 `qa_gpu_cache_seed.validate` 的真实原生签名/加载边准入。本接口不伪造或替代该准入，不启进程、GUI、GPU，不选择正式数据。project仍精确固定为Shared开发根；测试只patch作者临时根，不增加生产绕过参数。

## 新副本与来源

数字tag长度1–24，三处输出必须全部不存在（含dangling symlink也拒绝）：`P/state/benchmark-user-<tag>`、`benchmark-data-<tag>`、`gui-gpu-local-aot-<tag>`。任何已有输出均不覆盖。校验原source user/data/cache与同app manifest匹配，cache admission还精确绑定source目录、source manifest及SHA、raw upstream GPU和原包内GPU摘要。

- 复制原QA `user/savedata` 的全部普通owned目录/文件；不重新选择存档、不改存档命名空间。每个文件使用≤1MiB流式hash/复制、目标独立inode，复制前后完整文件SHA及目录清单一致，返回前再次复核源。不会用 `read_bytes` 读大存档，也不创建hardlink或指向旧QA的目录链接。
- 原INI/mods/patches三个不可变 `ConfigSnapshot.data` 原字节写入新data的 `bbport.ini/mods.json/patches.json`，源/快照/目标SHA均相同。原mods/patches目录存在时完整流式复制普通owned树；不存在时只在新data建立空目录。
- 缓存复用原 `qa_gpu_cache_seed.clone`，`baseline_root` 明确是**本次原sourceGPU**。缓存内非诊断配置保留原字节，排除旧 `game-pso-progress`，可选额外排除仅允许根级4项 `automatic-game-inputs/captures/log/temp`，绝不允许把 `cache` 或 `local-metal` 当额外排除项。原目录不删、不裁缓存。
- `cache_mode='seeded'` 默认完整复制两类cache；`empty` 只复制同一GPU配置，直接在新根建空 `cache/local-metal`，没有先复制再删除，receipt明确 `source_cache_cloned=False`、copied_files/bytes=0、copied_content_identity=None，不冒称克隆了着色器缓存。

为满足正确性要求，已有cache clone的baseline `shutil.copytree` 改为同一有界流式owned普通树复制，拒symlink/hardlink/特殊文件/错owner；stat包含ctime和nlink，前后manifest及每份目标SHA核对。目录清单在scandir收集时就限制节点总数（100000），避免先收集无限列表才拒绝。旧clone默认参数、两cache内容完整性及计数合同保持，既有17项控制仍全过。

## 原环境保持与显式差异

返回 `environment` 是原完整BB_/MTL_字典的一份派生副本，不重新运行任何前端策略。仅允许变化：

- 8个新data/user/mods/patches/config/GPU路径；
- 新session/restart nonce；
- 测试pad文件及ARM_QA_PAD_FILE、PAD_FILE_ONLY，FRAME_TIMING/FRAME_LOG_ONLY/BENCHMARK_MUTE。

原 `BB_QUICKSAVE`、音频、语言/中文来源、分辨率/特效、全部Metal选项、四保护均保持；不添加PYTHON/PYTHONHOME或系统继承环境。新user的 `.arm-runtime-session` 仍是原ARMQA1格式，boot/content指向新data/out，game保持原私有资产根，nonce与派生env相同。

返回至少包含：`environment`、`data_root/user_root/cache_root`、`pad`、`source_save_manifest`、`config_sha256`、`allowed_changes/actual_changes`、`unchanged_keys/unchanged_environment_sha256`、`source_unchanged`、源snapshot与cache clone结果。源配置和存档复制中有变化便拒绝成功；失败留下的部分目录仅在本次新输出根，不能复用成完成case，接口不会为清理而删除源或已存在目录。

原普通Play脚本会清理部分benchmark遥测键；driver如何在正确边界使用这些显式测试控制由主任务负责。本接口不改shell、runner、Swift或正式应用，不把已派生env当作遥测一定到达游戏的证据。

## 验证边界

新增 `tests/test_qa_launch_clone.py` 20项纯作者控制全过（约0.84秒）；原 `tools/test_qa_gpu_cache_seed.py` 17项全过（约0.22秒）。测试无原生调用，使用作者临时root及普通字节，覆盖seeded/empty、全部源选项保持、独立存档inode、配置原字节/完整mods、无read_bytes、大树预算、链接/hardlink/错owner、源中途变化、错误admission、cache排除白名单及已有输出保护。source save/cache的真实新副本与本夜游戏验证由主任务执行；这些作者控制不证明真实游戏启动、F3或帧数。
