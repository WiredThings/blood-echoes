# Owned shader 基础构建验证检查点

2026-10-25，尚未接scheduler，不宣称探索卡顿或白模已修复。

- 全量230 GPU单元重建成功；候选4e285c9b9df6f2d54e7d5558f7051ea844f7baef4d7474bd7eb70ea64ade61b9，证据owned-shader-gpu-build-1792914074256475000。先后暴露OwnedSpirv RuntimeInfo需{}初始化、overlay旧路径错位；已修正，后者复用现存且与原启动器字节一致的runtime_quicksave_bindings.h。旧库不变。
- first-upload-regression-1792914664168395000：真实GCN/owned源毒化/异线程独立context/独立Metal/detached模块不入表/只读缓存等控制通过。第一次因测试gpu目录未预创建被私有状态保护78拒绝，未绕过保护；后续创建私有目录成功。child_max_rss包含fixture编译，不是测试独立内存峰值。
- actual-shader-phase-controls-1792914761358088000：原路径两次真实GCN编译probe off/on SPIRV一致，与保存的参考623decccd1e59958cf087381db5a03d4a07ffbf50f161f3f368f1a2d78b99083一致。仅自造compute，不覆盖全游戏shader。
- texture-preservation-controls-1792914935759646000：24原始GPU字节/alias/mip/padding/negative控制符合预期。
- frozen_gpu_headers.py从Git f349取source-only输入，按原revision属性规范化声明EOL后逐Git blob校验；旧库诊断不再依赖新Info头。19旧库控制在texture-authority-order-1792915111521456000仍符合原已知缺陷/正负预期。
- 静音场景冒烟launcher-engine-abba-1792915191293494000完成：17.85秒进入，观察66.19秒，143为监督收尾，无严重压力，swap+828.94MiB。**用户看到固定脚本反复砍墙；这不是有效探索/战斗回归。不得拿它证明白模、真实探索负载或FPS收益。** 后续暂停该盲目动作脚本，先做无窗口接入；没有新价值的场景重复不得继续。

下一步仍是首见程序/新spec安全捕获、已有DrawPreparation实际提前量、有界单worker/slot、完整身份重验与GPU adoption、取消和内存/性能对照；不能缩成仅同draw搬线程。Mlock接入补丁已完成二次审查但公共三文件尚未应用，实际C/FEX例未执行，正式版不变。raw预写需真实payload gate与映射租约，Tracker元数据锁不等于实际guest内存生命周期。

验证基于开发分支94de6bb5；随后按精确清单导出本次shader源码，提交状态以Git为准。普通QA包仍c22970cc，不含此候选，Release不更新。
