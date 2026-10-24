// SPDX-License-Identifier: GPL-2.0-or-later
// Counterpart: renderer_vulkan/vk_presenter.cpp (cut down: no FSR, HDR).

#import <QuartzCore/CAMetalLayer.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
#include "video_core/renderer_metal/mtl_present_dump.h"

#include "common/assert.h"
#include "common/logging/log.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "bbport_game_menu.h"
#include "bbport_toggles.h"
#include "bbport_submit_timing.h"
#include "bbport_flush_reason.h"
#include "sdl_window.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_frame_interpolator.h"
#include "video_core/renderer_metal/mtl_overlay.h"
#include "video_core/renderer_metal/mtl_presenter.h"
#include "video_core/renderer_metal/mtl_rasterizer.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_upscaler.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/texture_cache/texture_cache.h"
#include "video_core/ui_compositor.h"

#include "video_core/host_shaders_msl/fs_tri_vert_msl.h"
#include "video_core/host_shaders_msl/post_process_frag_msl.h"

namespace Metal {
namespace H = HostShaders;
namespace {

/// BB_PRESENT_DUMP_TRIGGER=<file> (VideoCore::PresentDumpDue, as on Vulkan): when the file exists
/// it is removed and the game's display buffer of this frame (the output-size host display buffer
/// of a scaled session) is written raw (4 bytes per pixel, the texture's byte order) to
/// <BB_DUMP_DIR or out/dump>/fNNN_present_WxH_rgba.raw, for comparing the backends' images
/// without the window scaling.
void DumpPresented(id<MTLDevice> device,id<MTLCommandQueue> queue,id<MTLTexture> texture,int index,bool flip_vertical=false){
    static const std::string dir=[] {const char* e=std::getenv("BB_DUMP_DIR");return std::string{e&&*e?e:"out/dump"};}();
    BbQaPresentDump::Write(device,queue,texture,dir,index,flip_vertical);
}

/// The pixel format of the frame textures and of the layer. No HDR, so the post process pass
/// writes the gamma encoded SDR image.
constexpr MTLPixelFormat FramePixelFormat = MTLPixelFormatBGRA8Unorm;

/// What the present pass draws with: fs_tri's vertex shader (H::FS_TRI_VERT) and this fragment
/// shader. The frame texture holds the image upside down (fs_tri maps uv.y = 0 to the bottom of
/// the clip space, which is the bottom row of a Metal render target, while the video-out image's
/// row 0 is the top), and fs_tri does the same when it draws the frame into the drawable: the
/// two flips cancel.
NSString* const PresentSource = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct PresentIn { float2 uv [[user(locn0)]]; };\n"
    "fragment float4 present_main(PresentIn in [[stage_in]],\n"
    "                             texture2d<float> frame [[texture(0)]],\n"
    "                             sampler frame_sampler [[sampler(0)]]) {\n"
    "    return frame.sample(frame_sampler, in.uv);\n"
    "}\n";

/// The push constants of post_process.frag (H::POST_PROCESS_FRAG, buffer 0).
struct PostProcessSettings {
    float gamma;
    u32 hdr;
    u32 srgb_input;
};
static_assert(sizeof(PostProcessSettings) == 12);

vk::Format GetFrameViewFormat(const Libraries::VideoOut::PixelFormat format) {
    switch (format) {
    case Libraries::VideoOut::PixelFormat::A8B8G8R8Srgb:
        return vk::Format::eR8G8B8A8Srgb;
    case Libraries::VideoOut::PixelFormat::A8R8G8B8Srgb:
        return vk::Format::eB8G8R8A8Srgb;
    case Libraries::VideoOut::PixelFormat::A2R10G10B10:
    case Libraries::VideoOut::PixelFormat::A2R10G10B10Srgb:
    case Libraries::VideoOut::PixelFormat::A2R10G10B10Bt2020Pq:
        return vk::Format::eA2R10G10B10UnormPack32;
    default:
        break;
    }
    UNREACHABLE_MSG("Unknown format={}", static_cast<u32>(format));
    return {};
}

/// The destination of a frame_width x frame_height image fitted into a target, centred (the
/// Vulkan presenter's FitImage, same integer rounding: the backends' screenshots compare).
struct FitRect {
    s32 x, y;
    u32 width, height;
};
FitRect FitImage(s32 frame_width, s32 frame_height, s32 target_width, s32 target_height) {
    const float frame_aspect = static_cast<float>(frame_width) / frame_height;
    const float target_aspect = static_cast<float>(target_width) / target_height;
    u32 width = target_width;
    u32 height = target_height;
    if (frame_aspect > target_aspect) {
        height = static_cast<s32>(target_width / frame_aspect);
    } else {
        width = static_cast<s32>(target_height * frame_aspect);
    }
    return {(target_width - s32(width)) / 2, (target_height - s32(height)) / 2, width, height};
}

std::string ErrorText(NSError* error) {
    return error ? error.localizedDescription.UTF8String : "unknown";
}

/// The presented frame rate and the latency of the game's frames (P7.2), printed every 5 s (both
/// with and without frame interpolation, to compare them):
///   Metal present: <presented FPS> presented, <game FPS> game, latency mean/p95 <ms> ms,
///   <interpolated FPS> interpolated
/// Presented counts the drawables the display took (addPresentedHandler, presentedTime > 0:
/// interpolated frames and re-presents included); game counts the new frames Present received;
/// the latency of a game frame is its drawable's presentedTime minus the host time Present got
/// the frame (CACurrentMediaTime). Any thread (the handlers run on system threads), so it is
/// shared with them and outlives the presenter's Impl.
class PresentStats {
public:
    void OnGameFrame() {
        std::lock_guard lock{mutex};
        ++game;
    }
    /// A drawable was presented (`presented_time` 0: dropped). `arrival` is when Present got the
    /// game frame this drawable shows (0 for an interpolated frame, a re-present, a blank one).
    void OnPresented(double presented_time, bool interpolated, double arrival) {
        std::lock_guard lock{mutex};
        if (presented_time > 0.0) {
            ++presented;
            if (interpolated) {
                ++interpolated_count;
            }
            if (arrival > 0.0 && presented_time >= arrival) {
                latencies.push_back((presented_time - arrival) * 1000.0);
            }
        }
        const double now = CACurrentMediaTime();
        if (window_start == 0.0) {
            window_start = now;
        }
        const double elapsed = now - window_start;
        if (elapsed < 5.0) {
            return;
        }
        double mean = 0.0, p95 = 0.0;
        if (!latencies.empty()) {
            std::sort(latencies.begin(), latencies.end());
            for (const double ms : latencies) {
                mean += ms;
            }
            mean /= double(latencies.size());
            p95 = latencies[std::min(latencies.size() - 1,
                                     size_t(std::ceil(0.95 * double(latencies.size()))) - 1)];
        }
        std::printf("Metal present: %.1f presented, %.1f game, latency mean/p95 %.1f/%.1f ms, "
                    "%.1f interpolated\n",
                    presented / elapsed, game / elapsed, mean, p95, interpolated_count / elapsed);
        window_start = now;
        presented = game = interpolated_count = 0;
        latencies.clear();
    }

private:
    std::mutex mutex;
    double window_start = 0.0;
    u32 presented = 0, game = 0, interpolated_count = 0;
    std::vector<double> latencies; ///< ms, of the window
};

} // namespace

struct Presenter::Impl {
    CAMetalLayer* layer;
    u32 drawable_width{};
    u32 drawable_height{};
    /// The presenter's own queue: Present must not queue behind the draw scheduler's work (its
    /// command buffers wait for the draw ticks through the scheduler's event instead).
    id<MTLCommandQueue> present_queue;
    id<MTLRenderPipelineState> post_process_pipeline; ///< fs_tri + post_process into a frame
    id<MTLRenderPipelineState> present_pipeline;      ///< fs_tri + a plain sample into the drawable
    id<MTLSamplerState> sampler;                      ///< linear, clamped
    /// The frame textures, by Frame::index. A slot is written by the thread that prepares the
    /// frame, which owns the frame (it is out of the pool), and read by the one that presents it.
    std::array<id<MTLTexture>, NumFrames> textures;
    /// The interpolated frames' textures, as `textures` (made when a frame is first interpolated).
    std::array<id<MTLTexture>, NumFrames> interpolated;

