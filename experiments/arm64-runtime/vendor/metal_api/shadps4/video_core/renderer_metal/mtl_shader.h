// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <vector>
#include "common/types.h"
#include "video_core/renderer_metal/mtl_spirv_geometry.h"
#include "video_core/renderer_metal/mtl_function_identity.h"

namespace Metal {
namespace LocalLibrary { struct Origin; }


class Device;

/// The shader stages the compiler distinguishes (taken from the SPIR-V entry point).
enum class ShaderStage : u8 {
    Vertex,
    Fragment,
    Compute,
    TessellationControl,
    TessellationEval,
    Geometry,
    Unknown,
};
const char* ShaderStageName(ShaderStage stage);

// Direct slots (docs/METAL_BACKEND_PLAN.md D5), the same in every stage:
//   buffer 0                 push constants (always reserved, even when the shader has none)
//   buffer 1..               UBO/SSBO, in (set, binding) order, then SPIRV-Cross' buffer-size buffer
//                            when the shader needs one (CompiledShader::buffer_size_buffer)
//   buffer 30 downwards      vertex buffers (P3.2: the vertex descriptor's layouts; not assigned here)
// Tessellated draws (P4.4, CompileVariant) keep the resources below kTessFirstReservedBuffer and
// use the slots 13..18 for what SPIRV-Cross puts between the stages:
//   13  stage output: the vertex kernel's outputs, the control kernel's control points
//   14  vertex kernel: index buffer; control kernel: patch constants out; evaluation: patch constants in
//   15  tessellation factors (control kernel writes, the evaluation function may read)
//   16  control kernel: spvIndirectParams {input control points per patch, patch count}
//   17  control kernel: the vertex kernel's outputs; evaluation: the control kernel's control points
// (the vertex kernel's attribute buffers are 15..30, they are free of the others in that stage)
//   texture 0..              sampled/storage images and texel buffers, in (set, binding) order
//   sampler 0..              samplers, in (set, binding) order
constexpr u32 kPushConstantBuffer = 0;
constexpr u32 kFirstResourceBuffer = 1;
constexpr u32 kVertexBufferBase = 30;
constexpr u32 kMaxVertexBuffers = 16;        ///< slots 30..15 stay free for them in a vertex shader
constexpr u32 kMaxBuffers = 31;
constexpr u32 kTessOutputBuffer = 13;
constexpr u32 kTessIndexBuffer = 14;
constexpr u32 kTessPatchBuffer = 14;
constexpr u32 kTessFactorBuffer = 15;
constexpr u32 kTessParamsBuffer = 16;
constexpr u32 kTessInputBuffer = 17;
// The geometry kernel (P7.1, mtl_spirv_geometry.h) uses 13..16 for its own buffers:
constexpr u32 kGeoEsBuffer = 13;      ///< the export shader's outputs (input)
constexpr u32 kGeoRecordBuffer = 14;  ///< vertex records (output)
constexpr u32 kGeoIndexBuffer = 15;   ///< strip indices (output)
constexpr u32 kGeoParamsBuffer = 16;  ///< {prim_count, first_prim}
/// The resource buffers (and SPIRV-Cross' size buffer) of a tessellation variant stay below this.
constexpr u32 kTessFirstReservedBuffer = 13;
constexpr u32 kMaxTextures = 128;
constexpr u32 kMaxSamplers = 16;
/// Bump when the MSL options or the slot assignment change: it is part of the disk cache key.
constexpr u32 kMslCacheVersion = 3; // 3: stage input / export output layouts in the file (P7.1)
/// (The variants have their own cache files.)

enum class TessDomain : u8 { Unknown, Triangles, Quads, Isolines };
enum class TessSpacing : u8 { Equal, FractionalOdd, FractionalEven };

/// The tessellation execution modes of a TCS or TES module (read from the SPIR-V words).
struct TessellationInfo {
    TessDomain domain{};      ///< Triangles / Quads / Isolines; Unknown: the module declares none
    TessSpacing spacing{};    ///< TES: SpacingEqual (default) / FractionalOdd / FractionalEven
    bool clockwise{};         ///< TES: VertexOrderCw (neither declared: false)
    bool point_mode{};        ///< TES: PointMode
    u32 output_vertices{};    ///< TCS: OutputVertices (0 when not declared)
};
/// The tessellation modes of `spirv`; all default when it declares none.
TessellationInfo ReadTessellationInfo(std::span<const u32> spirv);
/// The stage of the module's first entry point (Unknown when it is not valid SPIR-V).
ShaderStage ReadShaderStage(std::span<const u32> spirv);

/// The float rules of the game's shaders (P9.1), chosen once at startup (ReadFloatRules). They
/// apply to game fragment shaders without depth export and to game compute shaders only; vertex,
/// tessellation, geometry, the ES/LS kernels and depth-exporting fragment shaders (the positions
/// and depths that depth-equal passes compare) keep NoContraction and Safe math.
enum class FloatRules : u8 {
    Strict,        ///< V0: NoContraction on every float op, every library with Safe math
    NoContraction, ///< V1: those shaders without NoContraction, Safe math
    RelaxedMath,   ///< V2: V1 plus MTLMathModeRelaxed for those shaders' libraries
};
const char* FloatRulesName(FloatRules rules);
/// BB_MTL_STRICT_FLOAT (1: V0, 0: V1 or V2) and BB_MTL_RELAXED_MATH (0: V1, 1: V2); read once.
/// Default V1.
FloatRules ReadFloatRules();

/// How a stage is compiled. Plain is the direct pipeline (vertex function, fragment function,
/// compute kernel); the others are the stages of a tessellated draw, the way MoltenVK builds them
/// with SPIRV-Cross (P4.4): the vertex shader runs as a compute kernel that writes its outputs to
/// a buffer, the control shader as a compute kernel that writes control points, patch constants
/// and tessellation factors, the evaluation shader is the post-tessellation vertex function.
struct CompileVariant {
    enum class Kind : u8 {
        Plain,
        TessVertex,
        TessControl,
        TessEval,
        GeometryKernel, ///< a geometry shader as a compute kernel (mtl_spirv_geometry.h)
    };
    Kind kind{Kind::Plain};
    u32 index_type{};         ///< TessVertex: 0 none, 1 uint16, 2 uint32 (the draw's index buffer)
    TessDomain domain{};      ///< TessControl: the evaluation shader's domain (the factor struct)
    u32 output_vertices{};    ///< TessEval: the control shader's OutputVertices (patch size)
    /// GeometryKernel: the export shader's output layout (TessVertex variant, TessLayout::members)
    GeometryEsLayout es;
    /// TessEval: the producer's actual per-patch buffer ABI, including unused locations.
    bool link_patch_input{};
    GeometryEsLayout patch_input;
    bool operator==(const CompileVariant&) const = default;
};

/// One resource of the SPIR-V module and where the MSL puts it. `set` and `binding` are the
/// SPIR-V decorations (the recompiler: set 0, `binding` from Shader::Backend::Bindings::unified,
/// so stage-relative only after subtracting the stage's first unified binding).
struct ShaderBinding {
    enum class Kind : u8 {
        Buffer,         ///< UBO or SSBO (device/constant buffer); msl_index is a buffer slot
        Texture,        ///< sampled image (separate or the image half of a combined one)
        StorageTexture, ///< read/write image
        Sampler,        ///< separate sampler (or the sampler half of a combined image sampler)
        TexelBuffer,    ///< image with Dim Buffer (a texture slot; none emitted by the recompiler)
        PushConstants,  ///< set and binding are ~0u, msl_index == kPushConstantBuffer
    };
    u32 set{};
    u32 binding{};
    Kind kind{};
    u32 msl_index{}; ///< slot in the class of `kind` (buffer, texture or sampler table)
    u32 count{1};    ///< array size: occupies msl_index .. msl_index + count - 1
};

/// One fragment shader input (SPIRV-Cross' stage-in) at a location.
struct StageInput {
    u32 location{};
    ScalarType type{ScalarType::Float};
    u32 components{};
    bool operator==(const StageInput&) const = default;
};

struct CompiledShader {
    CompiledShader() = default;
    ~CompiledShader(); // releases `library` and `function`
    CompiledShader(const CompiledShader&) = delete;
    CompiledShader& operator=(const CompiledShader&) = delete;

