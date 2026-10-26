// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_scene_targets (Metal::SceneTargets, plan P7.5 L2): the proxies
// of the live-resolution path and the copies between a proxy and the guest-size ("native") image
// in both directions at 1920x1080 <-> 960x540, values checked texel by texel: a colour format
// (RGBA8, an sRGB view of it, RGBA16F, R32Uint), the levels of a half resolution mip chain, and
// depth with stencil (D32F_S8). Also the coherence steps through the Metal part (fill when a
// proxy is created, resolve on a native access, invalidation on a native write) and the
// eligibility of formats. Needs a Metal device, no window or game. Run it with MTL_DEBUG_LAYER=1
// as well.
//
//   mtl-scene-targets-test
//
// Exit 0 when every check passed.
#import <Metal/Metal.h>

#include <setjmp.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <thread>
#include <vector>

#include "common/slot_vector.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include "video_core/texture_cache/tile_manager.h"
#include "video_core/host_shaders_msl/upscale_merge_comp_msl.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/vk_to_mtl.h"

#include <vulkan/vulkan_format_traits.hpp>

// The runtime services libbbgpu takes from bb-probe; this test has no guest process.
extern "C" {
    __thread sigjmp_buf* runtime_fault_recover = nullptr;
    uint64_t runtime_disabled_optimizations = 0;
    uint64_t runtime_experiment_bits = 0;
    uint64_t runtime_tsc_frequency() { return 1000000000; }
    int runtime_file_translate(const char*, char*, size_t) { std::abort(); }
    uint64_t runtime_memory_clamp(uintptr_t, uint64_t) { std::abort(); }
    uint64_t runtime_process_time_us() {
        return std::chrono::duration_cast<std::chrono::microseconds>(
                   std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    int runtime_memory_region(uintptr_t, uintptr_t*, uintptr_t*, int*) { std::abort(); }
    void runtime_memory_set_gpu_hooks(void (*)(uintptr_t, uint64_t), void (*)(uintptr_t, uint64_t),
                                      void (*)(uintptr_t, uint64_t)) { std::abort(); }
    void runtime_thread_attach_host(const char*) {}
    void runtime_memory_gpu_protect(uintptr_t, uint64_t, int, int) { std::abort(); }
    void runtime_restart() { std::abort(); }
    uint64_t runtime_process_time_counter() { return runtime_process_time_us() * 1000; }
    int32_t* runtime_errno() { std::abort(); }
    int runtime_memory_write_backing(uintptr_t, const void*, uint64_t) { std::abort(); }
    void runtime_memory_read_backing(uintptr_t, void*, uint64_t) { std::abort(); }
    const uint64_t* runtime_memory_generation() {
        static const uint64_t generation = 0;
        return &generation;
    }
    void runtime_memory_note_write(uintptr_t, uint64_t) { std::abort(); }
    void runtime_memory_set_note_write_hook(void (*)(uintptr_t, uint64_t)) {}
    void runtime_memory_set_cpu_write_hook(void (*)(uintptr_t, uint64_t)) {}
    void runtime_memory_set_write_watch(uintptr_t, uint64_t, int) { std::abort(); }
    void runtime_memory_set_guest_chunk_allocator(int (*)(uint64_t, uint64_t)) {}
    void runtime_memory_set_guest_chunk_whole(int) {}
    int runtime_memory_vma_info(uintptr_t, int*, int*, uintptr_t*) { std::abort(); }
    void runtime_memory_trap(uintptr_t, uint64_t, unsigned, int) { std::abort(); }
    unsigned runtime_memory_trap_reasons(uintptr_t) { std::abort(); }
    int runtime_memory_direct_phys(uintptr_t, uint64_t*, uintptr_t*) { std::abort(); }
    void* runtime_memory_backing_pointer(uint64_t) { return nullptr; }
    uintptr_t runtime_memory_resolve(const char*) { std::abort(); }
    void runtime_wait_report(double) {}
    void runtime_sleep_stats(uint64_t* calls, uint64_t* ns) {
        *calls = 0;
        *ns = 0;
    }
    uint64_t runtime_heap_growths() { return 0; }
}

using namespace VideoCore;
using Metal::Runtime;
using Metal::Scheduler;
using Metal::SceneTargets;

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

struct Fixture {
    Metal::Device device;
    Scheduler scheduler{device, "test-scene-targets"};
    Runtime runtime{device, scheduler};
    id<MTLDevice> mtl = device.GetImpl().device;

    Fixture() {
        scheduler.SetErrorHandler([](const std::string& report) {
            std::printf("  FAIL command buffer error: %s\n", report.c_str());
            ++failures;
        });
    }
};

/// Finish and give Metal a moment to retire the command buffers (the scheduler frees a tick when
/// its event is signalled, earlier than Metal retires the command buffer; the debug layer reports
/// an object released in between).
static void Settle(Fixture& f) {
    f.runtime.Finish();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
}

// ---- Texture access of the test (blits on a command buffer of its own) --------------------------

/// Copies one aspect of one level of a (private) texture to the host (`bytes` per texel) or back.
static std::vector<u8> ReadTexture(Fixture& f, id<MTLTexture> texture, u32 bytes, u32 level = 0,
                                   MTLBlitOption option = MTLBlitOptionNone) {
    Settle(f);
    const NSUInteger width = std::max<NSUInteger>(texture.width >> level, 1);
    const NSUInteger height = std::max<NSUInteger>(texture.height >> level, 1);
    id<MTLBuffer> buffer = [f.mtl newBufferWithLength:width * height * bytes
                                              options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> commands = [f.device.GetImpl().queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
    [blit copyFromTexture:texture
              sourceSlice:0
              sourceLevel:level
             sourceOrigin:MTLOriginMake(0, 0, 0)
               sourceSize:MTLSizeMake(width, height, 1)
                 toBuffer:buffer
        destinationOffset:0
   destinationBytesPerRow:width * bytes
 destinationBytesPerImage:width * height * bytes
                  options:option];
    [blit endEncoding];
    [commands commit];
    [commands waitUntilCompleted];
    CHECK(commands.error == nil);
    const u8* contents = static_cast<const u8*>(buffer.contents);
    return {contents, contents + width * height * bytes};
}

static void WriteTexture(Fixture& f, id<MTLTexture> texture, u32 bytes, const std::vector<u8>& data,
                         u32 level = 0, MTLBlitOption option = MTLBlitOptionNone) {
    Settle(f);
    const NSUInteger width = std::max<NSUInteger>(texture.width >> level, 1);
    const NSUInteger height = std::max<NSUInteger>(texture.height >> level, 1);
    CHECK(data.size() == width * height * bytes);
    id<MTLBuffer> buffer = [f.mtl newBufferWithBytes:data.data()
                                              length:data.size()
                                             options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> commands = [f.device.GetImpl().queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
    [blit copyFromBuffer:buffer
            sourceOffset:0
       sourceBytesPerRow:width * bytes
     sourceBytesPerImage:width * height * bytes
              sourceSize:MTLSizeMake(width, height, 1)
               toTexture:texture
        destinationSlice:0
        destinationLevel:level
       destinationOrigin:MTLOriginMake(0, 0, 0)
                 options:option];
    [blit endEncoding];
    [commands commit];
    [commands waitUntilCompleted];
    CHECK(commands.error == nil);
}

// ---- Images --------------------------------------------------------------------------------------

static ImageInfo MakeInfo(vk::Format format, u32 width, u32 height, u32 levels = 1) {
    ImageInfo info;
    info.pixel_format = format;
    info.type = AmdGpu::ImageType::Color2D;
    info.resources = {levels, 1};
    info.size = {width, height, 1};
    info.num_samples = 1;
    info.pitch = width;
    info.guest_address = 0x1000;
    info.guest_size = width * height * 4;
    info.props.is_block = vk::blockExtent(format)[0] > 1;
    info.props.is_depth = format == vk::Format::eD32Sfloat || format == vk::Format::eD32SfloatS8Uint;
    info.props.has_stencil = format == vk::Format::eD32SfloatS8Uint;
    info.num_bits = vk::blockSize(format) * 8;
    return info;
}

struct World {
    Common::SlotVector<ImageView> views;
    std::vector<std::unique_ptr<Image>> images;
    Fixture& f;

    explicit World(Fixture& f_) : f{f_} {}
    ~World() {
        Settle(f);
        images.clear();
    }
    ImageId Add(const ImageInfo& info) {
        images.push_back(std::make_unique<Image>(f.runtime, views, info));
        return ImageId{u32(images.size() - 1)};
    }
    Image& Get(ImageId id) {
        return *images[id.index];
    }
    SceneTargets::Lookup Lookup() {
        return [this](ImageId id, u64) -> Image* {
            return id.index < images.size() ? images[id.index].get() : nullptr;
        };
    }
};

static id<MTLTexture> NativeOf(Image& image) {
    return (__bridge id<MTLTexture>)image.backing->image.native;
}

static id<MTLTexture> TextureOf(NativeView view) {
    return (__bridge id<MTLTexture>)reinterpret_cast<void*>(view);
}

/// A pattern of `count` texels of `bytes` bytes where texel i depends on (x, y) and `seed`.
static std::vector<u8> Pattern(u32 width, u32 height, u32 bytes, u32 seed) {
    std::vector<u8> data(size_t(width) * height * bytes);
    for (u32 y = 0; y < height; ++y) {
        for (u32 x = 0; x < width; ++x) {
            for (u32 b = 0; b < bytes; ++b) {
                data[(size_t(y) * width + x) * bytes + b] =
                    u8((x * 7 + y * 13 + b * 31 + seed * 59 + (x >> 8) * 101 + (y >> 7) * 17) & 255);
            }
        }
    }
    return data;
}

/// The same for four half floats per texel: finite, normal values only (a copy through a float
/// shader canonicalizes NaNs and flushes denormals).
static std::vector<u8> HalfPattern(u32 width, u32 height, u32 seed) {
    const auto bytes = Pattern(width, height, 8, seed);
    std::vector<u8> data(bytes.size());
    for (size_t i = 0; i < bytes.size(); i += 2) {
        const u16 bits = u16((bytes[i + 1] & 0x80) << 8 | 0x3c00 | (bytes[i + 1] & 3) << 8 | bytes[i]);
        std::memcpy(&data[i], &bits, 2);
    }
    return data;
}

/// The texel (x, y) of `data` (`width` wide, `bytes` per texel) as a vector.
static std::vector<u8> Texel(const std::vector<u8>& data, u32 width, u32 bytes, u32 x, u32 y) {
    const u8* texel = data.data() + (size_t(y) * width + x) * bytes;
    return {texel, texel + bytes};
}

/// Every texel of `proxy` (width x height) equals the texel of `native` (nw x nh) the nearest copy
/// reads for it: for a smaller proxy the second texel of each block (the pixel centre scaled
/// lands on the border: floor((x + 0.5) * nw / width)), for a larger one the texel it lies in.
static bool ProxyMatches(const std::vector<u8>& proxy, u32 width, u32 height,
                         const std::vector<u8>& native, u32 nw, u32 nh, u32 bytes) {
    u32 bad = 0;
    for (u32 y = 0; y < height; ++y) {
        const u32 sy = std::min(u32(double(y + 0.5) * nh / height), nh - 1);
        for (u32 x = 0; x < width; ++x) {
            const u32 sx = std::min(u32(double(x + 0.5) * nw / width), nw - 1);
            if (std::memcmp(proxy.data() + (size_t(y) * width + x) * bytes,
                            native.data() + (size_t(sy) * nw + sx) * bytes, bytes) != 0) {
                if (bad++ < 3) {
                    std::printf("  texel (%u,%u) differs from the source (%u,%u)\n", x, y, sx, sy);
                }
            }
        }
    }
    return bad == 0;
}

// ---- Tests ----------------------------------------------------------------------------------------

/// A colour image of `format` (`bytes` per texel) at 1920x1080 with a proxy at 960x540: the fill
/// when the proxy is created, the resolve into the native image on a native access, the
/// invalidation by a native write and the fill after it.
static void TestColor(Fixture& f, vk::Format format, u32 bytes, vk::Format view_format) {
    std::printf("colour %s (views as %s)\n", vk::to_string(format).c_str(),
                vk::to_string(view_format).c_str());
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    const auto pattern = [&](u32 width, u32 height, u32 seed) {
        return bytes == 8 ? HalfPattern(width, height, seed) : Pattern(width, height, bytes, seed);
    };
    const ImageId image_id = world.Add(MakeInfo(format, 1920, 1080));
    Image& image = world.Get(image_id);
    id<MTLTexture> native = NativeOf(image);
    CHECK(native.width == 1920 && native.height == 1080);

    CHECK(targets.SetSize({960, 540}));
    CHECK(targets.Reduced());
    CHECK(targets.Eligible(image) && targets.EligibleScene(image));
    CHECK(targets.ProxySize(image, 0).width == 960 && targets.ProxySize(image, 0).height == 540);

    const auto first = pattern(1920, 1080, 1);
    WriteTexture(f, native, bytes, first);
    CHECK(!targets.Tracks(image.image_uid));
    CHECK(!targets.ProxyCurrent(image));

    ImageViewInfo info;
    info.format = view_format;
    const auto target = targets.Attachment(image_id, info);
    CHECK(target.view != 0);
    CHECK(targets.Tracks(image.image_uid));
    id<MTLTexture> proxy = TextureOf(target.view);
    CHECK(proxy.width == 960 && proxy.height == 540 && proxy.mipmapLevelCount == 1);
    CHECK(proxy.sampleCount == 1 && proxy.storageMode == MTLStorageModePrivate);
    CHECK(proxy.usage & MTLTextureUsageRenderTarget);
    CHECK(proxy.usage & MTLTextureUsageShaderRead);
    CHECK(!(proxy.usage & MTLTextureUsageShaderWrite));
    // The view is in the format the pass was asked for; the proxy itself in the image's.
    CHECK(proxy.pixelFormat ==
          MTLPixelFormat(Metal::VkToMtl::PixelFormat(f.runtime.SupportedFormat(view_format, {}))));
    // The proxy was filled from the native image (down: the second texel of each 2x2 block).
    const auto filled = ReadTexture(f, proxy, bytes);
    CHECK(ProxyMatches(filled, 960, 540, first, 1920, 1080, bytes));
    // It was handed out for drawing: ahead of the native image, valid.
    CHECK(targets.ProxyCurrent(image));

    // The pass drew something: the proxy has other contents than the native image.
    const auto drawn = pattern(960, 540, 2);
    WriteTexture(f, proxy, bytes, drawn);
    // A native read resolves the proxy into the native image (up: the texel each pixel lies in)
    // and leaves the proxy valid.
    f.runtime.Transit(&image, vk::ImageLayout::eShaderReadOnlyOptimal,
                      vk::PipelineStageFlagBits2::eFragmentShader,
                      vk::AccessFlagBits2::eShaderRead);
    CHECK(targets.ProxyCurrent(image));
    const auto resolved = ReadTexture(f, native, bytes);
    CHECK(ProxyMatches(resolved, 1920, 1080, drawn, 960, 540, bytes));
    // (Nothing is dirty now: a second read does not copy again, the contents are the same.)
    f.runtime.Transit(&image, vk::ImageLayout::eShaderReadOnlyOptimal,
                      vk::PipelineStageFlagBits2::eFragmentShader,
                      vk::AccessFlagBits2::eShaderRead);
    CHECK(ReadTexture(f, native, bytes) == resolved);

    // A native write invalidates the proxy; the next attachment fills it again.
    f.runtime.Transit(&image, vk::ImageLayout::eGeneral,
                      vk::PipelineStageFlagBits2::eComputeShader,
                      vk::AccessFlagBits2::eShaderWrite);
    CHECK(!targets.ProxyCurrent(image));
    const auto second = pattern(1920, 1080, 3);
    WriteTexture(f, native, bytes, second);
    const auto again = targets.Attachment(image_id, info);
    CHECK(again.view == target.view); // the same texture and view
    CHECK(ProxyMatches(ReadTexture(f, TextureOf(again.view), bytes), 960, 540, second, 1920, 1080,
                       bytes));

    // Sampling: the proxy of a current image comes back as a view with the view's format.
    const auto sampled = targets.SampleProxy(image, info);
    CHECK(sampled && sampled->view == again.view);

    // SetSize drops the proxies (after resolving the dirty ones: here the attachment is dirty).
    WriteTexture(f, TextureOf(again.view), bytes, drawn);
    const u64 generation = targets.Generation();
    CHECK(targets.SetSize({1280, 720}));
    CHECK(targets.Generation() != generation);
    CHECK(!targets.Tracks(image.image_uid));
    CHECK(ProxyMatches(ReadTexture(f, native, bytes), 1920, 1080, drawn, 960, 540, bytes));
    Settle(f);
}

/// The level of a half resolution mip chain has a proxy of its own: 480x270 native level 1 into a
/// 240x135 proxy (scene 960x540: the half resolution targets are 480x270).
static void TestMipChain(Fixture& f) {
    std::printf("half resolution mip chain\n");
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    const ImageId image_id = world.Add(MakeInfo(vk::Format::eR16G16B16A16Sfloat, 960, 540, 3));
    Image& image = world.Get(image_id);
    id<MTLTexture> native = NativeOf(image);
    CHECK(native.mipmapLevelCount == 3);
    CHECK(targets.SetSize({960, 540}));
    CHECK(targets.Eligible(image) && !targets.EligibleScene(image));
    CHECK(targets.ProxySize(image, 0).width == 480);
    CHECK(targets.ProxySize(image, 1).width == 240 && targets.ProxySize(image, 1).height == 135);

    const auto level1 = HalfPattern(480, 270, 5);
    WriteTexture(f, native, 8, level1, 1);
    ImageViewInfo info;
    info.format = vk::Format::eR16G16B16A16Sfloat;
    info.range = {{1, 0}, {1, 1}};
    const auto target = targets.Attachment(image_id, info);
    id<MTLTexture> proxy = TextureOf(target.view);
    CHECK(proxy.width == 240 && proxy.height == 135);
    CHECK(ProxyMatches(ReadTexture(f, proxy, 8), 240, 135, level1, 480, 270, 8));
    CHECK(targets.ProxyCurrent(image, 1, 1) && !targets.ProxyCurrent(image, 0, 1));
    const auto drawn = HalfPattern(240, 135, 6);
    WriteTexture(f, proxy, 8, drawn);
    f.runtime.Transit(&image, vk::ImageLayout::eShaderReadOnlyOptimal,
                      vk::PipelineStageFlagBits2::eFragmentShader,
                      vk::AccessFlagBits2::eShaderRead);
    CHECK(ProxyMatches(ReadTexture(f, native, 8, 1), 480, 270, drawn, 240, 135, 8));
    Settle(f);
}

/// Depth with stencil: both aspects through the shader copy, [[stencil]] exported.
static void TestDepthStencil(Fixture& f) {
    std::printf("depth with stencil (D32F_S8)\n");
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    const ImageId image_id = world.Add(MakeInfo(vk::Format::eD32SfloatS8Uint, 1920, 1080));
    Image& image = world.Get(image_id);
    id<MTLTexture> native = NativeOf(image);
    CHECK(native.pixelFormat == MTLPixelFormatDepth32Float_Stencil8);
    CHECK(targets.SetSize({960, 540}));
    CHECK(targets.Eligible(image) && targets.EligibleScene(image));

    const auto depth_of = [](u32 width, u32 height, u32 seed) {
        std::vector<u8> data(size_t(width) * height * 4);
        for (u32 y = 0; y < height; ++y) {
            for (u32 x = 0; x < width; ++x) {
                const float value = float((x * 3 + y * 5 + seed * 11) % 4096) / 4096.0f;
                std::memcpy(&data[(size_t(y) * width + x) * 4], &value, 4);
            }
        }
        return data;
    };
    const auto first_depth = depth_of(1920, 1080, 1);
    const auto first_stencil = Pattern(1920, 1080, 1, 7);
    WriteTexture(f, native, 4, first_depth, 0, MTLBlitOptionDepthFromDepthStencil);
    WriteTexture(f, native, 1, first_stencil, 0, MTLBlitOptionStencilFromDepthStencil);

    ImageViewInfo info;
    info.format = vk::Format::eD32SfloatS8Uint;
    info.is_storage = true;
    const auto target = targets.Attachment(image_id, info);
    id<MTLTexture> proxy = TextureOf(target.view);
    CHECK(proxy.width == 960 && proxy.height == 540);
    CHECK(proxy.pixelFormat == MTLPixelFormatDepth32Float_Stencil8);
    CHECK(proxy.usage & MTLTextureUsageRenderTarget);
    CHECK(ProxyMatches(ReadTexture(f, proxy, 4, 0, MTLBlitOptionDepthFromDepthStencil), 960, 540,
                       first_depth, 1920, 1080, 4));
    CHECK(ProxyMatches(ReadTexture(f, proxy, 1, 0, MTLBlitOptionStencilFromDepthStencil), 960, 540,
                       first_stencil, 1920, 1080, 1));
    // A depth proxy is never sampled in place of the native image (the core's rule).
    CHECK(!targets.SampleProxy(image, info));

    const auto drawn_depth = depth_of(960, 540, 2);
    const auto drawn_stencil = Pattern(960, 540, 1, 8);
    WriteTexture(f, proxy, 4, drawn_depth, 0, MTLBlitOptionDepthFromDepthStencil);
    WriteTexture(f, proxy, 1, drawn_stencil, 0, MTLBlitOptionStencilFromDepthStencil);
    f.runtime.Transit(&image, vk::ImageLayout::eDepthStencilReadOnlyOptimal,
                      vk::PipelineStageFlagBits2::eFragmentShader,
                      vk::AccessFlagBits2::eShaderRead);
    CHECK(ProxyMatches(ReadTexture(f, native, 4, 0, MTLBlitOptionDepthFromDepthStencil), 1920, 1080,
                       drawn_depth, 960, 540, 4));
    CHECK(ProxyMatches(ReadTexture(f, native, 1, 0, MTLBlitOptionStencilFromDepthStencil), 1920,
                       1080, drawn_stencil, 960, 540, 1));
    Settle(f);
}

static void TestEligibility(Fixture& f) {
    std::printf("eligibility\n");
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    CHECK(targets.SetSize({960, 540}));
    using F = vk::Format;
    const auto eligible = [&](F format, u32 w = 1920, u32 h = 1080, u32 levels = 1) {
        const ImageId image_id = world.Add(MakeInfo(format, w, h, levels));
        return targets.Eligible(world.Get(image_id));
    };
    CHECK(eligible(F::eR8G8B8A8Unorm) && eligible(F::eR8G8B8A8Srgb) && eligible(F::eB8G8R8A8Unorm));
    CHECK(eligible(F::eR16G16B16A16Sfloat) && eligible(F::eR32Sfloat) &&
          eligible(F::eB10G11R11UfloatPack32) && eligible(F::eA2B10G10R10UnormPack32));
    CHECK(eligible(F::eR8Uint) && eligible(F::eR32Uint) && eligible(F::eR16G16B16A16Uint));
    CHECK(eligible(F::eR8Sint) && eligible(F::eR32Sint));
    CHECK(eligible(F::eD32Sfloat) && eligible(F::eD32SfloatS8Uint));
    CHECK(!eligible(F::eBc1RgbaUnormBlock)); // block compressed
    CHECK(!eligible(F::eE5B9G9R9UfloatPack32)); // not renderable on every GPU: left native
    CHECK(!eligible(F::eR8G8B8A8Unorm, 1280, 720));
    // Mip chains: half resolution colour only.
    CHECK(eligible(F::eR16G16B16A16Sfloat, 960, 540, 4));
    CHECK(!eligible(F::eR16G16B16A16Sfloat, 1920, 1080, 4));
    CHECK(!eligible(F::eD32Sfloat, 960, 540, 4));
    Settle(f);
}

/// The proxies and their views go through the deferred operations: nothing is released before
/// the command buffers that use them completed (the debug layer reports otherwise), also when
/// the size changes while copies are pending.
static void TestLeanProxyViews(Fixture& f, vk::Format format, vk::Format integer, u32 bytes) {
    std::printf("lean proxy views: %s, exact format promotion and old backing retirement\n",
                vk::to_string(format).c_str());
    const char* full = std::getenv("BB_MTL_FULL_USAGE");
    const char* lean = std::getenv("BB_MTL_PROXY_LEAN");
    const bool expected_lean = !(full && full[0] == '1') && !(lean && lean[0] == '0');
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    targets.SetSize({960, 540});
    const auto image_id = world.Add(MakeInfo(format, 1920, 1080));
    auto& image = world.Get(image_id);
    ImageViewInfo view{};
    view.format = format;
    view.range.extent.levels = view.range.extent.layers = 1;
    auto target = targets.Attachment(image_id, view, true);
    id<MTLTexture> old = TextureOf(target.view);
    __weak id<MTLTexture> retired = old;
    CHECK(bool(old.usage & MTLTextureUsagePixelFormatView) == !expected_lean);
    CHECK(!(old.usage & MTLTextureUsageShaderWrite));
    auto clear = [&](id<MTLTexture> texture, MTLClearColor color) {
        auto pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture = texture;
        pass.colorAttachments[0].loadAction = MTLLoadActionClear;
        pass.colorAttachments[0].storeAction = MTLStoreActionStore;
        pass.colorAttachments[0].clearColor = color;
        f.scheduler.RenderEncoder((__bridge const void*)pass);
        f.scheduler.EndRendering();
    };
    std::optional<SceneTargets::Target> bits;
    std::vector<u8> copied;
    u64 promoted_generation = 0;
    auto reinterpreted = view;
    reinterpreted.format = integer;
    @autoreleasepool {
    clear(old, MTLClearColorMake(0, 1, 0, 1)); // pending GPU write, no CPU settle before promotion
    const auto generation = targets.Generation();
    if (format == vk::Format::eR8G8B8A8Unorm) {
        auto srgb = view;
        srgb.format = vk::Format::eR8G8B8A8Srgb;
        const auto twin = targets.SampleProxy(image, srgb);
        CHECK(twin && TextureOf(twin->view).pixelFormat == MTLPixelFormatRGBA8Unorm_sRGB);
        CHECK(targets.Generation() == generation); // sRGB-only views need no promotion
    }
    bits = targets.SampleProxy(image, reinterpreted);
    CHECK(bits.has_value());
    CHECK(TextureOf(bits->view).usage & MTLTextureUsagePixelFormatView);
    CHECK(targets.Generation() == generation + (expected_lean ? 1 : 0));
    copied = ReadTexture(f, TextureOf(bits->view), bytes);
    const auto original = ReadTexture(f, old, bytes);
    CHECK(copied == original); // integer view checks the original bits, not float tolerance
    promoted_generation = targets.Generation();
    CHECK(targets.SampleProxy(image, reinterpreted)->view == bits->view);
    CHECK(targets.Generation() == promoted_generation);
    } // Drain test descriptors/readback command buffers before checking real destruction.
    old = nil;
    Settle(f);
    if (expected_lean) CHECK(retired == nil); // not a second retained backing or view cache

    target = targets.Attachment(image_id, view);
    clear(TextureOf(target.view), MTLClearColorMake(0, 0, 1, 1));
    const auto newest = ReadTexture(f, TextureOf(bits->view), bytes);
    CHECK(newest != copied); // a later write uses the new backing for every cached view
    CHECK(targets.Generation() == promoted_generation);
    targets.NativeAccess(image, vk::AccessFlagBits2::eTransferRead);
    CHECK(ProxyMatches(ReadTexture(f, NativeOf(image), bytes), 1920, 1080,
                       newest, 960, 540, bytes));
    Settle(f);
}

static void TestLifetime(Fixture& f) {
    std::printf("lifetime\n");
    World world{f};
    {
        SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
        const ImageId image_id = world.Add(MakeInfo(vk::Format::eR8G8B8A8Unorm, 1920, 1080));
        ImageViewInfo info;
        info.format = vk::Format::eR8G8B8A8Srgb; // a view of its own
        for (int i = 0; i < 4; ++i) {
            targets.SetSize(i % 2 ? SceneResolution::Size{960, 540} : SceneResolution::Size{1280, 720});
            const auto target = targets.Attachment(image_id, info);
            CHECK(target.view != 0);
            // The next SetSize resolves the dirty proxy and drops it with commands in flight.
        }
        CHECK(f.runtime.scene_targets == &targets);
    }
    CHECK(f.runtime.scene_targets == nullptr);
    Settle(f);
}

#include "video_core/renderer_metal/mtl_velocity_mirror.h"
static void TestGameVelocityMirror(Fixture& f) {
    std::printf("game velocity: depth/stencil seed, GPU coefficients, packing, camera fallback and retirement\n");
    World world{f};
    SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    CHECK(Metal::VelocityMirror::PackingBinding(0x34bc187c) == 0);
    CHECK(Metal::VelocityMirror::PackingBinding(0x749e4f9e) == 1);
    CHECK(Metal::VelocityMirror::PackingBinding(0xb25e4fae) == 1);
    CHECK(Metal::VelocityMirror::PackingBinding(0xe0305cef) == -1);
    auto texture = [&](MTLPixelFormat format, u32 w, u32 h, MTLTextureUsage usage) {
        auto* desc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
        desc.storageMode = MTLStorageModePrivate;
        desc.usage = usage;
        return [f.mtl newTextureWithDescriptor:desc];
    };
    auto color = texture(MTLPixelFormatRGBA8Unorm,8,4,MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead);
    auto motion = texture(MTLPixelFormatRG16Float,32,16,MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite);
    float coefficients[8] = {0,0,0,0,1,.5f,0,0};
    auto packing = [f.mtl newBufferWithBytes:coefficients length:sizeof(coefficients) options:MTLResourceStorageModeShared];
    auto changed = [f.mtl newBufferWithLength:8 options:MTLResourceStorageModeShared];
    auto camera = [&] {
        std::vector<u16> data(32*16*2);
        for (u32 i=0;i<32*16;++i) { data[2*i]=0x3c00; data[2*i+1]=0x4000; } // 1,2
        const auto* bytes = reinterpret_cast<const u8*>(data.data());
        WriteTexture(f,motion,4,{bytes,bytes+data.size()*2});
    };
    for (auto format : {MTLPixelFormatDepth32Float_Stencil8, MTLPixelFormatDepth16Unorm}) {
        auto ds = texture(format,16,8,MTLTextureUsageRenderTarget); // deliberately no PixelFormatView
        auto* clear = [MTLRenderPassDescriptor renderPassDescriptor];
        clear.depthAttachment.texture = ds;
        clear.depthAttachment.loadAction = MTLLoadActionClear;
        clear.depthAttachment.storeAction = MTLStoreActionStore;
        clear.depthAttachment.clearDepth = .75;
        if (format == MTLPixelFormatDepth32Float_Stencil8) {
            clear.stencilAttachment.texture = ds;
            clear.stencilAttachment.loadAction = MTLLoadActionClear;
            clear.stencilAttachment.storeAction = MTLStoreActionStore;
            clear.stencilAttachment.clearStencil = 7;
        }
        f.scheduler.RenderEncoder((__bridge const void*)clear);
        f.scheduler.EndWithBarrier();
        // Only the top-left guest render rectangle is live; the larger backing's border
        // must not be included in the scaled depth/stencil seed.
        std::vector<u8> raw_depth(16*8*(format==MTLPixelFormatDepth16Unorm?2:4));
        std::vector<u8> raw_stencil(16*8);
        for (u32 y=0;y<8;++y) for(u32 x=0;x<16;++x) {
            const bool inside=x<8 && y<4;
            if(format==MTLPixelFormatDepth16Unorm) {
                const u16 value=inside?49151:16384;
                std::memcpy(raw_depth.data()+(y*16+x)*2,&value,2);
            } else {
                const float value=inside?.75f:.25f;
                std::memcpy(raw_depth.data()+(y*16+x)*4,&value,4);
            }
            raw_stencil[y*16+x]=inside?7:3;
        }
        WriteTexture(f,ds,format==MTLPixelFormatDepth16Unorm?2:4,raw_depth,0,
                     format==MTLPixelFormatDepth16Unorm?MTLBlitOptionNone:MTLBlitOptionDepthFromDepthStencil);
        if(format==MTLPixelFormatDepth32Float_Stencil8)
            WriteTexture(f,ds,1,raw_stencil,0,MTLBlitOptionStencilFromDepthStencil);
        RenderState guest{};
        guest.width=8; guest.height=4; guest.num_layers=guest.num_color_attachments=1;
        guest.color_attachments[0].image_view=reinterpret_cast<u64>((__bridge void*)color);
        guest.depth_stencil_attachment.image_view=reinterpret_cast<u64>((__bridge void*)ds);
        guest.depth_stencil_attachment.has_depth=true;
        guest.depth_stencil_attachment.has_stencil=format==MTLPixelFormatDepth32Float_Stencil8;
        Metal::VelocityMirror mirror{f.device,f.scheduler,targets,true};
        CHECK(mirror.Enabled());
        RenderState output{};
        CHECK(!mirror.Prepare(guest,32,16,output)); // no current packing constants
        CHECK(!mirror.Capture((__bridge const void*)packing,28,4));
        CHECK(mirror.Capture((__bridge const void*)packing,16,8));
        // GPU mutation after capture cannot change the snapshotted packing values.
        f.scheduler.BlitEncoder();
        [f.scheduler.GetImpl().blit copyFromBuffer:changed sourceOffset:0 toBuffer:packing destinationOffset:16 size:8];
        f.scheduler.EndWithBarrier();
        CHECK(mirror.Prepare(guest,32,16,output));
        auto depth = (__bridge id<MTLTexture>)reinterpret_cast<void*>(output.depth_stencil_attachment.image_view);
        auto seed = ReadTexture(f,depth,format==MTLPixelFormatDepth16Unorm?2:4,0,
                               format==MTLPixelFormatDepth16Unorm?MTLBlitOptionNone:MTLBlitOptionDepthFromDepthStencil);
        for (u32 i=0;i<32*16;++i) {
            if (format==MTLPixelFormatDepth16Unorm) CHECK(reinterpret_cast<const u16*>(seed.data())[i] >= 49150);
            else CHECK(reinterpret_cast<const float*>(seed.data())[i] == .75f);
        }
        if (format==MTLPixelFormatDepth32Float_Stencil8) {
            const auto stencil=ReadTexture(f,depth,1,0,MTLBlitOptionStencilFromDepthStencil);
            for (u8 n:stencil) CHECK(n==7);
        }
        auto field=(__bridge id<MTLTexture>)reinterpret_cast<void*>(output.color_attachments[0].image_view);
        NSString* shader = @R"MSL(
#include <metal_stdlib>
using namespace metal;
vertex float4 velocity_v(uint i [[vertex_id]]) {
    const float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};
    return float4(p[i],.5,1);
}
fragment float4 velocity_f(float4 p [[position]]) {
    return float4(.75,.25,0,p.x<16?1.0:0.0);
}
)MSL";
        NSError* error=nil;
        auto library=[f.mtl newLibraryWithSource:shader options:nil error:&error];
        auto* descriptor=[MTLRenderPipelineDescriptor new];
        descriptor.vertexFunction=[library newFunctionWithName:@"velocity_v"];
        descriptor.fragmentFunction=[library newFunctionWithName:@"velocity_f"];
        descriptor.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
        descriptor.depthAttachmentPixelFormat=format;
        if(format==MTLPixelFormatDepth32Float_Stencil8) descriptor.stencilAttachmentPixelFormat=format;
        auto pipeline=[f.mtl newRenderPipelineStateWithDescriptor:descriptor error:&error];
        CHECK(pipeline!=nil);
        auto* depth_desc=[MTLDepthStencilDescriptor new];
        depth_desc.depthCompareFunction=MTLCompareFunctionLessEqual;
        if(format==MTLPixelFormatDepth32Float_Stencil8) {
            auto* stencil=[MTLStencilDescriptor new];
            stencil.stencilCompareFunction=MTLCompareFunctionEqual;
            depth_desc.frontFaceStencil=depth_desc.backFaceStencil=stencil;
        }
        auto depth_state=[f.mtl newDepthStencilStateWithDescriptor:depth_desc];
        auto* render=[MTLRenderPassDescriptor renderPassDescriptor];
        render.colorAttachments[0].texture=field;
        render.colorAttachments[0].loadAction=MTLLoadActionClear;
        render.colorAttachments[0].storeAction=MTLStoreActionStore;
        render.colorAttachments[0].clearColor=MTLClearColorMake(.5,.5,0,0);
        render.depthAttachment.texture=depth;
        render.depthAttachment.loadAction=MTLLoadActionLoad;
        render.depthAttachment.storeAction=MTLStoreActionStore;
        if(format==MTLPixelFormatDepth32Float_Stencil8) {
            render.stencilAttachment.texture=depth;
            render.stencilAttachment.loadAction=MTLLoadActionLoad;
            render.stencilAttachment.storeAction=MTLStoreActionStore;
        }
        f.scheduler.RenderEncoder((__bridge const void*)render,output,false);
        auto& commands=f.scheduler.Commands();
        commands.SetRenderPipelineState((__bridge void*)pipeline);
        commands.SetDepthStencilState((__bridge void*)depth_state);
        commands.SetStencilReference(7,7);
        Metal::RenderList::Viewport viewport{0,0,32,16,0,1};
        commands.SetViewports(&viewport,1);
        commands.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        f.scheduler.EndWithBarrier();
        camera();
        mirror.Drawn();
        mirror.Merge((__bridge const void*)motion,32,16);
        auto result=ReadTexture(f,motion,4);
        auto* half=reinterpret_cast<const u16*>(result.data());
        for(u32 y=0;y<16;++y) for(u32 x=0;x<32;++x) {
            u32 i=(y*32+x)*2;
            CHECK(half[i]==(x<16?0xc800:0x3c00)); // -8 or camera 1
            CHECK(half[i+1]==(x<16?0xc800:0x4000)); // -8 or camera 2
        }
        // New frame: stale mirror/coefficients must never overwrite camera-only motion.
        mirror.FrameStart(); camera();
        mirror.Merge((__bridge const void*)motion,32,16);
        result=ReadTexture(f,motion,4); half=reinterpret_cast<const u16*>(result.data());
        CHECK(half[0]==0x3c00 && half[1]==0x4000);
        // Zero packing is rejected on the GPU, preserving existing camera vectors.
        CHECK(mirror.Capture((__bridge const void*)packing,16,8));
        CHECK(mirror.Prepare(guest,32,16,output));
        mirror.Drawn(); mirror.Merge((__bridge const void*)motion,32,16);
        result=ReadTexture(f,motion,4); half=reinterpret_cast<const u16*>(result.data());
        CHECK(half[0]==0x3c00 && half[1]==0x4000);
        // Non-finite packing is also rejected without corrupting camera vectors.
        const u32 nan_coefficients[2]={0x7fc00000,0x3f800000};
        std::memcpy(static_cast<u8*>(packing.contents)+16,nan_coefficients,8);
        mirror.FrameStart();
        CHECK(mirror.Capture((__bridge const void*)packing,16,8));
        CHECK(mirror.Prepare(guest,32,16,output));
        mirror.Drawn(); mirror.Merge((__bridge const void*)motion,32,16);
        result=ReadTexture(f,motion,4); half=reinterpret_cast<const u16*>(result.data());
        CHECK(half[0]==0x3c00 && half[1]==0x4000);
        CHECK(!mirror.Prepare(guest,8192,8192,output)); // auxiliary budget fallback
        auto layered=guest; layered.num_layers=2;
        CHECK(!mirror.Prepare(layered,32,16,output));
        // Resize with encoded work still pending: no Finish in Prepare, deferred old resources.
        for (u32 width : {16u,32u,24u}) {
            mirror.FrameStart();
            CHECK(mirror.Capture((__bridge const void*)packing,16,8));
            CHECK(mirror.Prepare(guest,width,16,output));
        }
        Settle(f);
        std::memcpy(static_cast<u8*>(packing.contents)+16,coefficients+4,8);
    }
    // Tag follows each held/replayed pass: host stateful mirrors must not count as guest.
    std::atomic<unsigned> guest_passes=0;
    f.scheduler.SetVisibilityQuery([&](const void*) { ++guest_passes; return std::pair<void*,u64>{}; });
    auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=color;
    pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    RenderState state{}; state.width=8;state.height=4;
    f.scheduler.RenderEncoder((__bridge const void*)pass,state,false);
    f.scheduler.RenderEncoder((__bridge const void*)pass,state,false);
    f.scheduler.RenderEncoder((__bridge const void*)pass,state,true);
    f.scheduler.EndWithBarrier();
    CHECK(guest_passes==1);
    f.scheduler.SetVisibilityQuery({});
    Settle(f);
}

#include "video_core/renderer_metal/mtl_motion_blur.h"
#include "video_core/renderer_metal/mtl_occlusion.h"
static void TestMotionBlurCopy(Fixture& f) {
    std::printf("motion blur bypass: lean blit, alpha, scale, aliases and validation\n");
    World world{f}; SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};
    auto make=[&](u32 w,u32 h,MTLTextureUsage usage,MTLPixelFormat fmt=MTLPixelFormatRGBA8Unorm) {
        auto* d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:w height:h mipmapped:NO];
        d.storageMode=MTLStorageModePrivate;d.usage=usage;
        return [f.mtl newTextureWithDescriptor:d];
    };
    auto a=make(8,8,MTLTextureUsageRenderTarget|MTLTextureUsagePixelFormatView);
    auto b=make(8,8,MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead);
    std::vector<u8> data(8*8*4);
    for(u32 i=0;i<data.size();++i)data[i]=(i*31+7)&255;
    WriteTexture(f,a,4,data);
    unsigned queries=0;
    f.scheduler.SetVisibilityQuery([&](const void*){++queries;return std::pair<void*,u64>{};});
    auto copy=[&](id<MTLTexture> x,id<MTLTexture> y){return Metal::CopyWithoutMotionBlur(f.scheduler,targets,(__bridge const void*)x,(__bridge const void*)y);};
    CHECK(copy(a,b)==Metal::BlurCopy::Blit);
    CHECK(ReadTexture(f,b,4)==data); // all channels, including alpha, preserved exactly
    CHECK(copy(b,b)==Metal::BlurCopy::Identity);
    auto view=[a newTextureViewWithPixelFormat:a.pixelFormat];
    CHECK(copy(a,view)==Metal::BlurCopy::Alias);
    CHECK(copy(nil,b)==Metal::BlurCopy::Missing);
    auto different=make(8,8,MTLTextureUsageRenderTarget,MTLPixelFormatRGBA16Float);
    CHECK(copy(a,different)==Metal::BlurCopy::Format);
    auto large=make(16,16,MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead);
    CHECK(copy(a,large)==Metal::BlurCopy::Usage);
    CHECK(copy(b,large)==Metal::BlurCopy::Scale);
    const auto scaled=ReadTexture(f,large,4);
    for(u32 y=0;y<16;++y)for(u32 x=0;x<16;++x)for(u32 c=0;c<4;++c)
        CHECK(scaled[(y*16+x)*4+c]==data[((y/2)*8+x/2)*4+c]);
    CHECK(queries==0);
    f.scheduler.SetVisibilityQuery({}); Settle(f);
}

static void TestVelocityBatch(Fixture& f) {
    std::printf("velocity batches: 64 draws -> 1 pass, strict LESS seed, hazards, queries and retirement\n");
    World world{f};SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};
    auto make=[&](MTLPixelFormat format,MTLTextureUsage usage) {
        auto* d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:32 height:16 mipmapped:NO];
        d.storageMode=MTLStorageModePrivate;d.usage=usage;return [f.mtl newTextureWithDescriptor:d];
    };
    auto color=make(MTLPixelFormatRGBA8Unorm,MTLTextureUsageRenderTarget);
    auto depth=make(MTLPixelFormatDepth32Float_Stencil8,MTLTextureUsageRenderTarget);
    auto input=make(MTLPixelFormatRGBA16Float,MTLTextureUsageShaderRead);
    auto motion=make(MTLPixelFormatRG16Float,MTLTextureUsageShaderWrite|MTLTextureUsageShaderRead);
    std::vector<u16> raw(32*16*4);
    for(u32 i=0;i<32*16;++i){raw[4*i]=0x3a00;raw[4*i+1]=0x3400;raw[4*i+3]=0x3c00;}
    auto* bytes=reinterpret_cast<const u8*>(raw.data());WriteTexture(f,input,8,{bytes,bytes+raw.size()*2});
    auto* clear=[MTLRenderPassDescriptor renderPassDescriptor];
    clear.depthAttachment.texture=depth;clear.depthAttachment.loadAction=MTLLoadActionClear;
    clear.depthAttachment.storeAction=MTLStoreActionStore;clear.depthAttachment.clearDepth=.75;
    clear.stencilAttachment.texture=depth;clear.stencilAttachment.loadAction=MTLLoadActionClear;
    clear.stencilAttachment.storeAction=MTLStoreActionStore;clear.stencilAttachment.clearStencil=7;
    f.scheduler.RenderEncoder((__bridge const void*)clear);f.scheduler.EndWithBarrier();
    const float coefficients[2]={1,.5};
    auto packing=[f.mtl newBufferWithBytes:coefficients length:8 options:MTLResourceStorageModeShared];
    RenderState guest{};guest.width=32;guest.height=16;guest.num_layers=guest.num_color_attachments=1;
    guest.color_attachments[0].image_view=reinterpret_cast<u64>((__bridge void*)color);
    guest.depth_stencil_attachment.image_view=reinterpret_cast<u64>((__bridge void*)depth);
    guest.depth_stencil_attachment.has_depth=guest.depth_stencil_attachment.has_stencil=true;
    Metal::VelocityMirror mirror{f.device,f.scheduler,targets,true};
    CHECK(mirror.Capture((__bridge const void*)packing,0,8));
    RenderState state{};CHECK(mirror.Prepare(guest,32,16,state,42));
    NSString* source=@R"MSL(
#include <metal_stdlib>
using namespace metal;
vertex float4 batch_v(uint i [[vertex_id]],constant float& z [[buffer(0)]]) {
    const float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],z,1);
}
fragment float4 batch_f(float4 p [[position]],texture2d<float,access::read> t [[texture(0)]]) {return t.read(uint2(p.xy));}
)MSL";
    NSError* error=nil;auto lib=[f.mtl newLibraryWithSource:source options:nil error:&error];
    auto* pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:@"batch_v"];
    pd.fragmentFunction=[lib newFunctionWithName:@"batch_f"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA16Float;
    pd.depthAttachmentPixelFormat=pd.stencilAttachmentPixelFormat=MTLPixelFormatDepth32Float_Stencil8;
    auto pipeline=[f.mtl newRenderPipelineStateWithDescriptor:pd error:&error];CHECK(pipeline!=nil);
    auto* zd=[MTLDepthStencilDescriptor new];zd.depthCompareFunction=MTLCompareFunctionLess;zd.depthWriteEnabled=YES;
    auto* sd=[MTLStencilDescriptor new];sd.stencilCompareFunction=MTLCompareFunctionEqual;
    zd.frontFaceStencil=zd.backFaceStencil=sd;auto zs=[f.mtl newDepthStencilStateWithDescriptor:zd];
    unsigned queries=0;f.scheduler.SetVisibilityQuery([&](const void*){++queries;return std::pair<void*,u64>{};});
    auto open_guest=[&] {
        auto* p=[MTLRenderPassDescriptor renderPassDescriptor];p.colorAttachments[0].texture=color;
        p.colorAttachments[0].loadAction=MTLLoadActionClear;p.colorAttachments[0].storeAction=MTLStoreActionStore;
        p.depthAttachment.texture=depth;p.depthAttachment.loadAction=MTLLoadActionClear;
        p.depthAttachment.storeAction=MTLStoreActionStore;p.depthAttachment.clearDepth=.1;
        p.stencilAttachment.texture=depth;p.stencilAttachment.loadAction=MTLLoadActionLoad;p.stencilAttachment.storeAction=MTLStoreActionStore;
        f.scheduler.RenderEncoder((__bridge const void*)p,guest);
    };
    auto queue=[&](u32 count) {
        for(u32 i=0;i<count;++i) {
            auto& list=mirror.Record(state);
            list.SetRenderPipelineState((__bridge void*)pipeline);list.SetDepthStencilState((__bridge void*)zs);
            list.SetStencilReference(7,7);const float z=.5f-float(i)/256;
            list.SetBytes(Metal::RenderList::Stage::Vertex,&z,4,0);
            list.SetTexture(Metal::RenderList::Stage::Fragment,(__bridge void*)input,0);
            Metal::RenderList::Viewport vp{0,0,32,16,0,1};list.SetViewports(&vp,1);
            list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);mirror.QueuedDraw();
        }
    };
    open_guest();queue(64);
    CHECK(mirror.PendingDraws()==64 && mirror.BatchCount()==0);
    // Starting a blit MUST replay the pending read-only draws first, before input is overwritten.
    auto zeros=[f.mtl newBufferWithLength:32*16*8 options:MTLResourceStorageModeShared];
    f.scheduler.BlitEncoder();
    CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==1);
    [f.scheduler.GetImpl().blit copyFromBuffer:zeros sourceOffset:0 sourceBytesPerRow:32*8 sourceBytesPerImage:32*16*8
                                 sourceSize:MTLSizeMake(32,16,1) toTexture:input destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
    f.scheduler.EndWithBarrier();mirror.Merge((__bridge const void*)motion,32,16);
    const auto result=ReadTexture(f,motion,4);const auto* half=reinterpret_cast<const u16*>(result.data());
    for(u32 i=0;i<32*16;++i)CHECK(half[2*i]==0xc800 && half[2*i+1]==0xc800);
    CHECK(queries==1); // neither mirror nor seed/merge contributes to visibility counters
    state.color_attachments[0].is_clear=0;
    open_guest();queue(1);f.scheduler.MemoryBarrier();CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==2);
    open_guest();queue(1);f.scheduler.BeginHoist();CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==3);f.scheduler.EndHoist();
    open_guest();queue(1);mirror.FrameStart();CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==4);
    // Prepare with new dimensions drains the old batch before retiring its textures.
    CHECK(mirror.Capture((__bridge const void*)packing,0,8));CHECK(mirror.Prepare(guest,32,16,state,42));
    open_guest();queue(1);CHECK(mirror.Prepare(guest,16,8,state,42));CHECK(mirror.BatchCount()==5);
    open_guest();queue(1);f.scheduler.Flush();CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==6);
    open_guest();queue(Metal::VelocityMirror::MaxBatchDraws);
    CHECK(mirror.PendingDraws()==0 && mirror.BatchCount()==7); // bounded command storage
    f.scheduler.SetVisibilityQuery({});Settle(f);
    mirror.InvalidateDepthSeed();
    CHECK(mirror.Prepare(guest,16,8,state,42));
    auto reseeded=(__bridge id<MTLTexture>)reinterpret_cast<void*>(state.depth_stencil_attachment.image_view);
    const auto depth_after_clear=ReadTexture(f,reseeded,4,0,MTLBlitOptionDepthFromDepthStencil);
    CHECK(reinterpret_cast<const float*>(depth_after_clear.data())[0]==.1f);
    // Actual ZPASS snapshots: a completely empty guest pass stays hidden after three zeros,
    // even while each interval contains 64 fully visible host mirror triangles.
    VideoCore::Buffer counters{f.runtime,0,4096,VideoCore::MemoryType::HostCached,"Batched visibility test"};
    Metal::OcclusionQueries occlusion{f.device,f.scheduler,f.runtime};
    for (u32 interval=0;interval<3;++interval) {
        clear.depthAttachment.clearDepth=1;
        f.scheduler.RenderEncoder((__bridge const void*)clear);f.scheduler.EndWithBarrier();
        mirror.FrameStart();CHECK(mirror.Capture((__bridge const void*)packing,0,8));
        CHECK(mirror.Prepare(guest,32,16,state,99));
        occlusion.Snapshot(&counters,0,0xa100,1);
        open_guest();queue(64);
        occlusion.Snapshot(&counters,8,0xa108,1);
        Settle(f);
        const auto* words=reinterpret_cast<const u64*>(counters.mapped_data.data());
        CHECK((words[0]>>63) && (words[1]>>63));
        CHECK((words[1]&~(1ull<<63))-(words[0]&~(1ull<<63))==(interval==2?0:1));
    }
    Settle(f);
}

