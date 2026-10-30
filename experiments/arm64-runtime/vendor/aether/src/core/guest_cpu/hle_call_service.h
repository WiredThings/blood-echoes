// SPDX-License-Identifier: MIT
#pragma once
#include "hle_call_adapter.h"
#include <atomic>
#include <optional>
#include <shared_mutex>
#include <vector>
namespace Core::GuestCpu {
// Keep the hot successful dispatch register-sized. Detailed failures are
// reported in place; the retained FEX wrapper maps only the error code back
// to its original EngineResult without copying another full HleCallFailure.
using HleDispatchResult=std::variant<bool,int>;
static_assert(sizeof(HleDispatchResult)<=16);
// CPU-neutral SysV HLE validation/dispatch. No execution-engine dependency.
class HleCallService final {
public:
 using RangeValidator=bool(*)(void*,std::uintptr_t,std::size_t,bool);
 using ExecutableRangeQuery=std::optional<GuestExecutionRange>(*)(void*,std::uintptr_t);
 using FailureReporter=void(*)(void*,const HleCallFailure&);
 HleCallService(HleCallRegistry&,RangeValidator,void*,FailureReporter=nullptr,void* =nullptr,
                ExecutableRangeQuery=nullptr,void* =nullptr,const char* ="CPU_HLE");
 HleDispatchResult Invoke(HleCallFrame&);
 std::optional<GuestExecutionRange> QueryExecutableRange(std::uintptr_t);
private:
 struct HostRange {std::uintptr_t begin{},end{};bool writable{};};
 static bool ValidateRange(void*,std::uintptr_t,std::size_t,bool);
 static bool PublishHostRange(void*,std::uintptr_t,std::size_t,bool);
 static bool RevokeHostRange(void*,std::uintptr_t);
 bool ValidatePublishedHostRange(std::uintptr_t,std::size_t,bool)const;
 void Report(const HleCallFailure&)const;
 HleCallRegistry& registry;RangeValidator validator;void* validator_context;
 FailureReporter reporter;void* reporter_context;
 ExecutableRangeQuery executable_query;void* executable_query_context;
 const char* log_prefix;
 mutable std::shared_mutex host_range_mutex;
 std::atomic<std::size_t> host_range_count{0};std::vector<HostRange> host_ranges;
};
}
