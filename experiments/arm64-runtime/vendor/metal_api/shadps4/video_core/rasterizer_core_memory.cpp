// SPDX-License-Identifier: GPL-2.0-or-later
// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project

#include <chrono>
#include <dlfcn.h>
#include <map>
#include <unordered_set>
#include <xxhash.h>
#include "video_core/ui_composition.h"
#include "bbport_timeline.h"
#include "bbport_sections.h"
#include "game_profile.h"
#include "bbport_ce_stats.h"
#include "bbport_toggles.h"
#include "bbport_write_log.h"
#include "bbport_free_check.h"
#include "bbport_guest_memory.h"
#include "video_core/frame_capture.h"
#include "bbport_resource_lifetime.h"
#include "common/hash.h"
#include "common/debug.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/amdgpu/pm4_cmds.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/rasterizer_core.h"
#include "bbport_threads.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/texture_cache.h"

// The draw pipeline's vocabulary (vk_draw_pipe.h, vk_draw_prep.h) and the translation helpers.
using Vulkan::DrawPipe;
using Vulkan::ImageDescHash;
using Vulkan::PreparedDraw;
using Vulkan::PreparedStage;
using Vulkan::PreparedVertexInputs;
namespace LiverpoolToVK = Vulkan::LiverpoolToVK;

namespace VideoCore {

static void CaptureCommandLifetime(VAddr address,const void* data,u32 size,
                                   const Buffer* buffer,u64 offset) {
    if(size!=192)return;
    const bool enabled=BbResourceLifetime::Enabled();
    const auto span=enabled?BbResourceLifetime::Get().InspectRange({},address,size)
                           :BbResourceLifetime::RangeInspection{};
    const auto& tag=span.covering_owner;
    FrameCapture::NativeCommandWrite(address,data,size,uintptr_t(buffer),
        uintptr_t(buffer->buffer.native),offset,tag.generation);
    FrameCapture::Native(std::format("{{\"op\":\"command_allocation\",\"address\":{},\"size\":192,\"tracker_enabled\":{},\"allocation_address\":{},\"allocation_bytes\":{},\"generation\":{},\"domain\":{},\"range_live_overlaps\":{},\"heap_live_overlaps\":{},\"overlap_truncated\":{},\"history_dropped\":{}}}",
        address,enabled,tag.address,tag.size,tag.generation,unsigned(tag.domain),
        span.overlaps[0],span.overlaps[1],span.truncated,span.history_dropped));
}

bool RasterizerCore::WriteGuestMemory(VAddr address, const void* data, u32 size) {
    if (!VideoCore::GuestInPlace() || size == 0 || (address | size) % 4 != 0) {
        return false;
    }
    const auto [buffer, offset] = buffer_cache.ObtainBuffer(address, size, true);
    if (!buffer_cache.IsInPlace(address, size)) {
        return false;
    }
    if(FrameCapture::NativeRecording())CaptureCommandLifetime(address,data,size,buffer,offset);
    backend.UpdateBuffer(buffer, offset, {static_cast<const u8*>(data), size});
    return true;
}

bool RasterizerCore::WriteDataOnGpu(VAddr address, const void* data, u32 size) {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_GPU_COMMAND_WRITES");
        return !env || env[0] != '0';
    }();
    if (!enabled || !VideoCore::GuestInPlace() || size == 0 || (address | size) % 4 != 0) {
        return false;
    }
    // bbport BB_LAYER_MEMORY: a range whole in a mirror is written there too (asked first: the
    // target demotes it otherwise).
    const auto mirror = buffer_cache.CommandWriteMirror(address, size);
    const auto target = buffer_cache.CommandWriteTarget(address, size);
    if (!target) {
        return false;
    }
    if (mirror) {
        backend.UpdateBuffer(mirror->first, mirror->second, {static_cast<const u8*>(data), size});
    }
    if(FrameCapture::NativeRecording())CaptureCommandLifetime(address,data,size,target->first,target->second);
    backend.UpdateBuffer(target->first, target->second, {static_cast<const u8*>(data), size});
    if (size <= sizeof(u64) && HonestLabels()) {
        u64 value = 0;
        std::memcpy(&value, data, size);
        SignalAfterGpu([] {}, address, value);
    }
    return true;
}

