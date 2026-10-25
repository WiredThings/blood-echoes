// SPDX-License-Identifier: GPL-2.0-or-later
// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project

#include <array>
#include <chrono>
#include <dlfcn.h>
#include <map>
#include <numeric>
#include <unordered_set>
#include <xxhash.h>
#include "video_core/ui_composition.h"
#include "bbport_timeline.h"
#include "bbport_sections.h"
#include "game_profile.h"
#include "bbport_ce_stats.h"
#include "bbport_toggles.h"
#include "bbport_texture_observation.h"
#include "bbport_write_log.h"
#include "bbport_free_check.h"
#include "bbport_guest_memory.h"
#include "video_core/frame_capture.h"
#include "video_core/constant_ring.h"
#include "bblayer_gpu_memory.h"
#include "video_core/buffer_cache/copy_ring_scope.h"
#include "common/hash.h"
#include "common/debug.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/amdgpu/pm4_cmds.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/rasterizer_core.h"
#include "bbport_threads.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/texture_cache.h"

// The draw pipeline's vocabulary (vk_draw_pipe.h, vk_draw_prep.h) and the translation helpers.
using Vulkan::DrawPipe;
using Vulkan::ImageDescHash;
using Vulkan::PreparedDraw;
using Vulkan::PreparedStage;
using Vulkan::PreparedVertexInputs;
namespace LiverpoolToVK = Vulkan::LiverpoolToVK;

namespace VideoCore {

RasterizerCore::RasterizerCore(VideoCore::CacheBackend& backend_, VideoCore::GpuQueue& queue_,
                               AmdGpu::Liverpool* liverpool_)
    : backend{backend_}, queue{queue_}, page_manager{this},
      buffer_cache{backend, liverpool_, texture_cache, page_manager},
      texture_cache{backend, liverpool_, buffer_cache, page_manager},
      liverpool{liverpool_}, memory{Core::Memory::Instance()},
      host_markers_enabled{EmulatorSettings.IsVkHostMarkersEnabled()},
      guest_markers_enabled{EmulatorSettings.IsVkGuestMarkersEnabled()} {
    SetSceneServices(nullptr, nullptr, nullptr, nullptr);
}

RasterizerCore::~RasterizerCore() = default;

void RasterizerCore::SetSceneServices(VideoCore::ScenePasses* scene,
                                      VideoCore::UpscalerApi* upscaler_,
                                      VideoCore::CameraMotionApi* camera,
                                      VideoCore::ObjectMotionApi* object) {
    static VideoCore::NullScenePasses null_scene;
    static VideoCore::NullUpscaler null_upscaler;
    static VideoCore::NullCameraMotion null_camera;
    static VideoCore::NullObjectMotion null_object;
    scene_targets = scene ? scene : &null_scene;
    upscaler = upscaler_ ? upscaler_ : &null_upscaler;
    camera_motion = camera ? camera : &null_camera;
    object_motion = object ? object : &null_object;
    // Fixed for the services' lifetime: read once, not through a virtual call per use.
    upscaler_enabled = upscaler->Enabled();
    camera_enabled = camera_motion->Enabled();
}

void RasterizerCore::ShutdownThreads() {
    // The destruction order of the members before the split: the signal thread (last member),
    // then the draw pipe (its recording thread) and what it kept alive.
    if (gpu_signal_thread.joinable()) {
        gpu_signal_thread.request_stop();
        gpu_signal_thread.join();
    }
    draw_pipe.reset();
    pipe_keepalive.clear();
}

bool RasterizerCore::FilterDraw() {
    const auto& regs = Regs();
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::EliminateFastClear) {
        // Clears the render target if FCE is launched before any draws
        EliminateFastClear();
        return false;
    }
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::FmaskDecompress) {
        // TODO: check for a valid MRT1 to promote the draw to the resolve pass.
        LOG_TRACE(Render_Vulkan, "FMask decompression pass skipped");
        ScopedMarkerInsert("FmaskDecompress");
        return false;
    }
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Resolve) {
        LOG_TRACE(Render_Vulkan, "Resolve pass");
        Resolve();
        return false;
    }
    if (regs.primitive_type == AmdGpu::PrimitiveType::None) {
        LOG_TRACE(Render_Vulkan, "Primitive type 'None' skipped");
        ScopedMarkerInsert("PrimitiveTypeNone");
        return false;
    }

    const bool cb_disabled =
        regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Disable;
    const auto depth_copy =
        regs.depth_render_override.force_z_dirty && regs.depth_render_override.force_z_valid &&
        regs.depth_buffer.DepthValid() && regs.depth_buffer.DepthWriteValid() &&
        regs.depth_buffer.DepthAddress() != regs.depth_buffer.DepthWriteAddress();
    const auto stencil_copy =
        regs.depth_render_override.force_stencil_dirty &&
        regs.depth_render_override.force_stencil_valid && regs.depth_buffer.StencilValid() &&
        regs.depth_buffer.StencilWriteValid() &&
        regs.depth_buffer.StencilAddress() != regs.depth_buffer.StencilWriteAddress();
    if (cb_disabled && (depth_copy || stencil_copy)) {
        // Games may disable color buffer and enable force depth/stencil dirty and valid to
        // do a copy from one depth-stencil surface to another, without a pixel shader.
        // We need to detect this case and perform the copy, otherwise it will have no effect.
        LOG_TRACE(Render_Vulkan, "Performing depth-stencil override copy");
        DepthStencilCopy(depth_copy, stencil_copy);
        return false;
    }

    return true;
}

template <typename... Parts>
VideoCore::ImageId RasterizerCore::FindTargetMemoized(VideoCore::TextureCache::ImageDesc& desc,
                                                  LastTarget& last, auto&& make_desc,
                                                  const Parts&... parts) {
    static_assert((sizeof(Parts) + ...) <= 256, "target memo key too large");
    std::array<u8, 256> key{};
    u32 key_size = 0;
    const auto append = [&](const auto& part) {
        std::memcpy(key.data() + key_size, &part, sizeof(part));
        key_size += sizeof(part);
    };
    (append(parts), ...);
    const u64 generation = texture_cache.RegistryGeneration();
    const bool memo_enabled = !BbToggle::Disabled(BbToggle::TextureBindingMemo);
    if (memo_enabled && last.generation == generation && last.key_size == key_size &&
        std::memcmp(last.key.data(), key.data(), key_size) == 0) {
        texture_cache.MarkFound(last.image_id);
        return last.image_id;
    }
    const auto remember = [&](VideoCore::ImageId image_id) {
        last.key = key;
        last.key_size = key_size;
        last.generation = generation;
        last.image_id = image_id;
    };
    u64 hash = 0xCBF29CE484222325ull;
    for (u32 i = 0; i < key_size; i += 8) {
        u64 word;
        std::memcpy(&word, key.data() + i, 8);
        hash = (hash ^ word) * 0x100000001B3ull;
        hash ^= hash >> 29;
    }
    auto& memo = target_memo[hash % target_memo.size()];
    if (memo.generation == generation && memo.key_size == key_size &&
        std::memcmp(memo.key.data(), key.data(), key_size) == 0 && memo_enabled) {
        desc = memo.desc;
        texture_cache.MarkFound(memo.image_id);
        remember(memo.image_id);
        return memo.image_id;
    }
    make_desc();
    const VideoCore::ImageId image_id = texture_cache.FindImage(desc);
    if (generation == texture_cache.RegistryGeneration()) {
        memo.key = key;
        memo.key_size = key_size;
        memo.generation = generation;
        memo.image_id = image_id;
        memo.desc = desc;
        remember(image_id);
    } else {
        last.generation = ~0ULL;
    }
    return image_id;
}

void RasterizerCore::PrepareRenderState(const PipelineBase* pipeline) {
    BB_SECTION(PrepareRenderState);
    // Prefetch render targets to handle overlaps with bound textures (e.g. mipgen)
    const auto& key = pipeline->GetGraphicsKey();
    const auto& regs = Regs();
    if (regs.color_control.degamma_enable) {
        LOG_WARNING(Render_Vulkan, "Color buffers require gamma correction");
    }

    const bool skip_cb_binding =
        regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Disable;
    for (s32 cb = 0; cb < std::bit_width(key.mrt_mask); ++cb) {
        auto& [image_id, desc] = cb_descs[cb];
        const auto& col_buf = regs.color_buffers[cb];
        const u32 target_mask = regs.color_target_mask.GetMask(cb);
        if (skip_cb_binding || !col_buf || !target_mask || (key.mrt_mask & (1 << cb)) == 0) {
            image_id = {};
            continue;
        }
        const auto& hint = CbExtent(cb);
        const u32 tag = 1;
        image_id = bound_images.emplace_back(FindTargetMemoized(
            desc, last_targets[cb], [&] { std::construct_at(&desc, col_buf, hint); }, tag,
            col_buf, hint));
        auto& image = texture_cache.GetImage(image_id);
        image.binding.is_target = 1u;
    }

    if ((regs.depth_control.depth_enable && regs.depth_buffer.DepthValid()) ||
        (regs.depth_control.stencil_enable && regs.depth_buffer.StencilValid())) {
        const auto htile_address = regs.depth_htile_data_base.GetAddress();
        const auto& hint = DbExtent();
        auto& [image_id, desc] = db_desc;
        const u32 tag = 2;
        image_id = bound_images.emplace_back(FindTargetMemoized(
            desc, last_targets[AmdGpu::NUM_COLOR_BUFFERS],
            [&] {
                std::construct_at(&desc, regs.depth_buffer, regs.depth_view, regs.depth_control,
                                  htile_address, hint);
            },
            tag, regs.depth_buffer, regs.depth_view, regs.depth_control, htile_address, hint));
        auto& image = texture_cache.GetImage(image_id);
        image.binding.is_target = 1u;
    } else {
        db_desc.first = {};
    }
    // bbport: the G-buffer pass (5+ color targets) holds the scene depth, and its constants the
    // main camera (shadow passes bind the same layout with the light's camera).
    gbuffer_draw = camera_enabled && std::popcount(key.mrt_mask) >= 5 && db_desc.first;
    if (gbuffer_draw) {
        const auto& vp = regs.viewports[0];
        camera_motion->OnGBufferPass(db_desc.first, vp.xscale < 0.0f ? -1.0f : 1.0f,
                                     vp.yscale < 0.0f ? -1.0f : 1.0f);
    }
    if (upscaler_enabled && cb_descs[0].first) {
        upscaler->OnColorTarget(cb_descs[0].first);
    }
    // bbport: scene color: a full-size RGBA16F target drawn with the scene depth.
    if (upscaler_enabled && db_desc.first && db_desc.first == camera_motion->Depth() &&
        cb_descs[0].first && std::popcount(key.mrt_mask) <= 2) {
        const auto& color = texture_cache.GetImage(cb_descs[0].first);
        const auto& depth = texture_cache.GetImage(db_desc.first);
        if (color.info.pixel_format == vk::Format::eR16G16B16A16Sfloat &&
            color.info.size.width == depth.info.size.width &&
            color.info.size.height == depth.info.size.height) {
            upscaler->OnSceneColor(cb_descs[0].first);
            // Blended geometry without depth writes (not full-screen passes): transparents and
            // effects start. Blended layers writing depth (decals, wet/blood ground) are
            // surfaces and stay in the history.
            if (regs.blend_control[0].enable && !regs.color_buffers[0].info.blend_bypass &&
                !regs.depth_control.depth_write_enable &&
                (regs.num_indices > 6 || regs.num_instances.NumInstances() > 1)) {
                upscaler->OnBlendedSceneDraw();
            } else if (regs.num_indices <= 6 && regs.num_instances.NumInstances() <= 1) {
                // A full-screen pass over the scene (the fog composite): the transparents are in.
                upscaler->OnSceneComposite();
            }
        }
    }
}

void RasterizerCore::EliminateFastClear() {
    auto& col_buf = Regs().color_buffers[0];
    if (!col_buf || !col_buf.info.fast_clear) {
        return;
    }
    VideoCore::TextureCache::ImageDesc desc(col_buf, CbExtent(0));
    const auto image_id = texture_cache.FindImage(desc);
    const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);
    if (!texture_cache.IsMetaCleared(col_buf.CmaskAddress(), col_buf.view.slice_start)) {
        return;
    }
    for (u32 slice = col_buf.view.slice_start; slice <= col_buf.view.slice_max; ++slice) {
        texture_cache.TouchMeta(col_buf.CmaskAddress(), slice, false);
    }
    auto& image = texture_cache.GetImage(image_id);
    const auto clear_value = LiverpoolToVK::ColorBufferClearValue(col_buf);

    ScopeMarkerBegin(fmt::format("EliminateFastClear:MRT={:#x}:M={:#x}", col_buf.Address(),
                                 col_buf.CmaskAddress()));
    backend.ClearImage(&image, desc.view_info.range, clear_value);
    ScopeMarkerEnd();
}

bool RasterizerCore::FilterDrawPasses() const {
    // FilterDraw's checks without its side effects: false when it would skip the draw or run
    // a pass of its own (fast clear elimination, resolve, depth/stencil copy).
    const auto& regs = Regs();
    using Mode = AmdGpu::ColorControl::OperationMode;
    const auto mode = regs.color_control.mode;
    if (mode == Mode::EliminateFastClear || mode == Mode::FmaskDecompress ||
        mode == Mode::Resolve || regs.primitive_type == AmdGpu::PrimitiveType::None) {
        return false;
    }
    const bool depth_copy =
        regs.depth_render_override.force_z_dirty && regs.depth_render_override.force_z_valid &&
        regs.depth_buffer.DepthValid() && regs.depth_buffer.DepthWriteValid() &&
        regs.depth_buffer.DepthAddress() != regs.depth_buffer.DepthWriteAddress();
    const bool stencil_copy =
        regs.depth_render_override.force_stencil_dirty &&
        regs.depth_render_override.force_stencil_valid && regs.depth_buffer.StencilValid() &&
        regs.depth_buffer.StencilWriteValid() &&
        regs.depth_buffer.StencilAddress() != regs.depth_buffer.StencilWriteAddress();
    return !(mode == Mode::Disable && (depth_copy || stencil_copy));
}

// bbport: a draw with no target at all (no color, depth or stencil attachment), no pixel shader
// and no shader writing memory has no effect: the game's depth decompress/resummarize passes
// (full-screen rectangles with the depth block in a special mode, ~10 a frame) arrive like that.
// MoltenVK still ran each through its tessellation emulation for the rectangle in a render pass
// of its own (~1 ms of GPU time each on an M4). BB_SKIP_EMPTY_DRAWS=0 keeps them.
bool RasterizerCore::DrawsNothing(const PipelineBase* pipeline, const RenderState& state) const {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_SKIP_EMPTY_DRAWS");
        return !(env && env[0] == '0');
    }();
    if (!enabled || state.depth_stencil_attachment.has_depth ||
        state.depth_stencil_attachment.has_stencil ||
        pipeline->GetStages()[u32(Shader::SwStage::Fragment)]) {
        return false;
    }
    for (u32 i = 0; i < state.num_color_attachments; ++i) {
        if (state.color_attachments[i].image_view) {
            return false;
        }
    }
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        if (std::ranges::any_of(stage->buffers, [](const auto& b) { return b.is_written; }) ||
            std::ranges::any_of(stage->images, [](const auto& i) { return i.is_written; })) {
            return false;
        }
    }
    static std::atomic<u64> skipped{0};
    if (skipped.fetch_add(1, std::memory_order_relaxed) == 0) {
        std::printf("GPU: draws without any target skipped (BB_SKIP_EMPTY_DRAWS)\n");
    }
    return true;
}

bool RasterizerCore::NoteCopyShaderAccesses(const Shader::Info& cs,
                                        const AmdGpu::ComputeProgram& cs_program) {
    // Three formatted buffers: control (dst index, src index, last element), source and
    // destination; one control entry per thread group (vk_shader_hle.cpp).
    if (cs.buffers.size() < 3) {
        return false;
    }
    const auto ctl = cs.buffers[0].GetSharp(cs);
    const auto src = cs.buffers[1].GetSharp(cs);
    const auto dst = cs.buffers[2].GetSharp(cs);
    const u64 stride = src.GetStride();
    struct Control {
        u32 dst_idx;
        u32 src_idx;
        u32 end;
    };
    const u64 count = cs_program.dim_x;
    if (stride == 0 || stride != dst.GetStride() || ctl.GetStride() != sizeof(Control) ||
        count == 0 || count * sizeof(Control) > ctl.GetSize() ||
        !memory->IsValidGpuMapping(ctl.base_address, count * sizeof(Control))) {
        return false;
    }
    const auto* list = reinterpret_cast<const Control*>(ctl.base_address);
    for (u64 i = 0; i < count; ++i) {
        const Control entry = list[i];
        const u64 size = (u64(entry.end) + 1) * stride;
        if(FrameCapture::NativeRecording()) {
            FrameCapture::NativeBufferUse(0,0,0,size,src.base_address+entry.src_idx*stride,5,cs.pgm_hash,false,"copy_control");
            FrameCapture::NativeBufferUse(0,0,0,size,dst.base_address+entry.dst_idx*stride,5,cs.pgm_hash,true,"copy_control");
        }
        pending_accesses.push_back({src.base_address + entry.src_idx * stride, size, false});
        pending_accesses.push_back({dst.base_address + entry.dst_idx * stride, size, true});
    }
    return true;
}

