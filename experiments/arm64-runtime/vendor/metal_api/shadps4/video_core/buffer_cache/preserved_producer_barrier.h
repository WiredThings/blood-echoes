// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <chrono>
namespace VideoCore {
// Cold producer admission only. try_flush returns with every live/cache/claim
// lease released before pause. A failed deadline must never become arena fallback.
// No GPU completion is inferred: try_flush owns the existing actual wait path.
template<class TryFlush,class Now,class Pause>
bool WaitPreservedProducerBarrier(TryFlush try_flush,Now now,Pause pause,
 std::chrono::steady_clock::duration budget=std::chrono::milliseconds(250)){
 const auto deadline=now()+budget;
 for(;;){
  if(try_flush())return true;
  if(now()>=deadline)return false;
  pause();
 }
}
} // namespace VideoCore
