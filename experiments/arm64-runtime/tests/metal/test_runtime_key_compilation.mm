// SPDX-License-Identifier: GPL-2.0-or-later
// Authored IR/copy shader only. Real passes, SPIR-V emitter and Metal GS analyzer.
#include <array>
#include <algorithm>
#include <cstdio>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>
#include <spirv/unified1/spirv.hpp11>
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/frontend/copy_shader.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "video_core/renderer_metal/mtl_spirv_geometry.h"
#include "test_gpu_runtime_stubs.cpp"
using namespace Shader;
static unsigned checks=0;
static void Check(bool ok,const char* message){++checks;if(!ok)throw std::runtime_error(message);}
struct AuthoredIR {
 Info info{};Pools pools;IR::Program program{info};IR::Block* block;
 explicit AuthoredIR(HwStage hw,SwStage sw){
  info.hw_stage=hw;info.sw_stage=sw;block=pools.block_pool.Create(pools.inst_pool);
  program.blocks.push_back(block);program.post_order_blocks.push_back(block);
  IR::AbstractSyntaxNode node{};node.type=IR::AbstractSyntaxNode::Type::Block;node.data.block=block;program.syntax_list.push_back(node);
  node={};node.type=IR::AbstractSyntaxNode::Type::Return;program.syntax_list.push_back(node);
 }
};
static Profile ProfileForIR(){Profile p{};p.supported_spirv=0x00010600;p.max_shared_memory_size=32768;p.subgroup_size=32;return p;}
static RuntimeInfo Compute(u32 bytes){RuntimeInfo r{};r.Initialize(HwStage::Compute,SwStage::Compute);r.hw.cs.workgroup_size={32,1,1};r.hw.cs.shared_memory_size=bytes;return r;}
static RuntimeInfo Geometry(u32 inv,u32 in=28,u32 out=4){RuntimeInfo r{};r.Initialize(HwStage::Geometry,SwStage::Geometry);auto& g=r.hw.gs;g.num_invocations=inv;g.output_vertices=2;g.in_vertex_data_size=in;g.out_vertex_data_size=out;g.in_primitive=AmdGpu::PrimitiveType::PointList;g.out_primitive.fill(AmdGpu::GsOutputPrimitiveType::TriangleStrip);return r;}
static std::vector<u32> EmitCompute(u32 bytes){
 AuthoredIR author{HwStage::Compute,SwStage::Compute};IR::IREmitter ir{*author.block};ir.Prologue();ir.WriteShared(32,ir.Imm32(0x13579bdfu),ir.Imm32(0u));ir.Barrier();
 auto p=ProfileForIR();Optimization::CollectShaderInfoPass(author.program,p);Check(author.info.shared_types==IR::Type::U32,"real info pass did not collect shared type");
 Backend::Bindings binding{};return Backend::SPIRV::EmitSPIRV(p,Compute(bytes),author.program,binding);
}
static std::vector<u32> EmitGeometry(u32 inv,u32 input){
 AuthoredIR author{HwStage::Geometry,SwStage::Geometry};IR::IREmitter ir{*author.block};ir.Prologue();
 for(u32 c=0;c<4;++c)ir.SetAttribute(IR::Attribute::Position0,ir.Imm32(c==3?1.0f:0.0f),c);
 author.info.gs_copy_data.num_attrs=1;ir.SetAttribute(IR::Attribute::Param0,ir.GetAttribute(IR::Attribute::Param0,0,0));ir.EmitVertex();ir.EmitPrimitive();
 auto p=ProfileForIR();Optimization::CollectShaderInfoPass(author.program,p);Backend::Bindings binding{};return Backend::SPIRV::EmitSPIRV(p,Geometry(inv,input),author.program,binding);
}
struct Shape{std::set<u32> arrays,locations;u32 invocations=0;};
static Shape Decode(std::span<const u32> words){
 Check(words.size()>=5&&words[0]==0x07230203,"SPIR-V header");Shape result;std::map<u32,u32> constants;
 for(size_t i=5;i<words.size();){const u32 n=words[i]>>16,op=words[i]&65535;Check(n>0&&i+n<=words.size(),"SPIR-V instruction span");
  if(op==u32(spv::Op::OpConstant)&&n==4)constants[words[i+2]]=words[i+3];i+=n;
 }
 for(size_t i=5;i<words.size();){const u32 n=words[i]>>16,op=words[i]&65535;
  if(op==u32(spv::Op::OpTypeArray)&&n==4){auto it=constants.find(words[i+3]);if(it!=constants.end())result.arrays.insert(it->second);}
  if(op==u32(spv::Op::OpDecorate)&&n>=4&&words[i+2]==u32(spv::Decoration::Location))result.locations.insert(words[i+3]);
  if(op==u32(spv::Op::OpExecutionMode)&&n==4&&words[i+2]==u32(spv::ExecutionMode::Invocations))result.invocations=words[i+3];i+=n;
 }return result;
}
static std::vector<u32> CopyShader(){
 std::vector<u32> code{0xBEEB03FF,0};const u32 offsets[]={0,128,512};
 for(u32 i=0;i<3;++i){
  code.push_back(u32(Gcn::InstEncoding::MUBUF)|(u32(Gcn::OpcodeMUBUF::BUFFER_LOAD_DWORD)<<18)|offsets[i]);code.push_back(0x80000000u|(i<<8));
  code.push_back(u32(Gcn::InstEncoding::EXP)|1u|(u32(IR::Attribute::Param0+i)<<4));code.push_back(i);
 }code.push_back(0xBF810000);return code;
}
static u32 RingResult(u32 out){
 auto copy=CopyShader();auto parsed=ParseCopyShader(copy);Check(parsed.num_comps==3&&parsed.output_vertices==2,"authored actual copy parser shape");Check(parsed.attr_map.at(0).first==IR::Attribute::Param0&&parsed.attr_map.at(512).first==IR::Attribute::Param2,"authored actual copy parser maps");
 AuthoredIR author{HwStage::Geometry,SwStage::Geometry};IR::IREmitter ir{*author.block};IR::BufferInstInfo flags{};flags.system_coherent.Assign(1);flags.globally_coherent.Assign(1);flags.inst_offset.Assign(32);
 ir.StoreBufferU32(1,ir.Imm32(0u),ir.Imm32(0u),ir.Imm32(123u),flags);
 auto r=Geometry(1,28,out);r.hw.gs.vs_copy=copy;Optimization::RingAccessElimination(author.program,r);
 u32 result=~0u;for(const IR::Inst& inst:author.block->Instructions())if(inst.GetOpcode()==IR::Opcode::SetAttribute){Check(result==~0u,"unexpected multiple ring exports");result=u32(inst.Arg(0).Attribute());}
 Check(result!=~0u,"no actual ring export");return result;
}
static u32 StorageStride(u32 size){
 AuthoredIR author{HwStage::Compute,SwStage::Compute};IR::IREmitter ir{*author.block};ir.WriteShared(32,ir.Imm32(123u),ir.Imm32(0u));auto p=ProfileForIR();p.max_shared_memory_size=512;
 Optimization::SharedMemoryToStoragePass(author.program,Compute(size),p);Check(author.info.buffers.size()==1,"real LDS lowering did not create storage binding");
 u32 stride=0;for(const IR::Inst& inst:author.block->Instructions())if(inst.GetOpcode()==IR::Opcode::IMul32&&inst.Arg(1).IsImmediate())stride=inst.Arg(1).U32();Check(stride!=0,"real LDS stride missing");return stride;
}
int main(){try{
 const auto small=EmitCompute(1024),large=EmitCompute(2048);Check(small!=large,"different LDS sizes emitted identical SPIR-V");Check(Decode(small).arrays.contains(256)&&Decode(large).arrays.contains(512),"LDS array lengths not 256/512");
 const auto gs1=EmitGeometry(1,28),gs2=EmitGeometry(2,28),gs_inputs=EmitGeometry(1,32);Check(Decode(gs1).invocations==1&&Decode(gs2).invocations==2,"GS invocations lost");
 Check(Decode(gs1).locations.contains(5)&&!Decode(gs1).locations.contains(6)&&Decode(gs_inputs).locations.contains(6),"GS input interface did not grow");
 auto accepted=Metal::AnalyzeGeometryShader(gs1),rejected=Metal::AnalyzeGeometryShader(gs2);Check(accepted.ok,accepted.error.c_str());Check(!rejected.ok&&rejected.error.find("Invocations > 1")!=std::string::npos,"Metal GS invocation rejection missing");
 Check(RingResult(4)==u32(IR::Attribute::Param0)&&RingResult(8)==u32(IR::Attribute::Param2),"GS out ring stride did not change attribute");Check(RingResult(1)==RingResult(2),"copy floor-equivalent GS outputs differ");
 Check(StorageStride(1024)==1024&&StorageStride(2048)==2048,"LDS storage stride did not change");
 std::printf("PASS runtime compilation consumers checks=%u; LDS arrays=256/512; GS invocation=1/2; input params=6/7; ring=Param0/Param2; LDS stride=1024/2048\n",checks);return 0;
 }catch(const std::exception& e){std::fprintf(stderr,"FAIL runtime compilation consumers: %s\n",e.what());return 2;}}
