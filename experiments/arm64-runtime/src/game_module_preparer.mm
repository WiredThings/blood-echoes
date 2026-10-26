// SPDX-License-Identifier: GPL-2.0-or-later
// CPU-only, owned GCN retranslation. No Device, old Info/SRT reader or runtime cache.
#include "preparation_json.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <signal.h>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include "common/serdes.h"
#include "shader_recompiler/compile_snapshot.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/resource.h"
#include "shader_recompiler/specialization.h"
#include "video_core/renderer_metal/mtl_function_identity.h"
namespace {
using Json=PreparationJson::Value;namespace fs=std::filesystem;
constexpr const char* Shared="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype";
constexpr size_t MaxBlob=1024*1024,MaxPacket=256*1024,MaxTotal=2*MaxBlob;
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
// Validate raw booleans and array discriminators before the original reader
// creates typed RuntimeInfo/specialization objects. Never inspect inactive unions.
void RuntimeEnvelope(const std::vector<u8>& b,bool geometry) {
 constexpr size_t r=sizeof(Shader::Backend::Bindings),h=r+offsetof(Shader::RuntimeInfo,hw),w=r+offsetof(Shader::RuntimeInfo,sw),prop=r+offsetof(Shader::RuntimeInfo,props);
 Need(b.size()>=r+sizeof(Shader::RuntimeInfo),"runtime raw extent");
 const auto flag=[&](size_t at){Need(b.at(at)<=1,"runtime raw boolean");};
 const auto hw=Word(b,r+offsetof(Shader::RuntimeInfo,hw_stage)),sw=Word(b,r+offsetof(Shader::RuntimeInfo,sw_stage));
 Need((hw==u32(Shader::HwStage::Compute)&&sw==u32(Shader::SwStage::Compute))||
      ((hw==u32(Shader::HwStage::Vertex)||hw==u32(Shader::HwStage::Export))&&sw==u32(Shader::SwStage::Vertex))||
      (hw==u32(Shader::HwStage::Fragment)&&sw==u32(Shader::SwStage::Fragment))||
      (geometry&&hw==u32(Shader::HwStage::Geometry)&&sw==u32(Shader::SwStage::Geometry)),"runtime plain stage discriminators");
 Need(geometry==(hw==u32(Shader::HwStage::Geometry)),"geometry schema and stage must match");
 if(sw==u32(Shader::SwStage::Vertex)) {
  using T=Shader::SwVertexRuntimeInfo;flag(w+offsetof(T,tess_emulated_primitive));
  const u32 base=hw==u32(Shader::HwStage::Export)?0xcc:0x4c;
  for(auto at:{offsetof(T,vertex_sgpr_offset),offsetof(T,instance_sgpr_offset)}){
   u16 reg;memcpy(&reg,b.data()+w+at,sizeof(reg));Need(reg==0||(reg>=base&&reg-base<16),"runtime vertex indirect SGPR range");
  }
 }
 using Props=Shader::RuntimeInfo::Properties;
 for(auto at:{offsetof(Props,fp_denorm_mode32),offsetof(Props,fp_denorm_mode16_64),offsetof(Props,fp_round_mode32),offsetof(Props,fp_round_mode16_64)})Need(Word(b,prop+at)<=3,"runtime FP mode enum");
 Need(Word(b,prop+offsetof(Props,num_user_data))<=16&&Word(b,prop+offsetof(Props,num_input_vgprs))<=256&&Word(b,prop+offsetof(Props,num_allocated_vgprs))<=256,"runtime register count");
 if(hw==u32(Shader::HwStage::Compute)){
  using T=Shader::HwComputeRuntimeInfo;uint64_t group=1;
  for(size_t i=0;i<3;++i){flag(h+offsetof(T,tgid_enable)+i);const auto dim=Word(b,h+offsetof(T,workgroup_size)+4*i);Need(dim>0&&dim<=1024,"runtime CS dimension");group*=dim;}
  Need(group<=1024&&Word(b,h+offsetof(T,shared_memory_size))<=1024*1024,"runtime CS bounded workgroup/shared memory");
 } else if(hw==u32(Shader::HwStage::Vertex)){
  using T=Shader::HwVertexRuntimeInfo;
  for(auto at:{offsetof(T,emulate_depth_negative_one_to_one),offsetof(T,clip_disable),offsetof(T,motion_vectors)})flag(h+at);
  flag(w+offsetof(Shader::SwVertexRuntimeInfo,tess_emulated_primitive));
  Need(Word(b,h+offsetof(T,num_outputs))<=32,"runtime VS output count");
  for(size_t i=0;i<12;++i)Need(b.at(h+offsetof(T,outputs)+i)<=u8(Shader::Output::ClipDist7),"runtime VS output enum");
 } else if(hw==u32(Shader::HwStage::Geometry)) {
  using T=Shader::HwGeometryRuntimeInfo;static_assert(sizeof(T::vs_copy)==16);
  Need(Word(b,h+offsetof(T,num_outputs))<=32&&Word(b,h+offsetof(T,num_invocations))==1,"bounded geometry output/invocation count");
  for(size_t i=0;i<12;++i)Need(b.at(h+offsetof(T,outputs)+i)<=u8(Shader::Output::ClipDist7),"geometry output enum");
  const auto vertices=Word(b,h+offsetof(T,output_vertices));const auto in=Word(b,h+offsetof(T,in_vertex_data_size));const auto out=Word(b,h+offsetof(T,out_vertex_data_size));
  Need(vertices>0&&vertices<=1024&&in>=4&&in%4==0&&in/4-1<=32&&out>0&&out<=1024,"bounded geometry vertex data");
  Need(Word(b,h+offsetof(T,in_primitive))==u32(AmdGpu::PrimitiveType::PointList)&&Word(b,h+offsetof(T,mode))==u32(AmdGpu::GsScenario::ScenarioG),"owned geometry point-list ScenarioG subset");
  for(size_t i=0;i<4;++i)Need(Word(b,h+offsetof(T,out_primitive)+4*i)<=u32(AmdGpu::GsOutputPrimitiveType::TriangleStrip),"geometry output primitive enum");
  for(size_t i=0;i<16;++i)Need(b.at(h+offsetof(T,vs_copy)+i)==0,"retired geometry span must be zero before typed reader");
 } else if(hw==u32(Shader::HwStage::Export)) {
  Need(Word(b,h+offsetof(Shader::HwExportRuntimeInfo,vertex_data_size))<=4096,"runtime export vertex extent");
 } else {
  using T=Shader::HwFragmentRuntimeInfo;using I=T::PsInput;
  const auto n=Word(b,h+offsetof(T,num_inputs));Need(n<=32,"runtime FS input count");
  // All bool representations are checked, including inactive array entries,
  // because the original struct reader copies the full fixed-size array.
  for(size_t i=0;i<32;++i){auto at=h+offsetof(T,inputs)+sizeof(I)*i;flag(at+offsetof(I,is_default));flag(at+offsetof(I,is_flat));if(i<n)Need(b.at(at+offsetof(I,param_index))<=31&&b.at(at+offsetof(I,default_value))<=3,"runtime FS input enum");}
  for(auto at:{offsetof(T,front_face_all_bits),offsetof(T,dual_source_blending),offsetof(T,clip_distance_emulation),offsetof(T,motion_vectors)})flag(h+at);
  const auto samples=b.at(h+offsetof(T,num_samples));Need(samples==1||samples==2||samples==4||samples==8||samples==16,"runtime sample count");
  Need(Word(b,h+offsetof(T,z_export_format))<=u32(AmdGpu::ShaderExportFormat::ABGR_32),"runtime depth export enum");
 }
}
void SpecEnvelope(const std::vector<u8>& b,bool geometry) {
 RuntimeEnvelope(b,geometry);
 size_t at=sizeof(Shader::Backend::Bindings)+sizeof(Shader::RuntimeInfo);
 const auto take=[&](size_t n){Need(at<=b.size()&&n<=b.size()-at,"spec allocation envelope");auto*p=b.data()+at;at+=n;return p;};
 const auto count=[&](){uint64_t n;memcpy(&n,take(8),8);return n;};
 Need(count()==128,"spec bitset shape");const auto*bits=take(128);
 for(size_t i=0;i<128;++i)Need(bits[i]=='0'||bits[i]=='1',"spec bitset value");
 Need(count()==0,"offline module fetch requires captured register reads");
 const auto swizzle=[](const u8*p){for(size_t j=0;j<4;++j)Need(p[j]<=1||(p[j]>=4&&p[j]<=7),"specialization component enum");};
 size_t kind=0;
 for(size_t stride:{sizeof(Shader::VsAttribSpecialization),sizeof(Shader::BufferSpecialization),sizeof(Shader::ImageSpecialization),sizeof(Shader::FMaskSpecialization),sizeof(Shader::SamplerSpecialization)}) {
  const auto n=count();Need(n<=128,"specialization vector allocation bound");const auto*items=take(n*stride);
  for(size_t i=0;i<n;++i){const auto*p=items+i*stride;
   const auto dword=[&](size_t offset){u32 v;memcpy(&v,p+offset,4);return v;};
   if(kind==0){using T=Shader::VsAttribSpecialization;Need(p[offsetof(T,num_class)]<=u8(AmdGpu::NumberClass::Uint),"attribute number class");swizzle(p+offsetof(T,dst_select));}
   if(kind==1){using T=Shader::BufferSpecialization;Need(dword(offsetof(T,num_conversion))<=u32(AmdGpu::NumberConversion::SrgbToNorm),"buffer conversion enum");swizzle(p+offsetof(T,dst_select));}
   if(kind==2){using T=Shader::ImageSpecialization;for(auto off:{offsetof(T,is_integer),offsetof(T,is_storage),offsetof(T,is_cube),offsetof(T,is_srgb)})Need(p[off]<=1,"image raw boolean");u64 type;memcpy(&type,p+offsetof(T,type),8);Need(type==0||(type>=8&&type<=15),"image type enum");Need(dword(offsetof(T,num_conversion))<=u32(AmdGpu::NumberConversion::SrgbToNorm)&&dword(offsetof(T,num_bindings))<=128,"image conversion/binding bound");swizzle(p+offsetof(T,dst_select));}
  }
  ++kind;
 }
 Need(at==b.size(),"exact spec-only interval without forbidden tail");
}

std::vector<u8> Reference(const Json&r,const fs::path&root,size_t&total){
 fs::path p=r.at("path").get<std::string>();Need(p.parent_path()==root&&p.filename()==r.at("name").get<std::string>(),"module file outside one frozen root");
 Need(Metal::FunctionIdentity::Hex(r.at("sha256").get<std::string>()),"required file SHA256 identity");auto b=Read(p,MaxBlob,r.at("sha256"));Need(b.size()==r.at("bytes").get<size_t>(),"module file byte count");total+=b.size();Need(total<=MaxTotal,"module whole-input budget");return b;
}
std::vector<u32> Words(const std::vector<u8>&b){Need(b.size()%4==0,"dword extent");std::vector<u32>out(b.size()/4);if(!b.empty())memcpy(out.data(),b.data(),b.size());return out;}
void WriteExclusive(const fs::path&p,std::span<const u8>b){
 int fd=open(p.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600);Need(fd>=0,"exclusive output creation");
 struct Guard{int fd;~Guard(){close(fd);}}guard{fd};size_t at=0;
 while(at<b.size()){auto n=write(fd,b.data()+at,b.size()-at);if(n<0&&errno==EINTR)continue;Need(n>0,"module output write");at+=n;}Need(fsync(fd)==0,"module output sync");
}
Shader::Profile Profile(const std::vector<u8>&raw){
 Need(raw.size()==16+sizeof(Shader::Profile)&&Word(raw,0)==0x42425043&&Word(raw,4)==10&&Word(raw,8)==7&&Word(raw,12)==5,"profile serialization ABI");
 #define MODULE_PROFILE_BOOL_FIELD(name) Need(raw[16+offsetof(Shader::Profile,name)]<=1,"profile bool representation");
 #include "module_profile_bool_fields.inc"
 #undef MODULE_PROFILE_BOOL_FIELD
 Shader::Profile p{};memcpy(&p,raw.data()+16,sizeof(p));
 Need(p.supported_spirv>=0x10000&&p.supported_spirv<=0x10600&&p.max_viewport_width<=65536&&p.max_viewport_height<=65536&&p.max_shared_memory_size<=1024*1024&&p.sparse_page_shift<64,"profile limits");return p;
}
}
int main(int argc,char**argv){@autoreleasepool{
 if(argc!=3){fputs("usage: game-module-preparer packet.json fresh-output\n",stderr);return 64;}
 signal(SIGINT,Signal);signal(SIGTERM,Signal);
 Json result{{"candidate_only",true},{"runtime_cache_import_allowed",false},{"gpu_started",false},{"native_info_srt_deserialized",false},{"old_walker_executed",false},{"guest_memory_read",false}};
 try{
  const fs::path packet=argv[1],output=argv[2];Directory(packet.parent_path());Directory(output);
  const fs::path evidence=fs::path(Shared)/"evidence";
  Need(Child(packet.parent_path(),evidence)&&Child(output,evidence)&&fs::is_empty(output),"new owned Shared evidence roots required");
  Need(output!=packet.parent_path()&&!Child(output,packet.parent_path())&&!Child(packet.parent_path(),output),"input/output separation");
  auto p=Json::parse(Read(packet,MaxPacket));
  const bool geometry=p.at("kind")=="owned-gcn-geometry-module-input";
  Need(p.at("packet_schema")==1&&(p.at("kind")=="owned-gcn-module-input"||geometry)&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false&&p.at("native_info_srt_allowed")==false,"module packet policy");
  auto c=p.at("constraints");Need(c.at("old_walker_execution_allowed")==false&&c.at("guest_dereference_allowed")==false&&c.at("final_runtime_import_allowed")==false&&c.at("requires_regenerated_srt_equality")==true&&c.at("requires_current_profile_match")==true,"module ownership constraints");
  Need(p.at("program_base").at("known")==false&&p.at("program_base").at("value")==Json{}&&p.at("program_base").at("required_gate")=="reject-offset-by-program-base","explicit unknown program base");
  Need(p.at("layout").at("bindings_size")==sizeof(Shader::Backend::Bindings)&&p.at("layout").at("runtime_size")==sizeof(Shader::RuntimeInfo)&&p.at("layout").at("profile_size")==sizeof(Shader::Profile),"actual native module layout");
  size_t total=0;const auto root=packet.parent_path();auto profile=Profile(Reference(p.at("profile"),root,total));auto code=Reference(p.at("code"),root,total);auto specbytes=Reference(p.at("spec"),root,total);auto flat=Reference(p.at("flat"),root,total);auto walker=p.at("walker_bytes")==0?std::vector<u8>{}:Reference(p.at("walker"),root,total);Need(p.at("walker_bytes")!=0||p.at("walker")==Json{},"empty walker must be null");auto expected=Reference(p.at("expected_spirv"),root,total);
  Need(code.size()>=8&&code.size()<=Shader::CompileSnapshot::MaxCodeBytes&&Word(code,0)==0xBEEB03FF,"actual bounded GCN prefix");
  Need(p.at("source_provenance").at("title_id")=="CUSA03173"&&p.at("source_provenance").at("app_ver")=="01.09"&&p.at("source_provenance").at("code_prefix_verified")==true&&p.at("source_provenance").at("code_sha256")==p.at("code").at("sha256"),"module exact code provenance");
  SpecEnvelope(specbytes,geometry);Serialization::Archive ar{std::vector<u8>(specbytes)};Shader::StageSpecialization spec{};Need(spec.Deserialize(ar)&&ar.IsEoS()&&!spec.fetch_shader_data,"original exact spec reader, no fetch");
  const auto hw=spec.runtime_info.hw_stage;const auto sw=spec.runtime_info.sw_stage;
  Need(geometry==(hw==Shader::HwStage::Geometry),"packet kind/stage domain");
  Need((hw==Shader::HwStage::Compute&&sw==Shader::SwStage::Compute&&p.at("hardware_stage")=="compute"&&p.at("logical_stage")=="compute")||
       (hw==Shader::HwStage::Fragment&&sw==Shader::SwStage::Fragment&&p.at("hardware_stage")=="fragment"&&p.at("logical_stage")=="fragment")||
       (hw==Shader::HwStage::Vertex&&sw==Shader::SwStage::Vertex&&p.at("hardware_stage")=="vertex"&&p.at("logical_stage")=="vertex")||
       (hw==Shader::HwStage::Export&&sw==Shader::SwStage::Vertex&&p.at("hardware_stage")=="export"&&p.at("logical_stage")=="vertex")||
       (geometry&&hw==Shader::HwStage::Geometry&&sw==Shader::SwStage::Geometry&&p.at("hardware_stage")=="geometry"&&p.at("logical_stage")=="geometry"),"complete plain stage pair");
  if(hw==Shader::HwStage::Vertex)Need(!spec.runtime_info.hw.vs.motion_vectors,"module motion VS capture unavailable");
  if(hw==Shader::HwStage::Fragment)Need(!spec.runtime_info.hw.fs.motion_vectors,"module motion FS capture unavailable");
  Need(p.at("user_data_words")==16&&flat.size()%4==0&&flat.size()>=64&&flat.size()<=Shader::CompileSnapshot::MaxFlatBytes&&p.at("flat_dwords")==flat.size()/4,"complete owned flat/UD extent");
  Need(walker.size()<=Shader::CompileSnapshot::MaxWalkerBytes&&walker.size()%12==0&&p.at("walker_bytes")==walker.size(),"data-only SRT triplets");
  auto inputs=std::make_shared<Shader::CompileSnapshot>();inputs->profile=profile;inputs->runtime=spec.runtime_info;inputs->start=spec.start;inputs->hash=Hash(p.at("program_hash"));inputs->hw_stage=hw;inputs->sw_stage=sw;
  // This scalar is not an accepted guest address. The separate translation API
  // rejects its only dependency before any resource patching can consume it.
  inputs->program_base=0;inputs->code=Words(code);inputs->flat=Words(flat);inputs->flat_dwords=inputs->flat.size();inputs->user_data.assign(inputs->flat.begin(),inputs->flat.begin()+16);inputs->walker=Words(walker);
  if(geometry){
   Need(c.at("requires_owned_GS_copy_rebinding")==true&&c.at("requires_explicit_geometry_scalar_equality")==true,"required owned geometry policy");
   const auto copy=p.at("gs_copy"),rebind=p.at("spec_rebinding");
   Need(Metal::FunctionIdentity::Hex(rebind.at("original_spec_sha256").get<std::string>()),"original geometry spec identity");
   constexpr size_t span_offset=sizeof(Shader::Backend::Bindings)+offsetof(Shader::RuntimeInfo,hw)+offsetof(Shader::HwGeometryRuntimeInfo,vs_copy);
   Need(rebind.at("runtime_span_offset")==span_offset&&rebind.at("bytes")==16&&rebind.at("serialized_span_zeroed")==true&&rebind.at("retired_pointer_exported")==false,"owned geometry span rebinding policy");
   const auto raw=Reference(copy.at("code"),root,total);Need(raw.size()>=8&&raw.size()<=Shader::CompileSnapshot::MaxCodeBytes&&Word(raw,0)==0xBEEB03FF&&copy.at("hardware_stage")=="vertex","owned geometry copy code");
   Need(copy.at("words")==raw.size()/4&&rebind.at("original_copy_words")==raw.size()/4&&copy.at("program_hash")==rebind.at("original_copy_hash")&&Hash(copy.at("program_hash"))==spec.runtime_info.hw.gs.vs_copy_hash,"owned geometry exact copy identity");
   const auto proof=copy.at("source_provenance"),main_proof=p.at("source_provenance");
   Need(proof.at("title_id")=="CUSA03173"&&proof.at("app_ver")=="01.09"&&proof.at("code_prefix_verified")==true&&proof.at("code_sha256")==copy.at("code").at("sha256")&&proof.at("container_sha256")==main_proof.at("container_sha256")&&proof.at("entry_sha256")==main_proof.at("entry_sha256"),"same-source owned geometry copy provenance");
   inputs->gs_copy=Words(raw);spec.runtime_info.hw.gs.vs_copy=inputs->gs_copy;result["owned_geometry_copy_rebound"]=true;result["geometry_copy_bytes"]=raw.size();
  }
  Shader::CompileContext context;auto info=inputs->MakeInfo();auto runtime=inputs->RebindRuntime();auto bindings=inputs->start;
  Stop();auto ir=Shader::TranslateProgramWithoutProgramBase(inputs->code,context.pools,info,runtime,profile);
  Shader::StageSpecialization actual{info,runtime,profile,inputs->start};
  Need(actual.vs_attribs.size()==spec.vs_attribs.size()&&actual.buffers.size()==spec.buffers.size()&&actual.images.size()==spec.images.size()&&actual.fmasks.size()==spec.fmasks.size()&&actual.samplers.size()==spec.samplers.size(),"retranslated specialization vector shape differs");
  if(geometry){const auto&a=actual.runtime_info.hw.gs;const auto&b=spec.runtime_info.hw.gs;
   Need(a.in_vertex_data_size==b.in_vertex_data_size&&a.out_vertex_data_size==b.out_vertex_data_size&&a.num_invocations==b.num_invocations,"exact geometry size/invocation state");}
  Need(actual.Valid()&&actual==spec,"retranslated specialization differs from captured state");
  auto words=Shader::Backend::SPIRV::EmitSPIRV(profile,runtime,ir,bindings);Need(!words.empty()&&words.size()<=512*1024/4,"bounded SPIR-V output");
  auto bytes=std::span<const u8>(reinterpret_cast<const u8*>(words.data()),words.size()*4);
  #if defined(BB_MODULE_DIAGNOSTIC)
  if(bytes.size()!=expected.size()||!std::equal(bytes.begin(),bytes.end(),expected.begin())){
   // Explicit development-only diagnostic, never a prepared module/cache entry.
   WriteExclusive(output/"rejected-generated.spv",bytes);
  }
  #endif
  Need(bytes.size()==expected.size()&&std::equal(bytes.begin(),bytes.end(),expected.begin()),"retranslated SPIR-V differs from observed module");
  Stop();WriteExclusive(output/"module.spv",bytes);
  result["status"]="completed";result["program_hash"]=p.at("program_hash");result["spirv_bytes"]=bytes.size();result["spirv_sha256"]=Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(bytes.data()),bytes.size()});result["expected_spirv_equal"]=true;result["current_generated_srt_verified"]=true;result["unknown_program_base_rejected_before_use"]=true;result["full_final_pso_ready"]=false;
  auto text=result.dump();WriteExclusive(output/"result.json",{reinterpret_cast<const u8*>(text.data()),text.size()});printf("GAME_MODULE_RESULT %s\n",text.c_str());return 0;
 }catch(const std::exception&e){result["status"]="rejected";result["error"]=e.what();printf("GAME_MODULE_RESULT %s\n",result.dump().c_str());return 65;}
}}
