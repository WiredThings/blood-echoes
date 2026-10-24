// SPDX-License-Identifier: GPL-2.0-or-later
// Authored images using the actual Metal guest-chunk allocator and cache write binding.
// Synthetic guest translation/backing only; no game files, sparse arena or real guest VMAs.
#define runtime_memory_set_guest_chunk_allocator unused_set_guest_chunk_allocator
#define runtime_memory_backing_pointer unused_memory_backing_pointer
#define runtime_memory_direct_phys unused_memory_direct_phys
#define runtime_memory_vma_info unused_memory_vma_info
#define main previous_expansion_main
#include "test_texture_expansion.mm"
#undef main
#undef runtime_memory_set_guest_chunk_allocator
#undef runtime_memory_backing_pointer
#undef runtime_memory_direct_phys
#undef runtime_memory_vma_info
#include "video_core/renderer_metal/mtl_guest_memory.h"
#include <execinfo.h>
#include <signal.h>
#include <unistd.h>
static int (*chunk_allocator)(uint64_t,uint64_t);
extern "C" void runtime_memory_set_guest_chunk_allocator(int(*fn)(uint64_t,uint64_t)){chunk_allocator=fn;}
extern "C" void* runtime_memory_backing_pointer(uint64_t phys){assert(phys<0x800000);return (void*)(test_base+phys);}
extern "C" int runtime_memory_direct_phys(uintptr_t addr,uint64_t* phys,uintptr_t* end){
 if(addr<test_base||addr>=test_base+0x800000)return 0;
 *phys=addr-test_base;*end=test_base+0x800000;return 1;
}
extern "C" int runtime_memory_vma_info(uintptr_t addr,int* prot,int* type,uintptr_t* end){
 if(addr<test_base||addr>=test_base+0x800000)return 0;
 *prot=PROT_READ|PROT_WRITE;*type=1;*end=test_base+0x800000;return 1;
}
static bool Pattern(Fixture& f,Image& image,size_t changed_begin,size_t changed_end){
 f.runtime.Finish();
 @autoreleasepool {
  id<MTLCommandQueue> q=[f.device.GetImpl().device newCommandQueue];
  id<MTLBuffer> b=[f.device.GetImpl().device newBufferWithLength:16384 options:MTLResourceStorageModeShared];
  id<MTLCommandBuffer> c=[q commandBuffer];id<MTLBlitCommandEncoder> e=[c blitCommandEncoder];
  [e copyFromTexture:(__bridge id<MTLTexture>)image.backing->image.native sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(64,64,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:16384];
  [e endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
  for(size_t i=0;i<16384;i++){
   const unsigned expected=i>=changed_begin&&i<changed_end?0x77:(i%4==0||i%4==3?255:0);
   if(((unsigned char*)b.contents)[i]!=expected){printf("PARTIAL mismatch byte=%zu actual=%u expected=%u\n",i,((unsigned char*)b.contents)[i],expected);return false;}
  }
 }
 return true;
}
int main(int argc,char**argv){
 signal(SIGABRT,[](int){void* frames[32];int n=backtrace(frames,32);backtrace_symbols_fd(frames,n,2);_Exit(99);});
 assert(argc==2);std::string_view kind=argv[1];assert(kind=="zero"||kind=="potential"||kind=="real-partial"||kind=="real-full"||kind=="real-interior"||kind=="real-prefix");
 setenv("BB_IMAGE_TRAPS","0",1);setenv("BB_GUEST_IN_PLACE","1",1);setenv("BB_LAYER_MEMORY","1",1);setenv("BB_GUEST_HOOKS","0",1);setenv("BB_GPU_WRITES_IN_PLACE","1",1);void* backing=MAP_FAILED;
 for(uintptr_t a=0x800000000ull;a<0x10000000000ull;a+=0x100000000ull){backing=mmap((void*)a,0x800000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);if(backing!=MAP_FAILED)break;}
 assert(backing!=MAP_FAILED);test_base=(uintptr_t)backing;bool pass=false;
 @autoreleasepool{
  Fixture f;Metal::GuestMemory::Install(f.device);assert(chunk_allocator && chunk_allocator(0,0x800000)==-3);assert(Metal::GuestMemory::Find(0));auto desc=Desc(test_base+0x100000,64,AmdGpu::ImageType::Color2D,64);desc.type=TextureCache::BindingType::Storage;
  memset((void*)desc.info.guest_address,0x11,desc.info.guest_size);auto image_id=f.textures.FindImage(desc);(void)f.textures.FindTexture(image_id,desc);f.runtime.Finish();auto& image=f.textures.GetImage(image_id);
  vk::ClearValue red{};red.color.float32[0]=1;red.color.float32[3]=1;f.runtime.ClearImage(&image,{.base={0,0},.extent={1,1}},red);f.runtime.Finish();assert(Pattern(f,image,0,0));
  const size_t offset=kind=="real-interior"?4:0;
  const uintptr_t write_address=desc.info.guest_address+offset-(kind=="real-prefix"?4:0);
  const size_t bytes=kind=="zero"?0:kind=="real-full"?desc.info.guest_size:kind=="real-prefix"?8:4;
  f.textures.InvalidateMemoryFromGPU(write_address,bytes);
  const bool actual=kind.starts_with("real-");
  if(bytes){
   const auto bound=f.buffers.ObtainBuffer(write_address,bytes,true);f.runtime.Finish();
   assert(bound.first && bound.first->buffer.native);
   assert(bound.first->buffer.native==Metal::GuestMemory::Find(0)->buffer);
   if(actual){
    id<MTLCommandQueue> q=[f.device.GetImpl().device newCommandQueue];
    id<MTLCommandBuffer> c=[q commandBuffer];id<MTLBlitCommandEncoder> e=[c blitCommandEncoder];
    [e fillBuffer:(__bridge id<MTLBuffer>)bound.first->buffer.native range:NSMakeRange(bound.second,bytes) value:0x77];
    [e endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
    assert(((unsigned char*)write_address)[0]==0x77);
   }
  }
  desc.type=TextureCache::BindingType::Texture;(void)f.textures.FindTexture(image_id,desc);f.runtime.Finish();
  pass=Pattern(f,image,actual?offset:0,actual?offset+bytes-(kind=="real-prefix"?4:0):0);
  printf("PARTIAL kind=%s pixels=%s flags=%#x\n",argv[1],pass?"PASS":"FAIL",u32(image.flags));
 }
 munmap(backing,0x800000);return pass?0:7;
}
