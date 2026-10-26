// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_shader: compiles every .spv of a shader cache directory
// (SPIR-V from the recompiler) to MSL and a MTLLibrary and prints a summary. Needs a Metal device,
// no window or game.
//
//   mtl-shader-test [<dir with .spv files>] [--cache <MSL cache dir>]
//
// The directory defaults to the clinic run's cache (~/Games/Bloodborne/bench/testuser/cache/
// CUSA03173, read only). The MSL disk cache goes to --cache (default: a folder in $TMPDIR; never
// the game's user directory): run twice, the second run must hit it. Exit 1 when a vertex,
// fragment or compute shader fails (PerVertexKHR inputs included since P3.1b), or when the MSL
// read back from the cache disagrees with what the compiler produced.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_shader.h"

// common/assert.cpp pulls in the emulator; a failed ASSERT here just aborts.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
using Metal::CompileStatus;
using Metal::ShaderStage;

static double MsSince(Clock::time_point t) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

struct Timing {
    std::vector<double> ms;
    void Add(double v) {
        ms.push_back(v);
    }
    void Print(const char* name) const {
        if (ms.empty()) {
            std::printf("  %-22s (none)\n", name);
            return;
        }
        std::vector<double> sorted = ms;
        std::sort(sorted.begin(), sorted.end());
        double sum = 0;
        for (double v : sorted) {
            sum += v;
        }
        std::printf("  %-22s n=%-4zu mean %7.2f  p95 %7.2f  max %7.2f  total %8.1f ms\n", name,
                    sorted.size(), sum / sorted.size(),
                    sorted[std::min(sorted.size() - 1, size_t(0.95 * sorted.size()))],
                    sorted.back(), sum);
    }
    double Total() const {
        double sum = 0;
        for (double v : ms) {
            sum += v;
        }
        return sum;
    }
};

static bool SameBindings(const Metal::CompiledShader& a, const Metal::CompiledShader& b) {
    if (a.entry != b.entry || a.stage != b.stage || a.uses_push_constants != b.uses_push_constants ||
        a.buffer_size_buffer != b.buffer_size_buffer ||
        a.uses_device_address != b.uses_device_address ||
        a.uses_bda_pagetable != b.uses_bda_pagetable || a.num_buffers != b.num_buffers ||
        a.num_textures != b.num_textures || a.num_samplers != b.num_samplers ||
        a.bindings.size() != b.bindings.size()) {
        return false;
    }
    for (size_t i = 0; i < a.bindings.size(); ++i) {
        const auto& x = a.bindings[i];
        const auto& y = b.bindings[i];
        if (x.set != y.set || x.binding != y.binding || x.kind != y.kind ||
            x.msl_index != y.msl_index || x.count != y.count) {
            return false;
        }
    }
    return true;
}

