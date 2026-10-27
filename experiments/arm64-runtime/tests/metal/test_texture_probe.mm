// SPDX-License-Identifier: GPL-2.0-or-later
// Authored patterns only. Tests the actual one-shot reader, not game correctness.
#include <cassert>
#include <filesystem>
#include <fstream>
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_texture_probe.h"
#include "test_gpu_runtime_stubs.cpp"
struct Capture {
    static inline std::string directory;
    static inline std::vector<std::string> events;
    static inline bool enabled=true,recording=true;
    static inline uint64_t sequence=1;
    static bool TextureBindingsEnabled(){return enabled;}
    static bool NativeRecording(){return recording;}
    static uint64_t NativeCaptureSequence(){return sequence;}
    static const std::string& TextureDiagnosticDirectory(){return directory;}
    static void Native(std::string value){events.push_back(std::move(value));}
};
static void Request(const std::string& text) {
    const auto path=Capture::directory+"/texel-request.json";
    int fd=open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);assert(fd>=0);
    assert(write(fd,text.data(),text.size())==ssize_t(text.size()));assert(!close(fd));
}
static NSDictionary* Event(const char* name) {
    for(const auto& text:Capture::events){
        NSData* data=[NSData dataWithBytes:text.data() length:text.size()];
        NSDictionary* d=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        assert(d);if([d[@"op"] isEqualToString:[NSString stringWithUTF8String:name]])return d;
    }
    return nil;
}
static constexpr auto Config=R"({"schema":1,"vs":101,"ps":[202],"indices":[303]})";
int main(int argc,char** argv) {
    assert(argc==3);Capture::directory=argv[1];std::string kind=argv[2];
    std::filesystem::create_directory(Capture::directory);chmod(Capture::directory.c_str(),0700);
    const auto invalid=Metal::TextureProbePolicy::Layout(0,1,1,4,0);assert(!invalid);
    assert(!Metal::TextureProbePolicy::Layout(16385,1,1,4,0));
    assert(!Metal::TextureProbePolicy::Layout(4096,4096,1,4,0));
    auto small=Metal::TextureProbePolicy::Layout(1,1,4,8,0);assert(small&&small->row==256&&small->rows==1);
    auto full=Metal::TextureProbePolicy::Layout(2048,2048,1,4,0);assert(full&&full->bytes==Metal::TextureProbePolicy::MaxBytes);
    assert(!Metal::TextureProbePolicy::Layout(1,1,1,4,full->bytes));
    @autoreleasepool {
        Metal::Device device;Metal::Scheduler scheduler(device,"texture-probe-test");
        Metal::CompiledShader shader;shader.bindings.push_back({.binding=0,.kind=Metal::ShaderBinding::Kind::Texture,.msl_index=2,.count=1});
        auto native=device.GetImpl().device;
        const bool compressed=kind=="bc1"||kind=="bc4"||kind=="bc7";
        const unsigned block=compressed?4:1,block_bytes=kind=="bc7"?16:compressed?8:4;
        const auto parent_format=kind=="bc1"?MTLPixelFormatBC1_RGBA:kind=="bc4"?MTLPixelFormatBC4_RUnorm:kind=="bc7"?MTLPixelFormatBC7_RGBAUnorm:MTLPixelFormatRGBA8Uint;
        const auto view_format=kind=="bc1"?MTLPixelFormatBC1_RGBA_sRGB:kind=="bc4"?MTLPixelFormatBC4_RUnorm:kind=="bc7"?MTLPixelFormatBC7_RGBAUnorm_sRGB:MTLPixelFormatRGBA8Unorm;
        MTLTextureDescriptor* desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:parent_format width:32 height:16 mipmapped:YES];
        desc.storageMode=MTLStorageModeShared;desc.usage=MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
        id<MTLTexture> parent=[native newTextureWithDescriptor:desc];assert(parent);
        for(unsigned l=0;l<parent.mipmapLevelCount;++l){
            size_t w=std::max<NSUInteger>(1,parent.width>>l),h=std::max<NSUInteger>(1,parent.height>>l);
            size_t row=((w+block-1)/block)*block_bytes,rows=(h+block-1)/block;
            std::vector<unsigned char> bytes(row*rows,0x12+l);
            [parent replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:l withBytes:bytes.data() bytesPerRow:row];
        }
        auto view=[parent newTextureViewWithPixelFormat:view_format textureType:MTLTextureType2D levels:NSMakeRange(1,parent.mipmapLevelCount-1) slices:NSMakeRange(0,1) swizzle:MTLTextureSwizzleChannelsMake(MTLTextureSwizzleBlue,MTLTextureSwizzleGreen,MTLTextureSwizzleRed,MTLTextureSwizzleAlpha)];assert(view);
        void* textures[]={(__bridge void*)view};
        Metal::BasicTextureProbe<Capture> probe;
        if(kind=="invalid")Request(R"({"schema":true,"vs":101,"ps":[202],"indices":[303]})");
        else if(kind=="symlink-request") {std::ofstream(Capture::directory+"/source")<<Config;symlink("source",(Capture::directory+"/texel-request.json").c_str());}
        else Request(Config);
        if(kind=="disabled")Capture::enabled=false;
        if(kind=="not-recording")Capture::recording=false;
        if(kind=="symlink-output") {
            std::ofstream(Capture::directory+"/sentinel")<<"preserve";
            auto file=std::format("{}/texels_{}_{}.raw",Capture::directory,Capture::sequence,uintptr_t((__bridge void*)view));
            assert(!symlink("sentinel",file.c_str()));
        }
        probe.Draw(scheduler,native,101,202,kind=="no-match"?304:303,shader,textures,1);
        // A later write must not change the earlier sample-point snapshot.
        if(kind=="pixels") {
            const NSUInteger row=256,h=view.height;id<MTLBuffer> upload=[native newBufferWithLength:row*h options:MTLResourceStorageModeShared];memset(upload.contents,0xa5,row*h);
            scheduler.BlitEncoder("later test write");[scheduler.GetImpl().blit copyFromBuffer:upload sourceOffset:0 sourceBytesPerRow:row sourceBytesPerImage:row*h sourceSize:MTLSizeMake(view.width,h,1) toTexture:view destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
        }
        probe.Drain(scheduler);
        if(kind=="pixels"||compressed) {
            NSDictionary* saved=Event("texture_probe_saved");assert([saved[@"written"] boolValue]&&[saved[@"gpu_complete"] boolValue]);
            NSDictionary* selected=Event("texture_sample_view");assert([selected[@"format"] unsignedIntegerValue]==view_format);assert([selected[@"parent_format"] unsignedIntegerValue]==parent_format);assert([selected[@"relative_level"] unsignedIntegerValue]==1);assert([selected[@"levels"] unsignedIntegerValue]==parent.mipmapLevelCount-1);
            NSData* raw=[NSData dataWithContentsOfFile:[NSString stringWithFormat:@"%s/%@",Capture::directory.c_str(),saved[@"file"]]];assert(raw);
            unsigned level=0;
            for(const auto& text:Capture::events){NSDictionary* d=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:text.data() length:text.size()] options:0 error:nil];if(![d[@"op"] isEqualToString:@"texture_probe_plane"])continue;
                const size_t offset=[d[@"offset"] unsignedIntegerValue],row=[d[@"row"] unsignedIntegerValue];size_t w=std::max<NSUInteger>(1,view.width>>level),h=std::max<NSUInteger>(1,view.height>>level);
                const auto* p=static_cast<const unsigned char*>(raw.bytes);
                for(size_t y=0;y<(h+block-1)/block;++y)for(size_t x=0;x<((w+block-1)/block)*block_bytes;++x)assert(p[offset+y*row+x]==0x13+level);
                ++level;
            }
            assert(level==view.mipmapLevelCount);
            if(!compressed){std::vector<unsigned char> actual(view.width*view.height*4);[view getBytes:actual.data() bytesPerRow:view.width*4 fromRegion:MTLRegionMake2D(0,0,view.width,view.height) mipmapLevel:0];assert(actual[0]==0xa5);}
        } else if(kind=="symlink-output") {
            NSDictionary* saved=Event("texture_probe_saved");assert(saved&&![saved[@"written"] boolValue]);
            std::ifstream f(Capture::directory+"/sentinel");std::string value;f>>value;assert(value=="preserve");
        } else {
            assert(!Event("texture_probe_saved"));
            assert(!Event("texture_probe_encoded"));
            if(kind=="disabled"||kind=="not-recording")assert(Capture::events.empty());
        }
    }
    printf("texture probe %s PASS\n",kind.c_str());
}
