// SPDX-License-Identifier: GPL-2.0-or-later
#include "../metal_api/shim/bbport_guest_hooks.cpp"
#include <cassert>
extern "C" int CheckGuestHookState(void (*)(), const void *, void *);
extern "C" void ClobberGuestHookState();
static uint64_t scope_generation=2;
static int scope_calls=0;
static bool gpu_mapping=true;
static int FakeScope(uintptr_t address,int* prot,int* type,uintptr_t* end) {
    ++scope_calls;*prot=gpu_mapping?0x33:0x3;*type=3;*end=address+65536;return 1;
}
static const uint64_t* FakeGeneration() {return &scope_generation;}
int main() {
  using namespace BbGuestHooks;
  setenv("BB_GPU_LIFETIME", "1", 1);
  setenv("BB_CAPTURE_NATIVE","1",1);setenv("BB_WATCH","0x300000,192",1);
  assert(ParseHeapWatch("0x300000,192",true).address==0x300000);
  assert(!ParseHeapWatch("0x300000,192",false).size);
  for(const char* bad:{"0,192","0x10000000000,192","0x300000,191","0x300000,192x","bad"})
      assert(!ParseHeapWatch(bad,true).size);
  assert(CoversHeapWatch({0x300000,192},0x2ffff0,208));
  assert(!CoversHeapWatch({0x300000,192},0x300001,256));
  assert(!CoversHeapWatch({0x300000,192},0x300000,191));
  alignas(16) u64 frames[8]{};
  for(unsigned n=0;n<4;++n){frames[n*2]=n<3?u64(&frames[n*2+2]):0;frames[n*2+1]=100+n;}
  auto callers=GatherHeapCallers(u64(frames),100);
  assert(callers.count==4 && callers.addresses[0]==100 && callers.addresses[3]==103);
  assert(!GatherHeapCallers(u64(frames),999).count);
  assert(!GatherHeapCallers(0,100).count && !GatherHeapCallers(1,100).count);
  assert(!GatherHeapCallers(~u64{0}-7,100).count);
  frames[0]=u64(frames);assert(GatherHeapCallers(u64(frames),100).count==1);
  frames[0]=u64(frames)+65544;assert(GatherHeapCallers(u64(frames),100).count==1);
  const long page_size=sysconf(_SC_PAGESIZE);
  void* unreadable=mmap(nullptr,page_size,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  assert(unreadable!=MAP_FAILED && !GatherHeapCallers(u64(unreadable),100).count);
  assert(munmap(unreadable,page_size)==0);
  scope_query=&FakeScope;generation_query=&FakeGeneration;
  assert(GpuScope(0x2000));const int before=scope_calls;
  assert(GpuScope(0x2010) && scope_calls==before);
  gpu_mapping=false;scope_generation+=2;
  assert(!GpuScope(0x2010) && scope_calls>before);
  gpu_mapping=true;scope_generation+=2;
  assert(GpuScope(0x2010));
  stub_page = MapStubPage();
  assert(stub_page);
  Emitter e;
  e.SaveFull();
  e.CallAbs(reinterpret_cast<const void *>(&ClobberGuestHookState));
  e.RestoreFull();
  e.Bytes({0xc3});
  const auto stub = PlaceStub(e);
  assert(stub);
  alignas(32) unsigned char input[512], output[512]{};
  for (unsigned i = 0; i < 512; ++i)
    input[i] = i * 17 + 3;
  assert(CheckGuestHookState(reinterpret_cast<void (*)()>(stub), input,
                             output) == 1);
  assert(!std::memcmp(input, output, 512));
  // Install against a tiny synthetic function with the verified game's
  // prologue. No game.
  const u64 entry = ImageBase + HeapFree;
  const auto page = entry & ~u64(4095);
  assert(mmap(reinterpret_cast<void *>(page), 4096,
              PROT_READ | PROT_WRITE | PROT_EXEC,
              MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1,
              0) != MAP_FAILED);
  auto *code = reinterpret_cast<u8 *>(entry);
  const u8 function[] = {
      0x55, 0x48, 0x89, 0xe5, 0x41, 0x57,
      0x41, 0x56, 0x48, 0xff, 0x07, // inc qword [rdi] -- prove original body
                                    // executes
      0x41, 0x5e, 0x41, 0x5f, 0x5d, 0xc3};
  std::memcpy(code, function, sizeof(function));
  assert(InstallLifetimeFree(native_sites[5]));
  auto tag = BbResourceLifetime::Get().Allocate(
      BbResourceLifetime::Domain::Heap, 0x100000, 4096);
  u64 called = 0;
  reinterpret_cast<HeapFreeFn>(entry)(reinterpret_cast<u64>(&called), 0x100000);
  assert(called == 1 && BbResourceLifetime::Get().Status(tag) ==
                            BbResourceLifetime::State::Released);
  assert(mprotect(reinterpret_cast<void *>(page), 4096,
                  PROT_READ | PROT_WRITE | PROT_EXEC) == 0);
  std::memcpy(code, function, sizeof(function));
  code[7] = 0x90; // unsupported version
  assert(!InstallLifetimeFree(native_sites[5]) && code[0] == 0x55);
  const u64 range_entry = ImageBase + RangeFree;
  const auto range_page = range_entry & ~u64(4095);
  assert(mmap(reinterpret_cast<void *>(range_page), 4096,
              PROT_READ | PROT_WRITE | PROT_EXEC,
              MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1,
              0) != MAP_FAILED);
  const u8 setup[] = {0x41, 0x56, 0x49, 0x89, 0xfe, 0x48, 0x83,
                      0xec, 0x30, 0x48, 0x89, 0x74, 0x24, 0x28};
  const u8 finish[] = {0x48, 0x83, 0xc4, 0x30, 0x41, 0x5e, 0xc3};
  auto *range_code = reinterpret_cast<u8 *>(range_entry);
  std::memcpy(range_code - sizeof(setup), setup, sizeof(setup));
  std::memcpy(range_code, RangeFreeCode, sizeof(RangeFreeCode));
  std::memcpy(range_code + sizeof(RangeFreeCode), finish, sizeof(finish));
  assert(InstallLifetimeFree(native_sites[4]));
  u64 node[6]{}, next[2]{}, allocator[32]{};
  node[1] = 0x200000;
  node[3] = reinterpret_cast<u64>(next);
  next[1] = node[1] + 8192;
  allocator[0xc8 / 8] = 3;
  auto range_tag = BbResourceLifetime::Get().Allocate(
      BbResourceLifetime::Domain::Range, node[1], 8192);
  reinterpret_cast<HeapFreeFn>(range_entry - sizeof(setup))(
      reinterpret_cast<u64>(node), reinterpret_cast<u64>(allocator));
  assert(allocator[0xc8 / 8] == 2 &&
         BbResourceLifetime::Get().Status(range_tag) ==
             BbResourceLifetime::State::Released);
  // Real malloc/memalign prologues: verify return values, original execution and pairing.
  assert(mprotect(reinterpret_cast<void*>(page),4096,PROT_READ|PROT_WRITE|PROT_EXEC)==0);
  const u8 plain_function[]={0x53,0x31,0xc0,0x48,0x39,0x77,0x18,
      0x48,0x8b,0x07,0x5b,0xc3}; // return [allocator]
  const u8 aligned_function[]={0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,
      0x48,0x8b,0x07,0x41,0x5c,0x41,0x5d,0x41,0x5e,0x41,0x5f,0xc3};
  auto* plain_code=reinterpret_cast<u8*>(ImageBase+HeapPlainAlloc);
  auto* aligned_code=reinterpret_cast<u8*>(ImageBase+HeapAlignedAlloc);
  std::memcpy(plain_code,plain_function,sizeof(plain_function));
  std::memcpy(aligned_code,aligned_function,sizeof(aligned_function));
  assert(InstallLifetimeFree(native_sites[6]));
  assert(InstallLifetimeFree(native_sites[7]));
  u64 heap[4]{0x300000,0,0,1<<20};
  assert(reinterpret_cast<HeapPlainFn>(plain_code)(reinterpret_cast<u64>(heap),4096)==heap[0]);
  auto plain_tag=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,heap[0]);
  assert(plain_tag && plain_tag.size==4096);
  assert(heap_watch_records.load()==1); // real detoured allocation still returns and pairs normally
  assert(BbResourceLifetime::Get().Release(plain_tag));
  heap[0]=0x400000;
  assert(reinterpret_cast<HeapAlignedFn>(aligned_code)(reinterpret_cast<u64>(heap),8192,256)==heap[0]);
  auto aligned_tag=BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,heap[0]);
  assert(aligned_tag && aligned_tag.size==8192);
  // Aligned allocation can tail-call the plain entry; count that allocation exactly once.
  assert(mprotect(reinterpret_cast<void*>(page),4096,PROT_READ|PROT_WRITE|PROT_EXEC)==0);
  u8 tail[]={0x41,0x5c,0x41,0x5d,0x41,0x5e,0x41,0x5f,0x48,0xb8,
      0,0,0,0,0,0,0,0,0xff,0xe0};
  const u64 plain_address=reinterpret_cast<u64>(plain_code);std::memcpy(tail+10,&plain_address,8);
  std::memcpy(aligned_code+8,tail,sizeof(tail));
  const auto before_nested=BbResourceLifetime::Get().Read().domains[1].allocations;
  heap[0]=0x600000;
  assert(reinterpret_cast<HeapAlignedFn>(aligned_code)(reinterpret_cast<u64>(heap),2048,16)==heap[0]);
  assert(BbResourceLifetime::Get().Read().domains[1].allocations==before_nested+1);
  // CPU-only heap calls must not grow the GPU interval map or count as unknown frees.
  gpu_mapping=false;scope_generation+=2;heap[0]=0x500000;
  assert(reinterpret_cast<HeapPlainFn>(plain_code)(reinterpret_cast<u64>(heap),1024)==heap[0]);
  assert(!BbResourceLifetime::Get().Find(BbResourceLifetime::Domain::Heap,heap[0]));
  std::puts("PASS: real inline emitter preserves AVX/GPR/flags, free "
            "trampoline calls original, version mismatch unchanged");
}
