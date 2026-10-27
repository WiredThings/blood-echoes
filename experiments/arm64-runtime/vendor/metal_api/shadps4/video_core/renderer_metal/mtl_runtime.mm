#include "bb_staging_recent.h"
#include "common/scope_exit.h"
#include "video_core/renderer_metal/mtl_texture_lineage.h"
#include "video_core/renderer_metal/mtl_texture_lineage_api.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// Metal::Runtime, the Metal implementation of VideoCore::CacheBackend (plan P2.4; design:
// docs/metal/cache_backend_design.md and mtl_scheduler_design.md). Counterpart of
// renderer_vulkan/vk_runtime.cpp: the method semantics are the Vulkan ones, the recording is
// immediate on the scheduler's blit/compute/render encoders. The kernels (detiling, fault
// parsing, MSAA copies) are in mtl_runtime_kernels.mm.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <bit>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <numeric>
#include <mach/mach.h>

#include <boost/container/small_vector.hpp>

#include "common/alignment.h"
#include "common/assert.h"
#include "bbport_toggles.h"
#include "common/logging/log.h"
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_format_convert_impl.h"
#include "video_core/renderer_metal/mtl_guest_memory.h"
#include "video_core/renderer_metal/mtl_image_usage_log.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_memory_budget.h"
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"

#include <vulkan/vulkan_format_traits.hpp>

