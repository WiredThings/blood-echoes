// SPDX-License-Identifier: GPL-2.0-or-later
// The shared UI composition (video_core/ui_compositor.h) against a backend that records its calls:
// when the UI phase starts and with which background, what the redirects return during and after
// it, and what the presenter sees.
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "video_core/ui_compositor.h"

namespace {

using VideoCore::ImageId;

constexpr u64 kMovie = 0x34e8a281;
constexpr VAddr kUiAddress = 0x1000, kDisplayAddress = 0x2000;
constexpr vk::Format kColor = vk::Format::eR8G8B8A8Unorm;
constexpr vk::Format kDepth = vk::Format::eD32SfloatS8Uint;

class FakeBackend final : public VideoCore::UiCompositionBackend {
public:
    void EnsureUiImages(u32 width, u32 height, vk::Format color, vk::Format depth, bool new_color,
                        bool new_depth) override {
        calls.push_back("ensure " + std::to_string(width) + "x" + std::to_string(height) + " " +
                        std::to_string(u32(color)) + " " + std::to_string(u32(depth)) +
                        (new_color ? " color" : "") + (new_depth ? " depth" : ""));
    }
    void CopyBackground(ImageId color, u32 width, u32 height) override {
        calls.push_back("copy " + std::to_string(color.index) + " " + std::to_string(width) + "x" +
                        std::to_string(height));
    }
    void PrepareUiDepth(ImageId depth, bool copy) override {
        calls.push_back("depth " + std::to_string(depth.index) + (copy ? " copy" : " clear"));
    }
    bool CanCopyDepth(vk::Format format) const override {
        return can_copy_depth && format == kDepth;
    }
    VideoCore::ReplacementView UiColorView(vk::Format format,
                                           vk::ComponentMapping mapping) override {
        calls.push_back("color_view " + std::to_string(u32(format)) +
                        (mapping == vk::ComponentMapping{} ? "" : " swizzled"));
        return {0xc0, vk::ImageLayout::eGeneral};
    }
    VideoCore::ReplacementView UiDepthView() override {
        return {0xd0, vk::ImageLayout::eGeneral};
    }
    void UiReadBarrier() override {
        calls.push_back("barrier");
    }
    VideoCore::ReplacementView DisplayTarget(VAddr address, vk::Format image_format,
                                             vk::Format view_format, u32 width,
                                             u32 height) override {
        calls.push_back("display " + std::to_string(address) + " " +
                        std::to_string(u32(image_format)) + " " + std::to_string(u32(view_format)) +
                        " " + std::to_string(width) + "x" + std::to_string(height));
        return {0xe0, vk::ImageLayout::eGeneral};
    }

    std::vector<std::string> Take() {
        return std::exchange(calls, {});
    }

    bool can_copy_depth = true;
    std::vector<std::string> calls;
};

struct Images {
    std::map<u32, VideoCore::UiImageInfo> table{
        {1, {kColor, 960, 540, kUiAddress}},       // the game's UI target
        {2, {kDepth, 960, 540, 0}},                // its depth
        {3, {kColor, 960, 540, kDisplayAddress}},  // a display buffer
        {4, {kDepth, 960, 540, 0}},                // another depth (a stencil mask pass)
        {5, {vk::Format::eR16G16B16A16Sfloat, 960, 540, 0x3000}}, // the HDR scene
    };

    VideoCore::UiCompositorEnv Env() {
        return {[this](ImageId id) { return table.at(id.index); },
                [](VAddr address) { return address == kDisplayAddress; }};
    }
};

bool Never() {
    assert(false && "temporal background not expected");
    return false;
}

} // namespace

