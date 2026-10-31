// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Code identity only. This interface never reads data, saves, settings or cache
// roots and never treats an environment flag as an application identity.
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <string_view>

namespace BbBundledRuntime {
enum class Role { CpuTop, CpuNested, Gpu, PsoWorker, ModuleWorker, BbcpuCpu, BbcpuPreparer };
struct Admission {
    Role role{};
    std::filesystem::path executable, app, code_root;
    std::string bundle_id, relative_role, native_manifest_sha256;
    std::map<std::string, std::filesystem::path> runtime, tools, support;
    std::map<std::string, std::string> runtime_sha256, tools_sha256, support_sha256;
    bool static_load_edges_verified=false;
    // Existing external signature/dyld validation remains a separate boundary.
    bool dynamic_load_edges_verified=false, signatures_verified=false;
    bool IsCpu() const { return role==Role::CpuTop || role==Role::CpuNested || role==Role::BbcpuCpu; }
    bool IsCpuPreparer() const { return role==Role::BbcpuPreparer; }
    std::string_view CpuBackend() const {
        if(role==Role::CpuTop || role==Role::CpuNested)return "fex";
        if(role==Role::BbcpuCpu || role==Role::BbcpuPreparer)return "bbcpu";
        return {};
    }
    bool IsWorker() const { return role==Role::PsoWorker || role==Role::ModuleWorker; }
    const std::filesystem::path& Runtime(std::string_view relative) const { return runtime.at(std::string(relative)); }
};
// Non-formal code returns nullopt: the caller MUST retain its old Shared gate.
// An actual formal bundle with an unknown role or bad code throws; no QA fallback.
__attribute__((visibility("default"))) std::optional<Admission> TryAdmit(
    const std::filesystem::path& actual_executable,
    const std::filesystem::path& qa_project="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype");
// One process-local immutable observation, derived from _NSGetExecutablePath.
// No argv[0]/environment code-root override. Safe to call before Metal main.
__attribute__((visibility("default"))) const std::optional<Admission>& Actual();
}

// Native network's C gate: -1 means retain the old QA guard; formal failure is
// 0 and MUST NOT fall back to a development path. No data/cache access.
extern "C" __attribute__((visibility("default"))) int bb_bundled_net_module_path(const char*module,const char*executable);
