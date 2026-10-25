#include "runtime_exit_codes.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#if defined(__aarch64__)
#define BB_MTL_HOST_NAMESPACE(name) name "-arm64-srt1"
#else
#define BB_MTL_HOST_NAMESPACE(name) name
#endif
// SPDX-License-Identifier: GPL-2.0-or-later
// Metal::PipelineCache, the Metal half of VideoCore::PipelineCacheCore (plan P4.3a). Counterpart of
// renderer_vulkan/vk_pipeline_cache.cpp: the hooks make shader modules and pipelines, everything
// else (keys, programs, permutations, SPIR-V, the disk cache) is the core's.

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <concepts>

#include "common/assert.h"
#include "common/logging/log.h"
#include "common/serdes.h"
#include "common/scope_exit.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "video_core/renderer_metal/mtl_cached_variants.h"
#include "video_core/renderer_metal/mtl_spirv_geometry.h"
#include "video_core/renderer_metal/mtl_spirv_rectlist.h"
#include "video_core/renderer_metal/mtl_vertex_metadata.h"

namespace Metal {
namespace {
using VideoCore::ShaderModuleHandle;

constexpr u32 SpirvVersion1_6 = 0x00010600U;
/// Xored into the guest hash of a vertex module's RectList variant (the log label and the name of
/// its MSL cache file; the file name also holds the hash of the rewritten SPIR-V).

/// The viewport limit of every Metal GPU the backend supports (Apple family 7+, Mac2).
constexpr u32 MaxViewportDimension = 16384;

ShaderModuleHandle ToHandle(ShaderModule* module) {
    return ShaderModuleHandle{reinterpret_cast<u64>(module)};
}

ShaderModule* ToModule(ShaderModuleHandle handle) {
    return reinterpret_cast<ShaderModule*>(handle.value);
}

/// The module of a stage when its shader is usable and of the stage's kind, else null.
const CompiledShader* UsableShader(ShaderModuleHandle handle, ShaderStage stage) {
    const ShaderModule* module = handle ? ToModule(handle) : nullptr;
    return module && module->shader && module->shader->stage == stage ? module->shader.get()
                                                                      : nullptr;
}
} // namespace

Shader::Profile MakeShaderProfile(Device& device, const VideoCore::BackendLimits& limits) {
    id<MTLDevice> mtl = device.GetImpl().device;
    // Every field has the value the Vulkan backend's MakeProfile computed on MoltenVK (read from
    // the profile.bin of a MoltenVK session's pipeline cache), so the recompiler emits the SPIR-V
    // the Metal shader tests (P3.1) compiled. Where that value came from a Vulkan feature query,
    // the column says what the Metal path supports instead.
    return Shader::Profile{
        // Metal viewport limit (16384, as MoltenVK's maxViewportDimensions).
        .max_viewport_width = limits.max_viewport_width ? limits.max_viewport_width
                                                         : MaxViewportDimension,
        .max_viewport_height = limits.max_viewport_height ? limits.max_viewport_height
                                                          : MaxViewportDimension,
        // maxThreadgroupMemoryLength (32 KiB on Apple GPUs; MoltenVK reported the same).
        .max_shared_memory_size = static_cast<u32>(mtl.maxThreadgroupMemoryLength),
        .supported_spirv = SpirvVersion1_6, // the constant the Vulkan backend passes
        // Apple GPUs' SIMD width (threadExecutionWidth, 32), MoltenVK's subgroupSize.
        .subgroup_size = 32,
        // MSL has char, short, long and half; no double.
        .support_int8 = true,
        .support_int16 = true,
        .support_int64 = true,
        .support_float16 = true,
        .support_float64 = false,
        // MSL has no per-shader float controls (MoltenVK: shaderDenorm*/shaderRounding* all 0,
        // independence none).
        .supports_denorm_behavior_independence = false,
        .supports_rounding_mode_independence = false,
        .support_fp16_denorm_preserve = false,
        .support_fp16_denorm_flush = false,
        .support_fp16_round_to_zero = false,
        .support_fp32_denorm_preserve = false,
        .support_fp32_denorm_flush = false,
        .support_fp32_round_to_zero = false,
        .support_fp64_denorm_preserve = false,
        .support_fp64_denorm_flush = false,
        .support_fp64_round_to_zero = false,
        // MoltenVK: fp16/fp32 true, fp64 false (as above, no double).
        .support_fp16_signed_zero_inf_nan_preserve = true,
        .support_fp32_signed_zero_inf_nan_preserve = true,
        .support_fp64_signed_zero_inf_nan_preserve = false,
        // MoltenVK: true (textures read/write with a level in MSL).
        .supports_image_load_store_lod = true,
        // VK_AMD_gcn_shader and the atomics/layout extensions MoltenVK lacks: false.
        .supports_native_cube_calc = false,
        // MoltenVK: true (the P3.1 shader tests compiled the SPIR-V emitted with it).
        .supports_trinary_minmax = true,
        .supports_buffer_fp32_atomic_min_max = false,
        .supports_image_fp32_atomic_min_max = false,
        .supports_buffer_int64_atomics = false,
        .supports_shared_int64_atomics = false,
        .supports_workgroup_explicit_memory_layout = false,
        .supports_amd_shader_explicit_vertex_parameter = false,
        // MoltenVK: true. The recompiler emits PerVertexKHR inputs, which the backend rewrites
        // to ordinary inputs and barycentric arithmetic (mtl_spirv_pervertex, P3.1b).
        .supports_fragment_shader_barycentric = true,
        .supports_shader_subgroup_clock = false,
        // Vulkan derives these from the driver id (NVIDIA, KosmicKrisp) and env overrides: all
        // false on MoltenVK. needs_buffer_offsets is storageMinAlignment > 4 (Metal::Runtime:
        // 16), true as on MoltenVK.
        .needs_manual_interpolation = false,
        .needs_integer_interpolation_fix = false,
        .needs_lds_barriers = false,
        .needs_buffer_offsets = limits.storage_min_alignment > 4,
        .needs_unorm_fixup = false,
        .needs_clip_distance_emulation = false,
        // MoltenVK: true (MSL [[stencil]] output).
        .supports_shader_stencil_export = true,
        // P9.1: V1 and V2 emit game fragment/compute shaders without NoContraction; part of the
        // profile, so the disk cache of those rules never meets a strict one.
        .relaxed_fragment_compute_float = ReadFloatRules() != FloatRules::Strict,
        .two_level_bda = limits.two_level_bda,
    };
}

VideoCore::PipelineCapabilities MakePipelineCapabilities() {
    return {
        // Metal's clip space z is [0, w] only: the recompiler emulates [-w, w] in the vertex
        // shader (depth_clip_enable itself is MTLDepthClipMode, set per draw).
        .depth_clip_control = false,
        // The vertex descriptor is part of the render pipeline state: pipelines depend on the
        // vertex formats (the key holds them).
        .vertex_input_dynamic_state = false,
        // Geometry draws: the export shader and the geometry shader run as compute kernels, the
        // strips they write are drawn by a pass-through vertex function (mtl_geometry.h, P7.1).
        .geometry_stage = true,
        // Tessellated pipelines: the vertex and control shaders run as compute kernels, the
        // evaluation shader is the post-tessellation vertex function (mtl_tessellation.h, P4.4).
        .tessellation = true,
        // One sample count for all attachments of a Metal render pass.
        .mixed_any_samples = false,
        .mixed_depth_samples = false,
    };
}

GraphicsPipeline::GraphicsPipeline(const VideoCore::GraphicsPipelineKey& key_,
                                   std::span<const Shader::Info*, VideoCore::MaxShaderStages> infos,
                                   std::optional<const Shader::Gcn::FetchShaderData> fetch_shader_)
    : PipelineBase{key_, std::move(fetch_shader_)} {
    std::ranges::copy(infos, stages.begin());
}

ComputeShaderPipeline::ComputeShaderPipeline(const Shader::Info& info,
                                             const ComputePipeline* compute_)
    : PipelineBase{true}, compute{compute_} {
    stages[u32(Shader::SwStage::Compute)] = &info;
}

void GraphicsSerialization::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer writer{ar};
    writer.Write(static_cast<u32>(vertex_attributes.size()));
    for (const VertexAttribute& attribute : vertex_attributes) {
        writer.Write(attribute.location);
        writer.Write(static_cast<u32>(attribute.per_instance));
        writer.Write(attribute.divisor);
    }
}

