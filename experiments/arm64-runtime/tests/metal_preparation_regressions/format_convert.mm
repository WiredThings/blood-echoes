// SPDX-License-Identifier: GPL-2.0-or-later
// Project-owned original FormatConvert only. Real DSO HostKernels, LocalLibrary, descriptor,
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
#include "video_core/renderer_metal/mtl_format_convert_impl.h"
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
bool negative_gate=false;unsigned checks=0;size_t ordinary=0,strict=0,sources=0,data_loads=0;
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
id<MTLLibrary> ObserveData(id self,SEL sel,dispatch_data_t d,NSError*__autoreleasing*e){++data_loads;auto library=original_data(self,sel,d,e);if(negative_gate&&library){Check([library newFunctionWithName:@"rgb_convert"]!=nil,"negative authored local library contains selected RGB entry");Check([library newFunctionWithName:@"depth_convert"]==nil,"negative authored local library lacks OTHER required depth entry");}return library;}
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
struct Buffer {id<MTLBuffer>native;std::vector<uint8_t>before;};
Buffer NewBuffer(id<MTLDevice>device,size_t bytes,uint8_t fill){Check(bytes<=8192,"small explicit author buffer budget");Buffer b;b.native=[device newBufferWithLength:bytes options:MTLResourceStorageModeShared];Check(b.native,"real guarded format buffer");std::memset(b.native.contents,fill,bytes);b.before.assign(bytes,fill);return b;}
uint8_t* Data(Buffer&b){return static_cast<uint8_t*>(b.native.contents);}
void Exact(const Buffer&b,const std::vector<uint8_t>&expected,const char*why){Check(b.native.length==expected.size(),"exact buffer byte count");auto*p=static_cast<const uint8_t*>(b.native.contents);for(size_t i=0;i<expected.size();++i)Check(p[i]==expected[i],why);}
void Put(std::vector<uint8_t>&b,size_t off,uint32_t value,unsigned n){for(unsigned c=0;c<n;++c)b[off+c]=uint8_t(value>>(8*c));}
void Dispatch(Metal::Device&device,Metal::FormatConvert::Converter&converter,const Metal::FormatConvert::Conversion&conversion,bool to_host,Buffer&guest,Buffer&host,const Metal::FormatConvert::Converter::Region&r,uint32_t row,uint32_t slice){
 auto cb=[device.GetImpl().queue commandBuffer];auto e=[cb computeCommandEncoder];Check(e,"actual converter compute encoder");
 converter.Encode(e,conversion,to_host,{guest.native,7,row,slice},{host.native,13},r);
 [e endEncoding];[cb commit];[cb waitUntilCompleted];Check(cb.status==MTLCommandBufferStatusCompleted,"actual Converter::Encode GPU completed");
}
std::array<size_t,4> NativeCounts(){return {ordinary,strict,sources,data_loads};}
void Pixels(Metal::Device&device,Metal::HostKernels&kernels,Metal::PsoCache&pso){
 namespace F=Metal::FormatConvert;F::Converter converter(kernels);const F::Converter::Region region{5,3,2};constexpr unsigned W=5,H=3,S=2;auto native=device.GetImpl().device;
 struct RgbCase {vk::Format guest,host;unsigned n;uint32_t one;};
 const std::array<RgbCase,9>cases{{
  {vk::Format::eR8G8B8Unorm,vk::Format::eR8G8B8A8Unorm,1,0xff},
  {vk::Format::eR8G8B8Snorm,vk::Format::eR8G8B8A8Snorm,1,0x7f},
  {vk::Format::eR8G8B8Uint,vk::Format::eR8G8B8A8Uint,1,1},
  {vk::Format::eR16G16B16Sfloat,vk::Format::eR16G16B16A16Sfloat,2,0x3c00},
  {vk::Format::eR16G16B16Unorm,vk::Format::eR16G16B16A16Unorm,2,0xffff},
  {vk::Format::eR16G16B16Snorm,vk::Format::eR16G16B16A16Snorm,2,0x7fff},
  {vk::Format::eR16G16B16Sint,vk::Format::eR16G16B16A16Sint,2,1},
  {vk::Format::eR32G32B32Sfloat,vk::Format::eR32G32B32A32Sfloat,4,0x3f800000},
  {vk::Format::eR32G32B32Uint,vk::Format::eR32G32B32A32Uint,4,1}}};
 std::array<size_t,4> rgb_hot{},both_hot{};uint64_t rgb_attempts=0,both_attempts=0;unsigned case_number=0;
 auto rgb=[&](const RgbCase&c,bool first){
  const auto conversion=F::Describe(c.guest,c.host,vk::ImageAspectFlagBits::eColor);Check(conversion.kind==F::Kind::RgbPad&&conversion.comp_bytes==c.n&&conversion.one==c.one&&conversion.guest_texel_bytes==3*c.n&&conversion.host_texel_bytes==4*c.n,"actual Describe RGB format and one bits");
  const uint32_t row=W*3*c.n+7,slice=row*H+11;auto guest=NewBuffer(native,7+slice*S+31,0xa7),host=NewBuffer(native,13+W*4*c.n*H*S+29,0x3a);auto expected_guest=guest.before,expected_host=host.before;
  for(unsigned z=0;z<S;++z)for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const size_t g=7+z*slice+y*row+x*3*c.n,h=13+(z*H*W+y*W+x)*4*c.n;for(unsigned k=0;k<3*c.n;++k)expected_host[h+k]=expected_guest[g+k]=uint8_t((x*37+y*51+z*71+k*13+case_number*11)&255);Put(expected_host,h+3*c.n,c.one,c.n);}
  std::memcpy(Data(guest),expected_guest.data(),expected_guest.size());Dispatch(device,converter,conversion,true,guest,host,region,row,slice);Exact(guest,expected_guest,"RGB upload guest all bytes unchanged");Exact(host,expected_host,"RGB upload exact components/alpha and offset/slice/row guards");
  if(first){rgb_hot=NativeCounts();rgb_attempts=pso.GetCounters().artifact_attempts.load();}else{Check(NativeCounts()==(both_attempts?both_hot:rgb_hot),"RGB existing owner slot avoids every library/native compile/lookup");Check(pso.GetCounters().artifact_attempts.load()==(both_attempts?both_attempts:rgb_attempts),"RGB slot hit has no artifact lookup");}
  std::fill(expected_guest.begin(),expected_guest.end(),0xb6);std::memset(Data(guest),0xb6,expected_guest.size());for(unsigned z=0;z<S;++z)for(unsigned y=0;y<H;++y)for(unsigned x=0;x<W;++x){const size_t g=7+z*slice+y*row+x*3*c.n,h=13+(z*H*W+y*W+x)*4*c.n;for(unsigned k=0;k<3*c.n;++k)expected_guest[g+k]=expected_host[h+k];}
  Dispatch(device,converter,conversion,false,guest,host,region,row,slice);Exact(guest,expected_guest,"RGB download drops alpha and keeps all padding/guards");Exact(host,expected_host,"RGB download host bytes unchanged");Check(NativeCounts()==(both_attempts?both_hot:rgb_hot),"RGB download uses same original slot");++case_number;
 };
 for(unsigned i=0;i<cases.size();++i)rgb(cases[i],i==0);
 for(unsigned depth_case=0;depth_case<3;++depth_case){
  const unsigned bits=depth_case==0?16:24,n=bits==16?2:4;const uint32_t maxcode=(1u<<bits)-1;const auto guest_format=depth_case==0?vk::Format::eD16UnormS8Uint:depth_case==1?vk::Format::eD24UnormS8Uint:vk::Format::eX8D24UnormPack32;
  auto conversion=F::Describe(guest_format,depth_case==2?vk::Format::eD32Sfloat:vk::Format::eD32SfloatS8Uint,vk::ImageAspectFlagBits::eDepth);Check(conversion.kind==F::Kind::DepthUnorm&&conversion.depth_bits==bits&&conversion.comp_bytes==n&&conversion.host_texel_bytes==4,"actual Describe depth aspect to Depth32Float host bytes");
  const uint32_t row=W*n+7,slice=row*H+11;auto guest=NewBuffer(native,7+slice*S+31,0xa7),host=NewBuffer(native,13+W*4*H*S+29,0x3a);auto expected_guest=guest.before,expected_host=host.before;std::array<uint32_t,W*H*S>codes{};
  for(unsigned i=0;i<codes.size();++i){codes[i]=i%6==0?0:i%6==1?1:i%6==2?maxcode:i%6==3?maxcode-1:i%6==4?maxcode/2:(i*149913u)&maxcode;unsigned z=i/(W*H),y=(i/W)%H,x=i%W;const size_t g=7+z*slice+y*row+x*n;Put(expected_guest,g,codes[i]|(n==4?0xa5000000u:0),n);const float value=float(codes[i])/float(maxcode);Put(expected_host,13+i*4,std::bit_cast<uint32_t>(value),4);}
  std::memcpy(Data(guest),expected_guest.data(),expected_guest.size());Dispatch(device,converter,conversion,true,guest,host,region,row,slice);Exact(guest,expected_guest,"depth upload guest code and unused top bytes unchanged");Exact(host,expected_host,"depth upload correctly rounded float32 bit oracle and padding guards");
  if(depth_case==0){both_hot=NativeCounts();both_attempts=pso.GetCounters().artifact_attempts.load();}else{Check(NativeCounts()==both_hot,"depth slot reused for 16/24/X8D24 formats");Check(pso.GetCounters().artifact_attempts.load()==both_attempts,"depth slot hit has no artifact lookup");}
  std::fill(expected_guest.begin(),expected_guest.end(),0xb6);std::memset(Data(guest),0xb6,expected_guest.size());for(unsigned i=0;i<codes.size();++i){unsigned z=i/(W*H),y=(i/W)%H,x=i%W;Put(expected_guest,7+z*slice+y*row+x*n,codes[i],n);}
  Dispatch(device,converter,conversion,false,guest,host,region,row,slice);Exact(guest,expected_guest,"depth roundtrip exact code, D24 top byte zero and guards");Exact(host,expected_host,"depth download host unchanged");Check(NativeCounts()==both_hot,"depth reverse direction same original pipeline slot");
  // Original Depth32Float download handles finite out-of-range values/NaN/infinity.
  const std::array<float,8>special{NAN,INFINITY,-INFINITY,-.5f,0.f,.5f,1.f,1.5f};std::fill(expected_guest.begin(),expected_guest.end(),0xb6);std::memset(Data(guest),0xb6,expected_guest.size());
  for(unsigned i=0;i<codes.size();++i){float f=special[i%special.size()];Put(expected_host,13+i*4,std::bit_cast<uint32_t>(f),4);const float v=std::isnan(f)?0.f:std::clamp(f,0.f,1.f);const uint32_t code=uint32_t(std::clamp(int(std::nearbyint(v*float(maxcode))),0,int(maxcode)));unsigned z=i/(W*H),y=(i/W)%H,x=i%W;Put(expected_guest,7+z*slice+y*row+x*n,code,n);}
  std::memcpy(Data(host),expected_host.data(),expected_host.size());Dispatch(device,converter,conversion,false,guest,host,region,row,slice);Exact(guest,expected_guest,"original NaN/clamp/rint depth download byte oracle and guards");Exact(host,expected_host,"special float download host unchanged");Check(NativeCounts()==both_hot,"special float path does not create another PSO");
 }
 rgb(cases[0],false);Check(NativeCounts()==both_hot&&pso.GetCounters().artifact_attempts.load()==both_attempts,"interleaved RGB after depth keeps both original slots and zero lookup");
}

}
int main(int argc,char**argv){@autoreleasepool{
 Check(argc==3,"mode + newly-authored state root");const std::string mode=argv[1];
 const bool record=mode=="record",prepare=mode=="source-prepare"||mode=="local-prepare",hit=mode=="source-hit"||mode=="local-hit"||mode=="local-hit-again",fallback=mode=="source-fallback";Check(record||prepare||hit||fallback,"known bounded mode");negative_gate=fallback;
 const auto root=std::filesystem::canonical(argv[2]);Check(root.parent_path()=="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state","private Shared root only");Check(std::filesystem::is_regular_file(root/"AUTHOR_FORMAT_V2"),"author fixture marker");
 Check(std::getenv("BB_MTL_REPLAY_THREAD")&&std::string(std::getenv("BB_MTL_REPLAY_THREAD"))=="0","no guest attach/replay in standalone worker");BbGpuConfig config{};config.serial="AUTHORFMT01";config.title="Authored project Format conversion pipelines";Core::Emulator::FillElfInfo(config);Check(Common::FS::GetUserPath(Common::FS::PathType::CacheDir)==root/"gpu/cache","actual Storage confined before opening");
 auto&storage=Storage::DataBase::Instance();storage.SetCacheTag("format-v2-author");storage.Open();Metal::Device device;Observer observer(device.GetImpl().device);Metal::Scheduler scheduler(device,"authored format conversion");Metal::PsoCache pso(device,root/"native");const bool explicit_release=record||prepare;if(explicit_release)pso.ReleaseWarmArchive();Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&pso);Check(Metal::LocalAot::Hex(pso.PreparationIdentity())&&Metal::LocalAot::Hex(pso.DiscoveryIdentity()),"verified native and discovery identities");
 Metal::HostPso::WarmResult warm;if(prepare){warm=kernels.WarmPipelines();if(warm.state==Metal::HostPso::WarmState::Critical)return BB_ARM_EXIT_HOST_PROTECTION;Check(warm.state==Metal::HostPso::WarmState::Complete&&warm.known==2&&warm.ready==2&&warm.built==2,"both real fixed requests publish independent artifacts");}
 NSError*error=nil;auto rgb=kernels.FormatConversionPipeline(false,&error);Check(rgb,"real RGB format factory");auto depth=kernels.FormatConversionPipeline(true,&error);Check(depth,"real depth format factory");const auto&c=pso.GetCounters();
 if(record||fallback)Check(c.artifact_hits==0&&c.artifact_built==0&&ordinary==2&&strict==0,"discovery/fallback cannot fake native artifact hit");if(prepare)Check(c.artifact_built==2&&c.artifact_hits==2&&c.artifact_failed==0&&ordinary==2&&strict==4,"temporary prepared states released and both cold factories strictly adopt");if(hit)Check(c.artifact_hits==2&&c.artifact_compile_attempts==0&&ordinary==0&&strict==2,"independent worker strictly adopts both artifacts without ordinary compile");
 Pixels(device,kernels,pso);
 if(record||fallback)Check(c.artifact_hits==0&&ordinary==4&&strict==0,"real Converter owns both states after exactly two cold slot misses");if(prepare)Check(c.artifact_built==2&&c.artifact_hits==4&&ordinary==2&&strict==6,"both production Converter slot misses adopt prepared artifacts");if(hit)Check(c.artifact_hits==4&&c.artifact_compile_attempts==0&&ordinary==0&&strict==4,"both production Converter slots strict hit and hot Encode zero further lookup");if(mode.starts_with("local"))Check(sources==0&&data_loads>=4,"actual local library route only");if(fallback)Check(sources==4&&data_loads==4,"library with rgb but no depth is rejected by every original dual-entry gate, including RGB");if((mode.starts_with("source")&&!fallback)||record)Check(sources>=4,"actual source route");Check(!descriptors.empty()&&Metal::LocalAot::Hex(descriptors[0])&&std::all_of(descriptors.begin(),descriptors.end(),[&](const auto&s){return s==descriptors[0];}),"both original compute descriptors have identical canonical default fields");Check(!hit||!explicit_release,"runtime hit path does not manually release main archive");kernels.BindPsoCache(nullptr);
 const auto owncache=root/"gpu/cache/AUTHORFMT01-format-v2-author";const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);size_t saved=0;while(std::chrono::steady_clock::now()<until){saved=0;for(const auto&e:std::filesystem::directory_iterator(owncache))if(e.path().extension()==".hostpso")++saved;if(saved==2)break;std::this_thread::sleep_for(std::chrono::milliseconds(2));}Check(saved==2,"both real async metadata publications complete before close");storage.Close();std::vector<std::filesystem::path>requests;for(const auto&e:std::filesystem::directory_iterator(owncache))if(e.path().extension()==".hostpso")requests.push_back(e.path());Check(requests.size()==2,"exactly two stable discovery records across source/load and both slots");std::vector<std::string>request_shas;unsigned kinds=0;
 for(const auto&path:requests){std::ifstream f(path,std::ios::binary);std::vector<uint8_t>bytes{std::istreambuf_iterator<char>(f),{}};auto request=Metal::HostPso::Decode(bytes);Check(request&&request->preparation==pso.DiscoveryIdentity()&&request->constants.empty(),"real Storage decoded fixed format request");if(request->invocation.kind==Metal::HostPso::Kind::FormatRgb){Check(request->entry0=="rgb_convert","real RGB request entry");kinds|=1;}else{Check(request->invocation.kind==Metal::HostPso::Kind::FormatDepth&&request->entry0=="depth_convert","real depth request entry");kinds|=2;}const auto sha=Metal::LocalAot::Sha(bytes.data(),bytes.size());Check(path.filename()=="h2_"+pso.DiscoveryIdentity()+"_"+sha+".hostpso","request identity excludes actual source/local route");request_shas.push_back(sha);}
 Check(kinds==3,"both actual request kinds present");std::sort(request_shas.begin(),request_shas.end());Check(!std::filesystem::exists(pso.ArchivePath()),"format preparation never writes/truncates main archive");
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"discovery\":\"%s\",\"preparation\":\"%s\",\"request_sha256\":[\"%s\",\"%s\"],\"descriptor_sha256\":\"%s\",\"built\":%llu,\"hits\":%llu,\"ordinary\":%zu,\"strict\":%zu,\"source_calls\":%zu,\"data_calls\":%zu,\"explicit_release\":%s,\"pixels\":true,\"production_Converter_Encode\":true,\"both_original_slots_reused\":true}\n",mode.c_str(),getpid(),checks,pso.DiscoveryIdentity().c_str(),pso.PreparationIdentity().c_str(),request_shas[0].c_str(),request_shas[1].c_str(),descriptors[0].c_str(),(unsigned long long)c.artifact_built.load(),(unsigned long long)c.artifact_hits.load(),ordinary,strict,sources,data_loads,explicit_release?"true":"false");return 0;
}}
