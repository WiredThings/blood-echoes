// SPDX-License-Identifier: GPL-2.0-or-later
// Reproduce malformed resource specialization without a GPU, game, cache, or network.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <csignal>
#include <string_view>
#include <sys/mman.h>
#include <pthread.h>
#include <unistd.h>
#include "bbport_toggles.h"
#include "shader_recompiler/specialization.h"
#include "video_core/pipeline_cache_core.h"
#include "video_core/amdgpu/regs.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"

namespace VideoCore {
struct PipelineCacheCoreTestAccess {
    static bool VertexFormats(PipelineCacheCore& cache, PipelineSelection& sel) {
        return cache.RefreshVertexInputFormats(sel);
    }
    static void CheckEmpty(PipelineCacheCore& cache) {
        assert(cache.program_cache.empty() && cache.graphics_pipelines.empty() &&
               cache.compute_pipelines.empty() && cache.num_new_pipelines == 0);
    }
    static void Seed(PipelineCacheCore& cache, PipelineSelection& sel,
                     const Shader::Info& info, const Shader::ShaderParams& params) {
        auto pgm = std::make_unique<Program>();
        pgm->info = info;
        const auto runtime = cache.BuildRuntimeInfo(sel, Shader::HwStage::Fragment,
                                                   Shader::SwStage::Fragment);
        Shader::StageSpecialization spec(pgm->info, runtime, cache.profile, {});
        pgm->AddPermut(ShaderModuleHandle{42}, std::move(spec));
        pgm->info_template = std::make_unique<Shader::Info>(pgm->info);
        cache.program_cache.emplace(params.hash, std::move(pgm));
    }
    static void CheckUnchanged(PipelineCacheCore& cache, u64 hash) {
        assert(cache.program_cache.size() == 1);
        assert(cache.program_cache.at(hash)->modules.size() == 1);
        assert(cache.graphics_pipelines.empty() && cache.compute_pipelines.empty());
        assert(cache.num_new_pipelines == 0);
    }
};
}

class TestCache final : public VideoCore::PipelineCacheCore {
public:
    explicit TestCache(bool dynamic_vertex = false)
        : PipelineCacheCore(nullptr, {}, {.vertex_input_dynamic_state = dynamic_vertex},
                            16, "descriptor-test") {
        assert(UsesDynamicVertexInputs() == dynamic_vertex);
    }
private:
    VideoCore::ShaderModuleHandle CreateShaderModule(std::span<const u32>, std::string_view, bool) override { std::abort(); }
    void DestroyShaderModule(VideoCore::ShaderModuleHandle) override { std::abort(); }
    std::unique_ptr<VideoCore::PipelineSerialization> NewGraphicsSerialization() override { std::abort(); }
    std::unique_ptr<VideoCore::PipelineSerialization> NewComputeSerialization() override { std::abort(); }
    std::unique_ptr<VideoCore::PipelineBase> CreateGraphicsPipeline(
        const VideoCore::GraphicsPipelineKey&,
        std::span<const Shader::Info*, VideoCore::MaxShaderStages>,
        std::span<const Shader::RuntimeInfo, VideoCore::MaxShaderStages>,
        std::optional<const Shader::Gcn::FetchShaderData>,
        std::span<const VideoCore::ShaderModuleHandle, VideoCore::MaxShaderStages>,
        VideoCore::PipelineSerialization&, bool) override { std::abort(); }
    std::unique_ptr<VideoCore::PipelineBase> CreateComputePipeline(
        VideoCore::ComputePipelineKey, const Shader::Info&, VideoCore::ShaderModuleHandle,
        VideoCore::PipelineSerialization&, bool) override { std::abort(); }
};

