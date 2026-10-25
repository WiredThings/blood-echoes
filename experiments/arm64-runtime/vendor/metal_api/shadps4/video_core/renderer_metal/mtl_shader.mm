#include "bb_stutter_audit.h"
#include "video_core/renderer_metal/mtl_compile_audit.h"
// SPDX-License-Identifier: GPL-2.0-or-later

// SPIR-V (the recompiler's output) -> MSL (SPIRV-Cross) -> MTLLibrary/MTLFunction. Resource
// bindings use the direct-slot layout of docs/METAL_BACKEND_PLAN.md D5 (see mtl_shader.h); the MSL
// text and the binding map are cached on disk (format: "Disk cache" below).

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <xxhash.h>
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <mutex>
#include <span>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <tuple>
#include <unistd.h>

#include <spirv_cross/spirv_msl.hpp>

#include "common/elf_info.h"
#include "common/logging/log.h"
#include "common/path_util.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_library_memo.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "video_core/renderer_metal/mtl_vertex_math.h"
#include "video_core/renderer_metal/mtl_spirv_geometry.h"
#include "video_core/renderer_metal/mtl_spirv_pervertex.h"
#include "video_core/renderer_metal/mtl_fragment_uniforms.h"
#include "video_core/renderer_metal/mtl_copy_watch.h"
#include "video_core/renderer_metal/mtl_copy_pair.h"
#include "video_core/frame_capture.h"

#ifndef BB_SPIRV_CROSS_VERSION
#define BB_SPIRV_CROSS_VERSION "unknown"
#endif

namespace Metal {

namespace {

namespace sc = spirv_cross;
using Kind = ShaderBinding::Kind;

constexpr const char* kSpirvCrossVersion = BB_SPIRV_CROSS_VERSION;
constexpr const char* kCacheMagic = "// bbport-msl";
constexpr const char* kCacheMslMarker = "// ---- MSL";

const char* KindName(Kind kind) {
    switch (kind) {
    case Kind::Buffer:
        return "buffer";
    case Kind::Texture:
        return "texture";
    case Kind::StorageTexture:
        return "storage_texture";
    case Kind::Sampler:
        return "sampler";
    case Kind::TexelBuffer:
        return "texel_buffer";
    case Kind::PushConstants:
        return "push_constants";
    }
    return "?";
}

std::string FirstLine(const std::string& text) {
    const auto end = text.find('\n');
    return end == std::string::npos ? text : text.substr(0, end);
}

/// What the module declares, read from the SPIR-V words without SPIRV-Cross (cheap enough to run
/// on every call, also on a disk cache hit and for modules SPIRV-Cross would reject).
struct SpirvScan {
    bool valid{};
    ShaderStage stage{ShaderStage::Unknown};
    bool per_vertex{};      ///< a PerVertexKHR decoration (pull-model fragment inputs)
    bool device_address{};  ///< Capability PhysicalStorageBufferAddresses
    bool bda_pagetable{};   ///< a variable named "bda_pagetable"
    bool depth_replacing{}; ///< OpExecutionMode DepthReplacing (a fragment shader exporting depth)
    std::array<u32, 3> local_size{0, 0, 0}; ///< OpExecutionMode LocalSize
    TessellationInfo tess;                  ///< the tessellation execution modes
};

SpirvScan ScanSpirv(std::span<const u32> code) {
    SpirvScan scan;
    if (code.size() < 5 || code[0] != spv::MagicNumber) {
        return scan;
    }
    scan.valid = true;
    std::vector<u32> pagetable_candidates;
    for (size_t i = 5; i < code.size();) {
        const u32 word = code[i];
        const u32 length = word >> spv::WordCountShift;
        const u32 op = word & spv::OpCodeMask;
        if (length == 0 || i + length > code.size()) {
            scan.valid = false;
            return scan;
        }
        switch (op) {
        case spv::OpCapability:
            if (length >= 2 && code[i + 1] == spv::CapabilityPhysicalStorageBufferAddresses) {
                scan.device_address = true;
            }
            break;
        case spv::OpEntryPoint:
            if (length >= 2 && scan.stage == ShaderStage::Unknown) {
                switch (code[i + 1]) {
                case spv::ExecutionModelVertex:
                    scan.stage = ShaderStage::Vertex;
                    break;
                case spv::ExecutionModelFragment:
                    scan.stage = ShaderStage::Fragment;
                    break;
                case spv::ExecutionModelGLCompute:
                    scan.stage = ShaderStage::Compute;
                    break;
                case spv::ExecutionModelTessellationControl:
                    scan.stage = ShaderStage::TessellationControl;
                    break;
                case spv::ExecutionModelTessellationEvaluation:
                    scan.stage = ShaderStage::TessellationEval;
                    break;
                case spv::ExecutionModelGeometry:
                    scan.stage = ShaderStage::Geometry;
                    break;
                default:
                    break;
                }
            }
            break;
        case spv::OpExecutionMode:
            if (length >= 6 && code[i + 2] == spv::ExecutionModeLocalSize) {
                scan.local_size = {code[i + 3], code[i + 4], code[i + 5]};
            }
            if (length >= 3) {
                switch (code[i + 2]) {
                case spv::ExecutionModeDepthReplacing:
                    scan.depth_replacing = true;
                    break;
                case spv::ExecutionModeTriangles:
                    scan.tess.domain = TessDomain::Triangles;
                    break;
                case spv::ExecutionModeQuads:
                    scan.tess.domain = TessDomain::Quads;
                    break;
                case spv::ExecutionModeIsolines:
                    scan.tess.domain = TessDomain::Isolines;
                    break;
                case spv::ExecutionModeSpacingEqual:
                    scan.tess.spacing = TessSpacing::Equal;
                    break;
                case spv::ExecutionModeSpacingFractionalOdd:
                    scan.tess.spacing = TessSpacing::FractionalOdd;
                    break;
                case spv::ExecutionModeSpacingFractionalEven:
                    scan.tess.spacing = TessSpacing::FractionalEven;
                    break;
                case spv::ExecutionModeVertexOrderCw:
                    scan.tess.clockwise = true;
                    break;
                case spv::ExecutionModeVertexOrderCcw:
                    scan.tess.clockwise = false;
                    break;
                case spv::ExecutionModePointMode:
                    scan.tess.point_mode = true;
                    break;
                case spv::ExecutionModeOutputVertices:
                    if (length >= 4) {
                        scan.tess.output_vertices = code[i + 3];
                    }
                    break;
                default:
                    break;
                }
            }
            break;
        case spv::OpName:
            if (length >= 3 &&
                std::strncmp(reinterpret_cast<const char*>(&code[i + 2]), "bda_pagetable",
                             (length - 2) * sizeof(u32)) == 0) {
                scan.bda_pagetable = true;
            }
            break;
        case spv::OpDecorate:
            if (length >= 3 && code[i + 2] == spv::DecorationPerVertexKHR) {
                scan.per_vertex = true;
            }
            break;
        default:
            break;
        }
        i += length;
    }
    return scan;
}

ScalarType ToScalarType(sc::SPIRType::BaseType base) {
    switch (base) {
    case sc::SPIRType::Float:
        return ScalarType::Float;
    case sc::SPIRType::Int:
        return ScalarType::Int;
    case sc::SPIRType::UInt:
        return ScalarType::Uint;
    default:
        return ScalarType::Other;
    }
}

/// CompilerMSL with access to the interface structs SPIRV-Cross declares (the size of the data
/// between the stages of a tessellated draw).
class TessCompilerMSL final : public sc::CompilerMSL {
public:
    using sc::CompilerMSL::CompilerMSL;