    std::shared_ptr<PresentStats> stats = std::make_shared<PresentStats>();
    // P7.2 (BB_FRAME_INTERPOLATION=1): null when off.
    std::unique_ptr<FrameInterpolator> interpolator;
    /// The game's frame interval in seconds (smoothed; 0 until measured): written by Present (the
    /// present thread), read by PrepareFrame for deltaTime.
    std::atomic<double> frame_interval{0.0};
    double last_game_present{0.0}; ///< CACurrentMediaTime of the last new game frame (Present)
    double last_prepare{0.0};      ///< the same in PrepareFrame (a pause resets the history)
};

Presenter::Presenter(Frontend::WindowSDL& window_, AmdGpu::Liverpool* liverpool_)
    : window{window_}, liverpool{liverpool_}, device{std::make_unique<Device>()},
      scheduler{std::make_unique<Scheduler>(*device, "bbport draw")},
      runtime{std::make_unique<Runtime>(*device, *scheduler)},
      rasterizer{std::make_unique<Rasterizer>(*device, *scheduler, *runtime, liverpool_)},
      impl{std::make_unique<Impl>()} {
    const auto info = window.GetWindowInfo();
    ASSERT_MSG(info.type == Frontend::WindowSystemType::Metal && info.render_surface,
               "The window has no CAMetalLayer");
    Device::Impl& dev = device->GetImpl();
    impl->layer = (__bridge CAMetalLayer*)info.render_surface;
    impl->layer.device = dev.device;
    impl->layer.pixelFormat = FramePixelFormat;
    impl->layer.framebufferOnly = YES;
    impl->layer.maximumDrawableCount = 3; // as MoltenVK's swapchain
    // The present mode as on the Vulkan path (Swapchain::FindPresentMode, BB_PRESENT_MODE,
    // default Mailbox): MoltenVK has no Mailbox and falls back to Immediate, which it implements
    // as displaySyncEnabled = NO; Fifo is displaySyncEnabled = YES. Without display sync the
    // window server composites a presented frame without tearing and nextDrawable does not tie
    // the game to the display's release of a drawable (FIFO: ~25 ms a frame).
    {
        const std::string requested = EmulatorSettings.GetPresentMode();
        std::string mode = requested;
        if (requested != "Mailbox" && requested != "Fifo" && requested != "Immediate") {
            LOG_ERROR(Render, "Unknown present mode {}, defaulting to Mailbox.", requested);
            mode = "Mailbox";
        }
        // MetalFX frame interpolation (P7.2) presents two frames a game frame: display sync spaces
        // them a vblank apart (Mailbox with presentDrawable:afterMinimumDuration: or atTime:
        // blocked the present thread: 15 and 9 game FPS instead of 27 with Fifo, m4air).
        const bool interpolating = FrameInterpolator::Requested((__bridge const void*)dev.device);
        if (interpolating && mode != "Fifo") {
            std::printf("Metal: frame interpolation paces by display sync: present mode Fifo\n");
            mode = "Fifo";
        }
        const bool display_sync = mode == "Fifo";
        impl->layer.displaySyncEnabled = display_sync ? YES : NO;
        std::printf("Metal: present mode %s (display sync %s)\n", mode.c_str(),
                    display_sync ? "on" : "off");
    }
    impl->present_queue = [dev.device newCommandQueue];
    impl->present_queue.label = @"bbport present";

