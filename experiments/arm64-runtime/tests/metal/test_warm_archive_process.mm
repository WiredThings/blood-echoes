// SPDX-License-Identifier: GPL-2.0-or-later
// Authored shaders only. One process per mode. Caller HOME/TMPDIR must be private state.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/renderer_metal/mtl_warm_archive_identity.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "test_gpu_runtime_stubs.cpp"
static std::string Read(const std::filesystem::path& path) {
 std::ifstream file(path,std::ios::binary);return {std::istreambuf_iterator<char>(file),{}};
}
static int RenderControl(Metal::Device& device,const std::filesystem::path& folder,
                         const std::string& mode,bool expected_loaded,int expected_hits) {
 auto native=device.GetImpl().device;NSError* error=nil;
 auto options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;
 if(@available(macOS 15.0,*))options.mathMode=MTLMathModeSafe;else options.fastMathEnabled=NO;
 auto vl=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;vertex float4 main0(uint i [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0.5,1);}"
 options:options error:&error];
 auto fl=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;fragment float4 main0(){return float4(0.25,0.5,0.75,1);}"
 options:options error:&error];assert(vl&&fl);
 Metal::CompiledShader vs,fs;vs.stage=Metal::ShaderStage::Vertex;fs.stage=Metal::ShaderStage::Fragment;
 vs.library=(__bridge_retained void*)vl;vs.function=(__bridge_retained void*)[vl newFunctionWithName:@"main0"];
 fs.library=(__bridge_retained void*)fl;fs.function=(__bridge_retained void*)[fl newFunctionWithName:@"main0"];
 Vulkan::GraphicsPipelineKey key{};
 key.stage_hashes[u32(Shader::SwStage::Vertex)]=0x12345;key.stage_hashes[u32(Shader::SwStage::Fragment)]=0x6789;
 key.prim_type=AmdGpu::PrimitiveType::TriangleList;key.num_samples=1;key.num_color_attachments=1;key.mrt_mask=1;
 key.color_buffers[0].data_format=AmdGpu::DataFormat::Format8_8_8_8;
 const bool changed=mode=="render-descriptor";
 key.color_buffers[0].num_format=changed?AmdGpu::NumberFormat::Srgb:AmdGpu::NumberFormat::Unorm;
 key.write_masks[0]=vk::ColorComponentFlagBits::eR|vk::ColorComponentFlagBits::eG|vk::ColorComponentFlagBits::eB|vk::ColorComponentFlagBits::eA;
 Metal::PsoCache cache{device,folder,false};assert(cache.ArchiveLoaded()==expected_loaded);
 auto pipeline=cache.GetRenderPipeline(key,&vs,&fs,{});assert(pipeline&&pipeline->state);
 assert(expected_hits<0||cache.GetCounters().archive_hits.load()==static_cast<unsigned>(expected_hits));
 const auto path=cache.ArchivePath();const auto before=Read(path);
 if(mode=="render-seed"){assert(before.empty());assert(cache.Flush());assert(!Read(path).empty());}
 if(mode=="render-load"){assert(!before.empty());assert(cache.Flush());assert(Read(path)==before);}
 cache.ReleaseWarmArchive();assert(cache.GetRenderPipeline(key,&vs,&fs,{})==pipeline);
 auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:changed?MTLPixelFormatRGBA8Unorm_sRGB:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
 td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
 auto texture=[native newTextureWithDescriptor:td];assert(texture);
 auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
 pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 auto command=[[native newCommandQueue] commandBuffer];auto encoder=[command renderCommandEncoderWithDescriptor:pass];
 [encoder setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)pipeline->state];
 [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];[command commit];[command waitUntilCompleted];
 assert(command.status==MTLCommandBufferStatusCompleted);unsigned char pixel[4]{};
 [texture getBytes:pixel bytesPerRow:4 fromRegion:MTLRegionMake2D(4,4,1,1) mipmapLevel:0];
 if(changed){assert(abs(int(pixel[0])-137)<=1&&abs(int(pixel[1])-188)<=1&&abs(int(pixel[2])-225)<=1&&pixel[3]==255);}
 else assert(pixel[0]==64&&pixel[1]==128&&pixel[2]==191&&pixel[3]==255);
 if(mode!="render-seed")assert(Read(path)==before);
 std::printf("{\"mode\":\"%s\",\"archive_loaded\":%s,\"strict_archive_hits\":%llu,\"readback_after_release\":true,\"pid\":%d}\n",mode.c_str(),expected_loaded?"true":"false",(unsigned long long)cache.GetCounters().archive_hits.load(),getpid());
 return 0;
}
int main(int argc,char**argv) { @autoreleasepool {
 assert(argc==5);std::string mode=argv[1];std::filesystem::path folder=argv[2];
 const bool expected_loaded=std::atoi(argv[3]);const int expected_hits=std::atoi(argv[4]);
 const auto state=std::filesystem::path("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state");
 assert(folder.is_absolute()&&folder.parent_path().parent_path()==state);
 assert(!std::filesystem::is_symlink(folder));std::filesystem::create_directories(folder);
 // Identity negative controls use explicit authored GPU/OS strings; production uses actual values.
 const std::string token(64,'a');const auto scope_id=Metal::WarmArchiveNamespace(token,"gpu-A","os-A","safe");
 assert(scope_id.size()==64&&scope_id!=Metal::WarmArchiveNamespace(token,"gpu-B","os-A","safe"));
 assert(scope_id!=Metal::WarmArchiveNamespace(token,"gpu-A","os-B","safe"));
 assert(scope_id!=Metal::WarmArchiveNamespace(token,"gpu-A","os-A","relaxed"));
 assert(Metal::WarmArchiveNamespace("bad","gpu-A","os-A","safe").empty());
 const auto source_options=Metal::WarmArchiveLibraryOptions("safe","off",token);
 assert(source_options==Metal::WarmArchiveLibraryOptions("safe","record",token));
 assert(source_options==Metal::WarmArchiveLibraryOptions("safe","load","bad"));
 const auto local_options=Metal::WarmArchiveLibraryOptions("safe","load",token);
 assert(local_options!=source_options);
 assert(local_options!=Metal::WarmArchiveLibraryOptions("safe","load",std::string(64,'b')));
 assert(Metal::WarmArchiveNamespace(token,"gpu-A","os-A",source_options)!=
        Metal::WarmArchiveNamespace(token,"gpu-A","os-A",local_options));
 Metal::Device device;auto native=device.GetImpl().device;
 if(mode.starts_with("render-"))return RenderControl(device,folder,mode,expected_loaded,expected_hits);
 const bool changed=mode=="source"||mode=="write-fail"||mode=="parent-write-fail";
 NSString* source=changed?@"#include <metal_stdlib>\nusing namespace metal;kernel void main0(device uint* p [[buffer(0)]],uint i [[thread_position_in_grid]]){p[i]=(i*17u)^0x87654321u;}"
 :@"#include <metal_stdlib>\nusing namespace metal;kernel void main0(device uint* p [[buffer(0)]],uint i [[thread_position_in_grid]]){p[i]=(i*17u)^0x12345678u;}";
 NSError* error=nil;auto options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;
 if(@available(macOS 15.0,*))options.mathMode=MTLMathModeSafe;else options.fastMathEnabled=NO;
 const std::string effective=source.UTF8String;
 auto admitted=Metal::LocalAot::Try(native,effective,"main0",false);
 auto library=admitted.library?admitted.library:[native newLibraryWithSource:source options:options error:&error];
 if(!library){std::fprintf(stderr,"%s\n",error.localizedDescription.UTF8String);return 3;}
 auto function=admitted.function?admitted.function:[library newFunctionWithName:@"main0"];assert(function);
 if(mode=="local-record") {
  auto recorded=Metal::LocalAot::Try(native,effective,"main0",false,true,false,function);
  assert(recorded.recorded);
 }
 if(mode=="local-seed"||mode=="local-load")assert(admitted.reason=="hit");
 Metal::CompiledShader shader;shader.stage=Metal::ShaderStage::Compute;shader.entry="main0";
 shader.local_size={mode=="descriptor"?1024u:16u,1,1};
 shader.library=(__bridge_retained void*)library;shader.function=(__bridge_retained void*)function;
 Metal::PsoCache cache{device,folder,false};assert(cache.ArchiveLoaded()==expected_loaded);
 Vulkan::ComputePipelineKey key{};key.value=0x13579bdf;
 const auto pipeline=cache.GetComputePipeline(key,&shader);assert(pipeline&&pipeline->state);
 assert(expected_hits<0||cache.GetCounters().archive_hits.load()==static_cast<unsigned>(expected_hits));
 const auto path=cache.ArchivePath();const auto before=Read(path);
 if(mode=="seed"||mode=="local-seed") {assert(!expected_loaded&&before.empty());assert(cache.Flush());assert(!Read(path).empty());}
 if(mode=="load"||mode=="local-load") {assert(expected_loaded&&!before.empty());assert(cache.Flush());assert(Read(path)==before);}
 if(mode=="parent-write-fail") {
  assert(!before.empty());auto saved=folder;saved+=".saved";assert(!std::filesystem::exists(saved));
  std::filesystem::rename(folder,saved);{std::ofstream blocker(folder);blocker<<"authored ENOTDIR blocker";}
  const bool wrote=cache.Flush();assert(Read(folder)=="authored ENOTDIR blocker");
  std::filesystem::remove(folder);std::filesystem::rename(saved,folder);
  assert(!wrote&&Read(path)==before);
 }
 if(mode=="write-fail") {
  // Fail the atomic publication, keeping the serializer's working directory valid.
  // An ENOTDIR parent also disrupts AGX's private intermediate files and can abort
  // inside the driver before returning an NSError; that is a separate failure.
  assert(!before.empty());auto saved=path;saved+=".saved";assert(!std::filesystem::exists(saved));
  std::filesystem::rename(path,saved);assert(std::filesystem::create_directory(path));
  const auto marker=path/"authored-publication-blocker";{std::ofstream blocker(marker);blocker<<"preserve";}
  const bool wrote=cache.Flush();assert(Read(marker)=="preserve"&&Read(saved)==before);
  std::filesystem::remove(marker);std::filesystem::remove(path);std::filesystem::rename(saved,path);
  assert(!wrote&&Read(path)==before);
  for(const auto& entry:std::filesystem::directory_iterator(folder))
   assert(!entry.path().filename().string().starts_with(path.filename().string()+".tmp-"));
 }
 cache.ReleaseWarmArchive();assert(cache.GetComputePipeline(key,&shader)==pipeline);
 // The released archive cannot be needed for execution: real authored output must survive.
 auto out=[native newBufferWithLength:64*sizeof(uint32_t) options:MTLResourceStorageModeShared];
 auto queue=[native newCommandQueue];auto command=[queue commandBuffer];auto encoder=[command computeCommandEncoder];
 [encoder setComputePipelineState:(__bridge id<MTLComputePipelineState>)pipeline->state];
 [encoder setBuffer:out offset:0 atIndex:0];
 [encoder dispatchThreads:MTLSizeMake(64,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)];
 [encoder endEncoding];[command commit];[command waitUntilCompleted];assert(command.status==MTLCommandBufferStatusCompleted);
 for(uint32_t i=0;i<64;i++)assert(((uint32_t*)out.contents)[i]==((i*17u)^(changed?0x87654321u:0x12345678u)));
 if(mode!="seed"&&mode!="local-seed")assert(Read(path)==before);
 std::printf("{\"mode\":\"%s\",\"archive_loaded\":%s,\"strict_archive_hits\":%llu,\"readback_after_release\":true,\"pid\":%d}\n",mode.c_str(),expected_loaded?"true":"false",(unsigned long long)cache.GetCounters().archive_hits.load(),getpid());
 return 0;
}}
