// SPDX-License-Identifier: GPL-2.0-or-later
// Real TextureCache::FindImage and Metal image views, without game data or disk caches.
#import <Metal/Metal.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string_view>
#include <sys/mman.h>
#include <cstring>
#include <chrono>
#include <thread>
#include "bbport_resource_lifetime.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/page_manager.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_scheduler.h"
#include "video_core/texture_cache/texture_cache.h"
#include "test_mapping_rasterizer.h"

// Only this test has synthetic guest backing, mapped below. All other runtime services
// retain the shared tests' fail-fast stubs; no real guest VMAs or game files are involved.
#define runtime_memory_clamp unused_memory_clamp
#define runtime_memory_read_backing unused_memory_read_backing
#define runtime_memory_gpu_protect unused_memory_gpu_protect
#define runtime_memory_set_write_watch unused_memory_set_write_watch
#define runtime_memory_region unused_memory_region
#include "test_gpu_runtime_stubs.cpp"
#undef runtime_memory_clamp
#undef runtime_memory_read_backing
#undef runtime_memory_gpu_protect
#undef runtime_memory_set_write_watch
#undef runtime_memory_region
static uintptr_t test_base;
// Controlled mapping model for the cache/Metal integration. The separate real-runtime
// mapping-retirement test proves physical alias exclusion and concurrent map serialization.
static bool test_mapping_alias;
static unsigned test_mapping_callbacks;
extern "C" int runtime_memory_reclaim_if_unaliased_v2(uintptr_t a,uint64_t n,int(*callback)(void*),void* context) {
    assert(a>=test_base && a+n<=test_base+0x800000);
    if(test_mapping_alias)return 0;
    ++test_mapping_callbacks;
    return callback(context);
}

extern "C" {
int runtime_memory_region(uintptr_t address, uintptr_t* start, uintptr_t* end, int* mapped) {
    assert(address>=test_base && address<test_base+0x800000);
    *start=test_base;*end=test_base+0x800000;*mapped=1;return 1;
}
uint64_t runtime_memory_clamp(uintptr_t address, uint64_t size) {
    assert(address>=test_base && address+size<=test_base+0x800000); return size;
}
void runtime_memory_read_backing(uintptr_t address, void* out, uint64_t size) {
    runtime_memory_clamp(address,size);std::memcpy(out,(void*)address,size);
}
void runtime_memory_gpu_protect(uintptr_t address,uint64_t size,int,int) {
    runtime_memory_clamp(address,size);
}
void runtime_memory_set_write_watch(uintptr_t address,uint64_t size,int) {
    runtime_memory_clamp(address,size);
}
}

using namespace VideoCore;
struct Fixture {
    Metal::Device device;
    Metal::Scheduler scheduler{device, "texture-cache-view-test"};
    Metal::Runtime runtime{device, scheduler};
    MappingOnlyRasterizer mappings;
    PageManager tracker{&mappings};
    BufferCache buffers{runtime, nullptr, textures, tracker};
    TextureCache textures{runtime, nullptr, buffers, tracker};
    ~Fixture() { runtime.Finish(); }
};

static TextureCache::ImageDesc Desc(u64 address, u32 width, AmdGpu::ImageType type, u32 height=1) {
    TextureCache::ImageDesc desc;
    auto& info=desc.info;
    info.guest_address=address;
    info.pixel_format=vk::Format::eR8G8B8A8Unorm;
    info.type=type;
    info.size={width,height,1};
    info.resources={1,1};
    info.pitch=width;
    info.num_bits=32;
    info.UpdateSize();
    desc.view_info.type=type;
    desc.view_info.range.extent={1,1};
    return desc;
}

