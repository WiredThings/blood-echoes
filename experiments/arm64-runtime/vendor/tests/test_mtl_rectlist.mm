// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of renderer_metal/mtl_spirv_rectlist (P4.3d): GCN RectList vertex shaders are rewritten
// to emit two triangles (6 vertices) per rectangle, with the 4th corner v3 = va + vb - vc.
//   (a) GPU correctness: a small vertex shader (glslang, embedded below) with three rectangles of
//       three vertices (the right-angle corner first, in the middle and last) and a colour, a flat
//       integer, PointSize and ClipDistance output is rewritten, compiled through ShaderCompiler and
//       drawn as 6 vertices per rectangle into a 64x64 target: the rectangles are fully covered,
//       the colours are the affine extrapolation (so the pixels near v3 too), the flat integer is
//       the corner's, and nothing outside is touched; also with a vertex start (first_vertex);
//   (b) shapes the rewrite refuses (vertex attributes, storage buffer stores);
//   (c) the shader cache of a game run: every vertex shader the pipeline keys use with RectList
//       (and, for the statistics, every other vertex shader) is rewritten and compiled.
// Needs a Metal device, no window or game.
//
//   mtl-rectlist-test [<shader cache dir>] [--dump <dir>]
//
// The directory defaults to the Metal backend's run cache (~/Games/Bloodborne/bench/testuser/cache/
// CUSA03173-metal, read only); --dump writes the rewritten SPIR-V of every case there.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <set>
#include <span>
#include <string>
#include <vector>

#include "video_core/pipeline_cache_core_types.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_spirv_rectlist.h"

// common/assert.cpp pulls in the emulator; a failed ASSERT here just aborts.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

namespace fs = std::filesystem;
using Metal::CompileStatus;

static int g_failures = 0;

static void Fail(const char* what) {
    std::printf("FAIL %s\n", what);
    ++g_failures;
}

// ---- (a) shaders: glslangValidator -V --target-env vulkan1.2 ------------------------------------

