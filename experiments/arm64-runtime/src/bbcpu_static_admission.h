// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "bbcpu_effective_image.h"
#include "session_state.h"
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <sys/mman.h>
#include <memory>

#ifndef BBCPU_ABI_IDENTITY
#error bbcpu static admission requires the actual compiled CPU header contract
#endif
namespace BbCpuArtifact {
namespace fs=std::filesystem;
using BbCpuImage::Require;
struct Receipt {
 char magic[8];uint64_t abi,architecture;
 BbCpuCache::Digest header_contract,effective_identity,elf_sha256,library_sha256;
};
static_assert(sizeof(Receipt)==152);
class File {
 fs::path path_;BbSession::OwnedFd fd_;struct stat before_{};
public:
 explicit File(const fs::path& path):path_(path),fd_(open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC)){
  before_=BbSession::WorkStat(fd_.get());
  Require(path.is_absolute()&&path==fs::canonical(path)&&S_ISREG(before_.st_mode)&&before_.st_uid==getuid()&&before_.st_nlink==1&&!(before_.st_mode&0022)&&before_.st_size>0&&before_.st_size<=(544ll<<20),"static artifact is not an owned bounded canonical file");
 }
 int Fd()const{return fd_.get();}size_t Size()const{return size_t(before_.st_size);}
 fs::path FdPath()const{return "/dev/fd/"+std::to_string(fd_.get());}
 void Read(uint64_t offset,void* data,size_t bytes)const{
  Require(offset<=Size()&&bytes<=Size()-offset,"static artifact read outside file");auto* p=static_cast<unsigned char*>(data);
  while(bytes){ssize_t n;do{n=pread(fd_.get(),p,bytes,off_t(offset));}while(n<0&&errno==EINTR);Require(n>0,"static artifact read failed");p+=n;bytes-=size_t(n);offset+=uint64_t(n);}
 }
 BbCpuCache::Digest Hash()const{
  CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);std::array<unsigned char,65536> buffer{};
  for(size_t at=0;at<Size();){const auto n=std::min(buffer.size(),Size()-at);Read(at,buffer.data(),n);CC_SHA256_Update(&ctx,buffer.data(),static_cast<CC_LONG>(n));at+=n;}
  BbCpuCache::Digest digest{};CC_SHA256_Final(digest.data(),&ctx);return digest;
 }
 void Unchanged()const{struct stat named{};Require(!lstat(path_.c_str(),&named)&&BbSession::SameWorkNode(before_,named)&&BbSession::SameWorkNode(before_,BbSession::WorkStat(fd_.get())),"static artifact changed during admission");}
};
class Map {
 void* ptr_=MAP_FAILED;size_t bytes_{};
public:
 explicit Map(const File& f):bytes_(f.Size()){ptr_=mmap(nullptr,bytes_,PROT_READ,MAP_PRIVATE,f.Fd(),0);Require(ptr_!=MAP_FAILED,"static input map failed");}
 ~Map(){if(ptr_!=MAP_FAILED)munmap(ptr_,bytes_);}
 std::span<const unsigned char> Bytes()const{return {static_cast<const unsigned char*>(ptr_),bytes_};}
};
inline void Mach(const File& file){
 mach_header_64 header{};file.Read(0,&header,sizeof(header));
 Require(header.magic==MH_MAGIC_64&&header.cputype==CPU_TYPE_ARM64&&header.filetype==MH_DYLIB&&header.ncmds&&header.ncmds<=16384&&header.sizeofcmds<=1048576&&sizeof(header)+header.sizeofcmds<=file.Size(),"static library is not a bounded ARM64 Mach-O dylib");
 std::vector<unsigned char> commands(header.sizeofcmds);file.Read(sizeof(header),commands.data(),commands.size());size_t at=0;
 for(uint32_t i=0;i<header.ncmds;++i){load_command command{};Require(at<=commands.size()&&sizeof(command)<=commands.size()-at,"truncated static load command");memcpy(&command,commands.data()+at,sizeof(command));Require(command.cmdsize>=sizeof(command)&&command.cmdsize<=commands.size()-at,"invalid static load-command length");
  if(command.cmd==LC_LOAD_DYLIB||command.cmd==LC_LOAD_WEAK_DYLIB||command.cmd==LC_REEXPORT_DYLIB||command.cmd==LC_LOAD_UPWARD_DYLIB){
   Require(command.cmdsize>=sizeof(dylib_command),"truncated static dylib command");dylib_command dylib{};memcpy(&dylib,commands.data()+at,sizeof(dylib));
   Require(dylib.dylib.name.offset>=sizeof(dylib)&&dylib.dylib.name.offset<command.cmdsize,"invalid static dependency offset");
   const char* name=reinterpret_cast<const char*>(commands.data()+at+dylib.dylib.name.offset);const size_t capacity=command.cmdsize-dylib.dylib.name.offset;
   Require(memchr(name,0,capacity)&&std::string_view(name)=="/usr/lib/libSystem.B.dylib","static library has an unadmitted dependency");
  }
  at+=command.cmdsize;
 }
 Require(at==commands.size(),"trailing static load-command bytes");
}
struct Candidate {
 std::shared_ptr<File> library;
 std::string effective_identity;
 std::shared_ptr<File> input;
 BbCpuImage::Layout layout;
};
struct Admitted:Candidate {Admitted()=default;explicit Admitted(Candidate value):Candidate(std::move(value)){}};
// Opening an authenticated artifact does not grant execution. Match must still
// validate the actual final image after its normal ordered startup installers.
template<class Allowed>Candidate Open(const BbBoot::Boot& boot,const fs::path& library_path,Allowed allowed){
 Require(library_path.filename()=="librecomp.dylib"&&allowed(library_path),"static library outside selected code authority");
 const auto root=library_path.parent_path(),elf_path=root/"effective.elf",receipt_path=root/"bbcpu.aot-receipt";
 Require(allowed(elf_path)&&allowed(receipt_path),"static descriptor/input outside selected code authority");
 File receipt_file(receipt_path);auto elf=std::make_shared<File>(elf_path);auto library=std::make_shared<File>(library_path);
 Require(receipt_file.Size()==sizeof(Receipt),"static receipt has wrong length");Receipt receipt{};receipt_file.Read(0,&receipt,sizeof(receipt));
 Require(!memcmp(receipt.magic,"BBAOT005",8)&&receipt.abi==5&&receipt.architecture==CPU_TYPE_ARM64&&BbStaticCode::Hex(receipt.header_contract)==BBCPU_ABI_IDENTITY,"static CPU ABI/header contract mismatch");
 Require(elf->Hash()==receipt.elf_sha256&&library->Hash()==receipt.library_sha256,"static input/library digest mismatch");Mach(*library);
 Map map(*elf);auto layout=BbCpuImage::ReadLayout(map.Bytes(),boot);
 receipt_file.Unchanged();elf->Unchanged();library->Unchanged();return {std::move(library),BbStaticCode::Hex(receipt.effective_identity),std::move(elf),std::move(layout)};
}
inline Admitted Match(const Candidate& candidate,const BbBoot::Boot& boot,const void* image){
 auto observed=BbCpuImage::Normalize(boot,candidate.layout,image,[](auto,auto,auto){});
 Require(observed.identity==candidate.effective_identity,"static effective-code identity mismatch");
 candidate.input->Unchanged();candidate.library->Unchanged();return Admitted(candidate);
}
template<class Allowed>Admitted Admit(const BbBoot::Boot& boot,const void* image,const fs::path& library_path,Allowed allowed){
 return Match(Open(boot,library_path,allowed),boot,image);
}
}
