// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "video_core/pipeline_cache_core.h"
namespace BbStaticVariant {
struct ReadyModule {
    std::unique_ptr<VideoCore::Program> program;
    Shader::StageSpecialization spec;
    std::vector<u32> spirv;
};
// Ordered GPU thread only. Read-only, at most one bounded record for a newly
// encountered program. No bulk preload, guest pointer retention or cache writes.
std::optional<ReadyModule> Load(const Shader::Profile&,Shader::HwStage,Shader::SwStage,
    const Shader::ShaderParams&,const Shader::RuntimeInfo&,const Shader::Backend::Bindings&);
void RuntimeCompile() noexcept;
void Initialize() noexcept;
void Adopted(u64 hash) noexcept;
void Report() noexcept;
}