namespace {
// bbport BB_LAYER_MEMORY: the page table the info collection pass appends to every shader with
// guest buffers is not one of the shader's own (the HLE patterns below count those).
size_t ShaderBufferCount(const Shader::Info& info) {
    size_t count = info.buffers.size();
    if (count != 0 && !info.uses_dma &&
        info.buffers.back().buffer_type == Shader::BufferType::BdaPagetable) {
        --count;
    }
    return count;
}
} // namespace

bool RasterizerCore::IsComputeMetaClear(const PipelineBase* pipeline) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Most of the time when a metadata is updated with a shader it gets cleared. It means
    // we can skip the whole dispatch and update the tracked state instead. Also, it is not
    // intended to be consumed and in such rare cases (e.g. HTile introspection, CRAA) we
    // will need its full emulation anyways.
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);

    // Assume if a shader reads metadata, it is a copy shader.
    for (const auto& desc : info.buffers) {
        const VAddr address = desc.GetSharp(info).base_address;
        if (!desc.IsSpecial() && !desc.is_written && texture_cache.IsMeta(address)) {
            return false;
        }
    }

    // Metadata surfaces are tiled and thus need address calculation to be written properly.
    // If a shader wants to encode HTILE, for example, from a depth image it will have to compute
    // proper tile address from dispatch invocation id. This address calculation contains an xor
    // operation so use it as a heuristic for metadata writes that are probably not clears.
    if (!info.has_bitwise_xor) {
        // Assume if a shader writes metadata without address calculation, it is a clear shader.
        for (const auto& desc : info.buffers) {
            const VAddr address = desc.GetSharp(info).base_address;
            if (!desc.IsSpecial() && desc.is_written && texture_cache.ClearMeta(address)) {
                // Assume all slices were updates
                LOG_TRACE(Render_Vulkan, "Metadata update skipped");
                return true;
            }
        }
    }
    return false;
}

bool RasterizerCore::IsComputeImageCopy(const PipelineBase* pipeline, bool dry_run) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Ensure shader only has 2 bound buffers
    const auto& cs_pgm = CsRegs();
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs_pgm.num_thread_x.full != 64 || ShaderBufferCount(info) != 2 || !info.images.empty()) {
        return false;
    }

    // Those 2 buffers must both be formatted. One must be source and another destination.
    const auto& desc0 = info.buffers[0];
    const auto& desc1 = info.buffers[1];
    if (!desc0.is_formatted || !desc1.is_formatted || desc0.is_written == desc1.is_written) {
        return false;
    }

    // Buffers must have the same size and each thread of the dispatch must copy 1 dword of data
    const AmdGpu::Buffer buf0 = desc0.GetSharp(info);
    const AmdGpu::Buffer buf1 = desc1.GetSharp(info);
    if (buf0.GetSize() != buf1.GetSize() || cs_pgm.dim_x != (buf0.GetSize() / 256)) {
        return false;
    }

    // Find images the buffer alias
    const auto image0_id = texture_cache.FindImageFromRange(buf0.base_address, buf0.GetSize());
    if (!image0_id) {
        return false;
    }
    const auto image1_id =
        texture_cache.FindImageFromRange(buf1.base_address, buf1.GetSize(), false);
    if (!image1_id) {
        return false;
    }

    // Image copy must be valid
    VideoCore::Image& image0 = texture_cache.GetImage(image0_id);
    VideoCore::Image& image1 = texture_cache.GetImage(image1_id);
    if (image0.info.guest_size != image1.info.guest_size ||
        image0.info.pitch != image1.info.pitch || image0.info.guest_size != buf0.GetSize() ||
        image0.info.num_bits != image1.info.num_bits) {
        return false;
    }

    // Perform image copy
    VideoCore::Image& src_image = desc0.is_written ? image1 : image0;
    VideoCore::Image& dst_image = desc0.is_written ? image0 : image1;
    // bbport BB_IMAGE_COPY_LOG=1 (diagnostics): the copies this recognizes.
    static const bool copy_log = std::getenv("BB_IMAGE_COPY_LOG") != nullptr;
    if (copy_log) {
        static std::atomic<u32> printed{0};
        if (printed.fetch_add(1) < 40) {
            std::printf("Image copy: %#llx (%ux%u fmt %u flags %#x) -> %#llx (%ux%u fmt %u flags "
                        "%#x), %u bytes\n",
                        (unsigned long long)src_image.info.guest_address, src_image.info.size.width,
                        src_image.info.size.height, u32(src_image.info.pixel_format),
                        u32(src_image.flags), (unsigned long long)dst_image.info.guest_address,
                        dst_image.info.size.width, dst_image.info.size.height,
                        u32(dst_image.info.pixel_format), u32(dst_image.flags),
                        u32(buf0.GetSize()));
        }
    }
    if (dry_run) {
        return false;
    }
    backend.CopyColorAndDepth(&src_image, &dst_image);
    return true;
}

bool RasterizerCore::IsComputeImageClear(const PipelineBase* pipeline) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Ensure shader only has 2 bound buffers
    const auto& cs_pgm = CsRegs();
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs_pgm.num_thread_x.full != 64 || ShaderBufferCount(info) != 2 || !info.images.empty()) {
        return false;
    }

    // From those 2 buffers, first must hold the clear vector and second the image being cleared
    const auto& desc0 = info.buffers[0];
    const auto& desc1 = info.buffers[1];
    if (desc0.is_formatted || !desc1.is_formatted || desc0.is_written || !desc1.is_written) {
        return false;
    }

    // First buffer must have size of vec4 and second the size of a single layer
    const AmdGpu::Buffer buf0 = desc0.GetSharp(info);
    const AmdGpu::Buffer buf1 = desc1.GetSharp(info);
    const u32 buf1_bpp = AmdGpu::NumBitsPerBlock(buf1.GetDataFmt());
    if (buf0.GetSize() != 16 || (cs_pgm.dim_x * 128ULL * (buf1_bpp / 8)) != buf1.GetSize()) {
        return false;
    }

    // Find image the buffer alias
    const auto image1_id =
        texture_cache.FindImageFromRange(buf1.base_address, buf1.GetSize(), false);
    if (!image1_id) {
        return false;
    }

    // Image clear must be valid
    VideoCore::Image& image1 = texture_cache.GetImage(image1_id);
    if (image1.info.guest_size != buf1.GetSize() || image1.info.num_bits != buf1_bpp ||
        image1.info.props.is_depth) {
        return false;
    }

    // Perform image clear
    const float* values = reinterpret_cast<float*>(buf0.base_address);
    const vk::ClearValue clear = {
        .color = {.float32 = std::array<float, 4>{values[0], values[1], values[2], values[3]}},
    };
    const VideoCore::SubresourceRange range = {
        .base =
            {
                .level = 0,
                .layer = 0,
            },
        .extent = image1.info.resources,
    };
    backend.ClearImage(&image1, range, clear);
    return true;
}

RasterizerCore::ImageDescCacheEntry& RasterizerCore::CachedImageDescEntry(const AmdGpu::Image& sharp,
                                                                   const Shader::ImageResource& res,
                                                                   u64 hash) {
    std::array<u64, 4> key;
    std::memcpy(key.data(), &sharp, sizeof(key));
    const u32 flags = u32(res.is_written) | u32(res.is_depth) << 1 | u32(res.is_array) << 2;
    // Two-way set associative: a frame binds a few thousand distinct T#s.
    auto* set = &image_desc_cache[(hash % (image_desc_cache.size() / 2)) * 2];
    const auto matches = [&](const ImageDescCacheEntry& e) {
        return e.flags == flags && e.sharp == key;
    };
    const bool disabled = BbToggle::Disabled(BbToggle::ImageDescCache);
    ImageDescCacheEntry* slot = nullptr;
    if (!disabled && matches(set[0])) {
        slot = &set[0];
    } else if (!disabled && matches(set[1])) {
        slot = &set[1];
    } else {
        // Replace the least recently used way that no binding of this call points at.
        slot = set[0].last_use <= set[1].last_use ? &set[0] : &set[1];
        if (slot->pinned == bind_epoch) {
            slot = slot == &set[0] ? &set[1] : &set[0];
        }
        if (slot->pinned == bind_epoch) {
            slot = &image_desc_overflow.emplace_back();
        }
    }
    slot->last_use = ++desc_use_counter;
    auto& entry = *slot;
    if (entry.flags != flags || entry.sharp != key ||
        BbToggle::Disabled(BbToggle::ImageDescCache)) {
        entry.desc = VideoCore::TextureCache::ImageDesc{sharp, res};
        entry.sharp = key;
        entry.flags = flags;
        entry.found_generation = ~0ULL;
        entry.view_memo = {};
    }
    return entry;
}

// bbport: the PS4 build samples most scene textures with little or no anisotropic filtering.
// Behind the temporal upscaler the output shows the detail of the output's mip level, and
// surfaces seen at a grazing angle (stairs, floors, walls along the view) stay blurred.
// Mipmapped linear samplers of graphics draws get BB_ANISO (16; 0 = the game's) times.
void RasterizerCore::ForceAnisotropy(AmdGpu::Sampler& sharp, bool is_depth) const {
    static const int forced = [] {
        const char* value = std::getenv("BB_ANISO");
        return value ? std::atoi(value) : 16;
    }();
    if (!is_depth && !pipeline_is_compute) {
        // The game's filters, once per kind: min filter, mip filter, ratio.
        static std::atomic<u64> seen{0};
        const u32 kind = u32(sharp.xy_min_filter.Value()) * 3 * 5 + u32(sharp.mip_filter.Value()) * 5 +
                         u32(sharp.max_aniso.Value());
        if (kind < 64 && !(seen.fetch_or(1ull << kind) & (1ull << kind))) {
            std::printf("Sampler: game uses min filter %u, mip filter %u, anisotropy %.0fx\n",
                        u32(sharp.xy_min_filter.Value()), u32(sharp.mip_filter.Value()),
                        sharp.MaxAniso());
        }
    }
    if (forced <= 1 || is_depth || pipeline_is_compute || BbToggle::Disabled(BbToggle::ForcedAniso) ||
        sharp.mip_filter == AmdGpu::MipFilter::None ||
        (sharp.xy_min_filter != AmdGpu::Filter::Bilinear &&
         sharp.xy_min_filter != AmdGpu::Filter::AnisoLinear)) {
        return;
    }
    const auto ratio = forced >= 16 ? AmdGpu::AnisoRatio::Sixteen
        : forced >= 8 ? AmdGpu::AnisoRatio::Eight
        : forced >= 4 ? AmdGpu::AnisoRatio::Four : AmdGpu::AnisoRatio::Two;
    if (AmdGpu::IsAnisoFilter(sharp.xy_min_filter) && sharp.MaxAniso() >= forced) {
        return;
    }
    sharp.xy_min_filter.Assign(AmdGpu::Filter::AnisoLinear);
    if (sharp.xy_mag_filter == AmdGpu::Filter::Bilinear) {
        sharp.xy_mag_filter.Assign(AmdGpu::Filter::AnisoLinear);
    }
    sharp.max_aniso.Assign(ratio);
}

RasterizerCore::BeginSignature RasterizerCore::MakeBeginSignature(const PipelineBase* pipeline) const {
    const auto& key = pipeline->GetGraphicsKey();
    const auto& regs = Regs();
    BeginSignature sig;
    const u32 num_color = std::bit_width(key.mrt_mask);
    for (u32 cb = 0; cb < num_color && cb < AmdGpu::NUM_COLOR_BUFFERS; ++cb) {
        sig.ids[cb] = cb_descs[cb].first;
        if (cb_descs[cb].first) {
            sig.views[cb] = cb_descs[cb].second.view_info;
        }
        sig.color_samples[cb] = key.color_samples[cb];
    }
    sig.ids[AmdGpu::NUM_COLOR_BUFFERS] = db_desc.first;
    if (db_desc.first) {
        sig.views[AmdGpu::NUM_COLOR_BUFFERS] = db_desc.second.view_info;
    }
    sig.mrt_mask = key.mrt_mask;
    sig.num_samples = key.num_samples;
    sig.motion = key.motion_vectors;
    sig.scene_started = scene_started;
    sig.raster_scaling = upscaler->RasterScaling();
    sig.upscaler_state = upscaler->RedirectState();
    sig.generation = texture_cache.RegistryGeneration();
    std::memcpy(&sig.depth_control, &regs.depth_control, sizeof(u32));
    sig.depth_valid = regs.depth_buffer.DepthValid();
    sig.stencil_valid = regs.depth_buffer.StencilValid();
    return sig;
}

RenderState RasterizerCore::BeginRendering(const PipelineBase* pipeline) {
    BB_SECTION(BeginRendering);
    // bbport: RenderStateMemo (see BeginMemo).
    const bool memo_on = !BbToggle::Disabled(BbToggle::RenderStateMemo) &&
                         (!FrameCapture::Active() || FrameCapture::TextureBindingsEnabled());
    if (BbStats::enabled && ((begin_memo_hits + begin_memo_misses) & 0x3FFFF) == 0x3FFFF) {
        std::printf("Render state memo: %llu hits, %llu misses; texture sets: %llu hits, %llu "
                    "misses\n",
                    static_cast<unsigned long long>(begin_memo_hits),
                    static_cast<unsigned long long>(begin_memo_misses),
                    static_cast<unsigned long long>(texture_set_hits),
                    static_cast<unsigned long long>(texture_set_misses));
        std::printf("  texture set misses: other key %llu, generation %llu, image check %llu, "
                    "other %llu\n",
                    (unsigned long long)texture_set_why[0], (unsigned long long)texture_set_why[1],
                    (unsigned long long)texture_set_why[2], (unsigned long long)texture_set_why[3]);
        std::printf("  scene proxies sampled directly: %llu bindings\n",
                    static_cast<unsigned long long>(proxy_samples));
        proxy_samples = 0;
        texture_set_why = {};
        begin_memo_hits = begin_memo_misses = texture_set_hits = texture_set_misses = 0;
    }
    BeginSignature signature;
    if (memo_on) {
        signature = MakeBeginSignature(pipeline);
        const auto& regs = Regs();
        bool usable = begin_memo.valid && queue.IsRenderingWith(begin_memo.state) &&
                      !regs.depth_render_control.depth_clear_enable &&
                      !regs.depth_render_control.stencil_clear_enable &&
                      signature == begin_memo.signature;
        for (const auto id : signature.ids) {
            if (usable && id) {
                const auto& image = texture_cache.GetImage(id);
                usable = !image.binding.is_bound && !image.binding.needs_rebind;
            }
        }
        if (usable) {
            ++begin_memo_hits;
            attachment_feedback_loop = false;
            push_data.scene_size = begin_memo.scene_size;
            target_scale = begin_memo.target_scale;
            return begin_memo.state;
        }
        ++begin_memo_misses;
    }
    begin_memo.valid = false;
    RenderState state = BeginRenderingFull(pipeline);
    if (memo_on && !attachment_feedback_loop) {
        // (is_clear of the depth attachment shares its bytes with has_depth.)
        bool clears = state.depth_stencil_attachment.depth_clear ||
                      state.depth_stencil_attachment.stencil_clear;
        for (u32 i = 0; i < state.num_color_attachments; ++i) {
            clears |= state.color_attachments[i].is_clear != 0;
        }
        if (!clears) {
            // Recomputed after the full path: it may have started the scene (scene_started).
            begin_memo = {true, MakeBeginSignature(pipeline), state, push_data.scene_size,
                          target_scale};
        }
    }
    return state;
}