static void TestFullColorClear(Fixture& f) {
    std::printf("full color clear avoids old-content fill; loading path preserves contents\n");
    World world{f};
    const auto id=world.Add(MakeInfo(vk::Format::eR8G8B8A8Unorm,1920,1080));
    auto& image=world.Get(id);
    SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};
    targets.SetSize({960,540});
    ImageViewInfo view{};view.format=vk::Format::eR8G8B8A8Unorm;
    view.range.extent.levels=view.range.extent.layers=1;
    const auto target=targets.Attachment(id,view,true);
    auto proxy=TextureOf(target.view);
    const char* disabled=std::getenv("BB_MTL_CLEAR_DISCARD");
    CHECK(targets.SkippedClearFills()==((disabled && disabled[0]=='0')?0:1));
    auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture=proxy;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    pass.colorAttachments[0].clearColor=MTLClearColorMake(0,1,0,1);
    f.scheduler.RenderEncoder((__bridge const void*)pass);
    [f.scheduler.GetImpl().render setScissorRect:MTLScissorRect{0,0,1,1}];
    f.scheduler.EndWithBarrier();
    auto data=ReadTexture(f,proxy,4);
    for(size_t i=0;i<data.size();i+=4) CHECK(data[i]==0 && data[i+1]==255 && data[i+2]==0 && data[i+3]==255);
    targets.NativeAccess(image,vk::AccessFlagBits2::eShaderRead);
    data=ReadTexture(f,NativeOf(image),4);
    for(size_t i=0;i<data.size();i+=4) CHECK(data[i]==0 && data[i+1]==255 && data[i+2]==0 && data[i+3]==255);
    targets.NativeAccess(image,vk::AccessFlagBits2::eShaderWrite);
    const std::vector<u8> original(1920*1080*4,0x35);
    WriteTexture(f,NativeOf(image),4,original);
    const auto load=targets.Attachment(id,view);
    data=ReadTexture(f,TextureOf(load.view),4);
    for(const auto b:data) CHECK(b==0x35);
    if (const char* control=std::getenv("BB_MTL_CLEAR_DISCARD_FILE")) {
        const auto skipped=targets.SkippedClearFills();
        auto set_mode=[&](char value) { FILE* file=std::fopen(control,"w");CHECK(file!=nullptr);if(file){std::fputc(value,file);std::fclose(file);}std::this_thread::sleep_for(std::chrono::milliseconds(1100)); };
        // Change policy on the same live proxy; no cache reset or object recreation.
        targets.NativeAccess(image,vk::AccessFlagBits2::eShaderWrite);
        set_mode('0');
        auto loading=targets.Attachment(id,view,true);
        CHECK(targets.SkippedClearFills()==skipped);
        data=ReadTexture(f,TextureOf(loading.view),4);
        for(const auto b:data) CHECK(b==0x35);
        targets.NativeAccess(image,vk::AccessFlagBits2::eShaderWrite);
        set_mode('1');
        auto clearing=targets.Attachment(id,view,true);
        CHECK(targets.SkippedClearFills()==skipped+1);
        pass.colorAttachments[0].texture=TextureOf(clearing.view);
        f.scheduler.RenderEncoder((__bridge const void*)pass);f.scheduler.EndWithBarrier();
        data=ReadTexture(f,TextureOf(clearing.view),4);
        for(size_t i=0;i<data.size();i+=4) CHECK(data[i]==0 && data[i+1]==255 && data[i+2]==0 && data[i+3]==255);
    }
    Settle(f);
}

