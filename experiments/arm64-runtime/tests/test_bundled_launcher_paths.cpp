// SPDX-License-Identifier: GPL-2.0-or-later
#include "launcher_bundled_paths.h"
#include <cassert>
#include <iostream>
int main(){
 namespace fs=std::filesystem;const auto root=fs::canonical(fs::temp_directory_path())/("bundled-launcher-"+std::to_string(getpid()));
 assert(fs::create_directory(root));fs::permissions(root,fs::perms::owner_all,fs::perm_options::replace);
 const auto data=root/"data",user=root/"progress",game=root/"selected-game";
 for(const auto&p:{data,user,game})fs::create_directory(p);fs::create_directory(data/"out");
 const auto config=data/"bbport.ini",boot=data/"out/boot-linked.bin",content=data/"out/content.bin",patch=data/"out/patches.bin";
 for(const auto&p:{config,boot,content,patch})std::ofstream(p)<<"author input";
 std::ofstream(user/"progress")<<"keep";
 unsigned cases=0;auto reject=[&](auto fn){bool no=false;try{fn();}catch(const std::exception&){no=true;}assert(no);++cases;};
 auto inputs=[&](auto selected){BbLauncherPaths::Inputs(data,config,boot,content,patch,user,game,selected);};
 inputs(game);++cases;
 const auto fresh_user=root/"new-progress";BbLauncherPaths::Inputs(data,config,boot,content,patch,fresh_user,game,game);assert(!fs::exists(fresh_user));++cases;
 const auto overlay=data/"out/mod-game-authored";fs::create_directory(overlay);fs::create_symlink(game,overlay/"original-assets");inputs(overlay);++cases; // original merged view may link assets, not its role root
 reject([&]{BbLauncherPaths::Inputs(data,config,boot,content,root/"patches.bin",user,game,game);});
 const auto wrong=data/"out/wrong";fs::create_directory(wrong);reject([&]{inputs(wrong);});
 const auto other=root/"mod-game-outside";fs::create_directory(other);reject([&]{inputs(other);});
 const auto restart_nonce=std::string(32,'f');const auto session_nonce=std::string(32,'e');
 assert(BbLauncherPaths::RestartMatches(user,restart_nonce,user.c_str(),restart_nonce.c_str()));++cases;
 assert(!BbLauncherPaths::RestartMatches(user,restart_nonce,user.c_str(),session_nonce.c_str()));++cases;
 assert(!BbLauncherPaths::RestartMatches(user,restart_nonce,(root/"other-user").c_str(),restart_nonce.c_str()));++cases;
 assert(!BbLauncherPaths::RestartMatches(user,restart_nonce,user.c_str(),"bad"));++cases;
 const auto nonce=std::string(32,'e');const auto identity="ARMLAUNCH1\n"+boot.string()+"\n"+game.string()+"\n"+content.string()+"\n"+nonce+"\n";
 auto work=BbSession::PrepareBundledLauncherWork(data,config,boot,content,nonce,identity);++cases;
 assert(work==data/"out/runtime-sessions"/nonce&&fs::file_size(user/"progress")==4&&!fs::exists(user/".arm-runtime-session"));
 assert(BbSession::PrepareBundledLauncherWork(data,config,boot,content,nonce,identity)==work);++cases;
 auto changed=identity;changed.replace(changed.find(game.string()),game.string().size(),other.string());reject([&]{BbSession::PrepareBundledLauncherWork(data,config,boot,content,nonce,changed);});
 reject([&]{BbSession::PrepareBundledLauncherWork(data,config,boot,content,nonce,"ARMQA1\n"+nonce+"\n");});
 reject([&]{BbSession::PrepareBundledLauncherWork(data,config,boot,content,"bad",identity);});
 fs::rename(config,data/"original.ini");fs::create_symlink(data/"original.ini",config);reject([&]{inputs(game);});fs::remove(config);fs::rename(data/"original.ini",config);
 fs::create_hard_link(config,data/"hard-config");reject([&]{inputs(game);});fs::remove(data/"hard-config");
 fs::permissions(user,fs::perms::group_write,fs::perm_options::add);reject([&]{inputs(game);});fs::permissions(user,fs::perms::group_write,fs::perm_options::remove);
 fs::rename(user,root/"actual-user");fs::create_directory_symlink(root/"actual-user",user);reject([&]{inputs(game);});fs::remove(user);fs::rename(root/"actual-user",user);
 fs::remove(work/".arm-runtime-session");reject([&]{BbSession::PrepareBundledLauncherWork(data,config,boot,content,nonce,identity);});
 assert(fs::file_size(user/"progress")==4&&!fs::exists(user/".arm-runtime-session"));fs::remove_all(root);
 std::cout<<"BUNDLED_LAUNCHER_PATHS "<<cases<<" author controls passed; no game/progress IO beyond authored fixture\n";
}
