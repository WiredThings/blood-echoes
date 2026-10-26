// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_compile_canonical.h"
#include "shader_recompiler/specialization.h"
#include "common/hash.h"
namespace BbFetchCapture {
inline constexpr size_t MaxStageBytes=16384;
// Explicit semantic encoding; never StageSpecialization::Serialize or native object bytes.
// Unused Buffer fields are zeroed according to its original equality semantics.
inline std::vector<std::byte> CanonicalStage(const Shader::StageSpecialization&s){
 using namespace BbFetchStore;std::vector<std::byte>b;
 auto u8=[&](uint8_t n){RequireSnapshot(b.size()<MaxStageBytes,"stage canonical budget");b.push_back(std::byte(n));};
 auto u32=[&](uint32_t n){for(unsigned i=0;i<4;++i)u8(n>>(8*i));};
 auto mapping=[&](const AmdGpu::CompMapping&m){for(auto c:m.array){auto n=uint8_t(c);RequireSnapshot(n<=1||(n>=4&&n<=7),"stage component swizzle");u8(n);}};
 auto conversion=[&](AmdGpu::NumberConversion n){RequireSnapshot(uint32_t(n)<=7,"stage number conversion");u32(uint32_t(n));};
 auto count=[&](size_t n,size_t max=128){RequireSnapshot(n<=max,"stage resource count");u32(n);};
 u32(1);auto runtime=CanonicalRuntime(s.runtime_info);u32(runtime.size());for(auto x:runtime)u8(std::to_integer<uint8_t>(x));
 for(auto n:{s.start.unified,s.start.buffer,s.start.user_data})u32(n);
 for(size_t i=0;i<128;i+=8){uint8_t n=0;for(unsigned j=0;j<8;++j)n|=uint8_t(s.bitset[i+j])<<j;u8(n);}
 u8(bool(s.fetch_shader_data));if(s.fetch_shader_data){auto&f=*s.fetch_shader_data;u32(f.size);u8(uint8_t(f.vertex_offset_sgpr));u8(uint8_t(f.instance_offset_sgpr));count(f.attributes.size(),32);for(auto&a:f.attributes)for(auto n:std::array<uint8_t,9>{a.semantic,a.dest_vgpr,a.num_elements,a.sgpr_base,a.dword_offset,a.instance_data,a.inst_offset,a.data_format,a.num_format})u8(n);}
 count(s.vs_attribs.size(),32);for(auto&a:s.vs_attribs){u32(a.divisor);RequireSnapshot(uint8_t(a.num_class)<=2,"stage number class");u8(uint8_t(a.num_class));mapping(a.dst_select);}
 count(s.buffers.size());for(auto&a:s.buffers){u32(a.stride);u8(a.is_formatted);u8(a.swizzle_enable);u8(a.paged);u8(a.is_formatted?a.data_format:0);u8(a.is_formatted?a.num_format:0);u8(a.swizzle_enable?a.index_stride:0);u8(a.swizzle_enable?a.element_size:0);AmdGpu::CompMapping zero{};mapping(a.is_formatted?a.dst_select:zero);conversion(a.is_formatted?a.num_conversion:AmdGpu::NumberConversion::None);}
 count(s.images.size());for(auto&a:s.images){RequireSnapshot((uint64_t(a.type)==0||(uint64_t(a.type)>=8&&uint64_t(a.type)<=15)),"stage image type");u32(uint32_t(a.type));u8(a.is_integer);u8(a.is_storage);u8(a.is_cube);u8(a.is_srgb);mapping(a.dst_select);conversion(a.num_conversion);u32(a.num_bindings);}
 count(s.fmasks.size());for(auto&a:s.fmasks){u32(a.width);u32(a.height);}count(s.samplers.size());for(auto&a:s.samplers){u8(a.force_unnormalized);u8(a.force_degamma);}return b;
}
inline Shader::StageSpecialization DecodeStage(std::span<const std::byte>b){
 using namespace BbFetchStore;RequireSnapshot(b.size()<=MaxStageBytes,"stage bounded bytes");size_t at=0;
 auto u8=[&](){RequireSnapshot(at<b.size(),"stage truncated field");return std::to_integer<uint8_t>(b[at++]);};auto u32=[&](){uint32_t n=0;for(unsigned i=0;i<4;++i)n|=uint32_t(u8())<<(8*i);return n;};auto boolean=[&](){auto n=u8();RequireSnapshot(n<=1,"stage boolean domain");return bool(n);};auto count=[&](size_t max=128){auto n=u32();RequireSnapshot(n<=max,"stage resource count");return n;};
 auto mapping=[&](){AmdGpu::CompMapping m{};for(auto&x:m.array){auto n=u8();RequireSnapshot(n<=1||(n>=4&&n<=7),"stage swizzle domain");x=AmdGpu::CompSwizzle(n);}return m;};auto conversion=[&](){auto n=u32();RequireSnapshot(n<=7,"stage conversion domain");return AmdGpu::NumberConversion(n);};
 Shader::StageSpecialization s;RequireSnapshot(u32()==1,"stage canonical version");auto n=u32();RequireSnapshot(n<=256&&n<=b.size()-at,"stage runtime extent");s.runtime_info=DecodeRuntime(b.subspan(at,n));at+=n;s.start.unified=u32();s.start.buffer=u32();s.start.user_data=u32();
 for(size_t i=0;i<128;i+=8){auto n=u8();for(unsigned j=0;j<8;++j)s.bitset[i+j]=(n>>j)&1;}
 if(boolean()){s.fetch_shader_data.emplace();auto&f=*s.fetch_shader_data;f.size=u32();f.vertex_offset_sgpr=int8_t(u8());f.instance_offset_sgpr=int8_t(u8());for(unsigned n=count(32);n--;){Shader::Gcn::VertexAttribute a;a.semantic=u8();a.dest_vgpr=u8();a.num_elements=u8();a.sgpr_base=u8();a.dword_offset=u8();a.instance_data=u8();a.inst_offset=u8();a.data_format=u8();a.num_format=u8();f.attributes.push_back(a);}}
 for(unsigned n=count(32);n--;){Shader::VsAttribSpecialization a;a.divisor=u32();auto kind=u8();RequireSnapshot(kind<=2,"stage number class");a.num_class=AmdGpu::NumberClass(kind);a.dst_select=mapping();s.vs_attribs.push_back(a);}
 for(unsigned n=count();n--;){Shader::BufferSpecialization a{};auto stride=u32();RequireSnapshot(stride<16384,"stage stride");a.stride=stride;a.is_formatted=boolean();a.swizzle_enable=boolean();a.paged=boolean();auto data=u8(),num=u8(),index=u8(),element=u8();RequireSnapshot(data<64&&num<16&&index<4&&element<4,"stage buffer domain");a.data_format=data;a.num_format=num;a.index_stride=index;a.element_size=element;a.dst_select=mapping();a.num_conversion=conversion();s.buffers.push_back(a);}
 for(unsigned n=count();n--;){Shader::ImageSpecialization a;auto type=u32();RequireSnapshot((type==0||(type>=8&&type<=15)),"stage image type");a.type=AmdGpu::ImageType(type);a.is_integer=boolean();a.is_storage=boolean();a.is_cube=boolean();a.is_srgb=boolean();a.dst_select=mapping();a.num_conversion=conversion();a.num_bindings=u32();s.images.push_back(a);}
 for(unsigned n=count();n--;){Shader::FMaskSpecialization a{u32(),u32()};s.fmasks.push_back(a);}for(unsigned n=count();n--;){Shader::SamplerSpecialization a{};a.force_unnormalized=boolean();a.force_degamma=boolean();s.samplers.push_back(a);}
 RequireSnapshot(at==b.size()&&CanonicalStage(s)==std::vector<std::byte>(b.begin(),b.end()),"noncanonical stage or trailing bytes");return s;
}
struct StageAssociation {
 uint64_t program_hash{},permutation{},permutation_hash{};
 uint32_t hardware_stage{},logical_stage{},reference_origin{}; //1 actual new emission,2 actual warm cached bytes
 std::vector<std::byte> canonical_spec;
};
inline StageAssociation Associate(const Shader::StageSpecialization&s,uint64_t hash,uint64_t permutation,uint64_t permutation_hash,uint32_t origin){
 BbFetchStore::RequireSnapshot(s.Valid()&&permutation_hash==HashCombine(hash,permutation)&&(origin==1||origin==2),"authoritative actual stage/ref required");return{hash,permutation,permutation_hash,uint32_t(s.runtime_info.hw_stage),uint32_t(s.runtime_info.sw_stage),origin,CanonicalStage(s)};
}
}
