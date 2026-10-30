// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
#include <stdexcept>

namespace BbFetchStore {
// Compatible extension of the LAST byte of the 74-byte canonical Profile.
// Old 0/1 bytes retain their exact meaning. Old readers reject 2/3 (not bool),
// and new readers reject every unknown bit. Raw Shader::Profile is still 80 bytes.
inline constexpr uint8_t ProfileTwoLevelBdaBit = 1;
inline constexpr uint8_t ProfileInvariantVertexBit = 2;
inline constexpr uint8_t ProfileBdaFeatureMask = ProfileTwoLevelBdaBit | ProfileInvariantVertexBit;
template <class Profile>
inline uint8_t EncodeProfileBdaFeatures(const Profile& profile) {
    return (profile.two_level_bda ? ProfileTwoLevelBdaBit : 0) |
           (profile.relaxed_vertex_float ? ProfileInvariantVertexBit : 0);
}
template <class Profile>
inline void DecodeProfileBdaFeatures(uint8_t flags, Profile& profile) {
    if (flags & ~ProfileBdaFeatureMask) throw std::runtime_error("unknown canonical Profile feature bit");
    profile.two_level_bda = (flags & ProfileTwoLevelBdaBit) != 0;
    profile.relaxed_vertex_float = (flags & ProfileInvariantVertexBit) != 0;
}
} // namespace BbFetchStore
