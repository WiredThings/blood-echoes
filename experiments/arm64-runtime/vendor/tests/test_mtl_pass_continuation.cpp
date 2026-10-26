// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdio>
#include "video_core/renderer_metal/mtl_pass_continuation.h"

int main() {
    using namespace Metal::PassContinuation;
    using VideoCore::RenderState;
    unsigned checks = 0, failures = 0;
    const auto check = [&](bool good, const char* name) {
        ++checks;
        if (!good) { ++failures; std::printf("FAIL %s\n", name); }
    };
    RenderState old{};
    old.width = 1920; old.height = 1080; old.num_layers = 1;
    old.num_color_attachments = 1;
    auto& color = old.color_attachments[0];
    color.image_view = 100; color.image_layout = vk::ImageLayout::eColorAttachmentOptimal;
    color.is_clear = 1; color.clear_value = {0x3f000000, 0, 0, 0x3f800000};
    auto& depth = old.depth_stencil_attachment;
    depth.image_view = 200; depth.image_layout = vk::ImageLayout::eDepthStencilAttachmentOptimal;
    depth.has_depth = true; depth.has_stencil = true; depth.depth_clear = true;
    depth.clear_value = {0x3f800000, 0, 0, 0};
    auto next = old;
    next.color_attachments[0].is_clear = 0;
    next.color_attachments[0].clear_value = {};
    next.depth_stencil_attachment.depth_clear = false;
    next.depth_stencil_attachment.clear_value = {};
    check(ConsumedClearBecomesLoad(old, next), "MRT and nonzero depth clear consumed");
    check(!ConsumedClearBecomesLoad(old, old), "a fresh identical clear must execute");
    check(!ConsumedClearBecomesLoad(next, next), "ordinary loads are outside this optimization");
    const auto rejects = [&](auto mutate, const char* name) {
        auto changed = next; mutate(changed);
        check(!ConsumedClearBecomesLoad(old, changed), name);
    };
    rejects([](auto& s) { ++s.width; }, "width");
    rejects([](auto& s) { ++s.height; }, "height");
    rejects([](auto& s) { ++s.num_layers; }, "layers");
    rejects([](auto& s) { ++s.num_color_attachments; }, "colour count");
    rejects([](auto& s) { ++s.color_attachments[0].image_view; }, "colour view");
    rejects([](auto& s) { ++s.depth_stencil_attachment.image_view; }, "depth view");
    rejects([](auto& s) { s.depth_stencil_attachment.has_depth = false; }, "depth presence");
    rejects([](auto& s) { s.depth_stencil_attachment.has_stencil = false; }, "stencil presence");
    rejects([](auto& s) { s.depth_stencil_attachment.image_layout = vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal; }, "depth/stencil access role");
    rejects([](auto& s) { s.color_attachments[0].image_layout = vk::ImageLayout::eGeneral; }, "feedback layout");
    rejects([](auto& s) { s.color_attachments[0].is_clear = 1; }, "fresh colour clear");
    rejects([](auto& s) { s.depth_stencil_attachment.depth_clear = true; }, "fresh depth clear");
    rejects([](auto& s) { s.depth_stencil_attachment.stencil_clear = true; }, "fresh stencil clear");
    // Shadow-only 4096 pass seen in real captures: consumed 1.0 must match a load.
    auto shadow = old;
    shadow.width = shadow.height = 4096; shadow.num_color_attachments = 0;
    shadow.color_attachments = {};
    shadow.depth_stencil_attachment.has_stencil = false;
    shadow.depth_stencil_attachment.image_layout = vk::ImageLayout::eDepthAttachmentOptimal;
    auto shadow_load = shadow;
    shadow_load.depth_stencil_attachment.depth_clear = false;
    shadow_load.depth_stencil_attachment.clear_value = {};
    check(ConsumedClearBecomesLoad(shadow, shadow_load), "shadow clear consumed");
    // After continuing, remember the LOAD state. Otherwise an identical fresh clear could
    // accidentally match the original remembered state and never execute.
    auto remembered = shadow_load;
    check(!(remembered == shadow), "later identical clear no longer matches remembered state");
    check(!ConsumedClearBecomesLoad(remembered, shadow), "later identical clear cannot continue");
    auto invalid = old; invalid.color_attachments[0].is_clear = 2;
    check(!ConsumedClearBecomesLoad(invalid, next), "unknown colour flags");
    // The diagnostic must classify only the one requested depth/stencil layout field.
    constexpr std::array layouts{vk::ImageLayout::eDepthStencilAttachmentOptimal,
        vk::ImageLayout::eDepthStencilReadOnlyOptimal,
        vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal,
        vk::ImageLayout::eDepthAttachmentStencilReadOnlyOptimal};
    auto loaded = next;
    for (auto from : layouts) for (auto to : layouts) {
        auto a = loaded, b = loaded;
        a.depth_stencil_attachment.image_layout = from;
        b.depth_stencil_attachment.image_layout = to;
        check(DepthStencilLayoutOnly(a, b) == (from != to), "all depth/stencil layout pairs");
        const auto mask=[](vk::ImageLayout l) {return l==vk::ImageLayout::eDepthStencilReadOnlyOptimal?0u:l==vk::ImageLayout::eDepthAttachmentStencilReadOnlyOptimal?1u:l==vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal?2u:3u;};
        check(DepthLayoutWritePromotion(a,b)==(mask(from)!=mask(to)&&!(mask(from)&~mask(to))), "exact readonly-to-write promotion matrix");

    }
    auto a = loaded, b = loaded;
    b.depth_stencil_attachment.image_layout = vk::ImageLayout::eDepthStencilReadOnlyOptimal;
    check(DepthStencilLayoutOnly(a, b), "diagnostic layout candidate");
    for (unsigned byte = 0; byte < sizeof(RenderState); ++byte) {
        const auto start = offsetof(RenderState, depth_stencil_attachment) +
                           offsetof(VideoCore::RenderAttachment, image_layout);
        if (byte >= start && byte < start + sizeof(vk::ImageLayout)) continue;
        auto changed = b;
        reinterpret_cast<unsigned char*>(&changed)[byte] ^= 1;
        check(!DepthStencilLayoutOnly(a, changed), "any other changed state byte rejects");
    }
    for (auto unknown : {vk::ImageLayout::eGeneral, vk::ImageLayout::eDepthAttachmentOptimal,
                        vk::ImageLayout::eUndefined}) {
        auto changed = b; changed.depth_stencil_attachment.image_layout = unknown;
        check(!DepthStencilLayoutOnly(a, changed), "unknown/partial plane layouts reject");
    }
    a.depth_stencil_attachment.has_stencil = false;
    b.depth_stencil_attachment.has_stencil = false;
    check(!DepthStencilLayoutOnly(a, b), "single-plane needs separate proof");
    check(QueryDensityIsAdditive(1920,1080), "native query density additive");
    check(QueryDensityIsAdditive(3840,2160), "larger query density additive");
    check(!QueryDensityIsAdditive(1280,720), "fractional density must keep per-pass rounding");
    check(!QueryDensityIsAdditive(0,1080), "empty query target rejects");
    check(!QueryDensityIsAdditive(UINT64_MAX,1080), "query dimensions cannot overflow");
    const auto scaled = [](std::uint64_t n) { return (n * 147456 + 65535) >> 16; };
    check(scaled(1)+scaled(1) != scaled(2), "720p per-pass ceil counterexample");
    std::printf("Pass continuation state policy: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
