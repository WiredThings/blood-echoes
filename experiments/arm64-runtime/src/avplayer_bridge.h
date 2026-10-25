// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "core/guest_cpu/hle_call_adapter.h"
#include <memory>
#include <string_view>
namespace BbAvPlayerBridge {
std::shared_ptr<Core::GuestCpu::HleCallAdapter> Adapter(std::string_view name,uintptr_t original);
size_t ActiveHandles();
size_t CallbackContexts();
}
