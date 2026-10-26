// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_render_list: the same command sequence, with redundant calls
// the filter drops and calls it must keep (a buffer slot overwritten by bytes, an offset-only
// change), is issued once directly and once recorded then replayed; both must draw the same
// pixels. Needs a Metal device, no window or game.
//
//   mtl-render-list-test
//
// Exit 0 when every check passed.
#import <Metal/Metal.h>

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "video_core/renderer_metal/mtl_render_list.h"

using Metal::RenderList;

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

static NSString* const shader_source = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "vertex float4 vs(uint id [[vertex_id]]) {\n"
    "    float2 p = float2((id << 1) & 2, id & 2);\n"
    "    return float4(p * 2.0 - 1.0, 0.0, 1.0);\n"
    "}\n"
    "fragment float4 fs(constant float4* color [[buffer(0)]]) { return color[0]; }\n";

constexpr uint32_t kWidth = 8;

static void* Raw(id object) {
    return (__bridge void*)object;
}

struct Fixture {
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    id<MTLRenderPipelineState> pipeline;
    id<MTLBuffer> green;  ///< one float4: green
    id<MTLBuffer> pair;   ///< two float4: blue, then white
    id<MTLTexture> target;
};

/// The commands of the scenario, one pixel column per draw.
static void Scenario(RenderList& list, const Fixture& f) {
    using Stage = RenderList::Stage;
    const RenderList::Viewport viewport{0, 0, double(kWidth), 1, 0, 1};
    const float red[4] = {1, 0, 0, 1};
    list.PushDebugGroup("scenario");
    list.SetRenderPipelineState(Raw(f.pipeline));
    list.SetCullMode(MTLCullModeNone);
    list.SetViewports(&viewport, 1);
    for (uint32_t i = 0; i < 6; ++i) {
        // Redundant after the first draw: pipeline, cull mode, viewport.
        list.SetRenderPipelineState(Raw(f.pipeline));
        list.SetCullMode(MTLCullModeNone);
        list.SetViewports(&viewport, 1);
        const RenderList::Scissor scissor{i, 0, 1, 1};
        list.SetScissorRects(&scissor, 1);
        switch (i) {
        case 0: // set
        case 1: // the same: dropped
            list.SetBuffer(Stage::Fragment, Raw(f.green), 0, 0);
            break;
        case 2: // bytes
            list.SetBytes(Stage::Fragment, red, sizeof(red), 0);
            break;
        case 3: // the same buffer again, after bytes took the slot: must not be dropped
            list.SetBuffer(Stage::Fragment, Raw(f.green), 0, 0);
            break;
        case 4: // a new buffer
            list.SetBuffer(Stage::Fragment, Raw(f.pair), 0, 0);
            break;
        case 5: // the same buffer at another offset: must not be dropped
            list.SetBuffer(Stage::Fragment, Raw(f.pair), 16, 0);
            break;
        }
        list.DrawPrimitives(MTLPrimitiveTypeTriangle, 0, 3, 1, 0);
    }
    list.InsertDebugSignpost("done");
    list.PopDebugGroup();
    list.MemoryBarrier();
}

static MTLRenderPassDescriptor* Pass(const Fixture& f) {
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = f.target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 0);
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    return pass;
}

static std::array<uint32_t, kWidth> Read(const Fixture& f) {
    std::array<uint32_t, kWidth> pixels{};
    [f.target getBytes:pixels.data()
           bytesPerRow:sizeof(pixels)
            fromRegion:MTLRegionMake2D(0, 0, kWidth, 1)
           mipmapLevel:0];
    return pixels;
}

static std::array<uint32_t, kWidth> Run(const Fixture& f, RenderList& list, bool recorded) {
    id<MTLCommandBuffer> cmd = [f.queue commandBuffer];
    id<MTLRenderCommandEncoder> encoder = [cmd renderCommandEncoderWithDescriptor:Pass(f)];
    if (recorded) {
        list.BeginRecording();
        CHECK(list.Recording() && list.Empty() && !list.HasBarrier());
        Scenario(list, f);
        CHECK(!list.Empty() && list.HasBarrier());
        list.Replay(Raw(encoder));
        CHECK(list.Empty());
    } else {
        list.BeginDirect(Raw(encoder));
        CHECK(!list.Recording() && list.Empty());
        Scenario(list, f);
        CHECK(list.Empty() && list.HasBarrier());
    }
    list.End();
    [encoder endEncoding];
    [cmd commit];
    [cmd waitUntilCompleted];
    CHECK(cmd.status == MTLCommandBufferStatusCompleted);
    return Read(f);
}


