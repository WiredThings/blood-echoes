// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <bit>
#include <cstddef>
#include <cstdlib>
#include <optional>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include "common/assert.h"
#include "common/types.h"
#include "video_core/renderer_vulkan/vk_common.h"

namespace VideoCore {
class CacheBackend;
} // namespace VideoCore

VK_DEFINE_HANDLE(VmaAllocation)

namespace VideoCore {

namespace Detail {
/// bbport: GuestInPlace() and WriteTracking() once known (every buffer binding asks): 0 not yet,
/// 1 on, 2 off.
inline std::atomic<u8> guest_in_place{0}, write_tracking{0};
bool ComputeGuestInPlace();
bool ComputeWriteTracking();
} // namespace Detail

/// bbport BB_GUEST_IN_PLACE=1: the GPU uses the game's direct memory where it is (the arena is bound to
/// the Vulkan chunks it lives in, gpu/shim/bbport_guest_memory.cpp) instead of copies in VRAM.
inline bool GuestInPlace() {
    const u8 state = Detail::guest_in_place.load(std::memory_order_relaxed);
    return state != 0 ? state == 1 : Detail::ComputeGuestInPlace();
}
/// The driver cannot bind guest memory to the arena: BB_GUEST_IN_PLACE stays off from now on.
void DisableGuestInPlace();
/// Whether CPU writes to GPU memory are caught by page protection. Off with BB_GUEST_IN_PLACE: the
/// GPU side learns of writes from the writers (file reads, the game's resource loaders, its own
/// DMA and command writes); BB_WRITE_TRACKING=1 brings the protection back.
inline bool WriteTracking() {
    const u8 state = Detail::write_tracking.load(std::memory_order_relaxed);
    return state != 0 ? state == 1 : Detail::ComputeWriteTracking();
}
/// BB_WRITE_VERIFY=1 (diagnostics, without write tracking): pages the GPU side watches are protected as
/// well, so writes into them that nothing announced show up as write faults (their sites).
bool WriteVerify();

/// bbport BB_CONSTANTS_IN_PLACE=1 (with BB_GUEST_IN_PLACE): small read-only buffers (constants) are
/// read by the GPU where the game wrote them, when the draw executes, like everything else in
/// place, instead of being copied when the draw is decoded (constant ring) or recorded (stream
/// buffer). The copies kept a draw's values from command writes the CPU performed at decode time;
/// in place those writes are GPU commands in stream order. Experiment bit 1 of BB_TOGGLE_FILE
/// inverts it for an A/B while the game runs.
bool ConstantsInPlace();

/// Hints and requirements for the backing memory type of a commit
enum class MemoryType : u8 {
    DeviceLocal,  ///< Requests device local buffer.
    HostUncached, ///< Requires a host visible memory type optimized for CPU to GPU uploads
    HostCached,   ///< Requires a host visible memory type optimized for GPU to CPU readbacks
    Stream,       ///< Requests device local host visible buffer, falling back host memory.
    Sparse,       ///< Requires an unbacked sparse resident buffer.
};

constexpr vk::BufferUsageFlags ReadFlags =
    vk::BufferUsageFlagBits::eTransferSrc | vk::BufferUsageFlagBits::eUniformBuffer |
    vk::BufferUsageFlagBits::eIndexBuffer | vk::BufferUsageFlagBits::eVertexBuffer |
    vk::BufferUsageFlagBits::eIndirectBuffer | vk::BufferUsageFlagBits::eShaderDeviceAddress;

constexpr vk::BufferUsageFlags AllFlags =
    ReadFlags | vk::BufferUsageFlagBits::eTransferDst | vk::BufferUsageFlagBits::eStorageBuffer;

struct UniqueBuffer {
    UniqueBuffer() = default;
    ~UniqueBuffer();

    UniqueBuffer(const UniqueBuffer&) = delete;
    UniqueBuffer& operator=(const UniqueBuffer&) = delete;

    UniqueBuffer(UniqueBuffer&& other) noexcept
        : owner{other.owner}, native{std::exchange(other.native, nullptr)},
          buffer{std::exchange(other.buffer, VK_NULL_HANDLE)},
          bda_addr{std::exchange(other.bda_addr, 0)},
          allocation{std::exchange(other.allocation, VK_NULL_HANDLE)} {}

    UniqueBuffer& operator=(UniqueBuffer&& other) noexcept {
        if (this != &other) {
            Destroy();
            owner = other.owner;
            native = std::exchange(other.native, nullptr);
            buffer = std::exchange(other.buffer, VK_NULL_HANDLE);
            bda_addr = std::exchange(other.bda_addr, 0);
            allocation = std::exchange(other.allocation, VK_NULL_HANDLE);
        }
        return *this;
    }

    /// Gives the buffer back to the backend that created it (CacheBackend::DestroyBuffer).
    void Destroy();

