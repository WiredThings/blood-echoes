// SPDX-License-Identifier: GPL-2.0-or-later
// Test-only tri-state; exercises the real old C gate, no dlopen/network.
#include <assert.h>
static int decision=-1;
int bb_bundled_net_module_path(const char*a,const char*b){(void)a;(void)b;return decision;}
#include "bb_private_net_module_path.h"
int main(void){
 const char*old=BB_PROTOTYPE_ROOT "/build/net/libbbnet.dylib";
 const char*exe="/private/author.app/Contents/Resources/bbport/bin/bb-probe-metal";
 assert(bb_arm_private_net_module_path(old,exe));
 decision=0;assert(!bb_arm_private_net_module_path(old,exe)); // formal failure never falls back even to old QA build
 assert(!bb_arm_private_net_module_path("/private/wrong-module",exe));
 decision=1;assert(bb_arm_private_net_module_path("/private/admitted-module",exe));
 assert(!bb_arm_private_net_module_path(0,exe));assert(!bb_arm_private_net_module_path(old,0));
 return 0;
}
