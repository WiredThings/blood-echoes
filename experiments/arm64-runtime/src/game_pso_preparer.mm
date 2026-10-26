// SPDX-License-Identifier: GPL-2.0-or-later
// Headless candidate-only preparation. No guest Runtime, Info/SRT or commands/draws.
#import <Foundation/Foundation.h>
#include "preparation_json.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <signal.h>
#include <stdexcept>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include "common/serdes.h"
#include "shader_recompiler/specialization.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_cached_variants.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "runtime_exit_codes.h"

namespace {
using Json=PreparationJson::Value;namespace fs=std::filesystem;
constexpr const char* Shared="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype";
constexpr size_t MaxBlob=1024*1024,MaxPacket=256*1024,MaxTotal=8*MaxBlob;
volatile sig_atomic_t interrupted=0;
void Signal(int){interrupted=1;}
void Need(bool b,const char* why){if(!b)throw std::runtime_error(why);}
void Stop(){Need(!interrupted,"preparation interrupted");}
bool Child(const fs::path& p,const fs::path& root){auto r=p.lexically_relative(root);return !r.empty()&&r!="."&&!r.is_absolute()&&*r.begin()!="..";}
void Directory(const fs::path& p){struct stat st{};Need(p.is_absolute()&&fs::canonical(p)==p&&lstat(p.c_str(),&st)==0&&S_ISDIR(st.st_mode)&&st.st_uid==getuid(),"owned canonical directory required");}
bool Same(const struct stat&a,const struct stat&b){return a.st_dev==b.st_dev&&a.st_ino==b.st_ino&&a.st_size==b.st_size&&a.st_mtimespec.tv_sec==b.st_mtimespec.tv_sec&&a.st_mtimespec.tv_nsec==b.st_mtimespec.tv_nsec;}
std::vector<u8> Read(const fs::path& p,size_t maximum,const std::string& expected={}){
 Stop();Need(p.is_absolute()&&fs::canonical(p)==p,"canonical nonlinked input required");
 int fd=open(p.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);Need(fd>=0,"input open");
 struct Guard{int fd;~Guard(){close(fd);}}guard{fd};struct stat a{},b{};
 Need(fstat(fd,&a)==0&&S_ISREG(a.st_mode)&&a.st_uid==getuid()&&a.st_size>0&&uint64_t(a.st_size)<=maximum,"owned regular bounded input");
 std::vector<u8> bytes(a.st_size);size_t at=0;
 while(at<bytes.size()){auto n=read(fd,bytes.data()+at,bytes.size()-at);if(n<0&&errno==EINTR)continue;Need(n>0,"short owned read");at+=n;}
 Need(fstat(fd,&b)==0&&Same(a,b),"input changed during owned read");
 if(!expected.empty())Need(Metal::FunctionIdentity::Hex(expected)&&Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(bytes.data()),bytes.size()})==expected,"input SHA mismatch");
 return bytes;
}
u64 Hash(const std::string&s){Need(s.size()==16&&s.find_first_not_of("0123456789abcdef")==s.npos,"64bit hex identity");return std::stoull(s,nullptr,16);}
u32 Word(const std::vector<u8>&b,size_t at){Need(at+4<=b.size(),"header extent");u32 v;memcpy(&v,b.data()+at,4);return v;}
// Allocation-envelope preflight only; semantic reconstruction remains the one
// original StageSpecialization reader. Reject container bombs before allocation.
void SpecEnvelope(const std::vector<u8>& b) {
 size_t at=sizeof(Shader::Backend::Bindings)+sizeof(Shader::RuntimeInfo);
 const auto take=[&](size_t n){Need(at<=b.size()&&n<=b.size()-at,"spec allocation envelope");auto*p=b.data()+at;at+=n;return p;};
 const auto count=[&](){uint64_t n;memcpy(&n,take(8),8);return n;};
 Need(count()==128,"spec bitset shape");const auto*bits=take(128);
 for(size_t i=0;i<128;++i)Need(bits[i]=='0'||bits[i]=='1',"spec bitset value");
 const auto fetch=count();Need(fetch==0||fetch==sizeof(Shader::Gcn::FetchShaderData),"spec fetch ABI");
 if(fetch){take(6);const auto n=count();Need(n<=128,"fetch allocation bound");take(n*sizeof(Shader::Gcn::VertexAttribute));}
 for(size_t stride:{sizeof(Shader::VsAttribSpecialization),sizeof(Shader::BufferSpecialization),
      sizeof(Shader::ImageSpecialization),sizeof(Shader::FMaskSpecialization),sizeof(Shader::SamplerSpecialization)}) {
  const auto n=count();Need(n<=128,"specialization vector allocation bound");take(n*stride);
 }
 Need(at==b.size(),"exact spec-only interval without forbidden tail");
}
struct Stage{
 std::string logical,hardware;u64 pgm{},permutation{},hash{};
 std::vector<u8> spec;std::vector<u32> spirv;std::unique_ptr<Metal::ShaderModule> module;
};
size_t Software(std::string_view s){constexpr std::array<std::string_view,6>a{"fragment","tessellation_control","tessellation_eval","vertex","geometry","compute"};for(size_t i=0;i<a.size();++i)if(a[i]==s)return i;throw std::runtime_error("logical stage");}
size_t Hardware(std::string_view s){constexpr std::array<std::string_view,7>a{"fragment","vertex","geometry","export","hull","local","compute"};for(size_t i=0;i<a.size();++i)if(a[i]==s)return i;throw std::runtime_error("hardware stage");}
Metal::ShaderStage NativeStage(size_t logical){using S=Metal::ShaderStage;constexpr std::array<S,6>a{S::Fragment,S::TessellationControl,S::TessellationEval,S::Vertex,S::Geometry,S::Compute};return a.at(logical);}
std::vector<u8> Reference(const Json& r,const fs::path& root,size_t& total){
 fs::path p=r.at("path").get<std::string>();Need(p.parent_path()==root&&p.filename()==r.at("name").get<std::string>(),"packet file outside one frozen root");
 auto bytes=Read(p,MaxBlob,r.at("sha256"));Need(bytes.size()==r.at("bytes").get<size_t>(),"packet file byte count");
 total+=bytes.size();Need(total<=MaxTotal,"whole owned recipe budget");return bytes;
}
Json Function(const Metal::CompiledShader* s,std::string label){
 Need(s&&s->function&&s->persistent_identity.valid(),"successful persistent function identity required");
 const auto route=s->persistent_identity.route==Metal::PersistentLibraryRoute::LocalMetallib?"local-metallib":"source";
 return {{"label",label},{"identity",s->persistent_identity.hex()},{"actual_route",route}};
}
int FixedHelpers(const std::string&mode,const Json&p,const fs::path&scratch,Json&result){
 Need(p.at("packet_schema")==1&&p.at("fixed_helper_seed_schema")==Metal::HostPso::FixedSeedSchema&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false,"fixed helper packet schema");
 const char* cohort=getenv("BB_MTL_ARCHIVE_COMPATIBILITY_ID");const char* discovery=getenv("BB_MTL_LOCAL_AOT_COHORT");const char* gpu_value=getenv("BB_GPU_USER_DIR");
 Need(cohort&&discovery&&gpu_value&&Metal::HostPso::Hex(cohort)&&std::string(cohort)==discovery&&p.at("current_cohort")==cohort,"verified helper cohort");
 const fs::path gpu=gpu_value,artifact=p.at("artifact_cache_dir").get<std::string>();Directory(gpu);Directory(artifact);
 Need(Child(gpu,fs::path(Shared)/"state")&&artifact.filename()=="metal"&&artifact.parent_path().parent_path()==gpu/"cache","actual renderer artifact cache required");
 const auto title=artifact.parent_path().filename().string();Need(title.size()==9&&title.starts_with("CUSA")&&title.find_first_not_of("0123456789",4)==title.npos,"actual title cache required");
 Need(artifact!=scratch&&!Child(artifact,scratch)&&!Child(scratch,artifact),"helper scratch/artifact separation");
 const char* persist=getenv("BB_MTL_PERSIST_WARM_ARCHIVE");const char* replay=getenv("BB_MTL_REPLAY_THREAD");const char*aot=getenv("BB_MTL_LOCAL_AOT_MODE");
 Need(persist&&std::string_view(persist)=="0"&&replay&&std::string_view(replay)=="0","headless helper main/replay policy");
 Need(aot&&(std::string_view(aot)=="record"||std::string_view(aot)=="load"),"actual helper local library phase");
 const bool compile=mode=="helpers-prepare";
 if(compile){const char*build=getenv("BB_MTL_LOCAL_AOT_BUILD_ID");Need(std::string_view(aot)=="load"&&build&&Metal::HostPso::Hex(build),"fixed prepare requires verified local build");}
 Stop();Metal::Device device;Metal::Scheduler scheduler(device,"fixed helper preparation");Metal::PsoCache cache(device,artifact,Metal::ReadFloatRules()==Metal::FloatRules::RelaxedMath);cache.ReleaseWarmArchive();
 Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&cache);
 const auto prepared=kernels.PrepareFixedSeeds(compile,[]{return bool(interrupted);});kernels.BindPsoCache(nullptr);Stop();
 result["mode"]=mode;result["fixed_helper_seed_schema"]=Metal::HostPso::FixedSeedSchema;result["fixed_only"]=true;result["storage_opened"]=false;result["renderer_owners_constructed"]=false;result["guest_Runtime_constructed"]=false;
 result["known"]=prepared.progress.known;result["processed"]=prepared.progress.processed;result["ready"]=prepared.progress.ready;result["built"]=prepared.progress.built;result["deferred"]=prepared.progress.deferred;result["failed"]=prepared.progress.failed;result["cancelled"]=prepared.cancelled;
 bool all_local=prepared.items.size()==Metal::HostPso::FixedSeedCount;
 Json rows=Json::array();for(const auto&item:prepared.items){
  const bool local=item.origin_count==(item.kind==Metal::HostPso::Kind::PostProcess?2u:1u)&&std::all_of(item.actual_routes.begin(),item.actual_routes.begin()+item.origin_count,[](auto route){return route==Metal::LocalLibrary::Route::LocalMetallib;});all_local&=local;
  Json routes=Json::array();for(unsigned i=0;i<item.origin_count;++i)routes.push_back(unsigned(item.actual_routes[i]));
  rows.push_back({{"kind",u32(item.kind)},{"request_sha256",item.request_sha256},{"descriptor_sha256",item.descriptor_sha256},{"inputs_verified",item.inputs_verified},{"native_ready",item.native_ready},{"outcome",unsigned(item.outcome.state)},{"archive_sha256",item.outcome.archive_sha256},{"actual_routes",routes}});}
 result["items"]=rows;result["all_actual_libraries_local"]=all_local;
 const auto&c=cache.GetCounters();result["artifact_counts"]={{"attempts",c.artifact_attempts.load()},{"hits",c.artifact_hits.load()},{"built",c.artifact_built.load()},{"compile_attempts",c.artifact_compile_attempts.load()},{"failed",c.artifact_failed.load()},{"deferred",c.artifact_deferred.load()},{"critical",c.artifact_critical.load()}};
 const bool critical=prepared.progress.state==Metal::HostPso::WarmState::Critical;const bool complete=prepared.progress.state==Metal::HostPso::WarmState::Complete&&prepared.progress.ready==Metal::HostPso::FixedSeedCount&&all_local;
 result["status"]=critical?"stopped":complete?"completed":compile?"deferred":"inputs-collected";result["all_fixed_native_ready"]=complete;
 std::printf("FIXED_HELPER_RESULT %s\n",result.dump().c_str());return critical?BB_ARM_EXIT_HOST_PROTECTION:0;
}

}

