#include "video_core/renderer_metal/mtl_local_library.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// Counterpart: renderer_vulkan/vk_scene_resolution.cpp (the Metal part of the reduced scene
// targets: proxy textures, the copies between a proxy and the native image, views). The
// bookkeeping is VideoCore::SceneTargetsCore's. Design: docs/metal/live_resolution_design.md.

#import <Metal/Metal.h>

#include <cstdio>
#include <algorithm>
#include <array>
#include <string_view>
#include <utility>
#include <chrono>
#include <cstdlib>
#include <unordered_map>
#include <vector>

#include "common/assert.h"
#include "video_core/renderer_metal/mtl_image_usage_log.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Metal {
namespace {

/// How a copy reads and writes a format.
enum class Class : u8 { None, Float, Uint, Sint, Depth, DepthStencil };

/// The class of a texture's pixel format; None for the formats the copy passes do not handle.
Class ClassOf(MTLPixelFormat format) {
    switch (format) {
    case MTLPixelFormatR8Unorm:
    case MTLPixelFormatR8Unorm_sRGB:
    case MTLPixelFormatR8Snorm:
    case MTLPixelFormatRG8Unorm:
    case MTLPixelFormatRG8Unorm_sRGB:
    case MTLPixelFormatRG8Snorm:
    case MTLPixelFormatRGBA8Unorm:
    case MTLPixelFormatRGBA8Unorm_sRGB:
    case MTLPixelFormatRGBA8Snorm:
    case MTLPixelFormatBGRA8Unorm:
    case MTLPixelFormatBGRA8Unorm_sRGB:
    case MTLPixelFormatR16Unorm:
    case MTLPixelFormatR16Snorm:
    case MTLPixelFormatR16Float:
    case MTLPixelFormatRG16Unorm:
    case MTLPixelFormatRG16Snorm:
    case MTLPixelFormatRG16Float:
    case MTLPixelFormatRGBA16Unorm:
    case MTLPixelFormatRGBA16Snorm:
    case MTLPixelFormatRGBA16Float:
    case MTLPixelFormatR32Float:
    case MTLPixelFormatRG32Float:
    case MTLPixelFormatRGBA32Float:
    case MTLPixelFormatRG11B10Float:
    case MTLPixelFormatRGB10A2Unorm:
    case MTLPixelFormatBGR10A2Unorm:
        return Class::Float;
    case MTLPixelFormatR8Uint:
    case MTLPixelFormatR16Uint:
    case MTLPixelFormatR32Uint:
    case MTLPixelFormatRG8Uint:
    case MTLPixelFormatRG16Uint:
    case MTLPixelFormatRG32Uint:
    case MTLPixelFormatRGBA8Uint:
    case MTLPixelFormatRGBA16Uint:
    case MTLPixelFormatRGBA32Uint:
    case MTLPixelFormatRGB10A2Uint:
        return Class::Uint;
    case MTLPixelFormatR8Sint:
    case MTLPixelFormatR16Sint:
    case MTLPixelFormatR32Sint:
    case MTLPixelFormatRG8Sint:
    case MTLPixelFormatRG16Sint:
    case MTLPixelFormatRG32Sint:
    case MTLPixelFormatRGBA8Sint:
    case MTLPixelFormatRGBA16Sint:
    case MTLPixelFormatRGBA32Sint:
        return Class::Sint;
    case MTLPixelFormatDepth16Unorm:
    case MTLPixelFormatDepth32Float:
        return Class::Depth;
    case MTLPixelFormatDepth32Float_Stencil8:
        return Class::DepthStencil;
    default:
        return Class::None;
    }
}

bool IsDepth(Class cls) {
    return cls == Class::Depth || cls == Class::DepthStencil;
}

bool LeanProxyUsage() {
    static const bool enabled = [] {
        const char* full = std::getenv("BB_MTL_FULL_USAGE");
        const char* lean = std::getenv("BB_MTL_PROXY_LEAN");
        return !(full && full[0] == '1') && !(lean && lean[0] == '0');
    }();
    return enabled;
}

/// What the copy passes draw with: a fullscreen triangle (vertex 0, 1, 2 cover the clip space) and
/// per format class a fragment shader that reads the source texel under the destination pixel,
/// scaled: `scale` is source size / destination size, `position.xy` the pixel centre, so a
/// pixel of a half size target reads the second texel of its 2x2 block, and a pixel of a double
/// size one the texel it lies in (nearest, as the Vulkan backend's blit).
NSString* const CopySource = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct TriOut { float4 position [[position]]; };\n"
    "vertex TriOut scene_tri(uint vertex_id [[vertex_id]]) {\n"
    "    float2 p = float2(float((vertex_id << 1) & 2), float(vertex_id & 2));\n"
    "    TriOut out;\n"
    "    out.position = float4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "    return out;\n"
    "}\n"
    "uint2 SourceTexel(float4 position, float2 scale, uint2 size) {\n"
    "    return min(uint2(position.xy * scale), size - 1);\n"
    "}\n"
    "fragment float4 scene_copy_float(TriOut in [[stage_in]],\n"
    "                                 texture2d<float> source [[texture(0)]],\n"
    "                                 constant float2& scale [[buffer(0)]]) {\n"
    "    return source.read(SourceTexel(in.position, scale,\n"
    "                                   uint2(source.get_width(), source.get_height())));\n"
    "}\n"
    "fragment uint4 scene_copy_uint(TriOut in [[stage_in]],\n"
    "                               texture2d<uint> source [[texture(0)]],\n"
    "                               constant float2& scale [[buffer(0)]]) {\n"
    "    return source.read(SourceTexel(in.position, scale,\n"
    "                                   uint2(source.get_width(), source.get_height())));\n"
    "}\n"
    "fragment int4 scene_copy_sint(TriOut in [[stage_in]],\n"
    "                              texture2d<int> source [[texture(0)]],\n"
    "                              constant float2& scale [[buffer(0)]]) {\n"
    "    return source.read(SourceTexel(in.position, scale,\n"
    "                                   uint2(source.get_width(), source.get_height())));\n"
    "}\n"
    "struct DepthOut { float depth [[depth(any)]]; };\n"
    "fragment DepthOut scene_copy_depth(TriOut in [[stage_in]],\n"
    "                                   depth2d<float> source [[texture(0)]],\n"
    "                                   constant float2& scale [[buffer(0)]]) {\n"
    "    DepthOut out;\n"
    "    out.depth = source.read(SourceTexel(in.position, scale,\n"
    "                                        uint2(source.get_width(), source.get_height())));\n"
    "    return out;\n"
    "}\n"
    "struct DepthStencilOut { float depth [[depth(any)]]; uint stencil [[stencil]]; };\n"
    "fragment DepthStencilOut scene_copy_depth_stencil(TriOut in [[stage_in]],\n"
    "                                                  depth2d<float> source [[texture(0)]],\n"
    "                                                  texture2d<uint> stencil [[texture(1)]],\n"
    "                                                  constant float2& scale [[buffer(0)]]) {\n"
    "    const uint2 texel = SourceTexel(in.position, scale,\n"
    "                                    uint2(source.get_width(), source.get_height()));\n"
    "    DepthStencilOut out;\n"
    "    out.depth = source.read(texel);\n"
    "    out.stencil = stencil.read(texel).x;\n"
    "    return out;\n"
    "}\n"
    "kernel void scene_upscale_alpha(texture2d<float, access::read> rgb [[texture(0)]],\n"
    "    texture2d<float, access::read> alpha [[texture(1)]],\n"
    "    texture2d<float, access::write> dst [[texture(2)]],\n"
    "    constant float2& scale [[buffer(0)]], uint2 p [[thread_position_in_grid]]) {\n"
    "    if (any(p >= uint2(dst.get_width(), dst.get_height()))) return;\n"
    "    uint2 a = SourceTexel(float4(float2(p) + 0.5f, 0, 1), scale,\n"
    "                          uint2(alpha.get_width(), alpha.get_height()));\n"
    "    dst.write(float4(rgb.read(p).rgb, alpha.read(a).a), p);\n"
    "}\n";

id<MTLTexture> TextureOf(const VideoCore::UniqueImage& image) {
    return (__bridge id<MTLTexture>)image.native;
}

VideoCore::NativeView NativeViewOf(id<MTLTexture> texture) {
    return reinterpret_cast<VideoCore::NativeView>((__bridge void*)texture);
}

std::string ErrorText(NSError* error) {
    return error ? error.localizedDescription.UTF8String : "unknown";
}

} // namespace

