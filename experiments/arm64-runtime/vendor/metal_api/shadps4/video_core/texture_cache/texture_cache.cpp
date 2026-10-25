#include "bb_stutter_audit.h"
#include "bb_raw_write_audit.h"
#include "bb_layout_audit.h"
#include "bbport_reclaim_mapping.h"
// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <xxhash.h>
#include <filesystem>

#include "bbport_toggles.h"
#include "bbport_sections.h"
#include "bbport_flush_reason.h"
#include "bbport_free_check.h"
#include "common/assert.h"
#include "common/debug.h"
#include "common/div_ceil.h"
#include "common/hash.h"
#include "common/scope_exit.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/cache_backend.h"
#include "video_core/page_manager.h"
#include "video_core/texture_cache/host_compatibility.h"
#include "video_core/texture_cache/texture_cache.h"
#include "video_core/frame_capture.h"
#ifdef __APPLE__
#include "video_core/renderer_metal/mtl_memory_stats.h"
#endif
#include "video_core/texture_cache/tile_manager.h"

namespace VideoCore {

static constexpr u64 PageShift = 12;
static constexpr u64 NumFramesBeforeRemoval = 32;

static void LogDimensionalReuse(const ImageInfo& requested, const Image& cached,
                                const char* path) {
    static std::atomic<u32> printed{0};
    if (printed.fetch_add(1, std::memory_order_relaxed) >= 8) return;
    LOG_WARNING(Render_Vulkan,
                "Texture dimensional reuse: path={} address={:#x} requested-type={} "
                "cached-type={} requested={}x{}x{} cached={}x{}x{} bytes={}/{} flags={:#x}",
                path, requested.guest_address, u32(requested.type), u32(cached.info.type),
                requested.size.width, requested.size.height, requested.size.depth,
                cached.info.size.width, cached.info.size.height, cached.info.size.depth,
                requested.guest_size, cached.info.guest_size, u32(cached.flags));
}

// Logical 1D/2D aliasing is safe only when every byte-layout field agrees.
// The memoized path must apply the same rule as overlap resolution.
static bool IsCrossDimensionalAlias(const ImageInfo& a, const ImageInfo& b) {
    const auto is_1d = [](AmdGpu::ImageType type) {
        return type == AmdGpu::ImageType::Color1D || type == AmdGpu::ImageType::Color1DArray;
    };
    return is_1d(a.type) != is_1d(b.type);
}

static bool SameDimensionalLayout(const ImageInfo& a, const ImageInfo& b) {
    const auto normalized_2d = [](AmdGpu::ImageType type) {
        return type == AmdGpu::ImageType::Color1D || type == AmdGpu::ImageType::Color1DArray ||
               type == AmdGpu::ImageType::Color2D || type == AmdGpu::ImageType::Color2DArray;
    };
    return normalized_2d(a.type) && normalized_2d(b.type) &&
           a.size == b.size && a.guest_size == b.guest_size &&
           a.pixel_format == b.pixel_format && a.resources == b.resources &&
           a.pitch == b.pitch && a.tile_mode == b.tile_mode && a.array_mode == b.array_mode &&
           a.bank_swizzle == b.bank_swizzle && a.alt_tile == b.alt_tile &&
           a.props.is_pow2 == b.props.is_pow2 && a.num_samples == 1 && b.num_samples == 1;
}

static bool CompatibleDimensionalLayout(const ImageInfo& a, const ImageInfo& b) {
    return !IsCrossDimensionalAlias(a, b) || SameDimensionalLayout(a, b);
}

TextureCache::TextureCache(CacheBackend& backend_, AmdGpu::Liverpool* liverpool_,
                           BufferCache& buffer_cache_, PageManager& tracker_)
    : backend{backend_}, liverpool{liverpool_},
      buffer_cache{buffer_cache_}, tracker{tracker_},
      tile_manager{backend, buffer_cache.GetStreamBuffer()},
      readback_linear_images{EmulatorSettings.IsReadbackLinearImagesEnabled()} {

    u32 max_samplers = backend.Limits().max_sampler_allocation_count;
    trigger_gc_samplers = max_samplers * 3 / 4;
    pressure_gc_samplers = max_samplers * 7 / 8;
    critical_gc_samplers = max_samplers * 15 / 16;

    // Set up garbage collection parameters.
    if (!backend.Limits().can_report_memory_usage) {
        trigger_gc_memory = 0;
        pressure_gc_memory = DEFAULT_PRESSURE_GC_MEMORY;
        critical_gc_memory = DEFAULT_CRITICAL_GC_MEMORY;
        return;
    }

    const s64 device_local_memory = static_cast<s64>(backend.Limits().total_memory_budget);
    const s64 min_spacing_expected = device_local_memory - 1_GB;
    const s64 min_spacing_critical = device_local_memory - 512_MB;
    const s64 mem_threshold = std::min<s64>(device_local_memory, TARGET_GC_THRESHOLD);
    const s64 min_vacancy_expected = (6 * mem_threshold) / 10;
    const s64 min_vacancy_critical = (2 * mem_threshold) / 10;
    pressure_gc_memory = static_cast<u64>(
        std::max<u64>(std::min(device_local_memory - min_vacancy_expected, min_spacing_expected),
                      DEFAULT_PRESSURE_GC_MEMORY));
    critical_gc_memory = static_cast<u64>(
        std::max<u64>(std::min(device_local_memory - min_vacancy_critical, min_spacing_critical),
                      DEFAULT_CRITICAL_GC_MEMORY));
    trigger_gc_memory = static_cast<u64>((device_local_memory - mem_threshold) / 2);
}

TextureCache::~TextureCache() = default;

std::string TextureCache::DescribeImagesIn(VAddr addr, u64 size) {
    std::unique_lock lock{mutex, std::try_to_lock}; // the caller may hold it already
    if (!lock.owns_lock()) {
        return " [images: not looked up]";
    }
    std::string out;
    int shown = 0;
    ForEachImageInRegion(addr, size, [&](ImageId, Image& image) {
        if (shown++ < 3) {
            out += fmt::format(" [image {:#x}+{:#x} {}x{} fmt {} tile {}{}{}]", image.info.guest_address,
                               image.info.guest_size, image.info.size.width,
                               image.info.size.height, u32(image.info.pixel_format),
                               u32(image.info.tile_mode), image.usage.render_target ? " RT" : "",
                               True(image.flags & ImageFlagBits::GpuModified) ? " gpu" : "");
        }
    });
    if (shown > 3) {
        out += fmt::format(" +{} more", shown - 3);
    }
    return out;
}

void TextureCache::ProcessDownloadImages() {
    // bbport: called for every fence (~450 a frame): the lock only when something is queued.
    if (!downloads_queued.load(std::memory_order_acquire)) {
        return;
    }
    std::unique_lock lk{download_images_mutex};
    downloads_queued.store(false, std::memory_order_relaxed);
    for (const ImageId image_id : download_images) {
        DownloadImageMemory(image_id, true);
    }
    download_images.clear();
}

void TextureCache::DownloadImageMemory(ImageId image_id, bool sync) {
    Image& image = slot_images[image_id];
    if (False(image.flags & ImageFlagBits::GpuModified)) {
        return;
    }
    const u32 download_size = image.info.pitch * image.info.size.height * image.info.size.depth *
                              image.info.resources.layers * (image.info.num_bits / 8);
    ASSERT(download_size <= image.info.guest_size);
    vk::BufferImageCopy image_copy = {
        .bufferOffset = 0,
        .bufferRowLength = image.info.pitch,
        .bufferImageHeight = image.info.size.height,
        .imageSubresource =
            {
                .aspectMask = image.info.props.is_depth ? vk::ImageAspectFlagBits::eDepth
                                                        : vk::ImageAspectFlagBits::eColor,
                .mipLevel = 0,
                .baseArrayLayer = 0,
                .layerCount = image.info.resources.layers,
            },
        .imageOffset = {0, 0, 0},
        .imageExtent = {image.info.size.width, image.info.size.height, image.info.size.depth},
    };
    // bbport BB_GUEST_IN_PLACE: the GPU copies the image into the game's memory itself, in stream
    // order: before the fences that follow, no wait here and no CPU copy afterwards.
    // Where the memory has a VRAM copy the GPU copies into that copy as well: buffers bound over
    // it read the image's data in stream order (issue #122: motion blur read a render target as a
    // buffer, 0x104ff00000, from a VRAM copy refreshed only once the CPU copy below landed, frames
    // later: the frame smeared while the camera turned). The game's memory still gets it below.
    if (GuestInPlace() && image.info.guest_address != 0) {
        const auto [arena, offset] =
            buffer_cache.ObtainBuffer(image.info.guest_address, download_size, true);
        image_copy.bufferOffset = offset;
        backend.DownloadImage(&image, arena, std::span{&image_copy, 1});
        if (buffer_cache.IsInPlace(image.info.guest_address, download_size)) {
            return;
        }
    }
    const auto download =
        backend.StagingRequest(download_size, MemoryType::HostCached, 16, !sync);
    const vk::BufferImageCopy image_download = {
        .bufferOffset = download.offset,
        .bufferRowLength = image.info.pitch,
        .bufferImageHeight = image.info.size.height,
        .imageSubresource =
            {
                .aspectMask = image.info.props.is_depth ? vk::ImageAspectFlagBits::eDepth
                                                        : vk::ImageAspectFlagBits::eColor,
                .mipLevel = 0,
                .baseArrayLayer = 0,
                .layerCount = image.info.resources.layers,
            },
        .imageOffset = {0, 0, 0},
        .imageExtent = {image.info.size.width, image.info.size.height, image.info.size.depth},
    };
    backend.DownloadImage(&image, download.buffer, std::span{&image_download, 1});
    if (sync) {
        {
            BbFlushReason::Scope reason{BbFlushReason::Reason::DownloadImage};
            backend.Finish();
        }
        download.Invalidate();
        BbFreeCheck::Check(image.info.guest_address, download_size, download.mapped,
                           BbFreeCheck::ImageDownload);
        Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(image.info.guest_address),
                                                  download.mapped, download_size);
        if (!WriteTracking()) {
            buffer_cache.InvalidateMemory(image.info.guest_address, download_size);
        }
    } else {
        backend.DeferPriorityOperation(
            [this, device_addr = image.info.guest_address, download, download_size] {
                download.Invalidate();
                BbFreeCheck::Check(device_addr, download_size, download.mapped,
                                   BbFreeCheck::ImageDownload);
                Core::Memory::Instance()->TryWriteBacking(std::bit_cast<u8*>(device_addr),
                                                          download.mapped, download_size);
                if (!WriteTracking()) {
                    buffer_cache.InvalidateMemory(device_addr, download_size);
                }
                backend.StagingFreeDeferred(download);
            });
    }
}

void TextureCache::MarkAsMaybeDirty(ImageId image_id, Image& image) {
    if (image.hash == 0) {
        // Initialize hash
        const u8* addr = std::bit_cast<u8*>(image.info.guest_address);
        image.hash = XXH3_64bits(addr, image.info.guest_size);
    }
    image.flags |= ImageFlagBits::MaybeCpuDirty;
    UntrackImage(image_id);
}

