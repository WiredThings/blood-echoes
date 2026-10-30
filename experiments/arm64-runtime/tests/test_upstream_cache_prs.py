"""Small native controls for shadPS4 #5570/#5577; no game or production data.

The LRU controls include the real header. The IO controls compile the actual
counter/queue declarations, ProcessIO and WriteVector bodies extracted verbatim
from cache_storage.cpp; only settings, logging and file/zip service dependencies
are supplied by this harness. The non-archive path writes authored temporary data.
This is not a full renderer/archive/lifecycle integration test.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ROOT / "vendor/metal_api/shadps4"
SOURCE = INCLUDE / "video_core/cache_storage.cpp"

LRU = r'''
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>
#include "common/lru_cache.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "failed line %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char** argv) {
    CHECK(argc == 2);
    std::string mode = argv[1];
    Common::LeastRecentlyUsedCache<int, unsigned long long> cache;
    if (mode == "bool-stop") {
        for (int i = 0; i < 1000; ++i) cache.Insert(i, 0);
        int calls = 0;
        cache.ForEachItemBelow(1, [&](int) { return ++calls == 3; });
        CHECK(calls == 3);
    } else if (mode == "ref-stop") {
        cache.Insert(1, 0); cache.Insert(2, 0);
        int calls = 0;
        cache.ForEachItemBelow(1, [&](int& value) -> bool { ++value; ++calls; return true; });
        CHECK(calls == 1);
        std::vector<int> values;
        cache.ForEachItemBelow(1, [&](int value) { values.push_back(value); });
        CHECK((values == std::vector<int>{2,2}));
    } else if (mode == "void-and-tick") {
        cache.Insert(1, 1); cache.Insert(2, 2); cache.Insert(3, 3);
        std::vector<int> values;
        cache.ForEachItemBelow(2, [&](int value) { values.push_back(value); });
        CHECK((values == std::vector<int>{1,2}));
    } else if (mode == "skip-touch-free") {
        const auto a = cache.Insert(1, 0);
        const auto b = cache.Insert(2, 0);
        const auto c = cache.Insert(3, 0);
        (void)c;
        int calls = 0;
        cache.ForEachItemBelow(1, [&](int value) {
            ++calls;
            if (value == 1) { cache.Touch(a, 5); return false; }
            if (value == 2) { cache.Free(b); return false; }
            return true;
        });
        CHECK(calls == 3);
        std::vector<int> values;
        cache.ForEachItemBelow(10, [&](int value) { values.push_back(value); });
        CHECK((values == std::vector<int>{3,1}));
    } else if (mode == "empty") {
        int calls = 0;
        cache.ForEachItemBelow(1, [&](int) { ++calls; return true; });
        CHECK(calls == 0);
    } else return 2;
    return 0;
}
'''

IO_PREFIX = r'''
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <functional>
#include <future>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>
#include "common/types.h"
#include "common/polyfill_thread.h"
namespace Common { inline void SetCurrentThreadName(const char*) {} }
std::atomic<int> errors{};
#define LOG_ERROR(...) (++errors)
#define ASSERT_MSG(x, ...) assert(x)
#define BB_CAPTURE_ARCHIVE_GUARD ((void)0)
std::function<void()> sink_hook;
namespace Common::FS {
enum class FileAccessMode { Create };
struct IOFile {
    mutable std::ofstream out;
    IOFile(const std::filesystem::path& path, FileAccessMode) {
        if (sink_hook) sink_hook();
        out.open(path, std::ios::binary);
    }
    bool IsOpen() const { return out.is_open(); }
    template<class T> size_t Write(const std::vector<T>& v) const {
        out.write(reinterpret_cast<const char*>(v.data()), v.size()*sizeof(T));
        return out.good() ? v.size() : 0;
    }
};
}
struct Settings { bool IsPipelineCacheArchived() const { return false; } } EmulatorSettings;
struct Zip {} zip_ar;
bool ar_is_read_only = false;
constexpr int MZ_BEST_COMPRESSION = 9;
inline bool mz_zip_writer_add_mem(Zip*, const char*, const void*, size_t, int) { std::abort(); }
namespace {
'''

IO_SUFFIX = r'''
static_assert(std::is_same_v<decltype(num_requests), std::atomic<u32>>,
              "request count must have atomic read/modify/write semantics");
int main(int argc, char** argv) {
    assert(argc == 3);
    const std::string mode = argv[1];
    const std::filesystem::path directory = argv[2];
    std::filesystem::create_directory(directory);
    if (mode == "idle-stop") {
        std::jthread worker(Storage::ProcessIO);
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        worker.request_stop(); worker.join();
        assert(num_requests.load() == 0);
        assert(req_queue.empty());
    } else if (mode == "concurrent") {
        std::jthread worker(Storage::ProcessIO);
        std::vector<std::thread> writers;
        for (int p = 0; p < 8; ++p) writers.emplace_back([&,p] {
            for (int i = 0; i < 64; ++i) {
                auto path = directory / (std::to_string(p) + "-" + std::to_string(i));
                Storage::WriteVector(Storage::BlobType::PipelineKey, std::move(path),
                                     std::vector<u32>{u32(p),u32(i),0xabcdef01});
            }
        });
        for (auto& writer : writers) writer.join();
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (num_requests.load() && std::chrono::steady_clock::now() < deadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        assert(num_requests.load() == 0);
        worker.request_stop(); worker.join();
        assert(req_queue.empty());
        for (int p = 0; p < 8; ++p) for (int i = 0; i < 64; ++i) {
            auto path = directory / (std::to_string(p) + "-" + std::to_string(i) + ".key");
            std::ifstream in(path, std::ios::binary);
            u32 got[3]{}; in.read(reinterpret_cast<char*>(got), sizeof(got));
            assert(in.gcount() == sizeof(got));
            assert(got[0] == u32(p) && got[1] == u32(i) && got[2] == 0xabcdef01);
        }
        assert(std::distance(std::filesystem::directory_iterator(directory),
                             std::filesystem::directory_iterator{}) == 512);
    } else if (mode == "active-stop-drains") {
        std::promise<void> entered, release;
        auto released = release.get_future().share();
        bool first = true;
        sink_hook = [&] { if (first) { first = false; entered.set_value(); released.wait(); } };
        std::jthread worker(Storage::ProcessIO);
        Storage::WriteVector(Storage::BlobType::PipelineKey, directory / "hold", std::vector<u8>{1});
        assert(entered.get_future().wait_for(std::chrono::seconds(2)) == std::future_status::ready);
        for (int i = 0; i < 32; ++i)
            Storage::WriteVector(Storage::BlobType::PipelineKey, directory / std::to_string(i),
                                 std::vector<u8>{u8(i)});
        worker.request_stop(); release.set_value(); worker.join();
        assert(num_requests.load() == 0);
        assert(req_queue.empty());
        assert(std::distance(std::filesystem::directory_iterator(directory),
                             std::filesystem::directory_iterator{}) == 33);
    } else if (mode == "already-stopped-preserves") {
        Storage::WriteVector(Storage::BlobType::PipelineKey, directory / "pending", std::vector<u8>{7});
        std::stop_source stop; stop.request_stop();
        Storage::ProcessIO(stop.get_token());
        // Existing ProcessIO contract: work not begun before a stop remains queued.
        // Do not silently reset the counter or discard tasks to make this test pass.
        assert(num_requests.load() == 1);
        assert(req_queue.size() == 1);
        assert(std::filesystem::is_empty(directory));
    } else return 2;
    assert(errors.load() == 0);
    return 0;
}
'''


def io_harness(source):
    declarations = source.split("std::mutex submit_mutex{};", 1)[1].split("mz_zip_archive", 1)[0]
    declarations = "std::mutex submit_mutex{};" + declarations
    process = source.split("void ProcessIO(", 1)[1].split("\nconstexpr std::string GetBlobFileExtension", 1)[0]
    process = "void ProcessIO(" + process
    write = source.split("template <typename T>\nbool WriteVector", 1)[1].split("template <typename T>\nvoid LoadVector", 1)[0]
    write = "template <typename T>\nbool WriteVector" + write
    return (IO_PREFIX + declarations + "}\nnamespace Storage {\nenum class BlobType { PipelineKey };\n"
            + 'inline std::string GetBlobFileExtension(BlobType) { return "key"; }\n'
            + process + write + "}\n" + IO_SUFFIX)


class NativeUpstreamCacheControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="be-upstream-cache-", dir="/private/tmp")
        cls.directory = Path(cls.tmp.name)
        cls.compiler = shutil.which("clang++")
        if not cls.compiler:
            raise RuntimeError("clang++ is required for native upstream cache controls")
        cls.lru = cls.build("lru", LRU)
        cls.io_source = io_harness(SOURCE.read_text())
        cls.io = cls.build("io", cls.io_source)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def build(cls, name, code, extra_include=None):
        source = cls.directory / (name + ".cpp")
        source.write_text(code)
        output = cls.directory / name
        command = [cls.compiler, "-std=c++23", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread"]
        if extra_include:
            command += ["-I", str(extra_include)]
        command += ["-I", str(INCLUDE), str(source), "-o", str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return output

    def run_lru(self, mode):
        subprocess.run([self.lru, mode], check=True, capture_output=True, text=True, timeout=5)

    def run_io(self, mode):
        destination = self.directory / (mode + "-" + str(os.getpid()))
        subprocess.run([self.io, mode, destination], check=True, capture_output=True, text=True, timeout=10)

    def test_bool_callback_stops_at_budget(self): self.run_lru("bool-stop")
    def test_reference_callback_uses_actual_argument_type(self): self.run_lru("ref-stop")
    def test_void_callback_and_inclusive_tick_boundary(self): self.run_lru("void-and-tick")
    def test_skipping_touching_and_freeing_during_traversal(self): self.run_lru("skip-touch-free")
    def test_empty_lru(self): self.run_lru("empty")
    def test_concurrent_real_enqueue_worker_and_authored_file_payloads(self): self.run_io("concurrent")
    def test_idle_worker_stop_wakes_waiter(self): self.run_io("idle-stop")
    def test_stop_during_active_drain_preserves_all_queued_writes(self): self.run_io("active-stop-drains")
    def test_preexisting_stop_does_not_hide_pending_requests(self): self.run_io("already-stopped-preserves")

    def test_old_lru_trait_is_detected_by_executable_counterexample(self):
        header = (INCLUDE / "common/lru_cache.h").read_text()
        old = header.replace("std::invoke_result_t<Func, ObjectType&>", "std::invoke_result<Func, ObjectType>")
        self.assertNotEqual(old, header)
        root = self.directory / "old-lru"
        (root / "common").mkdir(parents=True)
        (root / "common/lru_cache.h").write_text(old)
        executable = self.build("old-lru-trait", LRU, extra_include=root)
        result = subprocess.run([executable, "bool-stop"], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("calls == 3", result.stderr)

    def test_plain_counter_is_rejected_by_native_atomic_contract(self):
        old = self.io_source.replace("std::atomic<u32> num_requests{};", "u32 num_requests{};")
        self.assertNotEqual(old, self.io_source)
        with self.assertRaises(AssertionError):
            self.build("old-counter", old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