namespace Metal {
using namespace VideoCore;

namespace {

constexpr u64 StagingBlockSize = 16_MB;
constexpr u64 LargeMinGranularity = 64_KB;
constexpr u64 RingIdleFrames = 3000;
constexpr u64 LargeIdleFrames = 3000;
struct StagingAuditCounters {
    std::array<u64,3> calls{},bytes{},medium_calls{},medium_bytes{};
    bool Enabled() const {static const bool on=[] {const char* v=std::getenv("BB_MTL_STAGING_AUDIT");return v&&v[0]=='1';}();return on;}
} staging_audit;


constexpr vk::AccessFlags2 ReadMask =
    vk::AccessFlagBits2::eIndexRead | vk::AccessFlagBits2::eVertexAttributeRead |
    vk::AccessFlagBits2::eUniformRead | vk::AccessFlagBits2::eShaderRead |
    vk::AccessFlagBits2::eColorAttachmentRead | vk::AccessFlagBits2::eDepthStencilAttachmentRead |
    vk::AccessFlagBits2::eTransferRead | vk::AccessFlagBits2::eMemoryRead;

constexpr vk::AccessFlags2 WriteMask =
    vk::AccessFlagBits2::eShaderWrite | vk::AccessFlagBits2::eColorAttachmentWrite |
    vk::AccessFlagBits2::eDepthStencilAttachmentWrite | vk::AccessFlagBits2::eTransferWrite |
    vk::AccessFlagBits2::eMemoryWrite | vk::AccessFlagBits2::eTransformFeedbackWriteEXT;

// ---- Formats ----------------------------------------------------------------------------------

/// The format the texture of a guest format is made in. Metal has no 3-channel texture formats
/// and no A2B10G10R10 Snorm/Sint, R4G4 or scaled formats (VkToMtl::PixelFormat is 0 for them):
///  - RGB8/RGB16/RGB32: the RGBA format of the same type. The texel size differs from the guest's
///    (3, 6, 12 bytes against 4, 8, 16): uploads, downloads and copies through a buffer convert
///    on the GPU (mtl_format_convert.h: alpha is written as the format's one, and dropped on the
///    way back).
///  - everything else: the unsigned integer format of the same texel size, so the memory copies
///    stay byte exact; the shader gets the raw bits (the texture cache and shaders have to unpack
///    them: A2B10G10R10 Snorm/Sint).
/// Formats that have a texture format but not an exact one (D24S8, D16S8, X8D24: VkToMtl::
/// IsExactPixelFormat) are not substituted here; their transfers convert as well.
vk::Format SubstituteFormat(vk::Format format) {
    using F = vk::Format;
    if (format == F::eUndefined || VkToMtl::PixelFormat(format) != 0) {
        return format;
    }
    if (const F rgba = FormatConvert::RgbaFormatOf(format); rgba != F::eUndefined) {
        return rgba;
    }
    switch (vk::blockSize(format)) {
    case 1:
        return F::eR8Uint;
    case 2:
        return F::eR16Uint;
    case 4:
        return F::eR32Uint;
    case 8:
        return F::eR32G32Uint;
    case 16:
        return F::eR32G32B32A32Uint;
    default:
        return F::eR8G8B8A8Unorm;
    }
}

u32 BytesOf(vk::Format format) {
    return vk::blockSize(format);
}

/// Whether the Metal texture of `base` can have a view in the pixel format of `view`
/// (VkToMtl::ViewCompatible; Metal aborts on an incompatible view).
bool ViewCompatible(vk::Format base, vk::Format view) {
    return VkToMtl::ViewCompatible(base, view);
}

MTLTextureType TextureTypeOf(const vk::ImageCreateInfo& info) {
    const bool array = info.arrayLayers > 1;
    const bool ms = info.samples != vk::SampleCountFlagBits::e1;
    switch (info.imageType) {
    case vk::ImageType::e1D:
        return array ? MTLTextureType1DArray : MTLTextureType1D;
    case vk::ImageType::e3D:
        return MTLTextureType3D;
    case vk::ImageType::e2D:
    default:
        if (ms) {
            return array ? MTLTextureType2DMultisampleArray : MTLTextureType2DMultisample;
        }
        return array ? MTLTextureType2DArray : MTLTextureType2D;
    }
}

u32 SampleCountOf(vk::SampleCountFlagBits samples) {
    return static_cast<u32>(samples);
}

// ---- Image copy helpers (the statics of vk_runtime.cpp) ---------------------------------------

std::pair<u32, u32> SanitizeCopyLayers(vk::ImageType src_type, u32 src_layers, vk::ImageType dst_type,
                                       u32 dst_layers, const u32 depth) {
    if (src_type == vk::ImageType::e3D && src_layers != 1) {
        src_layers = 1;
    }
    if (dst_type == vk::ImageType::e3D && dst_layers != 1) {
        dst_layers = 1;
    }
    if (src_type == dst_type) {
        if (src_layers != dst_layers) {
            src_layers = dst_layers = std::min(src_layers, dst_layers);
        }
    } else {
        if (src_type == vk::ImageType::e2D && dst_type == vk::ImageType::e3D &&
            src_layers != depth) {
            src_layers = depth;
        }
        if (src_type == vk::ImageType::e3D && dst_type == vk::ImageType::e2D &&
            dst_layers != depth) {
            dst_layers = depth;
        }
    }
    return {src_layers, dst_layers};
}

u32 BufferImageCopySize(const vk::BufferImageCopy& copy, const vk::Format pixel_format) {
    const u32 row_length = copy.bufferRowLength ? copy.bufferRowLength : copy.imageExtent.width;
    const u32 height = copy.bufferImageHeight ? copy.bufferImageHeight : copy.imageExtent.height;

    const auto block = vk::blockExtent(pixel_format);
    const u32 block_size = vk::blockSize(pixel_format);
    const u32 row_pitch = (row_length / block[0]) * block_size;
    const u32 slice_pitch = (height / block[1]) * row_pitch;

    const u32 width_in_blocks = (copy.imageExtent.width + block[0] - 1) / block[0];
    const u32 height_in_blocks = (copy.imageExtent.height + block[1] - 1) / block[1];
    const u32 num_slices = copy.imageExtent.depth * copy.imageSubresource.layerCount;

    return (num_slices - 1) * slice_pitch + (height_in_blocks - 1) * row_pitch +
           width_in_blocks * block_size;
}

/// What a blit between a buffer and a texture needs for one vk::BufferImageCopy.
struct TextureCopy {
    NSUInteger bytes_per_row;
    NSUInteger bytes_per_image;
    MTLBlitOption options;
};

TextureCopy MakeTextureCopy(vk::Format format, const vk::BufferImageCopy& copy) {
    const vk::ImageAspectFlags aspect = copy.imageSubresource.aspectMask;
    u32 texel_bytes;
    std::array<u8, 3> block{1, 1, 1};
    MTLBlitOption options = MTLBlitOptionNone;
    const bool depth = VkToMtl::IsDepthFormat(format);
    const bool stencil = VkToMtl::HasStencil(format);
    if (depth || stencil) {
        // One aspect of the texture at a time, tightly packed.
        const bool combined = depth && stencil;
        if (aspect == vk::ImageAspectFlagBits::eStencil) {
            texel_bytes = 1;
            options = combined ? MTLBlitOptionStencilFromDepthStencil : MTLBlitOptionNone;
        } else {
            texel_bytes = format == vk::Format::eD16Unorm ? 2 : 4;
            options = combined ? MTLBlitOptionDepthFromDepthStencil : MTLBlitOptionNone;
        }
    } else {
        texel_bytes = vk::blockSize(format);
        block = vk::blockExtent(format);
    }
    const u32 row_length = copy.bufferRowLength ? copy.bufferRowLength : copy.imageExtent.width;
    const u32 height = copy.bufferImageHeight ? copy.bufferImageHeight : copy.imageExtent.height;
    const NSUInteger bytes_per_row = NSUInteger((row_length + block[0] - 1) / block[0]) * texel_bytes;
    return {bytes_per_row, NSUInteger((height + block[1] - 1) / block[1]) * bytes_per_row, options};
}

MTLOrigin OriginOf(const vk::Offset3D& offset) {
    return MTLOriginMake(NSUInteger(offset.x), NSUInteger(offset.y), NSUInteger(offset.z));
}

void EncodeBufferToTexture(id<MTLBlitCommandEncoder> blit, id<MTLBuffer> buffer,
                           id<MTLTexture> texture, vk::Format format,
                           const vk::BufferImageCopy& copy) {
    const TextureCopy layout = MakeTextureCopy(format, copy);
    const vk::Extent3D extent = copy.imageExtent;
    if (texture.textureType == MTLTextureType3D) {
        [blit copyFromBuffer:buffer
                sourceOffset:copy.bufferOffset
           sourceBytesPerRow:layout.bytes_per_row
         sourceBytesPerImage:layout.bytes_per_image
                  sourceSize:MTLSizeMake(extent.width, extent.height, extent.depth)
                   toTexture:texture
            destinationSlice:0
            destinationLevel:copy.imageSubresource.mipLevel
           destinationOrigin:OriginOf(copy.imageOffset)
                     options:layout.options];
        return;
    }
    for (u32 layer = 0; layer < copy.imageSubresource.layerCount; ++layer) {
        [blit copyFromBuffer:buffer
                sourceOffset:copy.bufferOffset + layer * layout.bytes_per_image
           sourceBytesPerRow:layout.bytes_per_row
         sourceBytesPerImage:0
                  sourceSize:MTLSizeMake(extent.width, extent.height, 1)
                   toTexture:texture
            destinationSlice:copy.imageSubresource.baseArrayLayer + layer
            destinationLevel:copy.imageSubresource.mipLevel
           destinationOrigin:OriginOf(copy.imageOffset)
                     options:layout.options];
    }
}

void EncodeTextureToBuffer(id<MTLBlitCommandEncoder> blit, id<MTLTexture> texture,
                           id<MTLBuffer> buffer, vk::Format format,
                           const vk::BufferImageCopy& copy) {
    const TextureCopy layout = MakeTextureCopy(format, copy);
    const vk::Extent3D extent = copy.imageExtent;
    if (texture.textureType == MTLTextureType3D) {
        [blit copyFromTexture:texture
                  sourceSlice:0
                  sourceLevel:copy.imageSubresource.mipLevel
                 sourceOrigin:OriginOf(copy.imageOffset)
                   sourceSize:MTLSizeMake(extent.width, extent.height, extent.depth)
                     toBuffer:buffer
            destinationOffset:copy.bufferOffset
       destinationBytesPerRow:layout.bytes_per_row
     destinationBytesPerImage:layout.bytes_per_image
                      options:layout.options];
        return;
    }
    for (u32 layer = 0; layer < copy.imageSubresource.layerCount; ++layer) {
        [blit copyFromTexture:texture
                  sourceSlice:copy.imageSubresource.baseArrayLayer + layer
                  sourceLevel:copy.imageSubresource.mipLevel
                 sourceOrigin:OriginOf(copy.imageOffset)
                   sourceSize:MTLSizeMake(extent.width, extent.height, 1)
                     toBuffer:buffer
            destinationOffset:copy.bufferOffset + layer * layout.bytes_per_image
       destinationBytesPerRow:layout.bytes_per_row
     destinationBytesPerImage:0
                      options:layout.options];
    }
}

// ---- Transfers that convert (mtl_format_convert.h) --------------------------------------------

/// What the transfer helpers record with. The converter (Runtime::Impl::Convert) is made only
/// when a copy converts.
struct TransferContext {
    CacheBackend& backend; ///< the staging memory of the converted copies
    Scheduler& scheduler;
    Runtime::Impl& impl;
};

/// A copy that converts: the conversion, and where its tightly packed host layout is in the
/// staging memory.
struct ConvertedCopy {
    FormatConvert::Conversion conversion;
    FormatConvert::Converter::Region region;
    u64 temp_offset;
};

FormatConvert::Converter::Region RegionOf(const vk::BufferImageCopy& copy) {
    return {copy.imageExtent.width, copy.imageExtent.height,
            copy.imageExtent.depth * copy.imageSubresource.layerCount};
}

/// The guest side of a copy: its buffer layout, rows and slices as the copy gives them.
FormatConvert::Converter::GuestSide GuestSideOf(id<MTLBuffer> buffer,
                                                const vk::BufferImageCopy& copy,
                                                const FormatConvert::Conversion& conversion) {
    const u32 row_length = copy.bufferRowLength ? copy.bufferRowLength : copy.imageExtent.width;
    const u32 height = copy.bufferImageHeight ? copy.bufferImageHeight : copy.imageExtent.height;
    const u32 row_pitch = row_length * conversion.guest_texel_bytes;
    return {buffer, copy.bufferOffset, row_pitch, row_pitch * height};
}

/// `copy` as the blit encoder reads or writes the host layout: tightly packed at `offset`.
vk::BufferImageCopy HostCopyOf(const vk::BufferImageCopy& copy, u64 offset) {
    vk::BufferImageCopy host = copy;
    host.bufferOffset = offset;
    host.bufferRowLength = 0;
    host.bufferImageHeight = 0;
    return host;
}

/// Plans the converted copies of `copies`: each gets a 16 byte aligned part of one staging
/// request (returned; empty when nothing converts). The staging memory is the runtime's ring or
/// large buffers: the request records the tick, so the memory is reused only after the command
/// buffer that holds the conversion completed.
StagingSpan PlanConversions(const TransferContext& ctx, vk::Format guest_format,
                            vk::Format host_format, std::span<const vk::BufferImageCopy> copies,
                            boost::container::small_vector<ConvertedCopy, 14>& converted) {
    u64 total = 0;
    for (const auto& copy : copies) {
        ConvertedCopy entry{
            .conversion = FormatConvert::Describe(guest_format, host_format,
                                                  copy.imageSubresource.aspectMask),
            .region = RegionOf(copy),
            .temp_offset = 0,
        };
        if (entry.conversion.kind != FormatConvert::Kind::None) {
            total = Common::AlignUp(total, 16);
            entry.temp_offset = total;
            total += FormatConvert::Converter::HostBytes(entry.conversion, entry.region);
        }
        converted.push_back(entry);
    }
    if (total == 0) {
        return {};
    }
    return ctx.backend.StagingRequest(total, MemoryType::DeviceLocal, 16);
}

/// Guest bytes in `guest_buffer` to `texture` (an upload): the copies that convert are rewritten
/// into the host layout by a compute pass first, the others blit directly. `host_format` is the
/// format the texture was made in.
void UploadCopies(const TransferContext& ctx, id<MTLTexture> texture, vk::Format guest_format,
                  vk::Format host_format, id<MTLBuffer> guest_buffer,
                  std::span<const vk::BufferImageCopy> copies, const char* label) {
    boost::container::small_vector<ConvertedCopy, 14> converted;
    const StagingSpan temp = PlanConversions(ctx, guest_format, host_format, copies, converted);
    if (temp.buffer) {
        ctx.scheduler.ComputeEncoder(label, true);
        id<MTLComputeCommandEncoder> compute = ctx.scheduler.GetImpl().compute;
        for (size_t i = 0; i < copies.size(); ++i) {
            const ConvertedCopy& entry = converted[i];
            if (entry.conversion.kind == FormatConvert::Kind::None) {
                continue;
            }
            ctx.impl.Convert().Encode(compute, entry.conversion, true,
                                 GuestSideOf(guest_buffer, copies[i], entry.conversion),
                                 {NativeBuffer(*temp.buffer), temp.offset + entry.temp_offset},
                                 entry.region);
        }
    }
    ctx.scheduler.BlitEncoder(label);
    id<MTLBlitCommandEncoder> blit = ctx.scheduler.GetImpl().blit;
    for (size_t i = 0; i < copies.size(); ++i) {
        if (converted[i].conversion.kind == FormatConvert::Kind::None) {
            EncodeBufferToTexture(blit, guest_buffer, texture, host_format, copies[i]);
        } else {
            EncodeBufferToTexture(blit, NativeBuffer(*temp.buffer), texture, host_format,
                                  HostCopyOf(copies[i], temp.offset + converted[i].temp_offset));
        }
    }
}

/// `texture` to guest bytes in `guest_buffer` (a download): converting copies blit into the host
/// layout first and a compute pass writes the guest layout.
void DownloadCopies(const TransferContext& ctx, id<MTLTexture> texture, vk::Format guest_format,
                    vk::Format host_format, id<MTLBuffer> guest_buffer,
                    std::span<const vk::BufferImageCopy> copies, const char* label) {
    boost::container::small_vector<ConvertedCopy, 14> converted;
    const StagingSpan temp = PlanConversions(ctx, guest_format, host_format, copies, converted);
    ctx.scheduler.BlitEncoder(label);
    id<MTLBlitCommandEncoder> blit = ctx.scheduler.GetImpl().blit;
    for (size_t i = 0; i < copies.size(); ++i) {
        if (converted[i].conversion.kind == FormatConvert::Kind::None) {
            EncodeTextureToBuffer(blit, texture, guest_buffer, host_format, copies[i]);
        } else {
            EncodeTextureToBuffer(blit, texture, NativeBuffer(*temp.buffer), host_format,
                                  HostCopyOf(copies[i], temp.offset + converted[i].temp_offset));
        }
    }
    if (!temp.buffer) {
        return;
    }
    ctx.scheduler.ComputeEncoder(label, true);
    id<MTLComputeCommandEncoder> compute = ctx.scheduler.GetImpl().compute;
    for (size_t i = 0; i < copies.size(); ++i) {
        const ConvertedCopy& entry = converted[i];
        if (entry.conversion.kind == FormatConvert::Kind::None) {
            continue;
        }
        ctx.impl.Convert().Encode(compute, entry.conversion, false,
                             GuestSideOf(guest_buffer, copies[i], entry.conversion),
                             {NativeBuffer(*temp.buffer), temp.offset + entry.temp_offset},
                             entry.region);
    }
}

BackendLimits MakeLimits(Device& device) {
    id<MTLDevice> mtl = device.GetImpl().device;
    BackendLimits limits{};
    // Apple GPUs bind buffers at 16 byte aligned offsets (the debug layer checks it); no host
    // cache maintenance exists in shared storage.
    limits.uniform_min_alignment = 16;
    limits.storage_min_alignment = 16;
    limits.non_coherent_atom_size = 1;
    limits.max_sampler_anisotropy = 16.0f;
    // MTLSamplerDescriptor.lodBias (S4.6, [-16, 15.999]) exists from macOS 26; the shared sampler
    // code clamps the bias to this limit, symmetric: the largest S4.6 value below 16. Earlier
    // systems have no bias, the guest's and the upscaler's scene mip bias then drop.
    if (@available(macOS 26.0, *)) {
        limits.max_sampler_lod_bias = 15.984375f;
    } else {
        limits.max_sampler_lod_bias = 0.0f;
        LOG_WARNING(Render, "Metal: sampler LOD bias needs macOS 26; the guest's and the "
                            "upscaler's mip bias are ignored");
    }
    limits.max_sampler_allocation_count =
        static_cast<u32>(std::min<NSUInteger>(mtl.maxArgumentBufferSamplerCount, 1u << 20));
    limits.total_memory_budget = mtl.recommendedMaxWorkingSetSize;
    limits.integrated = mtl.hasUnifiedMemory;
    limits.can_report_memory_usage = true;
    limits.image_view_min_lod = false;
    limits.anisotropic_filtering = true;
    limits.custom_border_color = false;
    limits.image_2d_view_of_3d = false;
    // The Metal debug layer: "BC1_RGBA not compatible with texture view RG32Uint".
    limits.block_texel_view = false;
    limits.attachment_feedback_loop_layout = false;
    limits.max_framebuffer_width = 16384;
    limits.max_framebuffer_height = 16384;
    // The viewport of the clip-disabled path (screen-space positions, RectList) is this size.
    limits.max_viewport_width = 16384;
    limits.max_viewport_height = 16384;
    // P10.1 (docs/metal/texture_compression_design.md): the texture cache creates images without
    // ShaderWrite and PixelFormatView and promotes them on first need, so the GPU keeps them
    // losslessly compressed. BB_MTL_FULL_USAGE=1 restores the usages every image had.
    const char* full_usage = std::getenv("BB_MTL_FULL_USAGE");
    limits.lean_image_usage = !(full_usage && full_usage[0] == '1');
    const char* page_table=std::getenv("BB_MTL_TWO_LEVEL_BDA");
    limits.two_level_bda=!(page_table && page_table[0]=='0');
    LOG_INFO(Render, "Metal: texture cache images are created {}",
             limits.lean_image_usage
                 ? "lean (ShaderWrite and PixelFormatView on first need, BB_MTL_FULL_USAGE=1 "
                   "restores them for every image)"
                 : "with ShaderWrite and PixelFormatView (BB_MTL_FULL_USAGE=1)");
    return limits;
}

} // namespace

// ---- BufferAccessTracker ----------------------------------------------------------------------

struct BufferAccessTracker::Audit {
    static constexpr size_t Resources=64, Intervals=256;
    struct Ref {const void* key;std::array<Range,Intervals> ranges;size_t size;bool complete;};
    std::array<Ref,Resources> refs;
    size_t used=0;
    AuditStats stats{};
    unsigned printed=0;
    std::chrono::steady_clock::time_point reported{};
    Ref* Lookup(const void* key) {for(size_t i=0;i<used;++i)if(refs[i].key==key)return &refs[i];return nullptr;}
    void Insert(const void* key,u64 start,u64 end,u8 access) {
        ++stats.inserts;
        Ref* ref=Lookup(key);
        if(!ref) {
            if(used==Resources)return;
            ref=&refs[used++];ref->key=key;ref->size=0;ref->complete=true;
        }
        if(!ref->complete)return;
        // Exact union of overlapping/adjacent intervals of the SAME access class only.
        for(size_t i=0;i<ref->size;) {
            const auto& r=ref->ranges[i];
            if(r.start<=start && end<=r.end && (r.access&access)==access)return;
            const bool separate=(end<r.start && r.start-end>1) || (r.end<start && start-r.end>1);
            if(r.access==access && !separate) {
                start=std::min(start,r.start);end=std::max(end,r.end);
                ref->ranges[i]=ref->ranges[--ref->size];i=0;
            } else ++i;
        }
        if(ref->size==Intervals){ref->complete=false;return;}
        ref->ranges[ref->size++]={start,end,access};
    }
    void Query(const void* key,u64 start,u64 end,u8 access,bool actual) {
        ++stats.queries;const auto* ref=Lookup(key);
        if(!ref || !ref->complete)++stats.unknown;
        else {
            bool exact=false;
            for(size_t i=0;i<ref->size;++i){const auto& r=ref->ranges[i];if((r.access&access) && r.start<=end && start<=r.end){exact=true;break;}}
            if(!actual && exact)++stats.missed;
            if(actual && !exact) {
                if(access==Write)++stats.false_reads;else ++stats.false_writes;
                if(printed<8) {++printed;std::printf("Metal buffer range false-hit: resource=%p start=%llu end=%llu access=%u exact-intervals=%zu\n",key,(unsigned long long)start,(unsigned long long)end,unsigned(access),ref->size);}
            }
        }
        const auto now=std::chrono::steady_clock::now();
        if(now-reported>=std::chrono::seconds(5)) {
            std::printf("Metal buffer range audit: frame=%llu inserts=%llu collapses=%llu mixed=%llu queries=%llu false-read-queries=%llu false-write-queries=%llu unknown=%llu missed=%llu; bounded exact reference, diagnostic only\n",(unsigned long long)BbStats::frame_number.load(std::memory_order_relaxed),(unsigned long long)stats.inserts,(unsigned long long)stats.collapses,(unsigned long long)stats.mixed,(unsigned long long)stats.queries,(unsigned long long)stats.false_reads,(unsigned long long)stats.false_writes,(unsigned long long)stats.unknown,(unsigned long long)stats.missed);reported=now;
        }
    }
};
BufferAccessTracker::BufferAccessTracker() {
    static_assert(sizeof(Audit)<400*1024);
    const char* flag=std::getenv("BB_MTL_BUFFER_RANGE_AUDIT");
    if(flag && flag[0]=='1')audit=std::make_unique<Audit>();
}
BufferAccessTracker::~BufferAccessTracker()=default;
void BufferAccessTracker::Clear() {entries.clear();if(audit)audit->used=0;}
BufferAccessTracker::AuditStats BufferAccessTracker::AuditSnapshot() const {return audit?audit->stats:AuditStats{};}

void BufferAccessTracker::Insert(const void* resource, u64 start, u64 end, u8 access) {
    if(audit)audit->Insert(resource,start,end,access);
    auto& ranges = entries[resource];
    for (Range& range : ranges) {
        if (range.start <= start && end <= range.end && (range.access & access) == access) {
            return;
        }
    }
    if (ranges.size() >= MaxRanges) {
        if(audit) {++audit->stats.collapses;u8 classes=access;for(const auto& r:ranges)classes|=r.access;if(classes==(Read|Write))++audit->stats.mixed;}
        Range bound{start, end, access};
        for (const Range& range : ranges) {
            bound.start = std::min(bound.start, range.start);
            bound.end = std::max(bound.end, range.end);
            bound.access |= range.access;
        }
        ranges.assign(1, bound);
        return;
    }
    ranges.push_back({start, end, access});
}

bool BufferAccessTracker::Find(const void* resource, u64 start, u64 end, u8 access) const {
    bool found=false;
    if(const auto it=entries.find(resource);it!=entries.end())
        for(const auto& range:it->second)
            if((range.access&access) && range.start<=end && start<=range.end){found=true;break;}
    if(audit)audit->Query(resource,start,end,access,found);
    return found;
}

// ---- StagingPool ------------------------------------------------------------------------------

StagingPool::StagingPool(Runtime& backend_) : backend{backend_} {
    const char* env = std::getenv("BB_STAGING_KEEP_MB");
    keep_blocks = (env ? std::strtoull(env, nullptr, 10) : 512) * 1_MB / StagingBlockSize;
    if(const char* hot=std::getenv("BB_MTL_HOT_STAGING_REUSE");hot&&hot[0]=='1')
        std::printf("Metal staging recent reuse: enabled; DeviceLocal only; original fences, capacity and idle policy\n");
}

StagingPool::~StagingPool() = default;

StagingSpan StagingPool::Request(u64 size, MemoryType type, u64 alignment, bool deferred) {
    ASSERT_MSG(u32(type) < NumTypes, "Staging memory of type {}", u32(type));
    if(staging_audit.Enabled()) {
        const auto i=u32(type);++staging_audit.calls[i];staging_audit.bytes[i]+=size;
        if(size>StagingBlockSize/2 && size<=StagingBlockSize){++staging_audit.medium_calls[i];staging_audit.medium_bytes[i]+=size;}
    }

    Ring& ring = rings[u32(type)];
    if (deferred || size > StagingBlockSize) {
        return RequestLarge(size, type, deferred);
    }
    return RequestFromRing(ring, size, alignment, type);
}

StagingSpan StagingPool::RequestFromRing(Ring& ring, u64 size, u64 alignment, MemoryType type) {
    Block* best{};
    u64 best_offset{};
    static const bool recent=[] {const char* v=std::getenv("BB_MTL_HOT_STAGING_REUSE");return v&&v[0]=='1';}();
    if (recent && type==MemoryType::DeviceLocal) {
        // Keep a hot subset active; cold blocks retain their slots/capacity and
        // age through the existing idle policy. Reserve remains the only range
        // and completion authority, with the original no-wait behavior.
        const auto slot=BbStagingRecent::Try(ring.blocks,ring.current,[&](size_t i) {
            return ring.blocks[i].buffer->Reserve(size,alignment,false);
        });
        if(slot) {ring.current=slot->index;best=&ring.blocks[slot->index];best_offset=slot->offset;}
    } else {
    for (size_t i = 0; i < ring.blocks.size(); ++i) {
        const size_t index = (ring.current + i) % ring.blocks.size();
        Block* block = &ring.blocks[index];
        if (const auto offset = block->buffer->Reserve(size, alignment, false)) {
            ring.current = index;
            best = block;
            best_offset = *offset;
            break;
        }
    }
    }
    if (!best) {
        static const bool adaptive=[] {const char* v=std::getenv("BB_MTL_ADAPTIVE_STAGING");return v&&v[0]=='1';}();
        // Large in-ring requests cannot share a 16 MiB block with a second such request.
        // Use a fitting size instead of retaining up to half a block as unusable tail.
        // Existing blocks, ring watches, wait policy and retained-byte budget are unchanged.
        const u64 block_size=adaptive && type==MemoryType::DeviceLocal && size>StagingBlockSize/2
            ? Common::AlignUp(size,LargeMinGranularity) : StagingBlockSize;
        best = &ring.blocks.emplace_back(Block{
            .buffer = std::make_unique<StreamBuffer>(backend, type, block_size),
            .last_used_frame = frame,
        });
        ring.current = ring.blocks.size() - 1;
        best_offset = *best->buffer->Reserve(size, alignment, false);
    }
    auto& buffer = *best->buffer;
    if (best->discarded) {
        backend.WakeStaging(buffer);
        best->discarded = false;
    }
    MemoryStats::Reclassify(buffer.buffer.native, MemoryStats::Kind::Staging);
    best->last_used_frame = frame;
    StagingSpan span{
        .buffer = &buffer,
        .offset = best_offset,
        .size = size,
        .type = type,
    };
    if (!buffer.mapped_data.empty()) {
        span.mapped = buffer.mapped_data.data() + best_offset;
    }
    return span;
}

StagingSpan StagingPool::RequestLarge(u64 size, MemoryType type, bool deferred) {
    auto& cache = large_caches[u32(type)];
    const u64 step = std::max<u64>(std::bit_floor(size) / 4, LargeMinGranularity);
    const u64 rounded = Common::AlignUp(size, step);
    const u64 max_size = std::max(size + size / 4, rounded);

    LargeBuffer* best{};
    for (LargeBuffer& entry : cache) {
        const u64 entry_size = entry.buffer->SizeBytes();
        if (entry.held || entry_size < size || entry_size > max_size || !backend.IsFree(entry.tick)) {
            continue;
        }
        if (!best || entry_size < best->buffer->SizeBytes()) {
            best = &entry;
        }
    }
    if (!best) {
        best = &cache.emplace_back(LargeBuffer{
            .buffer = std::make_unique<Buffer>(backend, 0, rounded, type,
                                               "StagingPool:Dedicated"),
            .tick = 0,
            .last_used_frame = frame,
            .held = false,
        });
    }
    MemoryStats::Reclassify(best->buffer->buffer.native, MemoryStats::Kind::Staging);
    best->tick = backend.CurrentTick();
    best->last_used_frame = frame;
    best->held = deferred;

    Buffer& buffer = *best->buffer;
    StagingSpan span{
        .buffer = &buffer,
        .offset = 0,
        .size = size,
        .type = type,
    };
    if (!buffer.mapped_data.empty()) {
        span.mapped = buffer.mapped_data.data();
    }
    return span;
}

void StagingPool::FreeDeferred(const StagingSpan& span) {
    auto& cache = large_caches[u32(span.type)];
    const auto it = std::ranges::find_if(
        cache, [&](const LargeBuffer& entry) { return entry.buffer.get() == span.buffer; });
    ASSERT_MSG(it != cache.end() && it->held, "FreeDeferred on an allocation that is not held");
    it->held = false;
    it->tick = backend.CurrentTick();
    it->last_used_frame = frame;
}

void StagingPool::TickFrame() {
    static u64 captured_sequence=0;
    const u64 sequence=FrameCapture::NativeCaptureSequence();
    if(sequence && sequence!=captured_sequence) {
        captured_sequence=sequence;
        MemoryStats::CaptureLifetimes();
        unsigned count=0;
        for(unsigned type=0;type<NumTypes;++type) {
            for(const auto& b:rings[type].blocks) {
                if(++count>256)break;
                FrameCapture::Native(std::format("{{\"op\":\"staging_lifetime\",\"native\":{},\"type\":{},\"large\":false,\"bytes\":{},\"age_ticks\":{},\"tick\":{},\"gpu_complete\":{},\"held\":false,\"discarded\":{}}}",uintptr_t(b.buffer->buffer.native),type,b.buffer->SizeBytes(),frame-b.last_used_frame,b.buffer->LastTick(),backend.IsFreeKnown(b.buffer->LastTick()),b.discarded));
            }
            for(const auto& b:large_caches[type]) {
                if(++count>256)break;
                FrameCapture::Native(std::format("{{\"op\":\"staging_lifetime\",\"native\":{},\"type\":{},\"large\":true,\"bytes\":{},\"age_ticks\":{},\"tick\":{},\"gpu_complete\":{},\"held\":{},\"discarded\":false}}",uintptr_t(b.buffer->buffer.native),type,b.buffer->SizeBytes(),frame-b.last_used_frame,b.tick,backend.IsFreeKnown(b.tick),b.held));
            }
        }
        if(count>256)FrameCapture::Native("{\"op\":\"lifetime_overflow\",\"kind\":\"staging\"}");
    }
    static const bool audit=[] {const char* v=std::getenv("BB_MTL_STAGING_AUDIT");return v&&v[0]=='1';}();
    if(audit && frame%300==0) {
        for(unsigned i=0;i<3;++i)std::printf("Metal staging requests: frame=%llu type=%u calls=%llu bytes=%llu medium-calls=%llu medium-bytes=%llu\n",(unsigned long long)frame,i,(unsigned long long)staging_audit.calls[i],(unsigned long long)staging_audit.bytes[i],(unsigned long long)staging_audit.medium_calls[i],(unsigned long long)staging_audit.medium_bytes[i]);

        u64 ring_bytes=0,idle600=0,idle3000=0,discarded=0,age=0;
        for(const auto& ring:rings)for(const auto& b:ring.blocks) {
            ring_bytes+=b.buffer->SizeBytes();age=std::max(age,frame-b.last_used_frame);
            if(b.discarded){discarded+=b.buffer->SizeBytes();continue;}
            if(!backend.IsFreeKnown(b.buffer->LastTick()))continue;
            if(frame-b.last_used_frame>=600)idle600+=b.buffer->SizeBytes();
            if(frame-b.last_used_frame>=3000)idle3000+=b.buffer->SizeBytes();
        }
        std::printf("Metal staging audit: frame=%llu ring-bytes=%llu idle600-retired=%llu idle3000-retired=%llu discarded-capacity=%llu oldest-age=%llu\n",(unsigned long long)frame,(unsigned long long)ring_bytes,(unsigned long long)idle600,(unsigned long long)idle3000,(unsigned long long)discarded,(unsigned long long)age);
    }
    ++frame;
    for (Ring& ring : rings) {
        TrimRing(ring);
    }
    for (auto& cache : large_caches) {
        TrimLarge(cache);
    }
}

void StagingPool::TrimRing(Ring& ring) {
    auto& blocks = ring.blocks;
    u64 cached_bytes=0;for(const auto& block:blocks)cached_bytes+=block.buffer->SizeBytes();
    for (size_t i = blocks.size(); i-- > 0;) {
        Block& block = blocks[i];
        if (frame - block.last_used_frame < RingIdleFrames ||
            !backend.IsFreeKnown(block.buffer->LastTick())) {
            continue;
        }
        if (cached_bytes <= keep_blocks * StagingBlockSize) {
            if (!block.discarded) block.discarded = backend.DiscardIdleStaging(*block.buffer);
            continue;
        }
        cached_bytes-=block.buffer->SizeBytes();
        blocks.erase(blocks.begin() + i);
        if (i < ring.current) {
            --ring.current;
        } else if (i == ring.current) {
            ring.current = 0;
        }
    }
    if (ring.current >= blocks.size()) {
        ring.current = 0;
    }
}

void StagingPool::TrimLarge(std::vector<LargeBuffer>& cache) {
    std::erase_if(cache, [this](const LargeBuffer& entry) {
        return !entry.held && frame - entry.last_used_frame >= LargeIdleFrames &&
               backend.IsFreeKnown(entry.tick);
    });
}

// ---- Runtime: construction, ticks, queries ------------------------------------------------------

// Source access provenance only. It is not a consumer dependency graph and cannot justify
// dropping barriers. Store integer identities, never resource ownership or payloads.
struct Runtime::BarrierAudit {
    struct Range { uintptr_t id; u64 offset, size, stages, access; };
    std::array<Range,8> ranges{};
    u64 reads=0, writes=0, read_stages=0, write_stages=0;
    u64 global_reads=0, global_writes=0;
    unsigned count=0, overflow=0;
    void Note(bool global, const Buffer* buffer, u64 offset, u64 size,
              vk::PipelineStageFlags2 stages, vk::AccessFlags2 access) {
        const bool read=bool(access&ReadMask), write=bool(access&WriteMask);
        if(read) {++reads;read_stages|=u64(VkPipelineStageFlags2(stages));if(global)++global_reads;}
        if(write) {++writes;write_stages|=u64(VkPipelineStageFlags2(stages));if(global)++global_writes;}
        if(!global && (read||write)) {
            if(count<ranges.size())ranges[count++]={uintptr_t(buffer),offset,size,
                u64(VkPipelineStageFlags2(stages)),u64(VkAccessFlags2(access))};
            else ++overflow;
        }
    }
    void Capture(uintptr_t queue) const {
        std::string list;
        for(unsigned i=0;i<count;++i) {
            const auto& r=ranges[i];if(i)list+=',';
            list+=std::format("{{\"id\":{},\"offset\":{},\"size\":{},\"stages\":{},\"access\":{}}}",
                r.id,r.offset,r.size,r.stages,r.access);
        }
        FrameCapture::Native(std::format(
            "{{\"op\":\"barrier_sources\",\"queue\":{},\"reads\":{},\"writes\":{},\"read_stages\":{},\"write_stages\":{},\"global_reads\":{},\"global_writes\":{},\"overflow\":{},\"ranges\":[{}]}}",
            queue,reads,writes,read_stages,write_stages,global_reads,global_writes,overflow,list));
    }
};

Runtime::Runtime(Device& device_, Scheduler& scheduler_)
    : CacheBackend{MakeLimits(device_)}, device{device_}, scheduler{scheduler_},
      impl{std::make_unique<Impl>(device_, scheduler_)}, staging_pool{*this} {
    if(FrameCapture::NativeMode())barrier_audit=std::make_unique<BarrierAudit>();
    GuestMemory::UseAsChunkProvider(); // the caches made after this ask Metal about chunks
}

Runtime::~Runtime() {
    CloseExactExportOwner(); // blocks only shutdown, makes escaped tokens harmless
    // Pipelines and the objects the caches still hold may be in use by the last command buffers.
    scheduler.Finish();
    ImageUsageLog::Flush();
}

void Runtime::RegisterBdaLeaf(const Buffer* buffer,bool attach) {
    impl->dev.BdaLeaf(NativeBuffer(*buffer),attach);
}

void Runtime::TickFrame() {
    staging_pool.TickFrame();
    ImageUsageLog::Tick();
    MemoryStats::Report((__bridge const void*)impl->dev.device);
    BbStats::metal_allocated_bytes.store(impl->dev.device.currentAllocatedSize,
                                         std::memory_order_relaxed);
}

StagingSpan Runtime::StagingRequest(u64 size, MemoryType type, u64 alignment, bool deferred) {
    return staging_pool.Request(size, type, alignment, deferred);
}

void Runtime::StagingFreeDeferred(const StagingSpan& span) {
    staging_pool.FreeDeferred(span);
}

u64 Runtime::CurrentTick() const noexcept {
    return scheduler.CurrentTick();
}

bool Runtime::IsFree(u64 tick) noexcept {
    return scheduler.IsFree(tick);
}

bool Runtime::IsFreeKnown(u64 tick) const noexcept {
    return scheduler.IsFreeKnown(tick);
}

void Runtime::RefreshTicks() {
    scheduler.RefreshTicks();
}

void Runtime::Wait(u64 tick) {
    scheduler.Wait(tick);
}

void Runtime::Finish() {
    scheduler.Finish();
}

void Runtime::DeferOperation(Common::UniqueFunction<void>&& func) {
    scheduler.DeferOperation(std::move(func));
}

void Runtime::DeferPriorityOperation(Common::UniqueFunction<void>&& func) {
    scheduler.DeferPriorityOperation(std::move(func));
}

void Runtime::PopPendingOperations() {
    scheduler.PopPendingOperations();
}

void Runtime::EndRendering() {
    scheduler.EndRendering();
}

bool Runtime::IsRendering() const noexcept {
    return scheduler.IsRendering();
}

vk::Format Runtime::SupportedFormat(vk::Format format, vk::FormatFeatureFlags2) const {
    return SubstituteFormat(format);
}

bool Runtime::ViewFormatCompatible(vk::Format base, vk::Format view) const {
    return ViewCompatible(base, SubstituteFormat(view));
}

// bmy/bbport-mac 4de5fd1: sampling is limited to four times a second.
// Graphics allocations already include Metal/driver-owned memory. Subtracting
// footprint minus currentAllocatedSize instead would misclassify those bytes as
// non-graphics and shrink the cache as its own resources grow (bmy issue #6).
static MemoryBudget::ProcessMemory CurrentProcessMemory() {
    static std::atomic<u64> outside_cached{0}, graphics_cached{0};
    static std::atomic<s64> sampled_ns{-1};
    const s64 now = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    const s64 sampled = sampled_ns.load(std::memory_order_relaxed);
    if (sampled < 0 || now - sampled >= 250'000'000) {
        task_vm_info_data_t info{};
        mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
        if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info),
                      &count) == KERN_SUCCESS && count >= TASK_VM_INFO_REV3_COUNT) {
            const auto memory = MemoryBudget::FromLedger(info.phys_footprint,
                info.ledger_tag_graphics_footprint + info.ledger_tag_graphics_footprint_compressed);
            outside_cached.store(memory.outside_graphics, std::memory_order_relaxed);
            graphics_cached.store(memory.graphics, std::memory_order_relaxed);
            sampled_ns.store(now, std::memory_order_relaxed);
        }
    }
    return {outside_cached.load(std::memory_order_relaxed),
            graphics_cached.load(std::memory_order_relaxed),
            sampled_ns.load(std::memory_order_relaxed) >= 0};
}

