// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>
namespace Metal::LocalLibrary {
enum class Route:std::uint32_t {None,Source,LocalMetallib};
// Value provenance, never a function-pointer registry. Library maps retain this
// alongside the original native library; callers only admit after their entry succeeds.
struct Origin {
 Route route=Route::None;
 std::string source_sha256,cohort,build_id,key,library_sha256;
 bool operator==(const Origin&)const=default;
 bool valid()const{
  const auto hex=[](std::string_view s){if(s.size()!=64)return false;for(char c:s)if(!((c>='a'&&c<='f')||(c>='0'&&c<='9')))return false;return true;};
  return hex(source_sha256)&&(route==Route::Source||(route==Route::LocalMetallib&&hex(cohort)&&hex(build_id)&&hex(key)&&hex(library_sha256)));
 }
 std::string Canonical()const{
  if(!valid())return {};std::string out="helper-library-success-v1:"+std::to_string(std::uint32_t(route));
  const auto text=[&](std::string_view s){out+=':';out+=std::to_string(s.size());out+=':';out+=s;};text(source_sha256);
  if(route==Route::LocalMetallib)for(auto*s:{&cohort,&build_id,&key,&library_sha256})text(*s);return out;
 }
};
}
