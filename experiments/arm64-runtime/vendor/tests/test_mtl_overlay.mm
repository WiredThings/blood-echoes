#import <Metal/Metal.h>
#include "bbport_overlay.h"
#include "imgui.h"
#include "imgui_impl_metal.h"
#include "video_core/renderer_metal/mtl_overlay.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

static bool visible=true;
static unsigned frame_index=0;
namespace BbOverlay {
void Init(const std::function<bool()>& init) { assert(init()); }
// Deterministic headless UI; exercise the real port glue and Metal backend. No SDL,
// settings file, save directory, game shaders, launcher or normal ImGui ini is used.
void Render(u32 w,u32 h,const Backend& backend) {
    if(!visible)return;
    backend.new_frame();
    auto& io=ImGui::GetIO();io.DisplaySize={float(w),float(h)};io.DeltaTime=1.f/60;
    ImGui::NewFrame();
    auto* list=ImGui::GetForegroundDrawList();
    list->AddRectFilled({1,1},{float(w)-1,float(h)-1},IM_COL32(20,60,200,80));
    list->PushClipRect({2,2},{float(w)-2,float(h)-2});
    ImGui::PushFont(nullptr,frame_index==2?20.f:13.f);
    list->AddText({3,3},IM_COL32_WHITE,frame_index<2?"FPS 60":"New glyphs xyz 0123456789");
    ImGui::PopFont();
    list->AddCallback(ImGui::GetPlatformIO().DrawCallback_ResetRenderState,nullptr);
    list->AddRectFilled({-10,-10},{8,8},IM_COL32(200,20,80,180));
    list->PopClipRect();
    ImGui::Render();backend.render(ImGui::GetDrawData());
}
}

static std::vector<std::vector<unsigned char>> Run(id<MTLDevice> device,bool merged) {
    ImGui::CreateContext();ImGui::GetIO().IniFilename=nullptr;ImGui::GetIO().LogFilename=nullptr;
    assert(ImGui_ImplMetal_Init(device));
    NSError* error=nil;
    NSString* source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint id [[vertex_id]]) {float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[id],0,1);}\n"
        "fragment float4 f(float4 p [[position]]) {return float4(p.x/128.,p.y/96.,.25,1);}\n";
    auto library=[device newLibraryWithSource:source options:nil error:&error];assert(library && !error);
    auto pipeline_desc=[MTLRenderPipelineDescriptor new];
    pipeline_desc.vertexFunction=[library newFunctionWithName:@"v"];
    pipeline_desc.fragmentFunction=[library newFunctionWithName:@"f"];
    pipeline_desc.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
    auto pipeline=[device newRenderPipelineStateWithDescriptor:pipeline_desc error:&error];assert(pipeline && !error);
    auto queue=[device newCommandQueue];
    std::vector<std::vector<unsigned char>> images;
    const std::array<std::array<unsigned,2>,5> sizes{{{64,48},{128,80},{96,64},{96,64},{64,48}}};
    for(frame_index=0;frame_index<sizes.size();++frame_index) @autoreleasepool {
        const auto [w,h]=sizes[frame_index];visible=frame_index!=3;
        auto desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
        desc.storageMode=MTLStorageModePrivate;desc.usage=MTLTextureUsageRenderTarget;
        auto target=[device newTextureWithDescriptor:desc];assert(target);
        auto buffer=[device newBufferWithLength:w*h*4 options:MTLResourceStorageModeShared];
        auto cb=[queue commandBuffer];
        auto pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
        pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,1);
        auto encoder=[cb renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder setViewport:MTLViewport{12,8,double(w-24),double(h-16),0,1}];
        [encoder setScissorRect:MTLScissorRect{12,8,w-24,h-16}];
        [encoder setCullMode:MTLCullModeBack];
        [encoder setFrontFacingWinding:MTLWindingCounterClockwise];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        if(merged)Metal::Overlay::Render((__bridge const void*)cb,(__bridge const void*)target,(__bridge const void*)encoder);
        [encoder endEncoding];
        if(!merged)Metal::Overlay::Render((__bridge const void*)cb,(__bridge const void*)target);
        auto blit=[cb blitCommandEncoder];
        [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
                  sourceSize:MTLSizeMake(w,h,1) toBuffer:buffer destinationOffset:0 destinationBytesPerRow:w*4 destinationBytesPerImage:w*h*4];
        [blit endEncoding];[cb commit];[cb waitUntilCompleted];assert(cb.status==MTLCommandBufferStatusCompleted && !cb.error);
        auto& pixels=images.emplace_back(w*h*4);std::memcpy(pixels.data(),buffer.contents,pixels.size());
    }
    ImGui_ImplMetal_Shutdown();ImGui::DestroyContext();return images;
}

int main() { @autoreleasepool {
    auto device=MTLCreateSystemDefaultDevice();assert(device);
    const auto original=Run(device,false),merged=Run(device,true);
    assert(original==merged);
    // Do not let identical blank results pass: game content survives, and the overlay
    // really reaches outside the game's letterboxed viewport in the visible case.
    const auto center=(32*96+48)*4, corner=(5*96+5)*4;
    assert(original[3][center]>0 && original[3][center+1]>0 && original[3][center+2]>0);
    assert(original[3][corner]==0 && original[3][corner+1]==0 && original[3][corner+2]==0);
    assert(original[0][(5*64+5)*4]>0);
    std::size_t bytes=0;for(const auto& image:original)bytes+=image.size();
    std::printf("present+overlay: %zu identical pixel bytes; resize, letterbox, clipped UI, font updates, hidden/reopened UI and state reset passed\n",bytes);
}}
