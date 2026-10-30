#!/usr/bin/env python3
"""CPU controls for shadPS4 #5524, with no game, device, or existing cache access.

Compile the full production types.h and the exact production extent-merge
assignment with the actual project logging shim and native dependency headers.
Mutation controls use the exact SubresourceExtent declaration extracted from
that header, with production common/types.h. No dependency or tested type is
stubbed. A normal renderer build and GPU tests are still required to execute
TextureCache allocation/copy integration.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHAD = ROOT / "vendor/metal_api/shadps4"
SHIM = ROOT / "vendor/metal_api/shim"
TYPES = SHAD / "video_core/texture_cache/types.h"
CACHE = SHAD / "video_core/texture_cache/texture_cache.cpp"

CPP = r"""
#include <compare>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <set>
#include <type_traits>
#include <utility>
#include "upstream_texture_extent_under_test.h"

using VideoCore::SubresourceExtent;
using Coordinates = std::set<std::pair<u32, u32>>;

static_assert(sizeof(SubresourceExtent) == 2 * sizeof(u32));
static_assert(alignof(SubresourceExtent) == alignof(u32));
static_assert(offsetof(SubresourceExtent, levels) == 0);
static_assert(offsetof(SubresourceExtent, layers) == sizeof(u32));
static_assert(std::is_trivially_copyable_v<SubresourceExtent>);
static_assert(noexcept(SubresourceExtent{}.Contains(SubresourceExtent{})));

static void require(bool condition, const char* name) {
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", name);
        std::exit(7);
    }
}

// Independent oracle: enumerate the actual subresource coordinates. Tests never
// use the implementation's two-count comparison to compute the expected result.
static Coordinates coordinates(SubresourceExtent extent) {
    Coordinates result;
    for (u32 mip = 0; mip < extent.levels; ++mip) {
        for (u32 layer = 0; layer < extent.layers; ++layer) {
            result.emplace(mip, layer);
        }
    }
    return result;
}

int main() {
    const SubresourceExtent crossed_mips{8, 1}, request{4, 6};
    require(!crossed_mips.Contains(request), "more mips cannot hide missing array layers");
    require(!SubresourceExtent{2, 12}.Contains(request),
            "more array layers cannot hide missing mips");
    require(SubresourceExtent{8, 12}.Contains(request), "both dimensions sufficient");
    require(request.Contains(request), "equal extents cover each other");
    require(SubresourceExtent{}.Contains(SubresourceExtent{1, 1}), "default extent");
    require(!SubresourceExtent{1, 1}.Contains(SubresourceExtent{1, 2}), "one missing layer");
    require(!SubresourceExtent{1, 1}.Contains(SubresourceExtent{2, 1}), "one missing mip");
    require(crossed_mips > request,
            "defaulted ordering is retained but must not be used as containment");
    require(request == SubresourceExtent{4, 6}, "equality remains unchanged");
    const auto max = std::numeric_limits<u32>::max();
    require(SubresourceExtent{max, max}.Contains(SubresourceExtent{max, max}),
            "full u32 representation, without multiplication or overflow");
    require(!SubresourceExtent{max, 1}.Contains(SubresourceExtent{max, 2}),
            "full u32 mip count cannot hide one missing layer");
    require(!SubresourceExtent{1, max}.Contains(SubresourceExtent{2, max}),
            "full u32 layer count cannot hide one missing mip");
    const auto crossed_merge = merge_extent(request, crossed_mips);
    require(crossed_merge == SubresourceExtent{8, 6},
            "crossed extents merge both dimensions, not lexicographically");
    require(crossed_merge.Contains(request) && crossed_merge.Contains(crossed_mips),
            "rebuilt extent passes final admission for request and retains cached subresources");
    require(merge_extent(SubresourceExtent{max, 1}, SubresourceExtent{1, max}) ==
                SubresourceExtent{max, max}, "merge keeps full u32 dimensions without overflow");

    unsigned checked = 0;
    for (u32 old_mips = 1; old_mips <= 8; ++old_mips) {
        for (u32 old_layers = 1; old_layers <= 8; ++old_layers) {
            const SubresourceExtent old{old_mips, old_layers};
            const auto available = coordinates(old);
            for (u32 new_mips = 1; new_mips <= 8; ++new_mips) {
                for (u32 new_layers = 1; new_layers <= 8; ++new_layers) {
                    const SubresourceExtent requested{new_mips, new_layers};
                    const auto needed = coordinates(requested);
                    bool covered = true;
                    for (const auto& coordinate : needed) {
                        if (!available.contains(coordinate)) {
                            covered = false;
                            break;
                        }
                    }
                    require(old.Contains(requested) == covered, "coordinate-set oracle");
                    Coordinates all = available;
                    all.insert(needed.begin(), needed.end());
                    u32 required_mips = 0, required_layers = 0;
                    for (const auto& coordinate : all) {
                        if (coordinate.first + 1 > required_mips)
                            required_mips = coordinate.first + 1;
                        if (coordinate.second + 1 > required_layers)
                            required_layers = coordinate.second + 1;
                    }
                    const auto merged = merge_extent(requested, old);
                    require(merged.Contains(old) && merged.Contains(requested),
                            "production merge covers both inputs before final admission");
                    require(merged == SubresourceExtent{required_mips, required_layers},
                            "production merge is the minimum enclosing coordinate rectangle");
                    require(merge_extent(old, requested) == merged, "merge order independence");
                    ++checked;
                }
            }
        }
    }
    std::printf("UPSTREAM_TEXTURE_EXTENTS_PASS matrix=%u named=15 abi=u32,u32 merge=production\n", checked);
}
"""


def declaration():
    source = TYPES.read_text()
    match = re.search(r"struct SubresourceExtent \{.*?\n\};", source, re.S)
    if match is None:
        raise AssertionError("production SubresourceExtent declaration not found")
    return match.group(0)


def merge_assignment():
    source = CACHE.read_text()
    match = re.search(r"new_info\.resources\s*=\s*.*?;", source, re.S)
    if match is None:
        raise AssertionError("production ResolveDepthOverlap merge assignment not found")
    return match.group(0)


def build_and_run(body=None, assignment=None):
    compiler = shutil.which("clang++")
    if compiler is None:
        raise AssertionError("clang++ is required for actual native extent controls")
    with tempfile.TemporaryDirectory(prefix="blood-echoes-upstream-texture-") as temporary:
        work = Path(temporary)
        include = ('#include "video_core/texture_cache/types.h"\n' if body is None else
                   '#include "common/types.h"\nnamespace VideoCore {\n' + body + "\n}\n")
        assignment = merge_assignment() if assignment is None else assignment
        merge = """