    /// id<MTLLibrary> and id<MTLFunction>, retained (+1); for .mm files: (__bridge id<...>).
    void* library{};
    void* function{};

    // Final successful effective source/entry/options/route and module ABI.
    // Empty means not admitted for persistent artifacts; existing live maps are unchanged.
    PersistentFunctionIdentity persistent_identity{};

    ShaderStage stage{ShaderStage::Unknown};
    std::string entry; ///< MSL function name ("main" becomes "main0")
    /// The push constants entry first, then sorted by (set, binding, kind). A combined image sampler yields a Texture and a Sampler
    /// entry with the same (set, binding). Several SPIR-V variables aliasing one (set, binding)
    /// (the recompiler's U8/U16/U32/U64/F32 views of one buffer) share one entry.
    std::vector<ShaderBinding> bindings;
    u32 push_constant_buffer{kPushConstantBuffer};
    bool uses_push_constants{};
    /// SPIRV-Cross' buffer of buffer sizes (OpArrayLength): constant uint[] indexed by the MSL
    /// buffer index of each SSBO; ~0u when the shader does not need it.
    u32 buffer_size_buffer{~0u};
    bool uses_device_address{};  ///< OpCapability PhysicalStorageBufferAddresses (raw gpuAddress pointers)
    bool uses_bda_pagetable{};   ///< the recompiler's "bda_pagetable" SSBO is declared
    u32 num_buffers{};           ///< buffer slots used, incl. push constants and the size buffer
    u32 num_textures{};
    u32 num_samplers{};
    bool from_disk_cache{};
    unsigned strict_vertex_helpers_inlined{};
    // Diagnostic only, limited to three selected plain vertex modules. Binding maps identical.
    std::unique_ptr<CompiledShader> strict_vertex_reference;
    // Opt-in, in-memory-only pointer-access diagnostics for the known copy kernel.
    bool copy_pair_merged{}; ///< runtime source specialization only; original disk text retained
    mutable std::unique_ptr<CompiledShader> copy_watch_reference;
    mutable std::string copy_watch_source;
    mutable bool copy_watch_attempted{};
    bool copy_watch_relaxed{};
    u32 copy_watch_reads{},copy_watch_writes{};
    /// Compute: the module's LocalSize execution mode (threadsPerThreadgroup of a dispatch); MSL
    /// has no way to declare it, SPIRV-Cross turns gl_WorkGroupSize into a constant. Always read
    /// from the SPIR-V, also on a disk cache hit.
    std::array<u32, 3> local_size{0, 0, 0};

