// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <mutex>
#include <unordered_map>
namespace BbResourceLifetime {
using u64 = std::uint64_t;
enum class Domain : unsigned { Range, Heap, Count };
enum class State { Unknown, Live, Released, Reused };
struct Tag {
  u64 address{}, size{}, generation{};
  Domain domain{};
  explicit operator bool() const { return generation != 0; }
  bool operator==(const Tag &) const = default;
};
struct Counts {
  u64 allocations{}, releases{}, unknown_frees{}, reuses{}, overlaps{},
      live_ranges{}, live_bytes{}, peak_bytes{}, dropped{};
};
struct Snapshot {
  std::array<Counts, unsigned(Domain::Count)> domains{};
  u64 generation{}, reclaimed_images{}, reclaimed_bytes{}, gc_scans{},
      gc_waiting{}, gc_protected{};
};
struct RangeInspection {
  State tag_state{}, recorded_state{};
  Tag covering_owner{};
  std::array<u64,unsigned(Domain::Count)> overlaps{}, overlap_bytes{};
  bool valid{}, covered_by_tag{}, history_dropped{}, truncated{};
};
// Bounded diagnostics: never tracks general malloc or unbounded call stacks.
// Unknown/missing/overlapping histories cannot authorize reclamation.
enum class EventKind : unsigned { Allocate=1, Release, Ready };
class Tracker {
public:
  explicit Tracker(std::size_t capacity = 32768, bool heap_priority = false)
      : capacity(capacity), heap_priority(heap_priority) {}
  using Observer = void (*)(EventKind,Tag);
  using RemoteReclaim = bool (*)(Tracker&,Tag,u64,u64,u64,const std::function<u64()>&);
  // Configured before worker threads start; ordinary in-process tracking stays local.
  void SetObserver(Observer value) { observer=value; }
  void SetRemoteReclaim(RemoteReclaim value) { remote_reclaim=value; }
  void NotifyReady(bool ready);
  Tag Allocate(Domain, u64 address, u64 size);
  Tag Find(Domain, u64 address) const;
  Tag Owner(u64 address, u64 size) const;
  bool Release(Tag);
  State Status(Tag) const;
  // Payload gate must already be held. Nonblocking actual live-Heap metadata
  // check; never Released authorization. Empty expected is a capture query.
  bool TryWithLiveHeap(Tag expected,u64 address,u64 size,
                       const std::function<bool(Tag)>& callback);
  // Holds the allocation lock through metadata removal. A replacement
  // allocation cannot publish the same address halfway through removal of the
  // old cache entry.
  bool WithReleased(Tag, u64 address, u64 size, u64 min_age_ms,
                    const std::function<u64()> &reclaim);
  // Local half of the remote lease, also used by the normal in-process path.
  bool WithReleasedLocal(Tag,u64,u64,u64,const std::function<u64()>&);
  // Read-only preflight can avoid an unnecessary encoder break. It is NOT
  // authorization: WithReleased must revalidate under the lock before removal.
  bool ReleasedRangeUnused(Tag, u64 address, u64 size, u64 min_age_ms) const;
  // Bounded read-only diagnostic, not permission to reclaim. Distinguishes a
  // covering owner from partial live overlaps and observed frees from replacements.
  RangeInspection InspectRange(Tag,u64 address,u64 size) const;
  Snapshot Read() const;
  void NoteScan(u64 waiting, u64 protected_images);

private:
  struct Record {
    Tag tag;
    State state;
    u64 released_ms{};
  };
  void Remember(Record);
  State StatusLocked(Tag) const;
  bool ReleasedRangeUnusedLocked(Tag,u64 address,u64 size,u64 min_age_ms) const;
  Observer observer{};
  RemoteReclaim remote_reclaim{};
  mutable std::mutex mutex;
  const std::size_t capacity;
  const bool heap_priority;
  std::array<std::map<u64, Tag>, unsigned(Domain::Count)> live;
  std::unordered_map<u64, Record> retired;
  std::deque<u64> order;
  std::array<std::deque<u64>, unsigned(Domain::Count)> priority_order;
  std::array<std::unordered_map<u64, u64>, unsigned(Domain::Count)> previous;
  Snapshot counters{};
};
bool Enabled();
bool ReclaimReady();
void SetReclaimReady(bool ready);
Tracker &Get();
void Report();
} // namespace BbResourceLifetime