int main(int argc, char** argv) {
    fs::path dir;
    fs::path cache;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--cache") && i + 1 < argc) {
            cache = argv[++i];
        } else if (argv[i][0] != '-' && dir.empty()) {
            dir = argv[i];
        } else {
            std::fprintf(stderr, "usage: %s [<spv dir>] [--cache <dir>]\n", argv[0]);
            return 2;
        }
    }
    if (dir.empty()) {
        const char* home = std::getenv("HOME");
        dir = fs::path{home ? home : "."} / "Games/Bloodborne/bench/testuser/cache/CUSA03173";
    }
    if (cache.empty()) {
        cache = fs::temp_directory_path() / "bbport-mtl-shader-test-cache";
    }
    std::vector<fs::path> files;
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        if (entry.path().extension() == ".spv") {
            files.push_back(entry.path());
        }
    }
    std::sort(files.begin(), files.end());
    if (files.empty()) {
        std::fprintf(stderr, "no .spv files in %s\n", dir.c_str());
        return 2;
    }
    std::printf("shaders: %zu from %s\nMSL cache: %s\n", files.size(), dir.c_str(), cache.c_str());

    Metal::Device device;
    Metal::ShaderCompiler compiler{device, cache};

    struct StageStats {
        int total = 0, ok = 0, pervertex = 0, unsupported = 0, failed = 0;
    };
    std::map<std::string, StageStats> stages;
    std::map<std::string, int> errors;
    Timing time_miss, time_hit, time_miss_by_stage[3];
    int bad_cache = 0, not_vfc_failures = 0;
    int push = 0, bda = 0, pagetable = 0, size_buffer = 0, texel = 0, arrays = 0;
    u32 max_buffers = 0, max_textures = 0, max_samplers = 0;

    for (const fs::path& path : files) {
        std::ifstream file(path, std::ios::binary);
        std::vector<char> raw((std::istreambuf_iterator<char>(file)),
                              std::istreambuf_iterator<char>());
        std::vector<u32> spirv(raw.size() / 4);
        std::memcpy(spirv.data(), raw.data(), spirv.size() * 4);
        const u64 hash = std::strtoull(path.stem().string().c_str(), nullptr, 16);
        const std::string name = path.stem().string();

        const auto t0 = Clock::now();
        Metal::CompileResult result = compiler.Compile(spirv, hash, name);
        const double ms = MsSince(t0);

        StageStats& stats = stages[Metal::ShaderStageName(result.stage)];
        ++stats.total;
        switch (result.status) {
        case CompileStatus::Ok: {
            ++stats.ok;
            const Metal::CompiledShader& shader = *result.shader;
            if(shader.strict_vertex_reference && !SameBindings(shader,*shader.strict_vertex_reference)) {
                ++bad_cache;std::printf("FAIL %s: strict reference binding map differs\n",name.c_str());
            }
            (shader.from_disk_cache ? time_hit : time_miss).Add(ms);
            if (!shader.from_disk_cache) {
                const int index = result.stage == ShaderStage::Vertex     ? 0
                                  : result.stage == ShaderStage::Fragment ? 1
                                                                          : 2;
                time_miss_by_stage[index].Add(ms);
                // The text and map read back from the cache must equal what was just produced.
                Metal::CompileResult again = compiler.Compile(spirv, hash, name);
                if (again.status != CompileStatus::Ok || !again.shader->from_disk_cache ||
                    !SameBindings(shader, *again.shader) ||
                    shader.strict_vertex_helpers_inlined!=again.shader->strict_vertex_helpers_inlined) {
                    ++bad_cache;
                    std::printf("FAIL %s: cache round trip differs\n", name.c_str());
                }
            }
            if(shader.stage==ShaderStage::Compute) {
                std::printf("  compute resource metadata %s direct=%d bda=%d paged=%d cached=%d\n",
                    name.c_str(),int(shader.HasOnlyDirectComputeResources()),int(shader.uses_device_address),
                    int(shader.uses_bda_pagetable),int(shader.from_disk_cache));
            }
            push += shader.uses_push_constants;
            bda += shader.uses_device_address;
            pagetable += shader.uses_bda_pagetable;
            size_buffer += shader.buffer_size_buffer != ~0u;
            for (const auto& b : shader.bindings) {
                texel += b.kind == Metal::ShaderBinding::Kind::TexelBuffer;
                arrays += b.count > 1;
            }
            max_buffers = std::max(max_buffers, shader.num_buffers);
            max_textures = std::max(max_textures, shader.num_textures);
            max_samplers = std::max(max_samplers, shader.num_samplers);
            if (!shader.library || !shader.function || shader.entry.empty()) {
                ++bad_cache;
                std::printf("FAIL %s: missing library or function\n", name.c_str());
            }
            break;
        }
        case CompileStatus::PerVertexInput:
            ++stats.pervertex;
            break;
        case CompileStatus::UnsupportedStage:
            ++stats.unsupported;
            break;
        case CompileStatus::Failed:
            ++stats.failed;
            ++not_vfc_failures;
            ++errors[result.error];
            break;
        }
    }

    const auto& counters = compiler.GetCounters();
    std::printf("\n== results\n");
    int ok_total = 0, pervertex_total = 0, unsupported_total = 0;
    for (const auto& [stage, s] : stages) {
        std::printf("  %-13s total %-4d ok %-4d pervertex %-2d unsupported-stage %-3d failures %d\n",
                    stage.c_str(), s.total, s.ok, s.pervertex, s.unsupported, s.failed);
        ok_total += s.ok;
        pervertex_total += s.pervertex;
        unsupported_total += s.unsupported;
    }
    std::printf("  ok %d, pervertex %d, unsupported-stage %d, failures %d\n", ok_total,
                pervertex_total, unsupported_total, not_vfc_failures);
    std::printf("  counters: compiled %llu, compile_failures %llu, pervertex_failures %llu, "
                "unsupported_stages %llu, cache_hits %llu, cache_stores %llu\n",
                (unsigned long long)counters.compiled, (unsigned long long)counters.compile_failures,
                (unsigned long long)counters.pervertex_failures,
                (unsigned long long)counters.unsupported_stages,
                (unsigned long long)counters.cache_hits, (unsigned long long)counters.cache_stores);
    if (!errors.empty()) {
        std::printf("\n== failures\n");
        for (const auto& [message, n] : errors) {
            std::printf("  %4d  %s\n", n, message.c_str());
        }
    }
    std::printf("\n== times (Compile, wall)\n");
    time_miss.Print("cache miss (convert)");
    time_miss_by_stage[0].Print("  vertex");
    time_miss_by_stage[1].Print("  fragment");
    time_miss_by_stage[2].Print("  compute");
    time_hit.Print("cache hit");
    if (!time_hit.ms.empty() && time_miss.ms.empty()) {
        std::printf("  (this run only had cache hits)\n");
    }
    std::printf("\n== resources\n  max buffers %u, textures %u, samplers %u (incl. push constants, "
                "size buffer)\n  push constants %d, device address %d, bda_pagetable %d, buffer-size "
                "buffer %d, texel buffers %d, bindings with arrays %d\n",
                max_buffers, max_textures, max_samplers, push, bda, pagetable, size_buffer, texel,
                arrays);

    const bool pass = not_vfc_failures == 0 && pervertex_total == 0 && bad_cache == 0;
    std::printf("\n%s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
