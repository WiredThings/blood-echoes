// SPDX-License-Identifier: GPL-2.0-or-later
#include "session_state.h"
#include <cassert>
#include <unistd.h>
int main(){
 namespace fs=std::filesystem;auto root=fs::canonical(fs::temp_directory_path())/("bb-session-unit-"+std::to_string(getpid()));assert(fs::create_directory(root));auto state=root/"qa";
 auto refused=[&](auto fn){bool failed=false;try{fn();}catch(const std::exception&){failed=true;}assert(failed);};
 BbSession::Prepare(state,root,"fixed boot and session identity\n",false);std::ofstream(state/"progress")<<"keep me";
 refused([&]{BbSession::Prepare(state,root,"fixed boot and session identity\n",false);});
 refused([&]{BbSession::Prepare(state,root,"other boot/session\n",true);});
 BbSession::Prepare(state,root,"fixed boot and session identity\n",true);
 assert(fs::file_size(state/"progress")==7);
 fs::rename(state/".arm-runtime-session",root/"marker");fs::create_symlink(root/"marker",state/".arm-runtime-session");
 refused([&]{BbSession::Prepare(state,root,"fixed boot and session identity\n",true);});assert(fs::file_size(state/"progress")==7);
 auto link=root/"linked";fs::create_directory_symlink(state,link);refused([&]{BbSession::Prepare(link,root,"fixed boot and session identity\n",true);});
 // Launcher progress remains at its original --user; only runtime scratch is
 // placed under the original data/out and reused by the original supervisor.
 auto data=root/"launcher-data";fs::create_directory(data);fs::create_directory(data/"out");
 const auto config=data/"bbport.ini",boot=data/"out/boot-linked.bin",content=data/"out/content.bin";
 std::ofstream(config)<<"original settings";std::ofstream(boot)<<"authored boot";std::ofstream(content)<<"authored content";
 const std::string nonce(32,'a');
 const auto identity="ARMQA1\n"+boot.string()+"\n"+(root/"base-game").string()+"\n"+content.string()+"\n"+nonce+"\n";
 auto user=root/"launcher-user";BbSession::Prepare(user,root,identity,false);std::ofstream(user/"progress")<<"original progress";
 const auto source_time=fs::last_write_time(user/".arm-runtime-session");
 auto prepare=[&](const std::string&key){BbSession::Prepare(user,root,identity,true);return BbSession::PrepareLauncherWork(data,root,config,boot,content,nonce,key);};
 auto work=prepare(identity);assert(work==data/"out/runtime-sessions"/nonce);
 assert((fs::status(work).permissions()&fs::perms::all)==fs::perms::owner_all);
 std::ofstream(work/".arm-module-info.bin")<<"runtime metadata";
 assert(prepare(identity)==work&&fs::file_size(work/".arm-module-info.bin")==16);
 assert(!fs::exists(user/".arm-module-info.bin")&&fs::file_size(user/"progress")==17&&fs::last_write_time(user/".arm-runtime-session")==source_time);
 for(const auto&field:{boot.string(),(root/"base-game").string(),content.string()}){
  auto changed=identity;changed.replace(changed.find(field),field.size(),field+"-changed");refused([&]{prepare(changed);});
 }
 refused([&]{BbSession::PrepareLauncherWork(data,root,config,boot,content,"../invalid",identity);});
 refused([&]{BbSession::PrepareLauncherWork(data,root,root/"other.ini",boot,content,nonce,identity);});
 refused([&]{BbSession::PrepareLauncherWork(data,root,config,root/"other.boot",content,nonce,identity);});
 refused([&]{BbSession::PrepareLauncherWork(data,root.parent_path()/"unrelated",config,boot,content,nonce,identity);});
 fs::permissions(work,fs::perms::owner_all|fs::perms::group_read,fs::perm_options::replace);refused([&]{prepare(identity);});fs::permissions(work,fs::perms::owner_all,fs::perm_options::replace);
 auto marker=work/".arm-runtime-session",saved=work/"original-marker";fs::rename(marker,saved);fs::create_symlink(saved,marker);refused([&]{prepare(identity);});fs::remove(marker);fs::create_hard_link(saved,marker);refused([&]{prepare(identity);});fs::remove(marker);fs::rename(saved,marker);
 fs::remove(marker);refused([&]{prepare(identity);}); // existing work is never silently adopted
 auto sessions=data/"out/runtime-sessions",moved=data/"out/runtime-sessions-owned";fs::rename(sessions,moved);fs::create_directory_symlink(moved,sessions);refused([&]{prepare(identity);});
 fs::remove_all(root);puts("SESSION fresh refusal, identity, resume preservation and symlink checks PASS");
}
