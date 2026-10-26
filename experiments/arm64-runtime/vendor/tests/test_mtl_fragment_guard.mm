// SPDX-License-Identifier: GPL-2.0-or-later
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_render_list.h"
#include <cassert>
#include <cstdio>
#include <thread>
int main(){@autoreleasepool {
    setenv("BB_CAPTURE_NATIVE","1",1);setenv("BB_CAPTURE_FRAGMENT_GUARD","1",1);
    auto device=MTLCreateSystemDefaultDevice();assert(device);
    NSError* error=nil;
    auto library=[device newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 vs(uint i[[vertex_id]]){return float4(i==1?3.0:-1.0,i==2?3.0:-1.0,0,1);}\nfragment float4 fs(device uint* b[[buffer(0)]]){return float4(0,b[0]==0x12345678u,0,1);}\n" options:nil error:&error];assert(library);
    auto descriptor=[MTLRenderPipelineDescriptor new];descriptor.vertexFunction=[library newFunctionWithName:@"vs"];descriptor.fragmentFunction=[library newFunctionWithName:@"fs"];descriptor.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    auto pipeline=[device newRenderPipelineStateWithDescriptor:descriptor error:&error];assert(pipeline);
    auto queue=[device newCommandQueue];
    auto buffer=[device newBufferWithLength:256 options:MTLResourceStorageModeShared];static_cast<uint32_t*>(buffer.contents)[4]=0x12345678;
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageRenderTarget;
    for(unsigned mode=0;mode<4;++mode) {
        auto texture=[device newTextureWithDescriptor:td];auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        auto command=[queue commandBuffer];auto encoder=[command renderCommandEncoderWithDescriptor:pass];
        Metal::RenderList list, moved;
        if(mode==0)list.BeginDirect((__bridge void*)encoder);else list.BeginRecording();
        assert(list.FragmentGuardEnabled());
        list.SetRenderPipelineState((__bridge void*)pipeline);list.SetBuffer(Metal::RenderList::Stage::Fragment,(__bridge void*)buffer,16,0);
        list.FragmentBuffer((__bridge void*)buffer,16,4);list.FragmentReady();list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        auto s=list.FragmentSnapshot((__bridge void*)buffer,16,1);assert(s.overlap&&!s.reasons&&s.draws==1&&s.known_draws==1);
        // A raw/manual draw without reflected classification cannot look complete.
        list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);s=list.FragmentSnapshot((__bridge void*)buffer,100,4);assert(!s.overlap&&(s.reasons&Metal::FragmentAccessGuard::MissingDraw)&&s.draws==2&&s.known_draws==1);
        if(mode==1 || mode==3)list.Replay((__bridge void*)encoder);
        if(mode==3) {
            list.ContinueDirect((__bridge void*)encoder);s=list.FragmentSnapshot((__bridge void*)buffer,16,1);
            assert(s.overlap&&s.reasons&&s.draws==2&&s.known_draws==1);
            list.SetRenderPipelineState((__bridge void*)pipeline);list.SetBuffer(Metal::RenderList::Stage::Fragment,(__bridge void*)buffer,16,0);
            list.FragmentReady();list.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);s=list.FragmentSnapshot((__bridge void*)buffer,16,1);
            assert(s.overlap&&s.reasons&&s.draws==3&&s.known_draws==2);
        }
        if(mode==2) {
            list.TakeRecording(moved);s=moved.FragmentSnapshot((__bridge void*)buffer,16,1);assert(s.overlap&&s.reasons&&s.draws==2);
            list.BeginRecording();s=list.FragmentSnapshot((__bridge void*)buffer,16,1);assert(!s.overlap&&!s.reasons&&!s.draws);
            std::thread worker([&]{@autoreleasepool {moved.Replay((__bridge void*)encoder);}});worker.join();moved.End();
        }
        list.End();[encoder endEncoding];[command commit];[command waitUntilCompleted];assert(command.status==MTLCommandBufferStatusCompleted);
        unsigned char pixels[256];[texture getBytes:pixels bytesPerRow:32 fromRegion:MTLRegionMake2D(0,0,8,8) mipmapLevel:0];
        for(unsigned i=0;i<64;++i)assert(!pixels[i*4]&&pixels[i*4+1]==255&&!pixels[i*4+2]&&pixels[i*4+3]==255);
    }
    puts("fragment guard RenderList: direct, recorded, continued-direct, moved/threaded replay, reset and all pixels passed");return 0;
}}