static void TestFullCopyOverwrite(Fixture& f) {
    std::printf("native full copy skips obsolete resolve; partial copy preserves untouched pixels\n");
    World world{f};
    auto info=MakeInfo(vk::Format::eR8G8B8A8Unorm,1920,1080);
    const auto dst=world.Add(info),src=world.Add(info);
    auto& target=world.Get(dst);auto& source=world.Get(src);
    SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};targets.SetSize({960,540});
    const auto previous=f.runtime.scene_targets;f.runtime.scene_targets=&targets;
    ImageViewInfo view{};view.format=info.pixel_format;view.range.extent.levels=view.range.extent.layers=1;
    auto clear_proxy=[&] {
        const auto a=targets.Attachment(dst,view);
        auto* pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=TextureOf(a.view);pass.colorAttachments[0].loadAction=MTLLoadActionClear;
        pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        pass.colorAttachments[0].clearColor=MTLClearColorMake(0,1,0,1);
        f.scheduler.RenderEncoder((__bridge const void*)pass);f.scheduler.EndWithBarrier();
    };
    WriteTexture(f,NativeOf(source),4,std::vector<u8>(1920*1080*4,0x35));
    clear_proxy();CHECK(targets.ProxyDirty(target,0));
    // The source is a validated full image; old green pixels will all be replaced by 0x35.
    f.runtime.CopyImage(&source,&target);
    const char* env=std::getenv("BB_MTL_OVERWRITE_DISCARD");const bool enabled=!env||env[0]!='0';
    CHECK(targets.DiscardedCopyResolves()==(enabled?1:0));
    auto data=ReadTexture(f,NativeOf(target),4);for(auto b:data) CHECK(b==0x35);
    const auto load=targets.Attachment(dst,view);
    data=ReadTexture(f,TextureOf(load.view),4);for(auto b:data) CHECK(b==0x35);
    // Same texture/view aliases and dimension mismatches must never discard dirty state.
    clear_proxy();
    targets.FullCopyOverwrite(target,(__bridge const void*)NativeOf(target),(__bridge const void*)NativeOf(target));
    CHECK(targets.ProxyDirty(target,0));
    const auto small_id=world.Add(MakeInfo(vk::Format::eR8G8B8A8Unorm,960,540));auto& small=world.Get(small_id);
    WriteTexture(f,NativeOf(small),4,std::vector<u8>(960*540*4,0x77));
    f.runtime.CopyImage(&small,&target);
    CHECK(targets.DiscardedCopyResolves()==(enabled?1:0));
    data=ReadTexture(f,NativeOf(target),4);
    for(unsigned y=0;y<1080;++y) for(unsigned x=0;x<1920;++x) {
        const auto* pixel=data.data()+(y*1920+x)*4;
        if(x<960 && y<540) {for(unsigned c=0;c<4;++c) CHECK(pixel[c]==0x77);}
        else {CHECK(pixel[0]==0 && pixel[1]==255 && pixel[2]==0 && pixel[3]==255);}
    }
    // Unsupported source format must not invalidate a live proxy or throw away its result.
    clear_proxy();
    const auto bad_id=world.Add(MakeInfo(vk::Format::eR16G16B16A16Sfloat,1920,1080));auto& bad=world.Get(bad_id);
    f.runtime.CopyImage(&bad,&target);CHECK(targets.ProxyDirty(target,0));
    CHECK(targets.DiscardedCopyResolves()==(enabled?1:0));
    if(const char* control=std::getenv("BB_MTL_OVERWRITE_DISCARD_FILE")) {
        const auto skipped=targets.DiscardedCopyResolves();
        auto set=[&](char mode) {FILE* file=std::fopen(control,"w");CHECK(file!=nullptr);if(file){std::fputc(mode,file);std::fclose(file);}std::this_thread::sleep_for(std::chrono::milliseconds(1100));};
        set('0');f.runtime.CopyImage(&source,&target);CHECK(targets.DiscardedCopyResolves()==skipped);
        clear_proxy();set('1');f.runtime.CopyImage(&source,&target);CHECK(targets.DiscardedCopyResolves()==skipped+1);
        data=ReadTexture(f,NativeOf(target),4);for(auto b:data) CHECK(b==0x35);
    }
    f.runtime.scene_targets=previous;Settle(f);
}

