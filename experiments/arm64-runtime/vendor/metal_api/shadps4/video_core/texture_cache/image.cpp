// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "bbport_toggles.h"
#include "remote/bb_remote.h"
#include <ranges>
#include "common/assert.h"
#include "video_core/cache_backend.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/texture_cache/image.h"

namespace VideoCore {

using namespace Vulkan;

Common::IncrementalIdProvider<u64> Image::global_image_uid{};

static vk::ImageUsageFlags ImageUsageFlags(const BackendLimits& limits, const ImageInfo& info,
                                           bool lean) {
    vk::ImageUsageFlags usage = vk::ImageUsageFlagBits::eTransferSrc |
                                vk::ImageUsageFlagBits::eTransferDst |
                                vk::ImageUsageFlagBits::eSampled;
    if (!info.props.is_block) {
        if (info.props.is_depth) {
            usage |= vk::ImageUsageFlagBits::eDepthStencilAttachment;
        } else {
            usage |= vk::ImageUsageFlagBits::eColorAttachment;
            if (limits.attachment_feedback_loop_layout) {
                usage |= vk::ImageUsageFlagBits::eAttachmentFeedbackLoopEXT;
            }
            // Always create images with storage flag to avoid needing re-creation in case of e.g
            // compute clears This sacrifices a bit of performance but is less work. ExtendedUsage
            // flag is also used.
            // bbport (P10.1): not when the backend promotes images on first need (lean).
            if (!lean) {
                usage |= vk::ImageUsageFlagBits::eStorage;
            }
        }
    } else {
        // Similarly to above, we specify storage usage. This is typically not supported by
        // compressed formats, but may be used for uncompressed views. In order to satisfy this,
        // we will also specify the extended usage bit.
        if (!lean) {
            usage |= vk::ImageUsageFlagBits::eStorage;
        }
    }

    return usage;
}

static vk::ImageType ConvertImageType(AmdGpu::ImageType type) noexcept {
    switch (type) {
    case AmdGpu::ImageType::Color1D:
    case AmdGpu::ImageType::Color1DArray:
    case AmdGpu::ImageType::Color2D:
    case AmdGpu::ImageType::Color2DMsaa:
    case AmdGpu::ImageType::Color2DArray:
        return vk::ImageType::e2D;
    case AmdGpu::ImageType::Color3D:
        return vk::ImageType::e3D;
    default:
        UNREACHABLE();
    }
}

static vk::FormatFeatureFlags2 FormatFeatureFlags(const vk::ImageUsageFlags usage_flags) {
    vk::FormatFeatureFlags2 feature_flags{};
    if (usage_flags & vk::ImageUsageFlagBits::eTransferSrc) {
        feature_flags |= vk::FormatFeatureFlagBits2::eTransferSrc;
    }
    if (usage_flags & vk::ImageUsageFlagBits::eTransferDst) {
        feature_flags |= vk::FormatFeatureFlagBits2::eTransferDst;
    }
    if (usage_flags & vk::ImageUsageFlagBits::eSampled) {
        feature_flags |= vk::FormatFeatureFlagBits2::eSampledImage;
    }
    if (usage_flags & vk::ImageUsageFlagBits::eColorAttachment) {
        feature_flags |= vk::FormatFeatureFlagBits2::eColorAttachment;
    }
    if (usage_flags & vk::ImageUsageFlagBits::eDepthStencilAttachment) {
        feature_flags |= vk::FormatFeatureFlagBits2::eDepthStencilAttachment;
    }
    // Note: StorageImage is intentionally ignored for now since it is always set, and can mess up
    // compatibility checks.
    return feature_flags;
}

bool Image::CanReleaseGuestBacking() const {
    return True(flags & ImageFlagBits::Registered) &&
        False(flags & (ImageFlagBits::GpuModified | ImageFlagBits::GpuDirty)) &&
        !usage.render_target && !usage.depth_target && !usage.storage && !usage.vo_surface &&
        !scene_proxy && !binding.is_bound && !binding.is_target && !binding.needs_rebind &&
        backend->IsFreeKnown(tick_accessed_last) &&
        (!gpu_write_tick || backend->IsFreeKnown(gpu_write_tick));
}

bool Image::CanReleaseFreedStorageBacking() const {
    // No readback to freed allocator payloads. This is NOT a normal cache eviction
    // predicate: only a confirmed dead logical allocation can make GPU-only data dead.
    return backing && True(flags & ImageFlagBits::Registered) &&
        True(flags & ImageFlagBits::GpuModified) && False(flags & ImageFlagBits::GpuDirty) &&
        usage.storage && !usage.render_target && !usage.depth_target && !usage.vo_surface &&
        !info.props.is_depth && !depth_id && info.num_samples==1 && !scene_proxy &&
        !binding.is_bound && !binding.is_target && !binding.needs_rebind && !binding.force_general &&
        backend->IsFreeKnown(tick_accessed_last) &&
        (!gpu_write_tick || backend->IsFreeKnown(gpu_write_tick));
}

bool Image::CanReleaseInvalidBacking() const {
    // An explicit CPU write already invalidated this native copy. Mere inactivity,
    // unknown allocation ownership and a MaybeCpuDirty page are not sufficient.
    return True(flags & ImageFlagBits::CpuDirty) && !info.props.is_depth && !depth_id &&
        info.num_samples == 1 && CanReleaseGuestBacking();
}

UniqueImage::~UniqueImage() {
    Destroy();
}

void UniqueImage::Destroy() {
    if (image || native) {
        if (owner) {
            owner->DestroyImage(*this);
        } else {
            Vulkan::DestroyVmaImage(*this);
        }
    }
}

void UniqueImage::Create(const vk::ImageCreateInfo& image_ci) {
    Vulkan::CreateVmaImage(*this, image_ci);
}

Image::Image(CacheBackend& backend_, Common::SlotVector<ImageView>& slot_image_views_,
             const ImageInfo& info_)
    : guest_begin{info_.guest_address}, guest_end{info_.guest_address + info_.guest_size},
      info{info_}, backend{&backend_}, slot_image_views{&slot_image_views_} {
    BbStats::Timer timer{BbStats::t_image_create};
    if (info.pixel_format == vk::Format::eUndefined) {
        return;
    }

    image_uid = global_image_uid.Next();
    mip_hashes.resize(info.resources.levels);
    // bbport (P10.1): a backend that promotes images on first need (BackendLimits::lean_image_usage)
    // gets colour and block images without the storage usage and the mutable format flag; depth
    // keeps the flag (it costs no compression there and the stencil view needs it) and so do
    // multisampled images (PromoteImageUsage cannot copy those).
    const bool lean = backend_.Limits().lean_image_usage && info.num_samples <= 1;
    vk::ImageCreateFlags flags{vk::ImageCreateFlagBits::eExtendedUsage};
    if (!lean || info.props.is_depth) {
        flags |= vk::ImageCreateFlagBits::eMutableFormat;
    }
    if (info.props.is_volume) {
        flags |= vk::ImageCreateFlagBits::e2DArrayCompatible;
        if (backend_.Limits().image_2d_view_of_3d) {
            flags |= vk::ImageCreateFlagBits::e2DViewCompatibleEXT;
        }
    }
    if (info.props.is_block && backend_.Limits().block_texel_view) {
        flags |= vk::ImageCreateFlagBits::eBlockTexelViewCompatible;
    }

    usage_flags = ImageUsageFlags(backend_.Limits(), info, lean);
    format_features = FormatFeatureFlags(usage_flags);
    if (info.props.is_depth) {
        aspect_mask = vk::ImageAspectFlagBits::eDepth;
        if (info.props.has_stencil) {
            aspect_mask |= vk::ImageAspectFlagBits::eStencil;
        }
    }

    constexpr auto tiling = vk::ImageTiling::eOptimal;
    const auto supported_format = backend_.SupportedFormat(info.pixel_format, format_features);
    const vk::ImageCreateInfo format_info{
        .flags = flags,
        .imageType = ConvertImageType(info.type),
        .format = supported_format,
        .tiling = tiling,
        .usage = usage_flags,
    };
    supported_samples = backend_.ImageSampleCounts(format_info);

    const vk::ImageCreateInfo image_ci = {
        .flags = flags,
        .imageType = ConvertImageType(info.type),
        .format = supported_format,
        .extent{
            .width = info.size.width,
            .height = info.size.height,
            .depth = info.size.depth,
        },
        .mipLevels = static_cast<u32>(info.resources.levels),
        .arrayLayers = static_cast<u32>(info.resources.layers),
        .samples = LiverpoolToVK::NumSamples(info.num_samples, supported_samples),
        .tiling = tiling,
        .usage = usage_flags,
        .initialLayout = vk::ImageLayout::eUndefined,
    };

    backing = &backing_images.emplace_back();
    backing->num_samples = info.num_samples;
    // Metadata-only pre-capture. Never keep frontend allocator/VMA/Tracker
    // locks across the driver allocation, resident-set insertion or format map.
    BbPayload::Origin captured{};
    bool observed=false;
    if (BbRemote::BackActive()) {
        observed=BbRemote::Back::CaptureLivePayload(info.guest_address,info.guest_size,
            [&](const BbRemote::LivePayloadReceipt& receipt) {
                captured={receipt.owner,receipt.mapping};return true;
            });
    }
    backend_.CreateImage(backing->image,image_ci);
    // A free/reallocation or remap during native construction changes Tag or
    // mapping generation, so post-validation cannot bless a replacement owner.
    // Failure leaves the successfully created native image usable as normal,
    // with unknown provenance. No later collector/association may fill it in.
    if(observed&&BbRemote::Back::WithLivePayload(captured,info.guest_address,info.guest_size,
            [](const BbRemote::LivePayloadReceipt&){return true;})) {
        original_payload_origin=captured;
        original_payload_native=backing->image.native;
    }

    backend_.SetImageName(
        backing->image,
        fmt::format("Image {}x{}x{} {} {} {:#x}:{:#x} L:{} M:{} S:{}", info.size.width,
                    info.size.height, info.size.depth, AmdGpu::NameOf(info.tile_mode),
                    vk::to_string(info.pixel_format), info.guest_address, info.guest_size,
                    info.resources.layers, info.resources.levels, info.num_samples));
}

Image::~Image() = default;

void Image::EnsureViewUsage(const ImageViewInfo& view_info) {
    if (!backing || !backend->Limits().lean_image_usage) {
        return;
    }
    // A view in the image's own format that no shader writes needs nothing (the common case).
    if (!view_info.is_storage && view_info.format == info.pixel_format) {
        return;
    }
    if (const auto usage = backend->RequiredViewUsage(*this, view_info);
        usage != ImageUsagePromotion::None) {
        backend->PromoteImageUsage(this, usage);
    }
}

ImageView& Image::FindView(const ImageViewInfo& view_info, bool ensure_guest_samples) {
    if (ensure_guest_samples && backing->num_samples > 1 != info.num_samples > 1) {
        backend->SetBackingSamples(this, info.num_samples);
    }
    // bbport (P10.1): before the views of the backing are looked up (a promotion replaces it).
    EnsureViewUsage(view_info);
    const auto& view_infos = backing->image_view_infos;
    if (backing->last_view < view_infos.size() && view_infos[backing->last_view] == view_info) {
        return (*slot_image_views)[backing->image_view_ids[backing->last_view]];
    }
    const auto it = std::ranges::find(view_infos, view_info);
    if (it != view_infos.end()) {
        backing->last_view = static_cast<u32>(std::distance(view_infos.begin(), it));
        const auto view_id = backing->image_view_ids[backing->last_view];
        return (*slot_image_views)[view_id];
    }
    const auto view_id = slot_image_views->insert(*backend, view_info, *this);
    backing->image_view_infos.emplace_back(view_info);
    backing->image_view_ids.emplace_back(view_id);
    return (*slot_image_views)[view_id];
}

void Image::GetBarriers(Barriers& barriers, vk::ImageLayout dst_layout, vk::AccessFlags2 dst_mask,
                        vk::PipelineStageFlags2 dst_stage,
                        std::optional<SubresourceRange> subres_range) {
    auto& last_state = backing->state;
    auto& subresource_states = backing->subresource_states;

    const bool needs_partial_transition =
        subres_range &&
        (subres_range->base != SubresourceBase{} || subres_range->extent != info.resources);
    const bool partially_transited = !subresource_states.empty();

    if (needs_partial_transition || partially_transited) {
        if (!partially_transited) {
            subresource_states.resize(info.resources.levels * info.resources.layers);
            std::fill(subresource_states.begin(), subresource_states.end(), last_state);
        }

        // In case of partial transition, we need to change the specified subresources only.
        // Otherwise all subresources need to be set to the same state so we can use a full
        // resource transition for the next time.
        const auto mips =
            needs_partial_transition
                ? std::ranges::views::iota(subres_range->base.level,
                                           subres_range->base.level + subres_range->extent.levels)
                : std::views::iota(0u, info.resources.levels);
        const auto layers =
            needs_partial_transition
                ? std::ranges::views::iota(subres_range->base.layer,
                                           subres_range->base.layer + subres_range->extent.layers)
                : std::views::iota(0u, info.resources.layers);

        for (u32 mip : mips) {
            for (u32 layer : layers) {
                // NOTE: these loops may produce a lot of small barriers.
                // If this becomes a problem, we can optimize it by merging adjacent barriers.
                const auto subres_idx = mip * info.resources.layers + layer;
                ASSERT(subres_idx < subresource_states.size());
                auto& state = subresource_states[subres_idx];

                constexpr auto write_flags = vk::AccessFlagBits2::eTransferWrite |
                                             vk::AccessFlagBits2::eShaderWrite |
                                             vk::AccessFlagBits2::eMemoryWrite;
                const bool is_write = static_cast<bool>(state.access_mask & write_flags);
                if (state.layout != dst_layout || state.access_mask != dst_mask || is_write) {
                    barriers.emplace_back(vk::ImageMemoryBarrier2{
                        .srcStageMask = state.pl_stage,
                        .srcAccessMask = state.access_mask,
                        .dstStageMask = dst_stage,
                        .dstAccessMask = dst_mask,
                        .oldLayout = state.layout,
                        .newLayout = dst_layout,
                        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                        .image = GetImage(),
                        .subresourceRange{
                            .aspectMask = aspect_mask,
                            .baseMipLevel = mip,
                            .levelCount = 1,
                            .baseArrayLayer = layer,
                            .layerCount = 1,
                        },
                    });
                    state.layout = dst_layout;
                    state.access_mask = dst_mask;
                    state.pl_stage = dst_stage;
                }
            }
        }

        if (!needs_partial_transition) {
            subresource_states.clear();
        }
    } else { // Full resource transition
        constexpr auto write_flags = vk::AccessFlagBits2::eTransferWrite |
                                     vk::AccessFlagBits2::eShaderWrite |
                                     vk::AccessFlagBits2::eMemoryWrite;
        const bool is_write = static_cast<bool>(last_state.access_mask & write_flags);
        if (last_state.layout == dst_layout && last_state.access_mask == dst_mask && !is_write) {
            return;
        }

        barriers.emplace_back(vk::ImageMemoryBarrier2{
            .srcStageMask = last_state.pl_stage,
            .srcAccessMask = last_state.access_mask,
            .dstStageMask = dst_stage,
            .dstAccessMask = dst_mask,
            .oldLayout = last_state.layout,
            .newLayout = dst_layout,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = GetImage(),
            .subresourceRange{
                .aspectMask = aspect_mask,
                .baseMipLevel = 0,
                .levelCount = VK_REMAINING_MIP_LEVELS,
                .baseArrayLayer = 0,
                .layerCount = VK_REMAINING_ARRAY_LAYERS,
            },
        });
    }

    last_state.layout = dst_layout;
    last_state.access_mask = dst_mask;
    last_state.pl_stage = dst_stage;
}

} // namespace VideoCore
