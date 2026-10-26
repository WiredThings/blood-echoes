// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_companion_mailbox.h"
#include <array>

namespace BbFetchCapture {
// Only this stack-owned scope retains an Info pointer, only for identity comparison.
// Finish materializes owned values; neither queue nor disk can retain the pointer.
class Observation {
 const Shader::Info* owner{};
 bool failed{},parsed{};
 uint32_t fetch_bytes{};int8_t vertex_offset{-1},instance_offset{-1};
 size_t attributes_count{},reads_count{};
 std::array<BbFetchStore::Attribute,BbFetchStore::MaxAttributes>attributes{};
 std::array<BbFetchStore::RegisterRead,BbFetchStore::MaxAttributes>reads{};
public:
 explicit Observation(const Shader::Info&i):owner(&i){}
 void Parsed(const Shader::Info&,const Shader::Gcn::FetchShaderData&)noexcept;
 void Sharp(const Shader::Info&,const Shader::Gcn::VertexAttribute&,const AmdGpu::Buffer&)noexcept;
 bool Complete()const noexcept;
 std::optional<Shader::Gcn::FetchShaderData> Fetch()const;
 std::vector<Shader::CompileSnapshot::RegisterRead> Reads()const;
 bool Failed()const noexcept{return failed;}
};
class CompileScope {
 const Shader::Info*info{};const Shader::RuntimeInfo*runtime{};const Shader::Profile*profile{};
 std::span<const uint32_t>code;Shader::Backend::Bindings start{};
 BbFetchStore::Digest begin_code{},begin_ud{};
 Shader::RuntimeInfo runtime_before{};
 std::array<uint8_t,BbFetchStore::ProfileBytes>begin_profile{};
 std::array<uint64_t,2>begin_masks{};
 Observation observation;Observation*previous{};bool active{},finished{},deferred_identity{};
public:
 CompileScope(const Shader::Info&,const Shader::RuntimeInfo&,const Shader::Profile&,
              Shader::Backend::Bindings,std::span<const uint32_t>,bool defer_code_identity=false)noexcept;
 ~CompileScope();CompileScope(const CompileScope&)=delete;CompileScope&operator=(const CompileScope&)=delete;
 // Called only after successful ordinary TranslateProgram+EmitSPIRV. No live GetSharp here.
 std::optional<BbFetchCompanion::Pending> Finish(std::span<const uint32_t>observed_spirv)noexcept;
 bool FinishWarm(const Shader::StageSpecialization&,uint64_t permutation,uint64_t permutation_hash,BbFetchCompanion::Reference&&)noexcept;
private:
 std::optional<Shader::CompileSnapshot> Owned(size_t spv_words)noexcept;
};
std::array<uint64_t,2> RuntimeMasks()noexcept;
} // namespace BbFetchCapture
