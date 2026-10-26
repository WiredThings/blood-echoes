// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the transfers that convert (renderer_metal/mtl_format_convert, called by
// Metal::Runtime): the guest formats Metal has no texture for, through the runtime's own entry
// points (UploadImage, DownloadImage, ClearImage). Per format: upload known guest bytes (random
// bits, 2 mips, 2 layers, an odd width of 37, row pitches and image heights with padding, buffer
// offsets that are not aligned), read the texture back in a compute kernel and check what the
// shader would see, download and compare with the guest bytes.
//
//  - RGB8/RGB16/RGB32 (every format Metal holds as RGBA): the texture holds the three channels
//    and the format's one in alpha (checked on the raw bits through an unsigned integer view and,
//    for the formats with float reads, as a float4 whose alpha must be 1.0); a download is byte
//    exact (the padding of rows and images is not touched). ClearImage keeps alpha at one.
//  - D24UnormS8Uint, D16UnormS8Uint, X8D24UnormPack32: depth as float32 code / (2^n - 1) (exactly
//    what a CPU division gives), the stencil of the combined formats through the stencil aspect.
//    The 8 unused bits of the 4 byte D24 texel (the X of X8D24 and the top byte of the depth
//    aspect of D24S8) carry no data: the upload ignores them, the download writes zero, and the
//    comparison does not look at them. Exhaustive cases round trip every code of the format
//    (16 777 216 codes of D24, 65 536 of D16), compared in the texture and in the download.
//
// Needs a Metal device, no window or game; run it with MTL_DEBUG_LAYER=1 as well.
//
//   mtl-format-convert-test
//
// Prints one line per case, then "N cases, M failed"; exit 0 when none failed.
#import <Metal/Metal.h>

#include <setjmp.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "common/slot_vector.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_format_convert.h"
#include "video_core/renderer_metal/mtl_runtime.h"
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
namespace FC = Metal::FormatConvert;

// ---- Fixture ------------------------------------------------------------------------------------

static int g_cases = 0;
static int g_failed = 0;
static int g_infra_failures = 0; // command buffer errors, outside any case

struct Fixture;
static Fixture* g_fixture = nullptr;
static void Settle(Fixture& f);
static void SettleGlobal();

struct Fixture {
    Metal::Device device;
    Scheduler scheduler{device, "test-format-convert"};
    Runtime runtime{device, scheduler};
    id<MTLDevice> mtl = device.GetImpl().device;

    Fixture() {
        g_fixture = this;
        scheduler.SetErrorHandler([](const std::string& report) {
            std::printf("  FAIL command buffer error: %s\n", report.c_str());
            ++g_infra_failures;
        });
    }
};

/// Finish and give Metal a moment to retire the command buffers (see test_mtl_runtime.mm: the
/// debug layer reports an object released between the event signal and the retirement).
static void Settle(Fixture& f) {
    f.runtime.Finish();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
}
static void SettleGlobal() {
    Settle(*g_fixture);
}

struct SettleDelete {
    void operator()(Buffer* buffer) const {
        SettleGlobal();
        delete buffer;
    }
};
using BufferPtr = std::unique_ptr<Buffer, SettleDelete>;

static BufferPtr MakeBuffer(Runtime& runtime, u64 size, MemoryType type, const char* name) {
    return BufferPtr{new Buffer(runtime, 0, size, type, name)};
}

struct Img {
    Common::SlotVector<ImageView> views;
    std::unique_ptr<Image> image;
    Img(Runtime& runtime, const ImageInfo& info)
        : image{std::make_unique<Image>(runtime, views, info)} {}
    ~Img() {
        SettleGlobal();
        image.reset();
    }
    id<MTLTexture> Texture() const {
        return (__bridge id<MTLTexture>)image->backing->image.native;
    }
};

/// The failures of one case: the first few are printed with it.
struct Report {
    int failures = 0;
    std::vector<std::string> notes;

    void Fail(const std::string& note) {
        ++failures;
        if (notes.size() < 6) {
            notes.push_back(note);
        }
    }
    void Check(bool condition, const std::string& note) {
        if (!condition) {
            Fail(note);
        }
    }
};

static void Finish(const std::string& name, Report& report, const std::string& detail = {}) {
    ++g_cases;
    if (report.failures) {
        ++g_failed;
    }
    std::printf("%-32s %s%s%s\n", name.c_str(), report.failures ? "FAILED" : "ok",
                detail.empty() ? "" : "  ", detail.c_str());
    for (const auto& note : report.notes) {
        std::printf("    %s\n", note.c_str());
    }
    if (report.failures > int(report.notes.size())) {
        std::printf("    ... %d failures in all\n", report.failures);
    }
}

