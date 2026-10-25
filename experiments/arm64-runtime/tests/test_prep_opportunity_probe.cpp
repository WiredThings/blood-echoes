#include "bb_shader_phase_audit.h"
#include <cassert>
#include <string_view>
#include <unistd.h>
static_assert(BbAudit::PreparedShaderCandidate==84&&BbAudit::PreparedShaderOverflow==86);
int main(int argc,char**argv){
 assert(argc==2);std::string_view mode=argv[1];
 if(mode=="invalid"){BbShaderPhaseAudit::PreparationMiss(1,7,1);BbShaderPhaseAudit::PreparationMiss(1,1,4);return 0;}
 if(mode=="overflow"){for(unsigned i=0;i<132;++i){BbShaderPhaseAudit::PreparationMiss(i+1,1,1);usleep(1);}return 0;}
 BbShaderPhaseAudit::PreparationMiss(123,1,1);
 for(unsigned i=0;i<100;++i)BbShaderPhaseAudit::PreparationMiss(123,1,1);
 if(mode=="identities"){
  usleep(1);BbShaderPhaseAudit::PreparationMiss(123,0,1);
  usleep(1);BbShaderPhaseAudit::PreparationMiss(123,1,2);
  usleep(1);BbShaderPhaseAudit::PreparationMiss(123,1,3);
 }
 return 0;
}
