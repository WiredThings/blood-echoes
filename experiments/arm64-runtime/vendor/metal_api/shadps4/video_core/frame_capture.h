// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: frame analyzer. Creating the file named by BB_CAPTURE_TRIGGER records the next full
// frame: every render pass (targets, depth, draw count, shaders, sampled textures) and compute
// dispatch, in order, plus the buffer that was presented. Input for placing a temporal
// upscaler (scene color, depth, motion vectors, UI) in Bloodborne's frame.

#pragma once

#include <atomic>
#include <cstdio>
#include <string>
#include <vector>

#include "common/types.h"

// bbport (plan P4.1 R6): moved from renderer_vulkan/vk_frame_capture.h (it only looks at what the
// rasterizer core decides); that header keeps the Vulkan::FrameCapture name.

namespace VideoCore {
struct ImageInfo;

/// Frames handed to video out (the frame stats count), for diagnostics. Vulkan::g_bb_flip_count.
extern std::atomic<u64> g_bb_flip_count;

class FrameCapture {
public:
    /// VideoOut thread, per flip: frame boundary, presented buffer, trigger file check.
    static void OnFlip(VAddr presented_address);

    /// VideoOut: a display buffer the game registered. A pass drawing into one starts a frame
    /// in the GPU command stream (the flip itself runs a frame behind).
    static void AddDisplayBuffer(VAddr address);
    static bool IsDisplayBuffer(VAddr address);

    /// GPU thread: whether passes are being looked at (armed or recording; cheap).
    static bool Active() {
        return state.load(std::memory_order_relaxed) != Idle;
    }
    /// GPU thread, at every draw/dispatch: starts or ends a recording at frame boundaries.
    static void Poll();
    // Opt-in, bounded command/attachment metadata. No texture/buffer snapshots.
    static bool NativeMode();
    static bool NativeRecording();
    // Private metadata capture: retain normal texture-set memoization while observing bindings.
    // No texture contents, cache invalidation or GPU allocation. Explicit opt-in only.
    static bool TextureBindingsEnabled();
    // Canonical, explicitly admitted private directory; empty in ordinary play.
    static const std::string& TextureDiagnosticDirectory();
    static void FrameBoundary(u64 frame);
    /// A single renderer owns native frame recording. Installed before recording threads run,
    /// cleared after they stop. No callback allocation; only invoked for armed/recording frames.
    static void SetNativeBoundaryDrain(void* owner, void (*drain)(void*));
    static void ClearNativeBoundaryDrain(void* owner);
    static void Native(std::string line);
    static void NativeLabel(const char* text, u64 queue=0);
    // Requested-command provenance only, never authorizes a dependency relaxation.
    // Stage: Shader::SwStage values; 6 = index fetch, 7 = runtime/unknown.
    static void NativeCommandWrite(VAddr address, const void* data, u64 size,
                                   u64 resource, u64 native, u64 offset, u64 generation);
    enum class WriteOrigin { GraphicsWriteData, ComputeWriteData, ConstantRam };
    // Graphics origins are observed in ordered-task execution, compute/CE origins
    // during their decode paths. None proves GPU completion. The graphics input may
    // already be a copied draw-pipe packet, not the original guest packet address.
    // No payload access. Captures only the same 192-byte diagnostic write class.
    static void NativeWriteOrigin(VAddr address, u64 size, WriteOrigin origin,
                                  u32 constant_offset = 0, const void* packet_address = nullptr);
    // Identify original/copied 208-byte ordered packets without changing queue layout.
    // Queue position is monotonic; a ring address alone can be reused after wrapping.
    static void NativeTaskPacket(bool copied, u64 queue, u64 position,
                                 const void* source, const void* payload, u32 size);
    // Resolve a decoder packet within an existing submission snapshot, without
    // reading either buffer. Missing/invalid ranges remain explicit observations.
    // Publication-time identity of an existing command-buffer copy. Metadata only.
    static void NativeCommandSnapshot(u64 snapshot, u64 bytes, u64 guest);
    static void NativeSubmittedPacket(bool copied, u64 packet, u32 bytes, u64 begin,
                                      u64 range_bytes, u64 guest_begin, u64 submission);
    static void NativeBufferUse(u64 resource, u64 native, u64 offset, u64 size,
                                VAddr address, u32 stage, u64 shader, bool write,
                                const char* source, u64 caller_stage = 0);
    static void NativeCpuAccess(VAddr address, u64 size, bool write);
    static void NativeSrtWalk(bool covered, u32 encoded_reads);
    static u64 NativeCaptureSequence();


    static void BeginPass(const VideoCore::ImageInfo* const* colors, u32 num_colors,
                          const VideoCore::ImageInfo* depth);
    static void Draw(u64 vs_hash, u64 ps_hash, u32 num_indices, u32 num_instances);
    static void Dispatch(u64 cs_hash, u32 x, u32 y, u32 z);
    static void Sampled(const VideoCore::ImageInfo& info, bool storage);
    /// Contents of a bound buffer (first 1 KiB), kept for small passes and a pass's first draw.
    static void Buffer(u64 stage_hash, u32 slot, VAddr address, const void* data, u64 size);
    static void Note(const char* text);

private:
    enum : u32 { Idle, Armed, Recording };
    static inline std::atomic<u32> state{Idle};
    static inline std::atomic<u64> flips{0};
    static inline std::atomic<VAddr> last_presented{0};
};

} // namespace VideoCore
