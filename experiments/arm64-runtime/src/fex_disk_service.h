// SPDX-License-Identifier: MIT
#pragma once
// Stable host-side interface. Storage/proof implementation must not be compiled
// into the translator archive used as a machine-code compatibility identity.
#include "bb_cpu_cache_identity.h"
#include <memory>
#include <span>
#include <string>
#include <utility>
namespace FEXCore {
struct ExecutableFileInfo;
namespace Context {class ContextImpl;}
namespace Core {struct InternalThreadState;}
}
namespace BbFexDisk {
class PreparedDirectory {
public:
 virtual ~PreparedDirectory()=default;
 virtual uintptr_t Load(FEXCore::Core::InternalThreadState*,uint64_t)=0;
 virtual void EndCompilation(uint64_t)=0;
 virtual uint64_t Hits()const=0;
 virtual uint64_t CopiedBytes()const=0;
 virtual uint64_t CatalogReads()const=0;
 virtual uint64_t WholeReads()const=0;
 virtual uint64_t NativeBytes()const=0;
 virtual uint64_t ReadBytes()const=0;
 virtual size_t ProofBytes()=0;
};
uint64_t ClockNow();
std::unique_ptr<PreparedDirectory> OpenDirectory(const std::string&,const BbCpuCache::Identity&,
 std::span<const std::pair<uint64_t,uint64_t>>,uintptr_t,uint64_t);
uint64_t GenerateDirectory(FEXCore::Context::ContextImpl&,FEXCore::Core::InternalThreadState*,
 const std::string&,const std::string&,const BbCpuCache::Identity&,
 std::span<const std::pair<uint64_t,uint64_t>>,uintptr_t,uint64_t,const FEXCore::ExecutableFileInfo&);
}