// ---- Guest layout of a copy ---------------------------------------------------------------------

/// Where the texels of every mip and layer are in a guest buffer. With `padded` the rows are
/// 3 texels longer than the image, images are a row taller and the mips start at odd offsets:
/// what a guest image with a pitch and a mip chain looks like.
struct Layout {
    struct Mip {
        u32 width, height, row_length, image_height;
        u64 offset;
    };
    u32 texel_bytes, layers;
    bool padded;
    std::vector<Mip> mips;
    u64 total = 0;

    Layout(u32 texel_bytes_, u32 width, u32 height, u32 levels, u32 layers_, bool padded_)
        : texel_bytes{texel_bytes_}, layers{layers_}, padded{padded_} {
        u64 offset = padded ? 3 : 0;
        for (u32 m = 0; m < levels; ++m) {
            Mip mip{std::max(width >> m, 1u), std::max(height >> m, 1u), 0, 0, offset};
            mip.row_length = mip.width + (padded ? 3 : 0);
            mip.image_height = mip.height + (padded ? 1 : 0);
            mips.push_back(mip);
            offset += u64{layers} * mip.image_height * mip.row_length * texel_bytes +
                      (padded ? 5 : 0);
        }
        total = offset;
    }

    u64 TexelOffset(u32 mip, u32 layer, u32 y, u32 x) const {
        const Mip& m = mips[mip];
        return m.offset + ((u64{layer} * m.image_height + y) * m.row_length + x) * texel_bytes;
    }

    /// The copies of one aspect: a copy per mip, covering all layers.
    std::vector<vk::BufferImageCopy> Copies(vk::ImageAspectFlags aspect) const {
        std::vector<vk::BufferImageCopy> copies;
        for (u32 m = 0; m < mips.size(); ++m) {
            vk::BufferImageCopy copy{};
            copy.bufferOffset = mips[m].offset;
            copy.bufferRowLength = padded ? mips[m].row_length : 0;
            copy.bufferImageHeight = padded ? mips[m].image_height : 0;
            copy.imageSubresource.aspectMask = aspect;
            copy.imageSubresource.mipLevel = m;
            copy.imageSubresource.baseArrayLayer = 0;
            copy.imageSubresource.layerCount = layers;
            copy.imageExtent = {mips[m].width, mips[m].height, 1};
            copies.push_back(copy);
        }
        return copies;
    }

    /// Calls `f(mip, layer, y, x, offset)` for every texel.
    void ForEachTexel(const std::function<void(u32, u32, u32, u32, u64)>& f) const {
        for (u32 m = 0; m < mips.size(); ++m) {
            for (u32 l = 0; l < layers; ++l) {
                for (u32 y = 0; y < mips[m].height; ++y) {
                    for (u32 x = 0; x < mips[m].width; ++x) {
                        f(m, l, y, x, TexelOffset(m, l, y, x));
                    }
                }
            }
        }
    }
};

static std::vector<u8> RandomBytes(size_t size, u32 seed) {
    std::vector<u8> data(size);
    u32 state = seed * 2654435761u + 12345;
    for (auto& byte : data) {
        state = state * 1664525u + 1013904223u;
        byte = static_cast<u8>(state >> 24);
    }
    return data;
}

static u32 Load(const u8* p, u32 bytes) {
    u32 value = 0;
    for (u32 i = 0; i < bytes; ++i) {
        value |= u32{p[i]} << (8 * i);
    }
    return value;
}

static ImageInfo MakeInfo(vk::Format format, u32 width, u32 height, u32 levels, u32 layers,
                          bool depth, bool stencil) {
    ImageInfo info;
    info.pixel_format = format;
    info.type = AmdGpu::ImageType::Color2DArray;
    info.resources = {levels, layers};
    info.size = {width, height, 1};
    info.num_samples = 1;
    info.pitch = width;
    info.guest_address = 0x1000;
    info.guest_size = u64{width} * height * 16;
    info.props.is_block = false;
    info.props.is_depth = depth;
    info.props.has_stencil = stencil;
    info.num_bits = vk::blockSize(format) * 8;
    return info;
}

// ---- Kernels that read a texture back ------------------------------------------------------------

