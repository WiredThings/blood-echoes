// SPDX-License-Identifier: GPL-2.0-or-later
// Entirely authored shaders/storage. Actual PipelineCache, WarmUp, serialization,
// SPIRV-Cross, native factories, binary archive and GPU readback are not mocked.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <array>
#include <algorithm>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <thread>
#include <unordered_map>
#include <unistd.h>
#include "bbgpu.h"
#include "common/elf_info.h"
#include "common/path_util.h"
#include "common/serdes.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/specialization.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"
#include "test_gpu_runtime_stubs.cpp"

namespace Core {
class Emulator {
public:
 static void FillElfInfo(const BbGpuConfig& config) {
  auto& info=Common::ElfInfo::Instance();info.initialized=true;
  info.game_serial=config.serial;info.title="Authored derived native PSO controls";
 }
};
}
namespace VideoCore {
struct PipelineCacheCoreTestAccess {
 static const Metal::GraphicsPipeline* Find(PipelineCacheCore& cache,
                                           const GraphicsPipelineKey& key) {
  auto it=cache.graphics_pipelines.find(key);
  if(it==cache.graphics_pipelines.end())return nullptr;
  return dynamic_cast<const Metal::GraphicsPipeline*>(it.value().get());
 }
 static size_t Size(PipelineCacheCore& cache){return cache.graphics_pipelines.size();}
};
// Transparent call-site observer: forwards every observed call to the real DSO
// RegisterPipelineData and real disk Save; no in-memory Storage substitute.
static unsigned observed_registers=0;
void AuthorObservedRegisterPipelineData(const GraphicsPipelineKey& key,u64 hash,
                                       const PipelineSerialization& data) {
 ++observed_registers;RegisterPipelineData(key,hash,data);
}
}

