// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <memory>
#include <optional>
#include <unordered_map>
#include <vector>

#include "common/types.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_metal/mtl_host_pso_recipe.h"
#include "video_core/cache_backend.h"
#include "video_core/texture_cache/image.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/sampler.h"
#include "video_core/texture_cache/types.h"

namespace Metal {

class Device;
class Scheduler;
class SceneTargets;
class Runtime;
class PsoCache;

/// Pending buffer accesses between two FlushBarriers (the Vulkan runtime's BarrierTracker, cut
/// down: a few ranges per buffer, merged into one bounding range when there are more).
class BufferAccessTracker {
public:
    enum Access : u8 { Read = 1, Write = 2 };
    BufferAccessTracker();
    ~BufferAccessTracker();
    struct AuditStats {u64 inserts{},collapses{},mixed{},queries{},false_reads{},false_writes{},unknown{},missed{};};
    AuditStats AuditSnapshot() const;

    void Insert(const void* resource, u64 start, u64 end, u8 access);
    /// Whether a range overlapping [start, end] was inserted with one of the `access` bits.
    [[nodiscard]] bool Find(const void* resource, u64 start, u64 end, u8 access) const;
    void Clear();

private:
    struct Range {
        u64 start, end;
        u8 access;
    };
    static constexpr size_t MaxRanges = 16;
    struct Audit;
    std::unique_ptr<Audit> audit; // diagnostic only, <400 KiB bounded exact reference, no GPU resource
    std::unordered_map<const void*, std::vector<Range>> entries;
};

/// Staging memory of the caches (the Vulkan StagingBufferPool): rings of 16 MiB shared stream
/// buffers per memory type, and dedicated buffers for what does not fit or is held. Everything is
/// shared host memory on Metal; the types only keep the callers' pools apart.
class StagingPool {
public:
    explicit StagingPool(Runtime& backend);
    ~StagingPool();

    StagingPool(const StagingPool&) = delete;
    StagingPool& operator=(const StagingPool&) = delete;

    [[nodiscard]] VideoCore::StagingSpan Request(u64 size, VideoCore::MemoryType type,
                                                 u64 alignment = 0, bool deferred = false);
    void FreeDeferred(const VideoCore::StagingSpan& span);
    /// Releases idle blocks (once per frame).
    void TickFrame();

private:
    struct Block {
        std::unique_ptr<VideoCore::StreamBuffer> buffer;
        u64 last_used_frame;
        bool discarded = false;
    };
    struct Ring {
        std::vector<Block> blocks;
        size_t current = 0;
    };
    struct LargeBuffer {
        std::unique_ptr<VideoCore::Buffer> buffer;
        u64 tick;
        u64 last_used_frame;
        bool held;
    };
    static constexpr size_t NumTypes = 3;

    VideoCore::StagingSpan RequestFromRing(Ring& ring, u64 size, u64 alignment,
                                           VideoCore::MemoryType type);
    VideoCore::StagingSpan RequestLarge(u64 size, VideoCore::MemoryType type, bool deferred);
    void TrimRing(Ring& ring);
    void TrimLarge(std::vector<LargeBuffer>& cache);

    Runtime& backend;
    std::array<Ring, NumTypes> rings;
    std::array<std::vector<LargeBuffer>, NumTypes> large_caches;
    u64 keep_blocks = 0;
    u64 frame = 0;
};

/// The Metal implementation of the seam under the buffer and texture caches (plan P2.4, D2,
/// docs/metal/cache_backend_design.md): buffers and images as MTLBuffer/MTLTexture (`native`
/// slots of the shared objects), transfers on blit encoders of the Scheduler, the caches' own
/// kernels (detiling, fault parsing, MSAA copies) from the D11 host shaders.
///
/// Lifetime (D6): every Destroy* defers the release (and the residency set removal) with
/// Scheduler::DeferOperation, which runs once the open tick's command buffer has completed.
/// Threads: as the Scheduler's, the recording calls come from one thread at a time; the object
/// creation and destruction calls may come from any thread.
class Runtime final : public VideoCore::CacheBackend {
public:
    Runtime(Device& device, Scheduler& scheduler);
    /// Finishes the GPU work (the kernels' pipelines are released with it).
    ~Runtime() override;

