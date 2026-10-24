// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Existing present-buffer readback, factored for actual Metal pixel tests.
// Diagnostic only: the caller must first wait for the producer's completed tick.
#import <Metal/Metal.h>
#include <cstdio>
#include <string>
#include <fmt/format.h>
namespace BbQaPresentDump {
constexpr NSUInteger MaxBytes=32u*1024u*1024u;
inline bool Supported(MTLPixelFormat f){
 return f==MTLPixelFormatRGBA8Unorm||f==MTLPixelFormatRGBA8Unorm_sRGB||
        f==MTLPixelFormatBGRA8Unorm||f==MTLPixelFormatBGRA8Unorm_sRGB||
        f==MTLPixelFormatRGB10A2Unorm||f==MTLPixelFormatBGR10A2Unorm;
}
constexpr bool Layout(NSUInteger width,NSUInteger height,NSUInteger& row){
 if(!width||!height||width>MaxBytes/4)return false;
 row=(width*4+255u)&~NSUInteger(255u);return height<=MaxBytes/row;
}
inline bool Write(id<MTLDevice> device,id<MTLCommandQueue> queue,id<MTLTexture> texture,const std::string& dir,int index,bool flip_vertical=false){
 @autoreleasepool {
  if(!texture||!Supported(texture.pixelFormat)||texture.textureType!=MTLTextureType2D||texture.sampleCount!=1){
   std::fputs("Metal: unsupported diagnostic display format or texture shape\n",stderr);return false;
  }
  const NSUInteger width=texture.width,height=texture.height;
  NSUInteger row{};if(!Layout(width,height,row)){std::fputs("Metal: display dump exceeds 32 MiB diagnostic bound\n",stderr);return false;}
  const NSUInteger tight=width*4;
  id<MTLBuffer> buffer=[device newBufferWithLength:row*height options:MTLResourceStorageModeShared];
  id<MTLCommandBuffer> cmd=[queue commandBuffer];
  if(!buffer||!cmd){std::fputs("Metal: diagnostic readback allocation failed\n",stderr);return false;}
  id<MTLBlitCommandEncoder> blit=[cmd blitCommandEncoder];
  if(!blit){std::fputs("Metal: diagnostic blit encoder unavailable\n",stderr);return false;}
  [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
    sourceSize:MTLSizeMake(width,height,1) toBuffer:buffer destinationOffset:0
    destinationBytesPerRow:row destinationBytesPerImage:row*height];
  [blit endEncoding];[cmd commit];[cmd waitUntilCompleted];
  if(cmd.status!=MTLCommandBufferStatusCompleted||!buffer.contents){std::fputs("Metal: diagnostic readback did not complete\n",stderr);return false;}
  const bool bgra=texture.pixelFormat==MTLPixelFormatBGRA8Unorm||texture.pixelFormat==MTLPixelFormatBGRA8Unorm_sRGB;
  const auto path=fmt::format("{}/f{:03d}_present_{}x{}_{}.raw",dir,index,width,height,bgra?"bgra":"rgba");
  FILE* file=std::fopen(path.c_str(),"wb");if(!file){std::fputs("Metal: diagnostic display file open failed\n",stderr);return false;}
  bool ok=true;for(NSUInteger y=0;y<height&&ok;y++)ok=std::fwrite(static_cast<const unsigned char*>(buffer.contents)+y*row,1,tight,file)==tight;
  if(std::fclose(file))ok=false;
  if(!ok){std::fputs("Metal: diagnostic display file write failed\n",stderr);return false;}
  std::printf("Metal: display buffer dumped to %s metal_format=%llu vertical_flip=%u\n",path.c_str(),(unsigned long long)texture.pixelFormat,flip_vertical?1u:0u);
  std::fflush(stdout); // publish native format proof before a controlled diagnostic stop
  return true;
 }
}
}
