// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Value-only persistent identity. No GPU objects, guest/program/permutation addresses,
// file reads or per-draw work. Call only after the requested function succeeded.
#include <CommonCrypto/CommonDigest.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>

namespace Metal {
enum class PersistentLibraryRoute : uint8_t { None, Source, LocalMetallib };
struct PersistentFunctionIdentity {
 static constexpr uint32_t Schema=1;
 std::array<unsigned char,32> digest{};
 PersistentLibraryRoute route{PersistentLibraryRoute::None};
 [[nodiscard]] bool valid()const noexcept {
  return (route==PersistentLibraryRoute::Source||route==PersistentLibraryRoute::LocalMetallib)&&
      std::ranges::any_of(digest,[](auto v){return v!=0;});
 }
 [[nodiscard]] std::string hex()const {
  if(!valid())return {};constexpr char h[]="0123456789abcdef";std::string s;s.reserve(64);
  for(auto c:digest){s+=h[c>>4];s+=h[c&15];}return s;
 }
 bool operator==(const PersistentFunctionIdentity&)const=default;
};
namespace FunctionIdentity {
inline bool Hex(std::string_view s){return s.size()==64&&std::ranges::all_of(s,[](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');});}
class Writer {
 CC_SHA256_CTX context{};
public:
 Writer(){CC_SHA256_Init(&context);}
 void raw(const void* bytes,size_t n){auto*p=static_cast<const unsigned char*>(bytes);while(n){auto count=std::min<size_t>(n,std::numeric_limits<CC_LONG>::max());CC_SHA256_Update(&context,p,static_cast<CC_LONG>(count));p+=count;n-=count;}}
 void word(uint64_t n){std::array<unsigned char,8> b{};for(unsigned i=0;i<8;++i)b[i]=static_cast<unsigned char>(n>>(i*8));raw(b.data(),b.size());}
 void text(std::string_view s){word(s.size());if(!s.empty())raw(s.data(),s.size());}
 std::array<unsigned char,32> finish(){std::array<unsigned char,32> d{};CC_SHA256_Final(d.data(),&context);return d;}
};
inline std::string Sha(std::string_view text){Writer w;w.raw(text.data(),text.size());PersistentFunctionIdentity id{w.finish(),PersistentLibraryRoute::Source};return id.hex();}

// Explicit effective values, not a hash of an Objective-C pointer/object layout.
// Unsupported nonempty macros/dynamic libraries/installName are rejected by the
// ObjC adapter rather than silently omitted. Changing this schema changes the key.
struct CompileOptions {
 uint64_t language{},math_api{},math_mode{},math_fp{},legacy_fast_math{};
 uint64_t library_type{},preserve_invariance{},optimization{},symbol_visibility{};
 uint64_t allow_undefined{},max_threads{},required_x{},required_y{},required_z{};
 uint64_t enable_logging{},conversion_rounding_api{},conversion_rounding{};
 bool supported{};
 void write(Writer& w)const {
  w.text("effective-mtl-compile-options-v1");
  for(auto v:{language,math_api,math_mode,math_fp,legacy_fast_math,library_type,
      preserve_invariance,optimization,symbol_visibility,allow_undefined,max_threads,
      required_x,required_y,required_z,enable_logging,conversion_rounding_api,conversion_rounding})w.word(v);
  // Canonical explicit defaults of all collection/name options used by this producer.
  w.text("preprocessor-macros:empty");w.text("dynamic-libraries:empty");w.text("install-name:empty");
 }
};
struct Context {
 std::string cohort,device_name,os_build,compiler;
 uint64_t registry_id{};
 [[nodiscard]] bool valid()const {
  return Hex(cohort)&&registry_id&& !device_name.empty()&&device_name.size()<=256&&
      !os_build.empty()&&os_build.size()<=256&&!compiler.empty()&&compiler.size()<=256;
 }
 void write(Writer& w)const {w.text(cohort);w.word(registry_id);w.text(device_name);w.text(os_build);w.text(compiler);}
};
inline std::string MemoNamespace(const Context& context,const CompileOptions& options,
                                 bool local_lookup=false,std::string_view local_cohort={},
                                 std::string_view local_build={}){
 if(!context.valid()||!options.supported)return {};Writer w;w.text("function-memo-context-v1");context.write(w);options.write(w);
 // A source-collection compiler must not satisfy a later load-mode request with
 // its old weak source library; the returned identity still records actual origin.
 w.word(local_lookup);if(local_lookup){w.word(Hex(local_cohort));if(Hex(local_cohort))w.text(local_cohort);
  w.word(Hex(local_build));if(Hex(local_build))w.text(local_build);}
 return PersistentFunctionIdentity{w.finish(),PersistentLibraryRoute::Source}.hex();
}
struct SuccessfulLibrary {
 // source_sha is the already computed exact effective-source SHA when available.
 // The caller must derive it from this exact successful source, never a filename/hash label.
 std::string_view source_sha,entry,local_cohort,local_build,local_key,local_library_sha;
 PersistentLibraryRoute route{PersistentLibraryRoute::None};
 bool requested_entry_succeeded{};
};
inline PersistentFunctionIdentity Make(const Context& context,const CompileOptions& options,
                                        const SuccessfulLibrary& success){
 if(!success.requested_entry_succeeded||!context.valid()||!options.supported||
    !Hex(success.source_sha)||success.entry.empty()||success.entry.size()>256||
    success.entry.find('\0')!=std::string_view::npos)return {};
 if(success.route!=PersistentLibraryRoute::Source&&success.route!=PersistentLibraryRoute::LocalMetallib)return {};
 if(success.route==PersistentLibraryRoute::LocalMetallib&&
    (!Hex(success.local_cohort)||success.local_cohort!=context.cohort||!Hex(success.local_build)||
     !Hex(success.local_key)||!Hex(success.local_library_sha)))return {};
 Writer w;w.text("successful-metal-function-v1");w.word(PersistentFunctionIdentity::Schema);
 context.write(w);options.write(w);w.text(success.source_sha);w.text(success.entry);w.word(static_cast<uint64_t>(success.route));
 if(success.route==PersistentLibraryRoute::LocalMetallib){w.text(success.local_cohort);w.text(success.local_build);w.text(success.local_key);w.text(success.local_library_sha);}
 return {w.finish(),success.route};
}
// Bind the successful library provenance to the actual per-module variant and ABI.
// No object representation/padding, guest hash, permutation or process pointer is hashed.
template<typename Shader>
PersistentFunctionIdentity BindModule(const PersistentFunctionIdentity& library, const Shader& shader) {
    if(!library.valid()||!shader.function||shader.stage==decltype(shader.stage)::Unknown||shader.bindings.size()>4096||
       shader.stage_inputs.size()>128||shader.tess_layout.members.size()>128||
       shader.tess_layout.patch_members.size()>128||shader.variant.es.members.size()>128||
       shader.variant.patch_input.members.size()>128||shader.geometry.varyings.size()>128)return {};
    Writer w;w.text("compiled-metal-module-abi-v1");
    w.raw(library.digest.data(),library.digest.size());w.word(static_cast<uint64_t>(library.route));
    w.word(static_cast<uint64_t>(shader.stage));
    const auto& v=shader.variant;w.word(static_cast<uint64_t>(v.kind));w.word(v.index_type);
    w.word(static_cast<uint64_t>(v.domain));w.word(v.output_vertices);w.word(v.link_patch_input);
    const auto members=[&](const auto& values){w.word(values.size());for(const auto& m:values){
        w.word(m.location);w.word(m.position);w.word(m.offset);w.word(m.components);w.word(static_cast<uint64_t>(m.type));}};
    w.word(v.es.stride);members(v.es.members);w.word(v.patch_input.stride);members(v.patch_input.members);
    w.word(static_cast<uint64_t>(shader.tess.domain));w.word(static_cast<uint64_t>(shader.tess.spacing));
    w.word(shader.tess.clockwise);w.word(shader.tess.point_mode);w.word(shader.tess.output_vertices);
    for(auto n:shader.local_size)w.word(n);
    w.word(shader.push_constant_buffer);w.word(shader.uses_push_constants);w.word(shader.buffer_size_buffer);
    w.word(shader.uses_device_address);w.word(shader.uses_bda_pagetable);
    w.word(shader.num_buffers);w.word(shader.num_textures);w.word(shader.num_samplers);
    w.word(shader.bindings.size());for(const auto& b:shader.bindings){w.word(b.set);w.word(b.binding);
        w.word(static_cast<uint64_t>(b.kind));w.word(b.msl_index);w.word(b.count);}
    const auto& t=shader.tess_layout;w.word(t.input);w.word(t.output);w.word(t.patch_input);w.word(t.patch_output);
    w.word(t.patch_output_complete);members(t.members);members(t.patch_members);
    w.word(shader.stage_inputs.size());for(const auto& i:shader.stage_inputs){w.word(i.location);w.word(static_cast<uint64_t>(i.type));w.word(i.components);}
    const auto& g=shader.geometry;w.word(static_cast<uint64_t>(g.input));w.word(g.input_vertices);
    w.word(static_cast<uint64_t>(g.output));w.word(g.max_vertices);w.word(g.varyings.size());
    for(const auto& varying:g.varyings){w.word(varying.location);w.word(varying.components);w.word(static_cast<uint64_t>(varying.type));}
    w.word(shader.strict_vertex_helpers_inlined);w.word(shader.copy_pair_merged);
    return {w.finish(),library.route};
}

} // namespace FunctionIdentity
} // namespace Metal
