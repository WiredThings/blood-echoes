// SPDX-License-Identifier: GPL-2.0-or-later
// Authored native regression: capability initialization is real; entry factories are mocked.
// Calls actual ShaderCompiler -> BuildLibrary with fake library/function objects;
// Metal::Device capability initialization is real if root later executes this native test.
// No fake object submits GPU work and no compiler subprocess is launched here.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cassert>
#include <filesystem>
#include <fstream>
#include <vector>
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "test_gpu_runtime_stubs.cpp"
@interface BbAotAuthoredFunction:NSObject
@property(nonatomic,copy) NSString* name;
@end
@implementation BbAotAuthoredFunction
@end
@interface BbAotAuthoredLibrary:NSObject
@property(nonatomic,copy) NSString* label;
@property(nonatomic) BOOL canCreateFunction;
@property(nonatomic) NSUInteger functionCalls;
@end
@implementation BbAotAuthoredLibrary
-(NSArray<NSString*>*)functionNames{return @[@"main0"];}
-(id)newFunctionWithName:(NSString*)name{
 self.functionCalls+=1;if(!self.canCreateFunction)return nil;
 BbAotAuthoredFunction* function=[BbAotAuthoredFunction new];function.name=name;return function;
}
@end
@interface BbAotAuthoredDevice:NSObject
@property(nonatomic) NSUInteger dataCalls;
@property(nonatomic) NSUInteger sourceCalls;
@property(nonatomic) BOOL sourceFunctionWorks;
@property(nonatomic,strong) BbAotAuthoredLibrary* sourceLatest;
@property(nonatomic,strong) BbAotAuthoredLibrary* artifact;
@end
@implementation BbAotAuthoredDevice
-(id)newLibraryWithData:(dispatch_data_t)data error:(NSError**)error{
 (void)data;(void)error;self.dataCalls+=1;return self.artifact;
}
-(id)newLibraryWithSource:(NSString*)source options:(MTLCompileOptions*)options error:(NSError**)error{
 (void)source;(void)options;(void)error;self.sourceCalls+=1;
 BbAotAuthoredLibrary* library=[BbAotAuthoredLibrary new];library.canCreateFunction=self.sourceFunctionWorks;self.sourceLatest=library;return library;
}
@end
static std::vector<u32> Spirv(){return{0x07230203,0x00010000,0,6,0,0x00020011,1,0x0003000e,0,1,
 0x0005000f,5,4,0x6e69616d,0,0x00060010,4,17,1,1,1,0x00020013,1,0x00030021,2,1,
 0x00050036,1,4,0,2,0x000200f8,5,0x000100fd,0x00010038};}
