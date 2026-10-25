# 布局错选观测与 GPU 原始字节保全检查点（2026-10-25）

## 当前结论

隔离诊断现在能记录完整的请求/选中布局；未改变 FindImage 选择、上传、dirty、回收或缓存容量。GPU-only 保全在原有 retile 路径中有 padding 边界缺陷；独立 GPU 覆盖合并已消除两个定向失败，但它仍是语义证明，不是游戏修复。白模、长编译、性能目标均未完成。

## 默认关闭的有界观测

`src/bb_layout_audit.h` 只在 BB_ARM_LAYOUT_AUDIT 精确为1时启用，启动打印一次READY证明；每进程饱和计数最多64条不一致，不用会回绕的无限fetch_add。唯一票号在八线程控制中验证。记录owned ImageInfo中的地址/extent/mip/layer、tile/array/pitch/bank/alt、bits/samples、物理属性和每个有效mip布局，以及选中uid/backing、registry/已关联分配代际、flags/binding/角色、GPU tick/write tick。不重新解引用guest描述符，不增加全量表或保留Metal资源。每条最多4KiB栈内文本；超过固定文本容量不落出半条记录。默认关闭不构建文本或修改渲染决策。

插入位置为TextureCache现有trace_selection，调用时仍持原mutex。观测只覆盖同址、同字节数、同extent/资源数但byte layout不同的请求，不是任意overlap或所有纹理错误的普查。

- 6项原生helper控制：关闭/0/1x、八线程票号、开启64条上限、16mip最大文本；通过。
- 13项实际TextureCache/Metal控制：默认关闭无日志、开启后memo/exact/generation正确报请求tile13/选中tile8，重复1000次只64条；同布局/sRGB/合法1D2D/Unmap正控仍不报。原11904/16384像素缺陷继续存在，绝不把控制通过当作行为已修复。
- 私有单对象GPU库基于first-upload GPU基线，SHA807a0c582d775d577e66790df41d30b9b4d0e0cc4472c36e644c014d21fc0dae；基线66d554bd未改。严格签名/ARM64/实际装载边和单份依赖已核对。CPU侧a500宿主库未替换，正式应用不变。

证据：layout-audit-controls-1792906535632831000、layout-audit-candidate-1792906536579689000、最终layout-audit-metal-1792907823612030000。测试构建现在只用仓库已有build_texture_test.py生成的准备元数据，不依赖未公开的subagent命令记录；显式-UNDEBUG保证所有assert实际执行。

## GPU-only 内容保全：通过子集与真实失败

详见docs/texture-layout-roundtrip.zh-CN.md。独立Metal compute写private buffer后GPU blit生成非均匀图案，CPU源/目标仍全跨度保持0x11/0x22；独立CPU地址oracle只负责校验。紧齐RGBA8、若干linear/Thin13/Display9/Macro14、mip/layer/sRGB和1D2D用例通过；24个原始控制中仍有两项真正的目标失败：

- Thin13 64×60→linear：旧raw padding被改1024字节，新布局可见像素错512字节。
- Thin13七级小mip→linear：padding错684字节，目标像素错156字节。

GPU实际定义texel与尾部guard均0错，失败全部落在GPU未写padding。不能让整张retile输出覆盖完整raw，不能把CPU旧数据或CopyImage像素当原始字节权威。

详见docs/texture-layout-coverage-merge.zh-CN.md：新增GPU compute按覆盖mask合并实际retile输出，只替换真实逻辑texel字节，原padding仍0x11。两项旧失败raw/目标错误都降到0，紧齐正控也通过，unmerged/陈旧CPU/CopyImage/跳过GPU生成负控继续拒绝。9项符合预期。mask由独立CPU地址oracle仅提供位置，没有给GPU expected像素；不得把逐像素CPU mask直接用于生产或把测试中的显式完成等待搬进每次draw。

