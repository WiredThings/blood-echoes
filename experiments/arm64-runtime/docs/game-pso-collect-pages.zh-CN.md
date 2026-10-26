# strict lookup／record 的有界页调度

## QA07 的实际根因

`P/state/game-pso-worker-1793030163155326000/result.json` 的实际结果是 409 recipe 完成，1227 个 native recipe 请求、844 个页进程，297.61 秒墙钟：

- collect 阶段 409 次 `prepare(false)`，全部 strict miss，`source_fallback=true`、artifact deferred，native PSO 编译计数为 0。
- 每次 miss 后立即切 `record(false)`，共 409 次。这使前 818 个请求逐条 prepare／record 交替，每个页进程都仅处理 sequence 1。
- 最后 409 次 `prepare(true)` 已正常分成 25 页×16＋1 页×9，即 26 个进程。
- 因此 `409 + 409 + 26 = 844`。capability 已正常准入，没有隐藏重试或失败。

初始 Local 集只有 22 个已准备请求；最终新增 431 个，总计 453。最终 501 个 native artifact attempt 中 111 hit、390 built；52 是“整个 recipe 都没有 built”的计数，不能当成 artifact hit 数。Local builder 两次调用合计 151.78 秒，因此不能将全部 297.61 秒归因于启动 844 个进程。

## 最短改动

仅重排原 `game_pso_queue.run_manifest()` 的 collect 页内顺序：

1. 使用原 `phase_page(..., 'collect')`，有效页窗口最多 16 个 item。原 ≤64 的 CLI 参数仍接受，但实际窗口 clamp 到 16。
2. 先逐项执行整页原 strict `prepare(false)`。真实 hit／Local inputs ready 继续立刻按原规则落 SQLite。
3. miss 仅暂存该页的 item 引用，至多 16 个；不复制 payload，不保留 owned shader/read-set bytes，也不积存整页 native outcome。
4. 再对这些 item 执行原 `record(false)`。每次仍重新 freeze 原完整输入、核摘要、执行原 worker 验证。首次 record 仍只调用一次原 `local('record')`。
5. 该页完成后再取下一页；最终的一次 Local builder、最终 native prepare 遍历、environment 验证和 commit 完全不变。

不改 worker、packet、native descriptor、artifact、driver／binary／source 身份或任何 runtime 检查；不引入磁盘 ready shortcut，`ready_hint` 继续没有准入权。

## 预算和中断

`max_recipes` 在取入页时限制新 item；strict 和后续 record 属于同一个已计数 work item，不重复收费。record 每条仍检查原全局 deadline、锁屏和压力，预算不会重置。取入部分页时不跨出已计数的窗口。

保护中断立即停止。尚未 record 的临时引用不落 ready，也不引入新的持久化成功状态；原 SQLite 中未收集的项仍可续跑，后续重新核验。已经完整 record 的项保留原进度，最终仍必须经过当前 native prepare，不能靠旧 miss／hint 变成 ready。

## 验证边界

新增 8 项纯 CPU mock runner 控制通过：409 cold 页顺序与请求数；hit／miss／Local inputs 混合；strict 和 record 阶段保护中断及恢复；坏包拒绝；显式 recipe 预算；record 中途时间预算；旧 64 页请求实际限制 16。原 strict-first 16、cross-page 15、queue 19、incremental 11、跨 Python 进程 resume 3、readiness 5 项也通过；没有 native／GPU／游戏／编译测试。

mock 的全 miss 409 项仍执行 **1227 个请求**，排程模型为 `26 strict + 26 record + 26 final = 78` 个页进程。这是作者模型的计数，**不是实际 native 性能测量**，也不会减少原 shader／Local 编译任务。实际 cold 启动时间、内存和真实进程数须由 root 统一集成后验证。
