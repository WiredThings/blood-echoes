#include "video_core/renderer_metal/mtl_local_library.h"
#include "video_core/renderer_metal/mtl_phase_counters.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// Counterpart: renderer_vulkan/vk_temporal_upscaler.cpp (the UI composition of a scaled session and
// its temporal background, RunScaled; MetalFX instead of FSR 3, no FSR 4, TAA or DLSS: D10).

#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_memory_stats.h"
#import <MetalFX/MetalFX.h>

#include <atomic>
#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <vector>

#include "video_core/renderer_metal/mtl_velocity_mirror.h"
#include "bbport_settings.h"
#include "bbport_toggles.h"
#include "common/assert.h"
#include "video_core/frame_capture.h"
#include "video_core/host_shaders_msl/upscale_merge_comp_msl.h"
#include "video_core/motion_history.h"
#include "video_core/renderer_metal/mtl_camera_motion.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_image_usage_log.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_upscaler.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Metal {
namespace {

/// What the two passes draw with. One full-screen triangle (vertex 0, 1, 2 cover the clip space
/// with the rows of the texture running top down: clip y = +1 is row 0 of a render target and
/// v = 0 row 0 of a texture, so uv = (x * 0.5 + 0.5, 0.5 - y * 0.5), nothing is flipped), `scale`
/// fits the uv to the part of the source that is read. The color pass samples with the sampler it
/// is given (linear), the depth pass copies the depth aspect (nearest) and writes it as the depth
/// of the fragment.
NSString* const UiSource = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct UiTriOut { float4 position [[position]]; float2 uv; };\n"
    "vertex UiTriOut ui_tri_main(uint vertex_id [[vertex_id]],\n"
    "                            constant float2& scale [[buffer(0)]]) {\n"
    "    float2 p = float2(float((vertex_id << 1) & 2), float(vertex_id & 2));\n"
    "    UiTriOut out;\n"
    "    out.position = float4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "    out.uv = float2(p.x, 1.0 - p.y) * scale;\n"
    "    return out;\n"
    "}\n"
    "fragment float4 ui_copy_main(UiTriOut in [[stage_in]],\n"
    "                             texture2d<float> source [[texture(0)]],\n"
    "                             sampler source_sampler [[sampler(0)]]) {\n"
    "    return source.sample(source_sampler, in.uv, level(0));\n"
    "}\n"
    "struct UiDepthOut { float depth [[depth(any)]]; };\n"
    "fragment UiDepthOut ui_depth_copy_main(UiTriOut in [[stage_in]],\n"
    "                                       depth2d<float> source [[texture(0)]],\n"
    "                                       sampler source_sampler [[sampler(0)]]) {\n"
    "    UiDepthOut out;\n"
    "    out.depth = source.sample(source_sampler, in.uv, level(0));\n"
    "    return out;\n"
    "}\n";

VideoCore::NativeView NativeViewOf(id<MTLTexture> texture) {
    return reinterpret_cast<VideoCore::NativeView>((__bridge void*)texture);
}

std::string ErrorText(NSError* error) {
    return error ? error.localizedDescription.UTF8String : "unknown";
}

id<MTLTexture> MakeTexture(id<MTLDevice> device, vk::Format format, u32 width, u32 height,
                           MTLTextureUsage usage, NSString* label) {
    const u32 pixel_format = VkToMtl::PixelFormat(format);
    ASSERT_MSG(pixel_format != 0, "No Metal texture format for {}", vk::to_string(format));
    MTLTextureDescriptor* descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormat(pixel_format)
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = usage;
    descriptor.storageMode = MTLStorageModePrivate;
    id<MTLTexture> texture = [device newTextureWithDescriptor:descriptor];
    ASSERT_MSG(texture, "Failed allocating the {}x{} {} texture", width, height, label.UTF8String);
    MemoryStats::Track((__bridge const void*)texture, MemoryStats::Kind::Upscaler);
    texture.label = label;
    return texture;
}

/// A texture of the UI composition with the views made of it (what UiColorView, DisplayTarget
/// hand out), by view format and swizzle.
struct Target {
    struct Mirror {
        vk::Format format;
        vk::ComponentMapping mapping;
        id<MTLTexture> texture;
    };
    id<MTLTexture> texture = nil;
    std::vector<Mirror> views;

    /// A view of the texture in `format` (the game's view format) with the swizzle of `mapping`;
    /// the texture itself when that is the same thing. Pixel formats the texture cannot be viewed
    /// as (none for the sRGB twins and the 4 byte formats of the display buffers) keep its own.
    id<MTLTexture> View(vk::Format format, const vk::ComponentMapping& mapping) {
        for (const Mirror& entry : views) {
            if (entry.format == format && entry.mapping == mapping) {
                return entry.texture;
            }
        }
        const u32 view_format = VkToMtl::PixelFormat(format);
        const MTLPixelFormat pixel_format =
            view_format ? MTLPixelFormat(view_format) : texture.pixelFormat;
        const VkToMtl::TextureSwizzle swizzle = VkToMtl::ComposeSwizzle(mapping, format);
        id<MTLTexture> view = texture;
        if (pixel_format != texture.pixelFormat || swizzle != VkToMtl::IdentitySwizzle) {
            view = [texture newTextureViewWithPixelFormat:pixel_format
                                              textureType:MTLTextureType2D
                                                   levels:NSMakeRange(0, 1)
                                                   slices:NSMakeRange(0, 1)
                                                  swizzle:MTLTextureSwizzleChannelsMake(
                                                              MTLTextureSwizzle(swizzle.r),
                                                              MTLTextureSwizzle(swizzle.g),
                                                              MTLTextureSwizzle(swizzle.b),
                                                              MTLTextureSwizzle(swizzle.a))];
            ASSERT_MSG(view, "Failed to create a view (format {}) of a UI texture",
                       vk::to_string(format));
        }
        views.push_back({format, mapping, view});
        return view;
    }
};

/// A MTLFXTemporalScaler with what its descriptor was made for.
struct Scaler {
    id<MTLFXTemporalScaler> scaler = nil;
    MTLPixelFormat color = MTLPixelFormatInvalid;
    MTLPixelFormat depth = MTLPixelFormatInvalid;
    MTLPixelFormat output = MTLPixelFormatInvalid;
    u32 input_width = 0, input_height = 0, output_width = 0, output_height = 0;

    /// The scaler takes this frame: the formats, an input of `w` x `h` and the output size.
    bool Fits(MTLPixelFormat color_format, MTLPixelFormat depth_format,
              MTLPixelFormat output_format, u32 w, u32 h, u32 ow, u32 oh) const {
        return scaler && color == color_format && depth == depth_format &&
               output == output_format && w <= input_width && h <= input_height &&
               ow == output_width && oh == output_height;
    }
};

/// What a live scaler is made for: exactly these formats and sizes (the scene fills the whole
/// input, and the output is the game's scene colour size).
struct LiveKey {
    MTLPixelFormat color = MTLPixelFormatInvalid;
    MTLPixelFormat depth = MTLPixelFormatInvalid;
    MTLPixelFormat output = MTLPixelFormatInvalid;
    u32 w = 0, h = 0, ow = 0, oh = 0;

