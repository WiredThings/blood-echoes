#include "video_core/renderer_metal/mtl_local_library.h"
#include "video_core/renderer_metal/mtl_game_pso_recipe.h"
#include "runtime_exit_codes.h"
#include <cstdlib>
#include "bb_stutter_audit.h"
#include "video_core/renderer_metal/mtl_compile_audit.h"
// SPDX-License-Identifier: GPL-2.0-or-later

#import <Metal/Metal.h>

#include <algorithm>
#include <atomic>
#include <sys/sysctl.h>
#include "video_core/renderer_metal/mtl_warm_archive_identity.h"
#include <chrono>
#include <cstring>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <vector>
#include <unistd.h>
#include <xxhash.h>

#include "common/assert.h"
#include "common/scope_exit.h"
#include "common/logging/log.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/renderer_metal/mtl_tessellation.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/frame_capture.h"

namespace Metal {

namespace {

using Clock = std::chrono::steady_clock;

// Startup preference only; actual factory consumption revalidates its own exact recipe.
bool HasPreparedArtifact(const std::filesystem::path&, std::string_view);

u64 UsSince(Clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count();
}

std::string FirstLine(NSError* error) {
    if (!error) {
        return "unknown error";
    }
    std::string text = error.localizedDescription.UTF8String;
    const auto end = text.find('\n');
    return end == std::string::npos ? text : text.substr(0, end);
}


// GraphicsPipelineKey deliberately serializes its object representation. Store that
// representation as bytes: ordinary memberwise copies can leave padding indeterminate.
using SerializedGraphicsKey = std::array<u8, sizeof(Vulkan::GraphicsPipelineKey)>;
SerializedGraphicsKey PipelineKeyBytes(const Vulkan::GraphicsPipelineKey& key) {
    SerializedGraphicsKey bytes;
    std::memcpy(bytes.data(), &key, sizeof(key));
    return bytes;
}

struct RenderMapKey {
    SerializedGraphicsKey key;
    const void* vs{}; ///< the MTLFunction objects (the entry retains them: no address reuse)
    const void* fs{};
    std::array<u64, 2> modules{}; ///< per-module binding/layout identities
    u64 attributes_hash{};
    bool rect_list_triangles{};
    bool tessellation{};
    bool geometry{};

    bool operator==(const RenderMapKey& other) const noexcept {
        return modules == other.modules && vs == other.vs && fs == other.fs && attributes_hash == other.attributes_hash &&
               rect_list_triangles == other.rect_list_triangles &&
               tessellation == other.tessellation && geometry == other.geometry &&
               key == other.key;
    }
};

struct RenderMapHash {
    size_t operator()(const RenderMapKey& k) const noexcept {
        u64 h = XXH3_64bits(&k.key, sizeof(k.key));
        h = XXH3_64bits_withSeed(k.modules.data(), sizeof(k.modules), h);
        const u64 rest[4] = {reinterpret_cast<u64>(k.vs), reinterpret_cast<u64>(k.fs),
                             k.attributes_hash,
                             u64(k.rect_list_triangles) | (u64(k.tessellation) << 1) | (u64(k.geometry) << 2)};
        return XXH3_64bits_withSeed(rest, sizeof(rest), h);
    }
};

/// A tessellation pipeline: the stages' functions name the compiled variants.
struct TessMapKey {
    SerializedGraphicsKey key;
    const void* vertex{}; ///< the vertex kernel's function without an index buffer
    const void* control{};
    const void* eval{};
    const void* fs{};
    std::array<u64, 4> modules{}; ///< per-module binding/layout identities
    u64 attributes_hash{};

    bool operator==(const TessMapKey& other) const noexcept {
        return modules == other.modules && vertex == other.vertex && control == other.control && eval == other.eval &&
               fs == other.fs && attributes_hash == other.attributes_hash && key == other.key;
    }
};

struct TessMapHash {
    size_t operator()(const TessMapKey& k) const noexcept {
        u64 h = XXH3_64bits(&k.key, sizeof(k.key));
        h = XXH3_64bits_withSeed(k.modules.data(), sizeof(k.modules), h);
        const u64 rest[5] = {reinterpret_cast<u64>(k.vertex), reinterpret_cast<u64>(k.control),
                             reinterpret_cast<u64>(k.eval), reinterpret_cast<u64>(k.fs),
                             k.attributes_hash};
        return XXH3_64bits_withSeed(rest, sizeof(rest), h);
    }
};

/// A geometry pipeline: the stages' functions name the compiled variants.
struct GeometryMapKey {
    SerializedGraphicsKey key;
    const void* es{}; ///< the export kernel's function without an index buffer
    const void* gs{};
    const void* pass_through{};
    const void* fs{};
    std::array<u64, 4> modules{}; ///< per-module binding/layout identities
    u64 attributes_hash{};

    bool operator==(const GeometryMapKey& other) const noexcept {
        return modules == other.modules && es == other.es && gs == other.gs && pass_through == other.pass_through &&
               fs == other.fs && attributes_hash == other.attributes_hash && key == other.key;
    }
};

struct GeometryMapHash {
    size_t operator()(const GeometryMapKey& k) const noexcept {
        u64 h = XXH3_64bits(&k.key, sizeof(k.key));
        h = XXH3_64bits_withSeed(k.modules.data(), sizeof(k.modules), h);
        const u64 rest[5] = {reinterpret_cast<u64>(k.es), reinterpret_cast<u64>(k.gs),
                             reinterpret_cast<u64>(k.pass_through), reinterpret_cast<u64>(k.fs),
                             k.attributes_hash};
        return XXH3_64bits_withSeed(rest, sizeof(rest), h);
    }
};

struct ComputeMapKey {
    size_t value{};
    const void* function{};
    u64 module{};
    bool operator==(const ComputeMapKey&) const = default;
};

struct ComputeMapHash {
    size_t operator()(const ComputeMapKey& k) const noexcept {
        const u64 data[3] = {k.value, reinterpret_cast<u64>(k.function), k.module};
        return XXH3_64bits(data, sizeof(data));
    }
};

struct DepthStencilHash {
    size_t operator()(const DepthStencilKey& k) const noexcept {
        const auto face = [](const DepthStencilKey::Face& f) {
            return (u64(f.fail_op) << 0) | (u64(f.pass_op) << 4) | (u64(f.depth_fail_op) << 8) |
                   (u64(f.compare_op) << 12) | (u64(f.compare_mask) << 16) |
                   (u64(f.write_mask) << 24);
        };
        const u64 data[3] = {u64(k.depth_test) | (u64(k.depth_write) << 1) |
                                 (u64(k.depth_compare) << 2) | (u64(k.stencil_test) << 6),
                             face(k.front), face(k.back)};
        return XXH3_64bits(data, sizeof(data));
    }
};

std::string StageHashes(const Vulkan::GraphicsPipelineKey& key) {
    return fmt::format("vs {:#x} fs {:#x}", key.stage_hashes[u32(Shader::SwStage::Vertex)],
                       key.stage_hashes[u32(Shader::SwStage::Fragment)]);
}

} // namespace

struct PsoCache::Impl {
    id<MTLDevice> mtl_device;

    std::shared_mutex mutex; ///< guards the three maps
    std::unordered_map<RenderMapKey, std::unique_ptr<RenderPipeline>, RenderMapHash> render;
    std::unordered_map<ComputeMapKey, std::unique_ptr<ComputePipeline>, ComputeMapHash> compute;
    std::unordered_map<TessMapKey, std::unique_ptr<TessellationPipeline>, TessMapHash> tessellation;
    std::unordered_map<GeometryMapKey, std::unique_ptr<GeometryPipeline>, GeometryMapHash> geometry;
    std::unordered_map<DepthStencilKey, id<MTLDepthStencilState>, DepthStencilHash> depth_stencil;