void TextureCache::InvalidateMemory(VAddr addr, size_t size) {
    std::scoped_lock lock{mutex};
    const auto pages_start = PageManager::GetPageAddr(addr);
    const auto pages_end = PageManager::GetNextPageAddr(addr + size - 1);
    ForEachImageInRegion(pages_start, pages_end - pages_start, [&](ImageId image_id, Image& image) {
        const auto image_begin = image.info.guest_address;
        const auto image_end = image.info.guest_address + image.info.guest_size;
        if (image.Overlaps(addr, size)) {
            // Modified region overlaps image, so the image was definitely accessed by this fault.
            // Untrack the image, so that the range is unprotected and the guest can write freely.
            image.flags |= ImageFlagBits::CpuDirty;
            PageManager::NoteImageFault(image.info.guest_address, image.info.guest_size,
                                        image.info.size.width, image.info.size.height,
                                        u32(image.info.pixel_format), u32(image.info.tile_mode));
            UntrackImage(image_id);
        } else if (pages_end < image_end) {
            // This page access may or may not modify the image.
            // We should not mark it as dirty now. If it really was modified
            // it will receive more invalidations on its other pages.
            // Remove tracking from this page only.
            UntrackImageHead(image_id);
        } else if (image_begin < pages_start) {
            // This page access does not modify the image but the page should be untracked.
            // We should not mark this image as dirty now. If it really was modified
            // it will receive more invalidations on its other pages.
            UntrackImageTail(image_id);
        } else {
            // Image begins and ends on this page so it can not receive any more invalidations.
            // We will check it's hash later to see if it really was modified.
            MarkAsMaybeDirty(image_id, image);
        }
    });
}

// bbport BB_IMAGE_WATCH=addr (diagnostics): how the image at that address is used (first 300).
void TextureCache::WatchImage(const Image& image, const char* what) {
    static const u64 watched = [] {
        const char* env = std::getenv("BB_IMAGE_WATCH");
        return env ? std::strtoull(env, nullptr, 0) : 0ull;
    }();
    if (!watched || image.info.guest_address != watched) {
        return;
    }
    static std::atomic<u32> printed{0};
    if (printed.fetch_add(1) < 300) {
        std::printf("Image watch %#llx: %s, flags %#x\n", (unsigned long long)watched, what,
                    u32(image.flags));
    }
}

void TextureCache::InvalidateMemoryFromGPU(VAddr address, size_t max_size) {
    std::scoped_lock lock{mutex};
    if (const auto probe = BbRawWriteAudit::Begin()) {
        unsigned inspected = 0;
        if (BbRawWriteAudit::Range(address, max_size)) {
            ForEachImageInRegion(address, max_size ? max_size : 1,
                BbRawWriteAudit::BoundedVisitor{inspected, [&](ImageId id, Image& image) {
                    const auto owner = guest_image_owners.find(id.index);
                    const u64 allocation = owner == guest_image_owners.end() ? 0 : owner->second.generation;
                    BbRawWriteAudit::Observe(probe, address, max_size, image,
                        registry_generation.load(std::memory_order_relaxed), allocation,
                        backend.CurrentTick());
                    }});
        }
        BbRawWriteAudit::End(probe, inspected);
    }

    // bbport: only an image starting at `address` is affected, so the images over its first byte
    // are all there is to look at (the whole range was walked for every GPU write binding).
    ForEachImageInRegion(address, 1, [&](ImageId image_id, Image& image) {
        static const bool log = std::getenv("BB_IMAGE_SYNC_LOG") != nullptr;
        if (log) {
            static std::atomic<u32> printed{0};
            if (printed.fetch_add(1) < 200) {
                std::printf("Image sync: GPU write %#llx+%#llx over image %#llx %ux%u fmt %u flags %#x%s\n",
                            (unsigned long long)address, (unsigned long long)max_size,
                            (unsigned long long)image.info.guest_address, image.info.size.width,
                            image.info.size.height, u32(image.info.pixel_format), u32(image.flags),
                            image.info.guest_address == address ? "" : " (not at its start: kept)");
            }
        }
        // Only consider images that match base address.
        // TODO: Maybe also consider subresources
        if (image.info.guest_address != address) {
            return;
        }
        // Ensure image is reuploaded when accessed again.
        WatchImage(image, "GPU write (marked for refresh)");
        image.flags |= ImageFlagBits::GpuDirty;
    });
}

void TextureCache::UnmapMemory(VAddr cpu_addr, size_t size) {
    std::scoped_lock lk{mutex};

    ImageIds deleted_images;
    ForEachImageInRegion(cpu_addr, size, [&](ImageId id, Image&) { deleted_images.push_back(id); });
    for (const ImageId id : deleted_images) {
        // TODO: Download image data back to host.
        FreeImage(id);
    }
}

ImageId TextureCache::ResolveDepthOverlap(const ImageInfo& requested_info, BindingType binding,
                                          ImageId cache_image_id) {
    auto& cache_image = slot_images[cache_image_id];

    if (!cache_image.info.props.is_depth && !requested_info.props.is_depth) {
        return {};
    }

    const bool stencil_match =
        requested_info.props.has_stencil == cache_image.info.props.has_stencil;
    const bool bpp_match = requested_info.num_bits == cache_image.info.num_bits;

    // If an image in the cache has less slices we need to expand it
    bool recreate = cache_image.info.resources < requested_info.resources;

    switch (binding) {
    case BindingType::Texture:
        // The guest requires a depth sampled texture, but cache can offer only Rxf. Need to
        // recreate the image.
        recreate |= requested_info.props.is_depth && !cache_image.info.props.is_depth;
        break;
    case BindingType::Storage:
        // If the guest is going to use previously created depth as storage, the image needs to be
        // recreated. (TODO: Probably a case with linear rgba8 aliasing is legit)
        recreate |= cache_image.info.props.is_depth;
        break;
    case BindingType::RenderTarget:
        // Render target can have only Rxf format. If the cache contains only Dx[S8] we need to
        // re-create the image.
        ASSERT(!requested_info.props.is_depth);
        recreate |= cache_image.info.props.is_depth;
        break;
    case BindingType::DepthTarget:
        // The guest has requested previously allocated texture to be bound as a depth target.
        // In this case we need to convert Rx float to a Dx[S8] as requested
        recreate |= !cache_image.info.props.is_depth;

        // The guest is trying to bind a depth target and cache has it. Need to be sure that aspects
        // and bpp match
        recreate |= cache_image.info.props.is_depth && !(stencil_match && bpp_match);
        break;
    default:
        break;
    }

    if (recreate) {
        auto new_info = requested_info;
        new_info.resources = std::max(requested_info.resources, cache_image.info.resources);
        new_info.UpdateSize();
        const auto new_image_id = slot_images.insert(backend, slot_image_views, new_info);
        RegisterImage(new_image_id);

        // Inherit image usage
        auto& new_image = slot_images[new_image_id];
        new_image.usage = cache_image.usage;
        new_image.flags &= ~ImageFlagBits::Dirty;
        // When creating a depth buffer through overlap resolution don't clear it on first use.
        new_image.info.meta_info.htile_clear_mask = 0;
        backend.CopyColorAndDepth(&cache_image, &new_image);

        // Free the cache image.
        FreeImage(cache_image_id);
        return new_image_id;
    }

    // Will be handled by view
    return cache_image_id;
}

