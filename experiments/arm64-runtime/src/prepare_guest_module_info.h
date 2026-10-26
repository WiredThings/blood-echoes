// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <filesystem>
#include "bundled_runtime_admission.h"
#include <vector>
#include <string>
#include <cstdlib>
#include <cerrno>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <chrono>
#include <stdexcept>
#include <cstdint>
#include <mach-o/dyld.h>
extern char **environ;
namespace BbGuestModules {
struct HelperSelection { std::filesystem::path executable,code_root,helper;bool bundled=false; };
inline HelperSelection SelectHelper(const std::filesystem::path&actual){
 namespace fs=std::filesystem;
 const auto executable=fs::absolute(actual);
 if(executable!=executable.lexically_normal()||executable!=fs::canonical(executable)||!fs::is_regular_file(executable))throw std::runtime_error("module runtime must be canonical regular executable");
 const auto project=fs::path(BB_PROTOTYPE_ROOT);
 bool application=false;for(const auto&part:executable)if(part.extension()==".app")application=true;
 if(application){
 for(const auto&role:{fs::path("bin/bb-probe-metal"),fs::path("bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal")}){
  auto root=executable;for(const auto&part:role){(void)part;root=root.parent_path();}
  if(root/role!=executable)continue;
  const auto app=root.parent_path().parent_path().parent_path();
  if(root.filename()!="bbport"||root.parent_path().filename()!="Resources"||root.parent_path().parent_path().filename()!="Contents"||app.extension()!=".app"||app.parent_path()!=project/"apps")throw std::runtime_error("module runtime is not a declared private QA application");
  const auto helper=root/"native-runtime-tools/prepare_module_metadata.py";
  if(helper!=fs::canonical(helper)||!fs::is_regular_file(helper))throw std::runtime_error("bundled module metadata helper missing or linked");
  return {executable,root,helper,true};
 }
 // An unknown bundled role is never an unbundled development fallback.
 throw std::runtime_error("module runtime application layout is not declared");
 }
 const auto relative=executable.lexically_relative(project);
 if(relative.empty()||relative=="."||*relative.begin()=="..")throw std::runtime_error("unbundled module runtime must stay in private development tree");
 return {executable,{},project/"tools/prepare_module_metadata.py",false};
}
inline HelperSelection ActualHelper(){
 const auto& admission=BbBundledRuntime::Actual();
 if(admission){
  if(!admission->IsCpu())throw std::runtime_error("module preparation requires admitted CPU role");
  return {admission->executable,admission->code_root,admission->tools.at("prepare_module_metadata.py"),true};
 }
 char path[4096];uint32_t size=sizeof(path);
 if(_NSGetExecutablePath(path,&size))throw std::runtime_error("module runtime executable path exceeds bound");
 return SelectHelper(path);
}
inline std::filesystem::path Prepare(const std::filesystem::path&boot,const std::filesystem::path&app0,const std::filesystem::path&runtime_work){
 // The launcher passes its verified session work directory, never substitutes
 // that directory for --user. Unbundled/legacy callers retain their old state.
 const auto output=runtime_work/".arm-module-info.bin";
 if(std::filesystem::is_symlink(output))throw std::runtime_error("linked module metadata output");
 const char*python=getenv("BB_QUICKSAVE_PYTHON");if(!python||!*python)python="/usr/bin/python3";
 const auto selected=ActualHelper();
 // -I ignores PYTHONDONTWRITEBYTECODE. Disable writes at interpreter startup,
 // before pathlib/stdlib imports can modify a signed bundled Python tree.
 std::vector<std::string> args{python,"-I","-B",selected.helper.string(),"--boot",boot.string(),"--app0",app0.string(),"--out",output.string()};
 if(selected.bundled){args.insert(args.end(),{"--code-root",selected.code_root.string(),"--runtime-executable",selected.executable.string()});}
 std::vector<char*> pointers;for(auto&arg:args)pointers.push_back(arg.data());pointers.push_back(nullptr);
 posix_spawnattr_t attr;posix_spawnattr_init(&attr);
 posix_spawnattr_setflags(&attr,POSIX_SPAWN_CLOEXEC_DEFAULT);
 posix_spawn_file_actions_t actions;posix_spawn_file_actions_init(&actions);
 for(int fd=0;fd<3;++fd)posix_spawn_file_actions_adddup2(&actions,fd,fd);
 pid_t child;const auto result=posix_spawn(&child,python,&actions,&attr,pointers.data(),environ);
 posix_spawn_file_actions_destroy(&actions);posix_spawnattr_destroy(&attr);
 if(result)throw std::runtime_error("module metadata helper spawn failed");
 const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(30);int status;
 for(;;){auto done=waitpid(child,&status,WNOHANG);if(done==child)break;if(done<0&&errno!=EINTR)throw std::runtime_error("module metadata helper wait failed");
  if(std::chrono::steady_clock::now()>deadline){kill(child,SIGKILL);while(waitpid(child,&status,0)<0&&errno==EINTR){}throw std::runtime_error("module metadata helper timeout");}usleep(10000);
 }
 if(!WIFEXITED(status)||WEXITSTATUS(status))throw std::runtime_error("module metadata preparation failed");return output;
}
}
