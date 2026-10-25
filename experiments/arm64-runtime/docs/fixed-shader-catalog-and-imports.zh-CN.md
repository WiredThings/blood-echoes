# 固定 1.09 程序目录、已有特化与九接口来源

本轮全部离线、只读，不启动游戏。正式应用、存档、设置、缓存未改，未清理现有QA缓存。新目录及原始资源的元数据仅保存在Shared evidence，不上传游戏负载、缓存blob或完整本地目录。

## 基础程序目录

fixed-game-shader-catalog-1792897460302182000/catalog.json：校验param.sfo的TITLE_ID/APP_VER并绑定SFO和各源文件摘要；本地版本为CUSA03173/01.09。逐包限定128MiB解压上限，拒绝超出范围、错格式、截断、坏名字指针和符号链接。不导出解压内容到文件。

| 内容 | 实际统计 |
| --- | ---: |
| 着色器包（含调试包） | 8 |
| 非调试包条目 | 15,323 |
| 单独统计的调试包条目 | 3,194 |
| 材质包/材质条目 | 1 / 304 |
| eboot与PRX输入 | 8 |
| 非调试BinaryInfo记录 | 22,519 |
| 不同程序哈希标识 | 8,853 |
| 不同已验证代码SHA256 | 8,311 |
| 同一哈希对应不同代码的标识 | 62 |

所有22,519条非调试记录均验证了实际GetParams使用的BEEB03FF前缀到OrbShdr元数据关系，并按运行时length计算代码摘要。不能用metadata_offset减length猜代码起点：实际中有填充及使用信息。原始二进制type与运行时HwStage不是同一枚举，目录保留raw type，不强行映射。

仅扫描shader目录时，最近一轮实测221个哈希中有59个未找到；它们全部在eboot内找到。最终比较221/221有存储程序对应，269次模块事件完整。这只是该轮观察到的哈希覆盖，不是全游戏所有分支/完整特化已经穷尽。

62个哈希的多代码情况包括几何主程序及同包其它代码段。实测出现的1635154c和8e3f8dc4各有两个存储代码，但本轮只编译geometry阶段；不能据此宣称缓存选错或白模根因。目录必须保留代码摘要和阶段，不能只按哈希折叠。

输入SELF以只读mmap扫描，工具未解压其压缩段，也没有重建全部ELF节；当前可见记录均能验证代码前缀，但目录不冒充对未知压缩/生成资源的全局穷尽证明。304份材质只完成条目索引，尚未解析参数与shader家族的语义关联。

## 已有特化与完整管线键的盘点

shader-cache-inventory-1792897675287451000/inventory.json：只读现有QA flat cache，不调用渲染器、WarmUp、Clear或Save。完整文件摘要和profile摘要限定同一缓存域；遵循当前canonical HashCombine64关联SPIR-V文件和meta头，拒绝错索引/版本/坏SPIR-V，保留孤立和歧义报告。结果：

- 1,426个文件，13,405,272字节（约12.8MiB）。
- 508份SPIR-V与508份特化meta全部一一配对，对应391个程序哈希；均在静态程序目录中找到。
- 370个graphics、39个compute序列化管线键。
- 未配对、孤立meta、歧义HashCombine和坏文件均为0。

这是已积累的完整序列化输入索引，不是全游戏所有特化。工具保存完整blob摘要但尚未语义解码每项资源/RuntimeInfo/顶点输入，亦未证明每个PSO可在当前驱动重新创建。perm_idx仅缓存域内有效，不能跨运行单独当身份。原WarmUp已使用已知管线键，不应把再次编译同一批已预载项包装成新优化。

## 九个未接导入的来源

inherited-import-source-audit-1792897413417680000：当前原版src与隔离vendor的runtime_resolve函数逐字节相同，SHA256均为a350fc63d71b4f6c3bbbf9ee4e6ca98d206a2a092d9e14c876307359b6f6209a。检查原版21个C源文件，九个名字/NID均未找到实现；名称表中的存在不代表实现。

legacy-nine-import-resolver-1792897461477131000：链接旧原生menu宿主库，实际Mach-O依赖、ARM64和严格签名核对通过。实际注册/解析16个AV接口成功作正控，以下九个NID全部未解析：sceKernelMlock、sceKernelGetModuleInfoFromAddr、__elf_phdr_match_addr、__pthread_cxa_finalize、msync、_is_signal_return、sigreturn、__Ux86_64_setcontext、__progname。

这证明原源码和旧GPU宿主同样有缺口，不能表述成ARM漏接了九个已有功能；没有执行正式Rosetta CPU解析器，亦未验证这些接口的PS4 ABI语义。此前第一次控制缺少BB_GPU_USER_DIR被原保护以78拒绝，后续显式私有state运行通过；没有绕过或放松保护。

原probe.c本就对未解析函数设置报错退出陷阱、对数据设置不可读页；ARM保留相应拒绝行为。它们在导入表里是客体真实重定位项，不能删掉来降低missing数量。下一步仍逐项核对客体调用点和真实契约：能复用则复用，涉及x86信号/上下文须走FEX语义；不直接绑定同名Darwin函数，不加空壳成功。最新探索日志没有ARM UNIMPLEMENTED，仅有veneer注册的missing标签；这不能证明其它路径永不调用。任务仍未完成。

## 测试与复现

- 20项自造DCX/BND4/SFO/OrbShdr正负例通过，包括只读总目录、非对齐嵌入代码、共享哈希不同代码、解压边界、目录指针及版本锁定。
- 6项自造cache配对/错索引/版本/符号链接/坏SPIR-V/只读控制通过。
- 旧GPU宿主实际16正例+9缺失对照通过，未执行guest。

准备过的隔离主机上：python3 tests/test_game_shader_catalog.py；python3 tests/test_shader_cache_inventory.py；python3 tools/game_shader_catalog.py --game-root <本地1.09目录>。九接口控制先用build_first_upload_test.py --suite avplayer-exports --library <旧menu库>构建，再run_import_resolver_controls.py。源码和自造测试可上传；catalog.json、inventory.json、任何源包、游戏代码或SPIR-V留本地。

格式依据：[SoulsFormats BND4](https://github.com/soulsmods/SoulsFormats/blob/master/SoulsFormats/Formats/Binder/BND4/BND4.cs)、[BinderFileHeader](https://github.com/soulsmods/SoulsFormats/blob/master/SoulsFormats/Formats/Binder/BinderFileHeader.cs)、[Binder flags](https://github.com/soulsmods/SoulsFormats/blob/master/SoulsFormats/Formats/Binder/Binder.cs)；独立的Python只读诊断实现，没有复制其C#源码。BinaryInfo、HashCombine和特化格式以本项目vendored shadPS4的canonical声明为准。

下一步解码已有特化与管线键、关联材质家族及缺失程序路径，再评估有界提前准备；白模和长编译仍待真正修复，不把目录覆盖当性能收益。
