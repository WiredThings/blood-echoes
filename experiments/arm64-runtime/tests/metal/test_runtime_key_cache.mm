// SPDX-License-Identifier: GPL-2.0-or-later
// Actual Storage/LoadPipelineStage and ordered/worker GetProgram. Backend factory counts
// valid SPIR-V modules only; actual compilation consumers are tested separately.
#define main WarmStageOriginalMain
#include "test_warm_stage_storage.cpp"
#undef main
#include <stdexcept>
namespace VideoCore {
class RuntimeKeyCore final:public PipelineCacheCore {
public:
 unsigned calls=0;
 RuntimeKeyCore(AmdGpu::Liverpool& l):PipelineCacheCore(&l,{}, {},16,"warm-stage"){}
 ~RuntimeKeyCore(){DestroyPipelines();}
 ShaderModuleHandle CreateShaderModule(std::span<const u32> spv,std::string_view,bool)override{if(spv.size()<5||spv[0]!=0x07230203)throw std::runtime_error("invalid actual SPIR-V");return {++calls};}
 void DestroyShaderModule(ShaderModuleHandle)override{}
 std::unique_ptr<PipelineSerialization> NewGraphicsSerialization()override{std::abort();}
 std::unique_ptr<PipelineSerialization> NewComputeSerialization()override{std::abort();}
 std::unique_ptr<PipelineBase> CreateGraphicsPipeline(const GraphicsPipelineKey&,std::span<const Shader::Info*,MaxShaderStages>,std::span<const Shader::RuntimeInfo,MaxShaderStages>,std::optional<const Shader::Gcn::FetchShaderData>,std::span<const ShaderModuleHandle,MaxShaderStages>,PipelineSerialization&,bool)override{std::abort();}
 std::unique_ptr<PipelineBase> CreateComputePipeline(ComputePipelineKey,const Shader::Info&,ShaderModuleHandle,PipelineSerialization&,bool)override{std::abort();}
};
}
static unsigned failures=0,checks=0;
static void Check(bool ok,const char* label){++checks;if(!ok){++failures;std::printf("FAIL %s\n",label);}}
static std::vector<u8> CompleteMeta(u64 hash,u64 meta,size_t index,const Shader::RuntimeInfo& runtime){
 Shader::Info info{};info.hw_stage=runtime.hw_stage;info.sw_stage=runtime.sw_stage;info.pgm_hash=hash;
 Shader::StageSpecialization spec{};spec.info=&info;spec.runtime_info=runtime;VideoCore::RegisterShaderMeta(info,{},spec,meta,index);
 const auto name=fmt::format("{:#018x}",meta);Wait(Blob(name,"meta"));std::vector<u8> out;Storage::DataBase::Instance().Load(Storage::BlobType::ShaderMeta,name,out);if(out.empty())throw std::runtime_error("empty actual metadata");return out;
}
static Shader::RuntimeInfo GeometryBase(){Shader::RuntimeInfo r{};r.Initialize(Shader::HwStage::Geometry,Shader::SwStage::Geometry);r.hw.gs.num_invocations=1;r.hw.gs.output_vertices=2;r.hw.gs.in_vertex_data_size=28;r.hw.gs.out_vertex_data_size=4;return r;}
static void StoredTransition(AmdGpu::Liverpool& l,u64 hash,Shader::RuntimeInfo base,Shader::RuntimeInfo different,const char* label){
 VideoCore::StageCore core{l};auto zero=CompleteMeta(hash,hash+100,0,base);auto one=CompleteMeta(hash,hash+101,1,different);SaveBinary(hash,0);SaveBinary(hash,1);
 Check(Load(core,zero)&&core.calls==1,label);const auto first=Access::Selected(core);
 bool loaded=Load(core,one);Check(loaded&&core.calls==2,label);if(loaded)Check(Access::Selected(core)!=first,label);
 const auto calls=core.calls;Check(Load(core,zero)&&core.calls==calls&&Access::Selected(core)==first,"stored-original-still-hits");
}
int main(int argc,char**argv){try{
 if(argc!=2)throw std::runtime_error("one private state directory required");const std::filesystem::path state=argv[1];const auto root=std::filesystem::path("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state");if(state.parent_path()!=root||!std::filesystem::is_directory(state)||std::filesystem::is_symlink(state))throw std::runtime_error("private state only");
 auto cache=state/"runtime-key-cache";std::filesystem::create_directory(cache);Common::FS::SetUserPath(Common::FS::PathType::CacheDir,cache);BbGpuConfig cfg{};cfg.serial="AUTHORED-RUNTIME-KEYS";Core::Emulator::FillElfInfo(cfg);auto& db=Storage::DataBase::Instance();db.SetCacheTag("warm-stage");db.Open();folder=cache/"AUTHORED-RUNTIME-KEYS-warm-stage";
 AmdGpu::Liverpool l;
 auto gs=GeometryBase(),other=gs;other.hw.gs.num_invocations=2;StoredTransition(l,0x777100,gs,other,"stored-gs-invocations");other=gs;other.hw.gs.in_vertex_data_size=32;StoredTransition(l,0x777200,gs,other,"stored-gs-input-ring");other=gs;other.hw.gs.out_vertex_data_size=8;StoredTransition(l,0x777300,gs,other,"stored-gs-output-ring");
 Shader::RuntimeInfo cs{};cs.Initialize(Shader::HwStage::Compute,Shader::SwStage::Compute);cs.hw.cs.workgroup_size={32,1,1};cs.hw.cs.shared_memory_size=1024;other=cs;other.hw.cs.shared_memory_size=2048;StoredTransition(l,0x777400,cs,other,"stored-cs-shared-size");
 {
 VideoCore::RuntimeKeyCore core{l};auto& regs=l.GetCsRegs();regs.num_thread_x.full=32;regs.num_thread_y.full=1;regs.num_thread_z.full=1;regs.settings.lds_dwords=2;
 std::array<u32,16> data{};const std::array<u32,3> code{0xBEEB03FF,0,0xBF810000};Shader::ShaderParams params{std::span<const u32,16>{data},code,0x98765432};VideoCore::PipelineSelection selection{};selection.regs=&l.regs;
 const auto get=[&](VideoCore::PipelineSelection& s){Shader::Backend::Bindings binding{};return core.GetProgram(s,Shader::HwStage::Compute,Shader::SwStage::Compute,params,binding);};
 const auto first=std::get<1>(get(selection));Check(bool(first)&&core.calls==1,"ordered-first-module");regs.settings.lds_dwords=4;const auto second=std::get<1>(get(selection));Check(bool(second)&&second!=first&&core.calls==2,"ordered-shared-size-must-create-module");
 regs.settings.lds_dwords=2;Check(std::get<1>(get(selection))==first,"ordered-return-original");const auto calls=core.calls;
 VideoCore::PrepWorker worker{};VideoCore::PipelineSelection prep{};prep.regs=&l.regs;prep.worker=&worker;regs.settings.lds_dwords=4;Check(std::get<1>(get(prep))==second&&!worker.failed&&core.calls==calls,"worker-known-specialization");
 VideoCore::PrepWorker unknown{};prep.worker=&unknown;regs.settings.lds_dwords=8;auto miss=get(prep);Check(unknown.failed&&!std::get<1>(miss)&&core.calls==calls,"worker-unknown-must-not-reuse-or-compile");
 }
 db.Close();std::printf("RESULT actual runtime-key cache checks=%u failures=%u; backend module factory is counting mock; no GPU commands\n",checks,failures);return failures?2:0;
 }catch(const std::exception& e){std::fprintf(stderr,"ERROR actual runtime-key cache: %s\n",e.what());return 3;}}
