// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "core/guest_cpu/guest_cpu.h"
#include "core/guest_cpu/hle_call_service.h"
#include <atomic>
#include <span>

namespace BbCpu {
// One execution engine per host process. The service and its authoritative VMA
// and executable queries must outlive this backend and all guest workers.
class BbcpuBackend final:public Core::GuestCpuBackend {
public:
 using HleSiteValidator=bool(*)(void*,uintptr_t,size_t);
 BbcpuBackend(Core::GuestCpu::HleCallService&,HleSiteValidator,void*);
 ~BbcpuBackend() override;
 Core::GuestExecutionResult Run(const Core::GuestExecutionRequest&) override;
 Core::GuestExecutionResult CallGuest(uintptr_t,std::span<const uint64_t>);
private:
 Core::GuestCpu::HleCallService& service;
 HleSiteValidator hle_site;void* context;
 std::atomic<unsigned> running{0};
 static int Query(uintptr_t);
 static int QuerySpan(uintptr_t,size_t);
 static BbcpuBackend* Owner();
 static int Syscall(void*);
};
}
