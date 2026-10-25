// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <span>
#include <string>
#include <string_view>

namespace Metal::GamePso {
inline bool Digest(std::string_view value) {
    return value.size() == 64 && std::all_of(value.begin(), value.end(), [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
    });
}

// Descriptor and function digests already describe actual successful compilation
// inputs. Never include guest program/permutation slots or process addresses.
inline std::string CanonicalRecipe(bool compute, std::string_view descriptor,
                                   std::span<const std::string_view> functions) {
    if (!Digest(descriptor) || functions.size() != (compute ? 1u : 2u) ||
        !Digest(functions[0])) return {};
    if (!compute && !functions[1].empty() && !Digest(functions[1])) return {};
    std::string result = "bb-metal-game-pso-v1\n";
    result += compute ? "compute\n" : "render\n";
    result += descriptor;
    result += '\n';
    for (auto function : functions) {
        result += function.empty() ? "none" : function;
        result += '\n';
    }
    return result;
}
} // namespace Metal::GamePso
