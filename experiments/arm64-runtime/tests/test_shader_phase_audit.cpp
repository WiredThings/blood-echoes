#include "bb_shader_phase_audit.h"
#include <cassert>
#include <fcntl.h>
#include <string_view>
#include <unistd.h>
static_assert(BbAudit::ShaderModuleCompile==78&&BbAudit::ShaderModuleBackend==83);
int main(int argc,char**argv){
 assert(argc==2);std::string_view mode=argv[1];
 const char* path=std::getenv("BB_STUTTER_GATE");
 int fd=path?open(path,O_WRONLY): -1;
 const uint32_t off=0;
 try{
  BbShaderPhaseAudit::Module module{0x123456789abcdef0ULL,mode=="bad-metadata"?7u:6u,0x87654321ULL};
  BbShaderPhaseAudit::Phase ir{module,BbAudit::ShaderGcnToIR};usleep(1000);
  if(mode=="throw")throw 1;
  if(mode=="close-gate"){assert(fd>=0&&pwrite(fd,&off,sizeof(off),0)==sizeof(off));}
  ir.End();
  BbShaderPhaseAudit::Phase spv{module,BbAudit::ShaderIRToSpirv};usleep(1000);spv.End();
  BbShaderPhaseAudit::Phase backend{module,BbAudit::ShaderModuleBackend};usleep(1000);backend.End();
  module.Returned(true);
 }catch(int){assert(mode=="throw");}
 if(fd>=0)close(fd);
 return 0;
}