    std::mutex debug_mutex; ///< BB_GS_DEBUG: the magenta fragment functions by source
    std::map<std::string, std::unique_ptr<CompiledShader>> debug_fragments;

    std::mutex archive_mutex; ///< MTLBinaryArchive is not documented as thread-safe
    id<MTLBinaryArchive> archive; ///< nil: none (yet / any more)
    bool archive_dirty{};
    bool persist_enabled{};
    bool archive_error_logged{};
    std::filesystem::path archive_snapshot;
    // Descriptors retain only existing functions and CPU metadata, never texture/buffer data.
    // Their binaryArchives arrays are cleared, so they cannot keep the read archive alive.
    std::vector<id> archive_descriptors;
};

RenderPipeline::~RenderPipeline() {
    // Balance the +1 of __bridge_retained; ARC releases at the end of the scope.
    for (void* object : {state, vertex_function, fragment_function}) {
        if (object) {
            id released = (__bridge_transfer id)object;
            (void)released;
        }
    }
}

TessKernel::~TessKernel() {
    for (void* object : {state, function}) {
        if (object) {
            id released = (__bridge_transfer id)object;
            (void)released;
        }
    }
}

ComputePipeline::~ComputePipeline() {
    for (void* object : {state, function, copy_watch_state}) {
        if (object) {
            id released = (__bridge_transfer id)object;
            (void)released;
        }
    }
}

PsoCache::PsoCache(Device& device_, std::filesystem::path cache_dir, bool relaxed_math)
    : device{device_}, impl{std::make_unique<Impl>()} {
    impl->mtl_device = device.GetImpl().device;
    const char* persist = std::getenv("BB_MTL_PERSIST_WARM_ARCHIVE");
    impl->persist_enabled = persist && persist[0] == '1';
    if (cache_dir.empty()) {
        cache_dir = ShaderCompiler::DefaultCacheDir();
    }
    // Persist only explicitly named, fully verified compatibility cohorts. The manifest
    // must bind the actual translator binary/dependencies, effective options, and game
    // image + applied patches. Real device/OS and source-affecting toggles add local guards.
    std::string compatibility;
    { // Identity also admits metadata-only helper warmup when disk PSO persistence is OFF.
        const char* token = std::getenv("BB_MTL_ARCHIVE_COMPATIBILITY_ID");
        char os_build[256]{}; size_t os_bytes = sizeof(os_build);
        if (token && sysctlbyname("kern.osversion", os_build, &os_bytes, nullptr, 0) == 0) {
            const auto gpu = std::to_string(impl->mtl_device.registryID) + ":" +
                             std::string(impl->mtl_device.name.UTF8String);
            std::string options = relaxed_math ? "relaxed" : "safe";
            for (const char* key : {"BB_MTL_STRICT_FLOAT", "BB_MTL_RELAXED_MATH",
                                    "BB_MTL_VERTEX_MATH", "BB_MTL_FRAGMENT_UNIFORMS",
                                    "BB_MTL_COPY_PAIR", "BB_GS_DEBUG"}) {
                const char* value = std::getenv(key);
                options += std::string(";") + key + "=" + (value ? value : "<unset>");
            }
            const char* local_mode = std::getenv("BB_MTL_LOCAL_AOT_MODE");
            const char* local_build = std::getenv("BB_MTL_LOCAL_AOT_BUILD_ID");
            options = WarmArchiveLibraryOptions(options, local_mode ? local_mode : "",
                                                local_build ? local_build : "");
            compatibility = WarmArchiveNamespace(token, gpu, os_build, options);
        }
        if (impl->persist_enabled && compatibility.empty()) {
            impl->persist_enabled = false;
            LOG_WARNING(Render, "Metal archive persistence requires a verified 64-hex compatibility manifest; ignoring disk archives");
        }
    }
    preparation_identity = compatibility;
    // The launcher verified this source/input cohort before choosing record/load
    // and the local toolchain build. It is deliberately stable across those phases.
    const auto* discovery=std::getenv("BB_MTL_LOCAL_AOT_COHORT");
    if(!compatibility.empty()&&discovery&&LocalAot::Hex(discovery))discovery_identity=discovery;
    // No automatic legacy import: it lacks a translator/options/game identity. It is
    // preserved unchanged. OFF keeps its prior fresh transient compilation behavior.
    archive_path = cache_dir / (compatibility.empty()
        ? (relaxed_math ? "pipelines-relaxed-math-v2.binaryarchive" : "pipelines-v2.binaryarchive")
        : "pipelines-v3-" + compatibility + ".binaryarchive");

    @autoreleasepool {
        std::error_code ec;
        std::filesystem::create_directories(cache_dir, ec);
        // OFF ignores existing disk archives as well as disabling automatic writes.
        // Retain the fresh compilation object and active-PSO lifetime of the accepted baseline.
        const auto& input_path=archive_path;
        const bool exists = impl->persist_enabled && std::filesystem::exists(input_path, ec);
        // A fresh empty main archive used to block all independent strict hits
        // throughout WarmUp. Keep a real old main archive's priority; otherwise
        // do not allocate the empty blocker when a prepared artifact is verified.
        if (!exists && HasPreparedArtifact(cache_dir, preparation_identity)) {
            std::puts("Metal pipeline startup: prepared artifacts; no empty main archive");
            return;
        }
        MTLBinaryArchiveDescriptor* descriptor = [MTLBinaryArchiveDescriptor new];
        if (exists) {
            // Metal may move an unchanged input archive on serialize. Always read a private
            // copy; the original/legacy file is never a serializer input or removed on error.
            std::string name=(std::filesystem::temp_directory_path()/
                ("bbport-metal-read-"+std::to_string(getpid())+"-XXXXXX")).string();
            const int fd=mkstemp(name.data());
            if(fd>=0) {
                close(fd);impl->archive_snapshot=name;
                std::filesystem::copy_file(input_path,impl->archive_snapshot,
                    std::filesystem::copy_options::overwrite_existing,ec);
                if(!ec)descriptor.url=[NSURL fileURLWithPath:@(name.c_str())];
            }
            if(!descriptor.url)LOG_WARNING(Render,"Metal archive snapshot unavailable; preserving {}",input_path.string());
        }
        NSError* error = nil;
        id<MTLBinaryArchive> archive =
            [impl->mtl_device newBinaryArchiveWithDescriptor:descriptor error:&error];
        if (archive && descriptor.url) {
            archive_loaded = true;
        } else if (!archive && descriptor.url) {
            LOG_WARNING(Render, "Metal binary archive {} unusable ({}); preserving original",
                        input_path.string(), FirstLine(error));
            descriptor.url = nil;
            error = nil;
            archive = [impl->mtl_device newBinaryArchiveWithDescriptor:descriptor error:&error];
        }
        if (!archive) {
            LOG_WARNING(Render, "Metal binary archive not available ({}): pipelines are compiled "
                                "on every start",
                        FirstLine(error));
        }
        // A preserved but unreadable/incompatible old file must not leave a
        // freshly allocated empty blocker either. Never release a usable main.
        if (!archive_loaded && HasPreparedArtifact(cache_dir, preparation_identity)) {
            archive = nil;
            std::puts("Metal pipeline startup: prepared artifacts after unusable main archive");
        }
        impl->archive = archive;
    }
}

PsoCache::~PsoCache() {
    if (impl->persist_enabled) Flush();
    ReleaseWarmArchive();
}

void PsoCache::ReleaseWarmArchive() {
    // Startup only: CreateWithArchive may read the object without this lock during creation.
    // All four pipeline maps, functions, libraries and depth/stencil states remain owned.
    std::scoped_lock lock{impl->archive_mutex};
    impl->archive = nil;
    impl->archive_dirty = false;
    impl->archive_descriptors.clear();
    std::error_code ec;
    if(!impl->archive_snapshot.empty())std::filesystem::remove(impl->archive_snapshot,ec);
    impl->archive_snapshot.clear();
}

bool PsoCache::Flush() {
    std::scoped_lock lock{impl->archive_mutex};
    if (!impl->archive || !impl->archive_dirty) {
        return true;
    }
    // AGX may abort while creating its intermediate metallib when the archive
    // parent has become ENOTDIR. Reject that state before any driver rebuild or
    // serialization. This is startup/flush-only, not a per-draw filesystem check.
    // An external directory removal racing the subsequent driver call is not
    // made safe by this preflight; existing archive bytes are never removed here.
    std::error_code parent_error;
    if (!std::filesystem::is_directory(archive_path.parent_path(), parent_error)) {
        if (!impl->archive_error_logged) {
            impl->archive_error_logged = true;
            LOG_WARNING(Render, "Metal archive parent unavailable: {}; existing file retained",
                        archive_path.parent_path().string());
        }
        return false;
    }
    @autoreleasepool {
        if(impl->persist_enabled && !impl->archive_descriptors.empty()) {
            // AGX can successfully read an archive, but corrupt its stage associations when
            // appending and repacking ("expecting fragment"/"missing vertex"). Never repack
            // a loaded object. Release it BEFORE creating the complete replacement, avoiding
            // two large archive objects in memory. All live PSOs remain in their maps.
            impl->archive=nil;
            NSError* rebuild_error=nil;
            auto fresh=[impl->mtl_device newBinaryArchiveWithDescriptor:[MTLBinaryArchiveDescriptor new]
                                                                  error:&rebuild_error];
            bool rebuilt=fresh!=nil;
            for(id descriptor:impl->archive_descriptors) {
                if(!rebuilt)break;
                if([descriptor isKindOfClass:[MTLRenderPipelineDescriptor class]])
                    rebuilt=[fresh addRenderPipelineFunctionsWithDescriptor:descriptor error:&rebuild_error];
                else
                    rebuilt=[fresh addComputePipelineFunctionsWithDescriptor:descriptor error:&rebuild_error];
            }
            impl->archive=fresh;
            archive_loaded=false;
            if(!rebuilt) {
                if(!impl->archive_error_logged) {
                    impl->archive_error_logged=true;
                    LOG_WARNING(Render,"Metal archive rebuild failed ({}); existing files retained",FirstLine(rebuild_error));
                }
                return false;
            }
        }
        std::error_code ec;
        static std::atomic<u64> sequence{0};
        const std::filesystem::path temp = archive_path.string() + ".tmp-" +
            std::to_string(getpid()) + "-" + std::to_string(sequence.fetch_add(1));
        NSError* error = nil;
        const BOOL ok = [impl->archive
            serializeToURL:[NSURL fileURLWithPath:[NSString stringWithUTF8String:temp.c_str()]]
                     error:&error];
        if (ok) {
            std::filesystem::rename(temp, archive_path, ec);
        }
        if (!ok || ec) {
            if (!impl->archive_error_logged) {
                impl->archive_error_logged = true;
                LOG_WARNING(Render, "Metal binary archive {} could not be written: {}",
                            archive_path.string(), ok ? ec.message() : FirstLine(error));
            }
            std::filesystem::remove(temp, ec);
            return false;
        }
    }
    impl->archive_dirty = false;
    if(impl->persist_enabled)
        LOG_INFO(Render,"Metal binary archive: saved {} prewarmed descriptors to {}",
                 impl->archive_descriptors.size(),archive_path.filename().string());
    return true;
}

namespace {

/// Makes `archive`'s handling of one descriptor uniform for render and compute: a lookup with
/// FailOnBinaryArchiveMiss first (an archive hit skips the compiler), the regular creation
/// otherwise. Persistence records every descriptor for a fresh Flush; loaded archives stay
/// lookup-only. The nonpersistent transient compilation archive retains its prior behavior.
template <typename Archive,typename Descriptor, typename Create, typename Add>
id CreateWithArchive(Archive& impl, bool archive_loaded, Descriptor* descriptor,
                     Create create, Add add, bool& from_archive, std::string& error,bool allow_compile=true,bool* compiled=nullptr) {
    BbAudit::Scope detailed_audit{BbAudit::MetalPSO,0,100000};
    CompileAudit audit{"pso"};
    audit.cached = 0;
    id<MTLBinaryArchive> archive = impl.archive;
    from_archive = false;
    NSError* ns_error = nil;
    const auto record=[&] {
        if(!impl.persist_enabled || !archive)return;
        Descriptor* saved=[descriptor copy];
        saved.binaryArchives=@[];
        std::scoped_lock lock{impl.archive_mutex};
        impl.archive_descriptors.push_back(saved);
    };
    if (archive) {
        descriptor.binaryArchives = @[ archive ];
        if (archive_loaded) {
            id state = create(MTLPipelineOptionFailOnBinaryArchiveMiss, &ns_error);
            if (state) {
                from_archive = true;
                audit.ok = true; audit.cached = true;
                record();
                return state;
            }
            ns_error = nil;
        }
    }
    if(!allow_compile){error="native pipeline archive miss; preparation deferred";return nil;}
    if(compiled)*compiled=true;
    id state = create(MTLPipelineOptionNone, &ns_error);
    if (!state) {
        error = FirstLine(ns_error);
        return nil;
    }
    audit.ok = true;
    record();
    if (archive) {
        std::scoped_lock lock{impl.archive_mutex};
        if(impl.persist_enabled) {
            // Read archive is lookup-only. Flush rebuilds from the full descriptor ledger.
            impl.archive_dirty=true;
            return state;
        }
        NSError* add_error = nil;
        if (add(archive, descriptor, &add_error)) {
            impl.archive_dirty = true;
        } else if (!impl.archive_error_logged) {
            impl.archive_error_logged = true;
            LOG_WARNING(Render, "Metal binary archive: adding a pipeline failed ({}), continuing "
                                "without further additions",
                        FirstLine(add_error));
        }
    }
    return state;
}

} // namespace

#include "video_core/renderer_metal/mtl_pso_descriptor_identity.inc"
#include "video_core/renderer_metal/mtl_pso_artifact.inc"

#include "video_core/renderer_metal/mtl_game_pso_artifact.inc"

const RenderPipeline* PsoCache::GetRenderPipeline(const Vulkan::GraphicsPipelineKey& key,
                                                       const CompiledShader* vs,
                                                       const CompiledShader* fs,
                                                       const RenderPipelineInput& input) {
    ASSERT(vs && vs->function);
    RenderMapKey map_key{
        .key = PipelineKeyBytes(key),
        .vs = vs->function,
        .fs = fs ? fs->function : nullptr,
        .modules = {vs->cache_identity, fs ? fs->cache_identity : 0},
        .attributes_hash = XXH3_64bits(input.vertex_attributes.data(),
                                       input.vertex_attributes.size_bytes()),
        .rect_list_triangles = input.rect_list_triangles,
        .tessellation = input.tessellation_eval,
        .geometry = input.geometry,
    };
    {
        std::shared_lock lock{impl->mutex};
        const auto it = impl->render.find(map_key);
        if (it != impl->render.end()) {
            return it->second->state ? it->second.get() : nullptr;
        }
    }

    namespace L = Vulkan::LiverpoolToVK;
    auto pipeline = std::make_unique<RenderPipeline>();
    pipeline->key = key;
    pipeline->vs = vs;
    pipeline->fs = fs;
    std::string error;
    bool from_archive = false;
    u64 elapsed_us = 0;
    @autoreleasepool {
        do {
            // ---- primitive and encoder state ----
            const auto topology = L::PrimitiveType(key.prim_type);
            VkToMtl::PrimitiveInfo primitive = VkToMtl::PrimitiveType(topology);
            if (input.rect_list_triangles) {
                // The rewritten vertex shader emits the two triangles of each rectangle.
                primitive = {MTLPrimitiveTypeTriangle, false};
            }
            const bool tessellation = input.tessellation_eval;
            if (tessellation) {
                // `vs` is the evaluation shader: the tessellator makes the triangles, drawPatches
                // is the draw (the guest's patch list is not an emulated primitive).
                if (vs->variant.kind != CompileVariant::Kind::TessEval) {
                    error = "the vertex stage of a tessellation pipeline is not an evaluation shader";
                    break;
                }
                primitive = {MTLPrimitiveTypeTriangle, false};
                pipeline->tessellation = true;
                pipeline->patch_control_points = vs->variant.output_vertices;
            }
            if (input.geometry) {
                // `vs` is the pass-through function: it draws the strips of the geometry kernel.
                primitive = {static_cast<u32>(input.geometry_output == GsOutputPrimitive::LineStrip
                                                  ? MTLPrimitiveTypeLineStrip
                                                  : MTLPrimitiveTypeTriangleStrip),
                             false};
                pipeline->geometry = true;
            }
            pipeline->primitive_type = primitive.type;
            pipeline->primitive_emulated = primitive.needs_emulation;
            pipeline->rect_list = key.prim_type == AmdGpu::PrimitiveType::RectList;
            pipeline->quad_list = key.prim_type == AmdGpu::PrimitiveType::QuadList;
            const VkToMtl::FillInfo fill = VkToMtl::PolygonMode(L::PolygonMode(key.polygon_mode));
            pipeline->fill_mode = fill.mode;
            pipeline->fill_point = fill.needs_emulation;
            pipeline->depth_clip = key.depth_clip_enable;
            pipeline->provoking_vertex_last =
                key.provoking_vtx_last != AmdGpu::ProvokingVtxLast::First;
            pipeline->clip_minus_w_to_w = key.clip_space == AmdGpu::ClipSpace::MinusWToW;
            pipeline->num_color_attachments = key.num_color_attachments;

            MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
            desc.label = [NSString stringWithUTF8String:StageHashes(key).c_str()];
            desc.vertexFunction = (__bridge id<MTLFunction>)vs->function;
            if (pipeline->fs) {
                desc.fragmentFunction = (__bridge id<MTLFunction>)fs->function;
            }
            if (tessellation) {
                // The tessellator's state comes from the evaluation shader's execution modes:
                // equal / fractional spacing, the order of the triangles it makes, a half
                // precision factor per patch (the control kernel's structs).
                const TessellationInfo& modes = vs->tess;
                desc.tessellationPartitionMode = static_cast<MTLTessellationPartitionMode>(
                    Tessellation::PartitionMode(modes.spacing));
                desc.tessellationFactorFormat = MTLTessellationFactorFormatHalf;
                desc.tessellationFactorStepFunction = MTLTessellationFactorStepFunctionPerPatch;
                desc.tessellationControlPointIndexType = MTLTessellationControlPointIndexTypeNone;
                desc.tessellationOutputWindingOrder = static_cast<MTLWinding>(
                    Tessellation::OutputWinding(modes.clockwise));
                desc.maxTessellationFactor = Tessellation::kMaxTessellationFactor;
            } else {
                switch (primitive.type) {
                case MTLPrimitiveTypePoint:
                    desc.inputPrimitiveTopology = MTLPrimitiveTopologyClassPoint;
                    break;
                case MTLPrimitiveTypeLine:
                case MTLPrimitiveTypeLineStrip:
                    desc.inputPrimitiveTopology = MTLPrimitiveTopologyClassLine;
                    break;
                default:
                    desc.inputPrimitiveTopology = MTLPrimitiveTopologyClassTriangle;
                    break;
                }
            }

            // ---- sample count ----
            u32 samples = std::max<u32>(key.num_samples, 1);
            while (samples > 1 && ![impl->mtl_device supportsTextureSampleCount:samples]) {
                samples >>= 1;
            }
            pipeline->sample_count = samples;
            desc.rasterSampleCount = samples;

            // ---- attachments ----
            {
                for (u32 i = 0; i < key.num_color_attachments; ++i) {
                    if (!(key.mrt_mask & (1u << i))) {
                        continue; // a hole: not written by the fragment shader
                    }
                    const auto& cb = key.color_buffers[i];
                    const vk::Format format = L::TrySurfaceFormat(cb.data_format, cb.num_format);
                    if (format == vk::Format::eUndefined) {
                        continue; // written by the shader but not bound: Vulkan leaves it unused
                    }
                    const u32 pixel_format = VkToMtl::PixelFormat(format);
                    if (!pixel_format) {
                        error = fmt::format("color attachment {} format {} has no Metal pixel "
                                            "format",
                                            i, vk::to_string(format));
                        break;
                    }
                    pipeline->color_pixel_formats[i] = pixel_format;
                    MTLRenderPipelineColorAttachmentDescriptor* att = desc.colorAttachments[i];
                    att.pixelFormat = static_cast<MTLPixelFormat>(pixel_format);
                    att.writeMask = static_cast<MTLColorWriteMask>(
                        VkToMtl::ColorWriteMask(key.write_masks[i]));
                    const Vulkan::AttachmentBlend blend = Vulkan::AttachmentBlendState(key, i);
                    att.blendingEnabled = blend.enable;
                    if (blend.enable) {
                        att.sourceRGBBlendFactor =
                            static_cast<MTLBlendFactor>(VkToMtl::BlendFactor(blend.src_color));
                        att.destinationRGBBlendFactor =
                            static_cast<MTLBlendFactor>(VkToMtl::BlendFactor(blend.dst_color));
                        att.rgbBlendOperation =
                            static_cast<MTLBlendOperation>(VkToMtl::BlendOp(blend.color_op));
                        att.sourceAlphaBlendFactor =
                            static_cast<MTLBlendFactor>(VkToMtl::BlendFactor(blend.src_alpha));
                        att.destinationAlphaBlendFactor =
                            static_cast<MTLBlendFactor>(VkToMtl::BlendFactor(blend.dst_alpha));
                        att.alphaBlendOperation =
                            static_cast<MTLBlendOperation>(VkToMtl::BlendOp(blend.alpha_op));
                    }
                }
                if (!error.empty()) {
                    break;
                }
                const vk::Format depth_format = L::DepthFormat(key.z_format, key.stencil_format);
                const u32 depth_pixel = VkToMtl::PixelFormat(depth_format);
                if (depth_format != vk::Format::eUndefined && !depth_pixel) {
                    error = fmt::format("depth format {} has no Metal pixel format",
                                        vk::to_string(depth_format));
                    break;
                }
                if (key.z_format != AmdGpu::DepthBuffer::ZFormat::Invalid) {
                    pipeline->depth_pixel_format = depth_pixel;
                    desc.depthAttachmentPixelFormat = static_cast<MTLPixelFormat>(depth_pixel);
                }
                if (key.stencil_format != AmdGpu::DepthBuffer::StencilFormat::Invalid) {
                    pipeline->stencil_pixel_format = depth_pixel;
                    desc.stencilAttachmentPixelFormat = static_cast<MTLPixelFormat>(depth_pixel);
                }
            }

            // ---- vertex input ----
            const auto attributes = input.vertex_attributes;
            if (attributes.size() > kMaxVertexBuffers) {
                error = fmt::format("{} vertex attributes, at most {} fit", attributes.size(),
                                    kMaxVertexBuffers);
                break;
            }
            if (!attributes.empty()) {
                MTLVertexDescriptor* vd = [MTLVertexDescriptor vertexDescriptor];
                for (u32 i = 0; i < attributes.size(); ++i) {
                    const VertexAttribute& attribute = attributes[i];
                    const vk::Format format = key.vertex_buffer_formats[i];
                    const u32 vertex_format = VkToMtl::VertexFormat(format);
                    if (!vertex_format || attribute.location >= 31) {
                        error = fmt::format("vertex attribute {} (location {}) format {} has no "
                                            "Metal vertex format",
                                            i, attribute.location, vk::to_string(format));
                        break;
                    }
                    const u32 slot = kVertexBufferBase - i;
                    const bool constant = attribute.per_instance && attribute.divisor == 0;
                    MTLVertexAttributeDescriptor* a = vd.attributes[attribute.location];
                    a.format = static_cast<MTLVertexFormat>(vertex_format);
                    a.offset = 0;
                    a.bufferIndex = slot;
                    MTLVertexBufferLayoutDescriptor* layout = vd.layouts[slot];
                    if (constant) {
                        layout.stepFunction = MTLVertexStepFunctionConstant;
                        layout.stepRate = 0;
                        layout.stride = MTLBufferLayoutStrideDynamic; // Metal rejects stride 0
                    } else {
                        layout.stepFunction = attribute.per_instance
                                                  ? MTLVertexStepFunctionPerInstance
                                                  : MTLVertexStepFunctionPerVertex;
                        layout.stepRate = attribute.per_instance ? attribute.divisor : 1;
                        layout.stride = MTLBufferLayoutStrideDynamic;
                    }
                    pipeline->vertex_slots[i] = {attribute.location, slot, constant};
                }
                if (!error.empty()) {
                    break;
                }
                pipeline->num_vertex_slots = static_cast<u32>(attributes.size());
                desc.vertexDescriptor = vd;
            }

            // ---- create ----
            static std::atomic<bool> logic_op_warned{};
            if (key.logic_op != AmdGpu::ColorControl::LogicOp::Copy &&
                !logic_op_warned.exchange(true)) {
                LOG_WARNING(Render, "Metal has no logic ops; the pipelines ignore them");
            }
            const auto start = Clock::now();
            const auto artifact = GamePso::TryArtifact(*this, (__bridge void*)desc, false,
                *vs, fs, error);
            from_archive = artifact.archive_hit;
            id state = artifact.state;
            if (!artifact.handled) state = CreateWithArchive(
                *impl, archive_loaded, desc,
                [&](MTLPipelineOption options, NSError** err) {
                    return [impl->mtl_device newRenderPipelineStateWithDescriptor:desc
                                                                          options:options
                                                                       reflection:nil
                                                                            error:err];
                },
                [](id<MTLBinaryArchive> archive, MTLRenderPipelineDescriptor* d, NSError** err) {
                    return [archive addRenderPipelineFunctionsWithDescriptor:d error:err];
                },
                from_archive, error);
            elapsed_us = UsSince(start);
            if (!state) {
                break;
            }
            pipeline->state = (__bridge_retained void*)state;

            pipeline->vertex_function = (__bridge_retained void*)desc.vertexFunction;
            if (desc.fragmentFunction) {
                pipeline->fragment_function = (__bridge_retained void*)desc.fragmentFunction;
            }
        } while (false);
    }

    if (!pipeline->state) {
        LOG_ERROR(Render, "Metal render pipeline ({}): {}", StageHashes(key), error);
        // The retained function pointers must exist for the identity check; a failed entry has
        // none and is found by its (never reused while we hold the shaders) key only. Keep the
        // functions alive by retaining them here.
        pipeline->vertex_function = (__bridge_retained void*)(__bridge id)vs->function;
        if (fs) {
            pipeline->fragment_function = (__bridge_retained void*)(__bridge id)fs->function;
        }
        ++counters.failures;
    }
    ++counters.render_pipelines;
    counters.render_us += elapsed_us;
    counters.archive_hits += from_archive;
    if (!from_archive && pipeline->state && impl->archive) {
        ++counters.archive_adds;
    }

    std::unique_lock lock{impl->mutex};
    const auto [it, inserted] = impl->render.try_emplace(map_key, std::move(pipeline));
    if (!inserted) {
        --counters.render_pipelines; // another thread built it first; this one is dropped
        if (!it->second->state) {
            --counters.failures;
        }
    }
    return it->second->state ? it->second.get() : nullptr;
}

void PrepareCopyWatchPipeline(Device& device,const ComputePipeline& pipeline) {
    if(!VideoCore::FrameCapture::NativeRecording())return;
    if(pipeline.copy_watch_attempted || !pipeline.shader)return;
    pipeline.copy_watch_attempted=true;
    PrepareCopyWatchShader(device,*pipeline.shader);
    if(!pipeline.shader->copy_watch_reference)return;
    const auto total=pipeline.threads_per_group[0]*pipeline.threads_per_group[1]*pipeline.threads_per_group[2];
    @autoreleasepool {
        MTLComputePipelineDescriptor* watched=[MTLComputePipelineDescriptor new];
        watched.computeFunction=(__bridge id<MTLFunction>)pipeline.shader->copy_watch_reference->function;
        watched.maxTotalThreadsPerThreadgroup=total;
        NSError* error=nil;
        id<MTLComputePipelineState> state=[device.GetImpl().device newComputePipelineStateWithDescriptor:watched
            options:MTLPipelineOptionNone reflection:nil error:&error];
        if(state && total<=state.maxTotalThreadsPerThreadgroup)
            pipeline.copy_watch_state=(__bridge_retained void*)state;
        else std::printf("Copy pointer watch: lazy pipeline unavailable %s\n",error?error.localizedDescription.UTF8String:"thread limit");
    }
}

const ComputePipeline* PsoCache::GetComputePipeline(const Vulkan::ComputePipelineKey& key,
                                                         const CompiledShader* cs) {
    ASSERT(cs && cs->function);
    const ComputeMapKey map_key{key.value, cs->function, cs->cache_identity};
    {
        std::shared_lock lock{impl->mutex};
        const auto it = impl->compute.find(map_key);
        if (it != impl->compute.end()) {
            return it->second->state ? it->second.get() : nullptr;
        }
    }

    auto pipeline = std::make_unique<ComputePipeline>();
    pipeline->shader = cs;
    pipeline->threads_per_group = cs->local_size;
    pipeline->function = (__bridge_retained void*)(__bridge id)cs->function;
    std::string error;
    bool from_archive = false;
    u64 elapsed_us = 0;
    @autoreleasepool {
        do {
            const u32 total = cs->local_size[0] * cs->local_size[1] * cs->local_size[2];
            if (total == 0) {
                error = "the shader declares no LocalSize";
                break;
            }
            MTLComputePipelineDescriptor* desc = [MTLComputePipelineDescriptor new];
            desc.label = [NSString stringWithFormat:@"cs %#llx", (unsigned long long)key.value];
            desc.computeFunction = (__bridge id<MTLFunction>)cs->function;
            if (total <= 1024) {
                desc.maxTotalThreadsPerThreadgroup = total;
            }
            const auto start = Clock::now();
            const auto artifact = GamePso::TryArtifact(*this, (__bridge void*)desc, true,
                *cs, nullptr, error);
            from_archive = artifact.archive_hit;
            id state = artifact.state;
            if (!artifact.handled) state = CreateWithArchive(
                *impl, archive_loaded, desc,
                [&](MTLPipelineOption options, NSError** err) {
                    return [impl->mtl_device newComputePipelineStateWithDescriptor:desc
                                                                           options:options
                                                                        reflection:nil
                                                                             error:err];
                },
                [](id<MTLBinaryArchive> archive, MTLComputePipelineDescriptor* d, NSError** err) {
                    return [archive addComputePipelineFunctionsWithDescriptor:d error:err];
                },
                from_archive, error);
            elapsed_us = UsSince(start);
            if (!state) {
                break;
            }
            id<MTLComputePipelineState> compute_state = state;
            pipeline->max_total_threads = static_cast<u32>(compute_state.maxTotalThreadsPerThreadgroup);
            pipeline->thread_execution_width = static_cast<u32>(compute_state.threadExecutionWidth);
            if (total > pipeline->max_total_threads) {
                error = fmt::format("LocalSize {}x{}x{} exceeds the pipeline's limit of {} threads",
                                    cs->local_size[0], cs->local_size[1], cs->local_size[2],
                                    pipeline->max_total_threads);
                break;
            }
            pipeline->state = (__bridge_retained void*)state;

        } while (false);
    }

    if (!pipeline->state) {
        LOG_ERROR(Render, "Metal compute pipeline cs {:#x}: {}", key.value, error);
        ++counters.failures;
    }
    ++counters.compute_pipelines;
    counters.compute_us += elapsed_us;
    counters.archive_hits += from_archive;
    if (!from_archive && pipeline->state && impl->archive) {
        ++counters.archive_adds;
    }

    std::unique_lock lock{impl->mutex};
    const auto [it, inserted] = impl->compute.try_emplace(map_key, std::move(pipeline));
    if (!inserted) {
        --counters.compute_pipelines;
        if (!it->second->state) {
            --counters.failures;
        }
    }
    return it->second->state ? it->second.get() : nullptr;
}

namespace {

/// One of the tessellation path's compute pipelines; `stage_in` (nil: none) describes the vertex
/// kernel's attributes. False with `error` set.
bool MakeKernel(PsoCache& owner, PsoCache::Impl& impl, bool archive_loaded, TessKernel& kernel,
                const CompiledShader& shader, NSString* label,
                MTLStageInputOutputDescriptor* stage_in, bool& from_archive, std::string& error) {
    kernel.shader = &shader;
    kernel.function = (__bridge_retained void*)(__bridge id)shader.function;
    MTLComputePipelineDescriptor* desc = [MTLComputePipelineDescriptor new];
    desc.label = label;
    desc.computeFunction = (__bridge id<MTLFunction>)shader.function;
    desc.stageInputDescriptor = stage_in;
    bool archived = false;
    const auto artifact = GamePso::TryArtifact(owner, (__bridge void*)desc, true,
        shader, nullptr, error);
    archived = artifact.archive_hit;
    id state = artifact.state;
    if (!artifact.handled) state = CreateWithArchive(
        impl, archive_loaded, desc,
        [&](MTLPipelineOption options, NSError** err) {
            return [impl.mtl_device newComputePipelineStateWithDescriptor:desc
                                                                  options:options
                                                               reflection:nil
                                                                    error:err];
        },
        [](id<MTLBinaryArchive> archive, MTLComputePipelineDescriptor* d, NSError** err) {
            return [archive addComputePipelineFunctionsWithDescriptor:d error:err];
        },
        archived, error);
    from_archive = from_archive && archived;
    if (!state) {
        return false;
    }
    id<MTLComputePipelineState> compute_state = state;
    kernel.max_total_threads = static_cast<u32>(compute_state.maxTotalThreadsPerThreadgroup);
    kernel.thread_execution_width = static_cast<u32>(compute_state.threadExecutionWidth);
    kernel.state = (__bridge_retained void*)state;
    return true;
}

/// The stage-input descriptors of a vertex kernel (one per index type) for the guest's
/// attributes, and the slots they use. False with `error`.
bool BuildStageInputs(const Vulkan::GraphicsPipelineKey& key,
                      std::span<const VertexAttribute> attributes,
                      std::array<MTLStageInputOutputDescriptor*, 3>& stage_in,
                      std::array<VertexBufferSlot, Vulkan::MaxVertexBufferCount>& vertex_slots,
                      u32& num_vertex_slots, std::string& error) {
    if (attributes.size() > kMaxVertexBuffers) {
        error = fmt::format("{} vertex attributes, at most {} fit", attributes.size(),
                            kMaxVertexBuffers);
        return false;
    }
    for (u32 index_type = 0; index_type < 3 && !attributes.empty(); ++index_type) {
        MTLStageInputOutputDescriptor* sid =
            [MTLStageInputOutputDescriptor stageInputOutputDescriptor];
        for (u32 i = 0; i < attributes.size(); ++i) {
            const VertexAttribute& attribute = attributes[i];
            const vk::Format format = key.vertex_buffer_formats[i];
            const u32 vertex_format = VkToMtl::VertexFormat(format);
            if (!vertex_format || attribute.location >= 31) {
                error = fmt::format("vertex attribute {} (location {}) format {} has no "
                                    "Metal vertex format",
                                    i, attribute.location, vk::to_string(format));
                return false;
            }
            const u32 slot = kVertexBufferBase - i;
            const bool constant = attribute.per_instance && attribute.divisor == 0;
            // (MTLAttributeFormat has the values of MTLVertexFormat.)
            MTLAttributeDescriptor* a = sid.attributes[attribute.location];
            a.format = static_cast<MTLAttributeFormat>(vertex_format);
            a.offset = 0;
            a.bufferIndex = slot;
            MTLBufferLayoutDescriptor* layout = sid.layouts[slot];
            layout.stride = MTLBufferLayoutStrideDynamic;
            if (constant) {
                layout.stepFunction = MTLStepFunctionConstant;
                layout.stepRate = 0;
            } else if (attribute.per_instance) {
                layout.stepFunction = index_type != 0
                                          ? MTLStepFunctionThreadPositionInGridYIndexed
                                          : MTLStepFunctionThreadPositionInGridY;
                layout.stepRate = attribute.divisor;
            } else {
                layout.stepFunction = index_type != 0
                                          ? MTLStepFunctionThreadPositionInGridXIndexed
                                          : MTLStepFunctionThreadPositionInGridX;
                layout.stepRate = 1;
            }
            vertex_slots[i] = {attribute.location, slot, constant};
        }
        if (index_type != 0) {
            sid.indexType = index_type == 1 ? MTLIndexTypeUInt16 : MTLIndexTypeUInt32;
            sid.indexBufferIndex = kTessIndexBuffer;
        }
        stage_in[index_type] = sid;
    }
    num_vertex_slots = static_cast<u32>(attributes.size());
    return true;
}

} // namespace

const TessellationPipeline* PsoCache::GetTessellationPipeline(
    const Vulkan::GraphicsPipelineKey& key, const TessellationInput& input,
    const CompiledShader* fs) {
    ASSERT(input.vertex[0] && input.control && input.eval);
    TessMapKey map_key{
        .key = PipelineKeyBytes(key),
        .vertex = input.vertex[0]->function,
        .control = input.control->function,
        .eval = input.eval->function,
        .fs = fs ? fs->function : nullptr,
        .modules = {input.vertex[0]->cache_identity, input.control->cache_identity,
                    input.eval->cache_identity, fs ? fs->cache_identity : 0},
        .attributes_hash = XXH3_64bits(input.vertex_attributes.data(),
                                       input.vertex_attributes.size_bytes()),
    };
    {
        std::shared_lock lock{impl->mutex};
        const auto it = impl->tessellation.find(map_key);
        if (it != impl->tessellation.end()) {
            return it->second->render ? it->second.get() : nullptr;
        }
    }

    auto pipeline = std::make_unique<TessellationPipeline>();
    std::string error;
    bool from_archive = true;
    u64 elapsed_us = 0;
    @autoreleasepool {
        do {
            const CompiledShader& tcs = *input.control;
            const CompiledShader& tes = *input.eval;
            // The buffers between the stages are indexed with each stage's own struct size: the
            // interfaces must agree (the recompiler's stages exchange one array of vec4s).
            const auto& vs_layout = input.vertex[0]->tess_layout;
            if (tcs.tess_layout.input != 0 && tcs.tess_layout.input != vs_layout.output) {
                error = fmt::format("the control shader reads {} bytes per vertex, the vertex shader "
                                    "writes {}",
                                    tcs.tess_layout.input, vs_layout.output);
                break;
            }
            if (tes.tess_layout.input != 0 && tes.tess_layout.input != tcs.tess_layout.output) {
                error = fmt::format("the evaluation shader reads {} bytes per control point, the "
                                    "control shader writes {}",
                                    tes.tess_layout.input, tcs.tess_layout.output);
                break;
            }
            if (tes.tess_layout.patch_input != 0 &&
                tes.tess_layout.patch_input != tcs.tess_layout.patch_output) {
                error = fmt::format("the evaluation shader reads {} bytes of patch constants, the "
                                    "control shader writes {}",
                                    tes.tess_layout.patch_input, tcs.tess_layout.patch_output);
                break;
            }
            if (tcs.tess.output_vertices == 0 || tcs.tess.output_vertices != tes.variant.output_vertices ||
                key.patch_control_points == 0) {
                error = "the control shader declares no OutputVertices (or the patch size is 0)";
                break;
            }
            pipeline->input_control_points = key.patch_control_points;
            pipeline->output_control_points = tcs.tess.output_vertices;
            pipeline->triangles = tes.tess.domain == TessDomain::Triangles;
            pipeline->vertex_stride = vs_layout.output;
            pipeline->control_point_stride = tcs.tess_layout.output;
            pipeline->patch_stride = tcs.tess_layout.patch_output;
            pipeline->factor_stride = Tessellation::FactorStride(pipeline->triangles);

            // The vertex kernel's stage input: the attributes of the guest's fetch shader, in the
            // slots of the render path's vertex descriptor (data is read by thread position, or
            // through the index buffer for indexed draws; strides are set at each draw).
            std::array<MTLStageInputOutputDescriptor*, 3> stage_in{};
            if (!BuildStageInputs(key, input.vertex_attributes, stage_in, pipeline->vertex_slots,
                                  pipeline->num_vertex_slots, error)) {
                break;
            }

            // The render pipeline (the evaluation shader is its vertex function) and the kernels.
            const RenderPipelineInput render_input{.tessellation_eval = true};
            pipeline->render = GetRenderPipeline(key, input.eval, fs, render_input);
            if (!pipeline->render) {
                error = "the render pipeline of the evaluation shader failed (see above)";
                break;
            }
            const auto start = Clock::now();
            for (u32 index_type = 0; index_type < 3; ++index_type) {
                if (!input.vertex[index_type] ||
                    input.vertex[index_type]->variant.index_type != index_type) {
                    error = "a vertex kernel variant is missing";
                    break;
                }
                NSString* label = [NSString
                    stringWithFormat:@"tess vs %#llx idx%u",
                                     (unsigned long long)key.stage_hashes[u32(Shader::SwStage::Vertex)],
                                     index_type];
                if (!MakeKernel(*this, *impl, archive_loaded, pipeline->vertex[index_type],
                                *input.vertex[index_type], label, stage_in[index_type],
                                from_archive, error)) {
                    break;
                }
            }
            if (!error.empty()) {
                break;
            }
            NSString* label = [NSString
                stringWithFormat:@"tess hs %#llx",
                                 (unsigned long long)key.stage_hashes[u32(Shader::SwStage::TessellationControl)]];
            if (!MakeKernel(*this, *impl, archive_loaded, pipeline->control, tcs, label, nil,
                            from_archive, error)) {
                break;
            }
            elapsed_us = UsSince(start);
        } while (false);
    }

    if (!error.empty()) {
        LOG_ERROR(Render, "Metal tessellation pipeline ({}): {}", StageHashes(key), error);
        pipeline->render = nullptr; // failed entries are remembered by their key
        ++counters.failures;
    }
    ++counters.tessellation_pipelines;
    counters.compute_us += elapsed_us;
    counters.archive_hits += from_archive && error.empty();

    std::unique_lock lock{impl->mutex};
    const auto [it, inserted] = impl->tessellation.try_emplace(map_key, std::move(pipeline));
    if (!inserted) {
        --counters.tessellation_pipelines; // another thread built it first; this one is dropped
        if (!it->second->render) {
            --counters.failures;
        }
    }
    return it->second->render ? it->second.get() : nullptr;
}

bool GeometryDebugEnabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("BB_GS_DEBUG");
        return value && value[0] == '1';
    }();
    return enabled;
}

