# 2026-10-30 上游移植独立源码审阅

审阅范围：#5524 的子资源判定与重建／释放调用链、DiAvisoo `8e678b6f` 的可选 UI 补丁检查，以及 Metal invariant-vertex 候选。主审工作树原 HEAD 为 `00385145`。本审阅未修改其它组源码、未运行游戏或 Cua、未操作正式数据。

本人编写的 FEX #6074、LRU #5570 与 cache IO #5577 不在本独立结论范围，由主代理另审。下列源码审阅不冒充完整游戏或持续性能验收。

## #5524：发现的 P1 已补正

最初的三处 Contains 替换仍遗漏 `texture_cache.cpp` 的深度重建：

```cpp
new_info.resources = std::max(requested_info.resources, cache_image.info.resources);
```

缓存 `{8 mip,1 layer}`、请求 `{4 mip,6 layers}` 时，Contains 会正确要求重建，但字典序 max 仍选择 `{8,1}`。后续 FindImage 再次拒绝缺层图像并释放，可能丢弃刚复制保留的 GPU 内容并产生无用分配。

该处现已逐维合并 `max(levels)`／`max(layers)`。只改变覆盖判定与所需资源 extent，原 GPU/CPU 权威判断、rebind、Free/Track 顺序保留。Metal 同格式 CopyImage 按两端 mip／层数最小值拷贝，不因 union 直接越界。

### 跨颜色／深度旧支持域：未发现新断言回归

`renderer_metal/mtl_runtime.mm` 的 CopyImageWithBuffer 既有合同是层数相等且 `min_mips == 1`。不能把它的旧限制误称为本次新回归：

1. 旧目的 extent 是字典序 `max(src, requested)`，其 mip 数必然不小于 src；故旧复制成功必然要求 src 只有一个 mip。
2. 旧成功同时要求旧目的层数等于 src；在这个条件下，逐维 union 的层数仍等于 src，mip 数与旧 max 相同。
3. 所以所有符合原断言的成功输入，union 后仍符合断言。

`src={8,1}/requested={4,6}` 旧路径已经违反单 mip 合同；`src={1,1}/requested={1,6}` 旧 max 也早已选层数6，已经违反等层数合同。本次没有证据证明此前合法支持的复制因此失败。

因此本轮保留明确旧能力边界，不扩 conversion scratch／多 mip 转换接口。新增 Contains 可能更早暴露原先错误复用的输入，不能宣称后端已支持任意跨格式 extent。后续 GPU 用例单独记录：单 mip、等层数的已有双向路径；增长层数、多 mip 的已知不支持路径，不将后者悄悄清空、跳过或当复制成功。

## 可选 UI 检查：未发现新增阻塞

- game_folder 移除的是可选 UI 目的地前置检查；普通 Play 仍由原 gameCheck／资源准备完整性检查负责。
- active／pending 安装仍经 check_current → current → check_menu 检查；启用 backup、恢复及逐文件替换前仍拒绝 dvdroot_ps4／menu 链接，单个 gfx 文件仍由 lstat 拒绝链接。
- 未管理／已禁用备份不再因可选目的地缺失或链接误阻 Play。活动资源被改坏仍阻止运行，备份、持久 pending journal、失败回滚和 Disable 恢复路径保留。
- 只读源根目录的既有 resolve 行为未改；不能将它与写入目的地链接混为一谈。
- Swift warning 与 blocksPlay 分开；已启用而下载源消失仍可恢复。新增 warning 不会替代真正的阻止条件。
- Python 的真实自造文件 IO、中断和回滚测试不是仅实现镜像。Swift 新测试检查实际 Check／allowsPlay 行为。

## Metal 候选：一项生产 codec 遗漏已修复

初审发现 Canonical Profile 末字节扩展为0..3，但 fetch_register_store.cpp 的生产 ValidateExpected 仍把全部尾部字节当作 bool 强制<=1，导致真正的 Store::Write／Read 拒绝新值2/3。此问题已报告并修复：前面的普通 bool 保持0/1，最后字节只接受已命名掩码，未知位拒绝。

