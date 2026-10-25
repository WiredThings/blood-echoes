// SPDX-License-Identifier: GPL-2.0-or-later
// Synthetic GPU ordering and actual TextureCache invalidation controls. No game assets.
#define BB_RAW_ROUNDTRIP_MAIN raw_roundtrip_control_main
#include "gpu_raw_roundtrip.mm"
#undef BB_RAW_ROUNDTRIP_MAIN

static bool IsGpuImageDirty(const Image& image) {
    return True(image.flags & ImageFlagBits::GpuDirty);
}

static int Invalidation(Fixture& f, std::string_view kind) {
    auto desc = Make(test_base + 0x100000, 13, 64, 64, 1, 1);
    memset(reinterpret_cast<void*>(desc.info.guest_address), 0x11, desc.info.guest_size);
    const auto first = f.textures.FindImage(desc);
    (void)f.textures.FindTexture(first, desc);
    GenerateGpu(f, f.textures.GetImage(first));
    assert(f.textures.GetImage(first).SafeToDownload());
    ImageId second{};
    if (kind == "invalidate-two-images") {
        auto other = Make(desc.info.guest_address + 0x10000, 13, 64, 64, 1, 1);
        memset(reinterpret_cast<void*>(other.info.guest_address), 0x11, other.info.guest_size);
        second = f.textures.FindImage(other);
        (void)f.textures.FindTexture(second, other);
        GenerateGpu(f, f.textures.GetImage(second));
    }
    if(kind == "invalidate-many") {
        for(unsigned n=1;n<10;++n) {
            auto other=Make(desc.info.guest_address+n*0x10000,13,64,64,1,1);
            memset(reinterpret_cast<void*>(other.info.guest_address),0x11,other.info.guest_size);
            auto id=f.textures.FindImage(other);(void)f.textures.FindTexture(id,other);
        }
    }
    const auto base = desc.info.guest_address;
    const auto bytes = desc.info.guest_size;
    VAddr start = base;
    size_t size = 256;
    bool expected = true;
    if (kind == "invalidate-inside" || kind == "invalidate-repeat") start += 512;
    else if (kind == "invalidate-before") { start -= 64; size = 128; }
    else if (kind == "invalidate-zero") { size = 0; expected = false; }
    else if (kind == "invalidate-outside") { start += bytes; expected = false; }
    else if (kind == "invalidate-two-images") size = 0x10000 + bytes;
    else if(kind == "invalidate-many") size = 10*0x10000;
    else assert(kind == "invalidate-base");
    // This is the real method called by shader and DMA write binding. It runs
    // before the GPU write; these cases check its metadata, not buffer tracking.
    for(unsigned n=0; n<(kind=="invalidate-repeat"?1000u:1u); ++n)
        f.textures.InvalidateMemoryFromGPU(start, size);
    assert(False(f.textures.GetImage(first).flags & ImageFlagBits::Picked));
    const bool actual = IsGpuImageDirty(f.textures.GetImage(first));
    const bool second_actual = second && IsGpuImageDirty(f.textures.GetImage(second));
    const bool correct = actual == expected && (!second || second_actual);
    printf("INVALIDATION case=%.*s expected_first=%d actual_first=%d second_present=%d actual_second=%d metadata_only=1 result=%s\n",
           int(kind.size()), kind.data(), expected, actual, bool(second), second_actual,
           correct ? "PASS" : "FAIL");
    return correct ? 0 : 7;
}

