// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <queue>
#include <string>
#include <thread>
#include <vector>

#include "common/types.h"
#include "common/unique_function.h"
#include "video_core/gpu_queue.h"
#include "video_core/renderer_metal/mtl_render_list.h"
#include "video_core/renderer_metal/mtl_device.h"

namespace Metal {

/// Command buffers, encoders, ticks and deferred operations of the Metal backend (D6-D8, see
/// docs/metal/mtl_scheduler_design.md). The Objective-C objects live in Impl
/// (mtl_scheduler_impl.h, included by .mm files only).
///
/// Threads: the recording calls (the encoder calls, MemoryBarrier, the debug labels, Flush,
/// Finish, and Wait/Pop on the open tick) come from one thread at a time, the one that records
/// (the draw recording thread or the GPU command thread). CurrentTick, IsFree, IsFreeKnown,
/// RefreshTicks, Wait/WaitTick on a tick that was flushed, DeferOperation and
/// DeferPriorityOperation may come from any thread.
///
/// A tick is the number of a command buffer; the open one has CurrentTick() (starting at 1). It
/// is free once the GPU has finished that command buffer and every earlier one.
///
/// Also the rasterizer core's queue (VideoCore::GpuQueue, P4.3a): recording is immediate (no
/// recording threads, host copies run at once), so the recording-order and host-copy members are
/// no-ops or run their function at once.
///
/// Held passes (P6.2, docs/metal/held_pass_design.md; BB_MTL_HOLD=0 turns them off): a pass the
/// rasterizer begins (RenderEncoder with a state) has no MTLRenderCommandEncoder at first; its
/// commands go to Commands() in recording mode. Compute work between BeginHoist and EndHoist is
/// encoded at once into a compute encoder that comes before the pass. Anything else that needs
/// an encoder (another pass, a dispatch, a transfer, Flush) releases the held pass first: the
/// hoisted encoder ends, the render encoder is made and the recorded commands replayed into it;
/// the pass then goes on unheld (direct mode).
///
/// Replay thread (P8.6; BB_MTL_REPLAY_THREAD=0 turns it off, experiment bit MtlNoReplayThread
/// while the game runs): a held pass released because the next held pass begins is encoded on
/// the scheduler's own thread (hoisted encoder ended, render encoder made, commands replayed,
/// encoder ended) while the recording thread records the next passes. Up to ReplayDepth jobs in
/// flight, in order; they own Impl's encoders, fences and the command buffer's encoding until
/// JoinReplay, which every member that touches them calls first (the held pass's own recorded
/// calls do not).
class Scheduler final : public VideoCore::GpuQueue {
public:
    enum class EncoderKind : u8 { None, Render, Compute, Blit };
    /// Held passes the replay thread may have in flight.
    static constexpr u32 ReplayDepth = 4;

    /// `name` labels the command buffers ("<name> #<tick>", seen in Xcode and error reports).
    explicit Scheduler(Device& device, const char* name = "bbport");
    /// Flushes what is open, waits for the GPU and runs every deferred operation.
    ~Scheduler() override;

    Scheduler(const Scheduler&) = delete;
    Scheduler& operator=(const Scheduler&) = delete;

    struct Impl;
    Impl& GetImpl() const {
        InvalidatePreparedCompute();
        return *impl;
    }

    // A physical guest render pass (also on the replay thread) may count its samples.
    // Host copies, resolves and postprocessing are excluded. Replacing the hook joins replay.
    using VisibilityQuery = std::function<std::pair<void*, u64>(const void*)>;
    enum class VisibilityMergePolicy { Forbidden, Additive, NativeDensity };
    void SetVisibilityQuery(VisibilityQuery query,
                            VisibilityMergePolicy policy = VisibilityMergePolicy::Forbidden);