已将原始roundtrip、覆盖merge和GPL CPU地址oracle整合进tests，并复跑13项目标控制：11项达到各自正/负例目标，2项原始retile缺陷明确作为known_defect保留，target_expected仍0、实际7，报告all_preservation_paths_correct=false，不改expected像素凑绿。最终texture-preservation-controls-1792907827241575000。未集成转换到FindImage，未测试深度/压缩/MSAA/3D/部分多源写入。

## 实际游戏三轮与环境变化

同一静音隔离配置、CPU67db33e2、上述诊断GPU，不录屏、不Cua、不测死亡/在线/菜单：

1. launcher-engine-abba-1792906627012001000：场景前17.38秒，被>1GiB/5秒swap保护停止，未通过。
2. 按既有授权只SIGTERM chronod：footprint约1256.6MiB→新进程25.8MiB，压力恢复1。随后launcher-engine-abba-1792906913533410000仍在场景前15.69秒触发相同保护，未通过；不能说widget是唯一原因。
3. 用户关闭其他应用后，swap从此前约10GiB降到约6.5GiB，压力1。launcher-engine-abba-1792907288347180000完成：17.07秒看到实际场景camera，两轮移动/攻击请求，65.53秒按private action trace completed正常由监督器结束。exit143是监督器SIGTERM收尾，不是异常崩溃；alias已恢复，源QA存档SHA不变。无HLE fault，trace无overflow/clock reject。

第三轮READY已记录，但同址同尺寸布局不一致为0。只说明短路线没有命中此类错选，未抓帧、未独立确认敌人接触或白模，不是排除该根因。最高压力仍短暂4（5次采样，首末约1.04秒），未持续至原5秒阈值；swap约+963.4MiB。没有改保护。诊断不作FPS或内存等价A/B，不能说所有内存问题消失。

## 当前本机QA包

打包器曾固定选择旧menu宿主库，与新CPU实际avplayer加载边不一致；已改成验证当前CPU真实LC_LOAD_DYLIB与准入a500哈希，再复制该库，没有退回旧实现。移除旧“25接口含16AV”和旧FPS宣传，原16启动脚本不变；默认静音、离线、物理输入、独立QA，诊断布局/逐帧日志/QoS均关闭。

最新本机包：artifacts/arm64-qa-20261025-contract-checkpoint/Blood-Echoes-ARM64-Local-QA.zip，43339648 bytes，SHA256 c22970cc38eb2a90946327a4a5f80e7525f12ddd22d9b5504a116615badb5032。
CPU上游输入67db33e25dfd65caa0f41cc12da2d76224134aa059d4399652ae00342c1d9c87；CPU宿主输入a5005c5f807c861eddd50a31fcf0e64295a463aeff9d33e5097cddf504ab2783，renderer仍66d554bd原first-upload候选，没有启用新布局转换。

manual QA policy三项、5核心Mach-O ARM64/严格签名、zip813项CRC与禁止游戏/存档/缓存路径检查通过。实际包内CPU的msync正例0、非法输出指针74负例通过，dyld确认只装包内宿主库；证据frozen-contract-package-1792908342063699000。初次手工fixture用了不被允许的嵌套state，被现有私有边界以1拒绝；修测试为原要求的直接state子目录后通过，没有放宽运行时门槛。

这是依赖Shared的host-local不稳定QA包，不是可分发安装器；没有打开新GUI或在包内进行新场景/F3/死亡/在线验收。正式应用、设置、存档、缓存和Release未改。

## 下一步

优先把retile只写源逻辑texel的覆盖规则传入GPU核，保留padded物理寻址；不要新增逐像素CPU mask。修改任何TilingInfo ABI前列出并重建全部生产/消费对象和shader参数布局，再用上述两个失败、mip/layer、合法别名、真实raw权威来源和生命周期正负例验证。源raw可能来自CPU、GPU buffer或镜像，不能直接假设CPU总是最新。之后才改memo/exact/overlap选择，并做真实白模和默认关闭诊断的性能/内存对照。Mlock及四项线程/上下文、完整特化后台准备、硬件TSO授权等原目标仍开放。
