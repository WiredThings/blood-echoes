// SPDX-License-Identifier: MIT
// Private bounded metadata trace. Enabled only by explicit isolated runner option.
#pragma once
#include <atomic>
#include <new>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cerrno>
#include <ctime>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
namespace BbAudit {
enum Kind : uint32_t { FexBlock=1, FexInvalidationLock, FexIR, FexCodegen, FexPublish, FexCodeBufferLock, FexMappingLock, FexCodePagesLock,
 MetalLibrary=10, MetalLibraryBuild, MetalPSO, MetalReplayWait, MetalGPUWait, MetalSubmitWait, MetalSpirvToMsl, MetalSlowPacket=17, MetalPacketCpuWindow=18, MetalPacketStatus=19,
 IPC=20, IPCWrite, IPCReply, IPCHandler, IPCWriterLock, IPCSpaceWait, IPCQueueWrite, HLE=30, GuestSleep=31, HleLookup=32, FrameInterval=40, LifetimeCollect=41, MemoryReport=42, MetalPacketIdentity=43, MetalTaskTarget=44, GuestFlipMarker=45, GuestSubmit=46, GuestFrameStatus=47, GuestFrameAccounting=48, GuestFrameCpuWindow=49, GuestConditionWait=50, GuestConditionIdentity=51, GuestConditionMutex=52, GuestConditionResult=53, GuestConditionWake=54, GuestConditionOperation=55, GuestConditionStatus=56, GuestConditionErrors=57, GuestProducerCpuWindow=58, ProducerConditionWait=65,ProducerConditionIdentity,ProducerConditionMutex,ProducerConditionResult,ProducerConditionToken,ProducerNotification,ProducerNotificationIdentity,ProducerNotificationOperation,UnalignedSignal=73,UnalignedRepair,UnalignedLog,UnalignedPublish,UnalignedRejected, ShaderModuleCompile=78, ShaderGcnToIR, ShaderIRToSpirv, ShaderModuleIdentity, ShaderModuleReturned, ShaderModuleBackend, PreparedShaderCandidate=84, PreparedShaderIdentity, PreparedShaderOverflow };
constexpr unsigned Capacity=16384;
inline uint64_t Now(clockid_t c=CLOCK_MONOTONIC) { timespec t{}; if(clock_gettime(c,&t))return 0; return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec; }
struct alignas(64) Row {
 std::atomic<uint64_t> sequence{0};
 std::atomic<uint32_t> busy{0}; uint32_t kind{};
 uint64_t tid{},start{},wall{},cpu{},arg{},reserved{};
};
static_assert(sizeof(Row)==64);
struct alignas(64) Header {
 char magic[8]; uint64_t version,pid,mono,utc,capacity;
 std::atomic<uint64_t> issued{0},dropped{0};
};
static_assert(sizeof(Header)==64);
struct Buffer {Header h; Row rows[Capacity];};
struct Store {
 Buffer* data{};
 bool coarse{};
 bool Allows(uint32_t kind) const {return !coarse || (kind!=GuestSleep && kind!=HleLookup && kind!=33 && kind!=IPCWrite && kind!=IPCWriterLock && kind!=IPCQueueWrite);}
 const std::atomic<uint32_t>* gate{};
 Store() {
  const char* mode=std::getenv("BB_STUTTER_COARSE");coarse=mode && mode[0]=='1';
  const char* root=std::getenv("BB_STUTTER_AUDIT"); if(!root||!*root)return;
  if(const char* gate_path=std::getenv("BB_STUTTER_GATE")) {
   int gfd=open(gate_path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);struct stat gst{};
   if(gfd<0)return;
   if(fstat(gfd,&gst)||!S_ISREG(gst.st_mode)||gst.st_nlink!=1||gst.st_uid!=geteuid()||gst.st_size!=4096){close(gfd);return;}
   void* gp=mmap(nullptr,4096,PROT_READ,MAP_SHARED,gfd,0);close(gfd);
   if(gp==MAP_FAILED)return;gate=static_cast<const std::atomic<uint32_t>*>(gp);
  }
  char path[4096]; const int n=std::snprintf(path,sizeof(path),"%s-%d.bin",root,getpid());
  if(n<0||size_t(n)>=sizeof(path))return;
  int fd=open(path,O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600); const bool created=fd>=0;
  if(fd<0 && errno==EEXIST)fd=open(path,O_RDWR|O_NOFOLLOW|O_CLOEXEC);
  if(fd<0)return;
  if(created && ftruncate(fd,sizeof(Buffer))!=0){close(fd);return;}
  struct stat st{};
  if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_nlink!=1||st.st_uid!=geteuid()||st.st_size!=sizeof(Buffer)){close(fd);return;}
  void* p=mmap(nullptr,sizeof(Buffer),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0); close(fd);
  if(p==MAP_FAILED)return;
  if(!created){ // A hidden inline in another DSO attaches the same process-local trace.
   auto* existing=static_cast<Buffer*>(p);
   if(__builtin_memcmp(existing->h.magic,"BBAUDIT1",8)||existing->h.pid!=uint64_t(getpid())||existing->h.capacity!=Capacity){munmap(p,sizeof(Buffer));return;}
   data=existing;return;
  }
  data=new(p) Buffer; // Fixed 1 MiB mapping; never grows and survives a controlled process exit.
  data->h.version=1;data->h.pid=getpid();
  data->h.mono=Now();data->h.utc=Now(CLOCK_REALTIME);data->h.capacity=Capacity;
  __builtin_memcpy(data->h.magic,"BBAUDIT1",8);
 }
};
inline Store& Get(){static Store s;return s;}
inline bool Enabled(){auto& s=Get();return s.data && (!s.gate || s.gate->load(std::memory_order_relaxed)==1);}
inline bool EnabledFor(uint32_t kind){return kind && Enabled() && Get().Allows(kind);}
inline void RecordPrepared(Buffer* b,uint32_t kind,uint64_t tid,uint64_t start,uint64_t wall,uint64_t cpu,uint64_t arg=0)noexcept{
 if(!b)return;
 const uint64_t i=b->h.issued.fetch_add(1,std::memory_order_relaxed);auto& r=b->rows[i%Capacity];
 if(r.busy.exchange(1,std::memory_order_acquire)){b->h.dropped.fetch_add(1,std::memory_order_relaxed);return;}
 if(r.sequence.load(std::memory_order_relaxed)>i+1){b->h.dropped.fetch_add(1,std::memory_order_relaxed);r.busy.store(0,std::memory_order_release);return;}
 r.sequence.store(0,std::memory_order_relaxed);r.kind=kind;r.tid=tid;
 r.start=start;r.wall=wall;r.cpu=cpu;r.arg=arg;r.reserved=0;
 r.sequence.store(i+1,std::memory_order_release);r.busy.store(0,std::memory_order_release);
}
inline void Record(uint32_t kind,uint64_t start,uint64_t wall,uint64_t cpu,uint64_t arg=0){
 auto& store=Get();auto* b=store.data;if(!b || !store.Allows(kind))return;
 uint64_t tid{};pthread_threadid_np(nullptr,&tid);RecordPrepared(b,kind,tid,start,wall,cpu,arg);
}
struct Scope {
 uint32_t kind;uint64_t arg,start{},cpu{},minimum;
 Scope(uint32_t k,uint64_t a=0,uint64_t min=1000000):kind(k),arg(a),minimum(min){if(EnabledFor(k)){start=Now();cpu=Now(CLOCK_THREAD_CPUTIME_ID);}}
 void End(){
  if(!start)return;
  const auto finish=Now(),used=Now(CLOCK_THREAD_CPUTIME_ID);
  // Failed/regressing clocks are incomplete diagnostics, never a huge unsigned wait.
  if(finish<start){if(auto* b=Get().data)b->h.dropped.fetch_add(1,std::memory_order_relaxed);start=0;return;}
  if(finish-start>=minimum)Record(kind,start,finish-start,used>=cpu?used-cpu:0,arg);
  start=0;
 }
 ~Scope(){End();}
};
}