u64 Runtime::DeviceMemoryUsage() const {
    return MemoryBudget::GraphicsUsage(impl->dev.device.currentAllocatedSize,
                                      GuestMemory::ImportedBytes(), CurrentProcessMemory());
}

u64 Runtime::DeviceMemoryBudgetNow() const {
    const u64 limit = impl->dev.device.recommendedMaxWorkingSetSize;
    const auto process = CurrentProcessMemory();
    const u64 budget = MemoryBudget::Unified(limit, process);
    static std::atomic<u64> printed_budget{0}, printed_graphics{0};
    const auto moved = [](u64 a, u64 b) {
        return (a > b ? a - b : b - a) >= (u64{1} << 30);
    };
    u64 last_budget = printed_budget.load(std::memory_order_relaxed);
    u64 last_graphics = printed_graphics.load(std::memory_order_relaxed);
    const bool budget_moved = moved(last_budget, budget) &&
        printed_budget.compare_exchange_strong(last_budget, budget, std::memory_order_relaxed);
    const bool graphics_moved = moved(last_graphics, process.graphics) &&
        printed_graphics.compare_exchange_strong(last_graphics, process.graphics, std::memory_order_relaxed);
    if (budget_moved || graphics_moved) {
        std::printf("Metal memory budget: %llu MiB (recommended %llu MiB, outside graphics %llu MiB); "
                    "graphics ledger %llu MiB, adjusted graphics %llu MiB, no-copy aliases %llu MiB; ledger %s\n",
                    (unsigned long long)(budget >> 20), (unsigned long long)(limit >> 20),
                    (unsigned long long)(process.outside_graphics >> 20),
                    (unsigned long long)(process.graphics >> 20),
                    (unsigned long long)(DeviceMemoryUsage() >> 20),
                    (unsigned long long)(GuestMemory::ImportedBytes() >> 20), process.valid ? "valid" : "unavailable");
    }
    return budget;
}

bool Runtime::GuestMemoryUsable() {
    return GuestMemory::Wanted();
}

bool Runtime::CopyCompatible(const Image& image, const char* what) {
    const vk::Format guest = image.info.pixel_format;
    const vk::Format host = image.backing->image.image_ci.format;
    // The formats the transfers convert (RGB8/16/32, D24S8, D16S8, X8D24: mtl_format_convert.h).
    // MakeTextureCopy sizes the depth aspect of D16S8 as 4 bytes (right for the Metal texture,
    // not for the 2 byte guest layout); no converted path uses it for that: the host layout of a
    // converted copy is 4 bytes per texel by design, the guest side is the converter's.
    if (FormatConvert::Convertible(guest, host)) {
        return true;
    }
    const bool same_size = guest == host || BytesOf(guest) == BytesOf(host);
    if (same_size && VkToMtl::IsExactPixelFormat(guest)) {
        return true;
    }
    // Nothing converts this one: never silent (the count is for tests)
    FormatConvert::UnhandledCount().fetch_add(1, std::memory_order_relaxed);
    if (impl->FirstTime(std::string("copy ") + vk::to_string(guest))) {
        LOG_ERROR(Render, "Metal {}: {} is held as {} (texel size or range differs); transfers of "
                          "such images are skipped",
                  what, vk::to_string(guest), vk::to_string(host));
    }
    return false;
}

// ---- Access tracking ----------------------------------------------------------------------------

void Runtime::AccessBuffer(const Buffer* handle, u64 offset, u64 size,
                           vk::PipelineStageFlags2 src_stage, vk::AccessFlags2 src_access) {
    if(barrier_audit)barrier_audit->Note(false,handle,offset,size,src_stage,src_access);
    if(FrameCapture::NativeRecording() && (src_access&(ReadMask|WriteMask)))FrameCapture::NativeBufferUse(uintptr_t(handle),
        handle?uintptr_t(handle->buffer.native):0,offset,size,0,7,0,bool(src_access&WriteMask),
        "runtime",u64(VkPipelineStageFlags2(src_stage)));
    u64 start = offset;
    u64 end = offset + std::max<u64>(size, 1) - 1;
    u8 access = 0;
    if (src_access & WriteMask) {
        ++buffer_write_epoch;
        access |= BufferAccessTracker::Write;
    } else {
        // Reads at 4 KiB granularity (Vulkan::Runtime does the same): constants come from ring
        // allocations at a new offset every draw.
        start &= ~u64{0xFFF};
        end |= 0xFFF;
    }
    if (src_access & ReadMask) {
        access |= BufferAccessTracker::Read;
    }
    if (access) {
        tracker.Insert(handle, start, end, access);
    }
    // Reads by blit commands need no barrier: buffers are hazard tracked (D7) and the writer
    // that follows is another encoder command on a bound buffer.
    if (src_stage && src_access != vk::AccessFlagBits2::eTransferRead) {
        memory_pending = true;
    }
}

bool Runtime::IsBufferAccessed(const Buffer* handle, u64 offset, u64 size, bool check_read_access) {
    if (global_write) {
        return true; // bbport: a paged write may have touched it (AccessGlobal)
    }
    const u64 end = offset + std::max<u64>(size, 1) - 1;
    u8 access = BufferAccessTracker::Write;
    if (check_read_access) {
        access |= BufferAccessTracker::Read;
    }
    return tracker.Find(handle, offset, end, access);
}

void Runtime::NoteOrderedGuestBufferWrite() noexcept {
    static const bool enabled = [] {
        const char* value = std::getenv("BB_MTL_QUERY_WRITE_EPOCH");
        return !value || value[0] != '0'; // diagnostic old-behavior reference only
    }();
    if (enabled) ++buffer_write_epoch;
}

void Runtime::AccessGlobal(vk::PipelineStageFlags2 src_stage, vk::AccessFlags2 src_access) {
    if(barrier_audit)barrier_audit->Note(true,nullptr,0,0,src_stage,src_access);
    if(FrameCapture::NativeRecording() && (src_access&(ReadMask|WriteMask)))FrameCapture::NativeBufferUse(0,0,0,0,0,7,0,
        bool(src_access&WriteMask),"global",u64(VkPipelineStageFlags2(src_stage)));
    if (src_stage) {
        memory_pending = true;
    }
    if (src_access & vk::AccessFlagBits2::eShaderWrite) {
        ++buffer_write_epoch;
        global_write = true;
    }
}

bool Runtime::Transit(Image* image, vk::ImageLayout dst_layout, vk::PipelineStageFlags2 dst_stage,
                      vk::AccessFlags2 dst_access, std::optional<SubresourceRange> subres_range) {
    BeforeImageAccess();
    // bbport (P7.5): a native use of a proxied scene image resolves its proxy first (this records
    // a copy pass, which ends the open render pass: Transit comes before the pass of a draw is
    // opened, the core's BindResources and BeginRendering order).
    if (scene_targets) scene_targets->NativeAccess(*image, dst_access, bool(dst_stage & vk::PipelineStageFlagBits2::eClear));
    // The bookkeeping of the shared image state only: Metal tracks the hazards of textures
    // itself and has no layouts.
    scratch_barriers.clear();
    image->GetBarriers(scratch_barriers, dst_layout, dst_access, dst_stage, subres_range);
    const bool changed = !scratch_barriers.empty();
    scratch_barriers.clear();
    return changed;
}

