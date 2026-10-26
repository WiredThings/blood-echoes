// SPDX-License-Identifier: GPL-3.0-or-later
// Cache lifetime regressions: active hits survive GC; collected/reused slots never escape.
#include <cassert>
#include <cstdio>
#include <unordered_map>
#include "metal_api/shadps4/common/lru_cache.h"
#include "metal_api/shadps4/video_core/texture_cache/sampler_memo.h"

int main() {
    using Memo = VideoCore::SamplerMemo<unsigned, 1>; // force collisions on every different key
    Memo memo;
    Common::LeastRecentlyUsedCache<unsigned, std::uint64_t> lru;
    std::unordered_map<unsigned, size_t> live;
    unsigned next = 1, lookups = 0, touches = 0;
    std::uint64_t generation = 0, tick = 0;
    const auto get = [&](Memo::Key key) {
        return memo.Get(key, generation, tick, [&](size_t& id) {
            ++lookups;
            const unsigned handle = next++;
            id = lru.Insert(handle, tick);
            live.emplace(handle, id);
            return handle;
        }, [&](size_t id) {
            ++touches;
            lru.Touch(id, tick);
        });
    };
    const auto collect = [&](std::uint64_t older_than) {
        lru.ForEachItemBelow(older_than, [&](unsigned handle) {
            const size_t id = live.at(handle);
            ++generation;
            live.erase(handle);
            lru.Free(id);
            return false;
        });
    };
    const Memo::Key plain{{123, 456}, 0};
    const auto first = get(plain);
    for (unsigned i = 0; i < 100000; ++i) assert(get(plain) == first);
    assert(lookups == 1 && touches == 0);
    tick = 100;
    assert(get(plain) == first && touches == 1);
    collect(99); // a memo hit must keep the owner's LRU alive, without pinning it indefinitely
    assert(live.contains(first));
    tick = 200;
    collect(199);
    assert(!live.contains(first));
    const auto second = get(plain); // the freed LRU slot can now be reused
    assert(second != first && lookups == 2 && touches == 1);
    auto depth = plain;
    depth.variant = 1ull << 32;
    const auto third = get(depth);
    assert(third != second); // comparison and plain samplers must never alias
    auto biased = plain;
    biased.variant = 0x3f800000;
    assert(get(biased) != third);
    auto different = plain;
    different.sharp[1] ^= 1;
    assert(get(different) != second);
    assert(get(plain) != first); // hash collisions must compare the entire descriptor
    std::printf("sampler memo: 100001 repeats use 1 lookup; LRU touch, GC reuse, depth, LOD and collisions passed\n");
}
