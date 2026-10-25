// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <setjmp.h>
#include <stdexcept>

extern "C" __thread sigjmp_buf* runtime_fault_recover;

namespace Common::GuestCapture {

/// No longjmp may cross a C++ owner: this leaf has POD locals only. The caller owns all
/// destination storage and any lease before entering. Do not move frontend work, locks,
/// callbacks, allocation, or logging between setjmp and memcpy. Kept out of caller frames.
[[gnu::noinline]] inline bool TryCopy(void* destination, const void* source,
                                    std::size_t bytes) noexcept {
    if (!bytes) return true;
    const auto src = reinterpret_cast<std::uintptr_t>(source);
    const auto dst = reinterpret_cast<std::uintptr_t>(destination);
    if (!src || !dst || bytes > std::numeric_limits<std::uintptr_t>::max() - src ||
        bytes > std::numeric_limits<std::uintptr_t>::max() - dst) return false;
    sigjmp_buf recover;
    auto* const previous = runtime_fault_recover;
    if (sigsetjmp(recover, 1)) {
        runtime_fault_recover = previous;
        return false;
    }
    runtime_fault_recover = &recover;
    std::atomic_signal_fence(std::memory_order_seq_cst);
    std::memcpy(destination, source, bytes);
    std::atomic_signal_fence(std::memory_order_seq_cst);
    runtime_fault_recover = previous;
    return true;
}

class Unavailable final : public std::runtime_error {
public:
    Unavailable() : std::runtime_error{"speculative guest input is unavailable"} {}
};

/// Throw only AFTER the POD leaf returns; callers then unwind ordinary C++ owners normally.
inline void CopyOrThrow(void* destination, const void* source, std::size_t bytes) {
    if (!TryCopy(destination, source, bytes)) throw Unavailable{};
}

} // namespace Common::GuestCapture