static void TestDirectProxyClear(Fixture& f) {
    std::printf("direct proxy color clear matches native pixels and lazy readers across formats\n");
    World world{f};SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};targets.SetSize({960,540});
    const auto previous=f.runtime.scene_targets;f.runtime.scene_targets=&targets;
    const char* disabled=std::getenv("BB_MTL_PROXY_CLEAR");const bool enabled=!disabled||disabled[0]!='0';
    const std::array<std::pair<vk::Format,unsigned>,6> formats{{
        {vk::Format::eR8G8B8A8Unorm,4},{vk::Format::eR8G8B8A8Srgb,4},{vk::Format::eB8G8R8A8Unorm,4},
        {vk::Format::eR32Uint,4},{vk::Format::eR32Sint,4},{vk::Format::eR16G16B16A16Sfloat,8}}};
    VideoCore::SubresourceRange range{};range.extent.levels=range.extent.layers=1;
    for(const auto [format,bpp]:formats) {
        const auto dst=world.Add(MakeInfo(format,1920,1080));auto& image=world.Get(dst);
        const auto reference=world.Add(MakeInfo(format,1920,1080));auto& ref=world.Get(reference);
        ImageViewInfo view{};view.format=format;view.range=range;
        WriteTexture(f,NativeOf(image),bpp,std::vector<u8>(1920*1080*bpp,0x35));
        targets.Attachment(dst,view); // a dirty proxy whose old pixels will be discarded
        vk::ClearValue value{};
        if(format==vk::Format::eR32Uint) value.color.uint32[0]=29;
        else if(format==vk::Format::eR32Sint) value.color.int32[0]=-29;
        else {const float color[4]={.25f,.5f,.75f,1.f};for(unsigned c=0;c<4;++c)value.color.float32[c]=color[c];}
        const auto before=targets.ProxyClearCount();
        f.runtime.ClearImage(&ref,range,value);f.runtime.ClearImage(&image,range,value);
        CHECK(targets.ProxyClearCount()==before+(enabled?1:0));
        if(enabled) CHECK(targets.ProxyDirty(image,0));
        const auto expected=ReadTexture(f,NativeOf(ref),bpp);
        const auto proxy=targets.Attachment(dst,view);const auto actual=ReadTexture(f,TextureOf(proxy.view),bpp);
        for(size_t offset=0;offset<actual.size();offset+=bpp)
            for(unsigned c=0;c<bpp;++c) CHECK(actual[offset+c]==expected[c]);
        // GPU download/native reads must still materialize the correct full-size result.
        targets.NativeAccess(image,vk::AccessFlagBits2::eTransferRead);
        CHECK(ReadTexture(f,NativeOf(image),bpp)==expected);
        // Native storage writes invalidate and a later loading attachment must refill.
        targets.NativeAccess(image,vk::AccessFlagBits2::eShaderWrite);
        WriteTexture(f,NativeOf(image),bpp,std::vector<u8>(1920*1080*bpp,0x35));
        const auto loaded=targets.Attachment(dst,view);
        const auto loaded_pixels=ReadTexture(f,TextureOf(loaded.view),bpp);for(auto b:loaded_pixels) CHECK(b==0x35);
        auto partial=range;partial.base.level=1;CHECK(!targets.ClearProxy(image,partial,value));
    }
    // A mip-chain/depth clear must keep the original preservation path.
    auto mip_info=MakeInfo(vk::Format::eR8G8B8A8Unorm,960,540);mip_info.resources.levels=3;
    const auto mip=world.Add(mip_info);const auto depth=world.Add(MakeInfo(vk::Format::eD32SfloatS8Uint,1920,1080));
    vk::ClearValue zero{};CHECK(!targets.ClearProxy(world.Get(mip),range,zero));CHECK(!targets.ClearProxy(world.Get(depth),range,zero));
    if(const char* control=std::getenv("BB_MTL_PROXY_CLEAR_FILE")) {
        const auto id=world.Add(MakeInfo(vk::Format::eR8G8B8A8Unorm,1920,1080));auto& image=world.Get(id);
        ImageViewInfo view{};view.format=vk::Format::eR8G8B8A8Unorm;view.range=range;targets.Attachment(id,view);
        auto set=[&](char mode) {FILE* file=std::fopen(control,"w");CHECK(file!=nullptr);if(file){std::fputc(mode,file);std::fclose(file);}std::this_thread::sleep_for(std::chrono::milliseconds(1100));};
        const auto before=targets.ProxyClearCount();set('0');f.runtime.ClearImage(&image,range,zero);CHECK(targets.ProxyClearCount()==before);
        targets.Attachment(id,view);set('1');f.runtime.ClearImage(&image,range,zero);CHECK(targets.ProxyClearCount()==before+1);
        targets.NativeAccess(image,vk::AccessFlagBits2::eTransferRead);
        const auto bytes=ReadTexture(f,NativeOf(image),4);for(auto b:bytes) CHECK(b==0);
    }
    f.runtime.scene_targets=previous;Settle(f);
}