bool RasterizerCore::OcclusionEvent(VAddr address, u32 pairs) {
    if (!OcclusionTranslated() || pairs == 0) {
        return false;
    }
    if (!occlusion) {
        occlusion = CreateOcclusion();
        if (!occlusion) {
            return false;
        }
    }
    occlusion->Event(address, pairs);
    return true;
}

u64 RasterizerCore::OcclusionEvents() const {
    return occlusion ? occlusion->Events() : 0;
}

bool RasterizerCore::OnOcclusionPageAccess(VAddr addr, u64 rip, bool write, bool gpu_thread) {
    return occlusion && occlusion->OnAccess(addr, rip, write, gpu_thread);
}

bool RasterizerCore::OnVramDataAccess(VAddr addr, bool assume_locks) {
    // A trap identifies a page, not the eventual instruction's exact byte span.
    if(FrameCapture::NativeRecording())FrameCapture::NativeCpuAccess(addr&~VAddr{4095},4096,false);
    if (!assume_locks) {
        DrainDrawPipe(); // as ReadMemory: the recording thread idle while the copy back runs
    }
    return buffer_cache.LayerReadTrapHit(addr, assume_locks);
}

bool RasterizerCore::WriteTimestampOnGpu(VAddr address, bool end_of_pipe) {
    // BB_GPU_TIMESTAMPS=0: written by the CPU (EOP: once the GPU is done; COPY_DATA: when decoded).
    static const bool enabled = [] {
        const char* env = std::getenv("BB_GPU_TIMESTAMPS");
        return !(env && env[0] == '0');
    }();
    if (!enabled || !VideoCore::GuestInPlace() || address % 8 != 0) {
        return false;
    }
    if (!timestamps) {
        timestamps = CreateTimestamps();
        if (!timestamps) {
            return false;
        }
    }
    return timestamps->Write(address, end_of_pipe);
}

void RasterizerCore::FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds) {
    DrainDrawPipe();
    if(FrameCapture::NativeRecording() && !is_gds)FrameCapture::NativeBufferUse(
        0,0,0,num_bytes,address,7,0,true,"dma_fill");
    ASSERT_MSG(address % 4 == 0 && num_bytes % 4 == 0,
               "FillBuffer address and size must be a multiple of 4 bytes");
    if (!is_gds) {
        texture_cache.ClearMeta(address);
        // bbport BB_GUEST_IN_PLACE: memory the GPU uses in place is filled by the GPU in stream order
        // (a fill on the CPU now would land before earlier draws read the old data). Elsewhere
        // (VRAM copies, unbound memory: shader code, descriptors this thread reads) on the CPU in
        // decode order, as before; the VRAM copy follows through write tracking.
        if (!buffer_cache.IsAnyInPlace(address, num_bytes) &&
            !buffer_cache.IsRegionGpuModified(address, num_bytes) &&
            !VideoCore::BufferCache::LayerMirrored(address, num_bytes)) {
            BbFreeCheck::Check(address, num_bytes, &value, BbFreeCheck::DmaFill);
            u32* buffer = std::bit_cast<u32*>(address);
            std::fill(buffer, buffer + (num_bytes / sizeof(u32)), value);
            BbWriteLog::Note(address, buffer, num_bytes, BbWriteLog::Dma);
            if (!VideoCore::WriteTracking()) {
                NoteAssetWrite(address, num_bytes);
            }
            return;
        }
    }
    auto [buffer, offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (is_gds) {
            return {buffer_cache.GetGdsBuffer(), address};
        }
        return buffer_cache.ObtainBuffer(address, num_bytes, true);
    }();
    if(!is_gds&&buffer_cache.PrepareImageAliasesForRawWrite(address,num_bytes,buffer,offset)==BufferCache::RawPrewriteResult::Rebind)
        std::tie(buffer,offset)=buffer_cache.ObtainBuffer(address,num_bytes,true);
    backend.FillBuffer(buffer, offset, num_bytes, value);
}

bool RasterizerCore::DmaMayWriteOnCpu(VAddr dst, u32 num_bytes) {
    // The first condition of both: false only where they record a GPU fill or copy. The model
    // of 0.3 keeps its waits (the new memory and translation model only).
    if (!VideoCore::GuestInPlace()) {
        return true;
    }
    return !buffer_cache.IsAnyInPlace(dst, num_bytes) &&
           !buffer_cache.IsRegionGpuModified(dst, num_bytes) &&
           !VideoCore::BufferCache::LayerMirrored(dst, num_bytes);
}

