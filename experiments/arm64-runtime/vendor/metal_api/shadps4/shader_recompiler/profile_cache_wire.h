// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "shader_recompiler/profile.h"
#include <array>
#include <bit>
#include <cstddef>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>

namespace Shader::ProfileCacheWire {
// Only ShaderProfile's body evolves; shader/meta/key version words stay intact.
inline constexpr u32 FeatureMagic=0x31504642u; // BFP1, not a valid viewport width.
inline constexpr u32 FeatureVersion=1;
inline constexpr size_t HeaderBytes=16,RawBytes=80,FeatureBytes=20,LegacyShortBytes=72;
static_assert(std::endian::native==std::endian::little);
static_assert(sizeof(Profile)==RawBytes&&sizeof(bool)==1);
static_assert(offsetof(Profile,support_int8)==24&&offsetof(Profile,paged_buffers)==63);
static_assert(offsetof(Profile,buffer_copy_shader_hash)==64);
static_assert(offsetof(Profile,relaxed_fragment_compute_float)==72);
static_assert(offsetof(Profile,two_level_bda)==73&&offsetof(Profile,relaxed_vertex_float)==74);

inline void Need(bool ok,const char*reason){if(!ok)throw std::runtime_error(reason);}
inline u32 Word(std::span<const u8>bytes,size_t at){Need(at<=bytes.size()&&4<=bytes.size()-at,"Profile word span");u32 word{};std::memcpy(&word,bytes.data()+at,4);return word;}
inline void Put(std::vector<u8>&bytes,size_t at,u32 word){std::memcpy(bytes.data()+at,&word,4);}
struct Decoded {Profile profile{};bool legacy{};bool legacy_short{};};

inline Decoded Decode(std::span<const u8>bytes,const std::array<u32,4>&versions){
 Need(bytes.size()>=HeaderBytes,"Profile header extent");
 Need(std::memcmp(bytes.data(),versions.data(),HeaderBytes)==0,"Profile compiler/cache versions");
 auto body=bytes.subspan(HeaderBytes);Decoded decoded;u32 viewport_width{};
 // An envelope marker is recognized BEFORE legacy size dispatch, so a truncated
 // current blob cannot masquerade as a padded legacy Profile.
 if(body.size()>=4&&Word(body,0)==FeatureMagic){
  Need(body.size()==FeatureBytes+RawBytes,"Profile feature envelope extent");
  Need(Word(body,4)==FeatureVersion&&Word(body,8)==RawBytes,"Profile feature envelope version/layout");
  const auto flags=Word(body,12);Need((flags&~3u)==0,"unknown Profile feature envelope bits");
  viewport_width=Word(body,16);Need(viewport_width<=65536,"Profile feature viewport width");
  body=body.subspan(FeatureBytes);
  Need(Word(body,0)==FeatureMagic,"Profile feature payload marker");
  Need(body[74]<=1,"Profile invariant bool representation");
  Need(flags==(u32(body[73])|(u32(body[74])<<1)),"Profile feature/raw disagreement");
 }else{
  Need(body.size()==RawBytes||body.size()==LegacyShortBytes,"legacy Profile extent");
  decoded.legacy=true;decoded.legacy_short=body.size()==LegacyShortBytes;
 }
 for(size_t at=24;at<64;++at)Need(body[at]<=1,"Profile legacy bool representation");
 if(body.size()==RawBytes){Need(body[72]<=1&&body[73]<=1,"Profile tail bool representation");}
 // Legacy byte74 was padding and may contain ANY byte. Copy only the old
 // named fields, never reinterpret that padding as the new bool.
 const auto named=decoded.legacy?(decoded.legacy_short?LegacyShortBytes:74u):75u;
 std::memcpy(&decoded.profile,body.data(),named);
 if(decoded.legacy)decoded.profile.relaxed_vertex_float=false;
 else decoded.profile.max_viewport_width=viewport_width;
 return decoded;
}

inline std::vector<u8> Encode(const Profile&profile,const std::array<u32,4>&versions){
 const bool feature=profile.relaxed_vertex_float;
 const size_t prefix=HeaderBytes+(feature?FeatureBytes:0);
 std::vector<u8>bytes(prefix+RawBytes,0);
 std::memcpy(bytes.data(),versions.data(),HeaderBytes);
 if(feature){Need(profile.max_viewport_width<=65536,"Profile feature viewport width");
  Put(bytes,16,FeatureMagic);Put(bytes,20,FeatureVersion);Put(bytes,24,RawBytes);
  Put(bytes,28,u32(profile.two_level_bda)|(u32(profile.relaxed_vertex_float)<<1));
  Put(bytes,32,profile.max_viewport_width);}
 // Canonical zero padding: named values only. Existing compatible legacy blobs
 // need not be rewritten, and default-off keeps the exact old wire length.
 std::memcpy(bytes.data()+prefix,&profile,feature?75u:74u);
 if(feature)Put(bytes,prefix,FeatureMagic);
 return bytes;
}
} // namespace Shader::ProfileCacheWire