    // Recording-thread host work after the current guest encoder, before the next encoder,
    // barrier, hoist, query snapshot or submission. Never invoked on the replay thread.
    void SetRenderTail(std::function<void()> callback);
    void RequestRenderTail() noexcept { InvalidatePreparedCompute(); render_tail_pending = true; }
    void FlushRenderTail() { if (render_tail_pending && !running_render_tail) EndEncoder(); }

    // Encoders. At most one is open; asking for another kind (or a new render pass) ends the
    // open one. The Objective-C encoder is Impl::render/compute/blit.

    /// Begins a render pass; `pass_descriptor` is an MTLRenderPassDescriptor (bridged void*).
    /// Always a new pass: the caller decides to continue a pass by checking IsRendering().
    void RenderEncoder(const void* pass_descriptor);
    /// Known host shaders with directly bound resources only; no raw guest GPU pointers.
    void HostRenderEncoder(const void* pass_descriptor);
    /// Union indirect residency into a recorded/reused pass before a shader uses raw pointers.
    void RequireGuestRender();
    /// The same, remembering the state the pass was begun with (IsRenderingWith compares it).
    void RenderEncoder(const void* pass_descriptor, const VideoCore::RenderState& state, bool guest = true, bool bound_only = false);
    /// The render commands of the rasterizer's pass (the one begun with a RenderState): valid
    /// while IsRendering() after such a RenderEncoder. Passes begun without a state (the
    /// upscaler, the presenter, the runtime's kernels) use Impl::render directly.
    [[nodiscard]] RenderList& Commands() noexcept {
        InvalidatePreparedCompute();
        return commands;
    }
    /// The open compute encoder, or a new one (dispatches of one encoder run in order). `label`
    /// names a new encoder with BB_PASS_LABELS=1.
    void ComputeEncoder(const char* label = nullptr, bool bound_only = false);
    // Recording-thread diagnostic snapshot; no joins, fence changes or resource accesses.
    // Bits: inactive1, no-state2, recorded-barrier4, pending-fence8, hoist16,
    // render-tail32, pending-clear64, folded-clear128. Zero is NOT merge permission.
    u32 ContinuationBlockers() const noexcept;
    // Opt-in. Same private attachment roots and no pending work; caller excludes
    // current sampled textures. Query snapshots already close/join this scheduler.
    bool ContinueDepthLayout(const VideoCore::RenderState& next);
    bool CanInlineBufferUpdate() const noexcept;
    bool InlineBufferUpdate(void* pipeline,void* buffer,u64 offset,const void* data,u32 bytes);
    /// The open blit encoder, or a new one.
    void BlitEncoder(const char* label = nullptr);
    /// Ends the open encoder, if any (the command buffer stays open).
    void EndEncoder();
    // Independent full color clears only, no new textures or expanded cache.
    bool QueueColorClear(void* texture, const std::array<double,4>& color);
    /// Ends the open encoder when it is a render pass.
    void EndRendering();
    /// Close all encoded work (join replay too); the next encoder waits for its fences.
    void EndWithBarrier() { EndEncoder(); need_fence_wait = true; }
    /// Join render/replay producers, then order directly bound host work in a serial compute
    /// encoder. Reuse an existing compute encoder without dropping its memory dependencies.
    void BeginOrderedHostCompute(const char* label);
    /// Publish host compute writes to the next dispatch and force the next physical encoder
    /// to wait for this encoder's fence. Keeps this serial compute encoder open.
    void OrderAfterHostCompute();
    [[nodiscard]] bool IsRendering() const noexcept {
        return open_encoder == EncoderKind::Render;
    }
    [[nodiscard]] EncoderKind OpenEncoder() const noexcept {
        return open_encoder;
    }
    /// Whether a command buffer is open (something may have been recorded for CurrentTick()).
    [[nodiscard]] bool HasOpenCommandBuffer() const noexcept;

    // Hazards (D7). Buffers and textures are hazard-tracked; this is for accesses Metal cannot
    // see (device addresses). The shared code reports them (AccessBuffer, AccessGlobal, Transit)
    // and calls FlushBarriers, which ends up here.

