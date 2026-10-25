// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbport_resource_lifetime.h"
#include "bbport_payload_gate.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <limits>
namespace BbResourceLifetime {
namespace {
// Only Get() in the explicitly selected private FEX heap experiment receives
// the larger live-proof bound. Retired history/seen-address bounds stay 32768;
// no texture/shader/resource cache budget changes or eager allocation.
std::atomic<const Tracker*> extra_live_tracker{nullptr};
constexpr std::size_t ExtraHeapLiveLimit=262144;
u64 NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}
bool Valid(u64 a, u64 n) {
  return a && n && n <= (u64{1} << 32) && a < (u64{1} << 40) &&
         n <= (u64{1} << 40) - a;
}
bool Overlap(u64 a, u64 n, u64 b, u64 m) { return a < b + m && b < a + n; }
} // namespace
void Tracker::Remember(Record r) {
  if (!capacity)
    return;
  if (!heap_priority) {
    // Preserve the original insertion FIFO, including out-of-order frees.
    while (retired.size() >= capacity && !order.empty()) {
      retired.erase(order.front());
      order.pop_front();
    }
    retired.emplace(r.tag.generation, r);
    order.push_back(r.tag.generation);
    return;
  }
  const auto domain = unsigned(r.tag.domain);
  if (retired.size() >= capacity) {
    unsigned victim = unsigned(Domain::Count);
    // Only heap releases can authorize texture removal. Frequent range
    // recycling must not evict that evidence. Keep the same total capacity.
    if (!priority_order[unsigned(Domain::Range)].empty())
      victim = unsigned(Domain::Range);
    else if (domain == unsigned(Domain::Range))
      return;
    else
      victim = unsigned(Domain::Heap);
    if (priority_order[victim].empty())
      return;
    retired.erase(priority_order[victim].front());
    priority_order[victim].pop_front();
  }
  retired.emplace(r.tag.generation, r);
  priority_order[domain].push_back(r.tag.generation);
}
Tag Tracker::Allocate(Domain d, u64 address, u64 size) {
  if (unsigned(d) >= unsigned(Domain::Count) || !Valid(address, size))
    return {};
  std::scoped_lock lock{mutex};
  auto &c = counters.domains[unsigned(d)];
  ++c.allocations;
  auto &entries = live[unsigned(d)];
  auto it = entries.lower_bound(address);
  if (it != entries.begin()) {
    auto prev = std::prev(it);
    if (Overlap(address, size, prev->second.address, prev->second.size))
      it = prev;
  }
  while (it != entries.end() && it->first < address + size) {
    Tag old = it->second;
    if (!Overlap(address, size, old.address, old.size)) {
      ++it;
      continue;
    }
    ++c.overlaps;
    Remember({old, State::Reused, NowMs()});
    c.live_bytes -= old.size;
    --c.live_ranges;
    it = entries.erase(it);
  }
  auto &seen = previous[unsigned(d)];
  if (seen.contains(address))
    ++c.reuses;
  const auto live_limit=d==Domain::Heap&&this==extra_live_tracker.load(std::memory_order_relaxed)?ExtraHeapLiveLimit:capacity;
  if (entries.size() >= live_limit ||
      counters.generation == std::numeric_limits<u64>::max()) {
    ++c.dropped;
    if (observer) observer(EventKind::Allocate,{address,size,0,d});
    return {};
  }
  Tag tag{address, size, ++counters.generation, d};
  entries.emplace(address, tag);
  if (seen.size() >= capacity && !seen.contains(address))
    seen.erase(seen.begin());
  seen[address] = tag.generation;
  ++c.live_ranges;
  c.live_bytes += size;
  c.peak_bytes = std::max(c.peak_bytes, c.live_bytes);
  if (observer) observer(EventKind::Allocate,tag);
  return tag;
}
Tag Tracker::Find(Domain d, u64 address) const {
  if (unsigned(d) >= unsigned(Domain::Count))
    return {};
  std::scoped_lock lock{mutex};
  const auto &entries = live[unsigned(d)];
  auto it = entries.find(address);
  return it == entries.end() ? Tag{} : it->second;
}
Tag Tracker::Owner(u64 address, u64 size) const {
  if (!Valid(address, size))
    return {};
  std::scoped_lock lock{mutex};
  Tag result{};
  for (const auto &entries : live) {
    auto it = entries.upper_bound(address);
    if (it == entries.begin())
      continue;
    --it;
    const Tag t = it->second;
    if (t.address <= address && size <= t.size &&
        address - t.address <= t.size - size &&
        (!result || t.size < result.size))
      result = t;
  }
  return result;
}
bool Tracker::Release(Tag tag) {
  if (unsigned(tag.domain) >= unsigned(Domain::Count))
    return false;
  std::scoped_lock lock{mutex};
  auto &c = counters.domains[unsigned(tag.domain)];
  auto &entries = live[unsigned(tag.domain)];
  auto it = entries.find(tag.address);
  if (!tag || it == entries.end() || it->second != tag) {
    ++c.unknown_frees;
    if (observer) observer(EventKind::Release,tag);
    return false;
  }
  entries.erase(it);
  --c.live_ranges;
  c.live_bytes -= tag.size;
  ++c.releases;
  Remember({tag, State::Released, NowMs()});
  if (observer) observer(EventKind::Release,tag);
  return true;
}
State Tracker::StatusLocked(Tag tag) const {
  if (!tag || unsigned(tag.domain) >= unsigned(Domain::Count))
    return State::Unknown;
  const auto &entries = live[unsigned(tag.domain)];
  auto it = entries.find(tag.address);
  if (it != entries.end() && it->second == tag)
    return State::Live;
  auto old = retired.find(tag.generation);
  if (old == retired.end() || old->second.tag != tag)
    return State::Unknown;
  if (old->second.state == State::Released) {
    for (const auto &domain : live) {
      auto p = domain.lower_bound(tag.address);
      if (p != domain.begin()) {
        auto before = std::prev(p);
        if (before->second.generation > tag.generation &&
            Overlap(tag.address, tag.size, before->second.address,
                    before->second.size))
          return State::Reused;
      }
      for (; p != domain.end() && p->first < tag.address + tag.size; ++p)
        if (p->second.generation > tag.generation)
          return State::Reused;
    }
  }
  return old->second.state;
}
State Tracker::Status(Tag tag) const {
  std::scoped_lock lock{mutex};
  return StatusLocked(tag);
}
bool Tracker::ReleasedRangeUnusedLocked(Tag tag,u64 address,u64 size,u64 age) const {
  if (!Valid(address,size)) return false;
  for (const auto &c : counters.domains)
    if (c.dropped)
      return false;
  if (StatusLocked(tag) != State::Released || address < tag.address ||
      size > tag.size || address - tag.address > tag.size - size)
    return false;
  const auto &record = retired.at(tag.generation);
  if (NowMs() - record.released_ms < age)
    return false;
  for (const auto &entries : live) {
    auto it = entries.lower_bound(address);
    if (it != entries.begin()) {
      auto p = std::prev(it);
      if (Overlap(address, size, p->second.address, p->second.size))
        return false;
    }
    if (it != entries.end() &&
        Overlap(address, size, it->second.address, it->second.size))
      return false;
  }
  return true;
}
bool Tracker::ReleasedRangeUnused(Tag tag,u64 address,u64 size,u64 age) const {
  std::scoped_lock lock{mutex};return ReleasedRangeUnusedLocked(tag,address,size,age);
}
RangeInspection Tracker::InspectRange(Tag tag,u64 address,u64 size) const {
  RangeInspection out;
  if(!Valid(address,size))return out;
  std::scoped_lock lock{mutex};
  out.valid=true;out.tag_state=StatusLocked(tag);
  out.covered_by_tag=Valid(tag.address,tag.size) && address>=tag.address &&
      size<=tag.size && address-tag.address<=tag.size-size;
  if(const auto old=retired.find(tag.generation);old!=retired.end() && old->second.tag==tag)
    out.recorded_state=old->second.state;
  for(unsigned d=0;d<unsigned(Domain::Count);++d) {
    out.history_dropped|=counters.domains[d].dropped!=0;
    const auto& entries=live[d];auto it=entries.lower_bound(address);
    if(it!=entries.begin()) {
      const auto before=std::prev(it);
      if(Overlap(address,size,before->second.address,before->second.size))it=before;
    }
    unsigned visited=0;
    for(;it!=entries.end() && it->first<address+size;++it) {
      if(visited++>=256) {out.truncated=true;break;}
      const auto t=it->second;
      if(!Overlap(address,size,t.address,t.size))continue;
      ++out.overlaps[d];
      out.overlap_bytes[d]+=std::min(address+size,t.address+t.size)-std::max(address,t.address);
      if(t.address<=address && size<=t.size && address-t.address<=t.size-size &&
          (!out.covering_owner || t.size<out.covering_owner.size))out.covering_owner=t;
    }
  }
  return out;
}
bool Tracker::WithReleased(Tag tag, u64 address, u64 size, u64 age,
                           const std::function<u64()> &reclaim) {
  if (remote_reclaim) return remote_reclaim(*this,tag,address,size,age,reclaim);
  return WithReleasedLocal(tag,address,size,age,reclaim);
}
bool Tracker::WithReleasedLocal(Tag tag, u64 address, u64 size, u64 age,
                           const std::function<u64()> &reclaim) {
  std::scoped_lock lock{mutex};
  if (!ReleasedRangeUnusedLocked(tag,address,size,age)) return false;
  const u64 bytes = reclaim();
  if (bytes) {
    ++counters.reclaimed_images;
    counters.reclaimed_bytes += bytes;
    return true;
  }
  return false;
}
Snapshot Tracker::Read() const {
  std::scoped_lock lock{mutex};
  return counters;
}
void Tracker::NoteScan(u64 waiting, u64 protected_images) {
  std::scoped_lock lock{mutex};
  ++counters.gc_scans;
  counters.gc_waiting += waiting;
  counters.gc_protected += protected_images;
}
namespace {
std::atomic<bool> reclaim_ready{false};
}
bool ReclaimReady() {
  return Enabled() && reclaim_ready.load(std::memory_order_acquire);
}
void Tracker::NotifyReady(bool ready) {
  std::scoped_lock lock{mutex};
  if(observer)observer(EventKind::Ready,{u64(ready),0,0,Domain::Range});
}
void SetReclaimReady(bool ready) {
  reclaim_ready.store(ready, std::memory_order_release);
  Get().NotifyReady(ready);
}
bool Enabled() {
  static const bool on = [] {
    const char *e = std::getenv("BB_GPU_LIFETIME");
    const char *traps = std::getenv("BB_GUEST_HOOK_TRAPS");
    return e && e[0] == '1' && !(traps && traps[0] == '1');
  }();
  return on;
}
Tracker &Get() {
  // Developer-only candidate until repeated-load and memory comparison pass.
  static auto *tracker = [] {
    const auto* value = std::getenv("BB_LIFETIME_HEAP_HISTORY_PRIORITY");
    const bool priority = value && value[0] == '1';
    std::printf("Guest lifetime history: heap-priority=%u, total-capacity=32768\n",
                unsigned(priority));
    auto* result=new Tracker(32768, priority);
    const auto* extra=std::getenv("BB_ARM_EXTRA_HEAPS");
    if(extra&&extra[0]=='1'){
      extra_live_tracker.store(result,std::memory_order_relaxed);
      std::printf("Guest lifetime private metadata: heap-live-limit=%zu, retired/seen=32768, lazy storage; resource cache budgets unchanged\n",ExtraHeapLiveLimit);
    }
    return result;
  }();
  return *tracker;
}
void Report() {
  if (!Enabled())
    return;
  const auto s = Get().Read();
  constexpr const char *names[] = {"range", "heap"};
  for (unsigned i = 0; i < unsigned(Domain::Count); ++i) {
    const auto &c = s.domains[i];
    std::printf(
        "Guest resource lifetime %s: allocations=%llu, releases=%llu, "
        "live-ranges=%llu, live-bytes=%llu, address-reuses=%llu, "
        "overlapping-replacements=%llu, unknown-frees=%llu, dropped=%llu\n",
        names[i], (unsigned long long)c.allocations,
        (unsigned long long)c.releases, (unsigned long long)c.live_ranges,
        (unsigned long long)c.live_bytes, (unsigned long long)c.reuses,
        (unsigned long long)c.overlaps, (unsigned long long)c.unknown_frees,
        (unsigned long long)c.dropped);
  }
  std::printf("Guest resource reclaim: images=%llu, allocation-bytes=%llu, "
              "scans=%llu, gpu-pending=%llu, protected=%llu; explicit releases "
              "only, no cache budget change\n",
              (unsigned long long)s.reclaimed_images,
              (unsigned long long)s.reclaimed_bytes,
              (unsigned long long)s.gc_scans, (unsigned long long)s.gc_waiting,
              (unsigned long long)s.gc_protected);
}
} // namespace BbResourceLifetime

