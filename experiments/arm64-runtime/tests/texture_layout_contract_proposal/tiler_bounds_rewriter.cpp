#include "video_core/renderer_metal/mtl_texture_tiling.h"
#include "video_core/host_shaders_msl/tiling_macro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_64_comp_msl.h"
#include <cassert>
#include <cstdio>
int main(){
 namespace H=Metal::HostShaders;
 for(bool half:{false,true}) {
  auto source=half?H::TILING_MACRO_64_COMP.msl:H::TILING_MACRO_32_COMP.msl;
  auto result=Metal::TextureTilingSource(source,half);assert(!result.empty());
  assert(result.find(".linear_data")==std::string::npos);
  assert(result.find("function_constant(13)")!=std::string::npos);
  assert(result.find("function_constant(12)")!=std::string::npos);
  std::string renamed(source);size_t at=0;
  // Independent numeric renaming must not silently disable the direct shader.
  for(const auto& token:{std::string("_790"),std::string("_796")}){
   at=0;assert(renamed.find(token)!=std::string::npos);while((at=renamed.find(token,at))!=std::string::npos){renamed.replace(at,token.size(),token+"777");at+=token.size()+3;}
  }
  assert(!Metal::TextureTilingSource(renamed,half).empty());
  assert(Metal::TextureTilingSource("using namespace metal;",half).empty());
  std::printf("REWRITER half=%d new_ids=1 malformed_rejected=1 pass=1\n",half);
 }
}
