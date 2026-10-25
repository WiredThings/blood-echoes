// SPDX-License-Identifier: GPL-2.0-or-later
// Counterpart of vk_camera_motion.cpp (RecordMotion): the motion vector pass on the scheduler's
// compute encoder. The pipeline comes from the D11 header of camera_motion.comp, built the way
// the caches' kernels are (HostKernels, mtl_runtime_kernels.mm).

#include "video_core/renderer_metal/mtl_camera_motion.h"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "common/assert.h"
#include "video_core/host_shaders_msl/camera_motion_comp_msl.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"

namespace Metal {
namespace H = HostShaders;

namespace {

/// The slot of the (set 0) `binding` among the textures of `shader` (sampled or storage).
u32 TextureSlot(const H::Shader& shader, u32 binding) {
    for (const H::Binding& b : shader.bindings) {
        if ((b.kind == H::Binding::Kind::Texture || b.kind == H::Binding::Kind::StorageTexture) &&
            b.set == 0 && b.binding == binding) {
            return b.msl_index;
        }
    }
    UNREACHABLE_MSG("camera_motion.comp without texture binding {}", binding);
}

} // namespace

struct CameraMotion::Impl {
    Scheduler::Impl& sched;
    id<MTLComputePipelineState> pipeline = nil;
    u32 depth_slot = 0;  ///< binding 0
    u32 motion_slot = 0; ///< binding 1
    u32 object_slot = 0; ///< binding 2 (object motion: off, the depth again)

    explicit Impl(Scheduler& scheduler) : sched{scheduler.GetImpl()} {}
};

CameraMotion::CameraMotion(Device& device, Scheduler& scheduler_, HostKernels& kernels)
    : scheduler{scheduler_}, impl{std::make_unique<Impl>(scheduler_)} {
    const char* upscaler = std::getenv("BB_UPSCALER");
    enabled = !(upscaler && std::strcmp(upscaler, "none") == 0);
    if (const char* debug = std::getenv("BB_DEBUG_MOTION"); debug && debug[0] == '1') {
        std::printf("Metal: camera motion debug overlay not available\n");
    }
    if (!enabled) {
        return;
    }
    const H::Shader& shader = H::CAMERA_MOTION_COMP;
    impl->depth_slot = TextureSlot(shader, 0);
    impl->motion_slot = TextureSlot(shader, 1);
    impl->object_slot = TextureSlot(shader, 2);
    NSError* error = nil;
    impl->pipeline = kernels.CameraMotionPipeline(&error);
    ASSERT_MSG(impl->pipeline, "Camera motion pipeline failed: {}",
               error ? error.localizedDescription.UTF8String : "unknown");
    ASSERT_MSG(impl->pipeline.device.registryID == device.GetImpl().device.registryID,
               "Camera motion pipeline belongs to another Metal device");
}

CameraMotion::~CameraMotion() = default;

void CameraMotion::RecordMotion(const void* depth_texture, const void* motion_texture, u32 width,
                                u32 height) {
    tracker.CommitFrameCamera();
    if (!impl->pipeline || width == 0 || height == 0) {
        return;
    }
    // Object motion is off: mode 0, and the depth stands in for its texture, as on Vulkan.
    const VideoCore::MotionConstants push = tracker.Constants(width, height, 0);
    const H::Shader& shader = H::CAMERA_MOTION_COMP;
    id<MTLTexture> depth = (__bridge id<MTLTexture>)depth_texture;
    id<MTLTexture> motion = (__bridge id<MTLTexture>)motion_texture;
    scheduler.ComputeEncoder(nullptr, true);
    scheduler.LabelEncoder("Camera motion");
    id<MTLComputeCommandEncoder> compute = impl->sched.compute;
    [compute setComputePipelineState:impl->pipeline];
    [compute setTexture:depth atIndex:impl->depth_slot];
    [compute setTexture:motion atIndex:impl->motion_slot];
    [compute setTexture:depth atIndex:impl->object_slot];
    // std430 carries 108 meaningful bytes; MSL aligns the float4-containing
    // struct to 16 bytes and its reflected argument size is 112. Keep every
    // original field offset and zero the tail without reading beyond push.
    std::array<u8, 112> metal_push{};
    static_assert(sizeof(push) == 108);
    std::memcpy(metal_push.data(), &push, sizeof(push));
    [compute setBytes:metal_push.data() length:metal_push.size() atIndex:0];
    [compute dispatchThreadgroups:MTLSizeMake((width + shader.local_size[0] - 1) / shader.local_size[0],
                                              (height + shader.local_size[1] - 1) / shader.local_size[1],
                                              1)
            threadsPerThreadgroup:MTLSizeMake(shader.local_size[0], shader.local_size[1], 1)];
}

} // namespace Metal
