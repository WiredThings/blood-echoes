// SPDX-License-Identifier: GPL-2.0-or-later
#include "fetch_compile_observer.h"
#include "fetch_compile_producer.h"
#include "bbport_toggles.h"
#include <algorithm>
#include <cstring>

namespace BbFetchCapture {namespace {
thread_local Observation*active_observation{};
BbFetchStore::Attribute Attr(const Shader::Gcn::VertexAttribute&a){return{{a.semantic,a.dest_vgpr,a.num_elements,a.sgpr_base,a.dword_offset,a.instance_data,a.inst_offset,a.data_format,a.num_format}};}

}
void Observation::Parsed(const Shader::Info&i,const Shader::Gcn::FetchShaderData&f)noexcept{
 if(&i!=owner||failed)return;if(f.attributes.size()>attributes.size()||f.attributes.empty()){failed=true;return;}
 if(parsed){if(fetch_bytes!=f.size||vertex_offset!=f.vertex_offset_sgpr||instance_offset!=f.instance_offset_sgpr||attributes_count!=f.attributes.size()){failed=true;return;}
  for(size_t n=0;n<attributes_count;++n)if(attributes[n]!=Attr(f.attributes[n]))failed=true;return;}
 parsed=true;fetch_bytes=f.size;vertex_offset=f.vertex_offset_sgpr;instance_offset=f.instance_offset_sgpr;attributes_count=f.attributes.size();
 for(size_t n=0;n<attributes_count;++n)attributes[n]=Attr(f.attributes[n]);
}
void Observation::Sharp(const Shader::Info&i,const Shader::Gcn::VertexAttribute&a,const AmdGpu::Buffer&buffer)noexcept{
 if(&i!=owner||failed)return;if(!parsed||a.semantic>=attributes_count||attributes[a.semantic]!=Attr(a)){failed=true;return;}
 BbFetchStore::RegisterRead value;value.pointer_index=a.sgpr_base;value.dword_offset=a.dword_offset;std::memcpy(value.words.data(),&buffer,16);
 for(size_t n=0;n<reads_count;++n)if(reads[n].pointer_index==value.pointer_index&&reads[n].dword_offset==value.dword_offset){if(reads[n]!=value)failed=true;return;}
 if(reads_count==reads.size()){failed=true;return;}reads[reads_count++]=value;
}
bool Observation::Complete()const noexcept{
 if(failed||!parsed||!attributes_count)return false;
 for(size_t i=0;i<attributes_count;++i){bool found=false;for(size_t n=0;n<reads_count;++n)found|=reads[n].pointer_index==attributes[i].fields[3]&&reads[n].dword_offset==attributes[i].fields[4];if(!found)return false;}return true;
}
std::optional<Shader::Gcn::FetchShaderData>Observation::Fetch()const{
 if(!Complete())return{};Shader::Gcn::FetchShaderData f;f.size=fetch_bytes;f.vertex_offset_sgpr=vertex_offset;f.instance_offset_sgpr=instance_offset;
 for(size_t n=0;n<attributes_count;++n){auto&a=attributes[n].fields;f.attributes.push_back({a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7],a[8]});}return f;
}
std::vector<Shader::CompileSnapshot::RegisterRead>Observation::Reads()const{
 BbFetchStore::RequireSnapshot(Complete(),"incomplete observed fetch reads");std::vector<Shader::CompileSnapshot::RegisterRead>out;out.reserve(reads_count);
 for(size_t n=0;n<reads_count;++n){Shader::CompileSnapshot::RegisterRead r;r.pointer_index=reads[n].pointer_index;r.dword_offset=reads[n].dword_offset;std::memcpy(&r.buffer,reads[n].words.data(),16);out.push_back(r);}return out;
}
std::array<uint64_t,2>RuntimeMasks()noexcept{return{__atomic_load_n(&runtime_disabled_optimizations,__ATOMIC_RELAXED),__atomic_load_n(&runtime_experiment_bits,__ATOMIC_RELAXED)};}
CompileScope::CompileScope(const Shader::Info&i,const Shader::RuntimeInfo&r,const Shader::Profile&p,Shader::Backend::Bindings bindings,std::span<const uint32_t>words,bool defer_code_identity)noexcept:observation(i){try{
 if(active_observation||i.compile_snapshot||i.speculative_reads||r.sw_stage!=Shader::SwStage::Vertex||!Producer::Enabled())return;
 if(words.empty()||words.size()>Shader::CompileSnapshot::MaxCodeBytes/4||i.UserData().size()!=16)return;
 if(r.hw_stage!=Shader::HwStage::Vertex&&r.hw_stage!=Shader::HwStage::Local&&r.hw_stage!=Shader::HwStage::Export)return;
 if(r.hw_stage==Shader::HwStage::Vertex&&r.hw.vs.motion_vectors)return;
 deferred_identity=defer_code_identity;if(!deferred_identity){begin_code=BbFetchStore::WordDigest(words);if(!Producer::Allows(i.pgm_hash,r.hw_stage,begin_code,words.size()*4))return;}
 begin_ud=BbFetchStore::WordDigest(i.UserData());begin_masks=RuntimeMasks();begin_profile=BbFetchStore::CanonicalProfile(p);runtime_before=r;info=&i;runtime=&r;profile=&p;code=words;start=bindings;previous=active_observation;active_observation=&observation;active=true;
 }catch(...){}}
