// SPDX-License-Identifier: GPL-2.0-or-later
// Authored CPU-only shader fixture, not game data or an installed runtime tool.
#include "fetch_compile_producer.h"
#include "common/hash.h"
#include "common/serdes.h"
#include "shader_recompiler/compile_snapshot.h"
#include <cstdio>
#include <fstream>
namespace {
void Need(bool ok,const char*why){if(!ok)throw std::runtime_error(why);}
void File(const std::filesystem::path&p,std::span<const std::byte>b){std::ofstream f(p,std::ios::binary);f.write(reinterpret_cast<const char*>(b.data()),b.size());f.close();Need(bool(f),"fixture output");}
}
int main(int argc,char**argv){try{
 Need(argc==5,"authored fixture: root game actual-dso new-output");const std::filesystem::path root=argv[1],game=argv[2],library=argv[3],out=argv[4];Need(std::filesystem::is_directory(out)&&std::filesystem::is_empty(out),"fresh fixture output");
 Shader::CompileSnapshot s;s.hash=0x1234;s.program_base=0x1900000000;s.hw_stage=Shader::HwStage::Vertex;s.sw_stage=Shader::SwStage::Vertex;s.runtime.Initialize(s.hw_stage,s.sw_stage);s.runtime.props.num_user_data=16;s.runtime.props.num_input_vgprs=3;s.runtime.props.num_allocated_vgprs=32;
 s.profile.max_viewport_width=16384;s.profile.max_viewport_height=16384;s.profile.subgroup_size=32;s.profile.supported_spirv=0x10600;s.profile.sparse_page_shift=14;s.profile.two_level_bda=true;
 s.code={0xBEEB03FF,0x00000000,0xBF810000};s.user_data.resize(16);s.flat=s.user_data;s.flat_dwords=16;s.fetch.emplace();s.fetch->size=32;s.fetch->vertex_offset_sgpr=-1;s.fetch->instance_offset_sgpr=-1;s.fetch->attributes.push_back({0,4,4,2,0,0,0,0,0});
 Shader::CompileSnapshot::RegisterRead read{};read.pointer_index=2;read.dword_offset=0;read.buffer.base_address=0x10000;read.buffer.stride=16;read.buffer.num_records=1;read.buffer.data_format=uint32_t(AmdGpu::DataFormat::Format32_32_32_32);read.buffer.num_format=uint32_t(AmdGpu::NumberFormat::Float);read.buffer.dst_sel_x=4;read.buffer.dst_sel_y=5;read.buffer.dst_sel_z=6;read.buffer.dst_sel_w=7;s.register_reads.push_back(read);
 auto owned=std::make_shared<const Shader::CompileSnapshot>(s);Shader::CompileContext context;auto actual=Shader::CompileOwnedToSpirv(owned,context);Shader::StageSpecialization spec(actual.info,actual.runtime,s.profile,s.start);Need(spec.Valid()&&!actual.spirv.empty(),"actual authored shader compilation");
 auto receipt=BbFetchCapture::VerifyPreparationReceipt(root,game,library);auto source=BbFetchCapture::SourceForReceipt(receipt,s.profile);auto builder=source;builder.require_association=false;BbFetchStore::Digest environment;environment.fill(0x44);auto companion=BbFetchCompanion::Make(std::move(s),actual.runtime,environment,{0,0},builder,actual.spirv);companion.association=BbFetchCapture::Associate(spec,0x1234,0,HashCombine(uint64_t(0x1234),uint64_t(0)),1);std::string error;auto store=BbFetchCompanion::Store::Open(root,{},error);Need(bool(store)&&store->Write(companion,source,error),"actual authored companion publication");
 std::array<uint32_t,4>header{0x42425043,10,7,5};std::vector<std::byte>profile(sizeof(header)+sizeof(Shader::Profile));memcpy(profile.data(),header.data(),sizeof(header));memcpy(profile.data()+sizeof(header),&owned->profile,sizeof(Shader::Profile));File(out/"profile.bin",profile);Serialization::Archive archive;spec.Serialize(archive);auto written_end=archive.CurrPtr();auto raw_spec=archive.TakeOff();Need(written_end>=raw_spec.data()&&written_end<=raw_spec.data()+raw_spec.size(),"authored written spec cursor");raw_spec.resize(written_end-raw_spec.data());File(out/"target-spec.bin",std::as_bytes(std::span(raw_spec)));printf("AUTHORED_COMPANION_KEY %s\n",BbFetchCompanion::Key(companion).c_str());return 0;
 }catch(const std::exception&e){fprintf(stderr,"%s\n",e.what());return 1;}}