    // The pipelines are made here, while nothing else records: the host shader functions come
    // from the cache backend's kernels, which are not thread safe.
    HostKernels& kernels = runtime->GetImpl().kernels;
    id<MTLFunction> vertex = kernels.Function(H::FS_TRI_VERT, nullptr, 0);
    NSError* error = nil;
    {
        MTLRenderPipelineDescriptor* descriptor = [MTLRenderPipelineDescriptor new];
        descriptor.label = @"Post process";
        descriptor.vertexFunction = vertex;
        descriptor.fragmentFunction = kernels.Function(H::POST_PROCESS_FRAG, nullptr, 0);
        descriptor.colorAttachments[0].pixelFormat = FramePixelFormat;
        impl->post_process_pipeline = [dev.device newRenderPipelineStateWithDescriptor:descriptor
                                                                                 error:&error];
        ASSERT_MSG(impl->post_process_pipeline, "Post process pipeline failed: {}",
                   ErrorText(error));
    }
    {
        MTLCompileOptions* options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_1;
        id<MTLLibrary> library = [dev.device newLibraryWithSource:PresentSource
                                                          options:options
                                                            error:&error];
        ASSERT_MSG(library, "Present shader did not compile: {}", ErrorText(error));
        MTLRenderPipelineDescriptor* descriptor = [MTLRenderPipelineDescriptor new];
        descriptor.label = @"Present";
        descriptor.vertexFunction = vertex;
        descriptor.fragmentFunction = [library newFunctionWithName:@"present_main"];
        descriptor.colorAttachments[0].pixelFormat = FramePixelFormat;
        impl->present_pipeline = [dev.device newRenderPipelineStateWithDescriptor:descriptor
                                                                            error:&error];
        ASSERT_MSG(impl->present_pipeline, "Present pipeline failed: {}", ErrorText(error));
    }
    {
        MTLSamplerDescriptor* descriptor = [MTLSamplerDescriptor new];
        descriptor.minFilter = MTLSamplerMinMagFilterLinear;
        descriptor.magFilter = MTLSamplerMinMagFilterLinear;
        descriptor.sAddressMode = MTLSamplerAddressModeClampToEdge;
        descriptor.tAddressMode = MTLSamplerAddressModeClampToEdge;
        impl->sampler = [dev.device newSamplerStateWithDescriptor:descriptor];
    }