独立复核了修复后源码和新增真实 Store 写读正反例：2/3 发布与读取、与旧 false Profile 不匹配、4..255 Encode／Write 拒绝。没有通过删掉校验或只改测试预期绕过。新的 feature 常量头不再引入 Shader/Profile，低层 store 的 CPU 依赖边界保持。

其余已核对链：

- 同一 stage 谓词同时控制浮点 contraction 和 Position Invariant；仅软件 Vertex + 硬件 Vertex，且 relaxed fragment/compute 与本功能都开启，排除 export/local/tessellation/geometry。
- 库选项由实际 SPIR-V 的 Position/Invariant 装饰决定；缓存 MSL 与新转换路线均传 preserveInvariance，不凭环境变量伪造该选项。
- FunctionOptions 写入 preserve_invariance；Source／Local 路线均将该实际选项纳入完整函数 identity，MSL 缓存还包含实际 SPIR-V 哈希。
- schema3 使用独立 recipe、显式 true 与独立 key；Python loader 严格字段／类型／原始 key／MSL 摘要校验，实际编译命令含 -fpreserve-invariance；回执和 portable product key 保留选项。
- 默认 off 的原 schema1/2 key、原 namespace 和 canonical0/1语义保留；UI 关闭时省略新变量并清理继承的开发值，避免仅新增默认0字符串造成选项身份变化。
- Profile80字节 ABI 和原字段 offset 保留；准备端 raw bool 和 typed decoder 已接新字段。扫描器新增的是编译时局部向量，未看到大型常驻表或新的逐绘制全量校验。

上述静态链未发现其它明确的 source admission 放宽、function identity 漏项或默认开关误启问题。

## P2（已修复并关闭）：合法旧 raw Profile 的 padding 复用

初审版本的 `pipeline_cache_core_serialization.cpp` WarmUp 把旧80字节 raw Profile memcpy进当前 Profile，再按成员 operator== 比较。新 relaxed_vertex_float 位于旧 padding 的74偏移；旧 padding 不属于逻辑字段，旧合法记录可非零。此处为已报告并随后修复的问题记录。

已用 `git show HEAD:.../profile.h` 的实际 LegacyProfile 声明、当前 Profile、真实 clang++ 编译无 GPU 反例：给旧 padding74写1，旧成员比较仍相等，而新比较变为 vertex=true：

```text
old_equal=1 new_equal=0 new_vertex_flag=1
```

这证明不能无条件承诺所有合法旧 raw Profile 在默认 off 时均命中。更需注意，实际 cache_storage.cpp:378–389 的 DataBase::Clear 是逐个删除缓存目录中的 regular files，非保留式 rename；若进入该不匹配分支，具有删除旧产物风险。没有在真实缓存触发此分支，也未证明本机旧记录有非零 padding。不能简单将任意 true 归零，因为真正 invariant 产物必须保持兼容性隔离。因此修复不能把实际删除写成保留迁移，也不能简单忽略真正不兼容输入。最终已采用下述显式 wire 方案，不再留下该本轮已知风险。


### 最终独立复核：显式 feature wire 闭环

新增 `shader_recompiler/profile_cache_wire.h`：默认 false 仍输出96字节（16版本头+80 raw body），旧88／96字节只读旧 named 字段，不解释 padding74；显式新 feature 为116字节（16版本头+20 envelope+80 payload）。shader/meta/key 版本没有泛化升级。

独立逐行确认：

