// Authored, game-free end-to-end fixture for FEX #6074.
// Link against the rebuilt FEX/embedding archives, run separately as "valid" and
// "fault". The fault mode intentionally exits from its own signal handler when
// the protected guest read faults before the following counter increment.
// Merely compiling this file is not a successful execution result.
#include "core/guest_cpu/fex_guest_cpu.h"
#include "core/guest_cpu/fex_hle_bridge.h"
#include <FEXCore/Core/X86Enums.h>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <optional>
#include <signal.h>
#include <string_view>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

using namespace Core;
using namespace Core::GuestCpu;
namespace {
constexpr size_t Page = 16384;
static std::atomic<uintptr_t> fault_address{};
static volatile sig_atomic_t armed{};
static volatile sig_atomic_t* counter{};
static_assert(sizeof(sig_atomic_t) == sizeof(uint32_t));
static_assert(std::atomic<uintptr_t>::is_always_lock_free);

struct Mapping {
    void* pointer;
    size_t size;
    explicit Mapping(size_t bytes) : size{bytes} {
        pointer = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(pointer != MAP_FAILED);
    }
    ~Mapping() { assert(munmap(pointer, size) == 0); }
    uintptr_t address() const { return reinterpret_cast<uintptr_t>(pointer); }
};
struct Ranges { std::vector<GuestExecutionRange> values; };
static bool valid(void* context, uintptr_t address, size_t size, bool writable) {
    if (!address || !size || address > UINTPTR_MAX-size) return false;
    for (auto range : static_cast<Ranges*>(context)->values)
        if ((!writable || range.Writable) && address >= range.Begin && size <= range.Size &&
            address - range.Begin <= range.Size - size) return true;
    return false;
}
static std::optional<GuestExecutionRange> executable(void* context, uintptr_t address) {
    for (auto range : static_cast<Ranges*>(context)->values)
        if (range.Executable && address >= range.Begin && address-range.Begin < range.Size) return range;
    return std::nullopt;
}
static void fault(int signal, siginfo_t* info, void*) {
    if (armed && (signal == SIGBUS || signal == SIGSEGV) && info &&
        reinterpret_cast<uintptr_t>(info->si_addr) == fault_address.load(std::memory_order_relaxed) &&
        *counter == 0) {
        constexpr char evidence[] = "{\"actual_fex\":true,\"dead_flags_guest_read_faulted\":true,\"following_store_executed\":false}\n";
        (void)write(STDOUT_FILENO, evidence, sizeof(evidence)-1);
        _exit(0);
    }
    constexpr char unexpected[] = "unexpected authored FEX load-fault location or phase\n";
    (void)write(STDERR_FILENO, unexpected, sizeof(unexpected)-1);
    _exit(91);
}
}
int main(int argc, char** argv) {
    assert(argc == 2);
    const bool protected_read = std::string_view{argv[1]} == "fault";
    assert(protected_read || std::string_view{argv[1]} == "valid");
    struct sigaction action{};
    action.sa_sigaction = fault;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    assert(sigaction(SIGBUS, &action, nullptr) == 0);
    assert(sigaction(SIGSEGV, &action, nullptr) == 0);
    Mapping code(Page), stack(Page*4), data(Page), readable(Page);
    counter = static_cast<volatile sig_atomic_t*>(data.pointer);
    *counter = 0;
    // cmp rax,[rdi]; add dword ptr [rsi],1; ret
    // ADD overwrites all CMP flags, so the load's fault is its only observable
    // effect. A pre-fix DCE may incorrectly delete that load with the dead flags.
    const unsigned char instructions[] = {0x48,0x3b,0x07,0x83,0x06,0x01,0xc3};
    std::memset(code.pointer, 0xf4, code.size);
    std::memcpy(code.pointer, instructions, sizeof(instructions));
    assert(mprotect(code.pointer, code.size, PROT_READ) == 0);
    Ranges ranges{{{code.address(),code.size,true,false}, {stack.address(),stack.size,false,true},
                  {data.address(),data.size,false,true}, {readable.address(),readable.size,false,true}}};
    HleCallRegistry registry;
    HleGuestBridge bridge{registry,valid,&ranges,nullptr,nullptr,executable,&ranges};
    auto created = FexGuestCpuBackend::Create(bridge);
    assert(std::holds_alternative<std::unique_ptr<FexGuestCpuBackend>>(created));
    auto cpu = std::move(std::get<std::unique_ptr<FexGuestCpuBackend>>(created));
    ranges.values.push_back(cpu->ReturnRange());
    ranges.values.push_back(cpu->CallbackReturnRange());
    GuestExecutionRequest request;
    request.Rip = code.address();
    request.Rsp = stack.address()+stack.size-8;
    request.Rflags = 2;
    request.Gpr[FEXCore::X86State::REG_RDI] = readable.address();
    request.Gpr[FEXCore::X86State::REG_RSI] = data.address();
    request.MappedRanges = ranges.values;
    const auto halt = cpu->ReturnAddress();
    std::memcpy(reinterpret_cast<void*>(request.Rsp), &halt, sizeof(halt));
    if (protected_read) {
        fault_address.store(readable.address(),std::memory_order_relaxed);
        assert(mprotect(readable.pointer, readable.size, PROT_NONE) == 0);
        armed = 1;
    }
    auto result = cpu->Run(request);
    armed = 0;
    if (protected_read) {
        std::fprintf(stderr, "guest read did not fault; counter=%d\n", int(*counter));
        return 1;
    }
    assert(std::holds_alternative<GuestExecutionState>(result));
    assert(*counter == 1);
    std::puts("{\"actual_fex\":true,\"valid_guest_read_returned\":true,\"following_store_executed\":true}");
}
