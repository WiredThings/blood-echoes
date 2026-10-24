// SPDX-License-Identifier: GPL-2.0-or-later
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#define FMT_HEADER_ONLY
#include <cassert>
#include <cstdint>
#include <cstring>
#include "video_core/renderer_metal/mtl_present_dump.h"
static uint32_t sample(unsigned x,unsigned y){
 if(y<9)return x<32?0xff0000:0x00ff00;
 return x<32?0x0000ff:0x808080;
}
int main(int argc,char**argv){
 assert(argc==2);std::string dir=argv[1];NSUInteger row{};
 assert(BbQaPresentDump::Layout(65,17,row)&&row==512);
 assert(!BbQaPresentDump::Layout(0,17,row)&&!BbQaPresentDump::Layout(65,0,row));
 assert(!BbQaPresentDump::Layout(UINT64_MAX,1,row)&&!BbQaPresentDump::Layout(1,UINT64_MAX,row));
 @autoreleasepool{
  id<MTLDevice> device=MTLCreateSystemDefaultDevice();assert(device);id<MTLCommandQueue> queue=[device newCommandQueue];assert(queue);
  MTLPixelFormat formats[]={MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA8Unorm_sRGB,MTLPixelFormatBGRA8Unorm,MTLPixelFormatBGRA8Unorm_sRGB,MTLPixelFormatRGB10A2Unorm,MTLPixelFormatBGR10A2Unorm};
  NSError* error=nil;NSString* source=@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 v(uint i [[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1); }\nfragment float4 f(float4 p [[position]],constant uint& flip [[buffer(0)]]){if(flip)p.y=17-p.y;float3 c=p.y<9?(p.x<32?float3(1,0,0):float3(0,1,0)):(p.x<32?float3(0,0,1):float3(128.0/255.0));return float4(c,0);}";
  id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];assert(library);
  int index=0;id<MTLTexture> last=nil;
  for(auto f:formats){
   @autoreleasepool{
    auto*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:65 height:17 mipmapped:NO];d.storageMode=MTLStorageModePrivate;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    id<MTLTexture> t=[device newTextureWithDescriptor:d];assert(t);last=t;
    const size_t stride=512;id<MTLBuffer> src=[device newBufferWithLength:stride*17 options:MTLResourceStorageModeShared];assert(src);memset(src.contents,0xee,stride*17);
    for(unsigned y=0;y<17;y++)for(unsigned x=0;x<65;x++){
     const auto c=sample(x,y);const uint32_t r=(c>>16)&255,g=(c>>8)&255,b=c&255;uint32_t value{};
     if(f==MTLPixelFormatRGBA8Unorm||f==MTLPixelFormatRGBA8Unorm_sRGB)value=r|(g<<8)|(b<<16);
     else if(f==MTLPixelFormatBGRA8Unorm||f==MTLPixelFormatBGRA8Unorm_sRGB)value=b|(g<<8)|(r<<16);
     else{const auto r10=(r*1023+127)/255,g10=(g*1023+127)/255,b10=(b*1023+127)/255;value=f==MTLPixelFormatRGB10A2Unorm?r10|(g10<<10)|(b10<<20):b10|(g10<<10)|(r10<<20);}
     memcpy(static_cast<unsigned char*>(src.contents)+y*stride+x*4,&value,4);
    }
    id<MTLCommandBuffer> c=[queue commandBuffer];id<MTLBlitCommandEncoder> e=[c blitCommandEncoder];[e copyFromBuffer:src sourceOffset:0 sourceBytesPerRow:stride sourceBytesPerImage:stride*17 sourceSize:MTLSizeMake(65,17,1) toTexture:t destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];[e endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
    assert(BbQaPresentDump::Write(device,queue,t,dir,index++));
    MTLRenderPipelineDescriptor* ps=[MTLRenderPipelineDescriptor new];ps.vertexFunction=[library newFunctionWithName:@"v"];ps.fragmentFunction=[library newFunctionWithName:@"f"];ps.colorAttachments[0].pixelFormat=f;
    id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:ps error:&error];assert(pipeline);
    MTLRenderPassDescriptor* pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=t;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    uint32_t flip=0;
    c=[queue commandBuffer];id<MTLRenderCommandEncoder> renderer=[c renderCommandEncoderWithDescriptor:pass];[renderer setRenderPipelineState:pipeline];[renderer setFragmentBytes:&flip length:sizeof(flip) atIndex:0];[renderer setViewport:MTLViewport{0,0,65,17,0,1}];[renderer drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[renderer endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
    assert(BbQaPresentDump::Write(device,queue,t,dir,index++));
    flip=1;c=[queue commandBuffer];renderer=[c renderCommandEncoderWithDescriptor:pass];[renderer setRenderPipelineState:pipeline];[renderer setFragmentBytes:&flip length:sizeof(flip) atIndex:0];[renderer setViewport:MTLViewport{0,0,65,17,0,1}];[renderer drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[renderer endEncoding];[c commit];[c waitUntilCompleted];assert(c.status==MTLCommandBufferStatusCompleted);
    assert(BbQaPresentDump::Write(device,queue,t,dir,index++,true));
   }
  }
  assert(!BbQaPresentDump::Write(device,nil,last,dir,18));
  auto*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:16 height:16 mipmapped:NO];d.storageMode=MTLStorageModePrivate;id<MTLTexture> unsupported=[device newTextureWithDescriptor:d];assert(unsupported);assert(!BbQaPresentDump::Write(device,queue,unsupported,dir,19));
  puts("present dump actual Metal: six 32-bit formats; odd-width padded rows; unsupported/allocator paths rejected");
 }
}
