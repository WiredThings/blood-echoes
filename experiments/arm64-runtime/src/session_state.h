// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utility>
#include <string>
namespace BbSession {
inline void check(bool b,const char* why){if(!b)throw std::runtime_error(why);}
inline void Prepare(const std::filesystem::path& state,const std::filesystem::path& root,const std::string& identity,bool resume){
 namespace fs=std::filesystem;
 check(fs::canonical(state.parent_path())==fs::canonical(root),"state outside prototype");
 auto marker=state/".arm-runtime-session";
 if(resume){
  check(!fs::is_symlink(state)&&fs::is_directory(state)&&!fs::is_symlink(marker)&&fs::is_regular_file(marker),"private resume session missing or linked");
  check(fs::file_size(marker)==identity.size(),"resume session identity size mismatch");
  std::ifstream f(marker);std::string actual((std::istreambuf_iterator<char>(f)),{});check(actual==identity,"resume session identity mismatch");
 } else {
  check(fs::create_directory(state),"state must be fresh");fs::permissions(state,fs::perms::owner_all,fs::perm_options::replace);
  std::ofstream f(marker);f<<identity;f.flush();check(bool(f),"private session marker write failed");
 }
}
// This is runtime scratch, never the progress directory. The launcher caller
// must first read-validate its existing QA source marker with Prepare(...,true).
// Actual bundled callers instead validate exact selected roles and never use a
// QA marker in their progress directory. Both share the fd-anchored work writer.
class OwnedFd {
 int fd_=-1;
public:
 explicit OwnedFd(int fd):fd_(fd){check(fd>=0,"private work descriptor unavailable");}
 ~OwnedFd(){if(fd_>=0)close(fd_);}
 OwnedFd(const OwnedFd&)=delete;OwnedFd&operator=(const OwnedFd&)=delete;
 OwnedFd(OwnedFd&&o)noexcept:fd_(std::exchange(o.fd_,-1)){}
 OwnedFd&operator=(OwnedFd&&o)noexcept{if(this!=&o){if(fd_>=0)close(fd_);fd_=std::exchange(o.fd_,-1);}return *this;}
 int get()const{return fd_;}
};
inline struct stat WorkStat(int fd){struct stat value{};check(!fstat(fd,&value),"private work stat failed");return value;}
inline bool SameWorkNode(const struct stat&a,const struct stat&b){
 return a.st_dev==b.st_dev&&a.st_ino==b.st_ino&&a.st_uid==b.st_uid&&a.st_mode==b.st_mode&&a.st_nlink==b.st_nlink&&a.st_size==b.st_size&&
#if defined(__APPLE__)
 a.st_mtimespec.tv_sec==b.st_mtimespec.tv_sec&&a.st_mtimespec.tv_nsec==b.st_mtimespec.tv_nsec&&a.st_ctimespec.tv_sec==b.st_ctimespec.tv_sec&&a.st_ctimespec.tv_nsec==b.st_ctimespec.tv_nsec;
#else
 a.st_mtim.tv_sec==b.st_mtim.tv_sec&&a.st_mtim.tv_nsec==b.st_mtim.tv_nsec&&a.st_ctim.tv_sec==b.st_ctim.tv_sec&&a.st_ctim.tv_nsec==b.st_ctim.tv_nsec;
#endif
}
inline void WorkDirectory(int fd,bool private_mode){
 const auto value=WorkStat(fd);check(S_ISDIR(value.st_mode)&&value.st_uid==getuid()&&!(value.st_mode&0022)&&(!private_mode||(value.st_mode&0777)==0700),"runtime work must be owned no-link directory with expected permissions");
}
inline OwnedFd OpenWorkChild(int parent,const char*name,bool private_mode){
 OwnedFd fd(openat(parent,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));WorkDirectory(fd.get(),private_mode);return fd;
}
inline bool MakeWorkChild(int parent,const char*name){
 if(!mkdirat(parent,name,0700))return true;
 check(errno==EEXIST,"runtime work directory creation failed");return false;
}
inline void VerifyWorkIdentity(int directory,const std::string&identity){
 OwnedFd marker(openat(directory,".arm-runtime-session",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));const auto before=WorkStat(marker.get());
 check(S_ISREG(before.st_mode)&&before.st_uid==getuid()&&before.st_nlink==1&&(before.st_mode&0777)==0600&&before.st_size==static_cast<off_t>(identity.size()),"runtime work marker missing, linked or wrong size/owner/mode");
 std::string actual(identity.size(),'\0');size_t at=0;
 while(at<actual.size()){const auto got=read(marker.get(),actual.data()+at,actual.size()-at);if(got<0&&errno==EINTR)continue;check(got>0,"runtime work marker read failed");at+=got;}
 char extra;ssize_t got;do{got=read(marker.get(),&extra,1);}while(got<0&&errno==EINTR);
 struct stat named{};check(!got&&!fstatat(directory,".arm-runtime-session",&named,AT_SYMLINK_NOFOLLOW)&&SameWorkNode(before,WorkStat(marker.get()))&&SameWorkNode(before,named)&&actual==identity,"runtime work session identity mismatch or changed marker");
}
inline std::filesystem::path PrepareSelectedWork(const std::filesystem::path&data,OwnedFd selected,
 const std::string&nonce,const std::string&identity){
 namespace fs=std::filesystem;
 OwnedFd settings(openat(selected.get(),"bbport.ini",O_RDONLY|O_NOFOLLOW|O_CLOEXEC));const auto settings_info=WorkStat(settings.get());
 check(S_ISREG(settings_info.st_mode)&&settings_info.st_uid==getuid()&&settings_info.st_nlink==1,"original launcher configuration must be owned ordinary file");
 auto out=OpenWorkChild(selected.get(),"out",false);MakeWorkChild(out.get(),"runtime-sessions");auto sessions=OpenWorkChild(out.get(),"runtime-sessions",true);
 const bool created=MakeWorkChild(sessions.get(),nonce.c_str());auto work=OpenWorkChild(sessions.get(),nonce.c_str(),true);
 if(created){
  OwnedFd marker(openat(work.get(),".arm-runtime-session",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600));size_t at=0;
  while(at<identity.size()){const auto put=write(marker.get(),identity.data()+at,identity.size()-at);if(put<0&&errno==EINTR)continue;check(put>0,"runtime work marker write failed");at+=put;}
  check(!fsync(marker.get()),"runtime work identity commit failed");
 }
 VerifyWorkIdentity(work.get(),identity);
 const auto result=data/"out/runtime-sessions"/nonce;
 check(fs::canonical(result)==result,"runtime work path changed");struct stat named{};
 check(!lstat(result.c_str(),&named)&&SameWorkNode(named,WorkStat(work.get())),"runtime work directory replaced");
 return result;
}
inline std::filesystem::path PrepareLauncherWork(const std::filesystem::path&data,const std::filesystem::path&private_root,
 const std::filesystem::path&config,const std::filesystem::path&boot,const std::filesystem::path&content,const std::string&nonce,const std::string&identity){
 namespace fs=std::filesystem;
 check(data.is_absolute()&&data==data.lexically_normal()&&private_root.is_absolute()&&private_root==private_root.lexically_normal(),"runtime work requires canonical absolute QA roles");
 const auto relative=data.lexically_relative(private_root);
 check(!relative.empty()&&relative!="."&&*relative.begin()!="..","launcher data outside unchanged private state root");
 check(nonce.size()==32&&nonce.find_first_not_of("0123456789abcdef")==std::string::npos&&identity.size()<=16384&&identity.starts_with("ARMQA1\n")&&identity.ends_with("\n"+nonce+"\n"),"runtime work requires the verified bounded QA session identity");
 check(config==data/"bbport.ini"&&boot==data/"out/boot-linked.bin"&&content==data/"out/content.bin","launcher work data/config/boot/content roles do not match");
 check(fs::canonical(private_root)==private_root&&fs::canonical(data)==data,"runtime work QA root/data is linked");
 OwnedFd root(open(private_root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));WorkDirectory(root.get(),false);
 OwnedFd selected(fcntl(root.get(),F_DUPFD_CLOEXEC,0));
 for(const auto&part:relative)selected=OpenWorkChild(selected.get(),part.c_str(),false);
 return PrepareSelectedWork(data,std::move(selected),nonce,identity);
}
// Only a previously admitted actual original-launcher CPU may call this path.
// It uses the exact selected data root, never writes a QA marker under --user.
inline std::filesystem::path PrepareBundledLauncherWork(const std::filesystem::path&data,
 const std::filesystem::path&config,const std::filesystem::path&boot,const std::filesystem::path&content,
 const std::string&nonce,const std::string&identity){
 namespace fs=std::filesystem;
 check(data.is_absolute()&&data==data.lexically_normal()&&fs::canonical(data)==data,"bundled work requires exact canonical data root");
 check(nonce.size()==32&&nonce.find_first_not_of("0123456789abcdef")==std::string::npos&&identity.size()<=16384&&identity.starts_with("ARMLAUNCH1\n")&&identity.ends_with("\n"+nonce+"\n"),"bundled work session identity invalid");
 check(config==data/"bbport.ini"&&boot==data/"out/boot-linked.bin"&&content==data/"out/content.bin","bundled work data/config/boot/content roles mismatch");
 OwnedFd selected(open(data.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC));WorkDirectory(selected.get(),false);
 return PrepareSelectedWork(data,std::move(selected),nonce,identity);
}
}
