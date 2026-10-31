// SPDX-License-Identifier: GPL-2.0-or-later
// The shared part of the reduced scene targets (video_core/scene_targets_core.h) against a backend
// that counts its calls: eligibility and proxy sizes, creation and fill on demand, the coherence
// steps (proxy write, native access, resolve) and the sequence of SetSize.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

#include "video_core/scene_targets_core.h"

#ifdef BB_SCENE_PROXY_NEGATIVE_CONTROL
// Mutant tests must reject an invalid policy without generating macOS crash
// reports. This preserves every assertion condition, only its failure delivery.
#undef assert
#define assert(condition) ((condition) ? static_cast<void>(0) : \
    (std::fprintf(stderr, "scene proxy assertion line %d: %s\n", __LINE__, #condition), std::_Exit(7)))
#endif

namespace {

using VideoCore::Image;
using VideoCore::ImageId;
using VideoCore::ImageViewInfo;
using VideoCore::SceneTargetsCore;
using SceneSize = VideoCore::SceneResolution::Size;

class FakeTargets final : public SceneTargetsCore {
public:
    using SceneTargetsCore::BeginAttachment;
    using SceneTargetsCore::BeginSample;
    using SceneTargetsCore::Get;

    explicit FakeTargets(Lookup get) : SceneTargetsCore{std::move(get)} {}

    bool ProxyFormatSupported(const Image&) const override {
        return format_supported;
    }
    bool MipChainFormatSupported(const Image&) const override {
        return mips_supported;
    }
    std::unique_ptr<ProxyData> CreateProxy(const Image&, SceneSize size) override {
        calls.push_back("create " + std::to_string(size.width) + "x" + std::to_string(size.height));
        ++creates;
        struct Probe final : ProxyData {
            explicit Probe(std::shared_ptr<int> count_) : count{std::move(count_)} {}
            ~Probe() override { ++*count; }
            std::shared_ptr<int> count;
        };
        return std::make_unique<Probe>(destroyed);
    }
    void CopyProxy(Entry& entry, Image&, bool to_native) override {
        calls.push_back(std::string(to_native ? "resolve " : "fill ") +
                        std::to_string(entry.level));
        ++(to_native ? resolves : fills);
        // The recursion guard: native accesses made by the copy itself are no-ops.
        assert(!Tracks(entry.uid));
        assert(CollectUnavailable() == 0); // the copy/resolve owns its catalogue traversal
    }
    vk::ImageLayout Prepare(Entry&, const Image&, Use use) override {
        calls.push_back(use == Use::Attachment ? "prepare attachment" : "prepare sample");
        return use == Use::Attachment ? vk::ImageLayout::eColorAttachmentOptimal
                                      : vk::ImageLayout::eShaderReadOnlyOptimal;
    }
    void Finish() override {
        calls.push_back("finish");
    }

    std::vector<std::string> Take() {
        return std::exchange(calls, {});
    }

    bool format_supported = true, mips_supported = true;
    int creates = 0, fills = 0, resolves = 0;
    std::shared_ptr<int> destroyed = std::make_shared<int>(0);
    std::vector<std::string> calls;
};

struct Fixture {
    Fixture() : targets{[this](ImageId id, u64) -> Image* {
        return id.index < images.size() ? images[id.index].get() : nullptr;
    }} {}

    ImageId Add(u32 width, u32 height, u32 levels = 1, bool depth = false) {
        // The constructor returns early for an undefined format: no cache backend is touched.
        auto* backend = static_cast<VideoCore::CacheBackend*>(nullptr);
        auto image = std::make_unique<Image>(*backend, slots, VideoCore::ImageInfo{});
        image->info.size = {width, height, 1};
        image->info.resources.levels = levels;
        image->info.props.is_depth = depth;
        image->info.pixel_format = vk::Format::eR8G8B8A8Unorm;
        image->image_uid = images.size() + 1;
        backings.push_back(std::make_unique<Image::BackingImage>());
        backings.back()->num_samples = 1;
        image->backing = backings.back().get();
        images.push_back(std::move(image));
        return ImageId{u32(images.size() - 1)};
    }

    Common::SlotVector<VideoCore::ImageView> slots;
    std::vector<std::unique_ptr<Image>> images;
    std::vector<std::unique_ptr<Image::BackingImage>> backings;
    FakeTargets targets;
};

bool Is(SceneSize size, u32 width, u32 height) {
    return size.width == width && size.height == height;
}

bool Eq(const std::vector<std::string>& a, const std::vector<std::string>& b) {
    if (a == b) return true;
    std::printf("got:");
    for (const auto& c : a) std::printf(" [%s]", c.c_str());
    std::printf("\nwant:");
    for (const auto& c : b) std::printf(" [%s]", c.c_str());
    std::printf("\n");
    return false;
}

void TestPresetDimensions() {
    using VideoCore::SceneResolution::ForPreset;
    assert(Is(ForPreset(0), 1920, 1080));
    assert(Is(ForPreset(5), 1536, 864));
    assert(Is(ForPreset(1), 1280, 720));
    assert(Is(ForPreset(2), 1130, 636));
    assert(Is(ForPreset(3), 960, 540));
    assert(Is(ForPreset(4), 640, 360));
    assert(Is(ForPreset(5, {1280, 720}), 1024, 576));
    assert(Is(ForPreset(5, {2560, 1440}), 2048, 1152));
    std::printf("six preset dimensions match labels; old stored IDs unchanged\n");
}

void TestEligibility() {
    Fixture f;
    const auto scene = f.Add(1920, 1080);
    const auto half = f.Add(960, 540);
    const auto other = f.Add(1280, 720);
    const auto chain = f.Add(960, 540, 5);
    const auto scene_chain = f.Add(1920, 1080, 3);
    const auto depth = f.Add(1920, 1080, 1, true);
    auto& t = f.targets;
    t.SetSize({960, 540});
    assert(t.Reduced() && Is(t.Size(), 960, 540));
    assert(t.Eligible(*f.images[scene.index]) && t.EligibleScene(*f.images[scene.index]));
    assert(t.Eligible(*f.images[half.index]) && !t.EligibleScene(*f.images[half.index]));
    assert(!t.Eligible(*f.images[other.index]));
    assert(t.Eligible(*f.images[chain.index]));
    assert(!t.Eligible(*f.images[scene_chain.index])); // chains only at half resolution
    assert(t.Eligible(*f.images[depth.index]));
    t.mips_supported = false;
    assert(!t.Eligible(*f.images[chain.index]));
    t.mips_supported = true;
    t.format_supported = false;
    assert(!t.Eligible(*f.images[scene.index]) && t.Eligible(*f.images[chain.index]));
    t.format_supported = true;
    // Half resolution needs a scene size that halves exactly.
    t.SetSize({961, 541});
    assert(!t.Eligible(*f.images[half.index]) && t.Eligible(*f.images[scene.index]));
    t.SetSize({960, 540});
    assert(Is(t.ProxySize(*f.images[scene.index]), 960, 540));
    assert(Is(t.ProxySize(*f.images[half.index]), 480, 270));
    assert(Is(t.ProxySize(*f.images[chain.index], 2), 120, 67));
    std::printf("eligibility ok\n");
}

void TestCoherence() {
    Fixture f;
    const auto scene = f.Add(1920, 1080);
    auto& image = *f.images[scene.index];
    auto& t = f.targets;
    t.SetSize({960, 540});
    f.targets.Take();

    // The first attachment creates the proxy and fills it from the native image.
    ImageViewInfo view{};
    vk::ImageLayout layout{};
    t.BeginAttachment(scene, view, layout);
    assert(layout == vk::ImageLayout::eColorAttachmentOptimal);
    assert(Eq(t.Take(), {"create 960x540", "fill 0", "prepare attachment"}));
    assert(image.scene_proxy && t.Tracks(image.image_uid));
    assert(t.ProxyCurrent(image));
    // Drawing again (the recent cache) does not create or fill.
    t.BeginAttachment(scene, view, layout);
    assert(Eq(t.Take(), {"prepare attachment"}));
    assert(t.creates == 1 && t.fills == 1);

    // A native read resolves the dirty proxy once; the proxy stays current.
    t.NativeAccess(image, false);
    t.NativeAccess(image, false);
    assert(Eq(t.Take(), {"resolve 0"}));
    assert(t.ProxyCurrent(image));
    // Sampling the current proxy.
    assert(t.BeginSample(image, view, layout) != nullptr);
    assert(layout == vk::ImageLayout::eShaderReadOnlyOptimal);
    assert(Eq(t.Take(), {"prepare sample"}));
    // A native write invalidates it: no proxy to sample, the next attachment fills again.
    t.NativeAccess(image, true);
    assert(t.Take().empty());
    assert(!t.ProxyCurrent(image) && t.BeginSample(image, view, layout) == nullptr);
    t.BeginAttachment(scene, view, layout);
    assert(Eq(t.Take(), {"fill 0", "prepare attachment"}));
    // A write that follows a dirty proxy resolves first, then invalidates.
    t.NativeAccess(image, true);
    assert(Eq(t.Take(), {"resolve 0"}));
    assert(!t.ProxyCurrent(image));
    // Images that never had a proxy cost nothing.
    const auto other = f.Add(1920, 1080);
    t.NativeAccess(*f.images[other.index], true);
    assert(t.Take().empty() && !t.Tracks(f.images[other.index]->image_uid));
    // Depth is never sampled from the proxy.
    const auto depth = f.Add(1920, 1080, 1, true);
    t.BeginAttachment(depth, view, layout);
    assert(!t.ProxyCurrent(*f.images[depth.index]));
    std::printf("coherence ok\n");
}

void TestSetSizeAndMips() {
    Fixture f;
    const auto scene = f.Add(1920, 1080);
    const auto chain = f.Add(960, 540, 3);
    auto& t = f.targets;
    constexpr SceneSize performance{960, 540}, larger{1280, 720};
    assert(t.SetSize(performance) && !t.SetSize(performance));
    const u64 generation = t.Generation();
    ImageViewInfo view{};
    vk::ImageLayout layout{};
    t.BeginAttachment(scene, view, layout);
    // One proxy per level of a chain; a native access resolves every dirty level.
    for (u32 level = 0; level < 3; ++level) {
        view.range.base.level = level;
        t.BeginAttachment(chain, view, layout);
    }
    assert(t.creates == 4);
    t.Take();
    t.NativeAccess(*f.images[chain.index], false);
    assert(Eq(t.Take(), {"resolve 0", "resolve 1", "resolve 2"}));
    // A new size resolves what is dirty, waits, drops everything and invalidates the views.
    view.range.base.level = 0;
    t.BeginAttachment(scene, view, layout);
    t.Take();
    assert(t.SetSize(larger));
    assert(Eq(t.Take(), {"resolve 0", "finish"}));
    assert(t.Generation() == generation + 1);
    assert(!t.Tracks(f.images[scene.index]->image_uid) && !t.ProxyCurrent(*f.images[scene.index]));
    t.BeginAttachment(scene, view, layout);
    assert(Eq(t.Take(), {"create 1280x720", "fill 0", "prepare attachment"}));
    // Back to the native size: the proxies go, nothing is reduced.
    assert(t.SetSize({}) && !t.Reduced());
    t.Take();
    std::printf("set size ok\n");
}

void TestFullColorClear() {
    Fixture f;
    const auto color=f.Add(1920,1080);
    const auto depth=f.Add(1920,1080,1,true);
    auto& t=f.targets;t.SetSize({960,540});t.Take();
    ImageViewInfo view{};vk::ImageLayout layout{};
    // The original contents are irrelevant only for a full color load-action clear.
    t.BeginAttachment(color,view,layout,true);
    assert(Eq(t.Take(),{"create 960x540","prepare attachment"}));
    assert(t.fills==0 && t.SkippedClearFills()==1 && t.ProxyCurrent(*f.images[color.index]));
    // A later native reader still resolves the newly cleared/drawn proxy.
    t.NativeAccess(*f.images[color.index],false);
    assert(Eq(t.Take(),{"resolve 0"}));
    t.NativeAccess(*f.images[color.index],true);t.Take();
    // Native writes invalidate; a loading attachment must refill, never reuse stale data.
    t.BeginAttachment(color,view,layout);
    assert(Eq(t.Take(),{"fill 0","prepare attachment"}));
    t.NativeAccess(*f.images[color.index],true);t.Take();
    t.BeginAttachment(color,view,layout,true);
    assert(Eq(t.Take(),{"prepare attachment"}) && t.SkippedClearFills()==2);
    // A depth-only or stencil-only clear must retain the other aspect.
    t.BeginAttachment(depth,view,layout,true);
    assert(Eq(t.Take(),{"create 960x540","fill 0","prepare attachment"}));
    assert(t.SkippedClearFills()==2);
    std::printf("full color clear preservation ok\n");
}

void TestDiscardOverwrite() {
    Fixture f;const auto chain=f.Add(960,540,3);
    auto& t=f.targets;auto& image=*f.images[chain.index];
    t.SetSize({960,540});t.Take();
    ImageViewInfo view{};vk::ImageLayout layout{};
    for(u32 i=0;i<3;++i) {view.range.base.level=i;t.BeginAttachment(chain,view,layout);}
    t.Take();
    assert(t.ProxyDirty(image,0) && t.ProxyDirty(image,1) && t.ProxyDirty(image,2));
    assert(t.DiscardNativeContents(image,1));
    assert(!t.ProxyDirty(image,1) && !t.ProxyCurrent(image,1));
    // Overwriting one mip must not discard or resolve a different dirty level.
    t.NativeAccess(image,false);
    assert(Eq(t.Take(),{"resolve 0","resolve 2"}));
    view.range.base.level=1;t.BeginAttachment(chain,view,layout);
    assert(Eq(t.Take(),{"fill 1","prepare attachment"}));
    assert(!t.DiscardNativeContents(image,9));
    std::printf("complete overwrite invalidation is mip-local ok\n");
}

void TestUnavailableSources() {
    Fixture f;
    const auto dead = f.Add(960, 540, 3);
    const auto live = f.Add(1920, 1080);
    auto& t = f.targets;
    t.SetSize({960, 540}); t.Take();
    ImageViewInfo view{}; vk::ImageLayout layout{};
    for (u32 level = 0; level < 3; ++level) {
        view.range.base.level = level;
        t.BeginAttachment(dead, view, layout);
    }
    view.range.base.level = 0;
    t.BeginAttachment(live, view, layout);
    const auto dead_uid = f.images[dead.index]->image_uid;
    const auto live_uid = f.images[live.index]->image_uid;
    const auto generation = t.Generation();
    t.Take();
    assert(t.CollectUnavailable() == 0 && t.Generation() == generation);
    assert(*t.destroyed == 0 && t.ProxyDirty(*f.images[live.index], 0));
    assert(t.Take().empty()); // no fill, resolve, Finish or live-cache eviction
    f.images[dead.index].reset();
    assert(t.CollectUnavailable() == 3 && *t.destroyed == 3);
    assert(t.Generation() == generation + 1 && !t.Tracks(dead_uid) && t.Tracks(live_uid));
    assert(t.ProxyDirty(*f.images[live.index], 0) && t.Take().empty());
    assert(!t.SetSize({960, 540}) && t.CollectUnavailable() == 0);
    assert(t.Generation() == generation + 1);
    // A surviving recent pointer must be re-created, not dereferenced after erase.
    t.BeginAttachment(live, view, layout);
    assert(Eq(t.Take(), {"prepare attachment"}));
    t.NativeAccess(*f.images[live.index], false);
    assert(Eq(t.Take(), {"resolve 0"}));
    // Simulate slot reuse even with a caller lookup that ignores uid. Collect
    // verifies the actual UID too; the new image's proxy remains independent.
    f.images[live.index]->image_uid = 999;
    assert(t.CollectUnavailable() == 1 && !t.Tracks(live_uid));
    t.BeginAttachment(live, view, layout);
    assert(Eq(t.Take(), {"create 960x540", "fill 0", "prepare attachment"}));
    assert(t.Tracks(999) && t.CollectUnavailable() == 0);
    std::printf("unavailable proxies: exact UID, all mips, recent pointers and dirty-live preservation ok\n");
}

} // namespace

int main() {
    TestPresetDimensions();
    TestEligibility();
    TestCoherence();
    TestSetSizeAndMips();
    TestFullColorClear();
    TestDiscardOverwrite();
    TestUnavailableSources();
    std::printf("scene-targets-core-test passed\n");
    return 0;
}
