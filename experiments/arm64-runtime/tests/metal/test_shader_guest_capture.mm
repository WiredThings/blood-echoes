// SPDX-License-Identifier: GPL-2.0-or-later
// Authored host pages and GCN only. No game, settings, private cache, or window.
#include <array>
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
    static bool Stages(PipelineCacheCore& core, PipelineSelection& selection) {
        return core.RefreshGraphicsStages(selection);
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
int main() {
    Signals signals;
    Pages memory;
    assert(mprotect(memory.bytes + memory.page, memory.page, PROT_NONE) == 0);
    std::array<unsigned char,32> copied{};
    assert(Common::GuestCapture::TryCopy(copied.data(), memory.bytes, copied.size()));
    sigset_t before_mask{}; assert(sigprocmask(SIG_SETMASK, nullptr, &before_mask) == 0);
    sigjmp_buf previous;
    if (sigsetjmp(previous, 0)) { assert(false && "inner fault escaped into previous recovery"); }
    runtime_fault_recover = &previous;
    assert(!Common::GuestCapture::TryCopy(copied.data(), memory.bytes+memory.page,
                                        copied.size()));
    assert(runtime_fault_recover == &previous);
    assert(!Common::GuestCapture::TryCopy(copied.data(), memory.bytes+memory.page-4,
                                        copied.size()));
    assert(runtime_fault_recover == &previous);
    assert(Common::GuestCapture::TryCopy(nullptr, nullptr, 0));
    assert(!Common::GuestCapture::TryCopy(copied.data(), nullptr, 1));
    assert(!Common::GuestCapture::TryCopy(copied.data(),
        reinterpret_cast<const void*>(std::numeric_limits<uintptr_t>::max()-1), 4));
    for (unsigned i = 0; i < 1000; ++i) {
        try {
            Owner owner;
            Common::GuestCapture::CopyOrThrow(owner.bytes.data(), memory.bytes+memory.page, 16);
            assert(false);
        } catch (const Common::GuestCapture::Unavailable&) {}
        assert(Owner::live == 0 && Owner::destroyed == i+1);
        assert(runtime_fault_recover == &previous);
    }
    runtime_fault_recover = nullptr;
    sigset_t after_mask{}; assert(sigprocmask(SIG_SETMASK, nullptr, &after_mask) == 0);
    assert(sigismember(&before_mask, SIGSEGV) == sigismember(&after_mask, SIGSEGV));
    assert(sigismember(&before_mask, SIGBUS) == sigismember(&after_mask, SIGBUS));
    assert(faults >= 1002);

    const std::array<u32,3> code{0xBEEB03FF,3,0xBF810000}; // BinaryInfo at +32
    std::memcpy(memory.bytes, code.data(), sizeof(code));
    auto header = Header(sizeof(code)); std::memcpy(memory.bytes+32, &header, sizeof(header));
    AmdGpu::ShaderCapture::Probe probe;
    using Status = AmdGpu::ShaderCapture::Status;
    const auto base = reinterpret_cast<uintptr_t>(memory.bytes);
    assert(AmdGpu::ShaderCapture::ProbeProgram(base, probe) == Status::Ready);
    assert(probe.header_offset == 32 && probe.header.shader_hash == header.shader_hash);
    std::array<u32,16> ud{}; ud[4] = 77;
    std::array<u32,3> owned_storage{};
    AmdGpu::ShaderCapture::ProgramBytes owned;
    assert(AmdGpu::ShaderCapture::CaptureProgramBytes(probe, ud, owned_storage, owned)
           == Status::Ready);
    assert(owned.original_base == base && owned.code.data() == owned_storage.data() &&
           owned.user_data[4] == 77);
    auto changed_probe = probe; changed_probe.header.shader_hash ^= 1;
    AmdGpu::ShaderCapture::ProgramBytes refused;
    assert(AmdGpu::ShaderCapture::CaptureProgramBytes(changed_probe, ud, owned_storage, refused)
           == Status::Changed && refused.code.empty());
    std::memset(memory.bytes, 0xff, sizeof(code)); ud.fill(99);
    assert(owned.code[2] == code[2] && owned.user_data[4] == 77);
    assert(AmdGpu::ShaderCapture::CaptureProgramBytes(probe, ud,
            std::span<u32>{owned_storage}.first(2), refused) == Status::StorageTooSmall);
    assert(refused.code.empty());

    // Exact boundary and first-header/length corruption. Every raw header read uses the leaf.
    std::memcpy(memory.bytes, code.data(), sizeof(code));
    for (u32 length : {0u, 3u, u32(AmdGpu::ShaderCapture::MaxProgramBytes+4)}) {
        auto bad = Header(length); std::memcpy(memory.bytes+32, &bad, sizeof(bad));
        assert(AmdGpu::ShaderCapture::ProbeProgram(base, probe) == Status::InvalidHeader);
    }
    header = Header(u32(AmdGpu::ShaderCapture::MaxProgramBytes));
    std::memcpy(memory.bytes+32, &header, sizeof(header));
    assert(AmdGpu::ShaderCapture::ProbeProgram(base, probe) == Status::Ready);
    auto* words = reinterpret_cast<u32*>(memory.bytes); words[1] = 0xffffffff;
    assert(AmdGpu::ShaderCapture::ProbeProgram(base, probe) == Status::InvalidHeader);
    assert(AmdGpu::ShaderCapture::ProbeProgram(base+2, probe) == Status::InvalidHeader);
    assert(AmdGpu::ShaderCapture::ProbeProgram(reinterpret_cast<uintptr_t>(memory.bytes+memory.page),
           probe) == Status::Unreadable);
    // Fast-path header crossing a guard is a refusal, never raw SearchBinaryInfo outside recover.
    words[1] = u32(memory.page/8 - 2);
    assert(AmdGpu::ShaderCapture::ProbeProgram(base, probe) == Status::Unreadable);

    for (size_t available = 0; available < sizeof(AmdGpu::BinaryInfo); available += 4) {
        std::memset(memory.bytes, 0, memory.page);
        auto* partial_base = memory.bytes + memory.page - available - 32;
        std::memcpy(partial_base, code.data(), sizeof(code));
        if (available) std::memcpy(partial_base+32, &header, available);
        assert(AmdGpu::ShaderCapture::ProbeProgram(reinterpret_cast<uintptr_t>(partial_base), probe)
               == Status::Unreadable);
    }
    for (size_t available : {4u,8u}) {
        AmdGpu::ShaderCapture::Probe partial{};
        partial.base = reinterpret_cast<uintptr_t>(memory.bytes+memory.page-available);
        partial.header_offset = 32; partial.header = Header(sizeof(code));
        assert(AmdGpu::ShaderCapture::CaptureProgramBytes(partial, ud, owned_storage, refused)
               == Status::Unreadable && refused.code.empty());
    }
    Pages scan(18); std::memset(scan.bytes, 0xff, scan.total);
    const auto scan_base = reinterpret_cast<uintptr_t>(scan.bytes);
    assert(AmdGpu::ShaderCapture::ProbeProgram(scan_base, probe) == Status::NotFound);
    header = Header(12); std::memcpy(scan.bytes+64, &header, sizeof(header));
    assert(AmdGpu::ShaderCapture::ProbeProgram(scan_base, probe) == Status::Ready &&
           probe.header_offset == 64);
    AmdGpu::ShaderProgram program{};
    assert(!(scan_base & 255)); program.address = scan_base >> 8; program.user_data.fill(8);
    auto params = AmdGpu::GetParamsForPreparation(program);
    assert(params && params->hash == header.shader_hash && params->Base() == scan_base &&
           params->code.size() == 3 && params->user_data[0] == 8);
    // Exercise the real prep selector and GS runtime entry, not just the new helper.
    // An unreadable shader must remain Unavailable without entering any compiler/backend.
    {
        AmdGpu::Liverpool liverpool;
        VideoCore::GuestProbeCore core{liverpool};
        VideoCore::PrepWorker worker;
        VideoCore::PipelineSelection selection{};
        selection.worker = &worker; selection.regs = &liverpool.regs;
        liverpool.regs.stage_enable.raw = AmdGpu::ShaderStageEnable::Vs;
        liverpool.regs.ps_program.address = 0;
        liverpool.regs.vs_program.address =
            reinterpret_cast<uintptr_t>(memory.bytes+memory.page) >> 8;
        VideoCore::PipelineCacheCoreTestAccess::Stages(core, selection);
        assert(worker.failed && runtime_fault_recover == nullptr);
        worker.failed = false;
        Shader::ShaderParams input{std::span<const u32,16>{ud}, code, header.shader_hash};
        Shader::Backend::Bindings binding{};
        const auto result = core.GetProgram(selection, Shader::HwStage::Geometry,
                                            Shader::SwStage::Geometry, input, binding);
        assert(worker.failed && !std::get<1>(result) && runtime_fault_recover == nullptr);
    }
    // GPU-authoritative GetParams is deliberately not called by this worker fixture.

    const std::array<u32,2> pair{0x11223344,0x55667788};
    Shader::Gcn::GcnCodeSlice complete(pair.data(),pair.data()+2,true);
    assert(complete.at(1) == pair[1]);
    assert(complete.readu64() == (u64(pair[1])<<32|pair[0]) && complete.atEnd());
    Rejects([&] { complete.readu32(); });
    Shader::Gcn::GcnCodeSlice one(pair.data(),pair.data()+1,true);
    Rejects([&] { one.at(std::numeric_limits<u32>::max()); });
    Rejects([&] { one.readu64(); }); assert(one.position() == pair.data());
    // Real decoder: s_mov_b32 with a literal must have its extension dword.
    const std::array<u32,2> literal{0xBEEB03FF,0};
    Shader::Gcn::GcnDecodeContext decoder;
    Shader::Gcn::GcnCodeSlice truncated_literal(literal.data(),literal.data()+1,true);
    Rejects([&] { decoder.decodeInstruction(truncated_literal); });
    Shader::Gcn::GcnCodeSlice full_literal(literal.data(),literal.data()+2,true);
    const auto decoded = decoder.decodeInstruction(full_literal);
    assert(decoded.length == 8 && full_literal.atEnd());
    // A 64-bit VOP3 encoding truncated after its first word.
    const std::array<u32,1> vop3{0xD0000000};
    Shader::Gcn::GcnCodeSlice truncated_vop3(vop3.data(),vop3.data()+1,true);
    Rejects([&] { decoder.decodeInstruction(truncated_vop3); });
    assert(runtime_fault_recover == nullptr && Owner::live == 0);
    puts("PASS POD guest recovery/previous point/unwind, bounded shader params/code copy, and real GCN truncation controls");
}
