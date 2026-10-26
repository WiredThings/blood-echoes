// SPDX-License-Identifier: GPL-2.0-or-later
// VideoCore::CameraTracker (shared by the Vulkan and Metal camera motion): the scene constant
// signature, the per-frame camera, the push constants of camera_motion.comp, and the history
// invalidation of a frame without a camera.
#include <array>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdint>

#include "video_core/camera_tracker.h"

// bbport_toggles.h (included by the tracker) reads this; nothing is disabled here.
extern "C" std::uint64_t runtime_disabled_optimizations = 0;
extern "C" std::uint64_t runtime_experiment_bits = 0;

namespace {

using VideoCore::CameraTracker;
using Matrix = std::array<float, 12>;

bool Near(float a, float b) {
    return std::abs(a - b) < 1e-5f;
}

bool Near(const Matrix& a, const Matrix& b) {
    for (size_t i = 0; i < a.size(); ++i) {
        if (!Near(a[i], b[i])) {
            return false;
        }
    }
    return true;
}

/// [I | t] and its inverse [I | -t], as 3x4 rows.
Matrix Translation(float x, float y, float z) {
    return {1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z};
}

/// The 864 bytes of constants the game binds for a G-buffer pass: signature, the view matrix at
/// float 8, its inverse at 180 and the projection scales at 52, 57, 62, 63.
std::array<float, 216> SceneConstants(const Matrix& view, const Matrix& inv_view) {
    std::array<float, 216> data{};
    data[0] = 3000.0f;
    data[1] = 1.0f / 3000.0f;
    data[4] = 960.0f;
    data[5] = 540.0f;
    for (size_t i = 0; i < 12; ++i) {
        data[8 + i] = view[i];
        data[180 + i] = inv_view[i];
    }
    data[52] = 1.5f;
    data[57] = -2.5f;
    data[62] = 0.5f;
    data[63] = -1.0f;
    return data;
}

/// A frame with a G-buffer pass (depth image 5) and a camera at `position`; no display pass yet.
void Gbuffer(CameraTracker& tracker, float x, float y, float z, float x_sign = 1.0f,
             float y_sign = 1.0f) {
    tracker.OnGBufferPass(VideoCore::ImageId{5}, x_sign, y_sign);
    const auto data = SceneConstants(Translation(x, y, z), Translation(-x, -y, -z));
    tracker.OnConstants(data.data());
}

} // namespace

