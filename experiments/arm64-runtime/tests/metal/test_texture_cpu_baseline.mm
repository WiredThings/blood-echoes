// SPDX-License-Identifier: GPL-2.0-or-later
// A sampled image may become GPU-owned AFTER its first CPU upload or expansion.
// Use authored bytes and the real cache, never game assets or production state.
#define main previous_expansion_main
#include "test_texture_expansion.mm"
#undef main

static bool MipConstant(Fixture& f, Image& image, u32 level, unsigned char value) {
    f.runtime.Finish();
    const NSUInteger w = std::max(image.info.size.width >> level, 1u);
    const NSUInteger h = std::max(image.info.size.height >> level, 1u);
    const NSUInteger row = (w * 4 + 255) / 256 * 256;
    id<MTLCommandQueue> queue = [f.device.GetImpl().device newCommandQueue];
    id<MTLBuffer> bytes = [f.device.GetImpl().device newBufferWithLength:row * h
                                                     options:MTLResourceStorageModeShared];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLBlitCommandEncoder> encoder = [command blitCommandEncoder];
    [encoder copyFromTexture:(__bridge id<MTLTexture>)image.backing->image.native
        sourceSlice:0 sourceLevel:level sourceOrigin:MTLOriginMake(0, 0, 0)
        sourceSize:MTLSizeMake(w, h, 1) toBuffer:bytes destinationOffset:0
        destinationBytesPerRow:row destinationBytesPerImage:row * h];
    [encoder endEncoding]; [command commit]; [command waitUntilCompleted];
    assert(command.status == MTLCommandBufferStatusCompleted);
    const auto* data = static_cast<const unsigned char*>(bytes.contents);
    for (NSUInteger y = 0; y < h; ++y)
        for (NSUInteger x = 0; x < w * 4; ++x)
            if (data[y * row + x] != value) return false;
    return true;
}

int main(int argc, char** argv) {
    assert(argc == 3);
    const std::string_view kind = argv[2];
    const bool expanded = kind.starts_with("expanded-");
    const bool changed = kind == "plain-changed" || kind == "expanded-changed";
    const bool tail_changed = kind == "expanded-tail-changed";
    const bool gpu_dirty = kind.ends_with("gpu-dirty");
    assert(kind == "plain-unchanged" || kind == "plain-changed" ||
           kind == "plain-gpu-dirty" || kind == "expanded-unchanged" ||
           kind == "expanded-changed" || kind == "expanded-gpu-dirty" || tail_changed);
    setenv("BB_GUEST_IN_PLACE", "0", 1);
    void* backing = MAP_FAILED;
    for (uintptr_t a = 0x800000000ull; a < 0x10000000000ull; a += 0x100000000ull) {
        backing = mmap((void*)a, 0x800000, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANON | MAP_FIXED_NOREPLACE, -1, 0);
        if (backing != MAP_FAILED) break;
    }
    assert(backing != MAP_FAILED);
    test_base = (uintptr_t)backing;
    bool pass = false;
    @autoreleasepool {
        Fixture f;
        auto desc = Desc(test_base + 0x100000, 64, AmdGpu::ImageType::Color2D, 64);
        memset((void*)desc.info.guest_address, 0x11, 32768);
        auto id = f.textures.FindImage(desc);
        (void)f.textures.FindTexture(id, desc);
        f.runtime.Finish();
        auto& initial = f.textures.GetImage(id);
        assert(False(initial.flags & ImageFlagBits::GpuModified));
        assert(Pixels(f, initial, 0, 0x11));
        const auto first_hash = initial.mip_hashes[0];
        vk::ClearValue red{};
        red.color.float32[0] = 1;
        red.color.float32[3] = 1;
        f.runtime.ClearImage(&initial, {.base = {0, 0}, .extent = {1, 1}}, red);
        f.runtime.Finish();
        assert(True(initial.flags & ImageFlagBits::GpuModified));
        assert(Pixels(f, initial, 0, 0, true));
        if (expanded) {
            // Match the observed CPU refresh -> GPU copy -> later CPU dirty path.
            desc.info.resources.levels = 2;
            desc.view_info.range.extent.levels = 2;
            desc.info.UpdateSize();
            const auto next = f.textures.FindImage(desc);
            assert(next != id);
            id = next;
            (void)f.textures.FindTexture(id, desc);
            f.runtime.Finish();
            assert(Pixels(f, f.textures.GetImage(id), 0, 0, true));
        }
        auto& image = f.textures.GetImage(id);
        const auto before_hash = image.mip_hashes[0];
        if (changed) {
            memset((void*)desc.info.guest_address, 0x77, desc.info.guest_size);
            f.buffers.InvalidateMemory(desc.info.guest_address, desc.info.guest_size);
        }
        if (tail_changed) {
            const auto& tail = image.info.mips_layout[1];
            memset((void*)(desc.info.guest_address + tail.offset), 0x77, tail.size);
            f.buffers.InvalidateMemory(desc.info.guest_address + tail.offset, tail.size);
        }
        f.textures.InvalidateMemory(desc.info.guest_address, desc.info.guest_size);
        assert(True(image.flags & ImageFlagBits::CpuDirty));
        if (gpu_dirty) image.flags |= ImageFlagBits::GpuDirty;
        (void)f.textures.FindTexture(id, desc);
        f.runtime.Finish();
        pass = Pixels(f, image, 0, changed ? 0x77 : 0x11, !changed && !gpu_dirty);
        if (tail_changed) pass &= MipConstant(f, image, 1, 0x77);
        printf("CPU_BASELINE kind=%s first=%#llx before=%#llx after=%#llx pixels=%s\n",
               argv[2], (unsigned long long)first_hash, (unsigned long long)before_hash,
               (unsigned long long)image.mip_hashes[0], pass ? "PASS" : "FAIL");
    }
    munmap(backing, 0x800000);
    return pass ? 0 : 7;
}
