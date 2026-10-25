// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstddef>
#include <cstring>
#include <stdexcept>

#include "shader_recompiler/frontend/instruction.h"

namespace Shader::Gcn {

struct InstFormat {
    InstClass inst_class = InstClass::Undefined;
    InstCategory inst_category = InstCategory::Undefined;
    u32 src_count = 0;
    u32 dst_count = 0;
    ScalarType src_type = ScalarType::Undefined;
    ScalarType dst_type = ScalarType::Undefined;
};

InstEncoding GetInstructionEncoding(u32 token);

u32 GetEncodingLength(InstEncoding encoding);

InstFormat InstructionFormat(InstEncoding encoding, u32 opcode);

Opcode DecodeOpcode(u32 token);

class GcnCodeSlice {
public:
    /// checked=true is for bounded owned/capture inputs; legacy ordered callers retain their
    /// current fast path. Checks apply to all decoder reads, including literal extensions.
    GcnCodeSlice(const u32* ptr, const u32* end, bool checked = false)
        : m_ptr(ptr), m_end(end), m_checked(checked) {}
    GcnCodeSlice(const GcnCodeSlice& other) = default;
    ~GcnCodeSlice() = default;

    u32 at(u32 id) const {
        RequireWords(size_t(id) + 1);
        return m_ptr[id];
    }

    u32 readu32() {
        RequireWords(1);
        return *(m_ptr++);
    }

    u64 readu64() {
        RequireWords(2);
        u64 value;
        std::memcpy(&value, m_ptr, sizeof(value));
        m_ptr += 2;
        return value;
    }

    bool atEnd() const {
        return m_ptr == m_end;
    }

    // bbport: current read position (fetch shader parse cache)
    const u32* position() const {
        return m_ptr;
    }

private:
    void RequireWords(size_t count) const {
        if (!m_checked) return;
        if (m_ptr > m_end || count > size_t(m_end - m_ptr)) {
            throw std::runtime_error{"truncated bounded GCN input"};
        }
    }
    const u32* m_ptr{};
    const u32* m_end{};
    bool m_checked{};
};

class GcnDecodeContext {
public:
    GcnInst decodeInstruction(GcnCodeSlice& code);

private:
    uint32_t getEncodingLength(InstEncoding encoding);
    uint32_t getOpMapOffset(InstEncoding encoding);
    uint32_t mapEncodingOp(InstEncoding encoding, Opcode opcode);
    void updateInstructionMeta(InstEncoding encoding);
    uint32_t getMimgModifier(Opcode opcode);
    void repairOperandType();

    OperandField getOperandField(uint32_t code);

    void decodeInstruction32(InstEncoding encoding, GcnCodeSlice& code);
    void decodeInstruction64(InstEncoding encoding, GcnCodeSlice& code);
    void decodeLiteralConstant(InstEncoding encoding, GcnCodeSlice& code);
    void decodeSubDwordAddressing(InstEncoding encoding, GcnCodeSlice& code);
    void decodeDataParallelPrimitive(InstEncoding encoding, GcnCodeSlice& code);

    // 32 bits encodings
    void decodeInstructionSOP1(uint32_t hexInstruction);
    void decodeInstructionSOPP(uint32_t hexInstruction);
    void decodeInstructionSOPC(uint32_t hexInstruction);
    void decodeInstructionSOPK(uint32_t hexInstruction);
    void decodeInstructionSOP2(uint32_t hexInstruction);
    void decodeInstructionVOP1(uint32_t hexInstruction);
    void decodeInstructionVOPC(uint32_t hexInstruction);
    void decodeInstructionVOP2(uint32_t hexInstruction);
    void decodeInstructionSMRD(uint32_t hexInstruction);
    void decodeInstructionVINTRP(uint32_t hexInstruction);
    // 64 bits encodings
    void decodeInstructionVOP3(uint64_t hexInstruction);
    void decodeInstructionVOP3P(uint64_t hexInstruction);
    void decodeInstructionMUBUF(uint64_t hexInstruction);
    void decodeInstructionMTBUF(uint64_t hexInstruction);
    void decodeInstructionMIMG(uint64_t hexInstruction);
    void decodeInstructionDS(uint64_t hexInstruction);
    void decodeInstructionEXP(uint64_t hexInstruction);

private:
    GcnInst m_instruction;
};

} // namespace Shader::Gcn
