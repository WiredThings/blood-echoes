#include "bb_preparation_window.h"
#include <cassert>
#include <cstdio>
int main(){
 for(auto key:{"BB_CPU_EXPORT_ROOT","BB_FEX_EXPORT_ROOT","BB_FEX_PREPARE_PLAN"})unsetenv(key);
 assert(!BbPreparationWindow::Preparing());setenv("BB_FEX_CACHE_DIRECTORY","/authored/cache",1);setenv("BB_RECOMP_LIB","/authored/lib",1);assert(!BbPreparationWindow::Preparing());
 for(auto key:{"BB_CPU_EXPORT_ROOT","BB_FEX_EXPORT_ROOT","BB_FEX_PREPARE_PLAN"}){
  setenv(key,"",1);assert(!BbPreparationWindow::Preparing());setenv(key,"/authored/preparation",1);assert(BbPreparationWindow::Preparing());unsetenv(key);assert(!BbPreparationWindow::Preparing());
 }
 std::puts("PREPARATION_WINDOW exporter/FEX-preparer positive; normal/cached/static-game negatives passed; no GUI/device");
}
