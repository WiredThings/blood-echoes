// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
namespace Metal::MemoryStats {
enum class Kind { Buffer, Staging, Texture, Target, SceneProxy, Velocity, Upscaler, Occlusion, Count };
struct Counters {
    std::uint64_t bytes{}, objects{}, retired{}, discarded{}, released{}, peak{};
};
using Snapshot = std::array<Counters, static_cast<unsigned>(Kind::Count)>;
// Unique owned allocations only; guest no-copy aliases and texture views are not allocations.
// Released advances on the real Metal object's destruction, including references from views.
void Track(const void* resource, Kind kind);
void Reclassify(const void* resource, Kind kind);
void Associate(const void* resource, std::uint64_t guest_address, std::uint64_t size);
struct Association { std::uint64_t live{}, released{}, reused{}, unknown{}; };
std::array<Association, static_cast<unsigned>(Kind::Count)> ReadAssociations();
void Retire(const void* resource);
bool IsDiscarded(const void* resource);
void Discarded(const void* resource, bool discarded);
Snapshot Read();
// Bounded metadata only; no resource retention, allocation, waits or readback.
void CaptureLifetimes();
// Native diagnostic inventory only. These are encoded-command observations, not
// per-texel coverage or permission to discard a backing. Views resolve to the base.
enum class Content { Upload, Clear, Copy, Draw, StorageBinding, Unknown, Count };
bool ContentEnabled();
struct ContentSnapshot { std::array<std::uint64_t,unsigned(Content::Count)> counts{}; std::uint64_t last_us=0; };
void NoteContent(const void* texture, Content event);
ContentSnapshot ReadContent(const void* texture);
// Separate return type preserves the existing ContentSnapshot ABI in other objects.
struct ContentIdentity { std::uint64_t uid=0, born_us=0, guest=0, generation=0; };
ContentIdentity ReadIdentity(const void* texture);
void Report(const void* device, bool force = false);
} // namespace Metal::MemoryStats
