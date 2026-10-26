// SPDX-License-Identifier: GPL-2.0-or-later
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
const char* runtime_file_user_dir(void){return getenv("BB_USER_DIR");}
void bbgpu_before_restart(void){puts("TEST owned backend reaped (mock)");}
// Test-only tri-state. Production definition observes the actual bundle and
// exact runtime session; this mock only covers native C dispatch/fail-closed.
int bb_arm_bundled_restart_admitted(const char*state,const char*nonce){
 const char*e=getenv("BB_TEST_FORMAL");return e?atoi(e):-1;
}
void runtime_restart(void);
extern volatile int runtime_restarting;
int main(void){if(runtime_restarting!=0)return 90;runtime_restart();return 99;}
