// SPDX-License-Identifier: MIT
// Bounded metadata observation only; never changes dirty state or follows guest pointers.
#pragma once
#include "bb_layout_audit.h"
#include "bb_stutter_audit.h"
#include <limits>
#include <unistd.h>
namespace BbRawWriteAudit {
constexpr unsigned MaxProbes = 256, MaxRecords = 64, MaxImages = 4;
constexpr uint64_t MaxBytes = 16ull << 20;
struct State {
 std::atomic<unsigned> probes{}, records{}, skipped{}, limited{};
};
inline State& Get(){static State s;return s;}
inline unsigned Claim(std::atomic<unsigned>& counter,unsigned limit){
 auto value=counter.load(std::memory_order_relaxed);
 while(value<limit)if(counter.compare_exchange_weak(value,value+1,std::memory_order_relaxed))return value+1;
 return 0;
}
inline bool Enabled(){static const bool enabled=[] {
 const auto* value=std::getenv("BB_ARM_RAW_WRITE_AUDIT");
 const bool yes=value&&value[0]=='1'&&value[1]=='\0';
 if(yes)std::fputs("BB_RAW_WRITE_AUDIT_READY schema=1 probes=256 records=64 images=4 bytes=16777216\n",stderr);
 return yes;
}();return enabled;}
inline unsigned Begin(){
 if(!Enabled())return 0;
 static const bool gated=[] {const auto* value=std::getenv("BB_ARM_RAW_WRITE_AUDIT_GATE");return value&&value[0]=='1'&&value[1]=='\0';}();
 if(gated&&!BbAudit::Enabled())return 0;
 return Claim(Get().probes,MaxProbes);
}
inline bool Range(uint64_t address,uint64_t bytes){
 if(bytes>MaxBytes||bytes>std::numeric_limits<uint64_t>::max()-address){Get().skipped.fetch_add(1,std::memory_order_relaxed);return false;}
 return true;
}
inline void End(unsigned probe,unsigned images){
 if(images==MaxImages)Get().limited.fetch_add(1,std::memory_order_relaxed);
 if(probe==MaxProbes)std::fprintf(stderr,"BB_RAW_WRITE_AUDIT_SUMMARY probes=%u records=%u skipped=%u limited=%u\n",probe,Get().records.load(),Get().skipped.load(),Get().limited.load());
}
// A distinct named callable avoids reusing the original InvalidateMemoryFromGPU
// lambda specialization when this diagnostic TU is linked with frozen LTO objects.
// The pre-check also bounds observations even if a walker visits again after stop.
template<class Callback> struct BoundedVisitor {
 unsigned& inspected;
 Callback callback;
 template<class Id,class Image> bool operator()(Id id,Image& image) const {
  if(inspected>=MaxImages)return true;
  ++inspected;callback(id,image);
  return inspected>=MaxImages;
 }
};
template<class Image> void Observe(unsigned probe,uint64_t address,uint64_t bytes,
 const Image& image,uint64_t registry,uint64_t allocation,uint64_t tick){
 const bool missed=address!=image.info.guest_address;
 if(bytes&&!missed)return;
 const auto record=Claim(Get().records,MaxRecords);if(!record)return;
 const auto roles=unsigned(image.usage.texture)|(unsigned(image.usage.storage)<<1)|
   (unsigned(image.usage.render_target)<<2)|(unsigned(image.usage.depth_target)<<3)|
   (unsigned(image.scene_proxy)<<4);
 BbLayoutAudit::Text text;
 text.Add("BB_RAW_WRITE_AUDIT {\"schema\":1,\"event\":%u,\"probe\":%u,\"mono_ns\":%llu,\"pid\":%u,\"write_address\":%llu,\"write_bytes\":%llu,\"would_miss\":%u,\"zero_write\":%u,\"uid\":%llu,\"image_address\":%llu,\"image_bytes\":%llu,\"flags\":%u,\"safe_to_download\":%u,\"roles\":%u,\"registry\":%llu,\"allocation\":%llu,\"tick\":%llu,\"image_write_tick\":%llu,\"image\":",
 record,probe,(unsigned long long)BbAudit::Now(),unsigned(getpid()),
 (unsigned long long)address,(unsigned long long)bytes,unsigned(missed),unsigned(bytes==0),
 (unsigned long long)image.image_uid,(unsigned long long)image.info.guest_address,
 (unsigned long long)image.info.guest_size,unsigned(image.flags),unsigned(image.SafeToDownload()),roles,
 (unsigned long long)registry,(unsigned long long)allocation,(unsigned long long)tick,
 (unsigned long long)image.gpu_write_tick);
 BbLayoutAudit::Describe(text,image.info);text.Add("}\n");
 if(!text.failed)std::fwrite(text.bytes,1,text.at,stderr);
}
}
