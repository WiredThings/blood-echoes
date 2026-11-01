// SPDX-License-Identifier: GPL-2.0-or-later
#include "static_variant_load.h"
#include "static_variant_native.h"
#include "bundled_runtime_admission.h"
#include "common/hash.h"
#include "common/path_util.h"
#include "common/serdes.h"
#include "video_core/renderer_metal/mtl_function_identity.h"
#include <atomic>
#include <array>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <filesystem>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace VideoCore {
bool LoadShaderMeta(Serialization::Archive&,Shader::Info&,
                   std::optional<Shader::Gcn::FetchShaderData>&,
                   Shader::StageSpecialization&,size_t&);
}
namespace BbStaticVariant {
namespace {
namespace fs=std::filesystem;
std::atomic<u64> compiled{},adopted{},context_miss{},rejected{};
constexpr size_t HeaderBytes=224,MaxMeta=1024*1024,MaxSpirv=512*1024;
void Need(bool b,const char* message){if(!b)throw std::runtime_error(message);}
struct Fd {int value=-1;~Fd(){if(value>=0)close(value);}};
void Read(int fd,void* bytes,size_t count){auto*out=static_cast<u8*>(bytes);while(count){auto n=read(fd,out,count);if(n<0&&errno==EINTR)continue;Need(n>0,"short record");out+=n;count-=size_t(n);}}
std::string Sha(std::span<const u8> bytes){return Metal::FunctionIdentity::Sha({reinterpret_cast<const char*>(bytes.data()),bytes.size()});}
std::string Hex(std::span<const u8> bytes){static constexpr char chars[]="0123456789abcdef";std::string s;for(auto b:bytes){s+=chars[b>>4];s+=chars[b&15];}return s;}
bool Same(const struct stat&a,const struct stat&b){return a.st_dev==b.st_dev&&a.st_ino==b.st_ino&&a.st_size==b.st_size&&a.st_mtimespec.tv_sec==b.st_mtimespec.tv_sec&&a.st_mtimespec.tv_nsec==b.st_mtimespec.tv_nsec;}
std::string LibraryIdentity(){
    // Reuse the immutable native code admission when available. Bare development
    // builds hash only their actual loaded image once, not on each lookup/draw.
#if defined(BB_BUNDLED_RUNTIME_ADMISSION)
    if(const auto& bundle=BbBundledRuntime::Actual();bundle){
        const auto it=bundle->runtime_sha256.find("bin/gpu-metal-arm64/libbbgpu.dylib");
        if(it!=bundle->runtime_sha256.end())return it->second;
    }
#endif
    Dl_info image{};Need(dladdr(reinterpret_cast<const void*>(&VideoCore::EncodeShaderMeta),&image)&&image.dli_fname,"actual compiler image");
    Fd fd;fd.value=open(image.dli_fname,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);Need(fd.value>=0,"actual compiler open");
    struct stat a{},b{};Need(fstat(fd.value,&a)==0&&S_ISREG(a.st_mode)&&a.st_size>0&&a.st_size<=512*1024*1024,"actual compiler bounds");
    Metal::FunctionIdentity::Writer hash;std::array<u8,65536> buffer{};for(;;){auto n=read(fd.value,buffer.data(),buffer.size());if(n<0&&errno==EINTR)continue;Need(n>=0,"actual compiler read");if(!n)break;hash.raw(buffer.data(),size_t(n));}
    Need(fstat(fd.value,&b)==0&&Same(a,b),"actual compiler changed");const auto digest=hash.finish();return Hex(digest);
}
struct Root {
    Fd directory;std::string compiler;
    Root(){try{
        const char* mode=std::getenv("BB_MTL_LOCAL_AOT_MODE");
        if(!mode||(std::string_view(mode)!="auto"&&std::string_view(mode)!="load"))return;
        const auto gpu=Common::FS::GetUserPath(Common::FS::PathType::UserDir);
        const auto base=gpu/"automatic-game-inputs/static-native-variants";
        // No image hashing or filesystem inventory when this launch has no seeds.
        if(!fs::is_directory(base))return;
        compiler=LibraryIdentity();const auto path=base/compiler;
        if(!fs::is_directory(path))return;
        Need(fs::canonical(path)==path,"canonical selected variant root");
        directory.value=open(path.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        struct stat st{};Need(directory.value>=0&&fstat(directory.value,&st)==0&&st.st_uid==getuid(),"owned selected variant root");
    }catch(const std::exception& e){if(directory.value>=0)close(directory.value);directory.value=-1;std::fprintf(stderr,"Static shader root unavailable: %.160s\n",e.what());}}
};
Root& Selected(){static Root root;return root;}
template<class T>T Word(const std::array<u8,HeaderBytes>& header,size_t offset){T value{};memcpy(&value,header.data()+offset,sizeof(value));return value;}
}
std::optional<ReadyModule> Load(const Shader::Profile& profile,Shader::HwStage hw,Shader::SwStage sw,
    const Shader::ShaderParams& params,const Shader::RuntimeInfo& runtime,const Shader::Backend::Bindings& start){
    if(!((hw==Shader::HwStage::Fragment&&sw==Shader::SwStage::Fragment)||
         (hw==Shader::HwStage::Compute&&sw==Shader::SwStage::Compute)))return std::nullopt;
    auto& root=Selected();if(root.directory.value<0)return std::nullopt;
    try{
        const auto profile_bytes=VideoCore::EncodeShaderProfile(profile);
        const auto profile_sha=Sha(profile_bytes);
        const auto group=fmt::format("{}.{}.{:016x}",profile_sha,u32(hw),params.hash);
        Fd target;target.value=openat(root.directory.value,group.c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
        if(target.value<0){if(errno==ENOENT)return std::nullopt;throw std::runtime_error("record group open");}
        struct stat owner{};Need(fstat(target.value,&owner)==0&&owner.st_uid==getuid(),"owned record group");
        Need(params.code.size_bytes()<=Shader::CompileSnapshot::MaxCodeBytes,"current code byte budget");
        const auto code_sha=Sha({reinterpret_cast<const u8*>(params.code.data()),params.code.size_bytes()});
        const auto name=code_sha+".bbsv";
        Fd fd;fd.value=openat(target.value,name.c_str(),O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
        if(fd.value<0){if(errno==ENOENT)return std::nullopt;throw std::runtime_error("record open");}
        struct stat before{},after{};Need(fstat(fd.value,&before)==0&&S_ISREG(before.st_mode)&&before.st_uid==getuid()&&
            before.st_size>=HeaderBytes&&before.st_size<=HeaderBytes+MaxMeta+MaxSpirv,"record bounds");
        std::array<u8,HeaderBytes> h{};Read(fd.value,h.data(),h.size());
        const auto meta_size=Word<u32>(h,52),spv_size=Word<u32>(h,56);
        Need(memcmp(h.data(),"BBSVAR01",8)==0&&Word<u32>(h,8)==1&&Word<u32>(h,12)==u32(hw)&&
             Word<u32>(h,16)==u32(sw)&&Word<u32>(h,20)==0&&Word<u64>(h,24)==params.hash&&
             Word<u64>(h,32)!=params.hash&&Word<u64>(h,40)==HashCombine(params.hash,u64{0})&&
             Word<u32>(h,48)==params.code.size_bytes()&&Word<u32>(h,60)==0&&
             meta_size>0&&meta_size<=MaxMeta&&spv_size>=20&&spv_size<=MaxSpirv&&spv_size%4==0&&
             size_t(before.st_size)==HeaderBytes+meta_size+spv_size,"record identity/length");
        Need(Hex(std::span(h).subspan(96,32))==profile_sha&&Hex(std::span(h).subspan(192,32))==root.compiler,"record profile/compiler");
        Need(Hex(std::span(h).subspan(64,32))==code_sha,"current exact code");
        std::vector<u8> metadata(meta_size);std::vector<u32> spirv(spv_size/4);
        Read(fd.value,metadata.data(),metadata.size());Read(fd.value,spirv.data(),spv_size);
        Need(fstat(fd.value,&after)==0&&Same(before,after)&&
             Hex(std::span(h).subspan(128,32))==Sha(metadata)&&
             Hex(std::span(h).subspan(160,32))==Sha({reinterpret_cast<const u8*>(spirv.data()),spv_size})&&spirv[0]==0x07230203,"record payload integrity");
        ReadyModule ready;ready.program=std::make_unique<VideoCore::Program>();ready.spec.info=&ready.program->info;
        Serialization::Archive archive{std::move(metadata)};std::optional<Shader::Gcn::FetchShaderData> fetch;size_t index{};
        Need(VideoCore::LoadShaderMeta(archive,ready.program->info,fetch,ready.spec,index)&&index==0&&!fetch&&
             ready.program->info.pgm_hash==params.hash&&ready.program->info.hw_stage==hw&&ready.program->info.sw_stage==sw,
             "regenerated module metadata identity");
        auto& info=ready.program->info;
        // State is checked BEFORE the recovered walker can read any current guest
        // data. No sampler/format/descriptor from the preparation snapshot is used
        // as a substitute for the live draw's resource specialization.
        if(!(ready.spec.runtime_info==runtime)||ready.spec.start.unified!=start.unified||
           ready.spec.start.buffer!=start.buffer||ready.spec.start.user_data!=start.user_data){++context_miss;return std::nullopt;}
        Need(info.srt_info.flattened_bufsize_dw<=Shader::SpeculativeReadState::MaxFlatBytes/4,"bounded regenerated flat table");
        info.pgm_base=params.Base();info.user_data=params.user_data;
        {
            Shader::SpeculativeReadState reads;Shader::ScopedSpeculativeReads scope{info,reads};
            info.RefreshFlatBuf();Shader::SpeculativeFormatFailure failure{};
            Shader::StageSpecialization live{info,runtime,profile,start,&failure};
            if(!live.Valid()||!(live==ready.spec)){++context_miss;return std::nullopt;}
            ready.spec=std::move(live);
        }
        ready.spirv=std::move(spirv);return ready;
    }catch(const std::exception& e){++rejected;std::fprintf(stderr,"Static shader record rejected hash=%016llx: %.160s\n",static_cast<unsigned long long>(params.hash),e.what());return std::nullopt;}
}
void RuntimeCompile()noexcept{compiled.fetch_add(1,std::memory_order_relaxed);}
void Initialize()noexcept{(void)Selected();}
void Adopted(u64 hash)noexcept{adopted.fetch_add(1,std::memory_order_relaxed);std::fprintf(stderr,"Static shader prepared hit hash=%016llx\n",static_cast<unsigned long long>(hash));}
void Report()noexcept{std::fprintf(stderr,"Static shader totals adopted=%llu context_miss=%llu rejected=%llu runtime_compiles=%llu\n",
    static_cast<unsigned long long>(adopted.load()),static_cast<unsigned long long>(context_miss.load()),
    static_cast<unsigned long long>(rejected.load()),static_cast<unsigned long long>(compiled.load()));}
}