// Use the actual cache, page tracking, memo, view slots and deferred destruction.
// No game files, launcher preferences or disk caches participate in this test.
static void TestReleasedStorageCache(Fixture& f, bool enabled) {
    namespace Life=BbResourceLifetime;
    auto& lifetime=Life::Get();
    auto desc=Desc(test_base+0x100000,64,AmdGpu::ImageType::Color2D,64);
    desc.type=TextureCache::BindingType::Storage;
    desc.view_info.is_storage=true;
    const auto size=desc.info.guest_size;
    std::memset((void*)desc.info.guest_address,0x5a,size);
    const auto allocation=lifetime.Allocate(Life::Domain::Heap,desc.info.guest_address,size+64);
    assert(allocation);
    const auto image_id=f.textures.FindImage(desc);
    TextureCache::ViewMemo memo;
    const auto& view=f.textures.FindTexture(image_id,desc,&memo);
    auto* image=f.textures.TryGetImage(image_id,0);
    assert(image);
    image->usage.storage=1; // RasterizerCore records this role when binding a writable T#.
    assert(image && image->IsTracked() && image->usage.storage);
    const auto uid=image->image_uid;
    const auto generation=f.textures.RegistryGeneration();
    __weak id<MTLTexture> weak_base=(__bridge id<MTLTexture>)image->backing->image.native;
    __attribute__((objc_precise_lifetime)) id<MTLTexture> held_view=(__bridge id<MTLTexture>)view.native;
    vk::ClearValue clear{};clear.color.float32[0]=1;clear.color.float32[3]=1;
    f.runtime.ClearImage(image,{.base={0,0},.extent={1,1}},clear);
    image->tick_accessed_last=f.runtime.CurrentTick();
    image->gpu_write_tick=f.runtime.CurrentTick();
    assert(lifetime.Release(allocation));
    Life::SetReclaimReady(true);
    const auto unchanged_cpu=[&] {
        const auto* bytes=(const unsigned char*)desc.info.guest_address;
        for (u64 i=0;i<size;++i) assert(bytes[i]==0x5a);
    };
    const auto verify_pixels=[&](void* native, bool red) {
        @autoreleasepool {
            id<MTLDevice> device=f.device.GetImpl().device;
            id<MTLCommandQueue> queue=[device newCommandQueue];
            id<MTLBuffer> bytes=[device newBufferWithLength:64*64*4 options:MTLResourceStorageModeShared];
            id<MTLCommandBuffer> command=[queue commandBuffer];
            id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
            [blit copyFromTexture:(__bridge id<MTLTexture>)native sourceSlice:0 sourceLevel:0
                sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(64,64,1)
                toBuffer:bytes destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:64*256];
            [blit endEncoding];[command commit];[command waitUntilCompleted];
            assert(command.status==MTLCommandBufferStatusCompleted);
            const auto* pixels=(const unsigned char*)bytes.contents;
            for (unsigned i=0;i<64*64*4;++i)
                assert(pixels[i]==(red ? (i%4==0 || i%4==3 ? 255 : 0) : 0x33));
        }
    };
    const auto collect_for=[&](int milliseconds) {
        const auto until=std::chrono::steady_clock::now()+std::chrono::milliseconds(milliseconds);
        do {
            f.textures.RunGarbageCollector();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        } while(std::chrono::steady_clock::now()<until);
    };
    const auto drain_deferred=[&] {
        // Cache slot deletion can itself queue native view/backing destruction.
        // This test-only wait also lets completed handlers retire the Metal buffers.
        for (unsigned i=0;i<3;++i) {
            f.runtime.Finish();
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            f.runtime.RefreshTicks();f.runtime.PopPendingOperations();
        }
    };
    // An old free and cold LRU cannot authorize deletion while the write tick is open.
    collect_for(3300);
    assert(f.textures.TryGetImage(image_id,uid)==image);
    assert(f.textures.RegistryGeneration()==generation && weak_base!=nil);
    unchanged_cpu();
    // Even after GPU completion a partial live allocation in the other domain blocks it.
    const auto overlap=lifetime.Allocate(Life::Domain::Range,desc.info.guest_address+128,32);
    assert(overlap);
    f.runtime.Finish();f.runtime.RefreshTicks();
    assert(image->CanReleaseFreedStorageBacking());
    verify_pixels(image->backing->image.native,true);
    collect_for(1200);
    assert(f.textures.TryGetImage(image_id,uid)==image && image->IsTracked());
    assert(f.textures.RegistryGeneration()==generation);
    unchanged_cpu();
    assert(lifetime.Release(overlap));
    collect_for(2300);
    unchanged_cpu();
    if(enabled && test_mapping_alias) {
        assert(f.textures.TryGetImage(image_id,uid)==image && image->IsTracked());
        assert(f.textures.RegistryGeneration()==generation && test_mapping_callbacks==0);
        verify_pixels(image->backing->image.native,true);
        test_mapping_alias=false; // only the controlled guard changes, not the heap proof
        collect_for(1200);
        unchanged_cpu();
        std::puts("Released storage: denied mapping lease retains the exact GPU contents and registry; fresh proof required");
    }
    if (!enabled) {
        assert(f.textures.TryGetImage(image_id,uid)==image && image->IsTracked());
        assert(f.textures.RegistryGeneration()==generation);
        assert(lifetime.Read().reclaimed_images==0);
        held_view=nil;Life::SetReclaimReady(false);
        std::puts("Released storage OFF: actual cache retains the GPU-written resource");
        return;
    }
    assert(f.textures.RegistryGeneration()>generation);
    // Slot destruction is deferred; the registry generation invalidates caller memos now.
    if (auto* pending=f.textures.TryGetImage(image_id,uid)) {
        assert(!pending->IsTracked() && False(pending->flags&ImageFlagBits::Registered));
    }
    memo={};
    drain_deferred();
    assert(f.textures.TryGetImage(image_id,uid)==nullptr);
    // FindTexture queued this linear storage image for readback. FreeImage must
    // remove that pending ID, so draining the queue cannot write into freed payloads.
    f.textures.ProcessDownloadImages();
    unchanged_cpu();
    assert(lifetime.Read().reclaimed_images==1 && weak_base!=nil);
    held_view=nil;
    drain_deferred();
    assert(weak_base==nil);
    unchanged_cpu();
    // The exact address may now be reused. Neither the old image slot nor its view memo
    // may return the destroyed backing; unchanged descriptors still hit the new cache entry.
    const auto replacement=lifetime.Allocate(Life::Domain::Heap,desc.info.guest_address,size+64);
    assert(replacement && replacement!=allocation);
    std::memset((void*)desc.info.guest_address,0x33,size);
    const auto new_id=f.textures.FindImage(desc);
    auto& new_view=f.textures.FindTexture(new_id,desc,&memo);
    auto* new_image=f.textures.TryGetImage(new_id,0);
    assert(new_image && new_image->image_uid!=uid && new_image->IsTracked());
    assert(new_view.native && f.textures.FindImage(desc)==new_id);
    f.runtime.Finish();f.runtime.RefreshTicks();
    verify_pixels(new_image->backing->image.native,false);
    assert(lifetime.Release(replacement));
    Life::SetReclaimReady(false);
    std::puts("Released storage ON: cache untracks, unregisters and destroys native views/backing; address reuse gets a fresh UID; no writeback");
}

