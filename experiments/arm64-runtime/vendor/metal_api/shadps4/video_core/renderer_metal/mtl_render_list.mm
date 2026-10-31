// SPDX-License-Identifier: GPL-2.0-or-later

#import <Metal/Metal.h>

#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <utility>

#include "video_core/renderer_metal/mtl_render_list.h"
#include "video_core/renderer_metal/mtl_pass_continuation.h"

namespace Metal {

namespace {

// The arena: records of {Header, payload struct, trailing bytes}, each padded to 8 bytes (so a
// payload or its trailing Viewport/Scissor array is read in place, aligned). Header::size is the
// whole record. The payloads are POD; pointers are the bridged objects (not retained).

enum class Op : u32 {
    SetRenderPipelineState,
    SetDepthStencilState,
    SetStencilReference,
    SetDepthBias,
    SetCullMode,
    SetFrontFacingWinding,
    SetTriangleFillMode,
    SetDepthClipMode,
    SetBlendColor,
    SetViewports,
    SetScissorRects,
    SetBuffer,
    SetBufferOffset,
    SetVertexBufferWithStride,
    SetBytes,
    SetTexture,
    SetSampler,
    SetTessellationFactorBuffer,
    DrawPrimitives,
    DrawIndexedPrimitives,
    DrawPrimitivesIndirect,
    DrawIndexedPrimitivesIndirect,
    DrawPatches,
    MemoryBarrier,
    PushDebugGroup,
    PopDebugGroup,
    InsertDebugSignpost,
    VertexBarrier,
};

struct Header {
    Op op;
    u32 size;
};
static_assert(sizeof(Header) == 8);

static_assert(sizeof(RenderList::Viewport) == sizeof(MTLViewport));
static_assert(sizeof(RenderList::Scissor) == sizeof(MTLScissorRect));

using Stage = RenderList::Stage;

// Payloads. `Apply` is the one place a command meets the encoder (direct mode and Replay).

struct CmdPipeline {
    static constexpr Op op = Op::SetRenderPipelineState;
    void* state;
};
struct CmdDepthStencil {
    static constexpr Op op = Op::SetDepthStencilState;
    void* state;
};
struct CmdStencilReference {
    static constexpr Op op = Op::SetStencilReference;
    u32 front, back;
};
struct CmdDepthBias {
    static constexpr Op op = Op::SetDepthBias;
    float constant, slope_scale, clamp;
    u32 pad;
};
template <Op O>
struct CmdMode {
    static constexpr Op op = O;
    u32 value;
    u32 pad;
};
struct CmdBlendColor {
    static constexpr Op op = Op::SetBlendColor;
    float r, g, b, a;
};
template <Op O>
struct CmdArray { ///< + `count` Viewports or Scissors
    static constexpr Op op = O;
    u32 count;
    u32 pad;
};
struct CmdBuffer {
    static constexpr Op op = Op::SetBuffer;
    void* buffer;
    u64 offset;
    u32 index;
    Stage stage;
};
struct CmdBufferOffset {
    static constexpr Op op = Op::SetBufferOffset;
    u64 offset;
    u32 index;
    Stage stage;
};
struct CmdVertexBuffer {
    static constexpr Op op = Op::SetVertexBufferWithStride;
    void* buffer;
    u64 offset;
    u64 stride;
    u32 index;
    u32 pad;
};
struct CmdBytes { ///< + `length` bytes
    static constexpr Op op = Op::SetBytes;
    u32 length;
    u32 index;
    Stage stage;
};
struct CmdTexture {
    static constexpr Op op = Op::SetTexture;
    void* texture;
    u32 index;
    Stage stage;
};
struct CmdSampler {
    static constexpr Op op = Op::SetSampler;
    void* sampler;
    u32 index;
    Stage stage;
};
struct CmdTessFactor {
    static constexpr Op op = Op::SetTessellationFactorBuffer;
    void* buffer;
    u64 offset;
    u64 instance_stride;
};
struct CmdDraw {
    static constexpr Op op = Op::DrawPrimitives;
    u64 vertex_start, vertex_count, instance_count, base_instance;
    u32 type;
    u32 pad;
};
struct CmdDrawIndexed {
    static constexpr Op op = Op::DrawIndexedPrimitives;
    void* index_buffer;
    u64 index_count, index_offset, instance_count;
    s64 base_vertex;
    u64 base_instance;
    u32 type;
    u32 index_type;
};
struct CmdDrawIndirect {
    static constexpr Op op = Op::DrawPrimitivesIndirect;
    void* indirect_buffer;
    u64 indirect_offset;
    u32 type;
    u32 pad;
};
struct CmdDrawIndexedIndirect {
    static constexpr Op op = Op::DrawIndexedPrimitivesIndirect;
    void* index_buffer;
    u64 index_offset;
    void* indirect_buffer;
    u64 indirect_offset;
    u32 type;
    u32 index_type;
};
struct CmdPatches {
    static constexpr Op op = Op::DrawPatches;
    u64 patch_start, patch_count, instance_count, base_instance;
    u32 control_points;
    u32 pad;
};
struct CmdBarrier {
    static constexpr Op op = Op::MemoryBarrier;
    u64 pad;
};
struct CmdVertexBarrier {static constexpr Op op=Op::VertexBarrier;u64 pad;};
template <Op O>
struct CmdLabel { ///< + `length` characters (with the terminating 0)
    static constexpr Op op = O;
    u32 length;
    u32 pad;
};
struct CmdPop {
    static constexpr Op op = Op::PopDebugGroup;
    u64 pad;
};

using CmdCull = CmdMode<Op::SetCullMode>;
using CmdWinding = CmdMode<Op::SetFrontFacingWinding>;
using CmdFill = CmdMode<Op::SetTriangleFillMode>;
using CmdDepthClip = CmdMode<Op::SetDepthClipMode>;
using CmdViewports = CmdArray<Op::SetViewports>;
using CmdScissors = CmdArray<Op::SetScissorRects>;
using CmdPush = CmdLabel<Op::PushDebugGroup>;
using CmdSignpost = CmdLabel<Op::InsertDebugSignpost>;

#define BB_RL_APPLY(Cmd) \
    void Apply(id<MTLRenderCommandEncoder> enc, [[maybe_unused]] const Cmd& c, [[maybe_unused]] const void* extra)


BB_RL_APPLY(CmdPipeline) {
    [enc setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)c.state];
}
BB_RL_APPLY(CmdDepthStencil) {
    [enc setDepthStencilState:(__bridge id<MTLDepthStencilState>)c.state];
}
BB_RL_APPLY(CmdStencilReference) {
    [enc setStencilFrontReferenceValue:c.front backReferenceValue:c.back];
}
BB_RL_APPLY(CmdDepthBias) {
    [enc setDepthBias:c.constant slopeScale:c.slope_scale clamp:c.clamp];
}
BB_RL_APPLY(CmdCull) {
    [enc setCullMode:static_cast<MTLCullMode>(c.value)];
}
BB_RL_APPLY(CmdWinding) {
    [enc setFrontFacingWinding:static_cast<MTLWinding>(c.value)];
}
BB_RL_APPLY(CmdFill) {
    [enc setTriangleFillMode:static_cast<MTLTriangleFillMode>(c.value)];
}
BB_RL_APPLY(CmdDepthClip) {
    [enc setDepthClipMode:static_cast<MTLDepthClipMode>(c.value)];
}
BB_RL_APPLY(CmdBlendColor) {
    [enc setBlendColorRed:c.r green:c.g blue:c.b alpha:c.a];
}
BB_RL_APPLY(CmdViewports) {
    [enc setViewports:static_cast<const MTLViewport*>(extra) count:c.count];
}
BB_RL_APPLY(CmdScissors) {
    [enc setScissorRects:static_cast<const MTLScissorRect*>(extra) count:c.count];
}
BB_RL_APPLY(CmdBuffer) {
    id<MTLBuffer> buffer = (__bridge id<MTLBuffer>)c.buffer;
    if (c.stage == Stage::Vertex) {
        [enc setVertexBuffer:buffer offset:c.offset atIndex:c.index];
    } else {
        [enc setFragmentBuffer:buffer offset:c.offset atIndex:c.index];
    }
}
BB_RL_APPLY(CmdBufferOffset) {
    if (c.stage == Stage::Vertex) {
        [enc setVertexBufferOffset:c.offset atIndex:c.index];
    } else {
        [enc setFragmentBufferOffset:c.offset atIndex:c.index];
    }
}
BB_RL_APPLY(CmdVertexBuffer) {
    [enc setVertexBuffer:(__bridge id<MTLBuffer>)c.buffer
                  offset:c.offset
         attributeStride:c.stride
                 atIndex:c.index];
}
BB_RL_APPLY(CmdBytes) {
    if (c.stage == Stage::Vertex) {
        [enc setVertexBytes:extra length:c.length atIndex:c.index];
    } else {
        [enc setFragmentBytes:extra length:c.length atIndex:c.index];
    }
}
BB_RL_APPLY(CmdTexture) {
    id<MTLTexture> texture = (__bridge id<MTLTexture>)c.texture;
    if (c.stage == Stage::Vertex) {
        [enc setVertexTexture:texture atIndex:c.index];
    } else {
        [enc setFragmentTexture:texture atIndex:c.index];
    }
}
BB_RL_APPLY(CmdSampler) {
    id<MTLSamplerState> sampler = (__bridge id<MTLSamplerState>)c.sampler;
    if (c.stage == Stage::Vertex) {
        [enc setVertexSamplerState:sampler atIndex:c.index];
    } else {
        [enc setFragmentSamplerState:sampler atIndex:c.index];
    }
}
BB_RL_APPLY(CmdTessFactor) {
    [enc setTessellationFactorBuffer:(__bridge id<MTLBuffer>)c.buffer
                              offset:c.offset
                      instanceStride:c.instance_stride];
}
BB_RL_APPLY(CmdDraw) {
    [enc drawPrimitives:static_cast<MTLPrimitiveType>(c.type)
            vertexStart:c.vertex_start
            vertexCount:c.vertex_count
          instanceCount:c.instance_count
           baseInstance:c.base_instance];
}
BB_RL_APPLY(CmdDrawIndexed) {
    [enc drawIndexedPrimitives:static_cast<MTLPrimitiveType>(c.type)
                    indexCount:c.index_count
                     indexType:static_cast<MTLIndexType>(c.index_type)
                   indexBuffer:(__bridge id<MTLBuffer>)c.index_buffer
             indexBufferOffset:c.index_offset
                 instanceCount:c.instance_count
                    baseVertex:static_cast<NSInteger>(c.base_vertex)
                  baseInstance:c.base_instance];
}
BB_RL_APPLY(CmdDrawIndirect) {
    [enc drawPrimitives:static_cast<MTLPrimitiveType>(c.type)
          indirectBuffer:(__bridge id<MTLBuffer>)c.indirect_buffer
    indirectBufferOffset:c.indirect_offset];
}
BB_RL_APPLY(CmdDrawIndexedIndirect) {
    [enc drawIndexedPrimitives:static_cast<MTLPrimitiveType>(c.type)
                     indexType:static_cast<MTLIndexType>(c.index_type)
                   indexBuffer:(__bridge id<MTLBuffer>)c.index_buffer
             indexBufferOffset:c.index_offset
                indirectBuffer:(__bridge id<MTLBuffer>)c.indirect_buffer
          indirectBufferOffset:c.indirect_offset];
}
BB_RL_APPLY(CmdPatches) {
    [enc drawPatches:c.control_points
              patchStart:c.patch_start
              patchCount:c.patch_count
        patchIndexBuffer:nil
  patchIndexBufferOffset:0
           instanceCount:c.instance_count
            baseInstance:c.base_instance];
}
BB_RL_APPLY(CmdBarrier) {
    [enc memoryBarrierWithScope:MTLBarrierScopeBuffers | MTLBarrierScopeTextures
                    afterStages:MTLRenderStageVertex | MTLRenderStageFragment
                   beforeStages:MTLRenderStageVertex | MTLRenderStageFragment];
}
BB_RL_APPLY(CmdVertexBarrier) {
    [enc memoryBarrierWithScope:MTLBarrierScopeBuffers afterStages:MTLRenderStageVertex
        beforeStages:MTLRenderStageVertex|MTLRenderStageFragment];
}
BB_RL_APPLY(CmdPush) {
    [enc pushDebugGroup:[NSString stringWithUTF8String:static_cast<const char*>(extra)]];
}
BB_RL_APPLY(CmdPop) {
    [enc popDebugGroup];
}
BB_RL_APPLY(CmdSignpost) {
    [enc insertDebugSignpost:[NSString stringWithUTF8String:static_cast<const char*>(extra)]];
}

#undef BB_RL_APPLY

constexpr u32 kMaxBuffers = 31;
constexpr u32 kMaxTextures = 128;
constexpr u32 kMaxSamplers = 16;
constexpr u32 kMaxViewports = 16;
constexpr u64 kNoOffset = ~u64{0};      ///< "nothing set" in a buffer slot
constexpr u64 kNoStride = ~u64{0};      ///< bound with setBuffer (no stride given)
void* const kUnset = reinterpret_cast<void*>(~uintptr_t{0}); ///< "nothing set" (nil is a value)

constexpr u64 Pad8(u64 n) {
    return (n + 7) & ~u64{7};
}

/// The last value of one piece of state; Hit() records `v` and says whether it was the same.
template <class T>
struct Last {
    T value{};
    bool valid = false;

