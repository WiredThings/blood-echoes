// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <memory>

#include "common/types.h"
#include "video_core/renderer_metal/mtl_fragment_guard.h"

namespace VideoCore { struct RenderState; }

namespace Metal {

/// The render commands of the rasterizer's passes (P6.2, design docs/metal/held_pass_design.md).
///
/// A held pass has no MTLRenderCommandEncoder while its draws arrive: the commands are recorded
/// here, and the scheduler replays them into the encoder it creates when the pass is released,
/// after whatever was hoisted before the pass (dispatches, tessellation kernels). In direct mode
/// every call goes to the open encoder at once (BB_MTL_HOLD=0, and the A/B baseline).
///
/// Both modes drop redundant state: a call that sets what the same slot already holds since the
/// pass began (pipeline, depth-stencil state, fixed-function state, buffer/texture/sampler of a
/// stage slot, vertex buffer with stride) is not forwarded or recorded. Byte data (SetBytes) is
/// copied; its slot is always set again.
///
/// Objects are plain pointers (bridged id<...>), not retained: the caches destroy them only after
/// the command buffer of the tick they were used in has completed, and a held pass is replayed
/// before its command buffer is committed. Labels and debug group names are copied.
///
/// Plain C++ header (no Objective-C types); one thread at a time (the recording thread, or the
/// replay thread for a list given to it with TakeRecording).
class RenderList {
public:
    enum class Stage : u8 { Vertex, Fragment };

    /// MTLViewport and MTLScissorRect, field for field.
    struct Viewport {
        double origin_x, origin_y, width, height, znear, zfar;
    };
    struct Scissor {
        u64 x, y, width, height;
    };

    RenderList();
    ~RenderList();
    RenderList(const RenderList&) = delete;
    RenderList& operator=(const RenderList&) = delete;

    // ---- Mode (the scheduler) ----

    /// Calls go to `encoder` (id<MTLRenderCommandEncoder>) from now on; the redundancy filter
    /// starts empty.
    void BeginDirect(void* encoder);
    /// Owner-supplied full V|F dependency. Apple GPUs require a physical pass boundary;
    /// returns its replacement encoder. The owner preserves attachments, queries and fences.
    using BarrierEncoder = void* (*)(void* owner, void* encoder);
    void SetBarrierEncoder(void* owner, BarrierEncoder barrier);
    /// Continue the SAME physical pass after Replay; preserve diagnostic access history.
    void ContinueDirect(void* encoder);
    /// Calls are recorded from now on; the redundancy filter starts empty.
    void BeginRecording();
    /// Replays what was recorded into `encoder`, in order, and empties the list. The mode stays
    /// recording until the next Begin*.
    void Replay(void* encoder);
    /// Moves what was recorded into `into` (empty, in no mode), which is then in recording mode
    /// and can be replayed on another thread (the scheduler's replay thread); this list stays
    /// recording, empty. The two lists may then be used on different threads.
    void TakeRecording(RenderList& into);
    /// Leaves either mode (the encoder is ended by the scheduler); recorded commands must have
    /// been replayed.
    void End();

    [[nodiscard]] bool Recording() const noexcept;
    /// Nothing recorded since BeginRecording or the last Replay.
    [[nodiscard]] bool Empty() const noexcept;
    /// A MemoryBarrier was recorded since BeginRecording (the scheduler waits for the fences of
    /// earlier encoders when it begins the encoder for the replay).
    [[nodiscard]] bool HasBarrier() const noexcept;
    // Opt-in fragment-access diagnostics; no render command or dependency changes.
    void TrackConsumedClear(const VideoCore::RenderState&, bool bound_only);
    void InvalidateConsumedClear() noexcept;
    bool CanContinueConsumedClear() const noexcept;
    bool CanContinueDepthLayout() const noexcept;
    bool FragmentGuardEnabled() const noexcept;
    void FragmentBuffer(void* native,u64 offset,u64 size);
    void FragmentUnknown(unsigned reason);
    void FragmentReady();
    FragmentAccessGuard::Snapshot FragmentSnapshot(void* native,u64 offset,u64 size) const;

    // ---- Fixed-function state (MTLRenderCommandEncoder setters of the same names) ----

    void SetRenderPipelineState(void* pipeline_state);
    void SetDepthStencilState(void* depth_stencil_state);
    void SetStencilReference(u32 front, u32 back);
    void SetDepthBias(float constant, float slope_scale, float clamp);
    void SetCullMode(u32 mode);             ///< MTLCullMode
    void SetFrontFacingWinding(u32 winding); ///< MTLWinding
    void SetTriangleFillMode(u32 mode);     ///< MTLTriangleFillMode
    void SetDepthClipMode(u32 mode);        ///< MTLDepthClipMode
    void SetBlendColor(float red, float green, float blue, float alpha);
    void SetViewports(const Viewport* viewports, u32 count);
    void SetScissorRects(const Scissor* scissors, u32 count);

    // ---- Resources ----

    void SetBuffer(Stage stage, void* buffer, u64 offset, u32 index);
    /// setVertexBuffer:offset:attributeStride:atIndex: (vertex descriptors with dynamic strides).
    void SetVertexBufferWithStride(void* buffer, u64 offset, u64 stride, u32 index);
    /// set{Vertex,Fragment}Bytes: `length` bytes copied now (at most 4 KiB).
    void SetBytes(Stage stage, const void* data, u32 length, u32 index);
    void SetTexture(Stage stage, void* texture, u32 index);
    void SetSampler(Stage stage, void* sampler, u32 index);
    void SetTessellationFactorBuffer(void* buffer, u64 offset, u64 instance_stride);

    // ---- Draws (MTLPrimitiveType, MTLIndexType as u32) ----

    void DrawPrimitives(u32 type, u64 vertex_start, u64 vertex_count, u64 instance_count,
                        u64 base_instance);
    void DrawIndexedPrimitives(u32 type, u64 index_count, u32 index_type, void* index_buffer,
                               u64 index_offset, u64 instance_count, s64 base_vertex,
                               u64 base_instance);
    void DrawPrimitivesIndirect(u32 type, void* indirect_buffer, u64 indirect_offset);
    void DrawIndexedPrimitivesIndirect(u32 type, u32 index_type, void* index_buffer,
                                       u64 index_offset, void* indirect_buffer,
                                       u64 indirect_offset);
    /// drawPatches without a patch index buffer.
    void DrawPatches(u32 control_points, u64 patch_start, u64 patch_count, u64 instance_count,
                     u64 base_instance);

    // ---- Ordering and labels ----

    /// memoryBarrierWithScope:Buffers|Textures afterStages:Vertex|Fragment
    /// beforeStages:Vertex|Fragment (the scheduler's in-pass barrier).
    void MemoryBarrier();
    /// Explicit vertex barriers; callers exclude earlier fragment/attachment aliases.
    void VertexBufferUpdate(void* pipeline,void* buffer,u64 offset,const void* data,u32 bytes);
    struct BarrierCounts { u64 requested{}, dropped{}; };
    [[nodiscard]] BarrierCounts BarrierSnapshot() const noexcept;
    void PushDebugGroup(const char* name);
    void PopDebugGroup();
    void InsertDebugSignpost(const char* name);

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Metal
