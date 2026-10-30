// SPDX-License-Identifier: MIT
#include "hle_call_service.inc"
#include "fex_hle_bridge.h"
namespace Core::GuestCpu {
HleGuestBridge::HleGuestBridge(HleCallRegistry& registry,RangeValidator validator,void* context,
 FailureReporter reporter,void* reporter_context,ExecutableRangeQuery query,void* query_context,
 CodeFileQuery code_query_,void* code_context_,FEXCore::CodeMapOpener* output)
 :service(registry,validator,context,reporter,reporter_context,query,query_context,"BACHATA_FEX_HLE"),
  code_query(code_query_),code_context(code_context_),code_output(output){}
AetherPS4::Fex::EngineResult<bool> HleGuestBridge::Invoke(HleCallFrame& frame){
 auto result=service.Invoke(frame);
 if(auto* failure=std::get_if<int>(&result))
  return AetherPS4::Fex::EngineFailure{AetherPS4::Fex::EngineStage::Bridge,*failure};
 return std::get<bool>(result);
}
std::optional<GuestExecutionRange> HleGuestBridge::QueryExecutableRange(std::uintptr_t address){
 return service.QueryExecutableRange(address);
}
std::optional<FEXCore::ExecutableFileSectionInfo> HleGuestBridge::QueryCodeFile(std::uintptr_t address){
 return code_query?code_query(code_context,address):std::nullopt;
}
}
