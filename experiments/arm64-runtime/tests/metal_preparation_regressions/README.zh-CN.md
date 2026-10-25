# Metal准备回归源（开发专用）

这些是作者自有的小场景/数据，不含游戏资产、存档、MSL游戏缓存或设备二进制；不会打进普通游戏。原生控制要链接实际构建的ARM64 Metal后端及`src/game_pso_host_services.cpp`，使用相同生成头/依赖。不能用stub backend、旧库或系统Python通过替代新包验收。当前执行harness依赖本机Prepared Shared开发树，尚不是跨机器一键native测试命令。

- `function_origin.mm`是value-only CPU单元，用实际CompiledShader字段验证身份/来源拒绝和ABI差异；其析构替身只用于此不持有GPU对象的单元，不能作生产GPU生命周期证明。
- `pipeline_artifact.mm`和自写VS/FS用于真实函数/descriptor/8×8像素、Source/Local严格归档、坏收据/启动偏好/缺库等回归。
- `postprocess.mm`用原BGRA8附件、HDR/gamma/sRGB和alpha、边框guard，调用实际共享工厂。
- `camera_record_motion.mm`通过原tracker公开入口调用真正CameraMotion::RecordMotion，不在fixture自己setBytes或编码compute；三帧静态/运动/抖动输出和guard均检查。

原生main故意只接受新建Shared作者状态和专用marker，拒绝正式命名空间；cold、local与独立warm要分进程执行。临时作者内容不是游戏白模复现，逐字节断言数也不是独立PSO数。

2026-10-26最终c2ec6e05库通过11个pipeline进程、7个Camera生产调用进程、7个PostProcess进程；内存压力1、swap无增长。最初Camera108B上传被Metal反射112B拒绝的失败已保留；生产仅补零尾4B并重新验证，公共MotionConstants仍108B，算法和字段未改。实际整包游戏/性能验收单独记录在`docs/local-pso-preparation.zh-CN.md`，不能用这些小控制代替。
