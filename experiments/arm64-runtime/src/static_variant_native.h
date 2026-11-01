// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>
namespace Shader { struct Info; struct StageSpecialization; struct Profile; }
namespace VideoCore {
// Same encoder as normal runtime registration; no Storage/global cache access.
std::vector<uint8_t> EncodeShaderMeta(const Shader::Info&,const Shader::StageSpecialization&,
                                    uint64_t permutation_hash,size_t permutation_index);
std::vector<uint8_t> EncodeShaderProfile(const Shader::Profile&);
}
