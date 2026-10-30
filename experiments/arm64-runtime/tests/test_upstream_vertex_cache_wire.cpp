// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/profile_cache_wire.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace Shader;
namespace Wire=Shader::ProfileCacheWire;
constexpr std::array<u32,4> Versions{0x42425043,10,7,5};
template<class Fn>void Reject(Fn fn){bool rejected=false;try{fn();}catch(const std::exception&){rejected=true;}assert(rejected);}
void Write(const std::filesystem::path&path,const std::vector<u8>&bytes){std::ofstream f(path,std::ios::binary);f.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());assert(f.good());}
int main(int argc,char**argv){
 assert(argc==2);const std::filesystem::path out=argv[1];
 Profile baseline{};baseline.max_viewport_width=16384;baseline.max_viewport_height=16384;
 baseline.supported_spirv=0x10600;baseline.subgroup_size=32;baseline.two_level_bda=true;
 baseline.relaxed_fragment_compute_float=true;baseline.buffer_copy_shader_hash=0x1234567890abcdef;
 auto old=Wire::Encode(baseline,Versions);assert(old.size()==96);Write(out/"legacy.bin",old);
 for(unsigned padding=0;padding<256;++padding){auto bytes=old;for(size_t at=74;at<80;++at)bytes[16+at]=padding;
  auto decoded=Wire::Decode(bytes,Versions);assert(decoded.legacy&&decoded.profile==baseline&&!decoded.profile.relaxed_vertex_float);
  if(padding==1)Write(out/"legacy-padding-one.bin",bytes);
 }
 auto short_profile=old;short_profile.resize(88);auto old_short=Wire::Decode(short_profile,Versions);
 auto strict=baseline;strict.relaxed_fragment_compute_float=false;strict.two_level_bda=false;
 assert(old_short.legacy_short&&old_short.profile==strict);Write(out/"legacy-short.bin",short_profile);
 auto current=baseline;current.relaxed_vertex_float=true;
 auto blob=Wire::Encode(current,Versions);assert(blob.size()==116);
 auto decoded=Wire::Decode(blob,Versions);assert(!decoded.legacy&&decoded.profile==current&&decoded.profile!=baseline);
 Write(out/"invariant.bin",blob);
 for(unsigned bda=0;bda<2;++bda){current.two_level_bda=bda;auto bytes=Wire::Encode(current,Versions);
  assert(Wire::Decode(bytes,Versions).profile==current);}
 for(size_t length=0;length<blob.size();++length){auto truncated=blob;truncated.resize(length);Reject([&]{Wire::Decode(truncated,Versions);});}
 // Excising the entire feature envelope still leaves a marker, never a legacy
 // payload whose new bool could be mistaken for old padding.
 auto downgrade=blob;downgrade.erase(downgrade.begin()+16,downgrade.begin()+36);
 Reject([&]{Wire::Decode(downgrade,Versions);});Write(out/"stripped-feature.bin",downgrade);
 auto shifted=blob;shifted.erase(shifted.begin()+16,shifted.begin()+20);Reject([&]{Wire::Decode(shifted,Versions);});
 for(auto at:{size_t(20),size_t(24),size_t(28),size_t(36)}){auto bad=blob;bad[at]^=0x40;Reject([&]{Wire::Decode(bad,Versions);});}
 auto inconsistent=blob;inconsistent[36+74]=0;Reject([&]{Wire::Decode(inconsistent,Versions);});
 auto badbool=blob;badbool[36+24]=2;Reject([&]{Wire::Decode(badbool,Versions);});
 auto bad_legacy=old;bad_legacy[16+24]=2;Reject([&]{Wire::Decode(bad_legacy,Versions);});
 auto trailing=blob;trailing.push_back(0);Reject([&]{Wire::Decode(trailing,Versions);});
 auto other_versions=Versions;other_versions[2]++;Reject([&]{Wire::Decode(blob,other_versions);});
 std::cout<<"{\"Profile.size\":"<<sizeof(Profile);
#define FIELD(name) std::cout<<",\"Profile." #name "\":"<<offsetof(Profile,name)<<",\"Profile." #name ".bytes\":"<<sizeof(baseline.name);
 FIELD(max_viewport_width) FIELD(max_viewport_height) FIELD(max_shared_memory_size)
 FIELD(supported_spirv) FIELD(subgroup_size) FIELD(sparse_page_shift) FIELD(buffer_copy_shader_hash)
#define MODULE_PROFILE_BOOL_FIELD(name) FIELD(name)
#include "module_profile_bool_fields.inc"
#undef MODULE_PROFILE_BOOL_FIELD
 FIELD(relaxed_vertex_float)
#undef FIELD
 std::cout<<"}\n";
}