// Compare against the real old path: nearest render resolve + generated UPSCALE_MERGE_COMP.
// The pattern varies alpha at every texel and includes finite half subnormals/signs/extremes.
static void TestUpscaleAlphaFusion(Fixture& f) {
    std::printf("upscale alpha fusion: byte-exact old merge, preset edges, coherence, no new backing\n");
    auto* previous = f.runtime.scene_targets;
    World world{f}; SceneTargets targets{f.device, f.scheduler, f.runtime, world.Lookup()};
    auto id = world.Add(MakeInfo(vk::Format::eR16G16B16A16Sfloat, 1920, 1080));
    auto& image = world.Get(id);
    f.runtime.PromoteImageUsage(&image, ImageUsagePromotion::Storage);
    auto native = NativeOf(image);
    auto* descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float
        width:1920 height:1080 mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    descriptor.storageMode = MTLStorageModePrivate;
    auto rgb = [f.mtl newTextureWithDescriptor:descriptor];
    auto pattern = [](u32 w, u32 h, unsigned seed) {
        std::vector<u8> bytes(size_t(w) * h * 8);
        auto* halves = reinterpret_cast<u16*>(bytes.data());
        for (size_t i = 0; i < size_t(w) * h * 4; ++i) {
            const u16 magnitude = (i * 73 + (i / (w * 4)) * 157 + seed) % 0x7c00;
            halves[i] = magnitude | ((i & 1) ? 0x8000 : 0);
        }
        return bytes;
    };
    WriteTexture(f, rgb, 8, pattern(1920, 1080, 97));
    const auto& shader = Metal::HostShaders::UPSCALE_MERGE_COMP;
    auto function = f.runtime.GetImpl().kernels.Function(shader, nullptr, 0);
    NSError* error = nil;
    auto reference = [f.mtl newComputePipelineStateWithFunction:function error:&error];
    CHECK(reference && !error);
    std::array<u32, 5> slots{};
    for (const auto& binding : shader.bindings) {
        if ((binding.kind == Metal::HostShaders::Binding::Kind::Texture ||
             binding.kind == Metal::HostShaders::Binding::Kind::StorageTexture) &&
            binding.set == 0 && binding.binding < slots.size()) slots[binding.binding] = binding.msl_index;
    }
    const auto merge_reference = [&] {
        f.runtime.Transit(&image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
            vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite);
        f.runtime.FlushBarriers(); f.scheduler.ComputeEncoder();
        auto encoder = f.scheduler.GetImpl().compute;
        [encoder setComputePipelineState:reference];
        [encoder setTexture:rgb atIndex:slots[0]]; [encoder setTexture:native atIndex:slots[1]];
        for (unsigned i = 2; i < 5; ++i) [encoder setTexture:rgb atIndex:slots[i]];
        const u32 mode = 0; [encoder setBytes:&mode length:sizeof(mode) atIndex:0];
        [encoder dispatchThreadgroups:MTLSizeMake(240, 135, 1) threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    };
    VideoCore::ImageViewInfo view{}; view.format = image.info.pixel_format;
    const bool enabled = !std::getenv("BB_MTL_UPSCALE_ALPHA") || std::getenv("BB_MTL_UPSCALE_ALPHA")[0] != '0';
    // All five reduced presets, plus a non-divisible ratio to exercise pixel-centre ties.
    for (auto size : {SceneResolution::Size{1536, 864}, {1280, 720}, {1130, 636}, {960, 540}, {640, 360}, {726, 402}}) {
        targets.SetSize(size);
        auto proxy = TextureOf(targets.Attachment(id, view, true).view);
        auto alpha = pattern(size.width, size.height, 13);
        WriteTexture(f, proxy, 8, alpha);
        merge_reference();
        const auto expected = ReadTexture(f, native, 8);
        const auto expected_fill = ReadTexture(f, TextureOf(targets.Read(id, view).view), 8);
        // Reuse the exact allocation without native access or new storage.
        proxy = TextureOf(targets.Attachment(id, view, true).view);
        WriteTexture(f, proxy, 8, alpha);
        CHECK(targets.ProxyDirty(image, 0));
        const auto before = targets.UpscaleAlphaCount();
        // Invalid source/alias/dimensions must not discard dirty alpha.
        CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)native, (__bridge const void*)proxy));
        CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)proxy, (__bridge const void*)proxy));
        CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)rgb));
        CHECK(targets.ProxyDirty(image, 0));
        const auto resources_before = Metal::MemoryStats::Read();
        const bool fused = targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)proxy);
        CHECK(fused == enabled); CHECK(targets.UpscaleAlphaCount() == before + enabled);
        const auto resources_after = Metal::MemoryStats::Read();
        for (unsigned i = 0; i < resources_before.size(); ++i) {
            CHECK(resources_before[i].bytes == resources_after[i].bytes);
            CHECK(resources_before[i].objects == resources_after[i].objects);
        }
        if (!fused) merge_reference();
        CHECK(!targets.ProxyDirty(image, 0));
        CHECK(!targets.ProxyCurrent(image));
        CHECK(ReadTexture(f, native, 8) == expected);
        f.runtime.Transit(&image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eTransfer, vk::AccessFlagBits2::eTransferRead);
        CHECK(ReadTexture(f, native, 8) == expected); // no stale resolve on later native reader
        CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)proxy));
        auto filled = TextureOf(targets.Read(id, view).view);
        auto filled_bytes = ReadTexture(f, filled, 8);
        CHECK(filled_bytes == expected_fill); // same GPU nearest mapping, including float boundary ties
        CHECK(targets.ProxyCurrent(image));
        CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)filled)); // clean proxy
    }
    targets.SetSize({1920, 1080});
    CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)native));
    // Optional live A/B control: each switch revalidates coherence, no allocation or cache reset.
    if (const char* control = std::getenv("BB_MTL_UPSCALE_ALPHA_FILE")) {
        targets.SetSize({960, 540});
        auto proxy = TextureOf(targets.Attachment(id, view, true).view);
        WriteTexture(f, proxy, 8, pattern(960, 540, 7));
        const auto set = [&](char v) { FILE* file = std::fopen(control, "w"); CHECK(file); if(file) { std::fputc(v,file); std::fclose(file); } std::this_thread::sleep_for(std::chrono::milliseconds(1050)); };
        set('0'); CHECK(!targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)proxy)); CHECK(targets.ProxyDirty(image,0));
        set('1'); CHECK(targets.MergeUpscaleAlpha(image, (__bridge const void*)rgb, (__bridge const void*)proxy)); CHECK(!targets.ProxyDirty(image,0));
    }
    Settle(f); f.runtime.scene_targets = previous;
}