RenderState RasterizerCore::BeginRenderingFull(const PipelineBase* pipeline) {
    attachment_feedback_loop = false;
    VideoCore::UpscalerTarget redirect{};
    bool color_redirected = false, depth_redirected = false;
    std::pair<u32, u32> guest_extent{1, 1};
    std::pair<VideoCore::NativeView, vk::ImageLayout> original_color{};
    const auto& regs = Regs();
    const auto& key = pipeline->GetGraphicsKey();
    if (std::popcount(key.mrt_mask & 0x7f) >= 5 && db_desc.first &&
        scene_targets->EligibleScene(texture_cache.GetImage(db_desc.first))) scene_started = true;
    // A proxy attachment cannot represent MSAA or a feedback loop that reads the
    // same image through the guest's native descriptor during this draw.
    bool reduced = scene_started && upscaler->RasterScaling() && key.num_samples == 1;
    // BB_SCENE_DEBUG=<file>: once the file exists, why the passes of one frame keep the
    // native size (see NoteFrameStart).
    const bool debug_pass = scene_debug_frame && scene_started;
    std::string why;
    if (debug_pass) {
        why = fmt::format("pass vs {:08x} ps {:08x} mrt {:#x} samples {} raster_scaling {}:",
                          pipeline->GetStage(Shader::SwStage::Vertex).pgm_hash,
                          key.mrt_mask ? pipeline->GetStage(Shader::SwStage::Fragment).pgm_hash
                                       : 0,
                          key.mrt_mask, u32(key.num_samples), upscaler->RasterScaling());
    }
    // Only this pass's attachments: slots past the mask's width keep earlier passes' targets,
    // often the G-buffer images the lighting passes sample (bound), which kept those native.
    const u32 num_attachments = BbToggle::Disabled(BbToggle::SceneAttachmentsOnly)
                                    ? u32(cb_descs.size())
                                    : u32(std::bit_width(key.mrt_mask));
    // All attachments of a reduced pass share one proxy size (scene or half resolution).
    std::optional<SceneResolution::Size> pass_size;
    const auto same_size = [&](const VideoCore::Image& image, u32 level) {
        const auto proxy = scene_targets->ProxySize(image, level);
        if (!pass_size) pass_size = proxy;
        return *pass_size == proxy;
    };
    for (u32 cb = 0; cb < num_attachments; ++cb) {
        const auto& [id, desc] = cb_descs[cb];
        if (id) {
            const auto& image = texture_cache.GetImage(id);
            // Mip levels of eligible chains have their own proxies (SceneTargets::Get).
            if (!scene_targets->Eligible(image) || image.binding.is_bound ||
                image.binding.needs_rebind || desc.view_info.range.base.layer ||
                !same_size(image, desc.view_info.range.base.level)) reduced = false;
            if (debug_pass) {
                why += fmt::format(" [{} {}x{} eligible {} bound {} rebind {} level {} layer {}]",
                                   vk::to_string(image.info.pixel_format), image.info.size.width,
                                   image.info.size.height, scene_targets->Eligible(image),
                                   u32(image.binding.is_bound), u32(image.binding.needs_rebind),
                                   desc.view_info.range.base.level,
                                   desc.view_info.range.base.layer);
            }
        }
    }
    if (db_desc.first && (!scene_targets->Eligible(texture_cache.GetImage(db_desc.first)) ||
                          !same_size(texture_cache.GetImage(db_desc.first), 0))) {
        reduced = false;
    }
    if (debug_pass) {
        if (db_desc.first) {
            const auto& depth = texture_cache.GetImage(db_desc.first);
            why += fmt::format(" depth [{} {}x{} eligible {}]",
                               vk::to_string(depth.info.pixel_format), depth.info.size.width,
                               depth.info.size.height, scene_targets->Eligible(depth));
        }
        std::printf("Scene pass: reduced %d %s\n", reduced, why.c_str());
    }
    push_data.scene_size = reduced ? SceneResolution::Pack(scene_targets->Size()) : 0;
    if (BbStats::enabled) {
        BbStats::reduced_draws.fetch_add(reduced, std::memory_order_relaxed);
        BbStats::scene_draws.fetch_add(scene_started, std::memory_order_relaxed);
    }
    RenderTargets targets;
    targets.width = backend.Limits().max_framebuffer_width;
    targets.height = backend.Limits().max_framebuffer_height;
    targets.num_layers = std::numeric_limits<u16>::max();
    targets.num_color_attachments = std::bit_width(key.mrt_mask);
    for (auto cb = 0u; cb < targets.num_color_attachments; ++cb) {
        auto& [image_id, desc] = cb_descs[cb];
        if (!image_id) {
            targets.color_attachments[cb] = {};
            continue;
        }
        auto* image = &texture_cache.GetImage(image_id);
        if (image->binding.needs_rebind) {
            last_targets[cb].generation = ~0ULL; // FindImage may rewrite the description
            image_id = bound_images.emplace_back(texture_cache.FindImage(desc));
            image = &texture_cache.GetImage(image_id);
        }
        texture_cache.UpdateImage(image_id);
        backend.SetBackingSamples(image, key.color_samples[cb]);
        const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);
        const auto view_id = ViewIdOf(*image, image_view);
        const auto slice = image_view.info.range.base.layer;
        const auto mip = image_view.info.range.base.level;

        const auto& col_buf = regs.color_buffers[cb];
        const bool is_clear = texture_cache.IsMetaCleared(col_buf.CmaskAddress(), slice);
        texture_cache.TouchMeta(col_buf.CmaskAddress(), slice, false);

        if (reduced) {
            // The native image is resolved lazily when a shader/copy actually reads it.
        } else if (image->binding.is_bound) {
            ASSERT_MSG(!image->binding.force_general,
                       "Having image both as storage and render target is unsupported");
            backend.FlushBarriers();
            needs_barrier |=
                backend.Transit(image,
                                backend.Limits().attachment_feedback_loop_layout
                                    ? vk::ImageLayout::eAttachmentFeedbackLoopOptimalEXT
                                    : vk::ImageLayout::eGeneral,
                                vk::PipelineStageFlagBits2::eColorAttachmentOutput,
                                vk::AccessFlagBits2::eColorAttachmentWrite |
                                    vk::AccessFlagBits2::eColorAttachmentRead);
            attachment_feedback_loop = true;
        } else {
            needs_barrier |= backend.Transit(image, vk::ImageLayout::eColorAttachmentOptimal,
                                             vk::PipelineStageFlagBits2::eColorAttachmentOutput,
                                             vk::AccessFlagBits2::eColorAttachmentWrite |
                                                 vk::AccessFlagBits2::eColorAttachmentRead,
                                             desc.view_info.range);
        }

        targets.width = std::min<u32>(targets.width, std::max(image->info.size.width >> mip, 1u));
        targets.height = std::min<u32>(targets.height, std::max(image->info.size.height >> mip, 1u));
        targets.num_layers = std::min<u32>(targets.num_layers, image_view.info.range.extent.layers);

        const auto clear_value =
            is_clear ? LiverpoolToVK::ColorBufferClearValue(col_buf) : vk::ClearValue{};
        auto& attachment = targets.color_attachments[cb];
        attachment.view = view_id;
        attachment.layout = image->backing->state.layout;
        attachment.clear_value = clear_value.color.uint32;
        attachment.is_clear = is_clear;

        if (reduced) {
            // All reduced attachments were checked to share a complete proxy extent.
            // Only pre-UI full-color clears opt in: no redirected UI/display surface can
            // replace this proxy. Metal loadActionClear covers it regardless of scissors.
            const bool discard_color = is_clear && !(upscaler->RedirectState() & 3u);
            const auto target = discard_color
                ? scene_targets->ClearColorAttachment(image_id, desc.view_info)
                : scene_targets->Attachment(image_id, desc.view_info);
            attachment.replacement_view = target.view;
            attachment.replacement_layout = target.layout;
        }
        image->usage.render_target = 1u;
        if (cb == 0 && upscaler_enabled) {
            color_redirected = upscaler->RedirectColor(image_id, desc.view_info, redirect);
            if (color_redirected) {
                guest_extent = {image->info.size.width, image->info.size.height};
                original_color = {attachment.replacement_view, attachment.replacement_layout};
                attachment.replacement_view = redirect.view;
                attachment.replacement_layout = redirect.layout;
            }
        }
    }
    for (u32 cb = targets.num_color_attachments; cb < targets.color_attachments.size(); ++cb) {
        targets.color_attachments[cb] = {};
    }

    if (auto image_id = db_desc.first; image_id) {
        auto& desc = db_desc.second;
        const auto htile_address = regs.depth_htile_data_base.GetAddress();
        const auto& image_view = texture_cache.FindDepthTarget(image_id, desc);
        auto& image = texture_cache.GetImage(image_id);

        const auto slice = image_view.info.range.base.layer;
        const bool is_depth_clear =
            (regs.depth_render_control.depth_clear_enable && regs.depth_control.depth_enable &&
             regs.depth_control.depth_write_enable) ||
            texture_cache.IsMetaCleared(htile_address, slice);
        const bool is_stencil_clear = regs.depth_render_control.stencil_clear_enable;
        texture_cache.TouchMeta(htile_address, slice, false);
        ASSERT(desc.view_info.range.extent.levels == 1 && !image.binding.needs_rebind);

        const bool has_stencil = image.info.props.has_stencil;
        // Stencil writes can be enabled while depth writes are off.
        const bool stencil_write =
            has_stencil && regs.depth_control.stencil_enable && !desc.view_info.is_storage;
        const auto new_layout = desc.view_info.is_storage
                                    ? has_stencil ? vk::ImageLayout::eDepthStencilAttachmentOptimal
                                                  : vk::ImageLayout::eDepthAttachmentOptimal
                                : stencil_write
                                    ? vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal
                                : has_stencil ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                                              : vk::ImageLayout::eDepthReadOnlyOptimal;
        if (!reduced) needs_barrier |= backend.Transit(&image, new_layout,
                                         vk::PipelineStageFlagBits2::eEarlyFragmentTests |
                                             vk::PipelineStageFlagBits2::eLateFragmentTests,
                                         vk::AccessFlagBits2::eDepthStencilAttachmentWrite |
                                             vk::AccessFlagBits2::eDepthStencilAttachmentRead,
                                         desc.view_info.range);

        targets.width = std::min<u32>(targets.width, image.info.size.width);
        targets.height = std::min<u32>(targets.height, image.info.size.height);
        targets.num_layers = std::min<u32>(targets.num_layers, image_view.info.range.extent.layers);

        auto& attachment = targets.depth_stencil_attachment;
        attachment.view = ViewIdOf(image, image_view);
        attachment.layout = image.backing->state.layout;
        attachment.clear_value = {};

        if (regs.depth_buffer.DepthValid()) {
            attachment.clear_value[0] = is_depth_clear ? std::bit_cast<u32>(regs.depth_clear) : 0u;
            attachment.has_depth = true;
            attachment.depth_clear = is_depth_clear;
        }
        if (regs.depth_buffer.StencilValid()) {
            attachment.clear_value[1] = is_stencil_clear ? regs.stencil_clear : 0u;
            attachment.has_stencil = true;
            attachment.stencil_clear = is_stencil_clear;
        }

        if (reduced) {
            const auto target = scene_targets->Attachment(image_id, desc.view_info);
            attachment.replacement_view = target.view;
            attachment.replacement_layout = target.layout;
        }
        image.usage.depth_target = true;
        if (upscaler_enabled) {
            VideoCore::UpscalerTarget depth_redirect;
            depth_redirected = upscaler->RedirectDepth(image_id, depth_redirect);
            if (depth_redirected) {
                guest_extent = {image.info.size.width, image.info.size.height};
                attachment.replacement_view = depth_redirect.view;
                attachment.replacement_layout = depth_redirect.layout;
                redirect = depth_redirect;
            }
        }
    } else {
        targets.depth_stencil_attachment = {};
    }

    // bbport: a pass drawn into the upscaler's output-size images (UI, display pass): every
    // attachment must be redirected, viewports and scissors are scaled.
    target_scale = {1.0f, 1.0f};
    if (color_redirected || depth_redirected) {
        u32 color_targets = 0;
        for (u32 cb = 0; cb < targets.num_color_attachments; ++cb) {
            color_targets += targets.color_attachments[cb].view ||
                             targets.color_attachments[cb].replacement_view ? 1 : 0;
        }
        const bool has_depth = bool(db_desc.first);
        if ((color_targets > 0 && !color_redirected) || (has_depth && !depth_redirected) ||
            color_targets > 1) {
            static u32 warned = 0;
            if (warned < 8) {
                ++warned;
                const auto describe = [&](VideoCore::ImageId id) {
                    if (!id) {
                        return std::string{"-"};
                    }
                    const auto& info = texture_cache.GetImage(id).info;
                    return fmt::format("{:#x} {}x{} {}", info.guest_address, info.size.width,
                                       info.size.height, vk::to_string(info.pixel_format));
                };
                std::printf("Upscaler: pass not redirected (color %s: %s, depth %s: %s, %u "
                            "targets)\n",
                            color_redirected ? "yes" : "no", describe(cb_descs[0].first).c_str(),
                            depth_redirected ? "yes" : "no", describe(db_desc.first).c_str(),
                            targets.num_color_attachments);
            }
            if (color_redirected) {
                targets.color_attachments[0].replacement_view = original_color.first;
                targets.color_attachments[0].replacement_layout = original_color.second;
            }
            if (depth_redirected) {
                auto& depth_image = texture_cache.GetImage(db_desc.first);
                const auto& depth_view =
                    texture_cache.FindDepthTarget(db_desc.first, db_desc.second);
                targets.depth_stencil_attachment.view = ViewIdOf(depth_image, depth_view);
                targets.depth_stencil_attachment.layout = depth_image.backing->state.layout;
                targets.depth_stencil_attachment.replacement_view = VideoCore::NativeView{};
                targets.depth_stencil_attachment.replacement_layout = vk::ImageLayout{};
            }
        } else {
            targets.width = redirect.width;
            targets.height = redirect.height;
            // The UI movie viewport is now 1920x1080 even when the scene is 960x540.
            // A native viewport (or native window-space UI vertices) must not be doubled
            // again. The final display copy still uses the guest render-size coordinates.
            const auto& viewport = regs.viewports[0];
            const bool native_coordinates = redirect.native_ui &&
                (UiComposition::NativeViewport(viewport.xscale * 2, viewport.yscale * 2) ||
                 (regs.IsClipDisabled() && !regs.viewport_control.xscale_enable &&
                  !regs.viewport_control.yscale_enable));
            target_scale = UiComposition::Scale(guest_extent.first, guest_extent.second,
                                                redirect.width, redirect.height,
                                                native_coordinates);
        }
    }

    if (targets.num_layers == std::numeric_limits<u16>::max()) {
        targets.num_layers = 1;
    }

    if (reduced) {
        // Half-resolution passes keep the scene's factor (proxy / native = scene / 1920).
        const auto size = scene_targets->Size();
        const auto proxy = pass_size.value_or(size);
        targets.width = proxy.width;
        targets.height = proxy.height;
        target_scale = {float(size.width) / 1920.0f, float(size.height) / 1080.0f};
    }
#ifdef __APPLE__
    // bbport (MoltenVK): a pass without attachments covered the largest framebuffer (16384x16384),
    // and Metal backs such a pass with a default render target of that size (~30 ms a frame on
    // an M4). Its draws cannot reach past the screen scissor.
    if (targets.width == backend.Limits().max_framebuffer_width &&
        targets.height == backend.Limits().max_framebuffer_height) {
        const auto& scissor = regs.screen_scissor;
        targets.width = std::clamp<u32>(u32(AmdGpu::Scissor::Clamp(scissor.bottom_right_x)), 1u,
                                      targets.width);
        targets.height = std::clamp<u32>(u32(AmdGpu::Scissor::Clamp(scissor.bottom_right_y)), 1u,
                                       targets.height);
    }
#endif
    if (FrameCapture::Active()) {
        std::array<const VideoCore::ImageInfo*, AmdGpu::NUM_COLOR_BUFFERS> colors{};
        for (u32 cb = 0; cb < targets.num_color_attachments; ++cb) {
            if (cb_descs[cb].first) {
                colors[cb] = &texture_cache.GetImage(cb_descs[cb].first).info;
            }
        }
        const auto* depth =
            db_desc.first ? &texture_cache.GetImage(db_desc.first).info : nullptr;
        FrameCapture::BeginPass(colors.data(), targets.num_color_attachments, depth);
    }
    RenderState state = BuildRenderState(targets);
    // bbport: object motion vector attachment of G-buffer pipelines.
    if (key.motion_vectors) {
        object_motion->Attach(state, state.width, state.height);
    }
    return state;
}

void RasterizerCore::Resolve() {
    const auto& mrt0_hint = CbExtent(0);
    const auto& mrt1_hint = CbExtent(1);
    VideoCore::TextureCache::ImageDesc mrt0_desc{Regs().color_buffers[0], mrt0_hint};
    VideoCore::TextureCache::ImageDesc mrt1_desc{Regs().color_buffers[1], mrt1_hint};
    auto& mrt0_image = texture_cache.GetImage(texture_cache.FindImage(mrt0_desc, true));
    auto& mrt1_image = texture_cache.GetImage(texture_cache.FindImage(mrt1_desc, true));

    ScopeMarkerBegin(fmt::format("Resolve:MRT0={:#x}:MRT1={:#x}",
                                 Regs().color_buffers[0].Address(),
                                 Regs().color_buffers[1].Address()));
    backend.ResolveImage(&mrt0_image, &mrt1_image, mrt0_desc.view_info.range,
                         mrt1_desc.view_info.range);
    ScopeMarkerEnd();
}

