// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>

#include "core/libraries/videoout/buffer.h"
#include "video_core/frames_ahead.h"
#include "video_core/presenter_api.h"

namespace Frontend {
class WindowSDL;
}

namespace AmdGpu {
struct Liverpool;
}

namespace Metal {

class Device;
class Rasterizer;
class Runtime;
class Scheduler;
// Pure scalar provider of the original frame attachment format; no Presenter,
// Window, texture or CAMetalLayer construction is involved.
u32 PresentationFrameFormat() noexcept;

/// A frame of the pool. The game's video-out image is rendered (post process pass) into the
/// frame's texture (Impl::textures[index]) on the draw scheduler; Present draws that texture into
/// the drawable on the presenter's own queue, after the draw tick `tick`.
struct Frame : VideoCore::PresentFrame {
    u32 index{};  ///< the slot in the pool (and in Impl's arrays)
    u32 width{};  ///< the frame texture's size (the video-out buffer's)
    u32 height{};
    /// The draw tick whose command buffer renders the texture; 0: a blank frame (Present clears).
    /// Written by the thread that prepares the frame, read by WaitRendered on another.
    std::atomic<u64> tick{0};
    /// P7.2: the frame between the previous game frame and this one is rendered into
    /// Impl::interpolated[index] by the same command buffer (same size as the texture). Written
    /// before `tick`, like the texture; Present shows it first when the frame is a new one.
    bool interpolated{false};
};

/// bbport: the Metal presenter (P4.3c, D9). Owns the device, scheduler ("bbport draw"), cache
/// backend and rasterizer like the Vulkan one. A game frame is the video-out image drawn through
/// the post process shader into a frame texture on the draw scheduler (PrepareFrame, the GPU
/// command thread); Present (the present thread) waits for that tick on its own command queue
/// and draws the texture, aspect-fit, into the CAMetalLayer's drawable.
///
/// Threads: PrepareFrame/PrepareBlankFrame(false): the GPU command thread or the draw recording
/// thread (one at a time: they own the draw scheduler, PrepareFrame drains the draw pipe first).
/// PrepareBlankFrame(true), PrepareLastFrame, Present: the present thread (touch no draw state).
/// WaitRendered: any thread. With BB_FRAME_INTERPOLATION=1 (P7.2, mtl_frame_interpolator.h) a
/// frame that came through the MetalFX scaler carries a second texture, the interpolated frame
/// before it: PrepareFrame renders both, Present shows both (two drawables, half the game's
/// frame interval apart). The pool is shared (free_mutex); a frame returns to it from the
/// completion of the command buffer that reads its texture.
class Presenter final : public VideoCore::PresenterApi {
public:
    Presenter(Frontend::WindowSDL& window, AmdGpu::Liverpool* liverpool);
    ~Presenter() override;

    bool IsHDRSupported() const override {
        return false;
    }
    void SetHDR(bool enable) override {
        hdr = enable;
    }
    void SetGamma(float value) override {
        gamma = value;
    }

    void RegisterVideoOutSurface(const Libraries::VideoOut::BufferAttributeGroup& attribute,
                                 VAddr cpu_address) override;
    VideoCore::PresentFrame* PrepareFrame(const Libraries::VideoOut::BufferAttributeGroup& attribute,
                                          VAddr cpu_address) override;
    VideoCore::PresentFrame* PrepareBlankFrame(bool present_thread) override;
    VideoCore::PresentFrame* PrepareLastFrame() override;
    void Present(VideoCore::PresentFrame* frame, bool is_reusing_frame = false,
                 bool is_game_frame = true) override;
    /// Waits until the GPU has finished the commands that render `frame` (any thread).
    void WaitRendered(const VideoCore::PresentFrame* frame) override;

private:
    /// A free frame; waits for one.
    Frame* GetRenderFrame();
    /// Puts a frame back into the pool (the completion of its Present, or a skipped Present).
    void FreeFrame(Frame* frame);

    static constexpr u32 NumFrames = 4;

    struct Impl; ///< the CAMetalLayer (mtl_presenter.mm)

    Frontend::WindowSDL& window;
    AmdGpu::Liverpool* liverpool;
    // Destroyed in reverse: the rasterizer before the cache backend, scheduler and device.
    std::unique_ptr<Device> device;
    std::unique_ptr<Scheduler> scheduler;
    std::unique_ptr<Runtime> runtime;
    std::unique_ptr<Rasterizer> rasterizer;
    std::unique_ptr<Impl> impl;
    std::array<Frame, NumFrames> frames;
    std::deque<Frame*> free_queue;
    std::mutex free_mutex;
    std::condition_variable free_cv;
    Frame* last_submit_frame{}; ///< under free_mutex
    bool hdr{};
    float gamma{1.0f};
    VideoCore::FramesAhead frames_ahead; ///< BB_FRAMES_AHEAD bound (PrepareFrame, draw thread)
    std::atomic<u32> presents_in_flight{0}; ///< committed Present command buffers not completed
};

} // namespace Metal
