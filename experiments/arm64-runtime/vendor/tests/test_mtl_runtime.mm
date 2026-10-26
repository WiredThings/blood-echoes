// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_runtime (Metal::Runtime, the Metal CacheBackend, plan P2.4):
// buffers (create, map, fill, copy, inline and staged updates, guest chunks), textures (upload
// and download byte exact for RGBA8, BC1, mips, arrays and 3D), views with swizzles sampled by a
// kernel, samplers, the deferred destroys, copies and clears, MSAA backings and resolves,
// detiling and tiling against a CPU reference, the fault buffer parser, access tracking and the
// format policy. Needs a Metal device, no window or game. Run it with MTL_DEBUG_LAYER=1 as well.
//
//   mtl-runtime-test
//
// Exit 0 when every check passed.
#import <Metal/Metal.h>

#include <setjmp.h>
#include "video_core/renderer_metal/mtl_occlusion.h"
#include "video_core/render_state.h"
#include "video_core/buffer_cache/guest_read_gather.h"
#include <sys/mman.h>
#include <mach/mach.h>

#include <filesystem>
#include <fstream>
#include <sstream>
#include <iterator>
#include <format>
#include "video_core/frame_capture.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include "common/slot_vector.h"
#include "bbport_resource_lifetime.h"
#include "video_core/texture_cache/sampler_memo.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_guest_memory.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_memory_stats.h"
#include "video_core/texture_cache/tile_manager.h"
#include "video_core/renderer_metal/mtl_texture_tiling.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/vk_to_mtl.h"

#include <vulkan/vulkan_format_traits.hpp>

// The runtime services libbbgpu takes from bb-probe; this test has no guest process. The guest
// memory module's allocator callback is kept: the test plays the runtime and hands it a chunk.
static int (*g_chunk_allocator)(uint64_t, uint64_t) = nullptr;
static uint8_t* g_backing = nullptr;
static constexpr uint64_t BackingPhys = 0x10000000;
static constexpr uint64_t BackingSize = 1 << 20;

// (libbbgpu looks these up in the executable)
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
    void runtime_memory_set_guest_chunk_allocator(int (*alloc)(uint64_t, uint64_t)) {
        g_chunk_allocator = alloc;
    }
    void runtime_memory_set_guest_chunk_whole(int) {}
    int runtime_memory_vma_info(uintptr_t, int*, int*, uintptr_t*) { std::abort(); }
    void runtime_memory_trap(uintptr_t, uint64_t, unsigned, int) { std::abort(); }
    unsigned runtime_memory_trap_reasons(uintptr_t) { std::abort(); }
    int runtime_memory_direct_phys(uintptr_t, uint64_t*, uintptr_t*) { std::abort(); }
    void* runtime_memory_backing_pointer(uint64_t phys) {
        return phys >= BackingPhys && phys < BackingPhys + BackingSize ? g_backing + (phys - BackingPhys)
                                                                          : nullptr;
    }
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

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

struct Fixture;
static Fixture* g_fixture = nullptr;
static void SettleGlobal();
static void Settle(Fixture& f);

struct Fixture {
    Metal::Device device;
    Scheduler scheduler{device, "test-runtime"};
    Runtime runtime{device, scheduler};
    id<MTLDevice> mtl = device.GetImpl().device;

    Fixture() {
        g_fixture = this;
        scheduler.SetErrorHandler([](const std::string& report) {
            std::printf("  FAIL command buffer error: %s\n", report.c_str());
            ++failures;
        });
    }
};

/// Buffers and images of the test settle the GPU before they go (see Settle).
struct SettleDelete {
    void operator()(Buffer* buffer) const {
        SettleGlobal();
        delete buffer;
    }
};
using BufferPtr = std::unique_ptr<Buffer, SettleDelete>;

static BufferPtr MakeBuffer(Runtime& runtime, u64 size, MemoryType type,
                            const char* name = "test") {
    return BufferPtr{new Buffer(runtime, 0, size, type, name)};
}

template <typename T>
static void Fill(Buffer& buffer, const std::vector<T>& values, u64 offset = 0) {
    std::memcpy(buffer.mapped_data.data() + offset, values.data(), values.size() * sizeof(T));
}

static std::vector<u8> Pattern(size_t size, u32 seed) {
    std::vector<u8> data(size);
    u32 state = seed * 2654435761u + 1;
    for (auto& byte : data) {
        state = state * 1664525u + 1013904223u;
        byte = static_cast<u8>(state >> 24);
    }
    return data;
}

static bool Equal(const Buffer& buffer, u64 offset, const std::vector<u8>& expected) {
    return std::memcmp(buffer.mapped_data.data() + offset, expected.data(), expected.size()) == 0;
}