void RasterizerCore::DepthStencilCopy(bool is_depth, bool is_stencil) {
    auto& regs = Regs();

    auto read_desc = VideoCore::TextureCache::ImageDesc(
        regs.depth_buffer, regs.depth_view, regs.depth_control,
        regs.depth_htile_data_base.GetAddress(), DbExtent(), false);
    auto write_desc = VideoCore::TextureCache::ImageDesc(
        regs.depth_buffer, regs.depth_view, regs.depth_control,
        regs.depth_htile_data_base.GetAddress(), DbExtent(), true);

    auto& read_image = texture_cache.GetImage(texture_cache.FindImage(read_desc));
    auto& write_image = texture_cache.GetImage(texture_cache.FindImage(write_desc));

    VideoCore::SubresourceRange sub_range;
    sub_range.base.layer = Regs().depth_view.slice_start;
    sub_range.extent.layers = Regs().depth_view.NumSlices() - sub_range.base.layer;

    ScopeMarkerBegin(fmt::format(
        "DepthStencilCopy:DR={:#x}:SR={:#x}:DW={:#x}:SW={:#x}", regs.depth_buffer.DepthAddress(),
        regs.depth_buffer.StencilAddress(), regs.depth_buffer.DepthWriteAddress(),
        regs.depth_buffer.StencilWriteAddress()));

    backend.CopyDepthStencil(&read_image, &write_image, sub_range);

    ScopeMarkerEnd();
}

void RasterizerCore::NoteFrameStart() {
    ProfilerBeginFrame();
    const u64 frame = BbStats::gpu_frames.fetch_add(1, std::memory_order_relaxed) + 1;
    if(FrameCapture::NativeMode()&&FrameCapture::Active()) {
        backend.EndRendering(); // drain held/replayed render metadata before changing frame
        FrameCapture::FrameBoundary(frame);
    }
    // BB_BUFFER_STATS=1: how many earlier frames the GPU has not finished when the GPU thread
    // starts a frame — the lag that decides whether guest memory can be read in place.
    static const bool stats = [] {
        const char* value = std::getenv("BB_BUFFER_STATS");
        return value && value[0] == '1';
    }();
    if (!stats) {
        return;
    }
    static std::array<u64, 16> frame_ticks{};
    static std::array<u64, 17> lag_histogram{};
    u32 lag = 0;
    for (u32 back = 1; back < frame_ticks.size() && back < frame; ++back) {
        const u64 tick = frame_ticks[(frame - back) % frame_ticks.size()];
        if (tick && !backend.IsFree(tick)) {
            lag = back;
        }
    }
    ++lag_histogram[lag];
    frame_ticks[frame % frame_ticks.size()] = backend.CurrentTick();
    if (frame % 600 == 0) {
        std::printf("GPU lag at frame start (frames not finished: count):");
        for (u32 i = 0; i < lag_histogram.size(); ++i) {
            if (lag_histogram[i]) {
                std::printf(" %u:%llu", i, static_cast<unsigned long long>(lag_histogram[i]));
            }
        }
        std::printf("\n");
        lag_histogram = {};
    }
}

// Awakenchan's BB_SAMPLER_MEMO: avoid repeated hashes, locks and map lookups in stage B.
SamplerRef RasterizerCore::CachedSampler(const AmdGpu::Sampler& sharp, bool is_depth,
                                         float lod_bias) {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_SAMPLER_MEMO");
        return !(env && env[0] == '0');
    }();
    if (!enabled) {
        return texture_cache.GetSampler(sharp, Regs().ta_bc_base, is_depth, lod_bias);
    }
    SamplerMemo<SamplerRef>::Key key;
    static_assert(sizeof(sharp) == sizeof(key.sharp));
    std::memcpy(key.sharp.data(), &sharp, sizeof(sharp));
    key.variant = u64(is_depth) << 32 | std::bit_cast<u32>(lod_bias);
    return sampler_memo.Get(key, texture_cache.SamplerGeneration(), texture_cache.GcTick(),
        [&](size_t& lru_id) {
            return texture_cache.GetSampler(sharp, Regs().ta_bc_base, is_depth, lod_bias, &lru_id);
        }, [&](size_t lru_id) { texture_cache.TouchSampler(lru_id); });
}

void RasterizerCore::BindSamplers(const Shader::Info& stage, const PreparedStage* prepared,
                                  Shader::Backend::Bindings& binding, u32& write_index) {
    boost::container::static_vector<VideoCore::SamplerRef, Shader::NUM_SAMPLERS> samplers;
    for (u32 sampler_index = 0; sampler_index < stage.samplers.size(); ++sampler_index) {
        const auto& sampler = stage.samplers[sampler_index];
        auto ssharp =
            prepared ? prepared->sampler_sharps[sampler_index] : sampler.GetSharp(stage);
        ForceAnisotropy(ssharp, sampler.is_depth);
        samplers.push_back(CachedSampler(
            ssharp, sampler.is_depth, sampler.is_depth ? 0.0f : sampler_lod_bias));
    }
    BindSamplerSet({samplers.data(), samplers.size()}, binding, write_index);
}

void RasterizerCore::BindTextures(const Shader::Info& stage, const PreparedStage* prepared,
                                  Shader::Backend::Bindings& binding, u32& write_index,
                                  bool& barrier, bool on_helper) {
    BB_SECTION(BindTextures);
    TextureBindings textures;
    TextureSet* set_slot = nullptr;
    if (!on_helper && BindTexturesFromSet(stage, prepared, barrier, set_slot, textures)) {
        // One descriptor per image (no mip arrays in a memoized set).
        if (!textures.empty()) {
            BindTextureSet({textures.data(), textures.size()}, binding, write_index);
        }
        BindSamplers(stage, prepared, binding, write_index);
        return;
    }
    image_bindings.clear();
    image_binding_entries.clear();
    image_desc_overflow.clear();
    image_desc_storage.clear();
    ++bind_epoch;
    // To emulate storing to explicit mip levels, build a descriptor array with each mip level.
    boost::container::small_vector<u32, 8> image_descriptor_array_sizes;
    // TextureSetMemo: what this call resolves, to remember when the set qualifies.
    std::array<TextureSetEntry, TextureSet::MaxImages> resolved{};
    bool set_ok = set_slot != nullptr;
    const u64 scene_generation_before = scene_targets->Generation();
    // SampleSceneProxies: bindings that may read a reduced scene proxy instead of the native
    // image (normalized sampling only; see Shader::ImageResource::needs_native).
    boost::container::small_vector<bool, 16> binding_proxy_ok;
    const bool sample_proxies = !BbToggle::Disabled(BbToggle::SampleSceneProxies) &&
                                !on_helper && scene_targets->Reduced();

    for (u32 image_index = 0; image_index < stage.images.size(); ++image_index) {
        const auto& image_desc = stage.images[image_index];
        const auto tsharp =
            prepared ? prepared->image_sharps[image_index] : image_desc.GetSharp(stage);
        // bbport: a hash lookup per texture per draw for a diagnostic only.
        static const bool warn_meta = std::getenv("BB_WARN_META_TEXTURE") != nullptr;
        if (warn_meta && texture_cache.IsMeta(tsharp.Address())) {
            LOG_WARNING(Render_Vulkan, "Unexpected metadata read by a shader (texture)");
        }

        const auto data_fmt = tsharp.GetDataFmt();
        const auto num_fmt = tsharp.GetNumberFmt();
        if (tsharp.Address() == 0 || data_fmt == AmdGpu::DataFormat::FormatInvalid) {
            image_bindings.emplace_back(VideoCore::ImageId{}, &image_desc_storage.emplace_back());
            image_binding_entries.push_back(nullptr);
            binding_proxy_ok.push_back(false);
            image_descriptor_array_sizes.push_back(1);
            continue;
        }

        if (!memory->IsValidGpuMapping(tsharp.Address(), 0) ||
            !IsKnownFormat(data_fmt, num_fmt)) {
            LOG_WARNING(Render_Vulkan,
                        "Rejecting invalid T# address={:#x}, pitch={}, width={}, "
                        "data_format={}, num_format={}",
                        tsharp.Address(), tsharp.pitch, tsharp.width, static_cast<u32>(data_fmt),
                        static_cast<u32>(num_fmt));
            image_bindings.emplace_back(VideoCore::ImageId{}, &image_desc_storage.emplace_back());
            image_binding_entries.push_back(nullptr);
            binding_proxy_ok.push_back(false);
            image_descriptor_array_sizes.push_back(1);
            continue;
        }

        const Shader::MipStorageFallbackMode mip_fallback_mode = image_desc.mip_fallback_mode;
        // Same as image_desc.NumBindings(stage), without fetching the T# again.
        const u32 num_bindings =
            mip_fallback_mode == Shader::MipStorageFallbackMode::DynamicIndex
                ? static_cast<u32>(tsharp.last_level - tsharp.base_level + 1)
                : 1u;
        const bool proxy_candidate = sample_proxies && num_bindings == 1 &&
                                     !image_desc.needs_native && !image_desc.is_written &&
                                     mip_fallback_mode == Shader::MipStorageFallbackMode::None;

        auto& desc_entry =
            prepared ? CachedImageDescEntry(tsharp, image_desc, prepared->image_hashes[image_index])
                     : CachedImageDescEntry(tsharp, image_desc);
        // BB_SCENE_DEBUG: proxied scene targets this binding reads at the native size, and why.
        const auto debug_native = [&](const VideoCore::Image& image) {
            if (!scene_debug_frame || proxy_candidate || !image.scene_proxy) return;
            std::printf("Scene native read: %s %016llx %s %ux%u needs_native %d written %d "
                        "bindings %u mip_fallback %d\n",
                        stage.sw_stage == Shader::SwStage::Compute ? "cs" : "gfx",
                        (unsigned long long)stage.pgm_hash,
                        vk::to_string(image.info.pixel_format).c_str(), image.info.size.width,
                        image.info.size.height, int(image_desc.needs_native),
                        int(image_desc.is_written), num_bindings, int(mip_fallback_mode));
        };
        for (auto i = 0; i < num_bindings; i++) {
            // bbport: a plain binding (no mip override) of the same T# resolves to the same image
            // while no image was registered or unregistered.
            if (mip_fallback_mode == Shader::MipStorageFallbackMode::None &&
                desc_entry.found_generation == texture_cache.RegistryGeneration() &&
                !BbToggle::Disabled(BbToggle::TextureBindingMemo)) {
                desc_entry.pinned = bind_epoch;
                auto& [image_id, _] =
                    image_bindings.emplace_back(desc_entry.found_id, &desc_entry.found_desc);
                image_binding_entries.push_back(&desc_entry);
                binding_proxy_ok.push_back(proxy_candidate);
                texture_cache.MarkFound(image_id);
                auto* image = &texture_cache.GetImage(image_id);
                if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                    image_id = depth_image_id;
                    image = &texture_cache.GetImage(image_id);
                }
                debug_native(*image);
                if (image->binding.is_bound) {
                    image->binding.force_general |= image_desc.is_written;
                }
                image->binding.is_bound = 1u;
                continue;
            }
            auto& desc = image_desc_storage.emplace_back(desc_entry.desc);
            auto& [image_id, _] = image_bindings.emplace_back(VideoCore::ImageId{}, &desc);
            image_binding_entries.push_back(nullptr);
            binding_proxy_ok.push_back(proxy_candidate);

            if (mip_fallback_mode == Shader::MipStorageFallbackMode::ConstantIndex) {
                ASSERT(num_bindings == 1);
                desc.view_info.range.base.level += image_desc.constant_mip_index;
                desc.view_info.range.extent.levels = 1;
            } else if (mip_fallback_mode == Shader::MipStorageFallbackMode::DynamicIndex) {
                desc.view_info.range.base.level += i;
                desc.view_info.range.extent.levels = 1;
            }

            const u64 generation = texture_cache.RegistryGeneration();
            image_id = texture_cache.FindImage(desc);
            if (mip_fallback_mode == Shader::MipStorageFallbackMode::None &&
                generation == texture_cache.RegistryGeneration() &&
                desc_entry.pinned != bind_epoch) {
                desc_entry.found_generation = generation;
                desc_entry.found_id = image_id;
                desc_entry.found_desc = desc;
                desc_entry.view_memo = {};
            }
            auto* image = &texture_cache.GetImage(image_id);
            if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                // If this image has an associated depth image, it's a stencil attachment.
                // Redirect the access to the actual depth-stencil buffer.
                image_id = depth_image_id;
                image = &texture_cache.GetImage(image_id);
            }
            debug_native(*image);
            if (image->binding.is_bound) {
                // The image is already bound. In case if it is about to be used as storage we
                // need to force general layout on it.
                image->binding.force_general |= image_desc.is_written;
            }
            image->binding.is_bound = 1u;
        }

        image_descriptor_array_sizes.push_back(num_bindings);
    }

    // Second pass to re-bind images that were updated after binding
    for (u32 binding_index = 0; binding_index < image_bindings.size(); ++binding_index) {
        auto& [image_id, desc_ptr] = image_bindings[binding_index];
        auto* memo_entry = image_binding_entries[binding_index];
        const auto& desc = *desc_ptr;
        bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;
        if (!image_id) {
            textures.push_back({.layout = vk::ImageLayout::eGeneral, .storage = is_storage});
        } else {
            if (auto& old_image = texture_cache.GetImage(image_id);
                old_image.binding.needs_rebind) {
                old_image.binding = {};
                auto& rebind_desc = image_desc_storage.emplace_back(desc);
                desc_ptr = &rebind_desc;
                image_id = texture_cache.FindImage(rebind_desc);
                memo_entry = nullptr;
                set_ok = false;
            }

            bound_images.emplace_back(image_id);

            auto& image = texture_cache.GetImage(image_id);
            auto& image_view = texture_cache.FindTexture(
                image_id, desc, memo_entry ? &memo_entry->view_memo : nullptr, !on_helper);
            const auto binding = image.binding;

            // bbport: a proxied scene image sampled with normalized coordinates reads the proxy:
            // no resample to the native size (the native image is not touched).
            if (binding_proxy_ok[binding_index] && !is_storage && !binding.force_general &&
                !binding.is_target &&
                !(upscaler_enabled && upscaler->RedirectsSampled(image_id))) {
                if (const auto proxy = scene_targets->SampleProxy(image, desc.view_info)) {
                    image.usage.texture = 1u;
                    textures.push_back({.replacement_view = proxy->view,
                                        .layout = proxy->layout,
                                        .storage = is_storage});
                    if (set_ok && binding_index < resolved.size()) {
                        resolved[binding_index] = {.id = image_id,
                                                   .proxy_view = proxy->view,
                                                   .backing = image.backing,
                                                   .range = desc.view_info.range,
                                                   .proxy = true};
                    }
                    ++proxy_samples;
                    continue;
                }
            }
            // The image is either bound as storage in a separate descriptor or bound as render
            // target in feedback loop. Depth images are excluded because they can't be bound as
            // storage and feedback loop doesn't make sense for them
            if ((binding.force_general || binding.is_target) && !image.info.props.is_depth) {
                if (backend.Limits().attachment_feedback_loop_layout && image.binding.is_target) {
                    barrier |= backend.Transit(
                        &image, vk::ImageLayout::eAttachmentFeedbackLoopOptimalEXT,
                        vk::PipelineStageFlagBits2::eAllGraphics, vk::AccessFlagBits2::eShaderRead);
                } else {
                    barrier |= backend.Transit(
                        &image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite);
                }
            } else {
                if (is_storage) {
                    barrier |= backend.Transit(
                        &image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
                        desc.view_info.range);
                } else {
                    const auto new_layout = image.info.props.is_depth
                                                ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                                                : vk::ImageLayout::eShaderReadOnlyOptimal;
                    barrier |= backend.Transit(
                        &image, new_layout, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead, desc.view_info.range);
                }
            }
            image.usage.storage |= is_storage;
            image.usage.texture |= !is_storage;
            if (FrameCapture::Active() && !FrameCapture::TextureBindingsEnabled()) {
                FrameCapture::Sampled(image.info, is_storage);
            }

            // A slot vector growing in a later FindTexture moves the views: the id is kept.
            const auto view_id = ViewIdOf(image, image_view);
            VideoCore::NativeView replacement_view{};
            vk::ImageLayout layout = image.backing->state.layout;
            if (upscaler_enabled) {
                // bbport: the display pass reads the upscaled frame (scaled presets).
                upscaler->RedirectSampled(image_id, image_view.info, replacement_view, layout);
            }
            textures.push_back({.view = view_id,
                                .replacement_view = replacement_view,
                                .layout = layout,
                                .storage = is_storage});
            if (set_ok && binding_index < resolved.size()) {
                resolved[binding_index] = {.id = image_id,
                                           .view = view_id,
                                           .backing = image.backing,
                                           .range = desc.view_info.range};
                set_ok = !is_storage && !binding.force_general && !binding.is_target;
            }
        }
    }
    if (set_ok && image_bindings.size() == stage.images.size() &&
        scene_targets->Generation() == scene_generation_before) {
        // Remember the set: the same T#s resolve the same way while no image is (un)registered.
        set_slot->generation = texture_cache.RegistryGeneration();
        set_slot->scene_generation = scene_targets->Generation();
        set_slot->count = static_cast<u32>(stage.images.size());
        std::copy_n(resolved.begin(), set_slot->count, set_slot->entries.begin());
    } else if (set_slot) {
        set_slot->key = 0;
    }

    // The bindings of a resource are its descriptor array (the resource's type is its first's).
    u32 texture_idx = 0;
    for (u32 array_size : image_descriptor_array_sizes) {
        const auto& desc = *image_bindings[texture_idx].second;
        const bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;
        for (u32 element = 0; element < array_size; ++element) {
            textures[texture_idx + element].array_index = element;
            textures[texture_idx + element].storage = is_storage;
        }
        texture_idx += array_size;
    }
    if (FrameCapture::TextureBindingsEnabled() && FrameCapture::NativeRecording()) {
        FrameCapture::Native(fmt::format("{{\"op\":\"texture_set\",\"core\":{},\"shader\":{},\"stage\":{},\"slot_base\":{},\"count\":{},\"source\":\"resolve\"}}",
            uintptr_t(this),stage.pgm_hash,u32(stage.sw_stage),binding.unified,textures.size()));
        for(u32 i=0;i<textures.size();++i) {
            const auto& texture=textures[i];const auto& bound=image_bindings[i];
            texture_cache.CaptureTextureBinding(bound.first,texture.view,texture.replacement_view,
                uintptr_t(this),stage.pgm_hash,u32(stage.sw_stage),binding.unified+i,
                bound.second?bound.second->info.guest_address:0,on_helper?"resolve_helper":"resolve");
        }
    }
    if (!textures.empty()) {
        BindTextureSet({textures.data(), textures.size()}, binding, write_index);
    }

    BindSamplers(stage, prepared, binding, write_index);
}


