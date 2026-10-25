// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <memory>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <variant>
#include <boost/container/small_vector.hpp>
#include <boost/container/static_vector.hpp>
#include <tsl/robin_map.h>
#include "shader_recompiler/profile.h"
#include "shader_recompiler/compile_snapshot.h"
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/specialization.h"
#include "video_core/pipeline_cache_core_types.h"

namespace AmdGpu {
class Liverpool;
union Regs;
} // namespace AmdGpu

namespace Serialization {
struct Archive;
}

namespace Shader {
struct Info;
}

namespace Vulkan {
struct PreparedDraw;
}

namespace VideoCore {

struct Program {
    struct Module {
        ShaderModuleHandle module;
        Shader::StageSpecialization spec;
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    Shader::Info info;
    ModuleList modules{};
    size_t last_used = 0; ///< bbport: permutation of the previous lookup, compared first
    /// bbport: `info` as translated, for draw-preparation workers (they must not read `info`,
    /// whose user data the GPU thread rewrites every draw). Guarded by programs_mutex.
    std::unique_ptr<Shader::Info> info_template;

    Program() = default;
    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)
        : info{stage, l_stage, params} {}

    void AddPermut(ShaderModuleHandle module, Shader::StageSpecialization&& spec) {
        modules.emplace_back(module, std::move(spec));
    }

    void InsertPermut(ShaderModuleHandle module, Shader::StageSpecialization&& spec,
                      size_t perm_idx) {
        modules.resize(std::max(modules.size(), perm_idx + 1)); // <-- beware of realloc
        modules[perm_idx] = {module, std::move(spec)};
    }
};

struct DrawIndirectParams {
    u16 vertex_sgpr_offset;
    u32 instance_sgpr_offset;
};

/// bbport: state of one graphics/compute pipeline selection. The GPU thread owns one
/// (PipelineCacheCore::sel); draw-preparation workers use their own with their register copies.
struct PipelineSelection {
    const AmdGpu::Regs* regs{};
    std::array<Shader::RuntimeInfo, MaxShaderStages> runtime_infos{};
    std::array<const Shader::Info*, MaxShaderStages> infos{};
    std::array<ShaderModuleHandle, MaxShaderStages> modules{};
    std::optional<Shader::Gcn::FetchShaderData> fetch_shader{};
    GraphicsPipelineKey graphics_key{};
    bool motion = false;
    DrawIndirectParams draw_indirect_params{};
    struct PrepWorker* worker{}; ///< set: read-only selection for a draw-preparation worker
};

/// bbport: a draw-preparation worker's own program state (see vk_draw_prep.h).
struct PrepWorker {
    struct Stage {
        const Program* program;
        u64 hash;
        Shader::HwStage hw_stage;
        VAddr pgm_base;
        const std::vector<u32>* flat;
    };
    // Node-based: stages keep pointers to these Infos while later stages are inserted.
    std::unordered_map<const Program*, Shader::Info> infos;
    boost::container::static_vector<Stage, MaxShaderStages> stages;
    bool failed = false;
};

/// What the key building asks of the device (the Vulkan instance's limits and extensions), once.
struct PipelineCapabilities {
    bool depth_clip_control{};          ///< the clip space z range [-w, w] is native
    bool vertex_input_dynamic_state{};  ///< a pipeline does not depend on the vertex formats
    bool geometry_stage{};
    bool tessellation{};
    bool mixed_any_samples{};   ///< color attachments of different sample counts
    bool mixed_depth_samples{}; ///< color samples that differ from the depth samples
};

/// Detached backend result, owned by the preparing job until GPU-thread adoption.
struct PreparedShaderModule {
    virtual ~PreparedShaderModule() = default;
};
struct PreparedShader {
    Shader::OwnedSpirv shader;
    std::unique_ptr<PreparedShaderModule> module;
};

class PipelineCompiler;
/// bbport: PipelineCompiler is complete only in pipeline_cache_core.cpp.
struct PipelineCompilerDeleter {
    void operator()(PipelineCompiler* compiler) const;
};

/// bbport (plan P4.1 R5): the backend-neutral half of a backend's pipeline cache: key building and
/// stage selection from the Liverpool registers, the recompiler programs and permutations, SPIR-V
/// emission, the key and pipeline maps, prepared pipelines, the async/skippable decisions and the
/// disk cache of keys, shader metadata and SPIR-V. The backend derives from it and implements the
/// protected hooks (shader modules, pipelines and their serialized part).
/// A derived class must call DestroyPipelines() first in its destructor: the compile threads and
/// the pipelines use the backend's members.
class PipelineCacheCore {
public:
    virtual ~PipelineCacheCore();

    void WarmUp();
    // Backend extras use the same open read-only store, before FinishPreload.
    virtual void WarmExtraPipelines() {}
    void Sync();