/// The pipelines of the copy passes (made on first use, on the recording thread).
struct SceneTargets::Impl {
    const char* copy_reason = "coherence";
    u64 lean_created = 0, full_created = 0, format_promotions = 0;
    struct CopyRow {
        u64 uid=0, count=0; u32 level=0, sw=0, sh=0, tw=0, th=0, format=0;
        bool resolve=false; const char* reason=nullptr;
    };
    struct CopyAudit {
        std::array<CopyRow,128> rows{}; unsigned used=0; u64 dropped=0, total=0;
        std::chrono::steady_clock::time_point reported=std::chrono::steady_clock::now();
        void Record(CopyRow key) {
            ++total;unsigned i=0;
            for(;i<used;++i) { const auto& r=rows[i];
                if(r.uid==key.uid && r.level==key.level && r.resolve==key.resolve && r.reason==key.reason &&
                   r.sw==key.sw && r.sh==key.sh && r.tw==key.tw && r.th==key.th && r.format==key.format) break;
            }
            if(i==used) { if(used==rows.size()) ++dropped;else rows[used++]=key; }
            if(i<used) ++rows[i].count;
            const auto now=std::chrono::steady_clock::now();
            if(now-reported<std::chrono::seconds(5)) return;
            std::printf("Metal scene copies: calls=%llu, overflow=%llu; fixed 128 keys, top 12 counts\n",(unsigned long long)total,(unsigned long long)dropped);
            std::sort(rows.begin(),rows.begin()+used,[](const auto& a,const auto& b){return a.count>b.count;});
            for(unsigned j=0;j<std::min(used,12u);++j) {const auto& r=rows[j];
                std::printf("Metal scene copy: uid=%llu, mip=%u, direction=%s, reason=%s, source=%ux%u, target=%ux%u, format=%u, calls=%llu\n",
                    (unsigned long long)r.uid,r.level,r.resolve?"resolve":"fill",r.reason,r.sw,r.sh,r.tw,r.th,r.format,(unsigned long long)r.count);
            }
            rows={}; used=0;total=dropped=0;reported=now;
        }
    };
    std::unique_ptr<CopyAudit> copy_audit;
    u64 direct_copies=0;
    std::chrono::steady_clock::time_point direct_reported{};
    u64 overwrite_candidates=0, overwrite_dirty=0, overwrite_discarded=0, proxy_clears=0;
    std::chrono::steady_clock::time_point proxy_clear_reported{};
    std::chrono::steady_clock::time_point overwrite_reported{};
    Device::Impl& dev;
    id<MTLLibrary> library = nil;
    id<MTLComputePipelineState> alpha_merge = nil;
    bool alpha_attempted = false;
    u64 alpha_fused = 0;
    std::chrono::steady_clock::time_point alpha_reported{};
    id<MTLDepthStencilState> depth_always = nil;   ///< writes every fragment's depth
    id<MTLDepthStencilState> stencil_replace = nil; ///< ... and the exported stencil
    std::unordered_map<u32, id<MTLRenderPipelineState>> pipelines; ///< by the target's pixel format

    explicit Impl(Device::Impl& dev_) : dev{dev_} {}