// The fake encoder checks forwarded API calls for every draw family, including zero-count
// draws. These are control-flow checks; the separate atomic producer/consumer runs on Metal.
@interface BarrierEncoder : NSObject
@property NSUInteger barriers;
@property NSUInteger draws;
@end
@implementation BarrierEncoder
- (void)memoryBarrierWithScope:(MTLBarrierScope)s afterStages:(MTLRenderStages)a beforeStages:(MTLRenderStages)b { ++_barriers; }
- (void)setCullMode:(MTLCullMode)m {}
- (void)insertDebugSignpost:(NSString*)s {}
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)i baseInstance:(NSUInteger)b { ++_draws; }
- (void)drawIndexedPrimitives:(MTLPrimitiveType)t indexCount:(NSUInteger)n indexType:(MTLIndexType)i indexBuffer:(id<MTLBuffer>)b indexBufferOffset:(NSUInteger)o instanceCount:(NSUInteger)c baseVertex:(NSInteger)v baseInstance:(NSUInteger)a { ++_draws; }
- (void)drawPrimitives:(MTLPrimitiveType)t indirectBuffer:(id<MTLBuffer>)b indirectBufferOffset:(NSUInteger)o { ++_draws; }
- (void)drawIndexedPrimitives:(MTLPrimitiveType)t indexType:(MTLIndexType)i indexBuffer:(id<MTLBuffer>)b indexBufferOffset:(NSUInteger)o indirectBuffer:(id<MTLBuffer>)a indirectBufferOffset:(NSUInteger)p { ++_draws; }
- (void)drawPatches:(NSUInteger)n patchStart:(NSUInteger)s patchCount:(NSUInteger)c patchIndexBuffer:(id<MTLBuffer>)b patchIndexBufferOffset:(NSUInteger)o instanceCount:(NSUInteger)i baseInstance:(NSUInteger)a { ++_draws; }
@end

static bool Coalescing() {
    const char* flag=std::getenv("BB_MTL_COALESCE_BARRIERS");return flag&&flag[0]=='1';
}
static void BarrierSequence(RenderList& l) {
    l.MemoryBarrier();l.SetCullMode(MTLCullModeNone);l.MemoryBarrier();
    l.DrawPrimitives(MTLPrimitiveTypeTriangle,0,0,1,0);
    l.MemoryBarrier();l.InsertDebugSignpost("binding-only gap");l.MemoryBarrier();
    l.DrawIndexedPrimitives(MTLPrimitiveTypeTriangle,0,MTLIndexTypeUInt16,nullptr,0,1,0,0);
    l.MemoryBarrier();l.MemoryBarrier();
    l.DrawPrimitivesIndirect(MTLPrimitiveTypeTriangle,nullptr,0);
    l.MemoryBarrier();l.MemoryBarrier();
    l.DrawIndexedPrimitivesIndirect(MTLPrimitiveTypeTriangle,MTLIndexTypeUInt16,nullptr,0,nullptr,0);
    l.MemoryBarrier();l.MemoryBarrier();
    l.DrawPatches(3,0,0,1,0);
    l.MemoryBarrier();l.MemoryBarrier();
}
static void TestBarrierControlFlow() {
    for(unsigned mode=0;mode<3;++mode) {
        RenderList source,destination;auto enc=[BarrierEncoder new];
        if(!mode)source.BeginDirect(Raw(enc));else source.BeginRecording();
        BarrierSequence(source);CHECK(source.HasBarrier());
        RenderList* active=&source;
        if(mode==2){source.TakeRecording(destination);source.End();active=&destination;CHECK(active->HasBarrier());}
        if(mode)active->Replay(Raw(enc));
        CHECK(enc.draws==5);CHECK(enc.barriers==(Coalescing()?6:12));
        auto stats=active->BarrierSnapshot();CHECK(stats.requested==12 && stats.dropped==(Coalescing()?6:0));
        active->End();
        // A new physical encoder must keep its first barrier, regardless of the old final op.
        active->BeginDirect(Raw(enc));active->MemoryBarrier();active->End();
        CHECK(enc.barriers==(Coalescing()?7:13));
    }
    std::puts("barrier filter: direct/recorded/moved/reset and every draw family passed");
}

