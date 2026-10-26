# Local 平铺库像素路径复测

本轮修正作者测试配置，未修改加载器：每例 `BB_MTL_LOCAL_AOT_ROOT` 置于同例 `BB_GPU_USER_DIR` 下。旧测试指向另一 fixture 的目录，原 `Directory` / `Child` 校验正确拒绝了它，导致 Local 为零。

## 实测结果

共九个最终进程全部通过，开启 Metal validation；逻辑边界检查开/关分别对照。

| 路径 | Source 对照 API | Local 模式 API | 输出对照 |
| --- | --- | --- | --- |
| 普通 32/64 位纹理，bounds=0/1 | 每组 Source 4、Local 0 | 每组 Source 0、Local 4 | 每组 18 例，全相同 |
| 场景代理 32/64 位纹理，bounds=0/1 | 每组 Source 6、Local 0 | 每组 Source 0、Local 6 | 每组 2 例，全相同 |
| 故意错误根目录，模式仍为 load | Source 4、Local 0 | Local key 无命中 | 18 例仍相同 |

普通纹理覆盖 128×64、128×73、1920×1080 和 swizzle 0/1/3；沿原测试比较 CPU 参考输出、缓冲区填充与 guard。代理沿原测试比较原生 1920×1080 / 960×540 副本、失效同步与资源数量。跨进程再对实际输出计算 SHA-256：36 个普通纹理比较、4 个代理比较，以及错误根目录的18个比较全部相同。全部实际加载同一 SHA 的原生 GPU DSO。

代理原测试还调用独立的 `SceneTargets::CopySource` 库。首次两组各有 Source 2、Local 4；这不是平铺回退。仅在各自新测试缓存克隆中，沿现有 builder 补建这一个原始依赖（23请求、22 verified skipped、1 built），随后重开独立进程获得 Source 0、Local 6。它不算十四平铺库新增一种，也未添加到候选首启集合。

诊断脚本初版通过头文件读取 inline 统计变量，在 executable 中产生了第二份存储；已改成从实际加载的 DSO 读取，并用 `nm` 断言测试 executable 不定义该变量。Source 阳性对照验证统计有效。两次未完整通过的尝试保留在本机，不计入通过集合。

## 边界与复测

28.16秒、426系统样本，最高压力2，swap 9233.06→9225.06 MiB、未增长。此为保护观察，不是匹配游戏性能或内存收益证据。原92文件 fixture cache 前后散列清单相同；没有启动游戏，未修改正式存档、设置、缓存、运行时代码或目录校验，也未更新应用和Release。

可复测工具 `../../tools/run_tiling_local_pixels.py` 消费已准备的作者源、同源 native build manifest 和本机 fixture。每次使用全新执行与缓存目录，拒绝覆盖输出；不分发 Metal 编译结果、游戏文件或存档。它依赖本机已有开发闭包，不代表干净源码构建验证。

```sh
python3 experiments/arm64-runtime/tools/run_tiling_local_pixels.py \
  --project /Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype \
  --native-dir experiments/arm64-runtime/evidence/tiling-library-integration-20261026/native \
  --output /private/tmp/blood-echoes-local-pixel-fresh-result
```

上述命令在仓库根运行，output 必须不存在。摘要保留全部逐例输出散列与实际API计数；本轮只补齐 Local 像素归因，不代表全部十四库的格式全集、全游戏PSO、配对游戏运行时或峰值内存验收。候选仍未启用。
