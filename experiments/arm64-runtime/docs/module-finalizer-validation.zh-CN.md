# 模块 finalizer：真实 C 和 FEX 回归（2026-10-25）

隔离ARM64运行时补齐 __pthread_cxa_finalize，并使已存在的 native __cxa_finalize(dso) 在原有atexit析构完成后走同一模块key清退。客体入口验证完整424字节canonical模块描述，按SHA绑定loader registry所属执行段准入；不是将Darwin同名函数或线程退出清理当模块终结。

## 具体语义

- 原256 key/9模块有界表。key generation防槽复用后读回旧值；回调取出时在key锁内增加模块in-flight，先清本槽，再在锁外真实bb_arm_call执行，返回后归还。无析构key的值保留到其它析构读完，四轮结束后统一清线程值。
- 模块清退只清本模块destructor指针，保留key/代际/其它线程值和其它模块，等已取出回调结束；关闭期间新注册返回SCE EBUSY。重复清退幂等。
- 不在TSD回调内等待自己/互相等待，明确EDEADLK；终结异常/等待超时走原HLE fault 74，不用75/76。取消状态只在持锁/回调租约区间受限，并在安全点恢复。
- 全局 __cxa_finalize(NULL) 保留原atexit逆序/先标记/锁外回调/递归注册算法，NULL仅跳过新增module-key子步骤；未知DSO仍先执行原匹配析构。
- DSO与用户值是opaque tokens，生成器修正对应参数；输出指针和guest代码回调准入没有放宽。实际Clang再生成348包装器、unsupported0，精确四输出与审查投影SHA一致。

## 已执行

两组直接包含真实runtime C的原生控制通过：key所有权/ABA/模块局部注销/四轮重入/无析构key可见性/真实pthread在途等待/closing拒绝；全局NULL、DSO顺序、递归/重复atexit。原生guest owner和callback trampoline是明确mock，不能替代FEX。
十一项真实FEX夹具全部通过：valid/global-null/unknown-dso/preserve-nondtor、host/data/veneer回调拒绝、截断/伪造描述、bad-atexit及递归终结74。回调确实在FEX执行，模块导入路由native-module-finalize。
全部56目标重建，121基础及完整专项回归通过，包含模块信息、AV、ABI、退出、msync、Mlock等。CPU SHA-256：7adf4d5e5c6b5fae8f71ad51342766e6ffe4d413f93385920ad32e95bf78b16d。

失败历史保留：首次native fixture误提供重复runtime_wait_note，Clang拒绝；仅删测试mock保留真实实现后通过。首次FEX fixture将模块init改成裸ret，残留RAX=0x8000令driver正确拒绝；仅将自造init修回xor eax,eax;ret后三字节返回零，11项通过。运行时错误判据未放宽，首次失败的整套报告没有改成成功。

## 范围与成本

原九个缺失导入中已有六项具备有界实现和定向回归，仍有三个信号/硬件上下文接口未接。真实模块在当前固定镜像中保留至进程退出，不能把TSD in-flight归零当通用dlclose安全；未来代码卸载还要保护普通执行/网络/atexit及所有入口。
全局key记录约多4KiB、每附着线程TLS约多2KiB和固定9项gate；get/set加入互斥，CPU成本尚未实际场景量化。不宣称FPS/白模或内存收益，不默认替换手动QA包或正式app。

复跑：python3 tests/test_module_finalize_native.py；python3 tests/test_module_finalize_fex.py；python3 tools/run_current_regressions.py。
只用作者夹具、Shared私有state，无游戏/菜单/在线/死亡测试，正式数据与缓存/Release不变。代码只推dev。
