// SPDX-License-Identifier: MIT
#pragma once

#include "../fex/fex_guest_engine.h"
#include "guest_cpu.h"

#include <memory>
#include <span>
#include <variant>

namespace Core {

class FexGuestCpuBackend final : public GuestCpuBackend {
public:
    class Thread final {
    public:
        ~Thread();

    private:
        friend class FexGuestCpuBackend;

        Thread(FexGuestCpuBackend& owner_, AetherPS4::Fex::GuestEngine::Thread* thread_)
            : owner{owner_}, thread{thread_} {}

        FexGuestCpuBackend& owner;
        AetherPS4::Fex::GuestEngine::Thread* thread{};
    };

    using CreateResult = std::variant<std::unique_ptr<FexGuestCpuBackend>, GuestExecutionFailure>;
    using ThreadResult = std::variant<std::unique_ptr<Thread>, GuestExecutionFailure>;
    using OperationResult = std::variant<bool, GuestExecutionFailure>;

    static CreateResult Create(AetherPS4::Fex::GuestBridge& bridge);

    FexGuestCpuBackend(const FexGuestCpuBackend&) = delete;
    FexGuestCpuBackend& operator=(const FexGuestCpuBackend&) = delete;
    ~FexGuestCpuBackend();

    using CacheEnvironmentResult = std::variant<AetherPS4::Fex::CpuCacheEnvironment, GuestExecutionFailure>;
    using StartupCacheResult = std::variant<AetherPS4::Fex::StartupCpuCacheInfo, GuestExecutionFailure>;
    CacheEnvironmentResult CacheEnvironment();
    std::variant<uint64_t,GuestExecutionFailure> ConfigureStartupJitReserve(uint64_t bytes);
    StartupCacheResult InstallStartupCodeCache(std::span<const std::byte> envelope,const BbCpuCache::Identity& identity,
        std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t image_base,uint64_t image_bytes,const FEXCore::ExecutableFileInfo& source);
    StartupCacheResult InspectStartupCodeCache();
    uint64_t StartupCacheResolutions() const;
    std::variant<uint64_t,GuestExecutionFailure> InstallPreparedDirectory(const std::string& path,const BbCpuCache::Identity& identity,
        std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t base,uint64_t bytes);
    std::variant<uint64_t,GuestExecutionFailure> PrepareDiskDirectory(const std::string& plan,const std::string& path,const BbCpuCache::Identity& identity,
        std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t base,uint64_t bytes,const FEXCore::ExecutableFileInfo& source);
    ThreadResult CreateThread(const GuestExecutionRequest& request);
    GuestExecutionResult Run(Thread& thread);
    GuestExecutionResult CallGuest(std::uintptr_t rip,
                                   std::span<const std::uint64_t> arguments);
    OperationResult Invalidate(Thread& thread, std::uintptr_t begin, std::size_t size);
    OperationResult DestroyThread(std::unique_ptr<Thread>& thread);
    GuestExecutionResult Run(const GuestExecutionRequest& request) override;
    std::uintptr_t ReturnAddress() const;
    GuestExecutionRange ReturnRange() const;
    GuestExecutionRange CallbackReturnRange() const;

private:
    explicit FexGuestCpuBackend(std::unique_ptr<AetherPS4::Fex::GuestEngine> engine_);
    void DestroyThreadOrAbort(Thread& thread) noexcept;

    std::unique_ptr<AetherPS4::Fex::GuestEngine> engine;
};

} // namespace Core
