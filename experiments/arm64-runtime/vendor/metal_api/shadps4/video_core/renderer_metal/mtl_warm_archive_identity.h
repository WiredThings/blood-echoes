// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <CommonCrypto/CommonDigest.h>
#include <array>
#include <string>
#include <string_view>

namespace Metal {
// The caller supplies SHA-256 of its verified translator/build + effective options +
// game image/patch manifest. No path, program hash or count is a compatibility identity.
inline bool ValidWarmArchiveManifest(std::string_view manifest) {
    if (manifest.size() != 64) return false;
    for (char c : manifest) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
// Source-created and offline-metallib functions can have different driver archive
// identities even for the same effective MSL. Retain both ledgers instead of
// replacing the other input route's archive after every switch. Unknown/invalid
// load identities still fall back to the original source route in LocalAot.
inline std::string WarmArchiveLibraryOptions(std::string_view options,
                                            std::string_view mode,
                                            std::string_view local_build) {
    std::string result{options};
    if (mode == "load" && ValidWarmArchiveManifest(local_build)) {
        result += ";local-metallib-build=";
        result += local_build;
    }
    return result;
}
// Explicit arguments also allow authored GPU/OS mismatch controls without lying about
// the actual Metal device. Production always passes the real registry ID/name and OS build.
inline std::string WarmArchiveNamespace(std::string_view manifest, std::string_view gpu,
                                        std::string_view os, std::string_view options) {
    if (!ValidWarmArchiveManifest(manifest) || gpu.empty() || os.empty()) return {};
    std::string canonical;
    for (auto field : std::array<std::string_view, 5>{"bb-metal-startup-archive-v3", manifest, gpu, os, options}) {
        canonical += std::to_string(field.size());
        canonical += ':';
        canonical += field;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(canonical.data(), static_cast<CC_LONG>(canonical.size()), digest);
    constexpr char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char byte : digest) { out += hex[byte >> 4]; out += hex[byte & 15]; }
    return out;
}
} // namespace Metal