static void TestVertexFormats(bool unsupported, bool speculative) {
    using namespace Shader;
    using namespace VideoCore;
    TestCache cache;
    assert(!cache.UsesDynamicVertexInputs());
    Info info{};
    info.sw_stage = SwStage::Vertex;
    info.pgm_hash = 0x135;
    std::array<u32, 16> words{};
    info.user_data = words;
    AmdGpu::Buffer buffer{};
    buffer.base_address = 0x1000;
    buffer.num_records = 16;
    buffer.data_format = unsupported ? 6 : 11;
    buffer.num_format = unsupported ? 5 : 7;
    std::memcpy(words.data(), &buffer, sizeof(buffer));
    PrepWorker worker{};
    PipelineSelection selection{};
    selection.worker = speculative ? &worker : nullptr;
    selection.infos[static_cast<u32>(SwStage::Vertex)] = &info;
    selection.fetch_shader.emplace();
    selection.fetch_shader->attributes.push_back({
        .semantic = 0, .dest_vgpr = 0, .num_elements = 2,
        .sgpr_base = IR::NumScalarRegs, .dword_offset = 0,
        .instance_data = 0, .inst_offset = 0,
    });
    const auto before = selection.graphics_key.vertex_buffer_formats;
    const bool result = PipelineCacheCoreTestAccess::VertexFormats(cache, selection);
    if (unsupported && speculative) {
        assert(!result && worker.failed);
        assert(selection.graphics_key.vertex_buffer_formats == before);
        buffer.data_format = 11;
        buffer.num_format = 7;
        std::memcpy(words.data(), &buffer, sizeof(buffer));
        selection.worker = nullptr;
        assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection));
        assert(selection.graphics_key.vertex_buffer_formats[0] == vk::Format::eR32G32Sfloat);
        worker.failed = false;
        selection.worker = &worker;
        assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection) && !worker.failed);
        std::puts("Malformed speculative vertex format rejected; ordered reread and next worker preserve exact valid format");
    } else {
        assert(result && !worker.failed);
        assert(selection.graphics_key.vertex_buffer_formats[0] == vk::Format::eR32G32Sfloat);
    }
    PipelineCacheCoreTestAccess::CheckEmpty(cache);
    if (speculative) {
        unsigned accepted = 0, rejected = 0;
        // V# fields are four data-format bits and three number-format bits.
        for (u32 d = 0; d < 16; ++d) for (u32 n = 0; n < 8; ++n) {
            buffer.data_format = d;
            buffer.num_format = n;
            std::memcpy(words.data(), &buffer, sizeof(buffer));
            worker.failed = false;
            selection.worker = &worker;
            const auto format = Vulkan::LiverpoolToVK::TrySurfaceFormat(
                buffer.GetDataFmt(), buffer.GetNumberFmt());
            const bool valid = format != vk::Format::eUndefined ||
                               buffer.GetDataFmt() == AmdGpu::DataFormat::FormatInvalid;
            assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection) == valid);
            assert(worker.failed == !valid);
            if (valid) {
                ++accepted;
                const auto worker_format = selection.graphics_key.vertex_buffer_formats[0];
                selection.worker = nullptr;
                assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection));
                assert(worker_format == format && selection.graphics_key.vertex_buffer_formats[0] == format);
            } else {
                ++rejected;
            }
        }
        // Typed fetch instructions override the raw V# format on both paths.
        buffer.data_format = 6;
        buffer.num_format = 5;
        std::memcpy(words.data(), &buffer, sizeof(buffer));
        auto& attribute = selection.fetch_shader->attributes[0];
        attribute.data_format = 11;
        attribute.num_format = 7;
        worker.failed = false;
        selection.worker = &worker;
        assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection) && !worker.failed);
        assert(selection.graphics_key.vertex_buffer_formats[0] == vk::Format::eR32G32Sfloat);
        selection.worker = nullptr;
        assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection));
        assert(selection.graphics_key.vertex_buffer_formats[0] == vk::Format::eR32G32Sfloat);
        PipelineCacheCoreTestAccess::CheckEmpty(cache);
        assert(accepted + rejected == 128 && accepted && rejected);
        std::printf("VERTEX135 accepted=%u rejected=%u; typed override exact; caches unchanged\n", accepted, rejected);
    }
}

static void VertexReadFault(int signal, siginfo_t*, void*) {
    if (runtime_fault_recover) {
        // Match probe.c: sigsetjmp(..., 0) needs explicit signal unblocking.
        auto* recover = runtime_fault_recover;
        runtime_fault_recover = nullptr;
        sigset_t unblock;
        sigemptyset(&unblock);
        sigaddset(&unblock, signal);
        pthread_sigmask(SIG_UNBLOCK, &unblock, nullptr);
        siglongjmp(*recover, 1);
    }
    _exit(99);
}