bool RasterizerCore::BindTexturesFromSet(const Shader::Info& stage, const PreparedStage* prepared,
                                         bool& barrier, TextureSet*& slot,
                                         TextureBindings& textures) {
    const u32 count = static_cast<u32>(stage.images.size());
    if (!prepared || count == 0 || count > TextureSet::MaxImages ||
        BbToggle::Disabled(BbToggle::TextureSetMemo) ||
        (FrameCapture::Active() && !FrameCapture::TextureBindingsEnabled())) {
        return false;
    }
    for (const auto& image : stage.images) {
        if (image.is_written || image.mip_fallback_mode != Shader::MipStorageFallbackMode::None) {
            return false;
        }
    }
    u64 key = XXH3_64bits_withSeed(prepared->image_hashes, count * sizeof(u64),
                                   reinterpret_cast<u64>(&stage));
    key |= 1; // 0 marks an empty slot
    auto& set = texture_sets[key % texture_sets.size()];
    slot = &set;
    const u64 generation = texture_cache.RegistryGeneration();
    const bool match = set.key == key && set.stage == &stage && set.count == count &&
                       set.generation == generation &&
                       set.scene_generation == scene_targets->Generation() &&
                       std::equal(set.hashes.begin(), set.hashes.begin() + count,
                                  prepared->image_hashes);
    if (!match) {
        ++texture_set_why[set.key != key ? 0 : set.generation != generation ? 1 : 3];
        ++texture_set_misses;
        set.key = key;
        set.stage = &stage;
        set.count = 0;
        set.generation = ~0ull;
        std::copy_n(prepared->image_hashes, count, set.hashes.begin());
        return false;
    }
    // Every image still has the backing its view belongs to and needs no refresh.
    for (u32 i = 0; i < count; ++i) {
        const auto& entry = set.entries[i];
        if (!entry.id) {
            continue;
        }
        const auto& image = texture_cache.GetImage(entry.id);
        // Proxy entries need a current proxy; native entries of a proxied image go through
        // BindTextures, which may sample the proxy instead.
        const bool proxies_on = !BbToggle::Disabled(BbToggle::SampleSceneProxies);
        if (image.backing != entry.backing || image.binding.needs_rebind ||
            image.binding.is_target || !texture_cache.IsUpToDate(entry.id) ||
            (entry.proxy ? !proxies_on ||
                               !scene_targets->ProxyCurrent(image, entry.range.base.level,
                                                            entry.range.extent.levels)
                         : image.scene_proxy && proxies_on) ||
            (upscaler_enabled && upscaler->RedirectsSampled(entry.id))) {
            ++texture_set_why[2];
            ++texture_set_misses;
            set.generation = ~0ull;
            return false;
        }
    }
    ++texture_set_hits;
    slot = nullptr;
    for (u32 i = 0; i < count; ++i) {
        const auto& entry = set.entries[i];
        if (!entry.id) {
            textures.push_back({.layout = vk::ImageLayout::eGeneral});
            continue;
        }
        texture_cache.MarkFound(entry.id);
        auto& image = texture_cache.GetImage(entry.id);
        image.binding.is_bound = 1u;
        bound_images.emplace_back(entry.id);
        image.usage.texture = 1u;
        if (entry.proxy) {
            textures.push_back(
                {.replacement_view = entry.proxy_view,
                 .layout = scene_targets->PrepareSample(image, entry.range.base.level)});
            ++proxy_samples;
            continue;
        }
        const auto new_layout = image.info.props.is_depth
                                    ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                                    : vk::ImageLayout::eShaderReadOnlyOptimal;
        barrier |= backend.Transit(&image, new_layout, vk::PipelineStageFlagBits2::eAllCommands,
                                   vk::AccessFlagBits2::eShaderRead, entry.range);
        textures.push_back({.view = entry.view, .layout = image.backing->state.layout});
    }
    if (FrameCapture::TextureBindingsEnabled() && FrameCapture::NativeRecording()) {
        FrameCapture::Native(fmt::format("{{\"op\":\"texture_set\",\"core\":{},\"shader\":{},\"stage\":{},\"slot_base\":0,\"slot_space\":\"stage_texture\",\"count\":{},\"source\":\"set_memo\"}}",
            uintptr_t(this),stage.pgm_hash,u32(stage.sw_stage),textures.size()));
        // slot was deliberately cleared above: it describes a MISS output only.
        // Observe the local hit set used to populate textures, not that output pointer.
        const bool complete=BbTextureObservation::MemoHit(set,textures,
            [&](std::size_t i,const auto& entry,const auto& texture) {
                texture_cache.CaptureTextureBinding(entry.id,texture.view,
                    texture.replacement_view,uintptr_t(this),stage.pgm_hash,u32(stage.sw_stage),
                    u32(i),prepared->image_sharps[i].Address(),"set_memo"); // owned prepared snapshot; never reread guest memory
            });
        if(!complete)FrameCapture::Native("{\"op\":\"texture_set_incomplete\",\"source\":\"set_memo\"}");
    }
    return true;
}

VideoCore::ImageViewId RasterizerCore::ViewIdOf(const VideoCore::Image& image,
                                                const VideoCore::ImageView& view) {
    // The view memo of FindTexture can return a view other than the last one FindView found.
    const auto& ids = image.backing->image_view_ids;
    const u32 last = image.backing->last_view;
    if (last < ids.size() && &texture_cache.GetImageView(ids[last]) == &view) {
        return ids[last];
    }
    for (const auto id : ids) {
        if (&texture_cache.GetImageView(id) == &view) {
            return id;
        }
    }
    UNREACHABLE();
    return {};
}

void RasterizerCore::ResolveIndexBuffer(u32 index_offset) {
    BB_SECTION(ResolveIndexBuffer);
    const auto& regs = Regs();

    // Figure out index type and size.
    const bool is_index16 = regs.index_buffer_type.index_type == AmdGpu::IndexType::Index16;
    const vk::IndexType index_type = is_index16 ? vk::IndexType::eUint16 : vk::IndexType::eUint32;
    const u32 index_size = is_index16 ? sizeof(u16) : sizeof(u32);
    const VAddr index_address =
        regs.index_base_address.Address<VAddr>() + index_offset * index_size;

    // Bind index buffer.
    const u32 index_buffer_size = regs.num_indices * index_size;
    const auto [buffer, offset] =
        buffer_cache.ObtainBuffer(index_address, index_buffer_size, false);
    needs_barrier |= backend.IsBufferAccessed(buffer, offset, index_buffer_size);
    NoteGuestAccess(index_address, index_buffer_size, false);
    // ResetBindings tracks graphics reads after the draw. Without this, an upload into a
    // mirrored index range could overtake the previous draw's fixed-function index fetches.
    bound_buffers.emplace_back(buffer, offset, index_buffer_size, false, 6, 0, index_address);
    index_bind = {buffer, offset, index_type};
}

void RasterizerCore::ResetBindings(bool is_compute) {
    if (!is_compute && queue.HoldingPass()) {
        if (pass_serial != queue.HeldPassSerial()) {
            pass_serial = queue.HeldPassSerial();
            pass_reads.clear();
            pass_writes.clear();
            pass_images.clear();
            pass_read_images.clear();
            ++pass_generation;
            pass_untracked = false;
        }
        pass_untracked |= pending_untracked;
        for (const auto image_id : bound_images) {
            const auto& image = texture_cache.GetImage(image_id);
            const auto layout = image.backing ? image.backing->state.layout : vk::ImageLayout::eUndefined;
            const bool read_only = layout == vk::ImageLayout::eShaderReadOnlyOptimal ||
                                   layout == vk::ImageLayout::eDepthStencilReadOnlyOptimal;
            if (!InPassImages(image_id.index)) {
                pass_images.push_back(image_id.index);
                SetStamp(pass_image_stamp, image_id.index, pass_generation);
                if (read_only) {
                    pass_read_images.push_back(image_id.index);
                    SetStamp(pass_read_stamp, image_id.index, pass_generation);
                }
            } else if (!read_only && InPassReadImages(image_id.index)) {
                pass_read_images.erase(std::ranges::find(pass_read_images, image_id.index));
                pass_read_stamp[image_id.index] = 0;
            }
        }
        for (const auto& access : pending_accesses) {
            (access.write ? pass_writes : pass_reads)
                .add(boost::icl::interval<VAddr>::right_open(access.address,
                                                             access.address + access.size));
        }
    }
    pending_accesses.clear();
    pending_untracked = false;
    for (auto& image_id : bound_images) {
        texture_cache.GetImage(image_id).binding = {};
    }
    for (const auto& b : bound_buffers) {
        const auto [buffer, offset, size, is_written, logical_stage, shader, guest_address] = b;
        if(FrameCapture::NativeRecording())FrameCapture::NativeBufferUse(
            uintptr_t(buffer),buffer?uintptr_t(buffer->buffer.native):0,offset,size,guest_address,
            logical_stage,shader,is_written,"shader");
        const auto dst_stage = is_compute ? vk::PipelineStageFlagBits2::eComputeShader
                                          : vk::PipelineStageFlagBits2::eAllGraphics;
        const auto write_flag =
            is_written ? vk::AccessFlagBits2::eShaderWrite : vk::AccessFlagBits2::eNone;
        if (!buffer) {
            // bbport BB_LAYER_MEMORY: a paged binding (any buffer of the game's memory).
            backend.AccessGlobal(dst_stage, vk::AccessFlagBits2::eShaderRead | write_flag);
            continue;
        }
        backend.AccessBuffer(buffer, offset, size, dst_stage,
                             vk::AccessFlagBits2::eShaderRead | write_flag);
    }
    bound_images.clear();
    bound_buffers.clear();
    needs_barrier = false;
}

void RasterizerCore::UpdateDepthStencilState(DepthStencilState& state) const {
    const auto& regs = Regs();
    state = DepthStencilState{};

    state.depth_test_enabled = regs.depth_control.depth_enable && regs.depth_buffer.DepthValid();
    if (state.depth_test_enabled) {
        state.depth_write_enabled = regs.depth_control.depth_write_enable &&
                                    !regs.depth_render_control.depth_clear_enable;
        state.depth_compare_op = LiverpoolToVK::CompareOp(regs.depth_control.depth_func);
    }

    state.depth_bounds_test_enabled = regs.depth_control.depth_bounds_enable;
    if (state.depth_bounds_test_enabled) {
        state.depth_bounds_min = regs.depth_bounds_min;
        state.depth_bounds_max = regs.depth_bounds_max;
    }

    state.depth_bias_enabled = regs.polygon_control.NeedsBias();
    if (state.depth_bias_enabled) {
        const bool front = regs.polygon_control.enable_polygon_offset_front;
        state.depth_bias_constant =
            front ? regs.poly_offset.front_offset : regs.poly_offset.back_offset;
        state.depth_bias_clamp = regs.poly_offset.depth_bias;
        state.depth_bias_slope =
            (front ? regs.poly_offset.front_scale : regs.poly_offset.back_scale) / 16.f;
    }

    state.stencil_test_enabled =
        regs.depth_control.stencil_enable && regs.depth_buffer.StencilValid();
    if (state.stencil_test_enabled) {
        const StencilOps front_ops{
            .fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_fail_front),
            .pass_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zpass_front),
            .depth_fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zfail_front),
            .compare_op = LiverpoolToVK::CompareOp(regs.depth_control.stencil_ref_func),
        };
        const StencilOps back_ops = regs.depth_control.backface_enable ? StencilOps{
            .fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_fail_back),
            .pass_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zpass_back),
            .depth_fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zfail_back),
            .compare_op = LiverpoolToVK::CompareOp(regs.depth_control.stencil_bf_func),
        } : front_ops;
        state.front_ops = front_ops;
        state.back_ops = back_ops;

        const bool stencil_clear = regs.depth_render_control.stencil_clear_enable;
        const auto front = regs.stencil_ref_front;
        const auto back =
            regs.depth_control.backface_enable ? regs.stencil_ref_back : regs.stencil_ref_front;
        // GCN REPLACE_OP writes DB_STENCILREFMASK.STENCILOPVAL, so a face whose stencil ops
        // include ReplaceOp takes its Vulkan reference from op_val.
        const auto& sc = regs.stencil_control;
        const auto uses_op_val = [](AmdGpu::StencilFunc fail, AmdGpu::StencilFunc zpass,
                                    AmdGpu::StencilFunc zfail) {
            return fail == AmdGpu::StencilFunc::ReplaceOp ||
                   zpass == AmdGpu::StencilFunc::ReplaceOp ||
                   zfail == AmdGpu::StencilFunc::ReplaceOp;
        };
        const bool front_op =
            uses_op_val(sc.stencil_fail_front, sc.stencil_zpass_front, sc.stencil_zfail_front);
        const bool back_op =
            regs.depth_control.backface_enable
                ? uses_op_val(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back)
                : front_op;
        const auto ref_conflict = [](AmdGpu::CompareFunc func, const AmdGpu::StencilRefMask& ref) {
            return func != AmdGpu::CompareFunc::Always && func != AmdGpu::CompareFunc::Never &&
                   ref.stencil_test_val != ref.stencil_op_val;
        };
        if ((front_op && ref_conflict(regs.depth_control.stencil_ref_func, front)) ||
            (back_op && regs.depth_control.backface_enable &&
             ref_conflict(regs.depth_control.stencil_bf_func, back))) {
            LOG_WARNING(Render_Vulkan, "Stencil test requires test_val while ReplaceOp requires "
                                       "op_val; the stencil test will use op_val");
        }
        state.front_reference = front_op ? front.stencil_op_val : front.stencil_test_val;
        state.back_reference = back_op ? back.stencil_op_val : back.stencil_test_val;
        state.front_write_mask = !stencil_clear ? front.stencil_write_mask : 0U;
        state.back_write_mask = !stencil_clear ? back.stencil_write_mask : 0U;
        state.front_compare_mask = front.stencil_mask;
        state.back_compare_mask = back.stencil_mask;
    }
}

