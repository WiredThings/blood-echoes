// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <optional>
#include <memory>
#include <array>
#include <span>
#include <string>
#include <string_view>
#include <boost/container_hash/hash.hpp>

#include "bbport_copy.h"
#include "common/enum.h"
#include "common/types.h"
#include "common/unique_function.h"
#include "video_core/amdgpu/tiling.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_common.h"
#include "video_core/texture_cache/types.h"

// The seam under the buffer and texture caches (docs/metal/cache_backend_design.md, plan D2): the
// caches talk to the GPU only through CacheBackend. Vulkan enums and plain structs are the shared
// vocabulary; Vulkan handles and calls are not.

namespace Vulkan {
struct SubmitInfo;
}

namespace VideoCore {

struct Image;
struct ImageView;
struct ImageViewInfo;
struct UniqueImage;
class Sampler;

/// Device limits and feature flags the caches test; filled once by the backend.
struct BackendLimits {
    u64 uniform_min_alignment{};    ///< minimum alignment of uniform buffer offsets
    u64 storage_min_alignment{};    ///< minimum alignment of storage buffer offsets
    u64 non_coherent_atom_size{};   ///< alignment for flushing and invalidating mapped memory
    float max_sampler_anisotropy{}; ///< maximum sampler anisotropy
    float max_sampler_lod_bias{};   ///< maximum absolute sampler LOD bias
    u32 max_sampler_allocation_count{}; ///< samplers the device can have at once
    u64 total_memory_budget{};          ///< total memory budget available to the device
    bool integrated{};                  ///< the GPU shares memory with the CPU
    bool can_report_memory_usage{};     ///< DeviceMemoryUsage and DeviceMemoryBudgetNow are valid
    bool image_view_min_lod{};          ///< image views can clamp the minimum LOD
    bool anisotropic_filtering{};       ///< samplers can use anisotropic filtering
    bool custom_border_color{};         ///< samplers can use custom border colors
    bool image_2d_view_of_3d{};         ///< 3D images can have 2D views
    bool block_texel_view{};            ///< block compressed images can have uncompressed views
    bool attachment_feedback_loop_layout{}; ///< images can be read while they are attachments
    u32 max_framebuffer_width{};            ///< largest render pass width
    u32 max_framebuffer_height{};           ///< largest render pass height
    // What the rasterizer core asks of the device (plan P4.1 R6).
    u32 max_viewport_width{};           ///< largest viewport width
    u32 max_viewport_height{};          ///< largest viewport height
    bool depth_range_unrestricted{};    ///< viewport depth outside [0, 1]
    bool list_restart{};                ///< primitive restart in list topologies
    bool patch_list_restart{};          ///< primitive restart in patch lists
    bool vertex_input_dynamic_state{};  ///< vertex input descriptions are set at draw time
    bool null_descriptor{};             ///< an unbound buffer can be a null descriptor
    /// bbport (P10.1): images are created without the storage usage and the mutable format flag
    /// (depth keeps the flag) and gain them by CacheBackend::PromoteImageUsage when a view needs
    /// them (the Apple GPU keeps images without them losslessly compressed). Only the Metal runtime
    /// sets it; with it clear the caches create the images as they always did.
    bool lean_image_usage{};
    bool two_level_bda{}; ///< Metal: physical-address second-level page tables
};

/// What an image created lean (BackendLimits::lean_image_usage) can gain afterwards.
enum class ImageUsagePromotion : u32 {
    None = 0,
    Storage = 1 << 0,       ///< the storage usage: a shader writes the image
    MutableFormat = 1 << 1, ///< the mutable format flag: a view in another (non sRGB twin) format
};
DECLARE_ENUM_FLAG_OPERATORS(ImageUsagePromotion)

/// What the creator of a buffer needs to know about its memory.
struct BufferMemory {
    u8* mapped{};    ///< the CPU's view of the whole buffer, null when it is not host visible
    bool coherent{}; ///< host and device writes are visible without Flush and Invalidate
};

/// Memory the CPU writes through (Flush) or reads through (Invalidate), with the buffer it is in.
struct StagingSpan {
    Buffer* buffer{};
    u64 offset{};
    u64 size{};
    u8* mapped{};
    MemoryType type{};

    void Flush() const noexcept {
        buffer->Flush(offset, size);
    }

