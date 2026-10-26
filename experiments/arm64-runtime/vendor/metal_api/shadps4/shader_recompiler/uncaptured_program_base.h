// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <stdexcept>
#include "shader_recompiler/resource.h"
#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/resource_pass.h"
namespace Shader {
// Preparation has no guest program address. Test the discovered dependency,
// before flattening or resource patching can use an unavailable scalar value.
inline void RejectUncapturedProgramBase(const Optimization::ResourceDiscoveryList& resources) {
    for (const auto& resource : resources) {
        for (const auto& sharp : resource.sharps) {
            if (sharp.post_op == SharpFetchPostOp::OffsetByProgramBase)
                throw std::runtime_error{"offline module requires an uncaptured program base"};
        }
    }
}
}
