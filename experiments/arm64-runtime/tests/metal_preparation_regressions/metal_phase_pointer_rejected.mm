#include "video_core/renderer_metal/mtl_phase_counters.h"
void* invalid(){return BbMetalPhase::Invoke(BbMetalPhase::Kind::SourceLibrary,[]{return static_cast<void*>(nullptr);});}
