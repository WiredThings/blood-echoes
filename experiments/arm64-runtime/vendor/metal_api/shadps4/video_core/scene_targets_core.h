// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <functional>
#include <memory>
#include <unordered_map>
#include <unordered_set>

#include "common/types.h"
#include "video_core/scene_resolution.h"
#include "video_core/texture_cache/image.h"

// bbport (plan P7.5 L1, docs/metal/live_resolution_design.md): the backend-neutral part of the
// reduced scene targets of the live-resolution path (Vulkan::SceneTargets, later
// Metal::SceneTargets): which images get a proxy, at which size, the proxies' coherence with the
// guest-size ("native") images and the sequences that keep it (resolve before a native access,
// fill when a proxy is created or invalid). The images, copies and layouts stay behind the
// virtual functions below. Plain types and the shared Vulkan vocabulary (vk::Format,
// vk::ImageLayout) only.

namespace VideoCore {

class SceneTargetsCore {
public:
    using Lookup = std::function<Image*(ImageId, u64)>;

    /// What the backend keeps of one proxy (its image or texture, views, layout state); the
    /// backend derives from it and casts back in its hooks.
    struct ProxyData {
        virtual ~ProxyData() = default;
    };
    struct Entry {
        ImageId source{};
        u64 uid = 0;
        u32 level = 0; ///< mip level of the native image this proxy stands for
        SceneResolution::Coherence state;
        std::unique_ptr<ProxyData> proxy;
    };
    /// The uses of a proxy that need its image in a particular state (Prepare).
    enum class Use { Attachment, Sample };

    explicit SceneTargetsCore(Lookup get) : lookup{std::move(get)} {}
    virtual ~SceneTargetsCore() = default;

    /// Resolves every dirty proxy, waits for the GPU, drops all proxies and bumps Generation().
    /// False when the size is unchanged.
    bool SetSize(SceneResolution::Size);
    [[nodiscard]] SceneResolution::Size Size() const {
        return size;
    }
    [[nodiscard]] bool Reduced() const {
        return size != SceneResolution::Size{};
    }
    /// A reduced proxy can stand in for the image: a 1920x1080 scene target or, scaled by
    /// the same factor, a half-resolution (960x540) one (toggle SceneHalfRes).
    [[nodiscard]] bool Eligible(const Image&) const;
    /// Eligible at the full scene size (1920x1080): the upscaler's inputs, the scene start.
    [[nodiscard]] bool EligibleScene(const Image&) const;
    /// The proxy size of an eligible image: the scene size divided like the native size.
    [[nodiscard]] SceneResolution::Size ProxySize(const Image&, u32 level = 0) const;
    /// Whether the proxy of `image` for `levels` mips from `level` holds its current contents.
    [[nodiscard]] bool ProxyCurrent(const Image& image, u32 level = 0, u32 levels = 1) const;
    /// Prepares the proxy of a sampled image (it has one: ProxyCurrent) for sampling; the
    /// layout it is in.
    vk::ImageLayout PrepareSample(const Image& image, u32 level = 0);
    /// Changes when proxies and their views are destroyed (SetSize).
    [[nodiscard]] u64 Generation() const noexcept {
        return generation;
    }
    /// Whether NativeAccess has work for this image (a reduced-size proxy exists).
    [[nodiscard]] bool Tracks(u64 image_uid) const {
        return !copying && tracked.contains(image_uid);
    }
    /// Every use of a native image (Runtime::Transit): resolves a dirty proxy into it first and,
    /// when the use `writes`, invalidates the proxy.
    void NativeAccess(Image&, bool writes);
    void ResolveAll();
    /// Recording-thread boundary, with no concurrent binding helper. Drop only
    /// proxies whose exact source UID no longer exists. Never evict a live
    /// image, resolve into a dead source, wait, or release GPU resources early:
    /// backend ProxyData/UniqueImage destruction retains the existing fence path.
    /// Returns removed CPU catalogue entries, not freed/resident byte counts.
    u64 CollectUnavailable();
    // Only a caller proving a full overwrite of this mip may discard its old contents.
    // Partial writes and read/modify/write accesses must continue through NativeAccess.
    bool DiscardNativeContents(Image&, u32 level);
    [[nodiscard]] bool ProxyDirty(const Image&, u32 level) const;
    [[nodiscard]] u64 SkippedClearFills() const { return skipped_clear_fills; }
    bool debug = false; ///< BB_SCENE_DEBUG frame: print resolves and fills

protected:
    static constexpr u64 Key(u64 uid, u32 level) {
        return uid << 4 | level;
    }
    /// The entry of mip `level` of the image, created on demand, filled from the native image
    /// when not valid.
    Entry& Get(ImageId, u32 level = 0, bool fill = true);
    /// Get for a target the pass draws into: Prepare for the attachment use (`layout` is the
    /// layout it returns), then the proxy is ahead of the native image.
    Entry& BeginAttachment(ImageId, const ImageViewInfo&, vk::ImageLayout& layout, bool clear_color = false);
    /// The entry whose proxy samples `image` for this view (null: the native image is current),
    /// prepared for sampling (`layout`).
    Entry* BeginSample(const Image&, const ImageViewInfo&, vk::ImageLayout& layout);
    Image& Original(ImageId id) const {
        return *lookup(id, 0);
    }

    /// The image can have a proxy: its format (Vulkan: blittable or shader-resampled).
    [[nodiscard]] virtual bool ProxyFormatSupported(const Image&) const = 0;
    /// Mip chains get one proxy per level, copied like single-level images but never resampled
    /// by shader (Vulkan: blittable).
    [[nodiscard]] virtual bool MipChainFormatSupported(const Image&) const = 0;
    /// The proxy of `original` at `size` (its format, one level, render target and sampled).
    virtual std::unique_ptr<ProxyData> CreateProxy(const Image& original,
                                                   SceneResolution::Size size) = 0;
    /// The pixels from the proxy into the native image (`to_native`) or the other way, nearest
    /// filtered, scaled; no bookkeeping (the core updates the coherence). During the call
    /// Tracks and NativeAccess do nothing.
    virtual void CopyProxy(Entry&, Image&, bool to_native) = 0;
    /// Makes the proxy ready for the use and returns the layout it is in (Metal: nothing).
    virtual vk::ImageLayout Prepare(Entry&, const Image&, Use) = 0;
    /// Waits until no submitted command can reference the proxies (before they are dropped).
    virtual void Finish() = 0;

    Lookup lookup;
    SceneResolution::Size size;
    std::unordered_map<u64, std::unique_ptr<Entry>> entries; ///< by Key(uid, level)

private:
    void Copy(Entry&, Image&, bool to_native);

    std::unordered_set<u64> tracked; ///< uids with proxies
    bool copying = false;
    u64 generation = 0;
    u64 skipped_clear_fills = 0;
    std::array<std::pair<u64, Entry*>, 8> recent{}; ///< last entries by Key
    u32 recent_next = 0;
};

} // namespace VideoCore
