// SPDX-License-Identifier: GPL-2.0-or-later
// Authored pixels and actual headers only; no game/window/assets or production IO.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cassert>
#include <cstddef>
#include <cstring>
#include <filesystem>
#include <fstream>
#include "shader_recompiler/vertex_float_rules.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "video_core/renderer_metal/mtl_local_library.h"
#include "video_core/renderer_metal/mtl_function_identity.h"

enum class Software { Fragment, TessellationControl, TessellationEval, Vertex, Geometry, Compute };
enum class Hardware { Fragment, Vertex, Geometry, Export, Hull, Local, Compute };
static_assert(sizeof(Shader::Profile) == 80);
static_assert(offsetof(Shader::Profile, relaxed_fragment_compute_float) == 72);
static_assert(offsetof(Shader::Profile, two_level_bda) == 73);
static_assert(offsetof(Shader::Profile, relaxed_vertex_float) == 74);

static std::string Source(bool second) {
    return std::string(R"(#include <metal_stdlib>
using namespace metal;
struct Out { float4 position [[position, invariant]]; };
vertex Out vertex0(uint id [[vertex_id]], constant float4& c [[buffer(0)]]) {
    float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};
    Out out;
    out.position=float4(p[id].x*c.x+c.y,p[id].y*c.x+c.y,c.z*c.w+c.y,1);
    return out;
}
fragment float4 fragment0() { return float4()") +
        (second ? "0,1,0,1); }\n" : "1,0,0,1); }\n");
}

static id<MTLRenderPipelineState> Pipeline(id<MTLDevice> device, id<MTLLibrary> library) {
    auto desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = [library newFunctionWithName:@"vertex0"];
    desc.fragmentFunction = [library newFunctionWithName:@"fragment0"];
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    desc.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
    NSError* error = nil;
    auto pso = [device newRenderPipelineStateWithDescriptor:desc error:&error];
    if (!pso) fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
    assert(pso);
    return pso;
}

int main(int argc, char** argv) { @autoreleasepool {
    assert(argc == 3);
    const std::filesystem::path dir = argv[1];
    const bool load = std::string_view(argv[2]) == "load";
    assert(dir.is_absolute() && std::filesystem::is_directory(dir));
    // Exhaustive policy matrix, including ES/LS/GS/TCS/TES and Strict.
    for (unsigned fragment = 0; fragment < 2; ++fragment)
        for (unsigned vertex = 0; vertex < 2; ++vertex)
            for (unsigned sw = 0; sw < 6; ++sw)
                for (unsigned hw = 0; hw < 7; ++hw) {
                    Shader::Profile profile{};
                    profile.relaxed_fragment_compute_float = fragment;
                    profile.relaxed_vertex_float = vertex;
                    assert(Shader::UsesInvariantVertexFloat(profile, Software(sw), Hardware(hw)) ==
                           (fragment && vertex && Software(sw) == Software::Vertex &&
                            Hardware(hw) == Hardware::Vertex));
                }
    id<MTLDevice> device = MTLCreateSystemDefaultDevice(); assert(device);
    auto options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    options.mathMode = MTLMathModeSafe;
    options.mathFloatingPointFunctions = MTLMathFloatingPointFunctionsFast;
    assert(Metal::LocalLibrary::Options(options));
    options.preserveInvariance = YES;
    // Full helper-library support is intentionally not broadened by the game recipe.
    assert(!Metal::LocalLibrary::Options(options));
    const std::string cohort(64, 'a'), digest(64, 'b');
    assert(Metal::LocalAot::Key(cohort, digest, "vertex0", false) !=
           Metal::LocalAot::Key(cohort, digest, "vertex0", false, true));
    id<MTLLibrary> libraries[2];
    NSMutableArray* keys = [NSMutableArray new];
    for (int i = 0; i < 2; ++i) {
        const auto source = Source(i);
        auto local = Metal::LocalAot::Try(device, source, "vertex0", false, false,
                                         true, nil, true);
        assert(bool(local.library) == load);
        if (load) {
            libraries[i] = local.library;
        } else {
            NSError* error = nil;
            libraries[i] = [device newLibraryWithSource:@(source.c_str()) options:options error:&error];
            if (!libraries[i]) fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
            assert(libraries[i]);
            auto entry = [libraries[i] newFunctionWithName:@"vertex0"]; assert(entry);
            local = Metal::LocalAot::Try(device, source, "vertex0", false, true,
                                        false, entry, true);
            assert(local.recorded);
        }
        [keys addObject:@(local.key.c_str())];
        // One source with different options must not consume the invariant artifact.
        auto wrong = Metal::LocalAot::Try(device, source, "vertex0", false);
        assert(!wrong.library && wrong.key != local.key);
    }
    auto colorDesc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
        width:32 height:32 mipmapped:NO];
    colorDesc.usage = MTLTextureUsageRenderTarget; colorDesc.storageMode = MTLStorageModePrivate;
    auto color = [device newTextureWithDescriptor:colorDesc]; assert(color);
    auto depthDesc = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth32Float
        width:32 height:32 mipmapped:NO];
    depthDesc.usage = MTLTextureUsageRenderTarget; depthDesc.storageMode = MTLStorageModePrivate;
    auto depth = [device newTextureWithDescriptor:depthDesc]; assert(depth);
    auto pass = [MTLRenderPassDescriptor new];
    pass.colorAttachments[0].texture=color; pass.colorAttachments[0].loadAction=MTLLoadActionClear;
    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
    pass.depthAttachment.texture=depth; pass.depthAttachment.loadAction=MTLLoadActionClear;
    pass.depthAttachment.storeAction=MTLStoreActionDontCare; pass.depthAttachment.clearDepth=1;
    auto writeDesc=[MTLDepthStencilDescriptor new];writeDesc.depthCompareFunction=MTLCompareFunctionAlways;
    writeDesc.depthWriteEnabled=YES;auto write=[device newDepthStencilStateWithDescriptor:writeDesc];
    auto equalDesc=[MTLDepthStencilDescriptor new];equalDesc.depthCompareFunction=MTLCompareFunctionEqual;
    auto equal=[device newDepthStencilStateWithDescriptor:equalDesc];
    float coefficients[4]{1.f,0.f,0.1234567f,0.3141593f};
    auto queue=[device newCommandQueue];auto command=[queue commandBuffer];
    auto encoder=[command renderCommandEncoderWithDescriptor:pass];
    [encoder setVertexBytes:coefficients length:sizeof(coefficients) atIndex:0];
    [encoder setRenderPipelineState:Pipeline(device,libraries[0])];[encoder setDepthStencilState:write];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder setRenderPipelineState:Pipeline(device,libraries[1])];[encoder setDepthStencilState:equal];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];
    auto bytes=[device newBufferWithLength:256*32 options:MTLResourceStorageModeShared];
    auto blit=[command blitCommandEncoder];[blit copyFromTexture:color sourceSlice:0 sourceLevel:0
        sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(32,32,1) toBuffer:bytes
        destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256*32];
    [blit endEncoding];[command commit];[command waitUntilCompleted];
    assert(command.status==MTLCommandBufferStatusCompleted);
    const auto* pixels=static_cast<const unsigned char*>(bytes.contents);
    for(unsigned y=0;y<32;++y)for(unsigned x=0;x<32;++x){auto p=pixels+y*256+x*4;
        assert(p[0]==0&&p[1]==255&&p[2]==0&&p[3]==255);}
    auto result=@{@"passed":@YES,@"local_hit":@(load),@"depth_equal_pixels":@1024,
        @"policy_cases":@168,@"keys":keys,@"game_started":@NO};
    auto data=[NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
    assert([data writeToFile:@((dir/"result.json").c_str()) atomically:YES]);
    printf("PASS invariant authored vertex, depth-equal 1024 pixels, local=%d\n",int(load));
}}