    /// Purely owned compilation with the caller's independent context. No cache-table mutation,
    /// guest access, or serialization. No scheduler calls this yet: source lifetime/generation
    /// capture and ordered adoption must be proven before admitting draw-preparation jobs.
    std::optional<PreparedShader> PrepareOwnedShader(
        std::shared_ptr<const Shader::CompileSnapshot> inputs, Shader::CompileContext& context);

    bool LoadComputePipeline(Serialization::Archive& ar);
    bool LoadGraphicsPipeline(Serialization::Archive& ar);
    bool LoadPipelineStage(Serialization::Archive& ar, size_t stage);

    /// `indirect`: the draw's arguments are in memory (bbport: never skipped while compiling).
    const PipelineBase* GetGraphicsPipeline(const DrawIndirectParams params = {},
                                            const Vulkan::PreparedDraw* prepared = nullptr,
                                            bool indirect = false);

    /// bbport: worker side of draw preparation: selects the pipeline key for `sel.regs` without
    /// creating anything. False when a program or permutation does not exist yet.
    bool PrepareGraphicsPipeline(PipelineSelection& sel);

    /// bbport: GPU-thread side: the pipeline for a prepared draw after checking that registers
    /// and flattened user data match; null to take the regular path.
    const PipelineBase* TryPreparedPipeline(const Vulkan::PreparedDraw& prepared);

    /// bbport: the prepared draw the last GetGraphicsPipeline used, or null (regular path).
    [[nodiscard]] const Vulkan::PreparedDraw* UsedPrepared() const noexcept {
        return used_prepared;
    }

    const PipelineBase* GetComputePipeline();

    using Result = std::tuple<const Shader::Info*, ShaderModuleHandle,
                              std::optional<Shader::Gcn::FetchShaderData>, u64>;
    Result GetProgram(PipelineSelection& sel, Shader::HwStage stage, Shader::SwStage l_stage,
                      const Shader::ShaderParams& params, Shader::Backend::Bindings& binding);

    std::optional<ShaderModuleHandle> ReplaceShader(ShaderModuleHandle module,
                                                    std::span<const u32> spv_code);

    static std::string GetShaderName(Shader::HwStage stage, u64 hash,
                                     std::optional<size_t> perm = {});

    /// Prepared dynamic vertex inputs are consumed only by a dynamic-input backend.
    /// Static-input backends (including Metal) bind the ordered packet's V# snapshot.
    [[nodiscard]] bool UsesDynamicVertexInputs() const noexcept {
        return caps.vertex_input_dynamic_state;
    }

    auto& GetProfile() const {
        return profile;
    }

protected:
    /// `device_profile`: the profile with the device-dependent fields the backend filled in; the
    /// core adds the neutral ones (sparse page shift, paged buffers, the game's copy shader).
    /// `cache_tag` names the backend's disk cache (<title id>-<tag>, empty: <title id>, the Vulkan
    /// one): the serialized pipeline part is the backend's own, so two backends must not share.
    PipelineCacheCore(AmdGpu::Liverpool* liverpool, const Shader::Profile& device_profile,
                      const PipelineCapabilities& caps, u32 sparse_page_shift,
                      std::string_view cache_tag = {});

    /// Stops the compile threads and destroys the pending and created pipelines.
    void DestroyPipelines();

    // ---- Backend hooks ----

    /// A module for `spirv`. `name` is its debug name. `from_cache`: the SPIR-V comes from the disk
    /// cache; a module the driver rejects returns the null handle (the entry is damaged), any
    /// other failure is fatal in the backend. Called on the GPU thread only.
    virtual ShaderModuleHandle CreateShaderModule(std::span<const u32> spirv, std::string_view name,
                                                  bool from_cache) = 0;
    virtual void DestroyShaderModule(ShaderModuleHandle module) = 0;
    virtual bool SupportsOwnedShaderPreparation() const { return false; }
    virtual std::unique_ptr<PreparedShaderModule> BuildOwnedShaderModule(
        std::span<const u32> spirv, std::string_view name) { return {}; }
    /// ONLY on the GPU command thread, after complete current-input revalidation.
    virtual ShaderModuleHandle AdoptOwnedShaderModule(
        std::unique_ptr<PreparedShaderModule> module) { return {}; }

    /// Shader collection for the debugger (EmulatorSettings.IsShaderCollect()); `spirv` is the
    /// emitted code, `patch` empty without a patch.
    virtual void CollectShader(const std::string& name, Shader::SwStage stage,
                               ShaderModuleHandle module, std::span<const u32> spirv,
                               std::span<const u32> code, std::span<const u32> patch,
                               bool is_patched) {}

    /// The backend's serialized part of a graphics / compute pipeline entry.
    virtual std::unique_ptr<PipelineSerialization> NewGraphicsSerialization() = 0;
    virtual std::unique_ptr<PipelineSerialization> NewComputeSerialization() = 0;

