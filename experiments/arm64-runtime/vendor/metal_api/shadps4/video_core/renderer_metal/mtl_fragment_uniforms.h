// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <cstdint>
#include <regex>
#include <string>
#include <unordered_map>
#include <vector>

namespace Metal {
struct FragmentUniformRewrite { std::string source; unsigned buffers = 0; };

/// Conservative address-space specialization of generated, read-only material parameters.
/// Every access must have a statically bounded index (literal + optional uniform 8-bit offset),
/// fit within 4 KiB, and use the original uint data[1] layout. No copies or new allocations.
/// Sampling, arithmetic, resources, buffer slots and original canonical cache text are unchanged.
inline FragmentUniformRewrite RewriteFragmentUniforms(const std::string& msl) {
    FragmentUniformRewrite out{msl};
    const auto fragment = msl.find("fragment ");
    if (fragment == std::string::npos) return out;
    const auto body = msl.find("\n{", fragment);
    if (body == std::string::npos) return out;
    // Pointer helpers/atomics or writable resources need original device semantics.
    for (const auto* token : {"atomic", "reinterpret_cast", "const_cast", "access::write", "access::read_write", "memory_barrier", "spvPhysicalPointer"})
        if (msl.find(token) != std::string::npos) return out;
    const std::string head=msl.substr(fragment,body-fragment), code=msl.substr(body);
    for(size_t p=0;(p=head.find("device ",p))!=std::string::npos;++p)
        if(p<6 || head.substr(p-6,6)!="const ") return out;
    static const std::regex arg(R"(const device ([_A-Za-z][_A-Za-z0-9]*)& (ssbo_[0-9]+) \[\[buffer\(([0-9]+)\)\]\])");
    static const std::regex off(R"(uint (buf[0-9]+_(?:dword_)?off) = extract_bits\(push_data\.buf_offsets[0-9]+\.[xyzw], [0-9]+u, 8u\)( >> 2u)?;)");
    static const std::regex shifted(R"(uint (buf[0-9]+_dword_off) = (buf[0-9]+_off) >> 2u;)");
    std::unordered_map<std::string, unsigned> offsets;
    for (std::sregex_iterator i(code.begin(),code.end(),off),end;i!=end;++i)
        offsets[(*i)[1]]=(*i)[2].matched?63:255;
    for (std::sregex_iterator i(code.begin(),code.end(),shifted),end;i!=end;++i)
        if(offsets.contains((*i)[2])) offsets[(*i)[1]]=offsets[(*i)[2]]>>2;
    // Uniform offset variables are immutable generated locals, never a varying or reassigned.
    for(auto i=offsets.begin();i!=offsets.end();) {
        const std::regex assign("\\b"+i->first+R"(\s*(?:=(?!=)|\+=|-=|\*=|/=|\+\+|--))");
        if(std::distance(std::sregex_iterator(code.begin(),code.end(),assign),std::sregex_iterator())!=1) i=offsets.erase(i);else ++i;
    }
    struct Edit {size_t at;};std::vector<Edit> edits;
    for(std::sregex_iterator i(head.begin(),head.end(),arg),end;i!=end;++i) {
        const std::string type=(*i)[1], name=(*i)[2];
        const std::regex layout("struct "+type+R"(\s*\{\s*uint data\[1\];\s*\};)");
        if(!std::regex_search(msl,layout)) continue;
        const std::regex use("\\b"+name+R"(\b)");
        const std::regex access("^"+name+R"(\.data\[(?:([0-9]+)u)(?: \+ (buf[0-9]+_(?:dword_)?off))?\])");
        bool valid=true;unsigned accesses=0;
        for(std::sregex_iterator j(code.begin(),code.end(),use);j!=end;++j) {
            // Match the same anchored expression directly against the original suffix.
            // Avoid a full suffix allocation/copy for every read; no match flags change.
            std::match_results<std::string::const_iterator> match;
            if(!std::regex_search(code.cbegin()+j->position(),code.cend(),match,access)) {valid=false;break;}
            std::uint64_t upper=0;
            try {upper=std::stoull(match[1]);}catch(...) {valid=false;break;}
            if(upper>=1024) {valid=false;break;}
            if(match[2].matched) {const auto v=offsets.find(match[2]);if(v==offsets.end()) {valid=false;break;} upper+=v->second;}
            if(upper>=1024) {valid=false;break;}
            ++accesses;
        }
        if(valid && accesses) edits.push_back({fragment+size_t(i->position())});
    }
    for(auto i=edits.rbegin();i!=edits.rend();++i) out.source.replace(i->at,12,"constant");
    out.buffers=unsigned(edits.size());return out;
}
} // namespace Metal