std::tuple<ImageId, int, int> TextureCache::ResolveOverlap(const ImageInfo& image_info,
                                                           BindingType binding,
                                                           ImageId cache_image_id,
                                                           ImageId merged_image_id) {
    auto& cache_image = slot_images[cache_image_id];
    const bool safe_to_delete =
        backend.CurrentTick() - cache_image.tick_accessed_last > NumFramesBeforeRemoval;

    // Equal address
    if (image_info.guest_address == cache_image.info.guest_address) {
        // Both logical dimensions now have 2D backing. Reuse only an identical
        // layout; retain adapter 0039's protection against unrelated address reuse.
        if (IsCrossDimensionalAlias(image_info, cache_image.info)) {
            const bool same_layout = SameDimensionalLayout(image_info, cache_image.info);
            LogDimensionalReuse(image_info, cache_image,
                                same_layout ? "overlap-normalized-2d" : "overlap-reject");
            return {same_layout ? cache_image_id : merged_image_id, -1, -1};
        }
        const u32 lhs_block_size = image_info.num_bits * image_info.num_samples;
        const u32 rhs_block_size = cache_image.info.num_bits * cache_image.info.num_samples;
        if (image_info.BlockDim() != cache_image.info.BlockDim() ||
            lhs_block_size != rhs_block_size) {
            // Very likely this kind of overlap is caused by allocation from a pool.
            if (safe_to_delete) {
                FreeImage(cache_image_id);
            }
            return {merged_image_id, -1, -1};
        }

        if (const auto depth_image_id = ResolveDepthOverlap(image_info, binding, cache_image_id)) {
            return {depth_image_id, -1, -1};
        }

        // Compressed view of uncompressed image with same block size.
        if (image_info.props.is_block && !cache_image.info.props.is_block) {
            return {ExpandImage(image_info, cache_image_id), -1, -1};
        }

        if (image_info.guest_size == cache_image.info.guest_size &&
            (image_info.type == AmdGpu::ImageType::Color3D ||
             cache_image.info.type == AmdGpu::ImageType::Color3D)) {
            return {ExpandImage(image_info, cache_image_id), -1, -1};
        }

        const bool pow2_padding_only =
            image_info.props.is_pow2 != cache_image.info.props.is_pow2 &&
            image_info.tile_mode == cache_image.info.tile_mode &&
            image_info.size == cache_image.info.size &&
            image_info.pitch == cache_image.info.pitch && image_info.resources.levels == 1 &&
            cache_image.info.resources.levels == 1 && image_info.resources.layers == 1 &&
            cache_image.info.resources.layers == 1;

        // Size and resources are less than or equal, use image view.
        if (image_info.pixel_format != cache_image.info.pixel_format ||
            image_info.guest_size <= cache_image.info.guest_size || pow2_padding_only) {
            auto result_id = merged_image_id ? merged_image_id : cache_image_id;
            const auto& result_image = slot_images[result_id];
            const bool is_compatible =
                IsVulkanFormatCompatible(result_image.info.pixel_format, image_info.pixel_format);
            return {is_compatible ? result_id : ImageId{}, -1, -1};
        }

        // Size and resources are greater, expand the image.
        if (image_info.type == cache_image.info.type &&
            image_info.resources > cache_image.info.resources) {
            return {ExpandImage(image_info, cache_image_id), -1, -1};
        }

        // Size is greater but resources are not, because the tiling mode is different.
        // Likely the address is reused for a image with a different tiling mode.
        if (image_info.tile_mode != cache_image.info.tile_mode) {
            if (safe_to_delete) {
                FreeImage(cache_image_id);
            }
            return {merged_image_id, -1, -1};
        }

        // Enhanced debug logging for unreachable case
        // Calculate expected size based on format and dimensions
        u64 expected_size =
            (static_cast<u64>(image_info.size.width) * static_cast<u64>(image_info.size.height) *
             static_cast<u64>(image_info.size.depth) * static_cast<u64>(image_info.num_bits) / 8);
        LOG_ERROR(Render_Vulkan,
                  "Unresolvable image overlap with equal memory address:\n"
                  "=== OLD IMAGE (cached) ===\n"
                  "  Address:        {:#x}\n"
                  "  Size:           {:#x} bytes\n"
                  "  Format:         {}\n"
                  "  Type:           {}\n"
                  "  Width:          {}\n"
                  "  Height:         {}\n"
                  "  Depth:          {}\n"
                  "  Pitch:          {}\n"
                  "  Mip levels:     {}\n"
                  "  Array layers:   {}\n"
                  "  Samples:        {}\n"
                  "  Tile mode:      {:#x}\n"
                  "  Block size:     {} bits\n"
                  "  Is block-comp:  {}\n"
                  "  Guest size:     {:#x}\n"
                  "  Last accessed:  tick {}\n"
                  "  Safe to delete: {}\n"
                  "  isPow2:         {}\n"
                  "  Alt tile:       {}\n"
                  "\n"
                  "=== NEW IMAGE (requested) ===\n"
                  "  Address:        {:#x}\n"
                  "  Size:           {:#x} bytes\n"
                  "  Format:         {}\n"
                  "  Type:           {}\n"
                  "  Width:          {}\n"
                  "  Height:         {}\n"
                  "  Depth:          {}\n"
                  "  Pitch:          {}\n"
                  "  Mip levels:     {}\n"
                  "  Array layers:   {}\n"
                  "  Samples:        {}\n"
                  "  Tile mode:      {:#x}\n"
                  "  Block size:     {} bits\n"
                  "  Is block-comp:  {}\n"
                  "  Guest size:     {:#x}\n"
                  "  isPow2:         {}\n"
                  "  Alt tile:       {}\n"
                  "\n"
                  "=== COMPARISON ===\n"
                  "  Same format:           {}\n"
                  "  Same type:             {}\n"
                  "  Same tile mode:        {}\n"
                  "  Same block size:       {}\n"
                  "  Same BlockDim:         {}\n"
                  "  Same pitch:            {}\n"
                  "  Same pow2:             {}\n"
                  "  Same alt tile:         {}\n"
                  "  Old resources <= new:  {} (old: {}, new: {})\n"
                  "  Old size <= new size:  {}\n"
                  "  Expected size (calc):  {} bytes\n"
                  "  Size ratio (new/expected): {:.2f}x\n"
                  "  Size ratio (new/old):  {:.2f}x\n"
                  "  Old vs expected diff:  {} bytes ({:+.2f}%)\n"
                  "  New vs expected diff:  {} bytes ({:+.2f}%)\n"
                  "  Merged image ID:       {}\n"
                  "  Binding type:          {}\n"
                  "  Current tick:          {}\n"
                  "  Age (ticks since last access): {}",

                  // Old image details
                  cache_image.info.guest_address, cache_image.info.guest_size,
                  vk::to_string(cache_image.info.pixel_format),
                  static_cast<int>(cache_image.info.type), cache_image.info.size.width,
                  cache_image.info.size.height, cache_image.info.size.depth, cache_image.info.pitch,
                  cache_image.info.resources.levels, cache_image.info.resources.layers,
                  cache_image.info.num_samples, static_cast<u32>(cache_image.info.tile_mode),
                  cache_image.info.num_bits, +cache_image.info.props.is_block,
                  cache_image.info.guest_size, cache_image.tick_accessed_last, safe_to_delete,
                  bool(cache_image.info.props.is_pow2), cache_image.info.alt_tile,

                  // New image details
                  image_info.guest_address, image_info.guest_size,
                  vk::to_string(image_info.pixel_format), static_cast<int>(image_info.type),
                  image_info.size.width, image_info.size.height, image_info.size.depth,
                  image_info.pitch, image_info.resources.levels, image_info.resources.layers,
                  image_info.num_samples, static_cast<u32>(image_info.tile_mode),
                  image_info.num_bits, image_info.props.is_block, image_info.guest_size,
                  bool(image_info.props.is_pow2), image_info.alt_tile,

                  // Comparison
                  (image_info.pixel_format == cache_image.info.pixel_format),
                  (image_info.type == cache_image.info.type),
                  (image_info.tile_mode == cache_image.info.tile_mode),
                  (image_info.num_bits == cache_image.info.num_bits),
                  (image_info.BlockDim() == cache_image.info.BlockDim()),
                  (image_info.pitch == cache_image.info.pitch),
                  (image_info.props.is_pow2 == cache_image.info.props.is_pow2),
                  (image_info.alt_tile == cache_image.info.alt_tile),
                  (cache_image.info.resources <= image_info.resources),
                  cache_image.info.resources.levels, image_info.resources.levels,
                  (cache_image.info.guest_size <= image_info.guest_size), expected_size,

                  // Size ratios
                  static_cast<double>(image_info.guest_size) / expected_size,
                  static_cast<double>(image_info.guest_size) / cache_image.info.guest_size,

                  // Difference between actual and expected sizes with percentages
                  static_cast<s64>(cache_image.info.guest_size) - static_cast<s64>(expected_size),
                  (static_cast<double>(cache_image.info.guest_size) / expected_size - 1.0) * 100.0,

                  static_cast<s64>(image_info.guest_size) - static_cast<s64>(expected_size),
                  (static_cast<double>(image_info.guest_size) / expected_size - 1.0) * 100.0,

                  merged_image_id.index, static_cast<int>(binding), backend.CurrentTick(),
                  backend.CurrentTick() - cache_image.tick_accessed_last);

        UNREACHABLE_MSG("Encountered unresolvable image overlap with equal memory address.");
    }

    // Right overlap, the image requested is a possible subresource of the image from cache.
    if (image_info.guest_address > cache_image.info.guest_address) {
        if (auto mip = image_info.MipOf(cache_image.info); mip >= 0) {
            if (auto slice = image_info.SliceOf(cache_image.info, mip); slice >= 0) {
                return {cache_image_id, mip, slice};
            }
        }

        // Image isn't a subresource but a chance overlap.
        if (safe_to_delete) {
            FreeImage(cache_image_id);
        }

        return {{}, -1, -1};
    } else {
        // Left overlap, the image from cache is a possible subresource of the image requested
        if (auto mip = cache_image.info.MipOf(image_info); mip >= 0) {
            if (auto slice = cache_image.info.SliceOf(image_info, mip); slice >= 0) {
                // We have a larger image created and a separate one, representing a subres of it
                // bound as render target. In this case we need to rebind render target.
                if (cache_image.binding.is_target) {
                    cache_image.binding.needs_rebind = 1u;
                    if (merged_image_id) {
                        GetImage(merged_image_id).binding.is_target = 1u;
                    }

                    FreeImage(cache_image_id);
                    return {merged_image_id, -1, -1};
                }

                // We need to have a larger, already allocated image to copy this one into
                if (merged_image_id) {
                    auto& merged_image = slot_images[merged_image_id];
                    backend.CopyMip(&cache_image, &merged_image, mip, slice);
                    FreeImage(cache_image_id);
                }
            }
        }
    }

    return {merged_image_id, -1, -1};
}

ImageId TextureCache::ExpandImage(const ImageInfo& info, ImageId image_id) {
    const auto new_image_id = slot_images.insert(backend, slot_image_views, info);
    RegisterImage(new_image_id);

    auto& src_image = slot_images[image_id];
    auto& new_image = slot_images[new_image_id];

    RefreshImage(new_image);
    // shadPS4 #5444 (699009c9): a newly refreshed CPU image is authoritative.
    // Copy only genuine GPU contents, never a stale CPU-dirty backing. This
    // preserves CopyImage's GPU ownership propagation without changing budgets.
    const bool gpu_valid = True(src_image.flags & ImageFlagBits::GpuModified) &&
        False(src_image.flags & (ImageFlagBits::CpuDirty | ImageFlagBits::MaybeCpuDirty));
    if (gpu_valid) {
        backend.CopyImage(&src_image, &new_image);
    }
    static const bool diagnostic = std::getenv("BB_ARM_TEXTURE_DIAG") != nullptr;
    if (diagnostic) {
        static std::atomic<u32> printed{0};
        if (printed.fetch_add(1) < 128) {
            std::printf("ARM texture expand: addr=%#llx old-flags=%#x gpu-copy=%u "
                        "old=%ux%u levels=%u layers=%u new=%ux%u levels=%u layers=%u\n",
                        (unsigned long long)info.guest_address, u32(src_image.flags), gpu_valid,
                        src_image.info.size.width, src_image.info.size.height,
                        src_image.info.resources.levels, src_image.info.resources.layers,
                        info.size.width, info.size.height, info.resources.levels,
                        info.resources.layers);
        }
    }

    if (src_image.binding.is_bound || src_image.binding.is_target) {
        src_image.binding.needs_rebind = 1u;
    }

    FreeImage(image_id);
    TrackImage(new_image_id);
    return new_image_id;
}