int main() {
    setenv("BB_RENDER_RES", "960x540", 1);
    unsetenv("BB_OUTPUT_RES");
    unsetenv("BB_UI_TRACE");
    Images images;
    const ImageId ui_target{1}, depth{2}, display{3}, mask_depth{4}, hdr{5};
    const VideoCore::ImageViewInfo view_info;
    const auto never = [] { (void)Never(); };

    {
        FakeBackend backend;
        VideoCore::UiCompositor ui{backend, images.Env()};
        assert(ui.Scaled() && ui.ScaledSession() && ui.RenderTarget(960, 540));
        assert(ui.OutputWidth() == 1920 && ui.OutputHeight() == 1080);
        VideoCore::UpscalerTarget target;

        // Draws that are not the UI start nothing: not a movie shader, not an RGBA8 target.
        assert(!ui.OnDraw(0x1234, ui_target, depth, false, false, false, never));
        assert(!ui.OnDraw(kMovie, hdr, depth, false, false, false, never));
        assert(!ui.OnDraw(kMovie, display, depth, false, false, false, never));
        assert(!ui.UiPhase() && backend.Take().empty());
        assert(!ui.RedirectColor(ui_target, view_info, target));
        assert(!ui.RedirectDepth(depth, target));

        // A movie draw into the render-size RGBA8 target, nothing to upscale: the copy.
        assert(ui.OnDraw(kMovie, ui_target, depth, false, false, false, never));
        assert(ui.UiPhase());
        const std::vector<std::string> start{
            "ensure 1920x1080 " + std::to_string(u32(kColor)) + " " + std::to_string(u32(kDepth)) +
                " color depth",
            "depth 2 copy", "copy 1 960x540"};
        assert(backend.Take() == start);

        // During the phase the UI target and its depth are the output-size images.
        assert(ui.RedirectColor(ui_target, view_info, target));
        assert(target.view == 0xc0 && target.native_ui && target.width == 1920 &&
               target.height == 1080);
        assert(ui.RedirectDepth(depth, target));
        assert(target.view == 0xd0 && target.native_ui && target.width == 1920);
        assert(!ui.RedirectDepth(mask_depth, target));
        const u64 phase_state = ui.RedirectState();
        assert(phase_state & 1);
        backend.Take();

        // The stencil-only mask pass (no color target) into another depth switches the UI depth
        // (issue #67); same formats: nothing is recreated.
        assert(!ui.OnDraw(kMovie, ImageId{}, mask_depth, false, false, false, never));
        assert((backend.Take() == std::vector<std::string>{"depth 4 copy"}));
        assert(ui.RedirectDepth(mask_depth, target) && !ui.RedirectDepth(depth, target));

        // The display pass is not redirected within the phase.
        assert(!ui.RedirectColor(display, view_info, target));
        assert(!ui.DisplayOverride(kDisplayAddress, [](vk::Format, u32, u32) { assert(false); }));
        assert(!ui.RedirectsSampled(ui_target));

        // Next frame: the display pass goes to the output-size display image, sampling the UI.
        ui.OnFrameStart();
        assert(!ui.UiPhase() && ui.RedirectsSampled(ui_target));
        assert(ui.RedirectState() != phase_state);
        backend.Take();
        assert(ui.RedirectColor(display, view_info, target));
        assert(target.view == 0xe0 && !target.native_ui && target.width == 1920 &&
               target.height == 1080);
        assert((backend.Take() == std::vector<std::string>{
                   "display 8192 " + std::to_string(u32(kColor)) + " " +
                   std::to_string(u32(view_info.format)) + " 1920x1080"}));
        u32 shown_w = 0, shown_h = 0;
        assert(ui.DisplayOverride(kDisplayAddress, [&](vk::Format format, u32 w, u32 h) {
            assert(format == kColor);
            shown_w = w;
            shown_h = h;
        }));
        assert(shown_w == 1920 && shown_h == 1080);
        assert(!ui.DisplayOverride(0x9000, [](vk::Format, u32, u32) { assert(false); }));

        // The display pass's sampled UI target is the UI image; the read barrier comes once.
        VideoCore::NativeView view = 0;
        vk::ImageLayout layout{};
        VideoCore::ImageViewInfo sampled;
        sampled.format = vk::Format::eR8G8B8A8Srgb;
        sampled.mapping.r = vk::ComponentSwizzle::eB;
        assert(!ui.RedirectSampled(hdr, sampled, view, layout) && view == 0);
        assert(ui.RedirectSampled(ui_target, sampled, view, layout));
        assert(view == 0xc0 && layout == vk::ImageLayout::eGeneral);
        assert(ui.RedirectSampled(ui_target, sampled, view, layout));
        assert((backend.Take() == std::vector<std::string>{
                   "barrier", "color_view " + std::to_string(u32(sampled.format)) + " swizzled",
                   "color_view " + std::to_string(u32(sampled.format)) + " swizzled"}));

        // A frame without UI draws: the display pass shows the guest buffer.
        ui.OnFrameStart();
        assert(!ui.RedirectsSampled(ui_target));
        assert(!ui.RedirectColor(display, view_info, target));
        assert(!ui.DisplayOverride(kDisplayAddress, [](vk::Format, u32, u32) { assert(false); }));
        assert(backend.Take().empty());
    }

    {
        // With an upscaler and the finished scene the temporal background goes first; the UI
        // phase it starts is the compositor's to track (Begin). When it did not start one (a
        // failed dispatch) the copy follows, exactly as without the upscaler.
        FakeBackend backend;
        VideoCore::UiCompositor ui{backend, images.Env()};
        int temporal = 0;
        assert(!ui.OnDraw(kMovie, ui_target, depth, false, true, true, [&] {
            ++temporal;
            ui.Begin(ui_target, depth);
        }));
        assert(temporal == 1 && ui.UiPhase() && backend.Take().empty());
        ui.OnFrameStart();
        ui.OnFrameStart();
        assert(ui.OnDraw(kMovie, ui_target, depth, false, true, true, [&] { ++temporal; }));
        assert(temporal == 2 && ui.UiPhase());
        assert(backend.Take().size() == 3); // ensure, depth, copy
        // No depth image: the depth is cleared in the default format.
        ui.OnFrameStart();
        backend.can_copy_depth = false;
        assert(ui.OnDraw(kMovie, ui_target, ImageId{}, false, false, false, never));
        const auto calls = backend.Take();
        assert(calls.size() == 2 && calls[0] == "depth 4294967295 clear");
    }

    {
        // A native-size viewport counts as UI in a scaled session.
        FakeBackend backend;
        VideoCore::UiCompositor ui{backend, images.Env()};
        assert(ui.OnDraw(0x1234, ui_target, depth, true, false, false, never));
        assert(ui.UiPhase());
    }

    {
        // Without BB_RENDER_RES and at the 1080p output there is no composition at all.
        unsetenv("BB_RENDER_RES");
        FakeBackend backend;
        VideoCore::UiCompositor ui{backend, images.Env()};
        assert(!ui.Scaled() && !ui.ScaledSession());
        assert(ui.RenderTarget(1920, 1080) && !ui.RenderTarget(960, 540));
        assert(!ui.OnDraw(kMovie, ui_target, depth, true, false, false, never));
        assert(!ui.UiPhase() && backend.Take().empty());
        // The live output change makes it scaled (the scene targets' size is the render size).
        ui.SetOutputSize(3840, 2160);
        ui.SetRenderSize(1920, 1080);
        assert(ui.Scaled() && ui.OutputWidth() == 3840 && ui.RenderWidth() == 1920);
    }
    std::puts("UI compositor: PASS (copy background, redirects, display pass, presenter)");
    return 0;
}
