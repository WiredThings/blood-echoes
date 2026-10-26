// SPDX-License-Identifier: GPL-2.0-or-later
// Execute real cloned and cached-form SRT programs without a game or GPU.
#include "shader_recompiler/ir/passes/srt_watch.h"
#include "video_core/frame_capture.h"
#include "core/signals.h"
#include <xbyak/xbyak.h>
#include <array>
#include <cassert>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <vector>
#include <sys/mman.h>
#include <signal.h>
using namespace Xbyak::util;
static std::vector<std::pair<u64,u64>> reads;
static void Observe(u64 a,u64 n) {reads.emplace_back(a,n);}
static void Fault(int,siginfo_t* info,void* context) {
    if(!Core::Signals::Instance()->DispatchAccessViolation(context,info->si_addr))_Exit(125);
}
int main() {
    constexpr size_t capacity=65536;
    auto* data=static_cast<u32*>(mmap(reinterpret_cast<void*>(0x900000000),4096,
        PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0));
    assert(data==reinterpret_cast<void*>(0x900000000));
    for(unsigned i=0;i<1024;++i)data[i]=i*17+7;
    alignas(16) u64 ud[8]={reinterpret_cast<u64>(data)};
    Xbyak::CodeGenerator original(capacity),clones(capacity);
    original.push(rdi);original.mov(rdi,ptr[rdi]);
    original.mov(r10d,dword[rdi+4]);original.mov(dword[rsi],r10d);
    original.stc();original.mov(r10d,dword[rdi+8]);original.adc(r10d,0);
    original.mov(dword[rsi+4],r10d);
    original.mov(r10d,12);original.mov(r10d,dword[rdi+r10]);original.mov(dword[rsi+8],r10d);
    original.mov(r10d,dword[rdi+196]);original.mov(dword[rsi+12],r10d);
    original.mov(r10d,dword[rdi+200]);original.mov(dword[rsi+16],r10d);
    original.push(rdi);original.mov(rdi,qword[rdi+4]);original.mov(qword[rsi+32],rdi);original.pop(rdi);
    original.pop(rdi);original.ret();original.ready();
    const auto fn=original.getCode<Shader::PFN_SrtWalker>();
    std::vector<u8> saved(original.getCode(),original.getCode()+original.getSize());
    std::array<u32,32> expected,actual;expected.fill(0x12345678);actual=expected;
    fn(reinterpret_cast<u32*>(ud),expected.data());
    u32 sites=0;
    const auto clone=Shader::BuildSrtWatchClone(clones,original.getCode(),original.getSize(),
        0x900000008,192,&Observe,sites,capacity);
    assert(clone && sites==7);
    clone(reinterpret_cast<u32*>(ud),actual.data());
    assert(actual==expected && actual[1]==data[2]+1);
    const std::vector<std::pair<u64,u64>> wanted={{0x900000008,4},{0x90000000c,4},{0x9000000c4,4},{0x900000004,8}};
    assert(reads==wanted);
    assert(!std::memcmp(saved.data(),original.getCode(),saved.size()));
    const auto before=clones.getSize();
    for(const std::vector<u8> invalid: {std::vector<u8>{0xe8,0,0,0,0,0xc3},
        {0x48,0x8b,0x00,0xc3},{0x48,0x89,0x07,0xc3},{0x48,0x8b,0x07}}) {
        assert(!Shader::BuildSrtWatchClone(clones,invalid.data(),invalid.size(),
            0x900000008,192,&Observe,sites,capacity));assert(clones.getSize()==before);
    }
    assert(!Shader::BuildSrtWatchClone(clones,original.getCode(),original.getSize(),
        0x900000008,192,&Observe,sites,before+1));assert(clones.getSize()==before);

    char temp[]="/private/tmp/bb-srt-watch-test-XXXXXX";const auto* root=mkdtemp(temp);assert(root);
    const std::filesystem::path trigger=std::filesystem::path(root)/"trigger";
    setenv("BB_CAPTURE_NATIVE","1",1);setenv("BB_CAPTURE_SRT_READS","1",1);
    setenv("BB_WATCH","0x900000008,192",1);setenv("BB_CAPTURE_DIR",root,1);
    setenv("BB_CAPTURE_TRIGGER",trigger.c_str(),1);
    Shader::PersistentSrtInfo info{fn,original.getSize(),32};
    assert(!Shader::TryRunWatchedSrt(info,reinterpret_cast<u32*>(ud),actual.data(),123,0));
    std::ofstream(trigger)<<"1";
    VideoCore::FrameCapture::OnFlip(123);VideoCore::FrameCapture::FrameBoundary(10);
    actual.fill(0x12345678);
    assert(Shader::TryRunWatchedSrt(info,reinterpret_cast<u32*>(ud),actual.data(),123,0));
    assert(actual==expected && info.walker_func==fn && info.walker_func_size==saved.size());
    assert(!std::memcmp(saved.data(),original.getCode(),saved.size()));

    // The real access-violation dispatcher must still repair a bad guest load in
    // the private clone. This prevents diagnostic-only faults becoming crashes.
    struct sigaction action{},old{};action.sa_sigaction=&Fault;action.sa_flags=SA_SIGINFO;
    sigemptyset(&action.sa_mask);assert(sigaction(SIGSEGV,&action,&old)==0);
    Xbyak::CodeGenerator bad(capacity);bad.mov(rdi,ptr[rdi]);bad.mov(r10d,dword[rdi]);
    bad.mov(dword[rsi],r10d);bad.ret();bad.ready();
    Shader::PersistentSrtInfo bad_info{bad.getCode<Shader::PFN_SrtWalker>(),bad.getSize(),32};
    ud[0]=0;actual[0]=~u32{0};
    assert(Shader::TryRunWatchedSrt(bad_info,reinterpret_cast<u32*>(ud),actual.data(),124,3));
    assert(actual[0]==0);assert(sigaction(SIGSEGV,&old,nullptr)==0);

    Xbyak::CodeGenerator branch(capacity);Xbyak::Label end;
    branch.jmp(end);branch.nop();branch.L(end);branch.ret();branch.ready();
    Shader::PersistentSrtInfo unsupported{branch.getCode<Shader::PFN_SrtWalker>(),branch.getSize(),32};
    assert(Shader::TryRunWatchedSrt(unsupported,reinterpret_cast<u32*>(ud),actual.data(),125,3));
    VideoCore::FrameCapture::FrameBoundary(11);
    std::string text;
    for(const auto& file:std::filesystem::directory_iterator(root))if(file.path().extension()==".txt") {
        std::ifstream in(file.path());text.append(std::istreambuf_iterator<char>(in),{});
    }
    assert(text.find("\"source\":\"srt_cpu\"")!=std::string::npos);
    assert(text.find("\"op\":\"srt_walk_coverage\",\"calls\":3,\"covered\":2,\"uncovered\":1")!=std::string::npos);
    assert(munmap(data,4096)==0);
    std::puts("PASS: SRT clone matches output/canaries/flags, exact overlapping loads; cached original unchanged; real fault repair and uncovered fallback");
}