static VideoCore::SubresourceExtent merge_extent(VideoCore::SubresourceExtent requested,
                                                 VideoCore::SubresourceExtent cached) {
    struct Info { VideoCore::SubresourceExtent resources; };
    const Info requested_info{requested};
    const struct { Info info; } cache_image{{cached}};
    Info new_info{};
    """ + assignment + "\n    return new_info.resources;\n}\n"
        (work / "upstream_texture_extent_under_test.h").write_text(include + merge)
        (work / "test.cpp").write_text(CPP)
        build = subprocess.run(
            [compiler, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
             "-I" + str(SHIM), "-I" + str(SHAD), "-I/opt/homebrew/include",
             str(work / "test.cpp"), "-o", str(work / "test")],
            text=True, capture_output=True, timeout=45,
        )
        if build.returncode:
            raise AssertionError(build.stdout + build.stderr)
        return subprocess.run([str(work / "test")], text=True, capture_output=True, timeout=15)


class UpstreamTextureExtentTests(unittest.TestCase):
    def test_native_full_production_header(self):
        result = build_and_run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UPSTREAM_TEXTURE_EXTENTS_PASS matrix=4096", result.stdout)

    def test_native_controls_reject_old_and_incomplete_comparisons(self):
        body = declaration()
        expression = "return levels >= other.levels && layers >= other.layers;"
        self.assertEqual(body.count(expression), 1)
        mutants = {
            "old-lexicographic-order": "return *this >= other;",
            "either-dimension-only": "return levels >= other.levels || layers >= other.layers;",
            "rejects-equal-extents": "return levels > other.levels && layers > other.layers;",
        }
        for name, replacement in mutants.items():
            with self.subTest(name=name):
                result = build_and_run(body.replace(expression, replacement))
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertIn("FAIL:", result.stderr)

    def test_native_controls_reject_lexicographic_rebuild(self):
        result = build_and_run(assignment=
            "new_info.resources = std::max(requested_info.resources, cache_image.info.resources);")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("crossed extents merge both dimensions", result.stderr)

    def test_all_production_range_decisions_are_wired(self):
        # These are integration guards, not a claim to execute TextureCache or Metal.
        source = CACHE.read_text()
        self.assertIn("bool recreate = !cache_image.info.resources.Contains(requested_info.resources);",
                      source)
        self.assertIn("image_info.type == cache_image.info.type &&\n"
                      "            !cache_image.info.resources.Contains(image_info.resources)", source)
        self.assertIn("else if (!image_resolved.info.resources.Contains(info.resources))", source)
        self.assertNotRegex(source, r"resources\s*[<>]=?\s*\w+(?:\.\w+)*\.resources")
        self.assertNotRegex(source, r"std::(?:max|min)\([^;]*\.resources\s*[,)]")
        self.assertIn("Old resources cover new:", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