static NSString* const kernel_source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    // p = (lod, width, height, layers)
    "kernel void read_raw(texture2d_array<uint, access::read> t [[texture(0)]],\n"
    "                     device uint4* out [[buffer(0)]], constant uint4& p [[buffer(1)]],\n"
    "                     uint3 gid [[thread_position_in_grid]]) {\n"
    "    if (gid.x >= p.y || gid.y >= p.z || gid.z >= p.w) { return; }\n"
    "    out[(gid.z * p.z + gid.y) * p.y + gid.x] = t.read(gid.xy, gid.z, p.x);\n"
    "}\n"
    "kernel void read_float(texture2d_array<float, access::read> t [[texture(0)]],\n"
    "                       device float4* out [[buffer(0)]], constant uint4& p [[buffer(1)]],\n"
    "                       uint3 gid [[thread_position_in_grid]]) {\n"
    "    if (gid.x >= p.y || gid.y >= p.z || gid.z >= p.w) { return; }\n"
    "    out[(gid.z * p.z + gid.y) * p.y + gid.x] = t.read(gid.xy, gid.z, p.x);\n"
    "}\n"
    "kernel void read_depth(depth2d_array<float, access::read> t [[texture(0)]],\n"
    "                       device float* out [[buffer(0)]], constant uint4& p [[buffer(1)]],\n"
    "                       uint3 gid [[thread_position_in_grid]]) {\n"
    "    if (gid.x >= p.y || gid.y >= p.z || gid.z >= p.w) { return; }\n"
    "    out[(gid.z * p.z + gid.y) * p.y + gid.x] = t.read(gid.xy, gid.z, p.x);\n"
    "}\n"
    "kernel void read_stencil(texture2d_array<uint, access::read> t [[texture(0)]],\n"
    "                         device uint* out [[buffer(0)]], constant uint4& p [[buffer(1)]],\n"
    "                         uint3 gid [[thread_position_in_grid]]) {\n"
    "    if (gid.x >= p.y || gid.y >= p.z || gid.z >= p.w) { return; }\n"
    "    out[(gid.z * p.z + gid.y) * p.y + gid.x] = t.read(gid.xy, gid.z, p.x).x;\n"
    "}\n";

/// The kernels live for the whole run: nothing retains a pipeline for the GPU.
static id<MTLComputePipelineState> Pipeline(id<MTLDevice> device, NSString* name) {
    static NSMutableDictionary<NSString*, id<MTLComputePipelineState>>* cache =
        [NSMutableDictionary new];
    if (cache[name]) {
        return cache[name];
    }
    NSError* error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:kernel_source options:nil error:&error];
    if (!library) {
        std::printf("kernel library: %s\n", error.localizedDescription.UTF8String);
        std::exit(2);
    }
    id<MTLComputePipelineState> pipeline =
        [device newComputePipelineStateWithFunction:[library newFunctionWithName:name]
                                              error:&error];
    if (!pipeline) {
        std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
        std::exit(2);
    }
    cache[name] = pipeline;
    return pipeline;
}

