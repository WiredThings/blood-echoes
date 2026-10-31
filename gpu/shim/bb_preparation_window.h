#pragma once
#include <cstdlib>
#include <initializer_list>
namespace BbPreparationWindow {
inline bool Preparing(){
 for(const char* key:{"BB_CPU_EXPORT_ROOT","BB_FEX_EXPORT_ROOT","BB_FEX_PREPARE_PLAN"}){
  const char* value=std::getenv(key);if(value&&*value)return true;
 }return false;
}
}