void RasterizerCore::UpdatePrimitiveState(PrimitiveState& state, const bool is_indexed) const {
    const auto& regs = Regs();

    const auto is_list_topology = [](const AmdGpu::PrimitiveType type) {
        const auto topology = LiverpoolToVK::PrimitiveType(type);
        return topology == vk::PrimitiveTopology::ePointList ||
               topology == vk::PrimitiveTopology::eLineList ||
               topology == vk::PrimitiveTopology::eTriangleList ||
               topology == vk::PrimitiveTopology::eLineListWithAdjacency ||
               topology == vk::PrimitiveTopology::eTriangleListWithAdjacency;
    };
    const auto is_patch_list_topology = [](const AmdGpu::PrimitiveType type) {
        // Quad and rect lists are emulated using tessellation.
        return type == AmdGpu::PrimitiveType::PatchPrimitive ||
               type == AmdGpu::PrimitiveType::QuadList || type == AmdGpu::PrimitiveType::RectList;
    };

    const auto prim_restart =
        (regs.enable_primitive_restart & 1) != 0 &&
        (backend.Limits().list_restart || !is_list_topology(regs.primitive_type)) &&
        (backend.Limits().patch_list_restart || !is_patch_list_topology(regs.primitive_type));
    ASSERT_MSG(!is_indexed || !prim_restart || regs.primitive_restart_index == 0xFFFF ||
                   regs.primitive_restart_index == 0xFFFFFFFF,
               "Primitive restart index other than -1 is not supported yet");

    const auto cull_mode = LiverpoolToVK::IsPrimitiveCulled(regs.primitive_type)
                               ? LiverpoolToVK::CullMode(regs.polygon_control.CullingMode())
                               : vk::CullModeFlagBits::eNone;
    const auto front_face = LiverpoolToVK::FrontFace(regs.polygon_control.front_face);

    state = {
        .restart_enabled = prim_restart,
        .rasterizer_discard = regs.clipper_control.dx_rasterization_kill,
        .cull_mode = cull_mode,
        .front_face = front_face,
    };
}

void RasterizerCore::MarkPass(const PipelineBase* pipeline, const RenderState& state) {
    if (!pass_labels || !queue.WillBeginRendering(state)) {
        return;
    }
    const auto& vs = pipeline->GetStage(Shader::SwStage::Vertex);
    const auto* ps = pipeline->GetStages()[u32(Shader::SwStage::Fragment)];
    std::array<u64, AmdGpu::NUM_COLOR_BUFFERS + 6> parts{};
    parts[0] = state.width | u64(state.height) << 32;
    parts[1] = vs.pgm_hash;
    parts[2] = ps ? ps->pgm_hash : 0;
    parts[3] = db_desc.first ? u64(texture_cache.GetImage(db_desc.first).info.pixel_format) : 0;
    PassLabel label{
        .width = state.width,
        .height = state.height,
        .num_color_attachments = state.num_color_attachments,
        .color_present = 0,
        .color_formats = {},
        .has_depth = bool(db_desc.first),
        .depth_format = db_desc.first ? texture_cache.GetImage(db_desc.first).info.pixel_format
                                      : vk::Format::eUndefined,
        .vs_hash = vs.pgm_hash,
        .ps_hash = ps ? ps->pgm_hash : 0,
    };
    for (u32 cb = 0; cb < state.num_color_attachments && cb < AmdGpu::NUM_COLOR_BUFFERS; ++cb) {
        parts[4 + cb] = cb_descs[cb].first
                            ? u64(texture_cache.GetImage(cb_descs[cb].first).info.pixel_format) + 1
                            : 0;
        if (cb_descs[cb].first) {
            label.color_present |= 1u << cb;
            label.color_formats[cb] = texture_cache.GetImage(cb_descs[cb].first).info.pixel_format;
        }
    }
    label.key = XXH3_64bits(parts.data(), sizeof(parts));
    SetPassLabel(label);
}


namespace {
Shader::PushData MakeUserData(const AmdGpu::Regs& regs) {
    // TODO(roamic): Add support for multiple viewports and geometry shaders when ViewportIndex
    // is encountered and implemented in the recompiler.
    Shader::PushData push_data{};
    push_data.xoffset = regs.viewport_control.xoffset_enable ? regs.viewports[0].xoffset : 0.f;
    push_data.xscale = regs.viewport_control.xscale_enable ? regs.viewports[0].xscale : 1.f;
    push_data.yoffset = regs.viewport_control.yoffset_enable ? regs.viewports[0].yoffset : 0.f;
    push_data.yscale = regs.viewport_control.yscale_enable ? regs.viewports[0].yscale : 1.f;
    return push_data;
}
} // namespace

void RasterizerCore::ResolveVertexBuffers(const PipelineBase* pipeline,
                                      const PreparedDraw* prepared) {
    BB_SECTION(ResolveVertexBuffers);
    const auto& regs = Regs();
    auto& v = vertex_binds;
    v.num_buffers = 0;
    v.host_buffers.clear();
    v.host_offsets.clear();
    v.host_sizes.clear();
    v.host_strides.clear();
    auto& attributes = v.attributes;
    auto& bindings = v.bindings;
    attributes.clear();
    bindings.clear();
    VertexInputs<vk::VertexInputBindingDivisorDescriptionEXT> divisors;
    VertexInputs<AmdGpu::Buffer> guest_buffers;
    // Inputs a draw-preparation worker read from the same registers and (verified) user data.
    const auto& fetch = pipeline->GetFetchShader();
    const PreparedVertexInputs* ready =
        prepared && prepared->vertex.valid && backend.Limits().vertex_input_dynamic_state && fetch &&
                fetch->attributes.size() == prepared->vertex.count &&
                (!FrameCapture::Active() || FrameCapture::TextureBindingsEnabled()) &&
                !BbToggle::Disabled(BbToggle::PreparedResources)
            ? &prepared->vertex
            : nullptr;
    if (ready) {
        attributes.assign(ready->attributes, ready->attributes + ready->count);
        bindings.assign(ready->bindings, ready->bindings + ready->count);
        guest_buffers.assign(ready->buffers, ready->buffers + ready->count);
        if (BbCeStats::Enabled() && packet_vsharps.size() == ready->count) {
            static u64 compared = 0, differed = 0;
            ++compared;
            differed += std::memcmp(ready->buffers, packet_vsharps.data(),
                                    ready->count * sizeof(AmdGpu::Buffer)) != 0;
            if ((compared & 0xFFFF) == 0) {
                std::printf("CE stats: prepared V#s differed from the packet's in %llu of %llu "
                            "draws\n",
                            (unsigned long long)differed, (unsigned long long)compared);
            }
        }
    } else {
        pipeline->GetVertexInputs(attributes, bindings, divisors, guest_buffers,
                                  regs.vgt_instance_step_rate_0, regs.vgt_instance_step_rate_1,
                                  packet_vsharps);
        if (BbCeStats::Enabled() && !packet_vsharps.empty()) {
            // What reading the V# tables here would have given instead (the old path).
            static u64 compared = 0, differed = 0;
            const auto& fetch = pipeline->GetFetchShader();
            const auto& vs = pipeline->GetStage(Shader::SwStage::Vertex);
            for (u32 i = 0; fetch && i < fetch->attributes.size() && i < packet_vsharps.size(); ++i) {
                ++compared;
                const auto now = fetch->attributes[i].GetSharp(vs);
                differed += std::memcmp(&now, &packet_vsharps[i], sizeof(now)) != 0;
            }
            if ((compared & 0xFFFF) == 0) {
                std::printf("CE stats: V#s read on the recording thread differed from the "
                            "packet's in %llu of %llu\n",
                            (unsigned long long)differed, (unsigned long long)compared);
            }
        }
    }

    if (bindings.empty()) {
        // If there are no bindings, there is nothing further to do.
        return;
    }
    if (motion_draw && !guest_buffers.empty()) {
        // Include every stream: identical position buffers can be paired with different
        // skinning/instance data. Index topology and base offsets are matched separately.
        motion_geometry = ready ? ready->buffers_hash
                                : XXH3_64bits(guest_buffers.data(),
                                              guest_buffers.size() * sizeof(AmdGpu::Buffer));
    }
    // Object motion research: vertex streams of G-buffer draws.
    if (FrameCapture::Active() && gbuffer_draw) {
        for (const auto& vb : guest_buffers) {
            char note[128];
            std::snprintf(note, sizeof(note), "\n  gb vb at %#llx size %u stride %u",
                          (unsigned long long)vb.base_address, u32(vb.GetSize()),
                          u32(vb.GetStride()));
            FrameCapture::Note(note);
        }
    }

    struct BufferRange {
        VAddr base_address;
        VAddr end_address;
        const VideoCore::Buffer* buffer;
        u64 offset;

        [[nodiscard]] size_t GetSize() const {
            return end_address - base_address;
        }
    };

    // Build list of ranges covering the requested buffers
    VertexInputs<BufferRange> ranges{};
    VertexInputs<BufferRange> ranges_merged{};
    if (ready) {
        for (u32 i = 0; i < ready->num_ranges; ++i) {
            ranges_merged.emplace_back(ready->ranges[i].base, ready->ranges[i].end);
        }
    } else {
        for (const auto& buffer : guest_buffers) {
            if (buffer.base_address != 0 && buffer.GetSize() > 0) {
                ranges.emplace_back(buffer.base_address, buffer.base_address + buffer.GetSize());
            }
        }
    }

    // Merge connecting ranges together
    if (!ranges.empty()) {
        std::ranges::sort(ranges, [](const BufferRange& lhv, const BufferRange& rhv) {
            return lhv.base_address < rhv.base_address;
        });
        ranges_merged.emplace_back(ranges[0]);
        for (auto range : ranges) {
            auto& prev_range = ranges_merged.back();
            if (prev_range.end_address < range.base_address) {
                ranges_merged.emplace_back(range);
            } else {
                prev_range.end_address = std::max(prev_range.end_address, range.end_address);
            }
        }
    }

    // Map buffers for merged ranges
    for (auto& range : ranges_merged) {
        const u64 size = memory->ClampRangeSize(range.base_address, range.GetSize());
        std::tie(range.buffer, range.offset) =
            buffer_cache.ObtainBuffer(range.base_address, size, false);
        needs_barrier |= backend.IsBufferAccessed(range.buffer, range.offset, size);
        NoteGuestAccess(range.base_address, size, false);
        if(FrameCapture::NativeRecording())FrameCapture::NativeBufferUse(uintptr_t(range.buffer),
            uintptr_t(range.buffer->buffer.native),range.offset,size,range.base_address,3,
            pipeline->GetStage(Shader::SwStage::Vertex).pgm_hash,false,"vertex_fetch");
    }

    // Bind vertex buffers
    auto& host_buffers = v.host_buffers;
    auto& host_offsets = v.host_offsets;
    auto& host_sizes = v.host_sizes;
    auto& host_strides = v.host_strides;
    u32 stream_with_memory = 0;
    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            const auto host_buffer_info =
                ready ? ranges_merged.begin() + ready->range_index[stream_with_memory++]
                      : std::ranges::find_if(ranges_merged, [&](const BufferRange& range) {
                            return buffer.base_address >= range.base_address &&
                                   buffer.base_address < range.end_address;
                        });
            ASSERT(host_buffer_info != ranges_merged.cend());
            host_buffers.emplace_back(host_buffer_info->buffer);
            host_offsets.push_back(host_buffer_info->offset + buffer.base_address -
                                   host_buffer_info->base_address);
        } else {
            // bbport: without null descriptors (MoltenVK) an empty stream reads a real buffer.
            host_buffers.emplace_back(backend.Limits().null_descriptor
                                          ? nullptr
                                          : buffer_cache.GetGdsBuffer());
            host_offsets.push_back(0);
        }
        host_sizes.push_back(buffer.GetSize());
        host_strides.push_back(buffer.GetStride());
    }

    v.num_buffers = static_cast<u32>(guest_buffers.size());
}

namespace {
// Read-only mapping admission probe. Recording-thread only; fixed metadata, no resources,
// GPU reads, mapping changes or lifetime decisions. Repeated identical states are suppressed;
// changes of physical source/offset, mirror state or contiguous prefix remain distinct.
void CapturePagedMapping(const Shader::Info& stage, VAddr address, u64 size, bool written) {
    auto& layer = BbLayer::GpuMemory::Get();
    const auto resolved = layer.Resolve(address, size);
    const auto prefix = layer.ResolvePrefix(address, size);
    const auto* whole = resolved.span.source;
    const auto* start = prefix ? prefix->source : nullptr;
    using Key = std::array<u64, 14>;
    const Key key{stage.pgm_hash, u64(stage.sw_stage), address, size, u64(written),
        u64(resolved.kind), whole ? whole->handle : 0, resolved.span.offset,
        prefix ? prefix->size : 0, start ? start->handle : 0,
        prefix ? prefix->offset : 0, start ? start->base : 0,
        start ? start->size : 0, whole ? whole->size : 0};
    static std::array<Key, 256> states;
    static u64 sequence = 0;
    static size_t count = 0;
    static bool overflow = false;
    const u64 now = FrameCapture::NativeCaptureSequence();
    if (now != sequence) { sequence = now; count = 0; overflow = false; }
    if (std::find(states.begin(), states.begin() + count, key) != states.begin() + count)
        return;
    if (count == states.size()) {
        if (!overflow) FrameCapture::Native("{\"op\":\"paged_mapping_overflow\"}");
        overflow = true;
        return;
    }
    states[count++] = key;
    FrameCapture::Native(std::format(
        "{{\"op\":\"paged_mapping\",\"shader\":{},\"stage\":{},\"address\":{},\"bytes\":{},"
        "\"write\":{},\"kind\":{},\"whole_native\":{},\"whole_offset\":{},"
        "\"prefix_bytes\":{},\"prefix_native\":{},\"prefix_offset\":{},"
        "\"source_physical\":{},\"source_bytes\":{},\"whole_source_bytes\":{}}}",
        key[0], key[1], key[2], key[3], written, key[5], key[6], key[7],
        key[8], key[9], key[10], key[11], key[12], key[13]));
}

// bbport: BB_TRACE_SHADER=hash[,hash...] (diagnostics) — the buffers those shaders bind: address,
// size, written or read, where the data is (in place: the game's memory over PCIe on a discrete
// GPU; VRAM; partly), how often; printed every 5 s.
void TraceShaderBinding(const Shader::Info& stage, VAddr addr, u64 size, bool written,
                        const VideoCore::BufferCache& cache) {
    static const std::vector<u64> hashes = [] {
        std::vector<u64> out;
        const char* env = std::getenv("BB_TRACE_SHADER");
        for (const char* at = env; at && *at;) {
            char* end = nullptr;
            out.push_back(std::strtoull(at, &end, 16));
            at = end && *end == ',' ? end + 1 : nullptr;
        }
        return out;
    }();
    if (hashes.empty() || std::ranges::find(hashes, stage.pgm_hash) == hashes.end()) {
        return;
    }
    const bool in_place = cache.IsInPlace(addr, size);
    const bool any_in_place = cache.IsAnyInPlace(addr, size);
    struct Key {
        u64 hash, addr, size;
        bool written;
        auto operator<=>(const Key&) const = default;
    };
    struct Entry {
        u64 count = 0;
        int place = 0; // 0 VRAM, 1 in place, 2 partly
    };
    static std::mutex mutex;
    static std::map<Key, Entry> entries;
    static auto report = std::chrono::steady_clock::now();
    std::scoped_lock lk{mutex};
    auto& entry = entries[{stage.pgm_hash, addr, size, written}];
    ++entry.count;
    entry.place = in_place ? 1 : any_in_place ? 2 : 0;
    const auto now = std::chrono::steady_clock::now();
    if (now - report < std::chrono::seconds(5)) {
        return;
    }
    report = now;
    static constexpr const char* Places[] = {"VRAM", "in place", "partly in place"};
    for (const auto& [key, value] : entries) {
        std::printf("Shader %016llx binds %#llx+%#llx %s, %s, x%llu (uses_dma %d)\n",
                    (unsigned long long)key.hash, (unsigned long long)key.addr,
                    (unsigned long long)key.size, key.written ? "written" : "read",
                    Places[value.place], (unsigned long long)value.count, int(stage.uses_dma));
    }
    entries.clear();
}
} // namespace

