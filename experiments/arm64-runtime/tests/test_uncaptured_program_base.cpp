// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include "shader_recompiler/uncaptured_program_base.h"
int main() {
 using namespace Shader;
 Optimization::ResourceDiscoveryList resources;
 RejectUncapturedProgramBase(resources);
 resources.resize(3);
 for(auto& resource:resources)for(auto& sharp:resource.sharps)sharp.post_op=SharpFetchPostOp::None;
 RejectUncapturedProgramBase(resources);
 for(size_t i=0;i<resources.size();++i)for(size_t j=0;j<2;++j){
   auto& sharp=resources[i].sharps[j];
   for(auto op:{SharpFetchPostOp::BitwiseOrDw1WithImm,SharpFetchPostOp::ConvertCubeTo2DArray,SharpFetchPostOp::DisableAnisoIfSingleLod,SharpFetchPostOp::ForceRepeatXyzClamp,SharpFetchPostOp::ForceLastTexelXyClamp,SharpFetchPostOp::ClearAnisoRatioAndThreshold}){
     sharp.post_op=op;RejectUncapturedProgramBase(resources);
   }
   sharp.post_op=SharpFetchPostOp::OffsetByProgramBase;
   bool rejected=false;try{RejectUncapturedProgramBase(resources);}catch(const std::runtime_error&){rejected=true;}
   assert(rejected);sharp.post_op=SharpFetchPostOp::None;
 }
 RejectUncapturedProgramBase(resources);
}
