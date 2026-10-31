// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/scene_targets_core.h"
#include "bbport_toggles.h"
#include <cstdio>

namespace VideoCore {

// 1 for the scene size, 2 for half resolution (the game's post-processing chain), else 0.
// Half resolution needs a scene size that halves exactly: the proxy keeps the scene's factor,
// so the shaders' FragCoord conversion (1920 / scene width) stays correct for it.
static u32 SizeDivisor(const ImageInfo& i, SceneResolution::Size scene) {
    if (i.size.width == 1920 && i.size.height == 1080) return 1;
    if (i.size.width == 960 && i.size.height == 540 && scene.width % 2 == 0 &&
        scene.height % 2 == 0 && !BbToggle::Disabled(BbToggle::SceneHalfRes)) {
        return 2;
    }
    return 0;
}

bool SceneTargetsCore::Eligible(const Image& image) const {
    const auto& i = image.info;
    const u32 div = SizeDivisor(i, size);
    if (!div || i.size.depth != 1 || i.resources.layers != 1 || i.num_samples != 1 ||
        i.props.is_block || !image.backing || image.backing->num_samples != 1) {
        return false;
    }
    if (i.resources.levels != 1) {
        // Mip chains (the bloom pyramid at half resolution): one blitted proxy per level.
        return div == 2 && i.resources.levels <= 16 && !i.props.is_depth &&
               MipChainFormatSupported(image);
    }
    return ProxyFormatSupported(image);
}

bool SceneTargetsCore::EligibleScene(const Image& image) const {
    return image.info.size.width == 1920 && image.info.size.height == 1080 && Eligible(image);
}

SceneResolution::Size SceneTargetsCore::ProxySize(const Image& image, u32 level) const {
    const u32 div = std::max(1u, SizeDivisor(image.info, size));
    return {std::max(1u, size.width / div >> level), std::max(1u, size.height / div >> level)};
}

bool SceneTargetsCore::SetSize(SceneResolution::Size next) {
    if (next == size) return false;
    ResolveAll();
    Finish();
    entries.clear(); // no command buffer can still reference these images/views
    tracked.clear();
    ++generation;
    recent = {};
    size = next;
    std::printf("Scene resolution: raster %ux%u, half-resolution targets %ux%u, UI 1920x1080 "
                "(live)\n", size.width, size.height, size.width / 2, size.height / 2);
    return true;
}

void SceneTargetsCore::ResolveAll() {
    for (auto& [key, entry] : entries) {
        if (entry->state.dirty) {
            if (auto* image = lookup(entry->source, entry->uid)) Copy(*entry, *image, true);
        }
    }
}

u64 SceneTargetsCore::CollectUnavailable() {
    if (copying || entries.empty()) return 0;
    u64 removed = 0;
    for (auto it = entries.begin(); it != entries.end();) {
        const auto& entry = *it->second;
        const auto* original = lookup(entry.source, entry.uid);
        if (original && original->image_uid == entry.uid) {
            ++it;
            continue;
        }
        // recent contains raw Entry pointers; clear it before their ownership
        // ends. A different Image may already occupy the same cache slot.
        if (!removed) {
            recent = {};
            recent_next = 0;
        }
        tracked.erase(entry.uid);
        it = entries.erase(it);
        ++removed;
    }
    if (removed) ++generation;
    return removed;
}

void SceneTargetsCore::NativeAccess(Image& image, bool writes) {
    if (copying || !image.scene_proxy) return; // most images never had a proxy
    const u32 levels = std::min(16u, image.info.resources.levels);
    for (u32 level = 0; level < levels; ++level) {
        const auto it = entries.find(Key(image.image_uid, level));
        if (it == entries.end()) continue;
        auto& entry = *it->second;
        if (entry.state.dirty) Copy(entry, image, true);
        if (writes) entry.state.NativeWrite();
    }
}

bool SceneTargetsCore::ProxyDirty(const Image& image, u32 level) const {
    if(copying || !image.scene_proxy) return false;
    const auto it=entries.find(Key(image.image_uid,level));
    return it!=entries.end() && it->second->state.dirty;
}

bool SceneTargetsCore::DiscardNativeContents(Image& image, u32 level) {
    if(copying || !image.scene_proxy) return false;
    const auto it=entries.find(Key(image.image_uid,level));
    if(it==entries.end()) return false;
    const bool dirty=it->second->state.dirty;
    it->second->state.NativeWrite();
    return dirty;
}

void SceneTargetsCore::Copy(Entry& entry, Image& original, bool to_native) {
    copying = true;
    CopyProxy(entry, original, to_native);
    if (to_native) entry.state.Resolved(); else entry.state.CopiedToProxy();
    copying = false;
}

SceneTargetsCore::Entry& SceneTargetsCore::Get(ImageId id, u32 level, bool fill) {
    auto& original = *lookup(id, 0);
    const u64 key = Key(original.image_uid, level);
    // A draw has up to six targets, mostly the same as the previous draw's.
    for (const auto& [recent_key, recent_entry] : recent) {
        if (recent_entry && recent_key == key) {
            if (!recent_entry->state.valid) {
                if (fill) Copy(*recent_entry, original, false); else ++skipped_clear_fills;
            }
            return *recent_entry;
        }
    }
    auto& entry = entries[key];
    original.scene_proxy = true;
    if (!entry) {
        entry = std::make_unique<Entry>();
        entry->source = id;
        entry->uid = original.image_uid;
        entry->level = level;
        entry->proxy = CreateProxy(original, ProxySize(original, level));
        tracked.insert(original.image_uid);
    }
    recent[recent_next++ % recent.size()] = {key, entry.get()};
    if (!entry->state.valid) {
        if (fill) Copy(*entry, original, false); else ++skipped_clear_fills;
    }
    return *entry;
}

SceneTargetsCore::Entry& SceneTargetsCore::BeginAttachment(ImageId id, const ImageViewInfo& info,
                                                           vk::ImageLayout& layout, bool clear_color) {
    auto& original = Original(id);
    // Depth/stencil is always preserved, including depth-only or stencil-only clears.
    const bool discard = clear_color && !original.info.props.is_depth;
    auto& entry = Get(id, info.range.base.level, !discard);
    layout = Prepare(entry, original, Use::Attachment);
    entry.state.ProxyWrite();
    return entry;
}

bool SceneTargetsCore::ProxyCurrent(const Image& image, u32 level, u32 levels) const {
    if (copying || !image.scene_proxy || image.info.props.is_depth || levels != 1) {
        return false;
    }
    const auto it = entries.find(Key(image.image_uid, level));
    return it != entries.end() && it->second->state.valid;
}

vk::ImageLayout SceneTargetsCore::PrepareSample(const Image& image, u32 level) {
    return Prepare(*entries.find(Key(image.image_uid, level))->second, image, Use::Sample);
}

SceneTargetsCore::Entry* SceneTargetsCore::BeginSample(const Image& image,
                                                       const ImageViewInfo& info,
                                                       vk::ImageLayout& layout) {
    const u32 level = info.range.base.level;
    if (!ProxyCurrent(image, level, info.range.extent.levels)) {
        return nullptr;
    }
    layout = PrepareSample(image, level);
    return entries.find(Key(image.image_uid, level))->second.get();
}

} // namespace VideoCore