    bool operator==(const LiveKey&) const = default;
};

/// Makes a scaler (the motion texture is RG16Float: previous minus current in render pixels) and
/// prints how long that took; null `scaler` on failure. Any thread; the first creation in a
/// process takes seconds.
Scaler CreateScaler(id<MTLDevice> device, MTLPixelFormat color, MTLPixelFormat depth,
                    MTLPixelFormat output, u32 w, u32 h, u32 ow, u32 oh) {
    const auto start = std::chrono::steady_clock::now();
    Scaler result;
    @autoreleasepool {
        MTLFXTemporalScalerDescriptor* descriptor = [MTLFXTemporalScalerDescriptor new];
        descriptor.colorTextureFormat = color;
        descriptor.depthTextureFormat = depth;
        descriptor.motionTextureFormat = MTLPixelFormatRG16Float;
        descriptor.outputTextureFormat = output;
        descriptor.inputWidth = w;
        descriptor.inputHeight = h;
        descriptor.outputWidth = ow;
        descriptor.outputHeight = oh;
        result.scaler = [descriptor newTemporalScalerWithDevice:device];
    }
    const double ms =
        std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    if (result.scaler) {
        result.color = color;
        result.depth = depth;
        result.output = output;
        result.input_width = w;
        result.input_height = h;
        result.output_width = ow;
        result.output_height = oh;
        std::printf("Upscaler: MetalFX temporal scaler %ux%u -> %ux%u ready (%.0f ms)\n", w, h, ow,
                    oh, ms);
    } else {
        std::printf("Upscaler: MetalFX temporal scaler %ux%u -> %ux%u failed (%.0f ms)\n", w, h,
                    ow, oh, ms);
    }
    return result;
}

/// `name` as "sx,sy" (the signs of MetalFX's jitter and motion vector scale), 1,1 by default.
std::array<float, 2> ReadSigns(const char* name) {
    std::array<float, 2> signs{1.0f, 1.0f};
    if (const char* env = std::getenv(name)) {
        float x = 1.0f, y = 1.0f;
        if (std::sscanf(env, "%f,%f", &x, &y) == 2) {
            signs = {x, y};
        }
    }
    return signs;
}

} // namespace

struct Upscaler::Impl {
    Device::Impl& dev;
    id<MTLLibrary> library;
    id<MTLSamplerState> linear;  ///< clamped, for the background
    id<MTLSamplerState> nearest; ///< clamped, for the depth
    id<MTLDepthStencilState> depth_always; ///< the depth copy writes every fragment's depth
    /// By the pixel format of the pass's attachment, made on first use (the recording thread).
    std::unordered_map<u32, id<MTLRenderPipelineState>> copy_pipelines;
    std::unordered_map<u32, id<MTLRenderPipelineState>> depth_pipelines;

    u32 width = 0, height = 0; ///< the UI textures' size
    Target color, depth;
    struct Display {
        Target target;
        vk::Format format{};
        u32 width = 0, height = 0;
    };
    /// Guarded by the compositor's display lock (DisplayTarget, DisplayOverride).
    std::unordered_map<VAddr, Display> displays;

    // MetalFX. `created` is the creation thread's result: it writes it (and `creation_done`)
    // under `scaler_mutex`, the recording thread takes it from there (TakeCreated). `current` and
    // `creation_pending` belong to the recording thread; `creator` is joined by the destructor.
    std::thread creator;
    std::mutex scaler_mutex;
    Scaler created;
    bool creation_done = false;
    bool creation_pending = false; ///< the startup creation has not been taken yet
    Scaler current;
    id<MTLTexture> motion = nil; ///< RG16Float, the size of the game's targets
    u32 motion_width = 0, motion_height = 0;
    // The live path (P7.5). The scalers (most recently used first) and the pending creation
    // belong to the recording thread; the creation thread writes `live_created` and then sets
    // `live_done`, the recording thread takes the result once it sees that (TakeLive).
    static constexpr std::size_t kLiveScalers = 3;
    std::vector<std::pair<LiveKey, Scaler>> live_scalers;
    std::thread live_creator;
    std::atomic<bool> live_done{false};
    bool live_pending = false;
    LiveKey live_key; ///< what the pending creation makes
    Scaler live_created;
    LiveKey last_live; ///< the last configuration RunLive saw (PrepareLive)
    bool last_live_valid = false;
    id<MTLTexture> live_output = nil; ///< RGBA16Float, the scene colour's size: the scaler's output
    u32 live_output_width = 0, live_output_height = 0;
    id<MTLComputePipelineState> merge = nil; ///< upscale_merge.comp
    std::array<u32, 5> merge_slots{};        ///< its texture slots by binding
    bool merge_failed = false;
    std::array<float, 2> jitter_sign{1.0f, 1.0f}, motion_sign{1.0f, 1.0f};
    bool log_scaler = false;
    u32 log_frames = 0;
    bool usage_reported = false;

