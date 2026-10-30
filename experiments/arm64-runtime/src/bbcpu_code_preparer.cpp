// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_effective_export.h"
#include "startup_patches.h"
#include "launcher_patch_input.h"
#include "session_state.h"
#include <sys/mman.h>
#include <iostream>

namespace {
namespace fs=std::filesystem;
using namespace BbCpuImage;
class Source {
 fs::path path_;BbSession::OwnedFd fd_;struct stat before_{};void* map_=MAP_FAILED;
public:
 explicit Source(const fs::path& path):path_(fs::absolute(path)),fd_(open(path_.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC)){
  before_=BbSession::WorkStat(fd_.get());
  Require(S_ISREG(before_.st_mode)&&before_.st_size>0&&before_.st_size<=(544ll<<20),"source must be a bounded ordinary file");
  map_=mmap(nullptr,size_t(before_.st_size),PROT_READ|PROT_WRITE,MAP_PRIVATE,fd_.get(),0);Require(map_!=MAP_FAILED,"source mapping unavailable");
 }
 ~Source(){if(map_!=MAP_FAILED)munmap(map_,size_t(before_.st_size));}
 Source(const Source&)=delete;
 fs::path HandlePath()const{return "/dev/fd/"+std::to_string(fd_.get());}
 std::span<const unsigned char> Bytes()const{return {static_cast<const unsigned char*>(map_),size_t(before_.st_size)};}
 void* MutableAt(size_t at){Require(at<Bytes().size(),"source image offset outside file");return static_cast<unsigned char*>(map_)+at;}
 void Unchanged()const{struct stat named{};Require(!lstat(path_.c_str(),&named)&&BbSession::SameWorkNode(before_,named)&&BbSession::SameWorkNode(before_,BbSession::WorkStat(fd_.get())),"source changed during effective-code preparation");}
 std::string Hash()const{
  // Darwin /dev/fd opens share an offset with the borrowed descriptor. pread
  // hashes original file bytes independently of parser positions and the
  // private mapping's already-applied relocations/patches.
  CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);std::array<unsigned char,65536> buffer{};
  for(off_t at=0;at<before_.st_size;){const auto wanted=size_t(std::min<off_t>(buffer.size(),before_.st_size-at));ssize_t got;
   do{got=pread(fd_.get(),buffer.data(),wanted,at);}while(got<0&&errno==EINTR);
   Require(got>0,"original code source read failed");CC_SHA256_Update(&ctx,buffer.data(),static_cast<CC_LONG>(got));at+=got;
  }BbCpuCache::Digest value{};CC_SHA256_Final(value.data(),&ctx);return BbStaticCode::Hex(value);
 }
};
}
int main(int argc,char** argv){
 try{
  std::map<std::string,fs::path> args;
  for(int i=1;i<argc;i+=2){Require(i+1<argc,"option requires a path");std::string key=argv[i];Require(key=="--boot"||key=="--elf"||key=="--startup"||key=="--patches"||key=="--out","unknown code-preparer option");Require(args.emplace(key,argv[i+1]).second,"duplicate code-preparer option");}
  Require(args.contains("--boot")&&args.contains("--elf")&&args.contains("--out"),"--boot, --elf and fresh --out required");
  Source source(args.at("--boot")),elf(args.at("--elf"));BbBoot::Boot boot(source.HandlePath());
  auto layout=ReadLayout(elf.Bytes(),boot);void* image=source.MutableAt(size_t(boot.image_offset));RelocateMain(image,boot);
  std::unique_ptr<Source> startup,patches;
  if(args.contains("--startup")){startup=std::make_unique<Source>(args.at("--startup"));BbStartup::Apply(startup->HandlePath(),image,boot);}
  if(args.contains("--patches")){patches=std::make_unique<Source>(args.at("--patches"));BbLauncherPatch::Apply(patches->HandlePath(),image,boot);}
  source.Unchanged();elf.Unchanged();if(startup)startup->Unchanged();if(patches)patches->Unchanged();
  const auto result=Export(args.at("--out"),boot,layout,image,elf.Bytes(),source.Hash(),elf.Hash());
  source.Unchanged();elf.Unchanged();if(startup)startup->Unchanged();if(patches)patches->Unchanged();
  std::cout<<"BBCPU effective code prepared identity="<<result.identity<<" bytes="<<result.bytes<<" excluded_code_slots="<<result.excluded.size()<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"BBCPU CODE PREPARE ERROR: "<<e.what()<<'\n';return 1;}
}
