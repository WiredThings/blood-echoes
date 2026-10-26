// SPDX-License-Identifier: GPL-2.0-or-later
// P9.1: the Metal backend's looser float rules. With Profile::relaxed_fragment_compute_float
// set, game fragment shaders without depth export and compute shaders carry no NoContraction on
// their float add/sub/mul/div/fma; vertex shaders and depth exports keep it. With the field clear
// (every Vulkan run, the strict Metal rules) the SPIR-V must stay exactly what it was before.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <set>
#include <string>
#include <vector>
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/ir/ir_emitter.h"

namespace {

enum class Kind { Vertex, Fragment, FragmentDepth, Compute };

constexpr const char* KindName(Kind kind) {
    switch (kind) {
    case Kind::Vertex:
        return "vertex";
    case Kind::Fragment:
        return "fragment";
    case Kind::FragmentDepth:
        return "fragment+depth";
    case Kind::Compute:
        return "compute";
    }
    return "?";
}

constexpr u32 kOpDecorate = 71;
constexpr u32 kOpFAdd = 129;
constexpr u32 kOpFSub = 131;
constexpr u32 kOpFMul = 133;
constexpr u32 kOpFDiv = 136;
constexpr u32 kOpExtInst = 12;
constexpr u32 kDecorationNoContraction = 42;
constexpr u32 kGlslFma = 50;

// Golden FNV-1a 64 hashes of the SPIR-V words of the field-false output, recorded on the tree
// before the relaxed field existed. They change only when the emitter changes on purpose.
constexpr uint64_t kGoldenVertex = 0x3295f1f67897cae1ull;
constexpr uint64_t kGoldenFragment = 0xb56aa8253170dc45ull;
constexpr uint64_t kGoldenFragmentDepth = 0x899f3d958e08b449ull;
constexpr uint64_t kGoldenCompute = 0xa4f406540561bf69ull;

uint64_t HashWords(const std::vector<u32>& words) {
    uint64_t hash = 0xcbf29ce484222325ull;
    for (const u32 word : words) {
        for (u32 i = 0; i < 4; ++i) {
            hash ^= (word >> (8 * i)) & 0xff;
            hash *= 0x100000001b3ull;
        }
    }
    return hash;
}

std::vector<u32> Emit(Kind kind, bool relaxed) {
    using namespace Shader;
    Info info{};
    const bool vertex = kind == Kind::Vertex;
    const bool compute = kind == Kind::Compute;
    info.hw_stage = vertex ? HwStage::Vertex : compute ? HwStage::Compute : HwStage::Fragment;
    info.sw_stage = vertex ? SwStage::Vertex : compute ? SwStage::Compute : SwStage::Fragment;
    RuntimeInfo runtime{};
    runtime.Initialize(info.hw_stage, info.sw_stage);
    if (compute) {
        runtime.hw.cs.workgroup_size = {8, 8, 1};
    } else if (!vertex) {
        runtime.hw.fs.color_buffers[0].num_format = AmdGpu::NumberFormat::Float;
    }
    Common::ObjectPool<IR::Inst> pool;
    IR::Block block(pool);
    IR::IREmitter ir(block);
    const auto a = ir.Imm32(1.5f);
    const auto b = ir.Imm32(2.25f);
    const auto c = ir.Imm32(0.5f);
    const IR::F32 add = ir.FPAdd(a, b);
    const IR::F32 sub = ir.FPSub(add, c);
    const IR::F32 mul = ir.FPMul(sub, b);
    const IR::F32 fma = ir.FPFma(mul, a, c);
    const IR::F32 div = ir.FPDiv(fma, b);
    if (!compute) {
        const auto output = vertex ? IR::Attribute::Position0 : IR::Attribute::RenderTarget0;
        const IR::F32 results[4] = {fma, div, mul, sub};
        for (u32 component = 0; component < 4; ++component) {
            info.stores.Set(output, component);
            ir.SetAttribute(output, results[component], component);
        }
        if (kind == Kind::FragmentDepth) {
            info.stores.Set(IR::Attribute::Depth, 0);
            ir.SetAttribute(IR::Attribute::Depth, div, 0);
        }
    }
    ir.Epilogue();
    IR::Program program(info);
    program.blocks.push_back(&block);
    program.syntax_list.push_back({.data = {.block = &block},
                                   .type = IR::AbstractSyntaxNode::Type::Block});
    program.syntax_list.push_back({.type = IR::AbstractSyntaxNode::Type::Return});
    Profile profile{};
    profile.supported_spirv = 0x00010600;
    profile.support_int64 = true;
    profile.relaxed_fragment_compute_float = relaxed;
    Backend::Bindings bindings{};
    return Backend::SPIRV::EmitSPIRV(profile, runtime, program, bindings);
}

struct Counts {
    u32 float_ops{};      // add/sub/mul/div/fma result ids
    u32 decorated{};      // of those, how many carry NoContraction
    u32 no_contraction{}; // all NoContraction decorations
};

Counts Count(const std::vector<u32>& words) {
    std::set<u32> float_results;
    std::set<u32> decorated_ids;
    Counts counts{};
    for (size_t i = 5; i < words.size();) {
        const u32 opcode = words[i] & 0xffff;
        const u32 length = words[i] >> 16;
        if (length == 0 || i + length > words.size()) {
            std::fprintf(stderr, "malformed SPIR-V at word %zu\n", i);
            std::exit(2);
        }
        switch (opcode) {
        case kOpFAdd:
        case kOpFSub:
        case kOpFMul:
        case kOpFDiv:
            float_results.insert(words[i + 2]);
            break;
        case kOpExtInst:
            if (length >= 5 && words[i + 4] == kGlslFma) {
                float_results.insert(words[i + 2]);
            }
            break;
        case kOpDecorate:
            if (length >= 3 && words[i + 2] == kDecorationNoContraction) {
                decorated_ids.insert(words[i + 1]);
                ++counts.no_contraction;
            }
            break;
        default:
            break;
        }
        i += length;
    }
    counts.float_ops = static_cast<u32>(float_results.size());
    for (const u32 id : float_results) {
        counts.decorated += decorated_ids.count(id);
    }
    return counts;
}

int failures = 0;

void Check(bool ok, const char* what, Kind kind) {
    if (!ok) {
        std::fprintf(stderr, "FAIL (%s): %s\n", KindName(kind), what);
        ++failures;
    }
}

} // namespace

