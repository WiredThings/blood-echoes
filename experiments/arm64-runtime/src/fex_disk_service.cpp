// SPDX-License-Identifier: MIT
#include "fex_disk_generate.h"
namespace BbFexDisk {
uint64_t ClockNow(){return Loader::Now();}
std::unique_ptr<PreparedDirectory> OpenDirectory(const std::string& path,const BbCpuCache::Identity& identity,
 std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t base,uint64_t bytes){
 return std::make_unique<Loader>(path,identity,sections,base,bytes);
}
uint64_t GenerateDirectory(FEXCore::Context::ContextImpl& context,FEXCore::Core::InternalThreadState* thread,
 const std::string& plan,const std::string& output,const BbCpuCache::Identity& identity,
 std::span<const std::pair<uint64_t,uint64_t>> sections,uintptr_t base,uint64_t bytes,const FEXCore::ExecutableFileInfo& source){
 return Generate(context,thread,plan,output,identity,sections,base,bytes,source);
}
}