    void BindHostPsoCache(PsoCache* cache);
    HostPso::WarmResult WarmHostPipelines();
    HostPso::WarmResult FinishHostPreparation();
    const HostPso::WarmResult& HostPreparationResult()const;
    bool HostPreparationCritical()const;

    struct Impl;
    Impl& GetImpl() const {
        return *impl;
    }
    Scheduler& GetScheduler() {
        return scheduler;
    }
    Device& GetDevice() {
        return device;
    }

    /// Releases idle staging memory; once per frame.
    void TickFrame() override;
    void RegisterBdaLeaf(const VideoCore::Buffer*,bool attach) override;
    bool DiscardIdleStaging(const VideoCore::Buffer& buffer);
    void WakeStaging(const VideoCore::Buffer& buffer);

    /// bbport (P7.5 L2): the reduced scene targets, set by their constructor (as
    /// Vulkan::Runtime::scene_targets): Transit makes every native use of a proxied image resolve
    /// its dirty proxy first.
    SceneTargets* scene_targets = nullptr;

    /// Whether the Metal texture of an image made in the format `base` can have a view in the
    /// format `view` (what CreateImageView allows); both as the caches pass them.
    [[nodiscard]] bool ViewFormatCompatible(vk::Format base, vk::Format view) const;

    /// bbport: runs before this thread changes image state (as Vulkan::Runtime).
    void SetImageAccessHook(void (*hook)(void*), void* context) {
        image_access_hook = hook;
        image_access_context = context;
    }

    // Transfers
    void CopyBuffer(const VideoCore::Buffer* src, const VideoCore::Buffer* dst,
                    std::span<const vk::BufferCopy> copies) override;
    void FillBuffer(const VideoCore::Buffer* dst, u64 offset, u64 size, u32 value) override;
    void InlineData(VideoCore::Buffer* dst, u64 offset, u32 value) override;
    void CopyFromGuestChunk(u64 chunk_phys, const VideoCore::Buffer* dst,
                            std::span<const vk::BufferCopy> copies) override;
    void UpdateBuffer(const VideoCore::Buffer* dst, u64 offset, std::span<const u8> data) override;
    void UploadImage(VideoCore::Image* dst, const VideoCore::Buffer* src,
                     std::span<const vk::BufferImageCopy> upload_copies) override;
    void DownloadImage(VideoCore::Image* src, const VideoCore::Buffer* dst,
                       std::span<const vk::BufferImageCopy> download_copies) override;
    void CopyImage(VideoCore::Image* src, VideoCore::Image* dst) override;
    void CopyImageWithBuffer(VideoCore::Image* src, VideoCore::Image* dst,
                             const VideoCore::Buffer* buffer, u64 offset) override;
    void CopyMip(VideoCore::Image* src, VideoCore::Image* dst, u32 mip, u32 slice) override;
    void CopyColorAndDepth(VideoCore::Image* src, VideoCore::Image* dst) override;
    void CopyDepthStencil(VideoCore::Image* src, VideoCore::Image* dst,
                          const VideoCore::SubresourceRange& sub_range) override;
    void ResolveImage(VideoCore::Image* src, VideoCore::Image* dst,
                      const VideoCore::SubresourceRange& src_range,
                      const VideoCore::SubresourceRange& dst_range) override;
    void ClearImage(VideoCore::Image* dst, const VideoCore::SubresourceRange& range,
                    const vk::ClearValue& clear_value) override;
    void SetBackingSamples(VideoCore::Image* image, u32 num_samples,
                           bool copy_backing = true) override;
    VideoCore::ImageUsagePromotion RequiredViewUsage(
        const VideoCore::Image& image, const VideoCore::ImageViewInfo& view) const override;
    /// P10.1: a new backing with the usage, the contents copied, the old one retired. The caller
    /// holds no view of the image across the call (a ViewId it got before stays valid until the
    /// open tick has completed and reads the old texture).
    void PromoteImageUsage(VideoCore::Image* image, VideoCore::ImageUsagePromotion usage) override;

