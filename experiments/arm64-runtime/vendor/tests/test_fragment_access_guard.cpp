// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/renderer_metal/mtl_fragment_guard.h"
#include <cassert>
#include <cstdio>
#include <random>
#include <tuple>
#include <vector>
using Metal::FragmentAccessGuard;
int main() {
    FragmentAccessGuard g;
    // Different cache wrappers must pass the SAME native-buffer key.
    g.Buffer(7,16,192);g.Ready();g.Draw();
    auto s=g.Inspect(7,208,1);assert(!s.reasons&&!s.overlap&&s.draws==s.known_draws);
    assert(g.Inspect(7,207,1).overlap);assert(!g.Inspect(8,16,192).overlap);
    g.Buffer(7,4096,4);assert(g.Inspect(7,512,4).overlap); // bounded union is conservative
    g.Draw();assert(g.Inspect(7,10000,4).reasons&FragmentAccessGuard::MissingDraw);
    g.Reset();g.Unknown(FragmentAccessGuard::RawAddress);assert(g.Inspect(7,0,4).reasons);
    g.Reset();g.Buffer(7,~std::uint64_t{0}-2,4);assert(g.Inspect(7,0,4).reasons);
    g.Reset();assert(g.Inspect(7,~std::uint64_t{0},1).reasons);g.Buffer(0,0,1);assert(g.Inspect(7,0,1).reasons);
    g.Reset();for(unsigned i=1;i<=65;++i)g.Buffer(i,0,4);assert(g.Inspect(100,0,4).reasons&FragmentAccessGuard::Capacity);
    std::mt19937 rng(0x68);unsigned checked=0;
    for(unsigned frame=0;frame<2000;++frame) {
        g.Reset();std::vector<std::tuple<unsigned,unsigned,unsigned>> exact;
        for(unsigned step=0;step<64;++step) {
            unsigned key=1+rng()%80,offset=rng()%16384,size=1+rng()%512;
            g.Buffer(key,offset,size);exact.emplace_back(key,offset,size);g.Ready();g.Draw();
            for(unsigned probe=0;probe<8;++probe) {
                unsigned k=1+rng()%80,o=rng()%16384,n=1+rng()%512;bool hit=false;
                for(auto [ek,eo,en]:exact)hit|=ek==k&&o<eo+en&&eo<o+n;
                auto r=g.Inspect(k,o,n);if(hit)assert(r.overlap||r.reasons);assert(r.draws==r.known_draws);++checked;
            }
        }
    }
    std::printf("fragment guard: %u independent exact-interval queries, no false negatives; unknowns and boundaries passed\n",checked);
}
