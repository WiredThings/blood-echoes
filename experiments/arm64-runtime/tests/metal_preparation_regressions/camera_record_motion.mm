// SPDX-License-Identifier: GPL-2.0-or-later
// Project-owned CameraMotion only. Real DSO HostKernels, LocalLibrary, descriptor,
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
#include "video_core/camera_tracker.h"
#include "video_core/renderer_metal/mtl_camera_motion.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
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
using ComputeFn=id<MTLComputePipelineState>(*)(id,SEL,MTLComputePipelineDescriptor*,MTLPipelineOption,MTLAutoreleasedComputePipelineReflection*,NSError*__autoreleasing*);
using SourceFn=id<MTLLibrary>(*)(id,SEL,NSString*,MTLCompileOptions*,NSError*__autoreleasing*);
using DataFn=id<MTLLibrary>(*)(id,SEL,dispatch_data_t,NSError*__autoreleasing*);
ComputeFn original_compute=nullptr;SourceFn original_source=nullptr;DataFn original_data=nullptr;
id<MTLComputePipelineState> ObserveCompute(id self,SEL sel,MTLComputePipelineDescriptor*d,MTLPipelineOption o,MTLAutoreleasedComputePipelineReflection*r,NSError*__autoreleasing*e){
 if(o&MTLPipelineOptionFailOnBinaryArchiveMiss)++strict;else ++ordinary;
 descriptors.push_back(Metal::PsoCache::ArtifactDescriptorIdentity((__bridge void*)d,true));
 return original_compute(self,sel,d,o,r,e);
}
id<MTLLibrary> ObserveSource(id self,SEL sel,NSString*s,MTLCompileOptions*o,NSError*__autoreleasing*e){++sources;return original_source(self,sel,s,o,e);}
id<MTLLibrary> ObserveData(id self,SEL sel,dispatch_data_t d,NSError*__autoreleasing*e){++data_loads;return original_data(self,sel,d,e);}
struct Observer {
 std::array<Method,3> methods{};std::array<IMP,3> old{};
 Observer(id device){
  const std::array<SEL,3> selectors={@selector(newComputePipelineStateWithDescriptor:options:reflection:error:),@selector(newLibraryWithSource:options:error:),@selector(newLibraryWithData:error:)};
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
void Pixels(Metal::Device&device,id<MTLComputePipelineState>pso){
 std::vector<Pixel> depth(16),object(16),initial(16,Pixel{-99,-98,-97,-96});
 for(unsigned i=0;i<16;++i){depth[i]={i%5==0?1.f:float((i%3)+1)*.125f,0,0,0};object[i]={float(i)*.125f,-float(i)*.0625f,i%4==0?1.f:0.f,depth[i][0]};}
 // Include valid, invalid-depth, NaN and infinite object candidates. The original
 // mode-1 depth/finite tests must keep camera motion for every invalid candidate.
 object[4][3]+=.1f;object[8][0]=NAN;object[12][1]=INFINITY;
 auto native=device.GetImpl().device;Image dep=Texture(native,4,4,depth,false),motion=Texture(native,4,4,initial,true),obj=Texture(native,4,4,object,false);
 Check(pso.maxTotalThreadsPerThreadgroup>=64,"original camera 8x8 local group fits pipeline");
 for(unsigned scenario=0;scenario<4;++scenario){
  VideoCore::MotionConstants push{};push.reproject={1,0,0,0,0,1,0,0,0,0,1,0};push.proj={1,1,1,-.5f};push.prev_proj={1,1,1,-.5f};push.size={4,4};push.mode=scenario==3?1:0;
  if(scenario>=1)push.reproject[3]=.25f;
  if(scenario>=2){push.reproject[7]=-.125f;push.jitter={.25f,-.125f};}
  auto cb=[device.GetImpl().queue commandBuffer];auto e=[cb computeCommandEncoder];Check(e,"actual camera encoder");[e setComputePipelineState:pso];[e setTexture:dep.texture atIndex:0];[e setTexture:motion.texture atIndex:1];[e setTexture:obj.texture atIndex:2];std::array<uint8_t,112> metal_push{};static_assert(sizeof(push)==108);std::memcpy(metal_push.data(),&push,sizeof(push));[e setBytes:metal_push.data() length:metal_push.size() atIndex:0];
  [e dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[e endEncoding];[cb commit];[cb waitUntilCompleted];Check(cb.status==MTLCommandBufferStatusCompleted,"actual camera dispatch completed");
  for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x){unsigned i=y*4+x;float d=depth[i][0],uvx=(float(x)+.5f-push.jitter[0])/4.f,uvy=(float(y)+.5f-push.jitter[1])/4.f;bool sky=d>=1;float z=sky?1.f:push.proj[3]/std::min(d-push.proj[2],-1e-8f);float view[4]={(uvx*2-1)*z/push.proj[0],(uvy*2-1)*z/push.proj[1],z,sky?0.f:1.f},prev[3]{};
   for(unsigned r=0;r<3;++r)for(unsigned c=0;c<4;++c)prev[r]+=push.reproject[r*4+c]*view[c];Pixel expected={(prev[0]*push.prev_proj[0]/std::max(prev[2],1e-4f)*.5f+.5f-uvx)*4.f,(prev[1]*push.prev_proj[1]/std::max(prev[2],1e-4f)*.5f+.5f-uvy)*4.f,0,0};
   const auto&o=object[i];if(push.mode==1&&o[2]>.99f&&std::fabs(o[3]-d)<std::max(2e-7f,(push.proj[2]-d)*.01f)&&std::isfinite(o[0])&&std::isfinite(o[1])){expected[0]=o[0];expected[1]=o[1];}
   Pixel actual;std::memcpy(actual.data(),static_cast<const uint8_t*>(motion.buffer.contents)+motion.prefix+y*motion.row+x*sizeof(Pixel),sizeof(Pixel));for(unsigned c=0;c<4;++c)Check(std::isfinite(actual[c])&&std::fabs(actual[c]-expected[c])<=2e-6f,"original camera static/translation/jitter/finite-object pixel oracle");
  }
  Guards(dep,true);Guards(obj,true);Guards(motion,false);
 }
}
void ProductionCamera(Metal::Device&device,Metal::Scheduler&scheduler,Metal::HostKernels&kernels){
 // This path does NOT upload any constants or encode compute itself: only the
 // real CameraMotion constructor/tracker/RecordMotion/Scheduler do so.
 Metal::CameraMotion camera(device,scheduler,kernels);Check(camera.Enabled(),"production CameraMotion enabled with original construction");
 std::vector<Pixel> depths(16),zero(16,Pixel{-90,-91,-92,-93});for(unsigned i=0;i<16;++i)depths[i]={i%5==0?1.f:.25f+float(i%3)*.125f,0,0,0};auto native=device.GetImpl().device;auto dep=Texture(native,4,4,depths,false),out=Texture(native,4,4,zero,true);
 auto feed=[&](float tx){std::array<float,216>scene{};scene[0]=3000;scene[1]=1.f/3000;scene[4]=64;scene[5]=64;const std::array<float,12>view{1,0,0,tx,0,1,0,0,0,0,1,0},inverse{1,0,0,-tx,0,1,0,0,0,0,1,0};std::copy(view.begin(),view.end(),scene.begin()+8);std::copy(inverse.begin(),inverse.end(),scene.begin()+180);scene[52]=1;scene[57]=1;scene[62]=1;scene[63]=-.5f;camera.OnGBufferPass(VideoCore::ImageId{0},1,1);camera.OnConstants(scene.data());};
 feed(0);camera.OnDisplayPass(VideoCore::ImageId{});float previous_tx=0;
 for(unsigned frame=0;frame<3;++frame){const float tx=frame==0?0:.25f;camera.SetJitter(frame==2?std::array<float,2>{.25f,-.125f}:std::array<float,2>{0,0});feed(tx);Check(camera.RenderSize()==std::array<u32,2>{64,64},"production tracker accepted authored 864B scene constants");
  camera.RecordMotion((__bridge const void*)dep.texture,(__bridge const void*)out.texture,4,4);Check(camera.Ready(),"production tracker has both committed cameras and actual depth ID");id<MTLCommandBuffer>cb=scheduler.GetImpl().cmdbuf;Check(cb,"production RecordMotion opened actual scheduler command buffer");scheduler.Finish();[cb waitUntilCompleted];Check(cb.status==MTLCommandBufferStatusCompleted,"production scheduler completed real CameraMotion dispatch");
  for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x){const float depth=depths[y*4+x][0],z=depth>=1?1.f:-.5f/std::min(depth-1.f,-1e-8f);const float expected_x=depth>=1?0:2*(previous_tx-tx)/z;Pixel pixel;std::memcpy(pixel.data(),static_cast<const uint8_t*>(out.buffer.contents)+out.prefix+y*out.row+x*sizeof(Pixel),sizeof(Pixel));Check(std::isfinite(pixel[0])&&std::fabs(pixel[0]-expected_x)<=2e-6f,"production camera static/moving world-to-view pixel oracle");for(unsigned c=1;c<4;++c)Check(std::isfinite(pixel[c])&&std::fabs(pixel[c])<=2e-6f,"production camera Y/Z/W fixed oracle");}
  Guards(dep,true);Guards(out,false);previous_tx=tx;camera.OnDisplayPass(VideoCore::ImageId{});
 }
}

}
int main(int argc,char**argv){@autoreleasepool{
 Check(argc==3,"mode + newly-authored state root");const std::string mode=argv[1];
 const bool record=mode=="record",prepare=mode=="source-prepare"||mode=="local-prepare",hit=mode=="source-hit"||mode=="local-hit"||mode=="local-hit-again",fallback=mode=="source-fallback";
 Check(record||prepare||hit||fallback,"known bounded mode");
 const auto root=std::filesystem::canonical(argv[2]);Check(root.parent_path()=="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state","private Shared root only");Check(std::filesystem::is_regular_file(root/"AUTHOR_CAMERA_V2"),"author fixture marker");
 Check(std::getenv("BB_MTL_REPLAY_THREAD")&&std::string(std::getenv("BB_MTL_REPLAY_THREAD"))=="0","no guest attach/replay thread in standalone worker");
 BbGpuConfig config{};config.serial="AUTHORCAM01";config.title="Authored project CameraMotion pipeline";Core::Emulator::FillElfInfo(config);
 Check(Common::FS::GetUserPath(Common::FS::PathType::CacheDir)==root/"gpu/cache","actual Storage path confined before opening");
 auto&storage=Storage::DataBase::Instance();storage.SetCacheTag("camera-v2-author");storage.Open();
 Metal::Device device;Observer observer(device.GetImpl().device);Metal::Scheduler scheduler(device,"authored CameraMotion");
 Metal::PsoCache pso(device,root/"native");const bool explicit_release=record||prepare;if(explicit_release)pso.ReleaseWarmArchive();Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&pso);
 Check(Metal::LocalAot::Hex(pso.PreparationIdentity())&&Metal::LocalAot::Hex(pso.DiscoveryIdentity()),"verified native and discovery identities");
 Metal::HostPso::WarmResult warm;
 if(prepare){warm=kernels.WarmPipelines();if(warm.state==Metal::HostPso::WarmState::Critical)return BB_ARM_EXIT_HOST_PROTECTION;Check(warm.state==Metal::HostPso::WarmState::Complete&&warm.known==1&&warm.ready==1&&warm.built==1,"real metadata replay publishes exactly one artifact");}
 NSError*error=nil;auto pipeline=kernels.CameraMotionPipeline(&error);Check(pipeline,"actual original CameraMotion factory");const auto&c=pso.GetCounters();
 if(record||fallback)Check(c.artifact_hits==0&&c.artifact_built==0&&ordinary==1&&strict==0,"source discovery/fallback cannot fake a local/native hit");
 if(prepare)Check(c.artifact_built==1&&c.artifact_hits==1&&c.artifact_failed==0&&ordinary==1&&strict==2,"prepared temporary state released then original cold factory strictly adopts");
 if(hit)Check(c.artifact_hits==1&&c.artifact_compile_attempts==0&&ordinary==0&&strict==1,"independent process strict artifact hit with zero ordinary PSO compilation");
 if(mode.starts_with("local"))Check(sources==0&&data_loads>=1,"actual local metallib route, no source API");
 if(fallback)Check(sources==1&&data_loads==1,"actual entry-rejected local library falls back to Source in SAME load build");
 if(mode.starts_with("source")||record)Check(sources>=1,"actual source route");
 Check(!descriptors.empty()&&Metal::LocalAot::Hex(descriptors[0])&&std::all_of(descriptors.begin(),descriptors.end(),[&](const auto&d){return d==descriptors[0];}),"real descriptor identity stable across ordinary/strict creation");
 // The original Upscaler owns its merge slot; this shared factory intentionally
 // has no extra permanent PSO map. Do not fake a hot-owner test by calling it 16x.
 Check(!hit||!explicit_release,"runtime strict test never manually releases main archive");
 Pixels(device,pipeline);ProductionCamera(device,scheduler,kernels);kernels.BindPsoCache(nullptr);
 // Save is asynchronous. Wait only for the first owned metadata publication;
 // never assume Stop/Close itself drained a newly queued request.
 const auto owncache=root/"gpu/cache/AUTHORCAM01-camera-v2-author";
 const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);bool published=false;
 while(std::chrono::steady_clock::now()<until){for(const auto&entry:std::filesystem::directory_iterator(owncache))if(entry.path().extension()==".hostpso")published=true;if(published)break;std::this_thread::sleep_for(std::chrono::milliseconds(2));}
 Check(published,"real async metadata publication before shutdown");storage.Close();
 const auto cache=root/"gpu/cache/AUTHORCAM01-camera-v2-author";std::vector<std::filesystem::path>requests;
 for(const auto&entry:std::filesystem::directory_iterator(cache))if(entry.path().extension()==".hostpso")requests.push_back(entry.path());
 Check(requests.size()==1,"one own discovery request remains across record/load");std::ifstream f(requests[0],std::ios::binary);std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(f),{}};auto request=Metal::HostPso::Decode(bytes);
 Check(request&&request->preparation==pso.DiscoveryIdentity()&&request->invocation.kind==Metal::HostPso::Kind::CameraMotion,"actual Storage request decodes under stable discovery cohort");
 const auto request_sha=Metal::LocalAot::Sha(bytes.data(),bytes.size());Check(requests[0].filename()=="h2_"+pso.DiscoveryIdentity()+"_"+request_sha+".hostpso","actual metadata filename has no native route/build token");
 Check(!std::filesystem::exists(pso.ArchivePath()),"helper never writes/truncates main archive");
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"discovery\":\"%s\",\"preparation\":\"%s\",\"request_sha256\":\"%s\",\"descriptor_sha256\":\"%s\",\"built\":%llu,\"hits\":%llu,\"ordinary\":%zu,\"strict\":%zu,\"source_calls\":%zu,\"data_calls\":%zu,\"explicit_release\":%s,\"pixels\":true,\"production_RecordMotion\":true}\n",mode.c_str(),getpid(),checks,pso.DiscoveryIdentity().c_str(),pso.PreparationIdentity().c_str(),request_sha.c_str(),descriptors[0].c_str(),(unsigned long long)c.artifact_built.load(),(unsigned long long)c.artifact_hits.load(),ordinary,strict,sources,data_loads,explicit_release?"true":"false");
 return 0;
}}