    bool Hit(const T& v) {
        if (valid && value == v) {
            return true;
        }
        value = v;
        valid = true;
        return false;
    }
    void Reset() {
        valid = false;
    }
};

struct StencilRefs {
    u32 front, back;
    bool operator==(const StencilRefs&) const = default;
};
struct DepthBiasValues {
    float constant, slope_scale, clamp;
    bool operator==(const DepthBiasValues&) const = default;
};
struct BlendColorValues {
    float r, g, b, a;
    bool operator==(const BlendColorValues&) const = default;
};

struct BufferSlot {
    void* buffer = nullptr;
    u64 offset = kNoOffset;
    u64 stride = kNoStride;
};

struct StageState {
    std::array<BufferSlot, kMaxBuffers> buffers;
    std::array<void*, kMaxTextures> textures;
    std::array<void*, kMaxSamplers> samplers;

    void Reset() {
        buffers.fill(BufferSlot{});
        textures.fill(kUnset);
        samplers.fill(kUnset);
    }
};

} // namespace

namespace PassContinuation {
bool Enabled() {
    static const bool enabled = [] { const char* v = std::getenv("BB_MTL_CONTINUE_CLEARS"); return !v || v[0] != '0'; }();
    return enabled;
}
static id<MTLTexture> TextureRoot(void* native) {
    id<MTLTexture> texture = (__bridge id<MTLTexture>)native;
    for (unsigned i = 0; texture && i < 8; ++i) {
        if (texture.heap || texture.buffer) return nil;
        if (!texture.parentTexture) return texture;
        texture = texture.parentTexture;
    }
    return nil;
}
static bool AttachmentRoots(const VideoCore::RenderState& state, std::array<void*, 9>& roots, unsigned& count) {
    count = 0;
    const auto add = [&](u64 view) {
        if (!view) return true;
        auto texture = TextureRoot(reinterpret_cast<void*>(view));
        if (!texture || texture.storageMode != MTLStorageModePrivate) return false;
        roots[count++] = (__bridge void*)texture;
        return true;
    };
    for (const auto& color : state.color_attachments) if (!add(color.image_view)) return false;
    return add(state.depth_stencil_attachment.image_view) && count != 0;
}
static bool Excludes(const std::array<void*, 9>& roots, unsigned count, void* native) {
    if (!native) return true;
    auto root = TextureRoot(native);
    if (!root) return false;
    for (unsigned i = 0; i < count; ++i) if (roots[i] == (__bridge void*)root) return false;
    return true;
}
bool TexturesExcludeAttachments(const VideoCore::RenderState& state, std::span<void* const> textures) {
    std::array<void*, 9> roots{}; unsigned count = 0;
    if (!AttachmentRoots(state, roots, count)) return false;
    for (void* texture : textures) if (!Excludes(roots, count, texture)) return false;
    return true;
}
} // namespace PassContinuation

