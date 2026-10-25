#include "video_core/renderer_metal/mtl_local_library.h"
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>

#include "bblayer_gpu_memory.h"
#include "video_core/guest_chunks.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_guest_memory.h"

extern "C" void runtime_memory_set_guest_chunk_allocator(int (*alloc)(uint64_t phys,
                                                                      uint64_t size));
extern "C" void runtime_memory_set_guest_chunk_whole(int whole);
extern "C" void* runtime_memory_backing_pointer(uint64_t phys);

namespace Metal::GuestMemory {
namespace {
using u64 = std::uint64_t;
constexpr std::size_t MaxChunks = 256;
/// The runtime's code for a chunk the GPU uses in place while it stays in the memfd.
constexpr int ChunkImported = -3;
/// No-copy buffers need page-aligned memory (accepted at 4 KiB under Rosetta, P0 probe).
constexpr u64 Alignment = 4096;

Device* device = nullptr;
std::mutex mutex;
std::array<Chunk*, MaxChunks> chunks{}; // sorted by phys
std::size_t chunk_count = 0;
std::atomic<u64> chunk_bytes{0};

bool LayerMemory() {
    // BB_LAYER_MEMORY=1/0 chooses; the layer's memory module is the default here, as it is on
    // Vulkan for every GPU once the PC model runs (no sparse buffers in Metal either). Read once:
    // the buffer cache asks per binding.
    static const bool layer = [] {
        const char* env = std::getenv("BB_LAYER_MEMORY");
        return !(env && *env) || env[0] == '1';
    }();
    return layer;
}

/// The first chunk: a kernel reads its first and last word through the gpuAddress (as shaders
/// will) and the buffer binding; both must match what the CPU sees (no-copy buffers over the
/// runtime's shared memfd view were not part of the P0 probe).
void SelfTest(Device::Impl& impl, id<MTLBuffer> buffer, std::uint8_t* host, u64 size) {
    static NSString* const source = @"#include <metal_stdlib>\n"
        "using namespace metal;\n"
        "kernel void probe(constant ulong& address [[buffer(0)]],\n"
        "                  device const uint* bound [[buffer(1)]],\n"
        "                  device uint* out [[buffer(2)]], constant ulong& last [[buffer(3)]]) {\n"
        "    device const uint* p = reinterpret_cast<device const uint*>(address);\n"
        "    out[0] = p[0]; out[1] = p[last]; out[2] = bound[0]; out[3] = bound[last];\n"
        "}\n";
    NSError* error = nil;
    id<MTLLibrary> library = Metal::LocalLibrary::Build(impl.device, source, nil, &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"probe"}); });
    id<MTLComputePipelineState> pipeline =
        library ? [impl.device newComputePipelineStateWithFunction:[library newFunctionWithName:@"probe"]
                                                             error:&error]
                : nil;
    if (!pipeline) {
        std::fprintf(stderr, "Guest memory: Metal self-test not compiled: %s\n",
                     error ? error.localizedDescription.UTF8String : "unknown");
        return;
    }
    id<MTLBuffer> out = [impl.device newBufferWithLength:16 options:MTLResourceStorageModeShared];
    const u64 address = buffer.gpuAddress;
    const u64 last = size / 4 - 1;
    // The chunk is not mapped for the game yet (the runtime holds its lock): a pattern the GPU
    // must see, put back afterwards.
    auto* words = reinterpret_cast<std::uint32_t*>(host);
    const std::uint32_t saved_first = words[0], saved_last = words[last];
    words[0] = 0x12345678u;
    words[last] = 0x9abcdef0u;
    id<MTLCommandBuffer> cmdbuf = [impl.queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [cmdbuf computeCommandEncoder];
    [encoder setComputePipelineState:pipeline];
    [encoder setBytes:&address length:sizeof(address) atIndex:0];
    [encoder setBuffer:buffer offset:0 atIndex:1];
    [encoder setBuffer:out offset:0 atIndex:2];
    [encoder setBytes:&last length:sizeof(last) atIndex:3];
    [encoder dispatchThreads:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
    [encoder endEncoding];
    [cmdbuf commit];
    [cmdbuf waitUntilCompleted];
    const auto* got = static_cast<const std::uint32_t*>(out.contents);
    const bool ok = cmdbuf.status == MTLCommandBufferStatusCompleted && got[0] == 0x12345678u &&
                    got[1] == 0x9abcdef0u && got[2] == 0x12345678u && got[3] == 0x9abcdef0u;
    std::printf("Guest memory: Metal self-test %s (through the address %#x/%#x, the binding "
                "%#x/%#x)\n",
                ok ? "passed" : "FAILED", got[0], got[1], got[2], got[3]);
    words[0] = saved_first;
    words[last] = saved_last;
}

/// The runtime's chunk allocator: the chunk's backing view as a no-copy MTLBuffer, or -1 (the
/// chunk stays the runtime's alone).
int AllocChunk(u64 phys, u64 size) {
    {
        std::scoped_lock lk{mutex};
        if (chunk_count == MaxChunks) {
            return -1;
        }
    }
    auto* host = static_cast<std::uint8_t*>(runtime_memory_backing_pointer(phys));
    auto& impl = device->GetImpl();
    if (!host || reinterpret_cast<std::uintptr_t>(host) % Alignment != 0 ||
        size % Alignment != 0 || size > impl.device.maxBufferLength) {
        std::fprintf(stderr, "Guest memory: chunk %#llx+%llu MiB cannot be a Metal buffer; it "
                             "stays in the memfd\n",
                     (unsigned long long)phys, (unsigned long long)(size >> 20));
        return -1;
    }
    Chunk* chunk = nullptr;
    @autoreleasepool {
        static const bool untracked = [] {
            const char* env = std::getenv("BB_MTL_GUEST_UNTRACKED");
            return env && env[0] == '1';
        }();
        id<MTLBuffer> buffer = [impl.device
            newBufferWithBytesNoCopy:host
                              length:size
                             options:MTLResourceStorageModeShared |
                                     (untracked ? MTLResourceHazardTrackingModeUntracked
                                                : MTLResourceHazardTrackingModeTracked)
                         deallocator:nil];
        if (!buffer) {
            std::fprintf(stderr, "Guest memory: no Metal buffer for chunk %#llx; it stays in "
                                 "the memfd\n",
                         (unsigned long long)phys);
            return -1;
        }
        buffer.label = [NSString stringWithFormat:@"guest %#llx", (unsigned long long)phys];
        // Not in the residency set: every render and compute encoder declares the chunks
        // (Scheduler, UseGuestChunks), blits name their buffers, as MoltenVK does with imported
        // memory. In the set, the driver unwired and rewired whole chunks (up to 2287 MiB)
        // whenever the GPU idled under a frame cap, ~36000 wiring events a second, and the next
        // submit stalled 30-100 ms in the kernel: a hitch in every 5-s window (P8.5).
        // BB_MTL_GUEST_IN_SET=1 puts them back (A/B).
        static const bool in_set = [] {
            const char* env = std::getenv("BB_MTL_GUEST_IN_SET");
            return env && env[0] == '1';
        }();
        if (in_set) {
            impl.AddResident(buffer);
        }
        impl.AddGuestBuffer(buffer);
        static std::once_flag tested;
        std::call_once(tested, [&] { SelfTest(impl, buffer, host, size); });
        chunk = new Chunk{phys, size, (__bridge_retained void*)buffer, buffer.gpuAddress, host};
    }
    {
        std::scoped_lock lk{mutex};
        std::size_t i = chunk_count++;
        for (; i > 0 && chunks[i - 1]->phys > phys; --i) {
            chunks[i] = chunks[i - 1];
        }
        static std::uint32_t next_index = 0;
        chunk->index = next_index++;
        chunks[i] = chunk;
    }
    if (LayerMemory()) {
        // A source of the layer's memory module (the buffer cache binds mappings in it in place).
        BbLayer::GpuMemory::Get().AddGuestSource(phys, size, reinterpret_cast<u64>(chunk->buffer),
                                                 chunk->gpu_address, chunk);
    }
    const u64 total = chunk_bytes.fetch_add(size) + size;
    std::printf("Guest memory: direct memory %#llx+%llu MiB used in place by Metal, %llu MiB so "
                "far\n",
                (unsigned long long)phys, (unsigned long long)(size >> 20),
                (unsigned long long)(total >> 20));
    return ChunkImported;
}

} // namespace

