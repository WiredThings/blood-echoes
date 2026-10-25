// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cerrno>
#include <sys/types.h>
#include <sys/wait.h>
namespace BbRemote {
// Only a reaped, owned process with terminal status proves it can no longer
// touch a granted payload. ECHILD/other failures are not death receipts.
template<class Wait>
bool WaitForConfirmedPeerExit(pid_t peer,int& status,Wait wait) {
 if(peer<=0){errno=EINVAL;return false;}
 pid_t result;
 do {result=wait(peer,&status,0);} while(result<0&&errno==EINTR);
 if(result!=peer)return false;
 return WIFEXITED(status)||WIFSIGNALED(status);
}
inline bool WaitForConfirmedPeerExit(pid_t peer,int& status) {
 return WaitForConfirmedPeerExit(peer,status,[](pid_t p,int* s,int flags){return waitpid(p,s,flags);});
}
} // namespace BbRemote