// rect.vert
static const uint32_t kRectVertSpirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x00000057, 0x00000000, 0x00020011, 0x00000001, 0x00020011,
    0x00000020, 0x0006000b, 0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e,
    0x00000000, 0x00000001, 0x0009000f, 0x00000000, 0x00000004, 0x6e69616d, 0x00000000, 0x0000000d,
    0x00000024, 0x0000003c, 0x00000055, 0x00030003, 0x00000002, 0x000001c2, 0x00040005, 0x00000004,
    0x6e69616d, 0x00000000, 0x00060005, 0x0000000b, 0x505f6c67, 0x65567265, 0x78657472, 0x00000000,
    0x00060006, 0x0000000b, 0x00000000, 0x505f6c67, 0x7469736f, 0x006e6f69, 0x00070006, 0x0000000b,
    0x00000001, 0x505f6c67, 0x746e696f, 0x657a6953, 0x00000000, 0x00070006, 0x0000000b, 0x00000002,
    0x435f6c67, 0x4470696c, 0x61747369, 0x0065636e, 0x00030005, 0x0000000d, 0x00000000, 0x00060005,
    0x00000024, 0x565f6c67, 0x65747265, 0x646e4978, 0x00007865, 0x00050005, 0x00000027, 0x65646e69,
    0x6c626178, 0x00000065, 0x00040005, 0x0000003c, 0x6f635f76, 0x00726f6c, 0x00050005, 0x00000050,
    0x65646e69, 0x6c626178, 0x00000065, 0x00040005, 0x00000055, 0x6c665f76, 0x00007461, 0x00030047,
    0x0000000b, 0x00000002, 0x00050048, 0x0000000b, 0x00000000, 0x0000000b, 0x00000000, 0x00050048,
    0x0000000b, 0x00000001, 0x0000000b, 0x00000001, 0x00050048, 0x0000000b, 0x00000002, 0x0000000b,
    0x00000003, 0x00040047, 0x00000024, 0x0000000b, 0x0000002a, 0x00030047, 0x00000027, 0x00000018,
    0x00040047, 0x0000003c, 0x0000001e, 0x00000000, 0x00030047, 0x00000050, 0x00000018, 0x00030047,
    0x00000055, 0x0000000e, 0x00040047, 0x00000055, 0x0000001e, 0x00000001, 0x00020013, 0x00000002,
    0x00030021, 0x00000003, 0x00000002, 0x00030016, 0x00000006, 0x00000020, 0x00040017, 0x00000007,
    0x00000006, 0x00000004, 0x00040015, 0x00000008, 0x00000020, 0x00000000, 0x0004002b, 0x00000008,
    0x00000009, 0x00000001, 0x0004001c, 0x0000000a, 0x00000006, 0x00000009, 0x0005001e, 0x0000000b,
    0x00000007, 0x00000006, 0x0000000a, 0x00040020, 0x0000000c, 0x00000003, 0x0000000b, 0x0004003b,
    0x0000000c, 0x0000000d, 0x00000003, 0x00040015, 0x0000000e, 0x00000020, 0x00000001, 0x0004002b,
    0x0000000e, 0x0000000f, 0x00000000, 0x00040017, 0x00000010, 0x00000006, 0x00000002, 0x0004002b,
    0x00000008, 0x00000011, 0x00000009, 0x0004001c, 0x00000012, 0x00000010, 0x00000011, 0x0004002b,
    0x00000006, 0x00000013, 0xbf400000, 0x0005002c, 0x00000010, 0x00000014, 0x00000013, 0x00000013,
    0x0004002b, 0x00000006, 0x00000015, 0xbe800000, 0x0005002c, 0x00000010, 0x00000016, 0x00000015,
    0x00000013, 0x0005002c, 0x00000010, 0x00000017, 0x00000013, 0x00000015, 0x0004002b, 0x00000006,
    0x00000018, 0x3e800000, 0x0005002c, 0x00000010, 0x00000019, 0x00000018, 0x00000018, 0x0004002b,
    0x00000006, 0x0000001a, 0x3f400000, 0x0005002c, 0x00000010, 0x0000001b, 0x0000001a, 0x00000018,
    0x0005002c, 0x00000010, 0x0000001c, 0x0000001a, 0x0000001a, 0x0004002b, 0x00000006, 0x0000001d,
    0x3f000000, 0x0005002c, 0x00000010, 0x0000001e, 0x00000013, 0x0000001d, 0x0004002b, 0x00000006,
    0x0000001f, 0x3f600000, 0x0005002c, 0x00000010, 0x00000020, 0x00000015, 0x0000001f, 0x0005002c,
    0x00000010, 0x00000021, 0x00000015, 0x0000001d, 0x000c002c, 0x00000012, 0x00000022, 0x00000014,
    0x00000016, 0x00000017, 0x00000019, 0x0000001b, 0x0000001c, 0x0000001e, 0x00000020, 0x00000021,
    0x00040020, 0x00000023, 0x00000001, 0x0000000e, 0x0004003b, 0x00000023, 0x00000024, 0x00000001,
    0x00040020, 0x00000026, 0x00000007, 0x00000012, 0x00040020, 0x00000028, 0x00000007, 0x00000010,
    0x0004002b, 0x00000006, 0x0000002b, 0x00000000, 0x0004002b, 0x00000006, 0x0000002c, 0x3f800000,
    0x00040020, 0x00000030, 0x00000003, 0x00000007, 0x0004002b, 0x0000000e, 0x00000032, 0x00000001,
    0x00040020, 0x00000033, 0x00000003, 0x00000006, 0x0004002b, 0x0000000e, 0x00000035, 0x00000002,
    0x00040017, 0x0000003a, 0x00000006, 0x00000003, 0x00040020, 0x0000003b, 0x00000003, 0x0000003a,
    0x0004003b, 0x0000003b, 0x0000003c, 0x00000003, 0x0004001c, 0x0000003d, 0x0000003a, 0x00000011,
    0x0006002c, 0x0000003a, 0x0000003e, 0x00000018, 0x0000002b, 0x0000002b, 0x0006002c, 0x0000003a,
    0x0000003f, 0x0000001a, 0x0000002b, 0x00000018, 0x0006002c, 0x0000003a, 0x00000040, 0x0000002b,
    0x0000001d, 0x0000001d, 0x0006002c, 0x0000003a, 0x00000041, 0x0000002c, 0x0000001d, 0x0000002b,
    0x0006002c, 0x0000003a, 0x00000042, 0x0000002b, 0x0000002c, 0x0000001d, 0x0006002c, 0x0000003a,
    0x00000043, 0x0000001d, 0x0000002b, 0x0000002c, 0x0004002b, 0x00000006, 0x00000044, 0x3dcccccd,
    0x0004002b, 0x00000006, 0x00000045, 0x3e4ccccd, 0x0004002b, 0x00000006, 0x00000046, 0x3e99999a,
    0x0006002c, 0x0000003a, 0x00000047, 0x00000044, 0x00000045, 0x00000046, 0x0004002b, 0x00000006,
    0x00000048, 0x3f666666, 0x0004002b, 0x00000006, 0x00000049, 0x3f4ccccd, 0x0004002b, 0x00000006,
    0x0000004a, 0x3f333333, 0x0006002c, 0x0000003a, 0x0000004b, 0x00000048, 0x00000049, 0x0000004a,
    0x0006002c, 0x0000003a, 0x0000004c, 0x0000002b, 0x0000002c, 0x0000002b, 0x000c002c, 0x0000003d,
    0x0000004d, 0x0000003e, 0x0000003f, 0x00000040, 0x00000041, 0x00000042, 0x00000043, 0x00000047,
    0x0000004b, 0x0000004c, 0x00040020, 0x0000004f, 0x00000007, 0x0000003d, 0x00040020, 0x00000051,
    0x00000007, 0x0000003a, 0x00040020, 0x00000054, 0x00000003, 0x0000000e, 0x0004003b, 0x00000054,
    0x00000055, 0x00000003, 0x00050036, 0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8,
    0x00000005, 0x0005003b, 0x00000026, 0x00000027, 0x00000007, 0x00000022, 0x0005003b, 0x0000004f,
    0x00000050, 0x00000007, 0x0000004d, 0x0004003d, 0x0000000e, 0x00000025, 0x00000024, 0x00050041,
    0x00000028, 0x00000029, 0x00000027, 0x00000025, 0x0004003d, 0x00000010, 0x0000002a, 0x00000029,
    0x00050051, 0x00000006, 0x0000002d, 0x0000002a, 0x00000000, 0x00050051, 0x00000006, 0x0000002e,
    0x0000002a, 0x00000001, 0x00070050, 0x00000007, 0x0000002f, 0x0000002d, 0x0000002e, 0x0000002b,
    0x0000002c, 0x00050041, 0x00000030, 0x00000031, 0x0000000d, 0x0000000f, 0x0003003e, 0x00000031,
    0x0000002f, 0x00050041, 0x00000033, 0x00000034, 0x0000000d, 0x00000032, 0x0003003e, 0x00000034,
    0x0000002c, 0x0004003d, 0x0000000e, 0x00000036, 0x00000024, 0x0004006f, 0x00000006, 0x00000037,
    0x00000036, 0x00050081, 0x00000006, 0x00000038, 0x0000002c, 0x00000037, 0x00060041, 0x00000033,
    0x00000039, 0x0000000d, 0x00000035, 0x0000000f, 0x0003003e, 0x00000039, 0x00000038, 0x0004003d,
    0x0000000e, 0x0000004e, 0x00000024, 0x00050041, 0x00000051, 0x00000052, 0x00000050, 0x0000004e,
    0x0004003d, 0x0000003a, 0x00000053, 0x00000052, 0x0003003e, 0x0000003c, 0x00000053, 0x0004003d,
    0x0000000e, 0x00000056, 0x00000024, 0x0003003e, 0x00000055, 0x00000056, 0x000100fd, 0x00010038,
};

