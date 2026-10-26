// SPDX-License-Identifier: GPL-2.0-or-later
// Authored CPU API controls; never translate this sentinel code or create Device.
#include <array>
#include <cstdio>
#include <stdexcept>
#include "shader_recompiler/compile_snapshot.h"
namespace {
void Need(bool b){if(!b)throw std::runtime_error{"projection API assertion"};}
template<class F>void Reject(F&&f){bool threw=false;try{f();}catch(const std::runtime_error&){threw=true;}Need(threw);}
}
int main(){
 using namespace Shader;CompileSnapshot s;s.hw_stage=HwStage::Vertex;s.sw_stage=SwStage::Vertex;
 s.runtime.Initialize(s.hw_stage,s.sw_stage);s.code={0xBEEB03FF};s.user_data.resize(16);s.flat.resize(16);s.flat_dwords=16;
 Gcn::FetchShaderData f;f.size=32;Gcn::VertexAttribute a{};a.semantic=0;a.dest_vgpr=4;a.num_elements=3;a.sgpr_base=2;f.attributes.push_back(a);s.fetch=f;
 FetchCompileProjection p;p.code_size=32;p.attributes.push_back({a,AmdGpu::NumberClass::Float,AmdGpu::NumberConversion::None,AmdGpu::IdentityMapping,0,true});s.fetch_projection=p;
 auto info=s.MakeInfo();Need(HasFetchCompileProjection(info));Need(QueryFetchCompileProjection(info,a)->nonnull);
 size_t cases=1;
 for(size_t field=0;field<3;++field){auto changed=a;if(field==0)changed.inst_offset=1;if(field==1)changed.data_format=1;if(field==2)changed.num_format=1;Reject([&]{QueryFetchCompileProjection(info,changed);});++cases;}
 auto missing=a;missing.semantic=1;Reject([&]{QueryFetchCompileProjection(info,missing);});++cases;
 std::array<u8,16> destination{};Reject([&]{ReadCompileSnapshotReg(s,2,0,destination.data(),destination.size());});++cases;
 const auto bytes=s.SizeBytes();auto copy=s;copy.fetch_projection.reset();Need(bytes-copy.SizeBytes()==sizeof(FetchAttributeProjection));++cases;
 Info ordinary;Need(!HasFetchCompileProjection(ordinary)&&QueryFetchCompileProjection(ordinary,a)==nullptr);++cases;
 copy=s;copy.register_reads.resize(1);Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.fetch_projection->attributes.clear();Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.fetch_projection->attributes[0].attribute.inst_offset=2;Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.fetch_projection->attributes[0].nonnull=false;Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.sw_stage=SwStage::Compute;copy.runtime.sw_stage=copy.sw_stage;Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.fetch_projection->attributes[0].read_number_conversion=AmdGpu::NumberConversion(8);Reject([&]{copy.MakeInfo();});++cases;
 copy=s;copy.fetch_projection->attributes.reserve(CompileSnapshot::MaxBytes/sizeof(FetchAttributeProjection)+1);Reject([&]{copy.MakeInfo();});++cases;
 printf("FETCH_PROJECTION_API cases=%zu guest_read=0 GPU=0 passed=1\n",cases);
}
