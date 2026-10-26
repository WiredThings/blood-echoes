// SPDX-License-Identifier: GPL-2.0-or-later
// Synthetic CPU/GPU data only; no game, archive, save, settings or cache access.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_copy_watch.h"
#include <cassert>
#include <array>
#include <cstring>
#include <fstream>
#include <iterator>
#include <cstdio>

static constexpr const char* Source=R"MSL(
#include <metal_stdlib>
using namespace metal;
struct Buffer {uint data[1];};struct Root {ulong data[1];};
kernel void main0(const device Buffer& meta [[buffer(0)]],device Root& bda_pagetable [[buffer(1)]],uint tid [[thread_position_in_grid]]) {
    if(tid<64u) {
        ulong a=ulong(meta.data[0])|(ulong(meta.data[1])<<32ul);
        ulong b=ulong(meta.data[2])|(ulong(meta.data[3])<<32ul);
        ulong c=ulong(meta.data[4])|(ulong(meta.data[5])<<32ul);
        uint value=*(reinterpret_cast<device uint*>(a+ulong(tid)*4ul));
        *(reinterpret_cast<device uint*>(b+ulong(tid)*4ul))=value;
        *(reinterpret_cast<device uint*>(c+ulong(tid)*4ul))=value;
    }
}
)MSL";
static id<MTLLibrary> Library(id<MTLDevice> device,const std::string& source,bool relaxed=false) {
    MTLCompileOptions* options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;
    options.mathMode=relaxed?MTLMathModeRelaxed:MTLMathModeSafe;
    NSError* error=nil;
    id<MTLLibrary> lib=[device newLibraryWithSource:[NSString stringWithUTF8String:source.c_str()] options:options error:&error];
    if(!lib)std::fprintf(stderr,"MSL: %s\n",error.localizedDescription.UTF8String);
    assert(lib);return lib;
}
int main(int argc,char** argv) {@autoreleasepool {
    const std::string original=Source;
    const auto rewritten=Metal::RewriteCopyWatch(original);
    assert(rewritten.ok && rewritten.physical_reads==1 && rewritten.physical_writes==2);
    assert(original==Source);
    for(const auto& bad:{std::string("kernel void k(){}"),original+"\nkernel void other(){}",original+"\n// [[buffer(30)]]"})
        assert(!Metal::RewriteCopyWatch(bad).ok);
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();assert(device);
    id<MTLCommandQueue> queue=[device newCommandQueue];
    auto buffer=[&](NSUInteger size){id<MTLBuffer> b=[device newBufferWithLength:size options:MTLResourceStorageModeShared];assert(b);std::memset(b.contents,0,size);return b;};
    id<MTLBuffer> src=buffer(512),dst=buffer(512),mirror=buffer(512),other=buffer(512),meta=buffer(256),root=buffer(2048),leaf0=buffer(32768),leaf1=buffer(32768),counter=buffer(256);
    for(unsigned n=0;n<128;++n)static_cast<uint32_t*>(src.contents)[n]=n*17+3;
    const std::array<uint64_t,3> addresses={src.gpuAddress,dst.gpuAddress,mirror.gpuAddress};std::memcpy(meta.contents,addresses.data(),sizeof(addresses));
    // Include NaN payloads, infinities, signed zero and subnormals in the
    // float copy cases; compare every output byte under both compiler modes.
    const uint32_t special[]={0x7fc00001,0x7f800000,0xff800000,0x80000000,1,0xffffffff};
    std::memcpy(src.contents,special,sizeof(special));
    for(bool relaxed:{false,true})for(bool float_copy:{false,true}) {@autoreleasepool {
        std::string variant=original;
        if(float_copy) {
            for(size_t at=0;(at=variant.find("device uint*>",at))!=variant.npos;at+=14)
                variant.replace(at,13,"device float*>");
            const auto value=variant.find("uint value=");assert(value!=variant.npos);variant.replace(value,11,"float value=");
        }
        const auto watched_source=Metal::RewriteCopyWatch(variant);assert(watched_source.ok);
        const auto baseline_lib=Library(device,variant,relaxed),watch_lib=Library(device,watched_source.source,relaxed);
        NSError* error=nil;
        id<MTLComputePipelineState> baseline=[device newComputePipelineStateWithFunction:[baseline_lib newFunctionWithName:@"main0"] error:&error];assert(baseline);
        id<MTLComputePipelineState> watched=[device newComputePipelineStateWithFunction:[watch_lib newFunctionWithName:@"main0"] error:&error];assert(watched);
    for(bool two:{false,true})for(unsigned target=0;target<4;++target) {
        auto* table=static_cast<uint64_t*>(root.contents);std::memset(table,0,2048);
        std::memset(leaf0.contents,0,32768);std::memset(leaf1.contents,0,32768);
        const uint64_t a=target==0?src.gpuAddress:target==1?dst.gpuAddress:target==2?other.gpuAddress:0;
        const uint64_t b=target==1?mirror.gpuAddress:0;
        if(two){table[0]=leaf0.gpuAddress;table[1]=leaf1.gpuAddress;static_cast<uint64_t*>(leaf0.contents)[64]=a;static_cast<uint64_t*>(leaf1.contents)[64]=b;}
        else {table[64]=a;table[64+128]=b;}
        std::array<uint8_t,512> expected{},expected_mirror{};
        for(bool observe:{false,true}) {
            std::memset(dst.contents,0x9a,512);std::memset(mirror.contents,0x6b,512);
            Metal::CopyWatchData data{};data.guest=0x100000;data.bytes=192;data.shift=14;
            data.plane_offset=two?1:128;data.two_level=two;std::memcpy(counter.contents,&data,sizeof(data));
            id<MTLCommandBuffer> commands=[queue commandBuffer];id<MTLComputeCommandEncoder> enc=[commands computeCommandEncoder];
            [enc setComputePipelineState:observe?watched:baseline];
            [enc setBuffer:meta offset:0 atIndex:0];[enc setBuffer:root offset:0 atIndex:1];
            if(observe)[enc setBuffer:counter offset:0 atIndex:Metal::CopyWatchSlot];
            if(observe)[enc setBuffer:root offset:0 atIndex:Metal::CopyWatchRootSlot];
            for(id<MTLBuffer> b in @[src,dst,mirror,other,leaf0,leaf1])
                [enc useResource:b usage:MTLResourceUsageRead|MTLResourceUsageWrite];
            [enc dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];
            [enc endEncoding];[commands commit];[commands waitUntilCompleted];assert(commands.status==MTLCommandBufferStatusCompleted);
            if(!observe){std::memcpy(expected.data(),dst.contents,512);std::memcpy(expected_mirror.data(),mirror.contents,512);}
            else {
                assert(!std::memcmp(expected.data(),dst.contents,512));assert(!std::memcmp(expected_mirror.data(),mirror.contents,512));
                std::memcpy(&data,counter.contents,sizeof(data));assert(!data.overflow);
                assert(data.mapping==(target==1?3:target==3?0:1));
                if(target==0){assert(data.flags==1 && data.reads==48 && !data.writes);}
                else if(target==1){assert(data.flags==2 && data.writes==96 && !data.reads && data.current_hits==48 && data.guest_hits==48);}
                else assert(!data.flags && !data.reads && !data.writes);
            }
        }
    }
    }}
    // Optional private local corpus: compile rewritten cache text without modifying it.
    for(int n=1;n<argc;++n){std::ifstream f(argv[n]);std::string source{std::istreambuf_iterator<char>(f),{}};auto r=Metal::RewriteCopyWatch(source);assert(r.ok);Library(device,r.source);std::printf("corpus: %s r=%u w=%u\n",argv[n],r.reads,r.writes);}
    std::puts("PASS: pointer diagnostics preserve complete output and canaries; uint/float and Safe/Relaxed modes; flat/two-level source, destination, guest mirror, unrelated and unmapped watches");
}}
