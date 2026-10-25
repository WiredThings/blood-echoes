#import <Foundation/Foundation.h>
#include <cassert>
#include <cstdio>
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_function_origin.h"
namespace Metal {CompiledShader::~CompiledShader(){assert(!library&&!function);}}
using namespace Metal;using namespace Metal::FunctionIdentity;
static unsigned checks=0;void Check(bool v){++checks;assert(v);}
int main(){@autoreleasepool{
 Context c{std::string(64,'a'),"author GPU","author OS","actual compiler",17};CompileOptions o{};o.supported=true;o.language=0x030100;o.math_api=1;
 LocalLibrary::Origin source{.route=LocalLibrary::Route::Source,.source_sha256=Sha("actual successful pass-through source")};
 CompiledShader shader;shader.stage=ShaderStage::Vertex;shader.entry="bb_gs_pass";shader.function=reinterpret_cast<void*>(1);
 shader.bindings.push_back({~0u,~0u,ShaderBinding::Kind::PushConstants,kPushConstantBuffer,1});shader.uses_push_constants=false;shader.num_buffers=1;
 const auto id=FromSuccessfulOrigin(c,o,source,shader.entry,true,shader);Check(id.valid());
 Check(id==BindModule(Make(c,o,{.source_sha=source.source_sha256,.entry=shader.entry,.route=PersistentLibraryRoute::Source,.requested_entry_succeeded=true}),shader));
 Check(!FromSuccessfulOrigin(c,o,source,shader.entry,false,shader).valid());
 auto invalid=source;invalid.route=LocalLibrary::Route::None;Check(!FromSuccessfulOrigin(c,o,invalid,shader.entry,true,shader).valid());
 invalid=source;invalid.source_sha256="bad";Check(!FromSuccessfulOrigin(c,o,invalid,shader.entry,true,shader).valid());
 const std::array<uint64_t CompileOptions::*,17> fields{&CompileOptions::language,&CompileOptions::math_api,&CompileOptions::math_mode,&CompileOptions::math_fp,&CompileOptions::legacy_fast_math,&CompileOptions::library_type,&CompileOptions::preserve_invariance,&CompileOptions::optimization,&CompileOptions::symbol_visibility,&CompileOptions::allow_undefined,&CompileOptions::max_threads,&CompileOptions::required_x,&CompileOptions::required_y,&CompileOptions::required_z,&CompileOptions::enable_logging,&CompileOptions::conversion_rounding_api,&CompileOptions::conversion_rounding};
 for(auto f:fields){auto changed=o;++(changed.*f);Check(FromSuccessfulOrigin(c,changed,source,shader.entry,true,shader)!=id);}
 auto unsupported=o;unsupported.supported=false;Check(!FromSuccessfulOrigin(c,unsupported,source,shader.entry,true,shader).valid());
 auto local=source;local.route=LocalLibrary::Route::LocalMetallib;local.cohort=c.cohort;local.build_id=std::string(64,'b');local.key=std::string(64,'c');local.library_sha256=std::string(64,'d');
 const auto lid=FromSuccessfulOrigin(c,o,local,shader.entry,true,shader);Check(lid.valid()&&lid!=id&&lid.route==PersistentLibraryRoute::LocalMetallib);
 auto fallback=local;fallback.route=LocalLibrary::Route::Source;Check(FromSuccessfulOrigin(c,o,fallback,shader.entry,true,shader)==id);
 auto badlocal=local;badlocal.cohort=std::string(64,'e');Check(!FromSuccessfulOrigin(c,o,badlocal,shader.entry,true,shader).valid());
 for(auto f:{&LocalLibrary::Origin::build_id,&LocalLibrary::Origin::key,&LocalLibrary::Origin::library_sha256}){auto changed=local;changed.*f="";Check(!FromSuccessfulOrigin(c,o,changed,shader.entry,true,shader).valid());}
 auto changedSource=source;changedSource.source_sha256=Sha("actual successful default debug fragment");Check(FromSuccessfulOrigin(c,o,changedSource,shader.entry,true,shader)!=id);
 shader.bindings[0].count=2;Check(FromSuccessfulOrigin(c,o,source,shader.entry,true,shader)!=id);shader.bindings[0].count=1;
 shader.num_buffers=2;Check(FromSuccessfulOrigin(c,o,source,shader.entry,true,shader)!=id);shader.num_buffers=1;
 shader.geometry.max_vertices=3;Check(FromSuccessfulOrigin(c,o,source,shader.entry,true,shader)!=id);shader.geometry.max_vertices=0;
 shader.stage=ShaderStage::Fragment;Check(FromSuccessfulOrigin(c,o,source,shader.entry,true,shader)!=id);shader.stage=ShaderStage::Vertex;
 shader.function=reinterpret_cast<void*>(0xf00);Check(FromSuccessfulOrigin(c,o,source,shader.entry,true,shader)==id);shader.function=nullptr;
 Check(!FromSuccessfulOrigin(c,o,source,shader.entry,true,shader).valid());
 std::printf("GEOMETRY_ORIGIN_CPU_RESULT checks=%u actual-CompiledShader/options17/origin/fallback/binding/geometry passed; no GPU\n",checks);
}}
