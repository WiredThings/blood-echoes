// SPDX-License-Identifier: GPL-2.0-or-later
// Project-owned FillU32 only. Real DSO HostKernels, LocalLibrary, descriptor,
// Storage, PsoCache, native compiler/archive and 1060-byte GPU readback; no game.
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
void Pixels(Metal::Device& device,id<MTLComputePipelineState> pso){
 constexpr uint32_t count=257,value=0x1234abcd,sentinel=0xcdcdcdcd;std::array<uint32_t,count+8>initial;initial.fill(sentinel);
 auto native=device.GetImpl().device;auto buffer=[native newBufferWithBytes:initial.data() length:sizeof(initial) options:MTLResourceStorageModeShared];Check(buffer,"small authored buffer");
 auto cb=[device.GetImpl().queue commandBuffer];auto encoder=[cb computeCommandEncoder];Check(encoder,"real compute encoder");
 [encoder setComputePipelineState:pso];[encoder setBuffer:buffer offset:0 atIndex:0];[encoder setBytes:&value length:sizeof(value) atIndex:1];[encoder setBytes:&count length:sizeof(count) atIndex:2];
 const auto group=std::min<NSUInteger>(64,pso.maxTotalThreadsPerThreadgroup);Check(group>0,"actual native group bound");
 [encoder dispatchThreads:MTLSizeMake(count+8,1,1) threadsPerThreadgroup:MTLSizeMake(group,1,1)];[encoder endEncoding];[cb commit];[cb waitUntilCompleted];
 Check(cb.status==MTLCommandBufferStatusCompleted,"real GPU completed");const auto*words=static_cast<const uint32_t*>(buffer.contents);
 for(size_t i=0;i<initial.size();++i)Check(words[i]==(i<count?value:sentinel),"FillU32 value and bound readback");
}
}
int main(int argc,char**argv){@autoreleasepool{
 Check(argc==3,"mode + newly-authored state root");const std::string mode=argv[1];
 const bool record=mode=="record",prepare=mode=="source-prepare"||mode=="local-prepare",hit=mode=="source-hit"||mode=="local-hit"||mode=="local-hit-again",fallback=mode=="source-fallback";
 Check(record||prepare||hit||fallback,"known bounded mode");
 const auto root=std::filesystem::canonical(argv[2]);Check(root.parent_path()=="/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state","private Shared root only");Check(std::filesystem::is_regular_file(root/"AUTHOR_HELPER_V2"),"author fixture marker");
 Check(std::getenv("BB_MTL_REPLAY_THREAD")&&std::string(std::getenv("BB_MTL_REPLAY_THREAD"))=="0","no guest attach/replay thread in standalone worker");
 BbGpuConfig config{};config.serial="AUTHORHLP01";config.title="Authored project FillU32 pipeline";Core::Emulator::FillElfInfo(config);
 Check(Common::FS::GetUserPath(Common::FS::PathType::CacheDir)==root/"gpu/cache","actual Storage path confined before opening");
 auto&storage=Storage::DataBase::Instance();storage.SetCacheTag("helper-v2-author");storage.Open();
 Metal::Device device;Observer observer(device.GetImpl().device);Metal::Scheduler scheduler(device,"authored FillU32");
 Metal::PsoCache pso(device,root/"native");pso.ReleaseWarmArchive();Metal::HostKernels kernels(device.GetImpl(),scheduler);kernels.BindPsoCache(&pso);
 Check(Metal::LocalAot::Hex(pso.PreparationIdentity())&&Metal::LocalAot::Hex(pso.DiscoveryIdentity()),"verified native and discovery identities");
 Metal::HostPso::WarmResult warm;
 if(prepare){warm=kernels.WarmPipelines();if(warm.state==Metal::HostPso::WarmState::Critical)return BB_ARM_EXIT_HOST_PROTECTION;Check(warm.state==Metal::HostPso::WarmState::Complete&&warm.known==1&&warm.ready==1&&warm.built==1,"real metadata replay publishes exactly one artifact");}
 auto pipeline=kernels.FillU32();Check(pipeline,"actual original FillU32 factory");const auto&c=pso.GetCounters();
 if(record||fallback)Check(c.artifact_hits==0&&c.artifact_built==0&&ordinary==1&&strict==0,"source discovery/fallback cannot fake a local/native hit");
 if(prepare)Check(c.artifact_built==1&&c.artifact_hits==1&&c.artifact_failed==0&&ordinary==1&&strict==2,"prepared temporary state released then original cold factory strictly adopts");
 if(hit)Check(c.artifact_hits==1&&c.artifact_compile_attempts==0&&ordinary==0&&strict==1,"independent process strict artifact hit with zero ordinary PSO compilation");
 if(mode.starts_with("local"))Check(sources==0&&data_loads>=1,"actual local metallib route, no source API");
 if(fallback)Check(sources==1&&data_loads==1,"actual entry-rejected local library falls back to Source in SAME load build");
 if(mode.starts_with("source")||record)Check(sources>=1,"actual source route");
 Check(!descriptors.empty()&&Metal::LocalAot::Hex(descriptors[0])&&std::all_of(descriptors.begin(),descriptors.end(),[&](const auto&d){return d==descriptors[0];}),"real descriptor identity stable across ordinary/strict creation");
 const auto calls=ordinary+strict;const auto attempts=c.artifact_attempts.load();const auto source_before=sources,data_before=data_loads;
 for(int i=0;i<16;++i)Check(kernels.FillU32()==pipeline,"original active helper map hit");Check(ordinary+strict==calls&&c.artifact_attempts==attempts&&sources==source_before&&data_loads==data_before,"warm helper hits perform no artifact lookup/library/native work");
 Pixels(device,pipeline);kernels.BindPsoCache(nullptr);
 // Save is asynchronous. Wait only for the first owned metadata publication;
 // never assume Stop/Close itself drained a newly queued request.
 const auto owncache=root/"gpu/cache/AUTHORHLP01-helper-v2-author";
 const auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);bool published=false;
 while(std::chrono::steady_clock::now()<until){for(const auto&entry:std::filesystem::directory_iterator(owncache))if(entry.path().extension()==".hostpso")published=true;if(published)break;std::this_thread::sleep_for(std::chrono::milliseconds(2));}
 Check(published,"real async metadata publication before shutdown");storage.Close();
 const auto cache=root/"gpu/cache/AUTHORHLP01-helper-v2-author";std::vector<std::filesystem::path>requests;
 for(const auto&entry:std::filesystem::directory_iterator(cache))if(entry.path().extension()==".hostpso")requests.push_back(entry.path());
 Check(requests.size()==1,"one own discovery request remains across record/load");std::ifstream f(requests[0],std::ios::binary);std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(f),{}};auto request=Metal::HostPso::Decode(bytes);
 Check(request&&request->preparation==pso.DiscoveryIdentity()&&request->invocation.kind==Metal::HostPso::Kind::Fill,"actual Storage request decodes under stable discovery cohort");
 const auto request_sha=Metal::LocalAot::Sha(bytes.data(),bytes.size());Check(requests[0].filename()=="h2_"+pso.DiscoveryIdentity()+"_"+request_sha+".hostpso","actual metadata filename has no native route/build token");
 Check(!std::filesystem::exists(pso.ArchivePath()),"helper never writes/truncates main archive");
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"discovery\":\"%s\",\"preparation\":\"%s\",\"request_sha256\":\"%s\",\"descriptor_sha256\":\"%s\",\"built\":%llu,\"hits\":%llu,\"ordinary\":%zu,\"strict\":%zu,\"source_calls\":%zu,\"data_calls\":%zu,\"pixels\":true}\n",mode.c_str(),getpid(),checks,pso.DiscoveryIdentity().c_str(),pso.PreparationIdentity().c_str(),request_sha.c_str(),descriptors[0].c_str(),(unsigned long long)c.artifact_built.load(),(unsigned long long)c.artifact_hits.load(),ordinary,strict,sources,data_loads);
 return 0;
}}
