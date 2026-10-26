// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// Data-role checks after native code admission, before UserPaths creates files.
// This is not an application identity check and supplies no HOME/cwd fallback.
#include <filesystem>
#include <functional>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace BbGpuState {
namespace fs = std::filesystem;
using Environment = std::function<std::string(const char*)>;
inline fs::path Directory(const std::string& value, bool private_mode=false) {
    fs::path path(value);
    if(value.empty() || !path.is_absolute() || path!=path.lexically_normal() || path!=fs::canonical(path))
        throw std::runtime_error("GPU state requires a canonical absolute directory");
    struct stat info{};
    if(lstat(path.c_str(),&info) || !S_ISDIR(info.st_mode) || info.st_uid!=getuid() ||
       (info.st_mode&0022) || (private_mode && (info.st_mode&0077)))
        throw std::runtime_error("GPU state directory ownership or mode");
    return path;
}
inline bool Within(const fs::path& child,const fs::path& root) {
    auto rel=child.lexically_relative(root);
    return !rel.empty() && rel!="." && *rel.begin()!="..";
}
inline fs::path Formal(const Environment& env,bool worker) {
    const auto selected=Directory(env("BB_GPU_USER_DIR"));
    const auto base_value=env("BB_ARM_GPU_BASE_DIR");
    if(!base_value.empty()) {
        const auto base=Directory(base_value);
        if(selected==base/"arm64-native-v1")return selected;
    }
    // The scalar helper CLI still loads this DSO before main. Give it only a
    // fresh disposable directory, never the caller's real GPU cache or HOME.
    if(worker && env("HOME")==selected.string() && env("TMPDIR")==selected.string()) {
        const auto data=Directory(env("BB_DATA_DIR"));
        const auto work=Directory((data/"arm64-runtime-work").string(),true);
        const auto capabilities=Directory((work/"capabilities").string(),true);
        if(selected.parent_path()==capabilities) {
            Directory(selected.string(),true);
            return selected;
        }
    }
    throw std::runtime_error("GPU state is outside the native namespace or helper scratch");
}
}
