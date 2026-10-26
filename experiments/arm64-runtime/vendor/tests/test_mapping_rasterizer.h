// SPDX-License-Identifier: GPL-2.0-or-later
// Page tracking needs only IsMapped; unexpected renderer calls fail the cache test.
#pragma once
#include <cstdlib>
#include "video_core/rasterizer_api.h"
class MappingOnlyRasterizer final : public VideoCore::RasterizerApi {
public:
    void Draw(bool is_indexed, u32 index_offset = 0, const Vulkan::PreparedDraw* prepared = nullptr) override { std::abort(); }
    void DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 size, u32 max_count, VAddr count_address, u16 vertex_sgpr_offset, u16 instance_sgpr_offset) override { std::abort(); }
    void DispatchDirect() override { std::abort(); }
    void DispatchIndirect(VAddr address, u32 offset, u32 size) override { std::abort(); }
    Vulkan::DrawPreparation* GetDrawPreparation() override { std::abort(); }
    bool HostMarkersEnabled() const override { std::abort(); }
    void ScopeMarkerBegin(const std::string_view& str, bool from_guest = false) override { std::abort(); }
    void ScopeMarkerEnd(bool from_guest = false) override { std::abort(); }
    void ScopedMarkerInsert(const std::string_view& str, bool from_guest = false) override { std::abort(); }
    void ScopedMarkerInsertColor(const std::string_view& str, const u32 color, bool from_guest = false) override { std::abort(); }
    void FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds) override { std::abort(); }
    void CopyBuffer(VAddr dst, VAddr src, u32 num_bytes, bool dst_gds, bool src_gds) override { std::abort(); }
    bool DmaMayWriteOnCpu(VAddr dst, u32 num_bytes) override { std::abort(); }
    u32 ReadDataFromGds(u32 gsd_offset) override { std::abort(); }
    bool InvalidateMemory(VAddr addr, u64 size, bool assume_locks = false) override { std::abort(); }
    void NoteAssetWrite(VAddr addr, u64 size) override { std::abort(); }
    void OnCpuWrite(VAddr addr, u64 size) override { std::abort(); }
    void NoteLateCommandWrite(VAddr addr, const void* data, u64 size) override { std::abort(); }
    void NoteCommandWrite(VAddr addr, const void* data, u64 size, bool recording_thread) override { std::abort(); }
    void NoteCommandWriteInOrder(VAddr addr, const void* data, u64 size) override { std::abort(); }
    void WaitForPendingReads(VAddr address, u64 size) override { std::abort(); }
    bool IsMapped(VAddr addr, u64 size) override { return true; }
    void MapMemory(VAddr addr, u64 size) override { std::abort(); }
    void RegisterMemory(VAddr addr, u64 size) override { std::abort(); }
    void UnmapMemory(VAddr addr, u64 size) override { std::abort(); }
    bool ReadMemory(VAddr addr, u64 size, bool assume_locks = false) override { std::abort(); }
    void ProcessDownloadImages() override { std::abort(); }
    bool IsGpuSideThread() const override { std::abort(); }
    bool IsGpuSideThreadId(u32 tid) const override { std::abort(); }
    bool OnWriteFault(VAddr addr, bool assume_locks, u64 guest_rip = 0) override { std::abort(); }
    bool OnOcclusionPageAccess(VAddr addr, u64 rip, bool write, bool gpu_thread) override { std::abort(); }
    bool OnVramDataAccess(VAddr addr, bool assume_locks) override { std::abort(); }
    void DrainDrawPipe(u32 reason = Vulkan::DrawPipe::ReasonRasterizer, u32 line = __builtin_LINE(), const char* function = __builtin_FUNCTION()) override { std::abort(); }
    bool RunInOrder(OrderedTask task, const void* data, u32 size, u64 toggle = BbToggle::PipelinedTasks, bool reads_guest_memory = true) override { std::abort(); }
    u64 DrawPipeHead() const override { std::abort(); }
    bool DrawPipeReached(u64 position) const override { std::abort(); }
    bool DrawPipeIdle() const override { std::abort(); }
    void RetireSubmission() override { std::abort(); }
    void NotePendingGpuWrite(VAddr address, u64 size) override { std::abort(); }
    void NotePendingRead(VAddr address, u64 size) override { std::abort(); }
    void WaitHostCopies() override { std::abort(); }
    void WaitHostCopiesFor(VAddr address, u64 size) override { std::abort(); }
    void WaitDeferredSignals() override { std::abort(); }
    void SignalAfterHostCopies(std::function<void()> signal) override { std::abort(); }
    void SignalAfterGpu(std::function<void()> signal, VAddr label = 0, u64 value = 0) override { std::abort(); }
    bool HasUnsubmittedSignals() override { std::abort(); }
    bool WriteDataOnGpu(VAddr address, const void* data, u32 size) override { std::abort(); }
    bool WriteLabelOnGpu(VAddr address, u64 value, u32 num_bytes) override { std::abort(); }
    bool WriteTimestampOnGpu(VAddr address, bool end_of_pipe) override { std::abort(); }
    bool OcclusionEvent(VAddr address, u32 pairs) override { std::abort(); }
    u64 OcclusionEvents() const override { std::abort(); }
    void SubmitForSignals() override { std::abort(); }
    bool PipeSignals() const override { std::abort(); }
    bool SignalsPublished(u64 position) const override { std::abort(); }
    void RequestSignalFlush() override { std::abort(); }
    bool PendingSignalValue(VAddr address, u64& value) override { std::abort(); }
    u64 CurrentTick() const override { std::abort(); }
    u64 Flush() override { std::abort(); }
    void Finish() override { std::abort(); }
    void OnSubmit() override { std::abort(); }
};