    explicit Impl(Device::Impl& dev_) : dev{dev_} {
        NSError* error = nil;
        MTLCompileOptions* options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_1;
        library = Metal::LocalLibrary::Build(dev.device, UiSource, options, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"ui_tri_main", @"ui_copy_main", @"ui_depth_copy_main"}); });
        ASSERT_MSG(library, "UI composition shaders did not compile: {}", ErrorText(error));
        const auto sampler = [&](MTLSamplerMinMagFilter filter) {
            MTLSamplerDescriptor* descriptor = [MTLSamplerDescriptor new];
            descriptor.minFilter = filter;
            descriptor.magFilter = filter;
            descriptor.sAddressMode = MTLSamplerAddressModeClampToEdge;
            descriptor.tAddressMode = MTLSamplerAddressModeClampToEdge;
            return [dev.device newSamplerStateWithDescriptor:descriptor];
        };
        linear = sampler(MTLSamplerMinMagFilterLinear);
        nearest = sampler(MTLSamplerMinMagFilterNearest);
        MTLDepthStencilDescriptor* descriptor = [MTLDepthStencilDescriptor new];
        descriptor.depthCompareFunction = MTLCompareFunctionAlways;
        descriptor.depthWriteEnabled = YES;
        depth_always = [dev.device newDepthStencilStateWithDescriptor:descriptor];
    }

    /// Takes the startup scaler once the creation thread is done; true when that thread failed.
    bool TakeCreated() {
        if (!creation_pending) {
            return false;
        }
        std::lock_guard lock{scaler_mutex};
        if (!creation_done) {
            return false;
        }
        creation_pending = false;
        current = std::move(created);
        created = {};
        return !current.scaler;
    }

    /// The live scaler made for `key`, moved to the front; null when there is none.
    Scaler* FindLive(const LiveKey& key) {
        for (std::size_t i = 0; i < live_scalers.size(); ++i) {
            if (live_scalers[i].first == key) {
                if (i != 0) {
                    std::rotate(live_scalers.begin(), live_scalers.begin() + i,
                                live_scalers.begin() + i + 1);
                }
                return &live_scalers.front().second;
            }
        }
        return nullptr;
    }

    enum class Taken { Nothing, Ready, Failed };
    /// Takes the pending creation's result once the thread is done (the scheduler releases the
    /// scaler that makes room).
    Taken TakeLive(Scheduler& scheduler);
    /// Starts the creation of the scaler of `key` on a thread (none is pending).
    void StartLive(const LiveKey& key) {
        live_key = key;
        live_done.store(false, std::memory_order_relaxed);
        live_pending = true;
        live_creator = std::thread([this, key] {
            Scaler scaler = CreateScaler(dev.device, key.color, key.depth, key.output, key.w, key.h,
                                         key.ow, key.oh);
            live_created = std::move(scaler);
            live_done.store(true, std::memory_order_release);
        });
    }

    /// The merge pass (upscale_merge.comp, D11); null when it did not compile.
    id<MTLComputePipelineState> MergePipeline(Runtime& runtime) {
        if (merge || merge_failed) {
            return merge;
        }
        const HostShaders::Shader& shader = HostShaders::UPSCALE_MERGE_COMP;
        for (const HostShaders::Binding& binding : shader.bindings) {
            if ((binding.kind == HostShaders::Binding::Kind::Texture ||
                 binding.kind == HostShaders::Binding::Kind::StorageTexture) &&
                binding.set == 0 && binding.binding < merge_slots.size()) {
                merge_slots[binding.binding] = binding.msl_index;
            }
        }
        NSError* error = nil;
        merge = runtime.GetImpl().kernels.UpscaleMerge(&error);
        if (!merge) {
            merge_failed = true;
            std::printf("Upscaler: the merge pass did not compile (%s); the live path is off\n",
                        ErrorText(error).c_str());
        }
        return merge;
    }

    /// fs_tri + a sample into a color target of `format`.
    id<MTLRenderPipelineState> CopyPipeline(MTLPixelFormat format) {
        auto& pipeline = copy_pipelines[u32(format)];
        if (!pipeline) {
            MTLRenderPipelineDescriptor* descriptor = [MTLRenderPipelineDescriptor new];
            descriptor.label = @"UI background";
            descriptor.vertexFunction = [library newFunctionWithName:@"ui_tri_main"];
            descriptor.fragmentFunction = [library newFunctionWithName:@"ui_copy_main"];
            descriptor.colorAttachments[0].pixelFormat = format;
            NSError* error = nil;
            BbMetalPhase::Call bb_metal_call_377{BbMetalPhase::Kind::Render}; pipeline = [dev.device newRenderPipelineStateWithDescriptor:descriptor error:&error]; bb_metal_call_377.Finish(bool(pipeline));
            ASSERT_MSG(pipeline, "UI background pipeline failed: {}", ErrorText(error));
        }
        return pipeline;
    }

    /// fs_tri + a depth copy into a depth (stencil) target of `format`, no color.
    id<MTLRenderPipelineState> DepthPipeline(MTLPixelFormat format) {
        auto& pipeline = depth_pipelines[u32(format)];
        if (!pipeline) {
            MTLRenderPipelineDescriptor* descriptor = [MTLRenderPipelineDescriptor new];
            descriptor.label = @"UI depth";
            descriptor.vertexFunction = [library newFunctionWithName:@"ui_tri_main"];
            descriptor.fragmentFunction = [library newFunctionWithName:@"ui_depth_copy_main"];
            descriptor.depthAttachmentPixelFormat = format;
            if (VkToMtl::HasStencilPixelFormat(u32(format))) {
                descriptor.stencilAttachmentPixelFormat = format;
            }
            NSError* error = nil;
            BbMetalPhase::Call bb_metal_call_396{BbMetalPhase::Kind::Render}; pipeline = [dev.device newRenderPipelineStateWithDescriptor:descriptor error:&error]; bb_metal_call_396.Finish(bool(pipeline));
            ASSERT_MSG(pipeline, "UI depth pipeline failed: {}", ErrorText(error));
        }
        return pipeline;
    }
};

namespace {

/// Releases a texture and its views once the GPU is done with the open tick: command buffers do
/// not retain what they use.
void Retire(Scheduler& scheduler, Target& target) {
    if (!target.texture) {
        return;
    }
    scheduler.DeferOperation([texture = std::move(target.texture), views = std::move(target.views)] {
        (void)texture;
        (void)views;
    });
    target.texture = nil;
    target.views.clear();
}

/// The same for a scaler: it is encoded into a command buffer that does not retain it.
void Retire(Scheduler& scheduler, Scaler& scaler) {
    if (!scaler.scaler) {
        return;
    }
    scheduler.DeferOperation([object = std::move(scaler.scaler)] { (void)object; });
    scaler = {};
}

/// The texture can be sampled by the passes of this file (a plain 2D texture).
bool Sampleable(id<MTLTexture> texture) {
    return texture.textureType == MTLTextureType2D && texture.sampleCount == 1 &&
           (texture.usage & MTLTextureUsageShaderRead);
}

} // namespace

Upscaler::Impl::Taken Upscaler::Impl::TakeLive(Scheduler& scheduler) {
    if (!live_pending || !live_done.load(std::memory_order_acquire)) {
        return Taken::Nothing;
    }
    live_creator.join();
    live_pending = false;
    Scaler scaler = std::move(live_created);
    live_created = {};
    if (!scaler.scaler) {
        return Taken::Failed;
    }
    live_scalers.insert(live_scalers.begin(), {live_key, std::move(scaler)});
    while (live_scalers.size() > kLiveScalers) {
        Retire(scheduler, live_scalers.back().second);
        live_scalers.pop_back();
    }
    return Taken::Ready;
}

Upscaler::Upscaler(Device& device, Scheduler& scheduler_, Runtime& runtime_,
                   VideoCore::TextureCache& texture_cache_, CameraMotion& camera_,
                   SceneTargets& scene_targets_)
    : scheduler{scheduler_}, runtime{runtime_}, texture_cache{texture_cache_}, camera{camera_},
      scene_targets{scene_targets_},
      ui{*this,
         {[this](VideoCore::ImageId id) {
              const auto& image = texture_cache.GetImage(id);
              return VideoCore::UiImageInfo{image.info.pixel_format, image.info.size.width,
                                            image.info.size.height, image.info.guest_address};
          },
          [](VAddr address) { return VideoCore::FrameCapture::IsDisplayBuffer(address); }}},
      impl{std::make_unique<Impl>(device.GetImpl())} {
    // Available unless BB_UPSCALER=none. MetalFX is the only provider besides Off (the settings
    // map the others to it); it needs the device to support the temporal scaler.
    const char* env = std::getenv("BB_UPSCALER");
    enabled = !(env && std::strcmp(env, "none") == 0);
    const bool supported = [MTLFXTemporalScalerDescriptor supportsDevice:impl->dev.device];
    BbSettings::ConfigureMetalUpscalers(supported);
    metalfx_supported = supported;
    if (const char* hash = std::getenv("BB_UPSCALE_BEFORE_CS")) {
        trigger_hash = std::strtoull(hash, nullptr, 16);
    }
    if (!supported || !enabled) {
        return;
    }
    impl->jitter_sign = ReadSigns("BB_METALFX_JITTER");
    impl->motion_sign = ReadSigns("BB_METALFX_MV");
    const char* log = std::getenv("BB_METALFX_LOG");
    impl->log_scaler = log && log[0] == '1';
    std::printf("Upscaler: MetalFX jitter signs %g,%g, motion vector signs %g,%g\n",
                impl->jitter_sign[0], impl->jitter_sign[1], impl->motion_sign[0],
                impl->motion_sign[1]);
    if (!ui.ScaledSession()) {
        // The live path: the scalers are made when the frames need them (PrepareLive, RunLive).
        std::printf("Upscaler: MetalFX available on scene color before compute shader %016llx\n",
                    static_cast<unsigned long long>(trigger_hash));
        return;
    }
    // The first creation takes seconds: on a thread of its own, taken at the frames that follow
    // (TakeCreated). It only touches the Impl, which outlives it (the destructor joins first).
    impl->creation_pending = true;
    Impl* state = impl.get();
    const u32 width = ui.RenderWidth(), height = ui.RenderHeight();
    const u32 out_width = ui.OutputWidth(), out_height = ui.OutputHeight();
    state->creator = std::thread([state, width, height, out_width, out_height] {
        Scaler scaler = CreateScaler(state->dev.device, MTLPixelFormatRGBA8Unorm,
                                     MTLPixelFormatDepth32Float_Stencil8, MTLPixelFormatRGBA8Unorm,
                                     width, height, out_width, out_height);
        std::lock_guard lock{state->scaler_mutex};
        state->created = std::move(scaler);
        state->creation_done = true;
    });
}