void Install(Device& device_) {
    if (!Wanted()) {
        std::printf("Guest memory: direct memory stays in the memfd\n");
        return;
    }
    device = &device_;
    // With the layer's memory module a mapping lies whole in one chunk buffer: a chunk per
    // direct allocation.
    runtime_memory_set_guest_chunk_whole(LayerMemory() ? 1 : 0);
    runtime_memory_set_guest_chunk_allocator(&AllocChunk);
    std::printf("Guest memory: direct memory chunks become Metal buffers\n");
}

bool Wanted() {
    // As on Vulkan: BB_GUEST_GPU_MEMORY=1, or the new memory model (BB_GUEST_IN_PLACE=1).
    const char* env = std::getenv("BB_GUEST_GPU_MEMORY");
    const char* in_place = std::getenv("BB_GUEST_IN_PLACE");
    return (env && env[0] == '1') || (in_place && in_place[0] == '1');
}

void UseAsChunkProvider() {
    static constexpr auto find = [](u64 phys, VideoCore::GuestChunks::Chunk& out) {
        const Chunk* chunk = Find(phys);
        if (!chunk) {
            return false;
        }
        out = {chunk->phys, chunk->size, chunk->index, 0};
        return true;
    };
    VideoCore::GuestChunks::SetProvider({
        .find = find,
        .layer_memory = &LayerMemory,
        .host_imported = [] { return false; },
    });
}

std::uint64_t ImportedBytes() { return chunk_bytes.load(std::memory_order_relaxed); }

bool Installed() {
    return device != nullptr;
}

const Chunk* Find(std::uint64_t phys) {
    std::scoped_lock lk{mutex};
    // The last chunk starting at or below phys.
    std::size_t lo = 0, hi = chunk_count;
    while (lo < hi) {
        const std::size_t mid = (lo + hi) / 2;
        if (chunks[mid]->phys <= phys) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo == 0) {
        return nullptr;
    }
    const Chunk* c = chunks[lo - 1];
    return phys < c->phys + c->size ? c : nullptr;
}

} // namespace Metal::GuestMemory
