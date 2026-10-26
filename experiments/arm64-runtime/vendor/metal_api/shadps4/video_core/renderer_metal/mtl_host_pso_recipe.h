// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Fixed helper metadata only. No source, resource data, addresses, native handles,
// executable cache walker, or pipeline ownership is stored in these records.
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstddef>
#include <optional>
#include <span>
#include <string>
#include <vector>
#include "video_core/renderer_metal/mtl_library_origin.h"
namespace Metal::HostPso {
using U=std::uint32_t;
enum class Kind:U {Tiling=1,TextureTiling,FaultParser,InlineUpdate,RenderInlineUpdate,Fill,MsCopy,MsDepth,UpscaleMerge,PostProcess,CameraMotion,FormatRgb,FormatDepth};
enum class Type:U {Bool=1,U32,I32,F32};
inline constexpr U Version=2,MaxConstants=14,MaxWarm=64,MaxBytes=2048,MaxMetadata=4096;
enum class ArtifactState {Unavailable,Hit,Built,Deferred,Failed,Critical};
struct ArtifactOutcome {ArtifactState state=ArtifactState::Unavailable;bool compiled=false;std::string archive_sha256;std::array<LocalLibrary::Route,2> actual_routes{};U origin_count{};bool ready()const{return state==ArtifactState::Hit||state==ArtifactState::Built;}};
enum class WarmState {Inactive,Complete,Deferred,Critical};
struct WarmResult {WarmState state=WarmState::Inactive;U known{},processed{},ready{},built{},rejected{},failed{},deferred{};bool catalog_complete=true;};
struct Invocation {
 Kind kind{};U bits{},macro{},proxy{},bounded{},samples{},format{},src_msaa{},count{};
 std::array<U,MaxConstants> provided{};
 bool operator==(const Invocation&)const=default;
};
struct Constant {U id{};Type type{};U bits{};bool operator==(const Constant&)const=default;};
struct Recipe {
 Invocation invocation;
 std::vector<Constant> constants;
 std::string preparation,source_options,descriptor,entry0,entry1;
 bool operator==(const Recipe&)const=default;
};
inline bool Hex(const std::string&s){return s.size()==64&&std::all_of(s.begin(),s.end(),[](char c){return(c>='0'&&c<='9')||(c>='a'&&c<='f');});}
inline bool Entry(const std::string&s){return s.size()<=64&&std::all_of(s.begin(),s.end(),[](char c){return(c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_';});}
inline bool Valid(const Recipe&r){
 const auto&i=r.invocation;const U kind=U(i.kind);
 if(kind<1||kind>13||i.macro>1||i.proxy>1||i.bounded>1||i.src_msaa>1||i.count>MaxConstants||
    !Hex(r.preparation)||!Hex(r.source_options)||!Hex(r.descriptor)||r.entry0.empty()||!Entry(r.entry0)||!Entry(r.entry1)||r.constants.size()>MaxConstants)return false;
 if(i.kind==Kind::Tiling||i.kind==Kind::TextureTiling){
  if(i.bits!=8&&i.bits!=16&&i.bits!=32&&i.bits!=64&&i.bits!=96&&i.bits!=128)return false;
  if(i.kind==Kind::TextureTiling&&(!i.macro||i.count>12))return false;
 }
 const auto zero_from=[&](U begin){return std::all_of(i.provided.begin()+begin,i.provided.end(),[](U n){return n==0;});};
 if(i.kind==Kind::Tiling&&(i.count!=14||i.proxy||i.samples||i.format||i.src_msaa||i.provided[13]!=i.bounded))return false;
 if(i.kind==Kind::TextureTiling&&(!zero_from(i.count)||i.samples||i.format||i.src_msaa))return false;
 if(i.kind==Kind::FaultParser&&(i.count!=2||!zero_from(2)||i.bits||i.macro||i.proxy||i.bounded||i.samples||i.format||i.src_msaa))return false;
 if(U(i.kind)>=U(Kind::InlineUpdate)&&(i.count||!zero_from(0)||i.bits||i.macro||i.proxy||i.bounded))return false;
 if((i.kind==Kind::InlineUpdate||i.kind==Kind::RenderInlineUpdate||i.kind==Kind::Fill||i.kind==Kind::UpscaleMerge||i.kind==Kind::CameraMotion||i.kind==Kind::FormatRgb||i.kind==Kind::FormatDepth)&&(i.samples||i.format||i.src_msaa))return false;
 if((i.kind==Kind::FormatRgb||i.kind==Kind::FormatDepth)&&(!r.constants.empty()||!r.entry1.empty()||r.entry0!=(i.kind==Kind::FormatRgb?"rgb_convert":"depth_convert")))return false;
 if(i.kind==Kind::MsDepth&&i.src_msaa)return false;
 if(i.kind==Kind::PostProcess&&(r.entry1.empty()||!i.format||i.format>65535||i.samples||i.src_msaa))return false;
 if(i.kind==Kind::MsCopy||i.kind==Kind::MsDepth){if(!i.samples||i.samples>64||(i.samples&(i.samples-1))||!i.format||i.format>65535)return false;}
 U last=0;bool first=true;
 for(const auto&c:r.constants){if(c.id>=MaxConstants||U(c.type)<1||U(c.type)>4||(!first&&c.id<=last)||(c.type==Type::Bool&&c.bits>1))return false;last=c.id;first=false;}
 if(i.bounded){const auto d=std::find_if(r.constants.begin(),r.constants.end(),[](const auto&c){return c.id==3;});if(d==r.constants.end()||!d->bits)return false;}
 return true;
}
// Fair deterministic continuation, including already-prepared entries. The
// cursor is cohort-scoped metadata; no live native objects are retained here.
inline std::vector<std::size_t> Order(const std::vector<std::string>&names,const std::string&cursor){
 std::vector<std::size_t> order(names.size());for(std::size_t i=0;i<order.size();++i)order[i]=i;
 std::sort(order.begin(),order.end(),[&](auto a,auto b){return names[a]<names[b];});
 const auto first=std::find_if(order.begin(),order.end(),[&](auto i){return names[i]>cursor;});
 std::rotate(order.begin(),first,order.end());return order;
}
inline void Word(std::vector<std::uint8_t>&o,U n){for(int b=0;b<4;++b)o.push_back(std::uint8_t(n>>(8*b)));}
inline void Text(std::vector<std::uint8_t>&o,const std::string&s){Word(o,U(s.size()));o.insert(o.end(),s.begin(),s.end());}
inline std::vector<std::uint8_t> Encode(const Recipe&r){
 if(!Valid(r))return {};std::vector<std::uint8_t>o;Word(o,0x4850534f);Word(o,Version);const auto&i=r.invocation;
 for(U n:{U(i.kind),i.bits,i.macro,i.proxy,i.bounded,i.samples,i.format,i.src_msaa,i.count})Word(o,n);
 for(auto n:i.provided)Word(o,n);
 Word(o,U(r.constants.size()));for(const auto&c:r.constants)for(U n:{c.id,U(c.type),c.bits})Word(o,n);
 for(const auto*s:{&r.preparation,&r.source_options,&r.descriptor,&r.entry0,&r.entry1})Text(o,*s);
 return o.size()<=MaxBytes?o:std::vector<std::uint8_t>{};
}
inline std::optional<Recipe> Decode(std::span<const std::uint8_t>data){
 if(data.size()>MaxBytes)return {};std::size_t at=0;bool good=true;
 const auto word=[&](){if(at>data.size()||data.size()-at<4){good=false;return U{};}U v=0;for(int b=0;b<4;++b)v|=U(data[at++])<<(8*b);return v;};
 const auto text=[&](){const auto n=word();if(!good||n>64||at>data.size()||n>data.size()-at){good=false;return std::string{};}std::string s(reinterpret_cast<const char*>(data.data()+at),n);at+=n;return s;};
 if(word()!=0x4850534f||word()!=Version||!good)return {};
 Recipe r;auto&i=r.invocation;i.kind=Kind(word());i.bits=word();i.macro=word();i.proxy=word();i.bounded=word();i.samples=word();i.format=word();i.src_msaa=word();i.count=word();
 for(auto&n:i.provided)n=word();const U n=word();if(!good||n>MaxConstants)return {};
 for(U k=0;k<n;++k){Constant c;c.id=word();c.type=Type(word());c.bits=word();r.constants.push_back(c);}
 r.preparation=text();r.source_options=text();r.descriptor=text();r.entry0=text();r.entry1=text();
 if(!good||at!=data.size()||!Valid(r)||Encode(r)!=std::vector<std::uint8_t>(data.begin(),data.end()))return {};return r;
}
// Discovery requests deliberately contain no actual toolchain route or native
// library bytes. Artifact admission combines the current successful origin only
// after the real entry/constants factory has succeeded.
inline std::vector<std::uint8_t> ArtifactIdentityBytes(const Recipe&r,const std::string&actual_preparation,
 std::span<const LocalLibrary::Origin> origins){
 if(!Valid(r)||!Hex(actual_preparation)||origins.size()!=(r.entry1.empty()?1u:2u))return {};
 std::vector<std::uint8_t>bytes;Text(bytes,"helper-native-pipeline-artifact-v2");Text(bytes,actual_preparation);
 const auto request=Encode(r);Word(bytes,U(request.size()));bytes.insert(bytes.end(),request.begin(),request.end());
 for(const auto&origin:origins){if(!origin.valid()||(origin.route==LocalLibrary::Route::LocalMetallib&&origin.cohort!=r.preparation))return {};Text(bytes,origin.Canonical());}
 return bytes;
}
// raw bits are deliberate for float and signed constants; only Bool normalizes.
inline U Bits(Type t,U n){return t==Type::Bool?U(n!=0):n;}
}
