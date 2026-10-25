// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <optional>
#include <span>
#include <type_traits>
#include "common/guest_capture.h"
#include "video_core/amdgpu/regs_shader.h"

namespace AmdGpu::ShaderCapture {
constexpr size_t MaxProgramBytes = 512 * 1024;
constexpr size_t MaxHeaderSearchWords = 0x4000;

enum class Status { Ready, Unreadable, InvalidHeader, NotFound, StorageTooSmall, Changed };
struct Probe {
    uintptr_t base{};
    size_t header_offset{};
    BinaryInfo header{};
};
static_assert(std::is_trivially_copyable_v<BinaryInfo>);

inline bool CheckedAddress(uintptr_t base, size_t offset, size_t bytes,
                           uintptr_t& address) noexcept {
    if (offset > std::numeric_limits<uintptr_t>::max() - base) return false;
    address = base + offset;
    return bytes <= std::numeric_limits<uintptr_t>::max() - address;
}

/// All source reads occur in the POD leaf, including the fast header and fallback search.
/// This is a bounded metadata probe, not ownership of pointed-to shader/resource memory.
inline Status ProbeProgram(uintptr_t base, Probe& output) noexcept {
    output = {};
    if (!base || (base & (alignof(u32) - 1))) return Status::InvalidHeader;
    std::array<u32,2> words{};
    if (!Common::GuestCapture::TryCopy(words.data(), reinterpret_cast<const void*>(base),
                                      sizeof(words))) return Status::Unreadable;
    BinaryInfo header{};
    size_t header_offset = 0;
    bool found = false;
    if (words[0] == 0xBEEB03FF) {
        // Widen before adding/multiplying: code[1] is an untrusted guest value.
        const u64 offset = (u64(words[1]) + 1) * 2 * sizeof(u32);
        uintptr_t address{};
        if (offset > MaxProgramBytes ||
            !CheckedAddress(base, size_t(offset), sizeof(header), address))
            return Status::InvalidHeader;
        if (!Common::GuestCapture::TryCopy(&header, reinterpret_cast<const void*>(address),
                                          sizeof(header))) return Status::Unreadable;
        if (header.Valid()) { found = true; header_offset = size_t(offset); }
    }
    if (!found) {
        for (size_t word = 0; word < MaxHeaderSearchWords; ++word) {
            uintptr_t address{};
            const size_t offset = word * sizeof(u32);
            if (!CheckedAddress(base, offset, sizeof(header), address))
                return Status::InvalidHeader;
            if (!Common::GuestCapture::TryCopy(&header, reinterpret_cast<const void*>(address),
                                              sizeof(header))) return Status::Unreadable;
            if (header.Valid()) { found = true; header_offset = offset; break; }
        }
    }
    if (!found) return Status::NotFound;
    uintptr_t end{};
    if (!header.length || header.length % sizeof(u32) || header.length > MaxProgramBytes ||
        !CheckedAddress(base, 0, header.length, end)) return Status::InvalidHeader;
    output = Probe{base, header_offset, header};
    return Status::Ready;
}

struct ProgramBytes {
    uintptr_t original_base{}; // numeric inline descriptor fixup; not code.data()
    u64 hash{};
    std::array<u32,16> user_data{};
    std::span<const u32> code; // caller's owned storage, which must outlive this view
};

/// No allocation: caller reserves a bounded job/slab BEFORE entering any fault leaf.
/// Header and copied bytes are rechecked, but this is not a content/allocator generation lease.
/// A future GPU adopter must still revalidate the complete current identity/read-set.
inline Status CaptureProgramBytes(const Probe& probe, std::span<const u32,16> registers,
                                  std::span<u32> storage, ProgramBytes& output) noexcept {
    output = {}; // no stale successful view may survive a failed/partial recapture
    const size_t words = probe.header.length / sizeof(u32);
    if (!probe.base || !probe.header.Valid() || !probe.header.length ||
        probe.header.length % sizeof(u32) || probe.header.length > MaxProgramBytes)
        return Status::InvalidHeader;
    if (storage.size() < words) return Status::StorageTooSmall;
    std::array<u32,16> user_data{};
    if (!Common::GuestCapture::TryCopy(user_data.data(), registers.data(), sizeof(user_data)) ||
        !Common::GuestCapture::TryCopy(storage.data(), reinterpret_cast<const void*>(probe.base),
                                      probe.header.length)) return Status::Unreadable;
    Probe after;
    if (ProbeProgram(probe.base, after) != Status::Ready) return Status::Changed;
    if (after.header_offset != probe.header_offset ||
        std::memcmp(&after.header, &probe.header, sizeof(BinaryInfo))) return Status::Changed;
    // A small POD stack buffer avoids a second uncharged shader allocation. Ordinary mutations
    // during capture are rejected; equality here is not an atomic snapshot guarantee.
    std::array<u32,64> check{};
    for (size_t at = 0; at < words;) {
        const size_t count = std::min(check.size(), words - at);
        uintptr_t address{};
        if (!CheckedAddress(probe.base, at * sizeof(u32), count * sizeof(u32), address) ||
            !Common::GuestCapture::TryCopy(check.data(), reinterpret_cast<const void*>(address),
                                          count * sizeof(u32))) return Status::Unreadable;
        if (std::memcmp(check.data(), storage.data() + at, count * sizeof(u32)))
            return Status::Changed;
        at += count;
    }
    output.original_base = probe.base; output.hash = probe.header.shader_hash;
    output.user_data = user_data; output.code = storage.first(words);
    return Status::Ready;
}

} // namespace AmdGpu::ShaderCapture

namespace AmdGpu {
/// Only the existing lookup-only DrawPreparation path may borrow this code span. A compiling
/// candidate must first CaptureProgramBytes into its owned slot, never decode this guest span.
inline std::optional<Shader::ShaderParams> GetParamsForPreparation(const auto& program) {
    ShaderCapture::Probe probe;
    const auto base = program.template Address<uintptr_t>();
    if (ShaderCapture::ProbeProgram(base, probe) != ShaderCapture::Status::Ready) return {};
    return Shader::ShaderParams{.user_data = program.user_data,
        .code = std::span{reinterpret_cast<const u32*>(base), probe.header.length / sizeof(u32)},
        .hash = probe.header.shader_hash};
}
} // namespace AmdGpu