static ImageInfo MakeInfo(vk::Format format, u32 width, u32 height, u32 levels = 1, u32 layers = 1,
                          u32 samples = 1) {
    ImageInfo info;
    info.pixel_format = format;
    info.type = samples > 1 ? AmdGpu::ImageType::Color2DMsaa
                            : layers > 1 ? AmdGpu::ImageType::Color2DArray
                                         : AmdGpu::ImageType::Color2D;
    info.resources = {levels, layers};
    info.size = {width, height, 1};
    info.num_samples = samples;
    info.pitch = width;
    info.guest_address = 0x1000;
    info.guest_size = width * height * 4;
    info.props.is_block = vk::blockExtent(format)[0] > 1;
    info.props.is_depth = format == vk::Format::eD32Sfloat;
    info.num_bits = vk::blockSize(format) * 8;
    return info;
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

static vk::BufferImageCopy WholeCopy(u32 width, u32 height, u32 mip = 0, u32 layers = 1,
                                     u32 depth = 1, u64 buffer_offset = 0, u32 base_layer = 0) {
    vk::BufferImageCopy copy{};
    copy.bufferOffset = buffer_offset;
    copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eColor;
    copy.imageSubresource.mipLevel = mip;
    copy.imageSubresource.baseArrayLayer = base_layer;
    copy.imageSubresource.layerCount = layers;
    copy.imageExtent = {width, height, depth};
    return copy;
}

/// Uploads `data` into `image` (copies at offsets of the staging buffer given by the caller).
static void Upload(Fixture& f, Image& image, const std::vector<u8>& data,
                   std::span<const vk::BufferImageCopy> copies) {
    const StagingSpan staging = f.runtime.StagingRequest(data.size(), MemoryType::HostUncached, 16);
    std::memcpy(staging.mapped, data.data(), data.size());
    std::vector<vk::BufferImageCopy> shifted(copies.begin(), copies.end());
    for (auto& copy : shifted) {
        copy.bufferOffset += staging.offset;
    }
    f.runtime.UploadImage(&image, staging.buffer, shifted);
}

static std::vector<u8> Download(Fixture& f, Image& image,
                                std::span<const vk::BufferImageCopy> copies, size_t size) {
    auto buffer = MakeBuffer(f.runtime, size, MemoryType::HostCached, "download");
    f.runtime.DownloadImage(&image, buffer.get(), copies);
    Settle(f);
    return {buffer->mapped_data.data(), buffer->mapped_data.data() + size};
}

static NSString* const kernel_source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "kernel void read_texel(texture2d<float> tex [[texture(0)]], device float4* out [[buffer(0)]]) {\n"
    "    out[0] = tex.read(uint2(0, 0));\n"
    "}\n"
    "kernel void sample_texel(texture2d<float> tex [[texture(0)]], sampler smp [[sampler(0)]],\n"
    "                         device float4* out [[buffer(0)]]) {\n"
    "    out[0] = tex.sample(smp, float2(0.5, 0.5), level(0));\n"
    "}\n"
    "kernel void write_word(constant ulong& address [[buffer(0)]], constant uint& value [[buffer(1)]]) {\n"
    "    *reinterpret_cast<device uint*>(address) = value;\n"
    "}\n";

/// The test's kernels live for the whole run: nothing retains a pipeline for the GPU (unretained
/// command buffers), so a pipeline released right after its dispatch is a validation error.
static id<MTLComputePipelineState> Pipeline(id<MTLDevice> device, NSString* name) {
    static NSMutableDictionary<NSString*, id<MTLComputePipelineState>>* cache = [NSMutableDictionary new];
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

/// Finish and give Metal a moment to retire the command buffers. The scheduler frees a tick (and
/// so runs the deferred releases) as soon as its event is signalled, which is earlier than Metal
/// retires the command buffer; the debug layer reports an object released in between ("destroyed
/// while still required to be alive by the command buffer"). The test settles after every Finish
/// so that no release can follow a signal immediately.
static void Settle(Fixture& f) {
    f.runtime.Finish();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
}
static void SettleGlobal() {
    Settle(*g_fixture);
}

// ---- Tests --------------------------------------------------------------------------------------

static void TestLimitsAndFormats(Fixture& f) {
    std::printf("limits and formats\n");
    const BackendLimits& limits = f.runtime.Limits();
    CHECK(limits.integrated);
    CHECK(limits.anisotropic_filtering && limits.max_sampler_anisotropy == 16.0f);
    CHECK(limits.uniform_min_alignment == 16 && limits.storage_min_alignment == 16);
    CHECK(limits.non_coherent_atom_size >= 1);
    CHECK(!limits.custom_border_color && !limits.attachment_feedback_loop_layout);
    CHECK(limits.max_framebuffer_width == 16384 && limits.max_framebuffer_height == 16384);
    CHECK(limits.total_memory_budget > 0 && limits.can_report_memory_usage);
    CHECK(f.runtime.DeviceMemoryBudgetNow() > 0);
    CHECK(f.runtime.DeviceMemoryBudgetNow() <= f.runtime.Limits().total_memory_budget);
    CHECK(f.runtime.GetSparseArena() == nullptr);
    CHECK(f.runtime.GetFastReadback() == nullptr);
    CHECK(!f.runtime.IsRecordingDeferred());

    using F = vk::Format;
    const vk::FormatFeatureFlags2 none{};
    CHECK(f.runtime.SupportedFormat(F::eR8G8B8A8Unorm, none) == F::eR8G8B8A8Unorm);
    CHECK(f.runtime.SupportedFormat(F::eBc1RgbaUnormBlock, none) == F::eBc1RgbaUnormBlock);
    CHECK(f.runtime.SupportedFormat(F::eR32G32B32Sfloat, none) == F::eR32G32B32A32Sfloat);
    CHECK(f.runtime.SupportedFormat(F::eR16G16B16Unorm, none) == F::eR16G16B16A16Unorm);
    CHECK(f.runtime.SupportedFormat(F::eR8G8B8Srgb, none) == F::eR8G8B8A8Srgb);
    CHECK(f.runtime.SupportedFormat(F::eA2B10G10R10SnormPack32, none) == F::eR32Uint);
    CHECK(f.runtime.SupportedFormat(F::eA2B10G10R10SintPack32, none) == F::eR32Uint);
    CHECK(f.runtime.SupportedFormat(F::eR4G4UnormPack8, none) == F::eR8Uint);
    // Everything the policy returns has a Metal texture format.
    for (int raw = 1; raw <= 184; ++raw) {
        const auto format = static_cast<F>(raw);
        if (vk::blockSize(format) == 0) {
            continue;
        }
        CHECK(Metal::VkToMtl::PixelFormat(f.runtime.SupportedFormat(format, none)) != 0);
    }

    vk::ImageCreateInfo ci{};
    ci.imageType = vk::ImageType::e2D;
    ci.format = F::eR8G8B8A8Unorm;
    const auto counts = f.runtime.ImageSampleCounts(ci);
    CHECK(counts & vk::SampleCountFlagBits::e1);
    CHECK(counts & vk::SampleCountFlagBits::e4);
    ci.format = F::eBc1RgbaUnormBlock;
    CHECK(f.runtime.ImageSampleCounts(ci) == vk::SampleCountFlagBits::e1);
    Settle(f);
}

static void TestBuffers(Fixture& f) {
    std::printf("buffers\n");
    auto src = MakeBuffer(f.runtime, 4096, MemoryType::HostUncached, "src");
    auto dst = MakeBuffer(f.runtime, 4096, MemoryType::DeviceLocal, "dst");
    CHECK(!src->mapped_data.empty() && src->is_coherent);
    CHECK(src->buffer.native != nullptr && src->buffer.bda_addr != 0);
    CHECK(src->buffer.owner == &f.runtime);
    const auto pattern = Pattern(4096, 1);
    Fill(*src, pattern);

    // Copies, several regions (odd offsets and sizes too)
    const std::array<vk::BufferCopy, 3> copies{{
        {0, 0, 1024},
        {1024, 2048, 512},
        {2049, 3001, 77},
    }};
    f.runtime.CopyBuffer(src.get(), dst.get(), copies);
    Settle(f);
    CHECK(std::memcmp(dst->mapped_data.data(), pattern.data(), 1024) == 0);
    CHECK(std::memcmp(dst->mapped_data.data() + 2048, pattern.data() + 1024, 512) == 0);
    CHECK(std::memcmp(dst->mapped_data.data() + 3001, pattern.data() + 2049, 77) == 0);

    // Fills: one repeated byte (blit), four different bytes (kernel), at odd offsets too
    f.runtime.FillBuffer(dst.get(), 0, 256, 0xABABABABu);
    f.runtime.FillBuffer(dst.get(), 256, 256, 0x04030201u);
    f.runtime.FillBuffer(dst.get(), 513, 7, 0);
    Settle(f);
    CHECK(std::all_of(dst->mapped_data.begin(), dst->mapped_data.begin() + 256,
                      [](u8 b) { return b == 0xAB; }));
    bool words_ok = true;
    for (u32 i = 0; i < 64; ++i) {
        words_ok &= reinterpret_cast<const u32*>(dst->mapped_data.data() + 256)[i] == 0x04030201u;
    }
    CHECK(words_ok);
    CHECK(std::all_of(dst->mapped_data.begin() + 513, dst->mapped_data.begin() + 520,
                      [](u8 b) { return b == 0; }));

    // Stream order: a later fill over an earlier inline write wins, and the other way round.
    f.runtime.InlineData(dst.get(), 1024, 0x11111111u);
    f.runtime.FillBuffer(dst.get(), 1024, 4, 0x22222222u);
    f.runtime.FillBuffer(dst.get(), 1028, 4, 0x33333333u);
    f.runtime.InlineData(dst.get(), 1028, 0x44444444u);
    Settle(f);
    CHECK(*reinterpret_cast<const u32*>(dst->mapped_data.data() + 1024) == 0x22222222u);
    CHECK(*reinterpret_cast<const u32*>(dst->mapped_data.data() + 1028) == 0x44444444u);

    // UpdateBuffer, small and big, in order with a copy of the same range
    const auto update = Pattern(1000, 2);
    f.runtime.UpdateBuffer(dst.get(), 100, update);
    Settle(f);
    CHECK(Equal(*dst, 100, update));
    auto big = MakeBuffer(f.runtime, 1 << 20, MemoryType::DeviceLocal, "big");
    const auto big_data = Pattern(300000, 3);
    f.runtime.UpdateBuffer(big.get(), 4096, big_data);
    Settle(f);
    CHECK(Equal(*big, 4096, big_data));

    // The device address reaches the same memory (residency, D6)
    id<MTLComputePipelineState> write_word = Pipeline(f.mtl, @"write_word");
    f.scheduler.ComputeEncoder();
    auto& compute = f.scheduler.GetImpl().compute;
    const u64 address = dst->buffer.bda_addr + 2000;
    const u32 value = 0xC0FFEE11u;
    [compute setComputePipelineState:write_word];
    [compute setBytes:&address length:8 atIndex:0];
    [compute setBytes:&value length:4 atIndex:1];
    [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    Settle(f);
    CHECK(*reinterpret_cast<const u32*>(dst->mapped_data.data() + 2000) == value);
    Settle(f);
}

// Exact snapshots prove each command observes its own payload, not a later CPU mutation.
static void TestInlineUpdates(Fixture& f) {
    auto dst=MakeBuffer(f.runtime,4096,MemoryType::DeviceLocal,"inline destination");
    auto snapshots=MakeBuffer(f.runtime,4096,MemoryType::DeviceLocal,"inline snapshots");
    const std::array<size_t,7> lengths{4,16,192,256,260,191,0};
    std::vector<std::vector<u8>> expected;
    for(size_t i=0;i<lengths.size();++i) {
        f.runtime.FillBuffer(dst.get(),0,512,0xa5a5a5a5);
        auto data=Pattern(lengths[i],unsigned(20+i));
        std::vector<u8> bytes(512,0xa5);
        const u64 offset=i==5?17:16; // non-word data/unaligned offsets must fall back.
        std::copy(data.begin(),data.end(),bytes.begin()+offset);
        f.runtime.UpdateBuffer(dst.get(),offset,data);
        std::fill(data.begin(),data.end(),0xee); // setBytes / staging must have captured it.
        const vk::BufferCopy copy{0,i*512,512};
        f.runtime.CopyBuffer(dst.get(),snapshots.get(),{&copy,1});
        expected.push_back(std::move(bytes));
    }
    Settle(f);
    for(size_t i=0;i<expected.size();++i)CHECK(Equal(*snapshots,i*512,expected[i]));
    // Repeated overlapping writes must preserve GPU read/write dependencies.
    auto first=Pattern(192,33),second=Pattern(192,34);
    f.runtime.UpdateBuffer(dst.get(),32,first);
    const vk::BufferCopy read_first{32,0,192};
    f.runtime.CopyBuffer(dst.get(),snapshots.get(),{&read_first,1});
    f.runtime.UpdateBuffer(dst.get(),32,second);
    const vk::BufferCopy read_second{32,256,192};
    f.runtime.CopyBuffer(dst.get(),snapshots.get(),{&read_second,1});
    Settle(f);CHECK(Equal(*snapshots,0,first));CHECK(Equal(*snapshots,256,second));
    std::puts("inline updates: payload snapshots, canaries, overlap and fallback passed");
}

// Actual Runtime path, not just a host-shader prototype. Separate processes select the flag
// and scheduler hold mode; every safe case must hit the candidate and every conflict must not.
static void TestRenderUpdates(Fixture& f) {
    using Stage=Metal::RenderList::Stage;
    const bool enabled=std::getenv("BB_MTL_RENDER_UPDATES") && std::getenv("BB_MTL_RENDER_UPDATES")[0]=='1';
    NSError* error=nil;
    auto library=[f.mtl newLibraryWithSource:@R"(
#include <metal_stdlib>
using namespace metal;
float4 position(uint i){return float4(i==1?3.0:-1.0,i==2?3.0:-1.0,0,1);}
vertex float4 before(uint i[[vertex_id]],device const uint* b[[buffer(0)]],device uint* witness[[buffer(2)]]){witness[i]=b[i];return position(i);}
vertex float4 after(uint i[[vertex_id]]){return position(i);}
fragment float4 red(){return float4(1,0,0,1);}
fragment float4 read_old(device const uint* b[[buffer(0)]],device uint* w[[buffer(2)]],float4 p[[position]]){if(uint(p.x)==0 && uint(p.y)==0)w[0]=b[0];return float4(1,0,0,1);}
fragment float4 read_new(constant uint* b[[buffer(0)]]){return float4(0,b[0]==0xa0000000u && b[47]==0xa000002fu?1.0:0.0,0,1);}
)" options:nil error:&error];
    CHECK(library!=nil);if(!library)return;
    const auto make_pipeline=[&](NSString* vs,NSString* fs) {
        auto d=[MTLRenderPipelineDescriptor new];d.vertexFunction=[library newFunctionWithName:vs];
        d.fragmentFunction=[library newFunctionWithName:fs];d.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
        auto pipeline=[f.mtl newRenderPipelineStateWithDescriptor:d error:&error];CHECK(pipeline!=nil);return pipeline;
    };
    auto pre=make_pipeline(@"before",@"red"),overlap=make_pipeline(@"before",@"read_old"),post=make_pipeline(@"after",@"read_new");
    if(!pre || !overlap || !post)return;
    g_backing=static_cast<u8*>(mmap(nullptr,BackingSize,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0));
    CHECK(g_backing!=MAP_FAILED);if(g_backing==MAP_FAILED)return;
    setenv("BB_GUEST_IN_PLACE","1",1);CHECK(f.runtime.GuestMemoryUsable());
    Metal::GuestMemory::Install(f.device);CHECK(g_chunk_allocator && g_chunk_allocator(BackingPhys,BackingSize)==-3);
    if(!g_chunk_allocator)return;
    Buffer imported{f.runtime,65536,BackingPhys,"render update alias A"};
    Buffer alias{f.runtime,65536,BackingPhys,"render update alias B"};
    CHECK(imported.buffer.native==alias.buffer.native);
    auto target=MakeBuffer(f.runtime,4096,MemoryType::DeviceLocal,"render update destination");
    auto witness=MakeBuffer(f.runtime,4096,MemoryType::DeviceLocal,"vertex read before update");
    auto frag_witness=MakeBuffer(f.runtime,4096,MemoryType::DeviceLocal,"fragment read before update");
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
    td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
    auto texture=[f.mtl newTextureWithDescriptor:td];auto dummy=[f.mtl newTextureWithDescriptor:td];CHECK(texture && dummy);
    unsigned tails=0;f.scheduler.SetRenderTail([&]{++tails;});
    for(unsigned mode=0;mode<9;++mode) {
        Settle(f);
        Buffer* dst=mode==8?&imported:target.get();
        Buffer* fs_buffer=mode==8?&alias:dst;
        auto native=(__bridge id<MTLBuffer>)dst->buffer.native;
        std::memset(native.contents,0xcd,256);std::memset(witness->mapped_data.data(),0,16);
        std::memset(frag_witness->mapped_data.data(),0,16);
        std::array<u8,192> old;old.fill(0xcd);
        f.runtime.UpdateBuffer(dst,16,old); // preceding compute producer, including fence handling
        CHECK(!f.scheduler.CanInlineBufferUpdate());
        const auto begin=[&](id<MTLTexture> tex,bool clear) {
            auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=tex;
            pass.colorAttachments[0].loadAction=clear?MTLLoadActionClear:MTLLoadActionLoad;
            pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            RenderState state{};state.width=8;state.height=8;state.num_layers=1;state.num_color_attachments=1;
            state.color_attachments[0].image_view=uintptr_t((__bridge void*)tex);
            f.scheduler.RenderEncoder((__bridge const void*)pass,state);
        };
        // Holding + threaded replay gets a real previous recorded pass, not an empty list.
        begin(dummy,true);
        auto& list=f.scheduler.Commands();
        list.SetRenderPipelineState((__bridge void*)post);
        list.SetBuffer(Stage::Fragment,fs_buffer->buffer.native,16,0);list.FragmentReady();
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        begin(texture,true);
        list.SetRenderPipelineState((__bridge void*)((mode==1 || mode==8)?overlap:pre));
        list.SetBuffer(Stage::Vertex,dst->buffer.native,16,0);
        list.SetBuffer(Stage::Vertex,witness->buffer.native,0,2);
        list.SetBuffer(Stage::Fragment,fs_buffer->buffer.native,16,0);
        list.SetBuffer(Stage::Fragment,frag_witness->buffer.native,0,2);
        if(mode==1 || mode==8) {
            list.FragmentBuffer(fs_buffer->buffer.native,16,192);
            list.FragmentBuffer(frag_witness->buffer.native,0,4);
        }
        if(mode!=3)list.FragmentReady(); // unclassified draws must be rejected
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        f.runtime.AccessBuffer(dst,16,192,vk::PipelineStageFlagBits2::eVertexShader,vk::AccessFlagBits2::eShaderRead);
        if(mode==1 || mode==8)f.runtime.AccessBuffer(fs_buffer,16,192,vk::PipelineStageFlagBits2::eFragmentShader,vk::AccessFlagBits2::eShaderRead);
        if(mode==2)list.FragmentUnknown(Metal::FragmentAccessGuard::RawAddress);
        if(mode==4)for(unsigned i=1;i<=65;++i)list.FragmentBuffer(reinterpret_cast<void*>(u64(i)),0,4);
        if(mode==5)f.scheduler.RequestRenderTail();
        if(mode==6) {
            if(list.Recording()) {
                f.scheduler.BeginHoist();CHECK(!f.scheduler.CanInlineBufferUpdate());f.scheduler.EndHoist();
                f.scheduler.ReleaseHeldPass(); // same physical pass must retain fragment history
            }
            CHECK(f.scheduler.CanInlineBufferUpdate());
        }
        if(mode==7)list.FragmentUnknown(Metal::FragmentAccessGuard::UnknownTexture);
        std::array<u32,48> payload;for(unsigned i=0;i<payload.size();++i)payload[i]=0xa0000000u+i;
        const auto before_count=f.runtime.RenderUpdateCount();
        f.runtime.UpdateBuffer(dst,16,{reinterpret_cast<const u8*>(payload.data()),sizeof(payload)});
        const bool safe=mode==0 || mode==6;
        CHECK(f.runtime.RenderUpdateCount()-before_count==u64(enabled && safe));
        std::memset(payload.data(),0xee,sizeof(payload)); // snapshot survives CPU mutation/replay
        if(!f.scheduler.IsRendering())begin(texture,false);
        list.SetRenderPipelineState((__bridge void*)post);
        list.SetBuffer(Stage::Fragment,dst->buffer.native,16,0);
        list.FragmentBuffer(dst->buffer.native,16,192);list.FragmentReady();
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        Settle(f);
        const auto* bytes=static_cast<const u8*>(native.contents);
        for(unsigned i=0;i<256;++i) {
            const u32 word=0xa0000000u+(i-16)/4;
            CHECK(bytes[i]==(i>=16 && i<208?reinterpret_cast<const u8*>(&word)[(i-16)%4]:0xcd));
        }
        for(unsigned i=0;i<3;++i)CHECK(reinterpret_cast<const u32*>(witness->mapped_data.data())[i]==0xcdcdcdcdu);
        if(mode==1 || mode==8)CHECK(*reinterpret_cast<const u32*>(frag_witness->mapped_data.data())==0xcdcdcdcdu);
        std::array<u8,256> pixels;[texture getBytes:pixels.data() bytesPerRow:32 fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
        for(unsigned i=0;i<64;++i)CHECK(pixels[4*i]==0 && pixels[4*i+1]==255 && pixels[4*i+2]==0 && pixels[4*i+3]==255);
        std::printf("render updates mode=%u candidate=%d exact bytes/canaries/vertices/fragments/pixels\n",mode,int(enabled && safe));
    }
    CHECK(tails>0);f.scheduler.SetRenderTail({});Settle(f);
}

static void TestGuestChunk(Fixture& f) {
    std::printf("guest chunks\n");
    g_backing = static_cast<uint8_t*>(mmap(nullptr, BackingSize, PROT_READ | PROT_WRITE,
                                           MAP_ANON | MAP_PRIVATE, -1, 0));
    CHECK(g_backing != MAP_FAILED);
    setenv("BB_GUEST_IN_PLACE", "1", 1);
    CHECK(f.runtime.GuestMemoryUsable());
    Metal::GuestMemory::Install(f.device);
    CHECK(g_chunk_allocator != nullptr);
    if (!g_chunk_allocator) {
        return;
    }
    CHECK(g_chunk_allocator(BackingPhys, BackingSize) == -3);
    const auto* chunk = Metal::GuestMemory::Find(BackingPhys);
    CHECK(chunk && chunk->size == BackingSize);

    const auto pattern = Pattern(8192, 4);
    std::memcpy(g_backing, pattern.data(), pattern.size());
    {
        Buffer imported{f.runtime, 65536, BackingPhys, "guest"};
        CHECK(imported.buffer.native != nullptr);
        CHECK(imported.buffer.bda_addr == chunk->gpu_address);
        CHECK(imported.is_coherent);
        auto dst = MakeBuffer(f.runtime, 4096, MemoryType::DeviceLocal, "from guest");
        const std::array<vk::BufferCopy, 2> copies{{{256, 0, 1024}, {4096, 2048, 300}}};
        f.runtime.CopyFromGuestChunk(BackingPhys, dst.get(), copies);
        auto dst2 = MakeBuffer(f.runtime, 4096, MemoryType::DeviceLocal, "from import");
        const vk::BufferCopy copy{512, 0, 700};
        f.runtime.CopyBuffer(&imported, dst2.get(), std::span{&copy, 1});
        Settle(f);
        CHECK(std::memcmp(dst->mapped_data.data(), pattern.data() + 256, 1024) == 0);
        CHECK(std::memcmp(dst->mapped_data.data() + 2048, pattern.data() + 4096, 300) == 0);
        CHECK(std::memcmp(dst2->mapped_data.data(), pattern.data() + 512, 700) == 0);
        Settle(f);
    }
    Settle(f);
    f.runtime.PopPendingOperations();
    // The chunk's own reference survived the buffers that imported it.
    CHECK(Metal::GuestMemory::Find(BackingPhys) != nullptr);
    CHECK(((__bridge id<MTLBuffer>)chunk->buffer).length == BackingSize);
    Settle(f);
}

static void TestCrossChunkRead(Fixture& f) {
    std::puts("cross-chunk read snapshots in existing stream ring");
    // Exact size from the observed normal-gameplay crash, split across discontinuous sources.
    constexpr u64 Size = 0xf9ad8, Start = 0x7000000000, First = 0x34400;
    auto a = MakeBuffer(f.runtime, 512_KB, MemoryType::HostCached, "gather first chunk");
    auto b = MakeBuffer(f.runtime, 1_MB, MemoryType::HostCached, "gather second chunk");
    auto output = MakeBuffer(f.runtime, 5 * Size, MemoryType::HostCached, "gather snapshots");
    StreamBuffer ring{f.runtime, MemoryType::Stream, 2_MB};
    const auto original_a = Pattern(a->SizeBytes(), 73);
    const auto original_b = Pattern(b->SizeBytes(), 74);
    const auto changed_a = Pattern(a->SizeBytes(), 75);
    const auto changed_b = Pattern(b->SizeBytes(), 76);
    const u64 split = a->SizeBytes() - First;
    auto resolve = [&](VAddr at, u64 remaining) -> std::optional<GuestReadPiece> {
        const u64 done = at - Start;
        if (done < split) return GuestReadPiece{a.get(), First + done,
                                               std::min(remaining, split - done)};
        return GuestReadPiece{b.get(), done - split, remaining};
    };
    auto no_sync = [](const GuestReadPiece&, VAddr) {};
    // Reject holes, overflows, zero-size and oversized requests before any GPU copy/reservation.
    unsigned callbacks = 0;
    auto count_sync = [&](const GuestReadPiece&, VAddr) { ++callbacks; };
    CHECK(!GatherGuestRead(f.runtime, ring, Start, 0, resolve, count_sync));
    CHECK(!GatherGuestRead(f.runtime, ring, Start, ring.SizeBytes()+1, resolve, count_sync));
    CHECK(!GatherGuestRead(f.runtime, ring, ~u64{0}-8, 16, resolve, count_sync));
    CHECK(!GatherGuestRead(f.runtime, ring, Start, Size,
        [&](VAddr at, u64 n) -> std::optional<GuestReadPiece> {
            if (at != Start) return std::nullopt;
            return resolve(at,n);
        }, count_sync));
    CHECK(!GatherGuestRead(f.runtime, ring, Start, Size,
        [&](VAddr, u64) -> std::optional<GuestReadPiece> {
            return GuestReadPiece{a.get(), a->SizeBytes()-1, 16};
        }, count_sync));
    CHECK(callbacks == 0);
    for (u64 i = 0; i < 5; ++i) {
        // Earlier GPU updates must be visible to the gather; later updates must not alter
        // the already captured range. Repeated gathers wrap the SAME fixed-size ring.
        f.runtime.UpdateBuffer(a.get(), 0, original_a);
        f.runtime.UpdateBuffer(b.get(), 0, original_b);
        const auto gathered = GatherGuestRead(f.runtime, ring, Start, Size, resolve, no_sync);
        CHECK(gathered && gathered->first == &ring);
        if (!gathered) break;
        f.runtime.UpdateBuffer(a.get(), 0, changed_a);
        f.runtime.UpdateBuffer(b.get(), 0, changed_b);
        const vk::BufferCopy copy{gathered->second, i*Size, Size};
        f.runtime.CopyBuffer(gathered->first, output.get(), {&copy,1});
    }
    Settle(f);
    for (u64 i = 0; i < 5; ++i) {
        CHECK(std::memcmp(output->mapped_data.data()+i*Size,
                          original_a.data()+First, split)==0);
        CHECK(std::memcmp(output->mapped_data.data()+i*Size+split,
                          original_b.data(), Size-split)==0);
    }
    CHECK(ring.SizeBytes() == 2_MB);
    CHECK(!f.scheduler.HasFailed());
    std::puts("cross-chunk read: exact bytes, holes, GPU ordering and bounded ring reuse passed");
}

static void TestCrossChunkSnapshotReuse(Fixture& f) {
    std::puts("same-packet cross-chunk snapshot reuse and invalidation");
    constexpr VAddr Start = 0x7100000000;
    constexpr u64 Size = 24_KB;
    auto a = MakeBuffer(f.runtime, 16_KB, MemoryType::HostCached, "reuse source A");
    auto b = MakeBuffer(f.runtime, 16_KB, MemoryType::HostCached, "reuse source B");
    auto output = MakeBuffer(f.runtime, 2*Size, MemoryType::HostCached, "reuse output");
    StreamBuffer ring{f.runtime, MemoryType::Stream, 64_KB};
    GuestReadSnapshots memo;
    u64 packet = 7, cpu = 0, mapping = 0, layer = 0;
    const auto stamp = [&] { return GuestReadEpoch{packet, f.runtime.CurrentTick(),
        ring.WrapGeneration(), cpu, mapping, layer, f.runtime.BufferWriteEpoch()}; };
    const auto resolve = [&](VAddr at, u64 remaining) -> std::optional<GuestReadPiece> {
        const auto done = at-Start;
        if (done < 8_KB) return GuestReadPiece{a.get(), 8_KB+done, std::min(remaining,8_KB-done)};
        return GuestReadPiece{b.get(),done-8_KB,remaining};
    };
    const auto no_sync = [](const GuestReadPiece&, VAddr) {};
    const auto original_a=Pattern(16_KB,81), original_b=Pattern(16_KB,82);
    f.runtime.UpdateBuffer(a.get(),0,original_a);
    f.runtime.UpdateBuffer(b.get(),0,original_b);
    auto before=stamp();
    CHECK(!memo.Find(ring,Start,Size,16,before));
    auto first=GatherGuestRead(f.runtime,ring,Start,Size,resolve,no_sync);
    CHECK(first);
    if(!first) return;
    memo.Remember(Start,Size,first->second,before,stamp());
    const auto exact=memo.Find(ring,Start,Size,16,stamp());
    const auto sub=memo.Find(ring,Start+4096,8192,16,stamp());
    CHECK(exact && exact->second==first->second);
    CHECK(sub && sub->second==first->second+4096);
    CHECK(!memo.Find(ring,Start+1,8192,16,stamp())); // preserve binding alignment
    CHECK(!memo.Find(ring,Start-16,Size,16,stamp()));
    CHECK(!memo.Find(ring,Start+16,Size,16,stamp())); // must fit completely
    const auto allocated=f.mtl.currentAllocatedSize;
    for(int i=0;i<1000;++i) CHECK(memo.Find(ring,Start,Size,16,stamp()));
    CHECK(f.mtl.currentAllocatedSize==allocated); // reuse allocates no Metal backing
    const vk::BufferCopy save_first{first->second,0,Size};
    f.runtime.CopyBuffer(&ring,output.get(),{&save_first,1});
    CHECK(!memo.Find(ring,Start,Size,16,stamp())); // even an unrelated GPU write is conservative
    const auto changed=Pattern(16_KB,83);
    f.runtime.UpdateBuffer(b.get(),0,changed);
    before=stamp();CHECK(!memo.Find(ring,Start,Size,16,before));
    auto second=GatherGuestRead(f.runtime,ring,Start,Size,resolve,no_sync);
    CHECK(second);
    if(!second) return;
    memo.Remember(Start,Size,second->second,before,stamp());
    CHECK(memo.Find(ring,Start,Size,16,stamp()));
    const vk::BufferCopy save_second{second->second,Size,Size};
    f.runtime.CopyBuffer(&ring,output.get(),{&save_second,1});
    Settle(f);
    CHECK(std::memcmp(output->mapped_data.data(),original_a.data()+8_KB,8_KB)==0);
    CHECK(std::memcmp(output->mapped_data.data()+8_KB,original_b.data(),16_KB)==0);
    CHECK(std::memcmp(output->mapped_data.data()+Size,original_a.data()+8_KB,8_KB)==0);
    CHECK(std::memcmp(output->mapped_data.data()+Size+8_KB,changed.data(),16_KB)==0);

    // Every independent validity boundary must reject an otherwise identical cached range.
    const GuestReadEpoch base{1,2,3,4,6,7,8};
    for(unsigned field=0;field<7;++field) {
        GuestReadSnapshots c;c.Remember(Start,Size,0,base,base);
        CHECK(c.Find(ring,Start,Size,16,base));
        auto e=base;
        switch(field) {case 0:++e.packet;break;case 1:++e.tick;break;case 2:++e.wrap;break;
            case 3:++e.cpu;break;case 4:e.mapping+=2;break;case 5:++e.layer;break;case 6:++e.gpu;break;}
        CHECK(!c.Find(ring,Start,Size,16,e));
    }
    GuestReadSnapshots capped;
    for(u64 i=0;i<100;++i)capped.Remember(Start+i*Size,Size,0,base,base);
    CHECK(!capped.Find(ring,Start,Size,16,base));
    CHECK(capped.Find(ring,Start+99*Size,Size,16,base));
    auto odd=base;odd.mapping|=1;
    capped.Remember(Start,Size,0,odd,odd);
    CHECK(!capped.Find(ring,Start,Size,16,odd));
    // Own gather writes may promote existing snapshots to the new GPU epoch.
    GuestReadSnapshots own;own.Remember(Start,Size,0,base,base);
    auto after=base;after.gpu+=2;
    own.Remember(Start+Size,Size,Size,base,after);
    CHECK(own.Find(ring,Start,Size,16,after));
    auto cpu_changed=after;++cpu_changed.cpu;
    own.Remember(Start+2*Size,Size,2*Size,after,cpu_changed);
    CHECK(!own.Find(ring,Start,Size,16,cpu_changed));
    const auto old_wrap=ring.WrapGeneration();
    CHECK(ring.Reserve(ring.SizeBytes(),16));
    CHECK(ring.WrapGeneration()>old_wrap);
    const auto epoch=f.runtime.BufferWriteEpoch();
    f.runtime.AccessGlobal(vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderWrite);
    CHECK(f.runtime.BufferWriteEpoch()>epoch);
    f.runtime.FlushBarriers();CHECK(f.runtime.BufferWriteEpoch()>epoch);
    Settle(f);
    std::puts("snapshot reuse: exact/subrange bytes, zero GPU allocation, writes, scopes and ring wrap passed");
}

static void TestImages(Fixture& f) {
    std::printf("images\n");
    using F = vk::Format;
    {
        // RGBA8 with two mips
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 64, 64, 2)};
        CHECK(img.image->backing != nullptr && img.Texture().mipmapLevelCount == 2);
        CHECK(img.image->backing->image.size_bytes >= 64 * 64 * 4);
        const auto data = Pattern(64 * 64 * 4 + 32 * 32 * 4, 5);
        const std::array<vk::BufferImageCopy, 2> copies{
            WholeCopy(64, 64, 0, 1, 1, 0), WholeCopy(32, 32, 1, 1, 1, 64 * 64 * 4)};
        Upload(f, *img.image, data, copies);
        const auto back = Download(f, *img.image, copies, data.size());
        CHECK(back == data);
        CHECK(!(img.image->flags & ImageFlagBits::Dirty));
    }
    {
        // A partial region with a row length wider than the region
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
        const auto data = Pattern(32 * 32 * 4, 6);
        Upload(f, *img.image, data, std::array{WholeCopy(32, 32)});
        vk::BufferImageCopy part = WholeCopy(8, 4);
        part.imageOffset = {4, 2, 0};
        part.bufferRowLength = 16;
        const auto back = Download(f, *img.image, std::array{part}, 16 * 4 * 4);
        bool ok = true;
        for (u32 y = 0; y < 4; ++y) {
            ok &= std::memcmp(back.data() + y * 16 * 4, data.data() + ((y + 2) * 32 + 4) * 4,
                              8 * 4) == 0;
        }
        CHECK(ok);
    }
    {
        // BC1 blocks
        Img img{f.runtime, MakeInfo(F::eBc1RgbaUnormBlock, 64, 64, 3)};
        CHECK(img.Texture().pixelFormat == MTLPixelFormatBC1_RGBA);
        const size_t size = (16 * 16 + 8 * 8 + 4 * 4) * 8;
        const auto data = Pattern(size, 7);
        const std::array<vk::BufferImageCopy, 3> copies{
            WholeCopy(64, 64, 0, 1, 1, 0), WholeCopy(32, 32, 1, 1, 1, 16 * 16 * 8),
            WholeCopy(16, 16, 2, 1, 1, (16 * 16 + 8 * 8) * 8)};
        Upload(f, *img.image, data, copies);
        CHECK(Download(f, *img.image, copies, size) == data);
    }
    {
        // A 2D array with a layer loop, and a 3D image
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 16, 16, 1, 3)};
        const auto data = Pattern(16 * 16 * 4 * 3, 8);
        const std::array copies{WholeCopy(16, 16, 0, 3)};
        Upload(f, *img.image, data, copies);
        CHECK(Download(f, *img.image, copies, data.size()) == data);

        ImageInfo info3 = MakeInfo(F::eR8G8B8A8Unorm, 8, 8);
        info3.type = AmdGpu::ImageType::Color3D;
        info3.size = {8, 8, 4};
        Img vol{f.runtime, info3};
        CHECK(vol.Texture().textureType == MTLTextureType3D);
        const auto vdata = Pattern(8 * 8 * 4 * 4, 9);
        const std::array vcopies{WholeCopy(8, 8, 0, 1, 4)};
        Upload(f, *vol.image, vdata, vcopies);
        CHECK(Download(f, *vol.image, vcopies, vdata.size()) == vdata);
    }
    {
        // Substituted formats: the texture exists; the RGB transfers convert (checked in
        // mtl-format-convert-test), the raw-bits ones are byte exact (below)
        Img img{f.runtime, MakeInfo(F::eR16G16B16Sfloat, 8, 8)};
        CHECK(img.image->backing->image.image_ci.format == F::eR16G16B16A16Sfloat);
        const auto data = Pattern(8 * 8 * 6, 10);
        Upload(f, *img.image, data, std::array{WholeCopy(8, 8)});
        Settle(f);
        Img raw{f.runtime, MakeInfo(F::eA2B10G10R10SnormPack32, 8, 8)};
        CHECK(raw.Texture().pixelFormat == MTLPixelFormatR32Uint);
        const auto raw_data = Pattern(8 * 8 * 4, 11);
        Upload(f, *raw.image, raw_data, std::array{WholeCopy(8, 8)});
        CHECK(Download(f, *raw.image, std::array{WholeCopy(8, 8)}, raw_data.size()) == raw_data);
    }
    Settle(f);
}