bool GraphicsSerialization::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader reader{ar};
    u32 count{};
    reader.Read(count);
    if (count > VideoCore::MaxVertexBufferCount) {
        throw Serialization::CorruptData{"too many vertex attributes in the pipeline cache"};
    }
    vertex_attributes.resize(count);
    for (VertexAttribute& attribute : vertex_attributes) {
        u32 per_instance{};
        reader.Read(attribute.location);
        reader.Read(per_instance);
        reader.Read(attribute.divisor);
        attribute.per_instance = per_instance != 0;
    }
    return true;
}

PipelineCache::PipelineCache(Device& device_, const VideoCore::BackendLimits& limits,
                             AmdGpu::Liverpool* liverpool_, u32 sparse_page_shift,
                             std::filesystem::path msl_cache_dir, Runtime* host_runtime_)
    : PipelineCacheCore{liverpool_, MakeShaderProfile(device_, limits), MakePipelineCapabilities(),
                        sparse_page_shift,
                        // 1D lowering changes SPIR-V image types. Preserve old core archives
                        // in their namespace; content-hashed MSL remains reusable.
                        // BDA V1 and V2 share SPIR-V; V0 remains separate.
                        limits.two_level_bda
                            ? (ReadFloatRules()==FloatRules::Strict?BB_MTL_HOST_NAMESPACE("metal-pt2-img2d-v2"):BB_MTL_HOST_NAMESPACE("metal-relaxed-pt2-img2d-v2"))
                            : (ReadFloatRules()==FloatRules::Strict?BB_MTL_HOST_NAMESPACE("metal-flat2-img2d-v2"):BB_MTL_HOST_NAMESPACE("metal-relaxed-flat2-img2d-v2"))},
      host_runtime{host_runtime_}, device{device_}, compiler{device_, msl_cache_dir, ReadFloatRules()},
      pso_cache{device_, msl_cache_dir, ReadFloatRules() == FloatRules::RelaxedMath},
      pass_through{device_} {
    std::printf("GPU: Metal float rules %s\n", FloatRulesName(ReadFloatRules()));
    bool host_bound=false;
    if(host_runtime){host_runtime->BindHostPsoCache(&pso_cache);host_bound=true;}
    bool construction_complete=false;
    SCOPE_EXIT{if(host_bound&&!construction_complete)host_runtime->BindHostPsoCache(nullptr);};
    WarmUp();
    FinishMainPreparation();
    if(host_runtime){const auto result=host_runtime->FinishHostPreparation();if(result.state==HostPso::WarmState::Critical){std::fputs("Metal preparation stopped: critical host pressure\n",stderr);std::_Exit(BB_ARM_EXIT_HOST_PROTECTION);}}
    // Test/benchmark shutdown can bypass destructors. Persist already known prewarmed PSOs
    // before gameplay, so the next start can actually use its binary archive.
    // No cache capacity/resource change and no periodic serialization in the render loop.
    // Loading serialized PSOs has a material CPU-memory cost on this 16 GiB machine.
    // Keep disk persistence opt-in; ordinary SPIR-V/MSL/keys and live PSOs stay cached.
    construction_complete=true;
}

