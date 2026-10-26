#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_copy_pair.h"
#include <cassert>
#include <array>
#include <cstring>
#include <fstream>
#include <iterator>
#include <cstdio>
static const char* Source=R"MSL(
#include <metal_stdlib>
using namespace metal;
struct B {uint data[1];};struct R {ulong data[1];};
kernel void main0(const device B& meta [[buffer(0)]],device R& root [[buffer(1)]],uint tid [[thread_position_in_grid]]) {
    ulong source=ulong(meta.data[0])|(ulong(meta.data[1])<<32ul);
    uint off=tid*4u+meta.data[12];
    ulong va=(ulong(meta.data[2])|(ulong(meta.data[3])<<32ul))+ulong(off);
    bool range=4u<=meta.data[4] && off<=meta.data[4]-4u;
    ulong page=(range && va<(1ul<<40ul)?va:0ul)>>14ul;
    ulong currentLeaf=root.data[meta.data[10]?page>>12ul:page];
    ulong currentEntry=meta.data[10]?*(reinterpret_cast<device ulong*>(currentLeaf+((page&4095ul)<<3ul))):currentLeaf;
    ulong trash=ulong(meta.data[6])|(ulong(meta.data[7])<<32ul);
    uint value=*(reinterpret_cast<device uint*>(source+ulong(tid)*4ul));
    *(reinterpret_cast<device uint*>(range && va<(1ul<<40ul) && currentEntry?currentEntry+(va&16383ul):trash))=value;
    bool guestRange=range && meta.data[5]!=0u;
    ulong guestPage=(guestRange && va<(1ul<<40ul)?va:0ul)>>14ul;
    ulong guestLeaf=root.data[(meta.data[10]?guestPage>>12ul:guestPage)+meta.data[9]];
    ulong guestEntry=meta.data[10]?*(reinterpret_cast<device ulong*>(guestLeaf+((guestPage&4095ul)<<3ul))):guestLeaf;
    *(reinterpret_cast<device uint*>(guestRange && va<(1ul<<40ul) && guestEntry?guestEntry+(va&16383ul):trash))=value;
}
)MSL";
static id<MTLLibrary> Library(id<MTLDevice> d,const std::string& s,bool relaxed=false){
 MTLCompileOptions* o=[MTLCompileOptions new];o.languageVersion=MTLLanguageVersion3_1;o.mathMode=relaxed?MTLMathModeRelaxed:MTLMathModeSafe;
 NSError* e=nil;auto l=[d newLibraryWithSource:[NSString stringWithUTF8String:s.c_str()] options:o error:&e];if(!l)fprintf(stderr,"%s\n",e.localizedDescription.UTF8String);assert(l);return l;
}
int main(int argc,char**argv){@autoreleasepool{
 std::string original=Source;assert(Metal::RewriteCopyPair(original).ok);
 const auto replace=[](std::string s,const char* from,const char* to){auto p=s.find(from);assert(p!=s.npos);s.replace(p,strlen(from),to);return s;};
 for(auto bad:{replace(original,"uint value=", "volatile uint value="),replace(original,"bool guestRange=", "value=9u; bool guestRange="),replace(original,"bool guestRange=", "meta.data[4]=9u; bool guestRange="),replace(original,"bool guestRange=", "mutate(); bool guestRange="),replace(original,"bool guestRange=", "mutate<uint>(); bool guestRange="),replace(original,"=value;", "=value+1u;"),replace(original,"bool guestRange=", "if (tid==0u) {} bool guestRange=")})assert(!Metal::RewriteCopyPair(bad).ok);
 auto d=MTLCreateSystemDefaultDevice();assert(d);auto q=[d newCommandQueue];
 const auto buffer=[&](NSUInteger n){auto b=[d newBufferWithLength:n options:MTLResourceStorageModeShared];assert(b);memset(b.contents,0,n);return b;};
 auto src=buffer(512),dst=buffer(512),mirror=buffer(512),meta=buffer(256),root=buffer(4096),leaf0=buffer(32768),leaf1=buffer(32768),zero=buffer(32768),trash=buffer(512);
 unsigned configurations=0;
 for(bool relaxed:{false,true})for(bool floating:{false,true}){@autoreleasepool{
  std::string text=original;
  if(floating){size_t at=0;while((at=text.find("device uint*>",at))!=text.npos){text.replace(at,13,"device float*>");at+=14;}text=replace(text,"uint value=","float value=");}
  auto merged=Metal::RewriteCopyPair(text);assert(merged.ok);
  auto lb=Library(d,text,relaxed),lm=Library(d,merged.source,relaxed);NSError* error=nil;
  auto pb=[d newComputePipelineStateWithFunction:[lb newFunctionWithName:@"main0"] error:&error];assert(pb);
  auto pm=[d newComputePipelineStateWithFunction:[lm newFunctionWithName:@"main0"] error:&error];assert(pm);
  for(bool two:{false,true})for(unsigned which=0;which<14;++which){
   unsigned lanes=which<2||which==12?64:1;
   std::array<unsigned char,512> expected_dst{},expected_mirror{},expected_trash{};std::array<unsigned char,256> expected_meta{};
   for(bool opt:{false,true}){
    memset(dst.contents,0x9a,512);memset(mirror.contents,0x6b,512);memset(trash.contents,0xa5,512);memset(meta.contents,0,256);memset(leaf0.contents,0,32768);memset(leaf1.contents,0,32768);memset(zero.contents,0,32768);
    auto* in=(uint32_t*)src.contents;for(unsigned i=0;i<128;++i)in[i]=i*17+3;uint32_t special[]={0x7fc00001,0x7f800000,0xff800000,0x80000000,1,0xffffffff};memcpy(in,special,sizeof(special));
    uint64_t source=which==12?dst.gpuAddress:src.gpuAddress,va=0x100000;
    uint32_t words[64]={};words[0]=source;words[1]=source>>32;words[2]=va;words[3]=va>>32;words[4]=256;words[5]=1;words[6]=trash.gpuAddress;words[7]=trash.gpuAddress>>32;words[9]=two?1:128;words[10]=two;
    if(which==6)words[5]=0;if(which==7)words[4]=0;if(which==8)words[4]=3;if(which==9){words[4]=64;words[12]=64;}if(which==10){words[4]=64;words[12]=60;}if(which==11){words[4]=0xffffffffu;words[12]=0xfffffffcu;}
    uint64_t current=dst.gpuAddress,guest=which==0?dst.gpuAddress:mirror.gpuAddress;
    if(which==2)guest=dst.gpuAddress+16;if(which==3||which==5)current=0;if(which==4||which==5)guest=0;
    if(which==13){current=meta.gpuAddress+20;in[0]=0;}
    memcpy(meta.contents,words,sizeof(words));
    auto* roots=(uint64_t*)root.contents;for(unsigned i=0;i<512;++i)roots[i]=two?zero.gpuAddress:0;
    if(two){roots[0]=leaf0.gpuAddress;roots[1]=leaf1.gpuAddress;((uint64_t*)leaf0.contents)[64]=current;((uint64_t*)leaf1.contents)[64]=guest;}
    else{roots[64]=current;roots[64+128]=guest;}
    auto cb=[q commandBuffer];auto enc=[cb computeCommandEncoder];[enc setComputePipelineState:opt?pm:pb];[enc setBuffer:meta offset:0 atIndex:0];[enc setBuffer:root offset:0 atIndex:1];
    for(id<MTLBuffer> b in @[src,dst,mirror,meta,root,leaf0,leaf1,zero,trash])[enc useResource:b usage:MTLResourceUsageRead|MTLResourceUsageWrite];
    [enc dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(lanes,1,1)];[enc endEncoding];[cb commit];[cb waitUntilCompleted];assert(cb.status==MTLCommandBufferStatusCompleted);
    if(!opt){memcpy(expected_dst.data(),dst.contents,512);memcpy(expected_mirror.data(),mirror.contents,512);memcpy(expected_trash.data(),trash.contents,512);memcpy(expected_meta.data(),meta.contents,256);}
    else{assert(!memcmp(expected_dst.data(),dst.contents,512));assert(!memcmp(expected_mirror.data(),mirror.contents,512));assert(!memcmp(expected_trash.data(),trash.contents,512));assert(!memcmp(expected_meta.data(),meta.contents,256));}
   }
   ++configurations;
  }
 }}
 for(int i=1;i<argc;++i){std::ifstream f(argv[i]);std::string text{std::istreambuf_iterator<char>(f),{}};auto r=Metal::RewriteCopyPair(text);if(r.ok){Library(d,r.source);printf("corpus merged %s\n",argv[i]);}}
 printf("PASS %u configurations / %u dispatches: uint/float Safe/Relaxed, flat/two-level, identical/separate/offset, absent views, write-through disabled, range edges, self-copy, metadata alias; complete outputs and canaries\n",configurations,configurations*2);
}}