void Runtime::FlushImageBarriers() {
    BeforeImageAccess();
}

void Runtime::FlushBarriers() {
    BeforeImageAccess();
    if (memory_pending) {
        if(barrier_audit && FrameCapture::NativeRecording())barrier_audit->Capture(uintptr_t(&scheduler));
        scheduler.MemoryBarrier();
    }
    memory_pending = false;
    global_write = false;
    tracker.Clear();
    if(barrier_audit)*barrier_audit={};
}

// ---- Objects ------------------------------------------------------------------------------------

namespace {
/// BB_FRAME_STATS: Metal buffers and textures created, with their bytes, every 5 s (P6.1: the
/// driver wires each new allocation on first use).
struct CreationStats {
    std::atomic<u64> buffers{}, buffer_bytes{}, textures{}, texture_bytes{};
    std::atomic<s64> last_ns{};
    const bool enabled{std::getenv("BB_FRAME_STATS") != nullptr};

    void Note(bool texture, u64 bytes) {
        if (!enabled) {
            return;
        }
        (texture ? textures : buffers).fetch_add(1, std::memory_order_relaxed);
        (texture ? texture_bytes : buffer_bytes).fetch_add(bytes, std::memory_order_relaxed);
        const s64 now = std::chrono::steady_clock::now().time_since_epoch().count();
        s64 last = last_ns.load(std::memory_order_relaxed);
        if (now - last < 5'000'000'000 || !last_ns.compare_exchange_strong(last, now)) {
            return;
        }
        std::printf("Metal objects created in the last 5 s: %llu buffers (%llu MiB), %llu textures "
                    "(%llu MiB)\n",
                    (unsigned long long)buffers.exchange(0), (unsigned long long)(buffer_bytes.exchange(0) >> 20),
                    (unsigned long long)textures.exchange(0), (unsigned long long)(texture_bytes.exchange(0) >> 20));
    }
};
CreationStats creation_stats;

/// Every buffer and texture of the caches in the residency set for its whole life (P6.1): outside
/// it the driver made each allocation resident per command buffer and unwired it once no command
/// buffer in flight used it (~55000 wiring changes a second, submits stalled in the kernel).
/// BB_MTL_RESIDENT_ALL=0: only buffers with a device address, as before (A/B).
bool ResidentAll() {
    static const bool all = [] {
        const char* env = std::getenv("BB_MTL_RESIDENT_ALL");
        return !(env && env[0] == '0');
    }();
    return all;
}
} // namespace

BufferMemory Runtime::CreateBuffer(UniqueBuffer& out, const vk::BufferCreateInfo& info,
                                   MemoryType type) {
    ASSERT_MSG(type != MemoryType::Sparse, "Metal has no sparse buffers (GetSparseArena is null)");
    const u64 size = std::max<u64>(info.size, 1);
    id<MTLDevice> mtl = impl->dev.device;
    ASSERT_MSG(size <= mtl.maxBufferLength, "Buffer of {} bytes exceeds the Metal limit {}", size,
               static_cast<u64>(mtl.maxBufferLength));
    @autoreleasepool {
        // Shared storage: one memory (Apple silicon), the pointer is the CPU's view. Hazard
        // tracked (D7).
        id<MTLBuffer> buffer = [mtl newBufferWithLength:size
                                                options:MTLResourceStorageModeShared |
                                                        MTLResourceHazardTrackingModeTracked];
        creation_stats.Note(false, size);
        ASSERT_MSG(buffer, "Failed allocating a Metal buffer of {} bytes (type {})", size,
                   static_cast<int>(type));
        MemoryStats::Track((__bridge const void*)buffer, MemoryStats::Kind::Buffer);
        const bool with_bda = bool(info.usage & vk::BufferUsageFlagBits::eShaderDeviceAddress);
        if (with_bda || ResidentAll()) {
            // Device addresses read and write nothing without residency (D6).
            impl->dev.AddResident(buffer);
        }
        if (with_bda) {
            out.bda_addr = buffer.gpuAddress;
        }
        out.owner = this;
        out.native = (__bridge_retained void*)buffer;
        return {.mapped = static_cast<u8*>(buffer.contents), .coherent = true};
    }
}

BufferMemory Runtime::ImportBuffer(UniqueBuffer& out, u64 size, u64 chunk_phys) {
    const GuestMemory::Chunk* chunk = GuestMemory::Find(chunk_phys);
    ASSERT_MSG(chunk, "No guest memory chunk at {:#x}", chunk_phys);
    ASSERT_MSG(chunk->phys == chunk_phys && size <= chunk->size,
               "Guest memory chunk {:#x}+{:#x} does not cover {:#x}+{:#x}", chunk->phys,
               chunk->size, chunk_phys, size);
    // The chunk's buffer is the module's, resident for the life of the process; this object takes
    // a reference of its own (offset 0 is the chunk's start).
    id<MTLBuffer> buffer = (__bridge id<MTLBuffer>)chunk->buffer;
    out.owner = this;
    out.native = (__bridge_retained void*)buffer;
    out.bda_addr = chunk->gpu_address;
    {
        std::scoped_lock lock{impl->imported_mutex};
        impl->imported.insert(out.native);
    }
    return {.coherent = true};
}

void Runtime::DestroyBuffer(UniqueBuffer& buffer) {
    InvalidateExactExports(); // logical wrapper retirement precedes deferred native release
    void* native = std::exchange(buffer.native, nullptr);
    // Buffers with a device address (or all, ResidentAll) were made resident (CreateBuffer).
    const bool resident = (ResidentAll() || buffer.bda_addr != 0) && !MemoryStats::IsDiscarded(native);
    buffer.buffer = VK_NULL_HANDLE;
    buffer.allocation = VK_NULL_HANDLE;
    buffer.bda_addr = 0;
    if (!native) {
        return;
    }
    bool imported;
    {
        std::scoped_lock lock{impl->imported_mutex};
        imported = impl->imported.erase(native) != 0; // the pointer can come back for another buffer
    }
    // D6: nothing retains the buffer for the GPU. It goes after the command buffer that may use
    // it completed, together with its place in the residency set.
    if (!imported) MemoryStats::Retire(native);
    scheduler.DeferOperation([dev = &impl->dev, native, imported, resident] {
        id<MTLBuffer> buffer = (__bridge_transfer id<MTLBuffer>)native;
        if (resident && !imported) {
            dev->RemoveResident(buffer);
        }
    });
}

bool Runtime::DiscardIdleStaging(const Buffer& buffer) {
    // Only StagingPool calls this, after idle expiry AND a completed last-use tick.
    // Never apply to guest imports, textures or held readbacks; no GPU wait is introduced.
    static const bool enabled = [] { const char* e = std::getenv("BB_MTL_IDLE_STAGING_DISCARD"); return !(e && e[0] == '0'); }();
    if (!enabled) return false;
    { std::scoped_lock lock{impl->imported_mutex}; if (impl->imported.contains(buffer.buffer.native)) return false; }
    id<MTLBuffer> native = NativeBuffer(buffer);
    if (ResidentAll() || buffer.buffer.bda_addr) impl->dev.RemoveResident(native);
    [native setPurgeableState:MTLPurgeableStateEmpty];
    MemoryStats::Discarded(buffer.buffer.native, true);
    return true;
}

void Runtime::WakeStaging(const Buffer& buffer) {
    id<MTLBuffer> native = NativeBuffer(buffer);
    [native setPurgeableState:MTLPurgeableStateNonVolatile];
    if (ResidentAll() || buffer.buffer.bda_addr) impl->dev.AddResident(native);
    MemoryStats::Discarded(buffer.buffer.native, false);
}

void Runtime::FlushBuffer(const UniqueBuffer&, u64, u64) {
    // Shared storage is coherent (BufferMemory::coherent).
}

void Runtime::InvalidateBuffer(const UniqueBuffer&, u64, u64) {}

void Runtime::AssociateGuestBuffer(const UniqueBuffer& buffer, u64 address, u64 size) {
    MemoryStats::Associate(buffer.native, address, size);
}

void Runtime::AssociateGuestImage(const UniqueImage& image, u64 address, u64 size) {
    MemoryStats::Associate(image.native, address, size);
}

void Runtime::CreateImage(UniqueImage& out, const vk::ImageCreateInfo& info) {
    @autoreleasepool {
        vk::ImageCreateInfo ci = info;
        ci.format = SubstituteFormat(ci.format);
        const u32 pixel_format = VkToMtl::PixelFormat(ci.format);
        ASSERT_MSG(pixel_format != 0, "No Metal texture format for {}", vk::to_string(info.format));
        const u32 samples = SampleCountOf(ci.samples);
        const bool depth = VkToMtl::IsDepthPixelFormat(pixel_format) ||
                           VkToMtl::HasStencilPixelFormat(pixel_format);
        const bool block = vk::blockExtent(ci.format)[0] > 1;

        MTLTextureDescriptor* descriptor = [MTLTextureDescriptor new];
        descriptor.textureType = TextureTypeOf(ci);
        descriptor.pixelFormat = MTLPixelFormat(pixel_format);
        descriptor.width = ci.extent.width;
        descriptor.height = ci.imageType == vk::ImageType::e1D ? 1 : ci.extent.height;
        descriptor.depth = ci.imageType == vk::ImageType::e3D ? ci.extent.depth : 1;
        descriptor.mipmapLevelCount = samples > 1 ? 1 : std::max(ci.mipLevels, 1u);
        descriptor.arrayLength = ci.imageType == vk::ImageType::e3D ? 1 : std::max(ci.arrayLayers, 1u);
        descriptor.sampleCount = samples;
        descriptor.storageMode = MTLStorageModePrivate;
        MTLTextureUsage usage = MTLTextureUsageUnknown;
        if (ci.usage & vk::ImageUsageFlagBits::eSampled) {
            usage |= MTLTextureUsageShaderRead;
        }
        if ((ci.usage & vk::ImageUsageFlagBits::eStorage) && !depth && samples == 1) {
            usage |= MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
        }
        if (!block && (ci.usage & (vk::ImageUsageFlagBits::eColorAttachment |
                                   vk::ImageUsageFlagBits::eDepthStencilAttachment))) {
            usage |= MTLTextureUsageRenderTarget;
        }
        if (ci.flags & vk::ImageCreateFlagBits::eMutableFormat) {
            usage |= MTLTextureUsagePixelFormatView;
        }
        descriptor.usage = usage;
        id<MTLTexture> texture = [impl->dev.device newTextureWithDescriptor:descriptor];
        creation_stats.Note(true, texture ? texture.allocatedSize : 0);
        ASSERT_MSG(texture, "Failed allocating a Metal texture {}x{}x{} {} samples {}",
                   ci.extent.width, ci.extent.height, ci.extent.depth, vk::to_string(ci.format),
                   samples);

        if (ResidentAll()) {
            impl->dev.AddResident(texture);
        }
        out.owner = this;
        out.image_ci = ci;
        MemoryStats::Track((__bridge const void*)texture,
            bool(ci.usage & (vk::ImageUsageFlagBits::eColorAttachment |
                             vk::ImageUsageFlagBits::eDepthStencilAttachment))
                ? MemoryStats::Kind::Target : MemoryStats::Kind::Texture);
        out.size_bytes = texture.allocatedSize;
        out.native = (__bridge_retained void*)texture;
        // The handle the caches pass on in vk::ImageViewCreateInfo::image (CreateImageView).
        out.image = vk::Image{reinterpret_cast<VkImage>(out.native)};
        {
            std::scoped_lock lock{impl->formats_mutex};
            impl->image_formats[out.native] = ci.format;
        }
        if (ImageUsageLog::Enabled()) {
            ImageUsageLog::Created(out.native, vk::to_string(ci.format), block);
        }
    }
}

void Runtime::DestroyImage(UniqueImage& image) {
    InvalidateExactExports();
    void* native = std::exchange(image.native, nullptr);
    image.image = VK_NULL_HANDLE;
    if (!native) {
        return;
    }
    {
        std::scoped_lock lock{impl->formats_mutex};
        impl->image_formats.erase(native);
    }
    if (ImageUsageLog::Enabled()) {
        ImageUsageLog::Destroyed(native);
    }
    MemoryStats::Retire(native);
    scheduler.DeferOperation([dev = &impl->dev, native] {
        id<MTLTexture> texture = (__bridge_transfer id<MTLTexture>)native;
        if (ResidentAll()) {
            dev->RemoveResident(texture);
        }
    });
}

vk::SampleCountFlags Runtime::ImageSampleCounts(const vk::ImageCreateInfo& info) const {
    vk::SampleCountFlags counts = vk::SampleCountFlagBits::e1;
    const u32 pixel_format = VkToMtl::PixelFormat(SubstituteFormat(info.format));
    // Compressed formats and 1D/3D images have no multisampled variant.
    if (info.imageType != vk::ImageType::e2D || vk::blockExtent(info.format)[0] > 1 ||
        pixel_format == 0) {
        return counts;
    }
    id<MTLDevice> mtl = impl->dev.device;
    constexpr std::array<std::pair<u32, vk::SampleCountFlagBits>, 4> candidates{{
        {2, vk::SampleCountFlagBits::e2},
        {4, vk::SampleCountFlagBits::e4},
        {8, vk::SampleCountFlagBits::e8},
        {16, vk::SampleCountFlagBits::e16},
    }};
    for (const auto& [count, bit] : candidates) {
        if ([mtl supportsTextureSampleCount:count]) {
            counts |= bit;
        }
    }
    return counts;
}

void Runtime::CreateImageView(ImageView& out, const vk::ImageViewCreateInfo& info) {
    @autoreleasepool {
        // The image handle is the retained MTLTexture of UniqueImage (CreateImage).
        void* base_native = reinterpret_cast<void*>(static_cast<VkImage>(info.image));
        ASSERT_MSG(base_native, "Image view of a null image");
        id<MTLTexture> base = (__bridge id<MTLTexture>)base_native;
        const auto& range = info.subresourceRange;

        // The view's pixel format: the base's for depth, X32_Stencil8 for the stencil of a
        // combined format, else the view format when the texture can be viewed that way.
        MTLPixelFormat pixel_format = base.pixelFormat;
        const vk::Format format = SubstituteFormat(info.format);
        const u32 view_pf = VkToMtl::PixelFormat(format);
        const bool base_depth = VkToMtl::IsDepthPixelFormat(u32(base.pixelFormat)) ||
                                VkToMtl::HasStencilPixelFormat(u32(base.pixelFormat));
        if (base_depth) {
            if (range.aspectMask == vk::ImageAspectFlagBits::eStencil &&
                VkToMtl::IsDepthPixelFormat(u32(base.pixelFormat))) {
                pixel_format = MTLPixelFormatX32_Stencil8;
            }
        } else if (view_pf != 0 && view_pf != u32(base.pixelFormat)) {
            // The Vulkan format the texture was made in decides what views are legal.
            std::optional<vk::Format> base_format;
            {
                std::scoped_lock lock{impl->formats_mutex};
                if (const auto it = impl->image_formats.find(base_native);
                    it != impl->image_formats.end()) {
                    base_format = it->second;
                }
            }
            if (base_format && ViewCompatible(*base_format, format)) {
                pixel_format = MTLPixelFormat(view_pf);
                // P10.1 safety net: the usage is promoted before (RequiredViewUsage); without it
                // the driver makes the view anyway and it can read 0xFFFFFFFF. Never silent.
                if (!(base.usage & MTLTextureUsagePixelFormatView) &&
                    !VkToMtl::IsSrgbTwinPixelFormat(u32(base.pixelFormat), view_pf) &&
                    impl->FirstTime("view usage " + vk::to_string(*base_format) +
                                    vk::to_string(format))) {
                    LOG_ERROR(Render, "Metal: a view of format {} of a texture of format {} "
                                      "without PixelFormatView (texture \"{}\", usage {}); the "
                                      "view may read garbage",
                              vk::to_string(format), vk::to_string(*base_format),
                              base.label ? [base.label UTF8String] : "?",
                              static_cast<unsigned long long>(base.usage));
                }
            } else if (impl->FirstTime("view format " + vk::to_string(format))) {
                LOG_ERROR(Render, "Metal: a view of format {} of a texture of format {} is not "
                                  "possible; using the texture's format",
                          vk::to_string(format),
                          base_format ? vk::to_string(*base_format) : std::string("?"));
            }
        }

        const bool ms = base.sampleCount > 1;
        MTLTextureType type;
        switch (info.viewType) {
        case vk::ImageViewType::e1D:
            type = MTLTextureType1D;
            break;
        case vk::ImageViewType::e1DArray:
            type = MTLTextureType1DArray;
            break;
        case vk::ImageViewType::e2D:
            type = ms ? MTLTextureType2DMultisample : MTLTextureType2D;
            break;
        case vk::ImageViewType::e2DArray:
            type = ms ? MTLTextureType2DMultisampleArray : MTLTextureType2DArray;
            break;
        case vk::ImageViewType::eCube:
            type = MTLTextureTypeCube;
            break;
        case vk::ImageViewType::eCubeArray:
            type = MTLTextureTypeCubeArray;
            break;
        case vk::ImageViewType::e3D:
        default:
            type = MTLTextureType3D;
            break;
        }
        // Metal cannot view a 3D texture as 2D (the debug layer aborts): the view stays 3D.
        if (base.textureType == MTLTextureType3D && type != MTLTextureType3D) {
            if (impl->FirstTime("view 3d")) {
                LOG_ERROR(Render, "Metal: 2D view of a 3D image is not supported; using the 3D "
                                  "texture");
            }
            type = MTLTextureType3D;
        }

        const NSUInteger levels_left = base.mipmapLevelCount - std::min<NSUInteger>(
                                                                    range.baseMipLevel,
                                                                    base.mipmapLevelCount - 1);
        const NSUInteger level_count =
            std::min<NSUInteger>(range.levelCount == VK_REMAINING_MIP_LEVELS ? levels_left
                                                                              : range.levelCount,
                                 levels_left);
        const NSUInteger layers = base.textureType == MTLTextureType3D ? 1 : base.arrayLength;
        const NSUInteger layers_left =
            layers - std::min<NSUInteger>(range.baseArrayLayer, layers - 1);
        NSUInteger layer_count = std::min<NSUInteger>(
            range.layerCount == VK_REMAINING_ARRAY_LAYERS ? layers_left : range.layerCount,
            layers_left);
        NSUInteger base_layer = base.textureType == MTLTextureType3D ? 0 : range.baseArrayLayer;
        if (type == MTLTextureTypeCube && layer_count < 6) {
            type = MTLTextureType2DArray; // incomplete cube
        }

        const VkToMtl::TextureSwizzle swizzle = VkToMtl::ComposeSwizzle(info.components, format);
        const bool identity = swizzle == VkToMtl::IdentitySwizzle;
        id<MTLTexture> texture = base;
        const bool whole = pixel_format == base.pixelFormat && type == base.textureType &&
                           range.baseMipLevel == 0 && level_count == base.mipmapLevelCount &&
                           base_layer == 0 && layer_count == layers && identity;
        if (!whole) {
            texture = [base newTextureViewWithPixelFormat:pixel_format
                                              textureType:type
                                                   levels:NSMakeRange(range.baseMipLevel,
                                                                      level_count)
                                                   slices:NSMakeRange(base_layer, layer_count)
                                                  swizzle:MTLTextureSwizzleChannelsMake(
                                                              MTLTextureSwizzle(swizzle.r),
                                                              MTLTextureSwizzle(swizzle.g),
                                                              MTLTextureSwizzle(swizzle.b),
                                                              MTLTextureSwizzle(swizzle.a))];
            ASSERT_MSG(texture, "Failed to create a Metal texture view (format {}, type {})",
                       static_cast<u32>(pixel_format), static_cast<u32>(type));
        }
        if (ImageUsageLog::Enabled()) {
            ImageUsageLog::ViewMade(base_native, (__bridge const void*)texture);
        }
        out.owner = this;
        out.native = (__bridge_retained void*)texture;
    }
}

