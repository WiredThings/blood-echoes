// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <atomic>
#include <cstdint>
#include <functional>
#include <shared_mutex>
#include <mutex>
#include "bbport_resource_lifetime.h"
namespace BbPayload {
// Authoritative FRONTEND gate, not the mirrored Tracker mutex. Only installed
// full allocator wrappers may advertise Ready. No gate call in flag-off wrappers.
class Gate {
public:
    class Mutation {
    public:
        explicit Mutation(Gate&);
        ~Mutation();
        Mutation(const Mutation&)=delete;
        Mutation& operator=(const Mutation&)=delete;
    private: Gate* gate{};bool outer{};
    };
    // Same faulting thread only. Other mutators remain serialized while a
    // synchronous readback RPC may borrow the payload read side.
    class FaultPause {
    public:
        explicit FaultPause(Gate&);
        ~FaultPause();
        FaultPause(const FaultPause&)=delete;
        FaultPause& operator=(const FaultPause&)=delete;
        bool Active() const noexcept {return gate!=nullptr;}
    private:Gate* gate{};
    };
    static bool CurrentMutation() noexcept;
    void Configure(bool covered) noexcept;
    bool Ready() const noexcept {return ready.load(std::memory_order_acquire);}
    bool TryWithLiveHeap(BbResourceLifetime::Tracker&,BbResourceLifetime::Tag,
                         std::uint64_t address,std::uint64_t size,
                         const std::function<bool(BbResourceLifetime::Tag)>&);
    // Read-only bounded observability for actual-FEX fixtures, no page locking.
    struct Stats {std::uint64_t entered{},waiting{},held{},completed{},readers{};};
    Stats Read() const noexcept;
private:
    std::mutex mutator_serial;
    std::shared_mutex mutex;
    std::atomic<bool> ready{false};
    std::atomic<std::uint64_t> entered{0},waiting{0},held{0},completed{0},readers{0};
};
// Defined once in bbport_resource_lifetime.cpp. Header-local singleton state
// would split between the executable and the frontend dylib.
Gate& Get();
} // namespace BbPayload