void RasterizerCore::CopyBuffer(VAddr dst, VAddr src, u32 num_bytes, bool dst_gds, bool src_gds) {
    DrainDrawPipe();
    if(FrameCapture::NativeRecording()) {
        if(!src_gds)FrameCapture::NativeBufferUse(0,0,0,num_bytes,src,7,0,false,"dma_copy");
        if(!dst_gds)FrameCapture::NativeBufferUse(0,0,0,num_bytes,dst,7,0,true,"dma_copy");
    }
    // bbport BB_GUEST_IN_PLACE: see FillBuffer (in place: the GPU, in stream order).
    if (!dst_gds && !buffer_cache.IsAnyInPlace(dst, num_bytes) &&
        !buffer_cache.IsRegionGpuModified(dst, num_bytes) &&
        !VideoCore::BufferCache::LayerMirrored(dst, num_bytes)) {
        // bbport BB_LAYER_MEMORY: a VRAM copy of the source may hold newer data than the game's memory.
        if (!src_gds && !buffer_cache.IsRegionGpuModified(src, num_bytes) &&
            !VideoCore::BufferCache::LayerMirrored(src, num_bytes) &&
            !texture_cache.FindImageFromRange(src, num_bytes)) {
            // Both buffers were not transferred to GPU yet. Can safely copy in host memory.
            BbFreeCheck::Check(dst, num_bytes, std::bit_cast<const void*>(src), BbFreeCheck::DmaCopy);
            std::memcpy(std::bit_cast<void*>(dst), std::bit_cast<void*>(src), num_bytes);
            BbWriteLog::Note(dst, std::bit_cast<const void*>(dst), num_bytes, BbWriteLog::Dma);
            if (!VideoCore::WriteTracking()) {
                NoteAssetWrite(dst, num_bytes);
            }
            return;
        }
    }

    const auto* gds_buffer = buffer_cache.GetGdsBuffer();
    const auto [src_buffer, src_offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (src_gds) {
            return {gds_buffer, src};
        }
        return buffer_cache.ObtainBuffer(src, num_bytes, false, true);
    }();
    auto [dst_buffer, dst_offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (dst_gds) {
            return {gds_buffer, dst};
        }
        return buffer_cache.ObtainBuffer(dst, num_bytes, true, true);
    }();
    if(!dst_gds&&buffer_cache.PrepareImageAliasesForRawWrite(dst,num_bytes,dst_buffer,dst_offset)==BufferCache::RawPrewriteResult::Rebind)
        std::tie(dst_buffer,dst_offset)=buffer_cache.ObtainBuffer(dst,num_bytes,true,true);
    texture_cache.InvalidateMemoryFromGPU(dst, num_bytes);
    const vk::BufferCopy copy = {
        .srcOffset = src_offset,
        .dstOffset = dst_offset,
        .size = num_bytes,
    };
    backend.CopyBuffer(src_buffer, dst_buffer, std::span{&copy, 1});
}

u32 RasterizerCore::ReadDataFromGds(u32 gds_offset) {
    DrainDrawPipe();
    auto* gds_buf = buffer_cache.GetGdsBuffer();
    u32 value;
    std::memcpy(&value, gds_buf->mapped_data.data() + gds_offset, sizeof(u32));
    return value;
}

bool RasterizerCore::InvalidateMemory(VAddr addr, u64 size, bool assume_locks) {
    DrainDrawPipe();
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.InvalidateMemory(addr, size, assume_locks);
    texture_cache.InvalidateMemory(addr, size);
    return true;
}

void RasterizerCore::NoteAssetWrite(VAddr addr, u64 size) {
    buffer_cache.NoteAssetWrite(addr, size);
    InvalidateAfterWrite(addr, size);
}

