// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#ifndef __OBJC__
#error "mtl_runtime_impl.h is for Objective-C++ (.mm) files only"
#endif

#import <Metal/Metal.h>

#include <map>
#include <functional>
#include <memory>
#include <mutex>
#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

#include "video_core/renderer_metal/msl_host_shader.h"
#include "video_core/renderer_metal/mtl_host_pso_recipe.h"
#include "video_core/renderer_metal/mtl_fixed_helper_seeds.h"
#include "video_core/renderer_metal/mtl_library_origin.h"
#include "video_core/renderer_metal/mtl_format_convert_impl.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"

namespace Metal {

/// The Metal object behind a cache object's `native` slot (a retained pointer, see
/// Runtime::CreateBuffer and CreateImage); not retained again here.
inline id<MTLBuffer> NativeBuffer(const VideoCore::Buffer& buffer) {
    return (__bridge id<MTLBuffer>)buffer.buffer.native;
}
inline id<MTLTexture> NativeTexture(const VideoCore::Image& image) {
    return (__bridge id<MTLTexture>)image.backing->image.native;
}

/// The caches' own kernels, built from the D11 host shaders (generated MSL headers) on first use:
/// tiling and detiling, the fault buffer parser, the 32 bit buffer fill (MSL of this file) and the
/// fullscreen-triangle passes of the MSAA copies (what Vulkan::BlitHelper does). One compute
/// pipeline per (shader, function constants).
class HostKernels {
public:
    HostKernels(Device::Impl& dev, Scheduler& scheduler);
    ~HostKernels();
    void BindPsoCache(PsoCache* cache);
    HostPso::WarmResult WarmPipelines();
    // Startup-only standalone worker: fixed complete requests, no Storage,
    // renderer owner slots, guest memory, framebuffers or extra state maps.
    HostPso::FixedSeedResult PrepareFixedSeeds(bool allow_compile,
        const std::function<bool()>& cancelled);
    HostPso::TilingLibraryResult PrepareTilingLibraries(const std::function<bool()>& cancelled);
    // Shared original source/options/typed-entry factory; no native PSO creation.
    id<MTLLibrary> TextureTilingLibrary(const HostShaders::Shader& shader,bool half,
        MTLFunctionConstantValues* values,id<MTLFunction> __strong* function,
        LocalLibrary::Origin* origin,NSError** error);
    HostPso::WarmResult FinishPreparation();
    const HostPso::WarmResult& PreparationResult()const{return warm_result;}
    void ConfigureFaultParser(u32 page_bits,u32 max_faults);

    /// The pipeline of the tiling kernel for `dispatch` and the shader it came from (the
    /// buffer slots); null pipeline when it did not compile (logged).
    id<MTLComputePipelineState> Tiling(const VideoCore::TilingDispatch& dispatch,
                                       const HostShaders::Shader*& shader);
    id<MTLComputePipelineState> TextureTiling(const VideoCore::TilingDispatch& dispatch, bool proxy);
    /// Builds the fault parser pipeline for bitmaps of 1 bit per `1 << page_bits` bytes.
    bool CreateFaultParser(u32 page_bits, u32 max_faults);
    [[nodiscard]] id<MTLComputePipelineState> FaultParser();
    /// `kernel fill_u32(device uint* dst [[buffer(0)]], constant uint& value [[buffer(1)]],
    /// constant uint& count [[buffer(2)]])`, one thread per word.
    id<MTLComputePipelineState> FillU32();
    // Uses the original upscaler's merge slot; preparation returns only a temporary
    // state, with no Upscaler/MetalFX/texture construction and no extra native map.
    id<MTLComputePipelineState> UpscaleMerge(NSError** error=nullptr);
    id<MTLComputePipelineState> CameraMotionPipeline(NSError** error=nullptr);
    // The original Converter owns rgb/depth slots. No buffers, Converter or
    // extra active map are constructed by loading-period preparation.
    id<MTLComputePipelineState> FormatConversionPipeline(bool depth,NSError** error=nullptr);
    // Presenter keeps its original slot and may reuse the existing shared vertex
    // object+actual origin. Warm replay omits it and builds the same two stages.
    id<MTLRenderPipelineState> PostProcessPipeline(MTLPixelFormat format,NSError** error=nullptr,
        id<MTLFunction> shared_vertex=nil,const LocalLibrary::Origin* shared_origin=nullptr);
    /// Optional <=256-byte ordered update, direct bound destination, no guest BDA lookup.
    id<MTLComputePipelineState> InlineUpdate();
    id<MTLRenderPipelineState> RenderInlineUpdate();

    /// The slot of the (set 0) `binding` among the buffers of `shader`.
    static u32 BufferSlot(const HostShaders::Shader& shader, u32 binding);

    /// Copies `src` (level 0, slice 0) into `dst` with a fullscreen pass: from a multisampled
    /// source (`src_msaa`, sample by sample) or a single sampled one (the color to every sample)
    /// into a color target of `samples` samples. Records a render pass and ends it.
    void CopyBetweenMsImages(id<MTLTexture> src, id<MTLTexture> dst, u32 samples, bool src_msaa);
    /// Writes channel `i` of the color texture `src` as the depth of sample `i` of `dst`, a
    /// multisampled depth target. Records a render pass and ends it.
    void ColorToMsDepth(id<MTLTexture> src, id<MTLTexture> dst);

