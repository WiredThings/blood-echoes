// SPDX-License-Identifier: MIT
#pragma once
#include "fex_disk_format.h"
#include <filesystem>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cerrno>
#include <atomic>
namespace BbFexDisk {
namespace fs=std::filesystem;
inline void Preserve(const fs::path& path){
 static std::atomic<uint64_t> sequence{};
 for(unsigned attempt=0;attempt<1024;++attempt){
  auto backup=fs::path(path.string()+".previous-"+std::to_string(getpid())+"-"+std::to_string(sequence.fetch_add(1)));
  if(!link(path.c_str(),backup.c_str())){Require(!unlink(path.c_str()),"FEX preserved path unlink");return;}
  Require(errno==EEXIST,"FEX preserve previous product");
 }throw std::runtime_error("FEX backup name budget");
}
inline std::vector<std::byte> Read(const fs::path& path,size_t limit){
 Require(path.is_absolute()&&fs::canonical(path)==path,"FEX disk input linked/noncanonical");
 const int fd=open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);Require(fd>=0,"FEX disk input open");
 struct Guard{int fd;~Guard(){close(fd);}} guard{fd};struct stat before{},after{};
 Require(fstat(fd,&before)==0&&S_ISREG(before.st_mode)&&before.st_uid==getuid()&&before.st_nlink==1&&!(before.st_mode&0022)&&before.st_size>0&&uint64_t(before.st_size)<=limit,"FEX disk input ownership/size");
 std::vector<std::byte> bytes(size_t(before.st_size));size_t done=0;
 while(done<bytes.size()){const auto n=read(fd,bytes.data()+done,bytes.size()-done);if(n<0&&errno==EINTR)continue;Require(n>0,"FEX disk short read");done+=size_t(n);}
 Require(fstat(fd,&after)==0&&before.st_dev==after.st_dev&&before.st_ino==after.st_ino&&before.st_size==after.st_size&&before.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec&&before.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec&&before.st_ctimespec.tv_sec==after.st_ctimespec.tv_sec&&before.st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec,"FEX disk input changed");return bytes;
}
inline void Publish(const fs::path& path,std::span<const std::byte> bytes){
 Require(path.is_absolute()&&fs::canonical(path.parent_path())==path.parent_path(),"FEX output parent linked");
 auto temp=path;temp+=".new-"+std::to_string(getpid());int fd=open(temp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);Require(fd>=0,"FEX fresh output unavailable");
 try{size_t at=0;while(at<bytes.size()){const auto n=write(fd,bytes.data()+at,bytes.size()-at);if(n<0&&errno==EINTR)continue;Require(n>0,"FEX short output write");at+=size_t(n);}Require(fsync(fd)==0,"FEX output sync");close(fd);fd=-1;Require(link(temp.c_str(),path.c_str())==0,"FEX output preserves existing product");unlink(temp.c_str());}
 catch(...){if(fd>=0)close(fd);unlink(temp.c_str());throw;}
}
}
