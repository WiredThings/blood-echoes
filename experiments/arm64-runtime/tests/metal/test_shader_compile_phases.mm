// SPDX-License-Identifier: GPL-2.0-or-later
// Authored GCN -> real PipelineCacheCore -> SPIR-V -> real Metal library. No game assets.
#import <Metal/Metal.h>
#include <array>
#include <cassert>
#include <filesystem>
#include <fstream>
#include "video_core/amdgpu/liverpool.h"
#include "video_core/pipeline_cache_core.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "test_gpu_runtime_stubs.cpp"
namespace VideoCore {
struct PipelineCacheCoreTestAccess {
 static ShaderModuleHandle Compile(PipelineCacheCore& cache,Shader::Info& info,Shader::RuntimeInfo& runtime,std::span<const u32> code,size_t permutation,Shader::Backend::Bindings& binding){return cache.CompileModule(info,runtime,code,permutation,binding);}
};
class AuthoredCore final:public PipelineCacheCore {
 Metal::ShaderCompiler& compiler;
public:
 std::vector<std::unique_ptr<Metal::CompiledShader>> shaders;
 std::vector<std::vector<u32>> binaries;
 AuthoredCore(AmdGpu::Liverpool& l,Metal::Device& device,Metal::ShaderCompiler& c):PipelineCacheCore(&l,Metal::MakeShaderProfile(device,BackendLimits{}),Metal::MakePipelineCapabilities(),16,"authored-shader-phases"),compiler(c){}
 ~AuthoredCore(){DestroyPipelines();}
 ShaderModuleHandle CreateShaderModule(std::span<const u32> spv,std::string_view name,bool)override{
  assert(spv.size()>5&&spv[0]==0x07230203);binaries.emplace_back(spv.begin(),spv.end());
  auto result=compiler.Compile(spv,0x13579bdfULL,name);assert(result.shader&&result.shader->library&&result.shader->function);
  auto pointer=reinterpret_cast<uintptr_t>(result.shader.get());shaders.push_back(std::move(result.shader));return {pointer};
 }
 void DestroyShaderModule(ShaderModuleHandle)override{}
 std::unique_ptr<PipelineSerialization> NewGraphicsSerialization()override{return {};}
 std::unique_ptr<PipelineSerialization> NewComputeSerialization()override{return {};}
 std::unique_ptr<PipelineBase> CreateGraphicsPipeline(const GraphicsPipelineKey&,std::span<const Shader::Info*,MaxShaderStages>,std::span<const Shader::RuntimeInfo,MaxShaderStages>,std::optional<const Shader::Gcn::FetchShaderData>,std::span<const ShaderModuleHandle,MaxShaderStages>,PipelineSerialization&,bool)override{std::abort();}
 std::unique_ptr<PipelineBase> CreateComputePipeline(ComputePipelineKey,const Shader::Info&,ShaderModuleHandle,PipelineSerialization&,bool)override{std::abort();}
};
}
int main(int argc,char**argv){
 assert(argc==2);const std::filesystem::path path=argv[1];
 @autoreleasepool {
  Metal::Device device;Metal::ShaderCompiler compiler{device,path/"msl"};AmdGpu::Liverpool liverpool;
  VideoCore::AuthoredCore core{liverpool,device,compiler};
  std::array<u32,16> data{};const std::array<u32,3> code{0xBEEB03FF,0,0xBF810000};
  // Run the real speculative lookup branch before any module exists. It must
  // report unavailable without compiling, publishing a program, or skipping a draw.
  {
   Shader::ShaderParams params{std::span<const u32,16>{data},code,0x2468ace0ULL};
   VideoCore::PrepWorker worker{};VideoCore::PipelineSelection selection{};
   selection.regs=&liverpool.regs;selection.worker=&worker;
   Shader::Backend::Bindings binding{};
   auto result=core.GetProgram(selection,Shader::HwStage::Compute,Shader::SwStage::Compute,params,binding);
   assert(worker.failed&&!std::get<1>(result)&&core.binaries.empty());
  }
  for(size_t permutation=0;permutation<2;++permutation){
   Shader::ShaderParams params{std::span<const u32,16>{data},code,0x13579bdfULL};
   Shader::Info info{Shader::HwStage::Compute,Shader::SwStage::Compute,params};
   Shader::RuntimeInfo runtime{};runtime.Initialize(Shader::HwStage::Compute,Shader::SwStage::Compute);runtime.hw.cs.workgroup_size={1,1,1};
   Shader::Backend::Bindings binding{};assert(VideoCore::PipelineCacheCoreTestAccess::Compile(core,info,runtime,code,permutation,binding));
  }
  assert(core.binaries.size()==2&&core.binaries[0]==core.binaries[1]);
  std::ofstream out(path/"authored.spv",std::ios::binary);out.write(reinterpret_cast<const char*>(core.binaries[0].data()),core.binaries[0].size()*sizeof(u32));assert(out.good());
 }
 puts("PASS authored GCN to real IR/SPIR-V and Metal library, two identical emitted modules");return 0;
}