static void TestVertexReadFault() {
    using namespace Shader;
    using namespace VideoCore;
    const auto size = sysconf(_SC_PAGESIZE);
    void* inaccessible = mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(inaccessible != MAP_FAILED);
    struct sigaction action{}, old_segv{}, old_bus{};
    action.sa_sigaction = VertexReadFault;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    assert(sigaction(SIGSEGV, &action, &old_segv) == 0);
    assert(sigaction(SIGBUS, &action, &old_bus) == 0);
    TestCache cache;
    Info info{};
    std::array<u32, 16> words{};
    std::memcpy(words.data(), &inaccessible, sizeof(inaccessible));
    info.user_data = words;
    PrepWorker worker{};
    PipelineSelection selection{};
    selection.worker = &worker;
    selection.infos[static_cast<u32>(SwStage::Vertex)] = &info;
    selection.fetch_shader.emplace();
    selection.fetch_shader->attributes.push_back({
        .semantic = 0, .dest_vgpr = 0, .num_elements = 2, .sgpr_base = 0,
        .dword_offset = 0, .instance_data = 0, .inst_offset = 0,
    });
    sigjmp_buf outer;
    assert(sigsetjmp(outer, 0) == 0); // the inner scope must recover the fault
    runtime_fault_recover = &outer;
    assert(!PipelineCacheCoreTestAccess::VertexFormats(cache, selection) && worker.failed);
    assert(runtime_fault_recover == &outer);
    sigset_t mask;
    assert(pthread_sigmask(SIG_BLOCK, nullptr, &mask) == 0);
    assert(!sigismember(&mask, SIGSEGV) && !sigismember(&mask, SIGBUS));
    assert(mprotect(inaccessible, size, PROT_READ | PROT_WRITE) == 0);
    AmdGpu::Buffer valid{};
    valid.base_address = 0x1000;
    valid.num_records = 16;
    valid.data_format = 11;
    valid.num_format = 7;
    std::memcpy(inaccessible, &valid, sizeof(valid));
    worker.failed = false;
    assert(PipelineCacheCoreTestAccess::VertexFormats(cache, selection) && !worker.failed);
    assert(runtime_fault_recover == &outer);
    assert(selection.graphics_key.vertex_buffer_formats[0] == vk::Format::eR32G32Sfloat);
    // Dynamic-input pipelines still ignore vertex formats at key construction.
    TestCache dynamic_cache(true);
    valid.data_format = 6;
    valid.num_format = 5;
    std::memcpy(inaccessible, &valid, sizeof(valid));
    const auto before = selection.graphics_key.vertex_buffer_formats;
    assert(PipelineCacheCoreTestAccess::VertexFormats(dynamic_cache, selection) && !worker.failed);
    assert(selection.graphics_key.vertex_buffer_formats == before && runtime_fault_recover == &outer);
    runtime_fault_recover = nullptr;
    PipelineCacheCoreTestAccess::CheckEmpty(cache);
    assert(sigaction(SIGSEGV, &old_segv, nullptr) == 0);
    assert(sigaction(SIGBUS, &old_bus, nullptr) == 0);
    assert(munmap(inaccessible, size) == 0);
    std::puts("VERTEX135 inaccessible guest table rejected; outer recovery restored; caches unchanged");
}