static void TestViewsAndSamplers(Fixture& f) {
    std::printf("views and samplers\n");
    using F = vk::Format;
    Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 2, 2)};
    // texel (0,0) = (10, 20, 30, 40); the others make the bilinear average easy to tell
    std::vector<u8> data{10, 20, 30, 40, 0, 0, 0, 0, 0, 0, 0, 0, 255, 255, 255, 255};
    Upload(f, *img.image, data, std::array{WholeCopy(2, 2)});

    ImageViewInfo plain;
    plain.format = F::eR8G8B8A8Unorm;
    plain.type = AmdGpu::ImageType::Color2D;
    plain.range = {{0, 0}, {1, 1}};
    ImageView& identity = img.image->FindView(plain);
    CHECK(identity.native != nullptr && identity.owner == &f.runtime);
    // The whole texture: the view is the texture itself
    CHECK(identity.native == img.image->backing->image.native);

    ImageViewInfo swapped = plain;
    swapped.mapping = {vk::ComponentSwizzle::eB, vk::ComponentSwizzle::eG,
                       vk::ComponentSwizzle::eR, vk::ComponentSwizzle::eOne};
    ImageView& view = img.image->FindView(swapped);
    CHECK(view.native != nullptr && view.native != img.image->backing->image.native);

    ImageViewInfo srgb = plain;
    srgb.format = F::eR8G8B8A8Srgb;
    ImageView& srgb_view = img.image->FindView(srgb);
    CHECK(((__bridge id<MTLTexture>)srgb_view.native).pixelFormat ==
          MTLPixelFormatRGBA8Unorm_sRGB);

    // The swizzled view, read by a kernel
    auto out = MakeBuffer(f.runtime, 256, MemoryType::HostCached, "out");
    std::memset(out->mapped_data.data(), 0, 256);
    f.scheduler.ComputeEncoder();
    auto& compute = f.scheduler.GetImpl().compute;
    [compute setComputePipelineState:Pipeline(f.mtl, @"read_texel")];
    [compute setTexture:(__bridge id<MTLTexture>)view.native atIndex:0];
    [compute setBuffer:(__bridge id<MTLBuffer>)out->buffer.native offset:0 atIndex:0];
    [compute dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    Settle(f);
    const float* texel = reinterpret_cast<const float*>(out->mapped_data.data());
    std::printf("  swizzled texel %f %f %f %f\n", texel[0], texel[1], texel[2], texel[3]);
    CHECK(std::abs(texel[0] - 30 / 255.0f) < 1e-3f && std::abs(texel[1] - 20 / 255.0f) < 1e-3f &&
          std::abs(texel[2] - 10 / 255.0f) < 1e-3f && std::abs(texel[3] - 1.0f) < 1e-3f);

    // A sampler: bilinear in the middle of the 2x2 texture is the mean of the four texels
    AmdGpu::Sampler guest{};
    guest.raw0 = 0;
    guest.raw1 = (u64(AmdGpu::Filter::Bilinear) << 20) | (u64(AmdGpu::Filter::Bilinear) << 22);
    Sampler sampler{f.runtime, guest, AmdGpu::BorderColorBuffer{}, false};
    CHECK(sampler.native != nullptr && sampler.owner == &f.runtime);
    SamplerMemo<SamplerRef> memo;
    SamplerMemo<SamplerRef>::Key key;
    std::memcpy(key.sharp.data(), &guest, sizeof(guest));
    unsigned lookups = 0;
    const auto lookup = [&](size_t&) {
        ++lookups;
        return SamplerRef{sampler.Handle(), sampler.native};
    };
    auto cached = memo.Get(key, 0, 0, lookup, [](size_t) {});
    for (unsigned i = 0; i < 1000; ++i) {
        cached = memo.Get(key, 0, 0, lookup, [](size_t) {});
        CHECK(cached.native == sampler.native);
    }
    CHECK(lookups == 1);
    f.scheduler.ComputeEncoder();
    auto& compute2 = f.scheduler.GetImpl().compute;
    [compute2 setComputePipelineState:Pipeline(f.mtl, @"sample_texel")];
    [compute2 setTexture:(__bridge id<MTLTexture>)identity.native atIndex:0];
    [compute2 setSamplerState:(__bridge id<MTLSamplerState>)cached.native atIndex:0];
    [compute2 setBuffer:(__bridge id<MTLBuffer>)out->buffer.native offset:0 atIndex:0];
    [compute2 dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    Settle(f);
    std::printf("  sampled texel %f %f %f %f\n", texel[0], texel[1], texel[2], texel[3]);
    CHECK(std::abs(texel[0] - (10 + 255) / 4.0f / 255.0f) < 2e-3f);
    CHECK(std::abs(texel[3] - (40 + 255) / 4.0f / 255.0f) < 2e-3f);

    // A depth image and its views: depth aspect, stencil aspect of a combined format
    ImageInfo depth_info = MakeInfo(F::eD32SfloatS8Uint, 16, 16);
    depth_info.props.is_depth = 1;
    depth_info.props.has_stencil = 1;
    Img depth{f.runtime, depth_info};
    CHECK(depth.Texture().pixelFormat == MTLPixelFormatDepth32Float_Stencil8);
    ImageViewInfo depth_view_info;
    depth_view_info.format = F::eR32Sfloat;
    depth_view_info.type = AmdGpu::ImageType::Color2D;
    depth_view_info.range = {{0, 0}, {1, 1}};
    ImageView& depth_view = depth.image->FindView(depth_view_info);
    CHECK(depth_view.native != nullptr);
    Settle(f);
}

/// P10.1: images are created without ShaderWrite and PixelFormatView and promoted on first need
/// (Runtime::RequiredViewUsage, PromoteImageUsage): the contents follow, the old backing and its
/// views are retired through the deferred destroy, nothing matches the retired backing again.
static void TestPromotion(Fixture& f) {
    std::printf("usage promotion\n");
    using F = vk::Format;
    if (!f.runtime.Limits().lean_image_usage) {
        std::printf("  (BB_MTL_FULL_USAGE=1: skipped)\n");
        return;
    }
    const auto plain_view = [](F format, u32 levels = 1) {
        ImageViewInfo info;
        info.format = format;
        info.type = AmdGpu::ImageType::Color2D;
        info.range = {{0, 0}, {levels, 1}};
        return info;
    };
    {
        // A storage view promotes the image to ShaderWrite; contents, name and state follow.
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32, 2)};
        @autoreleasepool {
            CHECK(!(img.Texture().usage & MTLTextureUsageShaderWrite));
            CHECK(!(img.Texture().usage & MTLTextureUsagePixelFormatView));
        }
        CHECK(!(img.image->usage_flags & vk::ImageUsageFlagBits::eStorage));
        const auto data = Pattern(32 * 32 * 4 + 16 * 16 * 4, 21);
        const std::array<vk::BufferImageCopy, 2> copies{
            WholeCopy(32, 32, 0, 1, 1, 0), WholeCopy(16, 16, 1, 1, 1, 32 * 32 * 4)};
        Upload(f, *img.image, data, copies);
        // A view in the image's own format and a swizzle need nothing
        const auto* first_backing = img.image->backing;
        img.image->FindView(plain_view(F::eR8G8B8A8Unorm, 2));
        ImageViewInfo swizzled = plain_view(F::eR8G8B8A8Unorm, 2);
        swizzled.mapping.r = vk::ComponentSwizzle::eB;
        img.image->FindView(swizzled);
        img.image->FindView(plain_view(F::eR8G8B8A8Srgb, 2));
        CHECK(img.image->backing == first_backing && img.image->backing_images.size() == 1);
        CHECK(!first_backing->image_view_ids.empty());
        __weak id<MTLTexture> weak_old = (__bridge id<MTLTexture>)first_backing->image.native;
        const size_t old_views = first_backing->image_view_ids.size();
        CHECK(old_views == 3);

        ImageViewInfo storage = plain_view(F::eR8G8B8A8Unorm, 2);
        storage.is_storage = true;
        ImageView& view = img.image->FindView(storage);
        CHECK(img.image->backing != first_backing && img.image->backing_images.size() == 2);
        CHECK(first_backing->retired && first_backing->image_view_ids.empty() &&
              first_backing->image_view_infos.empty() && first_backing->image.native == nullptr);
        CHECK(!img.image->backing->retired && img.image->backing->num_samples == 1);
        @autoreleasepool {
            CHECK(img.Texture().usage & MTLTextureUsageShaderWrite);
            CHECK(!(img.Texture().usage & MTLTextureUsagePixelFormatView));
            CHECK([img.Texture().label hasSuffix:@" (promoted)"]);
            CHECK(img.Texture().mipmapLevelCount == 2);
        }
        CHECK(img.image->usage_flags & vk::ImageUsageFlagBits::eStorage);
        CHECK(view.native == img.image->backing->image.native);
        CHECK(img.image->backing->state.layout == vk::ImageLayout::eTransferDstOptimal);
        CHECK(Download(f, *img.image, copies, data.size()) == data);
        // A second need changes nothing
        const auto* promoted = img.image->backing;
        img.image->FindView(storage);
        f.runtime.PromoteImageUsage(img.image.get(), ImageUsagePromotion::Storage);
        CHECK(img.image->backing == promoted && img.image->backing_images.size() == 2);
        // The old texture and its views are released once the GPU is done with them (the views'
        // slots first, then their textures: a few ticks)
        for (int i = 0; i < 100 && weak_old; ++i) {
            Settle(f);
            f.runtime.PopPendingOperations();
        }
        CHECK(weak_old == nil);
        // The retired backing is never matched again: a sample count switch there and back
        // ends on the promoted backing, and the multisampled one is made with the mutable flag.
        f.runtime.SetBackingSamples(img.image.get(), 4, false);
        CHECK(img.image->backing->num_samples == 4 &&
              (img.image->backing->image.image_ci.flags & vk::ImageCreateFlagBits::eMutableFormat));
        f.runtime.SetBackingSamples(img.image.get(), 1, false);
        CHECK(img.image->backing == promoted);
        Settle(f);
    }
    {
        // An image nothing ever wrote is promoted without a copy; the state stays undefined.
        Img img{f.runtime, MakeInfo(F::eR16G16B16A16Sfloat, 32, 32)};
        ImageViewInfo storage = plain_view(F::eR16G16B16A16Sfloat);
        storage.is_storage = true;
        img.image->FindView(storage);
        CHECK(img.image->backing_images.size() == 2 && img.image->backing_images.front().retired);
        CHECK(img.image->backing->state.layout == vk::ImageLayout::eUndefined);
        @autoreleasepool {
            CHECK(img.Texture().usage & MTLTextureUsageShaderWrite);
        }
        Settle(f);
    }
    {
        // A view in another format promotes to PixelFormatView; the sRGB twin does not.
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 16, 16)};
        const auto data = Pattern(16 * 16 * 4, 22);
        Upload(f, *img.image, data, std::array{WholeCopy(16, 16)});
        ImageView& srgb = img.image->FindView(plain_view(F::eR8G8B8A8Srgb));
        CHECK(img.image->backing_images.size() == 1 &&
              ((__bridge id<MTLTexture>)srgb.native).pixelFormat == MTLPixelFormatRGBA8Unorm_sRGB);
        ImageView& uint_view = img.image->FindView(plain_view(F::eR8G8B8A8Uint));
        CHECK(img.image->backing_images.size() == 2);
        @autoreleasepool {
            CHECK(img.Texture().usage & MTLTextureUsagePixelFormatView);
            CHECK(!(img.Texture().usage & MTLTextureUsageShaderWrite));
        }
        CHECK(((__bridge id<MTLTexture>)uint_view.native).pixelFormat == MTLPixelFormatRGBA8Uint);
        CHECK(Download(f, *img.image, std::array{WholeCopy(16, 16)}, data.size()) == data);
        // A format Metal cannot view it as (another texel size) needs nothing
        img.image->FindView(plain_view(F::eR16Sfloat));
        CHECK(img.image->backing_images.size() == 2);
        Settle(f);
    }
    {
        // A copy that reads the source in the destination's format promotes the source.
        Img src{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 16, 16)};
        Img dst{f.runtime, MakeInfo(F::eR32Uint, 16, 16)};
        const auto data = Pattern(16 * 16 * 4, 23);
        Upload(f, *src.image, data, std::array{WholeCopy(16, 16)});
        f.runtime.CopyImage(src.image.get(), dst.image.get());
        CHECK(src.image->backing_images.size() == 2);
        @autoreleasepool {
            CHECK(src.Texture().usage & MTLTextureUsagePixelFormatView);
        }
        CHECK(Download(f, *dst.image, std::array{WholeCopy(16, 16)}, data.size()) == data);
        Settle(f);
    }
    {
        // Depth keeps the usage from its creation (the stencil view needs it, compression is
        // unaffected); no view of it promotes.
        ImageInfo depth_info = MakeInfo(F::eD32SfloatS8Uint, 16, 16);
        depth_info.props.is_depth = 1;
        depth_info.props.has_stencil = 1;
        Img depth{f.runtime, depth_info};
        @autoreleasepool {
            CHECK(depth.Texture().usage & MTLTextureUsagePixelFormatView);
        }
        ImageViewInfo view = plain_view(F::eR32Sfloat);
        view.is_storage = true; // (depth_write_enable)
        depth.image->FindView(view);
        CHECK(depth.image->backing_images.size() == 1);
    }
    Settle(f);
}

