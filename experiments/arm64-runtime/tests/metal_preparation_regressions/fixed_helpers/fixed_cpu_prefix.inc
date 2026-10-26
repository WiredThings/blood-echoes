#include <cassert>
#include <iostream>
#include <functional>
#include <optional>
#include <chrono>
#include <stdexcept>
#include <set>
#include "common/scope_exit.h"
#include "video_core/renderer_metal/mtl_fixed_helper_seeds.h"
using u32=std::uint32_t;
unsigned checks=0;void ck(bool v){assert(v);++checks;}
namespace LocalLibrary=Metal::LocalLibrary;
namespace Metal {
namespace LocalAot {std::string Sha(const void*,size_t){return std::string(64,'f');}}
bool severe=false;u32 frame_format=80;
bool HostSeverePressure(){return severe;}
u32 PresentationFrameFormat(){return frame_format;}
struct Cache {std::string identity=std::string(64,'a');std::string DiscoveryIdentity(){return identity;}};
class HostKernels {
public:
 Cache cache;Cache*pso_cache=&cache;bool warming=false,warm_allow_compile=true;
 const HostPso::Recipe*expected_recipe{};HostPso::ArtifactOutcome artifact_outcome;
 std::function<bool(HostKernels&,const HostPso::Recipe&)> replay;
 std::function<void()>describe_hook;
 HostPso::Invocation Descriptor(const HostPso::Invocation&i){return i;}
 std::optional<HostPso::Recipe>Describe(const HostPso::Invocation&i,const HostPso::Invocation&){
  if(describe_hook)describe_hook();HostPso::Recipe r;r.invocation=i;r.preparation=cache.identity;r.source_options=std::string(64,'b');r.descriptor=std::string(64,'c');
  using K=HostPso::Kind;r.entry0=i.kind==K::InlineUpdate?"inline_update":i.kind==K::RenderInlineUpdate?"update":i.kind==K::Fill?"fill_u32":i.kind==K::FormatRgb?"rgb_convert":i.kind==K::FormatDepth?"depth_convert":"main0";if(i.kind==K::PostProcess)r.entry1="main0";if(!HostPso::Valid(r))return{};return r;
 }
 bool Replay(const HostPso::Recipe&r){ck(warming&&expected_recipe&&*expected_recipe==r);return replay?replay(*this,r):false;}
 HostPso::FixedSeedResult PrepareFixedSeeds(bool,const std::function<bool()>&);
};