Upscaler::~Upscaler() {
    // The creation thread first (this is its only joiner); it touches the Impl.
    if (impl->creator.joinable()) {
        impl->creator.join();
    }
    if (impl->live_creator.joinable()) {
        impl->live_creator.join();
    }
    // The recording thread is stopped (the rasterizer's destructor). What the GPU may still use
    // (the scaler, textures, views, pipelines, samplers) goes with the first tick that completes.
    // A scaler that was never taken was never encoded: it just goes.
    Retire(scheduler, impl->current);
    for (auto& [key, scaler] : impl->live_scalers) {
        Retire(scheduler, scaler);
    }
    Retire(scheduler, impl->live_created);
    if (impl->live_output) {
        scheduler.DeferOperation([texture = std::move(impl->live_output)] { (void)texture; });
        impl->live_output = nil;
    }
    if (impl->motion) {
        scheduler.DeferOperation([texture = std::move(impl->motion)] { (void)texture; });
        impl->motion = nil;
    }
    Retire(scheduler, impl->color);
    Retire(scheduler, impl->depth);
    for (auto& [address, display] : impl->displays) {
        Retire(scheduler, display.target);
    }
    scheduler.DeferOperation([impl = std::move(impl)] { (void)impl; });
}

bool Upscaler::Active() const {
    // Toggle 1 << 24 switches it off at run time (A/B); history restarts after. In a scaled
    // session not before the startup scaler is taken (OnFrameStart): until then the frame is
    // copied, so it must not be jittered either. On the live path (P7.5) the scalers are made on
    // demand and a frame without one is simply not upscaled.
    return enabled && metalfx_supported &&
           BbSettings::Get().upscaler == BbSettings::UpscalerMetalFx &&
           !BbToggle::Disabled(1u << 24) && !failed &&
           (!ui.ScaledSession() || !impl->creation_pending);
}

bool Upscaler::RasterScaling() const {
    return !ui.ScaledSession() && scene_targets.Reduced() && !done_this_frame;
}

std::array<u32, 2> Upscaler::VelocitySize() const {
    if (scene_targets.Reduced()) {
        const auto size = scene_targets.Size();
        return {size.width, size.height};
    }
    return SceneSize(16384, 16384);
}

bool Upscaler::OnFrameStart() {
    if (velocity_mirror) velocity_mirror->FrameStart();
    if (impl->TakeCreated()) {
        failed = true;
    }
    // BB_PRESET_FILE=<file> holding a preset number, read about once a second: switches the
    // preset like the menu does (scripted tests of live preset changes).
    BbSettings::PollPresetFile(preset_file_frames);
    const auto& settings = BbSettings::Get();
    const bool live = !ui.ScaledSession(); // the guest renders at 1920x1080 (the live path)
    const int preset = BbSettings::RenderPreset();
    const int output = settings.output_res.load();
    const int upscaler = settings.upscaler.load();
    bool output_changed = false;
    if (live) {
        if (applied_preset != preset || applied_upscaler != upscaler ||
            upscaler == BbSettings::UpscalerOff) {
            failed = false; // a retry after a menu change
        }
        output_changed = applied_output != output;
        if (output_changed) {
            ui.SetOutputSize(BbSettings::OutputWidths[output], BbSettings::OutputHeights[output]);
            std::printf("Output resolution: %ux%u (live)\n", ui.OutputWidth(), ui.OutputHeight());
            failed = false;
        }
    }
    const bool active = Active();
    const bool jitter_on = active && settings.jitter && !BbToggle::Disabled(1u << 25);
    const bool changed = active != last_active || jitter_on != last_jitter ||
                         applied_upscaler != upscaler ||
                         (live && (output_changed || applied_preset != preset));
    if (live) {
        // The scene targets' size follows the preset (SetSize resolves the dirty proxies, waits
        // for the GPU and drops them); the UI composition and the shaders' pixel math follow.
        scene_targets.SetSize(VideoCore::SceneResolution::ForPreset(
            active ? preset : 0, {ui.OutputWidth(), ui.OutputHeight()}));
        ui.SetRenderSize(scene_targets.Size().width, scene_targets.Size().height);
    }
    if (changed || !dispatched_last_frame) reset = true;
    if (changed) jitter_index = 0;
    applied_preset = preset;
    applied_output = output;
    applied_upscaler = upscaler;
    last_active = active;
    last_jitter = jitter_on;
    dispatched_last_frame = false;
    // The size the guest draws its scene at: the scene targets' on the live path (1920x1080
    // without a reduced scene).
    BbSettings::Get().active_render_width =
        ui.Scaled() ? ui.RenderWidth() : scene_targets.Size().width;
    BbSettings::Get().active_render_height =
        ui.Scaled() ? ui.RenderHeight() : scene_targets.Size().height;
    ui.OnFrameStart();
    done_this_frame = false;
    ldr_target = {};
    scene_color = {};
    if (live && !ui.Scaled()) {
        PrepareLive();
    }
    // Halton(2, 3); the menu or toggle 1 << 25 disables it.
    if (!jitter_on) {
        jitter = {};
        return changed;
    }
    const u32 phases = ui.Scaled()
                           ? VideoCore::Motion::JitterPhases(ui.RenderWidth(), ui.OutputWidth())
                           : VideoCore::Motion::JitterPhases(scene_targets.Size().width, 1920);
    jitter_index = jitter_index % phases + 1;
    jitter = {VideoCore::Motion::Halton(jitter_index, 2) - 0.5f,
              VideoCore::Motion::Halton(jitter_index, 3) - 0.5f};
    return changed; // the camera history restarts
}

float Upscaler::SceneMipBias() const {
    const float render = float(ui.RenderWidth());
    const float output = float(ui.OutputWidth());
    return Active() && render > 0.0f && render < output ? std::log2(render / output) : 0.0f;
}

std::array<u32, 2> Upscaler::SceneSize(u32 w, u32 h) const {
    if (!ui.ScaledSession()) {
        return {ui.RenderWidth(), ui.RenderHeight()}; // the scene targets' size
    }
    // The game's own render size (scene constants); BB_RENDER_RES until the first camera.
    auto size = camera.RenderSize();
    if (size[0] == 0 || size[1] == 0) {
        size = {ui.RenderWidth(), ui.RenderHeight()};
    }
    return {std::min(size[0], w), std::min(size[1], h)};
}

