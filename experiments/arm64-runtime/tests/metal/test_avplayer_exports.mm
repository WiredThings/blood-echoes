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
 // Bad/unknown scoped NID must not resolve through an unrelated function.
 assert(!bbgpu_resolve("not-a-real-AV-nid#m#n"));
 puts("PASS 16 real host exports and unknown-NID rejection; no guest ABI/callback claim");
}
