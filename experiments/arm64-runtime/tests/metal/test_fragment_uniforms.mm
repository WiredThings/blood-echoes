// SPDX-License-Identifier: GPL-2.0-or-later
#import <Metal/Metal.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "video_core/renderer_metal/mtl_fragment_uniforms.h"
static void Check(bool b) { if(!b) {std::puts("FAIL fragment uniform test");std::abort();} }
static const char* Source=R"(
#include <metal_stdlib>
using namespace metal;
struct Params { uint4 buf_offsets0; };
struct U { uint data[1]; };
struct V { float4 p [[position]]; float2 uv; };
vertex V vert(uint v [[vertex_id]]) {float2 p=float2((v<<1)&2,v&2);return {float4(p*2-1,0,1),p};}
fragment float4 pixel(V in [[stage_in]], constant Params& push_data [[buffer(0)]], const device U& ssbo_1 [[buffer(1)]], texture2d<float> image [[texture(0)]], sampler samp [[sampler(0)]])
{
    uint buf0_dword_off = extract_bits(push_data.buf_offsets0.x, 0u, 8u) >> 2u;
    float2 uv=in.uv*float2(as_type<float>(ssbo_1.data[0u + buf0_dword_off]),as_type<float>(ssbo_1.data[1u + buf0_dword_off]));
    float4 color=image.sample(samp,uv,level(as_type<float>(ssbo_1.data[2u + buf0_dword_off])));
    return float4(color.rgb*as_type<float>(ssbo_1.data[3u + buf0_dword_off]), color.a);
}
)";
int main() { @autoreleasepool {
    std::string original=Source;auto rewrite=Metal::RewriteFragmentUniforms(original);Check(rewrite.buffers==1);
    auto bad=[&](std::string from,std::string to) {auto s=original;auto p=s.find(from);Check(p!=std::string::npos);s.replace(p,from.size(),to);Check(Metal::RewriteFragmentUniforms(s).buffers==0);};
    bad("0u + buf0_dword_off","18446744073709551615u + buf0_dword_off");
    bad("0u + buf0_dword_off","1020u + buf0_dword_off");bad("0u + buf0_dword_off","uint(in.uv.x)");
    bad("const device U&","device U&");bad("uint data[1]","float data[1]");bad("return float4(color.rgb", "// atomic access is not eligible\n return float4(color.rgb");
    bad("    float2 uv=", "    buf0_dword_off = 9000;\n    float2 uv=");
    auto huge=original;huge.replace(huge.find("0u + buf0_dword_off"),std::string("0u + buf0_dword_off").size(),"999999999999999999999999999999u");Check(Metal::RewriteFragmentUniforms(huge).buffers==0);
    Check(Metal::RewriteFragmentUniforms(rewrite.source).buffers==0);
    id<MTLDevice>d=MTLCreateSystemDefaultDevice();Check(d!=nil);auto q=[d newCommandQueue];
    std::array<id<MTLLibrary>,2>lib;std::array<id<MTLRenderPipelineState>,2>pso;
    for(unsigned i=0;i<2;++i){auto options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_1;options.mathMode=MTLMathModeSafe;NSError*error=nil;
        lib[i]=[d newLibraryWithSource:[NSString stringWithUTF8String:(i?rewrite.source:original).c_str()] options:options error:&error];if(!lib[i])std::printf("%s\n",error.localizedDescription.UTF8String);Check(lib[i]!=nil);
        auto p=[MTLRenderPipelineDescriptor new];p.vertexFunction=[lib[i] newFunctionWithName:@"vert"];p.fragmentFunction=[lib[i] newFunctionWithName:@"pixel"];p.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;pso[i]=[d newRenderPipelineStateWithDescriptor:p error:&error];Check(pso[i]!=nil);}
    auto sampler=[MTLSamplerDescriptor new];sampler.minFilter=MTLSamplerMinMagFilterLinear;sampler.magFilter=MTLSamplerMinMagFilterLinear;sampler.mipFilter=MTLSamplerMipFilterLinear;sampler.sAddressMode=MTLSamplerAddressModeRepeat;sampler.tAddressMode=MTLSamplerAddressModeMirrorRepeat;auto samp=[d newSamplerStateWithDescriptor:sampler];
    auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:64 height:64 mipmapped:NO];td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModePrivate;auto dst=[d newTextureWithDescriptor:td];
    auto buffer=[d newBufferWithLength:4096 options:MTLResourceStorageModeShared];auto params=[d newBufferWithLength:256 options:MTLResourceStorageModeShared];
    for(auto format:{MTLPixelFormatRGBA8Unorm,MTLPixelFormatRGBA8Unorm_sRGB}) {
        td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:16 height:16 mipmapped:YES];td.usage=MTLTextureUsageShaderRead;td.storageMode=MTLStorageModeShared;auto input=[d newTextureWithDescriptor:td];
        for(unsigned level=0;level<input.mipmapLevelCount;++level){unsigned size=16>>level;std::vector<uint8_t>data(size*size*4);for(unsigned i=0;i<data.size();++i)data[i]=uint8_t(i*73+level*31);[input replaceRegion:MTLRegionMake2D(0,0,size,size) mipmapLevel:level withBytes:data.data() bytesPerRow:size*4];}
        for(unsigned offset:{0u,7u,63u})for(float lod:{0.f,.5f,1.25f,3.f}) {
            float constants[]={1.371f,-.917f,lod,.717173f};std::memcpy(static_cast<char*>(buffer.contents)+offset*4,constants,sizeof(constants));std::memset(params.contents,0,256);*static_cast<uint32_t*>(params.contents)=offset*4;
            std::array<std::vector<uint8_t>,2>pixels;
            for(unsigned i=0;i<2;++i){auto cb=[q commandBuffer];auto pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=dst;pass.colorAttachments[0].loadAction=MTLLoadActionDontCare;pass.colorAttachments[0].storeAction=MTLStoreActionStore;auto enc=[cb renderCommandEncoderWithDescriptor:pass];[enc setRenderPipelineState:pso[i]];[enc setFragmentBuffer:params offset:0 atIndex:0];[enc setFragmentBuffer:buffer offset:0 atIndex:1];[enc setFragmentTexture:input atIndex:0];[enc setFragmentSamplerState:samp atIndex:0];[enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[enc endEncoding];auto output=[d newBufferWithLength:64*64*16 options:MTLResourceStorageModeShared];auto blit=[cb blitCommandEncoder];[blit copyFromTexture:dst sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(64,64,1) toBuffer:output destinationOffset:0 destinationBytesPerRow:64*16 destinationBytesPerImage:64*64*16];[blit endEncoding];[cb commit];[cb waitUntilCompleted];Check(cb.error==nil);auto bytes=static_cast<uint8_t*>(output.contents);pixels[i]={bytes,bytes+64*64*16};}
            Check(pixels[0]==pixels[1]);
        }
    }
    std::puts("PASS 24 byte-exact fragment sampling cases: linear/sRGB, mip filtering, repeat/mirror, uniform offsets; unsafe source falls back");
} }
