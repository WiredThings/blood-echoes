// SPDX-License-Identifier: GPL-2.0-or-later
#ifdef NDEBUG
#error "Lifetime transport tests require assertions"
#endif
#include <cassert>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <cstring>
#include <new>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include "remote/bb_control.h"
#include "remote/bb_lifetime_reclaim.h"
using namespace BbRemote;
using namespace BbResourceLifetime;
using namespace std::chrono_literals;
static LifetimeJournal* observed;
static std::mutex producer;
static void Observe(EventKind kind,Tag t) {
    std::scoped_lock lock{producer};
    observed->Push({t.address,t.size,t.generation,unsigned(kind),unsigned(t.domain)});
}
static void Mirror(LifetimeJournal& journal,Tracker& tracker) {
    LifetimeEvent event;
    while(journal.Pop(event)) {
        Tag tag{event.address,event.size,event.generation,Domain(event.domain)};
        if(event.kind==unsigned(EventKind::Allocate)) {
            const auto actual=tracker.Allocate(tag.domain,tag.address,tag.size);
            assert(actual.generation==tag.generation);
        } else if(event.kind==unsigned(EventKind::Release))tracker.Release(tag);
    }
}
static int CrossPeer(int fd) {
    void* at=mmap(nullptr,sizeof(LifetimeShared),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    assert(at!=MAP_FAILED);auto& shared=*static_cast<LifetimeShared*>(at);
    Tracker backend;Mirror(shared.journal,backend);
    const Tag tag{0x800000,4096,1,Domain::Heap};
    assert(backend.Status(tag)==State::Released);
    assert(ReclaimWithLifetimeLease(shared,backend,tag,tag.address,tag.size,0,[]{return 4096;}));
    assert(backend.Read().reclaimed_bytes==4096);
    munmap(at,sizeof(LifetimeShared));return 0;
}
static int CrossParent(const char* executable) {
    char path[]="/private/tmp/bb-lifetime-XXXXXX";
    const int fd=mkstemp(path);assert(fd>=0);unlink(path);
    assert(ftruncate(fd,sizeof(LifetimeShared))==0);
    void* at=mmap(nullptr,sizeof(LifetimeShared),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);assert(at!=MAP_FAILED);
    auto& shared=*new(at)LifetimeShared{};observed=&shared.journal;
    Tracker frontend;frontend.SetObserver(Observe);
    const auto tag=frontend.Allocate(Domain::Heap,0x800000,4096);assert(frontend.Release(tag));
    const auto child=fork();assert(child>=0);
    if(!child) {
        const std::string number=std::to_string(fd);
        execl(executable,executable,"--peer-fd",number.c_str(),nullptr);_exit(127);
    }
    const auto start=std::chrono::steady_clock::now();int status=0;
    for(;;) {
        ServeLifetimeLease(shared,frontend,true);
        const auto result=waitpid(child,&status,WNOHANG);
        assert(result>=0);if(result==child)break;
        if(std::chrono::steady_clock::now()-start>10s) {kill(child,SIGTERM);waitpid(child,&status,0);assert(false);}
        std::this_thread::sleep_for(100us);
    }
    assert(WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(frontend.Read().reclaimed_bytes==4096);
    munmap(at,sizeof(LifetimeShared));close(fd);
    std::puts("Cross-architecture process-shared lifetime journal and authoritative reclaim lease passed");return 0;
}
int main(int argc,char** argv) {
    if(argc==3 && !std::strcmp(argv[1],"--peer-fd"))return CrossPeer(std::atoi(argv[2]));
    if(argc==3 && !std::strcmp(argv[1],"--cross-peer"))return CrossParent(argv[2]);

    auto shared=std::make_unique<LifetimeShared>();
    assert(sizeof(LifetimeEvent)==32 && sizeof(ControlBlock)<=ControlBytes);
    // Saturation cannot overwrite unread evidence and permanently vetoes reclaim.
    for(unsigned i=0;i<LifetimeCapacity;++i)assert(shared->journal.Push({i,16,i,1,1}));
    assert(!shared->journal.Push({9999,16,9999,1,1}));
    assert(shared->journal.lost.load()==1);
    LifetimeEvent event;
    for(unsigned i=0;i<LifetimeCapacity;++i) {assert(shared->journal.Pop(event));assert(event.address==i);}
    assert(!shared->journal.Pop(event));
    shared=std::make_unique<LifetimeShared>();
    observed=&shared->journal;Tracker front(32768),back(32768);front.SetObserver(Observe);
    std::vector<std::thread> workers;
    for(unsigned n=0;n<4;++n)workers.emplace_back([&,n] {
        for(unsigned i=0;i<400;++i) {
            auto tag=front.Allocate(Domain::Heap,0x100000+n*0x10000+i*64,64);
            assert(front.Release(tag));
        }
    });
    for(auto& worker:workers)worker.join();Mirror(shared->journal,back);
    assert(front.Read().generation==1600 && back.Read().generation==1600);
    assert(back.Read().domains[1].releases==1600);
    // Valid lease must retain the authoritative allocation lock throughout
    // metadata removal. An allocation of the same address cannot race through.
    const auto tag=front.Allocate(Domain::Heap,0x400000,4096);assert(front.Release(tag));
    Mirror(shared->journal,back);
    std::atomic<bool> stop=false,callback=false,started=false,allocated=false;
    std::thread server([&] { while(!stop.load()) {if(!ServeLifetimeLease(*shared,front,true))std::this_thread::yield();} });
    std::thread reuser([&] { while(!callback.load())std::this_thread::yield();started=true;front.Allocate(Domain::Heap,tag.address,tag.size);allocated=true; });
    assert(ReclaimWithLifetimeLease(*shared,back,tag,tag.address,tag.size,0,[&] {
        callback=true;while(!started.load())std::this_thread::yield();
        std::this_thread::sleep_for(15ms);assert(!allocated.load());return 4096;
    }));
    reuser.join();assert(allocated.load());
    // The backend mirror deliberately lags: authoritative reuse still rejects it.
    bool called=false;
    assert(!ReclaimWithLifetimeLease(*shared,back,tag,tag.address,tag.size,0,[&] {called=true;return 1;}));
    assert(!called);
    Mirror(shared->journal,back);
    assert(back.Status(tag)==State::Reused);
    // Unknown generation, partial live overlap and transport loss all fail closed.
    const auto live=front.Find(Domain::Heap,tag.address);
    auto wrong=live;wrong.generation+=99;
    assert(!ReclaimWithLifetimeLease(*shared,back,wrong,wrong.address,wrong.size,0,[&] {called=true;return 1;}));
    shared->journal.lost=1;
    assert(!ReclaimWithLifetimeLease(*shared,back,tag,tag.address,tag.size,0,[&] {called=true;return 1;}));
    stop=true;server.join();assert(!called);
    // Cancellation is legal only before claim/grant, never after permission.
    shared->lease.stage=LeaseRequest;assert(shared->lease.CancelUnclaimed());
    shared->lease.stage=LeaseGranted;assert(!shared->lease.CancelUnclaimed());
    shared->lease.stage=LeaseIdle;shared->journal.lost=0;
    const auto before=std::chrono::steady_clock::now();
    assert(!ReclaimWithLifetimeLease(*shared,back,tag,tag.address,tag.size,0,[&] {called=true;return 1;}));
    assert(std::chrono::steady_clock::now()-before>=250ms && shared->lease.stage==LeaseIdle);
    assert(!called);
    std::printf("Lifetime transport: ordering, overflow, mirror, lease, allocation race, stale reuse, cancellation passed; control=%zu lifetime=%zu event=%zu lease=%zu\n",sizeof(ControlBlock),sizeof(LifetimeShared),sizeof(LifetimeEvent),sizeof(LifetimeLease));
}
