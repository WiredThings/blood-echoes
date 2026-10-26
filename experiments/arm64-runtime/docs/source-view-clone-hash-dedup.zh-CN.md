# 首次 source-view CoW 克隆期间的重复读去除

2026-10-27，开发候选；尚未用本轮源码测真实启动耗时。根任务已测 QA09 的首次 source-view 约 217.65 秒，本轮只针对该阶段中间重复整文件 SHA，不改变原 mod 合并或静态目录算法。

## 原路径与修改边界

首次生成时，每个逻辑文件原有六次整文件读：initial records 的 source SHA，clone 前 source SHA，clone 后 destination/source 两次 SHA，最终 `verify_snapshot` destination SHA，以及最终 `verify_live_selection` source SHA。

现在只去掉中间三次整文件读，以 canonical 路径、O_NOFOLLOW、owned/regular、准确记录大小的 fd 元数据检查替代。仍只调用原 `clonefile`，不增加 copy/hardlink fallback；destination 必须为独立 inode、单链接，保持文件 0400 与最后目录 0500。原预算、逐文件及剩余完整哈希中的保护检查、失败不发布 READY/指针、原文件不改、旧 generation 不删除的行为保留。

**权威内容检查仍是三遍，未用 metadata/mtime 代替 SHA：**

1. initial records 扫描原完整映射并对所有 source 做 SHA，形成内容基准/身份；原 `file_sha` 的哈希读前后 stat 稳定门保留。
2. 克隆完成后，原 `verify_snapshot` 检查整个快照完整文件集合，每个 destination 的全部 SHA/大小必须与 initial records 相同。
3. 原 `verify_live_selection` 在原 mod 映射和 selection 的前后两次核验之间，对全部 source 再做 SHA/大小，必须仍与 initial records 相同。

元数据检查不会产生内容 ready 证据；仅三遍完整验证均通过才沿原逻辑发布 READY 和 pointer。只删除冷路径多余读；**已有 generation 的暖复用原本就是这三遍，本轮不减少暖复用 SHA，也不能据此声称解决所有暖启动时间。**

## 为什么中间 SHA 可去除

令 R 为 initial records 的全部 SHA，C 为原 CoW syscall 产生的独立快照，S 为最终 live read-set。采用条件仍为 `hash(C)=R` 且 `hash(S)=R`，同时快照完整文件集合及实际最终 app0 的源映射/原 mod 选择一致。假设原哈希抗碰撞，删除中间读不会让不满足这些条件的数据通过。

- 初读后、clone 前改动：克隆捕获新内容时最终 C≠R 被拒；若 clone 捕获旧 R 但源保持新内容，则最终 S≠R 被拒。
- clone 时改动：不依赖“并发写绝不会产生混合内容”的额外假设；捕获任何非 R 的字节都在最终 destination SHA 被拒。
- clone 后改源：C 即使仍等于 R，最终 live SHA 仍拒 S≠R；把 mtime 恢复、保持相同长度也无效。
- 坏 C 后把 source 恢复为 R：仍因 C≠R 被拒，不会只凭源已恢复而接受。
- 源暂时变动又恢复 R，且 C 也是 R：原采用数据在内容上等价，不需要仅因中间发生过改写而拒绝。
- 字节不变但实际 mod link 改目标/selection 变化：原最终 mapping/recipe 门仍拒绝，不能拿内容相等替代来源相同。

这是保留原最终采用门的证明，**不是源目录连续不可变的证明**。最终 live 扫描本身不是对整个可写源树的原子事务；某文件在其最后哈希后才改变的竞态，旧路径同样存在。本轮既没有新增这种保证，也没有删除能覆盖该时段的原保护。快照仍是独立 CoW，发布后使用经过完整 SHA 的 snapshot，正式最终 exec app0 参数不变。

## 作者验证

`tests/test_launcher_source_view.py` 26 项通过，使用全新临时目录与小型作者内容、原 mod 脚本和实际 APFS clonefile syscall；没有启动 native 可执行文件、GPU 或游戏，也未碰 QA09/Shared 游戏源/正式数据。

新增精确读取计数：冷路径每个 source 两次 SHA（initial/final live）、每个 destination 一次（final snapshot）；暖路径同样计数，且不再 clone。保持所有 snapshot 文件/目录 readonly。反例包括同长度并恢复 mtime 的源改动、源恢复而 clone 仍坏、直接篡改 clone、snapshot 验证后改源、额外 snapshot 文件、克隆后源变链接、hardlink 伪装克隆，以及晚期原 mod 选择/映射变化。既有中文最后层、禁用/相对路径、原算法身份、源外链拒绝、坏旧快照保留及不支持 clone 时不复制等原控制继续通过。

尚无真实时长收益结论；读取次数降低不能直接换算秒数。文件仅 `tools/launcher_source_view.py`、原 tests 与本开发说明；不改 native/清单/发布文档。
