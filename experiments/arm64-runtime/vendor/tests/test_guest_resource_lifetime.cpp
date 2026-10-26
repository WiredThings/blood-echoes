// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbport_resource_lifetime.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <thread>
using namespace BbResourceLifetime;
int main() {
  Tracker t;
  auto a = t.Allocate(Domain::Range, 0x10000, 0x1000);
  assert(a && t.Owner(0x10100, 128) == a && !t.Owner(0x10fff, 2));
  assert(!t.Release({0x10000, 0x1000, 0, Domain::Range}));
  assert(t.Status(a) == State::Live &&
         !t.WithReleased(a, 0x10000, 16, 0, [] { return 16; }));
  assert(t.Release(a) && !t.Release(a));
  bool called = false;
  assert(!t.WithReleased(a, 0x10000, 16, 60000, [&] {
    called = true;
    return 16;
  }));
  assert(!called && !t.WithReleased(a, 0xffff, 16, 0, [] { return 16; }));
  assert(t.WithReleased(a, 0x10100, 128, 0, [] { return 128; }));
  auto b = t.Allocate(Domain::Range, 0x10000, 0x1000);
  assert(b.generation != a.generation && !t.Release(a));
  assert(!t.WithReleased(a, 0x10100, 128, 0, [] { return 128; }));
  assert(t.Status(b) == State::Live);
  auto c = t.Allocate(Domain::Range, 0x10800, 0x1000);
  assert(t.Status(b) == State::Reused && !t.Release(b));
  assert(t.Release(c));
  auto parent = t.Allocate(Domain::Heap, 0x10000, 0x10000);
  assert(!t.WithReleased(c, 0x10800, 128, 0, [] { return 128; }));
  assert(t.Release(parent));
  assert(t.WithReleased(c, 0x10800, 128, 0, [] { return 128; }));
  Tracker limited(2);
  auto x = limited.Allocate(Domain::Range, 0x1000, 16);
  assert(limited.Release(x));
  for (unsigned i = 0; i < 3; ++i) {
    auto q = limited.Allocate(Domain::Range, 0x2000 + 32 * i, 16);
    assert(limited.Release(q));
  }
  assert(limited.Status(x) ==
         State::Unknown); // bounded tombstone history fails closed
  auto y = limited.Allocate(Domain::Heap, 0x1000, 16);
  assert(limited.Release(y));
  limited.Allocate(Domain::Range, 0x4000, 16);
  limited.Allocate(Domain::Range, 0x5000, 16);
  assert(
      !limited.Allocate(Domain::Range, 0x1000, 16)); // unobserved replacement
  assert(!limited.WithReleased(y, 0x1000, 16, 0, [] { return 16; }));
  assert(limited.Read().domains[0].dropped == 1);
  Tracker preflight;
  auto old=preflight.Allocate(Domain::Heap,0x80000,4096);
  assert(!preflight.ReleasedRangeUnused(old,0x80000,4096,0));
  assert(preflight.Release(old));
  assert(!preflight.ReleasedRangeUnused(old,0x80000,4096,60000));
  assert(preflight.ReleasedRangeUnused(old,0x80000,4096,0));
  // No covering allocation does NOT mean that every byte is unowned.
  Tracker inspected;
  const auto inspected_old=inspected.Allocate(Domain::Heap,0x100000,4096);
  assert(inspected.Release(inspected_old));
  const auto partial=inspected.Allocate(Domain::Heap,0x100100,128);
  auto view=inspected.InspectRange(inspected_old,0x100000,1024);
  assert(view.valid && view.covered_by_tag && !view.covering_owner && !view.truncated);
  assert(view.tag_state==State::Reused && view.recorded_state==State::Released);
  assert(view.overlaps[unsigned(Domain::Heap)]==1 && view.overlap_bytes[unsigned(Domain::Heap)]==128);
  assert(inspected.Release(partial));
  const auto outside=inspected.Allocate(Domain::Range,0x100f00,128);
  view=inspected.InspectRange(inspected_old,0x100000,1024);
  assert(view.tag_state==State::Reused && view.recorded_state==State::Released);
  assert(!view.covering_owner && !view.overlaps[0] && !view.overlaps[1]);
  assert(!inspected.WithReleased(inspected_old,0x100000,1024,0,[]{return 1024;})); // policy unchanged
  assert(inspected.Release(outside));
  assert(inspected.Read().reclaimed_images==0); // diagnostics do not reclaim
  const auto unfreed=inspected.Allocate(Domain::Heap,0x200000,4096);
  const auto replacement=inspected.Allocate(Domain::Heap,0x200100,128);
  assert(inspected.Release(replacement));
  view=inspected.InspectRange(unfreed,0x200000,1024);
  assert(view.recorded_state==State::Reused && !view.overlaps[0] && !view.overlaps[1]);
  assert(!inspected.WithReleased(unfreed,0x200000,1024,0,[]{return 1024;}));
  assert(!inspected.InspectRange(unfreed,(u64{1}<<40)-1,2).valid);
  Tracker dense;
  for(unsigned i=0;i<300;++i)assert(dense.Allocate(Domain::Heap,0x300000+i*32,16));
  view=dense.InspectRange({},0x300000,10000);
  assert(view.truncated && view.overlaps[1]==256 && view.overlap_bytes[1]==4096);
  assert(limited.InspectRange(y,0x1000,16).history_dropped);
  assert(preflight.Read().reclaimed_images==0); // inspection is not a fake reclaim
  auto raced=preflight.Allocate(Domain::Range,0x80100,32);
  bool deleted=false;
  assert(!preflight.WithReleased(old,0x80000,4096,0,[&] {deleted=true;return 4096;}));
  assert(!deleted && preflight.Release(raced));
  assert(preflight.ReleasedRangeUnused(old,0x80000,4096,0));
  Tracker concurrent;
  auto z = concurrent.Allocate(Domain::Heap, 0x1000, 1024);
  assert(concurrent.Release(z));
  std::atomic<bool> started = false, finished = false;
  std::thread worker;
  assert(concurrent.WithReleased(z, 0x1000, 16, 0, [&] {
    worker = std::thread([&] {
      started = true;
      concurrent.Allocate(Domain::Heap, 0x1000, 1024);
      finished = true;
    });
    while (!started.load())
      std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    assert(!finished.load());
    return 16;
  }));
  worker.join();
  assert(finished && concurrent.Owner(0x1000, 16));
  assert(t.Read().reclaimed_images == 2 && t.Read().reclaimed_bytes == 256);
  // Disabled mode must preserve release insertion order, not allocation generation.
  for (bool priority : {false, true}) {
    Tracker reverse(2, priority);
    const auto first = reverse.Allocate(Domain::Heap, 0x500000, 16);
    const auto second = reverse.Allocate(Domain::Heap, 0x500100, 16);
    assert(reverse.Release(second) && reverse.Release(first));
    const auto third = reverse.Allocate(Domain::Heap, 0x500200, 16);
    assert(reverse.Release(third));
    assert(reverse.Status(second) == State::Unknown);
    assert(reverse.Status(first) == State::Released);
  }
  { // Same total-capacity history retention policy.
    for (bool priority : {false, true}) {
      Tracker history(8, priority);
      const auto heap = history.Allocate(Domain::Heap, 0x100000, 4096);
      assert(history.Release(heap));
      for (unsigned i = 0; i < 32; ++i) {
        const auto range = history.Allocate(Domain::Range, 0x300000, 64);
        assert(history.Release(range));
      }
      assert(history.Status(heap) == (priority ? State::Released : State::Unknown));
      bool invoked = false;
      assert(history.WithReleased(heap, 0x100000, 4096, 0, [&] {
        invoked = true;
        return 4096;
      }) == priority);
      assert(invoked == priority);
      if (priority) {
        const auto overlap = history.Allocate(Domain::Range, 0x100080, 64);
        assert(!history.WithReleased(heap, 0x100000, 4096, 0, [] {
          assert(false);
          return 1;
        }));
        assert(history.Release(overlap));
      }
    }
    // Preserving history must not turn a replacement into a genuine release.
    Tracker priority(8, true);
    const auto stale = priority.Allocate(Domain::Heap, 0x100000, 4096);
    const auto replacement = priority.Allocate(Domain::Heap, 0x100000, 4096);
    assert(priority.Release(replacement));
    for (unsigned i = 0; i < 32; ++i) {
      const auto range = priority.Allocate(Domain::Range, 0x300000, 64);
      assert(priority.Release(range));
    }
    assert(priority.Status(stale) == State::Reused);
    assert(!priority.WithReleased(stale, 0x100000, 4096, 0, [] {
      assert(false);
      return 1;
    }));
    // All-heap churn still obeys the original total cap and fails closed.
    Tracker bounded(8, true);
    const auto old = bounded.Allocate(Domain::Heap, 0x100000, 64);
    assert(bounded.Release(old));
    for (unsigned i = 0; i < 32; ++i) {
      const auto heap = bounded.Allocate(Domain::Heap, 0x300000, 64);
      assert(bounded.Release(heap));
    }
    assert(bounded.Status(old) == State::Unknown);
    assert(!bounded.WithReleased(old, 0x100000, 64, 0, [] {
      assert(false);
      return 1;
    }));
    Tracker full(2, true);
    const auto first = full.Allocate(Domain::Heap, 0x100000, 64);
    const auto second = full.Allocate(Domain::Heap, 0x100100, 64);
    assert(full.Release(first) && full.Release(second));
    const auto range = full.Allocate(Domain::Range, 0x300000, 64);
    assert(full.Release(range));
    assert(full.Status(first) == State::Released);
    assert(full.Status(second) == State::Released);
    assert(full.Status(range) == State::Unknown);
  }
  std::puts("PASS: explicit frees, generations, overlapping domains, bounded "
            "history/overflow, atomic reuse protection");
}