static void TestProxyTextureTiling(Fixture& f) {
    if(const char* v=std::getenv("BB_MTL_TEXTURE_TILING");!v || v[0]!='1')return;
    std::puts("proxy texture tiling: exact native-resolve equivalent, stale native retained and future reads coherent");
    const bool half_enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING_HALF");return v&&v[0]=='1';}();
    for(auto format:{vk::Format::eR8G8B8A8Unorm,vk::Format::eR16G16B16A16Sfloat}) {
    const unsigned bytes=vk::blockSize(format);if(bytes==8 && !half_enabled)continue;
    World world{f};SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};targets.SetSize({960,540});
    auto info=MakeInfo(format,1920,1080);info.guest_address=0x10000000;
    info.tile_mode=AmdGpu::TileMode::Thin2DThin;info.array_mode=AmdGpu::GetArrayMode(info.tile_mode);info.props.is_tiled=1;info.UpdateSize();
    const auto id=world.Add(info);auto& source=world.Get(id);
    ImageViewInfo view{};view.format=info.pixel_format;view.range.extent.levels=view.range.extent.layers=1;
    auto pattern=Pattern(960,540,bytes,71);const std::vector<u8> stale(1920*1080*bytes,0x35);
    if(bytes==8)for(size_t i=0;i<pattern.size()/2;++i) {const u16 word=u16(i);std::memcpy(pattern.data()+2*i,&word,2);}
    auto reset=[&] {
        f.runtime.Transit(&source,vk::ImageLayout::eTransferDstOptimal,vk::PipelineStageFlagBits2::eCopy,vk::AccessFlagBits2::eTransferWrite);f.runtime.FlushBarriers();
        WriteTexture(f,NativeOf(source),bytes,stale);
        auto proxy=TextureOf(targets.Attachment(id,view,true).view);WriteTexture(f,proxy,bytes,pattern);
        CHECK(targets.ProxyDirty(source,0));CHECK(targets.ExistingTilingProxy(source)==(__bridge const void*)proxy);
    };
    StreamBuffer stream{f.runtime,MemoryType::HostUncached,1<<20};TileManager manager{f.runtime,stream};
    Buffer reference{f.runtime,0,info.guest_size,MemoryType::DeviceLocal,"tiling reference"};
    Buffer actual{f.runtime,0,info.guest_size,MemoryType::DeviceLocal,"proxy tiling"};
    std::memset(reference.mapped_data.data(),0xa5,info.guest_size);std::memset(actual.mapped_data.data(),0xa5,info.guest_size);
    vk::BufferImageCopy copy{};copy.bufferRowLength=1920;copy.bufferImageHeight=1080;
    copy.imageSubresource={vk::ImageAspectFlagBits::eColor,0,0,1};copy.imageExtent={1920,1080,1};
    reset();targets.NativeAccess(source,vk::AccessFlagBits2::eTransferRead);
    const auto expected_native=ReadTexture(f,NativeOf(source),bytes);
    CHECK(!targets.ExistingTilingProxy(source));manager.TileImage(source,{&copy,1},&reference,0);Settle(f);
    reset();const auto before=Metal::MemoryStats::Read();
    manager.TileImage(source,{&copy,1},&actual,0);Settle(f);
    if(bytes==8) {
        unsigned mismatches=0;
        for(size_t i=0;i<info.guest_size/2;++i) {
            u16 a,b;std::memcpy(&a,actual.mapped_data.data()+2*i,2);std::memcpy(&b,reference.mapped_data.data()+2*i,2);
            if(a!=b) {if(mismatches<12)std::printf("half proxy mismatch actual=%04x expected=%04x\n",a,b);++mismatches;}
        }
        std::printf("half proxy bit mismatches=%u\n",mismatches);
    }
    CHECK(std::memcmp(actual.mapped_data.data(),reference.mapped_data.data(),info.guest_size)==0);
    CHECK(targets.ProxyDirty(source,0));CHECK(ReadTexture(f,NativeOf(source),bytes)==stale);
    const auto after=Metal::MemoryStats::Read();
    for(unsigned i=0;i<before.size();++i){CHECK(before[i].bytes==after[i].bytes);CHECK(before[i].objects==after[i].objects);}
    targets.NativeAccess(source,vk::AccessFlagBits2::eTransferRead);
    CHECK(!targets.ProxyDirty(source,0));CHECK(ReadTexture(f,NativeOf(source),bytes)==expected_native);
    reset();targets.SetSize({1280,720});targets.Attachment(id,view,true);
    CHECK(!targets.ExistingTilingProxy(source)); // every unproven ratio materializes native as before
    Settle(f);
    }
}

