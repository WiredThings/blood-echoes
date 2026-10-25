// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/compile_snapshot.h"
#include <algorithm>
#include <cstring>
#include <limits>
#include "shader_recompiler/backend/spirv/emit_spirv.h"

namespace Shader {
namespace {
size_t WordsBytes(size_t words) {
    if (words > CompileSnapshot::MaxBytes / sizeof(u32)) {
        throw std::runtime_error{"owned shader word budget exceeded"};
    }
    return words * sizeof(u32);
}
}

size_t CompileSnapshot::SizeBytes() const {
    return sizeof(*this) + (code.size() + user_data.size() + flat.size() + walker.size() +
           gs_copy.size()) * sizeof(u32) + register_reads.size() * sizeof(RegisterRead) +
           (fetch ? fetch->attributes.size() * sizeof(Gcn::VertexAttribute) : 0);
}

std::optional<CompileSnapshot> CompileSnapshot::Capture(
    const Info& observed, const ShaderParams& params, const RuntimeInfo& runtime_,
    const Profile& profile_, Backend::Bindings start_,
    const std::optional<Gcn::FetchShaderData>& fetch_) {
#if !defined(__aarch64__)
    // x86 uses executable walker bytes and a global code generator: no asynchronous admission.
    return std::nullopt;
#else
    const auto ud = observed.UserData();
    const auto flattened = observed.FlatUserData();
    const auto& srt = observed.srt_info;
    if (observed.compile_snapshot || params.hash != observed.pgm_hash ||
        params.Base() != observed.ProgramBase() || params.code.empty() ||
        runtime_.hw_stage != observed.hw_stage || runtime_.sw_stage != observed.sw_stage ||
        params.code.size_bytes() > MaxCodeBytes || ud.size() != NUM_USER_DATA_REGS ||
        flattened.size_bytes() > MaxFlatBytes || flattened.size() != srt.flattened_bufsize_dw ||
        srt.walker_func_size > MaxWalkerBytes || srt.walker_func_size % (3 * sizeof(u32)) ||
        bool(srt.walker_func) != bool(srt.walker_func_size) ||
        observed.has_fetch_shader != bool(fetch_) ||
        (fetch_ && fetch_->attributes.size() > MaxFetchAttributes)) {
        return std::nullopt;
    }
    // Motion-vector emission still reads session-global BDA scalars. It needs an explicit
    // captured emitter interface before it can be admitted as an owned background job.
    if ((observed.hw_stage == HwStage::Vertex && runtime_.hw.vs.motion_vectors) ||
        (observed.hw_stage == HwStage::Fragment && runtime_.hw.fs.motion_vectors)) {
        return std::nullopt;
    }
    const bool geometry = observed.hw_stage == HwStage::Geometry;
    const auto copy = geometry ? runtime_.hw.gs.vs_copy : std::span<const u32>{};
    if (copy.size_bytes() > MaxCodeBytes) return std::nullopt;
    const size_t bytes = sizeof(CompileSnapshot) + params.code.size_bytes() + ud.size_bytes() +
        flattened.size_bytes() + srt.walker_func_size + copy.size_bytes() +
        (fetch_ ? fetch_->attributes.size() * (sizeof(RegisterRead) + sizeof(Gcn::VertexAttribute)) : 0);
    if (bytes > MaxBytes) return std::nullopt;

    CompileSnapshot result;
    result.profile = profile_; result.runtime = runtime_; result.start = start_;
    if (geometry) result.runtime.hw.gs.vs_copy = {};
    result.hash = params.hash; result.program_base = observed.ProgramBase();
    result.hw_stage = observed.hw_stage; result.sw_stage = observed.sw_stage;
    result.code.assign(params.code.begin(), params.code.end());
    result.user_data.assign(ud.begin(), ud.end());
    result.flat.assign(flattened.begin(), flattened.end());
    result.flat_dwords = srt.flattened_bufsize_dw;
    result.walker.resize(srt.walker_func_size / sizeof(u32));
    if (!result.walker.empty())
        std::memcpy(result.walker.data(), reinterpret_cast<const void*>(srt.walker_func),
                    srt.walker_func_size);
    result.gs_copy.assign(copy.begin(), copy.end());
    result.fetch = fetch_;
    if (fetch_) {
        result.register_reads.reserve(fetch_->attributes.size());
        for (const auto& attribute : fetch_->attributes) {
            result.register_reads.push_back({attribute.sgpr_base, attribute.dword_offset,
                observed.ReadUdReg<AmdGpu::Buffer>(attribute.sgpr_base, attribute.dword_offset)});
        }
    }
    if (observed.sw_stage == SwStage::TessellationControl ||
        observed.sw_stage == SwStage::TessellationEval) {
        result.tess.emplace(); observed.ReadTessConstantBuffer(*result.tess);
    }
    // Detect ordinary mutation during capture. This does not provide atomic guest lifetime or
    // a resource generation guarantee: callers must still own/pin the source while capturing.
    if (!std::equal(result.code.begin(), result.code.end(), params.code.begin()) ||
        !std::equal(result.user_data.begin(), result.user_data.end(), ud.begin()) ||
        !std::equal(result.flat.begin(), result.flat.end(), flattened.begin())) {
        return std::nullopt;
    }
    return result;
#endif
}

RuntimeInfo CompileSnapshot::RebindRuntime() const {
    auto result = runtime;
    if (hw_stage == HwStage::Geometry) result.hw.gs.vs_copy = gs_copy;
    return result;
}

Info CompileSnapshot::MakeInfo() const {
    if (code.empty() || code.size() * sizeof(u32) > MaxCodeBytes ||
        gs_copy.size() * sizeof(u32) > MaxCodeBytes ||
        user_data.size() != NUM_USER_DATA_REGS || flat_dwords < NUM_USER_DATA_REGS ||
        flat.size() != flat_dwords || flat.size() * sizeof(u32) > MaxFlatBytes ||
        walker.size() * sizeof(u32) > MaxWalkerBytes || walker.size() % 3 ||
        runtime.hw_stage != hw_stage || runtime.sw_stage != sw_stage ||
        register_reads.size() > MaxFetchAttributes ||
        (fetch && fetch->attributes.size() > MaxFetchAttributes) ||
        (hw_stage == HwStage::Vertex && runtime.hw.vs.motion_vectors) ||
        (hw_stage == HwStage::Fragment && runtime.hw.fs.motion_vectors) || SizeBytes() > MaxBytes) {
        throw std::runtime_error{"invalid owned shader inputs"};
    }
    Info result;
    result.hw_stage = hw_stage; result.sw_stage = sw_stage; result.pgm_hash = hash;
    result.pgm_base = program_base; result.user_data = user_data;
    result.compile_snapshot = this;
    // Fetch parsing in the translator sets the SGPR itself; the emitter also uses ParseFetchShader.
    result.has_fetch_shader = bool(fetch);
    return result;
}

void CompileSnapshot::ValidateSrt(std::span<const u32> generated,
                                  u32 generated_flat_dwords) const {
    if (generated_flat_dwords != flat_dwords || generated.size() != walker.size() ||
        !std::equal(generated.begin(), generated.end(), walker.begin())) {
        throw std::runtime_error{"owned shader SRT layout changed; retry synchronously"};
    }
}

void ReadCompileSnapshotReg(const CompileSnapshot& inputs, u32 pointer, u32 offset,
                           void* output, size_t bytes) {
    if (pointer == IR::NumScalarRegs) {
        const size_t begin = WordsBytes(offset);
        const size_t available = inputs.user_data.size() * sizeof(u32);
        if (begin > available || bytes > available - begin)
            throw std::runtime_error{"owned shader direct UD read outside snapshot"};
        std::memcpy(output, reinterpret_cast<const u8*>(inputs.user_data.data()) + begin, bytes);
        return;
    }
    for (const auto& read : inputs.register_reads) {
        if (read.pointer_index == pointer && read.dword_offset == offset &&
            bytes == sizeof(read.buffer)) {
            std::memcpy(output, &read.buffer, bytes); return;
        }
    }
    throw std::runtime_error{"owned shader attempted an uncaptured register read"};
}

void RefreshCompileSnapshotFlat(const CompileSnapshot& inputs, const PersistentSrtInfo& srt,
                                std::vector<u32>& output) {
    if (srt.flattened_bufsize_dw != inputs.flat_dwords ||
        srt.walker_func_size != inputs.walker.size() * sizeof(u32) ||
        reinterpret_cast<const void*>(srt.walker_func) !=
            (inputs.walker.empty() ? nullptr : inputs.walker.data())) {
        throw std::runtime_error{"owned shader refreshed an unvalidated SRT layout"};
    }
    output = inputs.flat;
}

void ReadCompileSnapshotTess(const CompileSnapshot& inputs,
                             TessellationDataConstantBuffer& output) {
    if (!inputs.tess) throw std::runtime_error{"owned shader has no tessellation constants"};
    output = *inputs.tess;
}

const std::optional<Gcn::FetchShaderData>& CompileSnapshotFetch(const CompileSnapshot& inputs) {
    return inputs.fetch;
}

void ApplyCompileSnapshotSrt(Info& info, std::span<const u32> code, u32 flat_dwords) {
    const auto& inputs = *info.compile_snapshot;
    inputs.ValidateSrt(code, flat_dwords);
    info.srt_info.walker_func = inputs.walker.empty() ? nullptr :
        reinterpret_cast<PFN_SrtWalker>(const_cast<u32*>(inputs.walker.data()));
    info.srt_info.walker_func_size = inputs.walker.size() * sizeof(u32);
    info.srt_info.flattened_bufsize_dw = flat_dwords;
}

OwnedSpirv CompileOwnedToSpirv(std::shared_ptr<const CompileSnapshot> inputs,
                              CompileContext& context) {
    if (!inputs) throw std::runtime_error{"missing owned shader snapshot"};
    OwnedSpirv result;
    result.inputs = std::move(inputs);
    result.info = result.inputs->MakeInfo();
    result.runtime = result.inputs->RebindRuntime(); result.bindings = result.inputs->start;
    const auto ir = TranslateProgram(result.inputs->code, context.pools, result.info,
                                     result.runtime, result.inputs->profile);
    result.spirv = Backend::SPIRV::EmitSPIRV(result.inputs->profile, result.runtime, ir,
                                           result.bindings);
    return result;
}
} // namespace Shader