// rect.frag
static const uint32_t kRectFragSpirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x00000017, 0x00000000, 0x00020011, 0x00000001, 0x0006000b,
    0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001,
    0x0008000f, 0x00000004, 0x00000004, 0x6e69616d, 0x00000000, 0x00000009, 0x0000000c, 0x00000010,
    0x00030010, 0x00000004, 0x00000007, 0x00030003, 0x00000002, 0x000001c2, 0x00040005, 0x00000004,
    0x6e69616d, 0x00000000, 0x00040005, 0x00000009, 0x6f635f6f, 0x00726f6c, 0x00040005, 0x0000000c,
    0x6f635f76, 0x00726f6c, 0x00040005, 0x00000010, 0x6c665f76, 0x00007461, 0x00040047, 0x00000009,
    0x0000001e, 0x00000000, 0x00040047, 0x0000000c, 0x0000001e, 0x00000000, 0x00030047, 0x00000010,
    0x0000000e, 0x00040047, 0x00000010, 0x0000001e, 0x00000001, 0x00020013, 0x00000002, 0x00030021,
    0x00000003, 0x00000002, 0x00030016, 0x00000006, 0x00000020, 0x00040017, 0x00000007, 0x00000006,
    0x00000004, 0x00040020, 0x00000008, 0x00000003, 0x00000007, 0x0004003b, 0x00000008, 0x00000009,
    0x00000003, 0x00040017, 0x0000000a, 0x00000006, 0x00000003, 0x00040020, 0x0000000b, 0x00000001,
    0x0000000a, 0x0004003b, 0x0000000b, 0x0000000c, 0x00000001, 0x00040015, 0x0000000e, 0x00000020,
    0x00000001, 0x00040020, 0x0000000f, 0x00000001, 0x0000000e, 0x0004003b, 0x0000000f, 0x00000010,
    0x00000001, 0x00050036, 0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8, 0x00000005,
    0x0004003d, 0x0000000a, 0x0000000d, 0x0000000c, 0x0004003d, 0x0000000e, 0x00000011, 0x00000010,
    0x0004006f, 0x00000006, 0x00000012, 0x00000011, 0x00050051, 0x00000006, 0x00000013, 0x0000000d,
    0x00000000, 0x00050051, 0x00000006, 0x00000014, 0x0000000d, 0x00000001, 0x00050051, 0x00000006,
    0x00000015, 0x0000000d, 0x00000002, 0x00070050, 0x00000007, 0x00000016, 0x00000013, 0x00000014,
    0x00000015, 0x00000012, 0x0003003e, 0x00000009, 0x00000016, 0x000100fd, 0x00010038,
};