struct RenderList::Impl {
    enum class Mode : u8 { None, Direct, Recording };

    Mode mode = Mode::None;
    id<MTLRenderCommandEncoder> encoder = nil;
    std::vector<u8> arena;
    std::unique_ptr<FragmentAccessGuard> fragment_guard;
    bool has_barrier = false;
    bool consumed_clear_safe = false;
    bool depth_layout_safe = false;
    std::array<void*, 9> clear_roots{};
    unsigned clear_root_count = 0;
    const bool coalesce_barriers = [] {
        const char* flag = std::getenv("BB_MTL_COALESCE_BARRIERS");
        return flag && flag[0] == '1';
    }();
    bool barrier_since_draw = false;
    // Non-owning proof of the encoder that executed the last recording. A replay
    // may become direct without ending that physical pass; a fresh pass must not
    // inherit its coverage, even when no draw separated the two API modes.
    void* last_replayed_encoder = nullptr;
    RenderList::BarrierCounts barriers{};

    // The redundancy filter (reset by the Begin* calls).
    void* pipeline = kUnset;
    void* depth_stencil = kUnset;
    Last<StencilRefs> stencil_refs;
    Last<DepthBiasValues> depth_bias;
    Last<u32> cull_mode, winding, fill_mode, depth_clip;
    Last<BlendColorValues> blend_color;
    u32 num_viewports = 0, num_scissors = 0;
    bool viewports_valid = false, scissors_valid = false;
    std::array<RenderList::Viewport, kMaxViewports> viewports;
    std::array<RenderList::Scissor, kMaxViewports> scissors;
    std::array<StageState, 2> stages;