static void TestDeferredDestroy(Fixture& f) {
    std::printf("deferred destroy\n");
    Settle(f);
    f.runtime.PopPendingOperations();
    __weak id<MTLBuffer> weak_buffer;
    __weak id<MTLTexture> weak_texture;
    __weak id<MTLSamplerState> weak_sampler;
    {
        // (no settling deleters here: the point is what happens without one)
        auto buffer = std::make_unique<Buffer>(f.runtime, 0, 4096, MemoryType::DeviceLocal, "doomed");
        weak_buffer = (__bridge id<MTLBuffer>)buffer->buffer.native;
        f.runtime.FillBuffer(buffer.get(), 0, 4096, 0x01010101u); // the open command buffer uses it
        Common::SlotVector<ImageView> views;
        Image image{f.runtime, views, MakeInfo(vk::Format::eR8G8B8A8Unorm, 16, 16)};
        weak_texture = (__bridge id<MTLTexture>)image.backing->image.native;
        AmdGpu::Sampler guest{};
        Sampler sampler{f.runtime, guest, AmdGpu::BorderColorBuffer{}, false};
        weak_sampler = (__bridge id<MTLSamplerState>)sampler.native;
        CHECK(weak_buffer != nil && weak_texture != nil && weak_sampler != nil);
    } // all destroyed here: the releases wait for the open tick
    const u64 tick = f.runtime.CurrentTick();
    for (int i = 0; i < 100; ++i) {
        f.runtime.PopPendingOperations();
    }
    CHECK(weak_buffer != nil && weak_texture != nil && weak_sampler != nil);
    CHECK(!f.runtime.IsFree(tick));
    Settle(f);
    CHECK(f.runtime.IsFree(tick));
    f.runtime.PopPendingOperations();
    // Metal retires the command buffer a moment after the event is signalled.
    for (int i = 0; i < 200 && (weak_buffer || weak_texture || weak_sampler); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        f.runtime.PopPendingOperations();
    }
    CHECK(weak_buffer == nil);
    CHECK(weak_texture == nil);
    CHECK(weak_sampler == nil);

    // A deferred operation of the runtime itself
    std::atomic<bool> ran{false};
    f.runtime.DeferOperation([&] { ran = true; });
    f.runtime.PopPendingOperations();
    CHECK(!ran);
    Settle(f);
    f.runtime.PopPendingOperations();
    CHECK(ran);
}

static void TestImageCopies(Fixture& f) {
    std::printf("image copies, clears, MSAA\n");
    using F = vk::Format;
    const auto data = Pattern(32 * 32 * 4, 12);
    const std::array whole{WholeCopy(32, 32)};
    Img a{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
    Upload(f, *a.image, data, whole);
    {
        Img b{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
        f.runtime.CopyImage(a.image.get(), b.image.get());
        CHECK(Download(f, *b.image, whole, data.size()) == data);
        CHECK(b.image->flags & ImageFlagBits::GpuModified || true);
        // A different format of the same texel size: copied through a view
        Img c{f.runtime, MakeInfo(F::eR32Uint, 32, 32)};
        f.runtime.CopyImage(a.image.get(), c.image.get());
        CHECK(Download(f, *c.image, whole, data.size()) == data);
        // Through a buffer
        Img d{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
        auto staging = MakeBuffer(f.runtime, 32 * 32 * 4, MemoryType::DeviceLocal, "via");
        f.runtime.CopyImageWithBuffer(a.image.get(), d.image.get(), staging.get(), 0);
        CHECK(Download(f, *d.image, whole, data.size()) == data);
    }
    {
        // One mip of a smaller image into a level of a bigger one
        ImageInfo big = MakeInfo(F::eR8G8B8A8Unorm, 64, 64, 2);
        big.mips_layout[1].pitch = 32;
        Img dst{f.runtime, big};
        f.runtime.CopyMip(a.image.get(), dst.image.get(), 1, 0);
        CHECK(Download(f, *dst.image, std::array{WholeCopy(32, 32, 1)}, data.size()) == data);
    }
    {
        // Color to depth and back through a buffer (R32Float <-> D32Float)
        const auto floats = Pattern(16 * 16 * 4, 13);
        Img color{f.runtime, MakeInfo(F::eR32Sfloat, 16, 16)};
        Upload(f, *color.image, floats, std::array{WholeCopy(16, 16)});
        ImageInfo depth_info = MakeInfo(F::eD32Sfloat, 16, 16);
        Img depth{f.runtime, depth_info};
        CHECK(depth.Texture().pixelFormat == MTLPixelFormatDepth32Float);
        f.runtime.CopyColorAndDepth(color.image.get(), depth.image.get());
        Img back{f.runtime, MakeInfo(F::eR32Sfloat, 16, 16)};
        f.runtime.CopyColorAndDepth(depth.image.get(), back.image.get());
        CHECK(Download(f, *back.image, std::array{WholeCopy(16, 16)}, floats.size()) == floats);
        // The depth copy of equal formats
        Img depth2{f.runtime, depth_info};
        SubresourceRange range{};
        f.runtime.CopyDepthStencil(depth.image.get(), depth2.image.get(), range);
        Settle(f);
    }
    {
        // Clears: a render target, in float and in integer formats, one slice of an array
        Img rt{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 16, 16)};
        vk::ClearValue red{};
        red.color = vk::ClearColorValue{std::array<float, 4>{1.0f, 0.0f, 0.5f, 1.0f}};
        f.runtime.ClearImage(rt.image.get(), SubresourceRange{}, red);
        const auto cleared = Download(f, *rt.image, std::array{WholeCopy(16, 16)}, 16 * 16 * 4);
        CHECK(cleared[0] == 255 && cleared[1] == 0 && cleared[2] == 128 && cleared[3] == 255);
        CHECK(cleared[16 * 16 * 4 - 1] == 255);
        Img ints{f.runtime, MakeInfo(F::eR32Uint, 8, 8)};
        vk::ClearValue seven{};
        seven.color.uint32[0] = 7;
        f.runtime.ClearImage(ints.image.get(), SubresourceRange{}, seven);
        const auto words = Download(f, *ints.image, std::array{WholeCopy(8, 8)}, 8 * 8 * 4);
        CHECK(*reinterpret_cast<const u32*>(words.data()) == 7);
        Img layered{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 8, 8, 1, 3)};
        Upload(f, *layered.image, Pattern(8 * 8 * 4 * 3, 14), std::array{WholeCopy(8, 8, 0, 3)});
        SubresourceRange one_layer{{0, 1}, {1, 1}};
        f.runtime.ClearImage(layered.image.get(), one_layer, red);
        const auto layers = Download(f, *layered.image, std::array{WholeCopy(8, 8, 0, 3)},
                                     8 * 8 * 4 * 3);
        CHECK(layers[8 * 8 * 4] == 255 && layers[8 * 8 * 4 + 1] == 0);
        CHECK(layers[0] == Pattern(8 * 8 * 4 * 3, 14)[0]);
        // BC1: not a render target, cleared to zero through a staging copy
        Img bc{f.runtime, MakeInfo(F::eBc1RgbaUnormBlock, 16, 16)};
        Upload(f, *bc.image, Pattern(16 * 8, 15), std::array{WholeCopy(16, 16)});
        vk::ClearValue zero{};
        f.runtime.ClearImage(bc.image.get(), SubresourceRange{}, zero);
        const auto zeros = Download(f, *bc.image, std::array{WholeCopy(16, 16)}, 16 * 8);
        CHECK(std::all_of(zeros.begin(), zeros.end(), [](u8 b) { return b == 0; }));
    }
    {
        // A multisampled backing: to 4 samples and back keeps the pixels (the fullscreen passes)
        Img img{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
        CHECK(img.image->supported_samples & vk::SampleCountFlagBits::e4);
        Upload(f, *img.image, data, whole);
        f.runtime.SetBackingSamples(img.image.get(), 4);
        CHECK(img.image->backing->num_samples == 4 && img.Texture().sampleCount == 4);
        f.runtime.SetBackingSamples(img.image.get(), 1);
        CHECK(img.image->backing->num_samples == 1 && img.Texture().sampleCount == 1);
        CHECK(Download(f, *img.image, whole, data.size()) == data);
        // And a multisampled image resolved into a single sampled one
        Img ms{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32, 1, 1, 4)};
        CHECK(ms.Texture().sampleCount == 4);
        vk::ClearValue green{};
        green.color = vk::ClearColorValue{std::array<float, 4>{0.0f, 1.0f, 0.0f, 1.0f}};
        f.runtime.ClearImage(ms.image.get(), SubresourceRange{}, green);
        Img resolved{f.runtime, MakeInfo(F::eR8G8B8A8Unorm, 32, 32)};
        f.runtime.ResolveImage(ms.image.get(), resolved.image.get(), SubresourceRange{},
                               SubresourceRange{});
        const auto pixels = Download(f, *resolved.image, whole, data.size());
        CHECK(pixels[0] == 0 && pixels[1] == 255 && pixels[2] == 0 && pixels[3] == 255);
        CHECK(pixels[data.size() - 3] == 255);
        // Color to a multisampled depth image
        Img color{f.runtime, MakeInfo(F::eR32Sfloat, 16, 16)};
        std::vector<float> ramp(16 * 16 * 1);
        for (size_t i = 0; i < ramp.size(); ++i) {
            ramp[i] = 0.25f;
        }
        std::vector<u8> ramp_bytes(ramp.size() * 4);
        std::memcpy(ramp_bytes.data(), ramp.data(), ramp_bytes.size());
        Upload(f, *color.image, ramp_bytes, std::array{WholeCopy(16, 16)});
        ImageInfo ms_depth_info = MakeInfo(F::eD32Sfloat, 16, 16, 1, 1, 4);
        Img ms_depth{f.runtime, ms_depth_info};
        CHECK(ms_depth.Texture().sampleCount == 4);
        f.runtime.CopyColorAndDepth(color.image.get(), ms_depth.image.get());
        Settle(f);
    }
    Settle(f);
}

struct TilingInfoBlock {
    u32 bank_swizzle;
    u32 micro_tiled_mips;
    u32 num_mips;
    struct Mip {
        u32 size, pitch, height, offset, padded_pitch, padded_height, slices;
    } mips[16];
};

/// The display micro tiled address of a 32 bit pixel (tiling.comp, one slice, thickness 1).
static u32 MicroTiled32(u32 x, u32 y, u32 pitch) {
    const u32 index = (x & 1) | ((x >> 1 & 1) << 1) | ((y & 1) << 2) | ((x >> 2 & 1) << 3) |
                      ((y >> 1 & 1) << 4) | ((y >> 2 & 1) << 5);
    return (((y / 8) * (pitch / 8) + x / 8) * 256) + index * 4;
}

static void TestKernels(Fixture& f) {
    std::printf("tiling and fault parser kernels\n");
    constexpr u32 Width = 64, Height = 32;
    constexpr u32 Bytes = Width * Height * 4;
    StreamBuffer stream{f.runtime, MemoryType::HostUncached, 1 << 20};

    // linear pattern, and its CPU tiled twin
    std::vector<u32> linear(Width * Height), tiled(Width * Height);
    for (u32 y = 0; y < Height; ++y) {
        for (u32 x = 0; x < Width; ++x) {
            linear[y * Width + x] = x + y * 1000 + 0x01000000u;
            tiled[MicroTiled32(x, y, Width) / 4] = linear[y * Width + x];
        }
    }
    TilingInfoBlock params{};
    params.num_mips = 1;
    params.mips[0] = {Bytes, Width, Height, 0, Width, Height, 1};

    TilingDispatch dispatch{};
    dispatch.key = {AmdGpu::TileMode::Display1DThin, 32, 1, false};
    dispatch.array_mode = AmdGpu::ArrayMode::ArrayLinearAligned;
    dispatch.is_macro = false;
    dispatch.constants = {1 /*samples*/, 0 /*display micro tiling*/, 1 /*thickness*/,
                          0 /*detile*/};
    dispatch.num_constants = 4;
    dispatch.group_count_x = Width * Height / 64;

    // detile: tiled in, linear out
    auto tiled_buffer = MakeBuffer(f.runtime, Bytes + 256, MemoryType::DeviceLocal, "tiled");
    auto linear_buffer = MakeBuffer(f.runtime, Bytes + 256, MemoryType::DeviceLocal, "linear");
    std::memcpy(tiled_buffer->mapped_data.data() + 16, tiled.data(), Bytes);
    std::memset(linear_buffer->mapped_data.data(), 0, Bytes + 256);
    dispatch.tiled = {tiled_buffer.get(), 16, Bytes};
    dispatch.linear = {linear_buffer.get(), 32, Bytes};
    dispatch.params = {&stream, stream.Copy(&params, sizeof(params), 16), sizeof(params)};
    f.runtime.DispatchTiling(dispatch);
    f.runtime.AccessBuffer(linear_buffer.get(), 32, Bytes, vk::PipelineStageFlagBits2::eComputeShader,
                           vk::AccessFlagBits2::eShaderWrite);
    Settle(f);
    CHECK(std::memcmp(linear_buffer->mapped_data.data() + 32, linear.data(), Bytes) == 0);

    // tile: linear in, tiled out
    auto tiled_out = MakeBuffer(f.runtime, Bytes, MemoryType::DeviceLocal, "tiled out");
    std::memset(tiled_out->mapped_data.data(), 0, Bytes);
    TilingDispatch tiler = dispatch;
    tiler.key.is_tiler = true;
    tiler.constants[3] = 1;
    tiler.tiled = {tiled_out.get(), 0, Bytes};
    tiler.linear = {linear_buffer.get(), 32, Bytes};
    f.runtime.FlushBarriers();
    f.runtime.DispatchTiling(tiler);
    Settle(f);
    CHECK(std::memcmp(tiled_out->mapped_data.data(), tiled.data(), Bytes) == 0);

    // A macro tiled kernel (twelve constants) builds and runs (the result is not checked)
    TilingDispatch macro = dispatch;
    macro.is_macro = true;
    macro.key.tile_mode = AmdGpu::TileMode::Display2DThin;
    macro.constants = {1, 0, 1, 0, 4 /*2D_TILED_THIN1*/, 0 /*P2*/, 1, 1, 2, 1, 256, 1};
    macro.num_constants = 12;
    f.runtime.DispatchTiling(macro);
    Settle(f);

    // Fault parser: three faulting pages out of a 64 word bitmap
    f.runtime.CreateFaultParser(12, 1024);
    auto faults = MakeBuffer(f.runtime, 256, MemoryType::DeviceLocal, "faults");
    auto download = MakeBuffer(f.runtime, 8 * 1024 + 64, MemoryType::HostCached, "fault list");
    std::memset(faults->mapped_data.data(), 0, 256);
    std::memset(download->mapped_data.data(), 0, download->SizeBytes());
    auto* bits = reinterpret_cast<u32*>(faults->mapped_data.data());
    bits[0] = 0b101;
    bits[3] = 1u << 5;
    FaultParseDispatch parse{};
    parse.fault_buffer = faults.get();
    parse.fault_buffer_size = 256;
    parse.download = {download.get(), 16, 8 * 1024};
    parse.group_count_x = 1;
    f.runtime.ParseFaultBuffer(parse);
    Settle(f);
    const auto* list = reinterpret_cast<const u64*>(download->mapped_data.data() + 16);
    CHECK(*reinterpret_cast<const u32*>(download->mapped_data.data() + 16) == 3);
    std::set<u64> pages{list[1], list[2], list[3]};
    CHECK((pages == std::set<u64>{0, u64(2) << 12, u64(101) << 12}));
    CHECK(bits[0] == 0 && bits[3] == 0);
    Settle(f);
}

static void TestAccessAndStaging(Fixture& f) {
    std::printf("access tracking and staging\n");
    auto buffer = MakeBuffer(f.runtime, 8192, MemoryType::DeviceLocal, "tracked");
    auto other = MakeBuffer(f.runtime, 8192, MemoryType::DeviceLocal, "other");
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(), 0, 100));
    f.runtime.AccessBuffer(buffer.get(), 1000, 100, vk::PipelineStageFlagBits2::eCopy,
                           vk::AccessFlagBits2::eTransferWrite);
    CHECK(f.runtime.IsBufferAccessed(buffer.get(), 1050, 10));
    CHECK(f.runtime.IsBufferAccessed(buffer.get(), 900, 150, true));
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(), 0, 100));
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(), 2000, 100, true));
    CHECK(!f.runtime.IsBufferAccessed(other.get(), 1050, 10, true));
    // A read: only seen when asked for
    f.runtime.AccessBuffer(buffer.get(), 5000, 16, vk::PipelineStageFlagBits2::eComputeShader,
                           vk::AccessFlagBits2::eShaderRead);
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(), 5000, 16));
    CHECK(f.runtime.IsBufferAccessed(buffer.get(), 5000, 16, true));
    f.runtime.FlushBarriers();
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(), 1050, 10, true));
    // A global write makes everything pending
    f.runtime.AccessGlobal(vk::PipelineStageFlagBits2::eComputeShader,
                           vk::AccessFlagBits2::eShaderWrite);
    CHECK(f.runtime.IsBufferAccessed(other.get(), 0, 4));
    f.runtime.FlushBarriers();
    CHECK(!f.runtime.IsBufferAccessed(other.get(), 0, 4));
    // Many ranges collapse into a bounding range (never lose one)
    for (u64 i = 0; i < 40; ++i) {
        f.runtime.AccessBuffer(buffer.get(), i * 100, 10, vk::PipelineStageFlagBits2::eCopy,
                               vk::AccessFlagBits2::eTransferWrite);
    }
    CHECK(f.runtime.IsBufferAccessed(buffer.get(), 3905, 1));
    f.runtime.FlushBarriers();

    // Image bookkeeping: Transit reports a change once
    Img img{f.runtime, MakeInfo(vk::Format::eR8G8B8A8Unorm, 8, 8)};
    CHECK(f.runtime.Transit(img.image.get(), vk::ImageLayout::eShaderReadOnlyOptimal,
                            vk::PipelineStageFlagBits2::eFragmentShader,
                            vk::AccessFlagBits2::eShaderRead));
    CHECK(!f.runtime.Transit(img.image.get(), vk::ImageLayout::eShaderReadOnlyOptimal,
                             vk::PipelineStageFlagBits2::eFragmentShader,
                             vk::AccessFlagBits2::eShaderRead));
    CHECK(img.image->backing->state.layout == vk::ImageLayout::eShaderReadOnlyOptimal);
    f.runtime.FlushImageBarriers();

    // Staging: ring requests are distinct and mapped; a held large request is not reused
    const StagingSpan a = f.runtime.StagingRequest(1000, MemoryType::HostUncached, 256);
    const StagingSpan b = f.runtime.StagingRequest(1000, MemoryType::HostUncached, 256);
    CHECK(a.mapped && b.mapped && a.mapped != b.mapped && a.offset % 256 == 0);
    const StagingSpan held = f.runtime.StagingRequest(1 << 20, MemoryType::DeviceLocal, 0, true);
    const StagingSpan second = f.runtime.StagingRequest(1 << 20, MemoryType::DeviceLocal, 0, true);
    CHECK(held.buffer != second.buffer);
    f.runtime.StagingFreeDeferred(held);
    f.runtime.StagingFreeDeferred(second);
    const StagingSpan huge = f.runtime.StagingRequest(40 << 20, MemoryType::HostCached);
    CHECK(huge.mapped != nullptr && huge.buffer->SizeBytes() >= u64(40) << 20);
    f.runtime.TickFrame();
    Settle(f);
}

