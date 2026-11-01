// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <charconv>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>

namespace BbStaticVariant {
inline std::string Marker(uint64_t hash) {
    char text[19] = {'0', 'x'};
    const auto end = std::to_chars(text+2, text+sizeof(text), hash, 16).ptr;
    return {text, end};
}
inline std::string_view StringOperand(std::span<const uint32_t> instruction) {
    if (instruction.size()<3 || (instruction[0]&65535)!=7) return {};
    const auto bytes=std::span(reinterpret_cast<const char*>(instruction.data()+2),
                               (instruction.size()-2)*sizeof(uint32_t));
    const auto zero=std::find(bytes.begin(),bytes.end(),'\0');
    if(zero==bytes.end() || !std::all_of(zero,bytes.end(),[](char c){return c==0;})) return {};
    return {bytes.data(),size_t(zero-bytes.begin())};
}
// The ordinary emitter writes String(format("{:#x}", info.pgm_hash)).
// Nothing is rewritten or executed from the reference binary. All words other
// than exactly that one debug string must match, including every result ID.
inline bool ProgramMarkerEquivalent(std::span<const uint32_t> expected,
                                    std::span<const uint32_t> actual,
                                    uint64_t source,uint64_t target) {
    if(source==target || expected.size()<5 || actual.size()<5 ||
       expected.size()>128*1024 || actual.size()>128*1024 || expected[0]!=0x07230203 ||
       !std::equal(expected.begin(),expected.begin()+5,actual.begin())) return false;
    const auto old=Marker(source), wanted=Marker(target);
    size_t i=5,j=5,changed=0;
    while(i<expected.size()&&j<actual.size()) {
        const auto a=expected[i]>>16,b=actual[j]>>16;
        if(!a||!b||a>expected.size()-i||b>actual.size()-j)return false;
        const auto x=expected.subspan(i,a),y=actual.subspan(j,b);
        if(StringOperand(x)==old&&StringOperand(y)==wanted) {
            if(x[1]!=y[1])return false;
            ++changed;
        } else if(x.size()!=y.size()||!std::equal(x.begin(),x.end(),y.begin())) return false;
        i+=a;j+=b;
    }
    return i==expected.size()&&j==actual.size()&&changed==1;
}
} // namespace BbStaticVariant