static int OrderedRaw(Fixture& f, std::string_view kind) {
    const bool multi = kind == "ordered-mips-layers";
    const bool padded = kind == "ordered-padding" || kind == "retile-after-padding";
    const unsigned width = 64, height = padded ? 60 : 64;
    const unsigned levels = multi ? 3 : 1, layers = multi ? 2 : 1;
    auto src = Make(test_base + 0x100000, 13, width, height, levels, layers);
    auto dst = Make(test_base + 0x300000, kind == "ordered-same-layout" ? 13 : 8,
                    width, height, levels, layers, false, false);
    const size_t span = std::max(src.info.guest_size, dst.info.guest_size);
    memset(reinterpret_cast<void*>(src.info.guest_address), 0x11, span);
    memset(reinterpret_cast<void*>(dst.info.guest_address), 0x22, span);
    const auto sid = f.textures.FindImage(src);
    (void)f.textures.FindTexture(sid, src);
    const auto did = f.textures.FindImage(dst);
    (void)f.textures.FindTexture(did, dst);
    auto& source = f.textures.GetImage(sid);
    auto& target = f.textures.GetImage(did);
    GenerateGpu(f, source);
    std::vector<uint8_t> expected(span + Guard, 0x11);
    std::fill(expected.begin() + span, expected.end(), 0xcd);
    std::vector<bool> coverage(span, false);
    for (unsigned m = 0; m < levels; ++m)
        for (unsigned l = 0; l < layers; ++l)
            for (unsigned y = 0; y < std::max(height >> m, 1u); ++y)
                for (unsigned x = 0; x < std::max(width >> m, 1u); ++x) {
                    const auto at = RawOffset(src.info, m, l, x, y);
                    const auto word = Word(x, y, m, l);
                    assert(at + 4 <= span);
                    memcpy(expected.data() + at, &word, 4);
                    for (unsigned b = 0; b < 4; ++b) {
                        assert(!coverage[at + b]); coverage[at + b] = true;
                    }
                }
    size_t patch = 512, patch_bytes = 256;
    if (padded) {
        patch = 0;
        while (patch + 4 <= dst.info.guest_size &&
               (coverage[patch] || coverage[patch+1] || coverage[patch+2] || coverage[patch+3]))
            patch += 4;
        assert(patch + 4 <= dst.info.guest_size);
        patch_bytes = 4;
    }
    const bool no_patch = kind == "image-only" || kind == "stale-cpu" || kind == "pixel-copy";
    const bool raw_only = kind == "raw-only-refresh" || kind == "stale-cpu";
    const bool reverse = kind == "late-retile" || kind == "retile-after-padding";
    const bool twice = kind == "ordered-two-writes";
    auto raw = f.runtime.StagingRequest(span + Guard, MemoryType::HostCached, 256);
    f.runtime.FillBuffer(raw.buffer, raw.offset, span, 0x11111111);
    f.runtime.FillBuffer(raw.buffer, raw.offset + span, Guard, 0xcdcdcdcd);
    const auto initial_tick = f.runtime.CurrentTick();
    auto export_image = [&] {
        auto copies = Copies(src.info);
        f.textures.GetTileManager().TileImage(source, copies, raw.buffer, raw.offset);
    };
    auto patch_raw = [&] {
        constexpr u32 word = 0x0f0e0d0c;
        f.runtime.FillBuffer(raw.buffer, raw.offset + patch, patch_bytes, word);
        for (size_t at = patch; at < patch + patch_bytes; at += 4)
            memcpy(expected.data() + at, &word, 4);
        if (twice) {
            constexpr u32 last = 0x28272625;
            f.runtime.FillBuffer(raw.buffer, raw.offset + patch + 64, 128, last);
            for (size_t at = patch + 64; at < patch + 192; at += 4)
                memcpy(expected.data() + at, &last, 4);
        }
    };
    if (reverse) { patch_raw(); export_image(); }
    else {
        if (!raw_only) export_image();
        if (!no_patch) patch_raw();
    }
    if (kind == "pixel-copy") f.runtime.CopyImage(&source, &target);
    else {
        const auto [linear, offset] = f.textures.GetTileManager().DetileImage(raw.buffer, raw.offset, dst.info);
        auto copies = Copies(dst.info, offset);
        f.runtime.UploadImage(&target, linear, copies);
    }
    const auto last_tick_before_finish = f.runtime.CurrentTick();
    // No explicit completion or CPU readback separates export, raw writes and
    // detile/upload. Finish/readback is only the final independent observation.
    f.runtime.Finish(); raw.Invalidate();
    size_t raw_wrong = 0, patch_wrong = 0, old_image_wrong = 0, guard_wrong = 0, pixels_wrong = 0;
    for (size_t i = 0; i < span; ++i) if (raw.mapped[i] != expected[i]) {
        ++raw_wrong;
        if (!no_patch && i >= patch && i < patch + patch_bytes) ++patch_wrong;
        else if (coverage[i]) ++old_image_wrong;
    }
    for (size_t i = span; i < span + Guard; ++i) guard_wrong += raw.mapped[i] != 0xcd;
    for (unsigned m = 0; m < levels; ++m)
        for (unsigned l = 0; l < layers; ++l) {
            const auto pixels = ReadPixels(f, target, m, l);
            const auto w = std::max(width >> m, 1u), h = std::max(height >> m, 1u);
            for (unsigned y = 0; y < h; ++y)
                for (unsigned x = 0; x < w; ++x) {
                    const auto at = RawOffset(dst.info, m, l, x, y);
                    assert(at + 4 <= span);
                    for (unsigned b = 0; b < 4; ++b)
                        pixels_wrong += pixels[(y*w+x)*4+b] != expected[at+b];
                }
        }
    for (size_t i = 0; i < span; ++i) {
        assert(reinterpret_cast<uint8_t*>(src.info.guest_address)[i] == 0x11);
        assert(reinterpret_cast<uint8_t*>(dst.info.guest_address)[i] == 0x22);
    }
    assert(initial_tick == last_tick_before_finish);
    const bool pass = !raw_wrong && !guard_wrong && !pixels_wrong;
    printf("AUTHORITY case=%.*s raw_wrong=%zu patch_wrong=%zu old_image_wrong=%zu guard_wrong=%zu pixel_wrong=%zu same_submission=1 cpu_source_stale=1 result=%s\n",
           int(kind.size()), kind.data(), raw_wrong, patch_wrong, old_image_wrong,
           guard_wrong, pixels_wrong, pass ? "PASS" : "FAIL");
    return pass ? 0 : 7;
}

int main(int argc, char** argv) {
    assert(argc == 2); const std::string_view kind = argv[1];
    setenv("BB_GUEST_IN_PLACE", "0", 1);
    void* map = MAP_FAILED;
    for (uintptr_t a=0x800000000ull; a<0x10000000000ull; a+=0x100000000ull) {
        map=mmap(reinterpret_cast<void*>(a),0x800000,PROT_READ|PROT_WRITE,
                 MAP_PRIVATE|MAP_ANON|MAP_FIXED_NOREPLACE,-1,0);
        if(map!=MAP_FAILED) break;
    }
    assert(map!=MAP_FAILED); test_base=reinterpret_cast<uintptr_t>(map);
    int result;
    @autoreleasepool {
        Fixture f;
        result = kind.starts_with("invalidate-") ? Invalidation(f,kind) : OrderedRaw(f,kind);
    }
    assert(munmap(map,0x800000)==0); return result;
}