    // Diagnostics (BB_FRAME_STATS).
    const bool stats_enabled = std::getenv("BB_FRAME_STATS") != nullptr;
    u64 total_calls = 0, dropped_calls = 0, arena_high_water = 0;
    u64 window_calls = 0, window_dropped = 0;
    std::chrono::steady_clock::time_point last_print = std::chrono::steady_clock::now();

    Impl() {
        const char* native=std::getenv("BB_CAPTURE_NATIVE"),*audit=std::getenv("BB_CAPTURE_FRAGMENT_GUARD");
        const char* updates=std::getenv("BB_MTL_RENDER_UPDATES");
        if((native && native[0]=='1' && audit && audit[0]=='1') || (updates && updates[0]=='1'))
            fragment_guard=std::make_unique<FragmentAccessGuard>();
        ResetFilter();
    }

    void ResetFilter() {
        if(fragment_guard)fragment_guard->Reset();
        pipeline = kUnset;
        depth_stencil = kUnset;
        stencil_refs.Reset();
        depth_bias.Reset();
        cull_mode.Reset();
        winding.Reset();
        fill_mode.Reset();
        depth_clip.Reset();
        blend_color.Reset();
        viewports_valid = scissors_valid = false;
        for (StageState& stage : stages) {
            stage.Reset();
        }
    }

    // ---- Counters ----

    void Count(bool dropped) {
        ++total_calls;
        dropped_calls += dropped;
        if (stats_enabled && (total_calls & 4095) == 0) {
            PrintStats();
        }
    }

    void PrintStats() {
        const auto now = std::chrono::steady_clock::now();
        if (now - last_print < std::chrono::seconds(5)) {
            return;
        }
        last_print = now;
        const u64 calls = total_calls - window_calls;
        const u64 dropped = dropped_calls - window_dropped;
        window_calls = total_calls;
        window_dropped = dropped_calls;
        std::printf("Metal render list: %llu calls, %llu dropped as redundant (%.1f%%), arena "
                    "high-water %llu KiB\n",
                    static_cast<unsigned long long>(calls),
                    static_cast<unsigned long long>(dropped),
                    calls ? 100.0 * double(dropped) / double(calls) : 0.0,
                    static_cast<unsigned long long>(arena_high_water >> 10));
    }

