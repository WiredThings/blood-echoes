// SPDX-License-Identifier: GPL-2.0-or-later

// Unit test of the index expansion for topologies Metal lacks (renderer_metal/mtl_index_expand,
// P4.3d): plain C++, no Metal device.

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

#include "video_core/renderer_metal/mtl_index_expand.h"

using namespace Metal::IndexExpand;
using Vec = std::vector<u32>;

namespace {

int failures = 0;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                            \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

constexpr u32 R = 0xffffffffu; // restart in the test inputs; truncated to the source width

const char* Name(Topology t) {
    switch (t) {
    case Topology::QuadList:
        return "QuadList";
    case Topology::TriangleFan:
        return "TriangleFan";
    case Topology::Polygon:
        return "Polygon";
    case Topology::LineLoop:
        return "LineLoop";
    case Topology::LineListAdjacency:
        return "LineListAdjacency";
    case Topology::LineStripAdjacency:
        return "LineStripAdjacency";
    case Topology::TriangleListAdjacency:
        return "TriangleListAdjacency";
    case Topology::TriangleStripAdjacency:
        return "TriangleStripAdjacency";
    }
    return "?";
}

Vec Run(Topology t, const void* src, u32 size, u32 count, u32 first, bool restart) {
    const u32 bound = OutputCount(t, count, restart);
    Vec out(bound + 8, 0xdeadbeef);
    const u32 n = Expand(t, src, size, count, first, restart, out.data());
    CHECK(n <= bound);
    // Nothing is written past the bound.
    for (size_t i = bound; i < out.size(); i++) {
        CHECK(out[i] == 0xdeadbeef);
    }
    out.resize(n);
    return out;
}

template <typename T>
Vec RunWidth(Topology t, const Vec& in, bool restart) {
    std::vector<T> src(in.begin(), in.end()); // truncates R to the all-ones value of T
    return Run(t, src.data(), sizeof(T), static_cast<u32>(src.size()), 0, restart);
}

/// `in` (values < 255, R = restart) expanded through every source width must give `expect`.
void CheckAll(Topology t, const Vec& in, const Vec& expect, bool restart = true) {
    const Vec r8 = RunWidth<u8>(t, in, restart);
    const Vec r16 = RunWidth<u16>(t, in, restart);
    const Vec r32 = RunWidth<u32>(t, in, restart);
    if (r8 != expect || r16 != expect || r32 != expect) {
        std::printf("FAIL %s: expected %zu entries, got u8 %zu, u16 %zu, u32 %zu\n", Name(t),
                    expect.size(), r8.size(), r16.size(), r32.size());
        failures++;
    }
}

Vec Iota(u32 n, u32 first = 0) {
    Vec v(n);
    for (u32 i = 0; i < n; i++) {
        v[i] = first + i;
    }
    return v;
}

void TestSmall() {
    // Sequential (null src) and indexed identity input.
    const auto both = [](Topology t, u32 n, const Vec& expect) {
        CHECK(Run(t, nullptr, 0, n, 0, false) == expect);
        CHECK(Run(t, nullptr, 0, n, 0, true) == expect);
        CheckAll(t, Iota(n), expect);
        CheckAll(t, Iota(n), expect, false);
    };
    both(Topology::QuadList, 8, {0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7});
    both(Topology::QuadList, 7, {0, 1, 2, 0, 2, 3});
    both(Topology::TriangleFan, 5, {0, 1, 2, 0, 2, 3, 0, 3, 4});
    both(Topology::Polygon, 4, {0, 1, 2, 0, 2, 3});
    both(Topology::LineLoop, 4, {0, 1, 1, 2, 2, 3, 3, 0});
    both(Topology::LineLoop, 2, {0, 1, 1, 0});
    both(Topology::LineListAdjacency, 8, {1, 2, 5, 6});
    both(Topology::LineStripAdjacency, 6, {1, 2, 3, 4});
    both(Topology::TriangleListAdjacency, 12, {0, 2, 4, 6, 8, 10});
    both(Topology::TriangleListAdjacency, 11, {0, 2, 4});
    both(Topology::TriangleStripAdjacency, 8, {0, 2, 4, 6});
    both(Topology::TriangleStripAdjacency, 7, {0, 2, 4});
    both(Topology::TriangleStripAdjacency, 6, {0, 2, 4});

    // A sequence starting at `first`.
    CHECK(Run(Topology::QuadList, nullptr, 0, 4, 100, false) ==
          (Vec{100, 101, 102, 100, 102, 103}));
    CHECK(Run(Topology::TriangleFan, nullptr, 0, 4, 7, true) == (Vec{7, 8, 9, 7, 9, 10}));

    // Indexed with real indices.
    CheckAll(Topology::QuadList, {9, 3, 7, 1}, {9, 3, 7, 9, 7, 1});
    CheckAll(Topology::TriangleFan, {5, 2, 8, 1}, {5, 2, 8, 5, 8, 1});
    CheckAll(Topology::LineStripAdjacency, {4, 5, 6, 7, 8}, {5, 6, 7});
}

void TestDegenerate() {
    for (u32 ti = 0; ti < 8; ti++) {
        const Topology t = static_cast<Topology>(ti);
        CHECK(OutputCount(t, 0) == 0);
        CHECK(Expand(t, nullptr, 0, 0, 0, false, nullptr) == 0);
        const u16 dummy = 0;
        CHECK(Expand(t, &dummy, 2, 0, 0, true, nullptr) == 0);
        // Fewer indices than the smallest primitive.
        CHECK(Run(t, nullptr, 0, 1, 0, false).empty());
    }
    CHECK(Run(Topology::QuadList, nullptr, 0, 3, 0, false).empty());
    CHECK(Run(Topology::TriangleFan, nullptr, 0, 2, 0, false).empty());
    CHECK(Run(Topology::LineLoop, nullptr, 0, 1, 0, false).empty());
    CHECK(Run(Topology::LineStripAdjacency, nullptr, 0, 3, 0, false).empty());
    CHECK(Run(Topology::TriangleStripAdjacency, nullptr, 0, 5, 0, false).empty());
    CHECK(Run(Topology::TriangleListAdjacency, nullptr, 0, 5, 0, false).empty());
}

void TestRestart() {
    CheckAll(Topology::QuadList, {0, 1, 2, R, 10, 11, 12, 13, 14, R, 20, 21, 22, 23},
             {10, 11, 12, 10, 12, 13, 20, 21, 22, 20, 22, 23});
    CheckAll(Topology::TriangleFan, {0, 1, 2, 3, R, 10, 11, 12}, {0, 1, 2, 0, 2, 3, 10, 11, 12});
    CheckAll(Topology::Polygon, {R, 0, 1, 2, R, R, 6, 7, 8, 9, R}, {0, 1, 2, 6, 7, 8, 6, 8, 9});
    CheckAll(Topology::LineLoop, {0, 1, 2, R, 5, 6, R, 9}, {0, 1, 1, 2, 2, 0, 5, 6, 6, 5});
    CheckAll(Topology::LineListAdjacency, {0, 1, 2, 3, 4, R, 10, 11, 12, 13}, {1, 2, 11, 12});
    CheckAll(Topology::TriangleListAdjacency, {0, 1, 2, 3, 4, 5, 6, R, 10, 11, 12, 13, 14, 15},
             {0, 2, 4, 10, 12, 14});
    // Strip outputs: one output restart between the strips, none at the ends or for empty runs.
    CheckAll(Topology::LineStripAdjacency, {0, 1, 2, 3, R, R, 10, 11, 12, 13, 14, R},
             {1, 2, R, 11, 12, 13});
    CheckAll(Topology::LineStripAdjacency, {R, 0, 1, R, 4, 5, 6, 7, R, R},
             {5, 6}); // the first run is too short
    CheckAll(Topology::TriangleStripAdjacency, {0, 1, 2, 3, 4, 5, R, 10, 11, 12, 13, 14, 15, 16, 17},
             {0, 2, 4, R, 10, 12, 14, 16});
    CheckAll(Topology::TriangleStripAdjacency, {0, 1, 2, R, 10, 11, 12, 13, 14, 15, R, 20, 21, 22},
             {10, 12, 14});

    // Without restart the all-ones value is an ordinary index.
    CHECK(RunWidth<u8>(Topology::QuadList, {0, 1, R, 3}, false) == (Vec{0, 1, 255, 0, 255, 3}));
    CHECK(RunWidth<u16>(Topology::QuadList, {0, 1, R, 3}, false) ==
          (Vec{0, 1, 0xffff, 0, 0xffff, 3}));
    CHECK(RunWidth<u32>(Topology::QuadList, {0, 1, R, 3}, false) == (Vec{0, 1, R, 0, R, 3}));
    // A restart is only recognised at the source width: 0xffff is an index in a u32 source.
    CHECK(RunWidth<u32>(Topology::TriangleFan, {0, 0xffff, 2}, true) == (Vec{0, 0xffff, 2}));
    CHECK(RunWidth<u16>(Topology::TriangleFan, {0, 1, 2, 3, R, 4, 5, 6}, true) ==
          (Vec{0, 1, 2, 0, 2, 3, 4, 5, 6}));
}

void TestRandomBounds() {
    // Random streams with restarts: the output never exceeds OutputCount, only contains input
    // values, and the three widths agree.
    std::mt19937 rng(1234);
    for (int iter = 0; iter < 2000; iter++) {
        const Topology t = static_cast<Topology>(rng() % 8);
        const u32 n = rng() % 40;
        Vec in(n);
        for (u32 i = 0; i < n; i++) {
            in[i] = (rng() % 5 == 0) ? R : rng() % 200;
        }
        const Vec r8 = RunWidth<u8>(t, in, true);
        CHECK(r8 == RunWidth<u16>(t, in, true));
        CHECK(r8 == RunWidth<u32>(t, in, true));
        for (const u32 v : r8) {
            CHECK(v == OutputRestart || std::find(in.begin(), in.end(), v) != in.end());
        }
        // Restarts are only part of a strip output.
        const OutputPrimitive out = OutputOf(t);
        if (out == OutputPrimitive::Triangle || out == OutputPrimitive::Line) {
            CHECK(std::find(r8.begin(), r8.end(), OutputRestart) == r8.end());
        }
        if (!r8.empty() && (out == OutputPrimitive::LineStrip ||
                            out == OutputPrimitive::TriangleStrip)) {
            CHECK(r8.front() != OutputRestart && r8.back() != OutputRestart);
        }
    }
}

// ---- Orientation ---------------------------------------------------------------------------

struct P {
    float x, y;
};

float Area(P a, P b, P c) {
    return 0.5f * ((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y));
}

void TestOrientation() {
    // Quads on a grid, every second one wound the other way; the vertex ids are shuffled so the
    // indices are not sequential. Both triangles of a quad must keep the quad's orientation and
    // together cover its area.
    constexpr u32 Quads = 5000;
    std::vector<P> pos(Quads * 4);
    std::vector<u32> perm = Iota(Quads * 4);
    std::mt19937 rng(7);
    std::shuffle(perm.begin(), perm.end(), rng);
    std::vector<u32> indices(Quads * 4);
    for (u32 q = 0; q < Quads; q++) {
        const float x = static_cast<float>(q % 71) * 3.0f;
        const float y = static_cast<float>(q / 71) * 3.0f;
        // A convex, non-square quad (a trapezoid) so the diagonal choice matters.
        P corner[4] = {{x, y}, {x + 2.0f, y}, {x + 1.5f, y + 2.0f}, {x + 0.2f, y + 1.5f}};
        if (q % 2 == 1) {
            std::swap(corner[1], corner[3]); // the opposite winding
        }
        for (u32 k = 0; k < 4; k++) {
            pos[perm[q * 4 + k]] = corner[k];
            indices[q * 4 + k] = perm[q * 4 + k];
        }
    }
    const Vec out = Run(Topology::QuadList, indices.data(), 4, Quads * 4, 0, false);
    CHECK(out.size() == Quads * 6);
    u32 bad = 0;
    for (u32 q = 0; q < Quads; q++) {
        const P a = pos[indices[q * 4]], b = pos[indices[q * 4 + 1]], c = pos[indices[q * 4 + 2]],
                d = pos[indices[q * 4 + 3]];
        const float quad_area = Area(a, b, c) + Area(a, c, d);
        const float t0 = Area(pos[out[q * 6]], pos[out[q * 6 + 1]], pos[out[q * 6 + 2]]);
        const float t1 = Area(pos[out[q * 6 + 3]], pos[out[q * 6 + 4]], pos[out[q * 6 + 5]]);
        const bool ccw = quad_area > 0.0f;
        if ((t0 > 0.0f) != ccw || (t1 > 0.0f) != ccw || t0 == 0.0f || t1 == 0.0f ||
            std::abs(t0 + t1 - quad_area) > 1e-4f) {
            bad++;
        }
    }
    CHECK(bad == 0);

    // A convex polygon as fan and polygon keeps its orientation too.
    const P hex[6] = {{2, 0}, {1, 1.7f}, {-1, 1.7f}, {-2, 0}, {-1, -1.7f}, {1, -1.7f}};
    for (const Topology t : {Topology::TriangleFan, Topology::Polygon}) {
        const Vec fan = Run(t, nullptr, 0, 6, 0, false);
        CHECK(fan.size() == 12);
        for (u32 i = 0; i + 2 < fan.size(); i += 3) {
            CHECK(Area(hex[fan[i]], hex[fan[i + 1]], hex[fan[i + 2]]) > 0.0f);
        }
    }
}

// ---- Misc ----------------------------------------------------------------------------------

void TestMisc() {
    using AmdGpu::PrimitiveType;
    CHECK(FromGuest(PrimitiveType::QuadList) == Topology::QuadList);
    CHECK(FromGuest(PrimitiveType::TriangleFan) == Topology::TriangleFan);
    CHECK(FromGuest(PrimitiveType::Polygon) == Topology::Polygon);
    CHECK(FromGuest(PrimitiveType::LineLoop) == Topology::LineLoop);
    CHECK(FromGuest(PrimitiveType::AdjLineList) == Topology::LineListAdjacency);
    CHECK(FromGuest(PrimitiveType::AdjLineStrip) == Topology::LineStripAdjacency);
    CHECK(FromGuest(PrimitiveType::AdjTriangleList) == Topology::TriangleListAdjacency);
    CHECK(FromGuest(PrimitiveType::AdjTriangleStrip) == Topology::TriangleStripAdjacency);
    for (const PrimitiveType p :
         {PrimitiveType::None, PrimitiveType::PointList, PrimitiveType::LineList,
          PrimitiveType::LineStrip, PrimitiveType::TriangleList, PrimitiveType::TriangleStrip,
          PrimitiveType::PatchPrimitive, PrimitiveType::RectList, PrimitiveType::QuadStrip}) {
        CHECK(!FromGuest(p).has_value());
    }

    CHECK(OutputOf(Topology::QuadList) == OutputPrimitive::Triangle);
    CHECK(OutputOf(Topology::TriangleFan) == OutputPrimitive::Triangle);
    CHECK(OutputOf(Topology::Polygon) == OutputPrimitive::Triangle);
    CHECK(OutputOf(Topology::LineLoop) == OutputPrimitive::Line);
    CHECK(OutputOf(Topology::LineListAdjacency) == OutputPrimitive::Line);
    CHECK(OutputOf(Topology::LineStripAdjacency) == OutputPrimitive::LineStrip);
    CHECK(OutputOf(Topology::TriangleListAdjacency) == OutputPrimitive::Triangle);
    CHECK(OutputOf(Topology::TriangleStripAdjacency) == OutputPrimitive::TriangleStrip);

    const u8 in[5] = {0, 1, 0xfe, 0xff, 7};
    u16 out[5];
    WidenU8(in, 5, true, out);
    CHECK(out[0] == 0 && out[1] == 1 && out[2] == 0xfe && out[3] == 0xffff && out[4] == 7);
    WidenU8(in, 5, false, out);
    CHECK(out[3] == 0xff && out[2] == 0xfe && out[4] == 7);
    WidenU8(in, 0, true, nullptr);
}

void TestPerformance() {
    constexpr u32 Count = 4u << 20;
    std::vector<u32> src32(Count);
    std::vector<u16> src16(Count);
    for (u32 i = 0; i < Count; i++) {
        src32[i] = i;
        src16[i] = static_cast<u16>(i);
    }
    std::vector<u32> dst(OutputCount(Topology::QuadList, Count));
    u32 sink = 0;
    const auto time = [&](const char* what, u32 bytes, const void* src, bool restart) {
        double best = 1e30;
        for (int rep = 0; rep < 5; rep++) {
            const auto t0 = std::chrono::steady_clock::now();
            const u32 n = Expand(Topology::QuadList, src, bytes, Count, 0, restart, dst.data());
            const auto t1 = std::chrono::steady_clock::now();
            sink += n + dst[n / 2];
            best = std::min(best, std::chrono::duration<double, std::nano>(t1 - t0).count());
        }
        std::printf("  quad list %-18s %.3f ns per input index (%u indices)\n", what,
                    best / Count, Count);
    };
    time("sequential", 0, nullptr, false);
    time("u32, no restart", 4, src32.data(), false);
    time("u32, restart", 4, src32.data(), true);
    time("u16, restart", 2, src16.data(), true);
    CHECK(sink != 1); // keep the work alive
}

} // namespace

int main() {
    TestSmall();
    TestDegenerate();
    TestRestart();
    TestRandomBounds();
    TestOrientation();
    TestMisc();
    TestPerformance();
    if (failures != 0) {
        std::printf("mtl-index-expand-test: %d failure(s)\n", failures);
        return 1;
    }
    std::printf("mtl-index-expand-test: OK\n");
    return 0;
}