    void EnsureLibrary() {
        if (library) return;
        NSError* error = nil;
        MTLCompileOptions* options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_1;
        library = Metal::LocalLibrary::Build(dev.device, CopySource, options, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"scene_tri", @"scene_copy_float", @"scene_copy_uint", @"scene_copy_sint", @"scene_copy_depth", @"scene_copy_depth_stencil", @"scene_upscale_alpha"}); });
        ASSERT_MSG(library, "Scene proxy copy shaders did not compile: {}", ErrorText(error));
    }

    id<MTLComputePipelineState> AlphaPipeline() {
        if (!alpha_attempted) {
            alpha_attempted = true;
            EnsureLibrary();
            NSError* error = nil;
            alpha_merge = [dev.device newComputePipelineStateWithFunction:
                [library newFunctionWithName:@"scene_upscale_alpha"] error:&error];
            if (!alpha_merge) std::printf("Metal upscale alpha: pipeline unavailable (%s); original merge retained\n", ErrorText(error).c_str());
        }
        return alpha_merge;
    }

    id<MTLRenderPipelineState> Pipeline(MTLPixelFormat format) {
        auto& pipeline = pipelines[u32(format)];
        if (pipeline) {
            return pipeline;
        }
        EnsureLibrary();
        const Class cls = ClassOf(format);
        NSString* fragment = nil;
        switch (cls) {
        case Class::Float:
            fragment = @"scene_copy_float";
            break;
        case Class::Uint:
            fragment = @"scene_copy_uint";
            break;
        case Class::Sint:
            fragment = @"scene_copy_sint";
            break;
        case Class::Depth:
            fragment = @"scene_copy_depth";
            break;
        case Class::DepthStencil:
            fragment = @"scene_copy_depth_stencil";
            break;
        case Class::None:
            UNREACHABLE_MSG("Scene proxy copy of pixel format {}", u32(format));
        }
        MTLRenderPipelineDescriptor* descriptor = [MTLRenderPipelineDescriptor new];
        descriptor.label = @"Scene proxy copy";
        descriptor.vertexFunction = [library newFunctionWithName:@"scene_tri"];
        descriptor.fragmentFunction = [library newFunctionWithName:fragment];
        if (IsDepth(cls)) {
            descriptor.depthAttachmentPixelFormat = format;
            if (cls == Class::DepthStencil) {
                descriptor.stencilAttachmentPixelFormat = format;
            }
        } else {
            descriptor.colorAttachments[0].pixelFormat = format;
        }
        NSError* error = nil;
        pipeline = [dev.device newRenderPipelineStateWithDescriptor:descriptor error:&error];
        ASSERT_MSG(pipeline, "Scene proxy copy pipeline failed (pixel format {}): {}", u32(format),
                   ErrorText(error));
        return pipeline;
    }

    id<MTLDepthStencilState> DepthState(bool stencil) {
        __strong id<MTLDepthStencilState>& state = stencil ? stencil_replace : depth_always;
        if (!state) {
            MTLDepthStencilDescriptor* descriptor = [MTLDepthStencilDescriptor new];
            descriptor.depthCompareFunction = MTLCompareFunctionAlways;
            descriptor.depthWriteEnabled = YES;
            if (stencil) {
                // The fragment's stencil output replaces the reference value; every outcome
                // writes it.
                MTLStencilDescriptor* replace = [MTLStencilDescriptor new];
                replace.stencilCompareFunction = MTLCompareFunctionAlways;
                replace.stencilFailureOperation = MTLStencilOperationReplace;
                replace.depthFailureOperation = MTLStencilOperationReplace;
                replace.depthStencilPassOperation = MTLStencilOperationReplace;
                replace.readMask = 0xff;
                replace.writeMask = 0xff;
                descriptor.frontFaceStencil = replace;
                descriptor.backFaceStencil = replace;
            }
            state = [dev.device newDepthStencilStateWithDescriptor:descriptor];
        }
        return state;
    }
};

/// A proxy: its texture (made by the runtime, so it is resident and released by the deferred
/// destroys) and the views of it handed out, by view format and swizzle.
struct SceneTargets::Proxy final : ProxyData {
    ~Proxy() override {
        // Command buffers do not retain what they use: the views go once the GPU is done with
        // the open tick, like the texture (Runtime::DestroyImage).
        if (!views.empty()) {
            scheduler->DeferOperation([views = std::move(views)] { (void)views; });
        }
    }

    struct View {
        vk::Format format;
        vk::ComponentMapping mapping;
        id<MTLTexture> texture;
    };
    VideoCore::UniqueImage image;
    vk::Format base_format{}; ///< the Vulkan format the texture was made in
    std::vector<View> views;
    Scheduler* scheduler = nullptr;
};

SceneTargets::Proxy& SceneTargets::Of(Entry& entry) {
    return static_cast<Proxy&>(*entry.proxy);
}

SceneTargets::SceneTargets(Device& device_, Scheduler& scheduler_, Runtime& runtime_,
                           VideoCore::TextureCache& texture_cache)
    : SceneTargets(device_, scheduler_, runtime_,
                   [&texture_cache](VideoCore::ImageId id, u64 uid) {
                       return texture_cache.TryGetImage(id, uid);
                   }) {}

SceneTargets::SceneTargets(Device& device_, Scheduler& scheduler_, Runtime& runtime_, Lookup get)
    : SceneTargetsCore{std::move(get)}, device{device_}, scheduler{scheduler_},
      runtime{runtime_}, impl{std::make_unique<Impl>(device_.GetImpl())} {
    runtime.scene_targets = this;
}

SceneTargets::~SceneTargets() {
    runtime.scene_targets = nullptr;
    // The pipelines may be in a command buffer in flight; the proxies and their views follow
    // (the entries' destructors) through the runtime's and the scheduler's deferred operations.
    scheduler.DeferOperation([impl = std::move(impl)] { (void)impl; });
}

bool SceneTargets::ProxyFormatSupported(const VideoCore::Image& image) const {
    // From the creation info of the texture (no message to the texture): Eligible asks this for
    // every attachment of every draw.
    const auto& ci = image.backing->image.image_ci;
    return image.backing->image.native && ci.imageType == vk::ImageType::e2D &&
           ci.samples == vk::SampleCountFlagBits::e1 &&
           ClassOf(MTLPixelFormat(VkToMtl::PixelFormat(ci.format))) != Class::None;
}

bool SceneTargets::MipChainFormatSupported(const VideoCore::Image& image) const {
    const auto& ci = image.backing->image.image_ci;
    const Class cls = ClassOf(MTLPixelFormat(VkToMtl::PixelFormat(ci.format)));
    return image.backing->image.native && ci.imageType == vk::ImageType::e2D && cls != Class::None &&
           !IsDepth(cls);
}

