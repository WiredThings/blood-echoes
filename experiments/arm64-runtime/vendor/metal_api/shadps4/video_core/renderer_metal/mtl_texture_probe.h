// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Private one-shot diagnosis. No preferences/cache writes, no readback in normal play.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <array>
#include <algorithm>
#include <cstring>
#include <format>
#include <set>
#include <string>
#include <vector>
#include "mtl_texture_probe_policy.h"
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_scheduler.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"

namespace Metal {
template<class FC=VideoCore::FrameCapture> class BasicTextureProbe {
    struct Image {
        id<MTLTexture> source=nil; // Scheduler uses unretained command references.
        id<MTLBuffer> bytes=nil;
        uint64_t native{},tick{},size{};
        std::string file;
    };
    std::vector<Image> pending;
    std::set<uint64_t> seen;
    std::set<uint32_t> fragment,indices;
    uint64_t sequence{},checked_sequence{},used{};
    uint32_t vertex{};
    int directory=-1;
    bool limited=false;
    static bool Number(id value,uint32_t& out) {
        if(![value isKindOfClass:[NSNumber class]]||CFGetTypeID((__bridge CFTypeRef)value)==CFBooleanGetTypeID())return false;
        NSNumber* n=value;const char* type=n.objCType;
        if(!type||*type=='d'||*type=='f'||n.longLongValue<0||n.unsignedLongLongValue>UINT32_MAX)return false;
        out=n.unsignedIntValue;return true;
    }
    static bool Numbers(id value,std::set<uint32_t>& out,unsigned bound) {
        if(![value isKindOfClass:[NSArray class]]||[value count]==0||[value count]>bound)return false;
        for(id item in value){uint32_t number;if(!Number(item,number)||!number)return false;out.insert(number);}
        return true;
    }
    void Admit(uint64_t current) {
        if(checked_sequence==current)return;
        checked_sequence=current;
        const auto& root=FC::TextureDiagnosticDirectory();if(root.empty())return;
        int dir=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if(dir<0)return;
        struct stat ds{};if(fstat(dir,&ds)||ds.st_uid!=getuid()||(ds.st_mode&077)){close(dir);return;}
        int fd=openat(dir,"texel-request.json",O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
        if(fd<0){close(dir);return;}
        struct stat st{},after{};std::array<char,4097> raw{};
        bool ok=!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_nlink==1&&!(st.st_mode&077)&&st.st_size>0&&st.st_size<=4096;
        ssize_t count=ok?read(fd,raw.data(),raw.size()):-1;
        ok=ok&&count==st.st_size&&!fstat(fd,&after)&&st.st_ino==after.st_ino&&st.st_size==after.st_size&&st.st_mtimespec.tv_sec==after.st_mtimespec.tv_sec&&st.st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec&&st.st_ctimespec.tv_sec==after.st_ctimespec.tv_sec&&st.st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec;
        close(fd);
        if(ok) {
            NSData* data=[NSData dataWithBytes:raw.data() length:count];
            id config=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];uint32_t schema=0;
            ok=[config isKindOfClass:[NSDictionary class]]&&[config count]==4&&Number(config[@"schema"],schema)&&schema==1&&Number(config[@"vs"],vertex)&&vertex&&Numbers(config[@"ps"],fragment,8)&&Numbers(config[@"indices"],indices,32);
        }
        if(!ok){FC::Native("{\"op\":\"texture_probe_request\",\"accepted\":false}");close(dir);fragment.clear();indices.clear();return;}
        if(unlinkat(dir,"texel-request.json",0)){close(dir);fragment.clear();indices.clear();return;}
        directory=dir;sequence=current;used=0;limited=false;seen.clear();
        FC::Native("{\"op\":\"texture_probe_request\",\"accepted\":true,\"max_bytes\":16777216,\"max_images\":32,\"first_sample_only\":true,\"performance_sample\":false}");
    }
    static bool Format(MTLPixelFormat format,unsigned& block,unsigned& bytes) {
        block=1;bytes=4;
        switch(format) {
        case MTLPixelFormatRGBA8Unorm:case MTLPixelFormatRGBA8Unorm_sRGB:
        case MTLPixelFormatRGBA8Uint:case MTLPixelFormatRGBA8Sint:
        case MTLPixelFormatRGBA8Snorm:case MTLPixelFormatBGRA8Unorm:
        case MTLPixelFormatBGRA8Unorm_sRGB:case MTLPixelFormatR32Float:return true;
        case MTLPixelFormatBC1_RGBA:case MTLPixelFormatBC1_RGBA_sRGB:
        case MTLPixelFormatBC4_RUnorm:case MTLPixelFormatBC4_RSnorm:block=4;bytes=8;return true;
        case MTLPixelFormatBC7_RGBAUnorm:case MTLPixelFormatBC7_RGBAUnorm_sRGB:
        case MTLPixelFormatBC5_RGUnorm:case MTLPixelFormatBC5_RGSnorm:block=4;bytes=16;return true;
        default:return false;
        }
    }
public:
    ~BasicTextureProbe(){if(directory>=0)close(directory);}
    bool Draw(Scheduler& scheduler,id<MTLDevice> device,uint64_t vs,uint64_t ps,
              uint32_t count,const CompiledShader& shader,void* const* textures,uint32_t size) {
        if(!FC::TextureBindingsEnabled()||!FC::NativeRecording())return false;
        const auto current=FC::NativeCaptureSequence();Admit(current);
        if(directory<0||sequence!=current||vs!=vertex||!fragment.contains(uint32_t(ps))||!indices.contains(count))return false;
        bool encoded=false;
        for(const auto& binding:shader.bindings) {
            if(binding.kind!=ShaderBinding::Kind::Texture)continue;
            for(uint32_t e=0;e<binding.count;++e) {
                const uint64_t slot=uint64_t(binding.binding)+e;
                if(slot>=size||!textures[slot])continue;
                auto texture=(__bridge id<MTLTexture>)textures[slot];
                auto parent=texture.parentTexture?texture.parentTexture:texture;
                const uint64_t native=uintptr_t((__bridge void*)texture);
                const auto sw=texture.swizzle;
                FC::Native(std::format("{{\"op\":\"texture_sample_view\",\"vs\":{},\"ps\":{},\"indices\":{},\"slot\":{},\"msl_index\":{},\"native\":{},\"parent\":{},\"format\":{},\"parent_format\":{},\"type\":{},\"width\":{},\"height\":{},\"levels\":{},\"layers\":{},\"relative_level\":{},\"relative_slice\":{},\"usage\":{},\"storage\":{},\"swizzle\":[{},{},{},{}]}}",
                    vs,ps,count,slot,binding.msl_index+e,native,uintptr_t((__bridge void*)parent),uint64_t(texture.pixelFormat),uint64_t(parent.pixelFormat),uint64_t(texture.textureType),texture.width,texture.height,texture.mipmapLevelCount,texture.arrayLength,texture.parentRelativeLevel,texture.parentRelativeSlice,uint64_t(texture.usage),uint64_t(texture.storageMode),uint64_t(sw.red),uint64_t(sw.green),uint64_t(sw.blue),uint64_t(sw.alpha)));
                if(seen.contains(native))continue;
                if(seen.size()>=TextureProbePolicy::MaxImages){limited=true;continue;}
                seen.insert(native);
                unsigned block=0,bytes=0;
                if(texture.textureType!=MTLTextureType2D||texture.sampleCount!=1||texture.arrayLength!=1||texture.mipmapLevelCount>TextureProbePolicy::MaxLevels||!Format(texture.pixelFormat,block,bytes)) {
                    FC::Native(std::format("{{\"op\":\"texture_probe_skip\",\"native\":{},\"reason\":\"unsupported-shape-or-format\"}}",native));continue;
                }
                std::vector<TextureProbePolicy::Plane> planes;uint64_t total=0;
                for(unsigned level=0;level<texture.mipmapLevelCount;++level) {
                    auto p=TextureProbePolicy::Layout(std::max<NSUInteger>(1,texture.width>>level),std::max<NSUInteger>(1,texture.height>>level),block,bytes,total);
                    if(!p){planes.clear();break;}planes.push_back(*p);total=p->offset+p->bytes;
                }
                if(planes.empty()||total>TextureProbePolicy::MaxBytes-used){limited=true;continue;}
                id<MTLBuffer> buffer=[device newBufferWithLength:total options:MTLResourceStorageModeShared];
                if(!buffer){FC::Native("{\"op\":\"texture_probe_skip\",\"reason\":\"allocation-failed\"}");continue;}
                // Padding is diagnostic-owned, initialized for deterministic safe file output.
                std::memset(buffer.contents,0,total);
                scheduler.BlitEncoder("bounded texture probe before sample");
                encoded=true;
                auto encoder=scheduler.GetImpl().blit;
                for(unsigned level=0;level<planes.size();++level) {
                    const auto& p=planes[level];
                    [encoder copyFromTexture:texture sourceSlice:0 sourceLevel:level sourceOrigin:MTLOriginMake(0,0,0)
                        sourceSize:MTLSizeMake(std::max<NSUInteger>(1,texture.width>>level),std::max<NSUInteger>(1,texture.height>>level),1)
                        toBuffer:buffer destinationOffset:p.offset destinationBytesPerRow:p.row destinationBytesPerImage:p.bytes];
                    FC::Native(std::format("{{\"op\":\"texture_probe_plane\",\"native\":{},\"level\":{},\"offset\":{},\"row\":{},\"rows\":{},\"block\":{},\"block_bytes\":{}}}",native,level,p.offset,p.row,p.rows,block,bytes));
                }
                used+=total;
                const auto file=std::format("texels_{}_{}.raw",sequence,native);
                pending.push_back({texture,buffer,native,scheduler.CurrentTick(),total,file});
                FC::Native(std::format("{{\"op\":\"texture_probe_encoded\",\"native\":{},\"tick\":{},\"bytes\":{},\"file\":\"{}\",\"before_sample\":true}}",native,scheduler.CurrentTick(),total,file));
            }
        }
        return encoded;
    }
    void Drain(Scheduler& scheduler) {
        if(directory<0)return;
        if(!pending.empty())scheduler.Finish();
        for(const auto& item:pending) {
            bool complete=scheduler.IsFreeKnown(item.tick),ok=false;
            if(complete&&item.bytes.contents) {
                int fd=openat(directory,item.file.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
                if(fd>=0){uint64_t done=0;while(done<item.size){ssize_t n=write(fd,static_cast<const char*>(item.bytes.contents)+done,item.size-done);if(n<=0)break;done+=n;}
                    ok=done==item.size;ok=(close(fd)==0)&&ok;if(!ok)unlinkat(directory,item.file.c_str(),0);}
            }
            FC::Native(std::format("{{\"op\":\"texture_probe_saved\",\"native\":{},\"bytes\":{},\"gpu_complete\":{},\"written\":{},\"file\":\"{}\"}}",item.native,item.size,complete,ok,item.file));
        }
        FC::Native(std::format("{{\"op\":\"texture_probe_end\",\"images\":{},\"bytes\":{},\"limited\":{}}}",pending.size(),used,limited));
        pending.clear();seen.clear();fragment.clear();indices.clear();used=0;sequence=0;close(directory);directory=-1;
    }
};
using TextureProbe=BasicTextureProbe<>;
}