static void TestDirectCopyToNative(Fixture& f) {
    std::puts("direct source-proxy resolve into copy destination, exact pixels and lazy source coherence");
    const bool enabled=[] {const char* v=std::getenv("BB_MTL_DIRECT_COPY");return v&&v[0]=='1';}();
    for(auto format:{vk::Format::eR8G8B8A8Unorm,vk::Format::eR8G8B8A8Srgb,
                     vk::Format::eR16G16B16A16Sfloat,vk::Format::eR32Uint}) {
        World world{f};SceneTargets targets{f.device,f.scheduler,f.runtime,world.Lookup()};targets.SetSize({960,540});
        auto info=MakeInfo(format,1920,1080);info.guest_address=0x10000000;
        const auto src=world.Add(info);info.guest_address=0x20000000;const auto dst=world.Add(info);
        auto& source=world.Get(src);auto& dest=world.Get(dst);const auto bytes=vk::blockSize(format);
        ImageViewInfo view{};view.format=format;view.range.extent.levels=view.range.extent.layers=1;
        auto pattern=format==vk::Format::eR16G16B16A16Sfloat?HalfPattern(960,540,5):Pattern(960,540,bytes,5);
        auto reset=[&] {
            f.runtime.Transit(&source,vk::ImageLayout::eTransferDstOptimal,vk::PipelineStageFlagBits2::eCopy,vk::AccessFlagBits2::eTransferWrite);
            f.runtime.FlushBarriers();WriteTexture(f,NativeOf(source),bytes,std::vector<u8>(1920*1080*bytes,0x35));
            auto proxy=TextureOf(targets.Attachment(src,view,true).view);WriteTexture(f,proxy,bytes,pattern);
            auto dest_proxy=TextureOf(targets.Attachment(dst,view,true).view);WriteTexture(f,dest_proxy,bytes,Pattern(960,540,bytes,17));
            source.flags|=ImageFlagBits::GpuModified;dest.flags|=ImageFlagBits::Dirty;
            return proxy;
        };
        reset();targets.NativeAccess(source,vk::AccessFlagBits2::eTransferRead);f.runtime.CopyImage(&source,&dest);
        const auto expected=ReadTexture(f,NativeOf(dest),bytes);
        auto proxy=reset();const auto stale=ReadTexture(f,NativeOf(source),bytes);
        const auto count=targets.DirectCopyCount();const auto before=Metal::MemoryStats::Read();
        f.runtime.CopyImage(&source,&dest);const auto after=Metal::MemoryStats::Read();
        CHECK(targets.DirectCopyCount()==count+enabled);
        CHECK(targets.ProxyDirty(source,0)==enabled);CHECK(!targets.ProxyCurrent(dest));
        CHECK(bool(dest.flags&ImageFlagBits::GpuModified));CHECK(!bool(dest.flags&ImageFlagBits::Dirty));
        for(unsigned i=0;i<before.size();++i){CHECK(before[i].bytes==after[i].bytes);CHECK(before[i].objects==after[i].objects);}
        CHECK(ReadTexture(f,NativeOf(dest),bytes)==expected);
        if(enabled)CHECK(ReadTexture(f,NativeOf(source),bytes)==stale);
        targets.NativeAccess(source,vk::AccessFlagBits2::eTransferRead);CHECK(!targets.ProxyDirty(source,0));
        CHECK(ReadTexture(f,NativeOf(source),bytes)==expected); // later source native read is materialized
        reset();const auto unchanged=targets.DirectCopyCount();
        CHECK(!targets.CopyDirectToNative(source,source));CHECK(targets.ProxyDirty(source,0));
        dest.info.guest_address=source.info.guest_address;CHECK(!targets.CopyDirectToNative(source,dest));CHECK(targets.ProxyDirty(source,0));dest.info.guest_address=0x20000000;
        dest.info.size.width=960;CHECK(!targets.CopyDirectToNative(source,dest));CHECK(targets.ProxyDirty(source,0));dest.info.size.width=1920;
        dest.info.resources.levels=2;CHECK(!targets.CopyDirectToNative(source,dest));dest.info.resources.levels=1;
        CHECK(targets.DirectCopyCount()==unchanged);
        targets.SetSize({1280,720});proxy=TextureOf(targets.Attachment(src,view,true).view);
        CHECK(!targets.CopyDirectToNative(source,dest)); // unproven non-2x ratio stays on original path
        Settle(f);
    }
}