    /// The backend that created the buffer (CacheBackend::CreateBuffer sets it), null if none.
    CacheBackend* owner{};
    /// The backend's own object (a retained MTLBuffer on Metal); null on Vulkan.
    void* native{};
    vk::Buffer buffer{};
    vk::DeviceAddress bda_addr{};
    VmaAllocation allocation{};
};

struct Buffer {
    explicit Buffer(CacheBackend& backend, VAddr cpu_addr_, u64 size_bytes_, MemoryType mem_type,
                    std::string_view debug_name = "");
    /// bbport BB_GUEST_IN_PLACE: a buffer over memory owned elsewhere (the guest memory chunk that
    /// starts at physical address `chunk_phys`, exported as a dma-buf): transfers and texel/storage
    /// reads, no device address.
    explicit Buffer(CacheBackend& backend, u64 size_bytes_, u64 chunk_phys,
                    std::string_view debug_name);

    Buffer& operator=(const Buffer&) = delete;
    Buffer(const Buffer&) = delete;

    Buffer& operator=(Buffer&&) = default;
    Buffer(Buffer&&) = default;

    [[nodiscard]] bool IsInBounds(VAddr addr, u64 size) const noexcept {
        return addr >= cpu_addr && addr + size <= cpu_addr + SizeBytes();
    }

    // Native resource lifetime, separate from its reusable wrapper/native address.
    // Default Buffer move carries this identity with the moved UniqueBuffer.
    struct LifetimeIdentity {
        static u64 Allocate(std::atomic<u64>& counter) noexcept {
            const u64 value = counter.fetch_add(1, std::memory_order_relaxed);
            if (!value) std::abort(); // wrap cannot authorize an ABA identity
            return value;
        }
        inline static std::atomic<u64> next{1};
        u64 value = Allocate(next);
    };
    [[nodiscard]] u64 Identity() const noexcept { return lifetime_identity.value; }

    [[nodiscard]] VAddr CpuAddr() const noexcept {
        return cpu_addr;
    }

    [[nodiscard]] u64 Offset(VAddr other_cpu_addr) const noexcept {
        return other_cpu_addr - cpu_addr;
    }

    size_t SizeBytes() const {
        return size_bytes;
    }

    vk::Buffer Handle() const noexcept {
        return buffer.buffer;
    }

    /// The buffer as the layer's memory module stores it (BbLayer::MemorySource::handle): the
    /// backend's object, opaque to the module.
    [[nodiscard]] u64 LayerHandle() const noexcept {
        return buffer.native ? reinterpret_cast<u64>(buffer.native)
                             : std::bit_cast<u64>(buffer.buffer);
    }

    vk::DeviceAddress BufferDeviceAddress() const noexcept {
        ASSERT_MSG(buffer.bda_addr != 0, "Can't get BDA from a non BDA buffer");
        return buffer.bda_addr;
    }

    /// Makes host writes in provided range visible to the device.
    void Flush(u64 offset, u64 size);

    /// Makes device writes in provided range visible to the host.
    void Invalidate(u64 offset, u64 size);

    VAddr cpu_addr = 0;
    size_t size_bytes = 0;
    std::span<u8> mapped_data{};
    bool is_coherent{};
    MemoryType mem_type{MemoryType::DeviceLocal};
    UniqueBuffer buffer;
    LifetimeIdentity lifetime_identity;
};

struct StreamBuffer : public Buffer {
    explicit StreamBuffer(CacheBackend& backend, MemoryType mem_type, u64 size_bytes);

    /// Reserves a region of memory from the stream buffer. Must be followed by Commit().
    std::pair<u8*, u64> Map(u64 size, u64 alignment = 0, bool allow_wait = true);

    /// Ensures that reserved bytes of memory are available to the GPU.
    void Commit();

    /// Reserves and commits a region in one step.
    std::optional<u64> Reserve(u64 size, u64 alignment = 0, bool allow_wait = true);

    /// Tick of the most recent commit into this buffer, 0 if never used.
    [[nodiscard]] u64 LastTick() const noexcept {
        return last_tick;
    }

    /// Changes on every wrap, including a nonblocking reservation that cannot yet proceed.
    [[nodiscard]] u64 WrapGeneration() const noexcept { return wrap_generation; }

    /// Maps and commits a memory region with user provided data
    u64 Copy(auto src, size_t size, size_t alignment = 0) {
        const auto [data, offset] = Map(size, alignment);
        ASSERT_MSG(data, "Failed to copy {:#x} bytes", size);
        std::memcpy(data, reinterpret_cast<const void*>(src), size);
        Commit();
        return offset;
    }

private:
    u64 wrap_generation = 1;
    struct Watch {
        u64 tick{};
        u64 upper_bound{};
    };

    /// Aligns, wraps if needed and waits for the GPU. On success mapped_size is set and the
    /// region starts at offset.
    bool PrepareMap(u64 size, u64 alignment, bool allow_wait);

    /// Advances offset past the mapped region and records the current tick for it.
    void AdvanceAndWatch();

    /// Increases the amount of watches available.
    void ReserveWatches(std::vector<Watch>& watches, std::size_t grow_size);

    /// Waits pending watches until requested upper bound.
    bool WaitPendingOperations(u64 requested_upper_bound, bool allow_wait);

private:
    CacheBackend& backend;
    vk::DeviceSize non_coherent_atom_size{};
    u64 offset{};
    u64 mapped_size{};
    u64 last_tick{};
    std::vector<Watch> current_watches;
    std::size_t current_watch_cursor{};
    std::optional<size_t> invalidation_mark;
    std::vector<Watch> previous_watches;
    std::size_t wait_cursor{};
    u64 wait_bound{};
};

} // namespace VideoCore
