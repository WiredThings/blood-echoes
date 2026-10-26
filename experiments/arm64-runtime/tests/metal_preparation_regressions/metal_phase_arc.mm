#include "video_core/renderer_metal/mtl_phase_counters.h"
__attribute__((objc_root_class)) @interface BBAPIDevice
- (id)newLibrary;
@end
extern "C" void observe(id);
extern "C" id BaselineAssign(BBAPIDevice*d){id value=[d newLibrary];observe(value);return value;}
extern "C" id MeasuredAssign(BBAPIDevice*d){BbMetalPhase::Call call{BbMetalPhase::Kind::SourceLibrary};id value=[d newLibrary];call.Finish(bool(value));observe(value);return value;}
extern "C" id BaselineReturn(BBAPIDevice*d){return[d newLibrary];}
extern "C" id MeasuredReturn(BBAPIDevice*d){BbMetalPhase::Call call{BbMetalPhase::Kind::SourceLibrary};id value=[d newLibrary];call.Finish(bool(value));return value;}
extern "C" id BaselineConditional(BBAPIDevice*d,bool ready){id value=ready?[d newLibrary]:nullptr;observe(value);return value;}
extern "C" id MeasuredConditional(BBAPIDevice*d,bool ready){id value=nullptr;if(ready){BbMetalPhase::Call call{BbMetalPhase::Kind::SourceLibrary};value=[d newLibrary];call.Finish(bool(value));}observe(value);return value;}
