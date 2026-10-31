# Blood Echoes：macOS 血源运行器

[English](README.md) · [下载 Latest](https://github.com/WiredThings/blood-echoes/releases/latest) · [使用指南](docs/USER_GUIDE.zh-CN.md) · [分支改动与第三方来源](docs/THIRD_PARTY_CHANGES.md)

开发分支的macOS 27+ ARM64版本使用FEX游戏运行时和原生Metal后端，无需Rosetta 2；默认增量准备本机Metal库和管线，普通暖启动复用未变化的已完成准备，F3保留原会话快速恢复。最新整合加入缓存／纹理范围修复及默认开启、可关闭的顶点运算优化。它与下文的Latest Release分开，见[复用规则](experiments/arm64-runtime/docs/ordinary-warm-reuse.zh-CN.md)和[本轮来源与验收](experiments/arm64-runtime/docs/upstream-refresh-20261030.zh-CN.md)。

隔离开发包正在接入 [bloodborne-native 的 bbcpu](https://github.com/bloodborne-native/bloodborne-recomp)，作为与FEX独立、可选择的CPU后端：本机生成/编译ARM64静态游戏代码，保留bbcpu自身译码器兜底，不建设FEX与静态库的混合桥。原启动器、当前Metal载荷和FEX选择继续保留。本机全游戏静态编译、已登记代码范围复用与HLE调用快路已通过隔离站桩对照；其余兼容性和正式封装仍待验收，尚非已发布Release，也未改为默认。见[接入方案](experiments/arm64-runtime/docs/bbcpu-integration.zh-CN.md)和[最新快路验证与对照](experiments/arm64-runtime/docs/bbcpu-fastpaths-20261031.zh-CN.md)。

这是 [DiAvisoo/bbport-mac-optimized](https://github.com/DiAvisoo/bbport-mac-optimized) 的
**WiredThings 分支**，提供独立 macOS 启动器、**直接 Metal 渲染**、**MetalFX 时域升频**，
以及英文、简体中文、繁体中文启动器界面。当前 **0.2.51** 修复细分曲面阶段的数据布局错配，
加入默认关闭的相同着色器库复用实验。渲染器可在独立 ARM64 进程中运行，游戏本身仍使用 Rosetta。
此前 0.2.50 的 M4 八轮对照测得原生宿主约 6% 的平均帧率收益；本版不宣称再次获得同等提升。
保留存档历史管理和可选 iCloud Drive 同步。“关于”括号内显示源码 Git 短提交号，下载请认准 Latest。

0.2.36 起为新配置默认采用原版模型细节，并提示强制低 LOD 可能造成墙体或地板缺失。
已有配置若遇到缺失场景，请将“模型细节”改为 **游戏默认（0）** 后重新启动。

启动器本身是原生 Apple Silicon 程序；游戏代码仍通过 **Rosetta 2** 运行，
受支持的 Metal 宿主在独立 ARM64 进程中原生执行，旧路径回退仍使用 Rosetta。项目基于 bbport、shadPS4 和社区渲染器工作，不是 FS／Sony 官方 PC 版。

独立 ARM64 Metal 宿主在支持的 macOS 27+ 安装包中默认开启，高级设置可切回旧路径；macOS 26 自动保留旧路径。
[实验说明与构建方法](docs/release/ARM64-METAL-EXPERIMENT.md)记录了兼容范围和验证边界。

## 安装与启动

需要 **Apple Silicon Mac、macOS 26 或更新版本、Rosetta 2**，以及自行准备的、
**已合并 1.09 更新的解密游戏文件**。安装包自带 Python 和运行库，游玩无需安装 Xcode、Homebrew 或自行编译。

1. 从 [Latest Release](https://github.com/WiredThings/blood-echoes/releases/latest)
   下载 **macOS.zip** 安装包。
2. 解压并把 **Blood Echoes.app** 放进“应用程序”；更新前先退出旧启动器和游戏。
3. 选择同时包含 `eboot.bin`、`sce_sys`、`dvdroot_ps4` 的完整合并目录，
   不要只选择单独的 `CUSA…-patch` 更新目录。
4. 选择界面／游戏语言、输出分辨率和全屏状态，点击“启动游戏”（⌘R）。
   MetalFX、游戏特效、帧率和优化开关位于“高级设置”。

应用使用临时签名，未经过 Apple 公证。若被 macOS 拦截，可在“系统设置 → 隐私与安全性”中
对这个应用选择“仍要打开”。更新合并、按键、存档位置和故障排查详见[使用指南](docs/USER_GUIDE.zh-CN.md)。

## 制作游戏目录

当前运行时针对 **CUSA03173 本体 + 1.09 更新**。模拟器可以分别挂载本体和更新，
这个启动器则需要一个**完整的合并目录**；不能直接选 PKG、未解密文件或单独的更新目录。

1. 准备自己导出的解密本体和同版本区域的 1.09 更新。如果导出结果外面包着 `app0`，
   应进入其中，找到直接包含 `eboot.bin` 的那一级。
2. 把完整本体复制到一个**新目录**，再将更新目录的**内容**覆盖进去：子目录要合并，
   同名文件用更新版替换，本体独有文件保留。包括更新中的 `eboot.bin`、`sce_sys/param.sfo`、
   `sce_module` 和 `dvdroot_ps4` 内容。不是把 `CUSA03173-patch` 整个文件夹放进本体。
3. 在启动器“游戏文件夹”选择合并结果。目录名字不限，结构应为：

```text
CUSA03173-1.09/          ← 启动器选择这一层
├── eboot.bin           ← 来自 1.09 更新
├── sce_sys/
│   └── param.sfo       ← APP_VER = 01.09
├── sce_module/
│   └── libc.prx
└── dvdroot_ps4/
    ├── menu/
    ├── msg/
    ├── map/
    ├── shader/
    └── …               ← 其余本体和更新资源也须保留
```

macOS 可按下面的例子操作，先把三个路径改成你实际解压的位置：

```sh
GAME_BASE="$HOME/Downloads/shadps4-game/CUSA03173"
GAME_UPDATE="$HOME/Downloads/shadps4-game/CUSA03173-patch"
GAME_MERGED="$HOME/Downloads/shadps4-game/CUSA03173-1.09"

mkdir "$GAME_MERGED" && \
  rsync -aL "$GAME_BASE/" "$GAME_MERGED/" && \
  rsync -aL "$GAME_UPDATE/" "$GAME_MERGED/"
```

目标必须是**尚不存在的新目录**；如果已存在，命令会停止。两份来源目录保持原样；
命令不使用 `--delete`，`-L` 会把符号链接指向的文件实体复制进新目录。请预留一份完整游戏的空间。
路径末尾的 `/` 表示复制里面的内容。使用 Finder 时应合并子目录，别把整个本体资源目录
替换成内容较少的更新目录，否则本体独有资源会丢失。

不要选同时装着本体、更新的上一级目录，也不要直接选 `dvdroot_ps4`。`menu` 应留在
`dvdroot_ps4` 内，不需要移到游戏根目录。超宽屏 UI 管理要求 `dvdroot_ps4` 和其下的 `menu`
是真实目录，不能是符号链接。`missing folder or unsupported link: menu` 表示缺少此目录或它是链接，
常见原因是只选了更新、或者合并时把本体子目录整体替换掉了。

仅显示 **01.09** 还不够：启动时还会校验实际加载的可执行映像，并检查着色器解压是否正常。
不能只换 `param.sfo` 却留下 1.00 的 `eboot.bin`；改文件夹名、改版本标记也无效。
本项目不提供游戏、本体更新或中文文本／字体。检查方法见[使用指南](docs/USER_GUIDE.zh-CN.md)，
语言要求见[中文支持](#中文支持与第三方补丁)。

## 本分支做了哪些工作

| 方面 | 相对优化版上游新增／集成的内容 |
| --- | --- |
| 多语言 | 三种启动器语言；独立的游戏语言选择；内置官方中文区域解锁，社区汉化独立导入与校验 |
| Metal 后端 | 将 Huax-1839 的 Metal 引擎接入现有启动器、运行时、语言／分辨率补丁和快速存读档；保留 Vulkan 兼容路径 |
| 时域输入 | 将 IFreemz 的游戏速度图镜像路线适配到 MetalFX；合批速度图绘制，关闭动态模糊时仍保留速度图生成 |
| 内存 | 按图形内存账本计预算，避免重复计算 no-copy 游戏内存；完善 GPU 完成后的回收、闲置上传后备回收、分配代次跟踪和失效采样副本回收 |
| 地址表 | 把固定 1 GiB GPU 平铺地址表改为两级页表，同步修改着色器寻址 |
| 稳定性 | 后台预处理遇到异常资源格式时回到顺序渲染路径重新读取；断言发生时记录线程和调用栈 |
| 渲染效率 | 采样器快速缓存；安全省略无用附件加载；直接清除场景副本；alpha 合并；兼容清除合批、复制／平铺、资源访问与同步细化 |
| 前端 | 最多 36 项 Metal 优化开关、依赖和证据标识；按后端显示适用选项；修正呈现／帧率标签；按 macOS 规格处理并署名图标 |
| Vulkan 兼容 | 保留此前移植的着色器和命令内存修复，使用独立缓存；这些 Vulkan 专用改动不算作 Metal 加速 |

独立启动器、超宽屏界面管理、补丁系统和快速存读档的基础来自上游。
Metal 引擎、原始游戏补丁、移植算法均保留原作者归属。
[来源表](docs/THIRD_PARTY_CHANGES.md)进一步区分上游已有功能、第三方移植和本分支的集成工作。

## 中文支持与第三方补丁

在“**游戏语言**”选择“**简体中文／繁體中文**”，下方才会出现“**中文文本**”选项：

- **官方中文（区域解锁）**：直接使用游戏内置中文，不需要外置字体或翻译包；自动应用
  **Lance McDonald（manfightdragon）、Kyo** 的 **Unlock Game Region** 补丁。
  这个补丁在上游 XML 中已存在，本分支新增了与语言选择的联动。
- **社区汉化**：从 [Supuritto 的 Bloodborne Chinese Retranslation 官方文件页](https://www.nexusmods.com/bloodborne/mods/146?tab=files)
  下载并导入 **GOTY zh-cn（简体）／GOTY zh-tw（繁体）** ZIP 或解压文件夹。
  作者为欧美版提供的 GOTY 包将英文替换为中文，适用于 **CUSA03173 欧美年度版**的语言限制。
  启动器保留所选中文，运行时改用英语（美国）资源槽；简繁汉化包分别保存。它是独立的中文重译，区别于港版 CUSA01363／CUSA03023 的原生中文，
  可按文本偏好选择。

两种方案均在下次启动游戏时生效，不改变配音。社区汉化仅通过临时启动视图挂载，
不覆盖原游戏文件；它优先于其他 Mod 的英文文本／字体，即使总 Mod 开关关闭也能生效。
切回官方中文或非中文语言即可停用，单独导入不会自动启用。

**安装包与仓库不再分发汉化文本、中文游戏数据或字体。** 作者不允许转载其文件，
因此启动器提供官方下载入口和本地导入；下载可能需要 Nexus 登录。
启动前会校验六个英文文本／字体文件，不完整、损坏或简繁不匹配时阻止启动。
官方模式直接使用游戏内置中文，不要求外置字体或翻译包。

其他帧率、分辨率、特效和调试补丁保留 [`patches/Bloodborne.xml`](patches/Bloodborne.xml)
中的作者信息。超宽屏 UI 包和调试字体需要用户另行取得。
详见[中文设置](docs/USER_GUIDE.zh-CN.md#启动器与游戏语言)和[补丁来源](docs/THIRD_PARTY_CHANGES.md#game-language-and-community-patches)。

## 画质、性能与优化开关

- “分辨率”是输出尺寸；“高级设置 → 超分辨率 → 预设”决定 MetalFX 输入尺寸，
  启动器会显示实际场景渲染分辨率。MetalFX 为时域升频，“原生抗锯齿”以 1:1 渲染。
- 支持原生宿主的安装包中 Metal 优化区共 35 项，默认 **22 项开启、13 项可选实验关闭**；不支持时为 34 项、21 项开启。已有的明确选择会保留。
  游戏物体速度图和实验性遮挡剔除分别有独立开关。
- 原有五项可选实验是跨块快照复用、片元参数专用化、计算内核小参数写入、宽松浮点运算、二进制管线归档。
  新恢复的六项实验涵盖原生清除合批、浮层合并、原生填充、驻留记录复用、相同物理地址复制合并和通道内小参数写入，全部默认关闭。
  正确性通过不等于有帧率收益；历史 M4 对照的收益不确定或偏慢。这些选项随 0.2.48 发布。0.2.49 新增深度／模板布局通道延续，默认关闭：M4 对照未证实收益，保留反馈、遮挡计数及同步保护。部分有实测内存成本，见[开关证据](docs/release/METAL-OPTIMIZATION-CONTROLS.md)。
- 本分支主要在 **基础款 M4 Mac mini：10 核 GPU、16 GB 内存**上验证。
  场景、渲染尺寸、特效和缓存状态都会影响帧率，**不保证全程 60 帧**。
  历史对照和内存取舍见[测量说明](docs/THIRD_PARTY_CHANGES.md#measurements-and-limits)。

优化保留已选分辨率、特效和有效缓存容量；速度图等功能仍有资源成本，内存预算也不是进程 RAM 硬上限。
首次进入区域仍可能因资源加载或着色器／管线编译短暂卡顿。此前放弃的离线编译库实验不在正式发行中。

## 实验性联网接入

0.2.51 启动器提供默认关闭的可选“联网”区，通过独立 bbnet 模块在 Metal 后端接入 The Hunter’s Dream。
隔离客户端已验证网站设备授权、游戏服务器公告和圣杯地牢“搜索 → 加入”；留言已完成游戏内
写入、列表查看和删除，尚未由第二客户端确认送达。按铭文直接搜索、合作和入侵尚未验证。
正式版联网、离线共用正常存档。进入联网前归档旧快速存档，并从当前正常存档建立新检查点。F2 在两种模式均可保存，F3 始终离线恢复，直到主动重新开启联网。存档管理区分当前正常存档、活动快速存档与历史备份，支持独立备份和校验导出；配对码可点击复制，联网服务固定为 The Hunter’s Dream。QA 存档只用于开发，永远不参与正式版迁移。自动历史按 3／3／5 保留，手动及标记保留的备份不清理。可选 iCloud Drive 只同步最新正常存档，冲突由用户选择；见[存档与同步说明](docs/USER_GUIDE.zh-CN.md#存档管理)。详见[联网说明](docs/ONLINE.md)。

## 按键、存档与更新

默认 **F1** 设置、**F2** 快速存档、**F3** 快速读档，可点击启动器按键标签重映射。
快速存读档需先开启；读档会恢复磁盘存档检查点并重启游戏，不是完整内存即时存档。
[更多默认键位与存档位置](docs/USER_GUIDE.zh-CN.md#按键与快速存读档)。

设置和存档在应用外，替换完整 App 会保留它们。默认存档根目录是
`~/Library/Application Support/bbport/user`，与已有 bbport 安装共享；隔离试用时可先复制存档目录，再选择副本。

## 从源码构建

需要 **Apple Silicon Mac、macOS 26+**、Xcode 27+（或包含 Swift 和 macOS 27+ SDK 的配套
Command Line Tools）、Apple Silicon [Homebrew](https://brew.sh)、Rosetta 2、Git 和网络。
脚本会下载固定版本的公开依赖源码，原生构建工具、头文件包和 Python 3.11 由 Homebrew 提供。
**不依赖游戏文件、模组、旧版 Release 或本机已安装的 Bloodborne App。**
依赖没有全部内置在主仓库中；这里提供的是联网源码构建流程，不承诺离线构建或产物逐字节一致。

```sh
xcode-select --install   # 已安装 Xcode / Command Line Tools 可跳过
softwareupdate --install-rosetta --agree-to-license

git clone --recurse-submodules --branch optimized https://github.com/WiredThings/blood-echoes.git
cd blood-echoes
export SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
BB_BUILD_JOBS=2 bash tools/macos/build_from_source.sh 0.2.51-source1
```

脚本依次初始化子模块、编译 x86-64 和 ARM64 Metal 依赖及 arm64 Vulkan 驱动、安装固定版本的
SPIRV-Cross／xbyak，构建两种架构的 Vulkan 运行组件、Metal 渲染器和增强运行时，
编译独立联网模块，测试启动器，再打包**包含两个后端、Python 和运行库的完整 App**。
Metal 适配补丁系列会按顺序应用。各组件串行构建，组件内部默认两并发；
内存紧张可改成 `BB_BUILD_JOBS=1`，编译前先退出游戏。
首次会下载并编译较多依赖，请预留数十 GiB 磁盘空间。脚本不会安装到“应用程序”、
启动游戏或修改存档／设置。

产物位置：

- `out/source-release-0.2.51-source1/Blood Echoes.app`
- `dist/Blood-Echoes-0.2.51-source1.zip`：Metal + Vulkan 完整应用
- `dist/blood-echoes-0.2.51-source1-source.tar.gz`：对应源码及依赖
- `dist/source-build-verification.json`：最终打包、签名与动态链接检查结果
- `dist/source-build-SHA256SUMS.txt`：最终 App ZIP 与源码包的校验和

请使用干净克隆；打包器会拒绝未提交的项目文件。再次生成完整 App 时换一个版本后缀，
避免覆盖已有暂存结果。依赖已由此流程准备好后，可运行
`BB_SKIP_DEPS=1 BB_BUILD_JOBS=2 bash tools/macos/build_from_source.sh 0.2.51-source2`。
保留 `.git`：GitHub 自动生成的源码 ZIP 不含子模块；Release 的源码检查包没有构建守卫所需的
Git 元数据。`optimized` 分支包含此构建入口，并继续接收后续修复。

如果 SDK 与链接器不匹配，将 `SDKROOT` 改为当前编译器支持的已安装 SDK，避免旧链接器搭配新 SDK。
分步指令、验证边界和排错见[详细构建指南](docs/release/BUILD-OPTIMIZED.md)。

## 源码与致谢

- [源码、固定依赖与构建方法](docs/release/SOURCE.md)
- [完整分支工作与第三方署名](docs/THIRD_PARTY_CHANGES.md)
- [0.2.51 变更](docs/release/0.2.51.md) · [反馈问题](https://github.com/WiredThings/blood-echoes/issues)

感谢 **DiAvisoo、bmy/bbport-mac、deadinside28、shadPS4、Huax-1839、IFreemz、Awakenchan、
Wooloomooloo2／Mark Hall** 及各依赖组件贡献者。
许可证以 [LICENSE](LICENSE)、各源码文件声明和包内第三方声明为准；
猎人印记图标[单独署名](launcher-macos/Sources/BBLauncher/Resources/Artwork/ATTRIBUTION.txt)。

公开包**不含游戏程序／数据、存档、着色器缓存、下载模组、World Warper、QA App、私人路线或本机采样产物**。
猎人印记和处理后的游戏封面图标是明确署名的例外艺术资源；游戏应用在启动、运行和重开时使用一致的封面，运行时仍读取玩家游戏目录里的封面。[游戏图标署名](tools/macos/resources/ATTRIBUTION.txt)。
上游历史技术介绍保留在 [UPSTREAM-README](docs/UPSTREAM-README.md) 和 Git 历史中。

更名说明：Blood Echoes 原名 Bloodborne Optimized。应用名和仓库名已更新，
Bundle ID、偏好设置域及原有存档／数据路径保持不变，无需迁移。