std::unique_ptr<VideoCore::SceneTargetsCore::ProxyData> SceneTargets::CreateProxy(
    const VideoCore::Image& original, VideoCore::SceneResolution::Size size) {
    auto data = std::make_unique<Proxy>();
    data->scheduler = &scheduler;
    auto ci = original.backing->image.image_ci;
    ci.pNext = nullptr;
    ci.extent = vk::Extent3D{size.width, size.height, 1};
    ci.mipLevels = 1;
    ci.arrayLayers = 1;
    ci.samples = vk::SampleCountFlagBits::e1;
    // Sampled by the passes and the upscaler, drawn into; never written by a shader (no storage:
    // a texture that is only a render target keeps the GPU's lossless compression).
    const bool depth = IsDepth(ClassOf(MTLPixelFormat(VkToMtl::PixelFormat(ci.format))));
    ci.usage = vk::ImageUsageFlagBits::eSampled |
               (depth ? vk::ImageUsageFlagBits::eDepthStencilAttachment
                      : vk::ImageUsageFlagBits::eColorAttachment);
    // Colour targets retain lossless compression until a non-sRGB format view is needed.
    // Depth/stencil keeps its stencil view usage, as in the texture-cache policy (P10.1).
    const bool lean = !depth && LeanProxyUsage();
    ci.flags = lean ? vk::ImageCreateFlags{} : vk::ImageCreateFlagBits::eMutableFormat;
    runtime.CreateImage(data->image, ci);
    runtime.AssociateGuestImage(data->image,original.info.guest_address,original.info.guest_size);
    data->base_format = ci.format;
    runtime.SetImageName(data->image, fmt::format("Scene proxy {}x{} {}", size.width, size.height,
                                                  vk::to_string(original.info.pixel_format)));
    ++(lean ? impl->lean_created : impl->full_created);
    std::printf("Metal proxy usage: lean-created=%llu, full-created=%llu, format-promotions=%llu; no storage usage, no extra cache\n",
                (unsigned long long)impl->lean_created, (unsigned long long)impl->full_created,
                (unsigned long long)impl->format_promotions);
    return data;
}

void SceneTargets::Finish() {
    scheduler.Finish();
}

vk::ImageLayout SceneTargets::Prepare(Entry&, const VideoCore::Image& original, Use use) {
    // Metal has no layouts; the values are the ones the Vulkan targets would hand out.
    if (use == Use::Sample) {
        return vk::ImageLayout::eShaderReadOnlyOptimal;
    }
    return original.info.props.is_depth ? vk::ImageLayout::eDepthStencilAttachmentOptimal
                                        : vk::ImageLayout::eColorAttachmentOptimal;
}

void SceneTargets::NativeAccess(VideoCore::Image& image, vk::AccessFlags2 access, bool clear) {
    const char* reason="native-read";
    if(access & vk::AccessFlagBits2::eTransferWrite) reason="transfer-write";
    else if(access & vk::AccessFlagBits2::eTransferRead) reason="transfer-read";
    else if(access & vk::AccessFlagBits2::eShaderWrite) reason="shader-write";
    else if(access & (vk::AccessFlagBits2::eColorAttachmentWrite|vk::AccessFlagBits2::eDepthStencilAttachmentWrite)) reason="attachment-write";
    else if(access & vk::AccessFlagBits2::eShaderRead) reason="shader-read";
    if(clear) reason="clear";
    const auto before=std::exchange(impl->copy_reason,reason);
    constexpr auto writes = vk::AccessFlagBits2::eShaderWrite | vk::AccessFlagBits2::eTransferWrite |
                            vk::AccessFlagBits2::eColorAttachmentWrite |
                            vk::AccessFlagBits2::eDepthStencilAttachmentWrite |
                            vk::AccessFlagBits2::eMemoryWrite;
    SceneTargetsCore::NativeAccess(image, bool(access & writes));
    impl->copy_reason=before;
}

void SceneTargets::CopyProxy(Entry& e, VideoCore::Image& original, bool to_native) {
    auto& data = Of(e);
    // The copy is a render pass of its own: the open one ends (a held pass is released), and the
    // draw that follows begins its pass again (the core decides that by IsRenderingWith).
    scheduler.EndRendering();
    if (debug) {
        std::printf("Scene %s: %s %ux%u level %u\n", to_native ? "resolve" : "fill",
                    vk::to_string(original.info.pixel_format).c_str(), original.info.size.width,
                    original.info.size.height, e.level);
    }
    id<MTLTexture> proxy = TextureOf(data.image);
    id<MTLTexture> native = NativeTexture(original);
    const Class cls = ClassOf(proxy.pixelFormat);
    const bool depth = IsDepth(cls);
    // The shared image state's bookkeeping, as Vulkan's copies keep it (Metal tracks the hazards
    // of the textures itself).
    const VideoCore::SubresourceRange level_range{.base = {e.level, 0}, .extent = {1, 1}};
    if (to_native) {
        runtime.Transit(&original,
                        depth ? vk::ImageLayout::eDepthStencilAttachmentOptimal
                              : vk::ImageLayout::eColorAttachmentOptimal,
                        depth ? vk::PipelineStageFlagBits2::eEarlyFragmentTests |
                                    vk::PipelineStageFlagBits2::eLateFragmentTests
                              : vk::PipelineStageFlags2(
                                    vk::PipelineStageFlagBits2::eColorAttachmentOutput),
                        depth ? vk::AccessFlagBits2::eDepthStencilAttachmentWrite
                              : vk::AccessFlagBits2::eColorAttachmentWrite,
                        level_range);
    } else {
        runtime.Transit(&original,
                        depth ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                              : vk::ImageLayout::eShaderReadOnlyOptimal,
                        vk::PipelineStageFlagBits2::eFragmentShader,
                        vk::AccessFlagBits2::eShaderRead, level_range);
    }
    runtime.FlushBarriers();

    @autoreleasepool {
        // The native side of one mip level: the texture itself, or a view of that level.
        id<MTLTexture> level = native;
        if (e.level != 0 || native.mipmapLevelCount > 1) {
            level = [native newTextureViewWithPixelFormat:native.pixelFormat
                                              textureType:MTLTextureType2D
                                                   levels:NSMakeRange(e.level, 1)
                                                   slices:NSMakeRange(0, 1)];
            ASSERT_MSG(level, "Failed to view level {} of a scene image", e.level);
            if (ImageUsageLog::Enabled()) {
                ImageUsageLog::ViewMade((__bridge const void*)native, (__bridge const void*)level);
            }
        }
        id<MTLTexture> source = to_native ? proxy : level;
        id<MTLTexture> target = to_native ? level : proxy;
        if (level != native) scheduler.DeferOperation([level] { (void)level; });
        char label[88];
        const char* reason=to_native?impl->copy_reason:(impl->copy_reason==std::string_view{"upscaler-input"}?"upscaler-input":"attachment-load");
        std::snprintf(label,sizeof(label),"Host texture copy %s %s %lux%lu>%lux%lu f%u m%u",
            to_native?"resolve":"fill",reason,(unsigned long)source.width,(unsigned long)source.height,
            (unsigned long)target.width,(unsigned long)target.height,unsigned(target.pixelFormat),e.level);
        if(scheduler.GetImpl().profile->Enabled()) {
            if(!impl->copy_audit) impl->copy_audit=std::make_unique<Impl::CopyAudit>();
            impl->copy_audit->Record({original.image_uid,0,e.level,u32(source.width),u32(source.height),u32(target.width),u32(target.height),u32(target.pixelFormat),to_native,reason});
        }
        CopyTextures((__bridge const void*)source, (__bridge const void*)target,label);
    }
}

