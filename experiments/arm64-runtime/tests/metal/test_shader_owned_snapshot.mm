// SPDX-License-Identifier: GPL-2.0-or-later
// Authored inputs only. Parent agent builds/runs serially; this source is not a game fixture.
#import <Metal/Metal.h>
#include <algorithm>
#include <array>
#include <cstdlib>
#include <cassert>
#include <cstring>
#include <filesystem>
#include <future>
#include "shader_recompiler/compile_snapshot.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/pipeline_cache_core.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "test_gpu_runtime_stubs.cpp"

namespace {
template<class F> void rejects(F&& fn) {
    bool caught = false; try { fn(); } catch (const std::runtime_error&) { caught = true; }
    assert(caught);
}
struct Source {
    std::array<u32,16> ud{};
    std::vector<u32> code{0xBEEB03FF, 0, 0xBF810000};
    Shader::Profile profile{};
    Shader::RuntimeInfo runtime{};
    Shader::Info info;
    Shader::Backend::Bindings bindings{};
    Source() {
        runtime.Initialize(Shader::HwStage::Compute, Shader::SwStage::Compute);
        runtime.hw.cs.workgroup_size = {1,1,1};
        info = Shader::Info{Shader::HwStage::Compute, Shader::SwStage::Compute, Params()};
        info.RefreshFlatBuf();
    }
    Shader::ShaderParams Params() { return {std::span<const u32,16>{ud}, code, 0x13579bdf}; }
    std::optional<Shader::CompileSnapshot> Capture(
        const std::optional<Shader::Gcn::FetchShaderData>& fetch = {}) {
        return Shader::CompileSnapshot::Capture(info, Params(), runtime, profile, {}, fetch);
    }
};
}
int main(int argc,char**argv) {
    assert(argc == 2);
    Source source;
    // Get a real translated reference and its exact SRT layout through the ordinary path.
    Shader::CompileContext reference_context;
    auto reference_info = source.info;
    auto reference_runtime = source.runtime;
    auto ir = Shader::TranslateProgram(source.code, reference_context.pools, reference_info,
                                       reference_runtime, source.profile);
    Shader::Backend::Bindings binding{};
    auto expected = Shader::Backend::SPIRV::EmitSPIRV(source.profile, reference_runtime, ir, binding);
    source.info = reference_info;
    auto capture = source.Capture(); assert(capture);
    auto owned = std::make_shared<const Shader::CompileSnapshot>(std::move(*capture));
    assert(owned->SizeBytes() <= Shader::CompileSnapshot::MaxBytes);
    const auto old_base = owned->program_base;
    // Poison all source memory before entering the independent compiler context.
    source.ud.fill(0xffffffff); std::fill(source.code.begin(), source.code.end(), 0xffffffff);
    source.info.flattened_ud_buf.assign(16, 0xffffffff);
    auto worker = std::async(std::launch::async, [owned] {
        Shader::CompileContext context; return Shader::CompileOwnedToSpirv(owned, context);
    });
    auto compiled = worker.get();
    assert(compiled.spirv == expected && compiled.info.ProgramBase() == old_base);
    assert(compiled.inputs == owned && compiled.info.compile_snapshot == owned.get());
    assert(compiled.info.ReadUdReg<u32>(Shader::IR::NumScalarRegs, 0) == 0);
    rejects([&] { compiled.info.ReadUdReg<u32>(0, 0); });
    rejects([&] { compiled.info.ReadUdReg<u32>(Shader::IR::NumScalarRegs, 16); });
    rejects([&] { owned->ValidateSrt(std::array<u32,3>{1,2,3}, owned->flat_dwords); });
    rejects([&] { owned->ValidateSrt(owned->walker, owned->flat_dwords + 1); });
    auto bad_info = owned->MakeInfo();
    bad_info.srt_info.flattened_bufsize_dw += 1;
    rejects([&] { bad_info.RefreshFlatBuf(); }); // never silently resize a captured layout

    // Fetch data and V# reads are structured owned inputs, never pointers into the original UD.
    Source vertex;
    vertex.info.hw_stage = Shader::HwStage::Vertex;
    vertex.info.sw_stage = Shader::SwStage::Vertex;
    vertex.runtime.Initialize(Shader::HwStage::Vertex, Shader::SwStage::Vertex);
    vertex.info.has_fetch_shader = true;
    AmdGpu::Buffer sharp{}; sharp.base_address = 1234; sharp.stride = 16;
    const auto pointer = reinterpret_cast<uintptr_t>(&sharp);
    std::memcpy(vertex.ud.data(), &pointer, sizeof(pointer));
    vertex.info.RefreshFlatBuf();
    Shader::Gcn::FetchShaderData fetch;
    Shader::Gcn::VertexAttribute attribute{};
    attribute.sgpr_base = 0; attribute.dword_offset = 0; fetch.attributes.push_back(attribute);
    auto vc = vertex.Capture(fetch); assert(vc);
    vertex.ud.fill(0xffffffff); sharp.base_address = 9999;
    auto vi = vc->MakeInfo();
    auto parsed = Shader::Gcn::ParseFetchShader(vi);
    assert(parsed && parsed->attributes.size() == 1);
    assert(parsed->attributes[0].GetSharp(vi).base_address == 1234);
    rejects([&] { Shader::Gcn::GetFetchShaderCode(vi, 0); });

    Source geometry;
    geometry.info.hw_stage = Shader::HwStage::Geometry;
    geometry.info.sw_stage = Shader::SwStage::Geometry;
    geometry.runtime.Initialize(Shader::HwStage::Geometry, Shader::SwStage::Geometry);
    std::array<u32,3> copy{11,22,33}; geometry.runtime.hw.gs.vs_copy = copy;
    auto gc = geometry.Capture(); assert(gc && gc->runtime.hw.gs.vs_copy.empty());
    copy.fill(99); auto gr = gc->RebindRuntime();
    assert(gr.hw.gs.vs_copy[0] == 11 && gr.hw.gs.vs_copy.data() == gc->gs_copy.data());

    Source tess;
    tess.info.hw_stage = Shader::HwStage::Hull;
    tess.info.sw_stage = Shader::SwStage::TessellationControl;
    tess.runtime.Initialize(Shader::HwStage::Hull, Shader::SwStage::TessellationControl);
    tess.info.tess_consts_ptr_base = Shader::IR::ScalarReg{0};
    tess.info.tess_consts_dword_offset = 0;
    Shader::TessellationDataConstantBuffer constants{}; constants.ls_stride = 321;
    AmdGpu::Buffer tess_buffer{};
    tess_buffer.base_address = reinterpret_cast<uintptr_t>(&constants);
    const auto tess_pointer = reinterpret_cast<uintptr_t>(&tess_buffer);
    std::memcpy(tess.ud.data(), &tess_pointer, sizeof(tess_pointer)); tess.info.RefreshFlatBuf();
    auto tc = tess.Capture(); assert(tc);
    tess.ud.fill(0xffffffff); constants.ls_stride = 987;
    auto ti = tc->MakeInfo(); Shader::TessellationDataConstantBuffer captured_constants{};
    ti.ReadTessConstantBuffer(captured_constants); assert(captured_constants.ls_stride == 321);
    rejects([&] { compiled.info.ReadTessConstantBuffer(captured_constants); });

    Source too_large; too_large.code.resize(Shader::CompileSnapshot::MaxCodeBytes/4 + 1);
    too_large.info.pgm_base = too_large.Params().Base(); assert(!too_large.Capture());
    Source motion; motion.info.hw_stage = Shader::HwStage::Vertex;
    motion.info.sw_stage = Shader::SwStage::Vertex;
    motion.runtime.Initialize(Shader::HwStage::Vertex, Shader::SwStage::Vertex);
    motion.runtime.hw.vs.motion_vectors = true; assert(!motion.Capture());

    // Same emitted SPIR-V must compile through an independent real Metal compiler. Cache is
    // read-only: a rejected speculative module must not create/delete MSL files.
    @autoreleasepool {
        Metal::Device device;
        const std::filesystem::path cache = std::filesystem::path{argv[1]}/"owned-read-only";
        Metal::ShaderCompiler backend{device, cache, Metal::FloatRules::Strict, false};
        auto module = backend.Compile(compiled.spirv, owned->hash, "compute_0x13579bdf_owned");
        assert(module.shader && module.shader->library && module.shader->function);
        assert(backend.GetCounters().cache_stores == 0);
        assert(!std::filesystem::exists(cache));
        // Exercise the exact core->detached Metal backend hook. WarmUp is disabled before
        // constructing the cache; no preexisting game cache is opened by this authored test.
        setenv("BB_PIPELINE_CACHE", "0", 1);
        AmdGpu::Liverpool liverpool;
        Source backend_source;
        backend_source.profile = Metal::MakeShaderProfile(device, VideoCore::BackendLimits{});
        VideoCore::PipelineCacheCore* base = nullptr;
        Metal::PipelineCache pipeline_cache{device, VideoCore::BackendLimits{}, &liverpool, 16,
                                           std::filesystem::path{argv[1]}/"detached-backend"};
        base = &pipeline_cache;
        backend_source.profile = base->GetProfile();
        auto bc = backend_source.Capture(); assert(bc);
        auto bi = std::make_shared<const Shader::CompileSnapshot>(std::move(*bc));
        Shader::CompileContext independent;
        const auto before = pipeline_cache.GetCounters().modules.load();
        auto detached = base->PrepareOwnedShader(bi, independent);
        assert(detached && detached->module && !detached->shader.spirv.empty());
        assert(pipeline_cache.GetCounters().modules.load() == before);
        detached.reset(); // rejected/unneeded output is fully RAII-owned, no map entry to clean
        assert(pipeline_cache.GetCounters().modules.load() == before);
    }
    puts("PASS owned GCN/SPIR-V equivalence, poisoned sources, fetch/V#, GS/tess, budgets, rejects and read-only real Metal");
}
