// SPDX-License-Identifier: GPL-2.0-or-later
// Strict scalar/vector helpers: adversarial numeric agreement and bounded offscreen timing.
#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <thread>
#include <atomic>
#include "video_core/renderer_metal/mtl_vertex_math.h"
static id<MTLLibrary> Library(id<MTLDevice> d,const std::string& s) {
 auto options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;options.mathMode=MTLMathModeSafe;
 NSError* error=nil;auto lib=[d newLibraryWithSource:@(s.c_str()) options:options error:&error];
 if(error)std::printf("compiler: %s\n",error.localizedDescription.UTF8String);
 if(!lib || (error && [error.localizedDescription containsString:@"unknown pragma"]))std::abort();
 return lib;
}
int main() { @autoreleasepool {
 if(const char* control=std::getenv("BB_MTL_VERTEX_MATH_FILE")) {
   auto set=[&](char value){FILE* f=std::fopen(control,"w");if(!f)std::abort();std::fputc(value,f);std::fclose(f);};
   set('0');if(Metal::VertexMathOptimizedSelection())std::abort();
   std::atomic<unsigned> wrong{0};std::vector<std::thread> workers;
   for(unsigned i=0;i<8;++i)workers.emplace_back([&]{for(unsigned j=0;j<1000;++j)if(Metal::VertexMathOptimizedSelection())++wrong;});
   for(auto& t:workers)t.join();if(wrong)std::abort();
   set('1');std::this_thread::sleep_for(std::chrono::milliseconds(1100));
   if(!Metal::VertexMathOptimizedSelection())std::abort();
   if(!Metal::VertexMathReferenceRequested(0x3deaa573) || Metal::VertexMathReferenceRequested(0xbad))std::abort();
   for(unsigned i=0;i<32;++i)if(!Metal::ReserveVertexMathReference())std::abort();
   if(Metal::ReserveVertexMathReference())std::abort();
   std::puts("PASS bounded diagnostic selector, concurrent reads, capacity and policy restore");
 }
 auto d=MTLCreateSystemDefaultDevice();auto queue=[d newCommandQueue];
 std::string helper="#include <metal_stdlib>\nusing namespace metal;\ntemplate<typename T>\n[[clang::optnone]] T spvFMul(T l, T r)\n{\n    return fma(l, r, T(0));\n}\ntemplate<typename T>\n[[clang::optnone]] T spvFAdd(T l, T r)\n{\n    return fma(T(1), l, r);\n}\ntemplate<typename T>\n[[clang::optnone]] T spvFSub(T l, T r)\n{\n    return fma(T(-1), r, l);\n}\n";
 std::string kernel="kernel void check(const device float4* v[[buffer(0)]],device float4* out[[buffer(1)]],uint i[[thread_position_in_grid]]) { float4 a=v[i];out[i]=float4(spvFMul(a.x,a.y),spvFAdd(a.x,a.y),spvFSub(a.x,a.y),spvFAdd(spvFMul(a.x,a.y),a.z));}\n";
 std::string bench="kernel void bench(const device float4* v[[buffer(0)]],device float4* out[[buffer(1)]],uint i[[thread_position_in_grid]]) {float4 a=v[i];float4 x=a;for(uint j=0;j<32;++j){x=spvFAdd(spvFMul(x,float4(0.99999f)),a);}out[i]=x;}\n";
 std::string vertex=R"MSL(
struct Position { float4 position [[position]]; };
vertex Position depth_vs(const device float4* input [[buffer(0)]], uint id [[vertex_id]]) {
    float4 p=input[id];
    float x=spvFAdd(spvFMul(p.x,0.913271f),spvFMul(p.y,0.027319f));
    float y=spvFSub(spvFMul(p.y,0.901137f),spvFMul(p.x,0.019731f));
    float z=spvFAdd(spvFMul(p.z,0.712931f),spvFAdd(spvFMul(x,0.027131f),0.111719f));
    return {float4(x,y,z,spvFAdd(spvFMul(p.z,0.001117f),1.f))};
}
fragment float4 depth_pixel() {return float4(1,0,0,1);}
)MSL";
 auto next=helper; if(Metal::InlineStrictVertexMath(next)!=3)std::abort();
 const auto once=next;if(Metal::InlineStrictVertexMath(next)!=0 || next!=once)std::abort();
 std::string unrelated="[[clang::optnone]] float unrelated(float x){return x;}";
 if(Metal::InlineStrictVertexMath(unrelated)!=0)std::abort();
 std::array<id<MTLLibrary>,2> libs{Library(d,helper+kernel+bench+vertex),Library(d,next+kernel+bench+vertex)};
 constexpr unsigned Count=262144;auto input=[d newBufferWithLength:Count*16 options:MTLResourceStorageModeShared];
 std::array<id<MTLBuffer>,2> outputs{[d newBufferWithLength:Count*16 options:MTLResourceStorageModeShared],[d newBufferWithLength:Count*16 options:MTLResourceStorageModeShared]};
 auto* words=static_cast<uint32_t*>(input.contents);uint32_t rng=12345;
 const uint32_t special[]={0,0x80000000,0x7f800000,0xff800000,0x7fc00001,0x00000001,0x007fffff,0x00800000,0x3f800001,0x3f7ffffe,0xbf800000,0x7f7fffff};
 for(unsigned i=0;i<Count*4;++i) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;words[i]=i<144?special[(i/12+i%12)%12]:rng;}
 words[0]=0x3f800001;words[1]=0x3f7ffffe;words[2]=0xbf800000; // fused and rounded products differ
 auto dispatch=[&](unsigned variant,const char* name,unsigned n) {
 NSError* error=nil;auto p=[d newComputePipelineStateWithFunction:[libs[variant] newFunctionWithName:@(name)] error:&error];if(!p)std::abort();
 auto cb=[queue commandBuffer];auto enc=[cb computeCommandEncoder];[enc setComputePipelineState:p];[enc setBuffer:input offset:0 atIndex:0];[enc setBuffer:outputs[variant] offset:0 atIndex:1];[enc dispatchThreads:MTLSizeMake(n,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];[enc endEncoding];[cb commit];[cb waitUntilCompleted];if(cb.status!=MTLCommandBufferStatusCompleted)std::abort();return (cb.GPUEndTime-cb.GPUStartTime)*1e3;
 };
 dispatch(0,"check",Count);dispatch(1,"check",Count);
 unsigned bad=0,nan_payloads=0;auto* a=static_cast<uint32_t*>(outputs[0].contents);auto* b=static_cast<uint32_t*>(outputs[1].contents);
 for(unsigned i=0;i<Count*4;++i)if(a[i]!=b[i]) {float x,y;std::memcpy(&x,a+i,4);std::memcpy(&y,b+i,4);if(std::isnan(x)&&std::isnan(y))++nan_payloads;else if(bad++<8)std::printf("mismatch %u %08x %08x\n",i,a[i],b[i]);}
 std::printf("scalar results=%u mismatches=%u NaN-payload-only=%u cancellation=%08x/%08x\n",Count*4,bad,nan_payloads,a[3],b[3]);if(bad)return 1;
 for(unsigned i=0;i<Count*4;++i)words[i]=0x3f000000+(i%128)*1024;
 std::array<std::vector<double>,2> times;
 for(unsigned round=0;round<10;++round)for(unsigned j=0;j<2;++j){unsigned variant=round%2?1-j:j;auto ms=dispatch(variant,"bench",65536);if(round>1)times[variant].push_back(ms);}
 for(unsigned i=0;i<2;++i){std::sort(times[i].begin(),times[i].end());std::printf("variant=%u median-ms=%.5f\n",i,times[i][times[i].size()/2]);}
 for(unsigned i=0;i<65536*4;++i)if(a[i]!=b[i]){std::printf("chain mismatch %u %08x %08x\n",i,a[i],b[i]);return 1;}
 std::puts("PASS strict inline scalar and vector chain bitwise agreement");
 // A real raster comparison catches cross-pass depth equality/invariance regressions.
 std::vector<std::array<float,4>> vertices;
 auto point=[](unsigned x,unsigned y){return std::array<float,4>{float(x)/16-1,float(y)/16-1,.25f+float(x+y)*.003171f,1};};
 for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x)
     for(auto p:{point(x,y),point(x+1,y),point(x,y+1),point(x+1,y),point(x+1,y+1),point(x,y+1)})vertices.push_back(p);
 auto vb=[d newBufferWithBytes:vertices.data() length:vertices.size()*16 options:MTLResourceStorageModeShared];
 std::array<id<MTLRenderPipelineState>,2> render;id<MTLRenderPipelineState> depth_only=nil;
 for(unsigned i=0;i<2;++i){auto pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[libs[i] newFunctionWithName:@"depth_vs"];pd.fragmentFunction=[libs[i] newFunctionWithName:@"depth_pixel"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;pd.depthAttachmentPixelFormat=MTLPixelFormatDepth32Float;NSError*error=nil;render[i]=[d newRenderPipelineStateWithDescriptor:pd error:&error];if(!render[i])std::abort();if(i==0){pd.colorAttachments[0].writeMask=MTLColorWriteMaskNone;depth_only=[d newRenderPipelineStateWithDescriptor:pd error:&error];if(!depth_only)std::abort();}}
 auto dd=[MTLDepthStencilDescriptor new];dd.depthCompareFunction=MTLCompareFunctionLessEqual;dd.depthWriteEnabled=YES;auto write=[d newDepthStencilStateWithDescriptor:dd];dd.depthCompareFunction=MTLCompareFunctionEqual;dd.depthWriteEnabled=NO;auto equal=[d newDepthStencilStateWithDescriptor:dd];
 std::array<std::vector<uint8_t>,2> pixels;
 for(unsigned i=0;i<2;++i){
   auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:256 height:256 mipmapped:NO];td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;auto color=[d newTextureWithDescriptor:td];td.pixelFormat=MTLPixelFormatDepth32Float;td.storageMode=MTLStorageModePrivate;auto depth=[d newTextureWithDescriptor:td];
   auto c=[queue commandBuffer];
   for(unsigned pass=0;pass<2;++pass){auto rp=[MTLRenderPassDescriptor renderPassDescriptor];rp.colorAttachments[0].texture=color;rp.colorAttachments[0].loadAction=pass?MTLLoadActionLoad:MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;rp.depthAttachment.texture=depth;rp.depthAttachment.loadAction=pass?MTLLoadActionLoad:MTLLoadActionClear;rp.depthAttachment.clearDepth=1;rp.depthAttachment.storeAction=MTLStoreActionStore;
      auto en=[c renderCommandEncoderWithDescriptor:rp];[en setRenderPipelineState:pass?render[i]:depth_only];[en setDepthStencilState:pass?equal:write];[en setVertexBuffer:vb offset:0 atIndex:0];[en setViewport:MTLViewport{0,0,256,256,0,1}];[en drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:vertices.size()];[en endEncoding];}
   [c commit];[c waitUntilCompleted];if(c.status!=MTLCommandBufferStatusCompleted)std::abort();pixels[i].resize(256*256*4);[color getBytes:pixels[i].data() bytesPerRow:1024 fromRegion:MTLRegionMake2D(0,0,256,256) mipmapLevel:0];
 }
 if(pixels[0]!=pixels[1]){std::puts("FAIL cross-pass depth-equal pixels");return 1;}
 const auto covered=std::count(pixels[0].begin(),pixels[0].end(),uint8_t(255));if(covered<65536)std::abort();
 std::printf("PASS cross-pass depth-equal identical, covered-components=%zu\n",size_t(covered));
}}