    // Bindings, fixed-function setters and labels perform no resource accesses. Retain the
    // first barrier since a draw and every barrier after any draw, including indirect/patch
    // draws. This filter applies only within ONE physical render encoder. Scheduler fences,
    // hoisted compute barriers, pass load/store actions and recorded HasBarrier are unchanged.
    template <class Cmd>
    void Execute(id<MTLRenderCommandEncoder> enc, const Cmd& c, const void* extra) {
        if constexpr (Cmd::op == Op::MemoryBarrier) {
            ++barriers.requested;
            if (coalesce_barriers && barrier_since_draw) {
                ++barriers.dropped;
                return;
            }
            barrier_since_draw = true;
        } else if constexpr (Cmd::op == Op::DrawPrimitives ||
                             Cmd::op == Op::DrawIndexedPrimitives ||
                             Cmd::op == Op::DrawPrimitivesIndirect ||
                             Cmd::op == Op::DrawIndexedPrimitivesIndirect ||
                             Cmd::op == Op::DrawPatches) {
            barrier_since_draw = false;
        }
        Apply(enc, c, extra);
    }

    // ---- Direct or recorded ----

    /// Records `c` and its trailing `extra_size` bytes, or applies it to the encoder at once.
    template <class Cmd>
    void Issue(const Cmd& c, const void* extra = nullptr, u32 extra_size = 0) {
        if constexpr(Cmd::op==Op::DrawPrimitives || Cmd::op==Op::DrawIndexedPrimitives ||
            Cmd::op==Op::DrawPrimitivesIndirect || Cmd::op==Op::DrawIndexedPrimitivesIndirect ||
            Cmd::op==Op::DrawPatches) {
            if(fragment_guard)fragment_guard->Draw();
        }
        if (mode == Mode::Direct) {
            Execute(encoder, c, extra);
            return;
        }
        assert(mode == Mode::Recording);
        last_replayed_encoder = nullptr; // new unpublished work cannot inherit a replay proof
        const u64 size = Pad8(sizeof(Header) + sizeof(Cmd) + extra_size);
        const size_t at = arena.size();
        arena.resize(at + size); // (zero fills the padding; capacity is kept across passes)
        const Header header{Cmd::op, static_cast<u32>(size)};
        std::memcpy(&arena[at], &header, sizeof(header));
        std::memcpy(&arena[at + sizeof(Header)], &c, sizeof(Cmd));
        if (extra_size) {
            std::memcpy(&arena[at + sizeof(Header) + sizeof(Cmd)], extra, extra_size);
        }
        arena_high_water = std::max<u64>(arena_high_water, arena.size());
    }

    /// Filtered: `hit` says the state is already set.
    template <class Cmd>
    void IssueUnless(bool hit, const Cmd& c, const void* extra = nullptr, u32 extra_size = 0) {
        Count(hit);
        if (!hit) {
            Issue(c, extra, extra_size);
        }
    }

    // ---- Replay ----

    template <class Cmd>
    void Run(id<MTLRenderCommandEncoder> enc, const u8* record) {
        Cmd c;
        std::memcpy(&c, record + sizeof(Header), sizeof(Cmd));
        Execute(enc, c, record + sizeof(Header) + sizeof(Cmd));
    }

    void Replay(id<MTLRenderCommandEncoder> enc) {
        barrier_since_draw = false;
        barriers = {};
        const u8* data = arena.data();
        for (size_t at = 0; at < arena.size();) {
            Header header;
            std::memcpy(&header, data + at, sizeof(header));
            const u8* record = data + at;
            switch (header.op) {
#define BB_RL_CASE(O, Cmd) \
    case Op::O:            \
        Run<Cmd>(enc, record); \
        break;
                BB_RL_CASE(SetRenderPipelineState, CmdPipeline)
                BB_RL_CASE(SetDepthStencilState, CmdDepthStencil)
                BB_RL_CASE(SetStencilReference, CmdStencilReference)
                BB_RL_CASE(SetDepthBias, CmdDepthBias)
                BB_RL_CASE(SetCullMode, CmdCull)
                BB_RL_CASE(SetFrontFacingWinding, CmdWinding)
                BB_RL_CASE(SetTriangleFillMode, CmdFill)
                BB_RL_CASE(SetDepthClipMode, CmdDepthClip)
                BB_RL_CASE(SetBlendColor, CmdBlendColor)
                BB_RL_CASE(SetViewports, CmdViewports)
                BB_RL_CASE(SetScissorRects, CmdScissors)
                BB_RL_CASE(SetBuffer, CmdBuffer)
                BB_RL_CASE(SetBufferOffset, CmdBufferOffset)
                BB_RL_CASE(SetVertexBufferWithStride, CmdVertexBuffer)
                BB_RL_CASE(SetBytes, CmdBytes)
                BB_RL_CASE(SetTexture, CmdTexture)
                BB_RL_CASE(SetSampler, CmdSampler)
                BB_RL_CASE(SetTessellationFactorBuffer, CmdTessFactor)
                BB_RL_CASE(DrawPrimitives, CmdDraw)
                BB_RL_CASE(DrawIndexedPrimitives, CmdDrawIndexed)
                BB_RL_CASE(DrawPrimitivesIndirect, CmdDrawIndirect)
                BB_RL_CASE(DrawIndexedPrimitivesIndirect, CmdDrawIndexedIndirect)
                BB_RL_CASE(DrawPatches, CmdPatches)
                BB_RL_CASE(MemoryBarrier, CmdBarrier)
                BB_RL_CASE(VertexBarrier, CmdVertexBarrier)
                BB_RL_CASE(PushDebugGroup, CmdPush)
                BB_RL_CASE(PopDebugGroup, CmdPop)
                BB_RL_CASE(InsertDebugSignpost, CmdSignpost)
#undef BB_RL_CASE
            }
            at += header.size;
        }
        arena.clear();
        last_replayed_encoder = (__bridge void*)enc;
    }
};