    /// Create a pipeline. `sdata` is filled (preloading false) or read (true). Graphics pipelines
    /// are also created on the compile threads (BB_ASYNC_PIPELINES).
    virtual std::unique_ptr<PipelineBase> CreateGraphicsPipeline(
        const GraphicsPipelineKey& key, std::span<const Shader::Info*, MaxShaderStages> infos,
        std::span<const Shader::RuntimeInfo, MaxShaderStages> runtime_infos,
        std::optional<const Shader::Gcn::FetchShaderData> fetch_shader,
        std::span<const ShaderModuleHandle, MaxShaderStages> modules,
        PipelineSerialization& sdata, bool preloading) = 0;
    virtual std::unique_ptr<PipelineBase> CreateComputePipeline(
        ComputePipelineKey key, const Shader::Info& info, ShaderModuleHandle module,
        PipelineSerialization& sdata, bool preloading) = 0;

    Shader::Profile profile{};
    PipelineCapabilities caps;
    AmdGpu::Liverpool* liverpool;

private:
    friend struct PipelineCacheCoreTestAccess; // deterministic no-GPU resource-table regressions
    std::string cache_tag;

    bool RefreshGraphicsKey(PipelineSelection& sel);
    bool RefreshGraphicsStages(PipelineSelection& sel);
    bool RefreshVertexInputFormats(PipelineSelection& sel);
    bool RefreshComputeKey();

    void DumpShader(std::span<const u32> code, u64 hash, Shader::HwStage stage, size_t perm_idx,
                    std::string_view ext);
    std::optional<std::vector<u32>> GetShaderPatch(u64 hash, Shader::HwStage stage, size_t perm_idx,
                                                   std::string_view ext);
    ShaderModuleHandle CompileModule(Shader::Info& info, Shader::RuntimeInfo& runtime_info,
                                     const std::span<const u32>& code, size_t perm_idx,
                                     Shader::Backend::Bindings& binding);
    const Shader::RuntimeInfo& BuildRuntimeInfo(PipelineSelection& sel, Shader::HwStage stage,
                                                Shader::SwStage l_stage);

    [[nodiscard]] bool IsPipelineCacheDirty() const {
        return num_new_pipelines > 0;
    }

    Shader::CompileContext compile_context;
    tsl::robin_map<size_t, std::unique_ptr<Program>> program_cache;
    /// bbport: exclusive for program/permutation insertions, shared for worker lookups.
    std::shared_mutex programs_mutex;
    u64 prepared_hits = 0, prepared_misses = 0;
    const Vulkan::PreparedDraw* used_prepared = nullptr;
    tsl::robin_map<ComputePipelineKey, std::unique_ptr<PipelineBase>> compute_pipelines;
    tsl::robin_map<GraphicsPipelineKey, std::unique_ptr<PipelineBase>> graphics_pipelines;
    PipelineSelection sel{}; ///< GPU thread selection state
    ComputePipelineKey compute_key{};
    u32 num_new_pipelines{}; // new pipelines added to the cache since the game start

    // Only if Config::collectShadersForDebug()
    tsl::robin_map<ShaderModuleHandle,
                   std::vector<std::variant<GraphicsPipelineKey, ComputePipelineKey>>>
        module_related_pipelines;

    /// bbport BB_ASYNC_PIPELINES: a new graphics pipeline of a pass drawn every frame is compiled
    /// on worker threads (PipelineCompiler) while its draws go without it for a frame or two,
    /// instead of the game standing still for the driver (18 ms a pipeline on a GTX 1060, 1-4 s
    /// hitches on a first session). Other passes (one-time renders) compile at once, as before.
    struct PendingPipeline;
    bool AsyncSkippable(const PipelineSelection& sel, bool indirect);
    const PipelineBase* FinishPipeline(const GraphicsPipelineKey& key, PendingPipeline& job);
    tsl::robin_map<GraphicsPipelineKey, std::shared_ptr<PendingPipeline>> pending_graphics;
    u64 async_started = 0, async_skipped = 0, async_waited = 0; ///< statistics
    std::unique_ptr<PipelineCompiler, PipelineCompilerDeleter> compiler; ///< last: its threads are joined first
};

// ---- The disk cache of keys, shader metadata and SPIR-V (pipeline_cache_core_serialization.cpp)

void RegisterPipelineData(const ComputePipelineKey& key, const PipelineSerialization& sdata);
void RegisterPipelineData(const GraphicsPipelineKey& key, u64 hash,
                          const PipelineSerialization& sdata);
void RegisterShaderMeta(const Shader::Info& info,
                        const std::optional<Shader::Gcn::FetchShaderData>& fetch_shader_data,
                        const Shader::StageSpecialization& spec, size_t perm_hash, size_t perm_idx);
void RegisterShaderBinary(std::vector<u32>&& spv, u64 pgm_hash, size_t perm_idx);

} // namespace VideoCore