    void Invalidate() const noexcept {
        buffer->Invalidate(offset, size);
    }
};

/// Identifies one tiling kernel: the backend keeps one pipeline per key.
struct TilingKey {
    AmdGpu::TileMode tile_mode;
    u32 num_bits;
    u32 num_samples;
    bool is_tiler; ///< linear to tiled, otherwise tiled to linear (detiling)

    bool operator==(const TilingKey&) const = default;

    struct Hash {
        size_t operator()(const TilingKey& key) const {
            size_t hash = 0;
            boost::hash_combine(hash, key.tile_mode);
            boost::hash_combine(hash, key.num_bits);
            boost::hash_combine(hash, key.num_samples);
            boost::hash_combine(hash, key.is_tiler);
            return hash;
        }
    };
};

/// A range of a buffer a compute kernel reads or writes.
struct KernelBuffer {
    const Buffer* buffer{};
    u64 offset{};
    u64 range{};
};

/// One tiling or detiling dispatch: a compute kernel over 64 threads per group, reading and
/// writing `tiled` and `linear` as storage buffers, with `params` (the TilingInfo of the
/// texture cache) as a uniform buffer.
struct TilingDispatch {
    TilingKey key;
    AmdGpu::ArrayMode array_mode;
    bool is_macro; ///< macro tiled (the macro tile constants are used), otherwise micro tiled
    /// Compile time constants of the kernel (specialization constants, function constants on
    /// Metal): the first `num_constants` entries of `constants` are used, in order.
    std::array<u32, 12> constants;
    u32 num_constants;
    KernelBuffer tiled;
    KernelBuffer linear;
    KernelBuffer params;
    u32 group_count_x;
};

/// One run of the fault buffer parser: reads the fault bitmap `fault_buffer` (1 bit per page)
/// and writes the list of faulting page addresses into `download` (a count, then the addresses).
struct FaultParseDispatch {
    const Buffer* fault_buffer{};
    u64 fault_buffer_size{};
    KernelBuffer download;
    u32 group_count_x{};
};

/// A block of device memory the sparse arena's ranges are bound to: an opaque handle chosen by the
/// backend (Vulkan: the VkDeviceMemory), 0 for none.
using ArenaMemory = u64;

/// One range of an arena buffer bound to `memory` (or unbound, `memory` 0) at `memory_offset`.
struct SparseBind {
    u64 resource_offset;
    u64 size;
    ArenaMemory memory;
    u64 memory_offset;
};

enum class ArenaAllocStatus : u8 {
    Success,
    OutOfMemory, ///< device or host memory exhausted: the caller may fall back
    Failed,      ///< any other error
};

struct ArenaAllocation {
    ArenaMemory memory{};
    ArenaAllocStatus status{ArenaAllocStatus::OutOfMemory};
    std::string error; ///< the backend's text for a failure
};

/// The arena bind queue's call counts, for statistics and the GPU watchdog.
struct ArenaBindStats {
    u64 calls{};
    u64 ranges{};
};

/// bbport: the sparse arena of the buffer cache and its residency memory (docs/metal/
/// cache_backend_design.md, group D). The cache decides when blocks move and which ranges are
/// bound; the backend allocates and frees the memory blocks, queues the sparse binds and puts them
/// into the queue with the submission. Optional (CacheBackend::GetSparseArena): a backend without
/// sparse binding uses the layer's memory module (BB_LAYER_MEMORY) only.
class SparseArena {
public:
    virtual ~SparseArena() = default;

    /// Alignment of the smallest range that can be bound (the sparse block size).
    [[nodiscard]] virtual u64 BlockAlignment() const noexcept = 0;

    /// Whether a system memory type exists residency can spill to when the device memory is full.
    [[nodiscard]] virtual bool HasFallbackMemory() const noexcept = 0;

    /// Allocates `size` bytes in the arena's preferred (device local) memory. Thread safe.
    [[nodiscard]] virtual ArenaAllocation Allocate(u64 size) = 0;

    /// Allocates `size` bytes in system memory (HasFallbackMemory); failing is fatal. Thread safe.
    [[nodiscard]] virtual ArenaMemory AllocateFallback(u64 size) = 0;

    /// Allocates `size` bytes in any other memory type the arena can be bound to; 0 when none
    /// takes it.
    [[nodiscard]] virtual ArenaMemory AllocateOtherType(u64 size) = 0;

    /// Gives a block of memory back. The caller makes sure no bind refers to it and the GPU is
    /// done with it.
    virtual void Free(ArenaMemory memory) = 0;