void PipelineCache::FinishMainPreparation(){
 if(main_preparation_finished)return;
 if(host_runtime&&host_runtime->HostPreparationCritical()){std::fputs("Metal preparation stopped: critical host pressure before main archive\n",stderr);std::_Exit(BB_ARM_EXIT_HOST_PROTECTION);}
 const char*persist=std::getenv("BB_MTL_PERSIST_WARM_ARCHIVE");if(persist&&persist[0]=='1')pso_cache.Flush();
 const char*release=std::getenv("BB_MTL_RELEASE_WARM_ARCHIVE");if(!release||release[0]!='0')pso_cache.ReleaseWarmArchive();
 main_preparation_finished=true;
}
void PipelineCache::WarmExtraPipelines(){
 FinishMainPreparation();
 if(host_runtime){const auto result=host_runtime->WarmHostPipelines();if(result.state==HostPso::WarmState::Critical){std::fputs("Metal preparation stopped: critical host pressure\n",stderr);std::_Exit(BB_ARM_EXIT_HOST_PROTECTION);}}
}

PipelineCache::~PipelineCache() {
    if(host_runtime)host_runtime->BindHostPsoCache(nullptr);
    // The pipelines refer to the modules' shaders and the PSO cache: stop the core first.
    DestroyPipelines();
}

