// SPDX-License-Identifier: GPL-2.0-or-later
// Compile canonical layouts only. No GPU library, cache deserialize, JIT or guest code.
#include <cstddef>
#include <cstdio>
#include <bit>
#include <fstream>
#include <filesystem>
#include <string_view>
#include "common/serdes.h"
#include "common/hash.h"
#include "shader_recompiler/specialization.h"
#include "video_core/pipeline_cache_core_types.h"
// Deterministic authored records through the actual canonical Writer.
template<size_t N,class T>void Put(std::array<unsigned char,N>& b,size_t off,const T& value){std::memcpy(b.data()+off,&value,sizeof(value));}
int Fixture(const char* path,std::string_view mode){
 using namespace Shader;using namespace VideoCore;
 if(std::filesystem::exists(path))return 2;
 if(mode=="profile-padding"){
  Profile a{},b{};std::memset(&a,0,sizeof(a));a.supported_spirv=0x10000;std::memcpy(&b,&a,sizeof(a));std::array<bool,sizeof(Profile)> fields{};
  #define PROFILE_FIELD(F) for(size_t i=0;i<sizeof(((Profile*)nullptr)->F);++i)fields[offsetof(Profile,F)+i]=true;
  #include "profile_fields.inc"
  #undef PROFILE_FIELD
  size_t padding{};auto* bytes=reinterpret_cast<unsigned char*>(&b);for(size_t i=0;i<fields.size();++i)if(!fields[i]){bytes[i]=0x5a;++padding;}
  if(!(a==b))return 4;std::ofstream out(path,std::ios::binary);out.write(reinterpret_cast<const char*>(&a),sizeof(a));out.write(reinterpret_cast<const char*>(&b),sizeof(b));if(!out.good())return 3;
  std::printf("{\"bytes\":%zu,\"used\":%zu,\"tail\":0,\"native_equality\":true,\"padding_bytes\":%zu}\n",2*sizeof(Profile),2*sizeof(Profile),padding);return 0;
 }
 Serialization::Archive ar;Serialization::Writer w{ar};
 if(mode=="graphics"){
  w.Write(u32{5});w.Write(u32{0});GraphicsPipelineKey key{};key.num_samples=1;key.depth_samples=1;key.num_color_attachments=2;key.stage_hashes[0]=0x1234;w.Write(&key,sizeof(key));w.Write(u32{1});w.Write(u32{2});w.Write(u32{1});w.Write(u32{3});
 }else if(mode=="compute"){
  w.Write(u32{5});w.Write(u32{1});w.Write(u64{0x1234});
 }else{
  const bool geometry=mode=="geometry",fetch=mode!="plain"&&!geometry;
  const auto hw=geometry?HwStage::Geometry:HwStage::Vertex;const auto sw=geometry?SwStage::Geometry:SwStage::Vertex;const u64 hash=0x11223344;
  w.Write(u32{7});w.Write(u32{10});w.Write(HashCombine(hash,u64{0}));w.Write(size_t{0});w.Write(Backend::Bindings{});
  RuntimeInfo runtime{};runtime.Initialize(hw,sw);if(!geometry)runtime.hw.vs.motion_vectors=mode=="motion";
  if(geometry){runtime.hw.gs.vs_copy=std::span<const u32>{reinterpret_cast<const u32*>(0x12345000ull),4};runtime.hw.gs.vs_copy_hash=0xaabbccdd;}
  w.Write(runtime);w.Write(std::string(128,'0'));w.Write(fetch?sizeof(Gcn::FetchShaderData):size_t{0});
  if(fetch){std::vector<Gcn::VertexAttribute> attrs(1);ar.Grow(6+attrs.size()*sizeof(Gcn::VertexAttribute));w.Write(u32{8});w.Write(s8{-1});w.Write(s8{-1});w.Write(attrs);}
  w.Write(std::vector<VsAttribSpecialization>{});w.Write(std::vector<BufferSpecialization>{});w.Write(std::vector<ImageSpecialization>{});w.Write(std::vector<FMaskSpecialization>{});w.Write(std::vector<SamplerSpecialization>{});
  std::array<unsigned char,sizeof(InfoPersistent)> info{};Put(info,offsetof(InfoPersistent,pgm_hash),hash);Put(info,offsetof(InfoPersistent,hw_stage),hw);Put(info,offsetof(InfoPersistent,sw_stage),sw);w.Write(info.data(),info.size());w.Write(std::vector<u32>(16));
  std::array<unsigned char,sizeof(PersistentSrtInfo)> srt{};Put(srt,offsetof(PersistentSrtInfo,flattened_bufsize_dw),u32{16});w.Write(srt.data(),srt.size());
 }
 const auto* cursor=ar.CurrPtr();auto bytes=ar.TakeOff();const size_t used=cursor-bytes.data();
 std::ofstream out(path,std::ios::binary);out.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());if(!out.good())return 3;
 std::printf("{\"bytes\":%zu,\"used\":%zu,\"tail\":%zu}\n",bytes.size(),used,bytes.size()-used);return 0;
}
int main(int argc,char**argv){
 if(argc==4&&std::string_view(argv[1])=="--fixture")return Fixture(argv[2],argv[3]);

 static_assert(sizeof(void*)==8&&sizeof(size_t)==8&&std::endian::native==std::endian::little);
 using namespace Shader;using namespace VideoCore;
 std::puts("{\"schema\":1,\"pointer_bytes\":8,\"size_t_bytes\":8,\"little_endian\":true");
 #define SIZE(T) std::printf(",\"" #T ".size\":%zu",sizeof(T))
 #define OFF(T,F) std::printf(",\"" #T "." #F "\":%zu,\"" #T "." #F ".bytes\":%zu",offsetof(T,F),sizeof(((T*)nullptr)->F))
 SIZE(Profile);
 #define PROFILE_FIELD(F) OFF(Profile,F);
 #include "profile_fields.inc"
 #undef PROFILE_FIELD
 SIZE(Backend::Bindings);SIZE(RuntimeInfo);SIZE(InfoPersistent);SIZE(PersistentSrtInfo);SIZE(VsAttribSpecialization);SIZE(BufferSpecialization);SIZE(ImageSpecialization);SIZE(FMaskSpecialization);SIZE(SamplerSpecialization);SIZE(Gcn::VertexAttribute);SIZE(Gcn::FetchShaderData);SIZE(GraphicsPipelineKey);SIZE(PsColorBuffer);
 OFF(Backend::Bindings,unified);OFF(Backend::Bindings,buffer);OFF(Backend::Bindings,user_data);
 OFF(RuntimeInfo,hw_stage);OFF(RuntimeInfo,sw_stage);OFF(RuntimeInfo,props);OFF(RuntimeInfo,sw);OFF(RuntimeInfo,hw);
 OFF(RuntimeInfo::Properties,num_user_data);OFF(RuntimeInfo::Properties,num_input_vgprs);OFF(RuntimeInfo::Properties,num_allocated_vgprs);
 OFF(SwVertexRuntimeInfo,step_rate_0);OFF(SwVertexRuntimeInfo,step_rate_1);OFF(HwVertexRuntimeInfo,motion_vectors);OFF(HwFragmentRuntimeInfo,motion_vectors);OFF(HwFragmentRuntimeInfo,num_inputs);OFF(HwFragmentRuntimeInfo,num_samples);OFF(HwComputeRuntimeInfo,workgroup_size);OFF(HwGeometryRuntimeInfo,vs_copy);OFF(HwGeometryRuntimeInfo,vs_copy_hash);
 OFF(InfoPersistent,pgm_hash);OFF(InfoPersistent,hw_stage);OFF(InfoPersistent,sw_stage);OFF(InfoPersistent,has_fetch_shader);OFF(InfoPersistent,mrt_mask);
 OFF(PersistentSrtInfo,walker_func_size);OFF(PersistentSrtInfo,flattened_bufsize_dw);
 OFF(VsAttribSpecialization,divisor);OFF(VsAttribSpecialization,num_class);OFF(VsAttribSpecialization,dst_select);
 OFF(BufferSpecialization,dst_select);OFF(BufferSpecialization,num_conversion);
 OFF(ImageSpecialization,type);OFF(ImageSpecialization,is_integer);OFF(ImageSpecialization,is_storage);OFF(ImageSpecialization,is_cube);OFF(ImageSpecialization,is_srgb);OFF(ImageSpecialization,dst_select);OFF(ImageSpecialization,num_conversion);OFF(ImageSpecialization,num_bindings);
 OFF(GraphicsPipelineKey,stage_hashes);OFF(GraphicsPipelineKey,vertex_buffer_formats);OFF(GraphicsPipelineKey,patch_control_points);OFF(GraphicsPipelineKey,num_color_attachments);OFF(GraphicsPipelineKey,color_buffers);OFF(GraphicsPipelineKey,num_samples);OFF(GraphicsPipelineKey,depth_samples);OFF(GraphicsPipelineKey,motion_vectors);
 std::printf(",\"MaxShaderStages\":%u,\"MaxVertexBufferCount\":%u,\"MaxColorBuffers\":%u}\n",MaxShaderStages,MaxVertexBufferCount,Shader::MaxColorBuffers);
}
