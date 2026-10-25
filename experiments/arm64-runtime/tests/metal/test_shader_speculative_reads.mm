// SPDX-License-Identifier: GPL-2.0-or-later
// Authored host pages and GCN only. No game, settings, private cache, or window.
#include <array>
#include <chrono>
#include <string_view>
#include "video_core/renderer_vulkan/vk_draw_prep.h"
#include <cassert>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>
#include "common/guest_capture.h"
#include "shader_recompiler/frontend/decode.h"
#include "video_core/amdgpu/shader_capture.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/pipeline_cache_core.h"
#include "test_gpu_runtime_stubs.cpp"
namespace {
volatile sig_atomic_t faults = 0;
void Fault(int signal, siginfo_t*, void*) {
    if (runtime_fault_recover) {
        faults = faults + 1;
        siglongjmp(*runtime_fault_recover, 1);
    }
    _Exit(128 + signal);
}
struct Signals {
    struct sigaction segv{}, bus{};
    Signals() {
        struct sigaction action{}; action.sa_sigaction = Fault; action.sa_flags = SA_SIGINFO;
        sigemptyset(&action.sa_mask);
        assert(sigaction(SIGSEGV, &action, &segv) == 0);
        assert(sigaction(SIGBUS, &action, &bus) == 0);
    }
    ~Signals() { sigaction(SIGSEGV, &segv, nullptr); sigaction(SIGBUS, &bus, nullptr); }
};
struct Pages {
    size_t page = size_t(sysconf(_SC_PAGESIZE));
    unsigned char* bytes;
    explicit Pages(size_t count = 2) {
        bytes = static_cast<unsigned char*>(mmap(nullptr, page*count, PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANON, -1, 0)); assert(bytes != MAP_FAILED);
        total = page*count;
    }
    size_t total{};
    ~Pages() { assert(munmap(bytes, total) == 0); }
};
struct Owner {
    static inline unsigned live = 0, destroyed = 0;
    std::vector<unsigned char> bytes = std::vector<unsigned char>(4096);
    Owner() { ++live; }
    ~Owner() { --live; ++destroyed; }
};
template<class F> void Rejects(F&& fn) {
    bool rejected = false;
    try { fn(); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
}
AmdGpu::BinaryInfo Header(u32 length) {
    AmdGpu::BinaryInfo h{}; h.signature = AmdGpu::BinaryInfo::signature_ref;
    h.length = length; h.shader_hash = 0x13579bdf2468ace0ULL; return h;
}
}
namespace VideoCore {
struct PipelineCacheCoreTestAccess {
    static void Install(PipelineCacheCore& core, PipelineSelection& selection,
                        Shader::Info input, const Shader::ShaderParams& params) {
        auto program = std::make_unique<Program>();
        program->info = input;
        program->info.RefreshFlatBuf(); // Authored valid baseline, original synchronous walker.
        auto runtime = core.BuildRuntimeInfo(selection, input.hw_stage, input.sw_stage);
        auto spec = Shader::StageSpecialization(program->info, runtime, Shader::Profile{}, {});
        assert(spec.Valid());
        program->AddPermut(ShaderModuleHandle{123}, std::move(spec));
        program->info_template = std::make_unique<Shader::Info>(input);
        core.program_cache.emplace(params.hash, std::move(program));
    }
    static Shader::Info& Template(PipelineCacheCore& core, u64 hash) {
        return *core.program_cache.at(hash)->info_template;
    }
    static void CheckUnchanged(PipelineCacheCore& core, const PrepWorker& worker, u64 hash) {
        assert(core.program_cache.size() == 1 && core.program_cache.at(hash)->modules.size() == 1);
        for (const auto& [p, info] : worker.infos) assert(!info.speculative_reads);
    }
};
class GuestProbeCore final : public PipelineCacheCore {
public:
    explicit GuestProbeCore(AmdGpu::Liverpool& l)
        : PipelineCacheCore(&l, Shader::Profile{}, PipelineCapabilities{}, 16, "authored-guest-probe") {}
    ~GuestProbeCore() { DestroyPipelines(); }
    ShaderModuleHandle CreateShaderModule(std::span<const u32>, std::string_view, bool) override {
        std::abort();
    }
    void DestroyShaderModule(ShaderModuleHandle) override {}
    std::unique_ptr<PipelineSerialization> NewGraphicsSerialization() override { return {}; }
    std::unique_ptr<PipelineSerialization> NewComputeSerialization() override { return {}; }
    std::unique_ptr<PipelineBase> CreateGraphicsPipeline(const GraphicsPipelineKey&,
        std::span<const Shader::Info*,MaxShaderStages>,
        std::span<const Shader::RuntimeInfo,MaxShaderStages>,
        std::optional<const Shader::Gcn::FetchShaderData>,
        std::span<const ShaderModuleHandle,MaxShaderStages>,PipelineSerialization&,bool) override {
        std::abort();
    }
    std::unique_ptr<PipelineBase> CreateComputePipeline(ComputePipelineKey,const Shader::Info&,
        ShaderModuleHandle,PipelineSerialization&,bool) override { std::abort(); }
};
}

namespace {
using Op = Shader::Optimization::SrtOp;
void Emit(std::vector<u32>& program, Op op, u32 a=0, u32 b=0) {
    program.insert(program.end(), {u32(op),a,b});
}
void SetWalker(Shader::Info& info, const std::vector<u32>& program, u32 flat = 24) {
    info.srt_info.walker_func = reinterpret_cast<Shader::PFN_SrtWalker>(const_cast<u32*>(program.data()));
    info.srt_info.walker_func_size = program.size()*sizeof(u32);
    info.srt_info.flattened_bufsize_dw = flat;
}
void Pointer(std::array<u32,16>& ud, size_t at, const void* ptr) {
    const auto address = reinterpret_cast<u64>(ptr); std::memcpy(ud.data()+at,&address,8);
}
[[gnu::noinline]] void HostCopy(void* to, const void* from, size_t bytes) {
    std::memcpy(to,from,bytes); std::atomic_signal_fence(std::memory_order_seq_cst);
}
int Bench() {
    // Successful host reads only. Each result is CPU cost per copy/per authored 64-read stage;
    // no game/FPS/frame-time claim is implied. Same size/payload/iteration count for both.
    constexpr unsigned rounds = 100000, per_stage = 64;
    std::array<u32,4> src{1,2,3,4},dst{};
    const auto run = [&](bool leaf) {
        const auto begin = std::chrono::steady_clock::now();
        for (unsigned r=0;r<rounds;++r) {
            if (leaf) assert(Common::GuestCapture::TryCopy(dst.data(),src.data(),sizeof(src)));
            else HostCopy(dst.data(),src.data(),sizeof(src));
        }
        const double ns = double(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now()-begin).count())/rounds;
        assert(dst == src); return ns;
    };
    const auto bounded_stage = [&]() {
        constexpr unsigned stages = 2000;
        const auto begin=std::chrono::steady_clock::now();
        for(unsigned stage=0;stage<stages;++stage) {
            Shader::SpeculativeReadState reads;
            for(unsigned i=0;i<per_stage;++i)
                reads.Copy(dst.data(),reinterpret_cast<u64>(src.data()),sizeof(src));
        }
        assert(dst==src);
        return double(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now()-begin).count())/stages/1000.0;
    };
    // Interleave comparisons to make frequency/order sensitivity visible in raw output.
    for (unsigned sample=0;sample<5;++sample) {
        const double direct = run(false), leaf = run(true);
        printf("BENCH sample=%u bytes=16 copies=%u direct_ns=%.3f leaf_ns=%.3f "
               "extra_ns=%.3f projected_64_leaf_extra_us=%.3f bounded_64_copy_stage_us=%.3f\n",
               sample,rounds,direct,leaf,leaf-direct,(leaf-direct)*per_stage/1000.0,
               bounded_stage());
    }
    return 0;
}
}
int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--bench") return Bench();
    Signals signals;
    Pages memory;
    assert(mprotect(memory.bytes+memory.page,memory.page,PROT_NONE)==0);
    auto* guard = memory.bytes+memory.page;
    std::array<u32,16> ud{};
    std::array<u32,16> root{}, nested{};
    AmdGpu::Buffer sharp{}; sharp.base_address = 0x10000; sharp.stride = 4;
    sharp.num_records = 16; sharp.data_format = u32(AmdGpu::DataFormat::Format32);
    sharp.num_format = u32(AmdGpu::NumberFormat::Float);
    std::memcpy(nested.data()+2,&sharp,sizeof(sharp));
    root[0] = 4;
    const auto child = reinterpret_cast<u64>(nested.data());
    std::memcpy(root.data()+4,&child,8); Pointer(ud,0,root.data());
    std::vector<u32> graph;
    Emit(graph,Op::PushPtrImm,0); Emit(graph,Op::CopyImm,0,20); // dynamic offset comes from guest
    Emit(graph,Op::AccFlat,20); Emit(graph,Op::PushPtrAcc);   // nested pointer read
    for (u32 i=0;i<4;++i) Emit(graph,Op::CopyImm,2+i,16+i);
    Emit(graph,Op::PopPtr); Emit(graph,Op::PopPtr); Emit(graph,Op::End);
    const std::array<u32,1> shader{0xBF810000};
    Shader::ShaderParams params{std::span<const u32,16>{ud},shader,0x5152535455565758ULL};
    Shader::Info input{Shader::HwStage::Vertex,Shader::SwStage::Vertex,params};
    SetWalker(input,graph);
    Shader::BufferResource resource{}; resource.sharp_fetch.load_mask = 15;
    resource.sharp_fetch.offsets = {16,17,18,19}; resource.is_formatted = true;
    input.buffers.push_back(resource);
    auto original = input; original.RefreshFlatBuf();
    {
        Shader::SpeculativeReadState state;
        Shader::ScopedSpeculativeReads scope{input,state};
        input.RefreshFlatBuf();
        assert(input.flattened_ud_buf == original.flattened_ud_buf);
        assert(state.reads == 7 && state.charged_bytes <= state.MaxBytes);
        assert(input.buffers[0].GetSharp(input) == sharp);
    }
    assert(!input.speculative_reads);
    sigjmp_buf previous;
    if (sigsetjmp(previous,0)) assert(false && "resource fault escaped the POD leaf");
    runtime_fault_recover = &previous;
    const auto reject_flat = [&](const std::vector<u32>& program, u32 flat=24) {
        Shader::Info local = input; SetWalker(local,program,flat);
        Rejects([&] {
            Owner owner;
            Shader::SpeculativeReadState state;
            Shader::ScopedSpeculativeReads scope{local,state}; local.RefreshFlatBuf();
        });
        assert(!local.speculative_reads && Owner::live == 0 && runtime_fault_recover == &previous);
    };
    // Fault after program/vector ownership exists; then recover repeatedly with the same outer point.
    auto invalid_child = reinterpret_cast<u64>(guard); std::memcpy(root.data()+4,&invalid_child,8);
    for (unsigned i=0;i<32;++i) reject_flat(graph);
    std::memcpy(root.data()+4,&child,8);
    std::vector<u32> bad;
    Emit(bad,Op::AccFlat,23); Emit(bad,Op::End); reject_flat(bad); // no synthetic zero
    bad.clear(); Emit(bad,Op::CopyImm,0,24); Emit(bad,Op::End); reject_flat(bad);
    bad.clear(); Emit(bad,Op::PopPtr); Emit(bad,Op::End); reject_flat(bad);
    bad.clear(); Emit(bad,Op::Add); Emit(bad,Op::End); reject_flat(bad);
    bad.clear(); for(unsigned i=0;i<65;++i) Emit(bad,Op::Push); Emit(bad,Op::End);reject_flat(bad);
    bad.clear(); for(unsigned i=0;i<65;++i) Emit(bad,Op::PushPtrImm,0); Emit(bad,Op::End);
    // Use a self-pointer table so the pointer-stack bound, rather than unreadable data, is reached.
    std::array<u32,2> self{};const u64 selfp=reinterpret_cast<u64>(self.data());
    std::memcpy(self.data(),&selfp,8);Pointer(ud,0,self.data());reject_flat(bad);Pointer(ud,0,root.data());
    bad.clear(); Emit(bad,Op::AccImm,3); reject_flat(bad); // missing End
    bad = {0xffffffff,0,0}; reject_flat(bad);
    reject_flat(graph,u32(Shader::SpeculativeReadState::MaxFlatBytes/4+1));
    {
        Shader::SpeculativeReadState state; state.Charge(state.MaxBytes);
        Rejects([&] { u32 value; state.Copy(&value,reinterpret_cast<u64>(root.data()),4); });
        assert(state.reads == 1 && state.charged_bytes == state.MaxBytes);
    }
    // Real GetProgram preparation lookup: valid nested SRT matches a real specialization;
    // unreadable SRT and budget exhaustion return Unavailable without backend/cache mutation.
    {
        AmdGpu::Liverpool liverpool;
        VideoCore::GuestProbeCore core{liverpool};
        VideoCore::PipelineSelection selection{}; selection.regs = &liverpool.regs;
        VideoCore::PipelineCacheCoreTestAccess::Install(core,selection,input,params);
        const auto lookup = [&]() {
            VideoCore::PrepWorker worker; selection.worker = &worker;
            Shader::Backend::Bindings bindings{};
            const auto result=core.GetProgram(selection,input.hw_stage,input.sw_stage,params,bindings);
            VideoCore::PipelineCacheCoreTestAccess::CheckUnchanged(core,worker,params.hash);
            assert(runtime_fault_recover == &previous);
            selection.worker=nullptr;
            return std::pair{worker.failed,bool(std::get<1>(result))};
        };
        assert((lookup() == std::pair{false,true}));
        std::memcpy(root.data()+4,&invalid_child,8);
        assert((lookup() == std::pair{true,false}));std::memcpy(root.data()+4,&child,8);
        auto& t=VideoCore::PipelineCacheCoreTestAccess::Template(core,params.hash);
        std::vector<u32> too_many;Emit(too_many,Op::PushPtrImm,0);
        for(size_t i=0;i<Shader::SpeculativeReadState::MaxReads;++i) Emit(too_many,Op::CopyImm,0,20);
        Emit(too_many,Op::End);SetWalker(t,too_many);
        assert((lookup() == std::pair{true,false}));SetWalker(t,graph);
        t.srt_info.flattened_bufsize_dw=u32(Shader::SpeculativeReadState::MaxFlatBytes/4+1);
        assert((lookup() == std::pair{true,false}));SetWalker(t,graph);
        // StageSpecialization's actual fetch branch must recover after growing its vector.
        const std::array<u32,4> fetch{
            u32(Shader::Gcn::InstEncoding::SMRD)|(2u<<22)|(8u<<15)|(1u<<9)|(1u<<8),
            u32(Shader::Gcn::InstEncoding::MUBUF)|(3u<<18)|(1u<<13),
            (2u<<16)|(4u<<8),
            0xBE802000};
        Pointer(ud,4,fetch.data()); Pointer(ud,2,&sharp);
        t.has_fetch_shader=true;t.fetch_shader_sgpr_base=4;
        auto vf=t;vf.user_data=ud;
        {
            Shader::SpeculativeReadState state;Shader::ScopedSpeculativeReads scope{vf,state};
            auto parsed=Shader::Gcn::ParseFetchShader(vf);assert(parsed && parsed->attributes.size()==1);
            assert(parsed->attributes[0].sgpr_base==2 && parsed->size==16);
            // The V# points through s[2:3], independently of the fetch code in s[4:5].
            assert(parsed->attributes[0].GetSharp(vf)==sharp);
            Pointer(ud,2,guard);
            AmdGpu::Buffer out{};
            assert(!Vulkan::ReadSpeculativeVertexSharp(vf,parsed->attributes[0],out));
            assert(vf.speculative_reads==&state && runtime_fault_recover==&previous);
        }
        // Put the already decoded scalar+attribute instructions at the page end. The next
        // instruction faults after FetchShaderData owns a nonempty attributes vector.
        Pointer(ud,2,&sharp);
        // A fully readable new fetch specialization still remains a module miss: lookup-only
        // work must not manufacture a ready module or publish compilation from this branch.
        assert((lookup() == std::pair{true,false}));
        std::memcpy(guard-12,fetch.data(),12);Pointer(ud,4,guard-12);
        assert((lookup() == std::pair{true,false}));
        t.has_fetch_shader=false;
    }
    // Checked invalid encoding/opcode must throw, never table-index out of bounds or ASSERT.
    for(u32 instruction : {0xffffffffu,0xBE80ff00u}) {
        Shader::Gcn::GcnDecodeContext decoder;
        Shader::Gcn::GcnCodeSlice slice(&instruction,&instruction+1,true);
        Rejects([&] { decoder.decodeInstruction(slice); });
    }
    runtime_fault_recover=nullptr;
    assert(Owner::live==0 && faults>=34);
    puts("PASS real prep lookup, nested dynamic SRT, strict initialization/budgets, bounded fetch/V#, and normal owner unwind");
}