void Upscaler::OnColorTarget(VideoCore::ImageId color) {
    if (ui.UiPhase() || !camera.Depth()) {
        return;
    }
    const auto& image = texture_cache.GetImage(color);
    const auto& depth = texture_cache.GetImage(camera.Depth());
    if (image.info.pixel_format == vk::Format::eR8G8B8A8Unorm &&
        image.info.size.width == depth.info.size.width &&
        image.info.size.height == depth.info.size.height) {
        ldr_target = color;
    }
}

void Upscaler::OnDraw(u64 vs_hash, VideoCore::ImageId color, VideoCore::ImageId depth,
                      bool native_viewport) {
    if (!ui.Scaled() && vs_hash == ui.TriggerVs()) {
        done_this_frame = true;
    }
    const bool copied = ui.OnDraw(vs_hash, color, depth, native_viewport,
                                  camera.Ready() && ldr_target == color, Active(),
                                  [this] { RunScaled(); });
    if (copied) {
        reset = true; // returning to 3D must not reuse history from before a menu/loading screen
        done_this_frame = true;
    }
}

void Upscaler::OnDispatch(u64 cs_hash) {
    if (cs_hash != trigger_hash || done_this_frame || failed || ui.Scaled()) {
        return;
    }
    // The post-processing combine shader: from here on the frame is at its native size (post,
    // tonemap and UI are not jittered, nor drawn reduced), upscaled or not.
    done_this_frame = true;
    if (!scene_color || !camera.Ready() || !Active()) {
        reset = true;
        return;
    }
    RunLive();
}

void Upscaler::PrepareLive() {
    if (!impl->last_live_valid || !Active()) {
        return;
    }
    if (impl->TakeLive(scheduler) == Impl::Taken::Failed) {
        failed = true;
        return;
    }
    LiveKey key = impl->last_live;
    if (scene_targets.Reduced()) {
        key.w = scene_targets.Size().width;
        key.h = scene_targets.Size().height;
    } else {
        key.w = key.ow;
        key.h = key.oh;
    }
    if (!impl->live_pending && !impl->FindLive(key)) {
        impl->StartLive(key);
    }
}

void Upscaler::RunScaled() {
    if (impl->TakeCreated()) {
        failed = true;
    }
    if (failed) {
        return;
    }
    if (impl->creation_pending) {
        reset = true; // the scaler is not ready: the compositor copies the frame
        return;
    }
    auto& color = texture_cache.GetImage(ldr_target);
    auto& depth = texture_cache.GetImage(camera.Depth());
    id<MTLTexture> color_texture = NativeTexture(color);
    id<MTLTexture> depth_texture = NativeTexture(depth);
    // The scene fills the top-left w x h of its targets (iw x ih, aligned by the game).
    u32 iw = color.info.size.width, ih = color.info.size.height;
    const auto [w, h] = SceneSize(iw, ih);
    const u32 ow = ui.OutputWidth(), oh = ui.OutputHeight();
    if (depth.info.size.width != iw || depth.info.size.height != ih || w > ow || h > oh) {
        return;
    }
    // The live path (an output size other than 1920x1080 without BB_RENDER_RES, P7.5): the scene
    // was drawn into the proxies of the scene targets, which are the scaler's inputs.
    const bool live = !ui.ScaledSession();
    if (live) {
        if (!scene_targets.Reduced() || !scene_targets.EligibleScene(color) ||
            !scene_targets.EligibleScene(depth)) {
            return;
        }
        VideoCore::ImageViewInfo color_info, depth_info;
        color_info.format = color.info.pixel_format;
        depth_info.format = depth.info.pixel_format;
        color_texture = (__bridge id<MTLTexture>)reinterpret_cast<void*>(
            scene_targets.Read(ldr_target, color_info).view);
        depth_texture = (__bridge id<MTLTexture>)reinterpret_cast<void*>(
            scene_targets.Read(camera.Depth(), depth_info).view);
        iw = w;
        ih = h;
    }
    const auto plain = [](id<MTLTexture> texture) {
        return texture.textureType == MTLTextureType2D && texture.sampleCount == 1;
    };
    const u32 output_pixel_format = VkToMtl::PixelFormat(color.info.pixel_format);
    if (!color_texture || !depth_texture || !plain(color_texture) || !plain(depth_texture) ||
        !output_pixel_format) {
        return;
    }
    const MTLPixelFormat color_format = color_texture.pixelFormat;
    const MTLPixelFormat depth_format = depth_texture.pixelFormat;
    const MTLPixelFormat output_format = MTLPixelFormat(output_pixel_format);
    Scaler* live_scaler = nullptr;
    if (live) {
        // One scaler per configuration, made on a thread when the preset or the output size
        // changes: until it is ready the compositor copies the frame (no stall).
        const LiveKey key{color_format, depth_format, output_format, w, h, ow, oh};
        if (impl->TakeLive(scheduler) == Impl::Taken::Failed) {
            failed = true;
            return;
        }
        live_scaler = impl->FindLive(key);
        if (!live_scaler) {
            reset = true;
            if (!impl->live_pending) {
                impl->StartLive(key);
            }
            return;
        }
    } else if (!impl->current.Fits(color_format, depth_format, output_format, w, h, ow, oh)) {
        // Another format or size than the startup scaler's (or its creation failed): on this
        // thread, with the frame's own.
        Retire(scheduler, impl->current);
        impl->current = CreateScaler(impl->dev.device, color_format, depth_format, output_format,
                                     w, h, ow, oh);
        if (!impl->current.scaler) {
            failed = true;
            return;
        }
        reset = true;
    }
    id<MTLFXTemporalScaler> scaler = live_scaler ? live_scaler->scaler : impl->current.scaler;
    const auto has_usage = [](id<MTLTexture> texture, MTLTextureUsage usage) {
        return (texture.usage & usage) == usage;
    };
    const auto missing = [&](const char* what, id<MTLTexture> texture, MTLTextureUsage usage) {
        if (has_usage(texture, usage)) {
            return false;
        }
        if (!std::exchange(impl->usage_reported, true)) {
            std::printf("Upscaler: the %s texture lacks the usage MetalFX needs (has %llu, needs "
                        "%llu); the frame is copied\n",
                        what, static_cast<unsigned long long>(texture.usage),
                        static_cast<unsigned long long>(usage));
        }
        failed = true;
        return true;
    };
    if (missing("color", color_texture, scaler.colorTextureUsage) ||
        missing("depth", depth_texture, scaler.depthTextureUsage)) {
        return;
    }
    // The motion vectors, of the size of the game's targets (the scene is their top-left w x h).
    if (!impl->motion || impl->motion_width != iw || impl->motion_height != ih) {
        if (impl->motion) {
            scheduler.DeferOperation([texture = std::move(impl->motion)] { (void)texture; });
            impl->motion = nil;
        }
        impl->motion = MakeTexture(impl->dev.device, vk::Format::eR16G16Sfloat, iw, ih,
                                   MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite,
                                   @"MetalFX motion");
        impl->motion_width = iw;
        impl->motion_height = ih;
    }
    if (missing("motion", impl->motion, scaler.motionTextureUsage)) {
        return;
    }
    ui.EnsureImages(ow, oh, color.info.pixel_format, depth.info.pixel_format);
    if (missing("output", impl->color.texture, scaler.outputTextureUsage)) {
        return;
    }
    ui.PrepareDepth(camera.Depth());

    // The frame and its depth are read by compute: the passes that wrote them end first.
    scheduler.EndRendering();
    if (!live) {
        // (The live path reads proxies: the native images are resolved when something reads them.)
        runtime.Transit(&color, vk::ImageLayout::eGeneral,
                        vk::PipelineStageFlagBits2::eComputeShader,
                        vk::AccessFlagBits2::eShaderRead);
        runtime.Transit(&depth, vk::ImageLayout::eGeneral,
                        vk::PipelineStageFlagBits2::eComputeShader,
                        vk::AccessFlagBits2::eShaderRead);
        runtime.FlushBarriers();
    }
    // The motion pass on a compute encoder of its own: MetalFX encodes its own work into the
    // command buffer, so no encoder may be open when it does.
    scheduler.EndEncoder();
    camera.RecordMotion((__bridge const void*)depth_texture, (__bridge const void*)impl->motion, w,
                        h);
    if (velocity_mirror) velocity_mirror->Merge((__bridge const void*)impl->motion, w, h);
    scheduler.EndEncoder();

    @autoreleasepool {
        scaler.colorTexture = color_texture;
        scaler.depthTexture = depth_texture;
        scaler.motionTexture = impl->motion;
        scaler.outputTexture = impl->color.texture;
        scaler.inputContentWidth = w;
        scaler.inputContentHeight = h;
        // Experiment bits of BB_TOGGLE_FILE (second number) flip the signs while the game
        // runs: 1/2 jitter x/y, 4/8 motion vector x/y, 16/32 no jitter x/y, 64 no motion vectors
        // (A/B of the conventions on one view).
        const auto flip = [](u64 bit) { return BbToggle::Experiment(bit) ? -1.0f : 1.0f; };
        const auto keep = [](u64 bit) { return BbToggle::Experiment(bit) ? 0.0f : 1.0f; };
        scaler.jitterOffsetX = jitter[0] * impl->jitter_sign[0] * flip(1) * keep(16);
        scaler.jitterOffsetY = jitter[1] * impl->jitter_sign[1] * flip(2) * keep(32);
        scaler.motionVectorScaleX = impl->motion_sign[0] * flip(4) * keep(64);
        scaler.motionVectorScaleY = impl->motion_sign[1] * flip(8) * keep(64);
        scaler.reset = reset;
        scaler.depthReversed = NO;
        scaler.preExposure = 1.0f;
        auto cb = scheduler.GetImpl().EnsureCommandBuffer();
        const auto original_fence = scaler.fence;
        scheduler.GetImpl().profile->Opaque(cb, true, original_fence);
        if (auto fence = scheduler.GetImpl().profile->OpaqueFence()) scaler.fence = fence;
        [scaler encodeToCommandBuffer:cb];
        scaler.fence = original_fence;
        scheduler.GetImpl().profile->Opaque(cb, false);
    }
    // What the frame interpolator takes from this frame (the presenter's PrepareFrame, which
    // follows this frame's last draw): the scaler's depth, motion and jitter, and the camera. The
    // far plane is the one the scene constants carry (CameraTracker::OnConstants checks 3000).
    interpolation = {
        .depth = (__bridge const void*)depth_texture,
        .motion = (__bridge const void*)impl->motion,
        .input_width = iw,
        .input_height = ih,
        .content_width = w,
        .content_height = h,
        .jitter = {scaler.jitterOffsetX, scaler.jitterOffsetY},
        .motion_scale = {scaler.motionVectorScaleX, scaler.motionVectorScaleY},
        .vertical_fov = camera.VerticalFov() * (180.0f / 3.14159265f),
        .near_plane = camera.Near(),
        .far_plane = 3000.0f,
        .reset = reset,
    };
    interpolation_valid = true;
    if (impl->log_scaler && impl->log_frames++ % 300 == 0) {
        std::printf("Upscaler: MetalFX %ux%u jitter %.3f,%.3f reset %d\n", w, h, jitter[0],
                    jitter[1], int(reset));
    }
    reset = false;
    dispatched_last_frame = true;
    done_this_frame = true; // the UI is not jittered
    ui.Begin(ldr_target, camera.Depth());
}

