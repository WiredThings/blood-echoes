// SPDX-License-Identifier: GPL-2.0-or-later
// Authored ShaderCompiler/PsoCache and GPU pixel controls; no guest Runtime.
// Constructor archive preference and legacy-main coexistence use real archives.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <array>
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <thread>
#include <unordered_map>
#include <unistd.h>
#include "bbgpu.h"
#include "common/elf_info.h"
#include "common/path_util.h"
#include "common/serdes.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/specialization.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"

namespace {
unsigned checks=0;
void Check(bool b,const char* s){++checks;if(!b){std::fprintf(stderr,"FAIL %s\n",s);std::abort();}}
std::vector<u8> Bytes(const std::filesystem::path&p){std::ifstream f(p,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};}
std::vector<u32> Spirv(const std::filesystem::path&p){auto b=Bytes(p);Check(b.size()>20&&b.size()%4==0,"authored SPV");std::vector<u32> w(b.size()/4);memcpy(w.data(),b.data(),b.size());return w;}
using Key=VideoCore::GraphicsPipelineKey;
constexpr u64 VS_META=0x71504201,FS_META=0x71504202;
Key BaseKey() {
 Key k{};k.stage_hashes[u32(Shader::SwStage::Vertex)]=VS_META;
 k.stage_hashes[u32(Shader::SwStage::Fragment)]=FS_META;
 k.prim_type=AmdGpu::PrimitiveType::TriangleList;k.num_samples=1;
 k.num_color_attachments=1;k.mrt_mask=1;
 k.z_format=AmdGpu::DepthBuffer::ZFormat::Z32Float;
 k.stencil_format=AmdGpu::DepthBuffer::StencilFormat::Stencil8;
 k.color_buffers[0].data_format=AmdGpu::DataFormat::Format8_8_8_8;
 k.color_buffers[0].num_format=AmdGpu::NumberFormat::Unorm;
 k.write_masks[0]=vk::ColorComponentFlagBits::eR|vk::ColorComponentFlagBits::eG|
                  vk::ColorComponentFlagBits::eB|vk::ColorComponentFlagBits::eA;
 // Formats are indexed by attribute-order, locations by serialized vertex metadata.
 k.vertex_buffer_formats[0]=vk::Format::eR32G32Sfloat;
 k.vertex_buffer_formats[1]=vk::Format::eR32G32B32A32Sfloat;
 k.logic_op=AmdGpu::ColorControl::LogicOp::Copy;return k;
}
Key NoDepthKey(){auto k=BaseKey();k.z_format=AmdGpu::DepthBuffer::ZFormat::Invalid;
 k.stencil_format=AmdGpu::DepthBuffer::StencilFormat::Invalid;return k;}
Key VelocityKey(){auto k=BaseKey();k.color_buffers[0].data_format=AmdGpu::DataFormat::Format16_16_16_16;
 k.color_buffers[0].num_format=AmdGpu::NumberFormat::Float;return k;}
Metal::GraphicsSerialization Layout(){Metal::GraphicsSerialization d;
 d.vertex_attributes={{2,false,1},{7,true,3}};return d;}
std::vector<u8> Draw(Metal::Device& device,const Metal::RenderPipeline& p,bool half) {
 auto native=device.GetImpl().device;
 auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
  half?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
 auto color=[native newTextureWithDescriptor:td];Check(color,"native color allocation");
 auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;
 pass.colorAttachments[0].loadAction=MTLLoadActionClear;
 pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLTexture> depth=nil;
 if(p.depth_pixel_format||p.stencil_pixel_format) {
  auto dt=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
    MTLPixelFormatDepth32Float_Stencil8 width:8 height:8 mipmapped:NO];
  dt.storageMode=MTLStorageModePrivate;dt.usage=MTLTextureUsageRenderTarget;
  depth=[native newTextureWithDescriptor:dt];Check(depth,"native depth/stencil allocation");
  pass.depthAttachment.texture=depth;pass.stencilAttachment.texture=depth;
  pass.depthAttachment.loadAction=MTLLoadActionClear;pass.stencilAttachment.loadAction=MTLLoadActionClear;
  pass.depthAttachment.storeAction=MTLStoreActionDontCare;pass.stencilAttachment.storeAction=MTLStoreActionDontCare;
  pass.depthAttachment.clearDepth=1;
 }
 const float vertices[]={-1,-1,3,-1,-1,3};
 const float value[]={-.375f,.15625f,.5625f,1.f};
 auto positions=[native newBufferWithBytes:vertices length:sizeof(vertices) options:MTLResourceStorageModeShared];
 auto velocities=[native newBufferWithBytes:value length:sizeof(value) options:MTLResourceStorageModeShared];
 auto queue=[native newCommandQueue];auto cb=[queue commandBuffer];
 auto e=[cb renderCommandEncoderWithDescriptor:pass];Check(e,"native render encoder");
 [e setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)p.state];
 [e setVertexBuffer:positions offset:0 attributeStride:2*sizeof(float) atIndex:30];
 [e setVertexBuffer:velocities offset:0 attributeStride:4*sizeof(float) atIndex:29];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
 [e endEncoding];[cb commit];[cb waitUntilCompleted];
 Check(cb.status==MTLCommandBufferStatusCompleted,"real GPU completed");
 const size_t row=8*(half?8:4);std::vector<u8> bytes(row*8);
 [color getBytes:bytes.data() bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
 for(size_t y=0;y<8;++y)for(size_t x=0;x<8;++x) {
  const auto at=(y*8+x)*(half?8:4);
  if(half) {
   const std::array<u16,4> expected{0xb600,0x3100,0x3880,0x3c00};
   Check(!std::memcmp(bytes.data()+at,expected.data(),8),"original signed fractional velocity half oracle");
  } else {
   const std::array<u8,4> expected{0,40,143,255};
   Check(!std::memcmp(bytes.data()+at,expected.data(),4),"no-depth/original pixel oracle");
  }
 }
 return bytes;
}
size_t calls=0,ordinary=0,strict=0;
using Factory=id<MTLRenderPipelineState>(*)(id,SEL,MTLRenderPipelineDescriptor*,MTLPipelineOption,MTLAutoreleasedRenderPipelineReflection*,NSError*__autoreleasing*);
Factory original=nullptr;
id<MTLRenderPipelineState> Observe(id self,SEL sel,MTLRenderPipelineDescriptor*d,MTLPipelineOption o,MTLAutoreleasedRenderPipelineReflection*r,NSError*__autoreleasing*e){++calls;if(o&MTLPipelineOptionFailOnBinaryArchiveMiss)++strict;else ++ordinary;return original(self,sel,d,o,r,e);}
struct Observer{Method m;IMP prev;Observer(id device){auto c=object_getClass(device);auto s=@selector(newRenderPipelineStateWithDescriptor:options:reflection:error:);auto inherited=class_getInstanceMethod(c,s);Check(inherited,"native factory");prev=method_getImplementation(inherited);original=reinterpret_cast<Factory>(prev);class_addMethod(c,s,prev,method_getTypeEncoding(inherited));m=class_getInstanceMethod(c,s);method_setImplementation(m,reinterpret_cast<IMP>(&Observe));}~Observer(){method_setImplementation(m,prev);}};
}
int main(int argc,char**argv){@autoreleasepool{
 Check(argc==3,"mode/root");std::string mode=argv[1];auto root=std::filesystem::canonical(argv[2]);
 Check(root.parent_path()=="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state","new Shared author state only");
 const bool seed=mode.ends_with("seed"),local=mode.starts_with("local"),negative=mode=="negative",corrupt=mode=="corrupt"||mode=="index-invalid";const bool legacy=mode.starts_with("legacy");
 Check(seed||mode.ends_with("warm")||negative||corrupt,"known mode");
 Metal::Device device;Observer observer(device.GetImpl().device);
 Metal::ShaderCompiler compiler(device,root/"msl",Metal::FloatRules::Strict);
 auto vs=compiler.Compile(Spirv(root/"authored.vert.spv"),VS_META,"authored vs");auto fs=compiler.Compile(Spirv(root/"authored.frag.spv"),FS_META,"authored fs");
 Check(vs.status==Metal::CompileStatus::Ok&&fs.status==Metal::CompileStatus::Ok,"actual source module success");
 Check(vs.shader->persistent_identity.valid()&&fs.shader->persistent_identity.valid(),"actual final function provenance valid");
 const auto wanted=local?Metal::PersistentLibraryRoute::LocalMetallib:Metal::PersistentLibraryRoute::Source;
 Check(vs.shader->persistent_identity.route==wanted&&fs.shader->persistent_identity.route==wanted,"actual library route, not requested mode");
 auto id=vs.shader->persistent_identity.hex()+"\n"+fs.shader->persistent_identity.hex()+"\n";
 auto idpath=root/(local?"local-identities.txt":"source-identities.txt");
 if(seed){std::ofstream f(idpath);f<<id;}else if(!negative){auto b=Bytes(idpath);Check(std::string(b.begin(),b.end())==id,"fresh vs disk MSL final function identities equal");}
 if(!seed)Check(compiler.GetCounters().cache_hits.load()==2,"both modules actually use MSL disk cache");
 Metal::PsoCache pso(device,root/"artifacts");if(seed&&!legacy)pso.ReleaseWarmArchive();pso.SetArtifactPreparation(seed&&!legacy,true);
 if(legacy)Check(pso.ArchiveLoaded()==!seed,"usable old main priority preserved");
 auto key=NoDepthKey();auto layout=Layout();Metal::RenderPipelineInput input{.vertex_attributes=layout.vertex_attributes};
 const auto* pipeline=pso.GetRenderPipeline(key,vs.shader.get(),fs.shader.get(),input);Check(pipeline,"actual original game render factory ready");
 const auto&c=pso.GetCounters();
 if(legacy){Check(c.artifact_hits==0&&c.artifact_deferred==1&&ordinary==(seed?1u:0u)&&strict==(seed?0u:1u),"legacy main path retained, strict warm hit");if(seed)Check(pso.Flush(),"actual legacy main archive published");}
 else if(seed)Check(c.artifact_built==1&&c.artifact_failed==0&&c.artifact_hits==0,"strictly verified published artifact");
 else if(!corrupt&&!negative)Check(c.artifact_hits==1&&c.artifact_compile_attempts==0&&ordinary==0&&strict==1,"independent runtime strict artifact hit, no ordinary factory");
 else if(corrupt)Check(c.artifact_hits==0&&c.artifact_deferred==1&&ordinary==1,"corrupt artifact rejected, original fallback once");
 const auto oldcalls=calls;const auto attempts=c.artifact_attempts.load();
 for(int n=0;n<16;++n)Check(pso.GetRenderPipeline(key,vs.shader.get(),fs.shader.get(),input)==pipeline,"in-memory map hit");
 Check(calls==oldcalls&&c.artifact_attempts==attempts,"map hits do not probe disk artifact or native driver");
 auto pixels=Draw(device,*pipeline,false);Check(pixels.size()==256,"actual GPU pixel oracle");
 if(negative){
  auto changed=key;changed.write_masks[0]=vk::ColorComponentFlagBits::eR|vk::ColorComponentFlagBits::eG|vk::ColorComponentFlagBits::eB;
  auto before=c.artifact_hits.load();auto a=c.artifact_attempts.load();auto o=ordinary;
  Check(pso.GetRenderPipeline(changed,vs.shader.get(),fs.shader.get(),input),"changed exact descriptor original fallback succeeds");
  Check(c.artifact_hits==before&&c.artifact_attempts==a+1&&ordinary==o+1,"descriptor change cannot alias old artifact");
  Metal::PsoCache unknown(device,root/"artifacts");unknown.ReleaseWarmArchive();auto keep=vs.shader->persistent_identity;vs.shader->persistent_identity={};
  auto missing=unknown.GetRenderPipeline(key,vs.shader.get(),fs.shader.get(),input);vs.shader->persistent_identity=keep;
  Check(missing&&unknown.GetCounters().artifact_attempts==0,"missing provenance keeps original behavior, no guessed key");
 }
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"msl_hits\":%llu,\"artifact_built\":%llu,\"artifact_hits\":%llu,\"artifact_deferred\":%llu,\"ordinary\":%zu,\"strict\":%zu,\"route\":%u,\"pixels\":true}\n",mode.c_str(),getpid(),checks,(unsigned long long)compiler.GetCounters().cache_hits.load(),(unsigned long long)c.artifact_built.load(),(unsigned long long)c.artifact_hits.load(),(unsigned long long)c.artifact_deferred.load(),ordinary,strict,unsigned(wanted));
 return 0;
}}