ImageId TextureCache::FindImage(ImageDesc& desc, bool exact_fmt) {
    BbStats::find_image_calls.fetch_add(1, std::memory_order_relaxed);
    BB_SECTION(FindImage);
    const auto& info = desc.info;
    ASSERT(info.guest_address != 0);

    const auto trace_selection = [&](ImageId id, const char* path) {
        const auto& selected = slot_images[id].info;
        if (BbLayoutAudit::Enabled() && BbLayoutAudit::IsMismatch(info, selected)) {
          if (const auto event = BbLayoutAudit::Counter().Claim()) {
            const auto& image = slot_images[id];
            const auto owner = guest_image_owners.find(id.index);
            const u64 generation = owner == guest_image_owners.end() ? 0 : owner->second.generation;
            const u32 roles = u32(image.usage.texture) | (u32(image.usage.storage) << 1) |
                              (u32(image.usage.render_target) << 2) |
                              (u32(image.usage.depth_target) << 3) |
                              (u32(image.scene_proxy) << 4);
            BbLayoutAudit::Print(event, info, selected, path, image.image_uid,
                image.backing ? uintptr_t(image.backing->image.native) : 0,
                registry_generation.load(std::memory_order_relaxed), generation,
                u32(image.flags), u32(desc.type), roles, backend.CurrentTick(), image.gpu_write_tick);
          }
        }
        if (!IsViewTypeCompatible(desc.view_info.type, selected.type)) {
            LOG_ERROR(Render_Vulkan,
                      "Texture cache incompatible selection: path={} address={:#x} "
                      "requested-type={} view-type={} selected-type={} requested={}x{}x{} "
                      "selected={}x{}x{} requested-size={} selected-size={} binding={}",
                      path, info.guest_address, u32(info.type), u32(desc.view_info.type),
                      u32(selected.type), info.size.width, info.size.height, info.size.depth,
                      selected.size.width, selected.size.height, selected.size.depth,
                      info.guest_size, selected.guest_size, u32(desc.type));
        }
    };

    std::scoped_lock lock{mutex};

    u64 key_hash = info.guest_address ^ info.guest_size << 17 ^ u64(info.pixel_format) << 40 ^
                   u64(info.size.width) << 24 ^ info.size.height ^ u64(desc.type) << 58 ^
                   u64(exact_fmt) << 63;
    key_hash = (key_hash ^ key_hash >> 33) * 0xFF51AFD7ED558CCDull;
    key_hash = (key_hash ^ key_hash >> 33) * 0xC4CEB9FE1A85EC53ull;
    key_hash ^= key_hash >> 33;
    auto& cached = find_image_cache[key_hash % find_image_cache.size()];
    // An exact match stays valid while that image is registered with the same description;
    // resolved overlaps (views into other images) only until any image registration changes.
    const auto still_exact = [&] {
        if (cached.view_mip >= 0 || cached.view_slice >= 0 || !cached.image_id ||
            !slot_images.is_allocated(cached.image_id)) {
            return false;
        }
        const Image& image = slot_images[cached.image_id];
        return True(image.flags & ImageFlagBits::Registered) &&
               image.info.guest_address == info.guest_address &&
               image.info.guest_size == info.guest_size && image.info.size == info.size &&
               image.info.pixel_format == info.pixel_format &&
               CompatibleDimensionalLayout(info, image.info) &&
               IsViewTypeCompatible(desc.view_info.type, image.info.type);
    };
    if (cached.image_id && slot_images.is_allocated(cached.image_id) &&
        (cached.generation == registry_generation.load(std::memory_order_relaxed) || still_exact()) &&
        cached.address == info.guest_address &&
        cached.size == info.guest_size && cached.extent == info.size &&
        cached.format == info.pixel_format && cached.type == info.type &&
        cached.exact_fmt == exact_fmt && cached.binding == desc.type &&
        cached.levels == info.resources.levels && cached.layers == info.resources.layers &&
        CompatibleDimensionalLayout(info, slot_images[cached.image_id].info) &&
        IsViewTypeCompatible(desc.view_info.type, slot_images[cached.image_id].info.type) &&
        !BbToggle::Disabled(BbToggle::FindImageCache)) {
        Image& image = slot_images[cached.image_id];
        image.tick_accessed_last = backend.CurrentTick();
        TouchImage(image);
        if (cached.view_mip > 0) {
            desc.view_info.range.base.level = cached.view_mip;
        }
        if (cached.view_slice > 0) {
            desc.view_info.range.base.layer = cached.view_slice;
        }
        trace_selection(cached.image_id, "memo");
        return cached.image_id;
    }

    ImageIds image_ids;
    ForEachImageInRegion(info.guest_address, info.guest_size,
                         [&](ImageId image_id, Image& image) { image_ids.push_back(image_id); });

    ImageId image_id{};
    const char* selection_path = "new";

    // Check for a perfect match first
    for (const auto& cache_id : image_ids) {
        auto& cache_image = slot_images[cache_id];
        if (cache_image.info.guest_address != info.guest_address) {
            continue;
        }
        if (cache_image.info.guest_size != info.guest_size) {
            continue;
        }
        if (cache_image.info.size != info.size) {
            continue;
        }
        if (!IsVulkanFormatCompatible(cache_image.info.pixel_format, info.pixel_format) ||
            cache_image.info.type != info.type ||
            !IsViewTypeCompatible(desc.view_info.type, cache_image.info.type)) {
            if (cache_image.info.type != info.type && info.size == Extent3D{1, 1, 1}) {
                LogDimensionalReuse(info, cache_image, "exact-1x1-reject");
            }
            continue;
        }
        if (exact_fmt && info.pixel_format != cache_image.info.pixel_format) {
            continue;
        }
        image_id = cache_id;
        selection_path = "exact";
    }

    // Try to resolve overlaps (if any)
    int view_mip{-1};
    int view_slice{-1};
    if (!image_id) {
        for (const auto& cache_id : image_ids) {
            view_mip = -1;
            view_slice = -1;

            const auto& merged_info = image_id ? slot_images[image_id].info : info;
            auto [overlap_image_id, overlap_view_mip, overlap_view_slice] =
                ResolveOverlap(merged_info, desc.type, cache_id, image_id);
            if (overlap_image_id) {
                image_id = overlap_image_id;
                selection_path = "overlap";
                view_mip = overlap_view_mip;
                view_slice = overlap_view_slice;
            }
        }
    }

    if (image_id) {
        Image& image_resolved = slot_images[image_id];
        if (exact_fmt && info.pixel_format != image_resolved.info.pixel_format) {
            // Cannot reuse this image as we need the exact requested format.
            image_id = {};
        } else if (image_resolved.info.resources < info.resources) {
            // The image was clearly picked up wrong.
            FreeImage(image_id);
            image_id = {};
            LOG_WARNING(Render_Vulkan, "Image overlap resolve failed");
        }
    }
    // Create and register a new image
    if (!image_id) {
        image_id = slot_images.insert(backend, slot_image_views, info);
        RegisterImage(image_id);
    }

    Image& image = slot_images[image_id];
    image.tick_accessed_last = backend.CurrentTick();
    TouchImage(image);

    // If the image requested is a subresource of the image from cache record its location.
    if (view_mip > 0) {
        desc.view_info.range.base.level = view_mip;
    }
    if (view_slice > 0) {
        desc.view_info.range.base.layer = view_slice;
    }

    cached = FindImageCacheEntry{
        .address = info.guest_address,
        .size = info.guest_size,
        .extent = info.size,
        .format = info.pixel_format,
        .type = info.type,
        .exact_fmt = exact_fmt,
        .binding = desc.type,
        .levels = info.resources.levels,
        .layers = info.resources.layers,
        .generation = registry_generation.load(std::memory_order_relaxed),
        .image_id = image_id,
        .view_mip = view_mip,
        .view_slice = view_slice,
    };
    trace_selection(image_id, selection_path);
    return image_id;
}

ImageId TextureCache::FindImageFromRange(VAddr address, size_t size, bool ensure_valid) {
    ImageIds image_ids;
    ForEachImageInRegion(address, size, [&](ImageId image_id, Image& image) {
        if (image.info.guest_address != address) {
            return;
        }
        if (ensure_valid && !image.SafeToDownload()) {
            return;
        }
        image_ids.push_back(image_id);
    });
    if (image_ids.size() == 1) {
        // Sometimes image size might not exactly match with requested buffer size
        // If we only found 1 candidate image use it without too many questions.
        return image_ids.back();
    }
    if (!image_ids.empty()) {
        for (s32 i = 0; i < image_ids.size(); ++i) {
            Image& image = slot_images[image_ids[i]];
            if (image.info.guest_size == size) {
                return image_ids[i];
            }
        }
        LOG_WARNING(Render_Vulkan,
                    "Failed to find exact image match for copy addr={:#x}, size={:#x}", address,
                    size);
    }
    return {};
}

ImageView& TextureCache::FindTexture(ImageId image_id, const ImageDesc& desc, ViewMemo* memo,
                                     bool refresh) {
    Image& image = slot_images[image_id];
    if(FrameCapture::NativeRecording())FrameCapture::NativeBufferUse(image.image_uid,0,0,
        image.info.guest_size,image.guest_begin,7,0,desc.type==BindingType::Storage,"texture_binding");
    WatchImage(image, refresh ? "texture (refresh)" : "texture (no refresh)");
    // One bounded CPU-side atlas snapshot per explicitly armed diagnostic frame.
    // This never runs during performance measurements or creates a persistent cache.
    if (FrameCapture::NativeRecording() && std::getenv("BB_ARM_TEXTURE_DIAG") &&
        image.info.pixel_format == vk::Format::eR8Unorm &&
        image.info.size.width == 1024 && image.info.size.height == 1024 &&
        image.info.guest_size <= 2 * 1024 * 1024) {
        static std::atomic<u64> last_sequence{0};
        const auto sequence = FrameCapture::NativeCaptureSequence();
        if (last_sequence.exchange(sequence) != sequence) {
            std::vector<u8> bytes(image.info.guest_size);
            Core::Memory::Instance()->ReadBacking(image.info.guest_address, bytes.data(), bytes.size());
            const auto nonzero = std::ranges::count_if(bytes, [](u8 b) { return b != 0; });
            const auto hash = XXH3_64bits(bytes.data(), bytes.size());
            FrameCapture::Native(fmt::format("{{\"op\":\"arm_atlas_cpu\",\"address\":{},"
                "\"bytes\":{},\"nonzero\":{},\"hash\":{},\"flags\":{},\"tile\":{}}}",
                image.info.guest_address, bytes.size(), nonzero, hash, u32(image.flags),
                u32(image.info.tile_mode)));
            if (const char* dir = std::getenv("BB_CAPTURE_DIR")) {
                const auto path = fmt::format("{}/atlas-cpu-{}.bin", dir, sequence);
                if (FILE* file = std::fopen(path.c_str(), "wb")) {
                    std::fwrite(bytes.data(), 1, bytes.size(), file); std::fclose(file);
                }
            }
        }
    }

    if (desc.type == BindingType::Storage) {
        image.flags |= ImageFlagBits::GpuModified;
        image.gpu_write_tick = backend.CurrentTick();
        if (readback_linear_images && (!image.info.props.is_tiled || image.info.size.width <= 8) &&
            image.info.guest_address != 0) {
            std::unique_lock lk{download_images_mutex};
            download_images.emplace(image_id);
            downloads_queued.store(true, std::memory_order_release);
        }
        // bbport (P10.1): a storage binding of an image created without the storage usage
        // promotes it here, before the refresh (which then uploads into the new backing) and
        // before the view memo (which could return a view of the old one). FindView does the
        // same for every other view that needs a usage; a memo hit needs none, it was made by a
        // FindView of the same T# on this backing.
        image.EnsureViewUsage(desc.view_info);
    }
    if (refresh) {
        UpdateImage(image_id);
    }
    if (memo && !BbToggle::Disabled(BbToggle::TextureViewMemo)) {
        if (memo->image_id == image_id && memo->backing == image.backing && memo->view_id) {
            return slot_image_views[memo->view_id];
        }
        ImageView& view = image.FindView(desc.view_info);
        const auto& ids = image.backing->image_view_ids;
        const u32 last = image.backing->last_view;
        memo->image_id = image_id;
        memo->backing = image.backing;
        memo->view_id = last < ids.size() && &slot_image_views[ids[last]] == &view ? ids[last]
                                                                                    : ids.back();
        return view;
    }
    return image.FindView(desc.view_info);
}

ImageView& TextureCache::FindRenderTarget(ImageId image_id, const ImageDesc& desc) {
    Image& image = slot_images[image_id];
    if(FrameCapture::NativeRecording())FrameCapture::NativeBufferUse(image.image_uid,0,0,
        image.info.guest_size,image.guest_begin,7,0,true,"color_target");
    WatchImage(image, "render target");
    image.flags |= ImageFlagBits::GpuModified;
    image.gpu_write_tick = backend.CurrentTick();
    if (readback_linear_images && (!image.info.props.is_tiled || image.info.size.width <= 8)) {
        std::unique_lock lk{download_images_mutex};
        download_images.emplace(image_id);
        downloads_queued.store(true, std::memory_order_release);
    }
    image.usage.render_target = 1u;
    UpdateImage(image_id);

    // Register meta data for this color buffer
    if (desc.info.meta_info.cmask_addr) {
        surface_metas.emplace(desc.info.meta_info.cmask_addr,
                              MetaDataInfo{.type = MetaType::CMask});
        image.info.meta_info.cmask_addr = desc.info.meta_info.cmask_addr;
    }

    if (desc.info.meta_info.fmask_addr) {
        surface_metas.emplace(desc.info.meta_info.fmask_addr,
                              MetaDataInfo{.type = MetaType::FMask});
        image.info.meta_info.fmask_addr = desc.info.meta_info.fmask_addr;
    }

    return image.FindView(desc.view_info, false);
}

