// SPDX-License-Identifier: GPL-2.0-or-later
// Actual IR->SPIR-V->MSL plus pixel comparison with Metal's native 1D sampler.
#import <Metal/Metal.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "video_core/renderer_metal/mtl_shader.h"

static std::vector<u32> Emit(bool array,std::string_view op, const Shader::Profile& profile) {
 using namespace Shader;
 Info info{};info.hw_stage=HwStage::Fragment;info.sw_stage=SwStage::Fragment;
 info.flattened_ud_buf.resize(16);
 info.has_storage_images=op=="write" || op=="atomic";
 info.has_image_gather=op=="gather" || op=="runtime-offset" || op=="dref-gather";
 auto sharp=AmdGpu::Image::Null(false);sharp.base_address=1;sharp.width=3;sharp.height=0;
 sharp.type=u32(array?AmdGpu::ImageType::Color1DArray:AmdGpu::ImageType::Color1D);
 sharp.depth=array?1:0;sharp.data_format=(op=="atomic" || op.starts_with("dref"))?4:10;sharp.num_format=op=="atomic"?4:op.starts_with("dref")?7:0;
 ImageResource image{};image.is_array=array;image.is_depth=op.starts_with("dref");image.is_written=op=="write" || op=="atomic";image.is_atomic=op=="atomic";
 std::memcpy(image.sharp_fetch.immediates.data(),&sharp,sizeof(sharp));info.images.push_back(image);
 AmdGpu::Sampler sampler{};SamplerResource sampler_resource{};
 std::memcpy(sampler_resource.sharp_fetch.immediates.data(),&sampler,sizeof(sampler));
 info.samplers.push_back(sampler_resource);
 RuntimeInfo runtime{};runtime.Initialize(info.hw_stage,info.sw_stage);
 runtime.hw.fs.color_buffers[0].num_format=AmdGpu::NumberFormat::Float;
 Common::ObjectPool<IR::Inst> pool;IR::Block block(pool);IR::IREmitter ir(block);
 info.loads.Set(IR::Attribute::FragCoord,0);
 const auto x=ir.FPMul(ir.GetAttribute(IR::Attribute::FragCoord,0),ir.Imm32(.25f));
 const IR::Value coords=array?ir.CompositeConstruct(x,ir.Imm32(1.f)):IR::Value{x};
 const auto ix=ir.ConvertFToU(32,ir.GetAttribute(IR::Attribute::FragCoord,0));
 const IR::Value icoords=array?ir.CompositeConstruct(ix,ir.Imm32(1u)):IR::Value{ix};
 IR::TextureInstInfo flags{};flags.is_array.Assign(array);
 IR::Value value;
 const IR::Value empty{};
 if(op=="sample")value=ir.ImageSampleExplicitLod(ir.Imm32(0u),coords,ir.Imm32(0.f),empty,flags);
 else if(op=="dref")value=ir.ImageSampleDrefExplicitLod(ir.Imm32(0u),coords,ir.Imm32(.5f),ir.Imm32(0.f),empty,flags);
 else if(op=="dref-implicit")value=ir.ImageSampleDrefImplicitLod(ir.Imm32(0u),coords,ir.Imm32(.5f),{},empty,flags);
 else if(op=="dref-gather")value=ir.ImageGatherDref(ir.Imm32(0u),coords,ir.Imm32(1u),ir.Imm32(.5f),flags);
 else if(op=="implicit")value=ir.ImageSampleImplicitLod(ir.Imm32(0u),coords,{},empty,flags);
 else if(op=="offset")value=ir.ImageSampleExplicitLod(ir.Imm32(0u),coords,ir.Imm32(0.f),ir.Imm32(1u),flags);
 else if(op=="gradient")value=ir.ImageGradient(ir.Imm32(0u),coords,ir.Imm32(.25f),ir.Imm32(0.f),empty,{},flags);
 else if(op=="gather")value=ir.ImageGather(ir.Imm32(0u),coords,ir.Imm32(1u),flags);
 else if(op=="runtime-offset")value=ir.ImageGather(ir.Imm32(0u),coords,ix,flags);
 else if(op=="lod") {
  // ResourcePatchingPass retains x,slice for array LOD queries. The backend
  // must discard the layer before supplying spatial-only 2D coordinates.
  auto lod=ir.ImageQueryLod(ir.Imm32(0u),coords,flags);
  auto first=IR::F32{ir.CompositeExtract(lod,0)};
  value=ir.CompositeConstruct(first,first,first,ir.Imm32(1.f));
 } else if(op=="dimensions") {
  auto dim=ir.ImageQueryDimension(ir.Imm32(0u),ir.Imm32(0u),ir.Imm1(false),flags);
  value=ir.CompositeConstruct(ir.ConvertUToF(32,32,IR::U32{ir.CompositeExtract(dim,0)}),
    ir.ConvertUToF(32,32,IR::U32{ir.CompositeExtract(dim,1)}),ir.Imm32(0.f),ir.Imm32(1.f));
 } else if(op=="read")value=ir.ImageRead(ir.Imm32(0u),icoords,ir.Imm32(0u),{},flags);
 else if(op=="atomic") {
  auto old=ir.ImageAtomicIAdd(ir.Imm32(0u),icoords,ir.Imm32(1u),flags);
  auto f=ir.ConvertUToF(32,32,old);value=ir.CompositeConstruct(f,f,f,ir.Imm32(1.f));
 } else if(op=="write") {
  value=ir.CompositeConstruct(ir.Imm32(.2f),ir.Imm32(.4f),ir.Imm32(.6f),ir.Imm32(1.f));
  ir.ImageWrite(ir.Imm32(0u),icoords,{}, {},value,flags);
 } else std::abort();
 for(u32 i=0;i<4;++i){info.stores.Set(IR::Attribute::RenderTarget0,i);ir.SetAttribute(IR::Attribute::RenderTarget0,IR::F32{ir.CompositeExtract(value,i)},i);}
 ir.Epilogue();IR::Program program(info);program.blocks.push_back(&block);
 program.syntax_list.push_back({.data={.block=&block},.type=IR::AbstractSyntaxNode::Type::Block});
 program.syntax_list.push_back({.type=IR::AbstractSyntaxNode::Type::Return});
 Backend::Bindings bindings{};return Backend::SPIRV::EmitSPIRV(profile,runtime,program,bindings);
}
static id<MTLRenderPipelineState> Pipeline(id<MTLDevice> device,id<MTLFunction> vs,id<MTLFunction> fs) {
 MTLRenderPipelineDescriptor* p=[MTLRenderPipelineDescriptor new];p.vertexFunction=vs;p.fragmentFunction=fs;p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
 NSError* error=nil;auto result=[device newRenderPipelineStateWithDescriptor:p error:&error];
 if(!result)std::fprintf(stderr,"pipeline: %s\n",error.localizedDescription.UTF8String);assert(result);return result;
}
static std::array<float,16> Draw(id<MTLCommandQueue> queue,id<MTLRenderPipelineState> pso,
 id<MTLTexture> input,id<MTLSamplerState> sampler,id<MTLTexture> target) {
 auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;
 pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 auto command=[queue commandBuffer];auto render=[command renderCommandEncoderWithDescriptor:pass];
 [render setRenderPipelineState:pso];[render setFragmentTexture:input atIndex:0];[render setFragmentSamplerState:sampler atIndex:0];
 const u32 push_data[64]{};[render setFragmentBytes:push_data length:sizeof(push_data) atIndex:0];
 [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[render endEncoding];[command commit];[command waitUntilCompleted];
 assert(command.status==MTLCommandBufferStatusCompleted);std::array<float,16> pixels;
 [target getBytes:pixels.data() bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,4,1) mipmapLevel:0];return pixels;
}
int main(int argc,char** argv) {
 assert(argc==2);const std::filesystem::path out=argv[1];std::filesystem::create_directories(out);
 @autoreleasepool {
  Metal::Device d;auto device=d.GetImpl().device;Metal::ShaderCompiler compiler(d,out/"msl");
  auto profile=Metal::MakeShaderProfile(d,{});
  NSString* reference=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\nfragment float4 r1(float4 p[[position]],texture1d<float> t[[texture(0)]],sampler s[[sampler(0)]]){return t.sample(s,p.x*.25f);}\nfragment float4 ra(float4 p[[position]],texture1d_array<float> t[[texture(0)]],sampler s[[sampler(0)]]){return t.sample(s,p.x*.25f,1u);}\n";
  NSError* error=nil;auto library=[device newLibraryWithSource:reference options:nil error:&error];assert(library);
  auto vs=[library newFunctionWithName:@"vs"];auto queue=[device newCommandQueue];unsigned failures=0,compiled=0;
  for(bool array:{false,true})for(const char* op:{"sample","implicit","offset","gradient","gather","runtime-offset","lod","dimensions","read","write","atomic","dref","dref-implicit","dref-gather"}) {
   std::string name=std::string(array?"array-":"one-")+op;
   auto spv=Emit(array,op,profile);{std::ofstream f(out/(name+".spv"),std::ios::binary);f.write((const char*)spv.data(),spv.size()*4);}
   auto result=compiler.Compile(spv,0x51330000+compiled,name);++compiled;
   if(result.status!=Metal::CompileStatus::Ok){std::printf("FAIL %s: %s\n",name.c_str(),result.error.c_str());++failures;continue;}
   std::printf("COMPILED %s\n",name.c_str());
   if(std::string_view(op)!="sample")continue;
   auto generated=Pipeline(device,vs,(__bridge id<MTLFunction>)result.shader->function);
   auto native=Pipeline(device,vs,[library newFunctionWithName:array?@"ra":@"r1"]);
   auto td=[MTLTextureDescriptor new];td.pixelFormat=MTLPixelFormatRGBA8Unorm;td.width=4;td.height=1;td.depth=1;td.arrayLength=array?2:1;td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;
   td.textureType=array?MTLTextureType1DArray:MTLTextureType1D;auto one=[device newTextureWithDescriptor:td];assert(one);
   td.textureType=array?MTLTextureType2DArray:MTLTextureType2D;auto two=[device newTextureWithDescriptor:td];assert(two);
   for(unsigned layer=0;layer<td.arrayLength;++layer){u8 bytes[16];for(unsigned i=0;i<16;++i)bytes[i]=u8(17+i*11+layer*7);
    for(auto tex:{one,two})[tex replaceRegion:MTLRegionMake2D(0,0,4,1) mipmapLevel:0 slice:layer withBytes:bytes bytesPerRow:16 bytesPerImage:16];}
   auto rt=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:4 height:1 mipmapped:NO];rt.storageMode=MTLStorageModeShared;rt.usage=MTLTextureUsageRenderTarget;auto target=[device newTextureWithDescriptor:rt];assert(target);
   for(unsigned filter=0;filter<3;++filter){auto sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=filter?MTLSamplerMinMagFilterLinear:MTLSamplerMinMagFilterNearest;sd.sAddressMode=sd.tAddressMode=filter==2?MTLSamplerAddressModeClampToBorderColor:MTLSamplerAddressModeClampToEdge;sd.borderColor=MTLSamplerBorderColorTransparentBlack;
    auto sampler=[device newSamplerStateWithDescriptor:sd];assert(sampler);auto expected=Draw(queue,native,one,sampler,target);auto actual=Draw(queue,generated,two,sampler,target);
    float difference=0;for(unsigned i=0;i<16;++i)difference=std::max(difference,std::abs(actual[i]-expected[i]));
    std::printf("PIXELS %s filter=%u max-difference=%g\n",name.c_str(),filter,difference);if(difference>1e-6)++failures;
   }
  }
  std::printf("1D lowering: %u shaders, %u failures\n",compiled,failures);return failures?1:0;
 }
}
