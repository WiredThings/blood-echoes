// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <vector>
#include "shader_recompiler/frontend/fetch_shader.h"
#include "video_core/amdgpu/pixel_format.h"

namespace Shader {
// Explicit compilation semantics. These are not, and cannot be read as, V# words.
struct FetchAttributeProjection {
    Gcn::VertexAttribute attribute{};
    AmdGpu::NumberClass number_class{};
    AmdGpu::NumberConversion read_number_conversion{};
    AmdGpu::CompMapping effective_dst_select{};
    u32 divisor{};
    bool nonnull{};
};
struct FetchCompileProjection {
    u32 code_size{};
    s8 vertex_offset_sgpr{-1}, instance_offset_sgpr{-1};
    std::vector<FetchAttributeProjection> attributes;
};
inline bool SameFetchFields(const Gcn::VertexAttribute& a,const Gcn::VertexAttribute& b) {
    return a.semantic==b.semantic&&a.dest_vgpr==b.dest_vgpr&&a.num_elements==b.num_elements&&
           a.sgpr_base==b.sgpr_base&&a.dword_offset==b.dword_offset&&
           a.instance_data==b.instance_data&&a.inst_offset==b.inst_offset&&
           a.data_format==b.data_format&&a.num_format==b.num_format;
}
// Null only when this compilation has no projection. A missing active entry throws;
// it never requests a raw descriptor, guest address or synchronous fallback.
const FetchAttributeProjection* QueryFetchCompileProjection(
    const Info& info,const Gcn::VertexAttribute& attribute);
bool HasFetchCompileProjection(const Info& info);
} // namespace Shader