void Upscaler::RunLive() {
    auto& color = texture_cache.GetImage(scene_color);
    auto& depth = texture_cache.GetImage(camera.Depth());
    // The upscaler's output is the game's scene colour size (1920x1080: not a scaled output).
    const u32 ow = color.info.size.width, oh = color.info.size.height;
    // The inputs are the proxies of the colour and the depth when the scene was drawn reduced,
    // else the native images (Native AA at 1:1).
    const bool reduced = scene_targets.Reduced() && scene_targets.EligibleScene(color) &&
                         scene_targets.EligibleScene(depth);
    const u32 w = reduced ? scene_targets.Size().width : ow;
    const u32 h = reduced ? scene_targets.Size().height : oh;
    if (color.info.pixel_format != vk::Format::eR16G16B16A16Sfloat || depth.info.size.width != ow ||
        depth.info.size.height != oh || w > ow || h > oh) {
        return;
    }
    // The merge writes the scene colour from a shader: an image the texture cache created without
    // ShaderWrite (P10.1) gets it now, before the texture is fetched (a promotion replaces it).
    runtime.PromoteImageUsage(&color, VideoCore::ImageUsagePromotion::Storage);
    id<MTLTexture> native_color = NativeTexture(color);
    id<MTLTexture> native_depth = NativeTexture(depth);
    const auto plain = [](id<MTLTexture> texture) {
        return texture.textureType == MTLTextureType2D && texture.sampleCount == 1;
    };
    // The merge writes the scene colour from a shader.
    if (!native_color || !native_depth || !plain(native_color) || !plain(native_depth) ||
        native_color.pixelFormat != MTLPixelFormatRGBA16Float ||
        !(native_color.usage & MTLTextureUsageShaderWrite)) {
        return;
    }
    const LiveKey key{native_color.pixelFormat, native_depth.pixelFormat, MTLPixelFormatRGBA16Float,
                      w, h, ow, oh};
    impl->last_live = key;
    impl->last_live_valid = true;
    if (impl->TakeLive(scheduler) == Impl::Taken::Failed) {
        failed = true;
        return;
    }
    Scaler* live = impl->FindLive(key);
    if (!live) {
        // No scaler of this configuration yet (a preset change, the first frame): made on a
        // thread; this frame is not upscaled and the proxy is resolved by the nearest copy.
        reset = true;
        if (!impl->live_pending) {
            impl->StartLive(key);
        }
        return;
    }
    id<MTLFXTemporalScaler> scaler = live->scaler;
    const auto missing = [&](const char* what, id<MTLTexture> texture, MTLTextureUsage usage) {
        if ((texture.usage & usage) == usage) {
            return false;
        }
        if (!std::exchange(impl->usage_reported, true)) {
            std::printf("Upscaler: the %s texture lacks the usage MetalFX needs (has %llu, needs "
                        "%llu); the frame is not upscaled\n",
                        what, static_cast<unsigned long long>(texture.usage),
                        static_cast<unsigned long long>(usage));
        }
        failed = true;
        return true;
    };
    id<MTLComputePipelineState> merge = impl->MergePipeline(runtime);
    if (!merge) {
        failed = true;
        return;
    }

    scheduler.EndRendering();
    id<MTLTexture> color_input = native_color;
    id<MTLTexture> depth_input = native_depth;
    if (reduced) {
        // The proxies the scene was drawn into (filled by the nearest copy when one is not
        // valid). The native images are resolved only when something reads them: the colour by
        // the merge below, the depth by whatever the post-processing does with it.
        VideoCore::ImageViewInfo color_info, depth_info;
        color_info.format = color.info.pixel_format;
        depth_info.format = depth.info.pixel_format;
        color_input = (__bridge id<MTLTexture>)reinterpret_cast<void*>(
            scene_targets.Read(scene_color, color_info).view);
        depth_input = (__bridge id<MTLTexture>)reinterpret_cast<void*>(
            scene_targets.Read(camera.Depth(), depth_info).view);
    } else {
        runtime.Transit(&depth, vk::ImageLayout::eGeneral,
                        vk::PipelineStageFlagBits2::eComputeShader,
                        vk::AccessFlagBits2::eShaderRead);
        runtime.Transit(&color, vk::ImageLayout::eGeneral,
                        vk::PipelineStageFlagBits2::eComputeShader,
                        vk::AccessFlagBits2::eShaderRead);
        runtime.FlushBarriers();
    }
    if (missing("color", color_input, scaler.colorTextureUsage) ||
        missing("depth", depth_input, scaler.depthTextureUsage)) {
        return;
    }
    // The motion vectors at the input size.
    if (!impl->motion || impl->motion_width != w || impl->motion_height != h) {
        if (impl->motion) {
            scheduler.DeferOperation([texture = std::move(impl->motion)] { (void)texture; });
            impl->motion = nil;
        }
        impl->motion = MakeTexture(impl->dev.device, vk::Format::eR16G16Sfloat, w, h,
                                   MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite,
                                   @"MetalFX motion");
        impl->motion_width = w;
        impl->motion_height = h;
    }
    // The scaler's output, which the merge reads.
    if (!impl->live_output || impl->live_output_width != ow || impl->live_output_height != oh) {
        if (impl->live_output) {
            scheduler.DeferOperation([texture = std::move(impl->live_output)] { (void)texture; });
            impl->live_output = nil;
        }
        impl->live_output =
            MakeTexture(impl->dev.device, vk::Format::eR16G16B16A16Sfloat, ow, oh,
                        scaler.outputTextureUsage | MTLTextureUsageShaderRead |
                            MTLTextureUsageShaderWrite,
                        @"MetalFX live output");
        impl->live_output_width = ow;
        impl->live_output_height = oh;
    }
    if (missing("motion", impl->motion, scaler.motionTextureUsage) ||
        missing("output", impl->live_output, scaler.outputTextureUsage)) {
        return;
    }
    // The motion pass on a compute encoder of its own: MetalFX encodes its own work into the
    // command buffer, so no encoder may be open when it does.
    scheduler.EndEncoder();
    camera.RecordMotion((__bridge const void*)depth_input, (__bridge const void*)impl->motion, w, h);
    if (velocity_mirror) velocity_mirror->Merge((__bridge const void*)impl->motion, w, h);
    scheduler.EndEncoder();

    @autoreleasepool {
        scaler.colorTexture = color_input;
        scaler.depthTexture = depth_input;
        scaler.motionTexture = impl->motion;
        scaler.outputTexture = impl->live_output;
        scaler.inputContentWidth = w;
        scaler.inputContentHeight = h;
        // The experiment bits of BB_TOGGLE_FILE flip the signs as in RunScaled.
        const auto flip = [](u64 bit) { return BbToggle::Experiment(bit) ? -1.0f : 1.0f; };
        const auto keep = [](u64 bit) { return BbToggle::Experiment(bit) ? 0.0f : 1.0f; };
        scaler.jitterOffsetX = jitter[0] * impl->jitter_sign[0] * flip(1) * keep(16);
        scaler.jitterOffsetY = jitter[1] * impl->jitter_sign[1] * flip(2) * keep(32);
        scaler.motionVectorScaleX = impl->motion_sign[0] * flip(4) * keep(64);
        scaler.motionVectorScaleY = impl->motion_sign[1] * flip(8) * keep(64);
        scaler.reset = reset;
        scaler.depthReversed = NO;
        scaler.preExposure = 1.0f;
        auto cb = scheduler.GetImpl().EnsureCommandBuffer();
        const auto original_fence = scaler.fence;
        scheduler.GetImpl().profile->Opaque(cb, true, original_fence);
        if (auto fence = scheduler.GetImpl().profile->OpaqueFence()) scaler.fence = fence;
        [scaler encodeToCommandBuffer:cb];
        scaler.fence = original_fence;
        scheduler.GetImpl().profile->Opaque(cb, false);
    }
    if (impl->log_scaler && impl->log_frames++ % 300 == 0) {
        std::printf("Upscaler: MetalFX live %ux%u -> %ux%u jitter %.3f,%.3f reset %d\n", w, h, ow,
                    oh, jitter[0], jitter[1], int(reset));
    }
    reset = false;
    dispatched_last_frame = true;

    // Normal output can merge RGB with the existing proxy alpha, avoiding the otherwise
    // redundant native-size resolve. Debug modes, Native AA and unproven resources fall back.
    const u32 mode = BbSettings::Get().debug_view == BbSettings::DebugMotion ? 2 : 0;
    if (mode == 0 && reduced && scene_targets.MergeUpscaleAlpha(color,
            (__bridge const void*)impl->live_output, (__bridge const void*)color_input)) return;

    // The result replaces the scene colour's RGB (its alpha carries data for the post): a native
    // write, which first resolves a dirty proxy of the colour into the native image (the alpha)
    // and then invalidates it.
    runtime.Transit(&color, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
                    vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite);
    runtime.FlushBarriers();
    // Debug view of the motion vectors (menu); the reactive mask has no counterpart here.
    scheduler.ComputeEncoder(nullptr, true);
    scheduler.LabelEncoder("Upscale merge");
    id<MTLComputeCommandEncoder> compute = scheduler.GetImpl().compute;
    const HostShaders::Shader& shader = HostShaders::UPSCALE_MERGE_COMP;
    [compute setComputePipelineState:merge];
    [compute setTexture:impl->live_output atIndex:impl->merge_slots[0]];
    [compute setTexture:native_color atIndex:impl->merge_slots[1]];
    if (ImageUsageLog::Enabled()) {
        ImageUsageLog::Bound((__bridge const void*)native_color, ImageUsageLog::Use::UpscalerWrite);
    }
    [compute setTexture:impl->live_output atIndex:impl->merge_slots[2]]; // (reactive: unused)
    [compute setTexture:impl->motion atIndex:impl->merge_slots[3]];
    [compute setTexture:impl->motion atIndex:impl->merge_slots[4]]; // (objects: no object motion)
    [compute setBytes:&mode length:sizeof(mode) atIndex:0];
    [compute dispatchThreadgroups:MTLSizeMake((ow + shader.local_size[0] - 1) / shader.local_size[0],
                                              (oh + shader.local_size[1] - 1) / shader.local_size[1],
                                              1)
            threadsPerThreadgroup:MTLSizeMake(shader.local_size[0], shader.local_size[1], 1)];
}