    // Access tracking
    void AccessBuffer(const VideoCore::Buffer* handle, u64 offset, u64 size,
                      vk::PipelineStageFlags2 src_stage, vk::AccessFlags2 src_access) override;
    bool IsBufferAccessed(const VideoCore::Buffer* handle, u64 offset, u64 size,
                          bool check_read_access = false) override;
    void AccessGlobal(vk::PipelineStageFlags2 src_stage, vk::AccessFlags2 src_access) override;
    bool Transit(VideoCore::Image* image, vk::ImageLayout dst_layout,
                 vk::PipelineStageFlags2 dst_stage, vk::AccessFlags2 dst_access,
                 std::optional<VideoCore::SubresourceRange> subres_range = {}) override;
    void FlushBarriers() override;
    u64 RenderUpdateCount() const;
    void FlushImageBarriers() override;
    void BeforeImageAccess() override {
        if (image_access_hook) {
            image_access_hook(image_access_context);
        }
    }

    // Staging
    VideoCore::StagingSpan StagingRequest(u64 size, VideoCore::MemoryType type, u64 alignment = 0,
                                          bool deferred = false) override;
    void StagingFreeDeferred(const VideoCore::StagingSpan& span) override;

    // Ticks and ordering (forwarded to the Scheduler)
    u64 CurrentTick() const noexcept override;
    bool IsFree(u64 tick) noexcept override;
    bool IsFreeKnown(u64 tick) const noexcept override;
    void RefreshTicks() override;
    void Wait(u64 tick) override;
    void Finish() override;
    void DeferOperation(Common::UniqueFunction<void>&& func) override;
    void DeferPriorityOperation(Common::UniqueFunction<void>&& func) override;
    void PopPendingOperations() override;
    void EndRendering() override;
    bool IsRendering() const noexcept override;
    bool IsRecordingDeferred() const noexcept override {
        return false;
    }
    void NoteHostCopySource(u64, u64) override {}
    void RecordHostCopy(const BbCopy::Item& item) override {
        item.run(item);
    }

    // Queries
    vk::Format SupportedFormat(vk::Format format, vk::FormatFeatureFlags2 features) const override;
    u64 DeviceMemoryUsage() const override;
    u64 DeviceMemoryBudgetNow() const override;

    // The caches' own GPU work: compute kernels from the D11 host shaders
    void DispatchTiling(const VideoCore::TilingDispatch& dispatch) override;
    bool DownloadTiledImage(VideoCore::Image* image, const VideoCore::TilingDispatch& dispatch) override;
    std::unique_ptr<VideoCore::ExactImageExportAttempt> ClaimExactImageExportAttempt() override;
    bool CanPrepareExactImageExport(const VideoCore::Image&) const override;
    std::unique_ptr<VideoCore::PreparedExactImageExport> PrepareExactImageExport(
        std::unique_ptr<VideoCore::ExactImageExportAttempt>, VideoCore::Image*,
        const VideoCore::Buffer*,u64,std::span<const vk::BufferImageCopy>,std::span<const u8>,
        const std::function<void()>& release_input_lease = {}) override;
    void InvalidatePreparedImageExport(u64) noexcept override;
    u64 PreparedExactPoolRequests() const noexcept {
        return exact_export_pool_requests.load(std::memory_order_relaxed);
    }

    void CreateFaultParser(u32 page_bits, u32 max_faults) override;
    void ParseFaultBuffer(const VideoCore::FaultParseDispatch& dispatch) override;

