// SPDX-License-Identifier: GPL-2.0-or-later
// Synthetic ARM64 Mach-O metadata only; no fixture is executed as game/GPU.
#import <Foundation/Foundation.h>
#include "bundled_runtime_admission.h"
#include <CommonCrypto/CommonDigest.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <cassert>
#include <cstring>
#include <fstream>
#include <functional>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
namespace fs=std::filesystem;
using namespace BbBundledRuntime;
static unsigned passed=0;
static void Refused(const std::function<void()>&body){bool refused=false;try{body();}catch(const std::exception&){refused=true;}assert(refused);++passed;}
static NSString* Ns(const std::string&value){return [[NSString alloc]initWithBytes:value.data() length:value.size() encoding:NSUTF8StringEncoding];}
static void Put(const fs::path&p,const std::vector<unsigned char>&bytes){fs::create_directories(p.parent_path());std::ofstream file(p,std::ios::binary);file.write(reinterpret_cast<const char*>(bytes.data()),bytes.size());file.close();assert(file);chmod(p.c_str(),0644);}
static void Put(const fs::path&p,const std::string&s){Put(p,std::vector<unsigned char>(s.begin(),s.end()));}
static std::string Hash(const fs::path&p){std::ifstream input(p,std::ios::binary);CC_SHA256_CTX h;CC_SHA256_Init(&h);char buffer[65536];while(input){input.read(buffer,sizeof(buffer));if(input.gcount())CC_SHA256_Update(&h,buffer,CC_LONG(input.gcount()));}unsigned char digest[32];CC_SHA256_Final(digest,&h);std::string result;constexpr char hex[]="0123456789abcdef";for(auto b:digest){result+=hex[b>>4];result+=hex[b&15];}return result;}
static void Json(const fs::path&p,id object){NSError*error=nil;NSData*data=[NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingPrettyPrinted error:&error];assert(data&&!error);const auto*b=static_cast<const unsigned char*>(data.bytes);Put(p,std::vector<unsigned char>(b,b+data.length));}
static std::vector<unsigned char> Image(uint32_t kind,const std::string&edge={},const std::vector<std::string>&rpaths={}){
 std::vector<unsigned char> commands;auto add=[&](uint32_t command,const std::string&text,bool rpath){const size_t prefix=rpath?sizeof(rpath_command):sizeof(dylib_command),size=(prefix+text.size()+1+7)&~size_t(7);std::vector<unsigned char> bytes(size);if(rpath){rpath_command value{};value.cmd=command;value.cmdsize=size;value.path.offset=prefix;memcpy(bytes.data(),&value,sizeof(value));}else{dylib_command value{};value.cmd=command;value.cmdsize=size;value.dylib.name.offset=prefix;memcpy(bytes.data(),&value,sizeof(value));}memcpy(bytes.data()+prefix,text.c_str(),text.size()+1);commands.insert(commands.end(),bytes.begin(),bytes.end());};
 if(!edge.empty())add(LC_LOAD_DYLIB,edge,false);for(const auto&p:rpaths)add(LC_RPATH,p,true);
 mach_header_64 header{};header.magic=MH_MAGIC_64;header.cputype=CPU_TYPE_ARM64;header.filetype=kind;header.ncmds=(!edge.empty())+rpaths.size();header.sizeofcmds=commands.size();std::vector<unsigned char> bytes(sizeof(header));memcpy(bytes.data(),&header,sizeof(header));bytes.insert(bytes.end(),commands.begin(),commands.end());return bytes;
}
struct Fixture {
 fs::path root,app,code;NSMutableDictionary*native;NSMutableDictionary*local;NSMutableDictionary*scripts;
 Fixture(const fs::path&base,unsigned index):root(base/('f'+std::to_string(index))),app(root/"App lives here.app"),code(app/"Contents/Resources/bbport"){
  fs::create_directories(code);Info("io.github.bbport.optimized");
  const std::vector<std::string> roles={"bin/bb-probe-metal","bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal","bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu","bin/game-pso-preparer","bin/game-module-preparer"};
  Put(code/"bin/gpu-metal/libbbgpu.dylib",Image(MH_DYLIB));Put(code/"bin/gpu-metal-arm64/libbbgpu.dylib",Image(MH_DYLIB));Put(code/"bin/gpu-net/libbbnet.dylib",Image(MH_DYLIB));
  for(const auto&name:roles){auto target=name.find("bb-probe-metal")!=name.npos?code/"bin/gpu-metal/libbbgpu.dylib":code/"bin/gpu-metal-arm64/libbbgpu.dylib";auto executable=code/name;Put(executable,Image(MH_EXECUTE,"@loader_path/"+target.lexically_relative(executable.parent_path()).string()));chmod(executable.c_str(),0755);}
  native=[@{@"schema":@1,@"game_runtime":@"fex-arm64",@"production":@YES,@"frozen_runtime":[NSMutableDictionary new]} mutableCopy];
  for(const auto&name:roles)native[@"frozen_runtime"][Ns(name)]=Ns(Hash(code/name));
  for(const auto&name:{"bin/gpu-metal/libbbgpu.dylib","bin/gpu-metal-arm64/libbbgpu.dylib","bin/gpu-net/libbbnet.dylib"})native[@"frozen_runtime"][Ns(name)]=Ns(Hash(code/name));
  NSMutableDictionary*tools=[NSMutableDictionary new];for(const auto&name:{"launcher_local_aot.py","prepare_module_metadata.py"}){Put(code/"native-runtime-tools"/name,"# authored tool\n");tools[Ns(name)]=Ns(Hash(code/"native-runtime-tools"/name));}
  NSMutableDictionary*support=[NSMutableDictionary new];for(const auto&name:{"vendor/blood_echoes/loader/prepare.py","vendor/blood_echoes/loader/game_check.py"}){Put(code/name,"# authored original parser\n");support[Ns(name)]=Ns(Hash(code/name));}
  Put(code/"tools/macos/local_metal_startup.py","# authored startup\n");local=[@{@"schema":@1,@"runtime_tools":tools,@"runtime_support":support,@"startup_bootstrap_sha256":Ns(Hash(code/"tools/macos/local_metal_startup.py"))} mutableCopy];
  scripts=[NSMutableDictionary new];for(unsigned i=0;i<16;++i){auto name="scripts/authored-"+std::to_string(i)+".py";Put(code/name,"# authored effective script\n");scripts[Ns(name)]=Ns(Hash(code/name));}Seal();
 }
 void Info(const std::string&id,bool binary=false){NSDictionary*info=@{@"CFBundleIdentifier":Ns(id),@"CFBundleExecutable":@"bbport",@"CFBundlePackageType":@"APPL"};NSError*error=nil;NSData*data=[NSPropertyListSerialization dataWithPropertyList:info format:binary?NSPropertyListBinaryFormat_v1_0:NSPropertyListXMLFormat_v1_0 options:0 error:&error];assert(data&&!error);const auto*b=static_cast<const unsigned char*>(data.bytes);Put(app/"Contents/Info.plist",std::vector<unsigned char>(b,b+data.length));}
 void Seal(){Json(code/"NATIVE-METAL.json",native);Json(code/"ARM64-LOCAL-PREPARATION.json",local);Json(code/"SOURCE-PARITY.json",@{@"effective_sources":scripts});}
 auto Admit(const std::string&relative="bin/bb-probe-metal"){return TryAdmit(code/relative);}
};
int main(int argc,char**argv){@autoreleasepool{
 assert(argc==2);const auto base=fs::canonical(argv[1]);assert(fs::is_empty(base));unsigned n=0;
 {Fixture f(base,++n);for(const auto&relative:{"bin/bb-probe-metal","bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal","bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu","bin/game-pso-preparer","bin/game-module-preparer"}){const auto result=f.Admit(relative);assert(result&&result->code_root==f.code&&result->static_load_edges_verified&&!result->dynamic_load_edges_verified&&!result->signatures_verified);++passed;}}
 {Fixture f(base,++n);f.Info("io.github.bbport.optimized",true);assert(f.Admit());++passed;}
 {Fixture f(base,++n);const auto alias=f.root/"alias-cpu";fs::create_symlink(f.code/"bin/bb-probe-metal",alias);assert(TryAdmit(alias)->role==Role::CpuTop);++passed;assert(TryAdmit(f.code/"bin/../bin/bb-probe-metal")->role==Role::CpuTop);++passed;const auto wrong=f.root/"cpu-named-alias";fs::create_symlink(f.code/"bin/game-pso-preparer",wrong);assert(TryAdmit(wrong)->role==Role::PsoWorker);++passed;}
 {Fixture f(base,++n);f.Info("io.github.bbport.optimized.options-qa.arm64.gui.123");Put(f.code/"NATIVE-METAL.json","not even JSON; QA must use old P branch");assert(!f.Admit());++passed;const auto alias=f.root/"qa-alias";fs::create_symlink(f.code/"bin/bb-probe-metal",alias);assert(!TryAdmit(alias));++passed;}
 {Fixture f(base,++n);Put(f.code/"bin/unknown",Image(MH_EXECUTE));Refused([&]{f.Admit("bin/unknown");});}
 {Fixture f(base,++n);Put(f.code/"bin/bb-probe-metal","changed code");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);f.native[@"production"]=@NO;f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);f.native[@"schema"]=@YES;f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);[f.native[@"frozen_runtime"] removeObjectForKey:@"bin/bb-probe-metal"];f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);Put(f.code/"native-runtime-tools/prepare_module_metadata.py","changed helper");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);Put(f.code/"tools/macos/local_metal_startup.py","changed bootstrap");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);Put(f.code/"vendor/blood_echoes/loader/prepare.py","changed original loader");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);Put(f.code/"scripts/authored-3.py","changed effective script");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);f.local[@"runtime_tools"][@"../outside.py"]=@"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);auto file=f.code/"bin/gpu-metal-arm64/libbbgpu.dylib",old=f.root/"old-library";fs::rename(file,old);fs::create_symlink(old,file);Refused([&]{f.Admit();});}
 {Fixture f(base,++n);fs::create_hard_link(f.code/"bin/gpu-metal-arm64/libbbgpu.dylib",f.root/"hardlink");Refused([&]{f.Admit();});}
 {Fixture f(base,++n);chmod((f.code/"bin/bb-probe-metal").c_str(),0777);Refused([&]{f.Admit();});}
 {Fixture f(base,++n);auto file=f.code/"bin/bb-probe-metal";Put(file,Image(MH_EXECUTE,"@loader_path/gpu-metal-arm64/libbbgpu.dylib"));f.native[@"frozen_runtime"][@"bin/bb-probe-metal"]=Ns(Hash(file));f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);auto file=f.code/"bin/bb-probe-metal";Put(file,Image(MH_EXECUTE,"@rpath/libbbgpu.dylib",{"@loader_path/gpu-metal","@loader_path/gpu-metal-arm64"}));f.native[@"frozen_runtime"][@"bin/bb-probe-metal"]=Ns(Hash(file));f.Seal();Refused([&]{f.Admit();});}
 {Fixture f(base,++n);auto file=f.code/"bin/bb-probe-metal";Put(file,Image(MH_EXECUTE,"@rpath/libbbgpu.dylib",{"@loader_path/gpu-metal"}));f.native[@"frozen_runtime"][@"bin/bb-probe-metal"]=Ns(Hash(file));f.Seal();assert(f.Admit());++passed;}
 {Fixture f(base,++n);auto gpu=f.code/"bin/Bloodborne Metal.app/Contents/Info.plist";Put(gpu,"<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>CFBundleIdentifier</key><string>io.github.bbport.optimized.metal-game</string></dict></plist>");assert(f.Admit("bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu")->bundle_id=="io.github.bbport.optimized");++passed;}
 assert(!Actual());setenv("BB_ARM_LAUNCHER_COMPAT","1",1);setenv("BB_CODE_ROOT","/Applications/Fake.app",1);assert(!Actual());++passed;
 printf("BUNDLED_ADMISSION_AUTHOR %u passed; no GPU/game/signature or dynamic-load acceptance\n",passed);return 0;
}}
