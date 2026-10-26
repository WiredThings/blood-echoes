// SPDX-FileCopyrightText: Copyright 2025-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/elf_info.h"
#include "common/io_file.h"
#include "common/polyfill_thread.h"
#include "common/thread.h"
#include "core/emulator_settings.h"

#include "video_core/cache_storage.h"

#include <miniz.h>

#include <condition_variable>
#include <functional>
#include <future>
#include <mutex>
#include <queue>

namespace {

std::mutex submit_mutex{};
u32 num_requests{};
std::condition_variable_any request_cv{};
std::queue<std::packaged_task<void()>> req_queue{};
std::mutex m_request{};

mz_zip_archive zip_ar{};
bool ar_is_read_only{true};
#if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
std::recursive_mutex capture_archive_mutex;
std::filesystem::path capture_archive_path;
u64 capture_archive_generation{};
#define BB_CAPTURE_ARCHIVE_GUARD std::lock_guard capture_archive_guard(capture_archive_mutex)
#else
#define BB_CAPTURE_ARCHIVE_GUARD ((void)0)
#endif

} // namespace

namespace Storage {

void ProcessIO(const std::stop_token& stoken) {
    Common::SetCurrentThreadName("shadPS4:PipelineCacheIO");

    while (!stoken.stop_requested()) {
        {
            std::unique_lock lk{submit_mutex};
            Common::CondvarWait(request_cv, lk, stoken, [&] { return num_requests; });
        }

        if (stoken.stop_requested()) {
            break;
        }

        while (num_requests) {
            std::packaged_task<void()> request{};
            {
                std::scoped_lock lock{m_request};
                if (req_queue.empty()) {
                    continue;
                }
                request = std::move(req_queue.front());
                req_queue.pop();
            }

            if (request.valid()) {
                request();
                request.get_future().wait();
            }

            --num_requests;
        }
    }
}

constexpr std::string GetBlobFileExtension(BlobType type) {
    switch (type) {
    case BlobType::ShaderMeta: {
        return "meta";
    }
    case BlobType::ShaderBinary: {
        return "spv";
    }
    case BlobType::PipelineKey: {
        return "key";
    }
    case BlobType::HostPipelineRecipe: {
        return "hostpso";
    }
    case BlobType::ShaderProfile: {
        return "bin";
    }
    default:
        UNREACHABLE();
    }
}

void DataBase::Open() {
    BB_CAPTURE_ARCHIVE_GUARD;
    if (opened) {
        return;
    }

    const auto& game_info = Common::ElfInfo::Instance();

    using namespace Common::FS;
    // bbport: a backend's own cache (Vulkan: none, the plain title id).
    const std::string name = cache_tag.empty() ? std::string{game_info.GameSerial()}
                                               : std::string{game_info.GameSerial()} + "-" + cache_tag;
    if (EmulatorSettings.IsPipelineCacheArchived()) {
        mz_zip_zero_struct(&zip_ar);

        cache_path = GetUserPath(PathType::CacheDir) /
                     std::filesystem::path{name}.replace_extension(".zip");

        if (!mz_zip_reader_init_file(&zip_ar, cache_path.string().c_str(),
                                     MZ_ZIP_FLAG_READ_ALLOW_WRITING) ||
            !mz_zip_validate_archive(&zip_ar, 0)) {
            LOG_INFO(Render, "Cache archive {} is not found or archive is corrupted",
                     cache_path.string().c_str());
            mz_zip_reader_end(&zip_ar);
            mz_zip_writer_init_file(&zip_ar, cache_path.string().c_str(), 0);
        }
    } else {
        cache_path = GetUserPath(PathType::CacheDir) / name;
        if (!std::filesystem::exists(cache_path)) {
            std::filesystem::create_directories(cache_path);
        }
    }

    #if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
    ++capture_archive_generation;capture_archive_path=EmulatorSettings.IsPipelineCacheArchived()?cache_path:std::filesystem::path{};
#endif
    io_worker = std::jthread{ProcessIO};
    opened = true;
}

void DataBase::Close() {
    if (!IsOpened()) {
        return;
    }

    #if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
    {BB_CAPTURE_ARCHIVE_GUARD;++capture_archive_generation;capture_archive_path.clear();}
#endif
    io_worker.request_stop();
    io_worker.join();
    BB_CAPTURE_ARCHIVE_GUARD;

    if (EmulatorSettings.IsPipelineCacheArchived()) {
        mz_zip_writer_finalize_archive(&zip_ar);
        mz_zip_writer_end(&zip_ar);
    }

    opened = false;
    LOG_INFO(Render, "Cache dumped");
}

template <typename T>
bool WriteVector(const BlobType type, std::filesystem::path&& path_, std::vector<T>&& v) {
    {
        auto request = std::packaged_task<void()>{[=]() {
            auto path{path_};
            path.replace_extension(GetBlobFileExtension(type));
            if (EmulatorSettings.IsPipelineCacheArchived()) {
                BB_CAPTURE_ARCHIVE_GUARD;
                ASSERT_MSG(!ar_is_read_only,
                           "The archive is read-only. Did you forget to call `FinishPreload`?");
                if (!mz_zip_writer_add_mem(&zip_ar, path.string().c_str(), v.data(),
                                           v.size() * sizeof(T), MZ_BEST_COMPRESSION)) {
                    LOG_ERROR(Render, "Failed to add {} to the archive", path.string().c_str());
                }
            } else {
                // bbport: written under a temporary name and renamed into place, so a crash or a
                // power loss mid-write leaves the old file or none, never a cut-short one that
                // the next start reads as a damaged entry (issue #28).
                using namespace Common::FS;
                auto temp = path;
                temp += ".tmp";
                bool written = false;
                {
                    const auto file = IOFile{temp, FileAccessMode::Create};
                    written = file.IsOpen() && file.Write(v) == v.size();
                }
                std::error_code ec;
                if (written) {
                    std::filesystem::rename(temp, path, ec);
                }
                if (!written || ec) {
                    LOG_ERROR(Render, "Failed to write {}", path.string());
                    std::filesystem::remove(temp, ec);
                }
            }
        }};
        std::scoped_lock lock{m_request};
        req_queue.emplace(std::move(request));
    }

    std::scoped_lock lk{submit_mutex};
    ++num_requests;
    request_cv.notify_one();
    return true;
}

template <typename T>
void LoadVector(BlobType type, std::filesystem::path& path, std::vector<T>& v) {
    using namespace Common::FS;
    path.replace_extension(GetBlobFileExtension(type));
    if (EmulatorSettings.IsPipelineCacheArchived()) {
        BB_CAPTURE_ARCHIVE_GUARD;
        int index{-1};
        index = mz_zip_reader_locate_file(&zip_ar, path.string().c_str(), nullptr, 0);
        if (index < 0) {
            LOG_WARNING(Render, "File {} is not found in the archive", path.string().c_str());
            return;
        }
        mz_zip_archive_file_stat stat{};
        mz_zip_reader_file_stat(&zip_ar, index, &stat);
        v.resize(stat.m_uncomp_size / sizeof(T));
        mz_zip_reader_extract_to_mem(&zip_ar, index, v.data(), stat.m_uncomp_size, 0);
    } else {
        const auto file = IOFile{path, FileAccessMode::Read};
        v.resize(file.GetSize() / sizeof(T));
        file.Read(v);
    }
}

bool DataBase::Save(BlobType type, const std::string& name, std::vector<u8>&& data) {
    if (!opened) {
        return false;
    }

    auto path = EmulatorSettings.IsPipelineCacheArchived() ? std::filesystem::path{name}
                                                           : cache_path / name;
    return WriteVector(type, std::move(path), std::move(data));
}

bool DataBase::Save(BlobType type, const std::string& name, std::vector<u32>&& data) {
    if (!opened) {
        return false;
    }

    auto path = EmulatorSettings.IsPipelineCacheArchived() ? std::filesystem::path{name}
                                                           : cache_path / name;
    return WriteVector(type, std::move(path), std::move(data));
}

void DataBase::Load(BlobType type, const std::string& name, std::vector<u8>& data) {
    if (!opened) {
        return;
    }

    auto path = EmulatorSettings.IsPipelineCacheArchived() ? std::filesystem::path{name}
                                                           : cache_path / name;
    return LoadVector(type, path, data);
}

void DataBase::Load(BlobType type, const std::string& name, std::vector<u32>& data) {
    if (!opened) {
        return;
    }

    auto path = EmulatorSettings.IsPipelineCacheArchived() ? std::filesystem::path{name}
                                                           : cache_path / name;
    return LoadVector(type, path, data);
}

#if defined(BB_DEV_FETCH_CAPTURE) && BB_DEV_FETCH_CAPTURE
std::function<bool(std::vector<u32>&)> DataBase::BoundedShaderReader(const std::string&name,size_t exact_bytes,bool*permanent_rejection)const {
    if(permanent_rejection)*permanent_rejection=false;
    if(exact_bytes<20||exact_bytes>2*1024*1024||exact_bytes%4||name.empty()||name.size()>64||
       name.find_first_not_of("0123456789abcdefx_")!=std::string::npos){if(permanent_rejection)*permanent_rejection=true;return{};}
    if(!opened)return{};
    const bool archive=EmulatorSettings.IsPipelineCacheArchived();
    const auto path=archive?cache_path:cache_path/(name+".spv");const auto member=name+".spv";
    u64 generation{};if(archive){BB_CAPTURE_ARCHIVE_GUARD;generation=capture_archive_generation;if(capture_archive_path!=path)return{};}
    return [path,member,archive,exact_bytes,generation](std::vector<u32>&out)->bool {
        try {
            out.clear();if(!std::filesystem::is_regular_file(std::filesystem::symlink_status(path)))return false;
            if(!archive){
                using namespace Common::FS;const IOFile file{path,FileAccessMode::Read};
                if(!file.IsOpen()||file.GetSize()!=exact_bytes)return false;
                out.resize(exact_bytes/4);if(file.Read(out)!=out.size()){out.clear();return false;}return true;
            }
            // Reuse its existing central directory. This lock also covers all original
            // archive reads/writes/transitions/close; no second index allocation.
            BB_CAPTURE_ARCHIVE_GUARD;
            if(generation!=capture_archive_generation||capture_archive_path!=path)return false;
            const int index=mz_zip_reader_locate_file(&zip_ar,member.c_str(),nullptr,0);mz_zip_archive_file_stat stat{};
            if(index<0||!mz_zip_reader_file_stat(&zip_ar,index,&stat)||stat.m_uncomp_size!=exact_bytes)return false;
            out.resize(exact_bytes/4);const bool ok=mz_zip_reader_extract_to_mem(&zip_ar,index,out.data(),exact_bytes,0);if(!ok)out.clear();return ok;
        }catch(...){out.clear();return false;}
    };
}
#endif

void DataBase::ForEachBlob(BlobType type, const std::function<void(std::vector<u8>&& data)>& func) {
    const auto& ext = GetBlobFileExtension(type);
    if (EmulatorSettings.IsPipelineCacheArchived()) {
        BB_CAPTURE_ARCHIVE_GUARD;
        const auto num_files = mz_zip_reader_get_num_files(&zip_ar);
        for (int index = 0; index < num_files; ++index) {
            std::array<char, MZ_ZIP_MAX_ARCHIVE_FILENAME_SIZE> file_name{};
            file_name.fill(0);
            mz_zip_reader_get_filename(&zip_ar, index, file_name.data(), file_name.size());
            if (std::string{file_name.data()}.ends_with(ext)) {
                mz_zip_archive_file_stat stat{};
                mz_zip_reader_file_stat(&zip_ar, index, &stat);
                std::vector<u8> data(stat.m_uncomp_size);
                mz_zip_reader_extract_to_mem(&zip_ar, index, data.data(), data.size(), 0);
                func(std::move(data));
            }
        }
    } else {
        for (const auto& file_name : std::filesystem::directory_iterator{cache_path}) {
            if (file_name.path().extension().string().ends_with(ext)) {
                using namespace Common::FS;
                const auto& file = IOFile{file_name, FileAccessMode::Read};
                if (file.IsOpen()) {
                    std::vector<u8> data(file.GetSize());
                    file.Read(data);
                    func(std::move(data));
                }
            }
        }
    }
}

BoundedReadResult DataBase::ForEachBlobBounded(BlobType type,std::string_view prefix,size_t max_count,
    size_t max_bytes,const std::function<bool(std::string_view,std::vector<u8>&&)>& func) noexcept {
    BoundedReadResult result{};
    try {
      if(!opened||!max_count||!max_bytes||prefix.empty()||!func){result.stop=BoundedStop::Unavailable;return result;}
      const std::string extension="."+GetBlobFileExtension(type);
      const auto matches=[&](const std::string&name){return name.starts_with(prefix)&&name.ends_with(extension)&&name.find('/')==std::string::npos&&name.find('\\')==std::string::npos;};
      const auto admit=[&](std::string_view name,std::vector<u8>&& data){++result.delivered;if(!func(name,std::move(data))){result.stop=BoundedStop::Cancelled;return false;}return true;};
      if(EmulatorSettings.IsPipelineCacheArchived()) {
        BB_CAPTURE_ARCHIVE_GUARD;
        const auto count=mz_zip_reader_get_num_files(&zip_ar);
        for(mz_uint index=0;index<count;++index) {
          if(result.scanned++>=65536){result.stop=BoundedStop::ScanLimit;break;}
          std::array<char,MZ_ZIP_MAX_ARCHIVE_FILENAME_SIZE> name{};
          const auto length=mz_zip_reader_get_filename(&zip_ar,index,name.data(),name.size());
          if(!length||length>name.size()||name[length-1]!=0){++result.rejected;continue;}
          const std::string text{name.data()};if(!matches(text))continue;
          if(result.delivered>=max_count){result.stop=BoundedStop::ItemLimit;break;}
          mz_zip_archive_file_stat info{};
          if(!mz_zip_reader_file_stat(&zip_ar,index,&info)||!info.m_uncomp_size||info.m_uncomp_size>max_bytes){++result.rejected;continue;}
          std::vector<u8> data(static_cast<size_t>(info.m_uncomp_size));
          if(!mz_zip_reader_extract_to_mem(&zip_ar,index,data.data(),data.size(),0)){++result.rejected;continue;}
          if(!admit(text,std::move(data)))break;
        }
      } else {
        std::error_code ec;std::filesystem::directory_iterator it(cache_path,ec),end;
        if(ec){result.stop=BoundedStop::Io;return result;}
        while(it!=end) {
          if(result.scanned++>=65536){result.stop=BoundedStop::ScanLimit;break;}
          const auto&entry=*it;const std::string name=entry.path().filename().string();
          if(matches(name)) {
            if(result.delivered>=max_count){result.stop=BoundedStop::ItemLimit;break;}
            const auto status=entry.symlink_status(ec);const auto bytes=ec?0:entry.file_size(ec);
            if(ec||!std::filesystem::is_regular_file(status)||!bytes||bytes>max_bytes){++result.rejected;ec.clear();}
            else {
              using namespace Common::FS;const IOFile file{entry.path(),FileAccessMode::Read};
              if(!file.IsOpen()||file.GetSize()!=bytes)++result.rejected;
              else {std::vector<u8> data(static_cast<size_t>(bytes));if(file.Read(data)!=data.size())++result.rejected;else if(!admit(name,std::move(data)))break;}
            }
          }
          it.increment(ec);if(ec){result.stop=BoundedStop::Io;break;}
        }
      }
    }catch(...){result.stop=BoundedStop::Io;}
    return result;
}

void DataBase::Clear() {
    if (!opened || EmulatorSettings.IsPipelineCacheArchived()) {
        return;
    }
    std::error_code ec;
    u64 removed = 0;
    for (const auto& entry : std::filesystem::directory_iterator(cache_path, ec)) {
        if (entry.is_regular_file(ec) && std::filesystem::remove(entry.path(), ec)) {
            ++removed;
        }
    }
    LOG_WARNING(Render, "Pipeline cache cleared ({} files): it is rebuilt for this build", removed);
}

void DataBase::FinishPreload() {
    if (EmulatorSettings.IsPipelineCacheArchived()) {
        BB_CAPTURE_ARCHIVE_GUARD;
        mz_zip_writer_init_from_reader(&zip_ar, cache_path.string().c_str());
        ar_is_read_only = false;
    }
}

} // namespace Storage