- Decode 先检查完整长度、版本、feature mask、原始 bool 字节，再 memcpy，避免将未知字节读成 C++ bool。
- 新 envelope 保存真实 viewport width；payload首 word 使用超过合法 viewport 范围的固定 marker，Decode 再恢复 width。直接截断或删掉20字节 prefix 后仍会触发 feature 格式识别并拒绝，不能降级成合法 legacy。
- legacy padding74任意0..255均不成为新 flag；新 true 与 default false 继续按真实 Profile 不匹配，未通过归零真实 feature 伪造兼容。
- core WarmUp、实际 module preparer、geometry PSO gate、Python semantic decoder、开发 companion fixture均已使用共享 wire；未发现遗留的这些实际入口仍 memcpy旧padding进新字段。
- 合法 legacy 匹配不调用 Save，也不调用 Clear，不修改旧 blob。真正不兼容／损坏分支仍沿用原规则，本轮未扩大缓存迁移协议。
- canonical74字节中的旧0/1、Local schema1/2键与默认 off namespace不由此改变。

审阅了真实 WarmUp 判定前缀编译测试（内存 Storage 仅记录调用），覆盖全部256个旧 padding 值的 Clear=0／Save=0／原字节保留；也检查 C++ codec／Python投影的显式 true、坏 feature／version／width、全截断与删 prefix 反例。本次独立复跑 `test_upstream_vertex_cache_wire.py` 两项控制通过，未运行游戏、GPU或打开真实缓存。

**最终未发现新的阻塞 finding；原 P1 与本 P2均已关闭，可进入主代理的干净提交与正常闭包构建。该结论不代替完整构建或隔离游戏验收。**

## 集中验收边界

agent 提供的库构建／像素测试、主代理的 Swift 测试应以各自实际回执为准。此文不把它们改写成独立重复执行结果，也不代替完整引擎编译、隔离游戏对照、F3 与场景视觉验收。

## 集中测试薄层：已修复冻结CSV误认完成

独立复核 `run_upstream_refresh_game.py --vertex-ab`：同一新应用中A保留默认off，B在既有环境准入后仅加入唯一 `BB_MTL_RELAX_VERTEX=1`；F3保持A，A／B有独立私有case。source receipt不改写，可选UI helper只有已审完整旧／新双SHA投影，未开放其它脚本或环境。原生样本字段与 `matched_summary` 对齐，保护中止仍不算采样完成，清理限定已拥有进程。Cua仅运行前status检查，不启动服务，性能阶段无截图／录屏。此项静态审阅不代替实采。

实际找到的阻塞是原driver场景识别后停止读CSV，只等待墙钟超时；末尾冻结的旧30秒尾部仍可能被标记sampled。现由实际 `FrameProgress` 每秒有界读尾，跟踪文件device／inode、大小、单调帧时钟和真实推进观察；要求完整测量墙钟截止后再次推进，且源时钟覆盖完整区间。文件替换、缩短、时钟回退、旧CSV或无有效推进均拒绝。

原生writer每120个flip至少fflush一次，因此允许10秒后批刷新窗口；这是采样有效性容忍，不改变严重内存压力、swap或锁屏保护。源时钟与墙钟整段跨度误差须≤10秒且覆盖≥90%；不再用最近30秒尾部代替整场。FPS按完整帧数／源稳态时钟跨度计算；帧时间取CSV值与相邻时钟差的较大值，中途长卡后恢复仍计入FPS、max frame及最大无推进间隔，不能用后来恢复抹掉卡顿。F3沿用既有流程，允许它有意更换进程／重置帧时钟。

14项新定向测试执行真实 `FrameProgress`、`scene_summary` 及受控actual sample循环，覆盖旧冻结反例、持续推进、尾部短批刷新、中途20秒停顿后恢复、时钟跳变／重置、文件替换／缩短、旧CSV及无证据拒绝。另20项thin harness、9项投影、6项scene、3项tail和5项tool-memory共57项通过，无游戏。确切命令和日志保存在 `evidence/upstream-frame-progress-m9t_aedw/summary.json`；冻结driver SHA256为 `eb52e334a1f92250470775602ec28c844ede64c3b80c81c7b54482fa29ecb098`。该结果只证明控制规则，不是完整ABBA实采通过。
