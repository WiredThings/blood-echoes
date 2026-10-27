# 私有自动死亡回归

`private_death_contract.h` 和 `private_death_runtime.h` 是测试源叠层，正常运行时没有 include，组包代码闭包也不包含 tests；不进入本机正式应用或 Release。不是游戏 Mod、存档或可分发的游戏资源。

本轮仅重编当前正常构建的 `game_runtime.cpp`，复用其余实际 CPU/FEX 对象与同一 Metal 后端。叠层在原开发专用 `ArmTravel` 的 FlipperUpdate 回调内读取当前角色与两套生命值；只在原私有目录、owner/session nonce、file-only pad、quicksave=0 门控通过且角色稳定后接受连续序号 1、2。生命周期和显存补丁仍保持原配置。

使用旧私有工具已验证的 1.09 SetHp 函数及其入口指令校验，不直接覆盖 HP。因现有 `bb_arm_call` 只传递整数寄存器，调用前通过 FEX 执行一个显式清零 XMM0 的 guest thunk；不将 x86 地址作为 ARM 函数指针。钩子完整保存/恢复原 guest 寄存器。签名、容量、角色或健康范围不符均拒绝。

通过标准为两次各自观察到 HP=0、world-down/loading、重新获得健康有效且 HP>0 的角色并持续稳定，之后再观察 12 秒。第一次状态、过期心跳或仅收到命令均不能视为重生完成。源 QA savedata 在前后做摘要校验，正式数据不参与。此次两轮的回执见 `../evidence/preparation-automatic-regressions-20261027/summary.json`。末段帧日志还确认在第二次重生后仍有有效场景绘制，但不替代人工检查贴图、战斗、菜单或联网。

纯契约测试（无需游戏、GPU 或窗口）：

```sh
clang++ -std=c++20 -Wall -Wextra \
  experiments/arm64-runtime/tests/test_private_death_contract.cpp \
  -o /private/tmp/blood-echoes-private-death-contract
/private/tmp/blood-echoes-private-death-contract
```

本次本地构建/驱动脚本为 workspace 的 `work/build_private_death.py`、`work/run_private_death.py`。它们明确绑定已核验的本机正常构建回执，不作为公共通用启动流程，不绕过正式包身份检查。两次早期控制端失败保留回执：缺原 shell 导出的内存环境参数、帧日志头尚未刷新；后续改为执行原 shell 参数策略并重试有界帧读取。失败不能被计为游戏回归通过。
