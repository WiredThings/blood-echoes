// Authored, game-free end-to-end fixture for FEX #6074.
// Link against the rebuilt FEX/embedding archives, run separately as "valid" and
// "fault". The fault mode intentionally exits from its own signal handler when
// the protected guest read faults before the following counter increment.
// Merely compiling this file is not a successful execution result.
#include "core/guest_cpu/fex_guest_cpu.h"
#include "core/guest_cpu/fex_hle_bridge.h"
#include <FEXCore/Core/X86Enums.h>
#include <atomic>
#include <chrono>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <optional>
#include <signal.h>
#include <string_view>
#include <thread>
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
static volatile sig_atomic_t* announced{};
static volatile sig_atomic_t* released{};
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
        *counter == 0 && *announced == 1 && *released == 1) {
        constexpr char evidence[] = "{\"actual_fex\":true,\"guest_gate_announced\":true,\"protection_published_after_guest_entry\":true,\"dead_flags_guest_read_faulted\":true,\"following_store_executed\":false}\n";
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
    announced = reinterpret_cast<volatile sig_atomic_t*>(data.address()+16);
    released = reinterpret_cast<volatile sig_atomic_t*>(data.address()+20);
    *announced = 0; *released = 0;
    // The initial mapping remains RW through FEX admission. Guest entry writes
    // gate0 and waits (bounded ECX loop) for gate1. Only then can the native
    // helper protect its OWN input page and publish completion. No HLE callback,
    // host re-admission or game instruction is involved.
    std::vector<unsigned char> instructions;
    auto emit=[&](std::initializer_list<unsigned char> bytes) {
        instructions.insert(instructions.end(),bytes);
    };
    emit({0xc7,0x02,1,0,0,0});                    // mov dword [rdx],1
    emit({0xb9,0,0,0,2});                        // mov ecx,0x02000000
    const auto wait=instructions.size();
    emit({0x83,0x7a,4,0,0x75,0});                // cmp dword [rdx+4],0; jne notified
    const auto notified_branch=instructions.size()-1;
    emit({0xf3,0x90,0xff,0xc9,0x75,0});          // pause; dec ecx; jnz wait
    const auto wait_branch=instructions.size()-1;
    const auto timeout=instructions.size();
    emit({0xb8,0xde,0xc0,0xad,0xde,0xc3});        // explicit timeout/error return
    const auto notified=instructions.size();
    emit({0x83,0x7a,4,1,0x75,0});                // helper must have published success=1
    const auto failed_branch=instructions.size()-1;
    // ADD overwrites all CMP flags: the protected read's only effect is its fault.
    emit({0x48,0x3b,0x07,0x83,0x06,0x01});       // cmp rax,[rdi]; add dword [rsi],1
    emit({0xb8,42,0,0,0,0xc3});                  // normal return only for valid input
    auto branch=[&](size_t at,size_t destination) {
        const auto delta=static_cast<ptrdiff_t>(destination)-static_cast<ptrdiff_t>(at+1);
        assert(delta>=-128 && delta<=127);
        instructions[at]=static_cast<unsigned char>(static_cast<signed char>(delta));
    };
    branch(notified_branch,notified);branch(wait_branch,wait);branch(failed_branch,timeout);
    std::memset(code.pointer, 0xf4, code.size);
    std::memcpy(code.pointer, instructions.data(), instructions.size());
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
    request.Gpr[FEXCore::X86State::REG_RDX] = data.address()+16;
    request.MappedRanges = ranges.values;
    const auto halt = cpu->ReturnAddress();
    std::memcpy(reinterpret_cast<void*>(request.Rsp), &halt, sizeof(halt));
    fault_address.store(readable.address(),std::memory_order_relaxed);
    armed = protected_read ? 1 : 0;
    std::atomic<int> helper_error{};
    std::thread helper([&] {
        const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(10);
        while (__atomic_load_n(announced,__ATOMIC_ACQUIRE)!=1) {
            if (std::chrono::steady_clock::now()>=deadline) {
                helper_error.store(1,std::memory_order_relaxed);
                __atomic_store_n(released,2,__ATOMIC_RELEASE);return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (protected_read && mprotect(readable.pointer,readable.size,PROT_NONE)!=0) {
            helper_error.store(2,std::memory_order_relaxed);
            __atomic_store_n(released,2,__ATOMIC_RELEASE);return;
        }
        // Last helper-side action before return. The expected signal path uses
        // async-safe _exit, so fixture process teardown also reaps this thread;
        // every non-signal return explicitly joins it below.
        __atomic_store_n(released,1,__ATOMIC_RELEASE);
    });
    auto result = cpu->Run(request);
    helper.join();
    armed = 0;
    if (protected_read) {
        std::fprintf(stderr, "guest read did not fault; counter=%d gate0=%d gate1=%d helper_error=%d\n",
                     int(*counter),int(*announced),int(*released),helper_error.load(std::memory_order_relaxed));
        return 1;
    }
    assert(helper_error.load(std::memory_order_relaxed)==0);
    assert(*announced==1 && *released==1);
    assert(std::holds_alternative<GuestExecutionState>(result));
    assert(std::get<GuestExecutionState>(result).Gpr[FEXCore::X86State::REG_RAX]==42);
    assert(*counter == 1);
    std::puts("{\"actual_fex\":true,\"valid_guest_read_returned\":true,\"following_store_executed\":true}");
}