void RasterizerCore::BindBuffers(const Shader::Info& stage, const PreparedStage* prepared,
                                 Shader::Backend::Bindings& binding,
                                 Shader::PushData& push_data, u32& write_index,
                                 bool direct_dispatch) {
    BB_SECTION(BindBuffers);
    if(FrameCapture::NativeRecording() && stage.uses_dma)FrameCapture::NativeBufferUse(
        0,0,0,0,0,u32(stage.sw_stage),stage.pgm_hash,false,"bda_unbounded");
    static const bool capture_ring_scope = [] {
        const char* env = std::getenv("BB_CAPTURE_COPY_SCOPE");
        return env && env[0] == '1';
    }();
    std::optional<CopyRingScope> copy_scope;
    const bool known_copy = direct_dispatch && stage.sw_stage == Shader::SwStage::Compute &&
                            Game::BufferCopyShader() == 0xfefebf9full && stage.pgm_hash == Game::BufferCopyShader();
    if (capture_ring_scope && FrameCapture::NativeRecording() && known_copy && stage.buffers.size() >= 3 &&
        !stage.uses_dma && stage.images.empty() && constant_ring) {
        bool supported = true;
        std::array<AmdGpu::Buffer, 3> sharps;
        for (u32 i = 0; i < stage.buffers.size(); ++i) {
            const auto& desc = stage.buffers[i];
            if (i < 3) {
                supported &= !desc.IsSpecial() && desc.is_formatted &&
                             desc.is_written == (i == 2);
                sharps[i] = prepared ? prepared->buffer_sharps[i] : desc.GetSharp(stage);
                // A copied source/destination would have different storage than its guest
                // descriptor. Only the control binding may use the constant ring here.
                if (i && FindRingBinding(stage, i)) supported = false;
            } else {
                supported &= desc.buffer_type == Shader::BufferType::BdaPagetable;
            }
        }
        supported &= Shader::IsPagedBuffer(BufferCache::LayerPagedActive(), sharps[1]) ||
                     Shader::IsPagedBuffer(BufferCache::LayerPagedActive(), sharps[2]);
        const RingBinding* ctl = FindRingBinding(stage, 0);
        const auto& grid = CsRegs();
        if (supported && ctl && ctl->address == sharps[0].base_address && ctl->size &&
            ctl->offset < ConstantRing::Capacity &&
            ctl->size <= ConstantRing::Capacity - ctl->offset) {
            copy_scope = BuildCopyRingScope({constant_ring->Data(ctl->offset), ctl->size}, sharps,
                {grid.dim_x, grid.dim_y, grid.dim_z, grid.start_x, grid.start_y, grid.start_z,
                 grid.num_thread_x.full, grid.num_thread_y.full, grid.num_thread_z.full,
                 bool(grid.num_thread_x.partial || grid.num_thread_y.partial || grid.num_thread_z.partial)});
        }
        if (FrameCapture::NativeRecording()) {
            FrameCapture::Native(std::format(
                "{{\"op\":\"copy_ring_scope\",\"shader\":{},\"valid\":{},\"ring\":{},"
                "\"src_address\":{},\"src_size\":{},\"dst_address\":{},\"dst_size\":{},"
                "\"src_descriptor_bytes\":{},\"dst_descriptor_bytes\":{},\"threads\":{},\"groups\":{},"
                "\"supported\":{},\"layout_mask\":{},\"ctl_size\":{},\"ctl_stride\":{},\"src_stride\":{},\"dst_stride\":{},"
                "\"ctl_format\":{},\"src_format\":{},\"dst_format\":{},\"ctl_number\":{},\"src_number\":{},\"dst_number\":{},"
                "\"partial\":{},\"grid_y\":{},\"grid_z\":{},\"src_intervals\":{},\"dst_intervals\":{},"
                "\"src_covered_bytes\":{},\"dst_covered_bytes\":{}}}",
                stage.pgm_hash, bool(copy_scope), bool(ctl),
                copy_scope ? copy_scope->source.address : 0, copy_scope ? copy_scope->source.size : 0,
                copy_scope ? copy_scope->destination.address : 0, copy_scope ? copy_scope->destination.size : 0,
                sharps[1].GetSize(), sharps[2].GetSize(), grid.num_thread_x.full, grid.dim_x,
                supported, unsigned(CopyRingLayout(sharps[0], true)) |
                    (unsigned(CopyRingLayout(sharps[1], false)) << 1) |
                    (unsigned(CopyRingLayout(sharps[2], false)) << 2),
                ctl ? ctl->size : 0, sharps[0].GetStride(), sharps[1].GetStride(), sharps[2].GetStride(),
                u32(sharps[0].GetDataFmt()), u32(sharps[1].GetDataFmt()), u32(sharps[2].GetDataFmt()),
                u32(sharps[0].GetNumberFmt()), u32(sharps[1].GetNumberFmt()), u32(sharps[2].GetNumberFmt()),
                unsigned(grid.num_thread_x.partial || grid.num_thread_y.partial || grid.num_thread_z.partial),
                grid.dim_y, grid.dim_z, copy_scope ? copy_scope->sources.size() : 0,
                copy_scope ? copy_scope->destinations.size() : 0,
                copy_scope ? std::accumulate(copy_scope->sources.begin(), copy_scope->sources.end(), u64{0},
                    [](u64 bytes, auto range) { return bytes + range.size; }) : 0,
                copy_scope ? std::accumulate(copy_scope->destinations.begin(), copy_scope->destinations.end(), u64{0},
                    [](u64 bytes, auto range) { return bytes + range.size; }) : 0));
        }
    }
    const u64 alignment = backend.Limits().storage_min_alignment;
    boost::container::static_vector<BufferBinding, Shader::NUM_BUFFERS> infos;
    for (u32 buffer_index = 0; buffer_index < stage.buffers.size(); ++buffer_index) {
        const auto& desc = stage.buffers[buffer_index];
        const RingBinding* ring = num_ring_stages ? FindRingBinding(stage, buffer_index) : nullptr;
        if (ring) {
            // Copied by the GPU command thread into the constant ring (read-only).
            if (!desc.IsSpecial()) {
                if (ring->size == 864 && gbuffer_draw &&
                    memory->IsValidGpuMapping(ring->address, 0)) {
                    camera_motion->OnConstants(reinterpret_cast<const float*>(ring->address));
                }
                push_data.AddOffset(binding.buffer, 0);
            }
            infos.push_back({nullptr, ring->offset, ring->size, true});
            if(FrameCapture::NativeRecording() && ring->address)FrameCapture::NativeBufferUse(
                0,0,ring->offset,ring->size,ring->address,u32(stage.sw_stage),stage.pgm_hash,false,"ring_binding");
        } else if (desc.IsSpecial()) {
            if (desc.buffer_type == Shader::BufferType::GdsBuffer) {
                const auto* gds_buf = buffer_cache.GetGdsBuffer();
                infos.push_back({gds_buf, 0, gds_buf->SizeBytes(), false});
                needs_barrier |= backend.IsBufferAccessed(gds_buf, 0, gds_buf->SizeBytes());
                pending_untracked = true;
            } else if (desc.buffer_type == Shader::BufferType::Flatbuf) {
                auto& vk_buffer = buffer_cache.GetStreamBuffer();
                const u32 ubo_size = stage.FlatUserData().size() * sizeof(u32);
                if (FrameCapture::Active()) {
                    FrameCapture::Buffer(stage.pgm_hash, binding.buffer, 0,
                                         stage.FlatUserData().data(), ubo_size);
                }
                const u64 offset =
                    vk_buffer.Copy(stage.FlatUserData().data(), ubo_size, alignment);
                infos.push_back({&vk_buffer, offset, ubo_size, false});
            } else if (desc.buffer_type == Shader::BufferType::ClipPlanes) {
                // Permutations compiled without enabled planes never read the buffer, so the
                // declared binding is satisfied with a null descriptor instead of a copy.
                if (Regs().clipper_control.user_clip_plane_enable == 0) {
                    infos.push_back({nullptr, 0, 0, false});
                } else {
                    auto& vk_buffer = buffer_cache.GetStreamBuffer();
                    std::array<float, AmdGpu::NUM_CLIP_PLANES * 4> planes{};
                    for (u32 i = 0; i < AmdGpu::NUM_CLIP_PLANES; ++i) {
                        const auto& plane = Regs().clip_user_data[i];
                        planes[i * 4 + 0] = std::bit_cast<float>(plane.data_x);
                        planes[i * 4 + 1] = std::bit_cast<float>(plane.data_y);
                        planes[i * 4 + 2] = std::bit_cast<float>(plane.data_z);
                        planes[i * 4 + 3] = std::bit_cast<float>(plane.data_w);
                    }
                    const u32 ubo_size = static_cast<u32>(sizeof(planes));
                    const u64 offset = vk_buffer.Copy(planes.data(), ubo_size, alignment);
                    infos.push_back({&vk_buffer, offset, ubo_size, false});
                }
            } else if (desc.buffer_type == Shader::BufferType::BdaPagetable) {
                const auto* bda_buffer = buffer_cache.GetBdaPageTableBuffer();
                infos.push_back({bda_buffer, 0, bda_buffer->SizeBytes(), false});
            } else if (desc.buffer_type == Shader::BufferType::FaultBuffer) {
                const auto* fault_buffer = buffer_cache.GetFaultBuffer();
                infos.push_back({fault_buffer, 0, fault_buffer->SizeBytes(), false});
            } else if (desc.buffer_type == Shader::BufferType::SharedMemory) {
                auto& lds_buffer = buffer_cache.GetStreamBuffer();
                const auto& cs_program = CsRegs();
                const auto lds_size = cs_program.SharedMemSize() * cs_program.NumWorkgroups();
                const auto [data, offset] = lds_buffer.Map(lds_size, alignment);
                std::memset(data, 0, lds_size);
                lds_buffer.Commit();
                infos.push_back({&lds_buffer, offset, lds_size, false});
            } else {
                UNREACHABLE_MSG("Unexpected buffer type {}", u32(desc.buffer_type));
            }
        } else {
            const auto vsharp =
                prepared ? prepared->buffer_sharps[buffer_index] : desc.GetSharp(stage);
            if (vsharp.GetSize() == 864 && gbuffer_draw &&
                memory->IsValidGpuMapping(vsharp.base_address, 0)) {
                camera_motion->OnConstants(reinterpret_cast<const float*>(vsharp.base_address));
            }
            // Object motion research: the vertex shader buffers of G-buffer draws.
            if (FrameCapture::Active() && gbuffer_draw && stage.sw_stage == Shader::SwStage::Vertex &&
                vsharp.base_address != 0 && memory->IsValidGpuMapping(vsharp.base_address, 0)) {
                const auto* f = reinterpret_cast<const float*>(vsharp.base_address);
                char note[192];
                std::snprintf(note, sizeof(note),
                              "\n  gb vs %08x slot %u at %#llx size %u stride %u: %g %g %g %g",
                              u32(stage.pgm_hash), binding.buffer,
                              (unsigned long long)vsharp.base_address, u32(vsharp.GetSize()),
                              u32(vsharp.GetStride()), f[0], f[1], f[2], f[3]);
                FrameCapture::Note(note);
            }
            if (FrameCapture::Active() && vsharp.base_address != 0 && vsharp.GetSize() != 0 &&
                memory->IsValidGpuMapping(vsharp.base_address, 0)) {
                FrameCapture::Buffer(stage.pgm_hash, binding.buffer, vsharp.base_address,
                                     reinterpret_cast<const void*>(vsharp.base_address),
                                     memory->ClampRangeSize(vsharp.base_address, vsharp.GetSize()));
            }
            if (vsharp.base_address == 0 || vsharp.GetSize() == 0) {
                infos.push_back({nullptr, 0, 0, false});
            } else if (Shader::IsPagedBuffer(VideoCore::BufferCache::LayerPagedActive(), vsharp)) {
                // bbport BB_LAYER_MEMORY: nearly all memory as one buffer: the shader reaches it
                // through the page table; the descriptor holds its record. It may touch any
                // buffer of the game's memory: barriers before it, and after it if it writes.
                const auto [record, record_offset] = buffer_cache.LayerPagedRecord(
                    vsharp.base_address, vsharp.GetSize(), desc.is_written);
                if (capture_ring_scope && FrameCapture::NativeRecording())
                    CapturePagedMapping(stage, vsharp.base_address, vsharp.GetSize(), desc.is_written);
                // BB_LAYER_PAGED_STATS=1: paged bindings per shader, every 5 s.
                static const bool paged_stats = std::getenv("BB_LAYER_PAGED_STATS") != nullptr;
                if (paged_stats) {
                    static std::map<std::pair<u64, u64>, std::pair<u64, u64>> counts;
                    static u32 printed = 0;
                    auto& c = counts[{stage.pgm_hash,
                                      (vsharp.base_address << 1) | u64(desc.is_written)}];
                    ++c.first;
                    c.second = vsharp.GetSize();
                    const u32 now = BbStats::coarse_second.load(std::memory_order_relaxed);
                    if (now - printed >= 5) {
                        printed = now;
                        for (const auto& [key, value] : counts) {
                            std::printf("Paged binding: shader %016llx buffer %#llx+%#llx %s: %llu "
                                        "in 5 s\n",
                                        (unsigned long long)key.first,
                                        (unsigned long long)(key.second >> 1),
                                        (unsigned long long)value.second,
                                        (key.second & 1) ? "written" : "read",
                                        (unsigned long long)value.first);
                        }
                        counts.clear();
                    }
                }
                push_data.AddOffset(binding.buffer, 0);
                infos.push_back({record, record_offset, 32, false});
                bound_buffers.emplace_back(nullptr, 0, 0, desc.is_written, u32(stage.sw_stage), stage.pgm_hash, 0);
                pending_untracked = true;
                if (desc.is_written) {
                    texture_cache.InvalidateMemoryFromGPU(
                        vsharp.base_address,
                        memory->ClampRangeSize(vsharp.base_address, vsharp.GetSize()));
                }
                needs_barrier = true;
            } else {
                const u64 size = memory->ClampRangeSize(vsharp.base_address, vsharp.GetSize());
                if (size != vsharp.GetSize()) {
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);
                }
                auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);
                if(desc.is_written&&buffer_cache.PrepareImageAliasesForRawWrite(vsharp.base_address,size,buffer,offset)==BufferCache::RawPrewriteResult::Rebind)
                    std::tie(buffer,offset)=buffer_cache.ObtainBuffer(vsharp.base_address,size,true,desc.is_formatted);
                TraceShaderBinding(stage, vsharp.base_address, size, desc.is_written, buffer_cache);
                const u64 offset_aligned = Common::AlignDown(offset, alignment);
                const u64 adjust = offset - offset_aligned;
                if (adjust % 4 != 0) {
                    LOG_WARNING(Render_Vulkan, "Buffer binding in shader {:#x} isn't dword aligned",
                                stage.pgm_hash);
                }
                push_data.AddOffset(binding.buffer, adjust);
                // bbport BB_LAYER_MEMORY: a huge binding may come back cut at the end of its
                // guest chunk's buffer.
                infos.push_back({buffer, offset_aligned,
                                 std::min<u64>(size + adjust, buffer->SizeBytes() - offset_aligned),
                                 false});
                bound_buffers.emplace_back(buffer, offset, size, desc.is_written, u32(stage.sw_stage), stage.pgm_hash, vsharp.base_address);
                NoteGuestAccess(vsharp.base_address, size, desc.is_written);
                if (desc.is_written) {
                    // Raw storage-buffer writes can also make an aliased cached image stale.
                    texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, size);
                }
                needs_barrier |= backend.IsBufferAccessed(buffer, offset, size, desc.is_written);
            }
        }

        ++binding.buffer;
    }
    if (!infos.empty()) {
        BindBufferSet({infos.data(), infos.size()}, binding, write_index);
    }
}

namespace {
// bbport: BB_RES_STATS=1 (diagnostics) — what the bound shaders read their resource descriptors
// from: user data registers (state the API call set) or tables in memory (SRT walker), by kind,
// per draw and dispatch; printed every 5 s. Measures the step "descriptors read by the GPU".
struct ResStats {
    u64 calls = 0, stages = 0, walker_stages = 0, readconst_stages = 0, dma_stages = 0;
    u64 flat_dwords = 0;
    // [kind][source]: kind 0 guest buffers, 1 images, 2 samplers; source 0 registers or
    // immediates, 1 memory (SRT), 2 mixed.
    u64 sharps[3][3]{};
    u64 buffers_written = 0, buffers_formatted = 0, images_written = 0, special_buffers = 0;
    std::unordered_set<u64> shaders, walker_shaders;
};
template <typename Fetch>
int SharpSource(const Fetch& fetch) {
    bool reg = false, mem = false;
    u8 mask = fetch.load_mask;
    for (u32 i = 0; i < fetch.offsets.size(); ++i, mask >>= 1) {
        if (!(mask & 1)) {
            continue;
        }
        (fetch.offsets[i] < Shader::NUM_USER_DATA_REGS ? reg : mem) = true;
    }
    return mem ? (reg ? 2 : 1) : 0;
}
void NoteResStats(const PipelineBase* pipeline) {
    static const bool enabled = [] {
        const char* env = std::getenv("BB_RES_STATS");
        return env && env[0] == '1';
    }();
    if (!enabled) {
        return;
    }
    static std::mutex mutex;
    static ResStats s;
    static auto window = std::chrono::steady_clock::now();
    std::scoped_lock lk{mutex};
    ++s.calls;
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        ++s.stages;
        s.shaders.insert(stage->pgm_hash);
        if (stage->srt_info.walker_func) {
            ++s.walker_stages;
            s.walker_shaders.insert(stage->pgm_hash);
        }
        s.readconst_stages += stage->has_readconst ? 1 : 0;
        s.dma_stages += stage->uses_dma ? 1 : 0;
        s.flat_dwords += stage->srt_info.flattened_bufsize_dw;
        for (const auto& b : stage->buffers) {
            if (b.IsSpecial()) {
                ++s.special_buffers;
                continue;
            }
            ++s.sharps[0][SharpSource(b.sharp_fetch)];
            s.buffers_written += b.is_written;
            s.buffers_formatted += b.is_formatted;
        }
        for (const auto& i : stage->images) {
            ++s.sharps[1][SharpSource(i.sharp_fetch)];
            s.images_written += i.is_written;
        }
        for (const auto& smp : stage->samplers) {
            ++s.sharps[2][SharpSource(smp.sharp_fetch)];
        }
    }
    const auto now = std::chrono::steady_clock::now();
    const double seconds = std::chrono::duration<double>(now - window).count();
    if (seconds < 5.0) {
        return;
    }
    window = now;
    const double c = std::max<double>(1, s.calls);
    std::printf("Resource stats: %.0f binds/s, %.2f stages/bind, %.2f with SRT walker (%zu of %zu "
                "shaders), readconst %.2f, dma %.2f, flat %.1f dw/bind; per bind: buffers "
                "reg %.2f mem %.2f mixed %.2f (written %.2f, formatted %.2f, special %.2f), "
                "images reg %.2f mem %.2f mixed %.2f (written %.2f), samplers reg %.2f mem %.2f "
                "mixed %.2f\n",
                s.calls / seconds, s.stages / c, s.walker_stages / c, s.walker_shaders.size(),
                s.shaders.size(), s.readconst_stages / c, s.dma_stages / c, s.flat_dwords / c,
                s.sharps[0][0] / c, s.sharps[0][1] / c, s.sharps[0][2] / c, s.buffers_written / c,
                s.buffers_formatted / c, s.special_buffers / c, s.sharps[1][0] / c,
                s.sharps[1][1] / c, s.sharps[1][2] / c, s.images_written / c, s.sharps[2][0] / c,
                s.sharps[2][1] / c, s.sharps[2][2] / c);
    s = {};
}
} // namespace

