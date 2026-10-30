// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "shader_recompiler/profile.h"

namespace Shader {
// One predicate for contraction and the matching Invariant decoration. Export,
// local, tessellation and geometry stages must never enter this raster-only path.
template <class SoftwareStage, class HardwareStage>
constexpr bool UsesInvariantVertexFloat(const Profile& profile, SoftwareStage sw,
                                       HardwareStage hw) {
    return profile.relaxed_fragment_compute_float && profile.relaxed_vertex_float &&
           sw == SoftwareStage::Vertex && hw == HardwareStage::Vertex;
}
} // namespace Shader