// attr.vert
static const uint32_t kAttributeVertSpirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x0000001b, 0x00000000, 0x00020011, 0x00000001, 0x0006000b,
    0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001,
    0x0007000f, 0x00000000, 0x00000004, 0x6e69616d, 0x00000000, 0x0000000d, 0x00000012, 0x00030003,
    0x00000002, 0x000001c2, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00060005, 0x0000000b,
    0x505f6c67, 0x65567265, 0x78657472, 0x00000000, 0x00060006, 0x0000000b, 0x00000000, 0x505f6c67,
    0x7469736f, 0x006e6f69, 0x00070006, 0x0000000b, 0x00000001, 0x505f6c67, 0x746e696f, 0x657a6953,
    0x00000000, 0x00070006, 0x0000000b, 0x00000002, 0x435f6c67, 0x4470696c, 0x61747369, 0x0065636e,
    0x00070006, 0x0000000b, 0x00000003, 0x435f6c67, 0x446c6c75, 0x61747369, 0x0065636e, 0x00030005,
    0x0000000d, 0x00000000, 0x00040005, 0x00000012, 0x6f705f61, 0x00000073, 0x00030047, 0x0000000b,
    0x00000002, 0x00050048, 0x0000000b, 0x00000000, 0x0000000b, 0x00000000, 0x00050048, 0x0000000b,
    0x00000001, 0x0000000b, 0x00000001, 0x00050048, 0x0000000b, 0x00000002, 0x0000000b, 0x00000003,
    0x00050048, 0x0000000b, 0x00000003, 0x0000000b, 0x00000004, 0x00040047, 0x00000012, 0x0000001e,
    0x00000000, 0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002, 0x00030016, 0x00000006,
    0x00000020, 0x00040017, 0x00000007, 0x00000006, 0x00000004, 0x00040015, 0x00000008, 0x00000020,
    0x00000000, 0x0004002b, 0x00000008, 0x00000009, 0x00000001, 0x0004001c, 0x0000000a, 0x00000006,
    0x00000009, 0x0006001e, 0x0000000b, 0x00000007, 0x00000006, 0x0000000a, 0x0000000a, 0x00040020,
    0x0000000c, 0x00000003, 0x0000000b, 0x0004003b, 0x0000000c, 0x0000000d, 0x00000003, 0x00040015,
    0x0000000e, 0x00000020, 0x00000001, 0x0004002b, 0x0000000e, 0x0000000f, 0x00000000, 0x00040017,
    0x00000010, 0x00000006, 0x00000002, 0x00040020, 0x00000011, 0x00000001, 0x00000010, 0x0004003b,
    0x00000011, 0x00000012, 0x00000001, 0x0004002b, 0x00000006, 0x00000014, 0x00000000, 0x0004002b,
    0x00000006, 0x00000015, 0x3f800000, 0x00040020, 0x00000019, 0x00000003, 0x00000007, 0x00050036,
    0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x0004003d, 0x00000010,
    0x00000013, 0x00000012, 0x00050051, 0x00000006, 0x00000016, 0x00000013, 0x00000000, 0x00050051,
    0x00000006, 0x00000017, 0x00000013, 0x00000001, 0x00070050, 0x00000007, 0x00000018, 0x00000016,
    0x00000017, 0x00000014, 0x00000015, 0x00050041, 0x00000019, 0x0000001a, 0x0000000d, 0x0000000f,
    0x0003003e, 0x0000001a, 0x00000018, 0x000100fd, 0x00010038,
};