bool Upscaler::TakeInterpolationInputs(InterpolationInputs& inputs) {
    if (!interpolation_valid) {
        return false;
    }
    interpolation_valid = false;
    inputs = interpolation;
    return true;
}

// ---- UiCompositionBackend ----

void Upscaler::EnsureUiImages(u32 width, u32 height, vk::Format color, vk::Format depth,
                              bool new_color, bool new_depth) {
    impl->width = width;
    impl->height = height;
    id<MTLDevice> device = impl->dev.device;
    if (new_color) {
        // Pixel format views: the game's views of the UI target differ in format (sRGB).
        Retire(scheduler, impl->color);
        impl->color.texture =
            MakeTexture(device, color, width, height,
                        MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                            MTLTextureUsageShaderWrite | MTLTextureUsagePixelFormatView,
                        @"UI color"); // ShaderWrite: the MetalFX output
    }
    if (new_depth) {
        Retire(scheduler, impl->depth);
        impl->depth.texture = MakeTexture(device, depth, width, height,
                                          MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead,
                                          @"UI depth");
        ui_depth_format = depth;
    }
}

void Upscaler::CopyBackground(VideoCore::ImageId color_id, u32 width, u32 height) {
    auto& image = texture_cache.GetImage(color_id);
    id<MTLTexture> source = NativeTexture(image);
    id<MTLTexture> target = impl->color.texture;
    if (!Sampleable(source)) {
        static bool logged = false;
        if (!std::exchange(logged, true)) {
            std::printf("Metal: the UI background cannot be copied (the frame is not a plain 2D "
                        "texture)\n");
        }
        return;
    }
    // The game's frame is read by a fragment shader: the pass that wrote it ends first, so that
    // the barrier the transition may ask for is not issued inside it.
    scheduler.EndRendering();
    runtime.Transit(&image, vk::ImageLayout::eShaderReadOnlyOptimal,
                    vk::PipelineStageFlagBits2::eFragmentShader, vk::AccessFlagBits2::eShaderRead);
    runtime.FlushBarriers();
    @autoreleasepool {
        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = target;
        pass.colorAttachments[0].loadAction = MTLLoadActionDontCare; // the triangle covers it
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        scheduler.HostRenderEncoder((__bridge const void*)pass);
        scheduler.LabelEncoder("UI background copy");
        id<MTLRenderCommandEncoder> render = scheduler.GetImpl().render;
        [render setRenderPipelineState:impl->CopyPipeline(target.pixelFormat)];
        [render setViewport:MTLViewport{0, 0, double(impl->width), double(impl->height), 0, 1}];
        // The top-left width x height of the frame fills the whole UI texture (Vulkan: the
        // linear blit of that region).
        const float scale[2] = {float(width) / float(source.width),
                                float(height) / float(source.height)};
        [render setVertexBytes:scale length:sizeof(scale) atIndex:0];
        [render setFragmentTexture:source atIndex:0];
        [render setFragmentSamplerState:impl->linear atIndex:0];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        scheduler.EndRendering();
    }
}