    /// Appends `binds` to the pending binds of `arena` (also when empty: the arena then has an
    /// entry); they go into the queue with the next submission (SubmitBinds).
    virtual void QueueBinds(const Buffer* arena, std::span<const SparseBind> binds) = 0;

    /// Whether binds are waiting for the next submission.
    [[nodiscard]] virtual bool HasPendingBinds() const noexcept = 0;

    /// Hands the pending binds to the submission going out: they are performed in the queue right
    /// before it (waiting for `wait_tick` of the work timeline when it is not 0), and the
    /// submission waits for them.
    virtual void SubmitBinds(Vulkan::SubmitInfo& info, u64 wait_tick) = 0;

    [[nodiscard]] virtual ArenaBindStats BindStats() const noexcept = 0;

    /// bbport (GPU watchdog): prints how far the GPU got with the binds.
    virtual void ReportBinds() = 0;
};

/// bbport: copies finished GPU data out of a buffer through a queue of its own, waiting only for
/// the writes up to a tick, not for the frames queued behind them. Optional
/// (CacheBackend::GetFastReadback).
class FastReadback {
public:
    virtual ~FastReadback() = default;

    [[nodiscard]] virtual bool Available() const noexcept = 0;

    /// Copies `copies` (destination offsets from 0, `total` bytes) of `arena` once the work
    /// timeline reaches `tick`; returns the host copy, or null on failure (the caller takes the
    /// regular path). One caller at a time.
    [[nodiscard]] virtual const u8* Copy(const Buffer* arena,
                                         std::span<const vk::BufferCopy> copies, u64 total,
                                         u64 tick) = 0;
};

enum class ExactImageExportResult { Recorded, Stale, Consumed };
// One-shot command token. Preparation may compile/allocate/join replay and record
// a read-only image snapshot. Record never calls an image-access hook or creates
// an encoder/pipeline/staging allocation. It grants NO payload/mapping lease.
class ExactImageExportAttempt {
public:
    virtual ~ExactImageExportAttempt() = default;
    virtual const void* OwnerCookie() const noexcept = 0;
};
class PreparedExactImageExport {
public:
    virtual ~PreparedExactImageExport() = default;
    virtual ExactImageExportResult Record() noexcept = 0;
};

class CacheBackend {
public:
    virtual ~CacheBackend() = default;
    virtual void RegisterBdaLeaf(const Buffer*, bool attach) {}
    // Optional diagnostics only. The default backend has no guest lifetime association.
    virtual void AssociateGuestImage(const UniqueImage&, u64 address, u64 size) {}
    virtual void AssociateGuestBuffer(const UniqueBuffer&, u64 address, u64 size) {}


    /// Device limits and feature flags, filled once when the backend is created.
    [[nodiscard]] const BackendLimits& Limits() const noexcept {
        return limits;
    }

    // Transfers

    /// Copies `copies` regions from `src` to `dst`, in stream order.
    virtual void CopyBuffer(const Buffer* src, const Buffer* dst,
                            std::span<const vk::BufferCopy> copies) = 0;

    /// Fills `size` bytes of `dst` at `offset` with `value`.
    virtual void FillBuffer(const Buffer* dst, u64 offset, u64 size, u32 value) = 0;

    /// Writes the 32 bit `value` into `dst` at `offset` in stream order.
    virtual void InlineData(Buffer* dst, u64 offset, u32 value) = 0;

    /// bbport BB_GUEST_IN_PLACE: copies from the game's memory itself (the guest memory chunk
    /// that starts at physical address `chunk_phys`, host memory the CPU writes) into `dst`:
    /// uploads without a CPU copy. The copies' source offsets are relative to the chunk.
    virtual void CopyFromGuestChunk(u64 chunk_phys, const Buffer* dst,
                                    std::span<const vk::BufferCopy> copies) = 0;

    /// bbport: `data` written into `dst` by the GPU in stream order (vkCmdUpdateBuffer pieces).
    virtual void UpdateBuffer(const Buffer* dst, u64 offset, std::span<const u8> data) = 0;

    /// Copies buffer regions into the subresources of `dst`.
    virtual void UploadImage(Image* dst, const Buffer* src,
                             std::span<const vk::BufferImageCopy> upload_copies) = 0;

    /// Copies the subresources of `src` into buffer regions.
    virtual void DownloadImage(Image* src, const Buffer* dst,
                               std::span<const vk::BufferImageCopy> download_copies) = 0;