RenderList::RenderList() : impl{std::make_unique<Impl>()} {}

RenderList::~RenderList() = default;

void RenderList::BeginDirect(void* encoder) {
    impl->mode = Impl::Mode::Direct;
    impl->encoder = (__bridge id<MTLRenderCommandEncoder>)encoder;
    impl->arena.clear();
    impl->has_barrier = false;
    impl->consumed_clear_safe = false;
    impl->barrier_since_draw = false;
    impl->last_replayed_encoder = nullptr;
    impl->barriers = {};
    impl->ResetFilter();
}

void RenderList::BeginRecording() {
    impl->mode = Impl::Mode::Recording;
    impl->encoder = nil;
    impl->arena.clear();
    impl->has_barrier = false;
    impl->consumed_clear_safe = false;
    impl->barrier_since_draw = false;
    impl->last_replayed_encoder = nullptr;
    impl->barriers = {};
    impl->ResetFilter();
}
void RenderList::ContinueDirect(void* encoder) {
    assert(impl->mode==Impl::Mode::Recording && impl->arena.empty());
    const bool same_encoder_coverage = encoder && impl->last_replayed_encoder==encoder &&
                                       impl->barrier_since_draw;
    auto guard=std::move(impl->fragment_guard);
    const bool safe = impl->consumed_clear_safe;
    BeginDirect(encoder);
    // Replay already applied exactly this global Buffers|Textures V|F barrier.
    // Bindings perform no GPU accesses; every draw still clears this bit in
    // Execute(). Do not retain counts: Scheduler captured the replay segment.
    impl->barrier_since_draw = same_encoder_coverage;
    impl->consumed_clear_safe = safe;
    impl->fragment_guard=std::move(guard);
}

void RenderList::Replay(void* encoder) {
    assert(impl->mode == Impl::Mode::Recording);
    impl->Replay((__bridge id<MTLRenderCommandEncoder>)encoder);
}

void RenderList::TakeRecording(RenderList& into) {
    assert(impl->mode == Impl::Mode::Recording);
    assert(into.impl->mode == Impl::Mode::None && into.impl->arena.empty());
    into.impl->mode = Impl::Mode::Recording;
    into.impl->last_replayed_encoder = impl->last_replayed_encoder = nullptr;
    into.impl->encoder = nil;
    into.impl->arena.swap(impl->arena); // (this list gets the other's capacity back)
    into.impl->has_barrier = impl->has_barrier;
    if(into.impl->fragment_guard && impl->fragment_guard)
        *into.impl->fragment_guard=*impl->fragment_guard;
    impl->arena.clear();
}

void RenderList::End() {
    assert(impl->arena.empty());
    impl->mode = Impl::Mode::None;
    impl->encoder = nil;
    impl->arena.clear();
    impl->last_replayed_encoder = nullptr;
}

bool RenderList::Recording() const noexcept {
    return impl->mode == Impl::Mode::Recording;
}

bool RenderList::Empty() const noexcept {
    return impl->arena.empty();
}

bool RenderList::HasBarrier() const noexcept {
    return impl->has_barrier;
}
void RenderList::TrackConsumedClear(const VideoCore::RenderState& state, bool bound_only) {
    const bool clears = PassContinuation::HasClears(state);
    impl->consumed_clear_safe = PassContinuation::Enabled() && bound_only && clears;
    const auto& ds = state.depth_stencil_attachment;
    impl->depth_layout_safe = PassContinuation::DepthLayoutEnabled() && bound_only && !clears &&
        ds.image_view && ds.has_depth && ds.has_stencil &&
        PassContinuation::DepthLayoutMayPromote(ds.image_layout);
    if ((impl->consumed_clear_safe || impl->depth_layout_safe) &&
        !PassContinuation::AttachmentRoots(state, impl->clear_roots, impl->clear_root_count)) {
        impl->consumed_clear_safe = impl->depth_layout_safe = false;
    }
}
void RenderList::InvalidateConsumedClear() noexcept {
    impl->consumed_clear_safe = impl->depth_layout_safe = false;
}
bool RenderList::CanContinueDepthLayout() const noexcept { return impl->depth_layout_safe; }
bool RenderList::CanContinueConsumedClear() const noexcept { return impl->consumed_clear_safe; }
bool RenderList::FragmentGuardEnabled() const noexcept {return bool(impl->fragment_guard);}
void RenderList::FragmentBuffer(void* native,u64 offset,u64 size) {
    if(impl->fragment_guard)impl->fragment_guard->Buffer(reinterpret_cast<uintptr_t>(native),offset,size);
}
void RenderList::FragmentUnknown(unsigned reason) {if(impl->fragment_guard)impl->fragment_guard->Unknown(reason);}
void RenderList::FragmentReady() {if(impl->fragment_guard)impl->fragment_guard->Ready();}
FragmentAccessGuard::Snapshot RenderList::FragmentSnapshot(void* native,u64 offset,u64 size) const {
    return impl->fragment_guard ? impl->fragment_guard->Inspect(reinterpret_cast<uintptr_t>(native),offset,size)
        : FragmentAccessGuard::Snapshot{0,0,0,FragmentAccessGuard::MissingDraw,false};
}