void Runtime::DestroyImageView(ImageView& view) {
    void* native = std::exchange(view.native, nullptr);
    view.image_view.reset();
    if (!native) {
        return;
    }
    scheduler.DeferOperation([native] { (void)(__bridge_transfer id<MTLTexture>)native; });
}

void Runtime::CreateSampler(Sampler& out, const vk::SamplerCreateInfo& info) {
    @autoreleasepool {
        MTLSamplerDescriptor* descriptor = [MTLSamplerDescriptor new];
        descriptor.minFilter = MTLSamplerMinMagFilter(VkToMtl::SamplerFilter(info.minFilter));
        descriptor.magFilter = MTLSamplerMinMagFilter(VkToMtl::SamplerFilter(info.magFilter));
        descriptor.mipFilter = MTLSamplerMipFilter(VkToMtl::MipFilter(info.mipmapMode));
        descriptor.sAddressMode = MTLSamplerAddressMode(VkToMtl::AddressMode(info.addressModeU));
        descriptor.tAddressMode = MTLSamplerAddressMode(VkToMtl::AddressMode(info.addressModeV));
        descriptor.rAddressMode = MTLSamplerAddressMode(VkToMtl::AddressMode(info.addressModeW));
        const VkToMtl::BorderInfo border = VkToMtl::BorderColor(info.borderColor);
        if (!border.supported && impl->FirstTime("custom border")) {
            LOG_WARNING(Render, "Metal: custom sampler border colors are not supported");
        }
        descriptor.borderColor = MTLSamplerBorderColor(border.color);
        descriptor.maxAnisotropy =
            info.anisotropyEnable ? std::clamp<NSUInteger>(NSUInteger(info.maxAnisotropy), 1, 16)
                                  : 1;
        if (info.compareEnable) {
            descriptor.compareFunction =
                MTLCompareFunction(VkToMtl::SamplerCompare(info.compareOp));
        }
        descriptor.lodMinClamp = info.minLod;
        descriptor.lodMaxClamp = std::min(info.maxLod, 1000.0f);
        if (@available(macOS 26.0, *)) {
            descriptor.lodBias = info.mipLodBias;
        }
        descriptor.normalizedCoordinates = YES;
        descriptor.supportArgumentBuffers = YES;
        id<MTLSamplerState> sampler = [impl->dev.device newSamplerStateWithDescriptor:descriptor];
        ASSERT_MSG(sampler, "Failed to create a Metal sampler");
        out.owner = this;
        out.native = (__bridge_retained void*)sampler;
    }
}

void Runtime::DestroySampler(Sampler& sampler) {
    void* native = std::exchange(sampler.native, nullptr);
    sampler.handle.reset();
    if (!native) {
        return;
    }
    scheduler.DeferOperation([native] { (void)(__bridge_transfer id<MTLSamplerState>)native; });
}

void Runtime::SetBufferName(const UniqueBuffer& buffer, std::string_view name) {
    if (!buffer.native) {
        return;
    }
    {
        // The guest chunk buffers keep the module's label.
        std::scoped_lock lock{impl->imported_mutex};
        if (impl->imported.contains(buffer.native)) {
            return;
        }
    }
    id<MTLBuffer> native = (__bridge id<MTLBuffer>)buffer.native;
    native.label = [[NSString alloc] initWithBytes:name.data()
                                            length:name.size()
                                          encoding:NSUTF8StringEncoding];
}

void Runtime::SetImageName(const UniqueImage& image, std::string_view name) {
    if (!image.native) {
        return;
    }
    if (ImageUsageLog::Enabled()) {
        ImageUsageLog::Named(image.native, name);
    }
    if (name.starts_with("Scene proxy "))
        MemoryStats::Reclassify(image.native, MemoryStats::Kind::SceneProxy);
    id<MTLTexture> native = (__bridge id<MTLTexture>)image.native;
    native.label = [[NSString alloc] initWithBytes:name.data()
                                            length:name.size()
                                          encoding:NSUTF8StringEncoding];
}

void Runtime::SetImageViewName(const ImageView& view, std::string_view name) {
    if (!view.native) {
        return;
    }
    id<MTLTexture> native = (__bridge id<MTLTexture>)view.native;
    // A view that is the image's own texture keeps the image's name.
    if (native.label == nil) {
        native.label = [[NSString alloc] initWithBytes:name.data()
                                                length:name.size()
                                              encoding:NSUTF8StringEncoding];
    }
}

// ---- Buffer transfers ---------------------------------------------------------------------------

void Runtime::CopyBuffer(const Buffer* src, const Buffer* dst,
                         std::span<const vk::BufferCopy> copies) {
    if (copies.empty()) {
        return;
    }
    // bbport: many regions (HLE copy shaders) are tracked as one bounding range per buffer:
    // conservative for barriers, and fewer lookups.
    const bool bounded = copies.size() > 4;
    u64 src_min = ~0ULL, src_max = 0, dst_min = ~0ULL, dst_max = 0;
    if (bounded) {
        for (const auto& copy : copies) {
            src_min = std::min<u64>(src_min, copy.srcOffset);
            src_max = std::max<u64>(src_max, copy.srcOffset + copy.size);
            dst_min = std::min<u64>(dst_min, copy.dstOffset);
            dst_max = std::max<u64>(dst_max, copy.dstOffset + copy.size);
        }
    }

    bool needs_flush{};
    if (bounded) {
        needs_flush = IsBufferAccessed(src, src_min, src_max - src_min) ||
                      IsBufferAccessed(dst, dst_min, dst_max - dst_min, true);
    } else {
        for (const auto& copy : copies) {
            needs_flush |= IsBufferAccessed(src, copy.srcOffset, copy.size);
            needs_flush |= IsBufferAccessed(dst, copy.dstOffset, copy.size, true);
        }
    }
    if (needs_flush) {
        FlushBarriers();
    }

    scheduler.BlitEncoder(__func__);
    id<MTLBlitCommandEncoder> blit = impl->sched.blit;
    id<MTLBuffer> src_buffer = NativeBuffer(*src);
    id<MTLBuffer> dst_buffer = NativeBuffer(*dst);
    // Recording uses FrameCapture's existing byte/event limits. No payload readback.
    if (FrameCapture::NativeRecording()) {
        const auto queue = reinterpret_cast<uintptr_t>(&scheduler);
        FrameCapture::NativeLabel((std::string{"Buffer copy source: "} +
            (src_buffer.label.UTF8String ?: "unlabelled")).c_str(), queue);
        FrameCapture::NativeLabel((std::string{"Buffer copy destination: "} +
            (dst_buffer.label.UTF8String ?: "unlabelled")).c_str(), queue);
        for (const auto& copy : copies)
            FrameCapture::Native(std::format("{{\"op\":\"buffer_copy\",\"queue\":{},\"src\":{},\"dst\":{},\"src_offset\":{},\"dst_offset\":{},\"bytes\":{},\"src_length\":{},\"dst_length\":{},\"flush\":{}}}",
                queue, (uintptr_t)(__bridge void*)src_buffer,
                (uintptr_t)(__bridge void*)dst_buffer, copy.srcOffset, copy.dstOffset,
                copy.size, src_buffer.length, dst_buffer.length, needs_flush));
    }
    for (const auto& copy : copies) {
        [blit copyFromBuffer:src_buffer
                sourceOffset:copy.srcOffset
                    toBuffer:dst_buffer
           destinationOffset:copy.dstOffset
                        size:copy.size];
    }

    if (bounded) {
        AccessBuffer(src, src_min, src_max - src_min, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferRead);
        AccessBuffer(dst, dst_min, dst_max - dst_min, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferWrite);
        return;
    }
    for (const auto& copy : copies) {
        AccessBuffer(src, copy.srcOffset, copy.size, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferRead);
        AccessBuffer(dst, copy.dstOffset, copy.size, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferWrite);
    }
}

void Runtime::FillBuffer(const Buffer* dst, u64 offset, u64 size, u32 value) {
    if (size == 0) {
        return;
    }
    if (IsBufferAccessed(dst, offset, size, true)) {
        FlushBarriers();
    }
    const u8 byte = static_cast<u8>(value);
    const bool one_byte = value == (u32{byte} * 0x01010101u);
    static const bool chain_queries = [] {
        const char* flag = std::getenv("BB_MTL_QUERY_COMPUTE_CHAIN");
        return flag && flag[0] == '1';
    }();
    const bool small_compute_fill = chain_queries && one_byte && size <= 256 &&
        !(offset & 3) && !(size & 3) && !scheduler.HoldingPass() &&
        scheduler.OpenEncoder() == Scheduler::EncoderKind::Compute;
    if (small_compute_fill) scheduler.MemoryBarrier();
    if (one_byte && !small_compute_fill) {
        scheduler.BlitEncoder(__func__);
        [impl->sched.blit fillBuffer:NativeBuffer(*dst) range:NSMakeRange(offset, size) value:byte];
    } else {
        // A pattern of four different bytes: a small kernel, one thread per word.
        const u32 words = static_cast<u32>(size / 4);
        id<MTLComputePipelineState> pipeline = impl->kernels.FillU32();
        scheduler.ComputeEncoder(__func__, true);
        id<MTLComputeCommandEncoder> compute = impl->sched.compute;
        [compute setComputePipelineState:pipeline];
        [compute setBuffer:NativeBuffer(*dst) offset:offset atIndex:0];
        [compute setBytes:&value length:sizeof(value) atIndex:1];
        [compute setBytes:&words length:sizeof(words) atIndex:2];
        [compute dispatchThreads:MTLSizeMake(words, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(std::min<NSUInteger>(
                                                  256, pipeline.maxTotalThreadsPerThreadgroup),
                                              1, 1)];
    }
    AccessBuffer(dst, offset, size, vk::PipelineStageFlagBits2::eClear,
                 vk::AccessFlagBits2::eTransferWrite);
}

void Runtime::CopyFromGuestChunk(u64 chunk_phys, const Buffer* dst,
                                 std::span<const vk::BufferCopy> copies) {
    if (copies.empty()) {
        return;
    }
    const GuestMemory::Chunk* chunk = GuestMemory::Find(chunk_phys);
    ASSERT_MSG(chunk, "No guest memory chunk at {:#x}", chunk_phys);
    bool needs_flush = false;
    for (const auto& copy : copies) {
        needs_flush |= IsBufferAccessed(dst, copy.dstOffset, copy.size, true);
    }
    if (needs_flush) {
        FlushBarriers();
    }
    scheduler.BlitEncoder(__func__);
    id<MTLBlitCommandEncoder> blit = impl->sched.blit;
    id<MTLBuffer> src_buffer = (__bridge id<MTLBuffer>)chunk->buffer;
    id<MTLBuffer> dst_buffer = NativeBuffer(*dst);
    for (const auto& copy : copies) {
        [blit copyFromBuffer:src_buffer
                sourceOffset:copy.srcOffset
                    toBuffer:dst_buffer
           destinationOffset:copy.dstOffset
                        size:copy.size];
    }
    for (const auto& copy : copies) {
        AccessBuffer(dst, copy.dstOffset, copy.size, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferWrite);
    }
}

void Runtime::UpdateBuffer(const Buffer* dst, u64 offset, std::span<const u8> data) {
    if (data.empty()) {
        return;
    }
    std::optional<FragmentAccessGuard::Snapshot> observed_fragments;
    const auto fragment_snapshot=[&] {
        if(observed_fragments)return *observed_fragments;
        auto& list=scheduler.Commands();const auto* pass=scheduler.OpenRenderState();
        if(pass) {
            const auto attachment=[&](u64 handle) {
                id<MTLTexture> t=(__bridge id<MTLTexture>)reinterpret_cast<void*>(handle);
                unsigned depth=0;
                for(;t;t=t.parentTexture) {
                    if(++depth>8 || t.heap){list.FragmentUnknown(FragmentAccessGuard::UnknownTexture);break;}
                    if(t.buffer){list.FragmentBuffer((__bridge void*)t.buffer,0,t.buffer.length);break;}
                }
            };
            for(const auto& color:pass->color_attachments)attachment(color.image_view);
            attachment(pass->depth_stencil_attachment.image_view);
        }
        const auto native=NativeBuffer(*dst);
        if(native.heap)list.FragmentUnknown(FragmentAccessGuard::InvalidRange);
        const auto guard=list.FragmentSnapshot((__bridge void*)native,offset,data.size());
        if(FrameCapture::NativeRecording() && data.size()==192)FrameCapture::Native(std::format("{{\"op\":\"inline_update_fragment_guard\",\"native\":{},\"offset\":{},\"bytes\":192,\"render_pass\":{},\"draws\":{},\"known_draws\":{},\"resources\":{},\"reasons\":{},\"overlap\":{}}}",
            uintptr_t((__bridge void*)native),offset,bool(pass),guard.draws,guard.known_draws,guard.resources,guard.reasons,guard.overlap));
        observed_fragments=guard;
        return guard;
    };
    if(FrameCapture::NativeRecording() && data.size()==192 && scheduler.Commands().FragmentGuardEnabled()) {
        fragment_snapshot();
    }
    static const bool fill_updates=[] {const char* v=std::getenv("BB_MTL_FILL_UPDATES");return v && v[0]=='1';}();
    // A repeated byte is an exact native fill, not a redundant-write assumption. Keep
    // every command in stream order; no GPU/guest-memory equality check or snapshot cache.
    if(fill_updates && data.size()<=256 && !(offset&3) && !(data.size()&3) &&
       offset<=dst->SizeBytes() && data.size()<=dst->SizeBytes()-offset &&
       std::ranges::all_of(data,[byte=data.front()](u8 v){return v==byte;})) {
        if(FrameCapture::NativeRecording())FrameCapture::Native(std::format(
            "{{\"op\":\"pattern_update\",\"bytes\":{},\"value\":{}}}",data.size(),data.front()));
        FillBuffer(dst,offset,data.size(),u32(data.front())*0x01010101u);
        return;
    }
    static const bool inline_updates = [] {
        const char* value = std::getenv("BB_MTL_INLINE_UPDATES");
        return value && value[0] == '1';
    }();
    static const bool render_updates=[] {
        const char* value=std::getenv("BB_MTL_RENDER_UPDATES");return value && value[0]=='1';
    }();
    // Keep every write in GPU stream order. setBytes snapshots the call's CPU payload;
    // neither the guest backing nor any existing copy/cache lifetime is bypassed.
    if (inline_updates && data.size() <= 256 && !(offset % 16) && !(data.size() % 4) &&
        offset <= dst->SizeBytes() && data.size() <= dst->SizeBytes() - offset) {
        // Apple tile render passes cannot wait for their prior fragment work here.
        // Unknown/raw fragment accesses, attachment aliases and scheduler tails fall back.
        // Owned buffers are distinct allocations or a canonical imported guest chunk.
        if(render_updates && scheduler.CanInlineBufferUpdate() && dst->buffer.owner==this &&
           scheduler.Commands().FragmentGuardEnabled()) {
            const auto native=NativeBuffer(*dst);
            if(native && !native.heap && offset<=native.length && data.size()<=native.length-offset) {
                const auto guard=fragment_snapshot();
                if(!guard.reasons && !guard.overlap && guard.draws==guard.known_draws) {
                    if(auto pipeline=impl->kernels.RenderInlineUpdate();pipeline &&
                       scheduler.InlineBufferUpdate((__bridge void*)pipeline,(__bridge void*)native,
                                                    offset,data.data(),u32(data.size()))) {
                        AccessBuffer(dst,offset,data.size(),vk::PipelineStageFlagBits2::eVertexShader,
                                     vk::AccessFlagBits2::eShaderWrite);
                        if(++impl->render_updates==1)LOG_INFO(Render,"Render inline buffer updates active");
                        if(FrameCapture::NativeRecording())FrameCapture::Native(std::format(
                            "{{\"op\":\"inline_render_update\",\"native\":{},\"offset\":{},\"bytes\":{}}}",
                            uintptr_t((__bridge void*)native),offset,data.size()));
                        return;
                    }
                }
            }
        }
        if (auto pipeline = impl->kernels.InlineUpdate()) {
            if (IsBufferAccessed(dst, offset, data.size(), true)) FlushBarriers();
            scheduler.ComputeEncoder("Inline buffer update", true);
            auto compute = impl->sched.compute;
            [compute setComputePipelineState:pipeline];
            [compute setBuffer:NativeBuffer(*dst) offset:offset atIndex:0];
            [compute setBytes:data.data() length:data.size() atIndex:1];
            const auto words = data.size() / 4;
            [compute dispatchThreads:MTLSizeMake(words, 1, 1)
                threadsPerThreadgroup:MTLSizeMake(std::min<NSUInteger>(words, pipeline.maxTotalThreadsPerThreadgroup), 1, 1)];
            AccessBuffer(dst, offset, data.size(), vk::PipelineStageFlagBits2::eComputeShader,
                         vk::AccessFlagBits2::eShaderWrite);
            return;
        }
    }
    // Stream order matters (earlier commands may still read the range): the data goes through
    // staging memory and a blit, not a write into the buffer's own memory.
    const StagingSpan staging = StagingRequest(data.size(), MemoryType::HostUncached, 4);
    std::memcpy(staging.mapped, data.data(), data.size());
    const vk::BufferCopy copy{.srcOffset = staging.offset, .dstOffset = offset, .size = data.size()};
    CopyBuffer(staging.buffer, dst, std::span{&copy, 1});
}

u64 Runtime::RenderUpdateCount() const { return impl->render_updates; }

void Runtime::InlineData(Buffer* dst, u64 offset, u32 value) {
    UpdateBuffer(dst, offset, std::span{reinterpret_cast<const u8*>(&value), sizeof(value)});
}

// ---- Image transfers ----------------------------------------------------------------------------

void Runtime::UploadImage(Image* dst, const Buffer* src,
                          std::span<const vk::BufferImageCopy> upload_copies) {
    if (!dst->backing) {
        return;
    }
    SetBackingSamples(dst, dst->info.num_samples, false);

    bool needs_flush = Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferWrite);
    for (const auto& copy : upload_copies) {
        const auto copy_size = BufferImageCopySize(copy, dst->info.pixel_format);
        needs_flush |= IsBufferAccessed(src, copy.bufferOffset, copy_size);
    }
    if (needs_flush) {
        FlushBarriers();
    }

    id<MTLTexture> texture = NativeTexture(*dst);
    if (CopyCompatible(*dst, "image upload") && texture.sampleCount == 1) {
        UploadCopies({*this, scheduler, *impl}, texture,
                     dst->info.pixel_format, dst->backing->image.image_ci.format,
                     NativeBuffer(*src), upload_copies, __func__);
        if(!upload_copies.empty())MemoryStats::NoteContent((__bridge const void*)texture,MemoryStats::Content::Upload);
        if(!upload_copies.empty()&&TextureLineage::Get().Armed())
            TextureLineage::Get().Texture("upload-result",scheduler,device.GetImpl().device,texture,nil,TextureLineage::Info(*dst));
    }

    for (const auto& copy : upload_copies) {
        const auto copy_size = BufferImageCopySize(copy, dst->info.pixel_format);
        AccessBuffer(src, copy.bufferOffset, copy_size, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferRead);
    }

    dst->flags &= ~ImageFlagBits::Dirty;
}

