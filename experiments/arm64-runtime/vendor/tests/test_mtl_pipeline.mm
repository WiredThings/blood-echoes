// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_pso_cache: builds a Metal pipeline for every graphics and
// compute pipeline key of a Vulkan pipeline cache directory (the clinic run's, read only) with the
// shaders of the same directory compiled by mtl_shader. Needs a Metal device, no window or game.
//
//   mtl-pipeline-test [<cache dir with g_*.key, c_*.key, *.spv>] [--cache <pipeline cache dir>]
//                     [--keep-archive] [--metal-keys]
//
// The binary archive goes to --cache (default: a folder in $TMPDIR, never the game's user
// directory) and is deleted at the start unless --keep-archive is given. Pass 1 builds everything
// (archive empty unless kept), the archive is flushed, pass 2 builds everything again with a new
// PipelineCache that loads it. Keys with tessellation/geometry stages and keys whose shaders
// mtl_shader rejects (PerVertexKHR) are skipped and counted. Exit 1 on any pipeline failure.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "common/hash.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
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

static double MsSince(Clock::time_point t) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

static std::vector<char> ReadAll(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

struct GraphicsEntry {
    Vulkan::GraphicsPipelineKey key;
    Vulkan::GraphicsPipeline::SerializationSupport sdata;
    std::string name;
    std::vector<Metal::VertexAttribute> metal_attributes;
};

// Reads g_*.key: u32 version, u32 kind (0), the raw key, then the raw SerializationSupport.
static bool LoadGraphicsKey(const fs::path& path, GraphicsEntry& entry, bool metal_format) {
    const std::vector<char> data = ReadAll(path);
    size_t pos = 0;
    auto take = [&](void* out, size_t size) {
        if (pos + size > data.size()) {
            return false;
        }
        std::memcpy(out, data.data() + pos, size);
        pos += size;
        return true;
    };
    u32 version = 0, kind = 1;
    auto& s = entry.sdata;
    if (metal_format) {
        u32 count=0;
        if (!take(&version,4) || !take(&kind,4) || kind!=0 ||
            !take(&entry.key,sizeof(entry.key)) || !take(&count,4) ||
            count>VideoCore::MaxVertexBufferCount) return false;
        for(u32 i=0;i<count;++i) {
            u32 location=0,per_instance=0,divisor=0;
            if(!take(&location,4) || !take(&per_instance,4) || !take(&divisor,4) ||
               per_instance>1) return false;
            entry.metal_attributes.push_back({location,per_instance!=0,divisor});
        }
        entry.name=path.stem().string();
        return true;
    }
    if (!take(&version, 4) || !take(&kind, 4) || kind != 0 ||
        !take(&entry.key, sizeof(entry.key)) ||
        !take(&s.vertex_attributes, sizeof(s.vertex_attributes)) ||
        !take(&s.vertex_bindings, sizeof(s.vertex_bindings)) ||
        !take(&s.divisors, sizeof(s.divisors))) {
        return false;
    }
    entry.name = path.stem().string();
    return true;
}

struct ShaderFile {
    fs::path path;
    u64 pgm_hash{};
    u64 perm{};
};

struct Shaders {
    std::map<u64, ShaderFile> by_stage_hash;
    std::map<u64, Metal::CompileResult> compiled;
    Metal::ShaderCompiler* compiler{};

    Metal::CompileResult* Get(u64 stage_hash) {
        const auto it = by_stage_hash.find(stage_hash);
        if (it == by_stage_hash.end()) {
            return nullptr;
        }
        auto [c, is_new] = compiled.try_emplace(stage_hash);
        if (is_new) {
            const std::vector<char> raw = ReadAll(it->second.path);
            std::vector<u32> spirv(raw.size() / 4);
            std::memcpy(spirv.data(), raw.data(), spirv.size() * 4);
            c->second = compiler->Compile(spirv, it->second.pgm_hash, it->second.path.stem().string());
        }
        return &c->second;
    }
};

struct PassResult {
    int built = 0, failed = 0;
    double ms = 0;
};

int main(int argc, char** argv) {
    fs::path dir, cache;
    bool keep_archive = false;
    bool metal_format = false;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--metal-keys")) {
            metal_format=true;
        } else if (!std::strcmp(argv[i], "--cache") && i + 1 < argc) {
            cache = argv[++i];
        } else if (!std::strcmp(argv[i], "--keep-archive")) {
            keep_archive = true;
        } else if (argv[i][0] != '-' && dir.empty()) {
            dir = argv[i];
        } else {
            std::fprintf(stderr, "usage: %s [<cache dir>] [--cache <dir>] [--keep-archive] [--metal-keys]\n",
                         argv[0]);
            return 2;
        }
    }
    if (dir.empty()) {
        const char* home = std::getenv("HOME");
        dir = fs::path{home ? home : "."} / "Games/Bloodborne/bench/testuser/cache/CUSA03173";
    }
    if (cache.empty()) {
        cache = fs::temp_directory_path() / "bbport-mtl-pipeline-test-cache";
    }
    std::error_code ec;
    fs::create_directories(cache, ec);
    if (!keep_archive) {
        fs::remove(cache / "pipelines.binaryarchive", ec);
    }

    Shaders shaders;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        const fs::path& p = entry.path();
        if (p.extension() != ".spv") {
            continue;
        }
        const std::string stem = p.stem().string(); // 0x<hash>_<perm>
        ShaderFile f{p, std::strtoull(stem.c_str(), nullptr, 16),
                     std::strtoull(stem.substr(stem.find('_') + 1).c_str(), nullptr, 10)};
        shaders.by_stage_hash[HashCombine(f.pgm_hash, f.perm)] = f;
    }
    std::vector<GraphicsEntry> graphics;
    std::vector<std::pair<Vulkan::ComputePipelineKey, std::string>> computes;
    int bad_keys = 0;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        const fs::path& p = entry.path();
        if (p.extension() != ".key") {
            continue;
        }
        const std::string name = p.filename().string();
        if (name.starts_with("g_")) {
            GraphicsEntry g;
            if (LoadGraphicsKey(p, g, metal_format)) {
                graphics.push_back(std::move(g));
            } else {
                ++bad_keys;
            }
        } else if (name.starts_with("c_")) {
            const std::vector<char> data = ReadAll(p);
            Vulkan::ComputePipelineKey key{};
            if (data.size() >= 16) {
                std::memcpy(&key.value, data.data() + 8, 8);
                computes.emplace_back(key, name);
            } else {
                ++bad_keys;
            }
        }
    }
    std::sort(graphics.begin(), graphics.end(),
              [](const auto& a, const auto& b) { return a.name < b.name; });
    std::printf("%s: %zu shaders, %zu graphics keys, %zu compute keys (%d unreadable)\n",
                dir.c_str(), shaders.by_stage_hash.size(), graphics.size(), computes.size(),
                bad_keys);
    std::printf("pipeline cache: %s\n", cache.c_str());
    if (graphics.empty() || shaders.by_stage_hash.empty()) {
        std::fprintf(stderr, "no keys or shaders found\n");
        return 2;
    }

    Metal::Device device;
    Metal::ShaderCompiler compiler{device, cache};
    shaders.compiler = &compiler;

    // Select what can be built: both shaders compiled, no tessellation/geometry stage.
    struct Job {
        const GraphicsEntry* entry;
        const Metal::CompiledShader* vs;
        const Metal::CompiledShader* fs;
        std::vector<Metal::VertexAttribute> attributes;
    };
    std::vector<Job> jobs;
    int skipped_stage = 0, skipped_shader = 0, skipped_missing = 0, attr_mismatch = 0;
    int with_blend = 0, mrt3 = 0, instanced = 0, depth_only = 0, multi_attr = 0, rect_quad = 0;
    constexpr u32 FS = u32(Shader::SwStage::Fragment), VS = u32(Shader::SwStage::Vertex);
    for (const GraphicsEntry& g : graphics) {
        const auto& key = g.key;
        if (key.stage_hashes[u32(Shader::SwStage::TessellationControl)] ||
            key.stage_hashes[u32(Shader::SwStage::TessellationEval)] ||
            key.stage_hashes[u32(Shader::SwStage::Geometry)]) {
            ++skipped_stage;
            continue;
        }
        Metal::CompileResult* vs = shaders.Get(key.stage_hashes[VS]);
        Metal::CompileResult* fs = key.stage_hashes[FS] ? shaders.Get(key.stage_hashes[FS]) : nullptr;
        if (!vs || (key.stage_hashes[FS] && !fs)) {
            ++skipped_missing;
            continue;
        }
        if (vs->status != Metal::CompileStatus::Ok || (fs && fs->status != Metal::CompileStatus::Ok)) {
            ++skipped_shader;
            continue;
        }
        Job job{&g, vs->shader.get(), fs ? fs->shader.get() : nullptr, {}};
        const auto& sd = g.sdata;
        job.attributes=g.metal_attributes;
        if (!metal_format) for (const auto& attr : sd.vertex_attributes) {
            Metal::VertexAttribute a;
            a.location = attr.location;
            for (const auto& b : sd.vertex_bindings) {
                if (b.binding == attr.binding && b.inputRate == vk::VertexInputRate::eInstance) {
                    a.per_instance = true;
                }
            }
            a.divisor = 1;
            for (const auto& d : sd.divisors) {
                if (d.binding == attr.binding) {
                    a.divisor = d.divisor;
                }
            }
            job.attributes.push_back(a);
        }
        u32 formats = 0;
        for (auto f : key.vertex_buffer_formats) {
            formats += f != vk::Format::eUndefined;
        }
        if (formats != job.attributes.size()) {
            ++attr_mismatch;
        }
        for (u32 i = 0; i < key.num_color_attachments; ++i) {
            with_blend += key.blend_controls[i].enable;
        }
        mrt3 += key.num_color_attachments >= 3;
        instanced += std::any_of(job.attributes.begin(), job.attributes.end(),
                                 [](const auto& a) { return a.per_instance; });
        depth_only += key.num_color_attachments == 0;
        multi_attr += job.attributes.size() >= 4;
        rect_quad += key.prim_type == AmdGpu::PrimitiveType::RectList ||
                     key.prim_type == AmdGpu::PrimitiveType::QuadList;
        jobs.push_back(std::move(job));
    }
    std::printf("graphics jobs %zu (skipped: %d tess/geometry, %d shader not translatable, %d "
                "shader file missing); attribute count != format count: %d\n",
                jobs.size(), skipped_stage, skipped_shader, skipped_missing, attr_mismatch);
    std::printf("  coverage: blended attachments %d, MRT>=3 %d, instanced %d, no color attachment %d, "
                ">=4 attributes %d, rect/quad lists %d\n",
                with_blend, mrt3, instanced, depth_only, multi_attr, rect_quad);

    // Compute: every cached compute shader.
    struct CJob {
        Vulkan::ComputePipelineKey key;
        const Metal::CompiledShader* cs;
    };
    std::vector<CJob> cjobs;
    int compute_skipped = 0;
    for (const auto& [key, name] : computes) {
        Metal::CompileResult* cs = shaders.Get(key.value);
        if (!cs || cs->status != Metal::CompileStatus::Ok) {
            ++compute_skipped;
            continue;
        }
        cjobs.push_back({key, cs->shader.get()});
    }
    std::printf("compute jobs %zu (skipped %d)\n", cjobs.size(), compute_skipped);

    auto run_pass = [&](Metal::PsoCache& pc, PassResult& gfx, PassResult& comp) {
        std::vector<double> times;
        for (const Job& job : jobs) {
            const auto t = Clock::now();
            const Metal::RenderPipeline* p = pc.GetRenderPipeline(
                job.entry->key, job.vs, job.fs, {.vertex_attributes = job.attributes});
            const double ms = MsSince(t);
            gfx.ms += ms;
            if (p) {
                ++gfx.built;
            } else {
                ++gfx.failed;
                std::printf("FAIL graphics %s\n", job.entry->name.c_str());
            }
            // The same request again is a cache hit and the same object.
            if (p && pc.GetRenderPipeline(job.entry->key, job.vs, job.fs,
                                          {.vertex_attributes = job.attributes}) != p) {
                ++gfx.failed;
                std::printf("FAIL graphics %s: second lookup returned another object\n",
                            job.entry->name.c_str());
            }
        }
        // Variants the cached keys do not have: an instanced, a divided and a constant attribute.
        for (const Job& job : jobs) {
            if (job.attributes.size() < 3) {
                continue;
            }
            std::vector<Metal::VertexAttribute> attrs = job.attributes;
            attrs[0].per_instance = true;
            attrs[0].divisor = 1;
            attrs[1].per_instance = true;
            attrs[1].divisor = 0;
            attrs[2].per_instance = true;
            attrs[2].divisor = 3;
            const Metal::RenderPipeline* p =
                pc.GetRenderPipeline(job.entry->key, job.vs, job.fs, {.vertex_attributes = attrs});
            const Metal::RenderPipeline* q = pc.GetRenderPipeline(
                job.entry->key, job.vs, job.fs, {.vertex_attributes = job.attributes});
            const bool ok = p && q && p != q && p->vertex_slots[1].constant &&
                            !p->vertex_slots[0].constant && p->vertex_slots[2].slot == 28;
            ++gfx.built;
            if (!ok) {
                ++gfx.failed;
                std::printf("FAIL variants of %s\n", job.entry->name.c_str());
            }
            break;
        }
        for (const CJob& job : cjobs) {
            const auto t = Clock::now();
            const Metal::ComputePipeline* p = pc.GetComputePipeline(job.key, job.cs);
            comp.ms += MsSince(t);
            if (p) {
                ++comp.built;
                if (p->threads_per_group[0] * p->threads_per_group[1] * p->threads_per_group[2] ==
                    0) {
                    ++comp.failed;
                }
            } else {
                ++comp.failed;
                std::printf("FAIL compute %#llx\n", (unsigned long long)job.key.value);
            }
        }
    };
    auto print_counters = [](const Metal::PsoCache& pc) {
        const auto& c = pc.GetCounters();
        std::printf("  counters: render_pipelines %llu, compute_pipelines %llu, failures %llu, "
                    "archive_hits %llu, archive_adds %llu, depth_stencil_states %llu\n",
                    (unsigned long long)c.render_pipelines, (unsigned long long)c.compute_pipelines,
                    (unsigned long long)c.failures, (unsigned long long)c.archive_hits,
                    (unsigned long long)c.archive_adds, (unsigned long long)c.depth_stencil_states);
    };

    bool pass = true;
    PassResult gfx1, comp1, gfx2, comp2;
    u64 distinct1 = 0;
    {
        Metal::PsoCache pc{device, cache};
        std::printf("\n== pass 1 (archive %s)\n", pc.ArchiveLoaded() ? "loaded" : "empty");
        run_pass(pc, gfx1, comp1);
        print_counters(pc);
        distinct1 = pc.GetCounters().render_pipelines;

        // Depth/stencil states: equal states are one object, differing ones are not.
        Metal::DepthStencilKey a;
        a.depth_test = true;
        a.depth_write = true;
        a.depth_compare = vk::CompareOp::eGreaterOrEqual;
        Metal::DepthStencilKey b = a;
        b.stencil_test = true;
        b.front = {vk::StencilOp::eKeep, vk::StencilOp::eReplace, vk::StencilOp::eKeep,
                   vk::CompareOp::eAlways, 0xff, 0xff};
        b.back = b.front;
        Metal::DepthStencilKey off; // everything off
        Metal::DepthStencilKey off2 = off;
        off2.depth_compare = vk::CompareOp::eLess; // ignored while the test is off
        off2.depth_write = true;
        void* sa = pc.GetDepthStencilState(a);
        void* sb = pc.GetDepthStencilState(b);
        void* so = pc.GetDepthStencilState(off);
        void* so2 = pc.GetDepthStencilState(off2);
        const bool ds_ok = sa && sb && so && sa != sb && sa != so && so == so2 &&
                           pc.GetDepthStencilState(a) == sa &&
                           pc.GetCounters().depth_stencil_states == 3;
        std::printf("  depth-stencil states: %s\n", ds_ok ? "ok" : "FAIL");
        pass &= ds_ok;
        pass &= pc.Flush();
    }
    std::error_code size_ec;
    std::printf("  archive file: %llu bytes\n",
                (unsigned long long)fs::file_size(cache / "pipelines.binaryarchive", size_ec));
    {
        Metal::PsoCache pc{device, cache};
        std::printf("\n== pass 2 (archive %s)\n", pc.ArchiveLoaded() ? "loaded" : "empty");
        run_pass(pc, gfx2, comp2);
        print_counters(pc);
        pass &= pc.GetCounters().render_pipelines == distinct1;
    }

    std::printf("\n== results\n");
    std::printf("  pass 1: graphics %d built %d failed in %.1f ms (%.2f ms each); compute %d built "
                "%d failed in %.1f ms (%.2f ms each)\n",
                gfx1.built, gfx1.failed, gfx1.ms, gfx1.ms / std::max(1, gfx1.built), comp1.built,
                comp1.failed, comp1.ms, comp1.ms / std::max(1, comp1.built));
    std::printf("  pass 2: graphics %d built %d failed in %.1f ms (%.2f ms each); compute %d built "
                "%d failed in %.1f ms (%.2f ms each)\n",
                gfx2.built, gfx2.failed, gfx2.ms, gfx2.ms / std::max(1, gfx2.built), comp2.built,
                comp2.failed, comp2.ms, comp2.ms / std::max(1, comp2.built));
    pass &= gfx1.failed == 0 && comp1.failed == 0 && gfx2.failed == 0 && comp2.failed == 0;
    pass &= gfx1.built == int(jobs.size()) + 1 && comp1.built == int(cjobs.size());
    std::printf("\n%s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}