static void TestWorker(bool image) {
    using namespace Shader;
    using namespace VideoCore;
    TestCache cache;
    auto regs = std::make_unique<AmdGpu::Regs>();
    PrepWorker worker{};
    PipelineSelection selection{};
    selection.regs = regs.get();
    selection.worker = &worker;
    std::array<u32,16> words{};
    Info info{};
    info.hw_stage = HwStage::Fragment; info.sw_stage = SwStage::Fragment;
    info.pgm_hash = 0x1234; info.user_data = words;
    info.srt_info.flattened_bufsize_dw = words.size();
    if (image) {
        auto sharp=AmdGpu::Image::Null(false); sharp.base_address=1; sharp.data_format=2; sharp.num_format=6;
        std::memcpy(words.data(),&sharp,sizeof(sharp));
        ImageResource resource{}; resource.sharp_fetch.load_mask=0xff;
        for(u32 i=0;i<8;++i) resource.sharp_fetch.offsets[i]=i;
        info.images.push_back(resource);
    } else {
        AmdGpu::Buffer sharp{};sharp.base_address=1;sharp.num_records=16;sharp.data_format=2;sharp.num_format=6;
        std::memcpy(words.data(),&sharp,sizeof(sharp));
        BufferResource resource{};resource.is_formatted=true;resource.sharp_fetch.load_mask=0xf;
        for(u32 i=0;i<4;++i)resource.sharp_fetch.offsets[i]=i;
        info.buffers.push_back(resource);
    }
    const ShaderParams params{words, {}, info.pgm_hash};
    info.RefreshFlatBuf();
    PipelineCacheCoreTestAccess::Seed(cache,selection,info,params);
    const auto set_format=[&](u32 fmt) {
        if(image) { AmdGpu::Image sharp;std::memcpy(&sharp,words.data(),sizeof(sharp));sharp.data_format=fmt;std::memcpy(words.data(),&sharp,sizeof(sharp)); }
        else { AmdGpu::Buffer sharp;std::memcpy(&sharp,words.data(),sizeof(sharp));sharp.data_format=fmt;std::memcpy(words.data(),&sharp,sizeof(sharp)); }
    };
    Backend::Bindings bindings{};
    set_format(15); // guest resource table temporarily contains an unsupported field
    auto result=cache.GetProgram(selection,HwStage::Fragment,SwStage::Fragment,params,bindings);
    assert(worker.failed && std::get<0>(result)==nullptr && std::get<1>(result).value==0);
    assert(worker.stages.empty() && bindings.unified==0);
    PipelineCacheCoreTestAccess::CheckUnchanged(cache,params.hash);
    set_format(2); // the submitted ordered draw observes the completed resource table
    selection.worker=nullptr;
    result=cache.GetProgram(selection,HwStage::Fragment,SwStage::Fragment,params,bindings);
    assert(std::get<0>(result)!=nullptr && std::get<1>(result).value==42);
    PipelineCacheCoreTestAccess::CheckUnchanged(cache,params.hash);
    selection.worker=&worker;worker.failed=false;bindings={};
    result=cache.GetProgram(selection,HwStage::Fragment,SwStage::Fragment,params,bindings);
    assert(!worker.failed && worker.stages.size()==1 && std::get<1>(result).value==42);
    PipelineCacheCoreTestAccess::CheckUnchanged(cache,params.hash);
    std::puts("Real GetProgram worker rejected malformed table; ordered draw and next valid worker reused original module; caches unchanged");
}

