// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/buffer_cache/residency_use_memo.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <random>
#include <set>
using VideoCore::ResidencyUseMemo;
int main() {
    static_assert(sizeof(ResidencyUseMemo) <= 1200);
    ResidencyUseMemo memo;memo.BeginSecond(10);assert(!memo.Seen(1,1000));
    memo.Record(1,1000,memo.Generation());assert(memo.Seen(1,1000));
    assert(!memo.Seen(1,999) && !memo.Seen(2,1000));
    memo.BeginSecond(10);assert(memo.Seen(1,1000));
    memo.Invalidate();assert(!memo.Seen(1,1000));
    memo.Record(1,1000,memo.Generation());memo.BeginSecond(11);assert(!memo.Seen(1,1000));
    memo.Record(1,1000,memo.Generation());memo.BeginSecond(10);assert(!memo.Seen(1,1000));
    auto stale=memo.BeginSecond(12);memo.Invalidate();memo.Record(5,500,stale);assert(!memo.Seen(5,500));
    // Cache collisions may miss, but can never claim another range is recorded.
    std::mt19937_64 random(64);std::set<std::pair<uint64_t,uint64_t>> known;
    for(unsigned n=0;n<100000;++n){
        if(n%137==0){memo.Invalidate();known.clear();}
        auto key=std::pair{random()%10000,random()%10000};if(key.first>key.second)std::swap(key.first,key.second);
        if(memo.Seen(key.first,key.second))assert(known.contains(key));
        memo.Record(key.first,key.second,memo.Generation());known.insert(key);assert(memo.Seen(key.first,key.second));
    }
    ResidencyUseMemo wrap(std::numeric_limits<uint64_t>::max()-1);
    wrap.BeginSecond(1);wrap.Record(0,100,wrap.Generation());assert(wrap.Seen(0,100));
    wrap.Invalidate();assert(!wrap.Seen(0,100));wrap.Record(0,100,wrap.Generation());assert(wrap.Seen(0,100));
    std::printf("residency use memo: time, rollback, demotion, collisions, wrap and 100000 randomized operations passed; bytes=%zu\n",sizeof(memo));
}
