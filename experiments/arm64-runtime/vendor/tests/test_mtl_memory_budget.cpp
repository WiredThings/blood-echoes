// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <limits>
#include "video_core/renderer_metal/mtl_memory_budget.h"
using namespace Metal::MemoryBudget;
constexpr std::uint64_t GiB = std::uint64_t{1} << 30;
int main() {
    assert(Unified(12 * GiB, {}) == 12 * GiB); // OS sample unavailable
    auto memory = FromLedger(8 * GiB, 5 * GiB);
    assert(memory.outside_graphics == 3 * GiB);
    assert(Unified(12 * GiB, memory) == 9 * GiB);
    // More driver-owned graphics must not recursively shrink the cache budget.
    assert(GraphicsUsage(8 * GiB, 3 * GiB, memory) == 5 * GiB);
    assert(GraphicsUsage(12 * GiB, 7 * GiB, memory) == 5 * GiB);
    assert(GraphicsUsage(2 * GiB, 3 * GiB, memory) == 5 * GiB);
    assert(GraphicsUsage(8 * GiB, 3 * GiB, {}) == 8 * GiB);
    const auto larger = FromLedger(12 * GiB, 9 * GiB);
    assert(Unified(12 * GiB, larger) == Unified(12 * GiB, memory));
    assert(Unified(12 * GiB, FromLedger(9 * GiB, 5 * GiB)) == 8 * GiB);
    assert(Unified(12 * GiB, FromLedger(20 * GiB, 0)) == 2 * GiB);
    assert(Unified(12 * GiB, FromLedger(8 * GiB, -1)) == 4 * GiB);
    assert(Unified(12 * GiB, FromLedger(2 * GiB, 3 * GiB)) == 12 * GiB);
    assert(Unified(1 * GiB, FromLedger(8 * GiB, 0)) == 1 * GiB);
    assert(Unified(0, memory) == 0);
    assert(Unified(12 * GiB, FromLedger(std::numeric_limits<std::uint64_t>::max(), 0)) == 2 * GiB);
}
