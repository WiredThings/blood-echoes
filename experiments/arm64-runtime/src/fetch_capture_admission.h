// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
namespace BbFetchCapture {
// One retry bit belongs to the existing Program. It is not a seen map. A skipped
// newly selected target arms the next observation; alternating A/B cannot starve B.
inline bool ObserveWarm(bool remaining,bool retry,bool last_valid,bool last_attempted)noexcept {
 return remaining&&(retry||!last_valid||!last_attempted);
}
inline bool RetryWarm(bool retry,bool prior_attempted,bool target_attempted,bool scope_observed)noexcept {
 if(!target_attempted)return true;
 if(scope_observed&&!prior_attempted)return false;
 return retry;
}
enum class Refusal : unsigned {NoReference=0,ReferenceBudget=1,Count=2};
}
