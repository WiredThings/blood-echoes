# msync 与数值虚拟地址契约检查点（2026-10-25）

仅隔离 ARM64/FEX 开发版。未修改正式应用、存档、设置、缓存，不更新 Release。白模、真实场景兼容性和帧率收益未在本检查点得到验证。

## 语义与实际调用

PS4 SDK/FreeBSD 9 为 MS_SYNC=0、MS_ASYNC=1、MS_INVALIDATE=2；FreeBSD 9 sys_msync 把地址向页下取整、长度加页内偏移后向上取整，低两位同时置位报 EINVAL，其它位在该版本被忽略。对齐地址、零长度选择当前映射区域；未对齐零长度按舍入后的单页处理。Darwin 的 MS_SYNC=0x10，0x4/0x8 是 KILLPAGES/DEACTIVATE，不能原样转交客体标志。

一手对照：
- https://github.com/ps4dev/ps4sdk/blob/master/include/sys/mman.h
- https://github.com/freebsd/freebsd-src/blob/releng/9.0/sys/vm/vm_mmap.c
- https://github.com/freebsd/freebsd-src/blob/releng/9.0/sys/vm/vm_map.c
- 本机 SDK sys/mman.h（当前实际编译环境）

本地原 boot 的 libc msync NID 为 tZY4+SZNFhA#p#J；直接调用点在模块代码中按 4KiB 地址探测，长度 4/8KiB、flags=1，并检查 -1。这是静态调用点证据，不证明当前场景动态命中次数，也不是白模归因。原 boot SHA256 b5cb1da3a15bb853c2adbc9ef03d7d8058f729b247a0646370cf61f056398e1d；游戏字节/反汇编不随源码导出。

## 实现边界

`posix_msync` 使用数值地址参数，检查 40 位溢出，按既有客体 16KiB 页大小舍入；显式转换标志。实际调用宿主 msync；失败写客体 errno，FreeBSD 的无效映射语义统一为 EINVAL，而非 Darwin ENOMEM。没有成功空壳，不锁页，不改变 GPU dirty/失效、页保护、缓存容量或像素。

客体 VMA 范围以现有读锁覆盖完整连续映射验证和宿主调用，拒绝空洞、保留区和宿主排除区；解除映射持写锁，不能在调用中换代。静态图像/低堆/客体栈只准入明确发布的客体范围，以 State 共享锁保持临时栈生命周期，内部回调不拿 VMA 锁，避免新的锁序循环。零长度只选择包含地址的 VMA/已发布范围，不扩到任意宿主内存。

## 定向测试发现并修复的既有桥接错误

第一次实际 FEX PROT_NONE 控制失败：`kernel_mprotect` 的地址被生成器误当可读 C 缓冲区，恢复权限前触发错误14。相同问题影响 readonly/PROT_NONE 的释放、保留地址映射提示及权限查询。六类 VM 接口的地址槽改为 opaque 数值地址：direct_unmap、release_flexible、posix_mmap、kernel_mprotect、query_protection、virtual_query。输出指针保持原跨度/可写校验；错误输出的真实 FEX 负例仍以 HLE fault 74 拒绝。没有全面关闭指针检查。

## 已执行验证

- `tests/test_memory_msync.c`：自己创建的 64KiB 文件与独立共享映射，真实 msync 和双视图内容保持；0/1/2 标志转换，未知位不传给 Darwin；零长度、页舍入、相邻区、空洞、保留区、PROT_NONE、错误转换及并发 VMA 生命周期租约。所有测试文件/映射退出清理，不涉及游戏缓存。
- `tests/test_msync_fex.py`：18 组真实 FEX 自造程序，包括静态图像、整区域、栈、动态 flexible、PROT_NONE 恢复/释放、只读释放、查询、保留区 mmap、已释放/空洞/溢出/标志错误。17 项退出0；非法输出指针负例预期退出74。msync 路由为 native。
- 新 CPU 的 121 基础回归、Clang ABI 声明控制、8组模块查询 FEX/73个原生负例、17退出/6监督器、4程序名和6 AvPlayer FEX 控制通过。
- 原 boot 无窗口 libc/Fios2 模块初始化约0.852秒，两个返回0；没有运行实际场景。所有采样 pressure=1，swap 增量0。不能当作启动性能 A/B。
- 原九接口已有4项实现，余5项为 sceKernelMlock、__pthread_cxa_finalize、_is_signal_return、sigreturn、__Ux86_64_setcontext；没有按失联图形导入数混算。新生成器344包装器不代表全量兼容。
- 正式既有11671个保护文件的长度与 SHA 逐项相同；16份原启动脚本 SHA 相同。只证明基线列出的文件不变，不声称全盘快照。

本地证据：msync-contract-1792904311857877000、suite-1792904313611561000、module-info-fex-1792904338635552000、exit-contract-1792904341711996000、guest-progname-1792904342526990000、avplayer-fex-1792904343556288000、game-1792904720942182000。CPU SHA256 67db33e25dfd65caa0f41cc12da2d76224134aa059d4399652ae00342c1d9c87。普通手动 QA 包仍为09af59d2，未将本检查点冒称安装或发布。

## 下一步与并行边界

已按用户要求分派三个互不修改公共文件的 subagent：sceKernelMlock 实际调用/有界锁页契约与独立测试、白模纹理身份/布局/生命周期审计、304材质与 shader/完整特化关联。候选只存各自 evidence 子目录，根代理审查后才整合；不能同时运行多份游戏或争改公共运行时。锁页不能用空成功或依赖本机无限 RLIMIT_MEMLOCK；真实锁页测试最多一个16KiB页面且确保解除。信号上下文和线程析构仍需要独立语义核实，未并行修改共享状态。