    /// How this shader was compiled (Plain unless it is a stage of a tessellated draw).
    CompileVariant variant;
    /// TCS and TES: the execution modes of the SPIR-V (also on a disk cache hit).
    TessellationInfo tess;
    /// Tessellation variants: sizes in bytes of the structs SPIRV-Cross declared for the data
    /// between the stages (MSL layout, 0: the stage has none); the buffers are indexed with them.
    struct TessLayout {
        u32 input{};         ///< stage input element (TCS: a vertex output; TES: a control point)
        u32 output{};        ///< stage output element (vertex kernel, TCS: a control point)
        u32 patch_input{};   ///< TES: the patch constants it reads
        u32 patch_output{};  ///< TCS: the patch constants it writes
        /// Vertex kernel: the members of the output struct (location / position, byte offset).
        std::vector<InterfaceMember> members;
        std::vector<InterfaceMember> patch_members; ///< TCS per-patch outputs
        bool patch_output_complete{}; ///< false if reflection omitted an unsupported member
    } tess_layout;
    /// Fragment shaders: the used non-builtin stage inputs (sorted by location).
    std::vector<StageInput> stage_inputs;
    /// GeometryKernel: the shape of the geometry shader and its records.
    GeometryInfo geometry;
    // Metal may return the same immutable function for distinct modules. Binding metadata
    // remains module-owned, so in-memory PSO keys also distinguish this monotonic identity.
    inline static std::atomic<u64> next_cache_identity{1};
    const u64 cache_identity = next_cache_identity.fetch_add(1, std::memory_order_relaxed);