ImageView& TextureCache::FindDepthTarget(ImageId image_id, const ImageDesc& desc) {
    Image& image = slot_images[image_id];
    if(FrameCapture::NativeRecording())FrameCapture::NativeBufferUse(image.image_uid,0,0,
        image.info.guest_size,image.guest_begin,7,0,true,"depth_target");
    WatchImage(image, "depth target");
    image.flags |= ImageFlagBits::GpuModified;
    image.gpu_write_tick = backend.CurrentTick();
    image.usage.depth_target = 1u;
    UpdateImage(image_id);

    // Register meta data for this depth buffer
    if (desc.info.meta_info.htile_addr) {
        surface_metas.emplace(desc.info.meta_info.htile_addr,
                              MetaDataInfo{.type = MetaType::HTile,
                                           .clear_mask = image.info.meta_info.htile_clear_mask});
        image.info.meta_info.htile_addr = desc.info.meta_info.htile_addr;
    }

    // If there is a stencil attachment, link depth and stencil.
    if (desc.info.stencil_addr != 0) {
        ImageId stencil_id{};
        ForEachImageInRegion(desc.info.stencil_addr, desc.info.stencil_size,
                             [&](ImageId image_id, Image& image) {
                                 if (image.info.guest_address == desc.info.stencil_addr) {
                                     stencil_id = image_id;
                                 }
                             });
        if (!stencil_id) {
            ImageInfo info{};
            info.guest_address = desc.info.stencil_addr;
            info.guest_size = desc.info.stencil_size;
            info.size = desc.info.size;
            stencil_id = slot_images.insert(backend, slot_image_views, info);
            RegisterImage(stencil_id);
        }
        Image& stencil_image = slot_images[stencil_id];
        TouchImage(stencil_image);
        stencil_image.AssociateDepth(image_id, image.image_uid);
    }

    return image.FindView(desc.view_info, false);
}

void TextureCache::RefreshImage(Image& image) {
    WatchImage(image, "refresh entered");
    if (False(image.flags & ImageFlagBits::Dirty) || image.info.num_samples > 1) {
        return;
    }
    BbStats::Timer timer{BbStats::t_refresh};

    RENDERER_TRACE;
    TRACE_HINT(fmt::format("{:x}:{:x}", image.info.guest_address, image.info.guest_size));

    if (True(image.flags & ImageFlagBits::MaybeCpuDirty) &&
        False(image.flags & ImageFlagBits::CpuDirty)) {
        // The image size should be less than page size to be considered MaybeCpuDirty
        // So this calculation should be very uncommon and reasonably fast
        // For now we'll just check up to 64 first pixels
        const auto addr = std::bit_cast<u8*>(image.info.guest_address);
        const u32 w = std::min(image.info.size.width, u32(8));
        const u32 h = std::min(image.info.size.height, u32(8));

        const u32 s_w = image.info.props.is_block ? Common::DivCeil(w, 4u) : w;
        const u32 s_h = image.info.props.is_block ? Common::DivCeil(h, 4u) : h;
        const u32 size = s_w * s_h * (image.info.num_bits / 8);
        const u64 hash = XXH3_64bits(addr, size);
        if (image.hash == hash) {
            image.flags &= ~ImageFlagBits::MaybeCpuDirty;
            return;
        }
        image.hash = hash;
    }

    const u32 num_layers = image.info.resources.layers;
    const u32 num_mips = image.info.resources.levels;
    const bool is_gpu_modified = True(image.flags & ImageFlagBits::GpuModified);
    const bool is_gpu_dirty = True(image.flags & ImageFlagBits::GpuDirty);
    static const bool sync_log = std::getenv("BB_IMAGE_SYNC_LOG") != nullptr;
    if (sync_log && is_gpu_dirty) {
        static std::atomic<u32> printed{0};
        if (printed.fetch_add(1) < 100) {
            std::printf("Image sync: refresh of %#llx (%ux%u) from memory the GPU wrote, flags %#x\n",
                        (unsigned long long)image.info.guest_address, image.info.size.width,
                        image.info.size.height, u32(image.flags));
        }
    }

    if (sync_log) {
        // BB_IMAGE_SYNC_LOG: the images refreshed most per 5 s (any reason), with their flags.
        struct Refreshes {
            u64 count = 0, bytes = 0;
            u32 width = 0, height = 0, format = 0, tiling = 0, flags = 0;
        };
        static std::mutex refresh_mutex;
        static std::unordered_map<VAddr, Refreshes> refreshes;
        static auto window_start = std::chrono::steady_clock::now();
        std::scoped_lock lk{refresh_mutex};
        auto& r = refreshes[image.info.guest_address];
        r = {r.count + 1,
             r.bytes + image.info.guest_size,
             image.info.size.width,
             image.info.size.height,
             u32(image.info.pixel_format),
             u32(image.info.tile_mode),
             r.flags | u32(image.flags)};
        if (const auto now = std::chrono::steady_clock::now();
            now - window_start >= std::chrono::seconds(5)) {
            std::vector<std::pair<VAddr, Refreshes>> top(refreshes.begin(), refreshes.end());
            std::ranges::sort(top, std::greater{}, [](const auto& e) { return e.second.bytes; });
            std::string text;
            for (size_t i = 0; i < std::min<size_t>(top.size(), 5); ++i) {
                const auto& [address, e] = top[i];
                text += fmt::format(" {:#x} {}x{} fmt {} tile {}: {}x {:.0f} MB flags {:#x};",
                                    address, e.width, e.height, e.format, e.tiling, e.count,
                                    e.bytes / 1e6, e.flags);
            }
            std::printf("Image refreshes (5 s, %zu images):%s\n", refreshes.size(), text.c_str());
            refreshes.clear();
            window_start = now;
        }
    }
    BbStats::image_upload_bytes.fetch_add(image.info.guest_size, std::memory_order_relaxed);
    boost::container::small_vector<vk::BufferImageCopy, 14> image_copies;
    for (u32 m = 0; m < num_mips; m++) {
        const u32 width = std::max(image.info.size.width >> m, 1u);
        const u32 height = std::max(image.info.size.height >> m, 1u);
        const u32 depth =
            image.info.props.is_volume ? std::max(image.info.size.depth >> m, 1u) : 1u;
        const auto [mip_size, mip_pitch, mip_height, mip_offset] = image.info.mips_layout[m];

        // Protect GPU modified resources from accidental CPU reuploads.
        // Record the first upload too. Otherwise an unrelated CPU dirty mark
        // can upload unchanged old bytes over a subsequent GPU write.
        if (is_gpu_modified) {
            const u8* addr = std::bit_cast<u8*>(image.info.guest_address);
            const u64 hash = XXH3_64bits(addr + mip_offset, mip_size);
            if (!is_gpu_dirty && image.mip_hashes[m] == hash) {
                continue;
            }
            image.mip_hashes[m] = hash;
        }

        const u32 extent_width = mip_pitch ? std::min(mip_pitch, width) : width;
        const u32 extent_height = mip_height ? std::min(mip_height, height) : height;
        image_copies.push_back({
            .bufferOffset = mip_offset,
            .bufferRowLength = mip_pitch,
            .bufferImageHeight = mip_height,
            .imageSubresource{
                .aspectMask = image.aspect_mask & ~vk::ImageAspectFlagBits::eStencil,
                .mipLevel = m,
                .baseArrayLayer = 0,
                .layerCount = num_layers,
            },
            .imageOffset = {0, 0, 0},
            .imageExtent = {extent_width, extent_height, depth},
        });
    }

    WatchImage(image, image_copies.empty() ? "refresh: nothing to copy" : "refresh: uploading");
    if (image_copies.empty()) {
        image.flags &= ~ImageFlagBits::Dirty;
        return;
    }

    backend.EndRendering();

    const auto [in_buffer, in_offset] =
        buffer_cache.ObtainBufferForImage(image.info.guest_address, image.info.guest_size);
    const auto [buffer, offset] = tile_manager.DetileImage(in_buffer, in_offset, image.info);
    for (auto& copy : image_copies) {
        copy.bufferOffset += offset;
    }

    backend.UploadImage(&image, buffer, image_copies);
}

SamplerRef TextureCache::GetSampler(const AmdGpu::Sampler& sampler,
                                    AmdGpu::BorderColorBuffer border_color_base,
                                    const bool is_depth, float extra_lod_bias, size_t* lru_id) {
    // Compare and plain uses of one S# need separate samplers; so do extra LOD biases.
    const u64 hash = HashCombine(HashCombine(XXH3_64bits(&sampler, sizeof(sampler)), is_depth),
                                 u64(std::bit_cast<u32>(extra_lod_bias)));

    std::scoped_lock lock{samplers_mutex};
    const auto [it, new_sampler] = samplers.try_emplace(
        hash, backend, sampler, border_color_base, is_depth, extra_lod_bias);
    if (new_sampler) {
        samplers.at(hash).lru_id = sampler_lru_cache.Insert(hash, gc_tick);
    } else {
        sampler_lru_cache.Touch(it->second.lru_id, gc_tick);
    }

    if (lru_id) {
        *lru_id = it->second.lru_id;
    }
    return {it->second.Handle(), it->second.native};
}

void TextureCache::RegisterImage(ImageId image_id) {
    BbStats::images_registered.fetch_add(1, std::memory_order_relaxed);
    Image& image = slot_images[image_id];
    ASSERT_MSG(False(image.flags & ImageFlagBits::Registered),
               "Trying to register an already registered image");
    image.flags |= ImageFlagBits::Registered;
    ++registry_generation;
    total_used_memory += Common::AlignUp(image.info.guest_size, 1024);
    BbStats::live_image_bytes.fetch_add(Common::AlignUp(image.info.guest_size, 1024),
                                        std::memory_order_relaxed);
    BbStats::live_images.fetch_add(1, std::memory_order_relaxed);
    image.lru_id = lru_cache.Insert(image_id, gc_tick);
    image.lru_touched_tick = gc_tick;
    AssociateGuestImage(image_id);
    ForEachPage(image.info.guest_address, image.info.guest_size,
                [this, image_id](u64 page) { page_table[page].push_back(image_id); });
}

void TextureCache::UnregisterImage(ImageId image_id) {
    Image& image = slot_images[image_id];
    ASSERT_MSG(True(image.flags & ImageFlagBits::Registered),
               "Trying to unregister an already unregistered image");
    guest_image_owners.erase(image_id.index);
    image.flags &= ~ImageFlagBits::Registered;
    ++registry_generation;
    lru_cache.Free(image.lru_id);
    total_used_memory -= Common::AlignUp(image.info.guest_size, 1024);
    BbStats::live_image_bytes.fetch_sub(Common::AlignUp(image.info.guest_size, 1024),
                                        std::memory_order_relaxed);
    BbStats::live_images.fetch_sub(1, std::memory_order_relaxed);
    ForEachPage(image.info.guest_address, image.info.guest_size, [this, image_id](u64 page) {
        const auto page_it = page_table.find(page);
        if (page_it == nullptr) {
            UNREACHABLE_MSG("Unregistering unregistered page=0x{:x}", page << PageShift);
            return;
        }
        auto& image_ids = *page_it;
        const auto vector_it = std::ranges::find(image_ids, image_id);
        if (vector_it == image_ids.end()) {
            ASSERT_MSG(false, "Unregistering unregistered image in page=0x{:x}", page << PageShift);
            return;
        }
        image_ids.erase(vector_it);
    });
}

