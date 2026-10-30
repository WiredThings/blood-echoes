// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_register_store.h"
#include "shader_recompiler/compile_snapshot.h"
#include "module_profile_feature_flags.h"
#include <algorithm>
#include <bit>
#include <cstring>
#include <map>
#include <stdexcept>

namespace BbFetchStore {
inline void RequireSnapshot(bool ok,const char*reason){if(!ok)throw std::runtime_error(reason);}
inline std::array<uint8_t,ProfileBytes> CanonicalProfile(const Shader::Profile&p){
 std::array<uint8_t,ProfileBytes>out{};size_t at=0;
 auto u32=[&](uint32_t n){for(unsigned i=0;i<4;++i)out.at(at++)=n>>(i*8);};
 for(auto n:{p.max_viewport_width,p.max_viewport_height,p.max_shared_memory_size,p.supported_spirv,p.subgroup_size,p.sparse_page_shift})u32(n);
 for(unsigned i=0;i<8;++i)out.at(at++)=p.buffer_copy_shader_hash>>(i*8);
#define MODULE_PROFILE_BOOL_FIELD(field) \
 if constexpr(std::string_view(#field)=="two_level_bda")out.at(at++)=EncodeProfileBdaFeatures(p); \
 else out.at(at++)=p.field?1:0;
#include "module_profile_bool_fields.inc"
#undef MODULE_PROFILE_BOOL_FIELD
 RequireSnapshot(at==out.size(),"complete Profile field count changed");return out;
}
inline Digest WordDigest(std::span<const uint32_t>words){
 static_assert(std::endian::native==std::endian::little);
 return BbCpuCache::Hash(std::as_bytes(words));
}
template<class Snapshot>inline bool HasSemanticProjection(const Snapshot&s){
 // Supports the original raw-only native type and its forthcoming explicit projection
 // extension. A type without this member cannot contain such a projection at all.
 if constexpr(requires{s.fetch_projection;})return s.fetch_projection.has_value();
 return false;
}
// This is a projection of an already-owned capture. It never calls Capture, ReadUdReg,
// MakeInfo, native deserializers or old walker code, and never reads live guest memory.
inline Expected DescribeSnapshot(const Shader::CompileSnapshot&s,const Environment&environment){
 static_assert(sizeof(AmdGpu::Buffer)==16&&sizeof(Shader::CompileSnapshot::RegisterRead)==24);
 RequireSnapshot(!HasSemanticProjection(s),"raw fetch store cannot mix semantic projection inputs");
 RequireSnapshot(s.fetch.has_value()&&!s.tess&&s.gs_copy.empty(),"owned fetch capture required; no GS/tess session context");
 RequireSnapshot(s.hw_stage==s.runtime.hw_stage&&s.sw_stage==s.runtime.sw_stage,"snapshot stage identity");
 RequireSnapshot(s.code.size()<=Shader::CompileSnapshot::MaxCodeBytes/4&&!s.code.empty()&&
  s.user_data.size()==16&&s.flat.size()<=Shader::CompileSnapshot::MaxFlatBytes/4&&s.flat.size()==s.flat_dwords&&s.flat_dwords>=16&&
  s.walker.size()<=Shader::CompileSnapshot::MaxWalkerBytes/4&&s.walker.size()%3==0&&
  s.fetch->attributes.size()<=MaxAttributes&&s.register_reads.size()<=MaxAttributes,"owned capture extent");
 RequireSnapshot(s.hw_stage!=Shader::HwStage::Vertex||!s.runtime.hw.vs.motion_vectors,"uncaptured motion globals");
 const size_t base=sizeof(s)+(s.code.size()+s.user_data.size()+s.flat.size()+s.walker.size())*4+s.fetch->attributes.size()*sizeof(Shader::Gcn::VertexAttribute);
 RequireSnapshot(base<=Shader::CompileSnapshot::MaxBytes&&s.register_reads.size()*sizeof(Shader::CompileSnapshot::RegisterRead)<=Shader::CompileSnapshot::MaxBytes-base,"owned snapshot total budget");
 Expected e;e.environment=environment;e.program_hash=s.hash;e.hardware_stage=uint32_t(s.hw_stage);e.logical_stage=uint32_t(s.sw_stage);e.code_bytes=s.code.size()*4;e.base_snapshot_bytes=base;e.code_sha256=WordDigest(s.code);e.profile=CanonicalProfile(s.profile);e.bindings={s.start.unified,s.start.buffer,s.start.user_data};
 e.fetch_code_bytes=s.fetch->size;e.vertex_offset=s.fetch->vertex_offset_sgpr;e.instance_offset=s.fetch->instance_offset_sgpr;
 for(auto&a:s.fetch->attributes)e.attributes.push_back({{a.semantic,a.dest_vgpr,a.num_elements,a.sgpr_base,a.dword_offset,a.instance_data,a.inst_offset,a.data_format,a.num_format}});
 // Persist digests of owned word arrays only, never their pointer-valued UD contents.
 // Full canonical runtime/spec/descriptor identity remains an explicit producer obligation.
 std::array<Digest,4>parts{WordDigest(s.code),WordDigest(s.user_data),WordDigest(s.flat),WordDigest(s.walker)};
 std::vector<std::byte>context(std::as_bytes(std::span(parts)).begin(),std::as_bytes(std::span(parts)).end());
 for(unsigned i=0;i<8;++i)context.push_back(std::byte(uint64_t(s.program_base)>>(i*8)));
 e.owned_context_sha256=BbCpuCache::Hash(context);return e;
}
inline Record FromSnapshot(const Shader::CompileSnapshot&s,const Environment&environment){
 Record out;out.expected=DescribeSnapshot(s,environment);
 std::map<std::pair<uint32_t,uint32_t>,RegisterRead>reads;
 for(auto&r:s.register_reads){RegisterRead value;value.pointer_index=r.pointer_index;value.dword_offset=r.dword_offset;std::memcpy(value.words.data(),&r.buffer,16);
  auto[it,inserted]=reads.emplace(std::pair{r.pointer_index,r.dword_offset},value);
  RequireSnapshot(inserted||it->second==value,"conflicting duplicate captured read");
 }
 for(auto&[key,value]:reads)out.reads.push_back(value);out.expected.register_reads_sha256=ReadsDigest(out.reads);(void)Encode(out);return out;
}
// Validates the exact caller-owned companion snapshot/identity before assigning reads.
// The assignment is the final step, so rejection leaves the caller's snapshot unchanged.
inline void RestoreSnapshotReads(Shader::CompileSnapshot&s,const Record&record,const Expected&trusted_expected){
 auto expected=DescribeSnapshot(s,trusted_expected.environment);expected.register_reads_sha256=trusted_expected.register_reads_sha256;
 RequireSnapshot(expected==trusted_expected&&record.expected==trusted_expected,"owned companion input mismatch");(void)Encode(record);
 std::vector<Shader::CompileSnapshot::RegisterRead>reads;reads.reserve(record.reads.size());
 for(auto&r:record.reads){Shader::CompileSnapshot::RegisterRead value;value.pointer_index=r.pointer_index;value.dword_offset=r.dword_offset;std::memcpy(&value.buffer,r.words.data(),16);reads.push_back(value);}
 s.register_reads=std::move(reads);
}
inline bool WriteCapturedFetch(Store&store,const Shader::CompileSnapshot&s,const Environment&environment,std::string&error,Cancellation cancelled={})noexcept{
 try{return store.Write(FromSnapshot(s,environment),error,cancelled);}catch(const std::exception&e){error=e.what();return false;}
}
} // namespace BbFetchStore