namespace {
using Key=VideoCore::GraphicsPipelineKey;
using Metal::GraphicsSerialization;
constexpr u64 VS=0x71504101,FS=0x71504102,VS_META=0x71504201,FS_META=0x71504202;
constexpr u64 INJECTED_META=0x71504999;
std::filesystem::path storage;
unsigned checks=0;
void Check(bool value,const char* label) {
 ++checks;if(!value){std::fprintf(stderr,"FAIL %s\n",label);std::abort();}
}
std::vector<u8> Bytes(const std::filesystem::path& p) {
 std::ifstream f(p,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};
}
void Wait(const std::filesystem::path& p) {
 auto until=std::chrono::steady_clock::now()+std::chrono::seconds(5);
 while(!std::filesystem::exists(p)&&std::chrono::steady_clock::now()<until)
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
 Check(std::filesystem::is_regular_file(p),"real async Storage publication");
}
std::filesystem::path KeyPath(const Key& k) {
 return storage/fmt::format("g_{:#018x}.key",std::hash<Key>{}(k));
}
std::vector<u32> Spirv(const std::filesystem::path& p) {
 auto bytes=Bytes(p);Check(bytes.size()%4==0&&bytes.size()>=20,"authored SPIR-V shape");
 std::vector<u32> v(bytes.size()/4);std::memcpy(v.data(),bytes.data(),bytes.size());return v;
}
void Meta(u64 hash,u64 meta,Shader::HwStage hw,Shader::SwStage sw,bool injected=false) {
 Shader::Info info{};info.pgm_hash=hash;info.hw_stage=hw;info.sw_stage=sw;
 Shader::StageSpecialization spec{};spec.info=&info;spec.runtime_info.Initialize(hw,sw);
 if(hw==Shader::HwStage::Vertex)spec.runtime_info.hw.vs.motion_vectors=injected;
 VideoCore::RegisterShaderMeta(info,{},spec,meta,0);
 Wait(storage/fmt::format("{:#018x}.meta",meta));
}
Key BaseKey() {
 Key k{};k.stage_hashes[u32(Shader::SwStage::Vertex)]=VS_META;
 k.stage_hashes[u32(Shader::SwStage::Fragment)]=FS_META;
 k.prim_type=AmdGpu::PrimitiveType::TriangleList;k.num_samples=1;
 k.num_color_attachments=1;k.mrt_mask=1;
 k.z_format=AmdGpu::DepthBuffer::ZFormat::Z32Float;
 k.stencil_format=AmdGpu::DepthBuffer::StencilFormat::Stencil8;
 k.color_buffers[0].data_format=AmdGpu::DataFormat::Format8_8_8_8;
 k.color_buffers[0].num_format=AmdGpu::NumberFormat::Unorm;
 k.write_masks[0]=vk::ColorComponentFlagBits::eR|vk::ColorComponentFlagBits::eG|
                  vk::ColorComponentFlagBits::eB|vk::ColorComponentFlagBits::eA;
 // Formats are indexed by attribute-order, locations by serialized vertex metadata.
 k.vertex_buffer_formats[0]=vk::Format::eR32G32Sfloat;
 k.vertex_buffer_formats[1]=vk::Format::eR32G32B32A32Sfloat;
 k.logic_op=AmdGpu::ColorControl::LogicOp::Copy;return k;
}
Key NoDepthKey(){auto k=BaseKey();k.z_format=AmdGpu::DepthBuffer::ZFormat::Invalid;
 k.stencil_format=AmdGpu::DepthBuffer::StencilFormat::Invalid;return k;}
Key VelocityKey(){auto k=BaseKey();k.color_buffers[0].data_format=AmdGpu::DataFormat::Format16_16_16_16;
 k.color_buffers[0].num_format=AmdGpu::NumberFormat::Float;return k;}
Metal::GraphicsSerialization Layout(){Metal::GraphicsSerialization d;
 d.vertex_attributes={{2,false,1},{7,true,3}};return d;}
bool LoadMemoryKey(Metal::PipelineCache& cache,const Key& key) {
 // Actual LoadGraphicsPipeline entry after its version/kind header, with real
 // metadata and SPIR-V from disk; no mock backend factory.
 Serialization::Archive written;key.Serialize(written);Layout().Serialize(written);
 Serialization::Archive reading{written.TakeOff()};return cache.LoadGraphicsPipeline(reading);
}

// Bounded, process-private observation of the descriptor actually supplied to
// Apple's factory. The original factory is always invoked with identical args.
struct Observed {
 MTLPixelFormat color{},depth{},stencil{};NSUInteger samples{},options{};
 bool position{},velocity{},success{};
};
std::array<Observed,24> observed{};size_t native_calls=0;
using Factory=id<MTLRenderPipelineState>(*)(id,SEL,MTLRenderPipelineDescriptor*,
   MTLPipelineOption,MTLAutoreleasedRenderPipelineReflection*,NSError*__autoreleasing*);
Factory original_factory=nullptr;
id<MTLRenderPipelineState> ObserveFactory(id self,SEL sel,MTLRenderPipelineDescriptor* d,
 MTLPipelineOption options,MTLAutoreleasedRenderPipelineReflection* reflection,
 NSError*__autoreleasing* error) {
 const bool authored=[d.label isEqualToString:@"vs 0x71504201 fs 0x71504202"];
 Observed* out=nullptr;
 if(authored) {
  Check(native_calls<observed.size(),"bounded native descriptor observer");
  out=&observed[native_calls++];out->color=d.colorAttachments[0].pixelFormat;
  out->depth=d.depthAttachmentPixelFormat;out->stencil=d.stencilAttachmentPixelFormat;
  out->samples=d.rasterSampleCount;out->options=options;
  auto v=d.vertexDescriptor;
  out->position=v&&v.attributes[2].format==MTLVertexFormatFloat2&&
      v.attributes[2].bufferIndex==30&&v.layouts[30].stepFunction==MTLVertexStepFunctionPerVertex&&
      v.layouts[30].stepRate==1&&v.layouts[30].stride==MTLBufferLayoutStrideDynamic;
  out->velocity=v&&v.attributes[7].format==MTLVertexFormatFloat4&&
      v.attributes[7].bufferIndex==29&&v.layouts[29].stepFunction==MTLVertexStepFunctionPerInstance&&
      v.layouts[29].stepRate==3&&v.layouts[29].stride==MTLBufferLayoutStrideDynamic;
 }
 auto p=original_factory(self,sel,d,options,reflection,error);if(out)out->success=p!=nil;return p;
}
struct FactoryObserver {
 Method method;IMP previous;
 explicit FactoryObserver(id<MTLDevice> device) {
  auto cls=object_getClass(device);
  auto sel=@selector(newRenderPipelineStateWithDescriptor:options:reflection:error:);
  auto inherited=class_getInstanceMethod(cls,sel);Check(inherited,"native factory exists");
  previous=method_getImplementation(inherited);original_factory=reinterpret_cast<Factory>(previous);
  class_addMethod(cls,sel,previous,method_getTypeEncoding(inherited));
  method=class_getInstanceMethod(cls,sel);
  method_setImplementation(method,reinterpret_cast<IMP>(&ObserveFactory));
 }
 ~FactoryObserver(){method_setImplementation(method,previous);original_factory=nullptr;}
};

struct Variants {
 Metal::PipelineCache& cache;
 std::unordered_map<const Metal::RenderPipeline*,const Metal::RenderPipeline*> no_depth,velocity;
 const Metal::RenderPipeline* NoDepth(const Metal::GraphicsPipeline& pipeline,
                                    bool force_failure=false,bool force_motion=false) {
  const auto& r=pipeline.Render();auto[it,inserted]=no_depth.try_emplace(&r,nullptr);
  if(inserted) {
   auto key=r.key;key.z_format=AmdGpu::DepthBuffer::ZFormat::Invalid;
   key.stencil_format=AmdGpu::DepthBuffer::StencilFormat::Invalid;
   if(force_failure)key.vertex_buffer_formats[0]=vk::Format::eUndefined;
   if(force_motion)key.motion_vectors=1;
   const Metal::RenderPipelineInput input{.vertex_attributes=pipeline.VertexAttributes(),
    .rect_list_triangles=r.rect_list&&!r.primitive_emulated,
    .tessellation_eval=r.tessellation,.geometry=r.geometry,
    .geometry_output=r.primitive_type==MTLPrimitiveTypeLineStrip?
       Metal::GsOutputPrimitive::LineStrip:Metal::GsOutputPrimitive::TriangleStrip};
   it->second=cache.GetPsoCache().GetRenderPipeline(key,r.vs,r.fs,input);
#define RegisterPipelineData AuthorObservedRegisterPipelineData
#include "generated/no_depth_success.inc"
#undef RegisterPipelineData
  }
  return it->second;
 }
 const Metal::RenderPipeline* Velocity(const Metal::GraphicsPipeline& pipeline) {
  const auto& r=pipeline.Render();auto[entry,added]=velocity.try_emplace(&pipeline.Render(),nullptr);
  if(added) {
   auto key=r.key;key.color_buffers[0].data_format=AmdGpu::DataFormat::Format16_16_16_16;
   key.color_buffers[0].num_format=AmdGpu::NumberFormat::Float;
   const Metal::RenderPipelineInput input{.vertex_attributes=pipeline.VertexAttributes(),
     .rect_list_triangles=r.rect_list&&!r.primitive_emulated};
   entry->second=cache.GetPsoCache().GetRenderPipeline(key,r.vs,r.fs,input);
#define RegisterPipelineData AuthorObservedRegisterPipelineData
#include "generated/velocity_success.inc"
#undef RegisterPipelineData
  }
  return entry->second;
 }
};

std::vector<u8> Draw(Metal::Device& device,const Metal::RenderPipeline& p,bool half) {
 auto native=device.GetImpl().device;
 auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
  half?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
 auto color=[native newTextureWithDescriptor:td];Check(color,"native color allocation");
 auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=color;
 pass.colorAttachments[0].loadAction=MTLLoadActionClear;
 pass.colorAttachments[0].storeAction=MTLStoreActionStore;
 id<MTLTexture> depth=nil;
 if(p.depth_pixel_format||p.stencil_pixel_format) {
  auto dt=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:
    MTLPixelFormatDepth32Float_Stencil8 width:8 height:8 mipmapped:NO];
  dt.storageMode=MTLStorageModePrivate;dt.usage=MTLTextureUsageRenderTarget;
  depth=[native newTextureWithDescriptor:dt];Check(depth,"native depth/stencil allocation");
  pass.depthAttachment.texture=depth;pass.stencilAttachment.texture=depth;
  pass.depthAttachment.loadAction=MTLLoadActionClear;pass.stencilAttachment.loadAction=MTLLoadActionClear;
  pass.depthAttachment.storeAction=MTLStoreActionDontCare;pass.stencilAttachment.storeAction=MTLStoreActionDontCare;
  pass.depthAttachment.clearDepth=1;
 }
 const float vertices[]={-1,-1,3,-1,-1,3};
 const float value[]={-.375f,.15625f,.5625f,1.f};
 auto positions=[native newBufferWithBytes:vertices length:sizeof(vertices) options:MTLResourceStorageModeShared];
 auto velocities=[native newBufferWithBytes:value length:sizeof(value) options:MTLResourceStorageModeShared];
 auto queue=[native newCommandQueue];auto cb=[queue commandBuffer];
 auto e=[cb renderCommandEncoderWithDescriptor:pass];Check(e,"native render encoder");
 [e setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)p.state];
 [e setVertexBuffer:positions offset:0 attributeStride:2*sizeof(float) atIndex:30];
 [e setVertexBuffer:velocities offset:0 attributeStride:4*sizeof(float) atIndex:29];
 [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
 [e endEncoding];[cb commit];[cb waitUntilCompleted];
 Check(cb.status==MTLCommandBufferStatusCompleted,"real GPU completed");
 const size_t row=8*(half?8:4);std::vector<u8> bytes(row*8);
 [color getBytes:bytes.data() bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
 for(size_t y=0;y<8;++y)for(size_t x=0;x<8;++x) {
  const auto at=(y*8+x)*(half?8:4);
  if(half) {
   const std::array<u16,4> expected{0xb600,0x3100,0x3880,0x3c00};
   Check(!std::memcmp(bytes.data()+at,expected.data(),8),"original signed fractional velocity half oracle");
  } else {
   const std::array<u8,4> expected{0,40,143,255};
   Check(!std::memcmp(bytes.data()+at,expected.data(),4),"no-depth/original pixel oracle");
  }
 }
 return bytes;
}
void DescriptorChecks(bool warm) {
 bool nodepth=false,velocity=false;
 for(size_t i=0;i<native_calls;++i) {
  const auto& o=observed[i];Check(o.position&&o.velocity&&o.samples==1,"actual serialized native vertex layout/sample");
  if(!o.success)continue;
  if(o.color==MTLPixelFormatRGBA8Unorm&&o.depth==MTLPixelFormatInvalid&&o.stencil==MTLPixelFormatInvalid)nodepth=true;
  if(o.color==MTLPixelFormatRGBA16Float&&o.depth==MTLPixelFormatDepth32Float_Stencil8&&
     o.stencil==MTLPixelFormatDepth32Float_Stencil8)velocity=true;
  if(warm)Check(o.options&MTLPipelineOptionFailOnBinaryArchiveMiss,"warm factory requires strict archive hit");
 }
 Check(nodepth&&velocity,"real native no-depth Invalid and velocity original RGBA16Float descriptors");
}
}

