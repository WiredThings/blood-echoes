// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Shared original runtime algorithms. No Info/SRT or guest resource execution.
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "video_core/renderer_metal/mtl_spirv_rectlist.h"
#include <cstdlib>
namespace Metal::CachedVariants {
inline constexpr u64 RectListHashSalt = 0x5265637400000000ULL;
inline std::unique_ptr<ShaderModule> BuildModule(ShaderCompiler& context,
    std::span<const u32> spirv, std::string_view name) {
    // The core's names carry the guest hash ("<stage>_0x<hash>_<perm>"): the MSL cache file and
    // the logs use it. Modules from the disk cache come without a name.
    u64 hash = 0;
    if (const size_t pos = name.find("0x"); pos != std::string_view::npos) {
        hash = std::strtoull(std::string{name.substr(pos)}.c_str(), nullptr, 16);
    }
    auto module = std::make_unique<ShaderModule>();
    const ShaderStage stage = ReadShaderStage(spirv);
    const bool tessellation_stage = stage == ShaderStage::TessellationControl ||
                                    stage == ShaderStage::TessellationEval ||
                                    stage == ShaderStage::Geometry;
    if (tessellation_stage) {
        // Only compiled as a variant of a tessellated or geometry pipeline.
        module->status = CompileStatus::Ok;
        module->stage = stage;
    } else {
        CompileResult result =
            context.Compile(spirv, hash, name.empty() ? std::string_view{"cached"} : name);
        module->status = result.status;
        module->stage = result.stage;
        module->shader = std::move(result.shader);
    }
    if (module->stage == ShaderStage::Vertex || tessellation_stage) { // (incl. geometry)
        module->spirv.assign(spirv.begin(), spirv.end());
        module->hash = hash;
        module->name = name.empty() ? std::string{"cached"} : std::string{name};
    }
    return module;
}

inline const CompiledShader* RectList(ShaderCompiler& compiler,ShaderModule* module,std::string& error) {
    if (!module || module->stage != ShaderStage::Vertex || module->spirv.empty()) {
        error = "no vertex module";
        return nullptr;
    }
    std::scoped_lock lock{module->rect_mutex};
    if (!module->rect_tried) {
        module->rect_tried = true;
        const RectListRewrite rewrite = RewriteRectListVertex(module->spirv);
        if (!rewrite.ok) {
            module->rect_error = rewrite.error;
        } else {
            CompileResult result = compiler.Compile(rewrite.spirv, module->hash ^ RectListHashSalt,
                                                    module->name + "_rect");
            if (result.shader) {
                module->rect_shader = std::move(result.shader);
            } else {
                module->rect_error = "the rewritten shader did not compile: " + result.error;
            }
        }
    }
    error = module->rect_error;
    return module->rect_shader.get();
}

inline const CompiledShader* Variant(ShaderCompiler& compiler,ShaderModule& module,
                                                   const CompileVariant& variant,
                                                   std::string& error) {
    std::scoped_lock lock{module.variant_mutex};
    for (const ShaderModule::Variant& known : module.variants) {
        if (known.variant == variant) {
            error = known.error;
            return known.shader.get();
        }
    }
    ShaderModule::Variant& made = module.variants.emplace_back();
    made.variant = variant;
    if (module.spirv.empty()) {
        made.error = "no SPIR-V kept for the module";
    } else {
        const char* suffix = variant.kind == CompileVariant::Kind::TessVertex    ? "_tessvs"
                             : variant.kind == CompileVariant::Kind::TessControl ? "_tesstcs"
                             : variant.kind == CompileVariant::Kind::GeometryKernel ? "_gskernel"
                                                                                 : "_tesstes";
        CompileResult result =
            compiler.Compile(module.spirv, module.hash, module.name + suffix, variant);
        if (result.shader) {
            made.shader = std::move(result.shader);
        } else {
            made.error = result.error;
        }
    }
    error = made.error;
    return made.shader.get();
}

inline bool Tessellation(ShaderCompiler& compiler,
    std::span<ShaderModule* const, VideoCore::MaxShaderStages> modules,
    TessellationInput& input, std::string& error, bool& unsupported) {
    using Shader::SwStage;
    unsupported = false;
    ShaderModule* vs = modules[u32(SwStage::Vertex)];
    ShaderModule* tcs = modules[u32(SwStage::TessellationControl)];
    ShaderModule* tes = modules[u32(SwStage::TessellationEval)];
    if (!vs || vs->stage != ShaderStage::Vertex || !tcs ||
        tcs->stage != ShaderStage::TessellationControl || !tes ||
        tes->stage != ShaderStage::TessellationEval) {
        error = "a stage's module is missing or of another stage";
        return false;
    }
    const TessellationInfo tes_info = ReadTessellationInfo(tes->spirv);
    const TessellationInfo tcs_info = ReadTessellationInfo(tcs->spirv);
    if (tes_info.domain != TessDomain::Triangles && tes_info.domain != TessDomain::Quads) {
        error = "Metal tessellates triangles and quads only (the evaluation shader's domain)";
        unsupported = true;
        return false;
    }
    if (tes_info.point_mode) {
        error = "Metal's tessellator has no point mode";
        unsupported = true;
        return false;
    }
    if (tcs_info.output_vertices == 0) {
        error = "the control shader declares no OutputVertices";
        return false;
    }
    for (u32 index_type = 0; index_type < 3; ++index_type) {
        input.vertex[index_type] = Variant(compiler,
            *vs, {.kind = CompileVariant::Kind::TessVertex, .index_type = index_type}, error);
        if (!input.vertex[index_type]) {
            error = "vertex kernel: " + error;
            return false;
        }
    }
    input.control = Variant(compiler,
        *tcs, {.kind = CompileVariant::Kind::TessControl, .domain = tes_info.domain}, error);
    if (!input.control) {
        error = "control kernel: " + error;
        return false;
    }
    if (!input.control->tess_layout.patch_output_complete) {
        error = "control kernel: incomplete per-patch output reflection";
        return false;
    }
    input.eval = Variant(compiler,
        *tes,
        {.kind = CompileVariant::Kind::TessEval,
         .output_vertices = tcs_info.output_vertices,
         .link_patch_input = true,
         .patch_input = {.stride = input.control->tess_layout.patch_output,
                         .members = input.control->tess_layout.patch_members}},
        error);
    if (!input.eval) {
        error = "evaluation function: " + error;
        return false;
    }
    return true;
}

} // namespace Metal::CachedVariants