    /// MSL sizes of the structs; valid after compile().
    CompiledShader::TessLayout Layout() {
        CompiledShader::TessLayout layout;
        layout.patch_output_complete = true;
        if (stage_in_var_id) {
            layout.input = InterfaceStructSize(get_stage_in_struct_type());
        }
        if (stage_out_var_id) {
            layout.output = InterfaceStructSize(get_stage_out_struct_type());
            layout.members = InterfaceMembers(get_stage_out_struct_type());
        }
        if (patch_stage_in_var_id) {
            layout.patch_input = InterfaceStructSize(get_patch_stage_in_struct_type());
        }
        if (patch_stage_out_var_id) {
            const auto& type = get_patch_stage_out_struct_type();
            layout.patch_output = InterfaceStructSize(type);
            layout.patch_members = InterfaceMembers(type);
            layout.patch_output_complete = layout.patch_members.size() == type.member_types.size();
        }
        return layout;
    }

    std::vector<InterfaceMember> PatchInputMembers() {
        return patch_stage_in_var_id ? InterfaceMembers(get_patch_stage_in_struct_type())
                                     : std::vector<InterfaceMember>{};
    }

private:
    /// sizeof() of an interface struct as MSL lays it out: members in order at their natural
    /// alignment, the size rounded up to the largest. (The synthesized structs have no Offset
    /// decorations, which get_declared_struct_size_msl wants.)
    u32 InterfaceStructSize(const sc::SPIRType& type) const {
        u32 offset = 0, alignment = 1;
        for (u32 i = 0; i < type.member_types.size(); ++i) {
            const u32 member_alignment = get_declared_struct_member_alignment_msl(type, i);
            offset = (offset + member_alignment - 1) / member_alignment * member_alignment;
            offset += get_declared_struct_member_size_msl(type, i);
            alignment = std::max(alignment, member_alignment);
        }
        return (offset + alignment - 1) / alignment * alignment;
    }