    /// Makes the writes of everything recorded so far visible to what is recorded next: inside a
    /// compute or render encoder a memory barrier (plus a wait on the fences of earlier encoders
    /// still outstanding), a blit encoder is ended. Earlier encoders are ordered through fences,
    /// which every encoder updates at its end.
    void MemoryBarrier();
    /// The next NEW encoder must wait for earlier fences, including an ordered host-compute
    /// encoder deliberately kept open. Reusing the current compute encoder retains this flag.
    [[nodiscard]] bool NeedFenceWait() const noexcept {
        return need_fence_wait;
    }

    // Exclusive recording-thread one-shot reservation, not a guest lifetime lease.
    // Any subsequent scheduler/native access cancels it, even in the same encoder/tick.
    struct PreparedComputeStamp {
        u64 epoch{},tick{};
        void* encoder{};
        void* command_buffer{};
        std::thread::id thread;
    };
    std::optional<PreparedComputeStamp> ArmPreparedCompute() noexcept;
    bool ConsumePreparedCompute(const PreparedComputeStamp&) noexcept;
    void CancelPreparedCompute(const PreparedComputeStamp&) noexcept;
    bool HasPreparedCompute() const noexcept { return prepared_armed.load(std::memory_order_acquire); }

    // Labels. Groups and signposts go to the open encoder; groups pushed while none is open
    // apply to the encoders that follow. Off unless BB_MTL_LABELS=1 (the strings cost per draw).

    void PushDebugGroup(const char* name);
    void PopDebugGroup();
    void InsertDebugSignpost(const char* name);
    /// BB_PASS_LABELS=1 (diagnostics, for Metal System Traces): names the open encoder.
    [[nodiscard]] bool PassLabels() const noexcept {
        return pass_labels;
    }
    void LabelEncoder(const char* label);

    // Ticks and submission

    [[nodiscard]] u64 CurrentTick() const noexcept {
        return current_tick.load(std::memory_order_acquire);
    }
    /// Ends the open encoder and submits the open command buffer (with a signal of CurrentTick()
    /// on the event), advances the tick and runs the operations whose tick is free. The submit
    /// callback (SetSubmitCallback) runs first, then the operations it put in the submit info.
    void Flush() override;
    /// Flush, then wait for the GPU.
    void Finish();
    /// Whether the GPU finished everything up to `tick` (refreshes the known tick if it was not).
    [[nodiscard]] bool IsFree(u64 tick) noexcept;
    /// Without the refresh.
    [[nodiscard]] bool IsFreeKnown(u64 tick) const noexcept {
        return tick <= known_tick.load(std::memory_order_acquire);
    }
    void RefreshTicks();
    /// Waits for `tick`; submits first when it is the open tick. Bounded waits in a loop; returns
    /// early only when a command buffer failed (reported, see SetErrorHandler).
    void Wait(u64 tick);
    /// One bounded wait (for the core's signal loop, WaitGpuTick): whether `tick` is free now.
    /// Never submits.
    [[nodiscard]] bool WaitTick(u64 tick, u64 timeout_ns);

    // VideoCore::GpuQueue (recording is immediate, see the class comment)

