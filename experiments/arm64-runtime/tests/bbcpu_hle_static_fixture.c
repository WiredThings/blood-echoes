/* SPDX-License-Identifier: GPL-2.0-or-later
 * Authored x86 bytes in bbcpu_hle_probe.cpp, never game data. The constants
 * hash those actual bytes; the normal runtime performs its full comparison.
 */
#include "../vendor/bbcpu/src/recomp/rc.h"
static const RcApi *api;
static void tail_entry(BbCpu *cpu) {
    cpu->rip=bb_load(api->image_base+0x4100,8);
    api->tail(cpu);
}
static void call_entry(BbCpu *cpu) {
    const uint64_t seventh=bb_load(cpu->r[RSP]+8,8);
    cpu->r[RSP]-=8;bb_store(cpu->r[RSP],8,seventh);
    api->call(cpu,bb_load(api->image_base+0x4100,8),api->image_base+0x14a);
    cpu->r[RSP]+=8; /* LEA in the authored code leaves flags unchanged. */
    cpu->rip=bb_load(cpu->r[RSP],8);cpu->r[RSP]+=8;
}
__attribute__((visibility("default")))
const RcFunction *bb_recomp_init(const RcApi *input,size_t *count) {
    static const RcFunction functions[]={
        {0x100,6,UINT64_C(0x229a6e055970615a),tail_entry,0},
        {0x140,16,UINT64_C(0xaa28a18db60fc973),call_entry,0}
    };
    if(input->version!=BB_RECOMP_API_VERSION){*count=0;return 0;}
    api=input;*count=2;return functions;
}
