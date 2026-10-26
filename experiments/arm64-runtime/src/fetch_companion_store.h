// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "fetch_stage_association.h"
#include <memory>
#include <atomic>
namespace BbFetchCompanion {
using Digest=BbFetchStore::Digest;
inline constexpr size_t MaxItemBytes=2*1024*1024,IoReserveBytes=128*1024;
inline constexpr size_t MaxStoreBytes=256*1024*1024,MaxFiles=8192;
inline constexpr char DirectoryName[]="fetch-compiler-companions-v1";
struct SourceRequirement {
 Digest game{},compiler{},abi{};
 std::array<uint8_t,BbFetchStore::ProfileBytes>profile{};
 bool(*allows)(void*,uint64_t,uint32_t,const Digest&,size_t)noexcept{};
 bool require_association{};
 void*context{}; // caller-owned registry, only used synchronously; never persisted
};
struct Companion {
 Shader::CompileSnapshot input; // actual BEFORE-translation active RuntimeInfo
 Shader::RuntimeInfo runtime_after{};
 BbFetchStore::Record record;
 Digest bb_environment{};
 std::array<uint64_t,2>masks{};
 std::vector<uint32_t>observed_spirv;
 std::optional<BbFetchCapture::StageAssociation>association;
};
// Whole-item memory includes vector capacities, duplicate small record fields, and an
// IO/codec working reserve. Queued items never own a second whole encoded blob.
size_t MemoryBytes(const Companion&);
bool FitsCounts(size_t code_words,size_t flat_words,size_t walker_words,size_t spv_words)noexcept;
Companion Make(Shader::CompileSnapshot&&,const Shader::RuntimeInfo&runtime_after,
 const Digest&bb_environment,const std::array<uint64_t,2>&masks,
 const SourceRequirement&,std::span<const uint32_t>observed_spirv);
Companion MakeOwnedSpirv(Shader::CompileSnapshot&&,const Shader::RuntimeInfo&,const Digest&,const std::array<uint64_t,2>&,const SourceRequirement&,std::vector<uint32_t>&&);
void Validate(const Companion&,const SourceRequirement&);
std::string Key(const Companion&);
// Memory codecs charge the external encoded allocation as well. Store::Write/Read use
// streaming IO and therefore support larger valid items without that extra allocation.
std::vector<std::byte>Encode(const Companion&,const SourceRequirement&);
Companion Decode(std::span<const std::byte>,const SourceRequirement&);
struct Limits {size_t bytes=MaxStoreBytes,files=MaxFiles;};
class Store {
 int directory=-1;Limits limits{};
 explicit Store(int d,Limits l):directory(d),limits(l){}
public:
 Store()=default;~Store();Store(const Store&)=delete;Store&operator=(const Store&)=delete;
 Store(Store&&)noexcept;Store&operator=(Store&&)noexcept;
 static std::optional<Store>Open(const std::filesystem::path&development_root,Limits,std::string&error)noexcept;
 std::vector<std::string> Keys(std::string&error)const noexcept;
 bool Write(const Companion&,const SourceRequirement&,std::string&error,BbFetchStore::Cancellation={})const noexcept;
 std::optional<Companion>Read(std::string_view key,const SourceRequirement&,std::string&error)const noexcept;
};
// Two leases cover BUILDING + QUEUED + WRITING. A worker pop does not release its
// reservation; only completion/destruction does. No queued+active third item.
struct InflightState {std::atomic<unsigned>count{};};
class Lease {
 std::shared_ptr<InflightState>state;
 explicit Lease(std::shared_ptr<InflightState>s):state(std::move(s)){}
 friend class InflightBudget;
public:
 Lease()=default;~Lease(){if(state)state->count.fetch_sub(1,std::memory_order_acq_rel);}
 Lease(const Lease&)=delete;Lease&operator=(const Lease&)=delete;
 Lease(Lease&&)noexcept=default;Lease&operator=(Lease&&other)noexcept{if(this!=&other){if(state)state->count.fetch_sub(1,std::memory_order_acq_rel);state=std::move(other.state);}return*this;}
 explicit operator bool()const noexcept{return bool(state);}
};
class InflightBudget {
 std::shared_ptr<InflightState>state=std::make_shared<InflightState>();
public:
 Lease TryAcquire()noexcept{unsigned n=state->count.load(std::memory_order_relaxed);while(n<2){if(state->count.compare_exchange_weak(n,n+1,std::memory_order_acq_rel,std::memory_order_relaxed))return Lease(state);}return{};}
 unsigned Count()const noexcept{return state->count.load(std::memory_order_acquire);}
};
}