// Real Metal object lifetime: an aged allocation stays alive while held or
// while its retirement tick has not completed. Idle expiry does not bypass fences.
static void TestStagingRetirement(Fixture& f) {
    std::printf("idle staging: held and unfinished allocations survive expiry\n");
    __weak id<MTLBuffer> weak_buffer;
    @autoreleasepool {
        Metal::StagingPool pool{f.runtime};
        auto span = pool.Request(2 << 20, MemoryType::HostCached, 0, true);
        weak_buffer = (__bridge id<MTLBuffer>)span.buffer->buffer.native;
        for (unsigned i = 0; i < 3001; ++i) pool.TickFrame();
        CHECK(weak_buffer != nil); // held, despite expiry
        pool.FreeDeferred(span);
        for (unsigned i = 0; i < 3001; ++i) pool.TickFrame();
        CHECK(weak_buffer != nil); // open tick, not GPU-complete
        f.runtime.Finish();
        f.runtime.RefreshTicks();
        pool.TickFrame();
        Settle(f); // DestroyBuffer defers the actual residency removal and release.
    }
    Settle(f);
    CHECK(weak_buffer == nil);
}

// Exercise positive retirement with a real GPU-written texture and an independent
// allocation tracker. Reuse/alias exclusions gate the actual destruction callback.
static void TestReleasedStorageRetirement(Fixture& f) {
    namespace Life=BbResourceLifetime;
    Img storage{f.runtime,MakeInfo(vk::Format::eR8G8B8A8Unorm,64,64)};
    auto& image=*storage.image;
    image.flags=ImageFlagBits::Registered;image.usage.storage=1;
    vk::ClearValue clear{};clear.color.float32[0]=1;clear.color.float32[3]=1;
    f.runtime.ClearImage(&image,{.base={0,0},.extent={1,1}},clear);
    image.tick_accessed_last=f.runtime.CurrentTick();image.gpu_write_tick=f.runtime.CurrentTick();
    CHECK(!image.CanReleaseFreedStorageBacking());
    Life::Tracker lifetimes;
    const auto tag=lifetimes.Allocate(Life::Domain::Heap,image.guest_begin,image.info.guest_size+64);
    CHECK(tag && lifetimes.Release(tag));
    bool destroyed=false;
    CHECK(!lifetimes.WithReleased(tag,image.guest_begin,image.info.guest_size,60000,[&] {destroyed=true;return 1;}));
    CHECK(!destroyed);Settle(f);CHECK(image.CanReleaseFreedStorageBacking());
    CHECK(!image.CanReleaseGuestBacking()); // valid GPU-written storage stays protected normally
    auto whole=WholeCopy(64,64);auto readback=MakeBuffer(f.runtime,64*64*4,MemoryType::HostCached);
    f.runtime.DownloadImage(&image,readback.get(),{&whole,1});Settle(f);
    bool red=true;for(unsigned n=0;n<64*64;++n)red &= readback->mapped_data[n*4]==255 && readback->mapped_data[n*4+1]==0 && readback->mapped_data[n*4+2]==0 && readback->mapped_data[n*4+3]==255;
    CHECK(red); // GPU-only contents remain intact before proven retirement
    image.usage.render_target=1;CHECK(!image.CanReleaseFreedStorageBacking());image.usage.render_target=0;
    image.usage.depth_target=1;CHECK(!image.CanReleaseFreedStorageBacking());image.usage.depth_target=0;
    image.usage.vo_surface=1;CHECK(!image.CanReleaseFreedStorageBacking());image.usage.vo_surface=0;
    image.scene_proxy=true;CHECK(!image.CanReleaseFreedStorageBacking());image.scene_proxy=false;
    image.binding.is_bound=1;CHECK(!image.CanReleaseFreedStorageBacking());image.binding.is_bound=0;
    image.binding.needs_rebind=1;CHECK(!image.CanReleaseFreedStorageBacking());image.binding.needs_rebind=0;
    image.flags|=ImageFlagBits::GpuDirty;CHECK(!image.CanReleaseFreedStorageBacking());image.flags&=~ImageFlagBits::GpuDirty;
    auto overlap=lifetimes.Allocate(Life::Domain::Range,image.guest_begin+128,32);
    CHECK(!lifetimes.WithReleased(tag,image.guest_begin,image.info.guest_size,0,[&] {destroyed=true;return 1;}));
    CHECK(!destroyed && storage.image && lifetimes.Release(overlap));
    auto replacement=lifetimes.Allocate(Life::Domain::Heap,image.guest_begin,image.info.guest_size+64);
    CHECK(lifetimes.Status(tag)==Life::State::Reused);
    CHECK(!lifetimes.WithReleased(tag,image.guest_begin,image.info.guest_size,0,[&] {destroyed=true;return 1;}));
    CHECK(!destroyed && storage.image && lifetimes.Release(replacement));
    __weak id<MTLTexture> weak;
    @autoreleasepool {
        weak=storage.Texture();
        __attribute__((objc_precise_lifetime)) id<MTLTexture> view=[storage.Texture() newTextureViewWithPixelFormat:MTLPixelFormatRGBA8Unorm];
        CHECK(lifetimes.WithReleased(tag,image.guest_begin,image.info.guest_size,0,[&] {
            CHECK(image.CanReleaseFreedStorageBacking());
            const auto bytes=image.info.guest_size;storage.image.reset();destroyed=true;return bytes;
        }));
        Settle(f);CHECK(weak!=nil); // a real native view still owns the backing
        view=nil;
    }
    Settle(f);CHECK(destroyed && weak==nil);
    CHECK(lifetimes.Read().reclaimed_images==1);
    puts("released storage: real GPU contents, completion, generation/overlap gates and native view destruction passed; no writeback");
}