static void TestSharp(bool image, bool valid, bool speculative) {
    using namespace Shader;
    Info info{};
    info.hw_stage = HwStage::Fragment;
    info.sw_stage = SwStage::Fragment;
    info.flattened_ud_buf.resize(8);
    if (image) {
        auto sharp = AmdGpu::Image::Null(false);
        sharp.base_address = 1;
        sharp.data_format = valid ? 2 : 15;
        sharp.num_format = 6;
        std::memcpy(info.flattened_ud_buf.data(), &sharp, sizeof(sharp));
        ImageResource res{};
        res.sharp_fetch.load_mask = 0xff;
        for (u32 i=0; i<8; ++i) res.sharp_fetch.offsets[i]=i;
        info.images.push_back(res);
    } else {
        AmdGpu::Buffer sharp{};
        sharp.base_address = 1;
        sharp.num_records = 16;
        sharp.data_format = valid ? 2 : 15;
        sharp.num_format = 6;
        std::memcpy(info.flattened_ud_buf.data(), &sharp, sizeof(sharp));
        BufferResource res{};
        res.sharp_fetch.load_mask = 0xf;
        for (u32 i=0; i<4; ++i) res.sharp_fetch.offsets[i]=i;
        res.is_formatted = true;
        info.buffers.push_back(res);
    }
    RuntimeInfo runtime{};
    runtime.Initialize(HwStage::Fragment, SwStage::Fragment);
    Profile profile{};
    SpeculativeFormatFailure failure{};
    StageSpecialization spec(info, runtime, profile, {}, speculative ? &failure : nullptr);
    assert(spec.Valid() == (valid || !speculative));
    if (!spec.Valid()) {
        assert(failure.image == image);
        assert(failure.data_format == 15 && failure.num_format == 6);
        assert(std::memcmp(failure.raw.data(), info.flattened_ud_buf.data(), image ? 32 : 16) == 0);
        // The ordered path sees the completed descriptor, not the speculative bytes.
        if (image) {
            auto& sharp = *reinterpret_cast<AmdGpu::Image*>(info.flattened_ud_buf.data());
            sharp.data_format = 2;
        } else {
            auto& sharp = *reinterpret_cast<AmdGpu::Buffer*>(info.flattened_ud_buf.data());
            sharp.data_format = 2;
        }
        StageSpecialization ordered(info, runtime, profile, {});
        assert(ordered.Valid());
        if (image) assert(ordered.images[0].num_conversion == AmdGpu::NumberConversion::Sint16ToSnormNz);
        else assert(ordered.buffers[0].num_conversion == AmdGpu::NumberConversion::Sint16ToSnormNz);
        std::puts("Malformed speculative descriptor rejected; ordered valid descriptor succeeds");
        return;
    }
    if (speculative) {
        StageSpecialization ordered(info, runtime, profile, {});
        assert(ordered == spec && spec == ordered);
    }
    // Exhaust the hardware field widths, preserving every old supported conversion.
    unsigned supported = 0, rejected = 0;
    using D = AmdGpu::DataFormat; using N = AmdGpu::NumberFormat; using C = AmdGpu::NumberConversion;
    for (u32 n=0; n<16; ++n) for (u32 d=0; d<64; ++d) {
        std::optional<C> expected = C::None;
        switch (N(n)) {
        case N::Unorm: if (d==4 || d==11 || d==13 || d==14) expected=C::Uint32ToUnorm; break;
        case N::Srgb: if(d==40) expected=C::SrgbToNorm; break;
        case N::Uscaled: expected=C::UintToUscaled; break;
        case N::Sscaled: expected=C::SintToSscaled; break;
        case N::Ubnorm: expected=C::UnormToUbnorm; break;
        case N::SnormNz:
            if(d==1 || d==3 || d==10) expected=C::Sint8ToSnormNz;
            else if(d==2 || d==5 || d==12) expected=C::Sint16ToSnormNz;
            else expected.reset();
            break;
        default: break;
        }
        const auto actual=AmdGpu::TryMapNumberConversion(N(n),D(d));
        assert(actual==expected);
        if(actual) { ++supported; assert(AmdGpu::MapNumberConversion(N(n),D(d)) == *expected); }
        else ++rejected;
    }
    assert(supported==966 && rejected==58);
    std::puts("Descriptor specialization succeeded");
}

int main(int argc, char** argv) {
    if (argc == 1) {
        TestVertexFormats(false, false);
        TestVertexFormats(false, true);
        TestVertexFormats(true, true);
        TestVertexReadFault();
        for (bool image : {false, true}) {
            TestSharp(image, true, false);
            TestSharp(image, true, true);
            TestSharp(image, false, true);
            TestWorker(image);
        }
        std::puts("PASS: 1024 format combinations, speculative rejection, strict semantics, real worker fallback, unchanged program/module caches");
    } else if (std::string_view(argv[1]) == "vertex-worker") {
        TestVertexFormats(true, true);
    } else if (std::string_view(argv[1]) == "vertex-ordered-invalid") {
        TestVertexFormats(true, false);
    } else if (std::string_view(argv[1]) == "worker") {
        TestWorker(argc > 2 && std::string_view(argv[2]) == "image");
    } else {
        TestSharp(std::string_view(argv[1]) == "image",
                  argc > 2 && std::string_view(argv[2]) == "valid",
                  argc > 3 && std::string_view(argv[3]) == "speculative");
    }
}
