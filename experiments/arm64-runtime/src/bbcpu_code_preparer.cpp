// SPDX-License-Identifier: GPL-2.0-or-later
#include "bbcpu_effective_image.h"
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
void Write(std::ofstream& out,u64 at,const void* data,size_t size){out.seekp(std::streamoff(at));out.write(static_cast<const char*>(data),std::streamsize(size));Require(bool(out),"effective ELF write failed");}
}
int main(int argc,char** argv){
 try{
  std::map<std::string,fs::path> args;
  for(int i=1;i<argc;i+=2){Require(i+1<argc,"option requires a path");std::string key=argv[i];Require(key=="--boot"||key=="--elf"||key=="--startup"||key=="--patches"||key=="--out","unknown code-preparer option");Require(args.emplace(key,argv[i+1]).second,"duplicate code-preparer option");}
  Require(args.contains("--boot")&&args.contains("--elf")&&args.contains("--out"),"--boot, --elf and fresh --out required");
  auto out=fs::absolute(args.at("--out")).lexically_normal();Require(fs::canonical(out.parent_path())==out.parent_path()&&fs::create_directory(out),"effective-code output must be a fresh non-linked directory");
  fs::permissions(out,fs::perms::owner_all,fs::perm_options::replace);
  Source source(args.at("--boot")),elf(args.at("--elf"));BbBoot::Boot boot(source.HandlePath());
  auto layout=ReadLayout(elf.Bytes(),boot);void* image=source.MutableAt(size_t(boot.image_offset));RelocateMain(image,boot);
  std::unique_ptr<Source> startup,patches;
  if(args.contains("--startup")){startup=std::make_unique<Source>(args.at("--startup"));BbStartup::Apply(startup->HandlePath(),image,boot);}
  if(args.contains("--patches")){patches=std::make_unique<Source>(args.at("--patches"));BbLauncherPatch::Apply(patches->HandlePath(),image,boot);}
  ElfHeader h{};memcpy(&h,elf.Bytes().data(),sizeof(h));h.phoff=sizeof(h);h.phnum=uint16_t(layout.loads.size()+1);h.phentsize=sizeof(Program);h.shoff=0;h.shnum=0;h.shentsize=0;h.shstrndx=0;
  std::vector<Program> programs;u64 at=4096;
  for(auto p:layout.loads){p.offset=at;p.paddr=p.address;p.filesz=p.memsz;p.align=4096;Require(p.memsz<=(544u<<20)-at,"effective ELF too large");programs.push_back(p);at=(at+p.memsz+4095)&~u64{4095};}
  auto eh=layout.eh;bool found=false;
  for(auto p:programs)if(eh.address>=p.address&&eh.address-p.address<=p.filesz&&eh.filesz<=p.filesz-(eh.address-p.address)){eh.offset=p.offset+eh.address-p.address;found=true;break;}
  Require(found,"effective ELF lost original unwind metadata");programs.push_back(eh);
  std::ofstream ready(out/"effective.elf",std::ios::binary);Require(bool(ready),"effective ELF unavailable");Write(ready,0,&h,sizeof(h));Write(ready,h.phoff,programs.data(),programs.size()*sizeof(Program));
  auto result=Normalize(boot,layout,image,[&](Program p,u64 offset,std::span<const unsigned char> bytes){
   auto match=std::find_if(programs.begin(),programs.end(),[&](auto row){return row.type==1&&row.address==p.address;});Require(match!=programs.end(),"effective output lost a load");Write(ready,match->offset+offset,bytes.data(),bytes.size());
  });ready.flush();Require(bool(ready),"effective ELF commit failed");ready.close();
  std::ofstream pointers(out/"pointers.txt");for(auto value:result.pointers)pointers<<"0x"<<std::hex<<value<<'\n';pointers.flush();Require(bool(pointers),"effective pointer catalog write failed");
  // Revalidate opened sources before publishing a completed descriptor. Failure
  // leaves a fresh incomplete scratch directory; no previous artifact is erased.
  source.Unchanged();elf.Unchanged();if(startup)startup->Unchanged();if(patches)patches->Unchanged();
  auto boot_hash=source.Hash();
  auto source_hash=elf.Hash();
  auto output_hash=BbStaticCode::Hex(BbStaticCode::SourceHash(out/"effective.elf"));
  source.Unchanged();elf.Unchanged();if(startup)startup->Unchanged();if(patches)patches->Unchanged();
  std::ofstream report(out/"effective-input.json");report<<"{\"schema\":1,\"effective_identity\":\""<<result.identity<<"\",\"effective_elf_sha256\":\""<<output_hash<<"\",\"source_boot_sha256\":\""<<boot_hash<<"\",\"source_elf_sha256\":\""<<source_hash<<"\",\"normalized_bytes\":"<<result.bytes<<",\"source_fields_are_provenance_only\":true,\"loads\":[";
  for(size_t i=0;i<layout.loads.size();++i){auto p=layout.loads[i];if(i)report<<',';report<<"{\"address\":"<<p.address<<",\"size\":"<<p.memsz<<",\"flags\":"<<p.flags<<'}';}
  report<<"],\"unwind\":{\"address\":"<<eh.address<<",\"size\":"<<eh.filesz<<"},\"excluded_code_slots\":[";
  for(size_t i=0;i<result.excluded.size();++i){if(i)report<<',';report<<'['<<result.excluded[i].first<<','<<result.excluded[i].second<<']';}report<<"]}\n";report.flush();Require(bool(report),"effective descriptor write failed");
  std::cout<<"BBCPU effective code prepared identity="<<result.identity<<" bytes="<<result.bytes<<" excluded_code_slots="<<result.excluded.size()<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<"BBCPU CODE PREPARE ERROR: "<<e.what()<<'\n';return 1;}
}
