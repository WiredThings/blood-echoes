# 2026-10-27：正常构建工具选择修复

本轮正常CPU构建暴露两项驱动错误，均非游戏崩溃：受限runner的PATH中没有裸cmake；随后缓存身份生成目标使用 `/usr/bin/env python3`，选到缺少 `hashlib.file_digest` 的系统旧Python，尽管CMake已找到3.14。

工具计划现在从已核renderer CMakeCache冻结CMake/Ninja实际路径与SHA；CPU目标显式要求Python3.11并使用`${Python_EXECUTABLE}`，不重新搜索PATH。两个无语言CMake作者控制验证受限PATH仍使用正确解释器、旧Python在配置阶段失败；没有编译GPU或游戏。实际修复构建仍单独记录，不把作者控制当成正常CPU构建通过。

不改变摘要算法、缓存预算、游戏画质或正式数据。旧失败日志保留。仅本机开发记录，Release及发布文档未更新。
