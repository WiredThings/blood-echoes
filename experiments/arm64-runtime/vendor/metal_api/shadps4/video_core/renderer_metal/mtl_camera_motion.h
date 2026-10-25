// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: camera motion vectors on Metal (plan P5.4, docs/metal/temporal_upscaling_design.md):
// the camera tracking of VideoCore::CameraTracker (shared with the Vulkan backend) plus the
// motion vector compute pass, camera_motion.comp as the D11 host shader. No object motion and no
// debug overlay (BB_DEBUG_MOTION is Vulkan only).

#pragma once

#include <array>
#include <memory>

#include "common/types.h"
#include "video_core/camera_tracker.h"
#include "video_core/scene_passes.h"

namespace Metal {

class Device;
class Scheduler;
class HostKernels;

/// Threads: the tracking calls and RecordMotion come from the thread that records (the draw
/// recording thread or the GPU command thread), one at a time, like the scheduler's.
class CameraMotion final : public VideoCore::CameraMotionApi {
public:
    // Shared kernels are consumed only during construction; no borrowed reference
    // is stored. Must correspond to the same renderer Device/Scheduler.
    CameraMotion(Device& device, Scheduler& scheduler, HostKernels& kernels);
    ~CameraMotion() override;

    CameraMotion(const CameraMotion&) = delete;
    CameraMotion& operator=(const CameraMotion&) = delete;

    /// The camera is tracked unless BB_UPSCALER=none (as on Vulkan: the upscaler can be switched
    /// on from the menu at any time).
    [[nodiscard]] bool Enabled() const noexcept override {
        return enabled;
    }
    [[nodiscard]] VideoCore::ImageId Depth() const noexcept override {
        return tracker.Depth();
    }
    void InvalidateHistory() override {
        tracker.InvalidateHistory();
    }
    void SetJitter(std::array<float, 2> value) noexcept override {
        tracker.SetJitter(value);
    }
    void OnConstants(const float* data) override {
        tracker.OnConstants(data);
    }
    void OnGBufferPass(VideoCore::ImageId depth, float x_sign, float y_sign) override {
        tracker.OnGBufferPass(depth, x_sign, y_sign);
    }
    /// The overlay is not available; the frame ends as the tracker says.
    void OnDisplayPass(VideoCore::ImageId) override {
        tracker.OnDisplayPass();
    }

    /// Both cameras and the scene depth of the current frame are known.
    [[nodiscard]] bool Ready() const noexcept {
        return tracker.Ready();
    }
    /// The render size in the scene constants, or zero before the first camera.
    [[nodiscard]] std::array<u32, 2> RenderSize() const noexcept {
        return tracker.RenderSize();
    }
    [[nodiscard]] float Near() const noexcept {
        return tracker.Near();
    }
    [[nodiscard]] float VerticalFov() const noexcept {
        return tracker.VerticalFov();
    }
    void PrintState(int frame) const {
        tracker.PrintState(frame);
    }

    /// Commits the frame's camera and dispatches the motion vector pass on the scheduler's
    /// compute encoder (opened if needed, left open): `depth_texture` (the scene depth) into
    /// `motion_texture` (RG16Float, ShaderWrite: pixels, previous minus current) over `width` x
    /// `height` pixels. Both are id<MTLTexture> (bridged void*); hazard tracked.
    void RecordMotion(const void* depth_texture, const void* motion_texture, u32 width, u32 height);

private:
    struct Impl;

    Scheduler& scheduler;
    VideoCore::CameraTracker tracker;
    bool enabled = false;
    std::unique_ptr<Impl> impl;
};

} // namespace Metal