int main(int argc,char** argv){@autoreleasepool {
 Check(argc==3,"mode and fresh Shared state root required");
 const std::string mode=argv[1];Check(mode=="seed"||mode=="warm","known mode");
 const auto state=std::filesystem::canonical(argv[2]);
 const auto allowed=std::filesystem::path("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state");
 Check(state.parent_path()==allowed&&!std::filesystem::is_symlink(argv[2]),"private Shared authored state only");
 Check(std::filesystem::path(std::getenv("HOME"))==state,"HOME private state");
 auto cache_path=state/"core";std::filesystem::create_directories(cache_path);
 Common::FS::SetUserPath(Common::FS::PathType::CacheDir,cache_path);
 BbGpuConfig cfg{};cfg.serial="AUTHORED-DERIVED-PSO";Core::Emulator::FillElfInfo(cfg);
 AmdGpu::Liverpool liverpool;Metal::Device device;FactoryObserver observer{device.GetImpl().device};
 VideoCore::BackendLimits limits{};limits.two_level_bda=true;
 Metal::PipelineCache cache{device,limits,&liverpool,16,state/"msl"};
 auto& pso=cache.GetPsoCache();auto& db=Storage::DataBase::Instance();
 for(auto& entry:std::filesystem::directory_iterator(cache_path))
  if(entry.is_directory())storage=entry.path();
 Check(!storage.empty()&&db.IsOpened(),"real portable disk database opened");
 const Metal::RenderPipeline* no_depth=nullptr;const Metal::RenderPipeline* velocity=nullptr;
 std::vector<u8> original_pixels;
 if(mode=="seed") {
  Check(VideoCore::PipelineCacheCoreTestAccess::Size(cache)==0&&!pso.ArchiveLoaded(),"seed truly fresh");
  Meta(VS,VS_META,Shader::HwStage::Vertex,Shader::SwStage::Vertex);
  Meta(FS,FS_META,Shader::HwStage::Fragment,Shader::SwStage::Fragment);
  VideoCore::RegisterShaderBinary(Spirv(state/"authored.vert.spv"),VS,0);
  VideoCore::RegisterShaderBinary(Spirv(state/"authored.frag.spv"),FS,0);
  Wait(storage/fmt::format("{:#018x}_0.spv",VS));Wait(storage/fmt::format("{:#018x}_0.spv",FS));
  Check(LoadMemoryKey(cache,BaseKey()),"actual original loader creates native base");
  auto* base=VideoCore::PipelineCacheCoreTestAccess::Find(cache,BaseKey());Check(base,"native base present");
  Variants derived{cache};no_depth=derived.NoDepth(*base);velocity=derived.Velocity(*base);
  Check(no_depth&&velocity&&VideoCore::observed_registers==2,"both real successful factories recorded");
  Check(no_depth->vs==base->Render().vs&&no_depth->fs==base->Render().fs&&
        velocity->vs==base->Render().vs&&velocity->fs==base->Render().fs,"derived paths reuse original VS/FS objects");
  Wait(KeyPath(NoDepthKey()));Wait(KeyPath(VelocityKey()));
  auto nbytes=Bytes(KeyPath(NoDepthKey())),vbytes=Bytes(KeyPath(VelocityKey()));
  auto ntime=std::filesystem::last_write_time(KeyPath(NoDepthKey()));
  auto vtime=std::filesystem::last_write_time(KeyPath(VelocityKey()));
  const auto renders=pso.GetCounters().render_pipelines.load();const auto calls=native_calls;
  for(unsigned i=0;i<16;++i)Check(derived.NoDepth(*base)==no_depth&&derived.Velocity(*base)==velocity,"repeated map hit retains actual PSO");
  std::this_thread::sleep_for(std::chrono::milliseconds(10));
  Check(VideoCore::observed_registers==2&&native_calls==calls&&pso.GetCounters().render_pipelines.load()==renders,
        "repeat hits no extra RegisterPipelineData or native factory");
  Check(Bytes(KeyPath(NoDepthKey()))==nbytes&&Bytes(KeyPath(VelocityKey()))==vbytes&&
        std::filesystem::last_write_time(KeyPath(NoDepthKey()))==ntime&&
        std::filesystem::last_write_time(KeyPath(VelocityKey()))==vtime,"repeat hits no disk key rewrite");
  Variants rejected{cache};Check(!rejected.NoDepth(*base,true),"actual predriver invalid-format native factory failure");
  Check(VideoCore::observed_registers==2,"failed actual PSO never recorded");
  Variants motion{cache};Check(motion.NoDepth(*base,false,true),"native original-stage motion-key negative prerequisite");
  Check(VideoCore::observed_registers==2,"motion key never recorded by added success block");
  Meta(VS+1,INJECTED_META,Shader::HwStage::Vertex,Shader::SwStage::Vertex,true);
  auto injected=BaseKey();injected.stage_hashes[u32(Shader::SwStage::Vertex)]=INJECTED_META;injected.motion_vectors=1;
  const auto before=pso.GetCounters().render_pipelines.load();
  Check(!LoadMemoryKey(cache,injected)&&pso.GetCounters().render_pipelines.load()==before,
        "actual LoadShaderMeta rejects injected session-address VS before native factory");
  original_pixels=Draw(device,base->Render(),false);
  Check(pso.Flush(),"real archive seed publication");
 } else {
  Check(pso.ArchiveLoaded()&&pso.GetCounters().archive_hits.load()==2,"distinct warm process strict hits for both derived keys");
  Check(VideoCore::PipelineCacheCoreTestAccess::Size(cache)==2,"actual WarmUp recovered exactly two recorded keys");
  auto* n=VideoCore::PipelineCacheCoreTestAccess::Find(cache,NoDepthKey());
  auto* v=VideoCore::PipelineCacheCoreTestAccess::Find(cache,VelocityKey());
  Check(n&&v,"actual warm native derived pipeline objects");no_depth=&n->Render();velocity=&v->Render();
  Check(std::ranges::equal(n->VertexAttributes(),Layout().vertex_attributes)&&
        std::ranges::equal(v->VertexAttributes(),Layout().vertex_attributes),
        "actual warm loader retained serialized layout");
  Check(no_depth->vs==velocity->vs&&no_depth->fs==velocity->fs,"warm derivations share original cached VS/FS modules");
  Check(n->GetStage(Shader::SwStage::Vertex).pgm_hash==VS&&v->GetStage(Shader::SwStage::Fragment).pgm_hash==FS,
        "warm actual original-stage identities");
  const auto ntime=std::filesystem::last_write_time(KeyPath(NoDepthKey()));
  const auto vtime=std::filesystem::last_write_time(KeyPath(VelocityKey()));
  const auto count=pso.GetCounters().render_pipelines.load();
  const auto ncalls=native_calls;
  for(unsigned i=0;i<16;++i) {
   Check(pso.GetRenderPipeline(NoDepthKey(),no_depth->vs,no_depth->fs,{.vertex_attributes=n->VertexAttributes()})==no_depth,"warm native cache hit no-depth");
   Check(pso.GetRenderPipeline(VelocityKey(),velocity->vs,velocity->fs,{.vertex_attributes=v->VertexAttributes()})==velocity,"warm native cache hit velocity");
  }
  Check(VideoCore::observed_registers==0&&native_calls==ncalls&&pso.GetCounters().render_pipelines.load()==count,
        "warm repeat no new factory or authored registration");
  Check(std::filesystem::last_write_time(KeyPath(NoDepthKey()))==ntime&&
        std::filesystem::last_write_time(KeyPath(VelocityKey()))==vtime,"warm repeat no actual key rewrite");
 }
 DescriptorChecks(mode=="warm");
 auto before_archive=Bytes(pso.ArchivePath());Check(!before_archive.empty(),"native binary archive exists");
 pso.ReleaseWarmArchive();
 auto pixels=Draw(device,*no_depth,false),half=Draw(device,*velocity,true);
 if(mode=="seed")Check(pixels==original_pixels,"removing depth attachments preserves all original pixels");
 if(mode=="seed") {
  std::ofstream n(state/"seed-pixels.bin",std::ios::binary);n.write(reinterpret_cast<const char*>(pixels.data()),pixels.size());
  std::ofstream v(state/"seed-half.bin",std::ios::binary);v.write(reinterpret_cast<const char*>(half.data()),half.size());
 } else Check(pixels==Bytes(state/"seed-pixels.bin")&&half==Bytes(state/"seed-half.bin"),
              "new-process warm bytes equal seed pixel and half outputs");
 Check(Bytes(pso.ArchivePath())==before_archive,"readback/repeat keeps old archive bytes");
 unsigned keys=0;for(auto& e:std::filesystem::directory_iterator(storage))keys+=e.path().extension()==".key";
 Check(keys==2,"only two portable derived keys saved");
 cache.Sync();
 std::printf("AUTHOR_RESULT {\"mode\":\"%s\",\"pid\":%d,\"checks\":%u,\"key_files\":%u,\"observed_real_registers\":%u,\"native_factory_calls\":%zu,\"strict_archive_hits\":%llu,\"native_pixel_oracle\":true,\"native_half_oracle\":true,\"actual_warmup\":true}\n",
  mode.c_str(),getpid(),checks,keys,VideoCore::observed_registers,native_calls,
  static_cast<unsigned long long>(pso.GetCounters().archive_hits.load()));
 return 0;
}}