ShaderModuleHandle PipelineCache::CreateShaderModule(std::span<const u32> spirv,
                                                     std::string_view name, bool from_cache) {
    return InsertShaderModule(BuildShaderModule(compiler, spirv, name));
}

namespace {
struct OwnedMetalShader final : VideoCore::PreparedShaderModule {
    std::unique_ptr<ShaderModule> module;
};
}

std::unique_ptr<VideoCore::PreparedShaderModule> PipelineCache::BuildOwnedShaderModule(
    std::span<const u32> spirv, std::string_view name) {
    // These diagnostics reserve session-global slots or create source references. They need
    // explicit ownership too; never consume their budget in an unadopted speculative job.
    if (std::getenv("BB_MTL_VERTEX_MATH_FILE") || std::getenv("BB_CAPTURE_COPY_POINTERS")) return {};
    // Independent frontend/backend compiler context. The job owns all IR, SPIRV-Cross and
    // returned Metal resources. No modules_ entry or live PSO/cache identity is published here.
    ShaderCompiler context{device, compiler.CacheDir(), ReadFloatRules(), false};
    auto result = std::make_unique<OwnedMetalShader>();
    @autoreleasepool {
        result->module = BuildShaderModule(context, spirv, name);
    }
    if (!result->module->shader) return {};
    return result;
}

ShaderModuleHandle PipelineCache::AdoptOwnedShaderModule(
    std::unique_ptr<VideoCore::PreparedShaderModule> prepared) {
    auto* owned = dynamic_cast<OwnedMetalShader*>(prepared.get());
    if (!owned || !owned->module) return {};
    return InsertShaderModule(std::move(owned->module));
}

std::unique_ptr<ShaderModule> PipelineCache::BuildShaderModule(ShaderCompiler& context,
    std::span<const u32> spirv,std::string_view name) {
    return CachedVariants::BuildModule(context,spirv,name);
}

ShaderModuleHandle PipelineCache::InsertShaderModule(std::unique_ptr<ShaderModule> module) {
    const bool tessellation_stage = module->stage == ShaderStage::TessellationControl ||
        module->stage == ShaderStage::TessellationEval || module->stage == ShaderStage::Geometry;
    ++counters.modules;
    if (!module->shader && !tessellation_stage) {
        // A cached shader that does not compile is no damaged entry, and a null handle from the
        // disk cache would make WarmUp drop the whole cache: the module stays, unusable.
        ++counters.unusable_modules;
    }
    ShaderModule* raw = module.get();
    std::scoped_lock lock{modules_mutex};
    modules_.emplace(raw, std::move(module));
    return ToHandle(raw);
}

void PipelineCache::DestroyShaderModule(ShaderModuleHandle handle) {
    std::scoped_lock lock{modules_mutex};
    const auto it = modules_.find(ToModule(handle));
    if (it == modules_.end()) {
        return;
    }
    if (it->second->shader) {
        retired_shaders.push_back(std::move(it->second->shader));
    }
    {
        std::scoped_lock rect_lock{it->second->rect_mutex};
        if (it->second->rect_shader) {
            retired_shaders.push_back(std::move(it->second->rect_shader));
        }
    }
    {
        std::scoped_lock variant_lock{it->second->variant_mutex};
        for (ShaderModule::Variant& variant : it->second->variants) {
            if (variant.shader) {
                retired_shaders.push_back(std::move(variant.shader));
            }
        }
    }
    modules_.erase(it);
}

