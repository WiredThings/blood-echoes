// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "bbport_resource_lifetime.h"
#include "../../bb_runtime/src/runtime_live_mapping.h"
namespace BbPayload {
// Captured before first native construction and validated again afterwards
// under real frontend allocator + mapping leases. No cold work inside a lease.
// Native-allocation identity only; it does not certify image contents or upload.
struct Origin {
 BbResourceLifetime::Tag owner{};
 RuntimeLiveMapping mapping{};
 explicit operator bool() const noexcept {return bool(owner)&&owner.domain==BbResourceLifetime::Domain::Heap&&mapping.end>mapping.begin;}
};
inline bool SameMapping(const RuntimeLiveMapping& a,const RuntimeLiveMapping& b) noexcept {
 return a.begin==b.begin&&a.end==b.end&&a.physical==b.physical&&
        a.generation==b.generation&&a.prot==b.prot&&a.type==b.type;
}
// Surviving split slices retain original insertion identity and physical offset.
inline bool SameMappingForRange(const RuntimeLiveMapping&a,const RuntimeLiveMapping&b,
                               std::uint64_t at,std::uint64_t bytes) noexcept {
 return runtime_live_mapping_same_slice(&a,&b,at,bytes);
}
}
