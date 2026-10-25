# 已有特化输入解码与缓存域字段比较

本轮为离线诊断，不启动游戏、不修改任何原缓存，不注册或执行SRT代码，不改变正式应用/存档/设置/画质/Release。未启用新预编译策略、未声称FPS或内存收益。

## 读取方法

原Info::Deserialize会调用PersistentSrtInfo::Deserialize，后者把walker代码注册到执行路径，因此没有拿原生反序列化器来“读个表”。新工具采用纯有界字节读取：布局由独立ARM64小程序编译canonical声明获取，工具只链接系统库，干净环境运行，不链接GPU/FEX库。头文件摘要、pointer/size_t大小和端序均记录；源布局改变即拒绝。每个blob限制1MiB，每个数组先按剩余字节和元素大小验证长度。缓存profile、meta、SPIR-V和key均核对先前清单摘要，只接受同目录普通文件。

读取内容包含绑定起点、RuntimeInfo及其阶段/寄存器属性、资源bitset、fetch属性、VS/buffer/image/FMask/sampler特化数组、InfoPersistent标识字段、flattened词数、SRT头及代码摘要。walker只消费/摘要，不创建对象或跟随指针。buffer部分保留packed_word与完整原始特化字节，RuntimeInfo亦保留完整raw表示；不将这些含padding或会话数据的字节摘要冒充跨运行归一化语义键。

管线键同时读取六个逻辑阶段的特化哈希、静态顶点格式/位置/实例步进、颜色附件数量、样本数及其他已索引字段，验证所引用metadata的逻辑阶段一致。compute的元数据虽装载到槽0，其逻辑阶段仍为compute，单独处理。

## 真实缓存结果

metadata-layout-1792899567531270000：当前canonical布局及全自造Writer/比较夹具。

decoded-shader-cache-1792899626496150000：主QA样本508份特化和409个管线键全部解析；阶段数fragment234、vertex216、export3、compute39、geometry3、hull7、local6。无读取错误、引用缺失、逻辑阶段错配或未被已有key引用的meta。350个graphics键有静态顶点属性。380个walker含代码字节，但均未注册/执行；这里只解析长度和摘要，不宣称对所有字节码语义做了验证。

decoded-shader-cache-1792899626818555000：此前捕获232ms库编译/84ms PSO的私有缓存，共327份特化、259个键，亦无上述错误。热点37a8af3d已找到：顶点、perm0、6个buffer、0个image、7个fetch属性。它不在主QA那份缓存中；这两份不同测试缓存不能因有相同文件夹标签就直接拼接，也不能把不同缓存的perm0当相同输入。

两份样本的motion_vertex_cache_load_rejected均为0。此前从源码提出“速度图顶点条目因会话地址拒载”是需核对的假设，现已排除它是这两份数据的拒载原因。该字段指翻译器额外合成的物体速度输出，不能拿0去判定游戏自带速度图补丁失效。3份geometry条目带序列化copy-span指针；指针未跟随，仅记录非零、词数和copy hash。这不是坏缓存或白模根因证明。

## 两项容易误判的序列化细节

1. 某些fetch条目由canonical Writer的显式Grow预留产生合法零尾部。初版解码器坚持offset==file_size，导致209条被拒绝；原始数据未改，也没当成坏缓存清理。现在精确重放原Writer的容量增长规则，必须最终容量等于文件大小且未使用字节全零才接受，禁止笼统忽略尾部。主样本299条无尾部、209条恰有1字节合法零尾部。
2. 两份profile.bin的原始SHA不同，但差异只在文件偏移90–95（Profile内74–79）的尾部padding。canonical Profile::operator==按字段比较。新工具从canonical声明编译49个字段的offset/width，规范化字段后两份semantic SHA相同，49个字段无差异。原生C++夹具只改padding后operator==仍true；另有有效特性位改变必须不同的负例。这修正的是诊断目录的分组依据，不是修复原版运行时：原版比较本来就忽略padding。字段相同仍不等于所有renderer/compiler/driver二进制兼容。

## 测试

18项控制通过：真实canonical Writer生成plain/fetch/motion/geometry/graphics/compute输入；合法预留尾部与非零/额外零尾部、截断、超长bitset、坏顶点count/实例率、完整引用图、SPIR-V摘要改变、逻辑阶段错配、跨目录输入拒绝，以及原生Profile相等与特性改变负例。native helper最初误访问Archive私有字段导致编译失败，已改用CurrPtr/TakeOff公共接口；依赖未就绪时的测试失败保留，不计为通过。

复现（准备过的隔离主机）：先python3 tools/build_metadata_layout.py，再python3 tests/test_decoded_shader_cache.py；然后tools/decode_shader_cache.py --inventory <先前只读inventory.json>。这些仍依赖准备好的编译stage及本地自造夹具，不是已验证的全新checkout构建。只上传源码、自造控制和本报告；实际layout/decoded/缓存blob留在私有evidence。

## 下一步

现有输入图完整，不能靠再次预载同一批409个键宣称解决新场景卡顿。应继续关联固定游戏材质/程序家族和未覆盖的完整运行时特化，避免按raw SHA、仅基础hash或本地perm_idx做跨缓存合并。仍需真正验证PSO创建、画面及收益，白模、九接口契约和其它兼容目标均未完成。保持全部原约束，死亡/在线/菜单实测继续暂缓。