    /// Copies all of `src` into `dst`.
    virtual void CopyImage(Image* src, Image* dst) = 0;

    /// Copies all of `src` into `dst` through `buffer` at `offset` (images of different formats).
    virtual void CopyImageWithBuffer(Image* src, Image* dst, const Buffer* buffer, u64 offset) = 0;

    /// Copies one mip level and slice of `src` into `dst`.
    virtual void CopyMip(Image* src, Image* dst, u32 mip, u32 slice) = 0;

    /// Copies a color image into a depth image or the other way round.
    virtual void CopyColorAndDepth(Image* src, Image* dst) = 0;

    /// Copies the depth and stencil of `src` into `dst` for `sub_range`.
    virtual void CopyDepthStencil(Image* src, Image* dst, const SubresourceRange& sub_range) = 0;

    /// Resolves the multisampled `src` into `dst`.
    virtual void ResolveImage(Image* src, Image* dst, const SubresourceRange& src_range,
                              const SubresourceRange& dst_range) = 0;

    /// Clears `range` of `dst`.
    virtual void ClearImage(Image* dst, const SubresourceRange& range,
                            const vk::ClearValue& clear_value) = 0;

    /// Gives `image` a backing image with `num_samples` samples (copying the old one if asked).
    virtual void SetBackingSamples(Image* image, u32 num_samples, bool copy_backing = true) = 0;

    /// bbport (P10.1, used with BackendLimits::lean_image_usage): what `image` has to gain, beyond
    /// what its current backing has, for the backend to make a view as `view` describes (and to be
    /// written by a shader when the view is a storage view). None when the view needs nothing.
    [[nodiscard]] virtual ImageUsagePromotion RequiredViewUsage(const Image& image,
                                                                const ImageViewInfo& view) const {
        return ImageUsagePromotion::None;
    }

    /// bbport (P10.1): gives `image` a backing with the extra usage (the contents carried over,
    /// the old backing and its views retired: the caller must not hold views of the image across
    /// the call). A no-op when the backing has it, and for a backend whose images always have it.
    virtual void PromoteImageUsage(Image* image, ImageUsagePromotion usage) {}

    // Access tracking

    /// Records that `handle` [offset, offset + size) was accessed by `src_stage` with `src_access`.
    virtual void AccessBuffer(const Buffer* handle, u64 offset, u64 size,
                              vk::PipelineStageFlags2 src_stage,
                              vk::AccessFlags2 src_access) = 0;

    /// Whether an access of the range is still pending (a barrier is needed before using it).
    virtual bool IsBufferAccessed(const Buffer* handle, u64 offset, u64 size,
                                  bool check_read_access = false) = 0;

    /// bbport BB_LAYER_MEMORY: a shader reached memory through the page table (any buffer of the
    /// game's memory). A write there makes every later access wait for a barrier.
    virtual void AccessGlobal(vk::PipelineStageFlags2 src_stage, vk::AccessFlags2 src_access) = 0;

    /// Transitions `image` (or `subres_range` of it) for the given use; true when it changed.
    virtual bool Transit(Image* image, vk::ImageLayout dst_layout,
                         vk::PipelineStageFlags2 dst_stage, vk::AccessFlags2 dst_access,
                         std::optional<SubresourceRange> subres_range = {}) = 0;

    /// Records the pending barriers (ends the render pass).
    virtual void FlushBarriers() = 0;

    /// bbport BB_HOIST_DISPATCH: records the pending image barriers only (a hoisted dispatch's
    /// layout changes, before the held render pass), without ending the render pass.
    virtual void FlushImageBarriers() = 0;

    /// bbport: runs before this thread changes image state (layouts, pending image
    /// barriers): the rasterizer joins its texture binding helper there (BindHelper).
    virtual void BeforeImageAccess() = 0;

    /// bbport: image barriers are waiting for the next FlushBarriers or FlushImageBarriers (a
    /// backend that only keeps layouts as bookkeeping has none).
    [[nodiscard]] virtual bool HasPendingImageBarriers() const noexcept {
        return false;
    }

    // Staging

    /// Requests `size` bytes of memory of the given type. A deferred request stays held until
    /// StagingFreeDeferred.
    [[nodiscard]] virtual StagingSpan StagingRequest(u64 size, MemoryType type, u64 alignment = 0,
                                                     bool deferred = false) = 0;