/// Runs `kernel` over (width x height x layers) texels of level `lod` of `texture` and returns
/// `bytes_per_texel` bytes of output for each. Views stay alive until the GPU is done.
static std::vector<u8> ReadTexture(Fixture& f, id<MTLTexture> texture, NSString* kernel, u32 lod,
                                   u32 width, u32 height, u32 layers, u32 bytes_per_texel) {
    const u64 count = u64{width} * height * layers;
    auto out = MakeBuffer(f.runtime, std::max<u64>(count * bytes_per_texel, 16),
                          MemoryType::HostCached, "readback");
    std::memset(out->mapped_data.data(), 0xCD, out->mapped_data.size());
    const uint32_t params[4] = {lod, width, height, layers};
    f.scheduler.ComputeEncoder();
    auto& compute = f.scheduler.GetImpl().compute;
    [compute setComputePipelineState:Pipeline(f.mtl, kernel)];
    [compute setTexture:texture atIndex:0];
    [compute setBuffer:(__bridge id<MTLBuffer>)out->buffer.native offset:0 atIndex:0];
    [compute setBytes:params length:sizeof(params) atIndex:1];
    [compute dispatchThreads:MTLSizeMake(width, height, layers)
        threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    Settle(f);
    return {out->mapped_data.data(), out->mapped_data.data() + count * bytes_per_texel};
}

// ---- Upload, download, compare --------------------------------------------------------------------

static void Upload(Fixture& f, Image& image, const std::vector<u8>& data,
                   const std::vector<vk::BufferImageCopy>& copies) {
    const StagingSpan staging = f.runtime.StagingRequest(data.size(), MemoryType::HostUncached, 16);
    std::memcpy(staging.mapped, data.data(), data.size());
    std::vector<vk::BufferImageCopy> shifted = copies;
    for (auto& copy : shifted) {
        copy.bufferOffset += staging.offset;
    }
    f.runtime.UploadImage(&image, staging.buffer, shifted);
}

constexpr u8 Sentinel = 0xA5;

/// Downloads the copies into a buffer of `size` bytes that held Sentinel before.
static std::vector<u8> Download(Fixture& f, Image& image,
                                const std::vector<vk::BufferImageCopy>& copies, size_t size) {
    auto buffer = MakeBuffer(f.runtime, size, MemoryType::HostCached, "download");
    std::memset(buffer->mapped_data.data(), Sentinel, size);
    f.runtime.DownloadImage(&image, buffer.get(), copies);
    Settle(f);
    return {buffer->mapped_data.data(), buffer->mapped_data.data() + size};
}

/// Compares a download with the guest bytes: the bytes of every texel (but those `ignore` marks
/// as unused: a byte index into the texel, -1 none) must be the guest's, every other byte of the
/// buffer must still be the sentinel.
static void CompareDownload(Report& report, const std::string& what, const Layout& layout,
                            const std::vector<u8>& guest, const std::vector<u8>& got,
                            int ignore_byte) {
    std::vector<u8> texel_byte(guest.size(), 0);
    u64 bad_texels = 0;
    layout.ForEachTexel([&](u32 m, u32 l, u32 y, u32 x, u64 offset) {
        bool bad = false;
        for (u32 i = 0; i < layout.texel_bytes; ++i) {
            texel_byte[offset + i] = 1;
            if (int(i) == ignore_byte) {
                continue;
            }
            bad |= got[offset + i] != guest[offset + i];
        }
        if (bad) {
            if (bad_texels++ < 2) {
                report.Fail(what + ": download differs at mip " + std::to_string(m) + " layer " +
                            std::to_string(l) + " (" + std::to_string(x) + "," +
                            std::to_string(y) + ")");
            }
        }
    });
    if (bad_texels > 2) {
        report.Fail(what + ": " + std::to_string(bad_texels) + " texels differ in the download");
    } else if (bad_texels == 0) {
        u64 touched_padding = 0;
        for (size_t i = 0; i < got.size(); ++i) {
            touched_padding += !texel_byte[i] && got[i] != Sentinel;
        }
        report.Check(touched_padding == 0,
                     what + ": the download wrote " + std::to_string(touched_padding) +
                         " bytes outside the texels");
    }
}

// ---- RGB formats -------------------------------------------------------------------------------

/// The bits of one in a component of the format held as RGBA (written independently of the
/// converter: this is what a read of the 3-component Vulkan format returns for alpha).
static u32 OneBits(vk::Format format, u32 component_bytes) {
    const std::string numeric = vk::componentNumericFormat(format, 0);
    if (numeric == "UINT" || numeric == "SINT") {
        return 1;
    }
    if (component_bytes == 1) {
        return numeric == "SNORM" ? 0x7F : 0xFF;
    }
    if (component_bytes == 2) {
        return numeric == "SFLOAT" ? 0x3C00 : numeric == "SNORM" ? 0x7FFF : 0xFFFF;
    }
    return 0x3F800000; // SFLOAT, 32 bit
}

static MTLPixelFormat RawViewFormat(u32 component_bytes) {
    return component_bytes == 1   ? MTLPixelFormatRGBA8Uint
           : component_bytes == 2 ? MTLPixelFormatRGBA16Uint
                                  : MTLPixelFormatRGBA32Uint;
}

/// Checks the raw bits of every texel of every mip and layer of the texture against `expected`
/// (called with mip, layer, y, x, giving the 4 components).
static void CheckRaw(Fixture& f, Report& report, const std::string& what, Img& img,
                     const Layout& layout, u32 component_bytes,
                     const std::function<std::array<u32, 4>(u32, u32, u32, u32)>& expected) {
    id<MTLTexture> raw = [img.Texture() newTextureViewWithPixelFormat:RawViewFormat(component_bytes)];
    if (!raw) {
        report.Fail(what + ": no raw view of the texture");
        return;
    }
    for (u32 m = 0; m < layout.mips.size(); ++m) {
        const auto& mip = layout.mips[m];
        const auto bytes = ReadTexture(f, raw, @"read_raw", m, mip.width, mip.height, layout.layers,
                                       16);
        const u32* texels = reinterpret_cast<const u32*>(bytes.data());
        u64 bad = 0;
        for (u32 l = 0; l < layout.layers; ++l) {
            for (u32 y = 0; y < mip.height; ++y) {
                for (u32 x = 0; x < mip.width; ++x) {
                    const u32* got = texels + 4 * ((u64{l} * mip.height + y) * mip.width + x);
                    const auto want = expected(m, l, y, x);
                    if (std::memcmp(got, want.data(), 16) != 0 && bad++ < 2) {
                        char note[200];
                        std::snprintf(note, sizeof(note),
                                      "%s: mip %u layer %u (%u,%u) holds %x %x %x %x, wanted %x %x "
                                      "%x %x",
                                      what.c_str(), m, l, x, y, got[0], got[1], got[2], got[3],
                                      want[0], want[1], want[2], want[3]);
                        report.Fail(note);
                    }
                }
            }
        }
        if (bad > 2) {
            report.Fail(what + ": " + std::to_string(bad) + " texels differ in mip " +
                        std::to_string(m));
        }
    }
}

/// The alpha a shader reads from the texture as a float: one.
static void CheckFloatAlpha(Fixture& f, Report& report, const std::string& what, Img& img,
                            const Layout& layout) {
    for (u32 m = 0; m < layout.mips.size(); ++m) {
        const auto& mip = layout.mips[m];
        const auto bytes = ReadTexture(f, img.Texture(), @"read_float", m, mip.width, mip.height,
                                       layout.layers, 16);
        const float* texels = reinterpret_cast<const float*>(bytes.data());
        u64 bad = 0;
        for (u64 i = 0; i < u64{mip.width} * mip.height * layout.layers; ++i) {
            bad += texels[4 * i + 3] != 1.0f;
        }
        report.Check(bad == 0, what + ": alpha is not 1.0 in " + std::to_string(bad) +
                                   " texels of mip " + std::to_string(m));
    }
}

static void TestRgbFormat(Fixture& f, vk::Format format, u32 seed) {
    const std::string name = vk::to_string(format);
    Report report;
    const u32 texel = vk::blockSize(format);
    const u32 cb = texel / 3;
    const std::string numeric = vk::componentNumericFormat(format, 0);
    const bool is_int = numeric == "UINT" || numeric == "SINT";
    const u32 one = OneBits(format, cb);
    const Layout layout{texel, 37, 6, 2, 2, true};

    Img img{f.runtime, MakeInfo(format, 37, 6, 2, 2, false, false)};
    report.Check(img.image->backing->image.image_ci.format == FC::RgbaFormatOf(format),
                 "texture is not held as the RGBA format");

    auto guest = RandomBytes(layout.total, seed);
    // No NaN or infinity in the float formats (their bits would not survive a float read)
    if (numeric == "SFLOAT") {
        layout.ForEachTexel([&](u32, u32, u32, u32, u64 offset) {
            for (u32 c = 0; c < 3; ++c) {
                guest[offset + c * cb + cb - 1] &= 0xBF; // the top exponent bit
            }
        });
    }
    const auto component = [&](const Layout& l, const std::vector<u8>& data, u32 m, u32 layer,
                               u32 y, u32 x, u32 c) {
        return Load(data.data() + l.TexelOffset(m, layer, y, x) + c * cb, cb);
    };

    const auto copies = layout.Copies(vk::ImageAspectFlagBits::eColor);
    Upload(f, *img.image, guest, copies);
    const auto expected = [&](u32 m, u32 l, u32 y, u32 x) {
        return std::array<u32, 4>{component(layout, guest, m, l, y, x, 0),
                                  component(layout, guest, m, l, y, x, 1),
                                  component(layout, guest, m, l, y, x, 2), one};
    };
    CheckRaw(f, report, "upload", img, layout, cb, expected);
    if (!is_int) {
        CheckFloatAlpha(f, report, "upload", img, layout);
    }
    CompareDownload(report, "round trip", layout, guest,
                    Download(f, *img.image, copies, guest.size()), -1);

    // A second upload over the first (the texture was written): the new bytes replace the old
    auto guest2 = RandomBytes(layout.total, seed + 1000);
    if (numeric == "SFLOAT") {
        layout.ForEachTexel([&](u32, u32, u32, u32, u64 offset) {
            for (u32 c = 0; c < 3; ++c) {
                guest2[offset + c * cb + cb - 1] &= 0xBF;
            }
        });
    }
    Upload(f, *img.image, guest2, copies);
    CompareDownload(report, "second upload", layout, guest2,
                    Download(f, *img.image, copies, guest2.size()), -1);

    // ClearImage: integers to (5, 6, 7), others to zero; alpha stays one
    {
        const u32 values[3] = {is_int ? 5u : 0u, is_int ? 6u : 0u, is_int ? 7u : 0u};
        vk::ClearValue clear{};
        clear.color.uint32 = std::array<u32, 4>{values[0], values[1], values[2], is_int ? 9u : 0u};
        const SubresourceRange range{{0, 0}, {2, 2}};
        f.runtime.ClearImage(img.image.get(), range, clear);
        CheckRaw(f, report, "clear", img, layout, cb, [&](u32, u32, u32, u32) {
            return std::array<u32, 4>{values[0], values[1], values[2], one};
        });
        if (!is_int) {
            CheckFloatAlpha(f, report, "clear", img, layout);
        }
    }
    if (numeric == "UNORM") {
        vk::ClearValue clear{};
        clear.color.float32 = std::array<float, 4>{1.0f, 0.0f, 1.0f, 0.3f};
        f.runtime.ClearImage(img.image.get(), SubresourceRange{{0, 0}, {2, 2}}, clear);
        const u32 full = cb == 1 ? 0xFFu : 0xFFFFu;
        CheckRaw(f, report, "clear to color", img, layout, cb, [&](u32, u32, u32, u32) {
            return std::array<u32, 4>{full, 0u, full, one};
        });
    }
    Settle(f);
    Finish(name, report);
}

// ---- Packed depth -------------------------------------------------------------------------------

static void CheckDepth(Fixture& f, Report& report, const std::string& what, Img& img,
                       const Layout& layout, u32 depth_bits, const std::vector<u8>& guest) {
    const u32 max_code = (1u << depth_bits) - 1;
    const u32 mask = max_code;
    for (u32 m = 0; m < layout.mips.size(); ++m) {
        const auto& mip = layout.mips[m];
        const auto bytes = ReadTexture(f, img.Texture(), @"read_depth", m, mip.width, mip.height,
                                       layout.layers, 4);
        const float* depth = reinterpret_cast<const float*>(bytes.data());
        u64 bad = 0;
        for (u32 l = 0; l < layout.layers; ++l) {
            for (u32 y = 0; y < mip.height; ++y) {
                for (u32 x = 0; x < mip.width; ++x) {
                    const u32 code =
                        Load(guest.data() + layout.TexelOffset(m, l, y, x), layout.texel_bytes) &
                        mask;
                    const float want = float(code) / float(max_code);
                    const float got = depth[(u64{l} * mip.height + y) * mip.width + x];
                    if (std::memcmp(&got, &want, 4) != 0 && bad++ < 2) {
                        char note[160];
                        std::snprintf(note, sizeof(note),
                                      "%s: mip %u layer %u (%u,%u) code %u reads %.9g, wanted %.9g",
                                      what.c_str(), m, l, x, y, code, got, want);
                        report.Fail(note);
                    }
                }
            }
        }
        if (bad > 2) {
            report.Fail(what + ": " + std::to_string(bad) + " depth values differ in mip " +
                        std::to_string(m));
        }
    }
}

static void CheckStencil(Fixture& f, Report& report, const std::string& what, Img& img,
                         const Layout& layout, const std::vector<u8>& guest) {
    id<MTLTexture> stencil = [img.Texture() newTextureViewWithPixelFormat:MTLPixelFormatX32_Stencil8];
    if (!stencil) {
        report.Fail(what + ": no stencil view");
        return;
    }
    for (u32 m = 0; m < layout.mips.size(); ++m) {
        const auto& mip = layout.mips[m];
        const auto bytes = ReadTexture(f, stencil, @"read_stencil", m, mip.width, mip.height,
                                       layout.layers, 4);
        const u32* values = reinterpret_cast<const u32*>(bytes.data());
        u64 bad = 0;
        for (u32 l = 0; l < layout.layers; ++l) {
            for (u32 y = 0; y < mip.height; ++y) {
                for (u32 x = 0; x < mip.width; ++x) {
                    bad += values[(u64{l} * mip.height + y) * mip.width + x] !=
                           guest[layout.TexelOffset(m, l, y, x)];
                }
            }
        }
        report.Check(bad == 0, what + ": " + std::to_string(bad) + " stencil values differ in mip " +
                                   std::to_string(m));
    }
}

struct DepthSpec {
    vk::Format format;
    bool stencil;     ///< a combined format (the stencil aspect goes through its own copies)
    u32 depth_bits;
    u32 depth_bytes;  ///< bytes of the depth aspect in the guest layout
};

/// A depth guest buffer in which every texel is `code(index) | random unused bits`.
static std::vector<u8> DepthGuest(const Layout& layout, const DepthSpec& spec, u32 seed,
                                  bool exhaustive) {
    auto guest = RandomBytes(layout.total, seed);
    if (exhaustive) {
        u32 index = 0;
        layout.ForEachTexel([&](u32, u32, u32, u32, u64 offset) {
            for (u32 i = 0; i < spec.depth_bits / 8; ++i) {
                guest[offset + i] = u8(index >> (8 * i));
            }
            ++index;
        });
    }
    return guest;
}

static void TestDepthFormat(Fixture& f, const DepthSpec& spec, u32 seed, bool exhaustive) {
    const std::string name = std::string(vk::to_string(spec.format)) + (exhaustive ? " all codes" : "");
    Report report;
    // The exhaustive case has as many texels as the format has codes: (2 layers) x h x w.
    const u32 width = exhaustive ? (spec.depth_bits == 24 ? 4096 : 256) : 37;
    const u32 height = exhaustive ? (spec.depth_bits == 24 ? 2048 : 128) : 6;
    const u32 levels = exhaustive ? 1 : 2;
    const Layout depth_layout{spec.depth_bytes, width, height, levels, 2, !exhaustive};
    const Layout stencil_layout{1, width, height, levels, 2, !exhaustive};

    Img img{f.runtime, MakeInfo(spec.format, width, height, levels, 2, true, spec.stencil)};
    const MTLPixelFormat want_pixel_format =
        spec.stencil ? MTLPixelFormatDepth32Float_Stencil8 : MTLPixelFormatDepth32Float;
    report.Check(img.Texture().pixelFormat == want_pixel_format, "texture is not held as 32 bit depth");

    const auto depth_guest = DepthGuest(depth_layout, spec, seed, exhaustive);
    const auto depth_copies = depth_layout.Copies(vk::ImageAspectFlagBits::eDepth);
    Upload(f, *img.image, depth_guest, depth_copies);
    CheckDepth(f, report, "upload", img, depth_layout, spec.depth_bits, depth_guest);

    std::vector<u8> stencil_guest;
    std::vector<vk::BufferImageCopy> stencil_copies;
    if (spec.stencil) {
        stencil_guest = RandomBytes(stencil_layout.total, seed + 77);
        stencil_copies = stencil_layout.Copies(vk::ImageAspectFlagBits::eStencil);
        Upload(f, *img.image, stencil_guest, stencil_copies);
        CheckStencil(f, report, "upload", img, stencil_layout, stencil_guest);
        // The stencil upload did not disturb the depth, and the reverse
        CheckDepth(f, report, "upload with stencil", img, depth_layout, spec.depth_bits,
                   depth_guest);
    }

    // The unused top byte of a 4 byte texel is not compared; the download writes zero there.
    const int ignore = spec.depth_bytes == 4 ? 3 : -1;
    const auto depth_down = Download(f, *img.image, depth_copies, depth_guest.size());
    CompareDownload(report, "depth round trip", depth_layout, depth_guest, depth_down, ignore);
    if (ignore >= 0) {
        u64 nonzero = 0;
        depth_layout.ForEachTexel([&](u32, u32, u32, u32, u64 offset) {
            nonzero += depth_down[offset + ignore] != 0;
        });
        report.Check(nonzero == 0, "the unused byte is not zero in " + std::to_string(nonzero) +
                                       " downloaded texels");
    }
    if (spec.stencil) {
        CompareDownload(report, "stencil round trip", stencil_layout, stencil_guest,
                        Download(f, *img.image, stencil_copies, stencil_guest.size()), -1);
    }

    // A GPU-side copy through a buffer keeps the codes too: depth -> depth image of the format
    if (!exhaustive) {
        Img other{f.runtime, MakeInfo(spec.format, width, height, 1, 2, true, spec.stencil)};
        const u64 bytes = u64{width} * height * 2 * 4 * 2;
        auto scratch = MakeBuffer(f.runtime, bytes, MemoryType::DeviceLocal, "copy through buffer");
        f.runtime.CopyImageWithBuffer(img.image.get(), other.image.get(), scratch.get(), 0);
        const Layout one_mip{spec.depth_bytes, width, height, 1, 2, true};
        // (the copy takes mip 0 of the source; compare with the codes of mip 0)
        const auto other_down =
            Download(f, *other.image, one_mip.Copies(vk::ImageAspectFlagBits::eDepth),
                     one_mip.total);
        u64 bad = 0;
        const Layout& src = depth_layout;
        one_mip.ForEachTexel([&](u32, u32 l, u32 y, u32 x, u64 offset) {
            const u32 mask = (1u << spec.depth_bits) - 1;
            bad += (Load(other_down.data() + offset, spec.depth_bytes) & mask) !=
                   (Load(depth_guest.data() + src.TexelOffset(0, l, y, x), spec.depth_bytes) & mask);
        });
        report.Check(bad == 0, "copy through a buffer: " + std::to_string(bad) + " codes differ");
    }
    Settle(f);
    Finish(name, report, exhaustive ? std::to_string(u64{width} * height * 2) + " codes" : "");
}

/// An RGB image copied into another through a buffer (CopyImageWithBuffer: the texture is
/// downloaded into the buffer in the guest layout, the other is uploaded from it): the texels
/// arrive unchanged, alpha is one.
static void TestCopyThroughBuffer(Fixture& f) {
    Report report;
    const vk::Format format = vk::Format::eR8G8B8Unorm;
    Img src{f.runtime, MakeInfo(format, 37, 6, 1, 2, false, false)};
    Img dst{f.runtime, MakeInfo(format, 37, 6, 1, 2, false, false)};
    const Layout layout{3, 37, 6, 1, 2, false};
    const auto guest = RandomBytes(layout.total, 99);
    Upload(f, *src.image, guest, layout.Copies(vk::ImageAspectFlagBits::eColor));
    auto scratch = MakeBuffer(f.runtime, 64 * 1024, MemoryType::DeviceLocal, "scratch");
    f.runtime.CopyImageWithBuffer(src.image.get(), dst.image.get(), scratch.get(), 16);
    CompareDownload(report, "copy through a buffer", layout, guest,
                    Download(f, *dst.image, layout.Copies(vk::ImageAspectFlagBits::eColor),
                             guest.size()),
                    -1);
    const u32 cb = 1;
    CheckRaw(f, report, "copy through a buffer", dst, layout, cb, [&](u32 m, u32 l, u32 y, u32 x) {
        const u8* texel = guest.data() + layout.TexelOffset(m, l, y, x);
        return std::array<u32, 4>{texel[0], texel[1], texel[2], 0xFFu};
    });
    Settle(f);
    Finish("R8G8B8Unorm through a buffer", report);
}

int main() {
    @autoreleasepool {
        Fixture f;
        using F = vk::Format;
        const std::array rgb_formats{
            F::eR8G8B8Unorm,    F::eR8G8B8Snorm,    F::eR8G8B8Uint,     F::eR8G8B8Sint,
            F::eR8G8B8Srgb,     F::eB8G8R8Unorm,    F::eB8G8R8Snorm,    F::eB8G8R8Uint,
            F::eB8G8R8Sint,     F::eB8G8R8Srgb,     F::eR16G16B16Unorm, F::eR16G16B16Snorm,
            F::eR16G16B16Uint,  F::eR16G16B16Sint,  F::eR16G16B16Sfloat, F::eR32G32B32Uint,
            F::eR32G32B32Sint,  F::eR32G32B32Sfloat,
        };
        u32 seed = 1;
        for (const F format : rgb_formats) {
            TestRgbFormat(f, format, seed++);
        }
        const std::array depth_formats{
            DepthSpec{F::eD24UnormS8Uint, true, 24, 4},
            DepthSpec{F::eD16UnormS8Uint, true, 16, 2},
            DepthSpec{F::eX8D24UnormPack32, false, 24, 4},
        };
        for (const DepthSpec& spec : depth_formats) {
            TestDepthFormat(f, spec, seed++, false);
        }
        for (const DepthSpec& spec : depth_formats) {
            TestDepthFormat(f, spec, seed++, true);
        }
        TestCopyThroughBuffer(f);

        {
            Report report;
            report.Check(FC::UnhandledCount().load() == 0,
                         std::to_string(FC::UnhandledCount().load()) +
                             " images had no way to transfer (Runtime::CopyCompatible)");
            Settle(f);
            report.Check(!f.scheduler.HasFailed(), "the scheduler reports a failed command buffer");
            report.Check(g_infra_failures == 0, "command buffer errors");
            Finish("no unhandled transfers", report);
        }
    }
    std::printf("%d cases, %d failed\n", g_cases, g_failed);
    return g_failed ? 1 : 0;
}