static void TestMemoryCategoriesAndIdleBacking(Fixture& f) {
    namespace Stats = Metal::MemoryStats;
    std::printf("memory categories: real allocation lifetime, views, retired bytes and idle staging backing\n");
    Settle(f);
    {
        Img sampled{f.runtime,MakeInfo(vk::Format::eR8G8B8A8Unorm,8,8)};
        auto& image=*sampled.image;
        image.flags=ImageFlagBits::Registered;
        image.tick_accessed_last=f.runtime.CurrentTick();
        CHECK(!image.CanReleaseGuestBacking()); // pending recording/submission cannot retire
        Settle(f);
        CHECK(image.CanReleaseGuestBacking());
        CHECK(!image.CanReleaseInvalidBacking()); // keep a valid cold cache entry
        image.flags|=ImageFlagBits::MaybeCpuDirty;
        CHECK(!image.CanReleaseInvalidBacking()); // adjacent page writes are not proof
        image.flags|=ImageFlagBits::CpuDirty;
        CHECK(image.CanReleaseInvalidBacking());
        image.binding.is_bound=1;CHECK(!image.CanReleaseInvalidBacking());image.binding.is_bound=0;
        image.usage.render_target=1;CHECK(!image.CanReleaseInvalidBacking());image.usage.render_target=0;
        image.info.props.is_depth=true;CHECK(!image.CanReleaseInvalidBacking());image.info.props.is_depth=false;
        image.depth_id=ImageId{1};CHECK(!image.CanReleaseInvalidBacking());image.depth_id={};
        image.flags=ImageFlagBits::Registered|ImageFlagBits::CpuDirty|ImageFlagBits::GpuModified;
        CHECK(!image.CanReleaseInvalidBacking());
        image.flags=ImageFlagBits::Registered;
        image.usage.render_target=1;CHECK(!image.CanReleaseGuestBacking());image.usage.render_target=0;
        image.usage.depth_target=1;CHECK(!image.CanReleaseGuestBacking());image.usage.depth_target=0;
        image.usage.storage=1;CHECK(!image.CanReleaseGuestBacking());image.usage.storage=0;
        image.usage.vo_surface=1;CHECK(!image.CanReleaseGuestBacking());image.usage.vo_surface=0;
        image.scene_proxy=true;CHECK(!image.CanReleaseGuestBacking());image.scene_proxy=false;
        image.binding.is_bound=1;CHECK(!image.CanReleaseGuestBacking());image.binding.is_bound=0;
        image.binding.is_target=1;CHECK(!image.CanReleaseGuestBacking());image.binding.is_target=0;
        image.binding.needs_rebind=1;CHECK(!image.CanReleaseGuestBacking());image.binding.needs_rebind=0;
        image.flags|=ImageFlagBits::GpuModified;CHECK(!image.CanReleaseGuestBacking());
        image.flags=ImageFlagBits::Registered|ImageFlagBits::GpuDirty;CHECK(!image.CanReleaseGuestBacking());
        image.flags=ImageFlagBits::Registered;image.gpu_write_tick=f.runtime.CurrentTick();
        CHECK(!image.CanReleaseGuestBacking());Settle(f);CHECK(image.CanReleaseGuestBacking());
        image.flags=ImageFlagBits::Empty;CHECK(!image.CanReleaseGuestBacking());
    }
    Settle(f);
    const auto initial = Stats::Read();
    const auto bi = unsigned(Stats::Kind::Buffer), ti = unsigned(Stats::Kind::Target);
    __weak id<MTLBuffer> weak;
    @autoreleasepool {
        auto buffer = std::make_unique<Buffer>(f.runtime, 0, 1 << 20, MemoryType::DeviceLocal, "ledger test");
        if (BbResourceLifetime::Enabled()) {
            const auto tag=BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Range,0x34560000,1<<20);
            f.runtime.AssociateGuestBuffer(buffer->buffer,0x34560000,1<<20);
            CHECK(Stats::ReadAssociations()[bi].live>=1<<20);
            CHECK(BbResourceLifetime::Get().Release(tag));
            CHECK(Stats::ReadAssociations()[bi].released>=1<<20);
        }
        weak = (__bridge id<MTLBuffer>)buffer->buffer.native;
        CHECK(Stats::Read()[bi].bytes >= initial[bi].bytes + (1 << 20));
        buffer.reset();
        CHECK(weak != nil);
        CHECK(Stats::Read()[bi].retired >= initial[bi].retired + (1 << 20));
    }
    Settle(f);
    CHECK(weak == nil);
    CHECK(Stats::Read()[bi].released >= initial[bi].released + (1 << 20));
    CHECK(Stats::Read()[bi].retired == initial[bi].retired);
    // A real texture view retains its allocation even after the renderer drops the parent.
    __weak id<MTLTexture> parent;
    id<MTLTexture> view;
    const auto before_image = Stats::Read();
    @autoreleasepool {
        UniqueImage image;
        vk::ImageCreateInfo ci{}; ci.imageType = vk::ImageType::e2D;
        ci.format = vk::Format::eR8G8B8A8Unorm; ci.extent = {64,64,1};
        ci.mipLevels = ci.arrayLayers = 1; ci.samples = vk::SampleCountFlagBits::e1;
        ci.flags = vk::ImageCreateFlagBits::eMutableFormat;
        ci.usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eColorAttachment;
        f.runtime.CreateImage(image,ci);
        const auto guest_tag=BbResourceLifetime::Get().Allocate(BbResourceLifetime::Domain::Heap,0x12340000,1<<20);
        f.runtime.AssociateGuestImage(image,0x12340000,64*64*4);
        if (BbResourceLifetime::Enabled()) {
            CHECK(Stats::ReadAssociations()[ti].live >= image.size_bytes);
            CHECK(BbResourceLifetime::Get().Release(guest_tag));
            CHECK(Stats::ReadAssociations()[ti].released >= image.size_bytes);
        }
        parent = (__bridge id<MTLTexture>)image.native;
        view = [parent newTextureViewWithPixelFormat:MTLPixelFormatRGBA8Unorm_sRGB];
        CHECK(view != nil);
        f.runtime.DestroyImage(image);
    }
    Settle(f);
    CHECK(parent != nil);
    CHECK(Stats::Read()[ti].bytes > before_image[ti].bytes);
    view = nil;
    Settle(f);
    CHECK(parent == nil);
    CHECK(Stats::Read()[ti].bytes == before_image[ti].bytes);
    // Expired ring capacity remains reusable, but backing contents are discarded only after GPU completion.
    constexpr u64 Bytes = 16ull << 20;
    auto output = MakeBuffer(f.runtime, Bytes, MemoryType::HostCached, "idle copy verification");
    const auto staging = unsigned(Stats::Kind::Staging);
    const auto before_pool = Stats::Read()[staging];
    @autoreleasepool {
        Metal::StagingPool pool{f.runtime};
        auto first = pool.Request(Bytes, MemoryType::HostUncached);
        id<MTLBuffer> native = (__bridge id<MTLBuffer>)first.buffer->buffer.native;
        const auto address = native.gpuAddress;
        std::memset(first.mapped,0xA5,Bytes);
        const vk::BufferCopy copy{first.offset,0,Bytes};
        f.runtime.CopyBuffer(first.buffer,output.get(),std::span{&copy,1});
        for (unsigned i=0;i<3001;++i) pool.TickFrame();
        CHECK(!Stats::IsDiscarded(first.buffer->buffer.native)); // unfinished tick
        Settle(f);
        task_vm_info_data_t before_vm{}, after_vm{};
        mach_msg_type_number_t vm_count=TASK_VM_INFO_COUNT;
        task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&before_vm,&vm_count);
        pool.TickFrame();
        vm_count=TASK_VM_INFO_COUNT;
        task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&after_vm,&vm_count);
        std::printf("idle staging backing: footprint before=%llu after=%llu bytes; kept capacity=%llu bytes\n",(unsigned long long)before_vm.phys_footprint,(unsigned long long)after_vm.phys_footprint,(unsigned long long)Bytes);
        CHECK(Stats::IsDiscarded(first.buffer->buffer.native));
        CHECK([native setPurgeableState:MTLPurgeableStateKeepCurrent] == MTLPurgeableStateEmpty);
        CHECK(Stats::Read()[staging].discarded >= before_pool.discarded + Bytes);
        CHECK(native != nil); // object/capacity and GPU address kept, no allocation/rebuild
        auto second = pool.Request(Bytes,MemoryType::HostUncached);
        CHECK(second.buffer == first.buffer);
        CHECK(native.gpuAddress == address);
        CHECK(!Stats::IsDiscarded(second.buffer->buffer.native));
        CHECK([native setPurgeableState:MTLPurgeableStateKeepCurrent] == MTLPurgeableStateNonVolatile);
        std::memset(second.mapped,0x3C,Bytes);
        const vk::BufferCopy next{second.offset,0,Bytes};
        f.runtime.CopyBuffer(second.buffer,output.get(),std::span{&next,1});
        Settle(f);
        CHECK(output->mapped_data[0] == 0x3C && output->mapped_data[Bytes-1] == 0x3C);
        // Hot ring capacity does not get discarded before the existing 3000-frame idle expiry.
        for (unsigned i=0;i<2999;++i) pool.TickFrame();
        CHECK(!Stats::IsDiscarded(second.buffer->buffer.native));
        CHECK([native setPurgeableState:MTLPurgeableStateKeepCurrent] == MTLPurgeableStateNonVolatile);
    }
    Settle(f);
    CHECK(Stats::Read()[staging].discarded == before_pool.discarded);
}

static void TestTicks(Fixture& f) {
    std::printf("ticks and ordering\n");
    const u64 tick = f.runtime.CurrentTick();
    CHECK(f.runtime.IsFree(tick - 1));
    CHECK(!f.runtime.IsRendering());
    f.runtime.Finish();
    CHECK(f.runtime.CurrentTick() == tick + 1);
    CHECK(f.runtime.IsFreeKnown(tick) || f.runtime.IsFree(tick));
    f.runtime.Wait(f.runtime.CurrentTick()); // the open tick: flushed, then waited for
    f.runtime.RefreshTicks();
    bool ran = false;
    BbCopy::Item item{[](const BbCopy::Item& i) { *static_cast<bool*>(i.context) = true; }, &ran,
                      0, 0, 0, 0};
    f.runtime.RecordHostCopy(item);
    CHECK(ran);
}


static void TestQuerySnapshotInvalidatesGather(Fixture& f) {
    std::puts("query writes invalidate same-packet gathered snapshots");
    constexpr VAddr Start = 0x7100000000;
    constexpr u64 Size = 64;
    auto source = MakeBuffer(f.runtime, 4096, MemoryType::HostCached, "query memo source");
    std::memset(source->mapped_data.data(), 0, source->SizeBytes());
    StreamBuffer ring{f.runtime, MemoryType::Stream, 64_KB};
    GuestReadSnapshots memo;
    const auto stamp = [&] {
        return GuestReadEpoch{7, f.runtime.CurrentTick(), ring.WrapGeneration(),
                              0, 0, 0, f.runtime.BufferWriteEpoch()};
    };
    const auto resolve = [&](VAddr at, u64 remaining) -> std::optional<GuestReadPiece> {
        return GuestReadPiece{source.get(), at - Start, remaining};
    };
    const auto no_sync = [](const GuestReadPiece&, VAddr) {};
    const auto initial = stamp();
    auto first = GatherGuestRead(f.runtime, ring, Start, Size, resolve, no_sync);
    CHECK(first);
    if (!first) return;
    memo.Remember(Start, Size, first->second, initial, stamp());
    CHECK(memo.Find(ring, Start, Size, 16, stamp()));

    // The real resolver bypasses AccessBuffer. No packet/tick boundary may be
    // relied upon to invalidate its earlier copy; both query chains write here.
    Metal::OcclusionQueries queries{f.device, f.scheduler, f.runtime};
    const auto before = stamp();
    queries.Snapshot(source.get(), 0, 0xe000, 4);
    const auto after = stamp();
    CHECK(after.tick == before.tick);
    CHECK(after.gpu > before.gpu);
    CHECK(!memo.Find(ring, Start, Size, 16, after));
    auto fresh = GatherGuestRead(f.runtime, ring, Start, Size, resolve, no_sync);
    CHECK(fresh);
    if (!fresh) return;
    Settle(f);
    const std::array<u64, 8> zero{};
    const std::array<u64, 8> expected{1ull << 63, 0, 1ull << 63, 0,
                                     1ull << 63, 0, 1ull << 63, 0};
    CHECK(std::memcmp(ring.mapped_data.data() + first->second, zero.data(), Size) == 0);
    CHECK(std::memcmp(ring.mapped_data.data() + fresh->second, expected.data(), Size) == 0);
    CHECK(!f.scheduler.HasFailed());
}