void TextureCache::TrackImage(ImageId image_id) {
    auto& image = slot_images[image_id];
    if (!(image.flags & ImageFlagBits::Registered)) {
        return;
    }
    const auto image_begin = image.info.guest_address;
    const auto image_end = image.info.guest_address + image.info.guest_size;
    if (image_begin == image.track_addr && image_end == image.track_addr_end) {
        return;
    }

    if (!image.IsTracked()) {
        // Re-track the whole image
        image.track_addr = image_begin;
        image.track_addr_end = image_end;
        tracker.UpdatePageWatchers<1>(image_begin, image.info.guest_size);
    } else {
        if (image_begin < image.track_addr) {
            TrackImageHead(image_id);
        }
        if (image.track_addr_end < image_end) {
            TrackImageTail(image_id);
        }
    }
}

void TextureCache::TrackImageHead(ImageId image_id) {
    auto& image = slot_images[image_id];
    if (!(image.flags & ImageFlagBits::Registered)) {
        return;
    }
    const auto image_begin = image.info.guest_address;
    if (image_begin == image.track_addr) {
        return;
    }
    ASSERT(image.track_addr != 0 && image_begin < image.track_addr);
    const auto size = image.track_addr - image_begin;
    image.track_addr = image_begin;
    tracker.UpdatePageWatchers<1>(image_begin, size);
}

void TextureCache::TrackImageTail(ImageId image_id) {
    auto& image = slot_images[image_id];
    if (!(image.flags & ImageFlagBits::Registered)) {
        return;
    }
    const auto image_end = image.info.guest_address + image.info.guest_size;
    if (image_end == image.track_addr_end) {
        return;
    }
    ASSERT(image.track_addr_end != 0 && image.track_addr_end < image_end);
    const auto addr = image.track_addr_end;
    const auto size = image_end - image.track_addr_end;
    image.track_addr_end = image_end;
    tracker.UpdatePageWatchers<1>(addr, size);
}

void TextureCache::UntrackImage(ImageId image_id) {
    auto& image = slot_images[image_id];
    if (!image.IsTracked()) {
        return;
    }
    const auto addr = image.track_addr;
    const auto size = image.track_addr_end - image.track_addr;
    image.track_addr = 0;
    image.track_addr_end = 0;
    if (size != 0) {
        tracker.UpdatePageWatchers<false>(addr, size);
    }
}

void TextureCache::UntrackImageHead(ImageId image_id) {
    auto& image = slot_images[image_id];
    const auto image_begin = image.info.guest_address;
    if (!image.IsTracked() || image_begin < image.track_addr) {
        return;
    }
    const auto addr = tracker.GetNextPageAddr(image_begin);
    const auto size = addr - image_begin;
    image.track_addr = addr;
    if (image.track_addr == image.track_addr_end) {
        // This image spans only 2 pages and both are modified,
        // but the image itself was not directly affected.
        // Cehck its hash later.
        MarkAsMaybeDirty(image_id, image);
    }
    tracker.UpdatePageWatchers<false>(image_begin, size);
}

void TextureCache::UntrackImageTail(ImageId image_id) {
    auto& image = slot_images[image_id];
    const auto image_end = image.info.guest_address + image.info.guest_size;
    if (!image.IsTracked() || image.track_addr_end < image_end) {
        return;
    }
    ASSERT(image.track_addr_end != 0);
    const auto addr = tracker.GetPageAddr(image_end);
    const auto size = image_end - addr;
    image.track_addr_end = addr;
    if (image.track_addr == image.track_addr_end) {
        // This image spans only 2 pages and both are modified,
        // but the image itself was not directly affected.
        // Cehck its hash later.
        MarkAsMaybeDirty(image_id, image);
    }
    tracker.UpdatePageWatchers<false>(addr, size);
}

