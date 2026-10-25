# 两类派生Metal管线补齐持久化

2026-10-26。开发源码已接入；隔离游戏场景收益尚未测量，正式应用、存档、设置、旧缓存和Release不变。

## 缺口与改动

`mtl_rasterizer.mm`原有两类派生管线只进入进程内PsoCache，未保存修改后的完整PipelineKey，下一进程WarmUp不知道它们存在：

1. 原绘制去除depth/stencil附件的native变体。
2. 原生速度图镜像，将color0改为RGBA16Float的native变体。

现在仅在原“首次创建成功”分支保存修改后的完整key及原VertexAttributes，沿用`GraphicsSerialization`、`RegisterPipelineData`和原WarmUp。不新增cache格式、第二套活跃PSO表、纹理资源或逐draw读取。重复查找沿原map返回，不再次记录。

`motion_vectors`注入式路径仍不记录：其旧SPIR-V把当次session地址烘焙为字面量，原LoadShaderMeta拒载继续保留。原生速度图镜像使用原VS/FS，与注入式地址路径不同。此补丁没有把两者混为一谈。

## 实际验证

- 以冻结资源回写/AOT源码为基础，只加这两处成功分支，完整230 GPU编译单元一致重建。新Shared DSO SHA为`d23894136ecd8df8109850d92bfc82399d0939dd2fbed1ce666dde6366ea26be`；266.9秒构建、压力1、swap增量0。
- 实际Metal Device、PipelineCache、Storage writer、原shader metadata/SPIR-V、原LoadGraphicsPipeline/WarmUp与系统native factory参与。使用作者生成的VS/FS与全新Shared存储；没有游戏编译资产、现有cache walker或游戏运行。
- Seed进程260项断言通过；独立Warm进程192项断言通过。第二进程两条native创建都携带`FailOnBinaryArchiveMiss`，实际archive命中2/2。
- 旁路观察并原样调用系统factory，确认无深度变体的depth/stencil均Invalid；速度变体实际color0为RGBA16Float，保留原深度/模板、sample及顶点布局。
- 原VS/FS的负数和小数速度值按half逐像素读回；原色值、seed/warm全部字节一致。移除depth附件未改变作者原始像素。
- 首次登记2次；重复16次不增加登记/native factory调用，key内容及纳秒mtime不变。真实无效格式创建失败和motion key均未记录；真实LoadShaderMeta拒绝注入motion VS。
- 运行合计约0.70秒、压力1、swap增量0。实际DYLD加载路径与DSO SHA核对通过。

第一次fixture误把写完游标的Archive传给读取器，在实际新增PSO之前退出；修成重新建立读取Archive后使用全新state重跑。失败日志保留，未覆盖或当作生产代码失败。早期CPU计数工厂控制只作为前置证据，未替代上述原生测试。

## 明确限度与后续

成功分支逐字提取后由作者wrapper触发真实PsoCache/Storage，未实测游戏Rasterizer的全部资格条件或统计真实场景命中率。登记次数是成功调用点透明观察，不是全局所有Storage::Save次数。尚无冷启动、暖场景FPS或内存压力对照，不能宣称消除了长编译或整游戏零成本。测试LocalAOT关闭，未把source模式archive结果当作离线metallib全覆盖。

原生证据及作者复现代码在`evidence/parallel-derived-pso-native-20261026/`，构建来源在本机新Shared `evidence/derived-pso-gpu-1792949840875543000/`。后续与helper完整recipe准备分别验证，再用原入口隔离场景对照；已验证的纹理回写、二级页表、速度图及内存补丁继续保留。
