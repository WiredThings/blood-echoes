// SPDX-License-Identifier: GPL-2.0-or-later
// Livio21/metalborne 6069d161 regression: a blocked LRU prefix must not starve eligible images.
#include <cassert>
#include <vector>

#include "metal_api/shadps4/common/lru_cache.h"

int main() {
    struct Image {
        size_t lru_id;
        bool blocked;
        bool evicted = false;
    };

    Common::LeastRecentlyUsedCache<size_t, u64> lru;
    std::vector<Image> images;
    for (size_t i = 0; i < 300; ++i) {
        images.push_back({lru.Insert(i, 0), true});
    }
    images.push_back({lru.Insert(images.size(), 0), false});

    const auto collect = [&](u64 tick) {
        size_t remaining = 10;
        size_t visited = 0;
        lru.ForEachItemBelow(tick - 20, [&](size_t index) {
            if (remaining == 0 || visited == 256) {
                return true;
            }
            ++visited;
            auto& image = images[index];
            if (image.blocked) {
                lru.Touch(image.lru_id, tick);
                return false;
            }
            --remaining;
            image.evicted = true;
            lru.Free(image.lru_id);
            return false;
        });
        return visited;
    };

    assert(collect(100) == 256);
    assert(!images.back().evicted);
    assert(collect(101) == 45);
    assert(images.back().evicted);
}