int main() {
    // The layout of the push constants is the GLSL block's (static_asserts in the header too).
    static_assert(sizeof(VideoCore::MotionConstants) == 108);
    static_assert(offsetof(VideoCore::MotionConstants, mode) == 104);

    // InverseAffine: a rotation about z by 90 degrees and a translation.
    {
        const Matrix m = {0, -1, 0, 1, 1, 0, 0, 2, 0, 0, 1, 3};
        const auto inverse = VideoCore::InverseAffine(m);
        assert(Near(VideoCore::Multiply(inverse, m), Translation(0, 0, 0)));
        assert(Near(VideoCore::Multiply(m, inverse), Translation(0, 0, 0)));
        // Rows of the inverse: R^T and -R^T t.
        assert(Near(inverse, {0, 1, 0, -2, -1, 0, 0, 1, 0, 0, 1, -3}));
        // A singular matrix comes back unchanged.
        const Matrix singular = {1, 0, 0, 5, 1, 0, 0, 6, 0, 0, 0, 7};
        assert(VideoCore::InverseAffine(singular) == singular);
        // Multiply: translations add (b first, then a).
        assert(Near(VideoCore::Multiply(Translation(1, 2, 3), Translation(4, 5, 6)),
                    Translation(5, 7, 9)));
    }

    // Constants without the signature are ignored.
    {
        CameraTracker tracker;
        auto data = SceneConstants(Translation(0, 0, 0), Translation(0, 0, 0));
        tracker.OnGBufferPass(VideoCore::ImageId{5});
        data[0] = 1000.0f;
        tracker.OnConstants(data.data());
        data[0] = 3000.0f;
        data[1] = 0.01f;
        tracker.OnConstants(data.data());
        data[1] = 1.0f / 3000.0f;
        data[4] = 32.0f;
        tracker.OnConstants(data.data());
        assert(tracker.RenderSize() == (std::array<u32, 2>{0, 0}));
        assert(!tracker.OnDisplayPass());
        assert(!tracker.Ready());
    }

    CameraTracker tracker;
    tracker.SetJitter({0.25f, -0.25f});
    tracker.SetJitter({-0.5f, 0.125f});

    // Frame 1: the first camera, there is no previous one: not ready, no overlay.
    Gbuffer(tracker, 0.5f, 0.0f, 0.0f);
    assert(tracker.Depth() == VideoCore::ImageId{5});
    assert(tracker.RenderSize() == (std::array<u32, 2>{960, 540}));
    tracker.CommitFrameCamera();
    assert(!tracker.Ready());
    assert(!tracker.OnDisplayPass());
    assert(!tracker.Depth());
    assert(!tracker.Ready());

    // Frame 2: both cameras and the depth. Constants() reprojects with previous view * current
    // inverse view: [I | t1] * [I | -t2] = [I | t1 - t2].
    Gbuffer(tracker, 1.0f, 2.0f, 3.0f);
    tracker.CommitFrameCamera();
    tracker.CommitFrameCamera(); // once per frame
    assert(tracker.Ready());
    assert(tracker.RenderSize() == (std::array<u32, 2>{960, 540}));
    {
        const auto constants = tracker.Constants(960, 544, 1);
        assert(Near(constants.reproject, Translation(-0.5f, -2.0f, -3.0f)));
        assert(Near(constants.proj[0], 1.5f) && Near(constants.proj[1], -2.5f) &&
               Near(constants.proj[2], 0.5f) && Near(constants.proj[3], -1.0f));
        assert(constants.prev_proj == constants.proj);
        assert(constants.size[0] == 960.0f && constants.size[1] == 544.0f);
        assert(constants.jitter == (std::array<float, 2>{-0.5f, 0.125f}));
        assert(constants.previous_jitter == (std::array<float, 2>{0.25f, -0.25f}));
        assert(constants.mode == 1);
        assert(Near(tracker.Near(), 2.0f));
        assert(Near(tracker.VerticalFov(), 2.0f * std::atan(1.0f / 2.5f)));
        const auto taa = tracker.TaaDepthParameters();
        assert(taa[0] == constants.proj && taa[1] == constants.prev_proj);
        assert(Near(taa[2][0], 0.0f) && Near(taa[2][1], 0.0f) && Near(taa[2][2], 1.0f) &&
               Near(taa[2][3], -3.0f));
        assert(Near(tracker.Current().view, Translation(1, 2, 3)));
        assert(Near(tracker.Previous().view, Translation(0.5f, 0, 0)));
    }
    assert(tracker.OnDisplayPass()); // the overlay condition held
    assert(!tracker.Depth());
    assert(!tracker.Ready());

    // Frame 3 has no camera at all: the history is invalid afterwards.
    assert(!tracker.OnDisplayPass());
    assert(!tracker.Current().valid && !tracker.Previous().valid);

    // Frame 4: a camera again, but nothing to compare with; frame 5 is ready.
    Gbuffer(tracker, 1.0f, 0.0f, 0.0f);
    tracker.CommitFrameCamera();
    assert(!tracker.Ready());
    assert(!tracker.OnDisplayPass());
    Gbuffer(tracker, 1.0f, 0.0f, 0.0f);
    tracker.CommitFrameCamera();
    assert(tracker.Ready());
    assert(Near(tracker.Constants(1, 1, 0).reproject, Translation(0, 0, 0)));
    tracker.InvalidateHistory();
    assert(!tracker.Ready());
    assert(!tracker.OnDisplayPass());

    // The viewport signs of the G-buffer pass scale the projection.
    Gbuffer(tracker, 0, 0, 0, -1.0f, -1.0f);
    tracker.CommitFrameCamera();
    {
        const auto constants = tracker.Constants(960, 540, 0);
        assert(Near(constants.proj[0], -1.5f) && Near(constants.proj[1], 2.5f));
    }
    // A second G-buffer pass (another depth) brings its own camera, which wins.
    tracker.OnDisplayPass();
    Gbuffer(tracker, 1, 0, 0);
    tracker.OnGBufferPass(VideoCore::ImageId{7});
    {
        auto data = SceneConstants(Translation(9, 0, 0), Translation(-9, 0, 0));
        tracker.OnConstants(data.data());
    }
    tracker.OnConstants(SceneConstants(Translation(4, 0, 0), Translation(-4, 0, 0)).data());
    tracker.CommitFrameCamera();
    assert(tracker.Depth() == VideoCore::ImageId{7});
    assert(Near(tracker.Current().view, Translation(9, 0, 0)));

    std::puts("Camera tracker: PASS (signature, two frames, reproject, history, signs)");
    return 0;
}
