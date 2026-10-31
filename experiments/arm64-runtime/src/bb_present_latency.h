// SPDX-License-Identifier: MIT
#pragma once
// Opt-in host timings, not GPU execution times. No payloads or resident ring.
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
namespace BbPresentLatency {
inline constexpr uint64_t SlowNs=50000000,MaxRecords=512;
inline bool Enabled(){static const bool value=[] {const char* p=std::getenv("BB_PRESENT_LATENCY_DIAGNOSTIC");return p&&p[0]=='1'&&p[1]==0;}();return value;}
inline uint64_t Now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
inline int64_t Cpu(){timespec t{};return clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t)?-1:int64_t(t.tv_sec)*1000000000+t.tv_nsec;}
inline std::atomic<uint64_t> records{};
inline thread_local uint64_t current_token{};
inline bool Armed(){
 struct Gate {
  int fd=-1;
  Gate(){const char* p=std::getenv("BB_PRESENT_LATENCY_GATE");if(p)fd=open(p,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);struct stat s{};
   if(fd<0||fstat(fd,&s)||!S_ISREG(s.st_mode)||s.st_uid!=getuid()||s.st_nlink!=1||s.st_size!=4||(s.st_mode&0022)){
    if(fd>=0)close(fd);fd=-1;std::fprintf(stderr,"PRESENT_PHASE_ERROR invalid-gate\n");
   }}
  ~Gate(){if(fd>=0)close(fd);}
 };
 static Gate gate;uint32_t value{};return gate.fd>=0&&pread(gate.fd,&value,4,0)==4&&value==1;
}
inline void Interval(const char* phase,uint64_t token,uint64_t start,uint64_t end,int64_t cpu=-1){
 if(!Enabled()||!start||end<start||end-start<SlowNs||!Armed())return;
 const auto count=records.fetch_add(1,std::memory_order_relaxed);
 if(count<MaxRecords)std::fprintf(stderr,"PRESENT_PHASE {\"phase\":\"%s\",\"pid\":%d,\"token\":%llu,\"start_ns\":%llu,\"end_ns\":%llu,\"wall_ns\":%llu,\"cpu_ns\":%lld}\n",phase,int(getpid()),(unsigned long long)token,(unsigned long long)start,(unsigned long long)end,(unsigned long long)(end-start),(long long)cpu);
 else if(count==MaxRecords)std::fprintf(stderr,"PRESENT_PHASE {\"overflow\":true,\"pid\":%d,\"limit\":512}\n",int(getpid()));
}
inline void Origin(uint64_t start){
 if(!Enabled())return;static std::atomic<bool> emitted{};
 if(!emitted.exchange(true,std::memory_order_relaxed))std::fprintf(stderr,"PRESENT_ORIGIN {\"pid\":%d,\"steady_ns\":%llu}\n",int(getpid()),(unsigned long long)start);
}
struct Context {
 uint64_t previous;
 explicit Context(uint64_t token):previous(current_token){current_token=token;}
 ~Context(){current_token=previous;}
 Context(const Context&)=delete;
};
struct Phase {
 const char* name;uint64_t token{},start{};int64_t cpu{-1};
 explicit Phase(const char* name):name(name){if(Enabled()){token=current_token;start=Now();cpu=Cpu();}}
 ~Phase(){if(start){auto end=Now();auto last=Cpu();Interval(name,token,start,end,cpu<0||last<cpu?-1:last-cpu);}}
 Phase(const Phase&)=delete;
};
}
