// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdlib>
#include <cstring>
#include <stdexcept>
namespace BbLauncherCpu {
inline bool Enabled(const char* key) {
    const auto* value = std::getenv(key);
    return value && !std::strcmp(value, "1");
}
inline void Default(const char* key) {
    if (!std::getenv(key) && setenv(key, "1", 0))
        throw std::runtime_error("cannot configure native component policy");
}
// Called only after the original-launcher private session has been validated.
inline bool ConfigureNetworkAdmission() {
    if (!Enabled("BB_ONLINE")) return false;
    Default("BB_ARM_ALLOW_NETWORK");
    return Enabled("BB_ARM_ALLOW_NETWORK");
}
inline void ConfigureExecutionDefaults(bool graphics) {
    Default("BB_ARM_PACKED_VENEERS");
    // Preserve the original launcher's memory-lifetime selection; these are its
    // architecture-specific hook implementations, not a second frontend policy.
    if (graphics && Enabled("BB_GPU_LIFETIME")) {
        Default("BB_ARM_RESOURCE_HOOKS");
        if (Enabled("BB_ARM_RESOURCE_HOOKS")) {
            Default("BB_ARM_RESOURCE_FAST");
            if (Enabled("BB_ARM_RESOURCE_FAST")) Default("BB_ARM_RESOURCE_LEAF");
            Default("BB_ARM_EXTRA_HEAPS");
            // Coherence/lifetime safety implementations used by the admitted
            // ARM backend. Keep explicit overrides and the original lifetime
            // selection; these do not select effects, resolution or cache size.
            Default("BB_ARM_PAYLOAD_GATE");
            Default("BB_LAYER_READ_TRAPS");
            Default("BB_ARM_RAW_PREWRITE");
        }
    }
}
// Native component policy, independent of the frontend. Preserve explicit legacy overrides.
inline bool ConfigurePublishedLookup() {
    if (!std::getenv("BB_ARM_HLE_FAST") && setenv("BB_ARM_HLE_FAST", "1", 0))
        throw std::runtime_error("cannot configure native HLE lookup");
    const auto* value = std::getenv("BB_ARM_HLE_FAST");
    return value && value[0] == '1';
}
}