int main() {
    @autoreleasepool {
        Fixture f;
        if(std::getenv("BB_TEST_PROXY_TILING_ONLY")) {TestProxyTextureTiling(f);Settle(f);return failures?1:0;}
        TestProxyTextureTiling(f);
        if(std::getenv("BB_TEST_DIRECT_COPY_ONLY")) {TestDirectCopyToNative(f);Settle(f);return failures?1:0;}
        TestDirectCopyToNative(f);
        TestColor(f, vk::Format::eR8G8B8A8Unorm, 4, vk::Format::eR8G8B8A8Unorm);
        TestColor(f, vk::Format::eR8G8B8A8Unorm, 4, vk::Format::eR8G8B8A8Srgb);
        TestColor(f, vk::Format::eR16G16B16A16Sfloat, 8, vk::Format::eR16G16B16A16Sfloat);
        TestColor(f, vk::Format::eR32Uint, 4, vk::Format::eR32Uint);
        TestMipChain(f);
        TestDepthStencil(f);
        TestEligibility(f);
        TestLifetime(f);
        TestLeanProxyViews(f, vk::Format::eR8G8B8A8Unorm, vk::Format::eR8G8B8A8Uint, 4);
        TestLeanProxyViews(f, vk::Format::eR16G16B16A16Sfloat, vk::Format::eR16G16B16A16Uint, 8);
        TestFullColorClear(f);
        TestFullCopyOverwrite(f);
        TestDirectProxyClear(f);
        TestUpscaleAlphaFusion(f);
        TestGameVelocityMirror(f);
        TestMotionBlurCopy(f);
        TestVelocityBatch(f);
        Settle(f);
        CHECK(!f.scheduler.HasFailed());
    }
    std::printf(failures ? "%d check(s) FAILED\n" : "all checks passed\n", failures);
    return failures ? 1 : 0;
}