void TraceTextureUploadBuffer(const Image& image,const Buffer* buffer,u64 offset,const char* phase) {
    if(!buffer||!image.backing||!image.backing->image.native||!buffer->buffer.native||
       !image.backing->image.owner||image.backing->image.owner!=buffer->buffer.owner)return;
    auto& trace=TextureLineage::Get();if(!trace.Armed())return;
    // Only Metal populates these native fields; Vulkan keeps them null.
    auto& runtime=*static_cast<Runtime*>(image.backing->image.owner);
    trace.Buffer(phase,runtime.GetScheduler(),runtime.GetDevice().GetImpl().device,
        NativeBuffer(*buffer),offset,NativeTexture(image),TextureLineage::Info(image));
}
void TraceTextureRefreshDecision(const Image& image) {
    if(!image.backing||!image.backing->image.native)return;
    auto& trace=TextureLineage::Get();if(trace.Armed())trace.Decision(
        "refresh-no-upload",NativeTexture(image),TextureLineage::Info(image));
}

void Runtime::DownloadImage(Image* src, const Buffer* dst,
                            std::span<const vk::BufferImageCopy> download_copies) {
    if (!src->backing) {
        return;
    }
    SetBackingSamples(src, src->info.num_samples);

    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    for (const auto& copy : download_copies) {
        const auto copy_size = BufferImageCopySize(copy, src->info.pixel_format);
        needs_flush |= IsBufferAccessed(dst, copy.bufferOffset, copy_size, true);
    }
    if (needs_flush) {
        FlushBarriers();
    }

    id<MTLTexture> texture = NativeTexture(*src);
    if (CopyCompatible(*src, "image download") && texture.sampleCount == 1) {
        DownloadCopies({*this, scheduler, *impl}, texture,
                       src->info.pixel_format, src->backing->image.image_ci.format,
                       NativeBuffer(*dst), download_copies, __func__);
    }

    for (const auto& copy : download_copies) {
        const auto copy_size = BufferImageCopySize(copy, src->info.pixel_format);
        AccessBuffer(dst, copy.bufferOffset, copy_size, vk::PipelineStageFlagBits2::eCopy,
                     vk::AccessFlagBits2::eTransferWrite);
    }
}

namespace {
/// The texture to copy `src` out of so that its pixel format equals the destination's, nil when
/// no such view exists (Metal blits between equal pixel formats only). A view made here is
/// recorded unretained: `keep_alive` is set so the caller holds it until the tick completes.
id<MTLTexture> CopySource(Runtime::Impl& impl, id<MTLTexture> src, vk::Format src_format,
                          id<MTLTexture> dst, vk::Format dst_format,
                          __strong id<MTLTexture>& keep_alive) {
    if (src.pixelFormat == dst.pixelFormat) {
        return src;
    }
    if (!ViewCompatible(src_format, dst_format)) {
        return nil;
    }
    // P10.1 safety net: the callers promote the source first (PromoteCopySource); without the
    // usage the driver makes the view anyway and it can read garbage. Never silent.
    if (!(src.usage & MTLTextureUsagePixelFormatView) &&
        !VkToMtl::IsSrgbTwinPixelFormat(u32(src.pixelFormat), u32(dst.pixelFormat)) &&
        impl.FirstTime("copy view usage " + vk::to_string(src_format) + vk::to_string(dst_format))) {
        LOG_ERROR(Render, "Metal: a copy reads a texture of format {} (usage {}) as {} without "
                          "PixelFormatView; the copy may read garbage",
                  vk::to_string(src_format), static_cast<unsigned long long>(src.usage),
                  vk::to_string(dst_format));
    }
    keep_alive = [src newTextureViewWithPixelFormat:dst.pixelFormat];
    if (ImageUsageLog::Enabled()) {
        ImageUsageLog::ViewMade((__bridge const void*)src, (__bridge const void*)keep_alive);
    }
    return keep_alive;
}
} // namespace