void RasterizerCore::InvalidateAfterWrite(VAddr addr, u64 size) {
    if (VideoCore::WriteTracking() || !buffer_cache.IsRegionGpuModified(addr, size)) {
        InvalidateMemory(addr, size);
        return;
    }
    // GPU data beside the bytes the CPU wrote lives in VRAM: an invalidation would read it back
    // (a 512 KiB window, whole pages) and upload pages over what the GPU writes meanwhile.
    // Exactly the CPU's bytes go into VRAM instead; images over them are refreshed.
    DrainDrawPipe();
    buffer_cache.NotePreciseUpload(addr, size);
    texture_cache.InvalidateMemory(addr, size);
}

void RasterizerCore::NoteCommandWrite(VAddr addr, const void* data, u64 size, bool recording_thread) {
    if(FrameCapture::NativeRecording())FrameCapture::NativeCpuAccess(addr,size,true);
    if (VideoCore::WriteTracking() || !IsMapped(addr, size)) {
        return; // tracking catches it, or the GPU does not use it
    }
    if (!recording_thread) {
        DrainDrawPipe();
    }
    buffer_cache.DropShadows(addr, size);
    // An invalidation would read the GPU's data back over these bytes first (a readback).
    if (!buffer_cache.UpdateGpuWritten(addr, std::span{static_cast<const u8*>(data), size})) {
        buffer_cache.InvalidateMemory(addr, size, recording_thread);
    }
    texture_cache.InvalidateMemory(addr, size);
}

void RasterizerCore::OnCpuWrite(VAddr addr, u64 size) {
    if(FrameCapture::NativeRecording())FrameCapture::NativeCpuAccess(addr,size,true);
    BbStats::cpu_write_notes.fetch_add(1, std::memory_order_relaxed);
    BbStats::cpu_write_note_bytes.fetch_add(size, std::memory_order_relaxed);
    if (IsMapped(addr, size)) {
        InvalidateAfterWrite(addr, size);
        buffer_cache.NoteCpuWriteRange(addr, size);
    }
}

bool RasterizerCore::OnWriteFault(VAddr addr, bool assume_locks, u64 guest_rip) {
    if(FrameCapture::NativeRecording())FrameCapture::NativeCpuAccess(addr&~VAddr{4095},4096,true);
    DrainDrawPipe();
    if (!InvalidateMemory(addr, 8, assume_locks)) {
        return false;
    }
    buffer_cache.ExtendWriteFault(addr, guest_rip);
    return true;
}

bool RasterizerCore::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if(FrameCapture::NativeRecording())FrameCapture::NativeCpuAccess(addr,size,false);
    DrainDrawPipe();
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.ReadMemory(addr, size, false, assume_locks);
    return true;
}

void RasterizerCore::ProcessDownloadImages() {
    DrainDrawPipe();
    texture_cache.ProcessDownloadImages();
}

bool RasterizerCore::IsMapped(VAddr addr, u64 size) {
    if (size == 0) {
        // There is no memory, so not mapped.
        return false;
    }
    if (static_cast<u64>(addr) > std::numeric_limits<u64>::max() - size) {
        // Memory range wrapped the address space, cannot be mapped.
        return false;
    }
    const auto range = decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);

    Common::RecursiveSharedLock lock{mapped_ranges_mutex};
    return boost::icl::contains(mapped_ranges, range);
}

void RasterizerCore::MapMemory(VAddr addr, u64 size) {
    {
        std::scoped_lock lock{mapped_ranges_mutex};
        mapped_ranges += decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);
    }
    buffer_cache.NotePreuploadMapping(addr, size, true);
}

void RasterizerCore::RegisterMemory(VAddr addr, u64 size) {
    page_manager.OnGpuMap(addr, size);
}

void RasterizerCore::UnmapMemory(VAddr addr, u64 size) {
    buffer_cache.NotePreuploadMapping(addr, size, false);
    buffer_cache.UnmapInPlace(addr, size);
    buffer_cache.DropTwins(addr, size);
    DrainDrawPipe();
    buffer_cache.InvalidateMemory(addr, size);
    texture_cache.UnmapMemory(addr, size);
    {
        std::scoped_lock lock{mapped_ranges_mutex};
        mapped_ranges -= decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);
    }
}

std::thread::id RasterizerCore::GetGpuCommandProcessorThread() {
    return liverpool->GetGpuCommandProcessorThread();
}

#ifdef __linux__
u32 RasterizerCore::GetGpuCommandProcessorThreadId() {
    return liverpool->GetGpuCommandProcessorThreadId();
}
#endif

} // namespace VideoCore