    /// Only untransformed game compute modules whose memory accesses are all explicit
    /// Metal bindings. Synthesized stages and physical-address accesses stay broad.
    /// uses_bda_pagetable records an OpName, even when that declaration is unused;
    /// actual pointer dereferences require the PhysicalStorageBufferAddresses capability.
    [[nodiscard]] bool HasOnlyDirectComputeResources() const noexcept {
        return stage == ShaderStage::Compute && variant.kind == CompileVariant::Kind::Plain &&
               !uses_device_address;
    }

    /// First entry for (set, binding, kind), or nullptr.
    const ShaderBinding* Find(u32 set, u32 binding, ShaderBinding::Kind kind) const;
};

// Only called for an active diagnostic window, never during startup/preloading.
void PrepareCopyWatchShader(Device&,const CompiledShader&);

enum class CompileStatus : u8 {
    Ok,
    UnsupportedStage, ///< geometry (P7.1), and a tessellation stage compiled as a plain stage
    PerVertexInput,   ///< a PerVertexKHR input the rewrite (mtl_spirv_pervertex.h) could not handle
    Failed,           ///< SPIRV-Cross, a resource limit or the Metal compiler
};

struct CompileResult {
    std::unique_ptr<CompiledShader> shader; ///< set when status == Ok
    CompileStatus status{CompileStatus::Failed};
    ShaderStage stage{ShaderStage::Unknown};
    std::string error; ///< first line of the cause (also logged with the hash)
};

/// SPIR-V -> MSL (SPIRV-Cross) -> MTLLibrary/MTLFunction. Thread-safe: Compile may run on several
/// threads at once.
class LibraryMemo;
struct LibraryReuseStats {
    u64 builds{}, reuses{}, waits{}, entries{};
};

/// Startup/cold factory only. Call after ALL CompiledShader ABI fields are set.
/// actual_compile_options is MTLCompileOptions* or nullptr for actual native defaults.
/// The adapter validates the real requested function name, reuses the same full
/// effective-options/context capture as normal game shaders, then binds module ABI.
PersistentFunctionIdentity CaptureSuccessfulFunctionIdentity(void* actual_mtl_device,
    void* actual_compile_options,const LocalLibrary::Origin& origin,const CompiledShader& shader);

class ShaderCompiler {
public:
    /// `cache_dir` holds the MSL disk cache; empty selects <user>/cache/<title id>/metal, created
    /// on first use. A cache that cannot be written only costs the speed-up.
    /// `rules` decides which game shaders get Relaxed math (FloatRules::RelaxedMath only); the
    /// SPIR-V's NoContraction is the recompiler's business (Shader::Profile).
    explicit ShaderCompiler(Device& device, std::filesystem::path cache_dir = {},
                            FloatRules rules = FloatRules::Strict, bool allow_cache_writes = true);
    ~ShaderCompiler();

    /// `hash` is the guest shader hash (log messages and cache file name), `name` a label for
    /// the logs and the MTLLibrary. The stage comes from the SPIR-V entry point.
    /// `variant` selects the tessellation forms of a vertex, control or evaluation shader
    /// (a control or evaluation shader has only those: Plain is UnsupportedStage for them).
    CompileResult Compile(std::span<const u32> spirv, u64 hash, std::string_view name,
                          const CompileVariant& variant = {});

    struct Counters {
        std::atomic<u64> compiled{};            ///< successful Compile calls
        std::atomic<u64> compile_failures{};    ///< CompileStatus::Failed
        std::atomic<u64> pervertex_failures{};  ///< CompileStatus::PerVertexInput
        std::atomic<u64> unsupported_stages{};  ///< CompileStatus::UnsupportedStage
        std::atomic<u64> cache_hits{};          ///< MSL taken from the disk cache
        std::atomic<u64> cache_stores{};        ///< MSL written to the disk cache
    };
    LibraryReuseStats GetLibraryReuseStats() const;
    const Counters& GetCounters() const {
        return counters;
    }
    const std::filesystem::path& CacheDir() const {
        return cache_dir;
    }

    /// <user>/cache/<title id>/metal (needs the user directories and ElfInfo initialised).
    static std::filesystem::path DefaultCacheDir();

private:
    Device& device;
    std::filesystem::path cache_dir;
    FloatRules rules;
    bool allow_cache_writes;
    Counters counters;
    std::unique_ptr<LibraryMemo> library_memo;
};

} // namespace Metal