    /// Releases a deferred allocation. It becomes reusable once the current tick completes.
    virtual void StagingFreeDeferred(const StagingSpan& span) = 0;

    // Ticks and ordering

    /// The tick of the submission being recorded (monotonically increasing).
    [[nodiscard]] virtual u64 CurrentTick() const noexcept = 0;

    /// Once per frame (the end of a guest submission): the staging memory ages here.
    virtual void TickFrame() {}

    /// Whether the GPU finished everything up to `tick` (refreshes the known tick if it was not).
    [[nodiscard]] virtual bool IsFree(u64 tick) noexcept = 0;

    /// Whether the GPU is known to have finished everything up to `tick`, without refreshing.
    [[nodiscard]] virtual bool IsFreeKnown(u64 tick) const noexcept = 0;

    /// Refreshes the known GPU tick.
    virtual void RefreshTicks() = 0;

    /// Waits for the given tick to trigger on the GPU.
    virtual void Wait(u64 tick) = 0;

    /// Sends the current execution context to the GPU and waits for it to complete.
    virtual void Finish() = 0;

    /// Runs `func` once the GPU is done with the current tick (or at PopPendingOperations).
    virtual void DeferOperation(Common::UniqueFunction<void>&& func) = 0;

    /// As DeferOperation, but runs on a priority thread as soon as the tick is done.
    virtual void DeferPriorityOperation(Common::UniqueFunction<void>&& func) = 0;

    /// Attempts to execute operations whose tick the GPU has caught up with.
    virtual void PopPendingOperations() = 0;

    /// Ends the current rendering scope.
    virtual void EndRendering() = 0;

    /// bbport: a render pass instance is open in the command stream.
    [[nodiscard]] virtual bool IsRendering() const noexcept = 0;

    /// True when recorded commands are deferred (host copies must then be recorded in order).
    [[nodiscard]] virtual bool IsRecordingDeferred() const noexcept = 0;

    /// bbport: guest memory [address, address + size) a host copy issued now will read (the
    /// source of a deferred upload or rename).
    virtual void NoteHostCopySource(u64 address, u64 size) = 0;

    /// Runs `item.run(item)` on a recording thread in order with the commands and the other host
    /// copies; the submission waits for it.
    virtual void RecordHostCopy(const BbCopy::Item& item) = 0;

    // Queries

    /// The format to use for `format` on this device given the needed `features`.
    [[nodiscard]] virtual vk::Format SupportedFormat(vk::Format format,
                                                     vk::FormatFeatureFlags2 features) const = 0;

    /// The amount of device memory in use (valid with Limits().can_report_memory_usage).
    [[nodiscard]] virtual u64 DeviceMemoryUsage() const = 0;

    /// bbport: the driver's current budget of the heaps DeviceMemoryUsage counts.
    [[nodiscard]] virtual u64 DeviceMemoryBudgetNow() const = 0;

    // The caches' own GPU work. These end the current rendering scope themselves where needed;
    // the caches order them with AccessBuffer and FlushBarriers as for any other GPU access.

    /// Records one tiling or detiling dispatch (compiling the kernel of `dispatch.key` on first
    /// use). The buffers are read and written by compute shaders; the caller has flushed the
    /// barriers before and records the accesses after.
    virtual void DispatchTiling(const TilingDispatch& dispatch) = 0;

    /// Optional fused texture-to-guest tiler. False leaves the caller's original staging path.
    virtual bool DownloadTiledImage(Image* image, const TilingDispatch& dispatch) { return false; }
    // Claim precedes EVERY user hook, stream copy and staging request. A failed
    // prepare still consumes the attempt. CanPrepare is a predicate, not a claim.
    virtual std::unique_ptr<ExactImageExportAttempt> ClaimExactImageExportAttempt() { return {}; }
    virtual bool CanPrepareExactImageExport(const Image&) const { return false; }
    virtual std::unique_ptr<PreparedExactImageExport> PrepareExactImageExport(
        std::unique_ptr<ExactImageExportAttempt>, Image*, const Buffer*, u64,
        std::span<const vk::BufferImageCopy>, std::span<const u8>,
        const std::function<void()>& release_input_lease = {}) { return {}; }
    // Managed image invalidation/retirement notifies tokens BEFORE slot reuse.
    virtual void InvalidatePreparedImageExport(u64) noexcept {}