    /// The scalar / vector members of an interface struct with their byte offsets (the same
    /// layout as InterfaceStructSize) and the Location or Position they carry.
    std::vector<InterfaceMember> InterfaceMembers(const sc::SPIRType& type) const {
        std::vector<InterfaceMember> members;
        u32 offset = 0;
        for (u32 i = 0; i < type.member_types.size(); ++i) {
            const u32 member_alignment = get_declared_struct_member_alignment_msl(type, i);
            offset = (offset + member_alignment - 1) / member_alignment * member_alignment;
            const sc::SPIRType& member_type = get_type(type.member_types[i]);
            const bool builtin = has_member_decoration(type.self, i, spv::DecorationBuiltIn);
            if (member_type.array.empty() && member_type.columns == 1 &&
                (builtin || has_member_decoration(type.self, i, spv::DecorationLocation))) {
                InterfaceMember m;
                m.offset = offset;
                m.components = member_type.vecsize;
                m.type = ToScalarType(member_type.basetype);
                if (builtin) {
                    m.position = get_member_decoration(type.self, i, spv::DecorationBuiltIn) ==
                                 spv::BuiltInPosition;
                } else {
                    m.location = get_member_decoration(type.self, i, spv::DecorationLocation);
                }
                if (builtin ? m.position : true) {
                    members.push_back(m);
                }
            }
            offset += get_declared_struct_member_size_msl(type, i);
        }
        return members;
    }
};

/// SPIR-V -> MSL; fills the binding map, the flags and the entry name of `out` (not the Metal
/// objects). Throws spirv_cross::CompilerError, or std::runtime_error for a resource limit.
std::string Convert(std::span<const u32> spirv, const SpirvScan& scan,
                    const CompileVariant& variant, CompiledShader& out) {
    TessCompilerMSL comp(spirv.data(), spirv.size());
    const auto entry_points = comp.get_entry_points_and_stages();
    if (entry_points.empty()) {
        throw std::runtime_error("no entry point");
    }
    const auto model = entry_points[0].execution_model;

    const sc::ShaderResources res = comp.get_shader_resources();
    out.uses_push_constants = !res.push_constant_buffers.empty();

    // One slot per (set, binding) per Metal resource class. Variables that alias a (set, binding)
    // (the recompiler's struct views of one SSBO) share it.
    struct Slot {
        sc::SPIRType::BaseType basetype{};
        Kind kind{};
        bool buffer{}, texture{}, sampler{};
        u32 count{1};
        u32 buffer_index{}, texture_index{}, sampler_index{};
    };
    std::map<std::pair<u32, u32>, Slot> slots;
    auto array_count = [&](const sc::Resource& r) -> u32 {
        const sc::SPIRType& type = comp.get_type(r.type_id);
        if (type.array.empty()) {
            return 1;
        }
        if (type.array.size() != 1 || !type.array_size_literal[0] || type.array[0] == 0) {
            throw std::runtime_error("unsupported resource array (specialization-constant, "
                                     "unsized or multi-dimensional)");
        }
        return type.array[0];
    };
    auto collect = [&](const sc::SmallVector<sc::Resource>& list, sc::SPIRType::BaseType basetype,
                       Kind kind, bool buffer, bool texture, bool sampler) {
        for (const sc::Resource& r : list) {
            const u32 set = comp.get_decoration(r.id, spv::DecorationDescriptorSet);
            const u32 binding = comp.get_decoration(r.id, spv::DecorationBinding);
            if (variant.kind == CompileVariant::Kind::GeometryKernel &&
                set == kGeometryKernelSet) {
                continue; // the kernel's own buffers: fixed slots below
            }
            if (slots.contains({set, binding})) {
                continue;
            }
            Kind slot_kind = kind;
            if (kind == Kind::Texture || kind == Kind::StorageTexture) {
                if (comp.get_type(r.type_id).image.dim == spv::DimBuffer) {
                    slot_kind = Kind::TexelBuffer;
                }
            }
            slots[{set, binding}] = {.basetype = basetype,
                                     .kind = slot_kind,
                                     .buffer = buffer,
                                     .texture = texture,
                                     .sampler = sampler,
                                     .count = array_count(r)};
        }
    };
    collect(res.uniform_buffers, sc::SPIRType::Struct, Kind::Buffer, true, false, false);
    collect(res.storage_buffers, sc::SPIRType::Struct, Kind::Buffer, true, false, false);
    collect(res.storage_images, sc::SPIRType::Image, Kind::StorageTexture, false, true, false);
    collect(res.sampled_images, sc::SPIRType::SampledImage, Kind::Texture, false, true, true);
    collect(res.separate_images, sc::SPIRType::Image, Kind::Texture, false, true, false);
    collect(res.separate_samplers, sc::SPIRType::Sampler, Kind::Sampler, false, false, true);
    collect(res.subpass_inputs, sc::SPIRType::Image, Kind::Texture, false, true, false);

    u32 next_buffer = kFirstResourceBuffer, next_texture = 0, next_sampler = 0;
    out.bindings.clear();
    out.bindings.push_back({~0u, ~0u, Kind::PushConstants, kPushConstantBuffer, 1});
    for (auto& [key, slot] : slots) { // std::map: sorted by (set, binding)
        const auto [set, binding] = key;
        sc::MSLResourceBinding b;
        b.stage = model;
        b.basetype = slot.basetype;
        b.desc_set = set;
        b.binding = binding;
        b.count = slot.count;
        if (slot.buffer) {
            slot.buffer_index = b.msl_buffer = next_buffer;
            out.bindings.push_back({set, binding, Kind::Buffer, next_buffer, slot.count});
            next_buffer += slot.count;
        }
        if (slot.texture) {
            slot.texture_index = b.msl_texture = next_texture;
            out.bindings.push_back({set, binding, slot.kind, next_texture, slot.count});
            next_texture += slot.count;
        }
        if (slot.sampler) {
            slot.sampler_index = b.msl_sampler = next_sampler;
            out.bindings.push_back({set, binding, Kind::Sampler, next_sampler, slot.count});
            next_sampler += slot.count;
        }
        comp.add_msl_resource_binding(b);
    }
    {
        sc::MSLResourceBinding b;
        b.stage = model;
        b.basetype = sc::SPIRType::Struct;
        b.desc_set = sc::kPushConstDescSet;
        b.binding = sc::kPushConstBinding;
        b.msl_buffer = kPushConstantBuffer;
        comp.add_msl_resource_binding(b);
    }

    if (variant.kind == CompileVariant::Kind::GeometryKernel) {
        for (u32 binding = 0; binding < 4; ++binding) {
            sc::MSLResourceBinding b;
            b.stage = model;
            b.basetype = sc::SPIRType::Struct;
            b.desc_set = kGeometryKernelSet;
            b.binding = binding;
            b.msl_buffer = kGeoEsBuffer + binding;
            comp.add_msl_resource_binding(b);
        }
    }
    // The fragment shader's used stage inputs (the geometry pass-through function declares them).
    out.stage_inputs.clear();
    if (model == spv::ExecutionModelFragment) {
        for (const sc::Resource& r : res.stage_inputs) {
            if (comp.has_decoration(r.id, spv::DecorationBuiltIn)) {
                continue;
            }
            const sc::SPIRType& type = comp.get_type(r.type_id);
            const u32 location = comp.get_decoration(r.id, spv::DecorationLocation);
            u32 count = 1;
            if (!type.array.empty()) {
                count = type.array.size() == 1 && type.array_size_literal[0] ? type.array[0] : 1;
            }
            for (u32 e = 0; e < count; ++e) {
                out.stage_inputs.push_back({location + e, ToScalarType(type.basetype),
                                            type.columns == 1 ? type.vecsize : 0});
            }
        }
        std::sort(out.stage_inputs.begin(), out.stage_inputs.end(),
                  [](const StageInput& a, const StageInput& b) { return a.location < b.location; });
    }

    // SPIRV-Cross' buffer of buffer sizes (OpArrayLength) goes behind the resource buffers.
    const u32 size_buffer_slot = next_buffer;
    sc::CompilerMSL::Options options;
    options.platform = sc::CompilerMSL::Options::macOS;
    options.set_msl_version(3, 1);
    options.argument_buffers = false;
    options.buffer_size_buffer_index = size_buffer_slot;
    using VariantKind = CompileVariant::Kind;
    if (variant.kind != VariantKind::Plain && variant.kind != VariantKind::GeometryKernel) {
        // Tessellation (P4.4): the slots between the stages (mtl_shader.h) and the stage's form.
        options.shader_output_buffer_index = kTessOutputBuffer;
        options.shader_index_buffer_index = kTessIndexBuffer;
        options.shader_patch_output_buffer_index = kTessPatchBuffer;
        options.shader_patch_input_buffer_index = kTessPatchBuffer;
        options.shader_tess_factor_buffer_index = kTessFactorBuffer;
        options.indirect_params_buffer_index = kTessParamsBuffer;
        options.shader_input_buffer_index = kTessInputBuffer;
        options.tess_domain_origin_lower_left = false; // Vulkan's default, Metal's native origin
        switch (variant.kind) {
        case VariantKind::TessVertex:
            options.vertex_for_tessellation = true;
            options.capture_output_to_buffer = true;
            options.vertex_index_type =
                variant.index_type == 1   ? sc::CompilerMSL::Options::IndexType::UInt16
                : variant.index_type == 2 ? sc::CompilerMSL::Options::IndexType::UInt32
                                          : sc::CompilerMSL::Options::IndexType::None;
            break;
        case VariantKind::TessControl:
            // One thread per output control point, across patches.
            options.multi_patch_workgroup = true;
            options.capture_output_to_buffer = true;
            break;
        case VariantKind::TessEval:
            options.raw_buffer_tese_input = true;
            break;
        case VariantKind::Plain:
        case VariantKind::GeometryKernel:
            break;
        }
    }
    comp.set_msl_options(options);
    if (variant.kind == VariantKind::TessControl) {
        // The tessellation factor struct (quad or triangle) follows the domain, which the control
        // shader need not declare.
        const auto& modes = comp.get_execution_mode_bitset();
        if (!modes.get(spv::ExecutionModeTriangles) && !modes.get(spv::ExecutionModeQuads) &&
            !modes.get(spv::ExecutionModeIsolines)) {
            if (variant.domain != TessDomain::Triangles && variant.domain != TessDomain::Quads) {
                throw std::runtime_error("no triangle or quad domain for the control shader");
            }
            comp.set_execution_mode(variant.domain == TessDomain::Triangles
                                        ? spv::ExecutionModeTriangles
                                        : spv::ExecutionModeQuads);
        }
    } else if (variant.kind == VariantKind::TessEval) {
        // The patch size of the post-tessellation function (patch(quad, N), the stride of the
        // control point buffer) is the control shader's output count.
        if (variant.output_vertices == 0) {
            throw std::runtime_error("no output vertex count for the evaluation shader");
        }
        comp.set_execution_mode(spv::ExecutionModeOutputVertices, variant.output_vertices);
    }

    // Raw TES buffers have no Metal stage linker to preserve holes. SPIRV-Cross must know
    // every output of the TCS, even locations this TES never reads. Merely declaring unused
    // inputs in SPIR-V is insufficient: inactive interfaces are pruned during conversion.
    std::vector<InterfaceMember> active_patch_inputs;
    if (variant.kind == VariantKind::TessEval && variant.link_patch_input) {
        const auto& layout = variant.patch_input;
        u32 end = 0, alignment = 1;
        std::set<u32> locations;
        for (const auto& m : layout.members) {
            if (m.position || m.location == ~0u || m.components < 1 || m.components > 4 ||
                m.type == ScalarType::Other || !locations.insert(m.location).second) {
                throw std::runtime_error("unsupported or duplicate TCS patch output member");
            }
            const u32 a = m.components == 3 ? 16 : m.components * 4;
            end = (end + a - 1) / a * a;
            if (m.offset != end) {
                throw std::runtime_error("TCS patch output offsets are not a complete natural layout");
            }
            end += m.components == 3 ? 16 : m.components * 4;
            alignment = std::max(alignment, a);
        }
        if ((end + alignment - 1) / alignment * alignment != layout.stride) {
            throw std::runtime_error("TCS patch output reflection does not cover its stride");
        }
        const auto active = comp.get_shader_resources(comp.get_active_interface_variables());
        for (const auto& input : active.stage_inputs) {
            if (!comp.has_decoration(input.id, spv::DecorationPatch)) {
                continue;
            }
            const auto& type = comp.get_type(input.type_id);
            const u32 location = comp.get_decoration(input.id, spv::DecorationLocation);
            const auto producer = std::find_if(layout.members.begin(), layout.members.end(),
                [&](const auto& m) { return !m.position && m.location == location; });
            if (!comp.has_decoration(input.id, spv::DecorationLocation) || !type.array.empty() ||
                type.columns != 1 || type.width != 32 || producer == layout.members.end() ||
                producer->components != type.vecsize || producer->type != ToScalarType(type.basetype)) {
                throw std::runtime_error("TES patch input has no type-compatible TCS output");
            }
            active_patch_inputs.push_back(*producer);
        }
        for (const auto& m : layout.members) {
            sc::MSLShaderInterfaceVariable input{};
            input.location = m.location;
            input.vecsize = m.components;
            input.format = sc::MSL_SHADER_VARIABLE_FORMAT_ANY32;
            input.rate = sc::MSL_SHADER_VARIABLE_RATE_PER_PATCH;
            comp.add_msl_shader_input(input);
        }
    }

    std::string msl = comp.compile();
    if (variant.kind == VariantKind::TessEval && variant.link_patch_input) {
        const auto linked = comp.Layout();
        // The stride alone cannot catch a compacted leading/middle member. Check every
        // actually-read field's byte offset too; padding fields intentionally have uint types.
        // A TES that reads no patch constants may omit the patch buffer entirely.
        if ((!active_patch_inputs.empty() || linked.patch_input != 0) &&
            linked.patch_input != variant.patch_input.stride) {
            throw std::runtime_error("linked TES patch stride differs from the TCS output");
        }
        // PatchInputMembers is reflected from the generated MSL struct, not SPIR-V locations.
        const auto members = comp.PatchInputMembers();
        for (const auto& expected : active_patch_inputs) {
            const auto actual = std::find_if(members.begin(), members.end(), [&](const auto& m) {
                return m.location == expected.location && !m.position;
            });
            if (actual == members.end() || *actual != expected) {
                throw std::runtime_error("linked TES patch field differs from the TCS output");
            }
        }
    }

    out.entry = comp.get_cleansed_entry_point_name(entry_points[0].name, model);
    out.num_buffers = next_buffer;
    out.num_textures = next_texture;
    out.num_samplers = next_sampler;
    if (comp.needs_buffer_size_buffer()) {
        out.buffer_size_buffer = size_buffer_slot;
        ++out.num_buffers;
    }
    const u32 max_buffers = variant.kind != VariantKind::Plain ? kTessFirstReservedBuffer
                            : scan.stage == ShaderStage::Vertex ? kMaxBuffers - kMaxVertexBuffers
                                                                : kMaxBuffers;
    if (out.num_buffers > max_buffers || out.num_textures > kMaxTextures ||
        out.num_samplers > kMaxSamplers) {
        throw std::runtime_error(fmt::format(
            "resource limit exceeded: {} buffers (max {}), {} textures (max {}), {} samplers "
            "(max {})",
            out.num_buffers, max_buffers, out.num_textures, kMaxTextures, out.num_samplers,
            kMaxSamplers));
    }
    std::stable_sort(out.bindings.begin() + 1, out.bindings.end(), [](const ShaderBinding& a, const ShaderBinding& b) {
        return std::tie(a.set, a.binding, a.kind) < std::tie(b.set, b.binding, b.kind);
    });
    if (variant.kind != VariantKind::Plain && variant.kind != VariantKind::GeometryKernel) {
        out.tess_layout = comp.Layout();
    }
    return msl;
}

// ---- Disk cache ----------------------------------------------------------------------------
//
// <dir>/<hash:016x>_<stage>_<xxh3(SPIR-V words):016x>.metal, plain text:
//   // bbport-msl <kMslCacheVersion>
//   // spirv-cross <version>
//   // stage <name>
//   // entry <MSL function name>
//   // flags push=<0|1> bda=<0|1> pagetable=<0|1> buffer_size=<slot or ->
//   // counts <buffers> <textures> <samplers>
//   // tess <input> <output> <patch input> <patch output>          (tessellation variants only)
//   // bind <set> <binding> <kind name> <msl index> <count>        (one line per binding)
//   // msl-bytes <n>
//   // ---- MSL
//   <the MSL, n bytes>
// A file that does not parse, has another version or SPIRV-Cross version, or whose MSL is not n
// bytes long, is a miss and is overwritten. Writes go to a temporary name and are renamed.

std::string CacheText(const CompiledShader& shader, const std::string& msl) {
    std::ostringstream text;
    text << kCacheMagic << ' ' << kMslCacheVersion << '\n'
         << "// spirv-cross " << kSpirvCrossVersion << '\n'
         << "// stage " << ShaderStageName(shader.stage) << '\n'
         << "// entry " << shader.entry << '\n'
         << "// flags push=" << shader.uses_push_constants << " bda=" << shader.uses_device_address
         << " pagetable=" << shader.uses_bda_pagetable << " buffer_size=";
    if (shader.buffer_size_buffer == ~0u) {
        text << '-';
    } else {
        text << shader.buffer_size_buffer;
    }
    text << '\n'
         << "// counts " << shader.num_buffers << ' ' << shader.num_textures << ' '
         << shader.num_samplers << '\n';
    if (shader.variant.kind != CompileVariant::Kind::Plain &&
        shader.variant.kind != CompileVariant::Kind::GeometryKernel) {
        text << "// tess " << shader.tess_layout.input << ' ' << shader.tess_layout.output << ' '
             << shader.tess_layout.patch_input << ' ' << shader.tess_layout.patch_output << '\n';
        const auto write_members = [&](const char* label, const auto& members) {
            text << label << members.size();
            for (const auto& m : members) {
                text << ' ';
                if (m.position) text << 'P'; else text << m.location;
                text << ':' << m.offset << ':' << m.components << ':' << u32(m.type);
            }
            text << '\n';
        };
        write_members("// tessout ", shader.tess_layout.members);
        text << "// tesspatchout " << shader.tess_layout.patch_output_complete << ' ';
        write_members("", shader.tess_layout.patch_members);
    }
    text << "// stagein " << shader.stage_inputs.size();
    for (const StageInput& input : shader.stage_inputs) {
        text << ' ' << input.location << ':' << input.components << ':' << u32(input.type);
    }
    text << '\n';
    for (const ShaderBinding& b : shader.bindings) {
        text << "// bind " << b.set << ' ' << b.binding << ' ' << KindName(b.kind) << ' '
             << b.msl_index << ' ' << b.count << '\n';
    }
    text << "// msl-bytes " << msl.size() << '\n' << kCacheMslMarker << '\n' << msl;
    return text.str();
}

bool ParseCache(const std::string& text, CompiledShader& shader, std::string& msl) {
    size_t pos = 0;
    auto next_line = [&](std::string& line) {
        if (pos >= text.size()) {
            return false;
        }
        const size_t end = text.find('\n', pos);
        if (end == std::string::npos) {
            return false;
        }
        line = text.substr(pos, end - pos);
        pos = end + 1;
        return true;
    };
    auto field = [&](const char* prefix, std::string& value) {
        std::string line;
        const std::string p = std::string("// ") + prefix + ' ';
        if (!next_line(line) || !line.starts_with(p)) {
            return false;
        }
        value = line.substr(p.size());
        return true;
    };
    std::string line, value;
    if (!next_line(line) || line != std::string(kCacheMagic) + ' ' + std::to_string(kMslCacheVersion)) {
        return false;
    }
    if (!field("spirv-cross", value) || value != kSpirvCrossVersion) {
        return false;
    }
    if (!field("stage", value)) {
        return false;
    }
    shader.stage = ShaderStage::Unknown;
    for (ShaderStage s : {ShaderStage::Vertex, ShaderStage::Fragment, ShaderStage::Compute,
                          ShaderStage::TessellationControl, ShaderStage::TessellationEval,
                          ShaderStage::Geometry}) {
        if (value == ShaderStageName(s)) {
            shader.stage = s;
        }
    }
    if (shader.stage == ShaderStage::Unknown || !field("entry", shader.entry) ||
        shader.entry.empty()) {
        return false;
    }
    if (!field("flags", value)) {
        return false;
    }
    {
        int push = 0, bda = 0, pagetable = 0;
        char size_buffer[16]{};
        if (std::sscanf(value.c_str(), "push=%d bda=%d pagetable=%d buffer_size=%15s", &push, &bda,
                        &pagetable, size_buffer) != 4) {
            return false;
        }
        shader.uses_push_constants = push != 0;
        shader.uses_device_address = bda != 0;
        shader.uses_bda_pagetable = pagetable != 0;
        shader.buffer_size_buffer =
            size_buffer[0] == '-' ? ~0u : static_cast<u32>(std::strtoul(size_buffer, nullptr, 10));
    }
    if (!field("counts", value) ||
        std::sscanf(value.c_str(), "%u %u %u", &shader.num_buffers, &shader.num_textures,
                    &shader.num_samplers) != 3) {
        return false;
    }
    shader.bindings.clear();
    shader.tess_layout = {};
    shader.stage_inputs.clear();
    bool first_line = true;
    bool have_stage_in = false;
    for (;;) {
        if (!next_line(line)) {
            return false;
        }
        if (first_line && line.starts_with("// tess ")) {
            first_line = false;
            if (std::sscanf(line.c_str(), "// tess %u %u %u %u", &shader.tess_layout.input,
                            &shader.tess_layout.output, &shader.tess_layout.patch_input,
                            &shader.tess_layout.patch_output) != 4) {
                return false;
            }
            if (!next_line(line) || !line.starts_with("// tessout ")) {
                return false;
            }
            std::istringstream members(line.substr(11));
            size_t count = 0;
            members >> count;
            for (size_t i = 0; i < count; ++i) {
                std::string token;
                if (!(members >> token)) {
                    return false;
                }
                InterfaceMember m;
                unsigned offset = 0, components = 0, type = 0;
                char location[16]{};
                if (std::sscanf(token.c_str(), "%15[^:]:%u:%u:%u", location, &offset, &components,
                                &type) != 4 ||
                    type > 3) {
                    return false;
                }
                if (location[0] == 'P') {
                    m.position = true;
                } else {
                    m.location = static_cast<u32>(std::strtoul(location, nullptr, 10));
                }
                m.offset = offset;
                m.components = components;
                m.type = static_cast<ScalarType>(type);
                shader.tess_layout.members.push_back(m);
            }
            continue;
        }
        if (line.starts_with("// tesspatchout ")) {
            std::istringstream members(line.substr(16));
            unsigned complete = 0;
            size_t count = 0;
            if (!(members >> complete >> count) || complete > 1 || count > 64) return false;
            shader.tess_layout.patch_output_complete = complete != 0;
            for (size_t i = 0; i < count; ++i) {
                std::string token;
                InterfaceMember m;
                unsigned location = 0, offset = 0, components = 0, type = 0;
                if (!(members >> token) ||
                    std::sscanf(token.c_str(), "%u:%u:%u:%u", &location, &offset, &components, &type) != 4 ||
                    type > 3 || components < 1 || components > 4) return false;
                m.location = location; m.offset = offset; m.components = components;
                m.type = static_cast<ScalarType>(type);
                shader.tess_layout.patch_members.push_back(m);
            }
            continue;
        }
        first_line = false;
        if (!have_stage_in) {
            // Always present, before the bindings.
            if (!line.starts_with("// stagein ")) {
                return false;
            }
            have_stage_in = true;
            std::istringstream inputs(line.substr(11));
            size_t count = 0;
            inputs >> count;
            for (size_t i = 0; i < count; ++i) {
                std::string token;
                if (!(inputs >> token)) {
                    return false;
                }
                StageInput input;
                unsigned location = 0, components = 0, type = 0;
                if (std::sscanf(token.c_str(), "%u:%u:%u", &location, &components, &type) != 3 ||
                    type > 3) {
                    return false;
                }
                input.location = location;
                input.components = components;
                input.type = static_cast<ScalarType>(type);
                shader.stage_inputs.push_back(input);
            }
            continue;
        }
        if (!line.starts_with("// bind ")) {
            break;
        }
        ShaderBinding b;
        char kind_name[32]{};
        if (std::sscanf(line.c_str(), "// bind %u %u %31s %u %u", &b.set, &b.binding, kind_name,
                        &b.msl_index, &b.count) != 5) {
            return false;
        }
        bool known = false;
        for (Kind k : {Kind::Buffer, Kind::Texture, Kind::StorageTexture, Kind::Sampler,
                       Kind::TexelBuffer, Kind::PushConstants}) {
            if (std::strcmp(kind_name, KindName(k)) == 0) {
                b.kind = k;
                known = true;
            }
        }
        if (!known) {
            return false;
        }
        shader.bindings.push_back(b);
    }
    // `line` is the msl-bytes line.
    unsigned long long bytes = 0;
    if (std::sscanf(line.c_str(), "// msl-bytes %llu", &bytes) != 1) {
        return false;
    }
    if (!next_line(line) || line != kCacheMslMarker) {
        return false;
    }
    if (text.size() - pos != bytes) {
        return false;
    }
    msl = text.substr(pos);
    return true;
}

bool ReadFile(const std::filesystem::path& path, std::string& text) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        return false;
    }
    text.assign(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
    return file.good() || file.eof();
}

bool WriteFileAtomic(const std::filesystem::path& path, const std::string& text) {
    static std::atomic<u32> counter{0};
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    auto temp = path;
    temp += fmt::format(".tmp{}_{}", getpid(), counter.fetch_add(1));
    {
        std::ofstream file(temp, std::ios::binary | std::ios::trunc);
        file.write(text.data(), static_cast<std::streamsize>(text.size()));
        file.flush();
        if (!file) {
            file.close();
            std::filesystem::remove(temp, ec);
            return false;
        }
    }
    std::filesystem::rename(temp, path, ec);
    if (ec) {
        std::filesystem::remove(temp, ec);
        return false;
    }
    return true;
}

// ---- Metal ---------------------------------------------------------------------------------

using MetalObjects = MetalLibraryObjects;

/// The first "error:" line of a Metal compiler message, without the "program_source:L:C:" prefix.
std::string FirstMetalError(const std::string& text) {
    size_t pos = 0;
    while (pos < text.size()) {
        const size_t end = text.find('\n', pos);
        const std::string line =
            text.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
        const auto at = line.find("error:");
        if (at != std::string::npos) {
            return line.substr(at);
        }
        if (end == std::string::npos) {
            break;
        }
        pos = end + 1;
    }
    return FirstLine(text);
}

/// `relaxed_math`: MTLMathModeRelaxed instead of Safe (macOS 15 and later; below it the library is
/// always built without fast math). mathFloatingPointFunctions stays at its default (Fast).
MetalObjects BuildLibrary(id<MTLDevice> device, const std::string& msl, const std::string& entry,
                          std::string_view label, bool relaxed_math, bool inline_vertex=false, unsigned* inlined=nullptr, bool fragment_uniforms=false, unsigned* uniform_buffers=nullptr, bool copy_pair=false, bool* pair_applied=nullptr, LibraryMemo* memo=nullptr) {
    BbAudit::Scope detailed_audit{BbAudit::MetalLibrary,0,100000};
    CompileAudit audit{"library"};
    MetalObjects objects{};
    std::string source=msl;
    bool paired=false;
    if(copy_pair){auto rewrite=RewriteCopyPair(source);if(rewrite.ok){source=std::move(rewrite.source);paired=true;}}
    if(pair_applied)*pair_applied=paired;
    const auto helpers=inline_vertex?InlineStrictVertexMath(source):0;
    if(inlined) *inlined=helpers;
    unsigned uniforms=0;
    if(fragment_uniforms) {auto rewritten=RewriteFragmentUniforms(source);uniforms=rewritten.buffers;source=std::move(rewritten.source);}
    if(uniform_buffers) *uniform_buffers=uniforms;
    @autoreleasepool {
        MTLCompileOptions* options = [MTLCompileOptions new];
        options.languageVersion = MTLLanguageVersion3_1;
        if (@available(macOS 15.0, *)) {
            options.mathMode = relaxed_math ? MTLMathModeRelaxed : MTLMathModeSafe;
            options.mathFloatingPointFunctions = MTLMathFloatingPointFunctionsFast;
        } else {
            options.fastMathEnabled = NO;
        }
        const auto compile = [&](const std::string& text) -> MetalObjects {
            const auto source_build = [&]() -> MetalObjects {
                BbAudit::Scope actual_compile_audit{BbAudit::MetalLibraryBuild,0,100000};
                NSError* error = nil;
                id<MTLLibrary> library =
                    [device newLibraryWithSource:[NSString stringWithUTF8String:text.c_str()]
                                         options:options error:&error];
                if (!library) {
                    return {nil, nil, "Metal: " + FirstMetalError(
                        error ? error.localizedDescription.UTF8String : "unknown error")};
                }
                id<MTLFunction> function = [library newFunctionWithName:
                    [NSString stringWithUTF8String:entry.c_str()]];
                if (!function) {
                    // Return no accepted library: this also keeps a speculative rewrite's
                    // original-source fallback and the weak memo's failure path intact.
                    return {nil, nil, fmt::format("Metal: function '{}' not found in the library", entry)};
                }
                auto recorded=LocalAot::Try(device,text,entry,relaxed_math,true,false,function);
                if(!recorded.key.empty())std::printf("Metal local AOT: source key=%s recorded=%d\n",recorded.key.c_str(),int(recorded.recorded));
                library.label = [[NSString alloc] initWithBytes:label.data() length:label.size()
                                                      encoding:NSUTF8StringEncoding];
                return {library, function, {}};
            };
            const auto build = [&]() -> MetalObjects {
                auto local=LocalAot::Try(device,text,entry,relaxed_math,false);
                if(local.library&&local.function){
                    std::printf("Metal local AOT: hit key=%s recorded=0\n",local.key.c_str());
                    local.library.label=[[NSString alloc] initWithBytes:label.data() length:label.size() encoding:NSUTF8StringEncoding];
                    return {local.library,local.function,{},true};
                }
                return source_build();
            };
            auto built = memo ? memo->Get(text, entry, relaxed_math, build) : build();
            if (built.library && !built.function) {
                // A memo owns only the library. Every module still requests its own
                // function and retains independent reflection/binding metadata.
                built.function = [built.library newFunctionWithName:
                    [NSString stringWithUTF8String:entry.c_str()]];
                if (!built.function) {
                    // Do not retry the same artifact/memo after an entry rejection.
                    // Compile this exact effective source once; its own entry gate
                    // decides success before any request can be recorded.
                    return source_build();
                }
            }
            return built;
        };
        objects = compile(source);
        if (!objects.library && (helpers || uniforms || paired)) {
            // Unsupported compiler/source combination keeps the original rendering path.
            // Fallback has its own effective-source key; never memoize it as the rewrite.
            std::printf("Metal shader specialization rejected; retaining original source\n");
            if (inlined) *inlined = 0;
            if (uniform_buffers) *uniform_buffers = 0;
            if (pair_applied) *pair_applied = false;
            objects = compile(msl);
        }
        if (!objects.error.empty()) return objects;
        // Keep a distinct function object and independent binding metadata for each module.
        // Function identity is part of existing PSO cache keys; library reuse must not merge it.
        if (!objects.function) objects.function = [objects.library newFunctionWithName:
            [NSString stringWithUTF8String:entry.c_str()]];
        if (!objects.function) {
            objects.error = fmt::format("Metal: function '{}' not found in the library", entry);
            return objects;
        }
        audit.cached = objects.reused;
        audit.ok = true;
    }
    return objects;
}

} // namespace

