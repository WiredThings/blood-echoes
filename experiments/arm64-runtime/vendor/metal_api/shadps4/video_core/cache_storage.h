// SPDX-FileCopyrightText: Copyright 2025 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/path_util.h"
#include "common/singleton.h"
#include "common/types.h"

#include <functional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

namespace Storage {

enum class BlobType : u32 {
    ShaderMeta,
    ShaderBinary,
    PipelineKey,
    ShaderProfile,
    HostPipelineRecipe, // independent bounded helper metadata; old enum values unchanged
};

enum class BoundedStop {End,Unavailable,Cancelled,ItemLimit,ScanLimit,Io};
struct BoundedReadResult {size_t delivered{},scanned{},rejected{};BoundedStop stop=BoundedStop::End;bool complete()const{return stop==BoundedStop::End;}};
class DataBase {
public:
    static DataBase& Instance() {
        return *Common::Singleton<DataBase>::Instance();
    }

    /// bbport: names this backend's cache (<title id>-<tag>[.zip] instead of <title id>[.zip]):
    /// backends serialize their own pipeline data. Takes effect at the next Open().
    void SetCacheTag(std::string_view tag) {
        cache_tag = tag;
    }

    void Open();
    void Close();
    [[nodiscard]] bool IsOpened() const {
        return opened;
    }
    void FinishPreload();
    /// bbport: removes every cached blob (an incompatible cache is rebuilt, not ignored).
    void Clear();

    bool Save(BlobType type, const std::string& name, std::vector<u8>&& data);
    bool Save(BlobType type, const std::string& name, std::vector<u32>&& data);

    void Load(BlobType type, const std::string& name, std::vector<u8>& data);
    void Load(BlobType type, const std::string& name, std::vector<u32>& data);

#if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
    // Captures only path/name/size now. Plain files open an independent handle; ZIP uses the existing
    // central directory under lifecycle/generation lock. Exact SHA checked by caller.
    std::function<bool(std::vector<u32>&)> BoundedShaderReader(const std::string&name,size_t exact_bytes,bool*permanent_rejection=nullptr)const;
#endif
    void ForEachBlob(BlobType type, const std::function<void(std::vector<u8>&& data)>& func);
    // Preparation only: bound matching files AND allocation size before reading.
    // Returning false stops admission without touching remaining existing blobs.
    BoundedReadResult ForEachBlobBounded(BlobType type, std::string_view prefix, size_t max_count,
        size_t max_bytes, const std::function<bool(std::string_view,std::vector<u8>&&)>& func) noexcept;

private:
    std::jthread io_worker{};
    std::filesystem::path cache_path{};
    std::string cache_tag{};
    bool opened{};
};

} // namespace Storage
