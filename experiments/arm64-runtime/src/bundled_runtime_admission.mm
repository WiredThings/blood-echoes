// SPDX-License-Identifier: GPL-2.0-or-later
#import <Foundation/Foundation.h>
#include "bundled_runtime_admission.h"
#include <CommonCrypto/CommonDigest.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <array>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <set>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace BbBundledRuntime {
namespace {
namespace fs=std::filesystem;
constexpr size_t Small=1024*1024, Large=512*1024*1024;
void Need(bool value,const char*why){if(!value)throw std::runtime_error(std::string("bundled runtime admission: ")+why);}
struct Fd {int value;explicit Fd(int v):value(v){Need(v>=0,"code file unavailable");}~Fd(){close(value);}Fd(const Fd&)=delete;};
bool Owner(uid_t uid){return uid==0||uid==getuid();}
bool Same(const struct stat&a,const struct stat&b){return a.st_dev==b.st_dev&&a.st_ino==b.st_ino&&a.st_uid==b.st_uid&&a.st_mode==b.st_mode&&a.st_nlink==b.st_nlink&&a.st_size==b.st_size&&a.st_mtimespec.tv_sec==b.st_mtimespec.tv_sec&&a.st_mtimespec.tv_nsec==b.st_mtimespec.tv_nsec&&a.st_ctimespec.tv_sec==b.st_ctimespec.tv_sec&&a.st_ctimespec.tv_nsec==b.st_ctimespec.tv_nsec;}
struct stat Shape(const fs::path&p,bool directory=false){
 Need(p.is_absolute()&&p==p.lexically_normal()&&p==fs::canonical(p),"canonical no-link code path required");struct stat value{};
 Need(!lstat(p.c_str(),&value)&&Owner(value.st_uid)&&!(value.st_mode&0022)&&
      (directory?S_ISDIR(value.st_mode):(S_ISREG(value.st_mode)&&value.st_nlink==1)),"code must be root/current-user owned and plain");return value;
}
std::string Hex(const std::array<unsigned char,CC_SHA256_DIGEST_LENGTH>&bytes){constexpr char chars[]="0123456789abcdef";std::string out;out.reserve(64);for(auto byte:bytes){out+=chars[byte>>4];out+=chars[byte&15];}return out;}
bool IsHex(const std::string&s){return s.size()==64&&s.find_first_not_of("0123456789abcdef")==s.npos;}
struct ReadResult {std::vector<unsigned char> bytes;std::string sha256;};
struct Ledger {
 std::map<fs::path,struct stat> files;size_t total=0;
 ReadResult Read(const fs::path&p,size_t maximum,bool retain){
  const auto before=Shape(p);Need(before.st_size>0&&uint64_t(before.st_size)<=maximum,"code file size limit");
  Need(uint64_t(before.st_size)<=2ull*1024*1024*1024-total,"code closure byte limit");total+=size_t(before.st_size);
  Fd input(open(p.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC));struct stat opened{};Need(!fstat(input.value,&opened)&&Same(before,opened),"code replaced before read");
  ReadResult result;if(retain)result.bytes.reserve(size_t(before.st_size));CC_SHA256_CTX hash;CC_SHA256_Init(&hash);std::array<unsigned char,65536> block{};size_t count=0;
  for(;;){const auto n=read(input.value,block.data(),block.size());if(n<0&&errno==EINTR)continue;Need(n>=0,"code read failed");if(!n)break;count+=size_t(n);Need(count<=size_t(before.st_size),"code grew during read");CC_SHA256_Update(&hash,block.data(),CC_LONG(n));if(retain)result.bytes.insert(result.bytes.end(),block.begin(),block.begin()+n);}
  struct stat after{};Need(count==size_t(before.st_size)&&!fstat(input.value,&after)&&Same(before,after)&&Same(before,Shape(p)),"code changed during read");
  auto found=files.find(p);Need(found==files.end()||Same(found->second,before),"code replaced between observations");files[p]=before;Need(files.size()<=192,"code closure file count");
  std::array<unsigned char,CC_SHA256_DIGEST_LENGTH> bytes;CC_SHA256_Final(bytes.data(),&hash);result.sha256=Hex(bytes);return result;
 }
 void Stable()const{for(const auto&[p,before]:files)Need(Same(before,Shape(p)),"code closure changed before admission");}
};
std::string Text(id value){
 Need([value isKindOfClass:[NSString class]],"manifest string required");NSData*bytes=[value dataUsingEncoding:NSUTF8StringEncoding];Need(bytes&&bytes.length<=4096,"bounded manifest UTF-8 required");std::string result(static_cast<const char*>(bytes.bytes),bytes.length);Need(result.find('\0')==result.npos,"NUL in manifest string");return result;
}
NSDictionary* Object(id value){Need([value isKindOfClass:[NSDictionary class]],"manifest object required");return value;}
id At(NSDictionary*value,NSString*key){id item=value[key];Need(item!=nil,"manifest field missing");return item;}
bool Boolean(id value){Need(value&&CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID(),"manifest boolean required");return [value boolValue];}
bool One(id value){return [value isKindOfClass:[NSNumber class]]&&CFGetTypeID((__bridge CFTypeRef)value)!=CFBooleanGetTypeID()&&!CFNumberIsFloatType((__bridge CFNumberRef)value)&&[value longLongValue]==1;}
NSDictionary* Json(const ReadResult&raw){
 NSError*error=nil;id value=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:raw.bytes.data() length:raw.bytes.size()] options:0 error:&error];Need(value&&!error,"invalid package JSON");return Object(value);
}
std::string InfoId(const fs::path&app,Ledger&ledger){
 auto raw=ledger.Read(app/"Contents/Info.plist",Small,true);CFDataRef data=CFDataCreate(kCFAllocatorDefault,raw.bytes.data(),raw.bytes.size());Need(data,"plist data unavailable");
 CFErrorRef error=nullptr;CFPropertyListRef plist=CFPropertyListCreateWithData(kCFAllocatorDefault,data,kCFPropertyListImmutable,nullptr,&error);CFRelease(data);
 if(error)CFRelease(error);Need(plist,"invalid actual outer Info.plist");
 try{
  Need(CFGetTypeID(plist)==CFDictionaryGetTypeID(),"actual Info.plist must be a dictionary");auto identifier=CFDictionaryGetValue(static_cast<CFDictionaryRef>(plist),CFSTR("CFBundleIdentifier"));
  Need(identifier&&CFGetTypeID(identifier)==CFStringGetTypeID()&&CFStringGetLength(static_cast<CFStringRef>(identifier))<=256,"actual outer bundle identifier missing");
  std::array<char,1024> buffer{};Need(CFStringGetCString(static_cast<CFStringRef>(identifier),buffer.data(),buffer.size(),kCFStringEncodingUTF8),"bundle identifier UTF-8");std::string value=buffer.data();CFRelease(plist);return value;
 }catch(...){CFRelease(plist);throw;}
}
fs::path Relative(const fs::path&root,const std::string&name){fs::path rel=name;Need(!rel.empty()&&!rel.is_absolute()&&rel==rel.lexically_normal()&&rel!="."&&name.find('\0')==name.npos,"package relative role required");for(const auto&part:rel)Need(part!="..","package role escaped");return root/rel;}
std::map<std::string,std::string> Hashes(id value,size_t limit){
 auto object=Object(value);Need(object.count>0&&object.count<=limit,"bounded code closure map required");std::map<std::string,std::string> result;
 for(id key in object){auto name=Text(key),digest=Text(object[key]);Need(IsHex(digest),"package SHA256 required");result.emplace(std::move(name),std::move(digest));}return result;
}
struct RolePath {Role role;const char*relative;};
constexpr const char* BbcpuCpuPath="bin/Bloodborne bbcpu Game.app/Contents/MacOS/bb-probe-metal";
constexpr const char* BbcpuPreparerPath="bin/bbcpu-tools/bbcpu-code-preparer";
constexpr RolePath Roles[]={{Role::CpuTop,"bin/bb-probe-metal"},{Role::CpuNested,"bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal"},{Role::Gpu,"bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu"},{Role::PsoWorker,"bin/game-pso-preparer"},{Role::ModuleWorker,"bin/game-module-preparer"},{Role::BbcpuCpu,BbcpuCpuPath},{Role::BbcpuPreparer,BbcpuPreparerPath}};
struct CpuKit {
 std::map<std::string,std::string> files;std::string host;
 static constexpr const char* Abi="bin/bbcpu-tools/bbcpu-abi-identity.json";
};
CpuKit BbcpuKit(NSDictionary*native,const fs::path&code,Ledger&ledger){
 CpuKit kit;auto mode=Text(At(native,@"game_runtime"));
 if(mode=="fex-arm64")return kit;
 Need(mode=="arm64","unknown formal CPU runtime");id names=At(native,@"cpu_backends");
 Need([names isKindOfClass:[NSArray class]]&&[(NSArray*)names isEqualToArray:@[@"fex",@"bbcpu"]],"explicit independent CPU backends required");
 auto raw=ledger.Read(code/"BBCPU-COMPONENT.json",Small,true);Need(raw.sha256==Text(At(native,@"cpu_component_sha256")),"CPU component descriptor SHA mismatch");
 auto doc=Json(raw);Need(One(At(doc,@"schema"))&&!Boolean(At(doc,@"development_only"))&&Text(At(doc,@"backend"))=="bbcpu"&&Text(At(doc,@"default_execution"))=="static"&&One(At(doc,@"runtime_image_export")),"formal static bbcpu component required");
 for(auto [key,name]:std::initializer_list<std::pair<NSString*,const char*>>{{@"cpu",BbcpuCpuPath},{@"preparer",BbcpuPreparerPath},{@"scanner","bin/bbcpu-tools/bbcpu-scan"},{@"generator","bin/bbcpu-tools/bbrecomp"},{@"abi",CpuKit::Abi}}){
  auto role=Object(At(doc,key));auto path=Text(At(role,@"path")),sha=Text(At(role,@"sha256"));
  Need(path==name&&IsHex(sha),"CPU role path/digest mismatch");kit.files.emplace(path,sha);
 }
 for(const auto&[name,sha]:Hashes(At(doc,@"runtime_dependencies"),64)){
  const auto path=fs::path(name);Relative(code,name);
  Need(name.starts_with("bbcpu-deps/lib/")&&path.extension()==".dylib"&&kit.files.emplace(name,sha).second,"CPU dependency role mismatch");
  if(path.filename()=="libbbgpu.dylib"){Need(kit.host.empty(),"duplicate CPU renderer host");kit.host=name;}
 }
 Need(!kit.host.empty(),"CPU renderer host dependency missing");return kit;
}
std::optional<std::pair<RolePath,fs::path>> Locate(const fs::path&executable){
 const auto text=executable.string();for(auto item:Roles){const auto suffix="/"+std::string(item.relative);if(!text.ends_with(suffix))continue;auto code=executable;for(const auto&part:fs::path(item.relative)){(void)part;code=code.parent_path();}
  if(code.filename()=="bbport"&&code.parent_path().filename()=="Resources"&&code.parent_path().parent_path().filename()=="Contents"&&code.parent_path().parent_path().parent_path().extension()==".app")return std::pair(item,code);
 }return std::nullopt;
}
bool FormalAncestor(const fs::path&executable){
 size_t count=0;for(auto current=executable.parent_path();!current.empty()&&current!=current.parent_path();current=current.parent_path()){
  if(current.extension()!=".app")continue;Need(++count<=8,"application nesting bound");
  if(!fs::exists(current/"Contents/Info.plist"))continue;Ledger info;if(InfoId(current,info)=="io.github.bbport.optimized")return true;
 }return false;
}
struct Image {std::vector<std::string> edges,rpaths;};
Image MachO(const fs::path&p,uint32_t kind,const Ledger&ledger){
 auto record=ledger.files.find(p);Need(record!=ledger.files.end(),"Mach-O image not SHA-admitted");Fd file(open(p.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC));struct stat before{};Need(!fstat(file.value,&before)&&Same(record->second,before),"Mach-O image changed");
 mach_header_64 header{};Need(pread(file.value,&header,sizeof(header),0)==sizeof(header)&&header.magic==MH_MAGIC_64&&header.cputype==CPU_TYPE_ARM64&&header.filetype==kind&&header.ncmds<=4096&&header.sizeofcmds<=Small&&sizeof(header)+uint64_t(header.sizeofcmds)<=uint64_t(before.st_size),"thin ARM64 image/commands required");
 std::vector<unsigned char> commands(header.sizeofcmds);Need(pread(file.value,commands.data(),commands.size(),sizeof(header))==ssize_t(commands.size()),"Mach-O command read");Image result;size_t at=0;
 auto string=[&](const load_command&command,uint32_t offset){Need(offset>=sizeof(load_command)&&offset<command.cmdsize,"Mach-O string offset");const auto*begin=reinterpret_cast<const char*>(commands.data()+at+offset);const auto*end=static_cast<const char*>(memchr(begin,0,command.cmdsize-offset));Need(end&&end>begin&&end-begin<=4096,"Mach-O command string");return std::string(begin,end);};
 for(uint32_t i=0;i<header.ncmds;++i){Need(at+sizeof(load_command)<=commands.size(),"Mach-O command extent");load_command command;memcpy(&command,commands.data()+at,sizeof(command));Need(command.cmdsize>=sizeof(command)&&command.cmdsize%8==0&&command.cmdsize<=commands.size()-at,"Mach-O command size");
  if(command.cmd==LC_LOAD_DYLIB||command.cmd==LC_LOAD_WEAK_DYLIB||command.cmd==LC_REEXPORT_DYLIB||command.cmd==LC_LOAD_UPWARD_DYLIB||command.cmd==LC_LAZY_LOAD_DYLIB){Need(command.cmdsize>=sizeof(dylib_command),"Mach-O dylib command size");dylib_command dylib;memcpy(&dylib,commands.data()+at,sizeof(dylib));auto edge=string(command,dylib.dylib.name.offset);if(fs::path(edge).filename().string().starts_with("libbbgpu"))result.edges.push_back(std::move(edge));}
  if(command.cmd==LC_RPATH){Need(command.cmdsize>=sizeof(rpath_command),"Mach-O RPATH command size");rpath_command rpath;memcpy(&rpath,commands.data()+at,sizeof(rpath));result.rpaths.push_back(string(command,rpath.path.offset));Need(result.rpaths.size()<=64,"Mach-O RPATH count");}at+=command.cmdsize;
 }Need(at==commands.size(),"Mach-O command trailing bytes");struct stat after{};Need(!fstat(file.value,&after)&&Same(before,after)&&Same(before,Shape(p)),"Mach-O changed during command read");return result;
}
fs::path Resolve(const fs::path&target,const std::string&edge,const std::vector<std::string>&rpaths){
 auto expand=[&](const std::string&value)->fs::path{
  for(auto prefix:{std::string("@loader_path/"),std::string("@executable_path/")})if(value.starts_with(prefix))return target.parent_path()/value.substr(prefix.size());
  if(value=="@loader_path"||value=="@executable_path")return target.parent_path();Need(value.starts_with("/"),"unresolved package load path");return value;
 };
 if(!edge.starts_with("@rpath/"))return fs::canonical(expand(edge));
 const fs::path suffix=edge.substr(7);Need(!suffix.is_absolute(),"invalid package RPATH suffix");for(const auto&part:suffix)Need(part!="..","package RPATH suffix escaped");
 std::set<fs::path> matches;for(const auto&entry:rpaths){auto candidate=expand(entry)/suffix;if(fs::exists(candidate))matches.insert(fs::canonical(candidate));}
 Need(matches.size()==1,"package RPATH must resolve uniquely");return *matches.begin();
}
}

