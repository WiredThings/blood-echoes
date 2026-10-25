// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Objective-C++ only. A compiler-local, bounded weak memo: it never owns a GPU object or
// caches shader payloads. Existing shader/PSO caches retain their own ownership and capacities.
#import <Metal/Metal.h>
#include <CommonCrypto/CommonDigest.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include "video_core/renderer_metal/mtl_shader.h"

namespace Metal {
struct MetalLibraryObjects {
    id<MTLLibrary> library{};
    id<MTLFunction> function{};
    std::string error;
    bool reused{};
    PersistentFunctionIdentity persistent_identity{};
};

class LibraryMemo {
    using Key = std::array<unsigned char, CC_SHA256_DIGEST_LENGTH>;
    struct KeyHash {
        size_t operator()(const Key& key) const noexcept {
            size_t result = 0;
            for (unsigned char byte : key) result = result * 131 + byte;
            return result;
        }
    };
    struct Entry {
        std::condition_variable ready;
        bool building{true};
        __weak id<MTLLibrary> library{};
        PersistentFunctionIdentity persistent_identity{};
    };
    static constexpr size_t Capacity = 1024;
    mutable std::mutex mutex;
    std::unordered_map<Key, std::shared_ptr<Entry>, KeyHash> entries;
    std::atomic<uint64_t> builds{}, hits{}, waits{};

    static Key MakeKey(const std::string& source, const std::string& entry, bool relaxed,
                       std::string_view identity_namespace) {
        CC_SHA256_CTX digest;
        CC_SHA256_Init(&digest);
        const auto update = [&](const void* data, size_t size) {
            const auto* p = static_cast<const unsigned char*>(data);
            while (size) {
                const auto n = std::min<size_t>(size, std::numeric_limits<CC_LONG>::max());
                CC_SHA256_Update(&digest, p, static_cast<CC_LONG>(n));
                p += n; size -= n;
            }
        };
        // Complete effective source after rewrites, entry point, and every non-default
        // compiler option used by BuildLibrary. Never guest hash or normalized format alone.
        const uint64_t options[]{1, 0x030100, relaxed ? 1u : 0u, source.size(), entry.size()};
        update(options, sizeof(options)); update(source.data(), source.size());
        update(entry.data(), entry.size());
        const uint64_t domain_size=identity_namespace.size();
        update(&domain_size,sizeof(domain_size));update(identity_namespace.data(),identity_namespace.size());
        Key key{}; CC_SHA256_Final(key.data(), &digest);
        return key;
    }
public:
    LibraryReuseStats Stats() const {
        std::scoped_lock lock{mutex};
        return {builds.load(), hits.load(), waits.load(), entries.size()};
    }

    template <typename Build>
    MetalLibraryObjects Get(const std::string& source, const std::string& entry,
                            bool relaxed, Build&& build, std::string_view identity_namespace = {}) {
        const auto key = MakeKey(source, entry, relaxed, identity_namespace);
        std::shared_ptr<Entry> slot;
        {
            std::unique_lock lock{mutex};
            const auto found = entries.find(key);
            if (found != entries.end()) {
                slot = found->second;
                if (slot->building) {
                    ++waits;
                    slot->ready.wait(lock, [&] { return !slot->building; });
                }
                // A strong ARC local pins the weak reference before dropping the mutex.
                id<MTLLibrary> library = slot->library;
                if (library) {
                    ++hits;
                    return {library, nil, {}, true, slot->persistent_identity};
                }
                slot->building = true;
                slot->persistent_identity = {};
            } else {
                if (entries.size() >= Capacity) {
                    for (auto it = entries.begin(); it != entries.end();) {
                        if (!it->second->building &&
                            !it->second->library) it = entries.erase(it);
                        else ++it;
                    }
                }
                if (entries.size() < Capacity) {
                    slot = std::make_shared<Entry>();
                    entries.emplace(key, slot);
                }
            }
        }
        // No global lock during driver work. A full memo simply keeps the original path;
        // an expired entry rebuilds, and failure never poisons a future attempt.
        ++builds;
        MetalLibraryObjects result;
        try {
            result = build();
            if(!result.library || !result.function) result.persistent_identity = {};
        } catch (...) {
            if (slot) {
                std::scoped_lock lock{mutex};
                slot->library = nil;slot->persistent_identity = {};
                slot->building = false; slot->ready.notify_all();
            }
            throw;
        }
        if (slot) {
            std::scoped_lock lock{mutex};
            slot->library = result.library;
            slot->persistent_identity = result.library ? result.persistent_identity : PersistentFunctionIdentity{};
            slot->building = false; slot->ready.notify_all();
        }
        return result;
    }
};
} // namespace Metal