void TextureCache::GarbageCollectImages() {
    if (backend.Limits().can_report_memory_usage) {
        total_used_memory = backend.DeviceMemoryUsage();
        // bbport: on integrated GPUs (Steam Deck) the usage covers system-memory heaps holding
        // much more than images (buffers backing guest memory), and the startup budget left
        // ~1 GB after its 8 GB system reserve: usage stayed above the critical mark, so the
        // collector evicted images used two or three frames ago on every submission and wrote
        // GPU-written ones back. Compare with the driver's current budget instead.
        // BB_GC_BUDGET_MB=N: this rule with a fixed budget on any GPU (tests on a desktop).
        static const u64 forced_budget = [] {
            const char* env = std::getenv("BB_GC_BUDGET_MB");
            return env ? std::strtoull(env, nullptr, 10) << 20 : 0;
        }();
        if (backend.Limits().integrated || forced_budget) {
            const u64 budget = forced_budget ? forced_budget : backend.DeviceMemoryBudgetNow();
            if (budget != 0) {
                trigger_gc_memory = budget / 10 * 7;
                pressure_gc_memory = budget / 100 * 85;
                critical_gc_memory = budget / 100 * 95;
            }
        }
    }
    BbStats::gc_used_bytes.store(total_used_memory, std::memory_order_relaxed);
    BbStats::gc_trigger_bytes.store(trigger_gc_memory, std::memory_order_relaxed);
    BbStats::gc_critical_bytes.store(critical_gc_memory, std::memory_order_relaxed);
    // bbport: gc_tick (one per guest submission, hundreds a second) at each of the last 64
    // seconds, for ages in seconds. The usage compared is all of our VRAM, not only images: on a
    // discrete GPU it stays over the trigger, where an age of 16 ticks (~3 frames) evicted every
    // texture briefly off screen and uploaded it again. Below the pressure mark an image goes
    // once unused for BB_GC_IDLE_SECONDS (20): the textures of an area left behind.
    const u64 second = u64(std::chrono::duration_cast<std::chrono::seconds>(
                               std::chrono::steady_clock::now().time_since_epoch())
                               .count());
    if (second != gc_second) {
        gc_second = second;
        // bbport: the driver's budget for our process, once a second: near it the collector evicts
        // by submissions (as before 0.5) rather than keeping textures for seconds (NoteVramShort).
        if (backend.Limits().can_report_memory_usage && !backend.Limits().integrated) {
            const u64 budget = backend.DeviceMemoryBudgetNow();
            if (budget != 0 && total_used_memory >= budget / 100 * 92 &&
                vram_short_until.load(std::memory_order_relaxed) < second + 2) {
                vram_short_until.store(second + 2, std::memory_order_relaxed);
            }
        }
        BbStats::coarse_second.store(u32(second), std::memory_order_relaxed);
        gc_tick_at_second[second % gc_tick_at_second.size()] = gc_tick;
    }
    if (BbToggle::Disabled(BbToggle::TextureCollector)) {
        return;
    }
    if (total_used_memory < trigger_gc_memory) {
        return;
    }
    static const u64 idle_seconds = [] {
        const char* env = std::getenv("BB_GC_IDLE_SECONDS");
        return std::clamp<u64>(env ? std::strtoull(env, nullptr, 10) : 20, 1, 63);
    }();
    // Under pressure an image goes once unused for BB_GC_PRESSURE_IDLE_SECONDS (5), over the
    // critical mark for 1 s first, and only then by submissions as before (80/160 ticks, a
    // fraction of a second). On cards up to 8 GB the pressure mark is 40 % of the budget: the
    // usage stayed over it, so textures off screen for a moment were evicted and uploaded again
    // when the camera turned back (9000 a minute with a 4.5 GB budget), read over the bus each
    // time: NVIDIA testers saw the frame rate fall the longer they played.
    static const u64 pressure_idle_seconds = [] {
        const char* env = std::getenv("BB_GC_PRESSURE_IDLE_SECONDS");
        return std::clamp<u64>(env ? std::strtoull(env, nullptr, 10) : 5, 0, 63);
    }();
    // The tick at the start of that second (an older one where no submission came then).
    const auto tick_seconds_ago = [&](u64 seconds) {
        return gc_tick_at_second[(second - seconds) % gc_tick_at_second.size()];
    };
    const u64 idle_tick = tick_seconds_ago(idle_seconds);
    std::scoped_lock lock{mutex};
    bool pressured = false;
    bool aggresive = false;
    u64 below_tick = 0;
    size_t num_deletions = 0;
    u32 visited = 0;

    // emergency: over the critical mark after evicting what was unused for a second.
    const auto configure = [&](bool allow_aggressive, bool emergency = false) {
        pressured = total_used_memory >= pressure_gc_memory;
        aggresive = allow_aggressive && total_used_memory >= critical_gc_memory;
        const u64 ticks_to_destroy = std::min<u64>(aggresive ? 160 : pressured ? 80 : 16, gc_tick);
        below_tick = gc_tick - ticks_to_destroy;
        if (!pressured && !aggresive) {
            below_tick = std::min(below_tick, idle_tick);
        } else if (!emergency && second >= vram_short_until.load(std::memory_order_relaxed)) {
            const u64 seconds = aggresive ? 1 : pressure_idle_seconds;
            below_tick = std::min(below_tick, tick_seconds_ago(seconds));
        }
        num_deletions = aggresive ? 40 : pressured ? 20 : 10;
        visited = 0;
    };
    const auto clean_up = [&](ImageId image_id) {
        if (num_deletions == 0 || ++visited > 256) {
            return true;
        }
        auto& image = slot_images[image_id];
        const bool download = image.SafeToDownload();
        const bool tiled = image.info.IsTiled();
        // bbport: images that cannot go now (GPU-written: their contents exist only here, and
        // tiled ones cannot be written back at all) neither use up the deletions nor stay first
        // in line. The oldest ten being such render targets stopped the collector for good, and
        // VRAM grew with every area visited up to the pressure mark (~10 GB on 16 GB).
        if ((tiled && download) || (download && !pressured)) {
            lru_cache.Touch(image.lru_id, gc_tick);
            BbStats::gc_kept_images.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        --num_deletions;
        if (download) {
            // bbport: synchronously, while the image still protects its pages. A deferred
            // write-back landed after FreeImage had unprotected them, over whatever the game
            // had meanwhile stored there (e.g. its heap after unloading an area).
            DownloadImageMemory(image_id, true);
            ++gc_downloads;
        }
        ++gc_evictions;
        BbStats::gc_freed_images.fetch_add(1, std::memory_order_relaxed);
        FreeImage(image_id);
        if (total_used_memory < critical_gc_memory) {
            if (aggresive) {
                num_deletions >>= 2;
                aggresive = false;
                return false;
            }
            if (pressured && total_used_memory < pressure_gc_memory) {
                num_deletions >>= 1;
                pressured = false;
            }
        }
        return false;
    };

    // Try to remove anything old enough and not high priority.
    configure(false);
    lru_cache.ForEachItemBelow(below_tick, clean_up);

    if (total_used_memory >= critical_gc_memory) {
        // If we are still over the critical limit, run an aggressive GC
        configure(true);
        lru_cache.ForEachItemBelow(below_tick, clean_up);
    }
    if (total_used_memory >= critical_gc_memory) {
        // Still over it: what was used a moment ago goes too (out of memory otherwise).
        configure(true, true);
        lru_cache.ForEachItemBelow(below_tick, clean_up);
    }
    // bbport: evictions under memory pressure, at most every 5 s (BB_FRAME_STATS or not).
    if (pressured || gc_downloads != 0) {
        const auto now = std::chrono::steady_clock::now();
        if (now - gc_report_time >= std::chrono::seconds(5)) {
            std::printf("Texture cache: memory pressure, %llu of %llu MiB (critical %llu): "
                        "%llu images evicted, %llu written back since the last report\n",
                        (unsigned long long)(total_used_memory >> 20),
                        (unsigned long long)(pressure_gc_memory >> 20),
                        (unsigned long long)(critical_gc_memory >> 20),
                        (unsigned long long)gc_evictions, (unsigned long long)gc_downloads);
            gc_report_time = now;
            gc_evictions = gc_downloads = 0;
        }
    }
}

void TextureCache::GarbageCollectSamplers() {
    total_used_samplers = samplers.size();
    if (total_used_samplers < trigger_gc_samplers) {
        return;
    }
    std::scoped_lock lock{samplers_mutex};
    bool pressured = false;
    bool aggresive = false;
    u64 ticks_to_destroy = 0;
    size_t num_deletions = 0;

    const auto configure = [&](bool allow_aggressive) {
        pressured = total_used_samplers >= pressure_gc_samplers;
        aggresive = allow_aggressive && total_used_samplers >= critical_gc_samplers;
        ticks_to_destroy = aggresive ? 160 : pressured ? 80 : 16;
        ticks_to_destroy = std::min(ticks_to_destroy, gc_tick);
        num_deletions = aggresive ? 40 : pressured ? 20 : 10;
    };
    const auto clean_up = [&](u64 hash) {
        if (num_deletions == 0) {
            return true;
        }
        --num_deletions;
        const size_t lru_id = samplers.at(hash).lru_id;
        ++sampler_generation; // invalidate references before a slot or handle can be reused
        samplers.erase(hash);
        sampler_lru_cache.Free(lru_id);
        return false;
    };

    // Try to remove anything old enough and not high priority.
    configure(false);
    sampler_lru_cache.ForEachItemBelow(gc_tick - ticks_to_destroy, clean_up);

    if (total_used_samplers >= critical_gc_samplers) {
        // If we are still over the critical limit, run an aggressive GC
        configure(true);
        sampler_lru_cache.ForEachItemBelow(gc_tick - ticks_to_destroy, clean_up);
    }
}

void TextureCache::NoteVramShort() {
    const u64 now = u64(std::chrono::duration_cast<std::chrono::seconds>(
                            std::chrono::steady_clock::now().time_since_epoch())
                            .count());
    vram_short_until.store(now + 30, std::memory_order_relaxed);
}

void TextureCache::AssociateGuestImage(ImageId image_id) {
    if (!BbResourceLifetime::Enabled()) return;
    const auto& image=slot_images[image_id];
    const auto tag=BbResourceLifetime::Get().Owner(image.info.guest_address,image.info.guest_size);
    guest_image_owners[image_id.index]=tag;
    for (const auto& backing:image.backing_images)
        backend.AssociateGuestImage(backing.image,image.info.guest_address,image.info.guest_size);
}

void TextureCache::CollectReleasedGuestImages() {
    if (!BbResourceLifetime::Enabled()) return;
    const auto now=std::chrono::steady_clock::now();
    if (now-guest_collect_time<std::chrono::seconds(1)) return;
    guest_collect_time=now;
    BbAudit::Scope collection_audit{BbAudit::LifetimeCollect,0,1000000};
    backend.BeforeImageAccess(); // join helper texture bindings before examining/removing slots
    std::scoped_lock lock{mutex};
    auto& lifetimes=BbResourceLifetime::Get();
    // Candidate stays opt-in until isolated repeated-load, death and FPS validation.
    static const bool dead_storage=[] {const auto* v=std::getenv("BB_TEXTURE_RELEASED_STORAGE");return v && v[0]=='1';}();
    const u64 cold_cutoff=gc_tick_at_second[(gc_second-2)%gc_tick_at_second.size()];
    struct Candidate {ImageId id;BbResourceLifetime::Tag tag;bool storage;};
    std::vector<Candidate> candidates;
    std::vector<ImageId> reassociate;
    u64 waiting=0, protected_images=0;
    for (const auto& [index,tag]:guest_image_owners) {
        const ImageId id{index};
        const auto& image=slot_images[id];
        const auto owner=lifetimes.Owner(image.info.guest_address,image.info.guest_size);
        if (owner && owner!=tag) { reassociate.push_back(id);continue; }
        if (lifetimes.Status(tag)!=BbResourceLifetime::State::Released) continue;
        // No writeback, no guest page decommit, no query/depth/velocity/proxy deletion.
        // The default path retains every GPU-written role. The optional storage-only
        // extension additionally requires both coldness and the locked generation proof.
        if (!backend.IsFreeKnown(image.tick_accessed_last) ||
            (image.gpu_write_tick && !backend.IsFreeKnown(image.gpu_write_tick))) {
            ++waiting;continue;
        }
        // Range-family unknown frees show additional allocation paths; keep it diagnostic
        // until those are paired too. Only the fully wrapped TLSF heap authorizes removal.
        const bool extra_storage=dead_storage && !BbToggle::Disabled(BbToggle::TextureCollector) &&
            cold_cutoff && image.lru_touched_tick<cold_cutoff && image.CanReleaseFreedStorageBacking();
        if (tag.domain!=BbResourceLifetime::Domain::Heap ||
            (!image.CanReleaseGuestBacking() && !extra_storage)) {
            ++protected_images;continue;
        }
        // Do not end a render pass for candidates which the interval proof
        // already rejects. Removal below repeats the proof atomically; allocations
        // are allowed to race with this read-only preflight.
        if (extra_storage && !lifetimes.ReleasedRangeUnused(tag,image.info.guest_address,
                                                          image.info.guest_size,2000)) continue;
        if (candidates.size()<16) candidates.push_back({id,tag,extra_storage});
    }
    for(const auto id:reassociate) AssociateGuestImage(id);
    lifetimes.NoteScan(waiting,protected_images);
    if (!BbResourceLifetime::ReclaimReady() || candidates.empty()) return;
    backend.EndRendering(); // release held pass references through the existing backend path
    u64 storage_images=0,storage_bytes=0;
    for(const auto& [id,tag,storage]:candidates) {
        const auto& image=slot_images[id];
        const u64 address=image.info.guest_address, size=image.info.guest_size;
        // Both allocator domains must have no replacement overlap. A dropped record disables
        // reclamation. Keep the generation locked through unregistration; never wait for GPU.
        u64 removed_bytes=0;
        const auto commit=[&] {
            return lifetimes.WithReleased(tag,address,size,2000,[&]() -> BbResourceLifetime::u64 {
                u64 bytes=0;for(const auto& backing:image.backing_images)bytes+=backing.image.size_bytes;
                removed_bytes=bytes;
                FreeImage(id); // normal unwatch/unregister/deferred destruction; no GPU wait
                return bytes;
            });
        };
        bool removed=false;
        if(storage) {
            // Virtual heap ownership alone does not exclude another guest mapping of
            // the same physical bytes. Keep mapping and allocation proof through removal.
            struct Context {const decltype(commit)* action;};
            Context context{&commit};
            removed=BbReclaimMapping::WithUnaliasedMapping(address,size,[](void* opaque) noexcept {
                return (*static_cast<Context*>(opaque)->action)() ? 1 : 0;
            },&context);
        } else removed=commit();
        if(removed && storage) {++storage_images;storage_bytes+=removed_bytes;}
    }
    if(storage_images)std::printf("Released storage reclaim: images=%llu capacity=%llu bytes; explicit dead heap, GPU-complete, no live overlap, no writeback\n",
        (unsigned long long)storage_images,(unsigned long long)storage_bytes);
}

void TextureCache::RunGarbageCollector() {
    SCOPE_EXIT {
        ++gc_tick;
    };

    CollectReleasedGuestImages();
    AuditGuestImages();
    GarbageCollectImages();
    CollectInvalidGuestImages();
    GarbageCollectSamplers();
}

void TextureCache::CollectInvalidGuestImages() {
    // Candidate remains opt-in until the isolated native-travel comparison passes.
    static const bool enabled = [] {
        const auto* e = std::getenv("BB_TEXTURE_INVALID_RECLAIM");
        return e && e[0] == '1';
    }();
    if (!enabled || !BbResourceLifetime::Enabled() || BbToggle::Disabled(BbToggle::TextureCollector)) return;
    const auto now = std::chrono::steady_clock::now();
    if (now - invalid_collect_time < std::chrono::seconds(1)) return;
    invalid_collect_time = now;
    // Do not churn actively rewritten textures. GC history uses wall-clock seconds,
    // not an assumed frame rate, and touches on every cache binding.
    const u64 cutoff = gc_tick_at_second[(gc_second - 2) % gc_tick_at_second.size()];
    if (!cutoff) return;
    backend.BeforeImageAccess();
    std::scoped_lock lock{mutex};
    std::vector<ImageId> candidates;
    u64 bytes = 0;
    for (const auto& [index, tag] : guest_image_owners) {
        const ImageId id{index};const auto& image=slot_images[id];
        if (image.lru_touched_tick >= cutoff || !image.CanReleaseInvalidBacking()) continue;
        candidates.push_back(id);
        for (const auto& backing:image.backing_images)
            if (backing.image.native || backing.image.image) bytes+=backing.image.size_bytes;
        if (candidates.size()>=32 || bytes>=(16ULL<<20)) break;
    }
    if (candidates.empty()) return;
    backend.EndRendering();
    for (const auto id:candidates) FreeImage(id);
    invalid_collected_images+=candidates.size();invalid_collected_bytes+=bytes;
    std::printf("Texture invalid reclaim: images=%llu capacity=%llu bytes; CPU-invalid, idle, GPU-complete copies only; valid cache unchanged\n",
        (unsigned long long)invalid_collected_images,(unsigned long long)invalid_collected_bytes);
}

void TextureCache::CaptureTextureBinding(ImageId id, ImageId view, u64 replacement,
                                        u64 core, u64 shader, u32 stage, u32 slot,
                                        VAddr requested, const char* source) {
    if (!FrameCapture::TextureBindingsEnabled() || !FrameCapture::NativeRecording()) return;
    std::scoped_lock lock{mutex};
    const u64 native = replacement ? u64(replacement)
        : view && slot_image_views.is_allocated(view)
            ? uintptr_t(slot_image_views[view].native) : 0;
    if (!id || !slot_images.is_allocated(id)) {
        FrameCapture::Native(fmt::format("{{\"op\":\"texture_binding\",\"core\":{},\"shader\":{},\"stage\":{},\"slot\":{},\"source\":\"{}\",\"requested\":{},\"nv\":{},\"image_known\":false}}",
            core, shader, stage, slot, source, requested, native));
        return;
    }
    const auto& image=slot_images[id];
    const auto found=guest_image_owners.find(id.index);
    const auto tag=found==guest_image_owners.end()?BbResourceLifetime::Tag{}:found->second;
    const auto overlap=BbResourceLifetime::Get().InspectRange(tag,image.guest_begin,image.info.guest_size);
    const u64 backing=image.backing?uintptr_t(image.backing->image.native):0;
    std::array<u64,6> content{};u64 content_us=0;
#ifdef __APPLE__
    if(backing) {const auto observed=Metal::MemoryStats::ReadContent(reinterpret_cast<const void*>(backing));
        content=observed.counts;content_us=observed.last_us;}
#endif
    FrameCapture::Native(fmt::format("{{\"op\":\"texture_binding\",\"core\":{},\"shader\":{},\"stage\":{},\"slot\":{},\"source\":\"{}\",\"requested\":{},\"nv\":{},\"image_known\":true,\"uid\":{},\"ni\":{},\"guest\":{},\"gb\":{},\"format\":{},\"width\":{},\"height\":{},\"flags\":{},\"tg\":{},\"ts\":{},\"co\":{},\"tc\":{},\"ov\":{},\"hd\":{},\"ot\":{},\"uploads\":{},\"clears\":{},\"copies\":{},\"draws\":{},\"storage\":{},\"uw\":{},\"cu\":{}}}",
        core,shader,stage,slot,source,requested,native,image.image_uid,backing,image.guest_begin,
        image.info.guest_size,u32(image.info.pixel_format),image.info.size.width,image.info.size.height,
        u32(image.flags),tag.generation,unsigned(overlap.tag_state),overlap.covering_owner.generation,
        overlap.covered_by_tag,overlap.valid,overlap.history_dropped,overlap.truncated,
        content[0],content[1],content[2],content[3],content[4],content[5],content_us));
}

void TextureCache::AuditGuestImages() {
    static u64 captured_sequence=0;
    const u64 capture_sequence=FrameCapture::NativeCaptureSequence();
    if(capture_sequence && capture_sequence!=captured_sequence) {
        captured_sequence=capture_sequence;
        std::scoped_lock lock{mutex};unsigned count=0;
        for(const auto& [index,tag]:guest_image_owners) {
            const auto& image=slot_images[ImageId{index}];
            if(False(image.flags&ImageFlagBits::Registered))continue;
            const auto overlap=BbResourceLifetime::Get().InspectRange(tag,image.guest_begin,image.info.guest_size);
            for(const auto& backing:image.backing_images) {
                if(!backing.image.native)continue;
                if(++count>2048)break;
                FrameCapture::Native(std::format("{{\"op\":\"texture_lifetime\",\"uid\":{},\"native\":{},\"bytes\":{},\"retired\":{},\"guest\":{},\"generation\":{},\"flags\":{},\"target\":{},\"proxy\":{},\"tick\":{},\"write_tick\":{},\"gpu_complete\":{},\"lru_age\":{},\"current_tick\":{},\"tag_state\":{},\"tag_address\":{},\"tag_bytes\":{},\"tag_domain\":{},\"guest_bytes\":{},\"current_owner\":{},\"safe_guest_release\":{},\"safe_invalid_release\":{},\"recorded_state\":{},\"tag_covered\":{},\"overlap_valid\":{},\"range_live_overlaps\":{},\"range_live_bytes\":{},\"heap_live_overlaps\":{},\"heap_live_bytes\":{},\"overlap_truncated\":{},\"history_dropped\":{}}}",image.image_uid,uintptr_t(backing.image.native),backing.image.size_bytes,backing.retired,image.guest_begin,tag.generation,u32(image.flags),bool(image.usage.render_target||image.usage.depth_target),unsigned(image.scene_proxy),image.tick_accessed_last,image.gpu_write_tick,backend.IsFreeKnown(image.tick_accessed_last)&&(!image.gpu_write_tick||backend.IsFreeKnown(image.gpu_write_tick)),gc_tick-image.lru_touched_tick,backend.CurrentTick(),unsigned(overlap.tag_state),tag.address,tag.size,unsigned(tag.domain),image.info.guest_size,overlap.covering_owner.generation,image.CanReleaseGuestBacking(),image.CanReleaseInvalidBacking(),unsigned(overlap.recorded_state),overlap.covered_by_tag,overlap.valid,overlap.overlaps[0],overlap.overlap_bytes[0],overlap.overlaps[1],overlap.overlap_bytes[1],overlap.truncated,overlap.history_dropped));
            }
            if(count>2048)break;
        }
        if(count>2048)FrameCapture::Native("{\"op\":\"lifetime_overflow\",\"kind\":\"textures\"}");
    }
    // Private diagnostics only: stream metadata, never read texture pixels or create GPU resources.
    static const char* directory = std::getenv("BB_TEXTURE_AUDIT_DIR");
    if (!directory || !*directory || !BbResourceLifetime::Enabled()) return;
    const auto now = std::chrono::steady_clock::now();
    if (now - image_audit_time < std::chrono::seconds(1)) return;
    image_audit_time = now;
    const std::filesystem::path root{directory};
    FILE* request = std::fopen((root / "request").c_str(), "r");
    if (!request) return;
    unsigned sequence = 0;
    const bool valid = std::fscanf(request, "%u", &sequence) == 1;
    std::fclose(request);
    if (!valid || sequence != image_audit_sequence + 1 || sequence > 32) return;
    const auto final = root / ("textures-" + std::to_string(sequence) + ".csv");
    const auto pending = root / "textures.pending";
    FILE* output = std::fopen(pending.c_str(), "w");
    if (!output) return;
    backend.BeforeImageAccess();
    std::scoped_lock lock{mutex};
    std::fprintf(output, "uid,address,guest_bytes,host_bytes,retired_bytes,width,height,format,tile,flags,usage,binding,proxy,access_tick,write_tick,gpu_complete,lru_tick,current_tick,gc_tick,tag_generation,tag_domain,tag_state,current_owner,depth,samples,uploads,clears,copies,draws,storage_binds,unknown_writes,content_us,owner_address,owner_bytes,owner_domain,heap_live_overlaps,heap_live_bytes,range_live_overlaps,range_live_bytes,overlap_truncated\n");
    u64 count = 0, capacity = 0, dirty_safe = 0;
    for (const auto& [index, tag] : guest_image_owners) {
        const auto& image = slot_images[ImageId{index}];
        if (False(image.flags & ImageFlagBits::Registered)) continue;
        u64 bytes = 0, retired = 0;
        std::array<u64,6> content{};u64 content_us=0;
        for (const auto& backing : image.backing_images) {
            if (!backing.image.native && !backing.image.image) continue;
            bytes += backing.image.size_bytes;
            if (backing.retired) retired += backing.image.size_bytes;
#ifdef __APPLE__
            const auto observed=Metal::MemoryStats::ReadContent(backing.image.native);
            for(unsigned i=0;i<content.size();++i)content[i]+=observed.counts[i];
            content_us=std::max(content_us,observed.last_us);
#endif
        }
        const u32 usage = image.usage.texture | image.usage.storage << 1 |
            image.usage.render_target << 2 | image.usage.depth_target << 3 | image.usage.vo_surface << 4;
        const u32 binding = image.binding.is_bound | image.binding.is_target << 1 |
            image.binding.needs_rebind << 2 | image.binding.force_general << 3;
        const bool complete = backend.IsFreeKnown(image.tick_accessed_last) &&
            (!image.gpu_write_tick || backend.IsFreeKnown(image.gpu_write_tick));
        const auto owner = BbResourceLifetime::Get().Owner(image.guest_begin, image.info.guest_size);
        const auto overlap=BbResourceLifetime::Get().InspectRange(tag,image.guest_begin,image.info.guest_size);
        // The image UID is monotonic; address reuse is not mistaken for object survival.
        const unsigned state = unsigned(BbResourceLifetime::Get().Status(tag));
        if (image.CanReleaseInvalidBacking()) dirty_safe += bytes;
        ++count; capacity += bytes;
        const auto u = [](u64 n) { return static_cast<unsigned long long>(n); };
        std::fprintf(output, "%llu,%llu,%llu,%llu,%llu,%u,%u,%u,%u,%u,%u,%u,%u,%llu,%llu,%u,%llu,%llu,%llu,%llu,%u,%u,%llu,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%llu,%llu,%llu,%llu,%u\n",
            u(image.image_uid), u(image.guest_begin), u(image.info.guest_size), u(bytes), u(retired),
            image.info.size.width, image.info.size.height, u32(image.info.pixel_format), u32(image.info.tile_mode),
            u32(image.flags), usage, binding, unsigned(image.scene_proxy), u(image.tick_accessed_last),
            u(image.gpu_write_tick), unsigned(complete), u(image.lru_touched_tick), u(backend.CurrentTick()),
            u(gc_tick), u(tag.generation), unsigned(tag.domain), state, u(owner.generation),
            unsigned(image.info.props.is_depth || bool(image.depth_id)), image.info.num_samples,
            u(content[0]),u(content[1]),u(content[2]),u(content[3]),u(content[4]),u(content[5]),u(content_us),
            u(owner.address),u(owner.size),unsigned(owner.domain),u(overlap.overlaps[1]),u(overlap.overlap_bytes[1]),u(overlap.overlaps[0]),u(overlap.overlap_bytes[0]),unsigned(overlap.truncated));
        if (count >= 16384) break;
    }
    const bool ok = !std::ferror(output);
    const bool closed = std::fclose(output) == 0;
    std::error_code error;
    if (ok && closed) std::filesystem::rename(pending, final, error);
    if (!ok || !closed || error) return;
    image_audit_sequence = sequence;
    std::printf("Texture retention audit: sequence=%u images=%llu capacity=%llu cpu-invalid-safe=%llu; metadata only, no collection change\n",
        sequence, (unsigned long long)count, (unsigned long long)capacity, (unsigned long long)dirty_safe);
}

void TextureCache::TouchImage(const Image& image) {
    if (image.lru_touched_tick == gc_tick) {
        return;
    }
    image.lru_touched_tick = gc_tick;
    lru_cache.Touch(image.lru_id, gc_tick);
}

void TextureCache::DeleteImage(ImageId image_id) {
    Image& image = slot_images[image_id];
    ASSERT_MSG(!image.IsTracked(), "Image was not untracked");
    ASSERT_MSG(False(image.flags & ImageFlagBits::Registered), "Image was not unregistered");

    // Remove any registered meta areas.
    const auto& meta_info = image.info.meta_info;
    if (meta_info.cmask_addr) {
        surface_metas.erase(meta_info.cmask_addr);
    }
    if (meta_info.fmask_addr) {
        surface_metas.erase(meta_info.fmask_addr);
    }
    if (meta_info.htile_addr) {
        surface_metas.erase(meta_info.htile_addr);
    }

    {
        std::unique_lock lk{download_images_mutex};
        if (download_images.contains(image_id)) {
            download_images.erase(image_id);
        }
    }

    // Reclaim image and any image views it references.
    backend.DeferOperation([this, image_id] {
        Image& image = slot_images[image_id];
        for (auto& backing : image.backing_images) {
            for (const ImageViewId image_view_id : backing.image_view_ids) {
                slot_image_views.erase(image_view_id);
            }
        }
        slot_images.erase(image_id);
    });
}

} // namespace VideoCore
