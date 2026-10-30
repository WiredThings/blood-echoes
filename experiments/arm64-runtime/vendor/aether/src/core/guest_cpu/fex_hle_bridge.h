// SPDX-License-Identifier: MIT
#pragma once

#include "core/fex/fex_guest_engine.h"
#include "hle_call_adapter.h"
#include "hle_call_service.h"

#include <optional>
#include <atomic>
#include <shared_mutex>
#include <vector>

namespace Core::GuestCpu {

class HleGuestBridge final : public AetherPS4::Fex::GuestBridge {
public:
    using RangeValidator = bool (*)(void* context, std::uintptr_t address, std::size_t size,
                                    bool writable);
    using ExecutableRangeQuery = std::optional<GuestExecutionRange> (*)(void* context,
                                                                        std::uintptr_t address);
    using CodeFileQuery = std::optional<FEXCore::ExecutableFileSectionInfo> (*)(void*,std::uintptr_t);
    using FailureReporter = void (*)(void* context, const HleCallFailure& failure);

    HleGuestBridge(HleCallRegistry& registry_, RangeValidator validator_, void* validator_context_,
                   FailureReporter reporter_ = nullptr, void* reporter_context_ = nullptr,
                   ExecutableRangeQuery executable_query_ = nullptr,
                   void* executable_query_context_ = nullptr, CodeFileQuery code_query_ = nullptr,
                   void* code_context_ = nullptr, FEXCore::CodeMapOpener* code_output_ = nullptr);

    AetherPS4::Fex::EngineResult<bool> Invoke(HleCallFrame& frame) override;
    std::optional<GuestExecutionRange> QueryExecutableRange(std::uintptr_t address) override;
    std::optional<FEXCore::ExecutableFileSectionInfo> QueryCodeFile(std::uintptr_t address) override;
    FEXCore::CodeMapOpener* CodeMapOutput() override { return code_output; }

private:
    HleCallService service;
    CodeFileQuery code_query{};void* code_context{};FEXCore::CodeMapOpener* code_output{};
};

} // namespace Core::GuestCpu