namespace {

/// BB_GS_DEBUG (P7.1c): the key with blending off and every colour attachment but the first
/// float one masked off, and a fragment function that writes magenta to that one (zero to the
/// integer attachments). Null with `error` when the function does not compile.
const CompiledShader* DebugFragment(PsoCache::Impl& impl, Vulkan::GraphicsPipelineKey& key,
                                    std::string& error) {
    std::string outputs, body;
    bool magenta_done = false;
    for (u32 i = 0; i < key.num_color_attachments; ++i) {
        key.blend_controls[i].enable = false;
        if (!(key.mrt_mask & (1u << i))) {
            continue;
        }
        const auto format = key.color_buffers[i].num_format;
        const bool integer = format == AmdGpu::NumberFormat::Uint || format == AmdGpu::NumberFormat::Sint;
        const char* type = format == AmdGpu::NumberFormat::Uint   ? "uint4"
                           : format == AmdGpu::NumberFormat::Sint ? "int4"
                                                                  : "float4";
        outputs += fmt::format("    {} c{} [[color({})]];\n", type, i, i);
        if (!integer && !magenta_done) {
            magenta_done = true;
            body += fmt::format("    o.c{} = float4(1.0, 0.0, 1.0, 1.0);\n", i);
        } else {
            body += fmt::format("    o.c{} = {}(0);\n", i, type);
            key.write_masks[i] = {};
        }
    }
    const std::string source = "#include <metal_stdlib>\nusing namespace metal;\n\nstruct FOut {\n" +
                               outputs + "};\n\nfragment FOut bb_gs_debug() {\n    FOut o;\n" +
                               body + "    return o;\n}\n";
    std::scoped_lock lock{impl.debug_mutex};
    if (const auto it = impl.debug_fragments.find(source); it != impl.debug_fragments.end()) {
        return it->second.get();
    }
    @autoreleasepool {
        NSError* ns_error = nil;
        Metal::LocalLibrary::Origin successful_origin;
        id<MTLLibrary> library =
            Metal::LocalLibrary::Build(impl.mtl_device, [NSString stringWithUTF8String:source.c_str()], nil, &ns_error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"bb_gs_debug"}); }, true, &successful_origin);
        id<MTLFunction> function = library ? [library newFunctionWithName:@"bb_gs_debug"] : nil;
        if (!function) {
            error = fmt::format("debug fragment function: {}", FirstLine(ns_error));
            return nullptr;
        }
        auto shader = std::make_unique<CompiledShader>();
        shader->stage = ShaderStage::Fragment;
        shader->entry = "bb_gs_debug";
        shader->bindings.push_back({~0u, ~0u, ShaderBinding::Kind::PushConstants,
                                    kPushConstantBuffer, 1});
        shader->num_buffers = 1;
        shader->library = (__bridge_retained void*)library;
        shader->function = (__bridge_retained void*)function;
        shader->persistent_identity=CaptureSuccessfulFunctionIdentity((__bridge void*)impl.mtl_device,
            nullptr,successful_origin,*shader);
        return impl.debug_fragments.emplace(source, std::move(shader)).first->second.get();
    }
}

} // namespace

