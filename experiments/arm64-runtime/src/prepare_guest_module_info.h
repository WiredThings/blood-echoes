// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <filesystem>
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
extern char **environ;
namespace BbGuestModules {
inline std::filesystem::path Prepare(const std::filesystem::path&boot,const std::filesystem::path&app0,const std::filesystem::path&state){
 const auto output=state/".arm-module-info.bin";
 if(std::filesystem::is_symlink(output))throw std::runtime_error("linked module metadata output");
 const char*python=getenv("BB_QUICKSAVE_PYTHON");if(!python||!*python)python="/usr/bin/python3";
 std::vector<std::string> args{python,"-I",std::string(BB_PROTOTYPE_ROOT)+"/tools/prepare_module_metadata.py","--boot",boot.string(),"--app0",app0.string(),"--out",output.string()};
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