// store.vert
static const uint32_t kStoreVertSpirv[] = {
    0x07230203, 0x00010500, 0x0008000b, 0x00000020, 0x00000000, 0x00020011, 0x00000001, 0x0006000b,
    0x00000001, 0x4c534c47, 0x6474732e, 0x3035342e, 0x00000000, 0x0003000e, 0x00000000, 0x00000001,
    0x0008000f, 0x00000000, 0x00000004, 0x6e69616d, 0x00000000, 0x0000000a, 0x0000000e, 0x00000018,
    0x00030003, 0x00000002, 0x000001c2, 0x00040005, 0x00000004, 0x6e69616d, 0x00000000, 0x00030005,
    0x00000008, 0x0074754f, 0x00050006, 0x00000008, 0x00000000, 0x61746164, 0x00000000, 0x00030005,
    0x0000000a, 0x00000000, 0x00060005, 0x0000000e, 0x565f6c67, 0x65747265, 0x646e4978, 0x00007865,
    0x00060005, 0x00000016, 0x505f6c67, 0x65567265, 0x78657472, 0x00000000, 0x00060006, 0x00000016,
    0x00000000, 0x505f6c67, 0x7469736f, 0x006e6f69, 0x00070006, 0x00000016, 0x00000001, 0x505f6c67,
    0x746e696f, 0x657a6953, 0x00000000, 0x00070006, 0x00000016, 0x00000002, 0x435f6c67, 0x4470696c,
    0x61747369, 0x0065636e, 0x00070006, 0x00000016, 0x00000003, 0x435f6c67, 0x446c6c75, 0x61747369,
    0x0065636e, 0x00030005, 0x00000018, 0x00000000, 0x00040047, 0x00000007, 0x00000006, 0x00000004,
    0x00030047, 0x00000008, 0x00000002, 0x00050048, 0x00000008, 0x00000000, 0x00000023, 0x00000000,
    0x00040047, 0x0000000a, 0x00000021, 0x00000000, 0x00040047, 0x0000000a, 0x00000022, 0x00000000,
    0x00040047, 0x0000000e, 0x0000000b, 0x0000002a, 0x00030047, 0x00000016, 0x00000002, 0x00050048,
    0x00000016, 0x00000000, 0x0000000b, 0x00000000, 0x00050048, 0x00000016, 0x00000001, 0x0000000b,
    0x00000001, 0x00050048, 0x00000016, 0x00000002, 0x0000000b, 0x00000003, 0x00050048, 0x00000016,
    0x00000003, 0x0000000b, 0x00000004, 0x00020013, 0x00000002, 0x00030021, 0x00000003, 0x00000002,
    0x00040015, 0x00000006, 0x00000020, 0x00000000, 0x0003001d, 0x00000007, 0x00000006, 0x0003001e,
    0x00000008, 0x00000007, 0x00040020, 0x00000009, 0x0000000c, 0x00000008, 0x0004003b, 0x00000009,
    0x0000000a, 0x0000000c, 0x00040015, 0x0000000b, 0x00000020, 0x00000001, 0x0004002b, 0x0000000b,
    0x0000000c, 0x00000000, 0x00040020, 0x0000000d, 0x00000001, 0x0000000b, 0x0004003b, 0x0000000d,
    0x0000000e, 0x00000001, 0x0004002b, 0x00000006, 0x00000010, 0x00000001, 0x00040020, 0x00000011,
    0x0000000c, 0x00000006, 0x00030016, 0x00000013, 0x00000020, 0x00040017, 0x00000014, 0x00000013,
    0x00000004, 0x0004001c, 0x00000015, 0x00000013, 0x00000010, 0x0006001e, 0x00000016, 0x00000014,
    0x00000013, 0x00000015, 0x00000015, 0x00040020, 0x00000017, 0x00000003, 0x00000016, 0x0004003b,
    0x00000017, 0x00000018, 0x00000003, 0x0004002b, 0x00000013, 0x0000001b, 0x00000000, 0x0004002b,
    0x00000013, 0x0000001c, 0x3f800000, 0x00040020, 0x0000001e, 0x00000003, 0x00000014, 0x00050036,
    0x00000002, 0x00000004, 0x00000000, 0x00000003, 0x000200f8, 0x00000005, 0x0004003d, 0x0000000b,
    0x0000000f, 0x0000000e, 0x00060041, 0x00000011, 0x00000012, 0x0000000a, 0x0000000c, 0x0000000f,
    0x0003003e, 0x00000012, 0x00000010, 0x0004003d, 0x0000000b, 0x00000019, 0x0000000e, 0x0004006f,
    0x00000013, 0x0000001a, 0x00000019, 0x00070050, 0x00000014, 0x0000001d, 0x0000001a, 0x0000001b,
    0x0000001b, 0x0000001c, 0x00050041, 0x0000001e, 0x0000001f, 0x00000018, 0x0000000c, 0x0003003e,
    0x0000001f, 0x0000001d, 0x000100fd, 0x00010038,
};

// ---- helpers --------------------------------------------------------------------------------------

static std::vector<u32> ReadSpirv(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    std::vector<char> raw((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    std::vector<u32> spirv(raw.size() / 4);
    std::memcpy(spirv.data(), raw.data(), spirv.size() * 4);
    return spirv;
}

static void WriteSpirv(const fs::path& path, const std::vector<u32>& spirv) {
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(spirv.data()), spirv.size() * 4);
}

// ---- (a) GPU correctness ----------------------------------------------------------------------------

namespace {

constexpr int kSize = 64;

struct Pixels {
    std::vector<float> data;  // RGBA32F
    const float* At(int x, int y) const {
        return &data[(size_t(y) * kSize + x) * 4];
    }
};

/// Draws `vertex_count` MSL vertices from `vertex_start` with the two functions into a cleared
/// (-999) RGBA32F target and reads it back.
bool Render(Metal::Device& device, id<MTLFunction> vs, id<MTLFunction> fs, u32 vertex_start,
            u32 vertex_count, Pixels& pixels) {
    id<MTLDevice> mtl = device.GetImpl().device;
    MTLRenderPipelineDescriptor* desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = vs;
    desc.fragmentFunction = fs;
    desc.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    NSError* error = nil;
    id<MTLRenderPipelineState> pso = [mtl newRenderPipelineStateWithDescriptor:desc error:&error];
    if (!pso) {
        std::printf("pipeline: %s\n", error.localizedDescription.UTF8String);
        return false;
    }
    MTLTextureDescriptor* td =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
                                                           width:kSize
                                                          height:kSize
                                                       mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeShared;
    id<MTLTexture> target = [mtl newTextureWithDescriptor:td];
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = target;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(-999, -999, -999, -999);

    id<MTLCommandBuffer> cb = [device.GetImpl().queue commandBuffer];
    id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:pass];
    [enc setRenderPipelineState:pso];
    [enc setCullMode:MTLCullModeNone];
    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:vertex_start vertexCount:vertex_count];
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    if (cb.error) {
        std::printf("command buffer: %s\n", cb.error.localizedDescription.UTF8String);
        return false;
    }
    pixels.data.assign(size_t(kSize) * kSize * 4, 0.0f);
    [target getBytes:pixels.data.data()
         bytesPerRow:kSize * 16
          fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
         mipmapLevel:0];
    return true;
}