const GeometryPipeline* PsoCache::GetGeometryPipeline(const Vulkan::GraphicsPipelineKey& key,
                                                      const GeometryPipelineInput& input,
                                                      const CompiledShader* fs) {
    ASSERT(input.es[0] && input.gs && input.pass_through);
    GeometryMapKey map_key{
        .key = PipelineKeyBytes(key),
        .es = input.es[0]->function,
        .gs = input.gs->function,
        .pass_through = input.pass_through->function,
        .fs = fs ? fs->function : nullptr,
        .modules = {input.es[0]->cache_identity, input.gs->cache_identity,
                    input.pass_through->cache_identity, fs ? fs->cache_identity : 0},
        .attributes_hash = XXH3_64bits(input.vertex_attributes.data(),
                                       input.vertex_attributes.size_bytes()),
    };
    {
        std::shared_lock lock{impl->mutex};
        const auto it = impl->geometry.find(map_key);
        if (it != impl->geometry.end()) {
            return it->second->render ? it->second.get() : nullptr;
        }
    }

    auto pipeline = std::make_unique<GeometryPipeline>();
    std::string error;
    bool from_archive = true;
    u64 elapsed_us = 0;
    @autoreleasepool {
        do {
            const CompiledShader& gs = *input.gs;
            pipeline->info = gs.geometry;
            pipeline->es_stride = input.es[0]->tess_layout.output;
            if (pipeline->es_stride == 0 || pipeline->info.max_vertices == 0) {
                error = "the export kernel has no output struct or the geometry shader no vertices";
                break;
            }
            std::array<MTLStageInputOutputDescriptor*, 3> stage_in{};
            if (!BuildStageInputs(key, input.vertex_attributes, stage_in, pipeline->vertex_slots,
                                  pipeline->num_vertex_slots, error)) {
                break;
            }
            const RenderPipelineInput render_input{.geometry = true,
                                                   .geometry_output = pipeline->info.output};
            Vulkan::GraphicsPipelineKey render_key = key;
            const CompiledShader* render_fs = fs;
            if (GeometryDebugEnabled() && fs) { // BB_GS_DEBUG
                render_fs = DebugFragment(*impl, render_key, error);
                if (!render_fs) {
                    break;
                }
            }
            pipeline->render =
                GetRenderPipeline(render_key, input.pass_through, render_fs, render_input);
            if (!pipeline->render) {
                error = "the render pipeline of the pass-through function failed (see above)";
                break;
            }
            const auto start = Clock::now();
            for (u32 index_type = 0; index_type < 3; ++index_type) {
                if (!input.es[index_type] ||
                    input.es[index_type]->variant.index_type != index_type) {
                    error = "an export kernel variant is missing";
                    break;
                }
                NSString* label = [NSString
                    stringWithFormat:@"gs es %#llx idx%u",
                                     (unsigned long long)key.stage_hashes[u32(Shader::SwStage::Vertex)],
                                     index_type];
                if (!MakeKernel(*this, *impl, archive_loaded, pipeline->es[index_type],
                                *input.es[index_type], label, stage_in[index_type], from_archive,
                                error)) {
                    break;
                }
            }
            if (!error.empty()) {
                break;
            }
            NSString* label = [NSString
                stringWithFormat:@"gs %#llx",
                                 (unsigned long long)key.stage_hashes[u32(Shader::SwStage::Geometry)]];
            if (!MakeKernel(*this, *impl, archive_loaded, pipeline->gs, gs, label, nil, from_archive,
                            error)) {
                break;
            }
            elapsed_us = UsSince(start);
        } while (false);
    }

    if (!error.empty()) {
        LOG_ERROR(Render, "Metal geometry pipeline ({}): {}", StageHashes(key), error);
        pipeline->render = nullptr; // failed entries are remembered by their key
        ++counters.failures;
    }
    ++counters.geometry_pipelines;
    counters.compute_us += elapsed_us;
    counters.archive_hits += from_archive && error.empty();

    std::unique_lock lock{impl->mutex};
    const auto [it, inserted] = impl->geometry.try_emplace(map_key, std::move(pipeline));
    if (!inserted) {
        --counters.geometry_pipelines; // another thread built it first; this one is dropped
        if (!it->second->render) {
            --counters.failures;
        }
    }
    return it->second->render ? it->second.get() : nullptr;
}

