// SPDX-License-Identifier: GPL-2.0-or-later
#include "fetch_compile_canonical.h"
#include "shader_recompiler/vertex_float_rules.h"
#include <cassert>
#include <cstddef>
#include <cstdio>

static_assert(sizeof(Shader::Profile)==80);
static_assert(offsetof(Shader::Profile,relaxed_fragment_compute_float)==72);
static_assert(offsetof(Shader::Profile,two_level_bda)==73);
static_assert(offsetof(Shader::Profile,relaxed_vertex_float)==74);
static_assert(BbFetchStore::ProfileBytes==74);

int main() {
    Shader::Profile p{};
    p.max_viewport_width=16384; p.max_viewport_height=16384;
    p.buffer_copy_shader_hash=0x1234567890abcdef;
    const auto original=BbFetchStore::CanonicalProfile(p);
    const auto baseline=p;
#define MODULE_PROFILE_BOOL_FIELD(field) {auto changed=baseline;changed.field=true;auto encoded=BbFetchStore::CanonicalProfile(changed);assert(encoded!=original);assert(BbFetchCapture::DecodeProfile(encoded)==changed);}
#include "module_profile_bool_fields.inc"
#undef MODULE_PROFILE_BOOL_FIELD
    for(unsigned bda=0;bda<2;++bda)for(unsigned invariant=0;invariant<2;++invariant) {
        p.two_level_bda=bda; p.relaxed_vertex_float=invariant;
        auto bytes=BbFetchStore::CanonicalProfile(p);
        assert(bytes.back()==(bda|(invariant<<1)));
        auto decoded=BbFetchCapture::DecodeProfile(bytes);
        assert(decoded==p);
        assert(BbFetchStore::CanonicalProfile(decoded)==bytes);
        if(!invariant){auto old=original;old.back()=bda;assert(bytes==old);}
        for(unsigned fragment=0;fragment<2;++fragment) {
            p.relaxed_fragment_compute_float=fragment;
            for(unsigned sw=0;sw<Shader::MaxStageTypes;++sw)for(unsigned hw=0;hw<7;++hw)
                assert(Shader::UsesInvariantVertexFloat(p,Shader::SwStage(sw),Shader::HwStage(hw))==
                    bool(fragment&&invariant&&sw==unsigned(Shader::SwStage::Vertex)&&hw==unsigned(Shader::HwStage::Vertex)));
        }
        p.relaxed_fragment_compute_float=false;
    }
    for(unsigned unknown=4;unknown<256;++unknown) {
        auto corrupt=original;corrupt.back()=unknown;
        bool rejected=false;try{(void)BbFetchCapture::DecodeProfile(corrupt);}catch(const std::runtime_error&){rejected=true;}
        assert(rejected);
    }
    // Every ordinary canonical bool byte still rejects a non-boolean value.
    for(unsigned i=32;i+1<original.size();++i) {
        auto corrupt=original;corrupt[i]=2;
        bool rejected=false;try{(void)BbFetchCapture::DecodeProfile(corrupt);}catch(const std::runtime_error&){rejected=true;}
        assert(rejected);
    }
    puts("PASS actual Profile ABI, canonical round-trip, legacy bytes, 252 bad feature bits, strict ordinary bools and all real stages");
}
