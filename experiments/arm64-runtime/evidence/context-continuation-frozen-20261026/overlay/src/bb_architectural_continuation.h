// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstdint>
#include <cstddef>
// Internal native snapshot, deliberately NOT any PS4/Darwin signal ABI. Only an
// actual spilled root HLE boundary may produce it. No JIT pointers/bookkeeping.
namespace BbArchitecturalContinuation {
struct alignas(16) State final {
 std::uintptr_t owner{};
 std::uint64_t boundary{};
 std::array<std::uint64_t,16> gpr{};
 std::uint64_t rip{},fsbase{},gsbase{};
 std::array<__uint128_t,16> xmm{},ymm_high{};
 std::array<std::array<std::uint64_t,2>,8> mm{};
 std::array<std::uint8_t,16> x87_flags{};
 std::uint32_t rflags{},mxcsr{};
 std::uint16_t fcw{};
 std::array<std::uint16_t,6> segments{};
 std::uint8_t abridged_ftw{};
 bool avx_supported{};
};
static_assert(sizeof(State)<=1024);
}
