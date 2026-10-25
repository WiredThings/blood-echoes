// SPDX-License-Identifier: GPL-2.0-or-later
// Pure CPU: the actual identity/template and actual weak-memo implementation.
// NSObject supplies ARC lifetimes only; no Metal driver factory or GPU exists.
#import <Foundation/Foundation.h>
#include <cassert>
#include <cstdio>
#include <stdexcept>
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_library_memo.h"
namespace Metal { CompiledShader::~CompiledShader(){assert(!library&&!function);} }
using namespace Metal;
using namespace Metal::FunctionIdentity;
static unsigned checks=0;
static void Check(bool v){++checks;assert(v);}
static Context context(){return {std::string(64,'a'),"author GPU","author OS","author compiler",0x1234};}
static CompileOptions options(){CompileOptions o{};o.supported=true;o.language=0x030100;o.math_api=1;return o;}
int main(){@autoreleasepool {
 auto c=context();auto o=options();const auto source=Sha("author source");
 SuccessfulLibrary s{.source_sha=source,.entry="main0",.route=PersistentLibraryRoute::Source,.requested_entry_succeeded=true};
 const auto base=Make(c,o,s);Check(base.valid()&&base.hex().size()==64&&base.route==PersistentLibraryRoute::Source);
 Check(base==Make(c,o,s));
 auto changed=s;changed.requested_entry_succeeded=false;Check(!Make(c,o,changed).valid());
 changed=s;changed.entry={};Check(!Make(c,o,changed).valid());
 const std::string nul("main\0poison",11);changed=s;changed.entry=nul;Check(!Make(c,o,changed).valid());
 changed=s;changed.source_sha="pgm_hash";Check(!Make(c,o,changed).valid());
 changed=s;const auto another=Sha("changed effective fallback source");changed.source_sha=another;Check(Make(c,o,changed)!=base);
 changed=s;changed.entry="main1";Check(Make(c,o,changed)!=base);
 auto badc=c;badc.cohort="wrong";Check(!Make(badc,o,s).valid());
 badc=c;badc.registry_id=0;Check(!Make(badc,o,s).valid());
 badc=c;badc.device_name="other GPU";Check(Make(badc,o,s)!=base);
 badc=c;badc.os_build="other OS";Check(Make(badc,o,s)!=base);
 badc=c;badc.compiler="other compiler";Check(Make(badc,o,s)!=base);
 badc=c;badc.cohort=std::string(64,'b');Check(Make(badc,o,s)!=base);
 const std::array<uint64_t CompileOptions::*,17> members{
  &CompileOptions::language,&CompileOptions::math_api,&CompileOptions::math_mode,&CompileOptions::math_fp,
  &CompileOptions::legacy_fast_math,&CompileOptions::library_type,&CompileOptions::preserve_invariance,
  &CompileOptions::optimization,&CompileOptions::symbol_visibility,&CompileOptions::allow_undefined,
  &CompileOptions::max_threads,&CompileOptions::required_x,&CompileOptions::required_y,&CompileOptions::required_z,
  &CompileOptions::enable_logging,&CompileOptions::conversion_rounding_api,&CompileOptions::conversion_rounding};
 for(auto m:members){auto v=o;++(v.*m);Check(Make(c,v,s)!=base);Check(MemoNamespace(c,v)!=MemoNamespace(c,o));}
 auto unsupported=o;unsupported.supported=false;Check(!Make(c,unsupported,s).valid()&&MemoNamespace(c,unsupported).empty());
 const std::string build(64,'b'),key(64,'c'),library(64,'d');
 auto local=s;local.route=PersistentLibraryRoute::LocalMetallib;local.local_cohort=c.cohort;
 local.local_build=build;local.local_key=key;local.local_library_sha=library;
 Check(MemoNamespace(c,o)!=MemoNamespace(c,o,true,c.cohort,build));
 Check(MemoNamespace(c,o,true,c.cohort,build)!=MemoNamespace(c,o,true,c.cohort,key));
 const auto loaded=Make(c,o,local);Check(loaded.valid()&&loaded!=base&&loaded.route==PersistentLibraryRoute::LocalMetallib);
 for(auto field:{&SuccessfulLibrary::local_cohort,&SuccessfulLibrary::local_build,&SuccessfulLibrary::local_key,&SuccessfulLibrary::local_library_sha}){
  auto v=local;v.*field="";Check(!Make(c,o,v).valid());
 }
 auto local_other=local;const std::string other_library(64,'e');local_other.local_library_sha=other_library;Check(Make(c,o,local_other)!=loaded);
 local_other=local;const std::string other_build(64,'f');local_other.local_build=other_build;Check(Make(c,o,local_other)!=loaded);
 auto fallback=local;fallback.route=PersistentLibraryRoute::Source;Check(Make(c,o,fallback)==base);
 // Actual CompiledShader type and actual production ABI template. Only fake pointer
 // non-nullness is observed; no pointer enters the digest and none is invoked.
 {
  CompiledShader shader;shader.stage=ShaderStage::Vertex;
  Check(!BindModule(base,shader).valid());
  shader.function=reinterpret_cast<void*>(1);const auto module=BindModule(base,shader);Check(module.valid());
  shader.stage=ShaderStage::Unknown;Check(!BindModule(base,shader).valid());shader.stage=ShaderStage::Vertex;
  shader.function=reinterpret_cast<void*>(0x123456);Check(BindModule(base,shader)==module);
  shader.stage=ShaderStage::Fragment;Check(BindModule(base,shader)!=module);shader.stage=ShaderStage::Vertex;
  shader.variant.index_type=1;Check(BindModule(base,shader)!=module);shader.variant.index_type=0;
  shader.variant.es.members.push_back({0,false,4,2,ScalarType::Float});Check(BindModule(base,shader)!=module);shader.variant.es.members.clear();
  shader.bindings.push_back({0,1,ShaderBinding::Kind::Buffer,1,1});Check(BindModule(base,shader)!=module);shader.bindings.clear();
  shader.stage_inputs.push_back({2,ScalarType::Float,4});Check(BindModule(base,shader)!=module);shader.stage_inputs.clear();
  shader.tess_layout.patch_output=16;Check(BindModule(base,shader)!=module);shader.tess_layout.patch_output=0;
  shader.local_size[0]=32;Check(BindModule(base,shader)!=module);shader.local_size[0]=0;
  shader.function=nullptr;
 }
 // Actual memo propagates the successful origin, does not infer it from reused.
 LibraryMemo memo;unsigned calls=0;
 auto object=(id<MTLLibrary>)[NSObject new];auto function=(id<MTLFunction>)[NSObject new];
 const auto ns=MemoNamespace(c,o);
 auto first=memo.Get("source","main0",false,[&]{++calls;return MetalLibraryObjects{object,function,{},false,loaded};},ns);
 auto hit=memo.Get("source","main0",false,[&]()->MetalLibraryObjects{std::abort();},ns);
 Check(calls==1&&hit.reused&&!hit.function&&hit.persistent_identity==loaded);
 auto different=memo.Get("source","main0",false,[&]{++calls;return MetalLibraryObjects{object,function,{},false,base};},MemoNamespace(badc,o));
 Check(calls==2&&!different.reused&&different.persistent_identity==base);
 auto failed=memo.Get("failure","main0",false,[&]{return MetalLibraryObjects{nil,nil,"author failure",false,loaded};},ns);
 Check(!failed.persistent_identity.valid());
 auto retry=memo.Get("failure","main0",false,[&]{return MetalLibraryObjects{object,function,{},false,base};},ns);
 Check(retry.persistent_identity==base);
 try {memo.Get("throw","main0",false,[]()->MetalLibraryObjects{throw std::runtime_error("author");},ns);Check(false);}catch(const std::runtime_error&){Check(true);}
 auto after_throw=memo.Get("throw","main0",false,[&]{return MetalLibraryObjects{object,function,{},false,base};},ns);Check(after_throw.persistent_identity==base);
 // Expired weak entry rebuilds with new actual origin and does not retain the object.
 __weak id expired=nil;
 @autoreleasepool {
  auto temporary=(id<MTLLibrary>)[NSObject new];expired=temporary;
  auto made=memo.Get("expires","main0",false,[&]{return MetalLibraryObjects{temporary,function,{},false,loaded};},ns);
  Check(made.persistent_identity==loaded);
 }
 Check(!expired);
 auto rebuilt=memo.Get("expires","main0",false,[&]{return MetalLibraryObjects{object,function,{},false,base};},ns);Check(rebuilt.persistent_identity==base&&!rebuilt.reused);
 std::printf("CPU_IDENTITY_RESULT checks=%u route/option/cohort/module/actual-weak-memo passed; no GPU\n",checks);
 return 0;
}}
