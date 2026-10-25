# 客体程序名数据符号与模块查询契约（2026-10-25）

本轮完成九项遗留导入中的 `__progname`；其余八项仍为未完成，不用同名宿主函数或假成功替代。正式应用、设置、存档、缓存及 Release 不变。未启动真实游戏，不宣称白模修复、FPS 或实际场景兼容已验证。

## 已修复与实际验证

`__progname` 是客体可写的 `char*` 数据槽，不是函数，也不是直接的字符数组。原 ARM 路径为它建立 PROT_NONE guard；现在在**已有**客体参数页的 128 字节偏移存放指针，指向同页已有 `/app0/eboot.bin` 字符串的 basename `eboot.bin`。argc/argv/env 形状保持原值，只提前到模块初始化前准备。精确检查 NID、数据种类与 capabilities，不覆盖有效原生解析或客体绑定；未知数据仍保护。没有新增映射、缓存或常驻池；本构建移除了该数据符号原先的一张空 guard 页，但不把它算成实际 RAM 节省。

`src/guest_process_parameters.h` 对参数页长度、空地址、整数溢出、路径长度及内嵌 NUL 做写入前检查。与其他前端头文件一样纳入 CPU 磁盘缓存构建身份；不改任何现有缓存。

- `guest-progname-1792900231203263000`：原生参数布局/边界/重置/精确符号控制通过；实际 FEX 四次运行通过（读取、改写指针、下一进程重置、错误声明为函数仍以80明确失败）。全部只执行自造代码。
- `suite-1792900273067226000`：既有121项无窗口基础回归通过。
- `exit-contract-1792900341396099000`：17项真实 FEX 退出代码控制、6组原监督器及 owned GPU 回收控制通过。75/76重启语义不变。
- `avplayer-fex-1792900342128029000`：原16项 AV 桥接保持，6项实际 FEX 自造媒体/回调及边界控制通过。
- CPU SHA256 `442a746fce4f402de5f0500348ae4996e36ca72bce0b56d22f812036a4644bb4`。普通手动 QA zip **没有更新**，仍为此前 CPU09af59d2；不要混为一份测试包。

F3 原实现及 helper 已核对：快速存档是游戏正常序列化文件的检查点，**不是内存快照**；恢复前复制正常存档，在全新进程加载。本补丁没有改变 helper、退出协议或存档格式，参数/guard 地址布局不需要兼容旧进程内存快照。此为源码核对和退出协议控制，不冒称本轮已做真实游戏 F2/F3 验收。

保护快照 `guest-progname-final-20261025.json`：11671个已保护文件逐文件字节/长度与原基线完全一致。这是既有文件清单验证，不宣称该数字全为存档，也不表示快照覆盖本机所有文件。

## 不能照搬同名接口的实证

本机固定 1.09 boot 中，九个名称对应10个重定位槽，全部位于 libc 模块0；八个函数与一个数据符号的种类和 addend=0已核对。

`guest-import-contract-1792900514642847000` 通过 Capstone 只读解码核对了实际 libc 调用点及各自 PLT→GOT 的精确关联：先以 flags=2 调用 `sceKernelGetModuleInfoFromAddr`，然后把**同一输出结构缓冲区**作为第一参数传给 `__elf_phdr_match_addr`，第二参数是析构函数候选地址。这不是 FreeBSD 公共版本接收的 `dl_phdr_info` 调用形状。只核对调用关系，并不推断未知内核函数的全部实现；原始客体字节、反汇编和私有 boot 不上传。

[shadPS4 process.cpp](https://github.com/shadps4-emu/shadPS4/blob/main/src/core/libraries/kernel/process.cpp) 中该查询返回 [OrbisKernelModuleInfoEx](https://github.com/shadps4-emu/shadPS4/blob/main/src/core/module.h)，与 ForUnwind 是不同结构；[OpenOrbis 声明](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/blob/master/include/orbis/libkernel.h) 也将两者区分。FreeBSD 的[同名地址匹配实现](https://github.com/freebsd/freebsd-src/blob/main/lib/libc/gen/elf_utils.c) 读取 program headers，不能因名称一致就绑定。程序名指针/basename 的一般依据见 [FreeBSD amd64 crt](https://github.com/freebsd/freebsd-src/blob/releng/9.0/lib/csu/amd64/crt1.c)；实际 ARM 行为另由上面的原创 FEX 用例验证，不声称拿到了官方 PS4 源码。

## 剩余八项的下一步

1. **模块地址查询 + 地址匹配**：作为一对实现；补齐并校验固定加载模块的名称、范围/权限、ID、TLS 和 unwind 元数据。现有 BBPROBE5 只有合并段及部分模块/TLS 字段，不足以填写完整 ModuleInfoEx，不能把缺失字段全置零就返回成功。先扩展有完整性绑定的元数据，再用已识别的实际 libc 调用形状验证析构筛选边界及非法指针。
2. **`__pthread_cxa_finalize`**：继续查实际客体调用参数、线程相关析构注册/取消与现有 runtime_finalize 的范围；不能直接映射进程级 finalize 或宿主 pthread 清理。
3. **`sceKernelMlock` / `msync`**：逐项确认 PS4 标志和 errno、已有 VMA/物理别名生命周期及文件/匿名 backing 的差别。Mlock 可能固定物理页，不能用无限 pin 或空成功通过兼容审计；msync 也不能直接转交未经翻译的 flags。
4. **`_is_signal_return` / `sigreturn` / `__Ux86_64_setcontext`**：只恢复客体 x86 寄存器/信号状态，经 FEX 当前线程和异常桥；不能把 x86 ucontext 交给 ARM64 Darwin 同名函数。先补专门的合法/非法上下文和返回控制测试。

长期 shader/特化目录与提前准备任务继续，不把本轮接口修复计作编译卡顿优化。用量最后可读剩余36%；保持15%交接阈值。