static void TestBarrierAtomicOrdering(const Fixture& f, bool vertex) {
    NSString* src=@"#include <metal_stdlib>\nusing namespace metal;\n"
      "vertex float4 vs(uint id[[vertex_id]]){float2 p=float2((id<<1)&2,id&2);return float4(p*2-1,0,1);}\n"
      "fragment float4 fs(device atomic_uint* counter[[buffer(0)]],constant uint& mode[[buffer(1)]]) {uint v=mode?atomic_load_explicit(counter,memory_order_relaxed):atomic_fetch_add_explicit(counter,1u,memory_order_relaxed)+1u;return float4(float(v)/255.0,0,0,1);}\n";
    if(vertex)src=@"#include <metal_stdlib>\nusing namespace metal;\n"
      "struct O{float4 position[[position]];float4 color[[user(locn0)]];};\n"
      "vertex O vs(uint id[[vertex_id]],device atomic_uint* counter[[buffer(0)]],constant uint& mode[[buffer(1)]]){if(!mode&&id==0)atomic_fetch_add_explicit(counter,1u,memory_order_relaxed);uint v=mode?atomic_load_explicit(counter,memory_order_relaxed):0u;float2 p=float2((id<<1)&2,id&2);return O{float4(p*2-1,0,1),float4(float(v)/255.0,0,0,1)};}\n"
      "fragment float4 fs(O in[[stage_in]]){return in.color;}\n";
    NSError* error=nil;auto lib=[f.device newLibraryWithSource:src options:nil error:&error];
    CHECK(lib);if(!lib){std::printf("atomic library: %s\n",error.localizedDescription.UTF8String);return;}
    auto desc=[MTLRenderPipelineDescriptor new];desc.vertexFunction=[lib newFunctionWithName:@"vs"];desc.fragmentFunction=[lib newFunctionWithName:@"fs"];desc.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    auto pipeline=[f.device newRenderPipelineStateWithDescriptor:desc error:&error];CHECK(pipeline);if(!pipeline)return;
    auto counter=[f.device newBufferWithLength:256 options:MTLResourceStorageModeShared];
    for(unsigned mode=0;mode<3;++mode) {
        *static_cast<uint32_t*>(counter.contents)=0;
        auto cmd=[f.queue commandBuffer];auto enc=[cmd renderCommandEncoderWithDescriptor:Pass(f)];
        RenderList l,moved;if(!mode)l.BeginDirect(Raw(enc));else l.BeginRecording();
        l.SetRenderPipelineState(Raw(pipeline));const auto stage=vertex?RenderList::Stage::Vertex:RenderList::Stage::Fragment;l.SetBuffer(stage,Raw(counter),0,0);
        RenderList::Viewport viewport{0,0,double(kWidth),1,0,1};l.SetViewports(&viewport,1);
        RenderList::Scissor scissor{0,0,1,1};l.SetScissorRects(&scissor,1);
        for(unsigned draw=0;draw<7;++draw) {
            l.MemoryBarrier();uint32_t read_only=draw==6;l.SetBytes(stage,&read_only,4,1);l.MemoryBarrier();l.SetCullMode(MTLCullModeNone);l.MemoryBarrier();
            l.DrawPrimitives(MTLPrimitiveTypeTriangle,0,3,1,0);
        }
        RenderList* active=&l;if(mode==2){l.TakeRecording(moved);l.End();active=&moved;}
        if(mode)active->Replay(Raw(enc));
        auto stats=active->BarrierSnapshot();CHECK(stats.requested==21 && stats.dropped==(Coalescing()?14:0));active->End();
        [enc endEncoding];[cmd commit];[cmd waitUntilCompleted];CHECK(cmd.status==MTLCommandBufferStatusCompleted);
        CHECK(*static_cast<uint32_t*>(counter.contents)==6);CHECK(Read(f)[0]==0xff000006);
    }
    std::puts("barrier filter: atomic write/read dependencies and exact pixels direct/recorded/moved passed");
}

int main() {
    @autoreleasepool {
        Fixture f;
        f.device = MTLCreateSystemDefaultDevice();
        if (!f.device) {
            std::printf("no Metal device\n");
            return 2;
        }
        f.queue = [f.device newCommandQueue];
        NSError* error = nil;
        id<MTLLibrary> library = [f.device newLibraryWithSource:shader_source
                                                        options:nil
                                                          error:&error];
        if (!library) {
            std::printf("shaders: %s\n", error.localizedDescription.UTF8String);
            return 2;
        }
        MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
        desc.vertexFunction = [library newFunctionWithName:@"vs"];
        desc.fragmentFunction = [library newFunctionWithName:@"fs"];
        desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
        f.pipeline = [f.device newRenderPipelineStateWithDescriptor:desc error:&error];
        if (!f.pipeline) {
            std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
            return 2;
        }
        const float green[4] = {0, 1, 0, 1};
        const float pair[8] = {0, 0, 1, 1, 1, 1, 1, 1};
        f.green = [f.device newBufferWithBytes:green length:sizeof(green) options:0];
        f.pair = [f.device newBufferWithBytes:pair length:sizeof(pair) options:0];
        MTLTextureDescriptor* td =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                               width:kWidth
                                                              height:1
                                                           mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget;
        td.storageMode = MTLStorageModeShared;
        f.target = [f.device newTextureWithDescriptor:td];

        // RGBA8 little endian: 0xAABBGGRR.
        constexpr uint32_t kGreen = 0xff00ff00, kRed = 0xff0000ff, kBlue = 0xffff0000,
                           kWhite = 0xffffffff;
        const std::array<uint32_t, kWidth> expected{kGreen, kGreen, kRed,  kGreen,
                                                    kBlue,  kWhite, 0,     0};
        TestBarrierControlFlow();
        TestBarrierAtomicOrdering(f,false);
        TestBarrierAtomicOrdering(f,true);
        RenderList list;
        const auto direct = Run(f, list, false);
        CHECK(direct == expected);
        const auto recorded = Run(f, list, true);
        CHECK(recorded == expected);
        // The list is reusable: the filter and the arena start empty again.
        const auto again = Run(f, list, true);
        CHECK(again == expected);
        if (failures) {
            for (uint32_t i = 0; i < kWidth; ++i) {
                std::printf("  pixel %u: direct %08x recorded %08x expected %08x\n", i, direct[i],
                            recorded[i], expected[i]);
            }
        }
    }
    std::printf(failures ? "FAILED: %d check(s)\n" : "all checks passed\n", failures);
    return failures ? 1 : 0;
}
