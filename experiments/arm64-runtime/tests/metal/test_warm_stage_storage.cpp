// SPDX-License-Identifier: GPL-2.0-or-later
// Authored inputs only. Real Storage + meta/spec serialization + LoadPipelineStage.
// The sole backend mock counts CreateShaderModule; no Metal/game is launched.
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>
#include <fmt/format.h>
#include "bbgpu.h"
#include "common/elf_info.h"
#include "common/path_util.h"
#include "common/serdes.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/specialization.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/cache_storage.h"
#include "video_core/pipeline_cache_core.h"
#include "test_gpu_runtime_stubs.cpp"
// Same friend setup class as shim/bbgpu.cpp; initializes only authored title metadata.
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
namespace VideoCore {
struct PipelineCacheCoreTestAccess {
 static size_t Programs(PipelineCacheCore& core){return core.program_cache.size();}
 static bool NoNullPrograms(PipelineCacheCore& core){for(const auto& entry:core.program_cache)if(!entry.second)return false;return true;}
 static ShaderModuleHandle Selected(PipelineCacheCore& core){return core.sel.modules[0];}
 static size_t Permutations(PipelineCacheCore& core,u64 hash){auto it=core.program_cache.find(hash);return it==core.program_cache.end()?0:it.value()->modules.size();}
};
class StageCore final:public PipelineCacheCore {
public:
 unsigned calls=0,created=0;bool reject_backend=false;
 StageCore(AmdGpu::Liverpool& l):PipelineCacheCore(&l,{}, {},16,"warm-stage"){}
 ~StageCore(){DestroyPipelines();}
 ShaderModuleHandle CreateShaderModule(std::span<const u32> spv,std::string_view,bool from_cache)override{
  ++calls;assert(from_cache&&spv.size()>=5&&spv[0]==0x07230203u);
  if(reject_backend)return {};return {++created};
 }
 void DestroyShaderModule(ShaderModuleHandle)override{}
 std::unique_ptr<PipelineSerialization> NewGraphicsSerialization()override{std::abort();}
 std::unique_ptr<PipelineSerialization> NewComputeSerialization()override{std::abort();}
 std::unique_ptr<PipelineBase> CreateGraphicsPipeline(const GraphicsPipelineKey&,std::span<const Shader::Info*,MaxShaderStages>,std::span<const Shader::RuntimeInfo,MaxShaderStages>,std::optional<const Shader::Gcn::FetchShaderData>,std::span<const ShaderModuleHandle,MaxShaderStages>,PipelineSerialization&,bool)override{std::abort();}
 std::unique_ptr<PipelineBase> CreateComputePipeline(ComputePipelineKey,const Shader::Info&,ShaderModuleHandle,PipelineSerialization&,bool)override{std::abort();}
};
}
namespace {
using Access=VideoCore::PipelineCacheCoreTestAccess;
std::filesystem::path folder;
std::filesystem::path Blob(std::string name,const char* ext){auto p=folder/name;p.replace_extension(ext);return p;}
void Wait(const std::filesystem::path& path){
 const auto limit=std::chrono::steady_clock::now()+std::chrono::seconds(5);
 while(!std::filesystem::exists(path)&&std::chrono::steady_clock::now()<limit)std::this_thread::sleep_for(std::chrono::milliseconds(2));
 assert(std::filesystem::is_regular_file(path));
}
std::vector<u32> AuthoredSpirv(){return {
 0x07230203,0x00010000,0,6,0,
 0x00020011,1, // OpCapability Shader
 0x0003000e,0,1, // Logical GLSL450
 0x0005000f,5,4,0x6e69616d,0, // GLCompute %4 "main"
 0x00060010,4,17,1,1,1, // LocalSize
 0x00020013,1, // void %1
 0x00030021,2,1, // function type %2
 0x00050036,1,4,0,2, // function %4
 0x000200f8,5, // label %5
 0x000100fd,0x00010038};}
std::filesystem::path Binary(u64 pgm,size_t index){return Blob(fmt::format("{:#018x}_{}",pgm,index),"spv");}
void SaveBinary(u64 pgm,size_t index){
 // Only this test owns these authored files. Ensure an old poison file cannot satisfy Wait.
 std::filesystem::remove(Binary(pgm,index));
 VideoCore::RegisterShaderBinary(AuthoredSpirv(),pgm,index);Wait(Binary(pgm,index));
 std::vector<u32> loaded;Storage::DataBase::Instance().Load(Storage::BlobType::ShaderBinary,fmt::format("{:#018x}_{}",pgm,index),loaded);
 assert(loaded==AuthoredSpirv());
}
std::vector<u8> Meta(u64 pgm,u64 meta_hash,size_t index,u32 workgroup=1){
 Shader::Info info{};info.hw_stage=Shader::HwStage::Compute;info.sw_stage=Shader::SwStage::Compute;info.pgm_hash=pgm;
 Shader::StageSpecialization spec{};spec.info=&info;
 spec.runtime_info.Initialize(Shader::HwStage::Compute,Shader::SwStage::Compute);
 spec.runtime_info.hw.cs.workgroup_size={workgroup,1,1};
 VideoCore::RegisterShaderMeta(info,{},spec,meta_hash,index);
 const auto name=fmt::format("{:#018x}",meta_hash);Wait(Blob(name,"meta"));
 std::vector<u8> bytes;Storage::DataBase::Instance().Load(Storage::BlobType::ShaderMeta,name,bytes);assert(!bytes.empty());return bytes;
}
bool Load(VideoCore::StageCore& core,const std::vector<u8>& meta){
 Serialization::Archive ar{std::vector<u8>{meta}};return core.LoadPipelineStage(ar,0);
}
void Poison(const std::filesystem::path& path,std::span<const u32> words){
 std::ofstream out(path,std::ios::binary|std::ios::trunc);out.write(reinterpret_cast<const char*>(words.data()),words.size_bytes());out.close();assert(out.good());
}
template<class F>void Corrupt(F&& f){bool threw=false;try{f();}catch(const Serialization::CorruptData&){threw=true;}assert(threw);}
}
int main(int argc,char**argv){
 assert(argc==2);const std::filesystem::path case_dir=argv[1];
 const auto root=std::filesystem::path("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state");
 assert(case_dir.is_absolute()&&case_dir.parent_path()==root&&!std::filesystem::is_symlink(case_dir));
 assert(std::filesystem::is_directory(case_dir));
 auto cache=case_dir/"stage-cache";std::filesystem::create_directory(cache);Common::FS::SetUserPath(Common::FS::PathType::CacheDir,cache);
 BbGpuConfig config{};config.serial="AUTHORED-WARM-STAGE";config.title="Authored storage controls";Core::Emulator::FillElfInfo(config);
 auto& db=Storage::DataBase::Instance();db.SetCacheTag("warm-stage");db.Open();
 folder=cache/"AUTHORED-WARM-STAGE-warm-stage";assert(std::filesystem::is_directory(folder));
 AmdGpu::Liverpool liverpool;
 {
  VideoCore::StageCore core{liverpool};constexpr u64 hash=0x13579bdf;
  auto same=Meta(hash,0xabc100,0);SaveBinary(hash,0);
  assert(Load(core,same)&&core.calls==1&&core.created==1);const auto original=Access::Selected(core);
  auto moved=Binary(hash,0);moved+=".saved";std::filesystem::rename(Binary(hash,0),moved);
  assert(Load(core,same)&&core.calls==1&&Access::Selected(core)==original);
  const std::array<u32,5> poison{0xdeadc0de,0,0,0,0};Poison(Binary(hash,0),poison);
  assert(Load(core,same)&&core.calls==1&&Access::Selected(core)==original);
  // Same program hash but a different complete runtime specialization cannot reuse module 0.
  auto other=Meta(hash,0xabc101,1,2);
  assert(!Load(core,other)&&core.calls==1&&Access::Permutations(core,hash)==1);
  SaveBinary(hash,1);assert(Load(core,other)&&core.calls==2&&Access::Selected(core)!=original);
  const auto second=Access::Selected(core);
  // Identical specialization at a different stored index is stale, even with valid SPIR-V.
  auto wrong_index=Meta(hash,0xabc102,3,1);SaveBinary(hash,3);
  assert(!Load(core,wrong_index)&&core.calls==2&&Access::Permutations(core,hash)==2);
  assert(Load(core,other)&&core.calls==2&&Access::Selected(core)==second);
  // Different specialization + corrupt SPIR-V fails without harming the existing program.
  auto invalid_other=Meta(hash,0xabc103,2,3);Poison(Binary(hash,2),poison);
  Corrupt([&]{Load(core,invalid_other);});assert(core.calls==2&&Access::NoNullPrograms(core));
  assert(Load(core,same)&&core.calls==2&&Access::Selected(core)==original);
 }
 {
  VideoCore::StageCore core{liverpool};constexpr u64 hash=0x2468ace0;auto meta=Meta(hash,0xabc200,0);
  assert(!Load(core,meta)&&!Load(core,meta)&&core.calls==0&&Access::Programs(core)==0&&Access::NoNullPrograms(core));
  SaveBinary(hash,0);assert(Load(core,meta)&&core.calls==1&&Access::Programs(core)==1);
 }
 {
  VideoCore::StageCore core{liverpool};constexpr u64 hash=0x2468ace1;auto meta=Meta(hash,0xabc201,0);
  const std::array<u32,2> short_header{0x07230203,0x10000};Poison(Binary(hash,0),short_header);
  Corrupt([&]{Load(core,meta);});assert(core.calls==0&&Access::Programs(core)==0&&Access::NoNullPrograms(core));
  const std::array<u32,5> wrong_magic{0xdeadbeef,0x10000,0,6,0};Poison(Binary(hash,0),wrong_magic);
  Corrupt([&]{Load(core,meta);});assert(core.calls==0&&Access::Programs(core)==0&&Access::NoNullPrograms(core));
  SaveBinary(hash,0);assert(Load(core,meta)&&core.calls==1&&Access::Programs(core)==1);
 }
 {
  VideoCore::StageCore core{liverpool};constexpr u64 hash=0x2468ace2;auto meta=Meta(hash,0xabc202,0);SaveBinary(hash,0);
  core.reject_backend=true;Corrupt([&]{Load(core,meta);});assert(core.calls==1&&core.created==0&&Access::Programs(core)==0&&Access::NoNullPrograms(core));
  core.reject_backend=false;assert(Load(core,meta)&&core.calls==2&&core.created==1&&Access::Programs(core)==1);
 }
 db.Close();puts("PASS actual LoadPipelineStage/Storage/meta/spec/index: same-module missing+poisoned-SPV reuse; distinct specialization and stale-index controls; missing/bad-header/backend-rejection publish no null program");
}
