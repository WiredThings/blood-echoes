// SPDX-License-Identifier: GPL-2.0-or-later
#ifdef NDEBUG
#error assertions required
#endif
#include <cassert>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <unistd.h>
#include "remote/bb_peer_exit.h"
int main(){
 int status=0;unsigned calls=0;
 assert(!BbRemote::WaitForConfirmedPeerExit(0,status,[&](pid_t,int*,int)->pid_t{++calls;return 0;}));assert(!calls);
 const auto child=fork();assert(child>=0);if(!child)_exit(42);
 assert(BbRemote::WaitForConfirmedPeerExit(child,status,[&](pid_t p,int*s,int flags)->pid_t{
  if(!calls++){errno=EINTR;return -1;}return waitpid(p,s,flags);
 }));assert(calls==2&&WIFEXITED(status)&&WEXITSTATUS(status)==42);
 assert(!BbRemote::WaitForConfirmedPeerExit(child,status));assert(errno==ECHILD);
 assert(!BbRemote::WaitForConfirmedPeerExit(child,status,[](pid_t,int*,int)->pid_t{return 0;}));
 assert(!BbRemote::WaitForConfirmedPeerExit(child,status,[](pid_t,int*,int)->pid_t{errno=EINVAL;return -1;}));
 assert(!BbRemote::WaitForConfirmedPeerExit(child,status,[](pid_t p,int*s,int)->pid_t{*s=(SIGSTOP<<8)|0x7f;return p;}));
 const auto dying=fork();assert(dying>=0);if(!dying){for(;;)pause();}
 assert(!kill(dying,SIGTERM));assert(BbRemote::WaitForConfirmedPeerExit(dying,status));assert(WIFSIGNALED(status)&&WTERMSIG(status)==SIGTERM);
 puts("PEER EXIT actual waitpid: exact owned exit/signal, EINTR retry, ECHILD/error/no-status rejection passed");
}