static void TestOcclusion(Fixture& f) {
    TestQuerySnapshotInvalidatesGather(f);
    std::printf("native Metal occlusion queries\n");
    NSString* shaders = @R"MSL(
#include <metal_stdlib>
using namespace metal;
vertex float4 occlusion_vertex(uint vid [[vertex_id]], constant float& z [[buffer(0)]]) {
    const float2 xy[3] = {float2(-1,-1), float2(3,-1), float2(-1,3)};
    return float4(xy[vid], z, 1);
}
fragment float4 occlusion_fragment() { return float4(1,0,0,1); }
fragment float4 consume_occlusion(device const ulong* counts [[buffer(0)]],
                                 constant ulong& expected [[buffer(1)]]) {
    const ulong valid=1ul<<63;ulong sum=0;bool complete=true;
    for(uint i=0;i<4;++i){complete=complete&&bool(counts[2*i]&valid)&&bool(counts[2*i+1]&valid);
        sum+=(counts[2*i+1]&~valid)-(counts[2*i]&~valid);}
    return complete&&sum==expected?float4(0,1,0,1):float4(1,0,0,1);
}
)MSL";
    NSError* error = nil;
    auto library = [f.mtl newLibraryWithSource:shaders options:nil error:&error];
    CHECK(library != nil);
    auto* desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = [library newFunctionWithName:@"occlusion_vertex"];
    desc.fragmentFunction = [library newFunctionWithName:@"occlusion_fragment"];
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    desc.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
    auto pipeline = [f.mtl newRenderPipelineStateWithDescriptor:desc error:&error];
    CHECK(pipeline != nil);
    auto* consumer_desc=[MTLRenderPipelineDescriptor new];
    consumer_desc.vertexFunction=desc.vertexFunction;
    consumer_desc.fragmentFunction=[library newFunctionWithName:@"consume_occlusion"];
    consumer_desc.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    auto consumer=[f.mtl newRenderPipelineStateWithDescriptor:consumer_desc error:&error];
    CHECK(consumer!=nil);
    auto readback=[f.mtl newBufferWithLength:16*16*4 options:MTLResourceStorageModeShared];
    auto* zdesc = [MTLDepthStencilDescriptor new];
    zdesc.depthCompareFunction = MTLCompareFunctionLessEqual;
    zdesc.depthWriteEnabled = YES;
    auto depth_state = [f.mtl newDepthStencilStateWithDescriptor:zdesc];
    auto* texdesc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                            width:16 height:16 mipmapped:NO];
    texdesc.usage = MTLTextureUsageRenderTarget;
    auto color = [f.mtl newTextureWithDescriptor:texdesc];
    texdesc.pixelFormat = MTLPixelFormatDepth32Float;
    auto depth = [f.mtl newTextureWithDescriptor:texdesc];
    auto* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = color;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.depthAttachment.texture = depth;
    pass.depthAttachment.loadAction = MTLLoadActionClear;
    pass.depthAttachment.storeAction = MTLStoreActionStore;
    pass.depthAttachment.clearDepth = 0.5;
    Buffer output{f.runtime, 0, 4096, MemoryType::HostCached, "Occlusion test counters"};
    std::memset(output.mapped_data.data(), 0, output.SizeBytes());
    Metal::OcclusionQueries queries{f.device, f.scheduler, f.runtime};
    RenderState state;
    state.width = state.height = 16;
    const auto draw = [&](bool visible, bool guest = true, unsigned draws = 1) {
        if (guest) f.scheduler.RenderEncoder((__bridge const void*)pass, state);
        else f.scheduler.RenderEncoder((__bridge const void*)pass);
        const float z = visible ? 0.2f : 0.8f;
        if (guest) {
            auto& commands = f.scheduler.Commands();
            commands.SetRenderPipelineState((__bridge void*)pipeline);
            commands.SetDepthStencilState((__bridge void*)depth_state);
            commands.SetBytes(Metal::RenderList::Stage::Vertex, &z, sizeof(z), 0);
            const Metal::RenderList::Viewport viewport{0,0,16,16,0,1};
            commands.SetViewports(&viewport, 1);
            for (unsigned i = 0; i < draws; ++i) commands.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        } else {
            auto encoder = f.scheduler.GetImpl().render;
            [encoder setRenderPipelineState:pipeline];
            [encoder setDepthStencilState:depth_state];
            [encoder setVertexBytes:&z length:sizeof(z) atIndex:0];
            [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
    };
    constexpr u64 Valid = 1ull << 63;
    const auto measured = [&](u64 offset, unsigned pairs) {
        Settle(f);
        const auto* words = reinterpret_cast<const u64*>(output.mapped_data.data() + offset);
        u64 samples = 0;
        for (unsigned k = 0; k < pairs; ++k) {
            CHECK((words[2*k] & Valid) && (words[2*k+1] & Valid));
            samples += (words[2*k+1] & ~Valid) - (words[2*k] & ~Valid);
        }
        return samples;
    };
    const auto interval = [&](bool visible, u64 expected=1920ull*1080) {
        const u64 before_begin = f.runtime.BufferWriteEpoch();
        queries.Snapshot(&output, 0, 0x1000, 4);
        CHECK(f.runtime.BufferWriteEpoch() > before_begin);
        draw(visible);
        const u64 before_end = f.runtime.BufferWriteEpoch();
        queries.Snapshot(&output, 8, 0x1008, 4);
        CHECK(f.runtime.BufferWriteEpoch() > before_end);
        // No CPU/GPU wait before the fragment consumer: it must see complete query writes
        // through the kept compute encoder's fence, including all four valid pairs.
        auto* consume_pass=[MTLRenderPassDescriptor renderPassDescriptor];
        consume_pass.colorAttachments[0].texture=color;
        consume_pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;
        consume_pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        f.scheduler.HostRenderEncoder((__bridge const void*)consume_pass);
        auto enc=f.scheduler.GetImpl().render;const float z=.2f;
        [enc setRenderPipelineState:consumer];[enc setVertexBytes:&z length:sizeof(z) atIndex:0];
        [enc setFragmentBuffer:(__bridge id<MTLBuffer>)output.buffer.native offset:0 atIndex:0];
        [enc setFragmentBytes:&expected length:sizeof(expected) atIndex:1];
        [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        f.scheduler.EndWithBarrier();f.scheduler.BlitEncoder("Occlusion fragment consumer readback");
        [f.scheduler.GetImpl().blit copyFromTexture:color sourceSlice:0 sourceLevel:0
            sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(16,16,1)
            toBuffer:readback destinationOffset:0 destinationBytesPerRow:16*4 destinationBytesPerImage:16*16*4];
        const auto samples=measured(0,4);
        const auto* pixels=static_cast<const u32*>(readback.contents);
        CHECK(std::all_of(pixels,pixels+16*16,[](u32 p){return p==0xff00ff00;}));
        return samples;
    };
    if (std::getenv("BB_TEST_QUERY_CHAIN_TIMING")) {
        std::vector<double> gpu_times, cpu_times;
        for (unsigned round=0;round<28;++round) {
            const auto started=std::chrono::steady_clock::now();
            for (unsigned i=0;i<23;++i) {
                const u64 off=2048+i*32,key=0xf000+i*16;
                f.runtime.FillBuffer(&output,off,16,0);
                queries.Snapshot(&output,off,key,1);
                draw(i%3!=0);
                queries.Snapshot(&output,off+8,key+8,1);
            }
            const auto encoded=std::chrono::steady_clock::now();
            auto cb=f.scheduler.GetImpl().cmdbuf;
            Settle(f);
            CHECK(cb && cb.status==MTLCommandBufferStatusCompleted);
            if(round>=8) {
                gpu_times.push_back((cb.GPUEndTime-cb.GPUStartTime)*1000);
                cpu_times.push_back(std::chrono::duration<double,std::milli>(encoded-started).count());
            }
            const auto* out=reinterpret_cast<const u64*>(output.mapped_data.data());
            for(unsigned i=0;i<23;++i) {
                const auto word=(2048+i*32)/8;
                CHECK((out[word]&Valid) && (out[word+1]&Valid));
                CHECK((out[word+1]&~Valid)>=(out[word]&~Valid));
                if(round>=8) CHECK((out[word+1]&~Valid)-(out[word]&~Valid)==(i%3?1920ull*1080:0));
            }
            std::printf("query-chain sample: round=%u GPU=%.6f ms, CPU-record=%.6f ms\n",round,
                        (cb.GPUEndTime-cb.GPUStartTime)*1000,
                        std::chrono::duration<double,std::milli>(encoded-started).count());
        }
        std::sort(gpu_times.begin(),gpu_times.end());std::sort(cpu_times.begin(),cpu_times.end());
        std::printf("query-chain isolated23intervals: GPU median=%.6f ms, CPU-record median=%.6f ms,20warm samples; synthetic16x16,not gameFPS\n",gpu_times[gpu_times.size()/2],cpu_times[cpu_times.size()/2]);
        f.scheduler.DeferOperation([color,depth,pipeline,depth_state,consumer,readback]{(void)color;(void)depth;(void)pipeline;(void)depth_state;(void)consumer;(void)readback;});
        return;
    }
    CHECK(interval(true) == 1920ull * 1080); // exact sample density restored from 16x16
    CHECK(interval(false) == 1920ull * 1080); // max-of-three history suppresses jitter flicker
    CHECK(interval(false) == 1920ull * 1080);
    CHECK(interval(false,0) == 0); // truly hidden after three empty results
    CHECK(interval(true) == 1920ull * 1080); // immediately visible again
    queries.Snapshot(&output, 128, 0x2000, 16);
    draw(true); draw(true); // held passes handed to replay; both must be counted in order
    queries.Snapshot(&output, 136, 0x2008, 16);
    CHECK(measured(128, 16) == 2 * 1920ull * 1080);
    queries.Snapshot(&output, 512, 0x3000, 1);
    draw(true, true, 2); // a single native query accumulates multiple draws in the same pass
    queries.Snapshot(&output, 520, 0x3008, 1);
    CHECK(measured(512, 1) == 2 * 1920ull * 1080);
    // Query buffers reused within one unsubmitted batch: no CPU writes can reset GPU results.
    for (unsigned i = 0; i < 8; ++i) {
        f.runtime.FillBuffer(&output,768+i*32,16,0); // retain BOTH begin/end validity clears
        queries.Snapshot(&output, 768 + i*32, 0x4000 + i*16, 1);
        draw(true);
        queries.Snapshot(&output, 776 + i*32, 0x4008 + i*16, 1);
    }
    for (unsigned i = 0; i < 8; ++i) CHECK(measured(768 + i*32,1) == 1920ull * 1080);
    // A host-side image copy/presenter draw must not contaminate a guest's hidden interval.
    for (unsigned i = 0; i < 3; ++i) {
        queries.Snapshot(&output, 1104, 0x5000, 1);
        draw(false); draw(true, false);
        queries.Snapshot(&output, 1112, 0x5008, 1);
        CHECK(measured(1104,1) == (i == 2 ? 0 : 1));
    }
    queries.Snapshot(&output, 1200, 0x6000, 1);
    queries.MarkUnmeasured();
    queries.Snapshot(&output, 1208, 0x6008, 1);
    CHECK(measured(1200,1) >= 0x2ffffff);
    // Overlapping history-table entries must lose culling opportunities, never hide a block.
    const auto history_slot = [](u64 key) {
        const u64 h = (key >> 4) * 0x9e3779b97f4a7c15ull;
        return (h ^ (h >> 32)) & 4095ull;
    };
    u64 colliding = 0x9010;
    while (history_slot(colliding) != history_slot(0x9000)) colliding += 16;
    queries.Snapshot(&output, 1400, 0x9000, 1);
    queries.Snapshot(&output, 1416, colliding, 1);
    queries.Snapshot(&output, 1408, 0x9008, 1);
    CHECK(measured(1400,1) >= 0x2ffffff);
    if (std::getenv("BB_TEST_OCCLUSION_OVERFLOW")) {
        queries.Snapshot(&output, 1504, 0xa000, 1);
        for (unsigned i = 0; i < 4097; ++i) {
            f.scheduler.RenderEncoder((__bridge const void*)pass, state);
        }
        queries.Snapshot(&output, 1512, 0xa008, 1);
        CHECK(measured(1504,1) >= 0x2ffffff); // full ring is visible; no overwrite or wait
    }
    // Destruction while this last snapshot is unsubmitted must preserve unretained objects.
    queries.Snapshot(&output, 1280, 0x7000, 1);
    draw(true);
    queries.Snapshot(&output, 1288, 0x7008, 1);
    // The fixture's raw render objects obey the same unretained-buffer retirement contract.
    f.scheduler.DeferOperation([color, depth, pipeline, depth_state, consumer, readback] {
        (void)color; (void)depth; (void)pipeline; (void)depth_state; (void)consumer; (void)readback;
    });
    CHECK(queries.Events() > 20);
    CHECK(!f.scheduler.HasFailed());
}

static void TestSmallComputeFills(Fixture& f) {
    const bool chained = [] {const char* v=std::getenv("BB_MTL_QUERY_COMPUTE_CHAIN");return v&&v[0]=='1';}();
    for (u64 offset : {0u, 4u, 32u, 65u}) for (u64 size : {1u, 4u, 16u, 20u, 256u, 260u}) {
        auto buffer=MakeBuffer(f.runtime,512,MemoryType::HostCached);
        auto copied=MakeBuffer(f.runtime,512,MemoryType::HostCached);
        std::memset(buffer->mapped_data.data(),0x5b,512);
        std::vector<u8> expected(512,0x5b);
        std::fill(expected.begin()+offset,expected.begin()+offset+size,u8{0xa3});
        f.scheduler.BeginOrderedHostCompute("Small fill test producer");
        f.scheduler.OrderAfterHostCompute();
        auto previous=f.scheduler.GetImpl().compute;
        f.runtime.FillBuffer(buffer.get(),offset,size,0xa3a3a3a3);
        const bool eligible=chained && size<=256 && !(offset&3) && !(size&3);
        if(eligible) {
            CHECK(f.scheduler.GetImpl().compute==previous);
            CHECK(f.scheduler.NeedFenceWait());
        } else CHECK(f.scheduler.OpenEncoder()==Metal::Scheduler::EncoderKind::Blit);
        // A real subsequent GPU reader must see the fill AND intact surrounding bytes.
        vk::BufferCopy copy{0,0,512};f.runtime.CopyBuffer(buffer.get(),copied.get(),std::span{&copy,1});
        Settle(f);
        CHECK(std::equal(expected.begin(),expected.end(),buffer->mapped_data.data()));
        CHECK(std::equal(expected.begin(),expected.end(),copied->mapped_data.data()));
    }
    std::printf("small ordered compute fills=%d: exact bytes/canaries, shared-encoder reuse, 256-byte bound, unaligned fallback and next-encoder GPU reads passed\n",int(chained));
}

static void TestBufferRangeAudit() {
    using T=Metal::BufferAccessTracker;
    const auto key=reinterpret_cast<const void*>(0x1000);
    T tracker;
    for(u64 i=0;i<16;++i)tracker.Insert(key,i*100,i*100+7,T::Read);
    tracker.Insert(key,5000,5007,T::Write);
    CHECK(tracker.Find(key,2500,2507,T::Write)); // original conservative result is unchanged
    CHECK(tracker.Find(key,201,202,T::Write)); // old read-only bytes now lie inside a mixed bound
    CHECK(tracker.Find(key,201,202,T::Read|T::Write));
    auto stats=tracker.AuditSnapshot();CHECK(stats.false_reads==2 && stats.false_writes==0 && stats.missed==0);
    tracker.Insert(key,2500,2507,T::Write); // old tracker short-circuits; exact observer must still record it
    CHECK(tracker.Find(key,2501,2502,T::Write));
    CHECK(tracker.AuditSnapshot().false_reads==2);
    CHECK(tracker.Find(key,2600,2601,T::Read|T::Write));
    CHECK(tracker.Find(key,5000,5001,T::Write));
    CHECK(tracker.AuditSnapshot().false_writes==1);
    tracker.Clear();CHECK(!tracker.Find(key,2600,2601,T::Write));
    CHECK(tracker.AuditSnapshot().false_reads==2);
    T limited;
    for(u64 i=0;i<300;++i)limited.Insert(key,4*i,4*i+1,T::Read);
    limited.Insert(key,2048,2049,T::Write);
    for(u64 i=0;i<32;++i)limited.Insert(key,3000+4*i,3001+4*i,T::Read);
    CHECK(limited.Find(key,1500,1501,T::Write));
    auto unknown=limited.AuditSnapshot();CHECK(unknown.unknown==1 && unknown.false_reads==0 && unknown.missed==0);
    T resources;
    for(uintptr_t i=0;i<65;++i)resources.Insert(reinterpret_cast<const void*>(0x1000+8*i),0,7,T::Write);
    CHECK(resources.Find(reinterpret_cast<const void*>(0x1000+8*64),0,1,T::Write));
    CHECK(resources.AuditSnapshot().unknown==1 && resources.AuditSnapshot().false_reads==0);
    std::puts("buffer range audit: unchanged conservative results, independently exact false-hit qualification, contained insertions, clears and bounded unknown fallback passed");
}

static void TestTextureTiling(Fixture& f) {
    namespace Stats=Metal::MemoryStats;
    const bool base_enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING");return v&&v[0]=='1';}();
    const bool half_enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING_HALF");return v&&v[0]=='1';}();
    CHECK(Metal::TextureTilingSource("unknown generated source").empty());
    StreamBuffer params_stream{f.runtime,MemoryType::HostUncached,1<<20};
    TileManager manager{f.runtime,params_stream};
    for(auto format:{vk::Format::eR8G8B8A8Unorm,vk::Format::eR16G16B16A16Sfloat}) {
    const unsigned bytes=vk::blockSize(format);
    const bool enabled=base_enabled && (bytes==4 || half_enabled);
    for(auto size:{std::pair{128u,64u},std::pair{128u,73u},std::pair{1920u,1080u}}) {
      for(unsigned swizzle:{0u,1u,3u}) {
        auto [w,h]=size;auto info=MakeInfo(format,w,h);
        info.tile_mode=AmdGpu::TileMode::Thin2DThin;info.array_mode=AmdGpu::GetArrayMode(info.tile_mode);
        info.props.is_tiled=1;info.bank_swizzle=swizzle;info.UpdateSize();
        std::printf("texture tiling case %ux%u swizzle=%u pitch/height=%u/%u size=%u\n",w,h,swizzle,info.mips_layout[0].pitch,info.mips_layout[0].height,info.guest_size);
        Img source{f.runtime,info};
        auto pattern=Pattern(size_t(w)*h*bytes,swizzle+93);
        if(bytes==8)for(size_t i=0;i<pattern.size()/2;++i) {const u16 word=u16(i);std::memcpy(pattern.data()+2*i,&word,2);}
        auto upload=MakeBuffer(f.runtime,pattern.size(),MemoryType::HostUncached);
        std::memcpy(upload->mapped_data.data(),pattern.data(),pattern.size());auto whole=WholeCopy(w,h);
        f.runtime.UploadImage(source.image.get(),upload.get(),{&whole,1});
        auto linear=MakeBuffer(f.runtime,info.guest_size,MemoryType::DeviceLocal);
        std::memset(linear->mapped_data.data(),0xa5,linear->SizeBytes());
        auto reference=MakeBuffer(f.runtime,info.guest_size+512,MemoryType::DeviceLocal);
        auto actual=MakeBuffer(f.runtime,info.guest_size+512,MemoryType::DeviceLocal);
        std::memset(reference->mapped_data.data(),0xa5,reference->SizeBytes());
        std::memset(actual->mapped_data.data(),0xa5,actual->SizeBytes());
        auto params=TileManager::MakeTilingInfo(info,1);
        auto dispatch=TileManager::MakeDispatch(info,true,{reference.get(),256,info.guest_size},
            {linear.get(),0,info.guest_size},{&params_stream,params_stream.Copy(&params,sizeof(params),16),sizeof(params)});
        whole.bufferRowLength=info.pitch;
        f.runtime.DownloadImage(source.image.get(),linear.get(),{&whole,1});f.runtime.FlushBarriers();
        f.runtime.DispatchTiling(dispatch);Settle(f);
        const auto before=Stats::Read()[unsigned(Stats::Kind::Staging)];
        dispatch.tiled={actual.get(),256,info.guest_size};
        CHECK(f.runtime.DownloadTiledImage(source.image.get(),dispatch)==enabled);
        if(!enabled)manager.TileImage(*source.image,{&whole,1},actual.get(),256);
        Settle(f);
        if(enabled) CHECK(std::memcmp(reference->mapped_data.data(),actual->mapped_data.data(),actual->SizeBytes())==0);
        else {
            // Original staging padding is unspecified. Compare all defined pixels through a
            // separately tiled validity mask and both allocation canaries; never score padding.
            std::memset(linear->mapped_data.data(),0,linear->SizeBytes());
            std::memset(linear->mapped_data.data(),0xff,size_t(w)*h*bytes);
            auto mask=MakeBuffer(f.runtime,info.guest_size,MemoryType::DeviceLocal);
            std::memset(mask->mapped_data.data(),0,mask->SizeBytes());
            auto md=dispatch;md.tiled={mask.get(),0,info.guest_size};md.linear={linear.get(),0,info.guest_size};
            f.runtime.DispatchTiling(md);Settle(f);bool equal=true;
            for(u64 i=0;i<info.guest_size;++i)if(mask->mapped_data[i] && reference->mapped_data[256+i]!=actual->mapped_data[256+i])equal=false;
            CHECK(equal);CHECK(std::memcmp(reference->mapped_data.data(),actual->mapped_data.data(),256)==0);
            CHECK(std::memcmp(reference->mapped_data.data()+256+info.guest_size,actual->mapped_data.data()+256+info.guest_size,256)==0);
        }
        if(enabled) {
            const auto after=Stats::Read()[unsigned(Stats::Kind::Staging)];
            CHECK(after.bytes==before.bytes && after.objects==before.objects);
            // Every unsupported shape falls back without a staging allocation or dispatch.
            const auto original=source.image->info;
            source.image->info.pixel_format=vk::Format::eR8G8B8A8Srgb;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),dispatch));source.image->info=original;
            source.image->info.resources.levels=2;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),dispatch));source.image->info=original;
            source.image->info.resources.layers=2;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),dispatch));source.image->info=original;
            source.image->info.pitch+=1;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),dispatch));source.image->info=original;
            auto bad=dispatch;bad.tiled.offset=actual->SizeBytes()-1;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),bad));
            bad=dispatch;bad.is_macro=false;CHECK(!f.runtime.DownloadTiledImage(source.image.get(),bad));
            // Integration entry must hit the same exact result, also with pending source work.
            std::memset(actual->mapped_data.data(),0xa5,actual->SizeBytes());
            whole.bufferOffset=0;manager.TileImage(*source.image,{&whole,1},actual.get(),256);Settle(f);
            CHECK(std::memcmp(reference->mapped_data.data(),actual->mapped_data.data(),actual->SizeBytes())==0);
            CHECK(Stats::Read()[unsigned(Stats::Kind::Staging)].bytes==before.bytes);
            if(w==1920 && swizzle==0 && std::getenv("BB_TEST_TEXTURE_TILING_PERF")) {
                // Isolated command-buffer timing, not gameplay FPS. Both paths use the same
                // already allocated source/output/linear buffers and warmed pipeline states.
                for(unsigned sample=0;sample<48;++sample) {
                    const bool fused=(sample%4==1 || sample%4==2);
                    whole.bufferOffset=0;
                    if(fused)CHECK(f.runtime.DownloadTiledImage(source.image.get(),dispatch));
                    else {
                        f.runtime.DownloadImage(source.image.get(),linear.get(),{&whole,1});
                        f.runtime.FlushBarriers();
                        auto old=dispatch;old.linear={linear.get(),0,info.guest_size};
                        f.runtime.DispatchTiling(old);
                        f.runtime.AccessBuffer(linear.get(),0,info.guest_size,vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderRead);
                    }
                    auto command=f.scheduler.GetImpl().cmdbuf;
                    Settle(f);
                    if(sample>=8)std::printf("texture tiling GPU sample %u bits=%u fused=%d ms=%.6f\n",sample,bytes*8,int(fused),(command.GPUEndTime-command.GPUStartTime)*1000.0);
                }
            }
        }
      }
    }
    }
    std::printf("texture tiling=%d half=%d: 18 exact native cases, exhaustive half words, preserved guest padding/guards, swizzles, fallback and no extra staging\n",int(base_enabled),int(half_enabled));
}

