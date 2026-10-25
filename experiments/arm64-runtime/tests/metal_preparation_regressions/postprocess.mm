// SPDX-License-Identifier: GPL-2.0-or-later
// Project-owned PostProcess only. Real DSO HostKernels, LocalLibrary, descriptor,
// Storage, PsoCache, native compiler/archive and small guarded GPU texture readback; no game.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <thread>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <bit>
#include <filesystem>
#include <fstream>
#include <string>
#include <unistd.h>
#include <vector>
#include "bbgpu.h"
#include "runtime_exit_codes.h"
#include "common/elf_info.h"
#include "common/path_util.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
// Same friend helper definition as production shim/bbgpu.cpp; only author serial.
namespace Core {
class Emulator {
public:
    static void FillElfInfo(const BbGpuConfig& config) {
        auto& info = Common::ElfInfo::Instance();
        info.initialized = true;
        info.game_serial = config.serial ? config.serial : "UNKNOWN";
        info.title = config.title ? config.title : "";
        info.sdk_ver = config.sdk_version;
        info.psf_attributes.raw = config.psf_attributes;
    }
};
}
namespace {
unsigned checks=0;size_t ordinary=0,strict=0,sources=0,data_loads=0;
std::vector<std::string> descriptors;
void Check(bool b,const char*label){++checks;if(!b){std::fprintf(stderr,"FAIL %s\n",label);std::abort();}}
using ComputeFn=id<MTLRenderPipelineState>(*)(id,SEL,MTLRenderPipelineDescriptor*,MTLPipelineOption,MTLAutoreleasedRenderPipelineReflection*,NSError*__autoreleasing*);
using SourceFn=id<MTLLibrary>(*)(id,SEL,NSString*,MTLCompileOptions*,NSError*__autoreleasing*);
using DataFn=id<MTLLibrary>(*)(id,SEL,dispatch_data_t,NSError*__autoreleasing*);
ComputeFn original_compute=nullptr;SourceFn original_source=nullptr;DataFn original_data=nullptr;
id<MTLRenderPipelineState> ObserveCompute(id self,SEL sel,MTLRenderPipelineDescriptor*d,MTLPipelineOption o,MTLAutoreleasedRenderPipelineReflection*r,NSError*__autoreleasing*e){
 if(o&MTLPipelineOptionFailOnBinaryArchiveMiss)++strict;else ++ordinary;
 descriptors.push_back(Metal::PsoCache::ArtifactDescriptorIdentity((__bridge void*)d,false));
 return original_compute(self,sel,d,o,r,e);
}
id<MTLLibrary> ObserveSource(id self,SEL sel,NSString*s,MTLCompileOptions*o,NSError*__autoreleasing*e){++sources;return original_source(self,sel,s,o,e);}
id<MTLLibrary> ObserveData(id self,SEL sel,dispatch_data_t d,NSError*__autoreleasing*e){++data_loads;return original_data(self,sel,d,e);}
struct Observer {
 std::array<Method,3> methods{};std::array<IMP,3> old{};
 Observer(id device){
  const std::array<SEL,3> selectors={@selector(newRenderPipelineStateWithDescriptor:options:reflection:error:),@selector(newLibraryWithSource:options:error:),@selector(newLibraryWithData:error:)};
  const std::array<IMP,3> replacements={reinterpret_cast<IMP>(&ObserveCompute),reinterpret_cast<IMP>(&ObserveSource),reinterpret_cast<IMP>(&ObserveData)};
  auto cls=object_getClass(device);
  for(size_t i=0;i<3;++i){auto inherited=class_getInstanceMethod(cls,selectors[i]);Check(inherited,"real native method exists");old[i]=method_getImplementation(inherited);class_addMethod(cls,selectors[i],old[i],method_getTypeEncoding(inherited));methods[i]=class_getInstanceMethod(cls,selectors[i]);method_setImplementation(methods[i],replacements[i]);}
  original_compute=reinterpret_cast<ComputeFn>(old[0]);original_source=reinterpret_cast<SourceFn>(old[1]);original_data=reinterpret_cast<DataFn>(old[2]);
 }
 ~Observer(){for(size_t i=0;i<3;++i)method_setImplementation(methods[i],old[i]);}
};
using Pixel=std::array<float,4>;
struct Image {id<MTLBuffer>buffer;id<MTLTexture>texture;size_t row{},prefix{},bytes{};uint32_t width{},height{};std::vector<uint8_t>before;};
Image Texture(id<MTLDevice>device,uint32_t w,uint32_t h,const std::vector<Pixel>&pixels,bool writable){
 Image out;out.width=w;out.height=h;const auto alignment=std::max<NSUInteger>(256,[device minimumLinearTextureAlignmentForPixelFormat:MTLPixelFormatRGBA32Float]);out.row=((w*16+alignment-1)/alignment)*alignment;out.prefix=out.row;out.bytes=(h+2)*out.row;Check(out.bytes<=4096,"small explicit guarded image budget");
 out.buffer=[device newBufferWithLength:out.bytes options:MTLResourceStorageModeShared];Check(out.buffer,"owned small image buffer");std::memset(out.buffer.contents,0xa7,out.bytes);
 for(uint32_t y=0;y<h;++y)std::memcpy(static_cast<uint8_t*>(out.buffer.contents)+out.prefix+y*out.row,pixels.data()+y*w,w*sizeof(Pixel));
 auto*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:w height:h mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead|(writable?MTLTextureUsageShaderWrite:0);
 out.texture=[out.buffer newTextureWithDescriptor:d offset:out.prefix bytesPerRow:out.row];Check(out.texture,"real small linear texture");auto*b=static_cast<const uint8_t*>(out.buffer.contents);out.before.assign(b,b+out.bytes);return out;
}
void Guards(const Image&image,bool all){const auto*b=static_cast<const uint8_t*>(image.buffer.contents);for(size_t at=0;at<image.bytes;++at){bool pixel=false;if(at>=image.prefix){const auto relative=at-image.prefix;pixel=relative/image.row<image.height&&relative%image.row<image.width*sizeof(Pixel);}if(all||!pixel)Check(b[at]==image.before[at],"exact outside-view/row-padding guard or read-only input unchanged");}}
void Pixels(Metal::Device&device,id<MTLRenderPipelineState>pso){
 std::vector<Pixel> input(16);for(unsigned i=0;i<16;++i)input[i]={float(i%4)*.2f,float(i/4)*.15f,.01f+float(i)*.04f,.25f+float(i%4)*.125f};
 auto native=device.GetImpl().device;Image source=Texture(native,4,4,input,false);
 auto*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:6 height:6 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget;id<MTLTexture>out=[native newTextureWithDescriptor:d];Check(out,"small actual original BGRA8 render target");
 std::array<uint8_t,6*6*4> seed{};for(size_t i=0;i<seed.size();i+=4){seed[i]=11;seed[i+1]=22;seed[i+2]=33;seed[i+3]=44;}
 const size_t row=256,prefix=256,bytes=8*row;id<MTLBuffer>readback=[native newBufferWithLength:bytes options:MTLResourceStorageModeShared];Check(readback,"small guarded BGRA8 readback");
 auto*sd=[MTLSamplerDescriptor new];sd.minFilter=MTLSamplerMinMagFilterNearest;sd.magFilter=MTLSamplerMinMagFilterNearest;sd.sAddressMode=MTLSamplerAddressModeClampToEdge;sd.tAddressMode=MTLSamplerAddressModeClampToEdge;id<MTLSamplerState>sampler=[native newSamplerStateWithDescriptor:sd];Check(sampler,"original sampled texture sampler interface");
 struct Push{float gamma;uint32_t hdr,srgb;};static_assert(sizeof(Push)==12);
 for(unsigned scenario=0;scenario<3;++scenario){
  [out replaceRegion:MTLRegionMake2D(0,0,6,6) mipmapLevel:0 withBytes:seed.data() bytesPerRow:6*4];std::memset(readback.contents,0xa7,bytes);Push push{1.f,scenario==0?1u:0u,scenario==2?1u:0u};
  auto*pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=out;pass.colorAttachments[0].loadAction=MTLLoadActionLoad;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
  auto cb=[device.GetImpl().queue commandBuffer];auto e=[cb renderCommandEncoderWithDescriptor:pass];Check(e,"actual postprocess render encoder");[e setRenderPipelineState:pso];[e setViewport:MTLViewport{1,1,4,4,0,1}];[e setScissorRect:MTLScissorRect{1,1,4,4}];[e setFragmentTexture:source.texture atIndex:0];[e setFragmentSamplerState:sampler atIndex:0];[e setFragmentBytes:&push length:sizeof(push) atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
  auto blit=[cb blitCommandEncoder];[blit copyFromTexture:out sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(6,6,1) toBuffer:readback destinationOffset:prefix destinationBytesPerRow:row destinationBytesPerImage:6*row];[blit endEncoding];[cb commit];[cb waitUntilCompleted];Check(cb.status==MTLCommandBufferStatusCompleted,"actual postprocess draw+guarded readback completed");
  const auto*b=static_cast<const uint8_t*>(readback.contents);
  for(size_t at=0;at<bytes;++at){bool pixel=at>=prefix&&(at-prefix)/row<6&&(at-prefix)%row<24;if(!pixel)Check(b[at]==0xa7,"readback prefix/suffix/row padding unchanged");}
  for(unsigned y=0;y<6;++y)for(unsigned x=0;x<6;++x){const auto*actual=b+prefix+y*row+x*4;if(x==0||x==5||y==0||y==5){for(unsigned c=0;c<4;++c)Check(actual[c]==seed[(y*6+x)*4+c],"render viewport/scissor exterior unchanged");continue;}
   Pixel wanted=input[(4-y)*4+(x-1)]; // Original FS_TRI Metal viewport has inverted texture Y.
   for(unsigned c=0;c<3;++c){float v=wanted[c];if(!push.hdr){if(push.srgb)v=v<.04044993594f?v/12.92f:std::pow(std::max(v+.055f,0.f)/1.055f,2.4f);v=v<.00313080009f?v*12.92f/push.gamma:std::pow(v,1.f/(3.4f-push.gamma))*1.055f-.055f;}wanted[c]=v;}
   const unsigned order[4]={2,1,0,3};for(unsigned c=0;c<4;++c){const int expected=std::lround(std::clamp(wanted[order[c]],0.f,1.f)*255);Check(std::abs(int(actual[c])-expected)<=1,"original HDR/gamma/sRGB and preserved alpha BGRA pixel oracle");}
  }
  Guards(source,true);
 }
}
}
int main(int argc,char**argv){@autoreleasepool{
 Check(argc==3,"mode + newly-authored state root");const std::string mode=argv[1];
 const bool record=mode=="record",prepare=mode=="source-prepare"||mode=="local-prepare",hit=mode=="source-hit"||mode=="local-hit"||mode=="local-hit-again",fallback=mode=="source-fallback";
 Check(record||prepare||hit||fallback,"known bounded mode");
 const auto root=std::filesystem::canonical(argv[2]);Check(root.parent_path()=="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state","private Shared root only");Check(std::filesystem::is_regular_file(root/"AUTHOR_POST_V2"),"author fixture marker");
 Check(std::getenv("BB_MTL_REPLAY_THREAD")&&std::string(std::getenv("BB_MTL_REPLAY_THREAD"))=="0","no guest attach/replay thread in standalone worker");
 BbGpuConfig config{};config.serial="AUTHORPOST01";config.title="Authored project PostProcess pipeline";Core::Emulator::FillElfInfo(config);
 Check(Common::FS::GetUserPath(Common::FS::PathType::CacheDir)==root/"gpu/cache","actual Storage path confined before opening");
 auto&storage=Storage::DataBase::Instance();storage.SetCacheTag("postprocess-v2-author");storage.Open();
 Metal::Device device;Observer observer(device.GetImpl().device);Metal::Scheduler scheduler(device,"authored PostProcess");
 Metal::PsoCache pso(device,root/"native");const bool explicit_release=record||prepare;if(explicit_release)pso.ReleaseWarmArchive();Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&pso);
 Check(Metal::LocalAot::Hex(pso.PreparationIdentity())&&Metal::LocalAot::Hex(pso.DiscoveryIdentity()),"verified native and discovery identities");
 Metal::HostPso::WarmResult warm;
 if(prepare){warm=kernels.WarmPipelines();if(warm.state==Metal::HostPso::WarmState::Critical)return BB_ARM_EXIT_HOST_PROTECTION;Check(warm.state==Metal::HostPso::WarmState::Complete&&warm.known==1&&warm.ready==1&&warm.built==1,"real metadata replay publishes exactly one artifact");}
 NSError*error=nil;auto pipeline=kernels.PostProcessPipeline(MTLPixelFormatBGRA8Unorm,&error);Check(pipeline,"actual original PostProcess factory");const auto&c=pso.GetCounters();
 if(record||fallback)Check(c.artifact_hits==0&&c.artifact_built==0&&ordinary==1&&strict==0,"source discovery/fallback cannot fake a local/native hit");
 if(prepare)Check(c.artifact_built==1&&c.artifact_hits==1&&c.artifact_failed==0&&ordinary==1&&strict==2,"prepared temporary state released then original cold factory strictly adopts");
 if(hit)Check(c.artifact_hits==1&&c.artifact_compile_attempts==0&&ordinary==0&&strict==1,"independent process strict artifact hit with zero ordinary PSO compilation");
 if(mode.starts_with("local"))Check(sources==0&&data_loads>=2,"actual local metallib route, no source API");
 if(fallback)Check(sources==1&&data_loads==2,"actual entry-rejected local library falls back to Source in SAME load build");
 if((mode.starts_with("source")&&!fallback)||record)Check(sources>=2,"actual source route");
 Check(!descriptors.empty()&&Metal::LocalAot::Hex(descriptors[0])&&std::all_of(descriptors.begin(),descriptors.end(),[&](const auto&d){return d==descriptors[0];}),"real descriptor identity stable across ordinary/strict creation");
 // The original Upscaler owns its merge slot; this shared factory intentionally
 // has no extra permanent PSO map. Do not fake a hot-owner test by calling it 16x.
 Check(!hit||!explicit_release,"runtime strict test never manually releases main archive");
 Pixels(device,pipeline);kernels.BindPsoCache(nullptr);
 // Save is asynchronous. Wait only for the first owned metadata publication;
 // never assume Stop/Close itself drained a newly queued request.
 const auto owncache=root/"gpu/cache/AUTHORPOST01-postprocess-v2-author";
 const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);bool published=false;
 while(std::chrono::steady_clock::now()<until){for(const auto&entry:std::filesystem::directory_iterator(owncache))if(entry.path().extension()==".hostpso")published=true;if(published)break;std::this_thread::sleep_for(std::chrono::milliseconds(2));}
 Check(published,"real async metadata publication before shutdown");storage.Close();
 const auto cache=root/"gpu/cache/AUTHORPOST01-postprocess-v2-author";std::vector<std::filesystem::path>requests;
 for(const auto&entry:std::filesystem::directory_iterator(cache))if(entry.path().extension()==".hostpso")requests.push_back(entry.path());
 Check(requests.size()==1,"one own discovery request remains across record/load");std::ifstream f(requests[0],std::ios::binary);std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(f),{}};auto request=Metal::HostPso::Decode(bytes);
 Check(request&&request->preparation==pso.DiscoveryIdentity()&&request->invocation.kind==Metal::HostPso::Kind::PostProcess,"actual Storage request decodes under stable discovery cohort");
 const auto request_sha=Metal::LocalAot::Sha(bytes.data(),bytes.size());Check(requests[0].filename()=="h2_"+pso.DiscoveryIdentity()+"_"+request_sha+".hostpso","actual metadata filename has no native route/build token");
 Check(!std::filesystem::exists(pso.ArchivePath()),"helper never writes/truncates main archive");
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"discovery\":\"%s\",\"preparation\":\"%s\",\"request_sha256\":\"%s\",\"descriptor_sha256\":\"%s\",\"built\":%llu,\"hits\":%llu,\"ordinary\":%zu,\"strict\":%zu,\"source_calls\":%zu,\"data_calls\":%zu,\"explicit_release\":%s,\"pixels\":true}\n",mode.c_str(),getpid(),checks,pso.DiscoveryIdentity().c_str(),pso.PreparationIdentity().c_str(),request_sha.c_str(),descriptors[0].c_str(),(unsigned long long)c.artifact_built.load(),(unsigned long long)c.artifact_hits.load(),ordinary,strict,sources,data_loads,explicit_release?"true":"false");
 return 0;
}}
