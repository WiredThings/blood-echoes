# FEX 编译兼容身份与磁盘服务来源分离

当前源码候选，Python11项正反例通过，原生构建/实物变更控制待做。保留0cda逻辑分片LRU、2MiB/256分片上限、全部源页/单元/重定位准入和自身JIT；不动正式应用、存档、设置或已有缓存，不发Release。

原耦合：fex_guest_engine.cpp直接include全部磁盘loader/generator，生成的embedding archive同时带翻译接入和磁盘管理代码，其整个摘要进入Runtime；Bridge还包含所有前端头。故修改日志/LRU等可使CPU静态产物全部换代。目标CMake和整个CMakeCache也参与Runtime，使测试目标和目录变化造成额外失效。

改法：bb_fex_disk_service独立静态target，稳定fex_disk_service.h仅声明目录服务和factory/generation接口。原Engine/桥/接口/线程和缓存入口流程保留，实际调用原Loader/Generate算法，无新CPU引擎或FEX/bbcpu混合桥。Loader通过稳定接口提供原Load/EndCompilation和只读计数；常规已编译块命中不经过此服务。增加一个host vptr，无大型常驻分配，原proof预算不变。

Runtime保留真实FEXCore/embedding/Common/CommonTools archive、实际生成器调用源码、服务转发源码与builder算法。Bridge保留真实native/ATRAC/GPU/AV适配物、game_runtime.cpp、稳定服务头及其它前端ABI输入、GPU库。只有纯存储parser/proof/LRU头归到provenance；生成policy头fex_disk_generate.h仍参与兼容身份。target CMake、CMakeCache、磁盘服务archive全部按原可靠文件规则计算摘要并保留于provenance，但不进入Runtime/Bridge摘要。若CMake更改了真正编译结果，真实archive摘要仍失效。未删除actual patched_code、module_set、registry/frame/veneer ABI、host_features、effective codegen/options或TSO字段。

Builder输出schema2新增provenance_parts，兼容键生成算法域保持原样，但输入分类和接口本次变化会可靠失效一次；不伪造旧键或将旧产物认作新产物。文件变化只改provenance时，生成的C++头不重写。Python控制覆盖这一点，以及真实archive/接口变化、缺失/escape/symlink provenance拒绝等。还需真实正常构建中改变Loader日志、核验archive/identity差分，并恢复干净源码；此验证不能冒充游戏或性能验收。

现阶段仍保守：embedding其余非存储实现、其他头/GPU库/真实archive的变化可能继续失效；不宣称已对所有无关更新实现最小失效。不扩展长期方向。原约295ms事件仍待独立诊断，不能因为无编译调用就断言某种锁或GPU瓶颈。

## 已完成的真实构建与执行控制

最终代码c8d2f2f6，完整导出源码核验、正常CMake编译通过；已补齐独立TU所需的Relocations显式包含，不依赖Engine先include的顺序。正常18次控制改用真正服务factory，覆盖原生成/加载/重定位/预算/源变化与自身JIT。最后证据private `unit-catalog-controls-1793453652017438000`。Python构建身份11项及归档5项正反例均通过。

真实构建实验private `service-identity-1793453434876104000`：以实际编译的Runtime和RegistryABI生成自造x86缓存；只修改临时Loader日志，约4.85s增量重建后实际CPU二进制与服务archive变化，编译身份不变，原有缓存可加载执行、native文件未改（0.39s控制，非游戏速度）。修改ABI输入约7.88s重建后正确拒绝旧缓存；恢复源码约7.88s重建后，原始embedding archive因容器日期不同，归一化身份仍与初始相等，缓存再次执行。原始失败实验保留，正是它定位了ar日期误失效，不将首次恢复失败冒充通过。精简结果 `fex-service-identity-controls-20261031.json`。

新增archive_identity.py只在构建身份生成时工作，不在游戏每帧/每次启动解析archive。完整流式读取和原文件稳定/所有权/路径检查保留；对标准ar只归一化60字节成员头中的日期/uid/gid，保留名字、mode、成员范围、全部payload和对齐，包括符号表/字符串表/所有Mach-O code/data/relocation/debug字节；thin引用和畸形结构拒绝。完整原始SHA保存在raw_input_sha256，normalizer源码自身进入身份。没有用仅text段摘要替代完整成员，也没有省掉运行时实际代码/源页准入。磁盘服务增加host vptr及冷缺失路径虚调用，其游戏开销尚待集中对照；没有新大型常驻对象或扩充原缓存。

隔离QA已更新，CPU SHA9c5737713a8d1c241f6e2caf395099413427b4db264db4ee73db0a28aa3e3f6b，冻结闭包/签名/加载边核验通过；其余Metal、bbcpu、联网物理字节保持相同，见private `service-split-20261031/fex-overlay/result.json`，之前版本有备份。该新包尚无场景性能结论，不能借用155的四轮结果。不改正式应用/存档/设置/旧缓存/Release。

## 约295ms长帧的后续边界

源码确认driver::Flip仅通过RunPresenter异步投递到swap_queue；ready_ns在PrepareFrame后进入requests时记录，CSV在投递后记录。因此ready_age约274ms既不是纯GPU时间，也不是完整显示延迟。PresentThread的请求队列、SwapThread的工作队列、Metal nextDrawable等待以及真正GPU完成必须拆开。Metal Present内部确有nextDrawable阻塞和跨队列event依赖，不能凭相邻操作删等待。当前仅完成这条语义审查，没有归因、GPU优化或新的现场复现；继续使用既有隔离入口，诊断/性能分轮，不恢复白模抓取，不扩长期方向。
