// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <vector>
#include "video_core/renderer_metal/mtl_host_pso_recipe.h"
namespace Metal::HostPso {
inline constexpr unsigned FixedSeedSchema=1,FixedSeedCount=8;
// Actual frame format comes from the original Presenter constant provider.
// No request invents a sample count, tiler constant or fault-parser argument.
inline std::array<Invocation,FixedSeedCount> FixedSeeds(U presentation_format){
 std::array<Invocation,FixedSeedCount> out{};
 constexpr std::array kinds{Kind::InlineUpdate,Kind::RenderInlineUpdate,Kind::Fill,
  Kind::UpscaleMerge,Kind::PostProcess,Kind::CameraMotion,Kind::FormatRgb,Kind::FormatDepth};
 for(unsigned i=0;i<out.size();++i){out[i].kind=kinds[i];if(kinds[i]==Kind::PostProcess)out[i].format=presentation_format;}
 return out;
}
struct FixedSeedItem {Kind kind{};std::string request_sha256,descriptor_sha256;ArtifactOutcome outcome;std::array<LocalLibrary::Route,2> actual_routes{};U origin_count{};bool inputs_verified{},native_ready{};};
struct FixedSeedResult {WarmResult progress;std::vector<FixedSeedItem> items;bool cancelled{},allow_compile{};};
}
