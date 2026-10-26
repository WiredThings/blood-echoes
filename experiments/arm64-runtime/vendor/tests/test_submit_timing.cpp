#include "bbport_submit_timing.h"
#include "bbport_toggles.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <thread>
#include <vector>

int main(int argc, char**) {
    using namespace BbSubmitTiming;
    if (argc==1) {
        assert(Begin(1)==Capacity && !Get().entries);
        // Actual legacy counter: a later-ended callback arriving first hides earlier work.
        BbStats::NoteGpuInterval(20,25);
        BbStats::NoteGpuInterval(10,21);
        assert(BbStats::gpu_busy_ns.load()==5000000000ULL); // true interval union is 15 s
        puts("disabled recorder allocates nothing; legacy out-of-order undercount reproduced");
        return 0;
    }
    char temp[]="/private/tmp/bb-submit-timing-XXXXXX";
    const auto* dir=mkdtemp(temp);assert(dir);
    const std::filesystem::path root=dir, out=root/"trace.csv", trigger=root/"trigger";
    setenv("BB_MTL_SUBMIT_AUDIT",out.c_str(),1);setenv("BB_MTL_SUBMIT_TRIGGER",trigger.c_str(),1);
    assert(Begin(1)==Capacity && !Get().active);
    { std::ofstream file{trigger};file<<1; }
    std::vector<std::jthread> threads;
    for(unsigned t=0;t<8;++t)threads.emplace_back([t] {
        for(unsigned frame=2;frame<1026;++frame) {
            const unsigned i=Begin(frame);if(i>=Capacity)continue;
            if((i&7)==0)std::this_thread::yield();
            Complete(i,{t,i,frame,t&1,1.+i,1.1+i,1.2+i,1.3+i});
        }
    });
    threads.clear();
    assert(Get().issued==Capacity && Get().completed==Capacity && Begin(1027)==Capacity);
    assert(sizeof(std::array<Entry,Capacity>)<=128*1024);
    std::ifstream file{out};std::string line;unsigned lines=0;
    std::vector<bool> seen(Capacity,false);
    while(std::getline(file,line)) {
        if(lines++) {
            unsigned long long queue,tick;unsigned frame,role;double commit,start,end,callback;
            assert(std::sscanf(line.c_str(),"%llu,%llu,%u,%u,%lf,%lf,%lf,%lf",&queue,&tick,&frame,&role,&commit,&start,&end,&callback)==8);
            assert(tick<Capacity && !seen[tick]);seen[tick]=true;
            assert(commit<start && start<end && end<callback);
        }
    }
    assert(lines==Capacity+1 && !std::filesystem::exists(out.string()+".tmp"));
    std::filesystem::remove_all(root);
    puts("concurrent completion: 2048 unique records, atomic publication and capacity saturation passed");
}
