// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Data roles for actual admitted preparation workers. Non-formal executables
// MUST retain their old Shared gates; an environment variable cannot admit code.
#include "bundled_runtime_admission.h"
#include "gpu_runtime_state.h"
#include <cstdlib>
#include <optional>

namespace BbPreparationPaths {
namespace fs=std::filesystem;
inline void Need(bool ok,const char*why){if(!ok)throw std::runtime_error(why);}
struct Roots {
    fs::path data,work,gpu;
    static Roots Formal(const BbGpuState::Environment&env) {
        Roots result;
        result.data=BbGpuState::Directory(env("BB_DATA_DIR"));
        result.work=BbGpuState::Directory((result.data/"arm64-runtime-work").string(),true);
        const auto base=BbGpuState::Directory(env("BB_ARM_GPU_BASE_DIR"));
        result.gpu=BbGpuState::Formal(env,true);
        Need(result.gpu==base/"arm64-native-v1","preparation requires selected native GPU namespace");
        Need(result.work!=result.gpu&&!BbGpuState::Within(result.work,result.gpu)&&!BbGpuState::Within(result.gpu,result.work),"preparation work/cache separation");
        return result;
    }
    void Pso(const fs::path&packet,const fs::path&scratch)const {
        BbGpuState::Directory(packet.parent_path().string());BbGpuState::Directory(scratch.string());
        Need(BbGpuState::Within(packet.parent_path(),work)&&BbGpuState::Within(scratch,work),"preparation packet/scratch outside admitted launcher work");
        Need(scratch!=packet.parent_path()&&fs::is_empty(scratch),"fresh empty preparation scratch required");
    }
    void Module(const fs::path&packet,const fs::path&output)const {
        const auto automatic=BbGpuState::Directory((gpu/"automatic-game-inputs").string());
        BbGpuState::Directory(packet.parent_path().string());BbGpuState::Directory(output.string());
        Need(BbGpuState::Within(packet.parent_path(),automatic)&&BbGpuState::Within(output,automatic),"module packet/output outside selected automatic input tree");
    }
    void Companion(const fs::path&store,const fs::path&game)const {
        BbGpuState::Directory(store.string());BbGpuState::Directory(game.string());
        Need(BbGpuState::Within(store,gpu/"automatic-game-inputs")&&BbGpuState::Within(game,gpu/"automatic-game-inputs/source-views"),"companion source/store outside selected native input tree");
    }
};
inline std::optional<Roots> Actual(BbBundledRuntime::Role expected) {
    const auto&code=BbBundledRuntime::Actual();
    if(!code)return std::nullopt;
    Need(code->IsWorker()&&code->role==expected,"preparation executable role mismatch");
    return Roots::Formal([](const char*key){const char*value=getenv(key);return std::string(value?value:"");});
}
}
