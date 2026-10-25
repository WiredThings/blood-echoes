// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <atomic>
#include <chrono>
#include <thread>
#include <cstdio>
#include <csignal>
#include <new>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#ifdef BB_AUTHOR_METAL
#import <Metal/Metal.h>
#endif
#include "remote/bb_live_payload_front.h"
#include "remote/bb_live_writeback.h"
#include "remote/bb_vma_table.h"
extern "C" {void WritebackTestSetup();void WritebackTestUnrelatedMap();void WritebackTestSamePhysRemap();void WritebackTestClear();void WritebackTestArm();void WritebackTestClose();int WritebackTestExclusiveProbe();void WritebackTestSplit();}
using namespace BbRemote;using namespace BbResourceLifetime;using namespace std::chrono_literals;
constexpr std::uint64_t Address=0x7000000000,Bytes=128;
struct Shared{LivePayloadShared p;LifetimeJournal journal;std::atomic<unsigned> phase{},mapped{},mutated{},remapped{},writer_waiting{},free_count{};};
void Until(auto f){auto deadline=std::chrono::steady_clock::now()+5s;while(!f()){assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}}
int Child(Shared&s){
 assert(!mprotect(reinterpret_cast<void*>(Address),0x8000,PROT_READ|PROT_WRITE));
 Tracker mirror;auto first=mirror.Allocate(Domain::Heap,Address,0x8000);VmaTable table;table.Map(Address,Address+0x8000,{Address+0x8000,MapDirect,0x33,3,0x10000});
 BbPayload::Origin origin{};auto gone=[] {return getppid()==1;};
 assert(CaptureLivePayload(s.p,Address,Bytes,gone,[&](auto&r){origin={r.owner,r.mapping};return true;}));
 auto lease=[&](auto&o,auto at,auto n,auto body){return WithProtectedLivePayload(s.p,o,at,n,gone,[&](auto&r){return table.TryWithLiveDirect(r.mapping,at,n,[&](auto){return mirror.TryWithLiveHeap(r.owner,at,n,[&](auto){return body(r);});});});};
 std::array<unsigned char,Bytes> data{};data.fill(0x42);
 const void* completed_data=data.data();
#ifdef BB_AUTHOR_METAL
 // No Metal work ran in the parent before fork. This child owns its own device,
 // queue, event and private readback output; no framework fork override is used.
 id<MTLDevice> device=MTLCreateSystemDefaultDevice();assert(device);
 id<MTLCommandQueue> queue=[device newCommandQueue];assert(queue);
 id<MTLBuffer> input=[device newBufferWithBytes:data.data() length:Bytes options:MTLResourceStorageModeShared];
 id<MTLBuffer> output=[device newBufferWithLength:Bytes options:MTLResourceStorageModeShared];
 id<MTLSharedEvent> event=[device newSharedEvent];assert(input&&output&&event);
 id<MTLCommandBuffer> gpu=[queue commandBuffer];assert(gpu);
 [gpu encodeWaitForEvent:event value:1];
 id<MTLBlitCommandEncoder> blit=[gpu blitCommandEncoder];
 [blit copyFromBuffer:input sourceOffset:0 toBuffer:output destinationOffset:0 size:Bytes];[blit endEncoding];[gpu commit];
 completed_data=output.contents;
#endif
 FrozenWriteback batch{};batch.address=Address;batch.bytes=Bytes;batch.tick=3;batch.content_epoch=7;assert(AppendWriteback(batch,Address,completed_data,Bytes));
#ifdef BB_AUTHOR_METAL
 auto result=CommitLiveWriteback(origin,batch,[&](auto){return gpu.status==MTLCommandBufferStatusCompleted;},lease,[](auto){return true;});
#else
 auto result=CommitLiveWriteback(origin,batch,[](auto){return false;},lease,[](auto){return true;});
#endif
 assert(result==LiveWritebackResult::NotReady&&s.p.stage==LiveIdle&&*reinterpret_cast<unsigned char*>(Address)==0x11);
#ifdef BB_AUTHOR_METAL
 assert(gpu.status!=MTLCommandBufferStatusCompleted);event.signaledValue=1;[gpu waitUntilCompleted];
 assert(gpu.status==MTLCommandBufferStatusCompleted&&!gpu.error);assert(!std::memcmp(output.contents,data.data(),Bytes));
#endif
 result=CommitLiveWriteback(origin,batch,[](auto){return true;},lease,[](auto){return false;});assert(result==LiveWritebackResult::Retry&&*reinterpret_cast<unsigned char*>(Address)==0x11);
 auto bad=batch;bad.count=2;bad.spans[1]={Address+64,reinterpret_cast<void*>(Address+1),data.data()+64,64};
 result=CommitLiveWriteback(origin,bad,[](auto){return true;},lease,[](auto){return true;});assert(result==LiveWritebackResult::Retry&&*reinterpret_cast<unsigned char*>(Address)==0x11);
 std::fprintf(stderr,"child before split phase2\n");s.phase=2;Until([&]{return s.mapped.load()!=0;});
 std::fprintf(stderr,"child split ready\n");table.Map(Address,Address+0x4000,{Address+0x4000,MapDirect,0x33,3,0x10000});
 auto right=batch;right.address=Address+0x4000;right.count=0;
 assert(AppendWriteback(right,right.address,completed_data,Bytes));
 assert(CommitLiveWriteback(origin,right,[](auto){return true;},lease,[](auto){return true;})==LiveWritebackResult::Committed);
 assert(*reinterpret_cast<unsigned char*>(right.address)==0x42);std::fprintf(stderr,"child right copied\n");
 for(unsigned i=0;i<17;++i){auto part=batch;part.address=Address+8*i;part.bytes=4;part.count=0;
  assert(AppendWriteback(part,part.address,completed_data,4));
  assert(CommitLiveWriteback(origin,part,[](auto){return true;},lease,[](auto){return true;})==LiveWritebackResult::Committed);
 }
 for(unsigned i=0;i<17;++i){auto* at=reinterpret_cast<unsigned char*>(Address+8*i);assert(at[0]==0x42&&at[3]==0x42&&at[4]==0x11);}

 result=CommitLiveWriteback(origin,batch,[](auto){return true;},lease,[&](auto){std::fprintf(stderr,"child phase3\n");s.phase=3;Until([&]{return s.writer_waiting.load()!=0;});assert(s.free_count==0);return true;});assert(result==LiveWritebackResult::Committed);
 std::fprintf(stderr,"child awaiting mutate\n");Until([&]{return s.mutated.load()!=0;});assert(*reinterpret_cast<unsigned char*>(Address)==0x77);
 result=CommitLiveWriteback(origin,batch,[](auto){return true;},lease,[](auto){return true;});assert(result==LiveWritebackResult::Retry&&*reinterpret_cast<unsigned char*>(Address)==0x77);
 assert(mirror.Release(first));auto second=mirror.Allocate(Domain::Heap,Address,0x8000);(void)second;
 assert(CaptureLivePayload(s.p,Address,Bytes,gone,[&](auto&r){origin={r.owner,r.mapping};return true;}));
 s.phase=4;Until([&]{return s.remapped.load()!=0;});
 result=CommitLiveWriteback(origin,batch,[](auto){return true;},lease,[](auto){return true;});assert(result==LiveWritebackResult::Retry&&*reinterpret_cast<unsigned char*>(Address)==0x77);
 return 0;
}
int main(){
 setenv("BB_LAYER_READ_TRAPS","1",1);WritebackTestSetup();assert(runtime_memory_live_fault_can_delegate());assert(!WritebackTestExclusiveProbe());
 Tracker front;auto old=front.Allocate(Domain::Heap,Address,0x8000);BbPayload::Get().Configure(true);
 auto* mem=mmap(nullptr,sizeof(Shared),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANON,-1,0);assert(mem!=MAP_FAILED);auto&s=*new(mem)Shared;
 auto child=fork();assert(child>=0);if(!child){
#ifdef BB_AUTHOR_METAL
 int code;@autoreleasepool {code=Child(s);}_exit(code);
#else
 _exit(Child(s));
#endif
 }
 std::thread actions([&]{Until([&]{return s.phase>=2;});WritebackTestUnrelatedMap();WritebackTestSplit();s.mapped=1;std::fprintf(stderr,"parent split done\n");Until([&]{return s.phase>=3;});{
  BbPayload::Gate::Mutation mutate{BbPayload::Get()};s.free_count=1;assert(front.Release(old));assert(front.Allocate(Domain::Heap,Address,0x8000).generation==2);WritebackTestClear();std::memset(reinterpret_cast<void*>(Address),0x77,Bytes);WritebackTestArm();}s.mutated=1;
  Until([&]{return s.phase>=4;});WritebackTestSamePhysRemap();s.remapped=1;
 });
 std::thread waiter([&]{std::fprintf(stderr,"parent waiter ready\n");Until([&]{return BbPayload::Get().Read().waiting!=0;});s.writer_waiting=1;});
 int status=0;bool dead=false;auto gone=[&]{if(!dead){auto n=waitpid(child,&status,WNOHANG);assert(n>=0);dead=n==child;}return dead;};
 auto deadline=std::chrono::steady_clock::now()+10s;while(!gone()){ServeLivePayload(s.p,s.journal,front,gone);assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();}
 actions.join();waiter.join();assert(WIFEXITED(status)&&!WEXITSTATUS(status));WritebackTestClear();assert(*reinterpret_cast<unsigned char*>(Address)==0x77);WritebackTestClose();munmap(mem,sizeof(Shared));
#ifdef BB_AUTHOR_METAL
 puts("ACTUAL METAL completion (event-blocked negative, finished blit positive) -> protected same-VA guest commit passed");
#endif
 puts("ACTUAL COMMIT: completed memcpy, pending/epoch/list veto, unrelated VMA stable, free blocked, reuse/same-phys remap rejected; VMA writer delegation refused");
}
