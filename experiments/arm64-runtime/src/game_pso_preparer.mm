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
#include <memory>
#include <optional>
#include <signal.h>
#include <stdexcept>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include "common/serdes.h"
#include "shader_recompiler/specialization.h"
#include "shader_recompiler/profile_cache_wire.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_cached_variants.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "runtime_exit_codes.h"
#include "preparation_runtime_paths.h"

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
// Schema 1 retains its historical GS rejection. This independent contract is
// only for SPIR-V actually retranslated from owned GCN by the module preparer.
bool GeometryPacket(const Json& p){
 const bool geometry=p.at("packet_schema")==2;
 if(geometry)Need(p.at("geometry_pso_schema")==1&&p.at("kind")=="owned-gcn-geometry-pso-input","owned geometry PSO packet contract");
 else Need(p.at("packet_schema")==1,"candidate packet version");
 return geometry;
}
void GeometryKey(bool geometry,bool compute,const VideoCore::GraphicsPipelineKey&key){
 Need(!geometry||!compute,"geometry packet cannot contain a compute key");
 if(!compute)Need(!key.motion_vectors&&(geometry?bool(key.stage_hashes[u32(Shader::SwStage::Geometry)]):!key.stage_hashes[u32(Shader::SwStage::Geometry)]),"motion/GS key contract rejected");
}
void GeometrySpan(const std::vector<u8>&spec,const Json&rebind,size_t offset){
 Need(Metal::FunctionIdentity::Hex(rebind.at("original_spec_sha256").get<std::string>())&&
      rebind.at("runtime_span_offset")==offset&&rebind.at("bytes")==16&&
      rebind.at("serialized_span_zeroed")==true&&rebind.at("retired_pointer_exported")==false,"geometry sanitized spec contract");
 Need(offset<=spec.size()&&16<=spec.size()-offset,"geometry span extent");
 for(size_t i=0;i<16;++i)Need(spec[offset+i]==0,"geometry retired span must be zero before typed reader");
}
void GeometryReceipt(const Json&r,const Json&proof,const std::string&profile,bool geometry){
 Need(proof.at("module_retranslation_schema")==1&&proof.at("status")=="completed"&&
      proof.at("candidate_only")==true&&proof.at("runtime_cache_import_allowed")==false&&
      proof.at("gpu_started")==false&&proof.at("native_info_srt_deserialized")==false&&
      proof.at("old_walker_executed")==false&&proof.at("guest_memory_read")==false,
      "owned GCN module receipt required");
 Need(proof.at("program_hash")==r.at("program_hash")&&proof.at("logical_stage")==r.at("logical_stage")&&
      proof.at("hardware_stage")==r.at("hardware_stage")&&proof.at("input_spec_sha256")==r.at("spec").at("sha256")&&
      proof.at("input_profile_sha256")==profile&&proof.at("spirv_sha256")==r.at("spirv").at("sha256")&&
      proof.at("expected_spirv_equal")==true&&proof.at("current_generated_srt_verified")==true&&
      proof.at("unknown_program_base_rejected_before_use")==true,"exact geometry module retranslation inputs");
 const auto provenance=r.at("source_provenance");
 Need(provenance.at("title_id")=="CUSA03173"&&provenance.at("app_ver")=="01.09"&&
      provenance.at("code_prefix_verified")==true&&proof.at("source_provenance")==provenance,
      "geometry source provenance mismatch");
 Need(Metal::FunctionIdentity::Hex(r.at("module_input_packet_sha256").get<std::string>())&&
      proof.at("input_packet_sha256")==r.at("module_input_packet_sha256")&&
      proof.at("input_code_sha256")==provenance.at("code_sha256"),"geometry module packet/code binding");
 for(const auto field:{"code_sha256","container_sha256","entry_sha256"})
  Need(Metal::FunctionIdentity::Hex(provenance.at(field).get<std::string>()),"geometry source provenance digest");
 if(geometry){const auto copy=r.at("gs_copy"),source=copy.at("source_provenance");
  Need(proof.at("owned_geometry_copy_rebound")==true&&proof.at("gs_copy_sha256")==copy.at("code").at("sha256")&&
       proof.at("gs_copy_source_provenance")==source&&source.at("title_id")=="CUSA03173"&&source.at("app_ver")=="01.09"&&
       source.at("code_prefix_verified")==true&&source.at("code_sha256")==copy.at("code").at("sha256")&&
       source.at("container_sha256")==provenance.at("container_sha256")&&source.at("entry_sha256")==provenance.at("entry_sha256"),"geometry owned copy proof");
 }
}
size_t Software(std::string_view s){constexpr std::array<std::string_view,6>a{"fragment","tessellation_control","tessellation_eval","vertex","geometry","compute"};for(size_t i=0;i<a.size();++i)if(a[i]==s)return i;throw std::runtime_error("logical stage");}
size_t Hardware(std::string_view s){constexpr std::array<std::string_view,7>a{"fragment","vertex","geometry","export","hull","local","compute"};for(size_t i=0;i<a.size();++i)if(a[i]==s)return i;throw std::runtime_error("hardware stage");}
Metal::ShaderStage NativeStage(size_t logical){using S=Metal::ShaderStage;constexpr std::array<S,6>a{S::Fragment,S::TessellationControl,S::TessellationEval,S::Vertex,S::Geometry,S::Compute};return a.at(logical);}
std::vector<u8> Reference(const Json& r,const fs::path& root,size_t& total){
 fs::path p=r.at("path").get<std::string>();Need(p.parent_path()==root&&p.filename()==r.at("name").get<std::string>(),"packet file outside one frozen root");
 Need(Metal::FunctionIdentity::Hex(r.at("sha256").get<std::string>()),"required packet file SHA256 identity");
 auto bytes=Read(p,MaxBlob,r.at("sha256"));Need(bytes.size()==r.at("bytes").get<size_t>(),"packet file byte count");
 total+=bytes.size();Need(total<=MaxTotal,"whole owned recipe budget");return bytes;
}
#ifndef BB_GEOMETRY_PSO_CONTRACT_ONLY
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
 const auto roots=BbPreparationPaths::Actual(BbBundledRuntime::Role::PsoWorker);
 Need((roots?gpu==roots->gpu:Child(gpu,fs::path(Shared)/"state"))&&artifact.filename()=="metal"&&artifact.parent_path().parent_path()==gpu/"cache","actual renderer artifact cache required");
 const auto title=artifact.parent_path().filename().string();Need(title.size()==9&&title.starts_with("CUSA")&&title.find_first_not_of("0123456789",4)==title.npos,"actual title cache required");
 Need(artifact!=scratch&&!Child(artifact,scratch)&&!Child(scratch,artifact),"helper scratch/artifact separation");
 const char* persist=getenv("BB_MTL_PERSIST_WARM_ARCHIVE");const char* replay=getenv("BB_MTL_REPLAY_THREAD");const char*aot=getenv("BB_MTL_LOCAL_AOT_MODE");
 Need(persist&&std::string_view(persist)=="0"&&replay&&std::string_view(replay)=="0","headless helper main/replay policy");
 Need(aot&&(std::string_view(aot)=="record"||std::string_view(aot)=="load"),"actual helper local library phase");
 const bool compile=mode=="helpers-prepare";
 if(compile){const char*build=getenv("BB_MTL_LOCAL_AOT_BUILD_ID");Need(std::string_view(aot)=="load"&&build&&Metal::HostPso::Hex(build),"fixed prepare requires verified local build");}
 Stop();Metal::Device device;Metal::Scheduler scheduler(device,"fixed helper preparation");Metal::PsoCache cache(device,artifact,Metal::ReadFloatRules()==Metal::FloatRules::RelaxedMath);cache.ReleaseWarmArchive();
 Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&cache);
 const auto libraries=kernels.PrepareTilingLibraries([]{return bool(interrupted);});Stop();
 const auto prepared=libraries.critical?Metal::HostPso::FixedSeedResult{}:kernels.PrepareFixedSeeds(compile,[]{return bool(interrupted);});kernels.BindPsoCache(nullptr);Stop();
 Json library_rows=Json::array();for(const auto&item:libraries.items)library_rows.push_back({
  {"bits",item.bits},{"macro",item.macro},{"texture",item.texture},{"entry_ready",item.entry_ready},
  {"actual_route",unsigned(item.origin.route)},{"source_sha256",item.origin.source_sha256},
  {"request_key",item.origin.key},{"library_sha256",item.origin.library_sha256}});
 result["tiling_libraries"]={{"schema",Metal::HostPso::TilingLibrarySchema},{"known",Metal::HostPso::TilingLibraryCount},
  {"processed",libraries.items.size()},{"local_ready",libraries.local_ready},{"complete",libraries.complete},
  {"final_psos_prepared",false},{"items",library_rows}};
 result["mode"]=mode;result["fixed_helper_seed_schema"]=Metal::HostPso::FixedSeedSchema;result["fixed_only"]=true;result["storage_opened"]=false;result["renderer_owners_constructed"]=false;result["guest_Runtime_constructed"]=false;
 result["known"]=prepared.progress.known;result["processed"]=prepared.progress.processed;result["ready"]=prepared.progress.ready;result["built"]=prepared.progress.built;result["deferred"]=prepared.progress.deferred;result["failed"]=prepared.progress.failed;result["cancelled"]=prepared.cancelled;
 bool all_local=prepared.items.size()==Metal::HostPso::FixedSeedCount;
 Json rows=Json::array();for(const auto&item:prepared.items){
  const bool local=item.origin_count==(item.kind==Metal::HostPso::Kind::PostProcess?2u:1u)&&std::all_of(item.actual_routes.begin(),item.actual_routes.begin()+item.origin_count,[](auto route){return route==Metal::LocalLibrary::Route::LocalMetallib;});all_local&=local;
  Json routes=Json::array();for(unsigned i=0;i<item.origin_count;++i)routes.push_back(unsigned(item.actual_routes[i]));
  rows.push_back({{"kind",u32(item.kind)},{"request_sha256",item.request_sha256},{"descriptor_sha256",item.descriptor_sha256},{"inputs_verified",item.inputs_verified},{"native_ready",item.native_ready},{"outcome",unsigned(item.outcome.state)},{"archive_sha256",item.outcome.archive_sha256},{"actual_routes",routes}});}
 result["items"]=rows;result["all_actual_libraries_local"]=all_local;
 const auto&c=cache.GetCounters();result["artifact_counts"]={{"attempts",c.artifact_attempts.load()},{"hits",c.artifact_hits.load()},{"built",c.artifact_built.load()},{"compile_attempts",c.artifact_compile_attempts.load()},{"failed",c.artifact_failed.load()},{"deferred",c.artifact_deferred.load()},{"critical",c.artifact_critical.load()}};
 const bool critical=libraries.critical||prepared.progress.state==Metal::HostPso::WarmState::Critical;const bool native_complete=prepared.progress.state==Metal::HostPso::WarmState::Complete&&prepared.progress.ready==Metal::HostPso::FixedSeedCount&&all_local;const bool complete=libraries.complete&&native_complete;
 result["status"]=critical?"stopped":complete?"completed":compile?"deferred":"inputs-collected";result["all_fixed_native_ready"]=native_complete;
 std::printf("FIXED_HELPER_RESULT %s\n",result.dump().c_str());return critical?BB_ARM_EXIT_HOST_PROTECTION:0;
}
#endif

}