    void SetSubmitCallback(VideoCore::SubmitFunc&& on_submit) override;
    /// Ticks below CurrentTick() are committed already; for a later one this waits (as the Vulkan
    /// queue does) until it is submitted.
    void WaitSubmitted(u64 tick) override;
    [[nodiscard]] u64 KnownGpuTick() const noexcept override {
        return known_tick.load(std::memory_order_acquire);
    }
    void ReportStuck(const char* where) override;
    void KickRecording(bool force = false) override {}
    void SyncRecording() override {}
    /// Whether the open encoder is a render pass begun with exactly `state`.
    /// The state the open render pass was begun with, if any.
    [[nodiscard]] const VideoCore::RenderState* OpenRenderState() const noexcept {
        return open_encoder == EncoderKind::Render && render_state ? &*render_state : nullptr;
    }
    [[nodiscard]] bool IsRenderingWith(const VideoCore::RenderState& state) const override {
        return open_encoder == EncoderKind::Render && render_state && *render_state == state;
    }
    [[nodiscard]] bool WillBeginRendering(const VideoCore::RenderState& state) const override {
        return !IsRenderingWith(state);
    }
    /// Continue a consumed-clear pass only after state and feedback checks by the caller.
    bool ContinueConsumedClear(const VideoCore::RenderState& next);
    /// Commands recorded from now on until EndHoist go before the held render pass.
    void BeginHoist() noexcept override {
        InvalidatePreparedCompute();
        FlushRenderTail();
        hoisting = holding;
    }
    void EndHoist() noexcept override {
        InvalidatePreparedCompute();
        hoisting = false;
    }
    /// Makes the held pass's render encoder and replays its commands; it stays open, unheld.
    void ReleaseHeldPass() override;
    void WaitHostCopies() override {}
    void WaitHostCopiesFor(u64 address, u64 size) override {}
    void SignalAfterHostCopies(std::function<void()> signal) override {
        signal();
    }
    void WaitDeferredSignals() override {}

    // Deferred operations (D6): destroys of GPU objects wait here, since the command buffers do
    // not retain what they use.

    /// Runs `func` once the GPU is done with the open tick (at PopPendingOperations or Flush).
    void DeferOperation(Common::UniqueFunction<void>&& func);
    /// Runs `func` on a priority thread as soon as the GPU is done with the open tick.
    void DeferPriorityOperation(Common::UniqueFunction<void>&& func);
    /// Runs the deferred operations whose tick is free (in order, outside the lock).
    void PopPendingOperations();

    // Errors

    /// A failed command buffer prints its error and label and asserts, as a lost Vulkan device
    /// does. With a handler set (the unit test) the handler gets the report instead.
    using ErrorHandler = std::function<void(const std::string&)>;
    void SetErrorHandler(ErrorHandler handler);
    [[nodiscard]] bool HasFailed() const noexcept {
        return failed.load(std::memory_order_acquire);
    }
    /// The completed handler's path for a command buffer that ended in error (Metal's thread);
    /// public for the unit test.
    void ReportFailure(u64 tick, const std::string& label, const std::string& what);

private:
    struct PendingOp {
        Common::UniqueFunction<void> callback;
        u64 gpu_tick;
    };

    void BeginEncoder(EncoderKind kind, const void* pass_descriptor, bool guest = false, bool bound_only = false);
    /// Creates the encoder of `kind` (none may be open): fence waits, guest chunk use, groups.
    /// `wait_all`: wait for the fences of every earlier encoder still outstanding first.
    void OpenPhysical(EncoderKind kind, const void* pass_descriptor, bool wait_all, bool guest = false, bool bound_only = false);
    /// Ends the physical encoder of `kind` (fence update, groups).
    void ClosePhysical(EncoderKind kind);
    void PriorityPendingOpsThread(std::stop_token stoken);
    /// ClosePhysical without the rasterizer's RenderList (the replay thread's part).
    void CloseNative(EncoderKind kind);
    void FlushColorClears();
    /// Consume only an exact, full-surface pending clear batch. No unknown lifetime inference.
    const void* FoldColorClears(const void* descriptor);
    /// Hoisted work may read the clear values before the held pass: restore and flush first.
    void RestoreFoldedClears();
    void CommitFoldedClears();
    /// Gives the held pass to the replay thread (waits for a free slot).
    void PostReplay();
    /// Waits until the replay thread's jobs are done (never called on that thread).
    void JoinReplay();
    /// Waits until `target` jobs are done.
    void WaitReplay(u32 target);
    void RunReplayJob(u32 slot);
    void ReplayThread(std::stop_token stoken);