void Upscaler::PrepareUiDepth(VideoCore::ImageId depth_id, bool copy) {
    id<MTLTexture> target = impl->depth.texture;
    id<MTLTexture> source = nil;
    if (copy) {
        auto& image = texture_cache.GetImage(depth_id);
        source = NativeTexture(image);
        if (!Sampleable(source)) {
            source = nil; // cleared, not copied
        } else {
            scheduler.EndRendering();
            runtime.Transit(&image, vk::ImageLayout::eDepthStencilReadOnlyOptimal,
                            vk::PipelineStageFlagBits2::eFragmentShader,
                            vk::AccessFlagBits2::eShaderRead);
            runtime.FlushBarriers();
        }
    }
    @autoreleasepool {
        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.depthAttachment.texture = target;
        pass.depthAttachment.loadAction = MTLLoadActionClear;
        pass.depthAttachment.storeAction = MTLStoreActionStore;
        pass.depthAttachment.clearDepth = 1.0;
        if (VkToMtl::HasStencilPixelFormat(u32(target.pixelFormat))) {
            pass.stencilAttachment.texture = target;
            pass.stencilAttachment.loadAction = MTLLoadActionClear;
            pass.stencilAttachment.storeAction = MTLStoreActionStore;
            pass.stencilAttachment.clearStencil = 0;
        }
        scheduler.HostRenderEncoder((__bridge const void*)pass);
        scheduler.LabelEncoder("UI depth");
        if (source) {
            // The depth aspect of the whole game depth into the whole UI depth, nearest (the
            // stencil keeps the clear).
            id<MTLRenderCommandEncoder> render = scheduler.GetImpl().render;
            [render setRenderPipelineState:impl->DepthPipeline(target.pixelFormat)];
            [render setDepthStencilState:impl->depth_always];
            [render setViewport:MTLViewport{0, 0, double(impl->width), double(impl->height), 0,
                                            1}];
            const float scale[2] = {1.0f, 1.0f};
            [render setVertexBytes:scale length:sizeof(scale) atIndex:0];
            [render setFragmentTexture:source atIndex:0];
            [render setFragmentSamplerState:impl->nearest atIndex:0];
            [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
        scheduler.EndRendering();
    }
}

bool Upscaler::CanCopyDepth(vk::Format depth_format) const {
    // The depth aspect of these is sampled as a depth texture.
    return depth_format == ui_depth_format &&
           (depth_format == vk::Format::eD32Sfloat || depth_format == vk::Format::eD32SfloatS8Uint);
}

VideoCore::ReplacementView Upscaler::UiColorView(vk::Format format, vk::ComponentMapping mapping) {
    return {NativeViewOf(impl->color.View(format, mapping)), vk::ImageLayout::eGeneral};
}

VideoCore::ReplacementView Upscaler::UiDepthView() {
    return {NativeViewOf(impl->depth.texture), vk::ImageLayout::eGeneral};
}

void Upscaler::UiReadBarrier() {
    // Metal orders the passes that write and then sample a (hazard tracked) texture: no barrier
    // to encode. The open pass is ended, as Vulkan's barrier does: it may be the last UI pass,
    // which must not still be rendering into the texture when the display pass samples it.
    scheduler.EndRendering();
}

VideoCore::ReplacementView Upscaler::DisplayTarget(VAddr address, vk::Format image_format,
                                                   vk::Format view_format, u32 width,
                                                   u32 height) {
    Impl::Display& display = impl->displays[address];
    if (!display.target.texture || display.format != image_format || display.width != width ||
        display.height != height) {
        Retire(scheduler, display.target);
        std::printf("Display: host buffer %ux%u (live)\n", width, height);
        display.format = image_format;
        display.width = width;
        display.height = height;
        display.target.texture =
            MakeTexture(impl->dev.device, image_format, width, height,
                        MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead |
                            MTLTextureUsagePixelFormatView,
                        @"Display buffer");
    }
    return {NativeViewOf(display.target.View(view_format, {})), vk::ImageLayout::eGeneral};
}

bool Upscaler::DisplayOverride(VAddr address, DisplayImage& display) {
    return ui.DisplayOverride(address, [&](vk::Format format, u32 width, u32 height) {
        display = {(__bridge const void*)impl->displays[address].target.texture, format, width,
                   height};
    });
}

} // namespace Metal