    /// The MTLLibrary of a host shader (compiled once).
    id<MTLLibrary> Library(const HostShaders::Shader& shader, MTLFunctionConstantValues* values, bool source_only=false, id<MTLFunction> __strong* prepared=nullptr, LocalLibrary::Origin* origin=nullptr);
    /// The function of a host shader with the function constants of `constants` (indexed by the
    /// constant's id; ids beyond `count` keep the shader's default).
    id<MTLFunction> Function(const HostShaders::Shader& shader, const u32* constants, u32 count, LocalLibrary::Origin* origin=nullptr);

private:
    id<MTLComputePipelineState> ComputePipeline(const HostShaders::Shader& shader,
                                                const u32* constants, u32 count,
                                                NSString* label, HostPso::Invocation invocation);
    id<MTLRenderPipelineState> MsCopyPipeline(u32 samples, MTLPixelFormat format, bool src_msaa);
    id<MTLRenderPipelineState> MsDepthPipeline(u32 samples, MTLPixelFormat format);

    id Descriptor(const HostPso::Invocation& invocation) const;
    std::optional<HostPso::Recipe> Describe(const HostPso::Invocation& invocation,id descriptor) const;
    id<MTLComputePipelineState> MakeCompute(MTLComputePipelineDescriptor*,const HostPso::Invocation&,std::span<const LocalLibrary::Origin>,NSError**);
    id<MTLRenderPipelineState> MakeRender(MTLRenderPipelineDescriptor*,const HostPso::Invocation&,std::span<const LocalLibrary::Origin>,NSError**);
    void RecordRecipe(const HostPso::Invocation&,id descriptor);
    bool Replay(const HostPso::Recipe&);
    PsoCache* pso_cache{}; // detached before PipelineCache dies; no native state ownership
    bool warming{},fault_known{},fault_with_cache{},warm_allow_compile=true;
    const HostPso::Recipe* expected_recipe{}; // one synchronous preparation only
    HostPso::ArtifactOutcome artifact_outcome{};
    HostPso::WarmResult warm_result{};
    std::array<u32,2> fault_arguments{};

    Device::Impl& dev;
    Scheduler& scheduler;
    struct LibraryRecord {id<MTLLibrary> library=nil;LocalLibrary::Origin origin;};
    std::unordered_map<const void*,LibraryRecord> libraries;
    using TilingKey = std::tuple<u32, bool, std::array<u32, 12>, bool>;
    using LegacyTilingKey = std::tuple<u32, bool, std::array<u32, 12>>;
    // Only HostKernels' out-of-line methods touch or destroy these nodes. Keep
    // the container ABI stable for the other Runtime::Impl translation units.
    static_assert(sizeof(std::map<TilingKey, id<MTLComputePipelineState>>) ==
                  sizeof(std::map<LegacyTilingKey, id<MTLComputePipelineState>>));
    static_assert(alignof(std::map<TilingKey, id<MTLComputePipelineState>>) ==
                  alignof(std::map<LegacyTilingKey, id<MTLComputePipelineState>>));
    static_assert(sizeof(std::map<std::pair<bool,TilingKey>, id<MTLComputePipelineState>>) ==
                  sizeof(std::map<std::pair<bool,LegacyTilingKey>, id<MTLComputePipelineState>>));
    static_assert(alignof(std::map<std::pair<bool,TilingKey>, id<MTLComputePipelineState>>) ==
                  alignof(std::map<std::pair<bool,LegacyTilingKey>, id<MTLComputePipelineState>>));

    std::map<TilingKey, id<MTLComputePipelineState>> tiling;
    std::map<std::pair<bool,TilingKey>, id<MTLComputePipelineState>> texture_tiling;
    id<MTLComputePipelineState> fault_parser = nil;
    id<MTLComputePipelineState> fill_u32 = nil;
    id<MTLComputePipelineState> inline_update = nil;
    bool inline_update_tried{};
    id<MTLRenderPipelineState> render_inline_update=nil;
    bool render_inline_update_tried{};
    std::map<std::tuple<u32, u32, bool>, id<MTLRenderPipelineState>> ms_copy;
    std::map<std::pair<u32, u32>, id<MTLRenderPipelineState>> ms_depth;
    id<MTLDepthStencilState> depth_always_write = nil;
};

struct Runtime::Impl {
    Device::Impl& dev;
    Scheduler::Impl& sched;
    HostKernels kernels;
    u64 render_updates{};
    /// Native chunk buffers handed out by ImportBuffer: shared with the guest memory module, so
    /// they stay resident and only lose the reference the cache object held.
    std::mutex imported_mutex;
    std::unordered_set<const void*> imported;
    /// The Vulkan format of every live texture CreateImage made (views are checked against it).
    std::mutex formats_mutex;
    std::unordered_map<const void*, vk::Format> image_formats;
    std::mutex warned_mutex;
    std::set<std::string> warned; ///< what was logged once

    /// The kernels of the transfers that convert (mtl_format_convert.h), made on the first copy
    /// that needs them. Only the recording calls (UploadImage, DownloadImage, CopyImageWithBuffer,
    /// ClearImage) reach it, and those come from one thread at a time (Runtime's contract), so
    /// the lazy creation needs no lock. Destroyed with the Impl, after ~Runtime's Finish: nothing
    /// retains a pipeline for the GPU.
    FormatConvert::Converter& Convert() {
        if (!converter) {
            converter = std::make_unique<FormatConvert::Converter>(kernels);
        }
        return *converter;
    }
    std::unique_ptr<FormatConvert::Converter> converter;

    Impl(Device& device, Scheduler& scheduler)
        : dev{device.GetImpl()}, sched{scheduler.GetImpl()}, kernels{device.GetImpl(), scheduler} {}

    /// True the first time `what` is asked.
    bool FirstTime(const std::string& what) {
        std::scoped_lock lock{warned_mutex};
        return warned.insert(what).second;
    }
};

} // namespace Metal