void* PsoCache::GetDepthStencilState(const DepthStencilKey& requested) {
    // Ignored parts are zeroed so that equal states share one object.
    DepthStencilKey key = requested;
    if (!key.depth_test) {
        key.depth_write = false;
        key.depth_compare = vk::CompareOp::eAlways;
    }
    if (!key.stencil_test) {
        key.front = {};
        key.back = {};
    }
    {
        std::shared_lock lock{impl->mutex};
        const auto it = impl->depth_stencil.find(key);
        if (it != impl->depth_stencil.end()) {
            return (__bridge void*)it->second;
        }
    }
    @autoreleasepool {
        MTLDepthStencilDescriptor* desc = [MTLDepthStencilDescriptor new];
        desc.depthCompareFunction = static_cast<MTLCompareFunction>(
            key.depth_test ? VkToMtl::CompareFunction(key.depth_compare) : MTLCompareFunctionAlways);
        desc.depthWriteEnabled = key.depth_test && key.depth_write;
        if (key.stencil_test) {
            const auto make = [](const DepthStencilKey::Face& f) {
                MTLStencilDescriptor* s = [MTLStencilDescriptor new];
                s.stencilCompareFunction =
                    static_cast<MTLCompareFunction>(VkToMtl::CompareFunction(f.compare_op));
                s.stencilFailureOperation =
                    static_cast<MTLStencilOperation>(VkToMtl::StencilOp(f.fail_op));
                s.depthFailureOperation =
                    static_cast<MTLStencilOperation>(VkToMtl::StencilOp(f.depth_fail_op));
                s.depthStencilPassOperation =
                    static_cast<MTLStencilOperation>(VkToMtl::StencilOp(f.pass_op));
                s.readMask = f.compare_mask;
                s.writeMask = f.write_mask;
                return s;
            };
            desc.frontFaceStencil = make(key.front);
            desc.backFaceStencil = make(key.back);
        }
        id<MTLDepthStencilState> state = [impl->mtl_device newDepthStencilStateWithDescriptor:desc];
        if (!state) {
            LOG_ERROR(Render, "Metal depth-stencil state could not be created");
            ++counters.failures;
            return nullptr;
        }
        std::unique_lock lock{impl->mutex};
        const auto [it, inserted] = impl->depth_stencil.try_emplace(key, state);
        if (inserted) {
            ++counters.depth_stencil_states;
        }
        return (__bridge void*)it->second;
    }
}

} // namespace Metal
