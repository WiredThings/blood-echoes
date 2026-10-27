// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <atomic>
#include <chrono>
#include <mutex>
#include <new>
#include "mtl_texture_probe.h"
#include "mtl_memory_stats.h"
#include "video_core/texture_cache/image.h"

namespace Metal {
// Bounded three-patch history, not a full-texture correctness proof. Output stays
// private; no GPU waits, host guest reads, texture mutation or cache invalidation.
template<class FC=VideoCore::FrameCapture> class BasicTextureLineage {
    static constexpr unsigned MaxEvents=4096,MaxPending=64;
    std::mutex mutex;
    std::atomic<unsigned> pending{0};
    unsigned next=0,skips=0;
    uint64_t epoch=0;
    int directory=-1;
    bool checked=false;
    std::atomic<bool> failed{false};
    void Fail()noexcept {
        failed=true;
        if(directory>=0){int fd=openat(directory,"lineage-incomplete",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
            if(fd>=0){const char text[]="Diagnostic setup or allocation failed; history is incomplete.\n";(void)write(fd,text,sizeof(text)-1);close(fd);}}
        std::fputs("Metal: texture lineage disabled after diagnostic failure; game behavior unchanged\n",stderr);
    }
    bool Enabled() {
        if(failed)return false;
        if(checked)return directory>=0;
        checked=true;
        const auto& root=FC::TextureDiagnosticDirectory();if(root.empty())return false;
        int dir=open(root.c_str(),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);if(dir<0)return false;
        struct stat st{};
        if(fstat(dir,&st)||st.st_uid!=getuid()||(st.st_mode&077)){close(dir);return false;}
        int fd=openat(dir,"lineage-enable",O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
        bool good=fd>=0&&!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&st.st_nlink==1&&!(st.st_mode&077)&&st.st_size==0;
        if(fd>=0)close(fd);if(!good){close(dir);return false;}
        directory=dir;epoch=std::chrono::steady_clock::now().time_since_epoch().count();
        return Log(std::format("{{\"op\":\"lineage_scope\",\"pid\":{},\"epoch\":{},\"max_events\":{},\"max_pending\":{},\"partial_patches\":true,\"candidate_width\":256,\"candidate_height\":256,\"candidate_mips\":9,\"render_writes_not_snapshotted\":true}}",getpid(),epoch,MaxEvents,MaxPending));
    }
    bool Log(const std::string& line) {
        if(failed)return false;
        std::scoped_lock lock{mutex};
        const auto name=std::format("lineage_{}_{}.jsonl",getpid(),epoch);
        const std::string row=line+'\n';
        int fd=openat(directory,name.c_str(),O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
        if(fd<0){Fail();return false;}
        struct stat st{};if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_uid!=getuid()||st.st_nlink!=1||(st.st_mode&077)||st.st_size+row.size()>8*1024*1024){close(fd);Fail();return false;}
        size_t n=0;while(n<row.size()){const auto wrote=write(fd,row.data()+n,row.size()-n);if(wrote<=0)break;n+=wrote;}
        const bool good=close(fd)==0&&n==row.size();if(!good)Fail();return good;
    }
    unsigned Claim() {
        if(!Enabled())return 0;
        if(next>=MaxEvents||pending.load()>=MaxPending){
            if(skips++<16)Log(std::format("{{\"op\":\"lineage_gap\",\"event_limit\":{},\"pending_limit\":{},\"events\":{},\"pending\":{}}}",MaxEvents,MaxPending,next,pending.load()));
            return 0;
        }
        return ++next;
    }
    bool Complete(Scheduler& scheduler,id<MTLResource> source,id<MTLBuffer> bytes,
                  unsigned event,std::string detail) {
        const auto tick=scheduler.CurrentTick();
        const auto file=std::format("lineage_{}_{}_{}.raw",getpid(),epoch,event);
        if(!Log(std::format("{{\"op\":\"lineage_encoded\",\"event\":{},\"tick\":{},\"file\":\"{}\",\"detail\":{}}}",event,tick,file,detail)))return false;
        // The scheduler uses unretained command references. This closure owns both
        // resources through the exact tick; old textures are not kept as history.
        scheduler.DeferOperation([this,&scheduler,source,bytes,event,tick,file] {
          try {
            (void)source;bool complete=scheduler.IsFree(tick),written=false;
            if(complete&&bytes.contents){
                int fd=openat(directory,file.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
                if(fd>=0){size_t done=0;while(done<bytes.length){auto n=write(fd,static_cast<const char*>(bytes.contents)+done,bytes.length-done);if(n<=0)break;done+=n;}
                    written=done==bytes.length;written=(close(fd)==0)&&written;if(!written)unlinkat(directory,file.c_str(),0);}
            }
            Log(std::format("{{\"op\":\"lineage_saved\",\"event\":{},\"tick\":{},\"gpu_complete\":{},\"written\":{},\"bytes\":{},\"file\":\"{}\"}}",event,tick,complete,written,bytes.length,file));
          }catch(...){Fail();}
          --pending;
        });
        ++pending;
        return true;
    }
public:
    static BasicTextureLineage& Get()noexcept {
        alignas(BasicTextureLineage) static unsigned char storage[sizeof(BasicTextureLineage)]{};
        static auto* value=new(storage)BasicTextureLineage;return *value;
    }
    bool Armed()noexcept try{return Enabled();}catch(...){Fail();return false;}
    unsigned PendingCount()const{return pending.load();}
    void Gap(const char* reason,id<MTLTexture> texture)noexcept try {
        if(Enabled()&&skips++<16)Log(std::format("{{\"op\":\"lineage_gap\",\"reason\":\"{}\",\"texture\":{}}}",reason,Identity(texture)));
    }catch(...){Fail();}
    void Decision(const char* phase,id<MTLTexture> texture,const std::string& context)noexcept try {
        if(!Enabled()||!Candidate(texture))return;
        const auto event=Claim();if(event)Log(std::format("{{\"op\":\"lineage_decision\",\"event\":{},\"phase\":\"{}\",\"texture\":{},\"context\":{},\"metadata_only\":true}}",event,phase,Identity(texture),context));
    }catch(...){Fail();}
    // Match the evidenced resource class, not a model UID/address or all textures.
    static bool Candidate(id<MTLTexture> t) {
        return t&&t.textureType==MTLTextureType2D&&t.sampleCount==1&&t.arrayLength==1&&
            t.width==256&&t.height==256&&t.mipmapLevelCount==9&&
            (t.pixelFormat==MTLPixelFormatRGBA8Uint||t.pixelFormat==MTLPixelFormatRGBA8Unorm||t.pixelFormat==MTLPixelFormatRGBA8Unorm_sRGB);
    }
    static std::string Identity(id<MTLTexture> t)noexcept try {
        if(!t)return "null";
        auto identity=MemoryStats::ReadIdentity((__bridge const void*)t);
        auto content=MemoryStats::ReadContent((__bridge const void*)t);
        return std::format("{{\"native\":{},\"uid\":{},\"born_us\":{},\"guest\":{},\"generation\":{},\"format\":{},\"width\":{},\"height\":{},\"levels\":{},\"usage\":{},\"uploads\":{},\"clears\":{},\"copies\":{},\"draws\":{},\"storage_binds\":{},\"content_us\":{}}}",
            uintptr_t((__bridge void*)t),identity.uid,identity.born_us,identity.guest,identity.generation,uint64_t(t.pixelFormat),t.width,t.height,t.mipmapLevelCount,uint64_t(t.usage),content.counts[0],content.counts[1],content.counts[2],content.counts[3],content.counts[4],content.last_us);
    }catch(...){return "null";}
    static std::string Info(const VideoCore::Image& image)noexcept try {
        const auto& i=image.info;
        return std::format("{{\"image_uid\":{},\"guest\":{},\"bytes\":{},\"format\":{},\"width\":{},\"height\":{},\"levels\":{},\"layers\":{},\"pitch\":{},\"bits\":{},\"tile\":{},\"array\":{},\"bank\":{},\"flags\":{},\"mip0_pitch\":{},\"mip0_height\":{},\"mip0_offset\":{},\"cpu_hash\":{},\"mip0_hash\":{}}}",
            image.image_uid,i.guest_address,i.guest_size,u32(i.pixel_format),i.size.width,i.size.height,i.resources.levels,i.resources.layers,i.pitch,i.num_bits,u32(i.tile_mode),u32(i.array_mode),i.bank_swizzle,u32(image.flags),i.mips_layout[0].pitch,i.mips_layout[0].height,i.mips_layout[0].offset,image.hash,image.mip_hashes[0]);
    }catch(...){return "null";}
    void Texture(const char* phase,Scheduler& scheduler,id<MTLDevice> device,id<MTLTexture> texture,
                 id<MTLTexture> predecessor=nil,const std::string& context="null",bool force=false)noexcept try {
        if(!Enabled()||(!force&&!Candidate(texture)))return;
        unsigned block=0,block_bytes=0;
        if(!texture||texture.textureType!=MTLTextureType2D||texture.sampleCount!=1||texture.arrayLength!=1||!BasicTextureProbe<FC>::Format(texture.pixelFormat,block,block_bytes)){
            if(force)Gap("unsupported-source-snapshot",texture);return;
        }
        const unsigned event=Claim();if(!event)return;
        const auto width=std::min<NSUInteger>(8,texture.width),height=std::min<NSUInteger>(8,texture.height);
        const NSUInteger rows=(height+block-1)/block,patch=rows*256;
        id<MTLBuffer> data=[device newBufferWithLength:patch*3 options:MTLResourceStorageModeShared];if(!data){Gap("snapshot-allocation-failed",texture);return;}
        memset(data.contents,0,data.length);std::string regions="[";
        std::array<MTLOrigin,3> origins{};
        for(unsigned n=0;n<3;++n){
            NSUInteger x=n==0?0:n==1?(texture.width-width)/2:texture.width-width;
            NSUInteger y=n==0?0:n==1?(texture.height-height)/2:texture.height-height;
            x=x/block*block;y=y/block*block;
            origins[n]=MTLOriginMake(x,y,0);
            if(n)regions+=",";regions+=std::format("{{\"x\":{},\"y\":{},\"width\":{},\"height\":{},\"offset\":{},\"row\":256,\"rows\":{},\"block\":{},\"block_bytes\":{}}}",x,y,width,height,n*patch,rows,block,block_bytes);
        }
        regions+="]";
        scheduler.BlitEncoder("bounded texture lineage");auto encoder=scheduler.GetImpl().blit;
        // Own both resources through retirement BEFORE encoding an unretained
        // command. A failing metadata allocation cannot strand a GPU reference.
        if(!Complete(scheduler,texture,data,event,std::format("{{\"phase\":\"{}\",\"texture\":{},\"predecessor\":{},\"context\":{},\"regions\":{},\"partial\":true}}",phase,Identity(texture),Identity(predecessor),context,regions)))return;
        for(unsigned n=0;n<3;++n)[encoder copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:origins[n]
            sourceSize:MTLSizeMake(width,height,1) toBuffer:data destinationOffset:n*patch destinationBytesPerRow:256 destinationBytesPerImage:patch];
    }catch(...){Fail();}
    void Buffer(const char* phase,Scheduler& scheduler,id<MTLDevice> device,id<MTLBuffer> source,
                uint64_t offset,id<MTLTexture> texture,const std::string& context)noexcept try {
        if(!Enabled()||!Candidate(texture))return;
        if(!source||offset>source.length||source.length-offset<128){Gap("upload-buffer-range-unavailable",texture);return;}
        const unsigned event=Claim();if(!event)return;
        id<MTLBuffer> data=[device newBufferWithLength:256 options:MTLResourceStorageModeShared];if(!data){Gap("snapshot-allocation-failed",texture);return;}
        memset(data.contents,0,data.length);scheduler.BlitEncoder("bounded lineage upload input");
        if(!Complete(scheduler,source,data,event,std::format("{{\"phase\":\"{}\",\"texture\":{},\"buffer\":{},\"offset\":{},\"logical_bytes\":128,\"context\":{},\"partial\":true}}",phase,Identity(texture),uintptr_t((__bridge void*)source),offset,context)))return;
        [scheduler.GetImpl().blit copyFromBuffer:source sourceOffset:offset toBuffer:data destinationOffset:0 size:128];
    }catch(...){Fail();}
};
using TextureLineage=BasicTextureLineage<>;
}
