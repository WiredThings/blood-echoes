// SPDX-License-Identifier: GPL-2.0-or-later
// Original host resolver control, deliberately no guest callbacks or graphics initialization.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include "test_gpu_runtime_stubs.cpp"
extern "C" void bbgpu_register_kernel();
extern "C" uintptr_t bbgpu_resolve(const char*);
int main(){
 bbgpu_register_kernel();
 struct Export{const char*nid;const char*name;};
 const Export exports[]={
 {"aS66RI0gGgo#m#n","sceAvPlayerInit"},{"HD1YKVU26-M#m#n","sceAvPlayerPostInit"},
 {"KMcEa+rHsIo#m#n","sceAvPlayerAddSource"},{"OVths0xGfho#m#n","sceAvPlayerSetLooping"},
 {"NkJwDzKmIlw#m#n","sceAvPlayerClose"},{"ET4Gr-Uu07s#m#n","sceAvPlayerStart"},
 {"ZC17w3vB5Lo#m#n","sceAvPlayerStop"},{"UbQoYawOsfY#m#n","sceAvPlayerIsActive"},
 {"9y5v+fGN4Wk#m#n","sceAvPlayerPause"},{"w5moABNwnRY#m#n","sceAvPlayerResume"},
 {"JdksQu8pNdQ#m#n","sceAvPlayerGetVideoDataEx"},{"wwM99gjFf1Y#m#n","sceAvPlayerCurrentTime"},
 {"hdTyRzCXQeQ#m#n","sceAvPlayerStreamCount"},{"d8FcbzfAdQw#m#n","sceAvPlayerGetStreamInfo"},
 {"ODJK2sn9w4A#m#n","sceAvPlayerEnableStream"},{"Wnp1OVcrZgk#m#n","sceAvPlayerGetAudioData"}};
 for(const auto&e:exports){assert(bbgpu_resolve(e.nid));std::printf("FOUND %s\n",e.name);}
 // These are separately audited unresolved imports, not success stubs.
 const Export unresolved[]={
 {"3k6kx-zOOSQ#p#J","sceKernelMlock"},{"f7KBOafysXo#p#J","sceKernelGetModuleInfoFromAddr"},
 {"Fjc4-n1+y2g#p#J","__elf_phdr_match_addr"},{"kbw4UHHSYy0#p#J","__pthread_cxa_finalize"},
 {"tZY4+SZNFhA#p#J","msync"},{"crb5j7mkk1c#p#J","_is_signal_return"},
 {"mo0bFmWppIw#p#J","sigreturn"},{"OjWstbIRPUo#p#J","__Ux86_64_setcontext"},
 {"djxxOmW6-aw#p#J","__progname"}};
 for(const auto&e:unresolved){assert(!bbgpu_resolve(e.nid));std::printf("UNRESOLVED_HOST %s\n",e.name);}
 // Bad/unknown scoped NID must not resolve through an unrelated function.
 assert(!bbgpu_resolve("not-a-real-AV-nid#m#n"));
 puts("PASS 16 real AV exports, 9 unresolved host imports and unknown-NID rejection; no guest ABI/callback claim");
}