std::optional<Admission> TryAdmit(const fs::path&actual_executable,const fs::path&qa_project){@autoreleasepool {
 // dyld may report a launch alias or ../ spelling. Identity follows the actual
 // target, never the alias basename; declared package closure paths stay plain.
 const auto executable=fs::canonical(fs::absolute(actual_executable));Shape(executable);const auto located=Locate(executable);
 if(!located){Need(!FormalAncestor(executable),"formal application executable role is not declared");return std::nullopt;}
 const auto [selected,code]=*located;const auto app=code.parent_path().parent_path().parent_path();Shape(app,true);Shape(code,true);Ledger ledger;
 // The outer actual Info is used, never CFBundleGetMainBundle (GPU is nested).
 const auto identifier=InfoId(app,ledger);if(identifier!="io.github.bbport.optimized")return std::nullopt;
 Admission result;result.role=selected.role;result.relative_role=selected.relative;result.executable=executable;result.app=app;result.code_root=code;result.bundle_id=identifier;
 auto native=ledger.Read(code/"NATIVE-METAL.json",Small,true);result.native_manifest_sha256=native.sha256;auto object=Json(native);
 Need(One(At(object,@"schema"))&&Boolean(At(object,@"production")),"formal component manifest required");
 const auto kit=BbcpuKit(object,code,ledger);
 if(selected.role==Role::BbcpuCpu||selected.role==Role::BbcpuPreparer)Need(!kit.files.empty(),"selected bbcpu role is not declared");
 result.runtime_sha256=Hashes(At(object,@"frozen_runtime"),64);std::set<std::string> allowed={"bin/gpu-metal/libbbgpu.dylib","bin/gpu-metal-arm64/libbbgpu.dylib","bin/gpu-net/libbbnet.dylib"};for(size_t i=0;i<5;++i)allowed.insert(Roles[i].relative);
 for(const auto&[name,sha]:kit.files){allowed.insert(name);Need(result.runtime_sha256.contains(name)&&result.runtime_sha256.at(name)==sha,"frozen CPU kit descriptor differs");}
 for(const auto&[name,digest]:result.runtime_sha256){Need(allowed.contains(name),"unknown frozen runtime role");auto file=Relative(code,name);Need(ledger.Read(file,Large,false).sha256==digest,"frozen runtime SHA mismatch");result.runtime[name]=file;}
 for(auto name:{"bin/gpu-metal/libbbgpu.dylib","bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu","bin/gpu-metal-arm64/libbbgpu.dylib","bin/game-pso-preparer","bin/game-module-preparer"})Need(result.runtime.contains(name),"formal runtime component missing");
 Need(result.runtime.contains("bin/bb-probe-metal")||result.runtime.contains("bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal"),"formal CPU role missing");Need(result.runtime.contains(result.relative_role)&&result.runtime.at(result.relative_role)==executable,"actual executable is not in frozen runtime");
 auto local=Json(ledger.Read(code/"ARM64-LOCAL-PREPARATION.json",Small,true));Need(One(At(local,@"schema")),"local tool closure schema");result.tools_sha256=Hashes(At(local,@"runtime_tools"),128);result.support_sha256=Hashes(At(local,@"runtime_support"),8);
 for(const auto&[name,digest]:result.tools_sha256){Need(fs::path(name).filename()==name&&fs::path(name).extension()==".py","tool must be an exact Python basename");auto file=Relative(code/"native-runtime-tools",name);Need(ledger.Read(file,2*Small,false).sha256==digest,"bundled tool SHA mismatch");result.tools[name]=file;}
 Need(result.tools.contains("launcher_local_aot.py")&&result.tools.contains("prepare_module_metadata.py"),"required bundled tool missing");
 const std::set<std::string> support={"vendor/blood_echoes/loader/prepare.py","vendor/blood_echoes/loader/game_check.py"};Need(result.support_sha256.size()==support.size(),"exact original parser closure required");
 for(const auto&[name,digest]:result.support_sha256){Need(support.contains(name),"unknown parser closure path");auto file=Relative(code,name);Need(ledger.Read(file,2*Small,false).sha256==digest,"bundled parser SHA mismatch");result.support[name]=file;}
 const auto bootstrap=Text(At(local,@"startup_bootstrap_sha256"));Need(IsHex(bootstrap)&&ledger.Read(code/"tools/macos/local_metal_startup.py",2*Small,false).sha256==bootstrap,"bundled startup SHA mismatch");
 auto parity=Json(ledger.Read(code/"SOURCE-PARITY.json",Small,true));const auto scripts=Hashes(At(parity,@"effective_sources"),32);Need(scripts.size()==16,"complete original script closure required");for(const auto&[name,digest]:scripts)Need(ledger.Read(Relative(code,name),2*Small,false).sha256==digest,"effective script SHA mismatch");
 for(const auto&[name,file]:result.runtime){
  if(name==CpuKit::Abi&&kit.files.contains(name)){Json(ledger.Read(file,Small,true));continue;}
  const auto image=MachO(file,file.extension()==".dylib"?MH_DYLIB:MH_EXECUTE,ledger);
  if(kit.files.contains(name)){
   bool host=false;
   for(const auto&edge:image.edges){const auto resolved=Resolve(file,edge,image.rpaths);bool declared=false;
    for(const auto&[role,sha]:kit.files)if(role!=CpuKit::Abi&&result.Runtime(role)==resolved){declared=true;break;}
    Need(declared,"CPU dependency edge escaped frozen kit");host=host||resolved==result.Runtime(kit.host);
   }
   if(name==BbcpuCpuPath||name==BbcpuPreparerPath)Need(host,"CPU actual renderer host edge missing");
   continue;
  }
  const fs::path*expected=nullptr;if(name=="bin/bb-probe-metal"||name=="bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal")expected=&result.Runtime("bin/gpu-metal/libbbgpu.dylib");
  else if(name=="bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu"||name=="bin/game-pso-preparer"||name=="bin/game-module-preparer")expected=&result.Runtime("bin/gpu-metal-arm64/libbbgpu.dylib");
  if(expected)Need(image.edges.size()==1&&Resolve(file,image.edges.front(),image.rpaths)==*expected,"actual static executable/GPU load edge mismatch");
 }
 ledger.Stable();result.static_load_edges_verified=true;
 (void)qa_project; // nullopt is not QA authorization; consumers retain old P checks.
 return result;
}}
const std::optional<Admission>& Actual(){
 static const auto result=[](){std::array<char,4096> path{};uint32_t size=path.size();Need(!_NSGetExecutablePath(path.data(),&size),"actual executable path bound");return TryAdmit(path.data());}();return result;
}
}

extern "C" int bb_bundled_net_module_path(const char*module,const char*executable){
 try{
  const auto& admission=BbBundledRuntime::Actual();if(!admission)return -1;
  if(!admission->IsCpu()||!module||!executable||std::filesystem::canonical(executable)!=admission->executable)return 0;
  const auto found=admission->runtime.find("bin/gpu-net/libbbnet.dylib");
  return found!=admission->runtime.end()&&std::filesystem::path(module)==found->second&&std::filesystem::canonical(module)==found->second;
 }catch(const std::exception&){return 0;}
}
