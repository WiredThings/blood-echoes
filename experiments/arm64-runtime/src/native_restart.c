#include "runtime_exit_codes.h"
// SPDX-License-Identifier: GPL-2.0-or-later
#define _DARWIN_C_SOURCE
#define BB_LOW_HEAP_IMPL
#include "runtime.h"
#include "runtime_restart_protocol.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdatomic.h>
#ifndef O_NOFOLLOW
#error "restart request requires O_NOFOLLOW"
#endif
extern void bbgpu_before_restart(void);
volatile int runtime_restarting;
#if defined(__APPLE__)
extern int bb_arm_bundled_restart_admitted(const char*,const char*) __attribute__((weak_import));
#else
extern int bb_arm_bundled_restart_admitted(const char*,const char*) __attribute__((weak));
#endif
void runtime_restart(void) {
 const char *state=runtime_file_user_dir(),*request=getenv("BB_ARM_RESTART_REQUEST"),*nonce=getenv("BB_ARM_RESTART_NONCE");
 const char *loading=getenv("BB_QUICKLOAD_PENDING");
 char expected[4096],root[4096];
 snprintf(root,sizeof(root),"%s/state/",BB_PROTOTYPE_ROOT);
 const int formal=bb_arm_bundled_restart_admitted?bb_arm_bundled_restart_admitted(state,nonce):-1;
 if(formal==0){fputs("ARM RESTART refused: bundled session changed\n",stderr);_Exit(BB_ARM_EXIT_RESTART_REJECTED);}
 if(!nonce||strlen(nonce)!=32||strspn(nonce,"0123456789abcdef")!=32||(formal<0&&(!state||strncmp(state,root,strlen(root))||strchr(state+strlen(root),'/')||
    ((!getenv("BB_ARM_LAUNCHER_COMPAT")||strcmp(getenv("BB_ARM_LAUNCHER_COMPAT"),"1"))&&
     (!request||snprintf(expected,sizeof(expected),"%s/.arm-restart-request",state)>=(int)sizeof(expected)||strcmp(expected,request)))))) {
  fputs("ARM RESTART refused: missing or non-private supervisor request\n",stderr);_Exit(BB_ARM_EXIT_RESTART_REJECTED);
 }
 const char*compat=getenv("BB_ARM_LAUNCHER_COMPAT");
 if(formal==1||(compat&&!strcmp(compat,"1"))){
  const int status=runtime_supervised_restart_status();
  if(!status){fputs("ARM LAUNCHER restart refused: original supervisor missing\n",stderr);_Exit(BB_ARM_EXIT_RESTART_REJECTED);}
  runtime_restarting=1;atomic_thread_fence(memory_order_seq_cst);bbgpu_before_restart();
  puts("ARM LAUNCHER owned backend reaped; returning original restart status");fflush(NULL);_Exit(status);
 }
 int fd=open(request,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
 char payload[128];int length=snprintf(payload,sizeof(payload),"ARMRESTART1\n%s\n%d\n",nonce,loading&&!strcmp(loading,"1"));
 if(fd<0||write(fd,payload,length)!=length||fsync(fd)){
  if(fd>=0)close(fd);fputs("ARM RESTART refused: request could not be committed\n",stderr);_Exit(BB_ARM_EXIT_RESTART_REJECTED);
 }
 close(fd);runtime_restarting=1;atomic_thread_fence(memory_order_seq_cst);
 bbgpu_before_restart();
 puts("ARM RESTART committed; owned backend reaped; supervisor must relaunch offline");fflush(NULL);
 _Exit(BB_ARM_EXIT_LEGACY_RESTART_REQUEST); // Deliberate supervisor request, not a game error or success sentinel.
}
