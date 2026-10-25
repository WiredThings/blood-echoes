// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <span>
#include <stdexcept>
#include <vector>
#include "common/guest_capture.h"
#include "shader_recompiler/ir/passes/srt.h"

namespace Shader {
// A failed speculative read is an ordinary exception AFTER the POD recovery leaf returns.
// This is a per-entry work/read budget, not a mapping lease or an adoption identity.
struct SpeculativeReadState {
    static constexpr size_t MaxBytes = 2 * 1024 * 1024;
    static constexpr size_t MaxReads = 16384;
    static constexpr size_t MaxFlatBytes = 1024 * 1024;
    static constexpr size_t MaxWalkerBytes = 256 * 1024;
    static constexpr size_t MaxFetchBytes = 32 * 1024;
    static constexpr size_t MaxAttributes = 32;
    size_t charged_bytes{}, reads{};
    std::vector<u8> initialized;

    [[noreturn]] static void Reject() { throw std::runtime_error{"unavailable bounded prep input"}; }
    void Charge(size_t bytes) {
        if (bytes > MaxBytes - charged_bytes) Reject();
        charged_bytes += bytes;
    }
    static u64 Address(u64 base, u64 offset) {
        if (!base || offset > std::numeric_limits<u64>::max() - base) Reject();
        return base + offset;
    }
    void Copy(void* dst, u64 address, size_t bytes, bool translate = false) {
        if (reads >= MaxReads) Reject();
        ++reads; Charge(bytes);
#if defined(__aarch64__)
        // Preserve the normal SRT address translation, but do not invoke the reader that
        // substitutes zero for unavailable memory. A successful copy is not content stability.
        if (translate && srt_guest_translate) address = srt_guest_translate(address);
#endif
        Common::GuestCapture::CopyOrThrow(dst, reinterpret_cast<const void*>(address), bytes);
    }
    void RequireFlat(size_t at, size_t words = 1) const {
        if (at > initialized.size() || words > initialized.size() - at) Reject();
        for (size_t i = at; i < at + words; ++i) if (!initialized[i]) Reject();
    }
    void PrepareFlat(const PersistentSrtInfo& srt, std::span<const u32> ud,
                     std::vector<u32>& flat) {
        const size_t words = srt.flattened_bufsize_dw;
        if (ud.size() > 16 || words < ud.size() || words > MaxFlatBytes / sizeof(u32)) Reject();
        // Charge output storage and initialization metadata as well as all copied inputs.
        Charge(words * (sizeof(u32) + sizeof(u8)));
        flat.resize(words); initialized.assign(words, 0);
        std::memcpy(flat.data(), ud.data(), ud.size_bytes());
        std::fill_n(initialized.begin(), ud.size(), u8{1});
        if (!srt.walker_func) {
            if (srt.walker_func_size) Reject();
            return;
        }
#if !defined(__aarch64__)
        // x86 walkers are executable machine code, not the portable bytecode below.
        Reject();
#else
        if (!srt.walker_func_size || srt.walker_func_size % (3 * sizeof(u32)) ||
            srt.walker_func_size > MaxWalkerBytes) Reject();
        std::vector<u32> program(srt.walker_func_size / sizeof(u32));
        Copy(program.data(), reinterpret_cast<u64>(srt.walker_func), srt.walker_func_size);
        using Optimization::SrtOp;
        std::array<u64,64> pointers{};
        std::array<u32,64> values{};
        size_t np = 0, nv = 0;
        u32 acc = 0;
        u64 pointer = reinterpret_cast<u64>(ud.data());
        const auto pop = [&]() { if (!nv) Reject(); return values[--nv]; };
        const auto load = [&]<class T>(u64 offset) {
            T value;
            if (!np) {
                if (offset > ud.size_bytes() || sizeof(T) > ud.size_bytes() - offset) Reject();
                std::memcpy(&value, reinterpret_cast<const u8*>(ud.data()) + offset, sizeof(T));
            } else {
                Copy(&value, Address(pointer, offset), sizeof(T), true);
            }
            return value;
        };
        // Exactly the registered bytecode extent is visited once: no sentinel scan, loops,
        // executable JIT registration, or fallback zero values. Dynamic/nested SRT is supported.
        for (size_t pc = 0; pc < program.size(); pc += 3) {
            const u32 a = program[pc+1], b = program[pc+2];
            switch (static_cast<SrtOp>(program[pc])) {
            case SrtOp::End: return;
            case SrtOp::PushPtrImm:
            case SrtOp::PushPtrAcc: {
                if (np == pointers.size()) Reject();
                const u64 offset = static_cast<SrtOp>(program[pc]) == SrtOp::PushPtrImm
                                       ? u64(a) << 2 : u64(u32(acc << 2));
                const u64 next = load.template operator()<u64>(offset) & 0xFFFFFFFFFFFFULL;
                pointers[np++] = pointer; pointer = next; break;
            }
            case SrtOp::PopPtr:
                if (!np) Reject();
                pointer = pointers[--np]; break;
            case SrtOp::CopyImm:
            case SrtOp::CopyAcc: {
                if (b >= flat.size()) Reject();
                const u64 offset = static_cast<SrtOp>(program[pc]) == SrtOp::CopyImm
                                       ? u64(a) << 2 : u64(u32(acc << 2));
                flat[b] = load.template operator()<u32>(offset); initialized[b] = 1; break;
            }
            case SrtOp::AccFlat: RequireFlat(a); acc = flat[a]; break;
            case SrtOp::AccImm: acc = a; break;
            case SrtOp::Push:
                if (nv == values.size()) Reject();
                values[nv++] = acc; break;
            case SrtOp::Add: acc = pop() + acc; break;
            case SrtOp::Sub: acc = pop() - acc; break;
            case SrtOp::Mul: acc = pop() * acc; break;
            case SrtOp::Shl: acc = pop() << (acc & 31); break;
            case SrtOp::Shr: acc = pop() >> (acc & 31); break;
            case SrtOp::And: acc = pop() & acc; break;
            case SrtOp::Or: acc = pop() | acc; break;
            case SrtOp::Xor: acc = pop() ^ acc; break;
            case SrtOp::UMin: acc = std::min(pop(), acc); break;
            case SrtOp::UMax: acc = std::max(pop(), acc); break;
            case SrtOp::Not: acc = ~acc; break;
            case SrtOp::Bfe: {
                const u32 count = acc, offset = pop(), value = pop();
                acc = (value >> (offset & 31)) & (count >= 32 ? ~0u : (1u << count) - 1);
                break;
            }
            default: Reject();
            }
        }
        Reject(); // Missing End is unavailable; do not walk beyond owned bytecode.
#endif
    }
};
} // namespace Shader
