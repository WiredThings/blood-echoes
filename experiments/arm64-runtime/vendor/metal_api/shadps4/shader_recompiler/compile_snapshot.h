// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <memory>
#include <optional>
#include <stdexcept>
#include <vector>
#include "shader_recompiler/frontend/fetch_shader.h"
#include "shader_recompiler/profile.h"
#include "shader_recompiler/recompiler.h"

namespace Shader {

/// Transient, owned compiler inputs. Capture is ONLY legal on the ordered GPU thread while
/// the caller owns the guest inputs; this API is not a speculative guest-memory reader.
/// Never serialize this object or use a guest hash alone to authorize adopting its output.
struct CompileSnapshot {
    static constexpr size_t MaxBytes = 2 * 1024 * 1024;
    static constexpr size_t MaxCodeBytes = 512 * 1024;
    static constexpr size_t MaxFlatBytes = 1024 * 1024;
    static constexpr size_t MaxWalkerBytes = 256 * 1024;
    static constexpr size_t MaxFetchAttributes = 32;
    struct RegisterRead {
        u32 pointer_index{}, dword_offset{};
        AmdGpu::Buffer buffer{};
    };
    Profile profile{};
    RuntimeInfo runtime{}; // GS vs_copy is empty here; RebindRuntime installs the owned span.
    Backend::Bindings start{};
    u64 hash{};
    VAddr program_base{}; // scalar for inline descriptor fixups, NEVER dereferenced
    HwStage hw_stage{};
    SwStage sw_stage{};
    std::vector<u32> code, user_data, flat, walker, gs_copy;
    u32 flat_dwords{};
    std::optional<Gcn::FetchShaderData> fetch;
    std::vector<RegisterRead> register_reads;
    std::optional<TessellationDataConstantBuffer> tess;

    /// Bounds are checked before allocation; nullopt means use the existing synchronous path.
    /// `fetch` is the already parsed specialization data, not another guest pointer.
    static std::optional<CompileSnapshot> Capture(
        const Info& observed, const ShaderParams& params, const RuntimeInfo& runtime,
        const Profile& profile, Backend::Bindings start,
        const std::optional<Gcn::FetchShaderData>& fetch);
    [[nodiscard]] size_t SizeBytes() const;
    [[nodiscard]] RuntimeInfo RebindRuntime() const;
    [[nodiscard]] Info MakeInfo() const;
    void ValidateSrt(std::span<const u32> generated, u32 generated_flat_dwords) const;
};

/// One independent context per compiler thread; no shared Pools or IR nodes.
/// The result retains the snapshot because Info has spans/pointers into its owned storage.
/// No GPU module, global program/module map, cache write, or global SRT registration occurs.
struct OwnedSpirv {
    std::shared_ptr<const CompileSnapshot> inputs;
    Info info;
    RuntimeInfo runtime{};
    Backend::Bindings bindings;
    std::vector<u32> spirv;
};
OwnedSpirv CompileOwnedToSpirv(std::shared_ptr<const CompileSnapshot> inputs,
                              CompileContext& context);

// Info/fetch integration. A missing captured read throws; never falls back to guest memory.
const std::optional<Gcn::FetchShaderData>& CompileSnapshotFetch(const CompileSnapshot& inputs);
void ApplyCompileSnapshotSrt(Info& info, std::span<const u32> code, u32 flat_dwords);

} // namespace Shader