void Runtime::CopyImage(Image* src, Image* dst) {
    if (!src->backing || !dst->backing) {
        return;
    }
    MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    const bool lineage=TextureLineage::Get().Armed()&&
        (TextureLineage::Candidate(NativeTexture(*src))||TextureLineage::Candidate(NativeTexture(*dst)));
    if(lineage)TextureLineage::Get().Texture("copy-image-entry",scheduler,device.GetImpl().device,
        NativeTexture(*src),NativeTexture(*dst),TextureLineage::Info(*src),true);
    SCOPE_EXIT {if(lineage)TextureLineage::Get().Texture("copy-image-return",scheduler,device.GetImpl().device,
        NativeTexture(*dst),NativeTexture(*src),TextureLineage::Info(*dst),true);};
    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);
    // Opt-in diagnostic: identify real proxy-to-native copy round trips before optimizing.
    static const bool copy_audit=[] {const char* v=std::getenv("BB_MTL_COPY_AUDIT");return v&&v[0]=='1';}();
    if(copy_audit && scene_targets) {
        static u64 calls=0,source_current=0,source_dirty=0,both_existing=0,equal_extent=0;
        static auto reported=std::chrono::steady_clock::now();
        const bool current=scene_targets->ProxyCurrent(*src);
        ++calls;source_current+=current;source_dirty+=scene_targets->ProxyDirty(*src,0);
        both_existing+=current&&scene_targets->Tracks(dst->image_uid);
        equal_extent+=current&&scene_targets->Tracks(dst->image_uid)&&src->info.size.width==dst->info.size.width&&src->info.size.height==dst->info.size.height;
        if(calls<=20 || (current && source_current<=16))std::printf("Metal image copy audit pair: src=%llu dst=%llu, size=%ux%u/%ux%u format=%u/%u mips=%u/%u layers=%u/%u, proxy=%d/%d dirty=%d\n",(unsigned long long)src->image_uid,(unsigned long long)dst->image_uid,src->info.size.width,src->info.size.height,dst->info.size.width,dst->info.size.height,unsigned(src->info.pixel_format),unsigned(dst->info.pixel_format),src->info.resources.levels,dst->info.resources.levels,src->info.resources.layers,dst->info.resources.layers,int(current),int(scene_targets->Tracks(dst->image_uid)),int(scene_targets->ProxyDirty(*src,0)));
        const auto now=std::chrono::steady_clock::now();
        if(now-reported>=std::chrono::seconds(5)) {std::printf("Metal image copy audit: calls=%llu current-source=%llu dirty-source=%llu both-existing=%llu equal-extent=%llu\n",(unsigned long long)calls,(unsigned long long)source_current,(unsigned long long)source_dirty,(unsigned long long)both_existing,(unsigned long long)equal_extent);reported=now;}
    }

    if (src->info.pixel_format != dst->info.pixel_format) {
        LOG_DEBUG(Render,
                  "Copy between different formats: src={}, dst={}. Result may be undefined.",
                  vk::to_string(src->info.pixel_format), vk::to_string(dst->info.pixel_format));
    }

    const u32 base_width = src->info.size.width;
    const u32 base_height = src->info.size.height;
    const u32 base_depth = dst->backing->image.image_ci.imageType == vk::ImageType::e3D
                               ? dst->info.size.depth
                               : src->info.size.depth;

    // Match sample count before copying
    SetBackingSamples(dst, dst->info.num_samples, false);
    SetBackingSamples(src, src->info.num_samples);
    if(scene_targets && scene_targets->CopyDirectToNative(*src,*dst)) {
        dst->flags |= (src->flags & ImageFlagBits::GpuModified);
        dst->flags &= ~ImageFlagBits::Dirty;
        return;
    }
    PromoteCopySource(src, *dst);

    const vk::ImageType src_type = src->backing->image.image_ci.imageType;
    const vk::ImageType dst_type = dst->backing->image.image_ci.imageType;
    const bool src_is_3d = src_type == vk::ImageType::e3D;
    const bool dst_is_3d = dst_type == vk::ImageType::e3D;
    const bool is_2d_to_3d = !src_is_3d && dst_is_3d;
    const bool is_3d_to_2d = src_is_3d && !dst_is_3d;

    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    @autoreleasepool {
        const vk::Format src_format = src->backing->image.image_ci.format;
        const vk::Format dst_format = dst->backing->image.image_ci.format;
        id<MTLTexture> dst_texture = NativeTexture(*dst);
        id<MTLTexture> view = nil;
        id<MTLTexture> src_texture =
            CopySource(*impl, NativeTexture(*src), src_format, dst_texture, dst_format, view);
        if (view) {
            scheduler.DeferOperation([view] { (void)view; });
        }
        if (!src_texture) {
            if (impl->FirstTime("copy " + vk::to_string(src_format) + vk::to_string(dst_format))) {
                LOG_ERROR(Render, "Metal CopyImage between {} and {} is not supported",
                          vk::to_string(src_format), vk::to_string(dst_format));
            }
            return;
        }
        // Only after the source view is valid: a complete, disjoint destination overwrite
        // makes its previous proxy pixels irrelevant. Partial/alias/failed copies retain them.
        if(scene_targets && src!=dst && src->backing!=dst->backing)
            scene_targets->FullCopyOverwrite(*dst,(__bridge const void*)src_texture,(__bridge const void*)dst_texture);
        needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                               vk::PipelineStageFlagBits2::eCopy, vk::AccessFlagBits2::eTransferWrite);
        if(needs_flush) FlushBarriers();
        scheduler.BlitEncoder(__func__);
        id<MTLBlitCommandEncoder> blit = impl->sched.blit;
        for (u32 mip = 0; mip < num_mips; ++mip) {
            const u32 mip_w = std::max(base_width >> mip, 1u);
            const u32 mip_h = std::max(base_height >> mip, 1u);
            const u32 mip_d = std::max(base_depth >> mip, 1u);
            const auto [src_layers, dst_layers] =
                SanitizeCopyLayers(src_type, src->info.resources.layers, dst_type,
                                   dst->info.resources.layers, mip_d);
            if (!is_2d_to_3d && !is_3d_to_2d && src_is_3d) {
                // 3D to 3D
                [blit copyFromTexture:src_texture
                          sourceSlice:0
                          sourceLevel:mip
                         sourceOrigin:MTLOriginMake(0, 0, 0)
                           sourceSize:MTLSizeMake(mip_w, mip_h, mip_d)
                            toTexture:dst_texture
                     destinationSlice:0
                     destinationLevel:mip
                    destinationOrigin:MTLOriginMake(0, 0, 0)];
            } else if (is_2d_to_3d) {
                for (u32 layer = 0; layer < src_layers; ++layer) {
                    [blit copyFromTexture:src_texture
                              sourceSlice:layer
                              sourceLevel:mip
                             sourceOrigin:MTLOriginMake(0, 0, 0)
                               sourceSize:MTLSizeMake(mip_w, mip_h, 1)
                                toTexture:dst_texture
                         destinationSlice:0
                         destinationLevel:mip
                        destinationOrigin:MTLOriginMake(0, 0, layer)];
                }
            } else if (is_3d_to_2d) {
                for (u32 layer = 0; layer < dst_layers; ++layer) {
                    [blit copyFromTexture:src_texture
                              sourceSlice:0
                              sourceLevel:mip
                             sourceOrigin:MTLOriginMake(0, 0, layer)
                               sourceSize:MTLSizeMake(mip_w, mip_h, 1)
                                toTexture:dst_texture
                         destinationSlice:layer
                         destinationLevel:mip
                        destinationOrigin:MTLOriginMake(0, 0, 0)];
                }
            } else {
                const u32 copy_layers = std::min(src_layers, dst_layers);
                for (u32 layer = 0; layer < copy_layers; ++layer) {
                    [blit copyFromTexture:src_texture
                              sourceSlice:layer
                              sourceLevel:mip
                             sourceOrigin:MTLOriginMake(0, 0, 0)
                               sourceSize:MTLSizeMake(mip_w, mip_h, 1)
                                toTexture:dst_texture
                         destinationSlice:layer
                         destinationLevel:mip
                        destinationOrigin:MTLOriginMake(0, 0, 0)];
                }
            }
        }
    }

    dst->flags |= (src->flags & ImageFlagBits::GpuModified);
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::CopyImageWithBuffer(Image* src, Image* dst, const Buffer* buffer, u64 offset) {
    if(dst && dst->backing)MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    const bool lineage=TextureLineage::Get().Armed()&&src&&dst&&src->backing&&dst->backing&&
        (TextureLineage::Candidate(NativeTexture(*src))||TextureLineage::Candidate(NativeTexture(*dst)));
    if(lineage)TextureLineage::Get().Texture("copy-buffer-entry",scheduler,device.GetImpl().device,
        NativeTexture(*src),NativeTexture(*dst),TextureLineage::Info(*src),true);
    SCOPE_EXIT {if(lineage)TextureLineage::Get().Texture("copy-buffer-return",scheduler,device.GetImpl().device,
        NativeTexture(*dst),NativeTexture(*src),TextureLineage::Info(*dst),true);};
    if (!src->backing || !dst->backing) {
        return;
    }
    const u32 num_mips = std::min(src->info.resources.levels, dst->info.resources.levels);
    const u32 num_layers = std::min(src->info.resources.layers, dst->info.resources.layers);
    ASSERT(src->info.resources.layers == dst->info.resources.layers && num_mips == 1);

    SetBackingSamples(dst, dst->info.num_samples, false);
    SetBackingSamples(src, src->info.num_samples);

    vk::BufferImageCopy buffer_copy = {
        .bufferOffset = offset,
        .bufferRowLength = 0,
        .bufferImageHeight = 0,
        .imageSubresource{
            .aspectMask = src->aspect_mask & ~vk::ImageAspectFlagBits::eStencil,
            .mipLevel = 0u,
            .baseArrayLayer = 0,
            .layerCount = num_layers,
        },
        .imageOffset = {0, 0, 0},
        .imageExtent = {src->info.size.width, src->info.size.height, src->info.size.depth},
    };
    const auto copy_size = BufferImageCopySize(buffer_copy, src->info.pixel_format);

    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                           vk::PipelineStageFlagBits2::eCopy, vk::AccessFlagBits2::eTransferWrite);
    needs_flush |= IsBufferAccessed(buffer, offset, copy_size);
    if (needs_flush) {
        FlushBarriers();
    }

    id<MTLTexture> src_texture = NativeTexture(*src);
    id<MTLTexture> dst_texture = NativeTexture(*dst);
    if (CopyCompatible(*src, "image copy through a buffer") &&
        CopyCompatible(*dst, "image copy through a buffer") && src_texture.sampleCount == 1 &&
        dst_texture.sampleCount == 1) {
        const TransferContext ctx{*this, scheduler, *impl};
        id<MTLBuffer> staging = NativeBuffer(*buffer);
        DownloadCopies(ctx, src_texture, src->info.pixel_format,
                       src->backing->image.image_ci.format, staging, std::span{&buffer_copy, 1},
                       __func__);
        buffer_copy.imageSubresource.aspectMask =
            dst->aspect_mask & ~vk::ImageAspectFlagBits::eStencil;
        UploadCopies(ctx, dst_texture, dst->info.pixel_format,
                     dst->backing->image.image_ci.format, staging, std::span{&buffer_copy, 1},
                     __func__);
    }

    dst->flags |= (src->flags & ImageFlagBits::GpuModified);
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::CopyMip(Image* src, Image* dst, u32 mip, u32 slice) {
    if(dst && dst->backing)MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    const bool lineage=TextureLineage::Get().Armed()&&src&&dst&&src->backing&&dst->backing&&
        (TextureLineage::Candidate(NativeTexture(*src))||TextureLineage::Candidate(NativeTexture(*dst)));
    if(lineage)TextureLineage::Get().Texture("copy-mip-entry",scheduler,device.GetImpl().device,
        NativeTexture(*src),NativeTexture(*dst),TextureLineage::Info(*src),true);
    SCOPE_EXIT {if(lineage)TextureLineage::Get().Texture("copy-mip-return",scheduler,device.GetImpl().device,
        NativeTexture(*dst),NativeTexture(*src),TextureLineage::Info(*dst),true);};
    if (!src->backing || !dst->backing) {
        return;
    }
    const auto dst_dim = dst->info.props.is_block ? 2 : 0;
    const auto mip_block_w = std::max(dst->info.size.width >> (mip + dst_dim), 1u);
    const auto mip_block_h = std::max(dst->info.size.height >> (mip + dst_dim), 1u);
    const auto mip_block_p = std::max(dst->info.mips_layout[mip].pitch >> dst_dim, 1u);

    const auto src_dim = src->info.props.is_block ? 2 : 0;
    ASSERT(mip_block_w == (src->info.size.width >> src_dim));
    ASSERT(mip_block_h == (src->info.size.height >> src_dim));
    ASSERT(mip_block_p == (src->info.pitch >> src_dim));

    const vk::ImageType src_type = src->backing->image.image_ci.imageType;
    const vk::ImageType dst_type = dst->backing->image.image_ci.imageType;
    const auto [src_layers, dst_layers] =
        SanitizeCopyLayers(src_type, src->info.resources.layers, dst_type,
                           dst->info.resources.layers, src->info.size.depth);

    SetBackingSamples(dst, dst->info.num_samples);
    SetBackingSamples(src, src->info.num_samples);
    PromoteCopySource(src, *dst);

    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                           vk::PipelineStageFlagBits2::eCopy, vk::AccessFlagBits2::eTransferWrite);
    if (needs_flush) {
        FlushBarriers();
    }

    @autoreleasepool {
        id<MTLTexture> dst_texture = NativeTexture(*dst);
        id<MTLTexture> view = nil;
        id<MTLTexture> src_texture =
            CopySource(*impl, NativeTexture(*src), src->backing->image.image_ci.format,
                       dst_texture, dst->backing->image.image_ci.format, view);
        if (view) {
            scheduler.DeferOperation([view] { (void)view; });
        }
        if (src_texture) {
            scheduler.BlitEncoder(__func__);
            id<MTLBlitCommandEncoder> blit = impl->sched.blit;
            const auto& size = src->info.size;
            if (src_type == vk::ImageType::e3D && dst_type == vk::ImageType::e3D) {
                [blit copyFromTexture:src_texture
                          sourceSlice:0
                          sourceLevel:0
                         sourceOrigin:MTLOriginMake(0, 0, 0)
                           sourceSize:MTLSizeMake(size.width, size.height, size.depth)
                            toTexture:dst_texture
                     destinationSlice:0
                     destinationLevel:mip
                    destinationOrigin:MTLOriginMake(0, 0, 0)];
            } else {
                for (u32 layer = 0; layer < std::min(src_layers, dst_layers); ++layer) {
                    [blit copyFromTexture:src_texture
                              sourceSlice:layer
                              sourceLevel:0
                             sourceOrigin:MTLOriginMake(0, 0, 0)
                               sourceSize:MTLSizeMake(size.width, size.height, 1)
                                toTexture:dst_texture
                         destinationSlice:slice + layer
                         destinationLevel:mip
                        destinationOrigin:MTLOriginMake(0, 0, 0)];
                }
            }
        } else if (impl->FirstTime("copy mip")) {
            LOG_ERROR(Render, "Metal CopyMip between {} and {} is not supported",
                      vk::to_string(src->backing->image.image_ci.format),
                      vk::to_string(dst->backing->image.image_ci.format));
        }
    }

    dst->flags |= (src->flags & ImageFlagBits::GpuModified);
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::CopyColorAndDepth(Image* src, Image* dst) {
    if(dst && dst->backing)MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    if (!src->backing || !dst->backing) {
        return;
    }
    if (src->info.num_samples == 1 && dst->info.num_samples == 1) {
        if (src->info.props.is_depth == dst->info.props.is_depth) {
            CopyImage(src, dst);
        } else {
            // Metal copies between a color and a depth texture through a buffer only.
            const u64 bytes = u64{src->info.size.width} * src->info.size.height *
                              src->info.size.depth * src->info.resources.layers * 4;
            const auto copy_ref = StagingRequest(std::max<u64>(bytes, 64_KB), MemoryType::DeviceLocal);
            CopyImageWithBuffer(src, dst, copy_ref.buffer, copy_ref.offset);
        }
    } else if (src->info.num_samples == 1 && dst->info.num_samples > 1 &&
               dst->info.props.is_depth) {
        // Perform a rendering pass to transfer the channels of source as samples in dest.
        bool needs_flush = Transit(src, vk::ImageLayout::eShaderReadOnlyOptimal,
                                   vk::PipelineStageFlagBits2::eFragmentShader,
                                   vk::AccessFlagBits2::eShaderRead);
        needs_flush |= Transit(dst, vk::ImageLayout::eDepthStencilAttachmentOptimal,
                               vk::PipelineStageFlagBits2::eEarlyFragmentTests |
                                   vk::PipelineStageFlagBits2::eLateFragmentTests,
                               vk::AccessFlagBits2::eDepthStencilAttachmentWrite);
        if (needs_flush) {
            FlushBarriers();
        }
        @autoreleasepool {
            impl->kernels.ColorToMsDepth(NativeTexture(*src), NativeTexture(*dst));
        }
    } else if (impl->FirstTime("depth overlap copy")) {
        LOG_WARNING(Render, "Unimplemented depth overlap copy (Metal)");
    }
}