/// The rect.vert data: positions, colours and the index of the corner within each rectangle.
const float kPos[9][2] = {{-0.75f, -0.75f}, {-0.25f, -0.75f}, {-0.75f, -0.25f},
                          {0.25f, 0.25f},   {0.75f, 0.25f},   {0.75f, 0.75f},
                          {-0.75f, 0.5f},   {-0.25f, 0.875f}, {-0.25f, 0.5f}};
const float kColor[9][3] = {{0.25f, 0.0f, 0.0f}, {0.75f, 0.0f, 0.25f}, {0.0f, 0.5f, 0.5f},
                            {1.0f, 0.5f, 0.0f},  {0.0f, 1.0f, 0.5f},   {0.5f, 0.0f, 1.0f},
                            {0.1f, 0.2f, 0.3f},  {0.9f, 0.8f, 0.7f},   {0.0f, 1.0f, 0.0f}};
const int kCorner[3] = {0, 1, 2};

struct Check {
    long covered = 0, inside = 0, bad = 0, stray = 0;
    double max_error = 0;
};

/// Checks `pixels` for the rectangles [first_rect, first_rect + count) of rect.vert's three: they
/// are fully covered with the extrapolated colours and the corner's vertex index (the flat
/// output), and nothing else is touched (the shader indexes its arrays with gl_VertexIndex, which
/// counts from the draw's first vertex, so rectangle r has the corner index 3 r + kCorner[r]).
Check CheckRects(const Pixels& pixels, int first_rect, int count) {
    Check check;
    for (int py = 0; py < kSize; ++py) {
        for (int px = 0; px < kSize; ++px) {
            const float x = (px + 0.5f) / (kSize / 2) - 1.0f;
            const float y = 1.0f - (py + 0.5f) / (kSize / 2);
            const float* got = pixels.At(px, py);
            bool in_any = false;
            for (int r = 0; r < 3; ++r) {
                const int c = 3 * r + kCorner[r];
                const int a = 3 * r + (kCorner[r] + 1) % 3;
                const int b = 3 * r + (kCorner[r] + 2) % 3;
                const float ax = kPos[a][0] - kPos[c][0], ay = kPos[a][1] - kPos[c][1];
                const float bx = kPos[b][0] - kPos[c][0], by = kPos[b][1] - kPos[c][1];
                const float dx = x - kPos[c][0], dy = y - kPos[c][1];
                const float s = (dx * ax + dy * ay) / (ax * ax + ay * ay);
                const float t = (dx * bx + dy * by) / (bx * bx + by * by);
                if (s < 0 || s > 1 || t < 0 || t > 1) {
                    continue;
                }
                in_any = true;
                if (r < first_rect || r >= first_rect + count) {
                    if (got[0] != -999.0f) {
                        ++check.stray;  // a rectangle that was not drawn
                    }
                    continue;
                }
                ++check.inside;
                if (got[0] == -999.0f) {
                    continue;  // not covered
                }
                ++check.covered;
                float want[4];
                for (int ch = 0; ch < 3; ++ch) {
                    want[ch] = kColor[c][ch] + s * (kColor[a][ch] - kColor[c][ch]) +
                               t * (kColor[b][ch] - kColor[c][ch]);
                }
                want[3] = static_cast<float>(c);
                for (int ch = 0; ch < 4; ++ch) {
                    const double error = std::fabs(double(got[ch]) - double(want[ch]));
                    check.max_error = std::fmax(check.max_error, error);
                    if (!(error <= 1.0 / 255.0)) {
                        ++check.bad;
                    }
                }
            }
            if (!in_any && got[0] != -999.0f) {
                ++check.stray;
            }
        }
    }
    return check;
}

void GpuCases(Metal::Device& device, Metal::ShaderCompiler& compiler, const fs::path& dump) {
    std::printf("== GPU correctness (%dx%d)\n", kSize, kSize);
    const std::span<const u32> vertex{kRectVertSpirv};
    const Metal::RectListRewrite rewrite = Metal::RewriteRectListVertex(vertex);
    std::printf("-- rect.vert: rewrite %s (%u output leaves)\n",
                rewrite.ok ? "ok" : rewrite.error.c_str(), rewrite.leaf_outputs);
    if (!rewrite.ok) {
        Fail("rewrite of rect.vert");
        return;
    }
    if (!dump.empty()) {
        WriteSpirv(dump / "rect.vert.rewritten.spv", rewrite.spirv);
    }
    Metal::CompileResult vs = compiler.Compile(rewrite.spirv, 0x7e570001, "rect_vs");
    Metal::CompileResult fs = compiler.Compile(kRectFragSpirv, 0x7e570002, "rect_fs");
    if (vs.status != CompileStatus::Ok || fs.status != CompileStatus::Ok) {
        std::printf("   compile: vs %s, fs %s\n", vs.error.c_str(), fs.error.c_str());
        Fail("compile");
        return;
    }
    struct Run {
        const char* name;
        u32 first_vertex;  // guest vertices
        u32 num_vertices;
        int first_rect, count;
    };
    const Run runs[] = {
        {"three rectangles", 0, 9, 0, 3},
        {"first_vertex 3 (two rectangles)", 3, 6, 1, 2},
        {"first_vertex 6 (one rectangle)", 6, 3, 2, 1},
    };
    for (const Run& run : runs) {
        Pixels pixels;
        if (!Render(device, (__bridge id<MTLFunction>)vs.shader->function,
                    (__bridge id<MTLFunction>)fs.shader->function, 2 * run.first_vertex,
                    run.num_vertices / 3 * 6, pixels)) {
            Fail("render");
            continue;
        }
        const Check check = CheckRects(pixels, run.first_rect, run.count);
        std::printf("   %s: %ld/%ld pixels covered, max error %.2e, %ld bad, %ld stray\n", run.name,
                    check.covered, check.inside, check.max_error, check.bad, check.stray);
        if (check.covered != check.inside || check.inside == 0 || check.bad != 0 ||
            check.stray != 0) {
            Fail(run.name);
        }
    }
}