    /// Creates the fault buffer parser: a kernel for fault bitmaps of 1 bit per page of
    /// `1 << page_bits` bytes that lists at most `max_faults` addresses. Called once, before the
    /// first ParseFaultBuffer.
    virtual void CreateFaultParser(u32 page_bits, u32 max_faults) = 0;

    /// Records the fault buffer parser (ends the rendering scope) with barriers before and after
    /// on the fault buffer: earlier shader writes to it are visible to the parser, and the
    /// parser's writes are visible to everything after.
    virtual void ParseFaultBuffer(const FaultParseDispatch& dispatch) = 0;

    // Objects. The Create methods fill the object and set its `owner` to this backend; the object
    // calls the matching Destroy method when it goes away (never for an object that holds nothing).

    /// Creates a buffer as described by `info` (size, usage) in memory of the given type. A buffer
    /// with the shader device address usage gets its address in `out.bda_addr`.
    virtual BufferMemory CreateBuffer(UniqueBuffer& out, const vk::BufferCreateInfo& info,
                                      MemoryType type) = 0;

    /// bbport BB_GUEST_IN_PLACE: a buffer over the game's memory itself (the guest memory chunk
    /// that starts at physical address `chunk_phys`, `size` bytes): transfers and texel/storage
    /// reads, a device address only with BB_LAYER_MEMORY. Never needs Flush and Invalidate.
    virtual BufferMemory ImportBuffer(UniqueBuffer& out, u64 size, u64 chunk_phys) = 0;

    /// Frees the buffer. The caller makes sure the GPU is done with it.
    virtual void DestroyBuffer(UniqueBuffer& buffer) = 0;

    /// Makes host writes in the range of the (host visible, non coherent) buffer visible to the
    /// device.
    virtual void FlushBuffer(const UniqueBuffer& buffer, u64 offset, u64 size) = 0;

    /// Makes device writes in the range of the (host visible, non coherent) buffer visible to the
    /// host.
    virtual void InvalidateBuffer(const UniqueBuffer& buffer, u64 offset, u64 size) = 0;

    /// Creates an image as described by `info` (device local; `out.image_ci` and `out.size_bytes`
    /// are filled).
    virtual void CreateImage(UniqueImage& out, const vk::ImageCreateInfo& info) = 0;

    /// Frees the image. The caller makes sure the GPU is done with it.
    virtual void DestroyImage(UniqueImage& image) = 0;

    /// The sample counts an image described by `info` (flags, type, format, tiling, usage) can have.
    [[nodiscard]] virtual vk::SampleCountFlags ImageSampleCounts(
        const vk::ImageCreateInfo& info) const = 0;

    /// Creates a view of the image `info.image` (usage and minimum LOD extensions are chained).
    virtual void CreateImageView(ImageView& out, const vk::ImageViewCreateInfo& info) = 0;

    /// Frees the view.
    virtual void DestroyImageView(ImageView& view) = 0;

    /// Creates a sampler as described by `info` (a custom border color is chained).
    virtual void CreateSampler(Sampler& out, const vk::SamplerCreateInfo& info) = 0;

    /// Frees the sampler.
    virtual void DestroySampler(Sampler& sampler) = 0;

    /// Changes for any recorded buffer/global write, even across barriers. Zero: unsupported.
    /// Used only to invalidate bounded same-packet read snapshots, never to infer write ranges.
    virtual u64 BufferWriteEpoch() const noexcept { return 0; }

    // Optional memory paths (docs/metal/cache_backend_design.md, group D).

    /// bbport: whether the game's direct memory can be given to the GPU in chunks this backend
    /// registers (BB_GUEST_IN_PLACE). Asked once, before the first chunk is used.
    virtual bool GuestMemoryUsable() {
        return false;
    }

    /// The sparse arena, or null when the backend has none.
    virtual SparseArena* GetSparseArena() {
        return nullptr;
    }

    /// The fast readback path, or null when the backend has none.
    virtual FastReadback* GetFastReadback() {
        return nullptr;
    }

    // Debug names (a backend may ignore them). `name` has to be null terminated.
    virtual void SetBufferName(const UniqueBuffer& buffer, std::string_view name) = 0;
    virtual void SetImageName(const UniqueImage& image, std::string_view name) = 0;
    virtual void SetImageViewName(const ImageView& view, std::string_view name) = 0;

protected:
    explicit CacheBackend(const BackendLimits& limits_) : limits{limits_} {}

private:
    const BackendLimits limits;
};

} // namespace VideoCore
