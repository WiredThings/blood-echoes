#include <cstdio>
#include "video_core/renderer_metal/mtl_host_pso_recipe.h"
int main(){std::printf("{\"size\":%zu,\"alignment\":%zu}\n",sizeof(Metal::HostPso::ArtifactOutcome),alignof(Metal::HostPso::ArtifactOutcome));}