void SceneTargets::CopyTextures(const void* src, const void* dst, const char* label) {
    scheduler.EndRendering();
    id<MTLTexture> source = (__bridge id<MTLTexture>)src;
    id<MTLTexture> target = (__bridge id<MTLTexture>)dst;
    const Class cls = ClassOf(target.pixelFormat);
    const bool depth = IsDepth(cls);
    @autoreleasepool {
        // The stencil of a combined format is read through a view of its own.
        id<MTLTexture> stencil = nil;
        if (cls == Class::DepthStencil) {
            stencil = [source newTextureViewWithPixelFormat:MTLPixelFormatX32_Stencil8];
            ASSERT_MSG(stencil, "Failed to view the stencil of a scene image");
            if (ImageUsageLog::Enabled()) {
                ImageUsageLog::ViewMade((__bridge const void*)source, (__bridge const void*)stencil);
            }
        }
        // What the command buffer uses but does not retain stays alive until it is done.
        if (stencil) {
            scheduler.DeferOperation([stencil] {
                (void)stencil;
            });
        }

        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        if (depth) {
            pass.depthAttachment.texture = target;
            pass.depthAttachment.loadAction = MTLLoadActionDontCare; // the triangle covers it
            pass.depthAttachment.storeAction = MTLStoreActionStore;
            if (cls == Class::DepthStencil) {
                pass.stencilAttachment.texture = target;
                pass.stencilAttachment.loadAction = MTLLoadActionDontCare;
                pass.stencilAttachment.storeAction = MTLStoreActionStore;
            }
        } else {
            pass.colorAttachments[0].texture = target;
            pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        }
        scheduler.HostRenderEncoder((__bridge const void*)pass);
        scheduler.LabelEncoder(label);
        id<MTLRenderCommandEncoder> render = scheduler.GetImpl().render;
        [render setRenderPipelineState:impl->Pipeline(target.pixelFormat)];
        if (depth) {
            [render setDepthStencilState:impl->DepthState(cls == Class::DepthStencil)];
        }
        [render setViewport:MTLViewport{0, 0, double(target.width), double(target.height), 0, 1}];
        const float scale[2] = {float(source.width) / float(target.width),
                                float(source.height) / float(target.height)};
        [render setFragmentBytes:scale length:sizeof(scale) atIndex:0];
        [render setFragmentTexture:source atIndex:0];
        if (stencil) {
            [render setFragmentTexture:stencil atIndex:1];
        }
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        scheduler.EndRendering();
    }
}

VideoCore::NativeView SceneTargets::View(Entry& e, const VideoCore::Image& original,
                                         const VideoCore::ImageViewInfo& info, bool) {
    auto& proxy = Of(e);
    id<MTLTexture> base = TextureOf(proxy.image);
    if (IsDepth(ClassOf(base.pixelFormat))) {
        return NativeViewOf(base); // the whole texture: depth is read and attached as it is
    }
    for (const auto& view : proxy.views) {
        if (view.format == info.format && view.mapping == info.mapping) {
            return NativeViewOf(view.texture);
        }
    }
    const vk::Format format = runtime.SupportedFormat(info.format, {});
    const u32 view_format = VkToMtl::PixelFormat(format);
    const bool compatible = runtime.ViewFormatCompatible(proxy.base_format, info.format);
    if (compatible && !(base.usage & MTLTextureUsagePixelFormatView) &&
        VkToMtl::ViewNeedsPixelFormatUsage(proxy.base_format, format)) {
        // First real reinterpretation: preserve exact bits and existing old-view users.
        // The old backing/views are retired after this tick, never retained as a second cache.
        scheduler.EndWithBarrier();
        auto ci = proxy.image.image_ci;
        ci.pNext = nullptr;
        ci.flags |= vk::ImageCreateFlagBits::eMutableFormat;
        VideoCore::UniqueImage fresh;
        runtime.CreateImage(fresh, ci);
        runtime.AssociateGuestImage(fresh, original.info.guest_address, original.info.guest_size);
        runtime.SetImageName(fresh, fmt::format("Scene proxy {}x{} {} (format view)",
                                              base.width, base.height, vk::to_string(proxy.base_format)));
        scheduler.BlitEncoder("Promote scene proxy format usage");
        [scheduler.GetImpl().blit copyFromTexture:base toTexture:TextureOf(fresh)];
        scheduler.EndWithBarrier();
        if (!proxy.views.empty()) {
            scheduler.DeferOperation([views = std::move(proxy.views)] { (void)views; });
            proxy.views.clear();
        }
        proxy.image = std::move(fresh); // Runtime::DestroyImage defers the old backing.
        base = TextureOf(proxy.image);
        ++view_generation;
        ++impl->format_promotions;
        std::printf("Metal proxy usage: lean-created=%llu, full-created=%llu, format-promotions=%llu; copied existing bits once, retired old backing/views\n",
                    (unsigned long long)impl->lean_created, (unsigned long long)impl->full_created,
                    (unsigned long long)impl->format_promotions);
    }
    // The view's pixel format and swizzle as Runtime::CreateImageView derives them for the
    // native image, so that a pass finds the format its pipeline was made for.
    MTLPixelFormat pixel_format = base.pixelFormat;
    if (view_format != 0 && view_format != u32(base.pixelFormat) &&
        compatible && ((base.usage & MTLTextureUsagePixelFormatView) ||
                       !VkToMtl::ViewNeedsPixelFormatUsage(proxy.base_format, format))) {
        pixel_format = MTLPixelFormat(view_format);
    }
    const VkToMtl::TextureSwizzle swizzle = VkToMtl::ComposeSwizzle(info.mapping, format);
    id<MTLTexture> texture = base;
    if (pixel_format != base.pixelFormat || swizzle != VkToMtl::IdentitySwizzle) {
        texture = [base newTextureViewWithPixelFormat:pixel_format
                                          textureType:MTLTextureType2D
                                               levels:NSMakeRange(0, 1)
                                               slices:NSMakeRange(0, 1)
                                              swizzle:MTLTextureSwizzleChannelsMake(
                                                          MTLTextureSwizzle(swizzle.r),
                                                          MTLTextureSwizzle(swizzle.g),
                                                          MTLTextureSwizzle(swizzle.b),
                                                          MTLTextureSwizzle(swizzle.a))];
        ASSERT_MSG(texture, "Failed to create a view (format {}) of a scene proxy",
                   vk::to_string(info.format));
    }
    if (ImageUsageLog::Enabled()) {
        ImageUsageLog::ViewMade((__bridge const void*)base, (__bridge const void*)texture);
    }
    proxy.views.push_back({info.format, info.mapping, texture});
    return NativeViewOf(texture);
}