int main(int argc,char**argv){@autoreleasepool{
 assert(argc==2);std::filesystem::path gpu=argv[1];assert(gpu.is_absolute()&&std::filesystem::is_directory(gpu));
 auto root=gpu/"local-metal";std::filesystem::create_directory(root);
 const std::string cohort(64,'a'),build(64,'b');setenv("BB_GPU_USER_DIR",gpu.c_str(),1);
 setenv("BB_MTL_LOCAL_AOT_ROOT",root.c_str(),1);setenv("BB_MTL_LOCAL_AOT_COHORT",cohort.c_str(),1);
 setenv("BB_MTL_LOCAL_AOT_BUILD_ID",build.c_str(),1);setenv("BB_MTL_LOCAL_AOT_MODE","record",1);setenv("BB_MTL_LIBRARY_REUSE","1",1);
 Metal::Device device;auto native=device.GetImpl().device;
 BbAotAuthoredDevice* fake=[BbAotAuthoredDevice new];fake.sourceFunctionWorks=YES;
 fake.artifact=[BbAotAuthoredLibrary new];fake.artifact.canCreateFunction=NO;
 device.GetImpl().device=(id<MTLDevice>)(id)fake;
 auto words=Spirv();
 {
  Metal::ShaderCompiler compiler{device,gpu/"source-msl"};
  auto first=compiler.Compile(words,0x13579bdf,"authored-a");assert(first.shader&&first.shader->function);
  auto library=fake.sourceLatest;assert(fake.sourceCalls==1&&library.functionCalls==1);
  auto second=compiler.Compile(words,0x2468ace0,"authored-b");assert(second.shader&&second.shader->function);
  assert(fake.sourceCalls==1&&library.functionCalls==2); // memo hit still calls newFunction
  assert(first.shader.get()!=second.shader.get());
 }
 const auto requests=root/cohort/"requests";std::filesystem::path manifest;
 for(const auto& row:std::filesystem::directory_iterator(requests))if(row.path().extension()==".json"){assert(manifest.empty());manifest=row.path();}
 assert(!manifest.empty());NSDictionary* request=Metal::LocalAot::Json(manifest);assert(request);
 NSString* key=request[@"key"];auto artifacts=root/cohort/"artifacts"/build;std::filesystem::create_directories(artifacts);
 NSData* blob=[@"AUTHORED-FAKE-IR" dataUsingEncoding:NSUTF8StringEncoding];auto libraryPath=artifacts/(std::string(key.UTF8String)+".metallib");
 assert([blob writeToFile:@(libraryPath.c_str()) atomically:YES]);
 NSMutableDictionary* receipt=[request mutableCopy];receipt[@"build_id"]=@(build.c_str());receipt[@"library_bytes"]=@(blob.length);
 receipt[@"library_sha256"]=@(Metal::LocalAot::Sha(blob.bytes,blob.length).c_str());
 auto bytes=[NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:nil];
 assert([bytes writeToFile:@((artifacts/(std::string(key.UTF8String)+".json")).c_str()) atomically:YES]);
 setenv("BB_MTL_LOCAL_AOT_MODE","load",1);fake.dataCalls=0;fake.sourceCalls=0;
 {
  Metal::ShaderCompiler compiler{device,gpu/"artifact-rejection-msl"};
  auto result=compiler.Compile(words,0x11223344,"authored-entry-rejected");
  assert(result.shader&&result.shader->function&&fake.dataCalls==1&&fake.sourceCalls==1);
  assert(fake.artifact.functionCalls==1); // reflected name existed; real function creation failed
 }
 // An admitted artifact's already-created function must not be constructed again outside Try.
 fake.artifact.canCreateFunction=YES;fake.artifact.functionCalls=0;fake.dataCalls=0;fake.sourceCalls=0;
 {
  Metal::ShaderCompiler compiler{device,gpu/"artifact-hit-msl"};
  auto result=compiler.Compile(words,0x11223345,"authored-hit");
  assert(result.shader&&result.shader->function&&fake.dataCalls==1&&fake.sourceCalls==0&&fake.artifact.functionCalls==1);
 }
 // Source library creation without an actual requested function cannot create a request.
 const std::string rejected_cohort(64,'c');setenv("BB_MTL_LOCAL_AOT_COHORT",rejected_cohort.c_str(),1);
 fake.sourceFunctionWorks=NO;fake.sourceCalls=0;
 {
  Metal::ShaderCompiler compiler{device,gpu/"source-rejected-msl"};
  auto result=compiler.Compile(words,0x11223346,"authored-source-entry-rejected");
  assert(!result.shader&&fake.sourceCalls==1&&!std::filesystem::exists(root/rejected_cohort/"requests"));
 }
 // A function rejected on a memo hit must compile source directly, without another artifact try.
 const std::string memo_cohort(64,'d');setenv("BB_MTL_LOCAL_AOT_COHORT",memo_cohort.c_str(),1);
 fake.sourceFunctionWorks=YES;fake.sourceCalls=0;fake.dataCalls=0;
 {
  Metal::ShaderCompiler compiler{device,gpu/"memo-entry-rejected-msl"};
  auto first=compiler.Compile(words,0x11223347,"authored-memo-first");assert(first.shader);
  fake.sourceLatest.canCreateFunction=NO;
  auto second=compiler.Compile(words,0x11223348,"authored-memo-second");
  assert(second.shader&&second.shader->function&&fake.sourceCalls==2&&fake.dataCalls==0);
 }
 // No unvalidated direct caller may record, even when it explicitly asks.
 const std::string unvalidated(64,'e');setenv("BB_MTL_LOCAL_AOT_COHORT",unvalidated.c_str(),1);
 auto rejected=Metal::LocalAot::Try((id<MTLDevice>)(id)fake,"kernel void main0(){}","main0",false,true,false);
 assert(!rejected.recorded&&!std::filesystem::exists(root/unvalidated/"requests"));
 // Equal roots and a symlinked cohort must fail before creating directories.
 assert(!Metal::LocalAot::Child(gpu,gpu));
 auto accepted=[fake.sourceLatest newFunctionWithName:@"main0"];
 const std::string path_cohort(64,'f');setenv("BB_MTL_LOCAL_AOT_COHORT",path_cohort.c_str(),1);
 setenv("BB_MTL_LOCAL_AOT_ROOT",gpu.c_str(),1);
 auto equal=Metal::LocalAot::Try((id<MTLDevice>)(id)fake,"kernel void main0(){}","main0",false,true,false,(id<MTLFunction>)accepted);
 assert(equal.reason=="private-root"&&!equal.recorded&&!std::filesystem::exists(gpu/path_cohort));
 setenv("BB_MTL_LOCAL_AOT_ROOT",root.c_str(),1);
 auto outside=gpu/"outside-aot";assert(std::filesystem::create_directory(outside));
 std::filesystem::create_directory_symlink(outside,root/path_cohort);
 auto linked=Metal::LocalAot::Try((id<MTLDevice>)(id)fake,"kernel void main0(){}","main0",false,true,false,(id<MTLFunction>)accepted);
 assert(!linked.recorded&&!std::filesystem::exists(outside/"requests"));
 std::filesystem::remove(root/path_cohort);
 device.GetImpl().device=native;puts("PASS authored future entry/fallback/memo controls; no fake GPU submissions");
}}
