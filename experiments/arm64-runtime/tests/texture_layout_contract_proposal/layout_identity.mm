// SPDX-License-Identifier: GPL-2.0-or-later
// Authored integration controls. Reuses synthetic-only fixture; no game content.
#define main previous_expansion_main
#include "../metal/test_texture_expansion.mm"
#undef main
#include <vector>
#include "bbport_toggles.h"
static std::vector<unsigned char> Read(Fixture& f,Image& image) {
 f.runtime.Finish();const unsigned width=image.info.size.width,height=image.info.size.height;std::vector<unsigned char> result(width*height*4);
 @autoreleasepool {
  id<MTLCommandQueue> q=[f.device.GetImpl().device newCommandQueue];
  id<MTLBuffer> b=[f.device.GetImpl().device newBufferWithLength:16384 options:MTLResourceStorageModeShared];
  id<MTLCommandBuffer> c=[q commandBuffer];id<MTLBlitCommandEncoder> enc=[c blitCommandEncoder];
  [enc copyFromTexture:(__bridge id<MTLTexture>)image.backing->image.native sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(width,height,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:16384];
  [enc endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);std::memcpy(result.data(),b.contents,result.size());
 }return result;
}
static void Pattern(uintptr_t a) {for(unsigned i=0;i<16384;i++)((unsigned char*)a)[i]=i%4==3?255:(i*17+i/64)%251;}
static TextureCache::ImageDesc Tiled(uintptr_t a) {auto d=Desc(a,64,AmdGpu::ImageType::Color2D,64);d.info.tile_mode=AmdGpu::TileMode::Thin1DThin;d.info.array_mode=AmdGpu::ArrayMode::Array1DTiledThin1;d.info.props.is_tiled=1;d.info.UpdateSize();assert(d.info.guest_size==16384);return d;}
int main(int argc,char**argv) {
 assert(argc==2);std::string_view kind=argv[1];setenv("BB_GUEST_IN_PLACE","0",1);
 void* m=MAP_FAILED;for(uintptr_t address=0x800000000ull;address<0x10000000000ull;address+=0x100000000ull){m=mmap((void*)address,0x800000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);if(m!=MAP_FAILED)break;}assert(m!=MAP_FAILED);test_base=(uintptr_t)m;
 @autoreleasepool {Fixture f;auto old=Desc(test_base+0x100000,64,AmdGpu::ImageType::Color2D,kind=="normalized-cross-dimension"?1:64);Pattern(old.info.guest_address);auto old_id=f.textures.FindImage(old);(void)f.textures.FindTexture(old_id,old);auto old_pixels=Read(f,f.textures.GetImage(old_id));auto old_uid=f.textures.GetImage(old_id).image_uid;
  if(kind=="normalized-cross-dimension") {auto alias=old;alias.info.type=AmdGpu::ImageType::Color1D;alias.view_info.type=AmdGpu::ImageType::Color1D;auto id=f.textures.FindImage(alias);assert(id==old_id);(void)f.textures.FindTexture(id,alias);assert(Read(f,f.textures.GetImage(id))==old_pixels);puts("CONTROL normalized logical1D/2D identical-layout alias reused: PASS");}
  else if(kind=="same") {auto id=f.textures.FindImage(old);assert(id==old_id);assert(Read(f,f.textures.GetImage(id))==old_pixels);puts("CONTROL same layout reused with identical bytes: PASS");}
  else if(kind=="typed-alias") {auto alias=old;alias.info.pixel_format=vk::Format::eR8G8B8A8Srgb;auto id=f.textures.FindImage(alias);assert(id==old_id);assert(Read(f,f.textures.GetImage(id))==old_pixels);puts("CONTROL compatible sRGB alias reused with identical backing: PASS");}
  else if(kind=="unmap-generation") {f.textures.UnmapMemory(old.info.guest_address,old.info.guest_size);auto id=f.textures.FindImage(old);assert(f.textures.GetImage(id).image_uid!=old_uid);(void)f.textures.FindTexture(id,old);assert(Read(f,f.textures.GetImage(id))==old_pixels);puts("CONTROL explicit unmap replaced uid and identical upload: PASS");}
  else {auto requested=Tiled(old.info.guest_address);assert(requested.info.guest_size==old.info.guest_size);auto ref=Tiled(test_base+0x200000);Pattern(ref.info.guest_address);auto ref_id=f.textures.FindImage(ref);(void)f.textures.FindTexture(ref_id,ref);auto expected=Read(f,f.textures.GetImage(ref_id));assert(expected!=old_pixels);
   if(kind=="memo-layout") {auto restore=old;assert(f.textures.FindImage(restore)==old_id);}
   if(kind=="exact-layout")runtime_disabled_optimizations|=BbToggle::FindImageCache;
   if(kind=="repeat-layout")for(unsigned n=0;n<1000;++n)(void)f.textures.FindImage(requested);
   auto id=f.textures.FindImage(requested);(void)f.textures.FindTexture(id,requested);auto actual=Read(f,f.textures.GetImage(id));size_t mismatches=0;for(size_t i=0;i<actual.size();i++)mismatches+=actual[i]!=expected[i];
   printf("LAYOUT case=%s old_uid=%llu selected_uid=%llu requested_tile=%u selected_tile=%u same_id=%u byte_mismatch=%zu of %zu\n",argv[1],(unsigned long long)old_uid,(unsigned long long)f.textures.GetImage(id).image_uid,unsigned(requested.info.tile_mode),unsigned(f.textures.GetImage(id).info.tile_mode),id==old_id,mismatches,actual.size());
   assert(id==old_id&&mismatches>0);
  }
 }munmap(m,0x800000);return 0;
}
