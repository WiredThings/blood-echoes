// SPDX-License-Identifier: GPL-2.0-or-later
#include "fetch_compile_canonical.h"
#include <algorithm>
namespace BbFetchCapture {namespace {
using BbFetchStore::RequireSnapshot;
struct Bytes {std::vector<std::byte>b;void U32(uint32_t n){for(unsigned i=0;i<4;++i)b.push_back(std::byte(n>>(i*8)));}void U8(uint8_t n){b.push_back(std::byte(n));}template<class T>void Add(const T&a){for(auto n:a)b.push_back(std::byte(n));}};
struct Reader {std::span<const std::byte>b;size_t at{};uint8_t U8(){RequireSnapshot(at<b.size(),"canonical field span");return std::to_integer<uint8_t>(b[at++]);}uint32_t U32(){uint32_t n=0;for(unsigned i=0;i<4;++i)n|=uint32_t(U8())<<(i*8);return n;}bool Bool(){auto n=U8();RequireSnapshot(n<=1,"canonical bool");return n!=0;}uint16_t U16From32(){auto n=U32();RequireSnapshot(n<=65535,"canonical u16");return n;}};
BbFetchStore::Digest HashInputs(const Shader::CompileSnapshot&s,const BbFetchStore::Record&record,const Shader::RuntimeInfo&after,const BbFetchStore::Digest&environment,const std::array<uint64_t,2>&masks,const BbFetchStore::Digest&before){
 Bytes b;for(char c:std::string_view("BE-FETCH-COMPILER-INPUTS-1"))b.U8(c);b.Add(environment);b.Add(before);b.Add(CanonicalRuntime(after));b.Add(BbFetchStore::CanonicalProfile(s.profile));
 for(auto n:masks){b.U32(uint32_t(n));b.U32(uint32_t(n>>32));}b.U32(uint32_t(s.hash));b.U32(uint32_t(s.hash>>32));for(auto n:{s.start.unified,s.start.buffer,s.start.user_data})b.U32(n);b.Add(record.expected.code_sha256);b.Add(record.expected.owned_context_sha256);b.Add(record.expected.register_reads_sha256);RequireSnapshot(s.fetch.has_value(),"canonical fetch missing");b.U32(s.fetch->size);b.U32(uint8_t(s.fetch->vertex_offset_sgpr));b.U32(uint8_t(s.fetch->instance_offset_sgpr));b.U32(record.expected.attributes.size());for(auto&a:record.expected.attributes)b.Add(a.fields);return BbCpuCache::Hash(b.b);
}
}
std::vector<std::byte>CanonicalRuntime(const Shader::RuntimeInfo&r){
 using namespace Shader;RequireSnapshot(r.sw_stage==SwStage::Vertex&&(r.hw_stage==HwStage::Vertex||r.hw_stage==HwStage::Export||r.hw_stage==HwStage::Local),"uncovered canonical runtime stage");Bytes b;
 b.U32(uint32_t(r.sw_stage));b.U32(uint32_t(r.hw_stage));const auto&p=r.props;
 for(auto n:{uint32_t(p.fp_denorm_mode32),uint32_t(p.fp_denorm_mode16_64),uint32_t(p.fp_round_mode32),uint32_t(p.fp_round_mode16_64),p.num_user_data,p.num_input_vgprs,p.num_allocated_vgprs})b.U32(n);
 const auto&sw=r.sw.vs;b.U32(sw.step_rate_0);b.U32(sw.step_rate_1);b.U32(sw.vertex_sgpr_offset);b.U32(sw.instance_sgpr_offset);b.U8(sw.tess_emulated_primitive);
 if(r.hw_stage==HwStage::Local)b.U32(r.hw.ls.ls_stride);
 if(r.hw_stage==HwStage::Export)b.U32(r.hw.es.vertex_data_size);
 if(r.hw_stage==HwStage::Vertex){const auto&hw=r.hw.vs;RequireSnapshot(!hw.motion_vectors&&hw.num_outputs<=3,"uncaptured motion/invalid outputs");b.U32(hw.num_outputs);for(auto&map:hw.outputs)for(auto output:map)b.U8(uint8_t(output));b.U8(hw.emulate_depth_negative_one_to_one);b.U8(hw.clip_disable);b.U32(hw.user_clip_plane_mask);b.U8(hw.motion_vectors);}
 return b.b;
}
Shader::RuntimeInfo DecodeRuntime(std::span<const std::byte>bytes){
 using namespace Shader;RequireSnapshot(bytes.size()<=256,"canonical runtime byte bound");Reader in{bytes};auto sw=in.U32(),hw=in.U32();RequireSnapshot(sw==uint32_t(SwStage::Vertex)&&(hw==1||hw==3||hw==5),"canonical runtime stage domain");RuntimeInfo r{};r.Initialize(HwStage(hw),SwStage(sw));
 auto mode=[&]{auto n=in.U32();RequireSnapshot(n<=3,"floating mode domain");return n;};r.props.fp_denorm_mode32=AmdGpu::FpDenormMode(mode());r.props.fp_denorm_mode16_64=AmdGpu::FpDenormMode(mode());r.props.fp_round_mode32=AmdGpu::FpRoundMode(mode());r.props.fp_round_mode16_64=AmdGpu::FpRoundMode(mode());
 r.props.num_user_data=in.U32();r.props.num_input_vgprs=in.U32();r.props.num_allocated_vgprs=in.U32();RequireSnapshot(r.props.num_user_data<=16&&r.props.num_input_vgprs<=3&&r.props.num_allocated_vgprs<=256,"runtime register domain");
 auto&v=r.sw.vs;v.step_rate_0=in.U32();v.step_rate_1=in.U32();v.vertex_sgpr_offset=in.U16From32();v.instance_sgpr_offset=in.U16From32();v.tess_emulated_primitive=in.Bool();
 if(hw==5)r.hw.ls.ls_stride=in.U32();if(hw==3)r.hw.es.vertex_data_size=in.U32();
 if(hw==1){auto&v=r.hw.vs;v.num_outputs=in.U32();RequireSnapshot(v.num_outputs<=3,"runtime output count");for(auto&map:v.outputs)for(auto&out:map){auto n=in.U8();RequireSnapshot(n<=uint8_t(Output::ClipDist7),"runtime output enum");out=Output(n);}v.emulate_depth_negative_one_to_one=in.Bool();v.clip_disable=in.Bool();v.user_clip_plane_mask=in.U32();v.motion_vectors=in.Bool();RequireSnapshot(!v.motion_vectors,"uncaptured motion globals");}
 RequireSnapshot(in.at==bytes.size()&&CanonicalRuntime(r)==std::vector<std::byte>(bytes.begin(),bytes.end()),"canonical runtime exact extent");return r;
}
Shader::Profile DecodeProfile(std::span<const uint8_t>bytes){
 RequireSnapshot(bytes.size()==BbFetchStore::ProfileBytes,"canonical Profile extent");Shader::Profile p;Reader r{std::as_bytes(bytes)};
 p.max_viewport_width=r.U32();p.max_viewport_height=r.U32();p.max_shared_memory_size=r.U32();p.supported_spirv=r.U32();p.subgroup_size=r.U32();p.sparse_page_shift=r.U32();uint64_t hash=0;for(unsigned i=0;i<8;++i)hash|=uint64_t(r.U8())<<(i*8);p.buffer_copy_shader_hash=hash;
#define MODULE_PROFILE_BOOL_FIELD(field) \
 if constexpr(std::string_view(#field)=="two_level_bda")BbFetchStore::DecodeProfileBdaFeatures(r.U8(),p); \
 else p.field=r.Bool();
#include "module_profile_bool_fields.inc"
#undef MODULE_PROFILE_BOOL_FIELD
 RequireSnapshot(r.at==bytes.size(),"canonical Profile field count");return p;
}
BbFetchStore::Digest CanonicalInputs(const Shader::CompileSnapshot&s,const BbFetchStore::Record&r,const BbFetchStore::Digest&e,const std::array<uint64_t,2>&m,const BbFetchStore::Digest&before){return HashInputs(s,r,s.runtime,e,m,before);}
BbFetchStore::Digest CompleteCanonicalInputs(const Shader::CompileSnapshot&s,const BbFetchStore::Record&r,const Shader::RuntimeInfo&after,const BbFetchStore::Digest&e,const std::array<uint64_t,2>&m){return HashInputs(s,r,after,e,m,BbCpuCache::Hash(CanonicalRuntime(s.runtime)));}
}
