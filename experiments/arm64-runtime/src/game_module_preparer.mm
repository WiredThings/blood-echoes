// SPDX-License-Identifier: GPL-2.0-or-later
// CPU-only, owned GCN retranslation. No Device, old Info/SRT reader or runtime cache.
#include "preparation_json.h"
#include "preparation_runtime_paths.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <dlfcn.h>
#include <filesystem>
#include <fcntl.h>
#include <signal.h>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include "common/serdes.h"
#include "common/hash.h"
#include "shader_recompiler/compile_snapshot.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/resource.h"
#include "shader_recompiler/specialization.h"
#include "video_core/renderer_metal/mtl_function_identity.h"
#include "video_core/pipeline_cache_core_types.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#if defined(BB_WITH_OWNED_COMPANION_CONSUMER)
#include "fetch_compile_producer.h"
#endif
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
void RuntimeEnvelope(const std::vector<u8>& b,bool geometry,bool projection) {
 constexpr size_t r=sizeof(Shader::Backend::Bindings),h=r+offsetof(Shader::RuntimeInfo,hw),w=r+offsetof(Shader::RuntimeInfo,sw),prop=r+offsetof(Shader::RuntimeInfo,props);
 Need(b.size()>=r+sizeof(Shader::RuntimeInfo),"runtime raw extent");
 const auto flag=[&](size_t at){Need(b.at(at)<=1,"runtime raw boolean");};
 const auto hw=Word(b,r+offsetof(Shader::RuntimeInfo,hw_stage)),sw=Word(b,r+offsetof(Shader::RuntimeInfo,sw_stage));
 Need((hw==u32(Shader::HwStage::Compute)&&sw==u32(Shader::SwStage::Compute))||
      ((hw==u32(Shader::HwStage::Vertex)||hw==u32(Shader::HwStage::Export))&&sw==u32(Shader::SwStage::Vertex))||
      (hw==u32(Shader::HwStage::Fragment)&&sw==u32(Shader::SwStage::Fragment))||
      (projection&&hw==u32(Shader::HwStage::Local)&&sw==u32(Shader::SwStage::Vertex))||
      (geometry&&hw==u32(Shader::HwStage::Geometry)&&sw==u32(Shader::SwStage::Geometry)),"runtime plain stage discriminators");
 Need(geometry==(hw==u32(Shader::HwStage::Geometry)),"geometry schema and stage must match");
 if(projection)Need((hw==u32(Shader::HwStage::Vertex)||hw==u32(Shader::HwStage::Local))&&sw==u32(Shader::SwStage::Vertex),"projection VS/Local stage domain");
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
  Need(Word(b,h+offsetof(T,num_outputs))<=3,"runtime VS output count");
  for(size_t i=0;i<12;++i)Need(b.at(h+offsetof(T,outputs)+i)<=u8(Shader::Output::ClipDist7),"runtime VS output enum");
 } else if(hw==u32(Shader::HwStage::Geometry)) {
  using T=Shader::HwGeometryRuntimeInfo;static_assert(sizeof(T::vs_copy)==16);
  Need(Word(b,h+offsetof(T,num_outputs))<=3&&Word(b,h+offsetof(T,num_invocations))==1,"bounded geometry output/invocation count");
  for(size_t i=0;i<12;++i)Need(b.at(h+offsetof(T,outputs)+i)<=u8(Shader::Output::ClipDist7),"geometry output enum");
  const auto vertices=Word(b,h+offsetof(T,output_vertices));const auto in=Word(b,h+offsetof(T,in_vertex_data_size));const auto out=Word(b,h+offsetof(T,out_vertex_data_size));
  Need(vertices>0&&vertices<=1024&&in>=4&&in%4==0&&in/4-1<=32&&out>0&&out<=1024,"bounded geometry vertex data");
  Need(Word(b,h+offsetof(T,in_primitive))==u32(AmdGpu::PrimitiveType::PointList)&&Word(b,h+offsetof(T,mode))==u32(AmdGpu::GsScenario::ScenarioG),"owned geometry point-list ScenarioG subset");
  for(size_t i=0;i<4;++i)Need(Word(b,h+offsetof(T,out_primitive)+4*i)<=u32(AmdGpu::GsOutputPrimitiveType::TriangleStrip),"geometry output primitive enum");
  for(size_t i=0;i<16;++i)Need(b.at(h+offsetof(T,vs_copy)+i)==0,"retired geometry span must be zero before typed reader");
 } else if(hw==u32(Shader::HwStage::Local)) {
  const auto stride=Word(b,h+offsetof(Shader::HwLocalRuntimeInfo,ls_stride));
  Need(projection&&stride>0&&stride<=4096&&stride%4==0,"projection Local stride");
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
void SpecEnvelope(const std::vector<u8>& b,bool geometry,bool projection) {
 RuntimeEnvelope(b,geometry,projection);
 size_t at=sizeof(Shader::Backend::Bindings)+sizeof(Shader::RuntimeInfo);
 const auto take=[&](size_t n){Need(at<=b.size()&&n<=b.size()-at,"spec allocation envelope");auto*p=b.data()+at;at+=n;return p;};
 const auto count=[&](){uint64_t n;memcpy(&n,take(8),8);return n;};
 Need(count()==128,"spec bitset shape");const auto*bits=take(128);
 for(size_t i=0;i<128;++i)Need(bits[i]=='0'||bits[i]=='1',"spec bitset value");
 const auto fetch=count();Need(projection?fetch==sizeof(Shader::Gcn::FetchShaderData):fetch==0,"offline module fetch requires captured register reads or explicit projection");
 if(fetch) {
  const auto*size=take(4);u32 bytes;memcpy(&bytes,size,4);Need(bytes>0&&bytes<=65536,"projection fetch byte bound");
  const auto*offsets=take(2);for(size_t i=0;i<2;++i)Need(s8(offsets[i])>=-1&&s8(offsets[i])<=15,"projection fetch SGPR offset");
  const auto n=count();Need(n>0&&n<=32,"projection fetch attribute bound");take(n*sizeof(Shader::Gcn::VertexAttribute));
 }
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
struct OutputTransaction;
OutputTransaction*active_outputs{};
struct OutputTransaction{
 struct Owned{fs::path path;dev_t device;ino_t inode;};std::vector<Owned>owned;bool committed{};
 OutputTransaction(){Need(!active_outputs,"one owned output transaction");owned.reserve(4);active_outputs=this;}
 void Add(int fd,const fs::path&path){struct stat st{};Need(owned.size()<4&&fstat(fd,&st)==0&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_nlink==1,"owned output identity");owned.push_back({path,st.st_dev,st.st_ino});}
 ~OutputTransaction(){active_outputs=nullptr;if(committed)return;for(const auto&file:owned){struct stat current{};if(lstat(file.path.c_str(),&current)==0&&S_ISREG(current.st_mode)&&current.st_dev==file.device&&current.st_ino==file.inode)unlink(file.path.c_str());}}
};
void CommitOutputs(){Need(active_outputs,"owned output transaction required");active_outputs->committed=true;}
void WriteExclusive(const fs::path&p,std::span<const u8>b){
 int fd=open(p.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600);Need(fd>=0,"exclusive output creation");
 struct Guard{int fd;~Guard(){close(fd);}}guard{fd};Need(active_outputs,"owned output scope");active_outputs->Add(fd,p);size_t at=0;
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

u64 Qword(const std::vector<u8>&b,size_t at){Need(at<=b.size()&&8<=b.size()-at,"u64 input extent");u64 value;memcpy(&value,b.data()+at,8);return value;}
AmdGpu::NumberConversion UniqueFetchConversion(u32 format,AmdGpu::NumberClass cls) {
 std::optional<AmdGpu::NumberConversion> unique;
 // Enumerate the physical V# field domains, not fabricated Buffer descriptors.
 for(u32 d=0;d<16;++d)for(u32 n=0;n<8;++n) {
  const auto df=AmdGpu::DataFormat(d);const auto nf=AmdGpu::NumberFormat(n);
  const auto remapped=AmdGpu::RemapNumberFormat(nf,df);
  if(u32(Vulkan::LiverpoolToVK::TrySurfaceFormat(AmdGpu::RemapDataFormat(df),remapped))!=format||AmdGpu::GetNumberClass(remapped)!=cls)continue;
  const auto conversion=AmdGpu::TryMapNumberConversion(nf,df);
  if(!conversion)continue; // This pair cannot produce a successful original compilation.
  Need(!unique||*unique==*conversion,"ambiguous original fetch number conversion");unique=conversion;
 }
 Need(unique.has_value(),"no original enum source for fetch format/class");return *unique;
}
Shader::FetchCompileProjection Projection(const Json&p,const fs::path&root,size_t&total,
                                          const Shader::StageSpecialization&spec) {
 const auto j=p.at("fetch_projection");Need(j.at("schema")==1&&j.at("raw_Vsharp_reconstructed")==false,"explicit projection policy");
 Need(spec.fetch_shader_data.has_value(),"projected original fetch required");const auto&fetch=*spec.fetch_shader_data;
 Need(fetch.attributes.size()>0&&fetch.attributes.size()<=32&&spec.vs_attribs.size()==fetch.attributes.size(),"projected fetch/spec vector shape");
 const auto source=p.at("source_metadata");const auto header=Reference(source.at("header"),root,total);
 Need(header.size()==24&&Word(header,0)==7&&Word(header,4)==10,"observed metadata header");const auto permutation=Qword(header,16),hash=Qword(header,8);
 Need(source.at("permutation")==permutation&&Hash(source.at("permutation_hash"))==hash&&HashCombine(Hash(p.at("program_hash")),permutation)==hash,"original vertex permutation identity");
 Need(source.at("original_spec_sha256")==p.at("spec").at("sha256"),"original projected spec fingerprint");
 const auto keys=p.at("projection_source_keys");Need(keys.size()>0&&keys.size()<=128,"bounded projection key proofs");
 std::vector<std::array<u32,VideoCore::MaxVertexBufferCount>> formats;
 for(const auto key:keys) {
  const auto data=Reference(key.at("reference"),root,total);Need(data.size()>=8+sizeof(VideoCore::GraphicsPipelineKey)&&Word(data,0)==5&&Word(data,4)==0,"actual graphics key prefix");
  constexpr size_t stage=8+offsetof(VideoCore::GraphicsPipelineKey,stage_hashes)+sizeof(size_t)*size_t(Shader::SwStage::Vertex);
  Need(Qword(data,stage)==hash&&Hash(key.at("vertex_permutation_hash"))==hash,"exact key logical Vertex permutation");
  std::array<u32,VideoCore::MaxVertexBufferCount> values{};memcpy(values.data(),data.data()+8+offsetof(VideoCore::GraphicsPipelineKey,vertex_buffer_formats),sizeof(values));
  const auto declared=key.at("vertex_formats");Need(declared.size()==values.size(),"full key format array");size_t i=0;for(const auto v:declared)Need(v.get<u32>()==values[i++],"raw key format proof");formats.push_back(values);
 }
 Shader::FetchCompileProjection result;const auto f=j.at("fetch");result.code_size=f.at("code_size").get<u32>();result.vertex_offset_sgpr=f.at("vertex_sgpr_offset").get<s8>();result.instance_offset_sgpr=f.at("instance_sgpr_offset").get<s8>();
 Need(result.code_size==fetch.size&&result.vertex_offset_sgpr==fetch.vertex_offset_sgpr&&result.instance_offset_sgpr==fetch.instance_offset_sgpr,"full original fetch scalars");
 const auto attributes=j.at("attributes");Need(attributes.size()==fetch.attributes.size(),"complete explicit projection entries");result.attributes.reserve(fetch.attributes.size());size_t i=0;
 for(const auto a:attributes) {
  Shader::FetchAttributeProjection value{};auto&v=value.attribute;const auto fields=a.at("fields");
  #define FETCH_FIELD(name) v.name=fields.at(#name).get<u8>();
  FETCH_FIELD(semantic) FETCH_FIELD(dest_vgpr) FETCH_FIELD(num_elements) FETCH_FIELD(sgpr_base) FETCH_FIELD(dword_offset) FETCH_FIELD(instance_data) FETCH_FIELD(inst_offset) FETCH_FIELD(data_format) FETCH_FIELD(num_format)
  #undef FETCH_FIELD
  Need(a.at("attribute_index")==i&&Shader::SameFetchFields(v,fetch.attributes[i])&&v.semantic==i&&v.dest_vgpr<=252&&v.num_elements>0&&v.num_elements<=4&&v.sgpr_base<=14&&v.instance_data<=3&&v.data_format==0,"all nine projected fetch fields");
  value.nonnull=a.at("nonnull").get<bool>();const auto&captured=spec.vs_attribs[i];
  Need(value.nonnull&&(captured.divisor||u8(captured.num_class)||captured.dst_select.r!=AmdGpu::CompSwizzle::Zero||captured.dst_select.g!=AmdGpu::CompSwizzle::Zero||captured.dst_select.b!=AmdGpu::CompSwizzle::Zero||captured.dst_select.a!=AmdGpu::CompSwizzle::Zero),"nonnull projection needs nondefault observed fields");
  const auto cls=a.at("number_class").get<u32>(),conversion=a.at("read_number_conversion").get<u32>();Need(cls<=u32(AmdGpu::NumberClass::Uint)&&conversion<=u32(AmdGpu::NumberConversion::SrgbToNorm),"projection enum bounds");value.number_class=AmdGpu::NumberClass(cls);value.read_number_conversion=AmdGpu::NumberConversion(conversion);value.divisor=a.at("divisor").get<u32>();
  const auto swizzle=a.at("effective_dst_select");Need(swizzle.size()==4,"projection swizzle extent");size_t component=0;for(const auto x:swizzle){const auto n=x.get<u8>();Need(n<=1||(n>=4&&n<=7),"projection swizzle enum");value.effective_dst_select.array[component++]=AmdGpu::CompSwizzle(n);}
  const auto divisor=v.instance_data==0?0u:v.instance_data==1?spec.runtime_info.sw.vs.step_rate_0:v.instance_data==2?spec.runtime_info.sw.vs.step_rate_1:1u;
  Need(value.number_class==captured.num_class&&value.divisor==captured.divisor&&value.divisor==divisor&&value.effective_dst_select==captured.dst_select,"exact observed projection semantics");
  std::vector<u32> raw_formats;for(const auto&key:formats)raw_formats.push_back(key[i]);std::sort(raw_formats.begin(),raw_formats.end());raw_formats.erase(std::unique(raw_formats.begin(),raw_formats.end()),raw_formats.end());
  const auto proof=a.at("proof");Need(proof.at("unique_conversion_required")==true&&proof.at("raw_descriptor_materialized")==false,"projection proof policy");const auto declared=proof.at("backend_formats");Need(declared.size()==raw_formats.size(),"all same-cache formats must be proved");size_t at=0;
  for(const auto fmt:declared){const auto raw=fmt.get<u32>();Need(raw==raw_formats[at++]&&UniqueFetchConversion(raw,value.number_class)==value.read_number_conversion,"unique original fetch conversion proof");}
  result.attributes.push_back(value);++i;
 }
 return result;
}
#if defined(BB_WITH_OWNED_COMPANION_CONSUMER)
std::string DigestText(const BbFetchStore::Digest&d){constexpr char h[]="0123456789abcdef";std::string s;for(auto x:d){s+=h[x>>4];s+=h[x&15];}return s;}
std::string ProgramText(u64 value){char text[17];snprintf(text,sizeof(text),"%016llx",static_cast<unsigned long long>(value));return text;}
const char* HwText(Shader::HwStage stage){switch(stage){case Shader::HwStage::Vertex:return "vertex";case Shader::HwStage::Export:return "export";case Shader::HwStage::Local:return "local";default:throw std::runtime_error("owned companion hardware stage");}}
Json CompanionFacts(const BbFetchCompanion::Companion&c){
 Need(c.association.has_value(),"actual companion association required");const auto&a=*c.association;
 Need(a.permutation_hash==HashCombine(a.program_hash,a.permutation),"actual companion permutation hash");
 return Json{{"companion_key",BbFetchCompanion::Key(c)},{"input_integrity_sha256",DigestText(c.record.expected.environment.compile_inputs)},{"program_hash",ProgramText(a.program_hash)},{"hardware_stage",HwText(c.input.hw_stage)},{"logical_stage","vertex"},{"permutation",a.permutation},{"permutation_hash",ProgramText(a.permutation_hash)},{"reference_origin",a.reference_origin},{"canonical_spec_sha256",DigestText(BbCpuCache::Hash(a.canonical_spec))},{"code_sha256",DigestText(c.record.expected.code_sha256)},{"observed_spirv_sha256",DigestText(BbFetchStore::WordDigest(c.observed_spirv))},{"observed_spirv_bytes",c.observed_spirv.size()*4}};
}
bool CompanionKind(const Json&p){return p.at("kind")=="owned-fetch-companion-inventory"||p.at("kind")=="owned-fetch-companion-module-input";}
int PrepareCompanion(const Json&p,const fs::path&root,const fs::path&output,std::span<const u8>raw,Json&result){
 Need(p.at("packet_schema")==1&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false&&p.at("native_info_srt_allowed")==false,"typed companion packet policy");
 const auto constraints=p.at("constraints");Need(constraints.at("guest_dereference_allowed")==false&&constraints.at("old_walker_execution_allowed")==false&&constraints.at("final_runtime_import_allowed")==false&&constraints.at("require_association")==true,"typed companion ownership policy");
 const fs::path store_root=p.at("companion_root").get<std::string>(),game=p.at("game_root").get<std::string>();Directory(store_root);Directory(game);
 const char*selected=getenv("BB_GPU_USER_DIR");Need(selected&&*selected,"selected companion GPU root");const fs::path gpu=selected;Directory(gpu);
 const auto roots=BbPreparationPaths::Actual(BbBundledRuntime::Role::ModuleWorker);
 if(roots){Need(gpu==roots->gpu,"selected companion GPU mismatch");roots->Companion(store_root,game);}
 else{
  Need(Child(gpu,fs::path(Shared)/"state")&&Child(store_root,gpu/"automatic-game-inputs"),"companion store stays below selected owned GPU");
  Need(Child(game,fs::path(Shared).parent_path()),"isolated effective game source required");
 }
 Dl_info image{};Need(dladdr(reinterpret_cast<const void*>(&Shader::CompileOwnedToSpirv),&image)&&image.dli_fname,"actual owned compiler image");
 const fs::path library=fs::canonical(image.dli_fname);Need(library.filename().string().starts_with("libbbgpu")&&p.at("selected_backend")==library.string(),"companion actual compiler DSO identity");
 auto receipt=BbFetchCapture::VerifyPreparationReceipt(store_root,game,library);size_t total=0;const auto profile=Profile(Reference(p.at("profile"),root,total));auto source=BbFetchCapture::SourceForReceipt(receipt,profile);Need(source.require_association,"complete actual association required");
 std::string error;auto store=BbFetchCompanion::Store::Open(store_root,{},error);Need(bool(store),"owned companion store unavailable");
 result["companion_contract_schema"]=1;result["input_packet_sha256"]=Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(raw.data()),raw.size()});result["source_game_sha256"]=DigestText(source.game);result["source_compiler_sha256"]=DigestText(source.compiler);result["source_abi_sha256"]=DigestText(source.abi);result["profile_canonical_sha256"]=DigestText(BbCpuCache::Hash(std::as_bytes(std::span(source.profile))));result["full_final_pso_ready"]=false;
 if(p.at("kind")=="owned-fetch-companion-inventory"){
  std::string lines;size_t accepted=0,rejected=0;const auto keys=store->Keys(error);Need(error.empty(),"owned companion enumeration failed");for(const auto&key:keys){
   Stop();std::string read_error;auto item=store->Read(key,source,read_error);if(!item){++rejected;continue;}auto facts=CompanionFacts(*item);auto line=facts.dump()+"\n";Need(lines.size()+line.size()<=16*MaxBlob,"bounded companion inventory");lines+=line;++accepted;
  }
  Need(error.empty(),"owned companion enumeration failed");if(!lines.empty())WriteExclusive(output/"inventory.jsonl",{reinterpret_cast<const u8*>(lines.data()),lines.size()});
  result["status"]="inventoried";result["accepted"]=accepted;result["rejected"]=rejected;result["native_module_compiled"]=false;
 }else{
  const std::string key=p.at("companion_key");Need(Metal::FunctionIdentity::Hex(key),"companion key syntax");auto item=store->Read(key,source,error);Need(item.has_value(),"owned companion source/profile/code/association rejected");const auto facts=CompanionFacts(*item);const auto expected=p.at("expected");
  for(const char*field:{"companion_key","input_integrity_sha256","program_hash","hardware_stage","logical_stage","permutation","permutation_hash","reference_origin","canonical_spec_sha256","code_sha256","observed_spirv_sha256","observed_spirv_bytes"})Need(facts.at(field)==expected.at(field),"exact inventoried companion association");
  const auto targets=p.at("target_specs");Need(targets.size()<=64,"bounded actual cache specialization matches");auto target_hashes=Json::array();
  for(const auto target_reference:targets){
   const auto bytes=Reference(target_reference,root,total);SpecEnvelope(bytes,false,true);Serialization::Archive archive{std::vector<u8>(bytes)};Shader::StageSpecialization target;
   Need(target.Deserialize(archive)&&archive.IsEoS()&&BbFetchCapture::CanonicalStage(target)==item->association->canonical_spec,"actual cache target complete canonical specialization differs");
   target_hashes.push_back(target_reference.at("sha256"));
  }
  result["target_spec_sha256s"]=target_hashes;result["target_canonical_specs_verified"]=true;
  auto observed=std::move(item->observed_spirv);auto after=item->runtime_after;auto canonical=std::move(item->association->canonical_spec);auto inputs=std::make_shared<Shader::CompileSnapshot>(std::move(item->input));Shader::CompileContext context;auto actual=Shader::CompileOwnedToSpirv(inputs,context);
  Need(BbFetchCapture::CanonicalRuntime(actual.runtime)==BbFetchCapture::CanonicalRuntime(after),"owned companion runtime-after differs");Shader::StageSpecialization spec(actual.info,actual.runtime,inputs->profile,inputs->start);Need(spec.Valid()&&BbFetchCapture::CanonicalStage(spec)==canonical,"owned companion complete canonical specialization differs");Need(actual.spirv==observed,"owned companion regenerated SPIR-V differs");
  auto bytes=std::span<const u8>(reinterpret_cast<const u8*>(actual.spirv.data()),actual.spirv.size()*4);Stop();WriteExclusive(output/"module.spv",bytes);WriteExclusive(output/"canonical-spec.bin",{reinterpret_cast<const u8*>(canonical.data()),canonical.size()});
  result["status"]="completed";result["association"]=facts;result["native_module_compiled"]=true;result["expected_spirv_equal"]=true;result["runtime_after_equal"]=true;result["canonical_specialization_equal"]=true;result["program_base_from_owned_snapshot"]=true;result["current_generated_srt_verified"]=true;result["spirv_sha256"]=facts.at("observed_spirv_sha256");result["spirv_bytes"]=bytes.size();
 }
 auto text=result.dump();WriteExclusive(output/"result.json",{reinterpret_cast<const u8*>(text.data()),text.size()});CommitOutputs();printf("GAME_MODULE_RESULT %s\n",text.c_str());return 0;
}
#endif
}
int main(int argc,char**argv){@autoreleasepool{
 if(argc==2&&std::string_view(argv[1])=="--capability"){
  Json capability{{"schema",1},{"cpu_only",true},{"automatic_input_root","selected-owned-gpu/automatic-game-inputs-v1"},{"packet_schema",1},{"requires_nonce_and_cohort",true},{"maximum_input_bytes",MaxTotal}};
  #if defined(BB_MODULE_DIAGNOSTIC)
  capability["diagnostic_only"]=true;
  #else
  capability["diagnostic_only"]=false;
  #endif
  #if defined(BB_WITH_OWNED_COMPANION_CONSUMER)
  capability["companion_contract_schema"]=1;
  #endif
  printf("GAME_MODULE_CAPABILITY %s\n",capability.dump().c_str());return 0;
 }
 if(argc!=3){fputs("usage: game-module-preparer packet.json fresh-output\n",stderr);return 64;}
 signal(SIGINT,Signal);signal(SIGTERM,Signal);
 Json result{{"candidate_only",true},{"runtime_cache_import_allowed",false},{"gpu_started",false},{"native_info_srt_deserialized",false},{"old_walker_executed",false},{"guest_memory_read",false}};
 try{
  OutputTransaction output_transaction;
  const fs::path packet=argv[1],output=argv[2];Directory(packet.parent_path());Directory(output);
  const fs::path evidence=fs::path(Shared)/"evidence";
  const auto roots=BbPreparationPaths::Actual(BbBundledRuntime::Role::ModuleWorker);
  const bool evidence_mode=!roots&&Child(packet.parent_path(),evidence)&&Child(output,evidence);
  bool automatic_mode=false;
  if(roots){roots->Module(packet,output);automatic_mode=true;}
  else if(!evidence_mode){
   const char* selected=getenv("BB_GPU_USER_DIR");Need(selected&&*selected,"selected isolated GPU directory required");
   const fs::path gpu=selected;Directory(gpu);Need(Child(gpu,fs::path(Shared)/"state"),"selected GPU must be below isolated state");
   const fs::path automatic=gpu/"automatic-game-inputs";Directory(automatic);
   Need(Child(packet.parent_path(),automatic)&&Child(output,automatic),"automatic input/output must be below selected owned GPU subtree");
   automatic_mode=true;
  }
  Need(fs::is_empty(output),"new empty module output required");
  Need(output!=packet.parent_path()&&!Child(output,packet.parent_path())&&!Child(packet.parent_path(),output),"input/output separation");
  const auto raw_packet=Read(packet,MaxPacket);auto p=Json::parse(raw_packet);
  if(automatic_mode){
   const char* nonce=getenv("BB_MODULE_PREPARATION_NONCE");const char* cohort=getenv("BB_MODULE_PREPARATION_COHORT");
   Need(nonce&&cohort&&Metal::FunctionIdentity::Hex(nonce)&&Metal::FunctionIdentity::Hex(cohort),"automatic nonce/cohort admission required");
   const auto admission=p.at("automatic_preparation");
   Need(admission.at("schema")==1&&admission.at("nonce")==std::string(nonce)&&admission.at("cohort")==std::string(cohort),"automatic packet nonce/cohort mismatch");
  }
  #if defined(BB_WITH_OWNED_COMPANION_CONSUMER)
  if(CompanionKind(p))return PrepareCompanion(p,packet.parent_path(),output,raw_packet,result);
  #endif
  const bool geometry=p.at("kind")=="owned-gcn-geometry-module-input";
  const bool projection=p.at("kind")=="owned-gcn-fetch-projection-module-input";
  Need(p.at("packet_schema")==1&&(p.at("kind")=="owned-gcn-module-input"||geometry||projection)&&p.at("candidate_only")==true&&p.at("runtime_cache_import_allowed")==false&&p.at("native_info_srt_allowed")==false,"module packet policy");
  auto c=p.at("constraints");Need(c.at("old_walker_execution_allowed")==false&&c.at("guest_dereference_allowed")==false&&c.at("final_runtime_import_allowed")==false&&c.at("requires_regenerated_srt_equality")==true&&c.at("requires_current_profile_match")==true,"module ownership constraints");
  Need(p.at("program_base").at("known")==false&&p.at("program_base").at("value")==Json{}&&p.at("program_base").at("required_gate")=="reject-offset-by-program-base","explicit unknown program base");
  Need(p.at("layout").at("bindings_size")==sizeof(Shader::Backend::Bindings)&&p.at("layout").at("runtime_size")==sizeof(Shader::RuntimeInfo)&&p.at("layout").at("profile_size")==sizeof(Shader::Profile),"actual native module layout");
  size_t total=0;const auto root=packet.parent_path();auto profile=Profile(Reference(p.at("profile"),root,total));auto code=Reference(p.at("code"),root,total);auto specbytes=Reference(p.at("spec"),root,total);auto flat=Reference(p.at("flat"),root,total);auto walker=p.at("walker_bytes")==0?std::vector<u8>{}:Reference(p.at("walker"),root,total);Need(p.at("walker_bytes")!=0||p.at("walker")==Json{},"empty walker must be null");auto expected=Reference(p.at("expected_spirv"),root,total);
  Need(code.size()>=8&&code.size()<=Shader::CompileSnapshot::MaxCodeBytes&&Word(code,0)==0xBEEB03FF,"actual bounded GCN prefix");
  Need(p.at("source_provenance").at("title_id")=="CUSA03173"&&p.at("source_provenance").at("app_ver")=="01.09"&&p.at("source_provenance").at("code_prefix_verified")==true&&p.at("source_provenance").at("code_sha256")==p.at("code").at("sha256"),"module exact code provenance");
  SpecEnvelope(specbytes,geometry,projection);Serialization::Archive ar{std::vector<u8>(specbytes)};Shader::StageSpecialization spec{};Need(spec.Deserialize(ar)&&ar.IsEoS()&&(projection?spec.fetch_shader_data.has_value():!spec.fetch_shader_data),"original exact spec reader/fetch domain");
  const auto hw=spec.runtime_info.hw_stage;const auto sw=spec.runtime_info.sw_stage;
  Need(geometry==(hw==Shader::HwStage::Geometry),"packet kind/stage domain");
  Need((hw==Shader::HwStage::Compute&&sw==Shader::SwStage::Compute&&p.at("hardware_stage")=="compute"&&p.at("logical_stage")=="compute")||
       (hw==Shader::HwStage::Fragment&&sw==Shader::SwStage::Fragment&&p.at("hardware_stage")=="fragment"&&p.at("logical_stage")=="fragment")||
       (hw==Shader::HwStage::Vertex&&sw==Shader::SwStage::Vertex&&p.at("hardware_stage")=="vertex"&&p.at("logical_stage")=="vertex")||
       (hw==Shader::HwStage::Export&&sw==Shader::SwStage::Vertex&&p.at("hardware_stage")=="export"&&p.at("logical_stage")=="vertex")||
       (projection&&hw==Shader::HwStage::Local&&sw==Shader::SwStage::Vertex&&p.at("hardware_stage")=="local"&&p.at("logical_stage")=="vertex")||
       (geometry&&hw==Shader::HwStage::Geometry&&sw==Shader::SwStage::Geometry&&p.at("hardware_stage")=="geometry"&&p.at("logical_stage")=="geometry"),"complete plain stage pair");
  if(hw==Shader::HwStage::Vertex)Need(!spec.runtime_info.hw.vs.motion_vectors,"module motion VS capture unavailable");
  if(hw==Shader::HwStage::Fragment)Need(!spec.runtime_info.hw.fs.motion_vectors,"module motion FS capture unavailable");
  Need(p.at("user_data_words")==16&&flat.size()%4==0&&flat.size()>=64&&flat.size()<=Shader::CompileSnapshot::MaxFlatBytes&&p.at("flat_dwords")==flat.size()/4,"complete owned flat/UD extent");
  Need(walker.size()<=Shader::CompileSnapshot::MaxWalkerBytes&&walker.size()%12==0&&p.at("walker_bytes")==walker.size(),"data-only SRT triplets");
  auto inputs=std::make_shared<Shader::CompileSnapshot>();inputs->profile=profile;inputs->runtime=spec.runtime_info;inputs->start=spec.start;inputs->hash=Hash(p.at("program_hash"));inputs->hw_stage=hw;inputs->sw_stage=sw;
  // This scalar is not an accepted guest address. The separate translation API
  // rejects its only dependency before any resource patching can consume it.
  inputs->program_base=0;inputs->code=Words(code);inputs->flat=Words(flat);inputs->flat_dwords=inputs->flat.size();inputs->user_data.assign(inputs->flat.begin(),inputs->flat.begin()+16);inputs->walker=Words(walker);
  if(projection){Need(c.at("requires_exact_fetch_projection")==true&&c.at("requires_native_enum_conversion_uniqueness")==true&&c.at("raw_Vsharp_synthesis_allowed")==false,"required explicit fetch projection policy");inputs->fetch=spec.fetch_shader_data;inputs->fetch_projection=Projection(p,root,total,spec);result["explicit_fetch_projection_used"]=true;result["raw_Vsharp_materialized"]=false;result["fetch_projection_attributes"]=inputs->fetch_projection->attributes.size();}
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
   CommitOutputs(); // Explicit diagnostic builds preserve their one rejected artifact.
  }
  #endif
  Need(bytes.size()==expected.size()&&std::equal(bytes.begin(),bytes.end(),expected.begin()),"retranslated SPIR-V differs from observed module");
  Stop();WriteExclusive(output/"module.spv",bytes);
  result["module_retranslation_schema"]=1;
  result["input_packet_sha256"]=Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(raw_packet.data()),raw_packet.size()});
  result["input_code_sha256"]=p.at("code").at("sha256");result["input_spec_sha256"]=p.at("spec").at("sha256");result["input_profile_sha256"]=p.at("profile").at("sha256");
  result["hardware_stage"]=p.at("hardware_stage");result["logical_stage"]=p.at("logical_stage");result["source_provenance"]=p.at("source_provenance");
  if(geometry){result["gs_copy_sha256"]=p.at("gs_copy").at("code").at("sha256");result["gs_copy_source_provenance"]=p.at("gs_copy").at("source_provenance");}
  result["status"]="completed";result["program_hash"]=p.at("program_hash");result["spirv_bytes"]=bytes.size();result["spirv_sha256"]=Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(bytes.data()),bytes.size()});result["expected_spirv_equal"]=true;result["current_generated_srt_verified"]=true;result["unknown_program_base_rejected_before_use"]=true;result["full_final_pso_ready"]=false;
  auto text=result.dump();WriteExclusive(output/"result.json",{reinterpret_cast<const u8*>(text.data()),text.size()});CommitOutputs();printf("GAME_MODULE_RESULT %s\n",text.c_str());return 0;
 }catch(const std::exception&e){result["status"]="rejected";result["error"]=e.what();printf("GAME_MODULE_RESULT %s\n",result.dump().c_str());return 65;}
}}