namespace {
bool ColorClearDiscardEnabled() {
    static const bool baseline=[] {
        const char* env=std::getenv("BB_MTL_CLEAR_DISCARD");
        return !env || env[0]!='0';
    }();
    static const char* control=std::getenv("BB_MTL_CLEAR_DISCARD_FILE");
    if(!control || !*control) return baseline;
    // Optional diagnostic A/B only. Poll at most once a second; the selected policy is
    // used for a new attachment, never to reinterpret resources already recorded.
    static bool enabled=baseline;
    static auto next=std::chrono::steady_clock::time_point{};
    const auto now=std::chrono::steady_clock::now();
    if(now>=next) {
        next=now+std::chrono::seconds(1);
        if(FILE* f=std::fopen(control,"r")) {
            const int value=std::fgetc(f);std::fclose(f);
            if((value=='0'||value=='1') && enabled!=(value=='1')) {
                enabled=value=='1';
                std::printf("Metal scene clear mode: enabled=%d; diagnostic control, no cache reset\n",int(enabled));
            }
        }
    }
    return enabled;
}
} // namespace

u64 SceneTargets::DiscardedCopyResolves() const { return impl->overwrite_discarded; }

u64 SceneTargets::DirectCopyCount() const {return impl->direct_copies;}

const void* SceneTargets::ExistingTilingProxy(const VideoCore::Image& source) const {
    if(!source.backing || !ProxyDirty(source,0) || !Eligible(source))return nullptr;
    const auto& a=source.info;
    const bool half=a.pixel_format==vk::Format::eR16G16B16A16Sfloat;
    static const bool half_enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING_HALF");return v&&v[0]=='1';}();
    if(!((!half && a.pixel_format==vk::Format::eR8G8B8A8Unorm) || (half && half_enabled)) ||
       a.resources.levels!=1 || a.resources.layers!=1)return nullptr;
    const auto found=entries.find(Key(source.image_uid,0));if(found==entries.end())return nullptr;
    auto proxy=TextureOf(Of(*found->second).image),native=NativeTexture(source);
    if(!proxy || !native || proxy.pixelFormat!=(half?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm) || native.pixelFormat!=proxy.pixelFormat ||
       native.width!=a.size.width || native.height!=a.size.height || native.width!=proxy.width*2 || native.height!=proxy.height*2 ||
       !(proxy.usage&MTLTextureUsageShaderRead))return nullptr;
    for(id<MTLTexture> t:{proxy,native})
        if(t.textureType!=MTLTextureType2D || t.sampleCount!=1 || t.mipmapLevelCount!=1 || t.arrayLength!=1 ||
           t.parentTexture || t.buffer || t.iosurface || t.heap || t.isAliasable || t.storageMode!=MTLStorageModePrivate)return nullptr;
    return (__bridge const void*)proxy;
}

bool SceneTargets::CopyDirectToNative(VideoCore::Image& source, VideoCore::Image& target) {
    static const bool enabled=[] {const char* v=std::getenv("BB_MTL_DIRECT_COPY");return v&&v[0]=='1';}();
    if(!enabled || &source==&target || !source.backing || !target.backing || source.backing==target.backing ||
       !ProxyDirty(source,0) || !Eligible(source) || !Eligible(target))return false;
    const auto& a=source.info;const auto& b=target.info;
    if(a.resources.levels!=1 || b.resources.levels!=1 || a.resources.layers!=1 || b.resources.layers!=1 ||
       a.size.width!=b.size.width || a.size.height!=b.size.height || a.pixel_format!=b.pixel_format ||
       !a.guest_address || !b.guest_address || !a.guest_size || !b.guest_size ||
       a.guest_size>~u64(0)-a.guest_address || b.guest_size>~u64(0)-b.guest_address ||
       !(a.guest_address+a.guest_size<=b.guest_address || b.guest_address+b.guest_size<=a.guest_address))return false;
    const auto found=entries.find(Key(source.image_uid,0));if(found==entries.end())return false;
    id<MTLTexture> proxy=TextureOf(Of(*found->second).image);
    id<MTLTexture> native=NativeTexture(source),dest=NativeTexture(target);
    if(!proxy || !native || !dest || native==dest || proxy==dest || IsDepth(ClassOf(proxy.pixelFormat)) ||
       proxy.pixelFormat!=native.pixelFormat || native.pixelFormat!=dest.pixelFormat ||
       native.width!=a.size.width || native.height!=a.size.height || dest.width!=b.size.width || dest.height!=b.size.height ||
       native.width!=proxy.width*2 || native.height!=proxy.height*2)return false;
    // No promoted/reinterpreted views, layered surfaces or potentially aliased allocations.
    for(id<MTLTexture> t:{proxy,native,dest})
        if(t.textureType!=MTLTextureType2D || t.sampleCount!=1 || t.mipmapLevelCount!=1 || t.arrayLength!=1 ||
           t.parentTexture || t.buffer || t.iosurface || t.heap || t.isAliasable || t.storageMode!=MTLStorageModePrivate)return false;
    if(!(proxy.usage&MTLTextureUsageShaderRead) || !(dest.usage&MTLTextureUsageRenderTarget))return false;
    // Exactly the existing nearest resolve into an identically formatted destination. The
    // source native image stays stale/dirty and will resolve normally on its next real read.
    FullCopyOverwrite(target,(__bridge const void*)native,(__bridge const void*)dest);
    runtime.Transit(&target,vk::ImageLayout::eColorAttachmentOptimal,
                    vk::PipelineStageFlagBits2::eColorAttachmentOutput,vk::AccessFlagBits2::eColorAttachmentWrite);
    runtime.FlushBarriers();
    CopyTextures((__bridge const void*)proxy,(__bridge const void*)dest,"Direct scene copy to native target");
    ++impl->direct_copies;
    const auto now=std::chrono::steady_clock::now();
    if(std::getenv("BB_FRAME_STATS") && now-impl->direct_reported>=std::chrono::seconds(5)) {
        std::printf("Metal direct image copy: copies=%llu; existing proxy to native destination, no extra texture\n",(unsigned long long)impl->direct_copies);impl->direct_reported=now;
    }
    return true;
}

