// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "session_state.h"
#include <string_view>
namespace BbLauncherPaths {
namespace fs=std::filesystem;
// The code admission is separate. These checks only bind the original selected
// argv/env roles; they do not grant a whole HOME or examine save file contents.
inline bool Nonce(const std::string_view value){return value.size()==32&&value.find_first_not_of("0123456789abcdef")==std::string_view::npos;}
inline bool RestartMatches(const fs::path&user,const std::string_view expected,const char*actual_user,const char*actual_nonce){
 return actual_user&&actual_nonce&&Nonce(expected)&&Nonce(actual_nonce)&&fs::path(actual_user)==user&&expected==actual_nonce;
}
inline fs::path Role(const fs::path& p,bool directory,bool writable){
 BbSession::check(p.is_absolute()&&p==p.lexically_normal()&&p.string().find_first_of("\r\n")==std::string::npos&&p.string().find('\0')==std::string::npos&&fs::canonical(p)==p,"launcher role must be exact canonical path");
 struct stat st{};BbSession::check(!lstat(p.c_str(),&st)&&!S_ISLNK(st.st_mode)&&(directory?S_ISDIR(st.st_mode):S_ISREG(st.st_mode))&&
  st.st_uid==getuid()&&(directory||st.st_nlink==1)&&(!writable||!(st.st_mode&0022)),"launcher role ownership/type/link/mode mismatch");return p;
}
inline void Progress(const fs::path&user){
 // The original runtime_file_configure creates one missing leaf. Do not create
 // or require a QA marker here; preserve its fresh-install behavior.
 const auto type=fs::symlink_status(user).type();
 if(type!=fs::file_type::not_found){Role(user,true,true);return;}
 BbSession::check(user.is_absolute()&&user==user.lexically_normal()&&user.filename()!="."&&user.filename()!=".."&&!user.filename().empty(),"invalid new progress role");
 Role(user.parent_path(),true,true);
}
inline void Inputs(const fs::path&data,const fs::path&config,const fs::path&boot,const fs::path&content,
 const fs::path&patches,const fs::path&user,const fs::path&base_game,const fs::path&app0){
 Role(data,true,true);Role(data/"out",true,true);
 BbSession::check(config==data/"bbport.ini"&&boot==data/"out/boot-linked.bin"&&content==data/"out/content.bin"&&patches==data/"out/patches.bin","launcher generated input roles disagree with selected data");
 for(const auto&p:{config,boot,content,patches})Role(p,false,true);
 Progress(user);Role(base_game,true,false);Role(app0,true,false);
 if(app0!=base_game)BbSession::check(app0.parent_path()==data/"out"&&app0.filename().string().starts_with("mod-game-")&&app0.filename().string().size()>9,"launcher app0 is neither selected game nor original merged-mod view");
}
}
