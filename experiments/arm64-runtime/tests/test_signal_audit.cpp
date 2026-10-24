// SPDX-License-Identifier: MIT
#include "bb_signal_audit.h"
#include <cassert>
#include <signal.h>
#include <thread>
#include <vector>
#include <string>
#include <set>
static_assert(BbSignalAudit::Valid(1,1,1,1)); // zero wall is valid at clock resolution
static_assert(BbSignalAudit::Valid(1,1,1,1000001));
static_assert(!BbSignalAudit::Valid(1,1,1,1000002));
static_assert(!BbSignalAudit::Valid(0,1,2,2));
static_assert(!BbSignalAudit::Valid(2,1,1,2));
static_assert(!BbSignalAudit::Valid(1,2,2,1));
static int sink=-1;static std::atomic<unsigned>seen{};
static void handler(int){
 BbSignalAudit::Scope all{BbAudit::UnalignedSignal,42};
 {BbSignalAudit::Scope repair{BbAudit::UnalignedRepair,42};seen.fetch_add(1,std::memory_order_relaxed);}
 {BbSignalAudit::Scope log{BbAudit::UnalignedLog,42};const char byte='x';assert(write(sink,&byte,1)==1);}
 {BbSignalAudit::Scope publish{BbAudit::UnalignedPublish,42};std::atomic_signal_fence(std::memory_order_seq_cst);}
}
int main(int argc,char**){
 const bool disabled=argc>1;std::string path="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/signal-audit-unit-"+std::to_string(getpid());
 setenv("BB_STUTTER_AUDIT",path.c_str(),1);setenv("BB_UNALIGNED_AUDIT",disabled?"0":"1",1);
 const std::string control=path+"-gate";int fd=open(control.c_str(),O_RDWR|O_CREAT|O_EXCL,0600);assert(fd>=0&&ftruncate(fd,4096)==0);
 auto* gate=static_cast<std::atomic<uint32_t>*>(mmap(nullptr,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0));close(fd);assert(gate!=MAP_FAILED);new(gate)std::atomic<uint32_t>{0};setenv("BB_STUTTER_GATE",control.c_str(),1);
 sink=open("/dev/null",O_WRONLY);assert(sink>=0);struct sigaction action{};action.sa_handler=handler;sigemptyset(&action.sa_mask);assert(!sigaction(SIGUSR1,&action,nullptr));
 // Before preparation no trace initialization is attempted by a signal.
 assert(!raise(SIGUSR1));assert(!BbSignalAudit::requested);BbSignalAudit::PrepareThread();
 assert(!raise(SIGUSR1)); // closed gate, still no rows
 if(disabled){assert(!BbSignalAudit::requested);assert(access((path+"-"+std::to_string(getpid())+".bin").c_str(),F_OK)==-1);puts("signal audit disabled: no trace file or signal-side initialization");}
 else{
  auto*b=BbAudit::Get().data;assert(b&&b->h.issued==0);gate->store(1);errno=E2BIG;assert(!raise(SIGUSR1));assert(errno==E2BIG);assert(b->h.issued==4);
  std::vector<std::thread>threads;for(unsigned i=0;i<4;i++)threads.emplace_back([]{BbSignalAudit::PrepareThread();for(unsigned j=0;j<128;j++)assert(!raise(SIGUSR1));});for(auto&t:threads)t.join();
  assert(b->h.issued==2052&&!b->h.dropped);std::set<uint64_t>tids;unsigned kinds[4]{};
  for(unsigned i=0;i<2052;i++){auto&r=b->rows[i];assert(r.sequence==i+1&&!r.busy&&r.arg==42&&r.tid&&r.kind>=73&&r.kind<=76&&r.start&&r.cpu<=r.wall+1000000);tids.insert(r.tid);++kinds[r.kind-73];}
  assert(tids.size()==5);for(auto n:kinds)assert(n==513);gate->store(0);assert(!raise(SIGUSR1));assert(b->h.issued==2052);
  printf("signal audit: 513 real signals, 5 prepared threads, 2052 complete rows; closed gate and errno preserved\n");unlink((path+"-"+std::to_string(getpid())+".bin").c_str());
 }
 close(sink);unlink(control.c_str());return 0;
}