void SceneTargets::FullCopyOverwrite(VideoCore::Image& image, const void* src, const void* dst) {
    const auto source=(__bridge id<MTLTexture>)src, target=(__bridge id<MTLTexture>)dst;
    if(!source || !target || source==target || target!=NativeTexture(image) ||
       image.info.resources.levels!=1 || image.info.resources.layers!=1 ||
       source.textureType!=MTLTextureType2D || target.textureType!=MTLTextureType2D ||
       source.sampleCount!=1 || target.sampleCount!=1 ||
       source.mipmapLevelCount!=1 || target.mipmapLevelCount!=1 ||
       source.width!=target.width || source.height!=target.height || source.pixelFormat!=target.pixelFormat) return;
    auto root=[](id<MTLTexture> t) { while(t.parentTexture) t=t.parentTexture;return t; };
    if(root(source)==root(target) || (source.buffer && source.buffer==target.buffer) ||
       (source.iosurface && source.iosurface==target.iosurface)) return;
    static const bool baseline=[] { const char* v=std::getenv("BB_MTL_OVERWRITE_DISCARD");return !v || v[0]!='0'; }();
    static const char* control=std::getenv("BB_MTL_OVERWRITE_DISCARD_FILE");
    static bool enabled=baseline;
    static auto next=std::chrono::steady_clock::time_point{};
    const auto now=std::chrono::steady_clock::now();
    if(control && *control && now>=next) {
        next=now+std::chrono::seconds(1);
        if(FILE* f=std::fopen(control,"r")) {
            const int value=std::fgetc(f);std::fclose(f);
            if((value=='0'||value=='1') && enabled!=(value=='1')) {
                enabled=value=='1';
                std::printf("Metal overwrite mode: enabled=%d; future complete CopyImage destinations only\n",int(enabled));
            }
        }
    }
    ++impl->overwrite_candidates;
    impl->overwrite_dirty+=ProxyDirty(image,0);
    if(enabled) impl->overwrite_discarded+=DiscardNativeContents(image,0);
    if(now-impl->overwrite_reported>=std::chrono::seconds(5)) {
        std::printf("Metal overwrite discard: candidates=%llu, dirty=%llu, skipped-resolves=%llu; cumulative, full nonalias CopyImage only\n",
            (unsigned long long)impl->overwrite_candidates,(unsigned long long)impl->overwrite_dirty,(unsigned long long)impl->overwrite_discarded);
        impl->overwrite_reported=now;
    }
}

namespace {
bool UpscaleAlphaEnabled() {
    static const bool baseline = [] {
        const char* v = std::getenv("BB_MTL_UPSCALE_ALPHA"); return !v || v[0] != '0';
    }();
    static const char* control = std::getenv("BB_MTL_UPSCALE_ALPHA_FILE");
    if (!control || !*control) return baseline;
    static bool enabled = baseline;
    static auto next = std::chrono::steady_clock::time_point{};
    const auto now = std::chrono::steady_clock::now();
    if (now >= next) {
        next = now + std::chrono::seconds(1);
        if (FILE* f = std::fopen(control, "r")) {
            const int v = std::fgetc(f); std::fclose(f);
            if ((v == '0' || v == '1') && enabled != (v == '1')) {
                enabled = v == '1';
                std::printf("Metal upscale alpha mode: enabled=%d; no resource recreation\n", int(enabled));
            }
        }
    }
    return enabled;
}
}

u64 SceneTargets::UpscaleAlphaCount() const { return impl->alpha_fused; }