    // MetalFX frame interpolation (P7.2): off unless BB_FRAME_INTERPOLATION=1 (and macOS 26).
    if (FrameInterpolator::Requested((__bridge const void*)dev.device)) {
        impl->interpolator = std::make_unique<FrameInterpolator>(*device, *scheduler);
        std::printf("Metal: MetalFX frame interpolation on (it runs while the MetalFX temporal "
                    "scaler does)\n");
    }

    // The settings menu / FPS counter (shim/bbport_overlay), drawn by Present over the frame.
    Overlay::Init(*device);

    u32 index = 0;
    for (auto& frame : frames) {
        frame.index = index++;
        free_queue.push_back(&frame);
    }
}

Presenter::~Presenter() {
    // The draw scheduler is done with the frame textures (it is idle, everything it recorded
    // has completed); the Present command buffers still in flight are waited for through their
    // completion handlers (which touch the pool), then Impl and the textures go. A failed
    // scheduler never signals the ticks the Present command buffers wait for: no waiting then.
    scheduler->Finish();
    while (presents_in_flight.load(std::memory_order_acquire) != 0 && !scheduler->HasFailed()) {
        std::this_thread::yield();
    }
}

void Presenter::RegisterVideoOutSurface(const Libraries::VideoOut::BufferAttributeGroup& attribute,
                                        VAddr cpu_address) {
    auto& texture_cache = rasterizer->GetTextureCache();
    auto desc = VideoCore::TextureCache::ImageDesc{attribute, cpu_address};
    const auto image_id = texture_cache.FindImage(desc);
    auto& image = texture_cache.GetImage(image_id);
    image.usage.vo_surface = 1u;
}

Frame* Presenter::GetRenderFrame() {
    std::unique_lock lock{free_mutex};
    free_cv.wait(lock, [this] { return !free_queue.empty(); });
    Frame* frame = free_queue.front();
    free_queue.pop_front();
    return frame;
}

void Presenter::FreeFrame(Frame* frame) {
    std::scoped_lock lock{free_mutex};
    free_queue.push_back(frame);
    free_cv.notify_one();
}

VideoCore::PresentFrame* Presenter::PrepareFrame(
    const Libraries::VideoOut::BufferAttributeGroup& attribute, VAddr cpu_address) {
    // Encoding is immediate and one thread at a time: the draw recording thread must be idle.
    rasterizer->DrainDrawPipe();

    auto& texture_cache = rasterizer->GetTextureCache();
    // bbport: scaled sessions (BB_RENDER_RES): the output-size display buffer drawn by the port
    // stands in for the guest's.
    Upscaler::DisplayImage display{};
    const bool upscaled = rasterizer->GetUpscaler().DisplayOverride(cpu_address, display);
    VideoCore::ImageId image_id{};
    if (!upscaled) {
        auto desc = VideoCore::TextureCache::ImageDesc{attribute, cpu_address};
        image_id = texture_cache.FindImage(desc);
        texture_cache.UpdateImage(image_id);
    }

    Frame* frame = GetRenderFrame();

    scheduler->EndRendering();

    VideoCore::ImageViewInfo view_info{};
    view_info.format = GetFrameViewFormat(attribute.attrib.pixel_format);
    // Exclude alpha from output frame to avoid blending with UI.
    view_info.mapping.a = vk::ComponentSwizzle::eOne;

    id<MTLTexture> source; ///< the view the post process pass samples
    id<MTLTexture> dump;   ///< the texture BB_PRESENT_DUMP_TRIGGER writes
    id<MTLTexture> interpolated_source = nil; ///< the same of the interpolated frame (P7.2)
    u32 width, height;     ///< the size of the image
    if (upscaled) {
        id<MTLTexture> base = (__bridge id<MTLTexture>)display.texture;
        // The view of a host display texture the post process pass samples: the video-out
        // format's, alpha forced to one.
        const auto make_view = [&](id<MTLTexture> texture) {
            const u32 view_format = VkToMtl::PixelFormat(view_info.format);
            const VkToMtl::TextureSwizzle swizzle =
                VkToMtl::ComposeSwizzle(view_info.mapping, view_info.format);
            id<MTLTexture> view = [texture
                newTextureViewWithPixelFormat:view_format ? MTLPixelFormat(view_format)
                                                          : texture.pixelFormat
                                  textureType:MTLTextureType2D
                                       levels:NSMakeRange(0, 1)
                                       slices:NSMakeRange(0, 1)
                                      swizzle:MTLTextureSwizzleChannelsMake(
                                                  MTLTextureSwizzle(swizzle.r),
                                                  MTLTextureSwizzle(swizzle.g),
                                                  MTLTextureSwizzle(swizzle.b),
                                                  MTLTextureSwizzle(swizzle.a))];
            ASSERT_MSG(view, "Failed to create a view of the host display buffer");
            // Command buffers do not retain: the view lives until the pass that samples it is
            // done.
            scheduler->DeferOperation([view] { (void)view; });
            return view;
        };
        source = make_view(base);
        dump = base;
        width = display.width;
        height = display.height;
        // The interpolated frame between the previous game frame and this one (P7.2): the
        // output of the MetalFX interpolator, in the display buffer's format, shown through the
        // same post process pass. Frames that do not come through the temporal scaler break
        // the history.
        if (impl->interpolator) {
            InterpolationInputs inputs;
            const bool have_inputs = rasterizer->GetUpscaler().TakeInterpolationInputs(inputs);
            const double now = CACurrentMediaTime();
            const bool paused = impl->last_prepare > 0.0 && now - impl->last_prepare > 0.5;
            impl->last_prepare = now;
            if (have_inputs) {
                const double interval = impl->frame_interval.load(std::memory_order_relaxed);
                if (const void* output = impl->interpolator->Run(
                        display.texture, inputs, interval > 0.0 ? interval : 1.0 / 30.0, paused)) {
                    interpolated_source = make_view((__bridge id<MTLTexture>)output);
                }
            } else {
                impl->interpolator->Invalidate();
            }
        }
    } else {
        if (impl->interpolator) {
            // The history is gone: the next scaled frame starts it again. (The inputs of a frame
            // that was not displayed through the host buffer are dropped.)
            InterpolationInputs inputs;
            rasterizer->GetUpscaler().TakeInterpolationInputs(inputs);
            impl->interpolator->Invalidate();
            impl->last_prepare = CACurrentMediaTime();
        }
        auto& image = texture_cache.GetImage(image_id);
        const VideoCore::ImageView& view = image.FindView(view_info);
        source = (__bridge id<MTLTexture>)view.native;
        dump = NativeTexture(image);
        width = image.info.size.width;
        height = image.info.size.height;
        runtime->Transit(&image, vk::ImageLayout::eShaderReadOnlyOptimal,
                         vk::PipelineStageFlagBits2::eFragmentShader,
                         vk::AccessFlagBits2::eShaderRead);
        runtime->FlushBarriers();
    }
    // As on Vulkan, the post process pass scales the image to the size it takes in the window
    // (aspect kept), so Present copies it 1:1 and the backends resample alike.
    if (const s32 window_width = s32(window.GetWidth()), window_height = s32(window.GetHeight());
        window_width > 0 && window_height > 0) {
        const FitRect fit = FitImage(s32(width), s32(height), window_width, window_height);
        width = std::max(fit.width, 1u);
        height = std::max(fit.height, 1u);
    }

    @autoreleasepool {
        // The frame texture, made again when the video-out buffer's size changed. The draw
        // scheduler's command buffers do not retain what they use: the old one is released once
        // they are done with the open tick (the frame's last render is in it or before).
        const auto frame_texture = [&](std::array<id<MTLTexture>, NumFrames>& textures,
                                       NSString* label) {
            id<MTLTexture> texture = textures[frame->index];
            if (!texture || texture.width != width || texture.height != height) {
                if (texture) {
                    scheduler->DeferOperation([old = texture] { (void)old; });
                }
                MTLTextureDescriptor* descriptor =
                    [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:FramePixelFormat
                                                                       width:width
                                                                      height:height
                                                                   mipmapped:NO];
                descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
                descriptor.storageMode = MTLStorageModePrivate;
                texture = [device->GetImpl().device newTextureWithDescriptor:descriptor];
                ASSERT_MSG(texture, "Failed allocating the {}x{} frame texture", width, height);
                texture.label = [NSString stringWithFormat:@"%@ #%u", label, frame->index];
                textures[frame->index] = texture;
            }
            return texture;
        };
        id<MTLTexture> texture = frame_texture(impl->textures, @"Frame");
        frame->width = width;
        frame->height = height;

        // Vulkan has no sRGB variant of the 10-bit format, so an A2R10G10B10Srgb buffer reaches
        // the post process pass still sRGB encoded and has to be decoded there. Metal has none
        // either (the view is BGR10A2Unorm).
        const PostProcessSettings settings{
            .gamma = gamma,
            .hdr = 0,
            .srgb_input = attribute.attrib.pixel_format ==
                                  Libraries::VideoOut::PixelFormat::A2R10G10B10Srgb
                              ? 1u
                              : 0u,
        };
        const auto post_process = [&](id<MTLTexture> from, id<MTLTexture> to) {
            MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = to;
            pass.colorAttachments[0].loadAction = MTLLoadActionDontCare; // the triangle covers it
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            scheduler->HostRenderEncoder((__bridge const void*)pass);
            id<MTLRenderCommandEncoder> render = scheduler->GetImpl().render;
            [render setRenderPipelineState:impl->post_process_pipeline];
            [render setViewport:MTLViewport{0, 0, double(width), double(height), 0, 1}];
            [render setFragmentBytes:&settings length:sizeof(settings) atIndex:0];
            [render setFragmentTexture:from atIndex:0];
            [render setFragmentSamplerState:impl->sampler atIndex:0];
            [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            scheduler->EndRendering();
        };
        post_process(source, texture);
        if (interpolated_source) {
            post_process(interpolated_source, frame_texture(impl->interpolated, @"Interpolated"));
            frame->interpolated = true;
        } else {
            frame->interpolated = false;
        }
    }

    // Flush frame creation commands: the present thread waits for this tick.
    BbStats::frame_number.fetch_add(1, std::memory_order_relaxed); // bbport: game frames shown
    frame->tick.store(scheduler->CurrentTick(), std::memory_order_release);
    {
        BbFlushReason::Scope reason{BbFlushReason::Reason::PresenterPrepare};
        rasterizer->Flush();
    }
    if (VideoCore::PresentDumpDue()) {
        BbFlushReason::Scope reason{BbFlushReason::Reason::PresenterWait};
        static int dump_index = 0;
        scheduler->Wait(frame->tick.load());
        static const bool final_image=[] {const char* e=std::getenv("BB_PRESENT_DUMP_FINAL");return e&&e[0]=='1';}();
        // Diagnostic preview of the already completed post-process target preserves
        // the actual present view's swizzle/gamma. Its rows are flipped by Present.
        DumpPresented(device->GetImpl().device,device->GetImpl().queue,
                      final_image?impl->textures[frame->index]:dump,dump_index++,final_image);
    }
    // The BB_FRAMES_AHEAD bound (video_core/frames_ahead.h), on the committed tick.
    frames_ahead.Throttle(frame->tick.load(std::memory_order_relaxed),
                          [this](u64 tick) { scheduler->Wait(tick); });
    return frame;
}

VideoCore::PresentFrame* Presenter::PrepareBlankFrame(bool present_thread) {
    // Nothing is rendered, so the draw scheduler stays untouched (the present thread must not
    // use it): Present clears the drawable.
    Frame* frame = GetRenderFrame();
    frame->interpolated = false;
    frame->tick.store(0, std::memory_order_release);
    return frame;
}

VideoCore::PresentFrame* Presenter::PrepareLastFrame() {
    // The last presented frame, if it is in the pool still (its Present completed and the GPU
    // thread did not take it for the next frame). It is taken out of the pool until the
    // re-present completes, so that nothing renders into its texture meanwhile.
    std::scoped_lock lock{free_mutex};
    if (last_submit_frame == nullptr) {
        return nullptr;
    }
    const auto it = std::ranges::find(free_queue, last_submit_frame);
    if (it == free_queue.end()) {
        return nullptr;
    }
    Frame* frame = *it;
    free_queue.erase(it);
    return frame;
}

void Presenter::WaitRendered(const VideoCore::PresentFrame* present_frame) {
    const auto* frame = static_cast<const Frame*>(present_frame);
    if (!frame) {
        return;
    }
    // A committed tick (PrepareFrame flushed it); 0 is a blank frame.
    if (const u64 tick = frame->tick.load(std::memory_order_acquire); tick != 0) {
        BbFlushReason::Scope reason{BbFlushReason::Reason::PresenterWait};
        scheduler->Wait(tick);
    }
}

void Presenter::Present(VideoCore::PresentFrame* present_frame, bool is_reusing_frame,
                        bool is_game_frame) {
    auto* frame = static_cast<Frame*>(present_frame);
    const double arrival = CACurrentMediaTime(); // the latency of the frame starts here
    const bool new_game_frame = !is_reusing_frame && is_game_frame;
    if (!is_reusing_frame) {
        std::scoped_lock lock{free_mutex};
        last_submit_frame = frame;
    }
    if (new_game_frame) {
        // The game's frame interval, smoothed (a pause is not a frame rate): the interpolator's
        // deltaTime.
        impl->stats->OnGameFrame();
        const double dt = arrival - impl->last_game_present;
        if (impl->last_game_present > 0.0 && dt > 0.001 && dt < 0.5) {
            const double previous = impl->frame_interval.load(std::memory_order_relaxed);
            impl->frame_interval.store(previous > 0.0 ? previous * 0.8 + dt * 0.2 : dt,
                                       std::memory_order_relaxed);
        }
        impl->last_game_present = arrival;
    }
    // The frame goes back to the pool: from the completion of the command buffer that reads its
    // texture (the next PrepareFrame of the frame writes it), or at once when nothing reads it.
    // A re-presented frame was taken out of the pool by PrepareLastFrame and goes back as well.
    // (By value: the completion handler outlives this call.)
    const auto free_frame = [this, frame] { FreeFrame(frame); };

    // Nothing to present to while the window is minimised (a 0x0 drawable).
    const u32 width = window.GetWidth();
    const u32 height = window.GetHeight();
    if (width == 0 || height == 0) {
        free_frame();
        return;
    }
    @autoreleasepool {
        if (width != impl->drawable_width || height != impl->drawable_height) {
            impl->layer.drawableSize = CGSizeMake(width, height);
            impl->drawable_width = width;
            impl->drawable_height = height;
        }
        // Blocks until a drawable is free (all 3 are queued for the display); with display sync
        // off that is short. After its 1 s timeout nextDrawable returns nil: the frame is skipped.
        id<CAMetalDrawable> drawable = [impl->layer nextDrawable];
        if (!drawable) {
            free_frame();
            return;
        }
        const u64 tick = frame->tick.load(std::memory_order_acquire);
        // P7.2: a new game frame with an interpolated one before it takes a second drawable; the
        // interpolated frame goes into the first. Without a second drawable the real frame is
        // shown alone.
        id<CAMetalDrawable> real_drawable = drawable;
        id<CAMetalDrawable> interpolated_drawable = nil;
        if (new_game_frame && tick != 0 && frame->interpolated) {
            if (id<CAMetalDrawable> second = [impl->layer nextDrawable]) {
                interpolated_drawable = drawable;
                real_drawable = second;
            }
        }
        // The command buffers that read the frame: the last of them to complete returns it.
        auto remaining = std::make_shared<std::atomic<int>>(interpolated_drawable ? 2 : 1);
        const std::shared_ptr<PresentStats> stats = impl->stats;

        BbGameMenu::Poll(); // the port's pages in the game's System menu
        const auto submit = [&](id<CAMetalDrawable> target, id<MTLTexture> frame_texture,
                                bool is_interpolated) {
            MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = target.texture;
            pass.colorAttachments[0].loadAction = MTLLoadActionClear; // the bars
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            pass.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 0.0, 1.0);
            id<MTLCommandBuffer> cmdbuf = [impl->present_queue commandBuffer];
            cmdbuf.label = is_interpolated ? @"Present interpolated" : @"Present";
            if (tick != 0) {
                // The post process pass of the frame, on the draw scheduler's queue.
                [cmdbuf encodeWaitForEvent:scheduler->GetImpl().event value:tick];
            }
            id<MTLRenderCommandEncoder> encoder = [cmdbuf renderCommandEncoderWithDescriptor:pass];
            if (tick != 0) {
                // Centred; 1:1 when the window kept its size since PrepareFrame.
                const FitRect fit =
                    FitImage(s32(frame->width), s32(frame->height), s32(target.texture.width),
                             s32(target.texture.height));
                [encoder setRenderPipelineState:impl->present_pipeline];
                [encoder setViewport:MTLViewport{double(fit.x), double(fit.y), double(fit.width),
                                                 double(fit.height), 0, 1}];
                [encoder setFragmentTexture:frame_texture atIndex:0];
                [encoder setFragmentSamplerState:impl->sampler atIndex:0];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            }
            static const bool merge_overlay=[] {
                const char* e=std::getenv("BB_MTL_MERGE_OVERLAY");return e && e[0]=='1';
            }();
            if (merge_overlay)
                Overlay::Render((__bridge const void*)cmdbuf, (__bridge const void*)target.texture,
                                (__bridge const void*)encoder);
            [encoder endEncoding];
            // bbport: the settings menu / FPS counter over the frame, at display resolution
            // (drawn into each of the frames shown, so the menu does not flicker).
            if (!merge_overlay)
                Overlay::Render((__bridge const void*)cmdbuf, (__bridge const void*)target.texture);
            [cmdbuf presentDrawable:target];
            // Blocks capture C++ references as references: copies of what outlives this call.
            const std::shared_ptr<std::atomic<int>> pending = remaining;
            const std::shared_ptr<PresentStats> frame_stats = stats;
            const auto release_frame = free_frame;
            // The latency of a game frame: from Present to the display taking its drawable.
            const double latency_start = !is_interpolated && new_game_frame && tick != 0 ? arrival : 0.0;
            [target addPresentedHandler:^(id<MTLDrawable> presented) {
                frame_stats->OnPresented(presented.presentedTime, is_interpolated, latency_start);
            }];
            presents_in_flight.fetch_add(1, std::memory_order_acq_rel);
            const unsigned timing_frame=BbStats::frame_number.load(std::memory_order_relaxed);
            const unsigned timing_index=BbSubmitTiming::Begin(timing_frame);
            const double timing_commit=timing_index<BbSubmitTiming::Capacity ? CACurrentMediaTime() : 0;
            const auto timing_queue=reinterpret_cast<std::uintptr_t>(this);
            [cmdbuf addCompletedHandler:^(id<MTLCommandBuffer> completed) {
                if (completed.status == MTLCommandBufferStatusError) {
                    LOG_ERROR(Render, "Metal present command buffer failed: {}",
                              ErrorText(completed.error));
                }
                BbStats::NoteGpuInterval(completed.GPUStartTime, completed.GPUEndTime);
                BbSubmitTiming::Complete(timing_index,{timing_queue,tick,timing_frame,1,timing_commit,
                                                      completed.GPUStartTime,completed.GPUEndTime,
                                                      timing_index<BbSubmitTiming::Capacity ? CACurrentMediaTime() : 0});
                if (pending->fetch_sub(1, std::memory_order_acq_rel) == 1) {
                    release_frame();
                }
                // Last: the destructor waits for this.
                presents_in_flight.fetch_sub(1, std::memory_order_acq_rel);
            }];
            [cmdbuf commit];
        };
        if (interpolated_drawable) {
            submit(interpolated_drawable, impl->interpolated[frame->index], true);
        }
        submit(real_drawable, impl->textures[frame->index], false);
    }
    if (new_game_frame) {
        DebugState.IncFlipFrameNum();
    }
}

} // namespace Metal
