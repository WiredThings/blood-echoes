// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstring>
#include <unordered_map>
#include <vector>
#include "bbport_toggles.h"
#include "common/assert.h"
#include "shader_recompiler/frontend/decode.h"
#include "shader_recompiler/compile_snapshot.h"
#include "shader_recompiler/frontend/fetch_shader.h"

namespace Shader::Gcn {

/**
 * s_load_dwordx4 s[8:11], s[2:3], 0x00
 * s_load_dwordx4 s[12:15], s[2:3], 0x04
 * s_load_dwordx4 s[16:19], s[2:3], 0x08
 * s_waitcnt     lgkmcnt(0)
 * buffer_load_format_xyzw v[4:7], v0, s[8:11], 0 idxen
 * buffer_load_format_xyz v[8:10], v0, s[12:15], 0 idxen
 * buffer_load_format_xy v[12:13], v0, s[16:19], 0 idxen
 * s_waitcnt     0
 * s_setpc_b64   s[0:1]

 * s_load_dwordx4  s[4:7], s[2:3], 0x0
 * s_waitcnt       lgkmcnt(0)
 * buffer_load_format_xyzw v[4:7], v0, s[4:7], 0 idxen
 * s_load_dwordx4  s[4:7], s[2:3], 0x8
 * s_waitcnt       lgkmcnt(0)
 * buffer_load_format_xyzw v[8:11], v0, s[4:7], 0 idxen
 * s_waitcnt       vmcnt(0) & expcnt(0) & lgkmcnt(0)
 * s_setpc_b64     s[0:1]

 * A normal fetch shader looks like the above, the instructions are generated
 * using input semantics on cpu side. Load instructions can either be separate or interleaved
 * We take the reverse way, extract the original input semantics from these instructions.
 **/

static bool IsTypedBufferLoad(const Gcn::GcnInst& inst) {
    return inst.opcode == Opcode::TBUFFER_LOAD_FORMAT_X ||
           inst.opcode == Opcode::TBUFFER_LOAD_FORMAT_XY ||
           inst.opcode == Opcode::TBUFFER_LOAD_FORMAT_XYZ ||
           inst.opcode == Opcode::TBUFFER_LOAD_FORMAT_XYZW;
}

const u32* GetFetchShaderCode(const Info& info, u32 sgpr_base) {
    if (info.compile_snapshot) {
        throw std::runtime_error{"owned shader raw fetch-code access was not captured"};
    }
    if (info.speculative_reads && (sgpr_base > info.UserData().size() ||
                                  2 > info.UserData().size() - sgpr_base))
        SpeculativeReadState::Reject();
    const u32* code;
    std::memcpy(&code, &info.UserData()[sgpr_base], sizeof(code));
    return code;
}

// Speculative parses never compare/cache guest pointers. Every instruction is assembled
// in a small owned slice, including literal/SDWA/DPP extensions, before checked decoding.
static FetchShaderData ParseSpeculativeFetch(const Info& info, const u32* code) {
    auto& reads = *info.speculative_reads;
    FetchShaderData data{};
    GcnDecodeContext decoder;
    struct VsharpLoad {
        u32 dword_offset{};
        u32 base_sgpr{};
    };
    std::array<VsharpLoad, 104> loads{};

    std::array<bool,104> loaded{};
    u32 semantic_index = 0;
    for (;;) {
        if (data.size >= SpeculativeReadState::MaxFetchBytes) SpeculativeReadState::Reject();
        std::array<u32,8> instruction{};
        GcnInst inst;
        size_t words = 0;
        for (;;) {
            if (words == instruction.size() ||
                data.size + (words + 1) * sizeof(u32) > SpeculativeReadState::MaxFetchBytes)
                SpeculativeReadState::Reject();
            reads.Copy(&instruction[words], SpeculativeReadState::Address(
                reinterpret_cast<u64>(code), data.size + words * sizeof(u32)), sizeof(u32));
            ++words;
            GcnCodeSlice owned(instruction.data(), instruction.data()+words, true);
            try { inst = decoder.decodeInstruction(owned); break; }
            catch (const GcnTruncatedInput&) { /* Capture the required extension next. */ }
        }
        data.size += inst.length;

        if (inst.opcode == Opcode::S_SETPC_B64) {
            break;
        }

        if (inst.inst_class == InstClass::ScalarMemRd) {
            if (inst.opcode != Opcode::S_LOAD_DWORDX4 || inst.dst[0].code > loads.size() - 4 ||
                inst.src[0].code * 2 > 14 || inst.control.smrd.offset > 255 ||
                !inst.control.smrd.imm) SpeculativeReadState::Reject();
            loaded[inst.dst[0].code] = true;
            loads[inst.dst[0].code] = VsharpLoad{inst.control.smrd.offset, inst.src[0].code * 2};
            continue;
        }

        if (inst.opcode == Opcode::V_ADD_I32) {
            if (inst.src[0].code > 15 || inst.src[0].field != OperandField::ScalarGPR ||
                inst.src[1].field != OperandField::VectorGPR) SpeculativeReadState::Reject();
            const auto vgpr = inst.dst[0].code;
            const auto sgpr = s8(inst.src[0].code);
            switch (vgpr) {
            case 0: // V0 is always the vertex offset
                data.vertex_offset_sgpr = sgpr;
                break;
            case 3: // V3 is always the instance offset
                data.instance_offset_sgpr = sgpr;
                break;
            default:
                SpeculativeReadState::Reject();
            }
        }

        if (inst.inst_class == InstClass::VectorMemBufFmt) {
            // SRSRC is in units of 4 SPGRs while SBASE is in pairs of SGPRs
            const u32 base_sgpr = inst.src[2].code * 4;
            const bool format_load = IsTypedBufferLoad(inst) ||
                (inst.opcode >= Opcode::BUFFER_LOAD_FORMAT_X &&
                 inst.opcode <= Opcode::BUFFER_LOAD_FORMAT_XYZW);
            if (!format_load || base_sgpr >= loads.size() || !loaded[base_sgpr] ||
                data.attributes.size() >= SpeculativeReadState::MaxAttributes ||
                inst.control.mubuf.count < 1 || inst.control.mubuf.count > 4 ||
                inst.control.mtbuf.offset > 255 || inst.src[0].code > 3)
                SpeculativeReadState::Reject();
            auto& attrib = data.attributes.emplace_back();
            attrib.semantic = semantic_index++;
            attrib.dest_vgpr = inst.src[1].code;
            attrib.num_elements = inst.control.mubuf.count;
            attrib.sgpr_base = loads[base_sgpr].base_sgpr;
            attrib.dword_offset = loads[base_sgpr].dword_offset;
            attrib.inst_offset = inst.control.mtbuf.offset;
            attrib.instance_data = inst.src[0].code;
            if (IsTypedBufferLoad(inst)) {
                attrib.data_format = inst.control.mtbuf.dfmt;
                attrib.num_format = inst.control.mtbuf.nfmt;
            }
            continue;
        }
        if (inst.opcode != Opcode::S_WAITCNT && inst.opcode != Opcode::S_NOP &&
            inst.opcode != Opcode::V_ADD_I32) SpeculativeReadState::Reject();
    }
    return data;
}

std::optional<FetchShaderData> ParseFetchShader(const Shader::Info& info) {
    if (info.compile_snapshot) {
        return CompileSnapshotFetch(*info.compile_snapshot);
    }
    if (!info.has_fetch_shader) {
        return std::nullopt;
    }

    const auto* code = GetFetchShaderCode(info, info.fetch_shader_sgpr_base);
    if (info.speculative_reads) return ParseSpeculativeFetch(info, code);

    // bbport: the pipeline cache parses the fetch shader on every draw. Results are cached
    // per thread by code address and revalidated against a copy of the code, which is
    // much cheaper than decoding it again.
    struct CachedParse {
        std::vector<u32> code;
        FetchShaderData data;
    };
    thread_local std::unordered_map<const u32*, CachedParse> cache;
    if (const auto it = cache.find(code);
        !BbToggle::Disabled(BbToggle::FetchShaderCache) && it != cache.end() &&
        std::memcmp(code, it->second.code.data(), it->second.code.size() * sizeof(u32)) == 0) {
#if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
        ObserveCapturedFetch(info,it->second.data);
#endif
        return it->second.data;
    }

    FetchShaderData data{};
    GcnCodeSlice code_slice(code, code + std::numeric_limits<u32>::max());
    GcnDecodeContext decoder;

    struct VsharpLoad {
        u32 dword_offset{};
        u32 base_sgpr{};
    };
    std::array<VsharpLoad, 104> loads{};

    u32 semantic_index = 0;
    while (!code_slice.atEnd()) {
        const auto inst = decoder.decodeInstruction(code_slice);
        data.size += inst.length;

        if (inst.opcode == Opcode::S_SETPC_B64) {
            break;
        }

        if (inst.inst_class == InstClass::ScalarMemRd) {
            loads[inst.dst[0].code] = VsharpLoad{inst.control.smrd.offset, inst.src[0].code * 2};
            continue;
        }

        if (inst.opcode == Opcode::V_ADD_I32) {
            const auto vgpr = inst.dst[0].code;
            const auto sgpr = s8(inst.src[0].code);
            switch (vgpr) {
            case 0: // V0 is always the vertex offset
                data.vertex_offset_sgpr = sgpr;
                break;
            case 3: // V3 is always the instance offset
                data.instance_offset_sgpr = sgpr;
                break;
            default:
                UNREACHABLE();
            }
        }

        if (inst.inst_class == InstClass::VectorMemBufFmt) {
            // SRSRC is in units of 4 SPGRs while SBASE is in pairs of SGPRs
            const u32 base_sgpr = inst.src[2].code * 4;
            auto& attrib = data.attributes.emplace_back();
            attrib.semantic = semantic_index++;
            attrib.dest_vgpr = inst.src[1].code;
            attrib.num_elements = inst.control.mubuf.count;
            attrib.sgpr_base = loads[base_sgpr].base_sgpr;
            attrib.dword_offset = loads[base_sgpr].dword_offset;
            attrib.inst_offset = inst.control.mtbuf.offset;
            attrib.instance_data = inst.src[0].code;
            if (IsTypedBufferLoad(inst)) {
                attrib.data_format = inst.control.mtbuf.dfmt;
                attrib.num_format = inst.control.mtbuf.nfmt;
            }
        }
    }

    if (cache.size() >= 4096) {
        cache.clear();
    }
    cache[code] = CachedParse{std::vector<u32>(code, code_slice.position()), data};
#if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
    ObserveCapturedFetch(info,data);
#endif
    return data;
}

} // namespace Shader::Gcn