    void InvalidatePreparedCompute() const noexcept {
        if(prepared_armed.load(std::memory_order_relaxed) &&
           prepared_armed.exchange(false,std::memory_order_acq_rel))
            prepared_epoch.fetch_add(1,std::memory_order_relaxed);
    }
    mutable std::atomic<bool> prepared_armed{false};
    mutable std::atomic<u64> prepared_epoch{0};
    Device& device;
    std::unique_ptr<Impl> impl;
    std::string name;

    EncoderKind open_encoder = EncoderKind::None;
    /// The state of the open render pass, when it was begun with one (IsRenderingWith).
    std::optional<VideoCore::RenderState> render_state;
    RenderList commands;
    bool commands_active = false; ///< the open render pass is the rasterizer's (Commands())
    bool held_bound_only = false; ///< union of all shader accesses in the pending pass
    bool held_guest = true;       ///< immutable tag carried into replay; host mirrors never count
    bool hold_env = true;         ///< BB_MTL_HOLD (default 1)
    bool hoisting = false;        ///< between BeginHoist and EndHoist of a held pass
    bool hoisted = false;         ///< the held pass has a compute encoder before it
    std::string held_label;       ///< BB_PASS_LABELS: the held pass's encoder label
    VideoCore::SubmitFunc on_submit;
    VisibilityQuery visibility_query;
    VisibilityMergePolicy visibility_merge_policy = VisibilityMergePolicy::Forbidden;
    std::function<void()> render_tail;
    bool render_tail_pending = false, running_render_tail = false;
    bool in_submit_callback = false; ///< the callback may record or flush again
    bool need_fence_wait = false; ///< a barrier came while no encoder was open
    bool labels_enabled = false;
    bool pass_labels = false;
    std::vector<std::string> debug_groups; ///< pushed, applied to every encoder that opens

    std::atomic<u64> current_tick{1};
    std::atomic<u64> known_tick{0};
    /// The newest tick whose command buffer has completed (its completed handler ran). The event
    /// is signalled by the buffer's last command, slightly before Metal retires the buffer and its
    /// references: deferred operations (destroys) wait for this one, not for known_tick.
    std::atomic<u64> retired_tick{0};
    std::atomic<u32> handlers_in_flight{0}; ///< committed command buffers whose handler has not run

    std::mutex pending_ops_mutex;
    std::queue<PendingOp> pending_ops;
    std::atomic<u32> num_pending_ops{0};

    std::mutex priority_mutex;
    std::condition_variable_any priority_cv;
    std::queue<PendingOp> priority_pending_ops;
    std::jthread priority_thread;

    // Replay thread (see the class comment).
    struct ReplayJob {
        RenderList list;            ///< the pass's commands
        bool bound_only = false;
        bool guest = true;         ///< captured when posted, never a global replay-time toggle
        bool close_compute = false; ///< end the hoisted compute encoder first
        bool wait_all = false;      ///< the render encoder waits for every earlier encoder
        std::string label;          ///< BB_PASS_LABELS: the encoder label
    };
    bool replay_enabled = false; ///< BB_MTL_REPLAY_THREAD (default 1)
    /// Jobs in flight, by number modulo ReplayDepth (the pass descriptors: Impl::replay_passes).
    std::array<ReplayJob, ReplayDepth> replay_ring;
    std::atomic<u32> replay_posted{0}; ///< jobs posted (by the thread that records)
    std::atomic<u32> replay_done{0};   ///< jobs finished (by the replay thread)
    u64 replay_jobs = 0, replay_joins = 0, replay_wait_ns = 0; ///< BB_FRAME_STATS
    std::chrono::steady_clock::time_point replay_window{};
    std::jthread replay_thread;

    std::mutex error_mutex;
    ErrorHandler error_handler;
    std::atomic<bool> failed{false};
};

} // namespace Metal