// ---- (b) refused shapes -----------------------------------------------------------------------------

void RefusedShapes() {
    std::printf("== shapes the rewrite refuses\n");
    struct Case {
        const char* name;
        std::span<const u32> spirv;
        const char* expect;  // substring of the reason
    };
    const Case cases[] = {
        {"vertex attribute input", kAttributeVertSpirv, "vertex attribute"},
        {"storage buffer store", kStoreVertSpirv, "storage class"},
        {"fragment shader", kRectFragSpirv, "not a vertex shader"},
    };
    for (const Case& c : cases) {
        const Metal::RectListRewrite rewrite = Metal::RewriteRectListVertex(c.spirv);
        const bool good = !rewrite.ok && rewrite.error.find(c.expect) != std::string::npos;
        std::printf("-- %s: %s\n", c.name, rewrite.ok ? "rewritten?!" : rewrite.error.c_str());
        if (!good) {
            Fail(c.name);
        }
    }
}

// ---- (c) the game's shaders -------------------------------------------------------------------------

/// The SPIR-V files (<program hash>_<permutation>.spv) of the vertex shaders of the pipelines drawn
/// with RectList, from the cache's pipeline keys. A key names a stage by its permutation hash; the
/// shader's meta file (<perm hash>.meta) starts with versions, that hash and the permutation index,
/// and holds the program hash (the SPIR-V file name) somewhere in the serialized Info: it is found
/// by matching the 32-bit words against the file names.
std::set<std::string> RectListVertexFiles(const fs::path& dir, size_t& keys, size_t& rect_keys,
                                          size_t& unresolved) {
    std::set<u32> program_hashes;
    for (const auto& entry : fs::directory_iterator(dir)) {
        const std::string name = entry.path().filename().string();
        if (entry.path().extension() == ".spv" && name.size() > 12) {
            program_hashes.insert(static_cast<u32>(std::strtoull(name.c_str() + 2, nullptr, 16)));
        }
    }
    std::set<u64> hashes;
    for (const auto& entry : fs::directory_iterator(dir)) {
        const std::string name = entry.path().filename().string();
        if (name.rfind("g_", 0) != 0 || entry.path().extension() != ".key") {
            continue;
        }
        const std::vector<u32> words = ReadSpirv(entry.path());  // raw bytes
        const size_t header = 8;
        if (words.size() * 4 < header + sizeof(VideoCore::GraphicsPipelineKey) || words[0] != 5 ||
            words[1] != 0) {
            continue;
        }
        VideoCore::GraphicsPipelineKey key;
        std::memcpy(&key, reinterpret_cast<const char*>(words.data()) + header, sizeof(key));
        ++keys;
        if (key.prim_type == AmdGpu::PrimitiveType::RectList) {
            ++rect_keys;
            hashes.insert(key.stage_hashes[u32(Shader::SwStage::Vertex)]);
        }
    }
    std::set<std::string> files;
    for (const u64 hash : hashes) {
        char name[40];
        std::snprintf(name, sizeof(name), "0x%016llx.meta", static_cast<unsigned long long>(hash));
        const std::vector<u32> meta = ReadSpirv(dir / name);
        bool found = false;
        if (meta.size() > 8) {
            const u64 perm = meta[4] | (u64(meta[5]) << 32);
            const auto* bytes = reinterpret_cast<const unsigned char*>(meta.data());
            for (size_t offset = 24; offset + 4 <= meta.size() * 4 && !found; ++offset) {
                u32 candidate;
                std::memcpy(&candidate, bytes + offset, 4);
                if (!program_hashes.count(candidate)) {
                    continue;
                }
                char file[48];
                std::snprintf(file, sizeof(file), "0x%016x_%llu.spv", candidate,
                              static_cast<unsigned long long>(perm));
                if (fs::exists(dir / file)) {
                    files.insert(file);
                    found = true;
                }
            }
        }
        unresolved += !found;
    }
    return files;
}

