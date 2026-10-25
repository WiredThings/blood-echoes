#include <cassert>
#include <thread>
#include <atomic>
#include "video_core/buffer_cache/preserved_writeback.h"
using VideoCore::PreservedWritebackLedger;
static auto Snapshot(std::uint64_t at,std::uint64_t tick=2){
 PreservedWritebackLedger::Snapshot s{};s.address=at;s.size=4096;s.tick=tick;s.buffer_identity=1;
 s.origin={{at,4096,1,BbResourceLifetime::Domain::Heap},{at,at+4096,0x10000,7,0x33,3}};
 s.mirror={{BbLayer::MemorySource::Kind::Mirror,at,4096,0x1234,0x20000,reinterpret_cast<void*>(0x5555)},at,4096,1,1};return s;
}
int main(){
 PreservedWritebackLedger l;auto scope=l.TryAcquire();assert(scope);std::atomic<bool> denied{};std::thread competitor([&]{denied=!l.TryAcquire();});competitor.join();assert(denied);auto s=Snapshot(0x7000000000);auto t=l.Reserve(s);assert(t&&l.Count()==1);assert(l.Overlaps(s.address,s.size));assert(!l.ClaimFirst(s.address,s.size));assert(l.Count()==1);l.Cancel(*t);assert(!l.Count());
 t=l.Reserve(s);l.CommitRecorded(*t);assert(l.Overlaps(s.address+10,20));auto claim=l.ClaimFirst(s.address+10,20);assert(claim&&l.Claimed(*claim).origin.owner==s.origin.owner);assert(!l.ClaimFirst(s.address,s.size));l.Retry(*claim);
 auto newer=s;newer.tick=3;auto conversion=l.Reserve(newer);assert(conversion&&conversion->conversion);l.Cancel(*conversion);claim=l.ClaimFirst(s.address,s.size);assert(l.Claimed(*claim).tick==2);l.Retry(*claim);
 conversion=l.Reserve(newer);l.CommitRecorded(*conversion);claim=l.ClaimFirst(s.address,s.size);assert(l.Claimed(*claim).tick==3);l.Complete(*claim);assert(!l.Count());
 for(std::size_t i=0;i<l.Capacity;++i){auto v=Snapshot(s.address+i*8192);auto r=l.Reserve(v);assert(r);l.CommitRecorded(*r);}assert(l.Count()==l.Capacity);assert(!l.Reserve(Snapshot(s.address+l.Capacity*8192)));l.ForgetConfirmed(s.address,4096);assert(l.Count()==l.Capacity-1);
 auto invalid=s;invalid.origin={};assert(!l.Reserve(invalid));invalid=s;invalid.size=0;assert(!l.Reserve(invalid));
 auto old=Snapshot(s.address+8192);old.origin.owner.generation=88;assert(!l.Reserve(old));
}