// ---- Fixed-function state ----

void RenderList::SetRenderPipelineState(void* pipeline_state) {
    const bool hit = impl->pipeline == pipeline_state;
    impl->pipeline = pipeline_state;
    impl->IssueUnless(hit, CmdPipeline{pipeline_state});
}

void RenderList::SetDepthStencilState(void* depth_stencil_state) {
    const bool hit = impl->depth_stencil == depth_stencil_state;
    impl->depth_stencil = depth_stencil_state;
    impl->IssueUnless(hit, CmdDepthStencil{depth_stencil_state});
}

void RenderList::SetStencilReference(u32 front, u32 back) {
    const StencilRefs refs{front, back};
    impl->IssueUnless(impl->stencil_refs.Hit(refs), CmdStencilReference{front, back});
}

void RenderList::SetDepthBias(float constant, float slope_scale, float clamp) {
    const DepthBiasValues values{constant, slope_scale, clamp};
    impl->IssueUnless(impl->depth_bias.Hit(values), CmdDepthBias{constant, slope_scale, clamp, 0});
}

void RenderList::SetCullMode(u32 mode) {
    impl->IssueUnless(impl->cull_mode.Hit(mode), CmdCull{mode, 0});
}

void RenderList::SetFrontFacingWinding(u32 winding) {
    impl->IssueUnless(impl->winding.Hit(winding), CmdWinding{winding, 0});
}

void RenderList::SetTriangleFillMode(u32 mode) {
    impl->IssueUnless(impl->fill_mode.Hit(mode), CmdFill{mode, 0});
}

void RenderList::SetDepthClipMode(u32 mode) {
    impl->IssueUnless(impl->depth_clip.Hit(mode), CmdDepthClip{mode, 0});
}

void RenderList::SetBlendColor(float red, float green, float blue, float alpha) {
    const BlendColorValues values{red, green, blue, alpha};
    impl->IssueUnless(impl->blend_color.Hit(values), CmdBlendColor{red, green, blue, alpha});
}

void RenderList::SetViewports(const Viewport* viewports, u32 count) {
    const u32 bytes = count * sizeof(Viewport);
    bool hit = false;
    if (count <= kMaxViewports) {
        hit = impl->viewports_valid && impl->num_viewports == count &&
              std::memcmp(impl->viewports.data(), viewports, bytes) == 0;
        if (!hit) {
            std::memcpy(impl->viewports.data(), viewports, bytes);
            impl->num_viewports = count;
            impl->viewports_valid = true;
        }
    } else {
        impl->viewports_valid = false;
    }
    impl->IssueUnless(hit, CmdViewports{count, 0}, viewports, bytes);
}

void RenderList::SetScissorRects(const Scissor* scissors, u32 count) {
    const u32 bytes = count * sizeof(Scissor);
    bool hit = false;
    if (count <= kMaxViewports) {
        hit = impl->scissors_valid && impl->num_scissors == count &&
              std::memcmp(impl->scissors.data(), scissors, bytes) == 0;
        if (!hit) {
            std::memcpy(impl->scissors.data(), scissors, bytes);
            impl->num_scissors = count;
            impl->scissors_valid = true;
        }
    } else {
        impl->scissors_valid = false;
    }
    impl->IssueUnless(hit, CmdScissors{count, 0}, scissors, bytes);
}

// ---- Resources ----

void RenderList::SetBuffer(Stage stage, void* buffer, u64 offset, u32 index) {
    if (index >= kMaxBuffers) {
        impl->Count(false);
        impl->Issue(CmdBuffer{buffer, offset, index, stage});
        return;
    }
    BufferSlot& slot = impl->stages[static_cast<size_t>(stage)].buffers[index];
    if (slot.offset != kNoOffset && slot.buffer == buffer && slot.stride == kNoStride) {
        const bool same = slot.offset == offset;
        slot.offset = offset;
        impl->Count(same);
        if (!same) {
            // Only the offset changed: the cheaper call.
            impl->Issue(CmdBufferOffset{offset, index, stage});
        }
        return;
    }
    slot = BufferSlot{buffer, offset, kNoStride};
    impl->Count(false);
    impl->Issue(CmdBuffer{buffer, offset, index, stage});
}

void RenderList::SetVertexBufferWithStride(void* buffer, u64 offset, u64 stride, u32 index) {
    if (index >= kMaxBuffers) {
        impl->Count(false);
        impl->Issue(CmdVertexBuffer{buffer, offset, stride, index, 0});
        return;
    }
    BufferSlot& slot = impl->stages[static_cast<size_t>(Stage::Vertex)].buffers[index];
    const bool hit = slot.offset != kNoOffset && slot.buffer == buffer && slot.offset == offset &&
                     slot.stride == stride;
    slot = BufferSlot{buffer, offset, stride};
    impl->IssueUnless(hit, CmdVertexBuffer{buffer, offset, stride, index, 0});
}