struct Totals {
    int shaders = 0, rewritten = 0, compiled = 0;
    std::map<std::string, int> reasons;
};

/// Rewrites and compiles one SPIR-V file; true when both worked.
bool Sweep(Metal::ShaderCompiler& compiler, const fs::path& path, Totals& totals, bool verbose,
           const fs::path& dump) {
    const std::vector<u32> spirv = ReadSpirv(path);
    // Only vertex shaders (the first OpEntryPoint's execution model).
    for (size_t i = 5; i < spirv.size();) {
        const u32 len = spirv[i] >> 16, op = spirv[i] & 0xffff;
        if (len == 0) {
            return false;
        }
        if (op == 15) {  // OpEntryPoint
            if (spirv[i + 1] != 0) {
                return false;
            }
            break;
        }
        i += len;
    }
    ++totals.shaders;
    const std::string name = path.stem().string();
    const Metal::RectListRewrite rewrite = Metal::RewriteRectListVertex(spirv);
    if (!rewrite.ok) {
        ++totals.reasons[rewrite.error];
        if (verbose) {
            std::printf("  %s: rewrite failed: %s\n", name.c_str(), rewrite.error.c_str());
        }
        return false;
    }
    ++totals.rewritten;
    if (!dump.empty()) {
        WriteSpirv(dump / (name + ".rewritten.spv"), rewrite.spirv);
    }
    const u64 hash = std::strtoull(name.c_str() + 2, nullptr, 16) ^ 0x5265637400000000ull;
    const Metal::CompileResult result = compiler.Compile(rewrite.spirv, hash, name + "_rect");
    if (result.status != CompileStatus::Ok) {
        ++totals.reasons["compile: " + result.error];
        if (verbose) {
            std::printf("  %s: compile failed: %s\n", name.c_str(), result.error.c_str());
        }
        return false;
    }
    ++totals.compiled;
    if (verbose) {
        std::printf("  %s: ok (%u leaves)\n", name.c_str(), rewrite.leaf_outputs);
    }
    return true;
}

void GameShaders(Metal::ShaderCompiler& compiler, const fs::path& dir, const fs::path& dump) {
    std::printf("== game shaders (%s)\n", dir.c_str());
    if (!fs::exists(dir)) {
        std::printf("  not found, skipped\n");
        return;
    }
    size_t keys = 0, rect_keys = 0, unresolved = 0;
    const std::set<std::string> files = RectListVertexFiles(dir, keys, rect_keys, unresolved);
    std::printf("  %zu graphics pipeline keys, %zu with RectList, %zu distinct vertex shader "
                "modules (%zu not found in the cache)\n",
                keys, rect_keys, files.size(), unresolved);
    Totals rect;
    std::set<std::string> sweep_names = files;
    for (const std::string& file : files) {
        Sweep(compiler, dir / file, rect, true, dump);
    }
    std::printf("  RectList vertex shaders: %d modules, %d rewritten, %d compiled\n", rect.shaders,
                rect.rewritten, rect.compiled);
    for (const auto& [reason, count] : rect.reasons) {
        std::printf("    %d x %s\n", count, reason.c_str());
    }
    if (rect_keys != 0 && (unresolved != 0 || rect.shaders == 0 || rect.compiled != rect.shaders)) {
        Fail("RectList vertex shaders of the cache");
    }

    // For the statistics: every other vertex shader of the cache.
    Totals all;
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (entry.path().extension() == ".spv" && !sweep_names.count(entry.path().filename().string())) {
            Sweep(compiler, entry.path(), all, false, {});
        }
    }
    std::printf("  all other vertex shaders: %d modules, %d rewritten, %d compiled\n", all.shaders,
                all.rewritten, all.compiled);
    for (const auto& [reason, count] : all.reasons) {
        std::printf("    %d x %s\n", count, reason.c_str());
    }
}

} // namespace

int main(int argc, char** argv) {
    fs::path dir, dump;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--dump") && i + 1 < argc) {
            dump = argv[++i];
        } else if (argv[i][0] != '-' && dir.empty()) {
            dir = argv[i];
        } else {
            std::fprintf(stderr, "usage: %s [<shader cache dir>] [--dump <dir>]\n", argv[0]);
            return 2;
        }
    }
    if (dir.empty()) {
        const char* home = std::getenv("HOME");
        dir = fs::path{home ? home : "."} /
              "Games/Bloodborne/bench/testuser/cache/CUSA03173-metal";
    }
    if (!dump.empty()) {
        fs::create_directories(dump);
    }
    const fs::path cache = fs::temp_directory_path() / "bbport-mtl-rectlist-test-cache";
    std::error_code ec;
    fs::remove_all(cache, ec);  // always convert: the rewrite is what is under test

    Metal::Device device;
    Metal::ShaderCompiler compiler{device, cache};
    GpuCases(device, compiler, dump);
    RefusedShapes();
    GameShaders(compiler, dir, dump);

    std::printf("\n%s\n", g_failures ? "FAIL" : "PASS");
    return g_failures ? 1 : 0;
}