int main() {
    constexpr Kind kinds[] = {Kind::Vertex, Kind::Fragment, Kind::FragmentDepth, Kind::Compute};
    constexpr uint64_t golden[] = {kGoldenVertex, kGoldenFragment, kGoldenFragmentDepth,
                                   kGoldenCompute};
    for (u32 i = 0; i < 4; ++i) {
        const Kind kind = kinds[i];
        const auto strict = Emit(kind, false);
        const auto relaxed = Emit(kind, true);
        const Counts strict_counts = Count(strict);
        const Counts relaxed_counts = Count(relaxed);
        const uint64_t hash = HashWords(strict);
        std::printf("%-15s float ops %u, strict NoContraction %u/%u, relaxed %u, hash %016llx\n",
                    KindName(kind), strict_counts.float_ops, strict_counts.decorated,
                    strict_counts.no_contraction, relaxed_counts.no_contraction,
                    static_cast<unsigned long long>(hash));
        Check(strict_counts.float_ops == 5, "expected add, sub, mul, fma and div in the SPIR-V", kind);
        Check(strict_counts.decorated == strict_counts.float_ops,
              "field false: every float op must carry NoContraction", kind);
        Check(hash == golden[i], "field false: SPIR-V differs from the golden hash", kind);
        if (kind == Kind::Fragment || kind == Kind::Compute) {
            Check(relaxed_counts.no_contraction == 0,
                  "field true: no NoContraction expected in this shader", kind);
        } else {
            Check(relaxed == strict,
                  "field true: this shader must stay byte-identical to the strict output", kind);
        }
    }
    if (failures != 0) {
        std::fprintf(stderr, "spirv-float-rules-test: %d failure(s)\n", failures);
        return 1;
    }
    std::puts("spirv-float-rules-test: ok");
    return 0;
}