const char* ShaderStageName(ShaderStage stage) {
    switch (stage) {
    case ShaderStage::Vertex:
        return "vertex";
    case ShaderStage::Fragment:
        return "fragment";
    case ShaderStage::Compute:
        return "compute";
    case ShaderStage::TessellationControl:
        return "tess_control";
    case ShaderStage::TessellationEval:
        return "tess_eval";
    case ShaderStage::Geometry:
        return "geometry";
    case ShaderStage::Unknown:
        break;
    }
    return "unknown";
}

CompiledShader::~CompiledShader() {
    // Balance the +1 of __bridge_retained; ARC releases at the end of the scope.
    if (function) {
        id<MTLFunction> released = (__bridge_transfer id<MTLFunction>)function;
        (void)released;
    }
    if (library) {
        id<MTLLibrary> released = (__bridge_transfer id<MTLLibrary>)library;
        (void)released;
    }
}

void PrepareCopyWatchShader(Device& device,const CompiledShader& shader) {
    if(!VideoCore::FrameCapture::NativeRecording())return;
    static std::mutex mutex;static unsigned modules=0;
    std::scoped_lock lock{mutex};
    if(shader.copy_watch_attempted)return;
    shader.copy_watch_attempted=true;
    if(shader.copy_watch_source.empty() || modules>=8)return;
    ++modules;
    auto watched=BuildLibrary(device.GetImpl().device,shader.copy_watch_source,shader.entry,
                              "copy pointer watch",shader.copy_watch_relaxed);
    std::string().swap(shader.copy_watch_source);
    if(watched.error.empty()) {
        auto reference=std::make_unique<CompiledShader>();
        reference->stage=shader.stage;reference->entry=shader.entry;
        reference->library=(__bridge_retained void*)watched.library;
        reference->function=(__bridge_retained void*)watched.function;
        shader.copy_watch_reference=std::move(reference);
    }
    std::printf("Copy pointer watch: lazy library reads=%u writes=%u ready=%d %s\n",
        shader.copy_watch_reads,shader.copy_watch_writes,int(shader.copy_watch_reference!=nullptr),watched.error.c_str());
}