void RenderList::SetBytes(Stage stage, const void* data, u32 length, u32 index) {
    assert(length <= 4096);
    if (index < kMaxBuffers) {
        // The slot no longer holds what the table says.
        impl->stages[static_cast<size_t>(stage)].buffers[index] = BufferSlot{};
    }
    impl->Count(false);
    impl->Issue(CmdBytes{length, index, stage}, data, length);
}

void RenderList::SetTexture(Stage stage, void* texture, u32 index) {
    bool hit = false;
    if (index < kMaxTextures) {
        void*& slot = impl->stages[static_cast<size_t>(stage)].textures[index];
        hit = slot == texture;
        slot = texture;
    }
    // The existing binding filter resets for every pass; attachment roots remain
    // exact across both continuations. An unchanged binding already passed this
    // root check. Keep out-of-range and changed bindings conservative, and never
    // restore validity after feedback even if that texture is later unbound.
    if ((!hit || !PassContinuation::DepthLayoutEnabled()) &&
        (impl->consumed_clear_safe || impl->depth_layout_safe) &&
        !PassContinuation::Excludes(impl->clear_roots, impl->clear_root_count, texture))
        impl->consumed_clear_safe = impl->depth_layout_safe = false;
    impl->IssueUnless(hit, CmdTexture{texture, index, stage});
}

void RenderList::SetSampler(Stage stage, void* sampler, u32 index) {
    bool hit = false;
    if (index < kMaxSamplers) {
        void*& slot = impl->stages[static_cast<size_t>(stage)].samplers[index];
        hit = slot == sampler;
        slot = sampler;
    }
    impl->IssueUnless(hit, CmdSampler{sampler, index, stage});
}

void RenderList::SetTessellationFactorBuffer(void* buffer, u64 offset, u64 instance_stride) {
    impl->Count(false);
    impl->Issue(CmdTessFactor{buffer, offset, instance_stride});
}

// ---- Draws ----

void RenderList::DrawPrimitives(u32 type, u64 vertex_start, u64 vertex_count, u64 instance_count,
                                u64 base_instance) {
    impl->Count(false);
    impl->Issue(CmdDraw{vertex_start, vertex_count, instance_count, base_instance, type, 0});
}
void RenderList::VertexBufferUpdate(void* pipeline,void* buffer,u64 offset,const void* data,u32 bytes) {
    assert(pipeline && buffer && data && bytes && bytes<=256 && !(bytes%4));
    impl->has_barrier=true; // held/replayed encoders must wait for incoming producers too
    impl->Issue(CmdVertexBarrier{0});
    SetRenderPipelineState(pipeline);
    SetBuffer(Stage::Vertex,buffer,offset,0);
    SetBytes(Stage::Vertex,data,bytes,1);
    FragmentReady(); // this host pipeline never rasterizes or executes a fragment function
    DrawPrimitives(MTLPrimitiveTypePoint,0,bytes/4,1,0);
    impl->Issue(CmdVertexBarrier{0}); // not subject to generic-barrier coalescing
}

void RenderList::DrawIndexedPrimitives(u32 type, u64 index_count, u32 index_type,
                                       void* index_buffer, u64 index_offset, u64 instance_count,
                                       s64 base_vertex, u64 base_instance) {
    impl->Count(false);
    impl->Issue(CmdDrawIndexed{index_buffer, index_count, index_offset, instance_count,
                               base_vertex, base_instance, type, index_type});
}

void RenderList::DrawPrimitivesIndirect(u32 type, void* indirect_buffer, u64 indirect_offset) {
    impl->Count(false);
    impl->Issue(CmdDrawIndirect{indirect_buffer, indirect_offset, type, 0});
}

void RenderList::DrawIndexedPrimitivesIndirect(u32 type, u32 index_type, void* index_buffer,
                                               u64 index_offset, void* indirect_buffer,
                                               u64 indirect_offset) {
    impl->Count(false);
    impl->Issue(CmdDrawIndexedIndirect{index_buffer, index_offset, indirect_buffer,
                                       indirect_offset, type, index_type});
}

void RenderList::DrawPatches(u32 control_points, u64 patch_start, u64 patch_count,
                             u64 instance_count, u64 base_instance) {
    impl->Count(false);
    impl->Issue(CmdPatches{patch_start, patch_count, instance_count, base_instance, control_points,
                           0});
}

// ---- Ordering and labels ----

RenderList::BarrierCounts RenderList::BarrierSnapshot() const noexcept {
    return impl->barriers;
}

void RenderList::MemoryBarrier() {
    impl->has_barrier = true;
    impl->Count(false);
    impl->Issue(CmdBarrier{0});
}

void RenderList::PushDebugGroup(const char* name) {
    const u32 length = static_cast<u32>(std::strlen(name)) + 1;
    impl->Count(false);
    impl->Issue(CmdPush{length, 0}, name, length);
}

void RenderList::PopDebugGroup() {
    impl->Count(false);
    impl->Issue(CmdPop{0});
}

void RenderList::InsertDebugSignpost(const char* name) {
    const u32 length = static_cast<u32>(std::strlen(name)) + 1;
    impl->Count(false);
    impl->Issue(CmdSignpost{length, 0}, name, length);
}

} // namespace Metal