const CompiledShader* PipelineCache::RectListShader(ShaderModuleHandle handle,std::string& error) {
    return CachedVariants::RectList(compiler,handle?ToModule(handle):nullptr,error);
}
const CompiledShader* PipelineCache::VariantShader(ShaderModule& module,const CompileVariant& variant,
                                                  std::string& error) {
    return CachedVariants::Variant(compiler,module,variant,error);
}
bool PipelineCache::TessellationShaders(
    std::span<const ShaderModuleHandle,VideoCore::MaxShaderStages> modules,TessellationInput& input,
    std::string& error,bool& unsupported) {
    std::array<ShaderModule*,VideoCore::MaxShaderStages> native{};
    for(size_t i=0;i<native.size();++i)native[i]=modules[i]?ToModule(modules[i]):nullptr;
    return CachedVariants::Tessellation(compiler,native,input,error,unsupported);
}

bool PipelineCache::GeometryShaders(
    const VideoCore::GraphicsPipelineKey& key,
    std::span<const ShaderModuleHandle, VideoCore::MaxShaderStages> modules,
    const CompiledShader* fs, GeometryPipelineInput& input, std::string& error,
    bool& unsupported) {
    using Shader::SwStage;
    unsupported = false;
    ShaderModule* es = modules[u32(SwStage::Vertex)] ? ToModule(modules[u32(SwStage::Vertex)]) : nullptr;
    ShaderModule* gs = modules[u32(SwStage::Geometry)] ? ToModule(modules[u32(SwStage::Geometry)])
                                                       : nullptr;
    if (!es || es->stage != ShaderStage::Vertex || !gs || gs->stage != ShaderStage::Geometry) {
        error = "a stage's module is missing or of another stage";
        return false;
    }
    // The shape first: the shaders the first version cannot handle are counted, not failures.
    const GeometryRewrite analysis = AnalyzeGeometryShader(gs->spirv);
    if (!analysis.ok) {
        error = analysis.error;
        unsupported = true;
        return false;
    }
    const AmdGpu::PrimitiveType prim = key.prim_type;
    const bool matches = (analysis.info.input == GsInputPrimitive::Points &&
                          prim == AmdGpu::PrimitiveType::PointList) ||
                         (analysis.info.input == GsInputPrimitive::Lines &&
                          prim == AmdGpu::PrimitiveType::LineList) ||
                         (analysis.info.input == GsInputPrimitive::Triangles &&
                          prim == AmdGpu::PrimitiveType::TriangleList);
    if (!matches) {
        error = "the draw's primitive type is not a list of the geometry shader's input primitive";
        unsupported = true;
        return false;
    }
    for (u32 index_type = 0; index_type < 3; ++index_type) {
        input.es[index_type] = VariantShader(
            *es, {.kind = CompileVariant::Kind::TessVertex, .index_type = index_type}, error);
        if (!input.es[index_type]) {
            error = "export kernel: " + error;
            return false;
        }
    }
    CompileVariant variant{.kind = CompileVariant::Kind::GeometryKernel};
    variant.es.stride = input.es[0]->tess_layout.output;
    variant.es.members = input.es[0]->tess_layout.members;
    input.gs = VariantShader(*gs, variant, error);
    if (!input.gs) {
        error = "geometry kernel: " + error;
        return false;
    }
    const bool clip_minus_w_to_w = key.clip_space == AmdGpu::ClipSpace::MinusWToW;
    std::span<const StageInput> fs_inputs;
    if (fs) {
        fs_inputs = fs->stage_inputs;
    }
    input.pass_through = pass_through.Get(input.gs->geometry, fs_inputs, clip_minus_w_to_w, error);
    if (!input.pass_through) {
        error = "pass-through function: " + error;
        return false;
    }
    return true;
}

std::unique_ptr<VideoCore::PipelineSerialization> PipelineCache::NewGraphicsSerialization() {
    return std::make_unique<GraphicsSerialization>();
}

std::unique_ptr<VideoCore::PipelineSerialization> PipelineCache::NewComputeSerialization() {
    return std::make_unique<ComputeSerialization>();
}