const ShaderBinding* CompiledShader::Find(u32 set, u32 binding, ShaderBinding::Kind kind) const {
    for (const ShaderBinding& b : bindings) {
        if (b.set == set && b.binding == binding && b.kind == kind) {
            return &b;
        }
    }
    return nullptr;
}

std::filesystem::path ShaderCompiler::DefaultCacheDir() {
    using namespace Common::FS;
    return GetUserPath(PathType::CacheDir) / std::filesystem::path{Common::ElfInfo::Instance().GameSerial()} /
           "metal";
}

const char* FloatRulesName(FloatRules rules) {
    switch (rules) {
    case FloatRules::Strict:
        return "V0 (NoContraction everywhere, Safe math)";
    case FloatRules::NoContraction:
        return "V1 (no NoContraction in game fragment/compute shaders, Safe math)";
    case FloatRules::RelaxedMath:
        return "V2 (no NoContraction, Relaxed math in game fragment/compute shaders)";
    }
    return "?";
}

FloatRules ReadFloatRules() {
    // Default V1 (plan P9.1): GPU busy a frame 24.7 -> 16.5 ms at 540p, 37.3 -> 24.6 at 720p,
    // images within §6. V2's further 0.3-0.9 ms were within the noise, so Relaxed math stays
    // opt-in.
    constexpr bool kRelaxedFloatDefault = true;
    constexpr bool kRelaxedMathDefault = false;
    static const FloatRules rules = [] {
        const char* strict = std::getenv("BB_MTL_STRICT_FLOAT");
        const char* math = std::getenv("BB_MTL_RELAXED_MATH");
        bool relaxed = kRelaxedFloatDefault;
        if (strict && strict[0] == '1') {
            relaxed = false;
        } else if (strict && strict[0] == '0') {
            relaxed = true;
        }
        bool relaxed_math = kRelaxedMathDefault;
        if (math && math[0] == '0') {
            relaxed_math = false;
        } else if (math && math[0] == '1') {
            relaxed_math = true;
        }
        return !relaxed ? FloatRules::Strict
                        : relaxed_math ? FloatRules::RelaxedMath : FloatRules::NoContraction;
    }();
    return rules;
}

