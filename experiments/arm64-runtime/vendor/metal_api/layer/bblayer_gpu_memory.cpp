// SPDX-License-Identifier: GPL-2.0-or-later
#include "bblayer_gpu_memory.h"

#include <algorithm>

namespace BbLayer {

GpuMemory& GpuMemory::Get() {
    static GpuMemory instance;
    return instance;
}

void GpuMemory::SetTranslate(GuestTranslate translate_) {
    translate = translate_;
}

void GpuMemory::SetBlockShift(std::uint32_t shift) {
    block_shift = shift;
}

void GpuMemory::AddGuestSource(std::uint64_t phys, std::uint64_t size, std::uint64_t handle,
                               std::uint64_t device_address, void* owner) {
    std::scoped_lock lk{write_mutex};
    const auto* old = guest_sources.load(std::memory_order_acquire);
    auto* list = old ? new std::vector<MemorySource*>(*old) : new std::vector<MemorySource*>();
    auto* source = new MemorySource{MemorySource::Kind::Guest, phys, size, handle, device_address,
                                    owner};
    list->insert(std::ranges::upper_bound(*list, phys, {}, &MemorySource::base), source);
    guest_sources.store(list, std::memory_order_release);
}

const MemorySource* GpuMemory::GuestSource(std::uint64_t phys) const {
    const auto* list = guest_sources.load(std::memory_order_acquire);
    if (!list || list->empty()) {
        return nullptr;
    }
    // The last source starting at or below phys.
    auto it = std::ranges::upper_bound(*list, phys, {}, &MemorySource::base);
    if (it == list->begin()) {
        return nullptr;
    }
    const MemorySource* source = *--it;
    return phys - source->base < source->size ? source : nullptr;
}

std::optional<Span> GpuMemory::ResolveInPlace(std::uint64_t va, std::uint64_t size) const {
    const auto prefix = ResolvePrefix(va, size);
    if (!prefix || prefix->size != size) {
        return std::nullopt;
    }
    return prefix;
}

std::optional<Span> GpuMemory::ResolvePrefix(std::uint64_t va, std::uint64_t size) const {
    if (!translate || size == 0) {
        return std::nullopt;
    }
    std::uint64_t phys = 0, map_end = 0;
    if (!translate(va, &phys, &map_end) || map_end <= va) {
        return std::nullopt;
    }
    const MemorySource* source = GuestSource(phys);
    if (!source) {
        return std::nullopt;
    }
    const std::uint64_t limit = std::min(size, source->base + source->size - phys);
    // The mapping may be split (protection changes): the next pieces must continue it in
    // physical memory.
    std::uint64_t covered = map_end - va;
    while (covered < limit) {
        const std::uint64_t at = va + covered;
        std::uint64_t next_phys = 0, next_end = 0;
        if (!translate(at, &next_phys, &next_end) || next_phys != phys + covered ||
            next_end <= at) {
            break;
        }
        covered = next_end - va;
    }
    return Span{source, phys - source->base, va, std::min(covered, limit)};
}

Resolution GpuMemory::Resolve(std::uint64_t va, std::uint64_t size) const {
    if (size == 0) {
        return {};
    }
    const std::uint64_t first = va >> block_shift;
    const std::uint64_t end = ((va + size - 1) >> block_shift) + 1;
    {
        std::shared_lock lk{mirror_mutex};
        if (AnyValidLocked(first, end)) {
            if (AllValidLocked(first, end)) {
                auto it = mirrors.upper_bound(va);
                if (it != mirrors.begin()) {
                    const MemorySource& mirror = (--it)->second;
                    if (va >= mirror.base && va + size <= mirror.base + mirror.size) {
                        return {Resolution::Kind::Mirror,
                                Span{&mirror, va - mirror.base, va, size}};
                    }
                }
            }
            return {Resolution::Kind::Mixed, {}};
        }
    }
    if (const auto span = ResolveInPlace(va, size)) {
        return {Resolution::Kind::InPlace, *span};
    }
    return {ResolvePrefix(va, size) ? Resolution::Kind::Mixed : Resolution::Kind::None, {}};
}

void GpuMemory::AddMirror(std::uint64_t start, std::uint64_t size, std::uint64_t handle,
                          std::uint64_t device_address, void* owner) {
    std::unique_lock lk{mirror_mutex};
    ++source_version;
    mirrors[start] = MemorySource{MemorySource::Kind::Mirror, start, size, handle, device_address,
                                  owner};
}

void GpuMemory::RemoveMirror(std::uint64_t start) {
    std::unique_lock lk{mirror_mutex};
    ++source_version;
    mirrors.erase(start);
}

std::optional<MemorySource> GpuMemory::MirrorAt(std::uint64_t va) const {
    std::shared_lock lk{mirror_mutex};
    auto it = mirrors.upper_bound(va);
    if (it == mirrors.begin()) {
        return std::nullopt;
    }
    const MemorySource& mirror = (--it)->second;
    if (va - mirror.base >= mirror.size) {
        return std::nullopt;
    }
    return mirror;
}

std::vector<MemorySource> GpuMemory::MirrorsIn(std::uint64_t start, std::uint64_t end) const {
    std::vector<MemorySource> out;
    std::shared_lock lk{mirror_mutex};
    auto it = mirrors.upper_bound(start);
    if (it != mirrors.begin()) {
        --it;
    }
    for (; it != mirrors.end() && it->first < end; ++it) {
        if (it->second.base + it->second.size > start) {
            out.push_back(it->second);
        }
    }
    return out;
}

void GpuMemory::MarkValid(std::uint64_t first, std::uint64_t end) {
    if (first >= end) {
        return;
    }
    std::unique_lock lk{mirror_mutex};
    valid_version.fetch_add(1, std::memory_order_release);
    // Merge with every run touching [first, end).
    auto it = valid.upper_bound(first);
    if (it != valid.begin() && std::prev(it)->second >= first) {
        --it;
    }
    std::uint64_t a = first, b = end;
    while (it != valid.end() && it->first <= b) {
        a = std::min(a, it->first);
        b = std::max(b, it->second);
        valid_blocks -= it->second - it->first;
        it = valid.erase(it);
    }
    valid[a] = b;
    valid_blocks += b - a;
}

void GpuMemory::MarkInvalid(std::uint64_t first, std::uint64_t end) {
    if (first >= end) {
        return;
    }
    std::unique_lock lk{mirror_mutex};
    valid_version.fetch_add(1, std::memory_order_release);
    auto it = valid.upper_bound(first);
    if (it != valid.begin()) {
        --it;
    }
    while (it != valid.end() && it->first < end) {
        const std::uint64_t a = it->first, b = it->second;
        if (b <= first) {
            ++it;
            continue;
        }
        it = valid.erase(it);
        valid_blocks -= b - a;
        if (a < first) {
            valid[a] = first;
            valid_blocks += first - a;
        }
        if (b > end) {
            it = valid.emplace(end, b).first;
            valid_blocks += b - end;
            ++it;
        }
    }
}

bool GpuMemory::AnyValidLocked(std::uint64_t first, std::uint64_t end) const {
    auto it = valid.upper_bound(first);
    if (it != valid.begin() && std::prev(it)->second > first) {
        return true;
    }
    return it != valid.end() && it->first < end;
}

bool GpuMemory::AllValidLocked(std::uint64_t first, std::uint64_t end) const {
    auto it = valid.upper_bound(first);
    if (it == valid.begin()) {
        return false;
    }
    --it;
    return it->first <= first && it->second >= end;
}

bool GpuMemory::AnyValid(std::uint64_t first, std::uint64_t end) const {
    std::shared_lock lk{mirror_mutex};
    return AnyValidLocked(first, end);
}

bool GpuMemory::AllValid(std::uint64_t first, std::uint64_t end) const {
    std::shared_lock lk{mirror_mutex};
    return AllValidLocked(first, end);
}

std::uint64_t GpuMemory::ValidBytes() const {
    std::shared_lock lk{mirror_mutex};
    return valid_blocks << block_shift;
}

} // namespace BbLayer