bool SceneTargets::MergeUpscaleAlpha(VideoCore::Image& image, const void* rgb_ptr,
                                     const void* alpha_ptr) {
    if (!UpscaleAlphaEnabled() || !Reduced() || !EligibleScene(image) ||
        image.info.pixel_format != vk::Format::eR16G16B16A16Sfloat ||
        image.info.resources.levels != 1 || image.info.resources.layers != 1 ||
        !ProxyDirty(image, 0)) return false;
    const auto it = entries.find(Key(image.image_uid, 0));
    if (it == entries.end()) return false;
    const auto rgb = (__bridge id<MTLTexture>)rgb_ptr;
    const auto alpha = (__bridge id<MTLTexture>)alpha_ptr;
    const auto native = NativeTexture(image);
    if (!rgb || !alpha || !native || alpha != TextureOf(Of(*it->second).image)) return false;
    const auto plain = [](id<MTLTexture> t) {
        return t.textureType == MTLTextureType2D && t.sampleCount == 1 &&
               t.mipmapLevelCount == 1 && t.pixelFormat == MTLPixelFormatRGBA16Float;
    };
    auto root = [](id<MTLTexture> t) { while (t.parentTexture) t = t.parentTexture; return t; };
    const auto aliases = [&](id<MTLTexture> a, id<MTLTexture> b) {
        return root(a) == root(b) || (a.buffer && a.buffer == b.buffer) ||
               (a.iosurface && a.iosurface == b.iosurface) || a.isAliasable || b.isAliasable;
    };
    if (!plain(rgb) || !plain(alpha) || !plain(native) ||
        rgb.width != native.width || rgb.height != native.height ||
        alpha.width != size.width || alpha.height != size.height ||
        !(rgb.usage & MTLTextureUsageShaderRead) || !(alpha.usage & MTLTextureUsageShaderRead) ||
        !(native.usage & MTLTextureUsageShaderWrite) ||
        aliases(native, rgb) || aliases(native, alpha) || aliases(rgb, alpha)) return false;
    const auto pipeline = impl->AlphaPipeline();
    if (!pipeline) return false;
    // The complete RGBA overwrite below reads alpha from the existing dirty proxy. Discarding
    // native contents only changes coherence; it does not free or modify that proxy's storage.
    // Validate everything before this point: a rejected call leaves the original path intact.
    DiscardNativeContents(image, 0);
    runtime.Transit(&image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
                    vk::AccessFlagBits2::eShaderWrite);
    runtime.FlushBarriers();
    scheduler.ComputeEncoder(nullptr, true);
    scheduler.LabelEncoder("Upscale merge alpha fusion");
    const auto compute = scheduler.GetImpl().compute;
    const std::array<float, 2> scale{float(alpha.width) / native.width,
                                      float(alpha.height) / native.height};
    [compute setComputePipelineState:pipeline];
    [compute setTexture:rgb atIndex:0];
    [compute setTexture:alpha atIndex:1];
    [compute setTexture:native atIndex:2];
    [compute setBytes:scale.data() length:sizeof(scale) atIndex:0];
    [compute dispatchThreadgroups:MTLSizeMake((native.width + 7) / 8, (native.height + 7) / 8, 1)
            threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    if (ImageUsageLog::Enabled()) ImageUsageLog::Bound((__bridge const void*)native, ImageUsageLog::Use::UpscalerWrite);
    ++impl->alpha_fused;
    const auto now = std::chrono::steady_clock::now();
    if (now - impl->alpha_reported >= std::chrono::seconds(5)) {
        std::printf("Metal upscale alpha: fused=%llu; existing proxy alpha, no native resolve, no extra texture\n", (unsigned long long)impl->alpha_fused);
        impl->alpha_reported = now;
    }
    return true;
}

u64 SceneTargets::ProxyClearCount() const { return impl->proxy_clears; }

bool SceneTargets::ClearProxy(VideoCore::Image& image, const VideoCore::SubresourceRange& range,
                              const vk::ClearValue& value) {
    if(!Reduced() || !Eligible(image) || image.info.props.is_depth ||
       image.info.resources.levels!=1 || image.info.resources.layers!=1 ||
       range.base.level!=0 || range.base.layer!=0 || range.extent.levels!=1 || range.extent.layers!=1 ||
       image.info.pixel_format!=image.backing->image.image_ci.format) return false;
    const auto it=entries.find(Key(image.image_uid,0));
    if(it==entries.end()) return false; // no new allocation/cache entry just for a clear
    auto& entry=*it->second;
    const auto proxy=TextureOf(Of(entry).image), native=NativeTexture(image);
    const auto cls=ClassOf(proxy.pixelFormat);
    if(IsDepth(cls) || cls==Class::None || proxy.pixelFormat!=native.pixelFormat ||
       native.textureType!=MTLTextureType2D || native.sampleCount!=1 ||
       !(native.usage & MTLTextureUsageRenderTarget)) return false;
    static const bool baseline=[] {const char* v=std::getenv("BB_MTL_PROXY_CLEAR");return !v||v[0]!='0';}();
    static const char* control=std::getenv("BB_MTL_PROXY_CLEAR_FILE");
    static bool enabled=baseline;
    static auto next=std::chrono::steady_clock::time_point{};
    const auto now=std::chrono::steady_clock::now();
    if(control && *control && now>=next) {
        next=now+std::chrono::seconds(1);
        if(FILE* file=std::fopen(control,"r")) {
            const int v=std::fgetc(file);std::fclose(file);
            if((v=='0'||v=='1') && enabled!=(v=='1')) {
                enabled=v=='1';std::printf("Metal proxy clear mode: enabled=%d; existing full color proxy only\n",int(enabled));
            }
        }
    }
    if(!enabled) return false;
    MTLClearColor color;
    if(cls==Class::Uint) color=MTLClearColorMake(value.color.uint32[0],value.color.uint32[1],value.color.uint32[2],value.color.uint32[3]);
    else if(cls==Class::Sint) color=MTLClearColorMake(value.color.int32[0],value.color.int32[1],value.color.int32[2],value.color.int32[3]);
    else color=MTLClearColorMake(value.color.float32[0],value.color.float32[1],value.color.float32[2],value.color.float32[3]);
    // The clear is constant over the entire logical surface, independent of resolution.
    // Do not touch native contents: NativeAccess will materialize the same constant on demand.
    runtime.FlushBarriers();
    if(!scheduler.QueueColorClear((__bridge void*)proxy,{color.red,color.green,color.blue,color.alpha})) {
    auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=proxy;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=color;
    scheduler.HostRenderEncoder((__bridge const void*)pass);
    scheduler.LabelEncoder("Scene proxy clear");scheduler.EndRendering();
    }
    entry.state.ProxyWrite();++impl->proxy_clears;
    if(now-impl->proxy_clear_reported>=std::chrono::seconds(5)) {
        std::printf("Metal proxy clear: direct=%llu; no native clear/resolve/refill, existing color proxy\n",(unsigned long long)impl->proxy_clears);
        impl->proxy_clear_reported=now;
    }
    return true;
}

SceneTargets::Target SceneTargets::Attachment(VideoCore::ImageId id,
                                              const VideoCore::ImageViewInfo& info, bool clear_color) {
    auto& original = Original(id);
    vk::ImageLayout layout;
    const auto before = SkippedClearFills();
    auto& entry = BeginAttachment(id, info, layout, clear_color && ColorClearDiscardEnabled());
    static auto reported = std::chrono::steady_clock::time_point{};
    if (SkippedClearFills()!=before) {
      const auto now = std::chrono::steady_clock::now();
      if (now-reported>=std::chrono::seconds(5)) {
        std::printf("Metal scene clear: skipped-fills=%llu; full color clears only, depth/stencil preserved\n", (unsigned long long)SkippedClearFills());
        reported=now;
      }
    }
    return {View(entry, original, info, true), layout};
}

std::optional<SceneTargets::Target> SceneTargets::SampleProxy(
    const VideoCore::Image& image, const VideoCore::ImageViewInfo& info) {
    vk::ImageLayout layout;
    Entry* entry = BeginSample(image, info, layout);
    if (!entry) {
        return std::nullopt;
    }
    return Target{View(*entry, image, info, false), layout};
}

SceneTargets::Target SceneTargets::Read(VideoCore::ImageId id,
                                        const VideoCore::ImageViewInfo& info) {
    auto& original = Original(id);
    const auto before=std::exchange(impl->copy_reason,"upscaler-input");
    auto& entry = Get(id);
    impl->copy_reason=before;
    return {View(entry, original, info, false), vk::ImageLayout::eGeneral};
}

} // namespace Metal