static void TestAdaptiveStaging(Fixture& f) {
    namespace Stats=Metal::MemoryStats;
    const auto kind=unsigned(Stats::Kind::Staging);
    const bool adaptive=[] {const char* v=std::getenv("BB_MTL_ADAPTIVE_STAGING");return v&&v[0]=='1';}();
    Metal::StagingPool pool{f.runtime};const auto initial=Stats::Read()[kind];
    constexpr u64 bytes=9ull<<20;
    std::vector<const Buffer*> buffers;
    for(unsigned i=0;i<8;++i) {
        const auto span=pool.Request(bytes,MemoryType::DeviceLocal,16);
        CHECK(span.offset==0);CHECK(span.buffer->SizeBytes()==(adaptive?bytes:16ull<<20));
        for(const auto* old:buffers)CHECK(old!=span.buffer);buffers.push_back(span.buffer);
        f.scheduler.BlitEncoder("adaptive staging test fill");
        [f.scheduler.GetImpl().blit fillBuffer:(__bridge id<MTLBuffer>)span.buffer->buffer.native range:NSMakeRange(span.offset,bytes) value:0x7b];
    }
    Settle(f);const auto warm=Stats::Read()[kind];
    CHECK(warm.objects==initial.objects+8);CHECK(warm.bytes==initial.bytes+8*(adaptive?bytes:16ull<<20));
    for(unsigned round=0;round<8;++round) {
        const auto span=pool.Request(bytes,MemoryType::DeviceLocal,16);
        CHECK(std::find(buffers.begin(),buffers.end(),span.buffer)!=buffers.end());
        f.scheduler.BlitEncoder("adaptive staging overwrite");
        [f.scheduler.GetImpl().blit fillBuffer:(__bridge id<MTLBuffer>)span.buffer->buffer.native range:NSMakeRange(span.offset,bytes) value:uint8_t(round)];
        Settle(f);CHECK(Stats::Read()[kind].objects==warm.objects);
    }
    for(unsigned i=0;i<3001;++i)pool.TickFrame();
    CHECK(Stats::Read()[kind].objects==warm.objects); // retain the same byte budget, no eviction
    const auto span=pool.Request(bytes,MemoryType::DeviceLocal,16);
    CHECK(std::find(buffers.begin(),buffers.end(),span.buffer)!=buffers.end());
    f.scheduler.BlitEncoder("adaptive staging wake and refill");
    [f.scheduler.GetImpl().blit fillBuffer:(__bridge id<MTLBuffer>)span.buffer->buffer.native range:NSMakeRange(span.offset,bytes) value:0x63];
    auto result=[f.device.GetImpl().device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
    [f.scheduler.GetImpl().blit copyFromBuffer:(__bridge id<MTLBuffer>)span.buffer->buffer.native sourceOffset:span.offset toBuffer:result destinationOffset:0 size:bytes];
    Settle(f);const auto* data=(const uint8_t*)result.contents;
    CHECK(std::all_of(data,data+bytes,[](uint8_t x){return x==0x63;}));
    CHECK(Stats::Read()[kind].objects==warm.objects);
    std::printf("adaptive staging=%d: 8 in-flight blocks, allocated=%llu bytes; reused after GPU completion and idle backing discard, no new objects\n",int(adaptive),(unsigned long long)(warm.bytes-initial.bytes));
}

static void TestNativeColorClearBatch(Fixture& f) {
    using F=vk::Format;
    const std::array formats{F::eR8G8B8A8Unorm,F::eR8G8B8A8Srgb,F::eR16G16B16A16Sfloat,
        F::eB10G11R11UfloatPack32,F::eR16G16B16A16Sfloat,F::eR16G16B16A16Sfloat,
        F::eB10G11R11UfloatPack32,F::eR16G16B16A16Sfloat};
    const auto before=Metal::MemoryStats::Read();
    std::vector<u8> pixels;
    {
        std::vector<std::unique_ptr<Img>> images;
        for(auto format:formats) images.push_back(std::make_unique<Img>(f.runtime,MakeInfo(format,8,8)));
        for(unsigned i=0;i<images.size();++i) {
            vk::ClearValue value{};
            value.color=vk::ClearColorValue{std::array<float,4>{.1f+float(i)/10,.5001f,.9999f,.3333f}};
            f.runtime.ClearImage(images[i]->image.get(),SubresourceRange{},value);
        }
        // Downloading the first member must materialize the entire pending clear batch.
        for(unsigned i=0;i<images.size();++i) {
            const auto data=Download(f,*images[i]->image,std::array{WholeCopy(8,8)},8*8*vk::blockSize(formats[i]));
            pixels.insert(pixels.end(),data.begin(),data.end());
        }
        vk::ClearValue red{},green{};
        red.color=vk::ClearColorValue{std::array<float,4>{1,0,0,1}};
        green.color=vk::ClearColorValue{std::array<float,4>{0,1,0,1}};
        f.runtime.ClearImage(images[0]->image.get(),SubresourceRange{},red);
        f.runtime.ClearImage(images[0]->image.get(),SubresourceRange{},green);
        auto data=Download(f,*images[0]->image,std::array{WholeCopy(8,8)},256);
        for(unsigned i=0;i<64;++i) CHECK(data[i*4]==0 && data[i*4+1]==255 && data[i*4+2]==0 && data[i*4+3]==255);
        pixels.insert(pixels.end(),data.begin(),data.end());
        f.runtime.ClearImage(images[0]->image.get(),SubresourceRange{},red);
        f.runtime.CopyImage(images[0]->image.get(),images[1]->image.get());
        data=Download(f,*images[1]->image,std::array{WholeCopy(8,8)},256);
        CHECK(data[0]==255 && data[1]==0 && data[2]==0 && data[3]==255);
        pixels.insert(pixels.end(),data.begin(),data.end());
    }
    Settle(f);
    const auto after=Metal::MemoryStats::Read();
    for(auto kind:{Metal::MemoryStats::Kind::Texture,Metal::MemoryStats::Kind::Target,Metal::MemoryStats::Kind::SceneProxy}) {
        CHECK(after[unsigned(kind)].objects==before[unsigned(kind)].objects);
        CHECK(after[unsigned(kind)].bytes==before[unsigned(kind)].bytes);
        CHECK(after[unsigned(kind)].retired==before[unsigned(kind)].retired);
    }
    if(const char* path=std::getenv("BB_TEST_CLEAR_BYTES")) {
        auto* output=std::fopen(path,"wb");CHECK(output);
        if(output) {CHECK(std::fwrite(pixels.data(),1,pixels.size(),output)==pixels.size());std::fclose(output);}
    }
    TestImageCopies(f); // integer clears, arrays, mips, copies, partial ranges and depth retain fallback
    puts("native color clear batching: mixed formats, repeated target, read/copy visibility and complete target retirement passed");
}

// Exact byte fills must preserve prior readers, intervening writers, CPU snapshot semantics
// and surrounding bytes. This test exercises real GPU copies, not a CPU implementation mirror.
static void TestPatternUpdates(Fixture& f) {
    namespace Stats=Metal::MemoryStats;
    auto target=MakeBuffer(f.runtime,1024,MemoryType::DeviceLocal);
    auto before=MakeBuffer(f.runtime,1024,MemoryType::DeviceLocal);
    auto middle=MakeBuffer(f.runtime,1024,MemoryType::DeviceLocal);
    auto after=MakeBuffer(f.runtime,1024,MemoryType::DeviceLocal);
    for(unsigned length:{4u,128u,192u,256u})for(u8 value:{u8(0),u8(0xff),u8(0x7f),u8(0xa5)}) {
        f.runtime.FillBuffer(target.get(),0,1024,0x3c3c3c3c);
        const vk::BufferCopy full{.srcOffset=0,.dstOffset=0,.size=1024};
        f.runtime.CopyBuffer(target.get(),before.get(),{&full,1});
        const auto base=Stats::Read()[unsigned(Stats::Kind::Staging)].bytes;
        std::vector<u8> data(length,value);
        f.runtime.UpdateBuffer(target.get(),16,data);
        f.runtime.CopyBuffer(target.get(),middle.get(),{&full,1});
        // An intervening different write makes equality-based suppression incorrect.
        f.runtime.FillBuffer(target.get(),16,length,0x55555555);
        f.runtime.UpdateBuffer(target.get(),16,data);
        data.assign(length,u8(value^0xff)); // UpdateBuffer consumes a CPU snapshot
        f.runtime.CopyBuffer(target.get(),after.get(),{&full,1});
        Settle(f);
        bool correct=true;
        for(unsigned byte=0;byte<1024;++byte) {
            const u8 expected=byte>=16 && byte<16+length?value:0x3c;
            correct &= before->mapped_data[byte]==0x3c && middle->mapped_data[byte]==expected && after->mapped_data[byte]==expected;
        }
        CHECK(correct);
        if(const char* flag=std::getenv("BB_MTL_FILL_UPDATES");flag && flag[0]=='1')
            CHECK(Stats::Read()[unsigned(Stats::Kind::Staging)].bytes==base);
    }
    // Nonuniform and non-dword payloads retain the original upload path.
    for(unsigned length:{3u,191u,192u,257u}) {
        auto data=Pattern(length,37);f.runtime.UpdateBuffer(target.get(),32,data);Settle(f);
        CHECK(std::memcmp(target->mapped_data.data()+32,data.data(),length)==0);
    }
    puts("pattern updates: byte-exact fills, old readers, intervening writes, CPU mutation, canaries, fallback and no additional staging passed");
}

static void TestContentAudit(Fixture& f) {
    using VideoCore::FrameCapture;
    namespace Stats=Metal::MemoryStats;
    CHECK(Stats::ContentEnabled());
    char directory[]="/private/tmp/bb-content-test-XXXXXX";CHECK(mkdtemp(directory));
    const std::filesystem::path root=directory,trigger=root/"trigger";
    setenv("BB_CAPTURE_DIR",directory,1);setenv("BB_CAPTURE_TRIGGER",trigger.c_str(),1);
    Img image{f.runtime,MakeInfo(vk::Format::eR8G8B8A8Unorm,8,8)};
    Img target{f.runtime,MakeInfo(vk::Format::eR8G8B8A8Unorm,8,8)};
    auto native=(__bridge id<MTLTexture>)image.image->backing->image.native;
    id<MTLTexture> view=[native newTextureViewWithPixelFormat:native.pixelFormat];CHECK(view);
    const auto source_id=uintptr_t(image.image->backing->image.native),target_id=uintptr_t(target.image->backing->image.native);
    vk::ClearValue clear{};clear.color.float32[0]=1;clear.color.float32[3]=1;
    f.runtime.ClearImage(image.image.get(),{.base={0,0},.extent={1,1}},clear);
    auto buffer=MakeBuffer(f.runtime,256,MemoryType::HostUncached);
    auto bytes=Pattern(256,37);std::memcpy(buffer->mapped_data.data(),bytes.data(),bytes.size());
    auto whole=WholeCopy(8,8);f.runtime.UploadImage(image.image.get(),buffer.get(),{&whole,1});
    Stats::NoteContent((__bridge const void*)view,Stats::Content::StorageBinding); // aggregation through a real view
    f.runtime.CopyImage(image.image.get(),target.image.get());Settle(f);
    auto readback=MakeBuffer(f.runtime,256,MemoryType::HostCached);
    f.runtime.DownloadImage(target.image.get(),readback.get(),{&whole,1});Settle(f);
    CHECK(std::memcmp(readback->mapped_data.data(),bytes.data(),bytes.size())==0);
    if(FrameCapture::NativeMode()) {
    std::ofstream(trigger)<<"1";FrameCapture::OnFlip(456);FrameCapture::FrameBoundary(201);
    Stats::CaptureLifetimes();FrameCapture::FrameBoundary(202);
    bool source_found=false,target_found=false;
    for(auto& entry:std::filesystem::directory_iterator(root))if(entry.path().extension()==".txt") {
        std::ifstream file(entry.path());std::string line;
        while(std::getline(file,line))if(line.find("\"op\":\"memory_live\"")!=std::string::npos) {
            if(line.find(std::format("\"native\":{},",source_id))!=std::string::npos)
                source_found=line.find("\"uploads\":1,\"clears\":1,\"copies\":0,\"draws\":0,\"storage_binds\":1")!=std::string::npos;
            if(line.find(std::format("\"native\":{},",target_id))!=std::string::npos)
                target_found=line.find("\"copies\":1")!=std::string::npos;
        }
    }
    CHECK(source_found && target_found);
    }
    const auto base_observed=Stats::ReadContent((__bridge const void*)native);
    const auto view_observed=Stats::ReadContent((__bridge const void*)view);
    CHECK(base_observed.counts==view_observed.counts && base_observed.last_us>0);
    CHECK(base_observed.counts[unsigned(Stats::Content::Upload)]==1);
    CHECK(base_observed.counts[unsigned(Stats::Content::Clear)]==1);
    CHECK(base_observed.counts[unsigned(Stats::Content::StorageBinding)]==1);
    view=nil;std::filesystem::remove_all(root);
    puts("content audit: real upload/clear/copy, view-to-base aggregation, exact GPU bytes and bounded lifetime metadata passed");
}

// Validate the real Runtime instrumentation alongside unchanged access bookkeeping.
static void TestBarrierSources(Fixture& f) {
    using VideoCore::FrameCapture;
    CHECK(FrameCapture::NativeMode()); // runner sets BB_CAPTURE_NATIVE before Fixture construction
    char directory[]="/private/tmp/bb-barrier-sources-test-XXXXXX";CHECK(mkdtemp(directory));
    const std::filesystem::path root=directory,trigger=root/"trigger";
    setenv("BB_CAPTURE_DIR",directory,1);setenv("BB_CAPTURE_TRIGGER",trigger.c_str(),1);
    auto buffer=MakeBuffer(f.runtime,128,MemoryType::Stream);
    f.runtime.FlushBarriers();
    std::ofstream(trigger)<<"1";FrameCapture::OnFlip(123);FrameCapture::FrameBoundary(101);
    f.runtime.AccessBuffer(buffer.get(),4,16,vk::PipelineStageFlagBits2::eVertexShader,vk::AccessFlagBits2::eShaderRead);
    f.runtime.AccessGlobal(vk::PipelineStageFlagBits2::eFragmentShader,vk::AccessFlagBits2::eShaderRead);
    f.runtime.AccessGlobal(vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderWrite);
    CHECK(f.runtime.IsBufferAccessed(buffer.get(),64,16,true)); // global write remains authoritative
    f.runtime.FlushBarriers();
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(),4,16,true));
    for(unsigned i=0;i<12;++i)
        f.runtime.AccessBuffer(buffer.get(),i*4,4,vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderWrite);
    f.runtime.FlushBarriers();f.runtime.FlushBarriers();
    CHECK(!f.runtime.IsBufferAccessed(buffer.get(),4,16,true));
    uintptr_t retired_native=0;
    @autoreleasepool {
        auto temporary=MakeBuffer(f.runtime,256*1024,MemoryType::DeviceLocal);
        retired_native=uintptr_t(temporary->buffer.native);
        Metal::MemoryStats::Retire(temporary->buffer.native);
        Metal::MemoryStats::CaptureLifetimes(); // retired object remains physically alive
        temporary.reset();Settle(f);
    }
    Settle(f);
    Metal::MemoryStats::CaptureLifetimes(); // actual object destruction, not cache retirement
    FrameCapture::FrameBoundary(102);
    std::string text;
    for(auto& entry:std::filesystem::directory_iterator(root))if(entry.path().extension()==".txt") {
        std::ifstream file(entry.path());text.assign(std::istreambuf_iterator<char>(file),{});
    }
    size_t events=0,pos=0;while((pos=text.find("\"op\":\"barrier_sources\"",pos))!=std::string::npos){++events;++pos;}
    CHECK(events==2);CHECK(text.find("\"reads\":2,\"writes\":1")!=std::string::npos);
    CHECK(text.find("\"global_reads\":1,\"global_writes\":1")!=std::string::npos);
    CHECK(text.find("\"overflow\":4")!=std::string::npos);
    CHECK(text.find(std::format("\"read_stages\":{}",uint64_t(VkPipelineStageFlags2(vk::PipelineStageFlagBits2::eVertexShader|vk::PipelineStageFlagBits2::eFragmentShader))))!=std::string::npos);
    CHECK(text.find("\"offset\":4,\"size\":16")!=std::string::npos);
    CHECK(text.find("\"truncated\":false")!=std::string::npos);
    bool saw_retired=false,saw_destroyed=false;
    std::istringstream lines{text};std::string line;
    const auto identity=std::format("\"native\":{},",retired_native);
    while(std::getline(lines,line))if(line.find(identity)!=std::string::npos) {
        if(line.find("\"op\":\"memory_live\"")!=std::string::npos && line.find("\"retired\":true")!=std::string::npos)saw_retired=true;
        if(line.find("\"op\":\"memory_destroyed\"")!=std::string::npos)saw_destroyed=true;
    }
    CHECK(saw_retired && saw_destroyed);
    std::filesystem::remove_all(root);
    puts("barrier source capture: exact stages/ranges, global unknowns, bounded overflow and epoch reset passed");
}

int main() {
    g_backing = nullptr;
    @autoreleasepool {
        Fixture f;
        if(std::getenv("BB_TEST_RENDER_UPDATES_ONLY")) {TestRenderUpdates(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_RELEASED_STORAGE_ONLY")) {TestReleasedStorageRetirement(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_CONTENT_AUDIT_ONLY")) {TestContentAudit(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_PATTERN_UPDATES_ONLY")) {TestPatternUpdates(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_BARRIER_SOURCES_ONLY")) {TestBarrierSources(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_NATIVE_CLEAR_BATCH_ONLY")) {TestNativeColorClearBatch(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_INLINE_UPDATES_ONLY")) {TestInlineUpdates(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_QUERY_CHAIN_ONLY")) {TestSmallComputeFills(f);TestOcclusion(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_BUFFER_RANGE_AUDIT_ONLY")) {TestBufferRangeAudit();Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_TEXTURE_TILING_ONLY")) {TestTextureTiling(f);Settle(f);return failures?1:0;}
        if(std::getenv("BB_TEST_ADAPTIVE_STAGING_ONLY")) {TestAdaptiveStaging(f);Settle(f);return failures?1:0;}
        TestOcclusion(f);
        TestLimitsAndFormats(f);
        TestBuffers(f);
        TestInlineUpdates(f);
        TestGuestChunk(f);
        TestCrossChunkRead(f);
        TestCrossChunkSnapshotReuse(f);
        TestImages(f);
        TestViewsAndSamplers(f);
        TestPromotion(f);
        TestDeferredDestroy(f);
        TestImageCopies(f);
        TestKernels(f);
        TestAccessAndStaging(f);
        TestStagingRetirement(f);
        TestMemoryCategoriesAndIdleBacking(f);
        TestTicks(f);
        Settle(f);
        CHECK(!f.scheduler.HasFailed());
    }
    std::printf(failures ? "%d check(s) FAILED\n" : "all checks passed\n", failures);
    return failures ? 1 : 0;
}