int main(int argc, char** argv) {
    const bool baseline=argc>1 && std::string_view(argv[1])=="baseline";
    const bool abort_view=argc>1 && std::string_view(argv[1])=="abort-view";
    setenv("BB_GUEST_IN_PLACE","0",1);
    const bool alias_case=argc>1 && std::string_view(argv[1])=="released-storage-alias";
    test_mapping_alias=alias_case;
    const bool released_on=alias_case || (argc>1 && std::string_view(argv[1])=="released-storage");
    const bool released_off=argc>1 && std::string_view(argv[1])=="released-storage-off";
    if (released_on || released_off) {
        setenv("BB_GPU_LIFETIME","1",1);
        setenv("BB_TEXTURE_RELEASED_STORAGE",released_on ? "1" : "0",1);
        setenv("BB_READBACK_LINEAR","1",1);
    }
    void* backing=MAP_FAILED;
    for (uintptr_t address=0x800000000ull;address<0x10000000000ull;address+=0x100000000ull) {
        backing=mmap((void*)address,0x800000,PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);
        if (backing!=MAP_FAILED) {test_base=(uintptr_t)backing;break;}
    }
    assert(backing!=MAP_FAILED);
    if (released_on || released_off) {
        @autoreleasepool {
            Fixture f;
            TestReleasedStorageCache(f,released_on);
        }
        munmap(backing,0x800000);
        return 0;
    }
    @autoreleasepool {
        Fixture f;
        if (argc>1 && std::string_view(argv[1])=="invalid-view") {
            auto desc=Desc(test_base,1,AmdGpu::ImageType::Color2D);
            auto image_id=f.textures.FindImage(desc);
            desc.view_info.type=AmdGpu::ImageType::Color3D;
            f.textures.GetImage(image_id).FindView(desc.view_info);
            std::abort(); // the strict guard must reject before the Metal call
        }
        for (u32 width : {1u,64u}) {
            auto two=Desc(test_base+width*0x10000,width,AmdGpu::ImageType::Color2D);
            const auto two_id=f.textures.FindImage(two);
            auto one=Desc(two.info.guest_address,width,AmdGpu::ImageType::Color1D);
            const auto one_id=f.textures.FindImage(one);
            const auto& chosen=f.textures.GetImage(one_id);
            const bool compatible=IsViewTypeCompatible(one.view_info.type,chosen.info.type);
            std::printf("width=%u selected=%u requested=%u compatible=%d same-id=%d\n",
                        width,u32(chosen.info.type),u32(one.info.type),compatible,one_id==two_id);
            std::fflush(stdout);
            if (baseline) { assert(!compatible && one_id==two_id); continue; }
            if (!abort_view) assert(compatible && one_id==two_id);
            auto& view=f.textures.GetImage(one_id).FindView(one.view_info);
            auto native=(__bridge id<MTLTexture>)view.native;
            assert(native.textureType==MTLTextureType2D && native.width==width);
            const auto repeated=f.textures.FindImage(one);
            assert(repeated==one_id);
            // Both logical types use the same normalized 2D backing. Repeated unchanged descriptors remain ordinary cache hits.
            const auto again=f.textures.FindImage(two);
            assert(again==one_id);
            assert(IsViewTypeCompatible(two.view_info.type,f.textures.GetImage(again).info.type));
            assert(f.textures.FindImage(two)==again);
            f.runtime.Finish();
        }
        if (!baseline && !abort_view) {
            // Normalized 1D/2D aliasing must not broaden to native 3D backing.
            auto three=Desc(test_base+0x450000,8,AmdGpu::ImageType::Color3D);
            const auto three_id=f.textures.FindImage(three);
            auto flat=Desc(three.info.guest_address,8,AmdGpu::ImageType::Color1D);
            assert(!IsViewTypeCompatible(flat.view_info.type,three.info.type));
            const auto flat_id=f.textures.FindImage(flat);assert(flat_id!=three_id);
            auto flat_native=(__bridge id<MTLTexture>)f.textures.GetImage(flat_id).FindView(flat.view_info).native;
            assert(flat_native.textureType==MTLTextureType2D);
            f.runtime.Finish();
            // The image stores the base dimension; the view carries arrayness.
            auto array_one=Desc(test_base+0x480000,8,AmdGpu::ImageType::Color1D);
            array_one.info.resources.layers=2;array_one.info.UpdateSize();
            array_one.view_info.type=AmdGpu::ImageType::Color1DArray;
            array_one.view_info.range.extent.layers=2;
            const auto array_id=f.textures.FindImage(array_one);
            auto& array_view=f.textures.GetImage(array_id).FindView(array_one.view_info);
            auto array_native=(__bridge id<MTLTexture>)array_view.native;
            assert(array_native.textureType==MTLTextureType2DArray && array_native.arrayLength==2);
            auto array_two=array_one;array_two.info.type=AmdGpu::ImageType::Color2D;
            array_two.view_info.type=AmdGpu::ImageType::Color2DArray;
            assert(f.textures.FindImage(array_two)==array_id);
            f.runtime.Finish();
            std::puts("1D-array and 2D-array views share their two-layer normalized backing");
            // A changed descriptor must not bypass layout checks through the memo.
            auto memo_two=Desc(test_base+0x500000,64,AmdGpu::ImageType::Color2D);
            const auto memo_two_id=f.textures.FindImage(memo_two);
            auto memo_one=Desc(memo_two.info.guest_address,64,AmdGpu::ImageType::Color1D);
            assert(f.textures.FindImage(memo_one)==memo_two_id);
            assert(f.textures.FindImage(memo_one)==memo_two_id);
            memo_one.info.bank_swizzle=1;
            assert(f.textures.FindImage(memo_one)!=memo_two_id);
            assert(f.textures.FindImage(memo_two)==memo_two_id);
            f.runtime.Finish();
            std::puts("Changed-layout descriptor rejected by the memoized dimensional-alias path");
            // Live death/respawn regression: same address, incompatible dimensions.
            // Normalizing native texture type does not make layouts interchangeable.
            auto large=Desc(test_base+0x600000,38,AmdGpu::ImageType::Color2D,14);
            const auto large_id=f.textures.FindImage(large);
            auto small=Desc(large.info.guest_address,1,AmdGpu::ImageType::Color1D);
            const auto small_id=f.textures.FindImage(small);
            assert(large_id!=small_id);
            auto& small_view=f.textures.GetImage(small_id).FindView(small.view_info);
            auto small_native=(__bridge id<MTLTexture>)small_view.native;
            assert(small_native.textureType==MTLTextureType2D && small_native.width==1 && small_native.height==1);
            assert(f.textures.GetImage(large_id).info.size==large.info.size);
            f.runtime.Finish();
            std::puts("Different-layout same-address alias rejected; each backing preserves its dimensions");
            auto desc=Desc(test_base+0x700000,64,AmdGpu::ImageType::Color2D);
            auto image_id=f.textures.FindImage(desc);
            const auto original_image_id=image_id;
            auto& source=f.textures.GetImage(image_id);
            id<MTLDevice> device=f.device.GetImpl().device;
            id<MTLCommandQueue> queue=[device newCommandQueue];
            id<MTLBuffer> bytes=[device newBufferWithLength:256 options:MTLResourceStorageModeShared];
            for (unsigned i=0;i<256;++i) ((u8*)bytes.contents)[i]=u8(i^0x5a);
            id<MTLCommandBuffer> upload=[queue commandBuffer];
            id<MTLBlitCommandEncoder> blit=[upload blitCommandEncoder];
            [blit copyFromBuffer:bytes sourceOffset:0 sourceBytesPerRow:256
                sourceBytesPerImage:256 sourceSize:MTLSizeMake(64,1,1)
                toTexture:(__bridge id<MTLTexture>)source.backing->image.native
                destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
            [blit endEncoding];[upload commit];[upload waitUntilCompleted];
            assert(upload.status==MTLCommandBufferStatusCompleted);
            source.flags=(source.flags & ImageFlagBits::Registered) | ImageFlagBits::GpuModified;
            const auto count=BbStats::live_images.load();
            for (unsigned iteration=0;iteration<32;++iteration) {
                desc.info.type=desc.view_info.type=iteration%2==0 ? AmdGpu::ImageType::Color1D
                                                                 : AmdGpu::ImageType::Color2D;
                image_id=f.textures.FindImage(desc);
                assert(image_id==original_image_id);
                auto& image=f.textures.GetImage(image_id);
                auto& view=image.FindView(desc.view_info);
                assert(IsViewTypeCompatible(desc.view_info.type,image.info.type));
                assert(f.textures.FindImage(desc)==image_id);
                f.runtime.Finish();
                std::memset(bytes.contents,0,256);
                id<MTLCommandBuffer> download=[queue commandBuffer];
                blit=[download blitCommandEncoder];
                [blit copyFromTexture:(__bridge id<MTLTexture>)view.native sourceSlice:0 sourceLevel:0
                    sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(64,1,1)
                    toBuffer:bytes destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
                [blit endEncoding];[download commit];[download waitUntilCompleted];
                assert(download.status==MTLCommandBufferStatusCompleted);
                for (unsigned i=0;i<256;++i) assert(((u8*)bytes.contents)[i]==u8(i^0x5a));
                assert(BbStats::live_images.load()==count);
            }
            std::puts("32 normalized dimensional aliases preserved GPU-only bytes; backing and live image count unchanged");
        }
    }
    munmap(backing,0x800000);
    std::puts(baseline ? "Both old invalid selection paths reproduced" : "Texture cache dimensional views passed");
}