void PipelineCache::NoteSkipped(const VideoCore::GraphicsPipelineKey& key,
                                std::atomic<u64>& counter, const char* reason) {
    ++counter;
    const size_t hash = std::hash<VideoCore::GraphicsPipelineKey>{}(key);
    bool first;
    {
        std::scoped_lock lock{skipped_mutex};
        first = skipped_keys.insert(hash).second;
    }
    if (first) {
        LOG_WARNING(Render, "Metal: graphics pipeline {:#x} (vs {:#x} fs {:#x}) not created: {}; "
                            "its draws are dropped",
                    hash, key.stage_hashes[u32(Shader::SwStage::Vertex)],
                    key.stage_hashes[u32(Shader::SwStage::Fragment)], reason);
    }
}

std::unique_ptr<VideoCore::PipelineBase> PipelineCache::CreateGraphicsPipeline(
    const VideoCore::GraphicsPipelineKey& key,
    std::span<const Shader::Info*, VideoCore::MaxShaderStages> infos,
    std::span<const Shader::RuntimeInfo, VideoCore::MaxShaderStages> runtime_infos,
    std::optional<const Shader::Gcn::FetchShaderData> fetch_shader,
    std::span<const ShaderModuleHandle, VideoCore::MaxShaderStages> modules,
    VideoCore::PipelineSerialization& sdata, bool preloading) {
    using Shader::SwStage;
    constexpr u32 VS = u32(SwStage::Vertex), FS = u32(SwStage::Fragment);
    const bool has_tcs = key.stage_hashes[u32(SwStage::TessellationControl)] != 0;
    const bool has_tes = key.stage_hashes[u32(SwStage::TessellationEval)] != 0;
    const bool has_gs = key.stage_hashes[u32(SwStage::Geometry)] != 0;
    if (has_gs && (has_tcs || has_tes)) {
        NoteSkipped(key, counters.unsupported_stage, "a geometry stage with tessellation");
        return nullptr;
    }
    if (has_tcs != has_tes) {
        NoteSkipped(key, counters.unsupported_stage,
                    "a tessellation control or evaluation stage without the other");
        return nullptr;
    }
    // Tessellated draws: the vertex, control and evaluation shaders compile as variants (no plain
    // vertex shader is needed), the draw runs the three stages (mtl_tessellation.h).
    const bool tessellation = has_tcs;
    const bool has_fs = infos[FS] != nullptr;
    // (A geometry draw's export shader compiles as kernels too, its plain form is not needed.)
    const bool variants_only = tessellation || has_gs;
    const CompiledShader* vs =
        infos[VS] && !variants_only ? UsableShader(modules[VS], ShaderStage::Vertex) : nullptr;
    const CompiledShader* fs = has_fs ? UsableShader(modules[FS], ShaderStage::Fragment) : nullptr;
    if ((!vs && !variants_only) || (has_fs && !fs)) {
        NoteSkipped(key, counters.unusable_shader, "a shader did not translate to Metal");
        return nullptr;
    }

    // RectList: a vertex shader that evaluates the three vertices and emits two triangles; the
    // pipeline is then an ordinary triangle list (RenderPipelineInput::rect_list_triangles).
    const bool rect_list = key.prim_type == AmdGpu::PrimitiveType::RectList;
    if (rect_list && !variants_only) {
        std::string error;
        vs = RectListShader(modules[VS], error);
        if (!vs) {
            const std::string reason = "rect list vertex shader not rewritable: " + error;
            NoteSkipped(key, counters.rect_list_unrewritable, reason.c_str());
            return nullptr;
        }
    }

    auto pipeline = std::make_unique<GraphicsPipeline>(key, infos, std::move(fetch_shader));
    auto& serialized = static_cast<GraphicsSerialization&>(sdata);
    if (preloading) {
        pipeline->vertex_attributes = serialized.vertex_attributes;
    } else {
        const auto& vs_info = runtime_infos[VS].sw.vs;
        const auto& fetch = pipeline->GetFetchShader();
        pipeline->vertex_attributes = VertexMetadataFromFetch(
            fetch ? &*fetch : nullptr, vs_info.step_rate_0, vs_info.step_rate_1);
        serialized.vertex_attributes = pipeline->vertex_attributes;
    }

    if (tessellation) {
        if (preloading && serialized.vertex_attributes.empty() &&
            key.vertex_buffer_formats[0] != vk::Format::eUndefined) {
            // An entry of a build before the tessellation path: the pipeline was dropped then
            // and its attributes never recorded. The core rebuilds the cache.
            throw Serialization::CorruptData{
                "a tessellated pipeline recorded without its vertex attributes (cache of a "
                "Metal build before tessellation support)"};
        }
        TessellationInput tess_input;
        tess_input.vertex_attributes = pipeline->vertex_attributes;
        std::string error;
        bool unsupported = false;
        if (!TessellationShaders(modules, tess_input, error, unsupported)) {
            const std::string reason = "tessellation: " + error;
            NoteSkipped(key, unsupported ? counters.tessellation_unsupported
                                         : counters.unusable_shader,
                        reason.c_str());
            return nullptr;
        }
        pipeline->tessellation = pso_cache.GetTessellationPipeline(key, tess_input, fs);
        if (!pipeline->tessellation) {
            // PsoCache logged the Metal error (once per key).
            ++counters.pso_failures;
            return nullptr;
        }
        pipeline->render = pipeline->tessellation->render;
        ++counters.pipelines;
        return pipeline;
    }

    if (has_gs) {
        if (preloading && serialized.vertex_attributes.empty() &&
            key.vertex_buffer_formats[0] != vk::Format::eUndefined) {
            throw Serialization::CorruptData{
                "a geometry pipeline recorded without its vertex attributes"};
        }
        GeometryPipelineInput geometry_input;
        geometry_input.vertex_attributes = pipeline->vertex_attributes;
        std::string error;
        bool unsupported = false;
        if (!GeometryShaders(key, modules, fs, geometry_input, error, unsupported)) {
            const std::string reason = "geometry: " + error;
            NoteSkipped(key, unsupported ? counters.geometry_unsupported
                                         : counters.unusable_shader,
                        reason.c_str());
            return nullptr;
        }
        pipeline->geometry = pso_cache.GetGeometryPipeline(key, geometry_input, fs);
        if (!pipeline->geometry) {
            // PsoCache logged the Metal error (once per key).
            ++counters.pso_failures;
            return nullptr;
        }
        pipeline->render = pipeline->geometry->render;
        ++counters.pipelines;
        return pipeline;
    }

    const RenderPipelineInput input{.vertex_attributes = pipeline->vertex_attributes,
                                    .rect_list_triangles = rect_list};
    pipeline->render = pso_cache.GetRenderPipeline(key, vs, fs, input);
    if (!pipeline->render) {
        // PsoCache logged the Metal error (once per key).
        ++counters.pso_failures;
        return nullptr;
    }
    if(vs->strict_vertex_reference && pipeline->render->num_color_attachments>=6) {
        const auto slot=strict_reference_pipelines.fetch_add(1);
        if(slot<32) {
            pipeline->strict_reference=pso_cache.GetRenderPipeline(key,vs->strict_vertex_reference.get(),fs,input);
            std::printf("Metal vertex math PSO: references=%u, available=%d, max=32; selected main color passes, diagnostic only\n",slot+1,int(pipeline->strict_reference!=nullptr));
        } else if(slot==32) {
            std::puts("Metal vertex math PSO: reference capacity reached; unpaired passes retain optimized state");
        }
    }
    ++counters.pipelines;
    return pipeline;
}

std::unique_ptr<VideoCore::PipelineBase> PipelineCache::CreateComputePipeline(
    VideoCore::ComputePipelineKey key, const Shader::Info& info, ShaderModuleHandle module,
    VideoCore::PipelineSerialization& sdata, bool preloading) {
    const CompiledShader* cs = UsableShader(module, ShaderStage::Compute);
    if (!cs) {
        ++counters.unusable_shader;
        LOG_WARNING(Render, "Metal: compute pipeline {:#x} not created: the shader did not "
                            "translate to Metal; its dispatches are dropped",
                    key.value);
        return nullptr;
    }
    const ComputePipeline* compute = pso_cache.GetComputePipeline(key, cs);
    if (!compute) {
        ++counters.pso_failures;
        return nullptr;
    }
    ++counters.pipelines;
    return std::make_unique<ComputeShaderPipeline>(info, compute);
}

} // namespace Metal