namespace BbLayer {
std::optional<StableMirrorSnapshot> GpuMemory::SnapshotStableMirror(
    std::uint64_t va, std::uint64_t size) const {
    if (!size || size > UINT64_MAX-va) return std::nullopt;
    std::shared_lock lk{mirror_mutex, std::try_to_lock};
    if (!lk.owns_lock()) return std::nullopt;
    auto it=mirrors.upper_bound(va);
    if (it==mirrors.begin()) return std::nullopt;
    const auto& m=(--it)->second;
    if (va<m.base || va-m.base>m.size || size>m.size-(va-m.base) ||
        !m.owner || !m.handle || !AllValidLocked(va>>block_shift,((va+size-1)>>block_shift)+1))
        return std::nullopt;
    return StableMirrorSnapshot{m,va,size,source_version,valid_version.load(std::memory_order_relaxed)};
}
bool GpuMemory::WithStableMirror(const StableMirrorSnapshot& e,
    const std::function<bool(const Span&)>& callback) const {
    if (!callback || e.source.kind!=MemorySource::Kind::Mirror ||
        !e.size || e.size>UINT64_MAX-e.va) return false;
    std::shared_lock lk{mirror_mutex,std::try_to_lock};
    if (!lk.owns_lock() || source_version!=e.source_version ||
        valid_version.load(std::memory_order_relaxed)!=e.valid_version) return false;
    auto it=mirrors.find(e.source.base);
    if (it==mirrors.end()) return false;
    const auto& m=it->second;
    if (m.kind!=MemorySource::Kind::Mirror || m.base!=e.source.base || m.size!=e.source.size ||
        m.owner!=e.source.owner || m.handle!=e.source.handle ||
        m.device_address!=e.source.device_address || e.va<m.base || e.va-m.base>m.size ||
        e.size>m.size-(e.va-m.base) ||
        !AllValidLocked(e.va>>block_shift,((e.va+e.size-1)>>block_shift)+1)) return false;
    // Pass an owned stack copy; the protected table cannot remove/replace its owner.
    const MemorySource owned=m;
    return callback(Span{&owned,e.va-m.base,e.va,e.size});
}
} // namespace BbLayer