CompileScope::~CompileScope(){if(active)active_observation=previous;}
std::optional<Shader::CompileSnapshot> CompileScope::Owned(size_t spv_words)noexcept{try{
 if(!active||finished)return{};finished=true;active_observation=previous;active=false;
 if(!observation.Complete()||(!deferred_identity&&begin_code!=BbFetchStore::WordDigest(code))||begin_ud!=BbFetchStore::WordDigest(info->UserData())||begin_masks!=RuntimeMasks()||begin_profile!=BbFetchStore::CanonicalProfile(*profile)){Producer::Reject();return{};}
 if(deferred_identity&&!Producer::Allows(info->pgm_hash,runtime_before.hw_stage,BbFetchStore::WordDigest(code),code.size()*4)){Producer::Reject();return{};}
 const auto flat=info->FlatUserData();const auto&walk=info->srt_info;
 if(flat.size()<16||flat.size()!=walk.flattened_bufsize_dw||flat.size()>Shader::CompileSnapshot::MaxFlatBytes/4||walk.walker_func_size>Shader::CompileSnapshot::MaxWalkerBytes||walk.walker_func_size%12||bool(walk.walker_func)!=bool(walk.walker_func_size)){Producer::Reject();return{};}
 if(!BbFetchCompanion::FitsCounts(code.size(),flat.size(),walk.walker_func_size/4,spv_words)){Producer::Reject();return{};}
 Shader::CompileSnapshot owned;owned.profile=*profile;owned.runtime=runtime_before;owned.start=start;owned.hash=info->pgm_hash;owned.program_base=info->ProgramBase();owned.hw_stage=runtime_before.hw_stage;owned.sw_stage=runtime_before.sw_stage;
 owned.code.assign(code.begin(),code.end());owned.user_data.assign(info->UserData().begin(),info->UserData().end());owned.flat.assign(flat.begin(),flat.end());owned.flat_dwords=flat.size();owned.walker.resize(walk.walker_func_size/4);if(!owned.walker.empty())std::memcpy(owned.walker.data(),reinterpret_cast<const void*>(walk.walker_func),walk.walker_func_size);
 owned.fetch=observation.Fetch();owned.register_reads=observation.Reads();return owned;
 }catch(...){Producer::Reject();return{};}}
std::optional<BbFetchCompanion::Pending>CompileScope::Finish(std::span<const uint32_t>spv)noexcept{auto lease=Producer::Acquire();if(!lease)return{};auto owned=Owned(spv.size());if(!owned)return{};return Producer::Build(std::move(lease),std::move(*owned),*runtime,spv,begin_masks);}
bool CompileScope::FinishWarm(const Shader::StageSpecialization&spec,uint64_t permutation,uint64_t permutation_hash,BbFetchCompanion::Reference&&ref)noexcept{if(!active)return true;auto lease=Producer::Acquire();if(!lease)return false;auto owned=Owned(ref.words);if(!owned)return true;return Producer::PublishWarm(std::move(lease),std::move(*owned),*runtime,spec,permutation,permutation_hash,std::move(ref),begin_masks);}
} // namespace BbFetchCapture

namespace Shader::Gcn {
void ObserveCapturedFetch(const Shader::Info&i,const FetchShaderData&f)noexcept{if(BbFetchCapture::active_observation)BbFetchCapture::active_observation->Parsed(i,f);}
void ObserveCapturedSharp(const Shader::Info&i,const VertexAttribute&a,const AmdGpu::Buffer&b)noexcept{if(BbFetchCapture::active_observation)BbFetchCapture::active_observation->Sharp(i,a,b);}
}
