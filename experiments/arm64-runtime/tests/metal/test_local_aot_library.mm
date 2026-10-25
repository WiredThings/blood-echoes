// SPDX-License-Identifier: GPL-2.0-or-later
// Authored sources, real Metal output. Does not launch a compiler or a game.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <bit>
#include <cassert>
#include <cstring>
#include <filesystem>
#include <fstream>
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "test_gpu_runtime_stubs.cpp"
int main(int argc,char**argv){@autoreleasepool{
 assert(argc==5);const std::filesystem::path dir=argv[1];const bool expected_hit=std::atoi(argv[2]);
 const bool relaxed=std::string_view(argv[3])=="relaxed",changed=std::string_view(argv[4])=="changed";
 assert(dir.is_absolute()&&std::filesystem::is_directory(dir));
 const std::string source=std::string("#include <metal_stdlib>\nusing namespace metal;kernel void main0(device uint* p [[buffer(0)]],uint i [[thread_position_in_grid]]){p[2*i]=(i*17u)^")+(changed?"0x87654321u":"0x12345678u")+";p[2*i+1]=as_type<uint>(float(i)*0.25f+0.5f);}";
 Metal::Device device;auto native=device.GetImpl().device;auto local=Metal::LocalAot::Try(native,source,"main0",relaxed);
 assert(bool(local.library)==expected_hit);id<MTLLibrary> library=local.library;NSError* error=nil;
 if(!library){auto options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;options.mathMode=relaxed?MTLMathModeRelaxed:MTLMathModeSafe;options.mathFloatingPointFunctions=MTLMathFloatingPointFunctionsFast;library=[native newLibraryWithSource:@(source.c_str()) options:options error:&error];}
 assert(library);auto function=local.function?:[library newFunctionWithName:@"main0"];assert(function);
 if(!local.library)local=Metal::LocalAot::Try(native,source,"main0",relaxed,true,false,function);
 auto pipeline=[native newComputePipelineStateWithFunction:function error:&error];assert(pipeline);
 auto out=[native newBufferWithLength:128*sizeof(uint32_t) options:MTLResourceStorageModeShared];assert(out);
 auto command=[[native newCommandQueue] commandBuffer];auto encoder=[command computeCommandEncoder];
 [encoder setComputePipelineState:pipeline];[encoder setBuffer:out offset:0 atIndex:0];[encoder dispatchThreads:MTLSizeMake(64,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)];[encoder endEncoding];[command commit];[command waitUntilCompleted];assert(command.status==MTLCommandBufferStatusCompleted);
 for(uint32_t i=0;i<64;++i){assert(((uint32_t*)out.contents)[2*i]==((i*17u)^(changed?0x87654321u:0x12345678u)));assert(((uint32_t*)out.contents)[2*i+1]==std::bit_cast<uint32_t>(float(i)*.25f+.5f));}
 NSDictionary* result=@{@"hit":@(expected_hit),@"recorded":@(local.recorded),@"key":@(local.key.c_str()),@"reason":@(local.reason.c_str()),@"source_sha256":@(Metal::LocalAot::Sha(source.data(),source.size()).c_str()),@"readback_correct":@YES};
 auto data=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];assert([data writeToFile:@((dir/"result.json").c_str()) atomically:YES]);
 printf("PASS authored local AOT hit=%d record=%d reason=%s, integer and exact-float GPU readback\n",int(expected_hit),int(local.recorded),local.reason.c_str());
}}