bool RasterizerCore::BindResources(const PipelineBase* pipeline, bool direct_dispatch) {
    BB_SECTION(BindResources);
    NoteResStats(pipeline);
    {
        // bbport BB_HLE_STATS=1 (diagnostics): how often the compute shader substitutions fire.
        static const bool hle_stats = std::getenv("BB_HLE_STATS") != nullptr;
        // BB_HLE_OFF=mask (experiment): 1 image copy, 2 metadata clear, 4 image clear run as the
        // game's shaders.
        static const u32 hle_off = [] {
            const char* env = std::getenv("BB_HLE_OFF");
            return env ? u32(std::strtoul(env, nullptr, 0)) : 0u;
        }();
        static const bool copy_log = std::getenv("BB_IMAGE_COPY_LOG") != nullptr;
        if ((hle_off & 1) && copy_log) {
            IsComputeImageCopy(pipeline, true);
        }
        const int which = !(hle_off & 1) && IsComputeImageCopy(pipeline)   ? 1
                          : !(hle_off & 2) && IsComputeMetaClear(pipeline) ? 2
                          : !(hle_off & 4) && IsComputeImageClear(pipeline) ? 3
                                                                             : 0;
        if (hle_stats) {
            static std::array<u64, 4> counts{};
            static auto last = std::chrono::steady_clock::now();
            ++counts[which];
            if (std::chrono::steady_clock::now() - last > std::chrono::seconds(5)) {
                last = std::chrono::steady_clock::now();
                std::printf("HLE stats (5 s): image copy %llu, meta clear %llu, image clear %llu, "
                            "other binds %llu\n",
                            (unsigned long long)counts[1], (unsigned long long)counts[2],
                            (unsigned long long)counts[3], (unsigned long long)counts[0]);
                counts = {};
            }
        }
        if (which) {
            return false;
        }
    }

    // bbport: G-buffer passes sample material textures for a scene rendered below the output
    // size; the temporal upscaler restores the detail of the output's mip level (FSR guide:
    // log2(render / output)). Shadows, post-processing and UI keep the guest's bias.
    sampler_lod_bias = 0.0f;
    pipeline_is_compute = pipeline->IsCompute();
    if (!pipeline->IsCompute() && !BbToggle::Disabled(BbToggle::SceneMipBias) &&
        std::popcount(pipeline->GetGraphicsKey().mrt_mask &
                      0xff) >= 5) {
        sampler_lod_bias = upscaler->SceneMipBias();
        static float reported = 0.0f;
        if (sampler_lod_bias != reported) {
            reported = sampler_lod_bias;
            std::printf("Upscaler: scene texture LOD bias %.2f\n", sampler_lod_bias);
        }
    }

    bool uses_dma = false;

    static const bool stats = std::getenv("BB_FRAME_STATS") != nullptr;
    if (stats && ((helper_full + helper_partial + helper_serial) & 1023) == 0) {
        static auto window = std::chrono::steady_clock::now();
        const auto now = std::chrono::steady_clock::now();
        if (now - window >= std::chrono::seconds(5)) {
            window = now;
            const double forks = std::max<double>(1, helper_full + helper_partial);
            std::printf("Texture helper: %llu binds in parallel, %llu partly, %llu serial; "
                        "per fork %.0f cycles helper task, %.0f cycles GPU thread wait\n",
                        static_cast<unsigned long long>(helper_full),
                        static_cast<unsigned long long>(helper_partial),
                        static_cast<unsigned long long>(helper_serial),
                        bind_helper.task_cycles.exchange(0) / forks,
                        bind_helper.wait_cycles / forks);
            bind_helper.wait_cycles = 0;
            helper_full = helper_partial = helper_serial = 0;
        }
    }

    // Bind resource buffers and textures.
    Shader::Backend::Bindings binding{};
    push_data = MakeUserData(Regs());
    u32 total_descriptors = 0;
    for (const auto* stage : pipeline->GetStages()) {
        if (stage) {
            total_descriptors += static_cast<u32>(stage->buffers.size() + stage->images.size() +
                                                  stage->samplers.size());
        }
    }
    BeginResourceBindings(total_descriptors);
    u32 write_index = 0;
    if (!HelperEligible(pipeline)) {
        ++helper_serial;
        for (const auto* stage : pipeline->GetStages()) {
            if (!stage) {
                continue;
            }
            stage->PushUd(binding, push_data);
            const PreparedStage* prepared = FindPreparedStage(*stage);
            BindBuffers(*stage, prepared, binding, push_data, write_index, direct_dispatch);
            BindTextures(*stage, prepared, binding, write_index, needs_barrier);
            uses_dma |= stage->uses_dma;
        }
    } else {
        // bbport: textures on the helper, buffers here. Without mip arrays every stage's
        // descriptors are [buffers][images][samplers], one each, so both sides know their
        // binding numbers and descriptor write slots up front.
        struct BufferStage {
            const Shader::Info* info;
            const PreparedStage* prepared;
            Shader::Backend::Bindings binding;
            u32 write_index;
        };
        std::array<BufferStage, Shader::MaxStageTypes> buffer_stages;
        auto& task = texture_task;
        task.count = 0;
        task.completed = 0;
        task.barrier = false;
        for (const auto* stage : pipeline->GetStages()) {
            if (!stage) {
                continue;
            }
            const u32 num_buffers = static_cast<u32>(stage->buffers.size());
            const u32 num_descriptors =
                num_buffers + static_cast<u32>(stage->images.size() + stage->samplers.size());
            stage->PushUd(binding, push_data);
            const PreparedStage* prepared = FindPreparedStage(*stage);
            buffer_stages[task.count] = {stage, prepared, binding, write_index};
            auto texture_binding = binding;
            texture_binding.buffer += num_buffers;
            texture_binding.unified += num_buffers;
            task.stages[task.count] = {stage, prepared, texture_binding,
                                       write_index + num_buffers};
            ++task.count;
            binding.buffer += num_buffers;
            binding.unified += num_descriptors;
            write_index += num_descriptors;
            uses_dma |= stage->uses_dma;
        }
        bind_helper.Fork(&RunTextureTask, this);
        for (u32 i = 0; i < task.count; ++i) {
            auto& stage = buffer_stages[i];
            BindBuffers(*stage.info, stage.prepared, stage.binding, push_data, stage.write_index,
                        direct_dispatch);
        }
        if (draw_inputs.pending && !BbToggle::Disabled(BbToggle::EarlyDrawInputs)) {
            ResolveVertexBuffers(draw_inputs.pipeline, draw_inputs.prepared);
            if (draw_inputs.is_indexed) {
                ResolveIndexBuffer(draw_inputs.index_offset);
            }
            draw_inputs.resolved = true;
        }
        bind_helper.Join();
        needs_barrier |= task.barrier;
        ++(task.completed == task.count ? helper_full
                                        : task.completed ? helper_partial : helper_serial);
        // Stages the helper left (an image needing work that records commands).
        for (u32 i = task.completed; i < task.count; ++i) {
            auto& stage = task.stages[i];
            u32 write_index = stage.write_index;
            BindTextures(*stage.info, stage.prepared, stage.binding, write_index, needs_barrier);
        }
    }

    if (uses_dma) {
        buffer_cache.SynchronizeDmaBuffers();
        fault_process_pending = true;
    }

    return true;
}

void RasterizerCore::UpdateViewportScissorState(ViewportScissors& state) const {
    const auto& regs = Regs();

    const auto combined_scissor_value_tl = [](s16 scr, s16 win, s16 gen, s16 win_offset) {
        return std::max({scr, s16(win + win_offset), s16(gen + win_offset)});
    };
    const auto combined_scissor_value_br = [](s16 scr, s16 win, s16 gen, s16 win_offset) {
        return std::min({scr, s16(win + win_offset), s16(gen + win_offset)});
    };
    const bool enable_offset = !regs.window_scissor.window_offset_disable;

    AmdGpu::Scissor scsr{};
    scsr.top_left_x = combined_scissor_value_tl(
        regs.screen_scissor.top_left_x, s16(regs.window_scissor.top_left_x),
        s16(regs.generic_scissor.top_left_x),
        enable_offset ? regs.window_offset.window_x_offset : 0);
    scsr.top_left_y = combined_scissor_value_tl(
        regs.screen_scissor.top_left_y, s16(regs.window_scissor.top_left_y),
        s16(regs.generic_scissor.top_left_y),
        enable_offset ? regs.window_offset.window_y_offset : 0);
    scsr.bottom_right_x = combined_scissor_value_br(
        regs.screen_scissor.bottom_right_x, regs.window_scissor.bottom_right_x,
        regs.generic_scissor.bottom_right_x,
        enable_offset ? regs.window_offset.window_x_offset : 0);
    scsr.bottom_right_y = combined_scissor_value_br(
        regs.screen_scissor.bottom_right_y, regs.window_scissor.bottom_right_y,
        regs.generic_scissor.bottom_right_y,
        enable_offset ? regs.window_offset.window_y_offset : 0);

    auto& viewports = state.viewports;
    auto& scissors = state.scissors;
    viewports.clear();
    scissors.clear();

    if (regs.polygon_control.enable_window_offset &&
        (regs.window_offset.window_x_offset != 0 || regs.window_offset.window_y_offset != 0)) {
        LOG_ERROR(Render_Vulkan,
                  "PA_SU_SC_MODE_CNTL.VTX_WINDOW_OFFSET_ENABLE support is not yet implemented.");
    }

    const auto& vp_ctl = regs.viewport_control;
    for (u32 i = 0; i < AmdGpu::NUM_VIEWPORTS; i++) {
        const auto& vp = regs.viewports[i];
        const auto& vp_d = regs.viewport_depths[i];
        if (vp.xscale == 0) {
            continue;
        }

        const auto zoffset = vp_ctl.zoffset_enable ? vp.zoffset : 0.f;
        const auto zscale = vp_ctl.zscale_enable ? vp.zscale : 1.f;

        vk::Viewport viewport{};

        // https://gitlab.freedesktop.org/mesa/mesa/-/blob/209a0ed/src/amd/vulkan/radv_pipeline_graphics.c#L688-689
        // https://gitlab.freedesktop.org/mesa/mesa/-/blob/209a0ed/src/amd/vulkan/radv_cmd_buffer.c#L3103-3109
        // When the clip space is ranged [-1...1], the zoffset is centered.
        // By reversing the above viewport calculations, we get the following:
        if (regs.clipper_control.clip_space == AmdGpu::ClipSpace::MinusWToW) {
            viewport.minDepth = zoffset - zscale;
            viewport.maxDepth = zoffset + zscale;
        } else {
            viewport.minDepth = zoffset;
            viewport.maxDepth = zoffset + zscale;
        }

        if (!backend.Limits().depth_range_unrestricted) {
            // Unrestricted depth range not supported by device. Restrict to valid range.
            viewport.minDepth = std::max(viewport.minDepth, 0.f);
            viewport.maxDepth = std::min(viewport.maxDepth, 1.f);
        }

        if (regs.IsClipDisabled()) {
            // In case if clipping is disabled we patch the shader to convert vertex position
            // from screen space coordinates to NDC by defining a render space as full hardware
            // window range [0..16383, 0..16383] and setting the viewport to its size.
            viewport.x = 0.f;
            viewport.y = 0.f;
            viewport.width = float(std::min<u32>(backend.Limits().max_viewport_width, 16_KB));
            viewport.height = float(std::min<u32>(backend.Limits().max_viewport_height, 16_KB));
        } else {
            const auto xoffset = vp_ctl.xoffset_enable ? vp.xoffset : 0.f;
            const auto xscale = vp_ctl.xscale_enable ? vp.xscale : 1.f;
            const auto yoffset = vp_ctl.yoffset_enable ? vp.yoffset : 0.f;
            const auto yscale = vp_ctl.yscale_enable ? vp.yscale : 1.f;

            // bbport: sub-pixel jitter of scene geometry for the temporal upscaler; the same
            // shift as jittering the projection.
            viewport.x = (xoffset - xscale) * target_scale[0] + draw_jitter[0];
            viewport.y = (yoffset - yscale) * target_scale[1] + draw_jitter[1];
            viewport.width = xscale * 2.0f * target_scale[0];
            viewport.height = yscale * 2.0f * target_scale[1];
        }

        viewports.push_back(viewport);

        auto vp_scsr = scsr;
        if (regs.mode_control.vport_scissor_enable) {
            vp_scsr.top_left_x =
                std::max(vp_scsr.top_left_x, s16(regs.viewport_scissors[i].top_left_x));
            vp_scsr.top_left_y =
                std::max(vp_scsr.top_left_y, s16(regs.viewport_scissors[i].top_left_y));
            vp_scsr.bottom_right_x = std::min(AmdGpu::Scissor::Clamp(vp_scsr.bottom_right_x),
                                              regs.viewport_scissors[i].bottom_right_x);
            vp_scsr.bottom_right_y = std::min(AmdGpu::Scissor::Clamp(vp_scsr.bottom_right_y),
                                              regs.viewport_scissors[i].bottom_right_y);
        }
        if (target_scale[0] != 1.0f || target_scale[1] != 1.0f) {
            const auto scale = [](s32 v, float f) { return s32(std::lround(float(v) * f)); };
            const s32 x0 = scale(vp_scsr.top_left_x, target_scale[0]);
            const s32 y0 = scale(vp_scsr.top_left_y, target_scale[1]);
            const s32 x1 = scale(vp_scsr.top_left_x + s32(vp_scsr.GetWidth()), target_scale[0]);
            const s32 y1 = scale(vp_scsr.top_left_y + s32(vp_scsr.GetHeight()), target_scale[1]);
            scissors.push_back({
                .offset = {x0, y0},
                .extent = {u32(std::max(x1 - x0, 0)), u32(std::max(y1 - y0, 0))},
            });
        } else {
            scissors.push_back({
                .offset = {vp_scsr.top_left_x, vp_scsr.top_left_y},
                .extent = {vp_scsr.GetWidth(), vp_scsr.GetHeight()},
            });
        }
    }

    if (viewports.empty()) {
        // Vulkan requires providing at least one viewport.
        constexpr vk::Viewport empty_viewport = {
            .x = -1.0f,
            .y = -1.0f,
            .width = 1.0f,
            .height = 1.0f,
            .minDepth = 0.0f,
            .maxDepth = 1.0f,
        };
        constexpr vk::Rect2D empty_scissor = {
            .offset = {0, 0},
            .extent = {1, 1},
        };
        viewports.push_back(empty_viewport);
        scissors.push_back(empty_scissor);
    }
}

void RasterizerCore::UpdateDynamicState(DynamicDrawState& state, const bool is_indexed) const {
    BB_SECTION(UpdateDynamicState);
    UpdateViewportScissorState(state.viewport_scissors);
    UpdateDepthStencilState(state.depth_stencil);
    UpdatePrimitiveState(state.primitive, is_indexed);
}

} // namespace VideoCore
