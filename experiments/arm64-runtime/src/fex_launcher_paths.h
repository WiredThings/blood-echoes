// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "launcher_bundled_paths.h"
namespace BbFexLauncher {
namespace fs=std::filesystem;
inline bool Hex(std::string_view text,size_t count){return text.size()==count&&text.find_first_not_of("0123456789abcdef")==text.npos;}
// Caller has admitted the actual executable and original launcher data roles.
// No environment flag grants access to arbitrary user directories.
struct Paths {
 fs::path data,root;
 Paths(const fs::path& selected,const fs::path&user,const fs::path&game):data(selected),root(selected/"fex-preparation"){
  BbLauncherPaths::Role(data,true,true);BbLauncherPaths::Role(root,true,true);
  for(const auto&other:{user,game}){auto a=root.lexically_relative(other),b=other.lexically_relative(root);
   BbSession::check((a.empty()||*a.begin()=="..")&&(b.empty()||*b.begin()==".."),"FEX preparation overlaps game/progress");}
 }
 fs::path Directory(const fs::path&p,const char* group,size_t digits)const{
  BbSession::check(p.parent_path()==root/group&&Hex(p.filename().string(),digits),"FEX selected directory role mismatch");
  BbLauncherPaths::Role(root/group,true,true);return BbLauncherPaths::Role(p,true,true);
 }
 void Export(const fs::path&p,const fs::path&elf)const{
  BbSession::check(elf==data/"out/eboot.elf","FEX original ELF role mismatch");BbLauncherPaths::Role(elf,false,true);
  BbSession::check(p.parent_path()==root/"effective"&&Hex(p.filename().string(),32)&&fs::symlink_status(p).type()==fs::file_type::not_found,"FEX fresh effective output role mismatch");
  BbLauncherPaths::Role(root/"effective",true,true);
 }
 void Plan(const fs::path&file,const fs::path&generation)const{
  Directory(generation,"products",64);auto name=file.filename().string();
  BbSession::check(file.parent_path()==generation&&name.starts_with("plan-")&&name.ends_with(".bin")&&Hex(std::string_view(name).substr(5,name.size()-9),64),"FEX plan role mismatch");BbLauncherPaths::Role(file,false,true);
 }
};
}
