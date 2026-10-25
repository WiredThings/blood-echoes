// SPDX-License-Identifier: GPL-2.0-or-later
// Authored GPU-only resource preservation control. No game files or disk caches.
#define main previous_expansion_main
#include "../metal/test_texture_expansion.mm"
#undef main
#include "video_core/texture_cache/tile_manager.h"
#include "../metal/tiling_cpu_reference.h"
#include <vector>
#include <algorithm>
#include <bit>
static constexpr unsigned Guard=256;
static uint32_t Word(unsigned x,unsigned y,unsigned mip,unsigned layer) {
 const uint32_t v=(x*37u+y*113u+mip*2971u+layer*7919u+0x381u)*0x45d9f3bu;
 return (v&0x00ffffffu)|0xff000000u;
}
static TilingCpu::Config Config(const ImageInfo& info) {
 TilingCpu::Config c;c.bits=info.num_bits;c.samples=info.num_samples;c.micro_mode=u32(AmdGpu::GetMicroTileMode(info.tile_mode));c.thickness=AmdGpu::GetMicroTileThickness(info.array_mode);c.macro=AmdGpu::IsMacroTiled(info.array_mode);c.bank_swizzle=info.bank_swizzle;
 if(!c.macro)return c;const auto a=info.array_mode;c.prt=AmdGpu::IsPrt(a);c.mode_2d=a==AmdGpu::ArrayMode::Array2DTiledThin1;c.mode_3d=false;c.split_rotates=c.mode_2d;
 auto pipe=AmdGpu::GetPipeConfig(info.tile_mode);c.pipe_config=u32(pipe);c.num_pipes=AmdGpu::GetPipeCount(pipe);auto m=AmdGpu::CalculateMacrotileMode(info.tile_mode,info.num_bits,info.num_samples);c.bank_width=AmdGpu::GetBankWidth(m);c.bank_height=AmdGpu::GetBankHeight(m);c.num_banks=AmdGpu::GetNumBanks(m);c.macro_aspect=AmdGpu::GetMacrotileAspect(m);c.tile_split_bytes=AmdGpu::CalculateTileSplit(info.tile_mode,info.array_mode,AmdGpu::GetMicroTileMode(info.tile_mode),info.num_bits);return c;
}
static size_t RawOffset(const ImageInfo& info,unsigned m,unsigned l,unsigned x,unsigned y) {
 const auto& a=info.mips_layout[m];if(!info.props.is_tiled)return a.offset+(size_t(l)*a.height+y)*a.pitch*4+x*4;
 auto c=Config(info);TilingCpu::Mip mip{a.size,a.pitch,a.height,a.offset,!c.macro||bool(info.micro_tiled_mips>>m&1)};return a.offset+TilingCpu::Offset(c,mip,x,y,l);
}
static TextureCache::ImageDesc Make(uintptr_t addr,unsigned mode,unsigned width,unsigned height,unsigned levels,unsigned layers,bool srgb=false,bool logical1d=false) {
 auto d=Desc(addr,width,logical1d?AmdGpu::ImageType::Color1D:AmdGpu::ImageType::Color2D,height);d.info.tile_mode=AmdGpu::TileMode(mode);d.info.array_mode=AmdGpu::GetArrayMode(d.info.tile_mode);d.info.props.is_tiled=mode!=8;d.info.resources={levels,layers};d.info.pixel_format=srgb?vk::Format::eR8G8B8A8Srgb:vk::Format::eR8G8B8A8Unorm;d.info.UpdateSize();d.view_info.range.extent={levels,layers};if(layers>1)d.view_info.type=AmdGpu::ImageType::Color2DArray;return d;
}
static std::vector<vk::BufferImageCopy> Copies(const ImageInfo& info,uint64_t base=0) {
 std::vector<vk::BufferImageCopy> result;for(unsigned m=0;m<info.resources.levels;m++){const auto& a=info.mips_layout[m];result.push_back({.bufferOffset=base+a.offset,.bufferRowLength=a.pitch,.bufferImageHeight=a.height,.imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.mipLevel=m,.baseArrayLayer=0,.layerCount=info.resources.layers},.imageOffset={0,0,0},.imageExtent={std::max(info.size.width>>m,1u),std::max(info.size.height>>m,1u),1}});}return result;
}
static id<MTLComputePipelineState> Generator(Fixture& f) {
 NSString* source=@"#include <metal_stdlib>\nusing namespace metal;struct P{uint width,height,row,mip,layer;};kernel void generate(device uint* out[[buffer(0)]],constant P& p[[buffer(1)]],uint i[[thread_position_in_grid]]){if(i>=p.row*p.height)return;uint x=i%p.row,y=i/p.row;uint v=(x*37u+y*113u+p.mip*2971u+p.layer*7919u+0x381u)*0x45d9f3bu;out[i]=(v&0x00ffffffu)|0xff000000u;}";
 NSError* error=nil;id<MTLLibrary> lib=[f.device.GetImpl().device newLibraryWithSource:source options:nil error:&error];if(!lib){fprintf(stderr,"generator: %s\n",error.localizedDescription.UTF8String);abort();}id<MTLComputePipelineState> pipe=[f.device.GetImpl().device newComputePipelineStateWithFunction:[lib newFunctionWithName:@"generate"] error:&error];assert(pipe);return pipe;
}
static void GenerateGpu(Fixture& f,Image& image) {
 f.runtime.Finish();id<MTLDevice> dev=f.device.GetImpl().device;id<MTLCommandQueue> queue=[dev newCommandQueue];auto pipe=Generator(f);
 for(unsigned m=0;m<image.info.resources.levels;m++)for(unsigned l=0;l<image.info.resources.layers;l++) {
  unsigned width=std::max(image.info.size.width>>m,1u),height=std::max(image.info.size.height>>m,1u),row=((width*4+255)/256)*64;struct P{unsigned width,height,row,mip,layer;}p{width,height,row,m,l};
  id<MTLBuffer> generated=[dev newBufferWithLength:size_t(row)*height*4 options:MTLResourceStorageModePrivate];assert(generated);id<MTLCommandBuffer> c=[queue commandBuffer];id<MTLComputeCommandEncoder> enc=[c computeCommandEncoder];[enc setComputePipelineState:pipe];[enc setBuffer:generated offset:0 atIndex:0];[enc setBytes:&p length:sizeof(p) atIndex:1];[enc dispatchThreads:MTLSizeMake(row*height,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];[enc endEncoding];auto blit=[c blitCommandEncoder];[blit copyFromBuffer:generated sourceOffset:0 sourceBytesPerRow:row*4 sourceBytesPerImage:size_t(row)*height*4 sourceSize:MTLSizeMake(width,height,1) toTexture:(__bridge id<MTLTexture>)image.backing->image.native destinationSlice:l destinationLevel:m destinationOrigin:MTLOriginMake(0,0,0)];[blit endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
 }
 image.flags|=ImageFlagBits::GpuModified;image.gpu_write_tick=f.runtime.CurrentTick();
}
static std::vector<uint8_t> ReadPixels(Fixture& f,Image& image,unsigned m,unsigned l) {
 f.runtime.Finish();unsigned w=std::max(image.info.size.width>>m,1u),h=std::max(image.info.size.height>>m,1u),row=((w*4+255)/256)*256;std::vector<uint8_t> out(w*h*4);id<MTLDevice> dev=f.device.GetImpl().device;auto q=[dev newCommandQueue];auto b=[dev newBufferWithLength:size_t(row)*h options:MTLResourceStorageModeShared];auto c=[q commandBuffer];auto enc=[c blitCommandEncoder];[enc copyFromTexture:(__bridge id<MTLTexture>)image.backing->image.native sourceSlice:l sourceLevel:m sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1) toBuffer:b destinationOffset:0 destinationBytesPerRow:row destinationBytesPerImage:size_t(row)*h];[enc endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);for(unsigned y=0;y<h;y++)memcpy(out.data()+y*w*4,(uint8_t*)b.contents+y*row,w*4);return out;
}
struct Case{const char* name;unsigned source,dest,w,h,levels,layers;bool src_srgb=false,dst_srgb=false,src1d=false,dst1d=false;};
static constexpr Case cases[]={
 {"linear-thin",8,13,64,64,1,1},{"thin-linear",13,8,64,64,1,1},
 {"display-thin",9,13,64,64,1,1},{"thin-display",13,9,64,64,1,1},
 {"macro-thin",14,13,256,256,1,1},{"thin-macro",13,14,256,256,1,1},
 {"macro-thin-mips",14,13,256,256,3,1},{"thin-linear-mips-layers",13,8,64,64,3,2},
 {"linear-thin-mips-layers",8,13,64,64,3,2},{"thin-display-layers",13,9,64,64,1,2},
 {"unorm-srgb",13,13,64,64,1,1,false,true},{"srgb-unorm",13,13,64,64,1,1,true,false},
 {"macro-srgb-thin",14,13,256,256,1,1,true,false},{"thin-linear-padded",13,8,64,60,1,1},{"linear-thin-padded",8,13,64,60,1,1},{"thin-linear-small-mips",13,8,64,64,7,1},
 {"logical1d-2d",8,8,64,1,1,1,false,false,true,false},{"logical2d-1d",8,8,64,1,1,1,false,false,false,true}
};
int main(int argc,char**argv){assert(argc==2||argc==3);std::string_view negative=argc==3?argv[2]:"";assert(negative.empty()||negative=="skip-gpu-generation"||negative=="stale-cpu-raw"||negative=="pixel-copy");const Case* k=nullptr;for(auto& c:cases)if(std::string_view(argv[1])==c.name)k=&c;assert(k);setenv("BB_GUEST_IN_PLACE","0",1);void* map=MAP_FAILED;for(uintptr_t a=0x800000000ull;a<0x10000000000ull;a+=0x100000000ull){map=mmap((void*)a,0x800000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);if(map!=MAP_FAILED)break;}assert(map!=MAP_FAILED);test_base=(uintptr_t)map;
 bool pass=true;unsigned subresources=0;size_t differences=0,pixel_differences=0,defined_differences=0,padding_differences=0,guard_differences=0,target_padding_reads=0,target_padding_errors=0;
 @autoreleasepool {Fixture f;auto src=Make(test_base+0x100000,k->source,k->w,k->h,k->levels,k->layers,k->src_srgb,k->src1d),dst=Make(test_base+0x300000,k->dest,k->w,k->h,k->levels,k->layers,k->dst_srgb,k->dst1d);assert(src.info.guest_size<0x100000&&dst.info.guest_size<0x100000);const size_t raw_span=std::max(src.info.guest_size,dst.info.guest_size);memset((void*)src.info.guest_address,0x11,raw_span);memset((void*)dst.info.guest_address,0x22,raw_span);auto sid=f.textures.FindImage(src);(void)f.textures.FindTexture(sid,src);auto did=f.textures.FindImage(dst);(void)f.textures.FindTexture(did,dst);auto& source=f.textures.GetImage(sid);auto& dest=f.textures.GetImage(did);if(negative!="skip-gpu-generation")GenerateGpu(f,source);
  // Independent CPU oracle derives expected words; it never populates guest memory.
  size_t size=std::max(src.info.guest_size,dst.info.guest_size);std::vector<uint8_t> expected(size+Guard,0x11);std::fill(expected.begin()+size,expected.end(),0xcd);std::vector<bool> covered(size,false);
  for(unsigned m=0;m<src.info.resources.levels;m++)for(unsigned l=0;l<src.info.resources.layers;l++){auto actual=ReadPixels(f,source,m,l);unsigned w=std::max(k->w>>m,1u),h=std::max(k->h>>m,1u);for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++){uint32_t word=Word(x,y,m,l),observed=0;memcpy(&observed,actual.data()+(y*w+x)*4,4);if(observed!=word){printf("SOURCE_GPU_ORACLE_REJECTED mip=%u layer=%u x=%u y=%u got=%#x expected=%#x negative=%s\n",m,l,x,y,observed,word,argc==3?argv[2]:"none");return 9;}size_t off=RawOffset(src.info,m,l,x,y);assert(off+4<=size);for(unsigned j=0;j<4;j++){assert(!covered[off+j]);covered[off+j]=true;}memcpy(expected.data()+off,&word,4);}}
  auto raw=f.runtime.StagingRequest(size+Guard,MemoryType::HostCached,256);f.runtime.FillBuffer(raw.buffer,raw.offset,size,0x11111111);f.runtime.FillBuffer(raw.buffer,raw.offset+size,Guard,0xcdcdcdcd);auto copies=Copies(src.info);f.textures.GetTileManager().TileImage(source,copies,raw.buffer,raw.offset);f.runtime.Finish();if(negative=="stale-cpu-raw"){f.runtime.FillBuffer(raw.buffer,raw.offset,size,0x11111111);f.runtime.Finish();}raw.Invalidate();for(size_t i=0;i<expected.size();i++)if(raw.mapped[i]!=expected[i]){if(differences<4)printf("RAW mismatch %zu got=%u expected=%u\n",i,raw.mapped[i],expected[i]);differences++;if(i>=size)guard_differences++;else if(covered[i])defined_differences++;else padding_differences++;}if(differences)pass=false;
  if(negative=="pixel-copy"){f.runtime.CopyImage(&source,&dest);}else {const auto [linear,offset]=f.textures.GetTileManager().DetileImage(raw.buffer,raw.offset,dst.info);auto uploads=Copies(dst.info,offset);f.runtime.UploadImage(&dest,linear,uploads);}f.runtime.Finish();
  for(unsigned m=0;m<dst.info.resources.levels;m++)for(unsigned l=0;l<dst.info.resources.layers;l++){subresources++;auto actual=ReadPixels(f,dest,m,l);unsigned w=std::max(k->w>>m,1u),h=std::max(k->h>>m,1u);for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++){size_t off=RawOffset(dst.info,m,l,x,y);assert(off+4<=size);for(unsigned j=0;j<4;j++){if(!covered[off+j])target_padding_reads++;if(actual[(y*w+x)*4+j]!=expected[off+j]){if(!covered[off+j])target_padding_errors++;if(pixel_differences<4)printf("PIXEL mismatch mip=%u layer=%u x=%u y=%u channel=%u got=%u expected=%u\n",m,l,x,y,j,actual[(y*w+x)*4+j],expected[off+j]);pixel_differences++;}}}}
  if(pixel_differences)pass=false;for(size_t i=0;i<raw_span;i++)assert(((uint8_t*)src.info.guest_address)[i]==0x11);for(size_t i=0;i<raw_span;i++)assert(((uint8_t*)dst.info.guest_address)[i]==0x22);
  printf("ROUNDTRIP case=%s src_tile=%u dst_tile=%u src_bytes=%u dst_bytes=%u subresources=%u raw_mismatch=%zu pixel_mismatch=%zu raw_defined_mismatch=%zu raw_padding_mismatch=%zu guard_mismatch=%zu target_cpu_padding_reads=%zu target_cpu_padding_errors=%zu guest_cpu_stale=1 real_gpu_generated=1 result=%s\n",argv[1],k->source,k->dest,src.info.guest_size,dst.info.guest_size,subresources,differences,pixel_differences,defined_differences,padding_differences,guard_differences,target_padding_reads,target_padding_errors,pass?"PASS":"FAIL");
 }
 munmap(map,0x800000);return pass?0:7;
}
