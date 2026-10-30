# 2026-10-30：LRU 与管线缓存 IO 上游修复

本次仅移植两个小范围修复，不改变缓存容量、纹理内容、缓存键、存档或准备准入规则。

## 来源与差异

- shadPS4 [#5570](https://github.com/shadps4-emu/shadPS4/pull/5570)，已合并；合并提交 `3b53747f5b0bb7124f7aa2c80a848684c0b56c92`，PR head `fb4f088b469f0d180c2fbcf0375aff94891a3cae`。`std::invoke_result` 是 trait 类，不能直接拿来与 `bool` 比较；改成 `std::invoke_result_t<Func, ObjectType&>`，按实际传入的对象左值取得回调返回类型。达到既有预算后 `true` 可正确终止遍历；并未增减任何回收预算。原回调自身已阻止预算外删除，不能将此修复解释成避免“无限驱逐”。
- shadPS4 [#5577](https://github.com/shadps4-emu/shadPS4/pull/5577)，调查时仍开放；PR head `b99240c5a057002d20aab0fab7a10667ac6bae0e`。生产者在 `submit_mutex` 下增加请求计数，但 IO 消费者此前无相同锁保护地递减，存在数据竞争／丢失更新。按 PR 增加 `<atomic>`，改为 `std::atomic<u32>`，条件变量谓词显式 `load() != 0`。原子默认顺序不放宽，队列仍由 `m_request` 保护，生产提交与条件变量等待仍沿用 `submit_mutex`。本地临时文件原子替换和开发抓取归档锁全部保留。

上游的其它游戏／Linux 帧率报告不代表本机收益。本次没有宣称已经定位到真实运行中的 IO 空转。

## 无游戏定向验证

执行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 experiments/arm64-runtime/tests/test_upstream_cache_prs.py
```

2026-10-30：11 项通过，约 5.5 秒。测试只在 `/private/tmp/be-upstream-cache-*` 编译小型 C++23 程序及读写自造文件，结束自动删除，不访问正式缓存、游戏或应用数据。

- 包含实际 LRU 头：bool 预算停止、仅接受对象引用的回调、void 回调、包含等于 tick 的边界、空表，以及遍历中跳过／Touch／Free。
- 将实际 `cache_storage.cpp` 的计数／队列声明、`ProcessIO` 与 `WriteVector` 函数体逐字抽取编译；仅替代设置、日志及 IO/zip 依赖。8 个生产者并发提交 512 次写入，真实临时文件逐个核对 payload、数量、无残留 `.tmp`，消费结束计数为零且队列为空。
- 空闲等待可被停止唤醒；已进入活跃消费的批次在停止请求后继续排空。
- 反例：将 LRU 恢复成旧 trait 时，实际执行的预算测试失败；将计数恢复成普通 `u32` 时，原子接口及类型合同编译拒绝。

这不等于完整引擎、ZIP 归档或游戏性能验收。ZIP 数据压缩没有在小型控制中执行，留给现有构建／隔离集成验证。

## 如实保留的既有停止边界

`ProcessIO` 在外层循环开始或条件变量醒来后发现 stop 时，会保留尚未开始的请求；如果 stop 在当前消费批次内到来，则该批次按原行为排空。定向测试覆盖了“预先停止但有一个待处理请求”的情况，明确检查计数仍为 1、队列仍为 1、文件未写，未通过清零计数或丢弃队列掩盖它。

该行为早于本次移植。本修复不承诺 `DataBase::Close` 对所有并发提交提供新 flush/drain 合同，未扩大为关闭协议重写。任何后续修改应另行审阅生产者停止顺序和重复 Open/Close，而不是将它混入此原子计数修复。