int main(int argc,char**argv){@autoreleasepool {
 if(argc==2&&std::string_view(argv[1])=="--fixed-helper-seed-schema"){
  std::printf("FIXED_HELPER_CAPABILITY %u\n",Metal::HostPso::FixedSeedSchema);return 0;
 }
 // This executable has its own worker contract; never emits launcher restart 75/76.
 if(argc!=4){std::fprintf(stderr,"usage: game-pso-preparer record|prepare|helpers-probe|helpers-prepare packet.json fresh-scratch\n");return 64;}
 signal(SIGINT,Signal);signal(SIGTERM,Signal);
 Json result{{"candidate_only",true},{"runtime_cache_import_allowed",false},{"actual_final_read_admission",{{"allowed",false}}},
  {"native_info_srt_deserialize",false},{"walker_registered_or_executed",false},{"artifact_publication_observed",false}};
 try {
  const std::string mode=argv[1];Need(mode=="record"||mode=="prepare"||mode=="helpers-probe"||mode=="helpers-prepare","worker mode");
  const fs::path packet=argv[2],scratch=argv[3];Directory(scratch);
  Need(Child(scratch,fs::path(Shared)/"state")&&fs::is_empty(scratch),"fresh empty Shared source scratch required");
  Directory(packet.parent_path());Need(Child(packet.parent_path(),fs::path(Shared)/"state"),"Shared packet root required");
  auto raw=Read(packet,MaxPacket);Json p=Json::parse(raw);
  if(mode=="helpers-probe"||mode=="helpers-prepare")return FixedHelpers(mode,p,scratch,result);
  Need(p.at("packet_schema")==1&&p.at("manifest_schema")==2&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false,"candidate packet schema/policy");
  Need(p.at("native_info_srt_allowed")==false&&p.at("walker_bytes_passed")==0,"forbidden metadata tail");
  Need(Metal::FunctionIdentity::Hex(p.at("cache_id").get<std::string>())&&Metal::FunctionIdentity::Hex(p.at("recipe_blob_identity").get<std::string>())&&Metal::FunctionIdentity::Hex(p.at("frozen_input_set_identity").get<std::string>()),"scoped packet identities");
  result["cache_id"]=p.at("cache_id");result["recipe_blob_identity"]=p.at("recipe_blob_identity");
  result["frozen_input_set_identity"]=p.at("frozen_input_set_identity");result["mode"]=mode;
  const fs::path source=p.at("original_cache").get<std::string>();
  Need(!Child(scratch,source)&&!Child(source,scratch)&&scratch!=source,"scratch overlaps old cache");
  const char* cohort=getenv("BB_MTL_ARCHIVE_COMPATIBILITY_ID");Need(cohort&&Metal::FunctionIdentity::Hex(cohort),"current verified compile cohort required");
  Need(p.at("current_cohort")==cohort,"packet/current environment cohort");
  const char* aot=getenv("BB_MTL_LOCAL_AOT_MODE");
  Need(aot&&std::string_view(aot)==(mode=="record"?"record":"load"),"actual local-library phase");
  if(mode=="prepare"){const char* build=getenv("BB_MTL_LOCAL_AOT_BUILD_ID");Need(build&&Metal::FunctionIdentity::Hex(build),"verified local build identity required");}
  Need(p.at("layout").at("bindings_size")==sizeof(Shader::Backend::Bindings)&&
       p.at("layout").at("runtime_size")==sizeof(Shader::RuntimeInfo)&&
       p.at("layout").at("graphics_key_size")==sizeof(VideoCore::GraphicsPipelineKey),"actual native spec/key layout");
  size_t total=0;auto keybytes=Reference(p.at("key"),packet.parent_path(),total);
  Need(Word(keybytes,0)==5&&Word(keybytes,4)<=1,"actual key version/kind");
  const bool compute=Word(keybytes,4)==1;
  Serialization::Archive keyar{std::vector<u8>(keybytes.begin()+8,keybytes.end())};
  VideoCore::GraphicsPipelineKey key{};Vulkan::ComputePipelineKey ckey{};Metal::GraphicsSerialization data;
  if(compute)ckey.Deserialize(keyar);else{key.Deserialize(keyar);data.Deserialize(keyar);Need(!key.motion_vectors&&!key.stage_hashes[u32(Shader::SwStage::Geometry)],"motion/GS key rejected");}
  Need(p.at("stages").size()>0&&p.at("stages").size()<=6,"stage count");
  std::vector<Stage> stages;std::array<bool,6> present{};
  for(const auto& r:p.at("stages")){
   Stage s;s.logical=r.at("logical_stage");s.hardware=r.at("hardware_stage");const auto sw=Software(s.logical),hw=Hardware(s.hardware);
   Need(!present[sw]&&sw!=u32(Shader::SwStage::Geometry)&&hw!=u32(Shader::HwStage::Geometry),"duplicate/GS stage rejected");present[sw]=true;
   s.pgm=Hash(r.at("program_hash"));s.permutation=r.at("permutation").get<u64>();s.hash=Hash(r.at("permutation_hash"));
   Need(s.hash==(compute?ckey.value:key.stage_hashes[sw]),"key/meta permutation equality");
   s.spec=Reference(r.at("spec"),packet.parent_path(),total);
   Need(r.at("spec").at("source_offset")==24&&r.at("spec").at("native_spec_only_reader_allowed")==true,"audited spec-only region");
   // Exact immutable spec interval was bounded-audited before packet creation.
   // This original reader does not touch Info, flat tables, SRT or walker code.
   SpecEnvelope(s.spec);Serialization::Archive specar{std::vector<u8>(s.spec)};Shader::StageSpecialization spec{};
   Need(spec.Deserialize(specar)&&specar.IsEoS(),"strict original spec-only reader extent");
   Need(u32(spec.runtime_info.sw_stage)==sw&&u32(spec.runtime_info.hw_stage)==hw,"actual spec logical/hardware stage");
   if(hw==u32(Shader::HwStage::Vertex))Need(!spec.runtime_info.hw.vs.motion_vectors,"actual motion VS rejected");
   auto spv=Reference(r.at("spirv"),packet.parent_path(),total);
   Need(spv.size()%4==0&&spv.size()>=20&&Word(spv,0)==0x07230203,"complete SPIR-V shape");
   s.spirv.resize(spv.size()/4);memcpy(s.spirv.data(),spv.data(),spv.size());
   Need(Metal::ReadShaderStage(s.spirv)==NativeStage(sw),"actual SPIR-V stage");stages.push_back(std::move(s));
  }
  for(size_t i=0;i<present.size();++i)Need(present[i]==(compute?i==u32(Shader::SwStage::Compute):bool(key.stage_hashes[i])),"complete local stage set");
  Need(compute||present[u32(Shader::SwStage::Vertex)],"graphics VS required");
  Need(present[u32(Shader::SwStage::TessellationControl)]==present[u32(Shader::SwStage::TessellationEval)],"paired tess stages");
  Stop();Metal::Device device;Metal::ShaderCompiler compiler{device,scratch,Metal::ReadFloatRules()};
  Need(compiler.CacheDir()==scratch,"actual fresh source cache_dir");
  std::array<Metal::ShaderModule*,VideoCore::MaxShaderStages> modules{};
  for(auto& s:stages){Stop();auto name=VideoCore::PipelineCacheCore::GetShaderName(static_cast<Shader::HwStage>(Hardware(s.hardware)),s.pgm,s.permutation);
   s.module=Metal::CachedVariants::BuildModule(compiler,s.spirv,name);Need(s.module&&s.module->status==Metal::CompileStatus::Ok,"original module compile failed");
   modules[Software(s.logical)]=s.module.get();}
  Json functions=Json::array();const Metal::CompiledShader* vs=nullptr,*fragment=nullptr,*cs=nullptr;Metal::TessellationInput tess;
  std::string error;bool unsupported=false;const bool is_tess=present[u32(Shader::SwStage::TessellationControl)];
  bool rect=false;
  if(compute){cs=modules[u32(Shader::SwStage::Compute)]->shader.get();functions.push_back(Function(cs,"compute"));}
  else {
   if(present[u32(Shader::SwStage::Fragment)]){fragment=modules[u32(Shader::SwStage::Fragment)]->shader.get();functions.push_back(Function(fragment,"fragment"));}
   if(is_tess){tess.vertex_attributes=data.vertex_attributes;const bool prepared=Metal::CachedVariants::Tessellation(compiler,modules,tess,error,unsupported);Need(prepared,error.c_str());
    for(size_t i=0;i<3;++i)functions.push_back(Function(tess.vertex[i],"tess-vertex-"+std::to_string(i)));
    functions.push_back(Function(tess.control,"tess-control"));functions.push_back(Function(tess.eval,"tess-eval"));}
   else {auto*m=modules[u32(Shader::SwStage::Vertex)];rect=key.prim_type==AmdGpu::PrimitiveType::RectList;
    vs=rect?Metal::CachedVariants::RectList(compiler,m,error):m->shader.get();Need(vs,error.c_str());functions.push_back(Function(vs,rect?"rectlist-original-rewrite":"vertex"));}
  }
  Stop();result["functions"]=functions;result["fresh_actual_source_cache_dir"]=scratch.string();
  bool any_source=false;for(const auto& f:functions)any_source|=f.at("actual_route")=="source";result["source_fallback"]=any_source;
  result["family"]=compute?"compute":is_tess?"tessellation":rect?"rectlist":"ordinary-graphics";
  if(mode=="prepare"){
   // Main archive never reads an old source-cache path. No full-game GPU Runtime.
   const char* persist=getenv("BB_MTL_PERSIST_WARM_ARCHIVE");Need(persist&&std::string_view(persist)=="0","headless main archive must be disabled before construction");
   const fs::path artifact=p.at("artifact_cache_dir").get<std::string>();Directory(artifact);
   const char* gpu=getenv("BB_GPU_USER_DIR");Need(gpu&&Child(artifact,fs::path(gpu)),"selected QA artifact root required");
   Need(artifact!=scratch&&!Child(artifact,scratch)&&!Child(scratch,artifact),"artifact/source scratch separation");
   Metal::PsoCache pso{device,artifact,Metal::ReadFloatRules()==Metal::FloatRules::RelaxedMath};
   pso.ReleaseWarmArchive();const bool allow=p.at("allow_compile").get<bool>();pso.SetArtifactPreparation(true,allow);
   bool ready=false;
   if(compute)ready=pso.GetComputePipeline(ckey,cs)!=nullptr;
   else if(is_tess)ready=pso.GetTessellationPipeline(key,tess,fragment)!=nullptr;
   else ready=pso.GetRenderPipeline(key,vs,fragment,{.vertex_attributes=data.vertex_attributes,.rect_list_triangles=rect})!=nullptr;
   result["native_pipeline_ready"]=ready;result["allow_compile"]=allow;
   const auto& c=pso.GetCounters();result["strict_archive_hits"]=c.archive_hits.load();
   result["artifact_counts"]={{"attempts",c.artifact_attempts.load()},{"hits",c.artifact_hits.load()},
      {"built",c.artifact_built.load()},{"deferred",c.artifact_deferred.load()},{"failed",c.artifact_failed.load()},
      {"critical",c.artifact_critical.load()},{"unavailable",c.artifact_unavailable.load()},
      {"compile_attempts",c.artifact_compile_attempts.load()}};
   result["artifact_publication_observed"]=c.artifact_attempts.load()>0&&
       c.artifact_hits.load()+c.artifact_built.load()==c.artifact_attempts.load()&&
       c.artifact_deferred.load()+c.artifact_failed.load()+c.artifact_critical.load()+c.artifact_unavailable.load()==0;
   result["status"]=ready?"native-ready-see-actual-artifact-counts-and-events":allow?"native-failed":"deferred";
  }else{result["native_pipeline_ready"]=false;result["status"]="source-requests-recorded";}
  std::printf("GAME_PSO_RESULT %s\n",result.dump().c_str());return 0;
 }catch(const std::exception& e){result["status"]=interrupted?"stopped":"rejected";result["error"]=e.what();
  std::fprintf(stderr,"GAME_PSO_RESULT %s\n",result.dump().c_str());return interrupted?70:65;}
}}