namespace BbResourceLifetime {
bool Tracker::TryWithLiveHeap(Tag expected,u64 address,u64 size,
                              const std::function<bool(Tag)>& callback) {
  if(!callback||!Valid(address,size)||(expected&&expected.domain!=Domain::Heap))return false;
  std::unique_lock held{mutex,std::try_to_lock};if(!held.owns_lock())return false;
  for(const auto& c:counters.domains)if(c.dropped)return false;
  const auto& heaps=live[unsigned(Domain::Heap)];auto it=heaps.upper_bound(address);
  if(it==heaps.begin())return false;--it;const auto actual=it->second;
  if(!actual||actual.address>address||size>actual.size||address-actual.address>actual.size-size||
     (expected&&expected!=actual))return false;
  // A containing coarse Range is normal; partial/crossing live Range owners
  // cannot be used as an absence proof. No new history/storage allocation here.
  const auto& ranges=live[unsigned(Domain::Range)];auto range=ranges.upper_bound(address);
  if(range!=ranges.begin())--range;
  for(;range!=ranges.end()&&range->first<address+size;++range) {
    const auto t=range->second;if(!Overlap(address,size,t.address,t.size))continue;
    if(t.address>address||size>t.size||address-t.address>t.size-size)return false;
  }
  return callback(actual);
}
} // namespace BbResourceLifetime
namespace BbPayload {
namespace {thread_local Gate* writer_owner{};thread_local unsigned writer_depth{},fault_pause_depth{};}
Gate& Get(){static auto* gate=new Gate;return *gate;}
Gate::Mutation::Mutation(Gate& g):gate(&g) {
  if(writer_depth) {if(writer_owner!=&g||fault_pause_depth)std::abort();++writer_depth;return;}
  g.waiting.fetch_add(1,std::memory_order_release);
  g.mutator_serial.lock();
  try{g.mutex.lock();}catch(...){g.mutator_serial.unlock();throw;}
  g.waiting.fetch_sub(1,std::memory_order_release);
  writer_owner=&g;writer_depth=1;outer=true;
  g.held.fetch_add(1,std::memory_order_relaxed);g.entered.fetch_add(1,std::memory_order_relaxed);
}
Gate::Mutation::~Mutation(){
  if(!gate||writer_owner!=gate||!writer_depth||fault_pause_depth)std::abort();
  if(--writer_depth)return;
  writer_owner=nullptr;gate->held.fetch_sub(1,std::memory_order_relaxed);
  gate->completed.fetch_add(1,std::memory_order_release);gate->mutex.unlock();gate->mutator_serial.unlock();
}
bool Gate::CurrentMutation() noexcept {return writer_owner&&writer_depth;}
Gate::FaultPause::FaultPause(Gate& g) {
  if(!writer_depth)return;
  if(writer_owner!=&g)std::abort();gate=&g;
  if(!fault_pause_depth++)g.mutex.unlock();
}
Gate::FaultPause::~FaultPause(){
  if(!gate)return;
  if(writer_owner!=gate||!writer_depth||!fault_pause_depth)std::abort();
  if(!--fault_pause_depth){try{gate->mutex.lock();}catch(...){std::abort();}}
}
void Gate::Configure(bool covered) noexcept {ready.store(covered,std::memory_order_release);}
bool Gate::TryWithLiveHeap(BbResourceLifetime::Tracker& tracker,BbResourceLifetime::Tag tag,
                          std::uint64_t address,std::uint64_t size,
                          const std::function<bool(BbResourceLifetime::Tag)>& callback) {
  if(writer_depth||!Ready())return false;
  std::shared_lock held{mutex,std::try_to_lock};if(!held.owns_lock()||!Ready())return false;
  readers.fetch_add(1,std::memory_order_relaxed);
  struct Count {std::atomic<std::uint64_t>& n;~Count(){n.fetch_sub(1,std::memory_order_relaxed);}} count{readers};
  return tracker.TryWithLiveHeap(tag,address,size,callback);
}
Gate::Stats Gate::Read() const noexcept {
 return {entered.load(),waiting.load(),held.load(),completed.load(),readers.load()};
}
} // namespace BbPayload