#ifndef BB_GEOMETRY_PSO_CONTRACT_ONLY
int RunRecipe(const std::string&mode,const fs::path&packet,const fs::path&scratch,
              std::unique_ptr<Metal::Device>*shared_device=nullptr,
              const std::string&packet_sha={},std::optional<bool>expected_allow={}){@autoreleasepool {
 signal(SIGINT,Signal);signal(SIGTERM,Signal);
 Json result{{"candidate_only",true},{"runtime_cache_import_allowed",false},{"actual_final_read_admission",{{"allowed",false}}},
  {"native_info_srt_deserialize",false},{"walker_registered_or_executed",false},{"artifact_publication_observed",false}};
 try {
  Need(mode=="record"||mode=="prepare"||mode=="helpers-probe"||mode=="helpers-prepare","worker mode");
  Directory(scratch);
  const auto roots=BbPreparationPaths::Actual(BbBundledRuntime::Role::PsoWorker);
  if(roots)roots->Pso(packet,scratch);
  else{
   Need(Child(scratch,fs::path(Shared)/"state")&&fs::is_empty(scratch),"fresh empty Shared source scratch required");
   Directory(packet.parent_path());Need(Child(packet.parent_path(),fs::path(Shared)/"state"),"Shared packet root required");
  }
  auto raw=Read(packet,MaxPacket,packet_sha);Json p=Json::parse(raw);
  if(expected_allow)Need(p.at("allow_compile").get<bool>()==*expected_allow,"batch packet policy differs from frame");
  if(mode=="helpers-probe"||mode=="helpers-prepare")return FixedHelpers(mode,p,scratch,result);
  const bool geometry_packet=GeometryPacket(p);
  Need(p.at("manifest_schema")==2&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false,"candidate packet schema/policy");
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
  if(compute)ckey.Deserialize(keyar);else{key.Deserialize(keyar);data.Deserialize(keyar);}
  GeometryKey(geometry_packet,compute,key);
  if(geometry_packet)Need(!data.vertex_attributes.empty()||key.vertex_buffer_formats[0]==vk::Format::eUndefined,"geometry pipeline missing original vertex attributes");
  std::string retranslation_profile;
  if(geometry_packet){auto profile=Reference(p.at("retranslation_profile"),packet.parent_path(),total);
   (void)Shader::ProfileCacheWire::Decode(profile,{0x42425043,10,7,5});
   retranslation_profile=p.at("retranslation_profile").at("sha256").get<std::string>();}
  Need(p.at("stages").size()>0&&p.at("stages").size()<=6,"stage count");
  std::vector<Stage> stages;std::array<bool,6> present{};
  for(const auto& r:p.at("stages")){
   Stage s;s.logical=r.at("logical_stage");s.hardware=r.at("hardware_stage");const auto sw=Software(s.logical),hw=Hardware(s.hardware);
   Need(!present[sw]&&(geometry_packet||(sw!=u32(Shader::SwStage::Geometry)&&hw!=u32(Shader::HwStage::Geometry))),"duplicate/GS stage rejected");present[sw]=true;
   if(geometry_packet)Need((sw==u32(Shader::SwStage::Vertex)&&(hw==u32(Shader::HwStage::Export)||hw==u32(Shader::HwStage::Vertex)))||
     (sw==u32(Shader::SwStage::Geometry)&&hw==u32(Shader::HwStage::Geometry))||
     (sw==u32(Shader::SwStage::Fragment)&&hw==u32(Shader::HwStage::Fragment)),"geometry ES/GS/FS stage set");
   s.pgm=Hash(r.at("program_hash"));s.permutation=r.at("permutation").get<u64>();s.hash=Hash(r.at("permutation_hash"));
   Need(s.hash==(compute?ckey.value:key.stage_hashes[sw]),"key/meta permutation equality");
   s.spec=Reference(r.at("spec"),packet.parent_path(),total);
   Need(r.at("spec").at("source_offset")==24&&r.at("spec").at("native_spec_only_reader_allowed")==true,"audited spec-only region");
   // Exact immutable spec interval was bounded-audited before packet creation.
   // This original reader does not touch Info, flat tables, SRT or walker code.
   if(geometry_packet&&hw==u32(Shader::HwStage::Geometry)){
    constexpr size_t span_offset=sizeof(Shader::Backend::Bindings)+offsetof(Shader::RuntimeInfo,hw)+offsetof(Shader::HwGeometryRuntimeInfo,vs_copy);
    GeometrySpan(s.spec,r.at("spec_rebinding"),span_offset);
   }
   auto spv=Reference(r.at("spirv"),packet.parent_path(),total);
   if(geometry_packet){auto receipt=Json::parse(Reference(r.at("retranslation_receipt"),packet.parent_path(),total));
    GeometryReceipt(r,receipt,retranslation_profile,hw==u32(Shader::HwStage::Geometry));}
   SpecEnvelope(s.spec);Serialization::Archive specar{std::vector<u8>(s.spec)};Shader::StageSpecialization spec{};
   Need(spec.Deserialize(specar)&&specar.IsEoS(),"strict original spec-only reader extent");
   Need(u32(spec.runtime_info.sw_stage)==sw&&u32(spec.runtime_info.hw_stage)==hw,"actual spec logical/hardware stage");
   if(hw==u32(Shader::HwStage::Vertex))Need(!spec.runtime_info.hw.vs.motion_vectors,"actual motion VS rejected");
   if(geometry_packet&&hw==u32(Shader::HwStage::Geometry)){const auto copy=r.at("gs_copy");auto code=Reference(copy.at("code"),packet.parent_path(),total);
     Need(code.size()>=8&&code.size()%4==0&&Word(code,0)==0xBEEB03FF&&Hash(copy.at("program_hash"))==spec.runtime_info.hw.gs.vs_copy_hash,"geometry copy code/hash bound");
   }
   Need(spv.size()%4==0&&spv.size()>=20&&Word(spv,0)==0x07230203,"complete SPIR-V shape");
   s.spirv.resize(spv.size()/4);memcpy(s.spirv.data(),spv.data(),spv.size());
   Need(Metal::ReadShaderStage(s.spirv)==NativeStage(sw),"actual SPIR-V stage");stages.push_back(std::move(s));
  }
  for(size_t i=0;i<present.size();++i)Need(present[i]==(compute?i==u32(Shader::SwStage::Compute):bool(key.stage_hashes[i])),"complete local stage set");
  Need(compute||present[u32(Shader::SwStage::Vertex)],"graphics VS required");
  Need(present[u32(Shader::SwStage::TessellationControl)]==present[u32(Shader::SwStage::TessellationEval)],"paired tess stages");
  Stop();std::unique_ptr<Metal::Device>own_device;
  // Only the device survives one recipe. Compiler/functions/modules/PSO maps
  // below are local and die before this recipe's autorelease pool drains.
  if(shared_device){if(!*shared_device)*shared_device=std::make_unique<Metal::Device>();}
  else own_device=std::make_unique<Metal::Device>();
  auto& device=shared_device?**shared_device:*own_device;
  Metal::ShaderCompiler compiler{device,scratch,Metal::ReadFloatRules()};
  Need(compiler.CacheDir()==scratch,"actual fresh source cache_dir");
  std::array<Metal::ShaderModule*,VideoCore::MaxShaderStages> modules{};
  for(auto& s:stages){Stop();auto name=VideoCore::PipelineCacheCore::GetShaderName(static_cast<Shader::HwStage>(Hardware(s.hardware)),s.pgm,s.permutation);
   s.module=Metal::CachedVariants::BuildModule(compiler,s.spirv,name);
   // The runtime's variants_only GS path does not require a usable plain ES.
   // Preserve that behavior; the three actual ES kernels must still all succeed.
   const bool geometry_export=geometry_packet&&Software(s.logical)==u32(Shader::SwStage::Vertex);
   Need(s.module&&(s.module->status==Metal::CompileStatus::Ok||
        (geometry_export&&s.module->stage==Metal::ShaderStage::Vertex&&!s.module->spirv.empty())),"original module compile failed");
   modules[Software(s.logical)]=s.module.get();}
  Json functions=Json::array();const Metal::CompiledShader* vs=nullptr,*fragment=nullptr,*cs=nullptr;Metal::TessellationInput tess;Metal::GeometryPipelineInput geometry_input;
  std::unique_ptr<Metal::Geometry::PassThroughCache> pass_through;
  if(geometry_packet)pass_through=std::make_unique<Metal::Geometry::PassThroughCache>(device);
  std::string error;bool unsupported=false;const bool is_tess=present[u32(Shader::SwStage::TessellationControl)];
  bool rect=false;
  if(compute){cs=modules[u32(Shader::SwStage::Compute)]->shader.get();functions.push_back(Function(cs,"compute"));}
  else {
   if(present[u32(Shader::SwStage::Fragment)]){fragment=modules[u32(Shader::SwStage::Fragment)]->shader.get();functions.push_back(Function(fragment,"fragment"));}
   if(geometry_packet){geometry_input.vertex_attributes=data.vertex_attributes;
    const bool prepared=Metal::CachedVariants::Geometry(compiler,*pass_through,key,modules,fragment,geometry_input,error,unsupported);Need(prepared,error.c_str());
    for(size_t i=0;i<3;++i)functions.push_back(Function(geometry_input.es[i],"geometry-export-index-"+std::to_string(i)));
    functions.push_back(Function(geometry_input.gs,"geometry-kernel"));functions.push_back(Function(geometry_input.pass_through,"geometry-pass-through"));}
   else if(is_tess){tess.vertex_attributes=data.vertex_attributes;const bool prepared=Metal::CachedVariants::Tessellation(compiler,modules,tess,error,unsupported);Need(prepared,error.c_str());
    for(size_t i=0;i<3;++i)functions.push_back(Function(tess.vertex[i],"tess-vertex-"+std::to_string(i)));
    functions.push_back(Function(tess.control,"tess-control"));functions.push_back(Function(tess.eval,"tess-eval"));}
   else {auto*m=modules[u32(Shader::SwStage::Vertex)];rect=key.prim_type==AmdGpu::PrimitiveType::RectList;
    vs=rect?Metal::CachedVariants::RectList(compiler,m,error):m->shader.get();Need(vs,error.c_str());functions.push_back(Function(vs,rect?"rectlist-original-rewrite":"vertex"));}
  }
  Stop();result["functions"]=functions;result["fresh_actual_source_cache_dir"]=scratch.string();
  bool any_source=false;for(const auto& f:functions)any_source|=f.at("actual_route")=="source";result["source_fallback"]=any_source;
  result["family"]=geometry_packet?"owned-gcn-geometry":compute?"compute":is_tess?"tessellation":rect?"rectlist":"ordinary-graphics";
  if(geometry_packet){result["geometry_pso_schema"]=1;result["source_retranslation_receipts_verified"]=true;result["retired_geometry_span_read"]=false;
   result["geometry_index_variants"]=3;result["expected_native_artifacts"]=5;result["native_function_count"]=functions.size();}
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
   else if(geometry_packet){const auto* pipeline=pso.GetGeometryPipeline(key,geometry_input,fragment);ready=pipeline!=nullptr;
    // Preserve BB_GS_DEBUG's original internal fragment replacement and report
    // its actual provenance too; never label the original FS as that function.
    if(pipeline&&pipeline->render&&pipeline->render->fs&&pipeline->render->fs!=fragment){
     auto actual=Function(pipeline->render->fs,"geometry-actual-render-fragment");functions.push_back(actual);
     result["functions"]=functions;result["native_function_count"]=functions.size();
     result["source_fallback"]=any_source||actual.at("actual_route")=="source";
    }
   }
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

constexpr unsigned BatchMaxRecipes=16;
int RunBatch(const std::string&phase){
 Need(phase=="record"||phase=="prepare","one explicit batch phase required");
 signal(SIGINT,Signal);signal(SIGTERM,Signal);
 std::unique_ptr<Metal::Device>device;
 unsigned processed=0,failed=0;std::optional<bool>batch_allow;
 std::array<char,32770>line{};
 try {
  while(processed<BatchMaxRecipes&&std::fgets(line.data(),line.size(),stdin)){
   Stop();const auto length=std::strlen(line.data());
   Need(length>0&&length<=32768&&line[length-1]=='\n',"bounded complete batch frame");
   auto frame=Json::parse(std::vector<u8>(reinterpret_cast<const u8*>(line.data()),reinterpret_cast<const u8*>(line.data())+length));
   Need(frame.at("schema")==1&&frame.at("phase")==phase,"batch schema/phase");
   const auto sequence=frame.at("sequence").get<unsigned>();
   const auto request=frame.at("request_id").get<std::string>();
   const auto packet_sha=frame.at("packet_sha256").get<std::string>();
   const bool allow=frame.at("allow_compile").get<bool>();
   Need(sequence==processed+1&&Metal::FunctionIdentity::Hex(request)&&Metal::FunctionIdentity::Hex(packet_sha),"batch exact request identity/sequence");
   Need(phase!="record"||!allow,"record batch cannot compile native PSOs");
   if(batch_allow)Need(*batch_allow==allow,"batch compilation policy cannot change");else batch_allow=allow;
   Json mark{{"schema",1},{"sequence",sequence},{"request_id",request},{"packet_sha256",packet_sha}};
   std::printf("GAME_PSO_BATCH_BEGIN %s\n",mark.dump().c_str());std::fflush(stdout);
   const auto code=RunRecipe(phase,frame.at("packet").get<std::string>(),frame.at("scratch").get<std::string>(),&device,packet_sha,allow);
   mark["exit"]=code;std::printf("GAME_PSO_BATCH_END %s\n",mark.dump().c_str());std::fflush(stdout);
   ++processed;if(code)++failed;
   if(code==70||code==79)return code;
  }
  Need(!std::ferror(stdin),"batch input error");
  Json result{{"schema",1},{"processed",processed},{"failed",failed},{"phase",phase},
    {"status",failed?"partial":"completed"}};
  std::printf("GAME_PSO_BATCH_DONE %s\n",result.dump().c_str());std::fflush(stdout);
  return failed?65:0;
 }catch(const std::exception&e){
  std::fprintf(stderr,"GAME_PSO_BATCH_ERROR %s\n",e.what());return interrupted?70:65;
 }
}
int main(int argc,char**argv){@autoreleasepool {
 // Capability is queried before any Device construction, never inferred from a
 // manifest boolean. The old CLI retains the same recipe validation path.
 if(argc==2&&std::string_view(argv[1])=="--game-pso-batch-capability"){
  std::puts("GAME_PSO_BATCH_CAPABILITY {\"schema\":1,\"max_recipes\":16,\"shared\":\"device-only\",\"per_recipe_native_validation\":true}");return 0;
 }
 if(argc==2&&std::string_view(argv[1])=="--fixed-helper-seed-schema"){
  std::printf("FIXED_HELPER_CAPABILITY %u\n",Metal::HostPso::FixedSeedSchema);return 0;
 }
 // No launcher return-to-menu/restart 75/76 values are used by this contract.
 if(argc==3&&std::string_view(argv[1])=="--batch"){
  try{return RunBatch(argv[2]);}catch(const std::exception&e){std::fprintf(stderr,"GAME_PSO_BATCH_ERROR %s\n",e.what());return 65;}
 }
 if(argc!=4){std::fprintf(stderr,"usage: game-pso-preparer record|prepare|helpers-probe|helpers-prepare packet.json fresh-scratch\n");return 64;}
 return RunRecipe(argv[1],argv[2],argv[3]);
}}
#endif