ShaderCompiler::ShaderCompiler(Device& device_, std::filesystem::path cache_dir_, FloatRules rules_,
                               bool allow_cache_writes_)
    : device{device_}, cache_dir{std::move(cache_dir_)}, rules{rules_},
      allow_cache_writes{allow_cache_writes_} {
    if (cache_dir.empty()) {
        cache_dir = DefaultCacheDir();
    }
    const char* reuse = std::getenv("BB_MTL_LIBRARY_REUSE");
    if (reuse && reuse[0] == '1') library_memo = std::make_unique<LibraryMemo>();
}

LibraryReuseStats ShaderCompiler::GetLibraryReuseStats() const {
    return library_memo ? library_memo->Stats() : LibraryReuseStats{};
}

ShaderCompiler::~ShaderCompiler() = default;

TessellationInfo ReadTessellationInfo(std::span<const u32> spirv) {
    return ScanSpirv(spirv).tess;
}

ShaderStage ReadShaderStage(std::span<const u32> spirv) {
    return ScanSpirv(spirv).stage;
}

CompileResult ShaderCompiler::Compile(std::span<const u32> spirv, u64 hash, std::string_view name,
                                      const CompileVariant& variant) {
    CompileAudit audit{"shader", hash};
    BbAudit::Scope translation_audit{BbAudit::MetalSpirvToMsl,hash,100000};
    CompileResult result;
    unsigned fragment_uniform_buffers=0;
    const SpirvScan scan = ScanSpirv(spirv);
    result.stage = scan.stage;
    const char* stage_name = ShaderStageName(scan.stage);
    auto fail = [&](CompileStatus status, std::string message) -> CompileResult {
        result.status = status;
        result.error = std::move(message);
        std::atomic<u64>* counter = nullptr;
        switch (status) {
        case CompileStatus::UnsupportedStage:
            counter = &counters.unsupported_stages;
            break;
        case CompileStatus::PerVertexInput:
            counter = &counters.pervertex_failures;
            break;
        default:
            counter = &counters.compile_failures;
            break;
        }
        ++*counter;
        LOG_ERROR(Render, "Metal shader {:#018x} {} ({}): {}", hash, name, stage_name, result.error);
        return std::move(result);
    };

    if (!scan.valid) {
        return fail(CompileStatus::Failed, "not a valid SPIR-V module");
    }
    if (scan.stage == ShaderStage::Unknown) {
        return fail(CompileStatus::Failed, "no supported entry point");
    }
    using VariantKind = CompileVariant::Kind;
    switch (variant.kind) {
    case VariantKind::Plain:
        if (scan.stage == ShaderStage::TessellationControl ||
            scan.stage == ShaderStage::TessellationEval) {
            return fail(CompileStatus::UnsupportedStage,
                        "a tessellation stage compiles only as a variant of a tessellated draw");
        }
        if (scan.stage == ShaderStage::Geometry) {
            return fail(CompileStatus::UnsupportedStage,
                        "a geometry stage compiles only as the kernel of a geometry draw");
        }
        if (scan.stage != ShaderStage::Vertex && scan.stage != ShaderStage::Fragment &&
            scan.stage != ShaderStage::Compute) {
            return fail(CompileStatus::UnsupportedStage, "stage not supported");
        }
        break;
    case VariantKind::TessVertex:
        if (scan.stage != ShaderStage::Vertex) {
            return fail(CompileStatus::Failed, "tessellation vertex variant of another stage");
        }
        break;
    case VariantKind::TessControl:
        if (scan.stage != ShaderStage::TessellationControl) {
            return fail(CompileStatus::Failed, "tessellation control variant of another stage");
        }
        break;
    case VariantKind::TessEval:
        if (scan.stage != ShaderStage::TessellationEval) {
            return fail(CompileStatus::Failed, "tessellation evaluation variant of another stage");
        }
        if (scan.tess.domain != TessDomain::Triangles && scan.tess.domain != TessDomain::Quads) {
            return fail(CompileStatus::Failed, "Metal tessellates triangles and quads only");
        }
        break;
    case VariantKind::GeometryKernel:
        if (scan.stage != ShaderStage::Geometry) {
            return fail(CompileStatus::Failed, "geometry kernel variant of another stage");
        }
        break;
    }

    auto shader = std::make_unique<CompiledShader>();
    shader->stage = scan.stage;
    shader->uses_device_address = scan.device_address;
    shader->local_size = scan.local_size;
    shader->uses_bda_pagetable = scan.bda_pagetable;
    shader->variant = variant;
    shader->tess = scan.tess;

    // The geometry shader becomes a compute kernel before anything else; its shape is read from
    // the SPIR-V every time (also on a disk cache hit).
    GeometryRewrite geometry;
    if (variant.kind == VariantKind::GeometryKernel) {
        geometry = RewriteGeometryShader(spirv, variant.es);
        if (!geometry.ok) {
            return fail(CompileStatus::UnsupportedStage, "geometry shader: " + geometry.error);
        }
        shader->geometry = geometry.info;
        shader->local_size = {kGeometryThreads, 1, 1};
    }

    const u64 spirv_hash = XXH3_64bits(spirv.data(), spirv.size_bytes());
    std::string tag; // the variants have their own cache files
    switch (variant.kind) {
    case VariantKind::Plain:
        break;
    case VariantKind::TessVertex:
        tag = fmt::format("_tv{}", variant.index_type);
        break;
    case VariantKind::TessControl:
        tag = fmt::format("_tc{}_pl1", u32(variant.domain));
        break;
    case VariantKind::TessEval:
        tag = fmt::format("_te{}", variant.output_vertices);
        if (variant.link_patch_input) {
            std::vector<u32> layout{variant.patch_input.stride};
            for (const auto& m : variant.patch_input.members) {
                layout.insert(layout.end(),
                              {m.location, u32(m.position), m.offset, m.components, u32(m.type)});
            }
            tag += fmt::format("_pl1_{:016x}", XXH3_64bits(layout.data(), layout.size() * sizeof(u32)));
        }
        break;
    case VariantKind::GeometryKernel: {
        // The rewritten module depends on the export layout.
        std::vector<u32> layout{variant.es.stride};
        for (const InterfaceMember& m : variant.es.members) {
            layout.insert(layout.end(),
                          {m.location, u32(m.position), m.offset, m.components, u32(m.type)});
        }
        tag = fmt::format("_gk{:016x}", XXH3_64bits(layout.data(), layout.size() * sizeof(u32)));
        break;
    }
    }
    const std::filesystem::path cache_file =
        cache_dir / fmt::format("{:016x}_{}{}_{:016x}.metal", hash, stage_name, tag, spirv_hash);
    id<MTLDevice> mtl_device = device.GetImpl().device;
    const std::string label{name};
    // P9.1: Relaxed math only for the game's fragment shaders without depth export and the
    // game's compute shaders (the Plain variants); the same decision on the cache-hit and the
    // converting path below.
    const bool relaxed_math = rules == FloatRules::RelaxedMath &&
                              variant.kind == VariantKind::Plain &&
                              (scan.stage == ShaderStage::Compute ||
                               (scan.stage == ShaderStage::Fragment && !scan.depth_replacing));

    static const bool uniforms_enabled=[] {
        const char* value=std::getenv("BB_MTL_FRAGMENT_UNIFORMS");return value && value[0]=='1';
    }();
    const bool fragment_uniforms=uniforms_enabled && scan.stage==ShaderStage::Fragment && variant.kind==VariantKind::Plain;
    const bool inline_vertex=VertexMathInliningEnabled() && scan.stage==ShaderStage::Vertex &&
                             variant.kind==VariantKind::Plain;
    static const bool pair_enabled=[]{const char* e=std::getenv("BB_MTL_COPY_PAIR");return e && e[0]=='1';}();
    const bool merge_pair=pair_enabled && hash==0xfefebf9full && scan.stage==ShaderStage::Compute && variant.kind==VariantKind::Plain;
    MetalObjects objects{};
    std::string msl;
    bool cached = false;
    {
        std::string text;
        if (ReadFile(cache_file, text)) {
            CompiledShader parsed;
            std::string cached_msl;
            if (ParseCache(text, parsed, cached_msl) && parsed.stage == scan.stage) {
                objects = BuildLibrary(mtl_device, cached_msl, parsed.entry, label, relaxed_math, inline_vertex, &shader->strict_vertex_helpers_inlined, fragment_uniforms, &fragment_uniform_buffers, merge_pair, &shader->copy_pair_merged, library_memo.get());
                if (objects.error.empty()) {
                    shader->entry = std::move(parsed.entry);
                    shader->bindings = std::move(parsed.bindings);
                    shader->uses_push_constants = parsed.uses_push_constants;
                    shader->buffer_size_buffer = parsed.buffer_size_buffer;
                    shader->num_buffers = parsed.num_buffers;
                    shader->num_textures = parsed.num_textures;
                    shader->num_samplers = parsed.num_samplers;
                    shader->tess_layout = std::move(parsed.tess_layout);
                    shader->stage_inputs = std::move(parsed.stage_inputs);
                    msl = std::move(cached_msl);
                    cached = true;
                } else {
                    // A cached text the Metal compiler rejects now (new OS?): convert again.
                    std::error_code ec;
                    if (allow_cache_writes) std::filesystem::remove(cache_file, ec);
                }
            }
        }
    }

    if (!cached) {
        // PerVertexKHR inputs: SPIRV-Cross cannot translate them, the rewrite replaces them with
        // ordinary inputs and barycentric arithmetic (P3.1b).
        std::span<const u32> module = spirv;
        PerVertexRewrite rewritten;
        if (variant.kind == VariantKind::GeometryKernel) {
            module = geometry.spirv;
        } else if (scan.per_vertex) {
            rewritten = RewritePerVertexInputs(spirv);
            if (!rewritten.ok) {
                return fail(CompileStatus::PerVertexInput,
                            "PerVertexKHR input (P3.1b): " + rewritten.error);
            }
            module = rewritten.spirv;
        }
        try {
            msl = Convert(module, scan, variant, *shader);
        } catch (const std::exception& e) {
            return fail(CompileStatus::Failed, "SPIRV-Cross: " + FirstLine(e.what()));
        }
        objects = BuildLibrary(mtl_device, msl, shader->entry, label, relaxed_math, inline_vertex, &shader->strict_vertex_helpers_inlined, fragment_uniforms, &fragment_uniform_buffers, merge_pair, &shader->copy_pair_merged, library_memo.get());
        if (!objects.error.empty()) {
            return fail(CompileStatus::Failed, objects.error);
        }
        if (allow_cache_writes && WriteFileAtomic(cache_file, CacheText(*shader, msl))) {
            ++counters.cache_stores;
        }
    } else {
        ++counters.cache_hits;
    }
    if (fragment_uniform_buffers) std::printf("Metal fragment uniforms: hash=%016llx, buffers=%u; bounded 4KiB reads, same textures and math\n", (unsigned long long)hash, fragment_uniform_buffers);
    shader->from_disk_cache = cached;
    shader->library = (__bridge_retained void*)objects.library;
    shader->function = (__bridge_retained void*)objects.function;

    static const bool copy_watch=[] {
        const char* mode=std::getenv("BB_CAPTURE_NATIVE");const char* flag=std::getenv("BB_CAPTURE_COPY_POINTERS");
        return mode && mode[0]=='1' && flag && flag[0]=='1';
    }();
    if(copy_watch && hash==0xfefebf9full && scan.stage==ShaderStage::Compute &&
       variant.kind==VariantKind::Plain && shader->num_buffers<CopyWatchRootSlot) {
        const auto rewrite=RewriteCopyWatch(msl);
        if(rewrite.ok) {
            shader->copy_watch_source=rewrite.source;shader->copy_watch_relaxed=relaxed_math;
            shader->copy_watch_reads=rewrite.reads;shader->copy_watch_writes=rewrite.writes;
        } else std::printf("Copy pointer watch: unsupported shader %llx: %s\n",
                           (unsigned long long)hash,rewrite.error.c_str());
    }

    if(shader->strict_vertex_helpers_inlined && VertexMathReferenceRequested(hash) && ReserveVertexMathReference()) {
        // Keep cached source byte-for-byte unchanged. Both states use the same bindings,
        // Safe mode, explicit fma operands and shader body; only helper call barriers differ.
        auto original=BuildLibrary(mtl_device,msl,shader->entry,label+" strict reference",false);
        if(original.error.empty()) {
            auto reference=std::make_unique<CompiledShader>();
            reference->stage=shader->stage;reference->entry=shader->entry;
            reference->bindings=shader->bindings;reference->push_constant_buffer=shader->push_constant_buffer;
            reference->uses_push_constants=shader->uses_push_constants;
            reference->buffer_size_buffer=shader->buffer_size_buffer;
            reference->uses_device_address=shader->uses_device_address;
            reference->uses_bda_pagetable=shader->uses_bda_pagetable;
            reference->num_buffers=shader->num_buffers;reference->num_textures=shader->num_textures;
            reference->num_samplers=shader->num_samplers;reference->variant=shader->variant;
            reference->library=(__bridge_retained void*)original.library;
            reference->function=(__bridge_retained void*)original.function;
            shader->strict_vertex_reference=std::move(reference);
        }
        std::printf("Metal vertex math shader: hash=%llx, helpers=%u, reference=%d; strict fma, Safe, contract(off)\n",
            (unsigned long long)hash,shader->strict_vertex_helpers_inlined,int(shader->strict_vertex_reference!=nullptr));
    }
    ++counters.compiled;
    audit.ok = true; audit.cached = cached;
    if (library_memo && CompileAudit::Enabled()) {
        const auto stats = library_memo->Stats();
        if (stats.builds + stats.reuses <= 8192)
            std::printf("Metal library reuse: builds=%llu reused=%llu waits=%llu entries=%llu\n",
                (unsigned long long)stats.builds, (unsigned long long)stats.reuses,
                (unsigned long long)stats.waits, (unsigned long long)stats.entries);
    }
    result.status = CompileStatus::Ok;
    result.shader = std::move(shader);
    return result;
}

} // namespace Metal
