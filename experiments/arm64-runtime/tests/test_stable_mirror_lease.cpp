// SPDX-License-Identifier: GPL-2.0-or-later
// Authored table identities only: no guest payload, GPU, production cache or window.
#include "bblayer_gpu_memory.h"
#include <cassert>
#include <chrono>
#include <future>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <thread>
#include <type_traits>
using namespace std::chrono_literals;
using BbLayer::GpuMemory;
static unsigned controls;
#define CHECK(x) do {assert(x);++controls;} while(0)
static_assert(std::is_trivially_copyable_v<BbLayer::StableMirrorSnapshot>);
int main(){
 auto&m=GpuMemory::Get();m.SetBlockShift(16);
 constexpr std::uint64_t base=0x100000,size=0x20000;
 int a{},b{};unsigned callbacks=0;
 auto reset=[&](){m.RemoveMirror(base);m.MarkInvalid(base>>16,(base+size)>>16);m.AddMirror(base,size,11,22,&a);m.MarkValid(base>>16,(base+size)>>16);};
 reset();auto snapshot=m.SnapshotStableMirror(base+64,4096);CHECK(snapshot.has_value());
 CHECK(m.WithStableMirror(*snapshot,[&](const auto&s){++callbacks;return s.source->owner==&a&&s.source->handle==11&&s.source->device_address==22&&s.va==base+64&&s.offset==64&&s.size==4096;}));
 CHECK(callbacks==1);
 CHECK(!m.SnapshotStableMirror(base,0));CHECK(!m.SnapshotStableMirror(base-1,2));CHECK(!m.SnapshotStableMirror(base+size-1,2));
 CHECK(!m.SnapshotStableMirror(std::numeric_limits<std::uint64_t>::max(),2));
 CHECK(!m.WithStableMirror(*snapshot,{}));
 CHECK(!m.WithStableMirror(*snapshot,[&](const auto&){++callbacks;return false;}));CHECK(callbacks==2);
 // Ordinary exceptions leave the read lease through C++ unwinding.
 bool threw=false;try{m.WithStableMirror(*snapshot,[](const auto&)->bool{throw std::runtime_error("authored");});}catch(const std::runtime_error&){threw=true;}CHECK(threw);
 m.MarkInvalid(base>>16,(base>>16)+1);
 CHECK(!m.WithStableMirror(*snapshot,[](const auto&){return true;}));CHECK(!m.SnapshotStableMirror(base,16));
 CHECK(!m.SnapshotStableMirror(base,size));CHECK(m.SnapshotStableMirror(base+0x10000,16).has_value());
 m.MarkValid(base>>16,(base+size)>>16);CHECK(!m.WithStableMirror(*snapshot,[](const auto&){return true;}));
 auto old=m.SnapshotStableMirror(base,size);CHECK(old.has_value());m.RemoveMirror(base);m.AddMirror(base,size,11,22,&a);
 CHECK(!m.WithStableMirror(*old,[](const auto&){return true;})); // exact pointer/handle ABA still fails
 auto fresh=m.SnapshotStableMirror(base,size);CHECK(fresh.has_value());
 m.AddMirror(base,size,11,22,&b);CHECK(!m.WithStableMirror(*fresh,[](const auto&){return true;}));
 fresh=m.SnapshotStableMirror(base,size);CHECK(fresh.has_value());
 for(unsigned which=0;which<10;++which){
  auto bad=*fresh;
  switch(which){case 0:bad.source.owner=&a;break;case 1:++bad.source.handle;break;case 2:++bad.source.device_address;break;case 3:++bad.source.base;break;case 4:++bad.source.size;break;case 5:bad.source.kind=BbLayer::MemorySource::Kind::Guest;break;case 6:bad.size=0;break;case 7:bad.va=base+size;break;case 8:++bad.source_version;break;case 9:++bad.valid_version;break;}
  CHECK(!m.WithStableMirror(bad,[](const auto&){return true;}));
 }
 // The snapshot's source kind itself participates in validation (not merely the current kind).
 reset();auto unchanged=m.SnapshotStableMirror(base,size);CHECK(unchanged.has_value());
 constexpr auto other=base+size;m.AddMirror(other,size,33,44,&b);m.MarkValid(other>>16,(other+size)>>16);
 CHECK(!m.WithStableMirror(*unchanged,[](const auto&){return true;}));CHECK(!m.SnapshotStableMirror(base,size+16));
 m.RemoveMirror(other);m.MarkInvalid(other>>16,(other+size)>>16);
 m.AddMirror(base,size,0,22,&a);CHECK(!m.SnapshotStableMirror(base,16));m.AddMirror(base,size,11,22,nullptr);CHECK(!m.SnapshotStableMirror(base,16));reset();
 // A real public RemoveMirror cannot complete while WithStableMirror holds its read lease.
 auto held=m.SnapshotStableMirror(base,size);CHECK(held.has_value());std::promise<void>entered,leave,attempt;
 auto release=leave.get_future().share();auto reader=std::async(std::launch::async,[&]{return m.WithStableMirror(*held,[&](const auto&s){entered.set_value();release.wait();return s.source->owner==&a;});});
 entered.get_future().wait();auto writer=std::async(std::launch::async,[&]{attempt.set_value();m.RemoveMirror(base);});attempt.get_future().wait();
 auto during=writer.wait_for(50ms);leave.set_value();CHECK(reader.get());writer.get();CHECK(during==std::future_status::timeout);CHECK(!m.WithStableMirror(*held,[](const auto&){return true;}));CHECK(!m.SnapshotStableMirror(base,16));
 // Concurrent source mutation + table reads: only complete, matching identities may invoke callback.
 reset();std::atomic<bool>done=false;std::atomic<unsigned>accepted=0;
 std::thread mutate([&]{for(unsigned n=0;n<500;++n){m.RemoveMirror(base);m.AddMirror(base,size,11+(n&1),22,&a);m.MarkInvalid(base>>16,(base+size)>>16);m.MarkValid(base>>16,(base+size)>>16);}done=true;});
 do {if(auto s=m.SnapshotStableMirror(base+64,4096))m.WithStableMirror(*s,[&](const auto&v){assert(v.source->owner==s->source.owner&&v.source->handle==s->source.handle&&v.source->base==s->source.base&&v.source->size==s->source.size&&v.source->device_address==s->source.device_address);++accepted;return true;});}while(!done.load());
 mutate.join();CHECK(m.SnapshotStableMirror(base,size).has_value());m.RemoveMirror(base);m.MarkInvalid(base>>16,(base+size)>>16);CHECK(!m.SnapshotStableMirror(base,16));CHECK(m.ValidBytes()==0);
 std::cout<<"PASS stable mirror controls="<<controls<<" concurrent_accepted="<<accepted.load()<<"; table identity only, not payload or native lifetime\n";
}