    // Objects
    VideoCore::BufferMemory CreateBuffer(VideoCore::UniqueBuffer& out,
                                         const vk::BufferCreateInfo& info,
                                         VideoCore::MemoryType type) override;
    VideoCore::BufferMemory ImportBuffer(VideoCore::UniqueBuffer& out, u64 size,
                                         u64 chunk_phys) override;
    void DestroyBuffer(VideoCore::UniqueBuffer& buffer) override;
    void FlushBuffer(const VideoCore::UniqueBuffer& buffer, u64 offset, u64 size) override;
    void InvalidateBuffer(const VideoCore::UniqueBuffer& buffer, u64 offset, u64 size) override;
    void CreateImage(VideoCore::UniqueImage& out, const vk::ImageCreateInfo& info) override;
    void AssociateGuestImage(const VideoCore::UniqueImage&, u64 address, u64 size) override;
    void AssociateGuestBuffer(const VideoCore::UniqueBuffer&, u64 address, u64 size) override;
    void DestroyImage(VideoCore::UniqueImage& image) override;
    vk::SampleCountFlags ImageSampleCounts(const vk::ImageCreateInfo& info) const override;
    void CreateImageView(VideoCore::ImageView& out, const vk::ImageViewCreateInfo& info) override;
    void DestroyImageView(VideoCore::ImageView& view) override;
    void CreateSampler(VideoCore::Sampler& out, const vk::SamplerCreateInfo& info) override;
    void DestroySampler(VideoCore::Sampler& sampler) override;
    void SetBufferName(const VideoCore::UniqueBuffer& buffer, std::string_view name) override;
    void SetImageName(const VideoCore::UniqueImage& image, std::string_view name) override;
    void SetImageViewName(const VideoCore::ImageView& view, std::string_view name) override;

    u64 BufferWriteEpoch() const noexcept override { return buffer_write_epoch; }
    // Host-compute paths which establish ordering themselves still invalidate source-copy
    // reuse when they write guest buffers. This records no command or extra barrier.
    void NoteOrderedGuestBufferWrite() noexcept;

    // Optional memory paths
    bool GuestMemoryUsable() override;

private:
    struct ExactExportOwner;
    struct PreparedExportState;
    struct ExportAttemptToken;
    class ExactExportToken;
    VideoCore::ExactImageExportResult RecordExactExport(PreparedExportState&,ExactExportOwner&) noexcept;
    void InvalidateExactExports() noexcept;
    void CloseExactExportOwner() noexcept;
    void PrepareExactSnapshot(const void*,const void*,std::span<const vk::BufferImageCopy>);
    std::atomic<u64> exact_export_attempt_tick{0};
    std::atomic<u64> exact_export_pool_requests{0};
    std::shared_ptr<ExactExportOwner> exact_export_owner{};
    /// Whether the texels of the image's Metal texture are laid out as its guest format says
    /// (same texel size, no depth conversion); logs once per format when not.
    bool CopyCompatible(const VideoCore::Image& image, const char* what);

    /// Promotes `src` to PixelFormatView when a copy into `dst` views it in another pixel format.
    void PromoteCopySource(VideoCore::Image* src, const VideoCore::Image& dst);

    Device& device;
    Scheduler& scheduler;
    std::unique_ptr<Impl> impl;
    StagingPool staging_pool;
    BufferAccessTracker tracker;
    VideoCore::Image::Barriers scratch_barriers; ///< Transit's bookkeeping, never recorded
    struct BarrierAudit;
    std::unique_ptr<BarrierAudit> barrier_audit; // bounded CPU metadata, native capture only
    bool memory_pending = false; ///< an access since the last FlushBarriers needs a barrier
    u64 buffer_write_epoch = 1; ///< recording thread, monotonic across FlushBarriers
    bool global_write = false;   ///< bbport: AccessGlobal wrote since the last FlushBarriers
    void (*image_access_hook)(void*) = nullptr;
    void* image_access_context = nullptr;
};

} // namespace Metal
