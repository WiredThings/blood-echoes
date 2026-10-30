// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "video_core/renderer_metal/mtl_phase_counters.h"
// Private Objective-C++ helper. OFF does no filesystem work. No compiler processes,
// shader/PSO residency, guest pointers, or work on an ordinary draw cache hit.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <CommonCrypto/CommonDigest.h>
#include <atomic>
#include <array>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <sys/stdio.h>
#include <unistd.h>
#include <fcntl.h>

namespace Metal::LocalAot {
inline constexpr size_t MaxSource=1024*1024,MaxLibrary=32*1024*1024;
inline constexpr const char* Recipe="bb-metal-local-aot-v1";
inline constexpr const char* InvariantRecipe="bb-metal-local-aot-invariant-v1";
inline bool Hex(std::string_view s){if(s.size()!=64)return false;for(char c:s)if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;return true;}
inline std::string Sha(const void* bytes,size_t size){
 unsigned char out[CC_SHA256_DIGEST_LENGTH];CC_SHA256(bytes,static_cast<CC_LONG>(size),out);
 constexpr char h[]="0123456789abcdef";std::string result;result.reserve(64);
 for(auto b:out){result+=h[b>>4];result+=h[b&15];}return result;
}
inline std::string Key(std::string_view cohort,std::string_view sha,std::string_view entry,bool relaxed,bool preserve_invariance=false){
 std::string value;for(auto field:std::array<std::string_view,7>{preserve_invariance?InvariantRecipe:Recipe,cohort,sha,entry,"metal3.1",relaxed?"relaxed":"safe","fast"}){
  value+=std::to_string(field.size());value+=':';value+=field;
 }if(preserve_invariance){constexpr std::string_view flag="preserve-invariance=1";value+=std::to_string(flag.size());value+=':';value+=flag;}
 return Sha(value.data(),value.size());
}
inline bool Child(const std::filesystem::path& child,const std::filesystem::path& parent){
 auto rel=child.lexically_relative(parent);return !rel.empty()&&rel!="."&&!rel.is_absolute()&&*rel.begin()!="..";
}
inline bool Directory(const std::filesystem::path& path,bool create){
 // Create one level only, after checking its immediate parent. Recursive creation
 // before canonical validation can otherwise write through an existing symlink.
 if(!path.is_absolute())return false;std::error_code ec;struct stat st{};
 if(create){
  const auto parent=path.parent_path();
  if(lstat(parent.c_str(),&st)!=0||!S_ISDIR(st.st_mode)||st.st_uid!=getuid()||std::filesystem::canonical(parent,ec)!=parent||ec)return false;
  if(mkdir(path.c_str(),0700)!=0&&errno!=EEXIST)return false;
 }
 return lstat(path.c_str(),&st)==0&&S_ISDIR(st.st_mode)&&st.st_uid==getuid()&&std::filesystem::canonical(path,ec)==path&&!ec;
}
inline NSData* Read(const std::filesystem::path& path,size_t limit){
 // Validate the actual descriptor, including ownership and the regular-file shape.
 int fd=open(path.c_str(),O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return nil;
 struct stat st{};bool valid=fstat(fd,&st)==0&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_size>0&&static_cast<uint64_t>(st.st_size)<=limit;
 if(!valid){close(fd);return nil;}
 NSMutableData* data=[NSMutableData dataWithLength:static_cast<NSUInteger>(st.st_size)];size_t at=0;
 while(at<data.length){auto n=read(fd,static_cast<char*>(data.mutableBytes)+at,data.length-at);if(n<0&&errno==EINTR)continue;if(n<=0){close(fd);return nil;}at+=n;}
 struct stat end{};valid=fstat(fd,&end)==0&&st.st_ino==end.st_ino&&st.st_dev==end.st_dev&&st.st_size==end.st_size&&st.st_mtimespec.tv_sec==end.st_mtimespec.tv_sec&&st.st_mtimespec.tv_nsec==end.st_mtimespec.tv_nsec;
 close(fd);return valid?data:nil;
}
inline NSDictionary* Json(const std::filesystem::path& path){
 auto data=Read(path,16384);if(!data)return nil;id obj=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
 return [obj isKindOfClass:[NSDictionary class]]?obj:nil;
}
inline bool String(NSDictionary* object,NSString* key,std::string_view expected){
 id v=object[key];if(![v isKindOfClass:[NSString class]])return false;
 NSData* bytes=[(NSString*)v dataUsingEncoding:NSUTF8StringEncoding];return bytes.length==expected.size()&&memcmp(bytes.bytes,expected.data(),expected.size())==0;
}
inline bool Integer(NSDictionary* object,NSString* key,uint64_t expected){
 id value=object[key];return value&&CFGetTypeID((__bridge CFTypeRef)value)==CFNumberGetTypeID()&&
  !CFNumberIsFloatType((__bridge CFNumberRef)value)&&[(NSNumber*)value unsignedLongLongValue]==expected;
}
inline bool Publish(const std::filesystem::path& path,NSData* bytes){
 if(!bytes)return false;static std::atomic<uint64_t> seq{};
 auto tmp=path.string()+".tmp-"+std::to_string(getpid())+"-"+std::to_string(seq.fetch_add(1));
 int fd=open(tmp.c_str(),O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW|O_CLOEXEC,0600);if(fd<0)return false;
 size_t at=0;bool ok=true;while(at<bytes.length){auto n=write(fd,static_cast<const char*>(bytes.bytes)+at,bytes.length-at);if(n<0&&errno==EINTR)continue;if(n<=0){ok=false;break;}at+=n;}
 if(ok)ok=fsync(fd)==0;if(close(fd)!=0)ok=false;
 // Requests are immutable by content key. A pre-existing successful request is
 // never replaced, and concurrent identical publishers can simply retain it.
 // Darwin's exclusive rename publishes exactly one link and never replaces an
 // existing immutable request. A crash can leave a temp, but cannot leave the
 // final file with a second temporary hardlink as link+unlink did.
 if(ok&&renamex_np(tmp.c_str(),path.c_str(),RENAME_EXCL)!=0)ok=errno==EEXIST;
 unlink(tmp.c_str());return ok;
}
// Content-addressed leaves bound directory size without limiting full-game discovery.
inline bool RequestShard(const std::filesystem::path& folder,std::string_view key,
                         std::filesystem::path& requests){
 if(!Hex(key)||!Directory(folder,true))return false;
 auto root=folder/"requests";if(!Directory(root,true))return false;
 requests=root/std::string(key.substr(0,2));return Directory(requests,true);
}
struct Result{ id<MTLLibrary> library{};id<MTLFunction> function{};std::string key;std::string reason;bool recorded{};
 // Successful loaded-byte provenance; populated from the same pinned data already verified.
 std::string source_sha256,cohort,build_id,library_sha256;
};
inline Result Try(id<MTLDevice> device,const std::string& source,const std::string& entry,bool relaxed,bool allow_record=false,bool allow_lookup=true,id<MTLFunction> accepted_function=nil,bool preserve_invariance=false){
 const char* mode_env=std::getenv("BB_MTL_LOCAL_AOT_MODE");
 if(!mode_env)return {};
 std::string_view mode=mode_env;if(mode!="record"&&mode!="load")return {};
 Result result;if(!device){result.reason="device";return result;}const char* root_env=std::getenv("BB_MTL_LOCAL_AOT_ROOT");const char* cohort_env=std::getenv("BB_MTL_LOCAL_AOT_COHORT");const char* gpu_env=std::getenv("BB_GPU_USER_DIR");
 if(!root_env||!cohort_env||!gpu_env||!Hex(cohort_env)){result.reason="configuration";return result;}
 if(source.empty()||source.size()>MaxSource||source.find('\0')!=std::string::npos||entry.empty()||entry.size()>256||entry.find('\0')!=std::string::npos){result.reason="source-budget";return result;}
 std::filesystem::path root=root_env,gpu=gpu_env;std::error_code ec;
 if(!root.is_absolute()||!gpu.is_absolute()||!Directory(gpu,false)||!Directory(root,false)||!Child(root,gpu)){result.reason="private-root";return result;}
 std::string cohort=cohort_env;const auto source_sha=Sha(source.data(),source.size());result.key=Key(cohort,source_sha,entry,relaxed,preserve_invariance);
 result.source_sha256=source_sha;result.cohort=cohort;
 auto folder=root/cohort;auto requests=folder/"requests";
 const auto options=[&](NSDictionary* json){
  id invariant=json[@"preserve_invariance"];
  const bool invariant_matches=preserve_invariance
      ? invariant&&CFGetTypeID((__bridge CFTypeRef)invariant)==CFBooleanGetTypeID()&&[(NSNumber*)invariant boolValue]
      : invariant==nil;
  return invariant_matches&&Integer(json,@"schema",preserve_invariance?3:1)&&
   String(json,@"key",result.key)&&String(json,@"cohort",cohort)&&String(json,@"source_sha256",source_sha)&&String(json,@"entry",entry)&&
   String(json,@"language","metal3.1")&&String(json,@"math_mode",relaxed?"relaxed":"safe")&&String(json,@"math_fp32","fast")&&String(json,@"recipe",preserve_invariance?InvariantRecipe:Recipe);
 };
 if(mode=="load"&&allow_lookup){
  const char* build_env=std::getenv("BB_MTL_LOCAL_AOT_BUILD_ID");
  if(build_env&&Hex(build_env)){
   auto artifacts=folder/"artifacts"/build_env;
   if(Directory(folder,false)&&Directory(artifacts,false)){
    auto receipt=Json(artifacts/(result.key+".json"));
    if(receipt&&options(receipt)&&String(receipt,@"build_id",build_env)){
     auto data=Read(artifacts/(result.key+".metallib"),MaxLibrary);
     const auto library_sha=data?Sha(data.bytes,data.length):std::string{};
     if(data&&Integer(receipt,@"library_bytes",data.length)&&String(receipt,@"library_sha256",library_sha)){
      NSError* error=nil;dispatch_data_t input=dispatch_data_create(data.bytes,data.length,dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{(void)data.length;});
      BbMetalPhase::Call bb_metal_call_125{BbMetalPhase::Kind::LocalLibraryData}; id<MTLLibrary> library=[device newLibraryWithData:input error:&error]; bb_metal_call_125.Finish(bool(library));
      // A name in reflection is insufficient: the requested function must actually
      // be constructible before the artifact can bypass source compilation.
      id<MTLFunction> function=library?[library newFunctionWithName:[NSString stringWithUTF8String:entry.c_str()]]:nil;
      if(function&&[function.name isEqualToString:[NSString stringWithUTF8String:entry.c_str()]]){result.library=library;result.function=function;result.build_id=build_env;result.library_sha256=library_sha;result.reason="hit";return result;}
      result.reason=library?"entry-rejected":"library-rejected";
     }else result.reason="library-integrity";
    }else result.reason="receipt-miss";
   }else result.reason="artifact-miss";
  }else result.reason="build-identity";
 }else result.reason="record";
 // All modes may collect unknown inputs for a future preparation phase. They
 // never run a compiler subprocess, wait for a builder, or omit a draw.
 if(!allow_record)return result;
 // Recording is an explicit post-source-success operation. A failed/absent entry
 // cannot become a supposedly preparable request. OFF still returned above any IO.
 if(!accepted_function||![accepted_function.name isEqualToString:[NSString stringWithUTF8String:entry.c_str()]]){result.reason+="/unvalidated-entry";return result;}
 if(!RequestShard(folder,result.key,requests)){result.reason+="/request-directory";return result;}
 NSMutableDictionary* metadata=[@{@"schema":@(preserve_invariance?3:1),@"key":@(result.key.c_str()),@"cohort":@(cohort.c_str()),@"source_sha256":@(source_sha.c_str()),@"entry":@(entry.c_str()),@"language":@"metal3.1",@"math_mode":relaxed?@"relaxed":@"safe",@"math_fp32":@"fast",@"recipe":@(preserve_invariance?InvariantRecipe:Recipe)} mutableCopy];
 if(preserve_invariance)metadata[@"preserve_invariance"]=@YES;
 NSData* encoded=[NSJSONSerialization dataWithJSONObject:metadata options:NSJSONWritingSortedKeys error:nil];
 auto source_path=requests/(result.key+".metal");auto manifest_path=requests/(result.key+".json");
 if(!Publish(source_path,[NSData dataWithBytes:source.data() length:source.size()]))return result;
 auto saved=Read(source_path,MaxSource);if(!saved||Sha(saved.bytes,saved.length)!=source_sha)return result;
 if(!Publish(manifest_path,encoded))return result;
 result.recorded=options(Json(manifest_path));return result;
}
} // namespace Metal::LocalAot