void Runtime::CopyDepthStencil(Image* src, Image* dst, const SubresourceRange& sub_range) {
    if(dst && dst->backing)MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    if (!src->backing || !dst->backing) {
        return;
    }
    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal,
                               vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferRead);
    needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal,
                           vk::PipelineStageFlagBits2::eCopy, vk::AccessFlagBits2::eTransferWrite);
    if (needs_flush) {
        FlushBarriers();
    }

    id<MTLTexture> src_texture = NativeTexture(*src);
    id<MTLTexture> dst_texture = NativeTexture(*dst);
    // Depth and stencil go together in a combined format; a copy needs equal pixel formats.
    if (src_texture.pixelFormat == dst_texture.pixelFormat) {
        scheduler.BlitEncoder(__func__);
        id<MTLBlitCommandEncoder> blit = impl->sched.blit;
        for (u32 layer = 0; layer < sub_range.extent.layers; ++layer) {
            [blit copyFromTexture:src_texture
                      sourceSlice:sub_range.base.layer + layer
                      sourceLevel:0
                     sourceOrigin:MTLOriginMake(0, 0, 0)
                       sourceSize:MTLSizeMake(dst->info.size.width, dst->info.size.height, 1)
                        toTexture:dst_texture
                 destinationSlice:sub_range.base.layer + layer
                 destinationLevel:0
                destinationOrigin:MTLOriginMake(0, 0, 0)];
        }
    } else if (impl->FirstTime("depth stencil copy")) {
        LOG_ERROR(Render, "Metal CopyDepthStencil between {} and {} is not supported",
                  vk::to_string(src->backing->image.image_ci.format),
                  vk::to_string(dst->backing->image.image_ci.format));
    }

    dst->flags |= ImageFlagBits::GpuModified;
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::ResolveImage(Image* src, Image* dst, const SubresourceRange& src_range,
                           const SubresourceRange& dst_range) {
    if (!src->backing || !dst->backing) {
        return;
    }
    SetBackingSamples(dst, 1, false);
    MemoryStats::NoteContent(dst->backing->image.native,MemoryStats::Content::Copy);
    PromoteCopySource(src, *dst);

    const bool needs_resolve = src->backing->num_samples != 1;
    const auto dst_stage =
        needs_resolve ? vk::PipelineStageFlagBits2::eResolve : vk::PipelineStageFlagBits2::eCopy;

    bool needs_flush = Transit(src, vk::ImageLayout::eTransferSrcOptimal, dst_stage,
                               vk::AccessFlagBits2::eTransferRead);
    needs_flush |= Transit(dst, vk::ImageLayout::eTransferDstOptimal, dst_stage,
                           vk::AccessFlagBits2::eTransferWrite);
    if (needs_flush) {
        FlushBarriers();
    }

    const auto [src_layers, dst_layers] =
        SanitizeCopyLayers(src->backing->image.image_ci.imageType, src->info.resources.layers,
                           dst->backing->image.image_ci.imageType, dst->info.resources.layers, 1);
    const u32 layers = std::min(src_layers, dst_layers);
    @autoreleasepool {
        id<MTLTexture> src_texture = NativeTexture(*src);
        id<MTLTexture> dst_texture = NativeTexture(*dst);
        if (!needs_resolve) {
            id<MTLTexture> view = nil;
            id<MTLTexture> source = CopySource(*impl, src_texture,
                                               src->backing->image.image_ci.format, dst_texture,
                                               dst->backing->image.image_ci.format, view);
            if (view) {
                scheduler.DeferOperation([view] { (void)view; });
            }
            if (source) {
                scheduler.BlitEncoder(__func__);
                id<MTLBlitCommandEncoder> blit = impl->sched.blit;
                for (u32 layer = 0; layer < layers; ++layer) {
                    [blit copyFromTexture:source
                              sourceSlice:src_range.base.layer + layer
                              sourceLevel:0
                             sourceOrigin:MTLOriginMake(0, 0, 0)
                               sourceSize:MTLSizeMake(src->info.size.width,
                                                      src->info.size.height, 1)
                                toTexture:dst_texture
                         destinationSlice:dst_range.base.layer + layer
                         destinationLevel:0
                        destinationOrigin:MTLOriginMake(0, 0, 0)];
                }
            }
        } else {
            // A pass that loads the samples and stores their resolve; the samples stay (the
            // source is read again).
            for (u32 layer = 0; layer < layers; ++layer) {
                MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
                MTLRenderPassColorAttachmentDescriptor* color = pass.colorAttachments[0];
                color.texture = src_texture;
                color.slice = src_range.base.layer + layer;
                color.loadAction = MTLLoadActionLoad;
                color.storeAction = MTLStoreActionStoreAndMultisampleResolve;
                color.resolveTexture = dst_texture;
                color.resolveSlice = dst_range.base.layer + layer;
                scheduler.HostRenderEncoder((__bridge const void*)pass);
                scheduler.EndRendering();
            }
        }
    }

    dst->flags |= ImageFlagBits::GpuModified;
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::ClearImage(Image* dst, const SubresourceRange& range,
                         const vk::ClearValue& clear_value) {
    if (!dst->backing) {
        return;
    }
    if(scene_targets && scene_targets->ClearProxy(*dst,range,clear_value)) {
        dst->flags |= ImageFlagBits::GpuModified;
        dst->flags &= ~ImageFlagBits::Dirty;
        return;
    }
    const bool needs_flush =
        Transit(dst, vk::ImageLayout::eTransferDstOptimal, vk::PipelineStageFlagBits2::eClear,
                vk::AccessFlagBits2::eTransferWrite, range);
    if (needs_flush) {
        FlushBarriers();
    }

    @autoreleasepool {
        id<MTLTexture> texture = NativeTexture(*dst);
        MemoryStats::NoteContent((__bridge const void*)texture,MemoryStats::Content::Clear);
        // The actual clear is encoded below; sample its result on scope exit.
        SCOPE_EXIT {if(TextureLineage::Get().Armed())TextureLineage::Get().Texture(
            "clear-return",scheduler,device.GetImpl().device,texture,nil,TextureLineage::Info(*dst));};
        const vk::Format format = dst->backing->image.image_ci.format;
        const bool is_3d = texture.textureType == MTLTextureType3D;
        const u32 levels = std::min<u32>(range.extent.levels, texture.mipmapLevelCount);
        // A 3-component guest format held as RGBA: the alpha it reads is one (mtl_format_convert.h)
        const vk::Format guest_format = dst->info.pixel_format;
        const bool rgb_guest = FormatConvert::RgbaFormatOf(guest_format) == format;
        const bool depth_texture = VkToMtl::IsDepthPixelFormat(u32(texture.pixelFormat)) ||
                                   VkToMtl::HasStencilPixelFormat(u32(texture.pixelFormat));
        if ((texture.usage & MTLTextureUsageRenderTarget) && !depth_texture) {
            // A render pass per level and slice with a clear load action.
            MTLClearColor color;
            const char* numeric = vk::componentNumericFormat(format, 0);
            if (std::strcmp(numeric, "UINT") == 0) {
                color = MTLClearColorMake(clear_value.color.uint32[0], clear_value.color.uint32[1],
                                          clear_value.color.uint32[2], clear_value.color.uint32[3]);
            } else if (std::strcmp(numeric, "SINT") == 0) {
                color = MTLClearColorMake(clear_value.color.int32[0], clear_value.color.int32[1],
                                          clear_value.color.int32[2], clear_value.color.int32[3]);
            } else {
                color = MTLClearColorMake(clear_value.color.float32[0], clear_value.color.float32[1],
                                          clear_value.color.float32[2], clear_value.color.float32[3]);
            }
            if (rgb_guest) {
                color.alpha = 1.0;
            }
            // Native-resolution clears can use the same bounded queue as proxy clears.
            // Transit/FlushBarriers above preserve the old native/proxy coherence. The queue
            // rejects aliased/shared/mipped/MSAA targets and flushes before another consumer.
            static const bool batch_native = [] {
                const char* e=std::getenv("BB_MTL_NATIVE_CLEAR_BATCH");
                return e && e[0]=='1';
            }();
            if (batch_native && dst->info.pixel_format==format &&
                dst->info.resources.levels==1 && dst->info.resources.layers==1 &&
                range.base.level==0 && range.base.layer==0 &&
                range.extent.levels==1 && range.extent.layers==1 &&
                scheduler.QueueColorClear((__bridge void*)texture,
                                         {color.red,color.green,color.blue,color.alpha})) {
                dst->flags |= ImageFlagBits::GpuModified;
                dst->flags &= ~ImageFlagBits::Dirty;
                return;
            }
            for (u32 level = range.base.level; level < range.base.level + levels; ++level) {
                const u32 planes = is_3d ? std::max<u32>(u32(texture.depth) >> level, 1u)
                                         : std::min<u32>(range.extent.layers,
                                                         u32(texture.arrayLength));
                for (u32 plane = 0; plane < planes; ++plane) {
                    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
                    MTLRenderPassColorAttachmentDescriptor* attachment = pass.colorAttachments[0];
                    attachment.texture = texture;
                    attachment.level = level;
                    if (is_3d) {
                        attachment.depthPlane = plane;
                    } else {
                        attachment.slice = range.base.layer + plane;
                    }
                    attachment.loadAction = MTLLoadActionClear;
                    attachment.clearColor = color;
                    attachment.storeAction = MTLStoreActionStore;
                    scheduler.HostRenderEncoder((__bridge const void*)pass);
                    scheduler.EndRendering();
                }
            }
        } else if (clear_value.color.uint32[0] == 0 && clear_value.color.uint32[1] == 0 &&
                   clear_value.color.uint32[2] == 0 && clear_value.color.uint32[3] == 0) {
            // Not a render target (BC, say): zero bits are zero in every format. A zeroed
            // staging range copied into each level; the data of a clear never leaves the GPU.
            if (CopyCompatible(*dst, "image clear")) {
                for (u32 level = range.base.level; level < range.base.level + levels; ++level) {
                    vk::BufferImageCopy copy{
                        .imageSubresource{
                            .aspectMask = vk::ImageAspectFlagBits::eColor,
                            .mipLevel = level,
                            .baseArrayLayer = is_3d ? 0 : range.base.layer,
                            .layerCount = is_3d ? 1 : range.extent.layers,
                        },
                        .imageExtent = {std::max<u32>(u32(texture.width) >> level, 1u),
                                        std::max<u32>(u32(texture.height) >> level, 1u),
                                        is_3d ? std::max<u32>(u32(texture.depth) >> level, 1u) : 1u},
                    };
                    // A converted format: zero guest texels uploaded, so alpha comes out as one
                    const u64 size = BufferImageCopySize(copy, rgb_guest ? guest_format : format);
                    const StagingSpan staging = StagingRequest(size, MemoryType::DeviceLocal, 16);
                    copy.bufferOffset = staging.offset;
                    scheduler.BlitEncoder(__func__);
                    id<MTLBlitCommandEncoder> blit = impl->sched.blit;
                    id<MTLBuffer> buffer = NativeBuffer(*staging.buffer);
                    [blit fillBuffer:buffer range:NSMakeRange(staging.offset, size) value:0];
                    if (rgb_guest) {
                        UploadCopies({*this, scheduler, *impl},
                                     texture, guest_format, format, buffer, std::span{&copy, 1},
                                     __func__);
                    } else {
                        EncodeBufferToTexture(blit, buffer, texture, format, copy);
                    }
                    AccessBuffer(staging.buffer, staging.offset, size,
                                 vk::PipelineStageFlagBits2::eCopy,
                                 vk::AccessFlagBits2::eTransferWrite);
                }
            }
        } else if (impl->FirstTime("clear " + vk::to_string(format))) {
            LOG_ERROR(Render, "Metal ClearImage of {} (not a render target) to a non-zero value is "
                              "not supported",
                      vk::to_string(format));
        }
    }

    dst->flags |= ImageFlagBits::GpuModified;
    dst->flags &= ~ImageFlagBits::Dirty;
}

void Runtime::SetBackingSamples(Image* image, u32 num_samples, bool copy_backing) {
    BeforeImageAccess();
    auto& backing = image->backing;
    auto& backing_images = image->backing_images;
    const auto& info = image->info;
    if (!backing || backing->num_samples == num_samples) {
        return;
    }
    ASSERT_MSG(!image->info.props.is_depth, "Swapping samples is only valid for color images");
    Image::BackingImage* new_backing;
    // (A backing PromoteImageUsage replaced is retired: never matched again.)
    auto it = std::ranges::find_if(backing_images, [num_samples](const Image::BackingImage& b) {
        return !b.retired && b.num_samples == num_samples;
    });
    if (it == backing_images.end()) {
        auto new_image_ci = backing->image.image_ci;
        new_image_ci.samples =
            Vulkan::LiverpoolToVK::NumSamples(num_samples, image->supported_samples);
        if (new_image_ci.samples != vk::SampleCountFlagBits::e1) {
            // P10.1: a multisampled backing keeps the mutable format flag (PromoteImageUsage
            // copies single sampled textures only).
            new_image_ci.flags |= vk::ImageCreateFlagBits::eMutableFormat;
        }

        new_backing = &backing_images.emplace_back();
        new_backing->num_samples = num_samples;
        CreateImage(new_backing->image, new_image_ci);
        AssociateGuestImage(new_backing->image,image->info.guest_address,image->info.guest_size);

        SetImageName(new_backing->image,
                     fmt::format("Image {}x{}x{} {} {:#x}:{:#x} L:{} M:{} S:{} (backing)",
                                 info.size.width, info.size.height, info.size.depth,
                                 vk::to_string(info.pixel_format), info.guest_address,
                                 info.guest_size, info.resources.layers, info.resources.levels,
                                 num_samples));
    } else {
        new_backing = std::addressof(*it);
    }

    if (copy_backing) {
        ASSERT(image->info.resources.levels == 1 && image->info.resources.layers == 1);

        // Transition current backing to shader read layout
        Transit(image, vk::ImageLayout::eShaderReadOnlyOptimal,
                vk::PipelineStageFlagBits2::eFragmentShader, vk::AccessFlagBits2::eShaderRead);

        // The destination's state, not caring of previous contents
        constexpr auto dst_stage = vk::PipelineStageFlagBits2::eColorAttachmentOutput;
        constexpr auto dst_access =
            vk::AccessFlagBits2::eColorAttachmentRead | vk::AccessFlagBits2::eColorAttachmentWrite;
        constexpr auto dst_layout = vk::ImageLayout::eColorAttachmentOptimal;
        FlushBarriers();

        // Copy between ms and non ms backing images
        @autoreleasepool {
            impl->kernels.CopyBetweenMsImages(
                (__bridge id<MTLTexture>)backing->image.native,
                (__bridge id<MTLTexture>)new_backing->image.native, new_backing->num_samples,
                backing->num_samples > 1);
        }

        // Update current layout in tracker to new backings layout
        new_backing->state.layout = dst_layout;
        new_backing->state.access_mask = dst_access;
        new_backing->state.pl_stage = dst_stage;
    }

    if(copy_backing)MemoryStats::NoteContent(new_backing->image.native,MemoryStats::Content::Copy);
    backing = new_backing;
}

// ---- Promotion of images created lean (P10.1) ------------------------------------------------------

ImageUsagePromotion Runtime::RequiredViewUsage(const Image& image, const ImageViewInfo& view) const {
    const Image::BackingImage* backing = image.backing;
    if (!backing || !backing->image.native || backing->retired) {
        return ImageUsagePromotion::None;
    }
    const vk::ImageCreateInfo& ci = backing->image.image_ci;
    if (ci.samples != vk::SampleCountFlagBits::e1) {
        return ImageUsagePromotion::None; // multisampled backings are made with the flag
    }
    const u32 pixel_format = VkToMtl::PixelFormat(ci.format);
    if (VkToMtl::IsDepthPixelFormat(pixel_format) || VkToMtl::HasStencilPixelFormat(pixel_format)) {
        return ImageUsagePromotion::None; // depth keeps both usages from its creation
    }
    ImageUsagePromotion need = ImageUsagePromotion::None;
    // A shader writes the image (CreateImage: the storage usage is ShaderWrite of a single sampled
    // texture; block formats are not written by shaders).
    if (view.is_storage && !(ci.usage & vk::ImageUsageFlagBits::eStorage) &&
        vk::blockExtent(ci.format)[0] <= 1) {
        need |= ImageUsagePromotion::Storage;
    }
    // CreateImageView makes the view in another format only when ViewCompatible; the sRGB twin
    // needs no usage.
    if (!(ci.flags & vk::ImageCreateFlagBits::eMutableFormat) &&
        VkToMtl::ViewNeedsPixelFormatUsage(ci.format, SubstituteFormat(view.format))) {
        need |= ImageUsagePromotion::MutableFormat;
    }
    return need;
}

void Runtime::PromoteCopySource(Image* src, const Image& dst) {
    if (!Limits().lean_image_usage || !src->backing || !dst.backing) {
        return;
    }
    const vk::ImageCreateInfo& src_ci = src->backing->image.image_ci;
    if ((src_ci.flags & vk::ImageCreateFlagBits::eMutableFormat) ||
        src_ci.samples != vk::SampleCountFlagBits::e1) {
        return;
    }
    // CopySource views the source in the destination's pixel format when they differ.
    if (VkToMtl::ViewNeedsPixelFormatUsage(src_ci.format, dst.backing->image.image_ci.format)) {
        PromoteImageUsage(src, ImageUsagePromotion::MutableFormat);
    }
}

void Runtime::PromoteImageUsage(Image* image, ImageUsagePromotion usage) {
    Image::BackingImage* old = image->backing;
    if (!old || !old->image.native || old->retired) {
        return;
    }
    const vk::ImageCreateInfo& old_ci = old->image.image_ci;
    ImageUsagePromotion missing = ImageUsagePromotion::None;
    if (True(usage & ImageUsagePromotion::Storage) &&
        !(old_ci.usage & vk::ImageUsageFlagBits::eStorage)) {
        missing |= ImageUsagePromotion::Storage;
    }
    if (True(usage & ImageUsagePromotion::MutableFormat) &&
        !(old_ci.flags & vk::ImageCreateFlagBits::eMutableFormat)) {
        missing |= ImageUsagePromotion::MutableFormat;
    }
    if (False(missing)) {
        return;
    }
    BeforeImageAccess();
    if (old_ci.samples != vk::SampleCountFlagBits::e1) {
        // Never made lean (Image::Image, SetBackingSamples): nothing copies multisampled textures.
        if (impl->FirstTime("promote multisampled")) {
            LOG_ERROR(Render, "Metal: a multisampled texture ({}) needs a usage it was created "
                              "without; not promoted",
                      vk::to_string(old_ci.format));
        }
        return;
    }

    @autoreleasepool {
        // 1. The new backing: the creation info of the old one with the usage, the same name.
        vk::ImageCreateInfo ci = old_ci;
        ci.pNext = nullptr;
        if (True(missing & ImageUsagePromotion::Storage)) {
            ci.usage |= vk::ImageUsageFlagBits::eStorage;
        }
        if (True(missing & ImageUsagePromotion::MutableFormat)) {
            ci.flags |= vk::ImageCreateFlagBits::eMutableFormat;
        }
        std::string name;
        {
            id<MTLTexture> old_texture = (__bridge id<MTLTexture>)old->image.native;
            name = old_texture.label ? std::string([old_texture.label UTF8String])
                                     : fmt::format("Image {}x{}x{} {} {:#x}:{:#x}",
                                                   image->info.size.width, image->info.size.height,
                                                   image->info.size.depth,
                                                   vk::to_string(image->info.pixel_format),
                                                   image->info.guest_address,
                                                   image->info.guest_size);
        }
        constexpr std::string_view suffix = " (promoted)";
        if (!name.ends_with(suffix)) {
            name += suffix;
        }
        // (A deque: the address of `old` stays valid.)
        Image::BackingImage& fresh = image->backing_images.emplace_back();
        fresh.num_samples = old->num_samples;
        CreateImage(fresh.image, ci);
        AssociateGuestImage(fresh.image,image->info.guest_address,image->info.guest_size);
        SetImageName(fresh.image, name);

        // 2. The contents. Everything recorded so far writes the old texture, everything after
        // this call uses the new one, so the copy goes between them in stream order: the Transit
        // resolves a scene proxy that is ahead of its native image (Runtime::Transit), the blit
        // encoder ends the open render pass (a held pass is released first) and Metal orders the
        // blit after the encoders that wrote the (hazard tracked) old texture. A texture that was
        // never used (no transition left its state) has nothing to carry over.
        const bool written = old->state.layout != vk::ImageLayout::eUndefined;
        if (written) {
            if (Transit(image, vk::ImageLayout::eTransferSrcOptimal,
                        vk::PipelineStageFlagBits2::eCopy, vk::AccessFlagBits2::eTransferRead)) {
                FlushBarriers();
            }
            scheduler.BlitEncoder(__func__);
            const bool lineage=TextureLineage::Get().Armed()&&
                TextureLineage::Candidate((__bridge id<MTLTexture>)old->image.native);
            if(lineage)TextureLineage::Get().Texture("promotion-source",scheduler,device.GetImpl().device,
                (__bridge id<MTLTexture>)old->image.native,(__bridge id<MTLTexture>)fresh.image.native,TextureLineage::Info(*image));
            id<MTLBlitCommandEncoder> blit = impl->sched.blit;
            [blit copyFromTexture:(__bridge id<MTLTexture>)old->image.native
                        toTexture:(__bridge id<MTLTexture>)fresh.image.native];
            MemoryStats::NoteContent(fresh.image.native,MemoryStats::Content::Copy);
            if(lineage)TextureLineage::Get().Texture("promotion-result",scheduler,device.GetImpl().device,
                (__bridge id<MTLTexture>)fresh.image.native,(__bridge id<MTLTexture>)old->image.native,TextureLineage::Info(*image));
            // What the copy leaves: all subresources written by a transfer.
            fresh.state.pl_stage = vk::PipelineStageFlagBits2::eCopy;
            fresh.state.access_mask = vk::AccessFlagBits2::eTransferWrite;
            fresh.state.layout = vk::ImageLayout::eTransferDstOptimal;
        }
        LOG_DEBUG(Render, "Metal: promoted {} (storage {}, mutable format {}, copied {})", name,
                  True(missing & ImageUsagePromotion::Storage),
                  True(missing & ImageUsagePromotion::MutableFormat), written);

        // 3. Switch, and retire the old backing. Its views go through the deferred destroy like
        // the views of a deleted image (TextureCache::DeleteImage): the slots are erased once the
        // GPU is done with the open tick, and each view's native texture is released after that.
        // Until then a ViewId of the old backing that the caller still holds (a stage's bindings
        // made before this call) stays valid and reads the old texture, whose contents are the
        // ones copied. The old texture itself is released by DestroyImage after the open tick,
        // which contains the copy; the native views keep it alive until they are released.
        image->backing = &fresh;
        if (True(missing & ImageUsagePromotion::Storage)) {
            image->usage_flags |= vk::ImageUsageFlagBits::eStorage;
        }
        old->retired = true;
        old->image_view_infos.clear();
        old->last_view = 0;
        old->subresource_states.clear();
        if (!old->image_view_ids.empty()) {
            scheduler.DeferOperation([views = image->slot_image_views,
                                      ids = std::move(old->image_view_ids)] {
                for (const ImageViewId id : ids) {
                    views->erase(id);
                }
            });
            old->image_view_ids.clear();
        }
        DestroyImage(old->image);
    }
}

} // namespace Metal

namespace Metal {
void Runtime::PrepareExactSnapshot(const void* source,const void* target,
    std::span<const vk::BufferImageCopy> copies) {
    // Source and target are retained native objects, not cache-slot pointers.
    // Prelude already drained image helpers before pool allocation. No guest
    // memory access, format substitution, conversion or void-copy success guess.
    scheduler.EndWithBarrier();
    scheduler.BlitEncoder("Prepared exact native snapshot");
    const auto blit=scheduler.GetImpl().blit;
    for(const auto& copy:copies)
        EncodeTextureToBuffer(blit,(__bridge id<MTLTexture>)source,
            (__bridge id<MTLBuffer>)target,vk::Format::eR8G8B8A8Unorm,copy);
    scheduler.BeginOrderedHostCompute("Prepared exact image export");
    scheduler.OrderAfterHostCompute();
}
} // namespace Metal
