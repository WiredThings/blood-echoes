// Shared implementation, compiled exactly once by each independent CPU host.
#include "bb_guest_frame_audit.h"
#include "bb_guest_sync_audit.h"
#include "bb_stutter_audit.h"
// SPDX-License-Identifier: MIT

#include "hle_call_service.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <string_view>
#include <cstdio>
#include <mutex>

namespace {
thread_local Core::GuestCpu::HleCallFrame* ActiveHleCallFrame{};

class ActiveHleCallScope final {
public:
    explicit ActiveHleCallScope(Core::GuestCpu::HleCallFrame& frame)
        : previous{ActiveHleCallFrame} {
        ActiveHleCallFrame = &frame;
    }

    ~ActiveHleCallScope() {
        ActiveHleCallFrame = previous;
    }

private:
    Core::GuestCpu::HleCallFrame* previous;
};
} // namespace

namespace Core::GuestCpu {

bool PublishHostRange(const void* pointer, std::size_t size, bool writable) {
    if (ActiveHleCallFrame == nullptr || pointer == nullptr ||
        ActiveHleCallFrame->publish_host_range == nullptr) {
        return false;
    }
    return ActiveHleCallFrame->publish_host_range(
        ActiveHleCallFrame->host_range_context, reinterpret_cast<std::uintptr_t>(pointer), size,
        writable);
}

bool RevokeHostRange(const void* pointer) {
    if (ActiveHleCallFrame == nullptr || pointer == nullptr ||
        ActiveHleCallFrame->revoke_host_range == nullptr) {
        return false;
    }
    return ActiveHleCallFrame->revoke_host_range(ActiveHleCallFrame->host_range_context,
                                                  reinterpret_cast<std::uintptr_t>(pointer));
}

HleCallService::HleCallService(HleCallRegistry& registry_, RangeValidator validator_,
    void* validator_context_, FailureReporter reporter_, void* reporter_context_,
    ExecutableRangeQuery executable_query_, void* executable_query_context_, const char* log_prefix_)
    : registry{registry_}, validator{validator_}, validator_context{validator_context_},
      reporter{reporter_}, reporter_context{reporter_context_},
      executable_query{executable_query_}, executable_query_context{executable_query_context_},
      log_prefix{log_prefix_} {}

// Keep diagnostic stack state out of the hot path for all other HLE calls.
[[gnu::noinline]] static HleCallResult InvokeSelectedFrame(const HleCallAdapter& adapter,HleCallFrame& frame) {
    auto& state=BbGuestFrame::ThreadState();
    BbGuestFrame::Clock clock;BbGuestFrame::Sink sink;
    BbGuestFrame::Call scope(clock,sink,state,frame.operation,adapter.guest_frame_stage,BbAudit::EnabledFor(BbAudit::GuestSubmit));
    auto result=adapter.Invoke(frame);scope.End();return result;
}

[[gnu::noinline]] static HleCallResult InvokeSelectedCondition(const HleCallAdapter& adapter,HleCallFrame& frame) {
    BbGuestFrame::Clock clock;BbGuestSync::Sink sink;
    BbGuestSync::Call scope(BbGuestSync::Get(),clock,sink,BbGuestSync::ThreadState(),adapter.guest_sync_stage,frame.gpr[detail::SysVIntegerArgs[0]],frame.gpr[detail::SysVIntegerArgs[1]],frame.operation,BbAudit::EnabledFor(BbAudit::GuestConditionWait),BbGuestFrame::ThreadState().markers!=0);
    // Cold diagnostic helper only: producer waits used to disappear when the
    // generic condition scopes were suppressed in explicit render-wait mode.
    BbProducerWait::Sink producer_sink;
    BbProducerWait::Call producer_scope(clock,producer_sink,frame.operation,
        frame.gpr[detail::SysVIntegerArgs[0]],frame.gpr[detail::SysVIntegerArgs[1]],
        BbProducerWait::Requested()&&BbAudit::EnabledFor(BbAudit::ProducerConditionWait),
        BbGuestSync::ThreadState().known_producer,adapter.guest_sync_stage==BbGuestSync::Wait);
    BbProducerWait::Link producer_link(BbProducerWait::Get(),clock,producer_sink,
        adapter.guest_sync_stage,frame.gpr[detail::SysVIntegerArgs[0]],frame.operation,
        BbProducerWait::Requested()&&BbAudit::EnabledFor(BbAudit::ProducerConditionWait),
        BbGuestSync::ThreadState().known_producer,producer_scope.Start());
    auto result=adapter.Invoke(frame);const auto* ok=std::get_if<bool>(&result);
    const bool producer_recorded=producer_scope.End(uint32_t(frame.gpr[0]),ok&&*ok);
    producer_link.End(uint32_t(frame.gpr[0]),ok&&*ok,producer_recorded);
    scope.End(uint32_t(frame.gpr[0]),ok&&*ok);return result;
}

HleDispatchResult HleCallService::Invoke(HleCallFrame& frame) {
    BbAudit::Scope lookup_audit{BbAudit::HleLookup,frame.operation,1000000};
    const auto* adapter=registry.FindBorrowed(frame.operation);
    lookup_audit.End();
    if (adapter == nullptr) {
        const HleCallFailure failure{ENOSYS, "unregistered HLE operation"};
        Report(failure);
        return failure.error;
    }
    const bool selected_condition=BbGuestSync::SuppressGeneric(adapter->guest_sync_stage,adapter->guest_sync_stage&&BbGuestSync::Requested());
    // Explicit condition mode replaces duplicate background condition clocks; other HLE waits remain.
    BbAudit::Scope wait_audit{adapter->stutter_wait&&!selected_condition?BbAudit::HLE:0,frame.operation,40000000};
    const bool short_sleep=BbAudit::EnabledFor(BbAudit::GuestSleep) && uint32_t(frame.gpr[7])<=5000 && (adapter->Name()=="runtime.kernel_usleep" || adapter->Name()=="runtime.posix_usleep");
    // Requested microseconds are a scalar SysV argument, never a guest pointer.
    // Observe oversleep without changing the timer or raising process/thread priority.
    BbAudit::Scope sleep_audit{short_sleep?BbAudit::GuestSleep:0,uint32_t(frame.gpr[7]),10000000};
    // Was a global first-256-calls-total budget. Confirmed on-device (three separate crash
    // investigations -- Sonic Mania, Rocket League, Journey) that this always exhausts during
    // boot alone: every one of those logs shows tracing stopping around index 250-255 within
    // the first couple thousand log lines, meaning nothing from actual gameplay -- where the
    // crashes this trace exists to help diagnose all actually happened -- was ever traced.
    // Trace the first call of each *distinct* function instead: an atomic exchange per
    // HleCallAdapter (traced_once), so a hot-polling API that crosses this bridge millions of
    // times still only logs once (on its first call), while a function that's never called
    // during the first 256 total calls -- like whatever void(void*) HLE function corrupted the
    // host allocator during Journey's actual gameplay -- still gets its one, only log line
    // whenever it first runs, no matter how deep into the session that is.
    const bool trace = !adapter->traced_once.exchange(true, std::memory_order_relaxed);
    if (trace) {
        std::fprintf(stderr, "%s_BEGIN operation=%llu name=%.*s rsp=%#llx\n",
                     log_prefix, static_cast<unsigned long long>(frame.operation),
                     static_cast<int>(adapter->Name().size()), adapter->Name().data(),
                     static_cast<unsigned long long>(frame.rsp));
    }
    frame.validate_range = ValidateRange;
    frame.validate_context = this;
    frame.publish_host_range = PublishHostRange;
    frame.revoke_host_range = RevokeHostRange;
    frame.host_range_context = this;
    const ActiveHleCallScope active_frame{frame};
    const auto result = adapter->guest_frame_stage && BbGuestFrame::Requested()?InvokeSelectedFrame(*adapter,frame):selected_condition?InvokeSelectedCondition(*adapter,frame):adapter->Invoke(frame);
    if (const auto* failure = std::get_if<HleCallFailure>(&result)) {
        if (trace) {
            std::fprintf(stderr, "%s_END operation=%llu name=%.*s error=%d\n",
                         log_prefix, static_cast<unsigned long long>(frame.operation),
                         static_cast<int>(adapter->Name().size()), adapter->Name().data(),
                         failure->error);
        }
        Report(*failure);
        return failure->error;
    }
    if (trace) {
        std::fprintf(stderr, "%s_END operation=%llu name=%.*s rax=%#llx\n",
                     log_prefix, static_cast<unsigned long long>(frame.operation),
                     static_cast<int>(adapter->Name().size()), adapter->Name().data(),
                     static_cast<unsigned long long>(frame.gpr[0]));
    }
    return true;
}

std::optional<GuestExecutionRange> HleCallService::QueryExecutableRange(
    std::uintptr_t address) {
    if (executable_query == nullptr) {
        return std::nullopt;
    }
    return executable_query(executable_query_context, address);
}

bool HleCallService::ValidateRange(void* context, std::uintptr_t address, std::size_t size,
                                   bool writable) {
    if (context == nullptr) {
        return false;
    }
    const auto* bridge = static_cast<const HleCallService*>(context);
    if (bridge->ValidatePublishedHostRange(address, size, writable)) {
        return true;
    }
    return bridge->validator != nullptr &&
           bridge->validator(bridge->validator_context, address, size, writable);
}

bool HleCallService::PublishHostRange(void* context, std::uintptr_t address, std::size_t size,
                                      bool writable) {
    if (context == nullptr || address == 0 || size == 0 || address > UINTPTR_MAX - size) {
        return false;
    }
    auto* bridge = static_cast<HleCallService*>(context);
    const HostRange range{address, address + size, writable};
    std::unique_lock lock{bridge->host_range_mutex};
    const bool overlaps = std::ranges::any_of(bridge->host_ranges, [&](const HostRange& existing) {
        return range.begin < existing.end && existing.begin < range.end;
    });
    if (overlaps) {
        return false;
    }
    bridge->host_ranges.push_back(range);
    bridge->host_range_count.store(bridge->host_ranges.size(),std::memory_order_release);
    return true;
}

bool HleCallService::RevokeHostRange(void* context, std::uintptr_t address) {
    if (context == nullptr || address == 0) {
        return false;
    }
    auto* bridge = static_cast<HleCallService*>(context);
    std::unique_lock lock{bridge->host_range_mutex};
    const auto range = std::ranges::find(bridge->host_ranges, address, &HostRange::begin);
    if (range == bridge->host_ranges.end()) {
        return false;
    }
    bridge->host_ranges.erase(range);
    bridge->host_range_count.store(bridge->host_ranges.size(),std::memory_order_release);
    return true;
}

bool HleCallService::ValidatePublishedHostRange(std::uintptr_t address, std::size_t size,
                                                bool writable) const {
    if (address == 0 || size == 0 || address > UINTPTR_MAX - size) {
        return false;
    }
    static const bool fast=!std::getenv("BB_ARM_RANGE_FAST")||std::string_view(std::getenv("BB_ARM_RANGE_FAST"))!="0";
    // Empty publication tables need no read lock. Nonempty tables and every
    // publish/revoke keep the existing synchronization and bounds checks.
    if(fast&&host_range_count.load(std::memory_order_acquire)==0)return false;
    const auto end = address + size;
    std::shared_lock lock{host_range_mutex};
    return std::ranges::any_of(host_ranges, [&](const HostRange& range) {
        return range.begin <= address && end <= range.end && (!writable || range.writable);
    });
}

void HleCallService::Report(const HleCallFailure& failure) const {
    if (reporter != nullptr) {
        reporter(reporter_context, failure);
    }
}

} // namespace Core::GuestCpu
