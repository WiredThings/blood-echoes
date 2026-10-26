// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the Metal geometry-shader path (renderer_metal/mtl_geometry, mtl_spirv_geometry,
// mtl_shader's GeometryKernel variant and PsoCache::GetGeometryPipeline, plan P7.1): the export
// shader as a compute kernel, the geometry shader rewritten into a compute kernel, a generated
// pass-through vertex function and an indexed strip draw, through the same compile, pipeline and
// encode functions the rasterizer uses.
//   (a) the rewrite: shape analysis, refusals (Invocations 2, OutputPoints, adjacency input);
//   (b) the export layout reflected from the vertex kernel, the geometry kernel's records and
//       index list read back from the scratch buffer (restart markers, unused slots, gl_PrimitiveID
//       counting from first_prim);
//   (c) rendered draws (points -> two triangle-strip quads each, EndPrimitive between the quads):
//       direct, first vertex, uint16 / uint32 indices, several chunks;
//   (d) the pass-through function (depth conversion text, cache).
// GLSL shaders (glslang, embedded below). MTL_DEBUG_LAYER is switched on unless the environment
// sets it. Needs a Metal device, no window or game.
//
//   mtl-geometry-test [--game-dumps <dir>]
//
// --game-dumps (local check, not run by default): rewrites and compiles the geometry shaders
// (gs_*.spv) of a shader dump directory against the export shader es_*.spv there and builds the
// pass-through function for its fragment shaders (fs_*.spv); prints max vertices and record stride.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <span>
#include <string>
#include <vector>

#include <fmt/format.h>

#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_geometry.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/renderer_metal/mtl_raster_util.h"
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_spirv_geometry.h"
#include "video_core/renderer_metal/vk_to_mtl.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"

// common/assert.cpp pulls in the emulator; a failed ASSERT here just aborts.
void assert_fail_impl() {
    std::abort();
}
[[noreturn]] void unreachable_impl() {
    std::abort();
}

namespace fs = std::filesystem;
using Metal::CompileStatus;
using Metal::CompileVariant;

static int g_failures = 0;
static bool g_debug_view = false; // --gs-debug: BB_GS_DEBUG=1, the magenta view

static void Fail(const std::string& what) {
    std::printf("FAIL %s\n", what.c_str());
    ++g_failures;
}

// ---- shaders: glslangValidator -V --target-env vulkan1.2 ----------------------------------------

// es.vert: location 0 colour, location 2 the vertex index, vec2 + vec4 attributes
static const uint32_t kEsSpirv[] = {
    0x07230203,0x00010500,0x0008000b,0x00000025,0x00000000,0x00020011,0x00000001,0x0006000b,
    0x00000001,0x4c534c47,0x6474732e,0x3035342e,0x00000000,0x0003000e,0x00000000,0x00000001,
    0x000b000f,0x00000000,0x00000004,0x6e69616d,0x00000000,0x0000000d,0x00000012,0x0000001b,
    0x0000001d,0x0000001f,0x00000021,0x00030003,0x00000002,0x000001c2,0x00040005,0x00000004,
    0x6e69616d,0x00000000,0x00060005,0x0000000b,0x505f6c67,0x65567265,0x78657472,0x00000000,
    0x00060006,0x0000000b,0x00000000,0x505f6c67,0x7469736f,0x006e6f69,0x00070006,0x0000000b,
    0x00000001,0x505f6c67,0x746e696f,0x657a6953,0x00000000,0x00070006,0x0000000b,0x00000002,
    0x435f6c67,0x4470696c,0x61747369,0x0065636e,0x00070006,0x0000000b,0x00000003,0x435f6c67,
    0x446c6c75,0x61747369,0x0065636e,0x00030005,0x0000000d,0x00000000,0x00040005,0x00000012,
    0x6f705f61,0x00000073,0x00040005,0x0000001b,0x6f635f76,0x00726f6c,0x00040005,0x0000001d,
    0x6f635f61,0x00726f6c,0x00040005,0x0000001f,0x78655f76,0x00617274,0x00060005,0x00000021,
    0x565f6c67,0x65747265,0x646e4978,0x00007865,0x00030047,0x0000000b,0x00000002,0x00050048,
    0x0000000b,0x00000000,0x0000000b,0x00000000,0x00050048,0x0000000b,0x00000001,0x0000000b,
    0x00000001,0x00050048,0x0000000b,0x00000002,0x0000000b,0x00000003,0x00050048,0x0000000b,
    0x00000003,0x0000000b,0x00000004,0x00040047,0x00000012,0x0000001e,0x00000000,0x00040047,
    0x0000001b,0x0000001e,0x00000000,0x00040047,0x0000001d,0x0000001e,0x00000001,0x00040047,
    0x0000001f,0x0000001e,0x00000002,0x00040047,0x00000021,0x0000000b,0x0000002a,0x00020013,
    0x00000002,0x00030021,0x00000003,0x00000002,0x00030016,0x00000006,0x00000020,0x00040017,
    0x00000007,0x00000006,0x00000004,0x00040015,0x00000008,0x00000020,0x00000000,0x0004002b,
    0x00000008,0x00000009,0x00000001,0x0004001c,0x0000000a,0x00000006,0x00000009,0x0006001e,
    0x0000000b,0x00000007,0x00000006,0x0000000a,0x0000000a,0x00040020,0x0000000c,0x00000003,
    0x0000000b,0x0004003b,0x0000000c,0x0000000d,0x00000003,0x00040015,0x0000000e,0x00000020,
    0x00000001,0x0004002b,0x0000000e,0x0000000f,0x00000000,0x00040017,0x00000010,0x00000006,
    0x00000002,0x00040020,0x00000011,0x00000001,0x00000010,0x0004003b,0x00000011,0x00000012,
    0x00000001,0x0004002b,0x00000006,0x00000014,0x00000000,0x0004002b,0x00000006,0x00000015,
    0x3f800000,0x00040020,0x00000019,0x00000003,0x00000007,0x0004003b,0x00000019,0x0000001b,
    0x00000003,0x00040020,0x0000001c,0x00000001,0x00000007,0x0004003b,0x0000001c,0x0000001d,
    0x00000001,0x0004003b,0x00000019,0x0000001f,0x00000003,0x00040020,0x00000020,0x00000001,
    0x0000000e,0x0004003b,0x00000020,0x00000021,0x00000001,0x00050036,0x00000002,0x00000004,
    0x00000000,0x00000003,0x000200f8,0x00000005,0x0004003d,0x00000010,0x00000013,0x00000012,
    0x00050051,0x00000006,0x00000016,0x00000013,0x00000000,0x00050051,0x00000006,0x00000017,
    0x00000013,0x00000001,0x00070050,0x00000007,0x00000018,0x00000016,0x00000017,0x00000014,
    0x00000015,0x00050041,0x00000019,0x0000001a,0x0000000d,0x0000000f,0x0003003e,0x0000001a,
    0x00000018,0x0004003d,0x00000007,0x0000001e,0x0000001d,0x0003003e,0x0000001b,0x0000001e,
    0x0004003d,0x0000000e,0x00000022,0x00000021,0x0004006f,0x00000006,0x00000023,0x00000022,
    0x00070050,0x00000007,0x00000024,0x00000023,0x00000014,0x00000014,0x00000014,0x0003003e,
    0x0000001f,0x00000024,0x000100fd,0x00010038
};

// gs.geom: points in, two quads (4 vertices each, EndPrimitive between them) out, max_vertices 8;
// inputs 0 and 2, outputs 0 and 1
static const uint32_t kGsSpirv[] = {
    0x07230203,0x00010500,0x0008000b,0x00000072,0x00000000,0x00020011,0x00000002,0x0006000b,
    0x00000001,0x4c534c47,0x6474732e,0x3035342e,0x00000000,0x0003000e,0x00000000,0x00000001,
    0x000c000f,0x00000003,0x00000004,0x6e69616d,0x00000000,0x00000016,0x0000001c,0x0000002a,
    0x0000002d,0x00000035,0x00000037,0x0000003b,0x00030010,0x00000004,0x00000013,0x00040010,
    0x00000004,0x00000000,0x00000001,0x00030010,0x00000004,0x0000001d,0x00040010,0x00000004,
    0x0000001a,0x00000008,0x00030003,0x00000002,0x000001c2,0x00040005,0x00000004,0x6e69616d,
    0x00000000,0x00060005,0x0000000e,0x74696d65,0x32667628,0x3b31663b,0x003b3166,0x00040005,
    0x0000000b,0x7366666f,0x00007465,0x00040005,0x0000000c,0x64617571,0x00000000,0x00040005,
    0x0000000d,0x6e726f63,0x00007265,0x00060005,0x00000014,0x505f6c67,0x65567265,0x78657472,
    0x00000000,0x00060006,0x00000014,0x00000000,0x505f6c67,0x7469736f,0x006e6f69,0x00070006,
    0x00000014,0x00000001,0x505f6c67,0x746e696f,0x657a6953,0x00000000,0x00070006,0x00000014,
    0x00000002,0x435f6c67,0x4470696c,0x61747369,0x0065636e,0x00070006,0x00000014,0x00000003,
    0x435f6c67,0x446c6c75,0x61747369,0x0065636e,0x00030005,0x00000016,0x00000000,0x00060005,
    0x00000019,0x505f6c67,0x65567265,0x78657472,0x00000000,0x00060006,0x00000019,0x00000000,
    0x505f6c67,0x7469736f,0x006e6f69,0x00070006,0x00000019,0x00000001,0x505f6c67,0x746e696f,
    0x657a6953,0x00000000,0x00070006,0x00000019,0x00000002,0x435f6c67,0x4470696c,0x61747369,
    0x0065636e,0x00070006,0x00000019,0x00000003,0x435f6c67,0x446c6c75,0x61747369,0x0065636e,
    0x00040005,0x0000001c,0x695f6c67,0x0000006e,0x00050005,0x0000002a,0x5f74756f,0x6f6c6f63,
    0x00000072,0x00050005,0x0000002d,0x635f7367,0x726f6c6f,0x00000000,0x00050005,0x00000035,
    0x5f74756f,0x6f666e69,0x00000000,0x00070005,0x00000037,0x505f6c67,0x696d6972,0x65766974,
    0x6e494449,0x00000000,0x00050005,0x0000003b,0x655f7367,0x61727478,0x00000000,0x00040005,
    0x00000044,0x61726170,0x0000006d,0x00040005,0x00000045,0x61726170,0x0000006d,0x00040005,
    0x00000046,0x61726170,0x0000006d,0x00040005,0x0000004a,0x61726170,0x0000006d,0x00040005,
    0x0000004b,0x61726170,0x0000006d,0x00040005,0x0000004c,0x61726170,0x0000006d,0x00040005,
    0x00000050,0x61726170,0x0000006d,0x00040005,0x00000051,0x61726170,0x0000006d,0x00040005,
    0x00000052,0x61726170,0x0000006d,0x00040005,0x00000056,0x61726170,0x0000006d,0x00040005,
    0x00000057,0x61726170,0x0000006d,0x00040005,0x00000058,0x61726170,0x0000006d,0x00040005,
    0x0000005d,0x61726170,0x0000006d,0x00040005,0x0000005e,0x61726170,0x0000006d,0x00040005,
    0x0000005f,0x61726170,0x0000006d,0x00040005,0x00000063,0x61726170,0x0000006d,0x00040005,
    0x00000064,0x61726170,0x0000006d,0x00040005,0x00000065,0x61726170,0x0000006d,0x00040005,
    0x00000069,0x61726170,0x0000006d,0x00040005,0x0000006a,0x61726170,0x0000006d,0x00040005,
    0x0000006b,0x61726170,0x0000006d,0x00040005,0x0000006e,0x61726170,0x0000006d,0x00040005,
    0x0000006f,0x61726170,0x0000006d,0x00040005,0x00000070,0x61726170,0x0000006d,0x00030047,
    0x00000014,0x00000002,0x00050048,0x00000014,0x00000000,0x0000000b,0x00000000,0x00050048,
    0x00000014,0x00000001,0x0000000b,0x00000001,0x00050048,0x00000014,0x00000002,0x0000000b,
    0x00000003,0x00050048,0x00000014,0x00000003,0x0000000b,0x00000004,0x00030047,0x00000019,
    0x00000002,0x00050048,0x00000019,0x00000000,0x0000000b,0x00000000,0x00050048,0x00000019,
    0x00000001,0x0000000b,0x00000001,0x00050048,0x00000019,0x00000002,0x0000000b,0x00000003,
    0x00050048,0x00000019,0x00000003,0x0000000b,0x00000004,0x00040047,0x0000002a,0x0000001e,
    0x00000000,0x00040047,0x0000002d,0x0000001e,0x00000000,0x00040047,0x00000035,0x0000001e,
    0x00000001,0x00040047,0x00000037,0x0000000b,0x00000007,0x00040047,0x0000003b,0x0000001e,
    0x00000002,0x00020013,0x00000002,0x00030021,0x00000003,0x00000002,0x00030016,0x00000006,
    0x00000020,0x00040017,0x00000007,0x00000006,0x00000002,0x00040020,0x00000008,0x00000007,
    0x00000007,0x00040020,0x00000009,0x00000007,0x00000006,0x00060021,0x0000000a,0x00000002,
    0x00000008,0x00000009,0x00000009,0x00040017,0x00000010,0x00000006,0x00000004,0x00040015,
    0x00000011,0x00000020,0x00000000,0x0004002b,0x00000011,0x00000012,0x00000001,0x0004001c,
    0x00000013,0x00000006,0x00000012,0x0006001e,0x00000014,0x00000010,0x00000006,0x00000013,
    0x00000013,0x00040020,0x00000015,0x00000003,0x00000014,0x0004003b,0x00000015,0x00000016,
    0x00000003,0x00040015,0x00000017,0x00000020,0x00000001,0x0004002b,0x00000017,0x00000018,
    0x00000000,0x0006001e,0x00000019,0x00000010,0x00000006,0x00000013,0x00000013,0x0004001c,
    0x0000001a,0x00000019,0x00000012,0x00040020,0x0000001b,0x00000001,0x0000001a,0x0004003b,
    0x0000001b,0x0000001c,0x00000001,0x00040020,0x0000001d,0x00000001,0x00000010,0x0004002b,
    0x00000006,0x00000023,0x00000000,0x0004002b,0x00000006,0x00000024,0x3f800000,0x00040020,
    0x00000028,0x00000003,0x00000010,0x0004003b,0x00000028,0x0000002a,0x00000003,0x0004001c,
    0x0000002b,0x00000010,0x00000012,0x00040020,0x0000002c,0x00000001,0x0000002b,0x0004003b,
    0x0000002c,0x0000002d,0x00000001,0x0004002b,0x00000006,0x00000030,0x3e800000,0x0004003b,
    0x00000028,0x00000035,0x00000003,0x00040020,0x00000036,0x00000001,0x00000017,0x0004003b,
    0x00000036,0x00000037,0x00000001,0x0004003b,0x0000002c,0x0000003b,0x00000001,0x0004002b,
    0x00000011,0x0000003c,0x00000000,0x00040020,0x0000003d,0x00000001,0x00000006,0x0004002b,
    0x00000006,0x00000042,0xbdf5c28f,0x0005002c,0x00000007,0x00000043,0x00000042,0x00000042,
    0x0004002b,0x00000006,0x00000048,0x3df5c28f,0x0005002c,0x00000007,0x00000049,0x00000042,
    0x00000048,0x0005002c,0x00000007,0x0000004e,0x00000048,0x00000042,0x0004002b,0x00000006,
    0x0000004f,0x40000000,0x0005002c,0x00000007,0x00000054,0x00000048,0x00000048,0x0004002b,
    0x00000006,0x00000055,0x40400000,0x0004002b,0x00000006,0x0000005a,0xbd4ccccd,0x0004002b,
    0x00000006,0x0000005b,0x3eb33333,0x0005002c,0x00000007,0x0000005c,0x0000005a,0x0000005b,
    0x0004002b,0x00000006,0x00000061,0x3f0ccccd,0x0005002c,0x00000007,0x00000062,0x0000005a,
    0x00000061,0x0004002b,0x00000006,0x00000067,0x3d4ccccd,0x0005002c,0x00000007,0x00000068,
    0x00000067,0x0000005b,0x0005002c,0x00000007,0x0000006d,0x00000067,0x00000061,0x00050036,
    0x00000002,0x00000004,0x00000000,0x00000003,0x000200f8,0x00000005,0x0004003b,0x00000008,
    0x00000044,0x00000007,0x0004003b,0x00000009,0x00000045,0x00000007,0x0004003b,0x00000009,
    0x00000046,0x00000007,0x0004003b,0x00000008,0x0000004a,0x00000007,0x0004003b,0x00000009,
    0x0000004b,0x00000007,0x0004003b,0x00000009,0x0000004c,0x00000007,0x0004003b,0x00000008,
    0x00000050,0x00000007,0x0004003b,0x00000009,0x00000051,0x00000007,0x0004003b,0x00000009,
    0x00000052,0x00000007,0x0004003b,0x00000008,0x00000056,0x00000007,0x0004003b,0x00000009,
    0x00000057,0x00000007,0x0004003b,0x00000009,0x00000058,0x00000007,0x0004003b,0x00000008,
    0x0000005d,0x00000007,0x0004003b,0x00000009,0x0000005e,0x00000007,0x0004003b,0x00000009,
    0x0000005f,0x00000007,0x0004003b,0x00000008,0x00000063,0x00000007,0x0004003b,0x00000009,
    0x00000064,0x00000007,0x0004003b,0x00000009,0x00000065,0x00000007,0x0004003b,0x00000008,
    0x00000069,0x00000007,0x0004003b,0x00000009,0x0000006a,0x00000007,0x0004003b,0x00000009,
    0x0000006b,0x00000007,0x0004003b,0x00000008,0x0000006e,0x00000007,0x0004003b,0x00000009,
    0x0000006f,0x00000007,0x0004003b,0x00000009,0x00000070,0x00000007,0x0003003e,0x00000044,
    0x00000043,0x0003003e,0x00000045,0x00000023,0x0003003e,0x00000046,0x00000023,0x00070039,
    0x00000002,0x00000047,0x0000000e,0x00000044,0x00000045,0x00000046,0x0003003e,0x0000004a,
    0x00000049,0x0003003e,0x0000004b,0x00000023,0x0003003e,0x0000004c,0x00000024,0x00070039,
    0x00000002,0x0000004d,0x0000000e,0x0000004a,0x0000004b,0x0000004c,0x0003003e,0x00000050,
    0x0000004e,0x0003003e,0x00000051,0x00000023,0x0003003e,0x00000052,0x0000004f,0x00070039,
    0x00000002,0x00000053,0x0000000e,0x00000050,0x00000051,0x00000052,0x0003003e,0x00000056,
    0x00000054,0x0003003e,0x00000057,0x00000023,0x0003003e,0x00000058,0x00000055,0x00070039,
    0x00000002,0x00000059,0x0000000e,0x00000056,0x00000057,0x00000058,0x000100db,0x0003003e,
    0x0000005d,0x0000005c,0x0003003e,0x0000005e,0x00000024,0x0003003e,0x0000005f,0x00000023,
    0x00070039,0x00000002,0x00000060,0x0000000e,0x0000005d,0x0000005e,0x0000005f,0x0003003e,
    0x00000063,0x00000062,0x0003003e,0x00000064,0x00000024,0x0003003e,0x00000065,0x00000024,
    0x00070039,0x00000002,0x00000066,0x0000000e,0x00000063,0x00000064,0x00000065,0x0003003e,
    0x00000069,0x00000068,0x0003003e,0x0000006a,0x00000024,0x0003003e,0x0000006b,0x0000004f,
    0x00070039,0x00000002,0x0000006c,0x0000000e,0x00000069,0x0000006a,0x0000006b,0x0003003e,
    0x0000006e,0x0000006d,0x0003003e,0x0000006f,0x00000024,0x0003003e,0x00000070,0x00000055,
    0x00070039,0x00000002,0x00000071,0x0000000e,0x0000006e,0x0000006f,0x00000070,0x000100fd,
    0x00010038,0x00050036,0x00000002,0x0000000e,0x00000000,0x0000000a,0x00030037,0x00000008,
    0x0000000b,0x00030037,0x00000009,0x0000000c,0x00030037,0x00000009,0x0000000d,0x000200f8,
    0x0000000f,0x00060041,0x0000001d,0x0000001e,0x0000001c,0x00000018,0x00000018,0x0004003d,
    0x00000010,0x0000001f,0x0000001e,0x0007004f,0x00000007,0x00000020,0x0000001f,0x0000001f,
    0x00000000,0x00000001,0x0004003d,0x00000007,0x00000021,0x0000000b,0x00050081,0x00000007,
    0x00000022,0x00000020,0x00000021,0x00050051,0x00000006,0x00000025,0x00000022,0x00000000,
    0x00050051,0x00000006,0x00000026,0x00000022,0x00000001,0x00070050,0x00000010,0x00000027,
    0x00000025,0x00000026,0x00000023,0x00000024,0x00050041,0x00000028,0x00000029,0x00000016,
    0x00000018,0x0003003e,0x00000029,0x00000027,0x00050041,0x0000001d,0x0000002e,0x0000002d,
    0x00000018,0x0004003d,0x00000010,0x0000002f,0x0000002e,0x0004003d,0x00000006,0x00000031,
    0x0000000c,0x00050085,0x00000006,0x00000032,0x00000030,0x00000031,0x00050083,0x00000006,
    0x00000033,0x00000024,0x00000032,0x0005008e,0x00000010,0x00000034,0x0000002f,0x00000033,
    0x0003003e,0x0000002a,0x00000034,0x0004003d,0x00000017,0x00000038,0x00000037,0x0004006f,
    0x00000006,0x00000039,0x00000038,0x0004003d,0x00000006,0x0000003a,0x0000000c,0x00060041,
    0x0000003d,0x0000003e,0x0000003b,0x00000018,0x0000003c,0x0004003d,0x00000006,0x0000003f,
    0x0000003e,0x0004003d,0x00000006,0x00000040,0x0000000d,0x00070050,0x00000010,0x00000041,
    0x00000039,0x0000003a,0x0000003f,0x00000040,0x0003003e,0x00000035,0x00000041,0x000100da,
    0x000100fd,0x00010038
};

// fs.frag: inputs 0, 1 and 3 (3 is not written by the geometry shader), two targets
static const uint32_t kFsSpirv[] = {
    0x07230203,0x00010500,0x0008000b,0x00000013,0x00000000,0x00020011,0x00000001,0x0006000b,
    0x00000001,0x4c534c47,0x6474732e,0x3035342e,0x00000000,0x0003000e,0x00000000,0x00000001,
    0x000a000f,0x00000004,0x00000004,0x6e69616d,0x00000000,0x00000009,0x0000000b,0x0000000d,
    0x0000000e,0x00000010,0x00030010,0x00000004,0x00000007,0x00030003,0x00000002,0x000001c2,
    0x00040005,0x00000004,0x6e69616d,0x00000000,0x00040005,0x00000009,0x6f635f6f,0x00726f6c,
    0x00050005,0x0000000b,0x635f6e69,0x726f6c6f,0x00000000,0x00040005,0x0000000d,0x6e695f6f,
    0x00006f66,0x00040005,0x0000000e,0x695f6e69,0x006f666e,0x00060005,0x00000010,0x755f6e69,
    0x6972776e,0x6e657474,0x00000000,0x00040047,0x00000009,0x0000001e,0x00000000,0x00040047,
    0x0000000b,0x0000001e,0x00000000,0x00040047,0x0000000d,0x0000001e,0x00000001,0x00040047,
    0x0000000e,0x0000001e,0x00000001,0x00040047,0x00000010,0x0000001e,0x00000003,0x00020013,
    0x00000002,0x00030021,0x00000003,0x00000002,0x00030016,0x00000006,0x00000020,0x00040017,
    0x00000007,0x00000006,0x00000004,0x00040020,0x00000008,0x00000003,0x00000007,0x0004003b,
    0x00000008,0x00000009,0x00000003,0x00040020,0x0000000a,0x00000001,0x00000007,0x0004003b,
    0x0000000a,0x0000000b,0x00000001,0x0004003b,0x00000008,0x0000000d,0x00000003,0x0004003b,
    0x0000000a,0x0000000e,0x00000001,0x0004003b,0x0000000a,0x00000010,0x00000001,0x00050036,
    0x00000002,0x00000004,0x00000000,0x00000003,0x000200f8,0x00000005,0x0004003d,0x00000007,
    0x0000000c,0x0000000b,0x0003003e,0x00000009,0x0000000c,0x0004003d,0x00000007,0x0000000f,
    0x0000000e,0x0004003d,0x00000007,0x00000011,0x00000010,0x00050081,0x00000007,0x00000012,
    0x0000000f,0x00000011,0x0003003e,0x0000000d,0x00000012,0x000100fd,0x00010038
};

namespace {

constexpr int kSize = 64;
constexpr float kNone = -999.0f;
constexpr double kEdge = 0.75; // pixels around a primitive's edge that are not checked
constexpr u32 kRestart = 0xFFFFFFFFu;

struct Point {
    float x, y;
    float color[4];
};
const Point kPoints[3] = {{-0.6f, -0.7f, {1, 0, 0, 1}},
                          {0.0f, -0.4f, {0, 1, 0, 1}},
                          {0.6f, -0.7f, {0, 0, 1, 1}}};

std::vector<char> ReadAll(const fs::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

std::vector<u32> ReadSpirv(const fs::path& path) {
    const std::vector<char> raw = ReadAll(path);
    std::vector<u32> words(raw.size() / 4);
    std::memcpy(words.data(), raw.data(), words.size() * 4);
    return words;
}

/// A copy of `spirv` with the geometry shader's execution mode `from` (operand `value`, when it
/// has one) replaced: OpExecutionMode is opcode 16.
std::vector<u32> WithMode(std::span<const u32> spirv, u32 from, u32 to, u32 value = ~0u) {
    std::vector<u32> out(spirv.begin(), spirv.end());
    for (size_t i = 5; i < out.size();) {
        const u32 len = out[i] >> 16, op = out[i] & 0xffff;
        if (len == 0) {
            break;
        }
        if (op == 16 && len >= 3 && out[i + 2] == from) {
            out[i + 2] = to;
            if (value != ~0u && len >= 4) {
                out[i + 3] = value;
            }
        }
        i += len;
    }
    return out;
}

Vulkan::GraphicsPipelineKey MakeKey(u64 hash, bool minus_w_to_w = false) {
    Vulkan::GraphicsPipelineKey key;
    key.stage_hashes[u32(Shader::SwStage::Vertex)] = hash;
    key.stage_hashes[u32(Shader::SwStage::Geometry)] = hash + 1;
    key.stage_hashes[u32(Shader::SwStage::Fragment)] = hash + 2;
    key.vertex_buffer_formats[0] = vk::Format::eR32G32Sfloat;
    key.vertex_buffer_formats[1] = vk::Format::eR32G32B32A32Sfloat;
    key.num_color_attachments = 2;
    for (u32 i = 0; i < 2; ++i) {
        key.color_buffers[i].data_format = AmdGpu::DataFormat::Format32_32_32_32;
        key.color_buffers[i].num_format = AmdGpu::NumberFormat::Float;
        key.write_masks[i] = vk::ColorComponentFlagBits::eR | vk::ColorComponentFlagBits::eG |
                             vk::ColorComponentFlagBits::eB | vk::ColorComponentFlagBits::eA;
    }
    key.mrt_mask = 3;
    key.num_samples = 1;
    key.prim_type = AmdGpu::PrimitiveType::PointList;
    key.logic_op = AmdGpu::ColorControl::LogicOp::Copy;
    if (minus_w_to_w) {
        key.clip_space = AmdGpu::ClipSpace::MinusWToW;
    }
    return key;
}

// ---- (a) the rewrite and the compiles -----------------------------------------------------------

struct Stages {
    Metal::CompileResult es[3], gs, fs;
    bool ok{};
};

Stages CompileStages(Metal::ShaderCompiler& compiler, u64 hash) {
    Stages stages;
    stages.ok = true;
    for (u32 i = 0; i < 3; ++i) {
        stages.es[i] = compiler.Compile(
            kEsSpirv, hash, "gs_es", {.kind = CompileVariant::Kind::TessVertex, .index_type = i});
        stages.ok &= stages.es[i].status == CompileStatus::Ok;
    }
    if (!stages.ok) {
        std::printf("   export kernel: %s\n", stages.es[0].error.c_str());
        return stages;
    }
    CompileVariant variant{.kind = CompileVariant::Kind::GeometryKernel};
    variant.es.stride = stages.es[0].shader->tess_layout.output;
    variant.es.members = stages.es[0].shader->tess_layout.members;
    if (const char* dump = std::getenv("BB_GEOMETRY_DUMP")) {
        // Debugging: the rewritten kernel as SPIR-V (spirv-dis / spirv-val it).
        const Metal::GeometryRewrite rewrite = Metal::RewriteGeometryShader(kGsSpirv, variant.es);
        std::ofstream(dump, std::ios::binary)
            .write(reinterpret_cast<const char*>(rewrite.spirv.data()), rewrite.spirv.size() * 4);
    }
    stages.gs = compiler.Compile(kGsSpirv, hash + 1, "gs_kernel", variant);
    stages.fs = compiler.Compile(kFsSpirv, hash + 2, "gs_fs");
    stages.ok &= stages.gs.status == CompileStatus::Ok && stages.fs.status == CompileStatus::Ok;
    if (!stages.ok) {
        std::printf("   geometry kernel: %s; fragment: %s\n", stages.gs.error.c_str(),
                    stages.fs.error.c_str());
    }
    return stages;
}

void RewriteCases() {
    std::printf("== the rewrite\n");
    const Metal::GeometryRewrite analysis = Metal::AnalyzeGeometryShader(kGsSpirv);
    if (!analysis.ok) {
        Fail("analysis: " + analysis.error);
        return;
    }
    const Metal::GeometryInfo& info = analysis.info;
    std::printf("-- points -> triangle strip, max %u vertices, %zu varyings, record %u B\n",
                info.max_vertices, info.varyings.size(), info.RecordStride());
    if (info.input != Metal::GsInputPrimitive::Points || info.input_vertices != 1 ||
        info.output != Metal::GsOutputPrimitive::TriangleStrip || info.max_vertices != 8 ||
        info.varyings.size() != 2 || info.varyings[0].location != 0 ||
        info.varyings[0].components != 4 || info.varyings[1].location != 1 ||
        info.RecordStride() != 48 || info.IndexSlots() != 16) {
        Fail("analysis of the test geometry shader");
    }

    struct Refusal {
        const char* name;
        std::vector<u32> spirv;
        const char* expect;
    };
    const Refusal refusals[] = {
        {"Invocations 2", WithMode(kGsSpirv, 0 /*Invocations*/, 0, 2), "Invocations"},
        {"OutputPoints", WithMode(kGsSpirv, 29 /*OutputTriangleStrip*/, 27 /*OutputPoints*/),
         "points"},
        {"adjacency input", WithMode(kGsSpirv, 19 /*InputPoints*/, 21 /*InputLinesAdjacency*/),
         "adjacency"},
    };
    for (const Refusal& r : refusals) {
        const Metal::GeometryRewrite result = Metal::AnalyzeGeometryShader(r.spirv);
        std::printf("-- %s: %s\n", r.name, result.ok ? "accepted" : result.error.c_str());
        if (result.ok || result.error.find(r.expect) == std::string::npos) {
            Fail(std::string("refusal: ") + r.name);
        }
    }
}

// ---- (c) running draws ------------------------------------------------------------------------------

struct Rect {
    double x0, x1, y0, y1;
    float color[4];
    float prim, quad, extra;
};

struct Pixels {
    std::vector<float> color, info;
};

struct Check {
    long inside{}, covered{}, bad{}, stray{};
    double max_error{};
};

Check Verify(const Pixels& pixels, std::span<const Rect> rects) {
    Check check;
    for (int py = 0; py < kSize; ++py) {
        for (int px = 0; px < kSize; ++px) {
            const double cx = (px + 0.5) / (kSize / 2) - 1.0, cy = (py + 0.5) / (kSize / 2) - 1.0;
            const float* got = &pixels.color[(py * kSize + px) * 4];
            const float* info = &pixels.info[(py * kSize + px) * 4];
            const bool covered = got[0] != kNone;
            const Rect* inside = nullptr;
            bool outside_all = true;
            for (const Rect& r : rects) {
                const double d =
                    std::min({cx - r.x0, r.x1 - cx, cy - r.y0, r.y1 - cy}) * kSize / 2;
                if (d > kEdge && !inside) {
                    inside = &r;
                }
                outside_all &= d < -kEdge;
            }
            if (inside) {
                ++check.inside;
                if (!covered) {
                    continue;
                }
                ++check.covered;
                for (int ch = 0; ch < 4; ++ch) {
                    const double error = std::fabs(double(got[ch]) - double(inside->color[ch]));
                    check.max_error = std::fmax(check.max_error, error);
                    check.bad += !(error <= 1e-5);
                }
                // info: primitive id, quad, vertex index; the 4th is the corner (interpolated)
                const float want_info[3] = {inside->prim, inside->quad, inside->extra};
                for (int ch = 0; ch < 3; ++ch) {
                    check.bad += !(std::fabs(info[ch] - want_info[ch]) < 1e-3);
                }
            } else if (outside_all && covered) {
                ++check.stray;
            }
        }
    }
    return check;
}

/// The rectangles of the quads of `point` as primitive `prim` with the export shader's vertex
/// index `vertex`.
void AddRects(std::vector<Rect>& rects, int point, u32 prim, u32 vertex) {
    const Point& p = kPoints[point];
    Rect q0{p.x - 0.12, p.x + 0.12, p.y - 0.12, p.y + 0.12, {}, float(prim), 0, float(vertex)};
    Rect q1{p.x - 0.05, p.x + 0.05, p.y + 0.35, p.y + 0.55, {}, float(prim), 1, float(vertex)};
    for (int ch = 0; ch < 4; ++ch) {
        q0.color[ch] = p.color[ch];
        q1.color[ch] = p.color[ch] * 0.75f;
    }
    rects.push_back(q0);
    rects.push_back(q1);
}

struct DrawCase {
    std::string name;
    std::vector<int> vertex_points; ///< point per vertex slot, -1: a poison vertex
    Metal::Tessellation::DrawParams draw;
    std::vector<u8> index_data;
    std::vector<u32> chunks;        ///< primitives per chunk
    u32 first_prim_base{7};         ///< the draw's first primitive id (chunks continue it)
    std::vector<int> prim_points;   ///< expected: the point each primitive draws
    std::vector<u32> prim_vertices; ///< expected: the vertex index its export shader sees
    bool check_scratch{};           ///< verify the records and indices of the first chunk
};

/// What the first chunk's scratch must hold for a draw of points `prim_points`.
void CheckScratch(const Metal::GeometryPipeline& pipe, const Metal::Geometry::Plan& plan,
                  const u8* span, const DrawCase& dc) {
    const u32 max = pipe.info.max_vertices;
    const u32 stride_words = pipe.info.RecordStride() / 4;
    const u32* records = reinterpret_cast<const u32*>(span + plan.record_offset);
    const u32* indices = reinterpret_cast<const u32*>(span + plan.index_offset);
    long bad = 0;
    for (u32 p = 0; p < plan.prims; ++p) {
        // Slots: 4 vertices, a restart (EndPrimitive), 4 vertices, 7 unused = restarts.
        for (u32 s = 0; s < pipe.info.IndexSlots(); ++s) {
            u32 want = kRestart;
            if (s < 4) {
                want = p * max + s;
            } else if (s >= 5 && s < 9) {
                want = p * max + (s - 1);
            }
            bad += indices[p * pipe.info.IndexSlots() + s] != want;
        }
        const Point& pt = kPoints[dc.prim_points[p]];
        for (u32 v = 0; v < 8; ++v) {
            const u32* record = records + (p * max + v) * stride_words;
            const float x = std::bit_cast<float>(record[0]), y = std::bit_cast<float>(record[1]);
            const float z = std::bit_cast<float>(record[2]), w = std::bit_cast<float>(record[3]);
            const float dx[2][4] = {{-0.12f, -0.12f, 0.12f, 0.12f}, {-0.05f, -0.05f, 0.05f, 0.05f}};
            const float dy[2][4] = {{-0.12f, 0.12f, -0.12f, 0.12f}, {0.35f, 0.55f, 0.35f, 0.55f}};
            const u32 quad = v / 4, corner = v % 4;
            bad += std::fabs(x - (pt.x + dx[quad][corner])) > 1e-6f;
            bad += std::fabs(y - (pt.y + dy[quad][corner])) > 1e-6f;
            bad += z != 0.0f || w != 1.0f;
            // slot 1: out_color (location 0), slot 2: out_info (location 1)
            const float scale = quad ? 0.75f : 1.0f;
            for (int ch = 0; ch < 4; ++ch) {
                bad += std::fabs(std::bit_cast<float>(record[4 + ch]) - pt.color[ch] * scale) > 1e-6f;
            }
            bad += std::bit_cast<float>(record[8]) != float(dc.first_prim_base + p);
            bad += std::bit_cast<float>(record[9]) != float(quad);
            bad += std::bit_cast<float>(record[10]) != float(dc.prim_vertices[p]);
            bad += std::bit_cast<float>(record[11]) != float(corner);
        }
    }
    std::printf("   %s: records and index list: %ld bad words\n", dc.name.c_str(), bad);
    if (bad) {
        Fail(dc.name + ": records / indices");
    }
}

bool RunDraw(Metal::Device& device, const Metal::GeometryPipeline& pipe, DrawCase& dc,
             Pixels& out) {
    @autoreleasepool {
        id<MTLDevice> dev = device.GetImpl().device;
        id<MTLCommandQueue> queue = device.GetImpl().queue;
        const auto buffer_of = [&](const void* data, size_t size) {
            return [dev newBufferWithBytes:data length:std::max<size_t>(size, 16)
                                   options:MTLResourceStorageModeShared];
        };
        std::vector<float> pos, color;
        for (int point : dc.vertex_points) {
            const Point& p = point < 0 ? Point{5, 5, {0, 0, 0, 0}} : kPoints[point];
            pos.insert(pos.end(), {p.x, p.y});
            color.insert(color.end(), p.color, p.color + 4);
        }
        id<MTLBuffer> pos_buffer = buffer_of(pos.data(), pos.size() * 4);
        id<MTLBuffer> color_buffer = buffer_of(color.data(), color.size() * 4);
        id<MTLBuffer> indices =
            dc.index_data.empty() ? nil : buffer_of(dc.index_data.data(), dc.index_data.size());
        dc.draw.index_buffer = (__bridge void*)indices;
        const u64 index_size = dc.draw.index_type == 1 ? 2 : 4;

        constexpr u64 kScratchOffset = 256;
        struct Chunk {
            Metal::Geometry::Plan plan;
            id<MTLBuffer> scratch;
            u32 first;
        };
        std::vector<Chunk> chunks;
        u32 first = 0;
        for (u32 count : dc.chunks) {
            Chunk chunk;
            chunk.plan = Metal::Geometry::PlanScratch(pipe, count);
            chunk.scratch = [dev newBufferWithLength:kScratchOffset + chunk.plan.total
                                             options:MTLResourceStorageModeShared];
            std::memset(chunk.scratch.contents, 0xcd, chunk.scratch.length);
            chunk.first = first;
            chunks.push_back(chunk);
            first += count;
        }

        MTLTextureDescriptor* td =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
                                                               width:kSize
                                                              height:kSize
                                                           mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        td.storageMode = MTLStorageModeShared;
        id<MTLTexture> targets[2] = {[dev newTextureWithDescriptor:td],
                                     [dev newTextureWithDescriptor:td]};
        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        for (int i = 0; i < 2; ++i) {
            pass.colorAttachments[i].texture = targets[i];
            pass.colorAttachments[i].loadAction = MTLLoadActionClear;
            pass.colorAttachments[i].clearColor = MTLClearColorMake(kNone, kNone, kNone, kNone);
            pass.colorAttachments[i].storeAction = MTLStoreActionStore;
        }

        id<MTLCommandBuffer> cmd = [queue commandBuffer];
        std::vector<Metal::Tessellation::VertexBinding> bindings;
        for (u32 i = 0; i < pipe.num_vertex_slots; ++i) {
            const bool is_pos = i == 0;
            bindings.push_back({(__bridge void*)(is_pos ? pos_buffer : color_buffer), 0,
                                is_pos ? 8u : 16u, pipe.vertex_slots[i].slot});
        }
        {
            id<MTLComputeCommandEncoder> compute = [cmd computeCommandEncoder];
            for (const Chunk& chunk : chunks) {
                // As Rasterizer::RecordGeometryDraw: the chunk's vertices.
                Metal::Tessellation::DrawParams draw = dc.draw;
                draw.num_vertices = chunk.plan.prims * pipe.info.input_vertices;
                const u64 first_vertex = u64(chunk.first) * pipe.info.input_vertices;
                if (dc.draw.index_type != 0) {
                    draw.index_offset = dc.draw.index_offset + first_vertex * index_size;
                } else {
                    draw.first_vertex = dc.draw.first_vertex + static_cast<u32>(first_vertex);
                }
                Metal::Geometry::EncodeKernels(
                    (__bridge void*)compute, pipe, draw, dc.first_prim_base + chunk.first,
                    bindings, (__bridge void*)chunk.scratch, kScratchOffset, chunk.plan,
                    [](const Metal::CompiledShader&) {}, [](const Metal::CompiledShader&) {});
            }
            [compute endEncoding];
        }
        {
            id<MTLRenderCommandEncoder> render = [cmd renderCommandEncoderWithDescriptor:pass];
            [render setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)pipe.render->state];
            const auto v = Metal::RasterUtil::ToMetalViewport(0, 0, kSize, kSize, 0, 1);
            const MTLViewport viewport{v.origin_x, v.origin_y, v.width, v.height, v.znear, v.zfar};
            [render setViewport:viewport];
            [render setCullMode:MTLCullModeNone];
            Metal::RenderList list;
            list.BeginDirect((__bridge void*)render);
            for (const Chunk& chunk : chunks) {
                Metal::Geometry::EncodeDraw(list, pipe, (__bridge void*)chunk.scratch,
                                            kScratchOffset, chunk.plan);
            }
            list.End();
            [render endEncoding];
        }
        [cmd commit];
        [cmd waitUntilCompleted];
        if (cmd.status != MTLCommandBufferStatusCompleted) {
            std::printf("   command buffer failed: %s\n",
                        cmd.error ? cmd.error.localizedDescription.UTF8String : "?");
            return false;
        }
        if (dc.check_scratch) {
            CheckScratch(pipe, chunks[0].plan,
                         static_cast<const u8*>(chunks[0].scratch.contents) + kScratchOffset,
                         dc);
        }
        if (g_debug_view) {
            Metal::Geometry::DebugLog(
                pipe, static_cast<const u8*>(chunks[0].scratch.contents) + kScratchOffset,
                chunks[0].plan, false, chunks[0].plan.prims);
        }
        out.color.resize(kSize * kSize * 4);
        out.info.resize(kSize * kSize * 4);
        [targets[0] getBytes:out.color.data()
                 bytesPerRow:kSize * 16
                  fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
                 mipmapLevel:0];
        [targets[1] getBytes:out.info.data()
                 bytesPerRow:kSize * 16
                  fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
                 mipmapLevel:0];
        return true;
    }
}

std::vector<u8> Bytes16(std::initializer_list<u16> values) {
    std::vector<u8> bytes(values.size() * 2);
    std::memcpy(bytes.data(), values.begin(), bytes.size());
    return bytes;
}

std::vector<u8> Bytes32(std::initializer_list<u32> values) {
    std::vector<u8> bytes(values.size() * 4);
    std::memcpy(bytes.data(), values.begin(), bytes.size());
    return bytes;
}

// ---- indirect draws (P7.1b) ---------------------------------------------------------------------------

struct IndirectCase {
    std::string name;
    std::vector<int> vertex_points; ///< point per vertex slot, -1: a poison vertex
    std::vector<u32> args;          ///< VkDrawIndirectCommand / VkDrawIndexedIndirectCommand words
    u64 args_offset{};
    u32 index_size{};               ///< 0: not indexed
    std::vector<u8> index_data;
    u64 index_offset{};
    std::vector<Rect> expect;
};

bool RunIndirect(Metal::Device& device, const Metal::GeometryPipeline& pipe,
                 const Metal::Geometry::IndirectKernels& kernels, const IndirectCase& ic,
                 Pixels& out) {
    @autoreleasepool {
        id<MTLDevice> dev = device.GetImpl().device;
        id<MTLCommandQueue> queue = device.GetImpl().queue;
        const auto buffer_of = [&](const void* data, size_t size) {
            return [dev newBufferWithBytes:data length:std::max<size_t>(size, 16)
                                   options:MTLResourceStorageModeShared];
        };
        std::vector<float> pos, color;
        for (int point : ic.vertex_points) {
            const Point& p = point < 0 ? Point{5, 5, {0, 0, 0, 0}} : kPoints[point];
            pos.insert(pos.end(), {p.x, p.y});
            color.insert(color.end(), p.color, p.color + 4);
        }
        id<MTLBuffer> pos_buffer = buffer_of(pos.data(), pos.size() * 4);
        id<MTLBuffer> color_buffer = buffer_of(color.data(), color.size() * 4);
        // The arguments at an offset into a bigger buffer.
        std::vector<u8> args_bytes(ic.args_offset + ic.args.size() * 4, 0xee);
        std::memcpy(args_bytes.data() + ic.args_offset, ic.args.data(), ic.args.size() * 4);
        id<MTLBuffer> args = buffer_of(args_bytes.data(), args_bytes.size());
        id<MTLBuffer> indices =
            ic.index_data.empty() ? nil : buffer_of(ic.index_data.data(), ic.index_data.size());

        constexpr u64 kScratchOffset = 256;
        const Metal::Geometry::IndirectPlan plan =
            Metal::Geometry::PlanIndirect(pipe, ic.index_size != 0);
        id<MTLBuffer> scratch = [dev newBufferWithLength:kScratchOffset + plan.total
                                                 options:MTLResourceStorageModeShared];
        std::memset(scratch.contents, 0xcd, scratch.length);

        MTLTextureDescriptor* td =
            [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
                                                               width:kSize
                                                              height:kSize
                                                           mipmapped:NO];
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        td.storageMode = MTLStorageModeShared;
        id<MTLTexture> targets[2] = {[dev newTextureWithDescriptor:td],
                                     [dev newTextureWithDescriptor:td]};
        MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
        for (int i = 0; i < 2; ++i) {
            pass.colorAttachments[i].texture = targets[i];
            pass.colorAttachments[i].loadAction = MTLLoadActionClear;
            pass.colorAttachments[i].clearColor = MTLClearColorMake(kNone, kNone, kNone, kNone);
            pass.colorAttachments[i].storeAction = MTLStoreActionStore;
        }

        id<MTLCommandBuffer> cmd = [queue commandBuffer];
        std::vector<Metal::Tessellation::VertexBinding> bindings;
        for (u32 i = 0; i < pipe.num_vertex_slots; ++i) {
            const bool is_pos = i == 0;
            bindings.push_back({(__bridge void*)(is_pos ? pos_buffer : color_buffer), 0,
                                is_pos ? 8u : 16u, pipe.vertex_slots[i].slot});
        }
        {
            id<MTLComputeCommandEncoder> compute = [cmd computeCommandEncoder];
            Metal::Geometry::IndirectDraw draw;
            draw.args = (__bridge void*)args;
            draw.args_offset = ic.args_offset;
            draw.index_size = ic.index_size;
            draw.index_buffer = (__bridge void*)indices;
            draw.index_offset = ic.index_offset;
            draw.index_limit = ic.index_size ? (ic.index_data.size() - ic.index_offset) / ic.index_size : 0;
            Metal::Geometry::EncodeIndirectKernels(
                (__bridge void*)compute, pipe, kernels, draw, bindings, (__bridge void*)scratch,
                kScratchOffset, plan, [](const Metal::CompiledShader&) {},
                [](const Metal::CompiledShader&) {});
            [compute endEncoding];
        }
        {
            id<MTLRenderCommandEncoder> render = [cmd renderCommandEncoderWithDescriptor:pass];
            [render setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)pipe.render->state];
            const auto v = Metal::RasterUtil::ToMetalViewport(0, 0, kSize, kSize, 0, 1);
            const MTLViewport viewport{v.origin_x, v.origin_y, v.width, v.height, v.znear, v.zfar};
            [render setViewport:viewport];
            [render setCullMode:MTLCullModeNone];
            Metal::RenderList list;
            list.BeginDirect((__bridge void*)render);
            Metal::Geometry::EncodeIndirectDraw(list, pipe, (__bridge void*)scratch, kScratchOffset,
                                                plan);
            list.End();
            [render endEncoding];
        }
        [cmd commit];
        [cmd waitUntilCompleted];
        if (cmd.status != MTLCommandBufferStatusCompleted) {
            std::printf("   command buffer failed: %s\n",
                        cmd.error ? cmd.error.localizedDescription.UTF8String : "?");
            return false;
        }
        out.color.resize(kSize * kSize * 4);
        out.info.resize(kSize * kSize * 4);
        [targets[0] getBytes:out.color.data()
                 bytesPerRow:kSize * 16
                  fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
                 mipmapLevel:0];
        [targets[1] getBytes:out.info.data()
                 bytesPerRow:kSize * 16
                  fromRegion:MTLRegionMake2D(0, 0, kSize, kSize)
                 mipmapLevel:0];
        return true;
    }
}

std::vector<u8> Bytes16(std::initializer_list<u16> values);
std::vector<u8> Bytes32(std::initializer_list<u32> values);

void IndirectCases(Metal::Device& device, const Metal::GeometryPipeline& pipe) {
    std::printf("== indirect draws (GPU-side prep)\n");
    Metal::Geometry::IndirectKernels kernels{device};
    if (!kernels.Ok()) {
        std::printf("   %s\n", kernels.Error().c_str());
        Fail("indirect kernels");
        return;
    }
    const Metal::Geometry::IndirectPlan plan = Metal::Geometry::PlanIndirect(pipe, false);
    std::printf("-- indirect capacity: %u primitives (%u indexed), scratch %llu bytes\n",
                plan.capacity, Metal::Geometry::PlanIndirect(pipe, true).capacity,
                (unsigned long long)plan.total);
    auto rects_of = [](std::initializer_list<std::pair<int, u32>> prims, u32 vertex_base = 0,
                       u32 first_prim = 0) {
        std::vector<Rect> rects;
        u32 i = 0;
        for (const auto& [point, vertex] : prims) {
            AddRects(rects, point, first_prim + i++, vertex + vertex_base);
        }
        return rects;
    };
    std::vector<IndirectCase> cases;
    {
        IndirectCase c;
        c.name = "non-indexed {3 vertices}";
        c.vertex_points = {0, 1, 2};
        c.args = {3, 1, 0, 0};
        c.args_offset = 16;
        c.expect = rects_of({{0, 0}, {1, 1}, {2, 2}});
        cases.push_back(c);
    }
    {
        IndirectCase c;
        c.name = "non-indexed, first vertex 1";
        c.vertex_points = {-1, 0, 1, 2};
        c.args = {3, 1, 1, 0};
        c.expect = rects_of({{0, 1}, {1, 2}, {2, 3}});
        cases.push_back(c);
    }
    {
        IndirectCase c;
        c.name = "two instances (instance 0 only)";
        c.vertex_points = {0, 1, 2};
        c.args = {3, 2, 0, 0};
        c.expect = rects_of({{0, 0}, {1, 1}, {2, 2}});
        cases.push_back(c);
    }
    {
        IndirectCase c;
        c.name = "no instances";
        c.vertex_points = {0, 1, 2};
        c.args = {3, 0, 0, 0};
        cases.push_back(c); // nothing drawn
    }
    {
        IndirectCase c;
        c.name = "indexed uint16, first index 1";
        c.vertex_points = {0, 1, 2};
        c.args = {3, 1, 1, 0, 0};
        c.args_offset = 8;
        c.index_size = 2;
        c.index_data = Bytes16({9, 2, 1, 0});
        c.expect = rects_of({{2, 2}, {1, 1}, {0, 0}});
        cases.push_back(c);
    }
    {
        IndirectCase c;
        c.name = "indexed uint32 at a buffer offset, base vertex 1";
        c.vertex_points = {-1, 0, 1, 2};
        c.args = {3, 1, 0, 1, 0};
        c.index_size = 4;
        c.index_data = Bytes32({99, 99, 0, 1, 2});
        c.index_offset = 8;
        c.expect = rects_of({{0, 1}, {1, 2}, {2, 3}});
        cases.push_back(c);
    }
    {
        IndirectCase c;
        c.name = "indexed, count beyond the index buffer (clamped to 3)";
        c.vertex_points = {0, 1, 2};
        c.args = {6, 1, 0, 0, 0};
        c.index_size = 2;
        c.index_data = Bytes16({1, 2, 0});
        c.expect = rects_of({{1, 1}, {2, 2}, {0, 0}});
        cases.push_back(c);
    }
    {
        // More primitives than the capacity: the first `capacity` are drawn, the later ones
        // overdraw the earlier ones.
        IndirectCase c;
        c.name = "count above the capacity (truncated)";
        for (u32 i = 0; i < 3 * (plan.capacity / 3 + 40); ++i) {
            c.vertex_points.push_back(static_cast<int>(i % 3));
        }
        c.args = {static_cast<u32>(c.vertex_points.size()), 1, 0, 0};
        for (int point = 0; point < 3; ++point) {
            u32 last = 0;
            for (u32 p = point; p < plan.capacity; p += 3) {
                last = p;
            }
            AddRects(c.expect, point, last, last);
        }
        cases.push_back(c);
    }
    u64 expect_instanced = 0, expect_truncated = 0, expect_draws = 0;
    for (const IndirectCase& c : cases) {
        Pixels pixels;
        if (!RunIndirect(device, pipe, kernels, c, pixels)) {
            Fail(c.name);
            continue;
        }
        ++expect_draws;
        expect_instanced += c.args[1] > 1;
        expect_truncated += c.name.find("truncated") != std::string::npos;
        const Check check = Verify(pixels, c.expect);
        long drawn = 0;
        for (int i = 0; i < kSize * kSize; ++i) {
            drawn += pixels.color[i * 4] != kNone;
        }
        std::printf("   %s: %ld/%ld pixels covered, max colour error %.6f, %ld bad, %ld stray\n",
                    c.name.c_str(), check.covered, check.inside, check.max_error, check.bad,
                    check.stray);
        const bool nothing = c.expect.empty();
        if ((nothing && drawn != 0) ||
            (!nothing && (check.inside == 0 || check.covered != check.inside || check.bad != 0 ||
                          check.stray != 0))) {
            Fail("indirect: " + c.name);
        }
    }
    const auto counters = kernels.Read();
    std::printf("-- counters: %llu draws, %llu instanced, %llu truncated\n",
                (unsigned long long)counters.draws, (unsigned long long)counters.instanced,
                (unsigned long long)counters.truncated);
    if (counters.draws != expect_draws || counters.instanced != expect_instanced ||
        counters.truncated != expect_truncated) {
        Fail("indirect counters");
    }
}

void GpuCases(Metal::Device& device, Metal::ShaderCompiler& compiler, Metal::PsoCache& pso,
              Metal::Geometry::PassThroughCache& pass_through) {
    std::printf("== GPU correctness (%dx%d)\n", kSize, kSize);
    const u64 hash = 0x6e570100;
    Stages stages = CompileStages(compiler, hash);
    if (!stages.ok) {
        Fail("compile");
        return;
    }
    const auto& layout = stages.es[0].shader->tess_layout;
    std::printf("-- export outputs: %u B per vertex:", layout.output);
    bool have_position = false, have0 = false, have2 = false;
    for (const Metal::InterfaceMember& m : layout.members) {
        if (m.position) {
            std::printf(" position@%u", m.offset);
            have_position = true;
        } else {
            std::printf(" loc%u@%u(x%u)", m.location, m.offset, m.components);
            have0 |= m.location == 0 && m.components == 4;
            have2 |= m.location == 2 && m.components == 4;
        }
    }
    std::printf("\n");
    if (!have_position || !have0 || !have2 || layout.output != 48) {
        Fail("export output layout");
    }
    const auto& geometry = stages.gs.shader->geometry;
    std::printf("-- geometry kernel: max %u vertices, record %u B\n", geometry.max_vertices,
                geometry.RecordStride());
    if (stages.fs.shader->stage_inputs.size() != 3 || stages.fs.shader->stage_inputs[2].location != 3) {
        Fail("fragment stage inputs");
    }
    // The plain compile of a geometry shader stays refused.
    {
        const auto plain = compiler.Compile(kGsSpirv, hash + 9, "gs_plain");
        if (plain.status != CompileStatus::UnsupportedStage) {
            Fail("a plain geometry compile must be UnsupportedStage");
        }
    }

    std::string error;
    const Metal::CompiledShader* pass = pass_through.Get(
        geometry, stages.fs.shader->stage_inputs, false, error);
    if (!pass) {
        std::printf("   pass-through: %s\n", error.c_str());
        Fail("pass-through function");
        return;
    }
    const std::vector<Metal::VertexAttribute> attributes{{0, false, 1}, {1, false, 1}};
    Metal::GeometryPipelineInput input;
    input.vertex_attributes = attributes;
    input.es = {stages.es[0].shader.get(), stages.es[1].shader.get(), stages.es[2].shader.get()};
    input.gs = stages.gs.shader.get();
    input.pass_through = pass;
    const auto key = MakeKey(hash);
    const Metal::GeometryPipeline* pipe = pso.GetGeometryPipeline(key, input, stages.fs.shader.get());
    if (!pipe) {
        Fail("geometry pipeline");
        return;
    }
    std::printf("-- max primitives per chunk: %u\n", Metal::Geometry::MaxPrimitivesPerChunk(*pipe));
    {
        const Metal::Geometry::Plan plan = Metal::Geometry::PlanScratch(*pipe, 3);
        if (plan.es_offset % 16 || plan.record_offset % 16 || plan.index_offset % 16 ||
            plan.total % 16 || plan.record_size < 3 * 8 * 48 || plan.index_size < 3 * 16 * 4) {
            Fail("scratch plan");
        }
    }

    struct Case {
        const char* name;
        std::vector<int> vertex_points;
        u32 first_vertex, index_type;
        std::vector<u8> index_data;
        u64 index_offset;
        std::vector<u32> chunks;
        std::vector<int> prim_points;
        std::vector<u32> prim_vertices;
        bool check_scratch;
    };
    const std::vector<Case> cases = {
        {"direct draw", {0, 1, 2}, 0, 0, {}, 0, {3}, {0, 1, 2}, {0, 1, 2}, true},
        {"first vertex 1", {-1, 0, 1, 2}, 1, 0, {}, 0, {3}, {0, 1, 2}, {1, 2, 3}, false},
        {"uint16 indices reversed", {0, 1, 2}, 0, 1, Bytes16({2, 1, 0}), 0, {3}, {2, 1, 0},
         {2, 1, 0}, false},
        {"uint32 indices at a buffer offset", {0, 1, 2}, 0, 2, Bytes32({99, 99, 1, 2, 0}), 8, {3},
         {1, 2, 0}, {1, 2, 0}, false},
        {"uint16 indices, base vertex 1", {-1, 0, 1, 2}, 1, 1, Bytes16({0, 1, 2}), 0, {3},
         {0, 1, 2}, {1, 2, 3}, false},
        {"two chunks (2 + 1)", {0, 1, 2}, 0, 0, {}, 0, {2, 1}, {0, 1, 2}, {0, 1, 2}, true},
        {"three chunks of indices (1 + 1 + 1)", {0, 1, 2}, 0, 1, Bytes16({1, 2, 0}), 0, {1, 1, 1},
         {1, 2, 0}, {1, 2, 0}, false},
    };
    for (const Case& c : cases) {
        if (g_debug_view && &c != &cases[0]) {
            continue;
        }
        DrawCase dc;
        dc.name = c.name;
        dc.vertex_points = c.vertex_points;
        dc.draw.first_vertex = c.first_vertex;
        dc.draw.index_type = c.index_type;
        dc.draw.index_offset = c.index_offset;
        dc.index_data = c.index_data;
        dc.chunks = c.chunks;
        dc.prim_points = c.prim_points;
        dc.prim_vertices = c.prim_vertices;
        dc.check_scratch = c.check_scratch;
        dc.draw.num_vertices = 3;
        std::vector<Rect> rects;
        for (u32 p = 0; p < c.prim_points.size(); ++p) {
            AddRects(rects, c.prim_points[p], dc.first_prim_base + p, c.prim_vertices[p]);
        }
        Pixels pixels;
        if (!RunDraw(device, *pipe, dc, pixels)) {
            Fail(dc.name);
            continue;
        }
        if (g_debug_view) {
            // BB_GS_DEBUG: every primitive is solid magenta on the first target, the second
            // target (masked off) is untouched.
            long inside = 0, magenta = 0, other = 0;
            for (int py = 0; py < kSize; ++py) {
                for (int px = 0; px < kSize; ++px) {
                    const double cx = (px + 0.5) / (kSize / 2) - 1.0;
                    const double cy = (py + 0.5) / (kSize / 2) - 1.0;
                    const float* got = &pixels.color[(py * kSize + px) * 4];
                    const float* info = &pixels.info[(py * kSize + px) * 4];
                    for (const Rect& r : rects) {
                        if (std::min({cx - r.x0, r.x1 - cx, cy - r.y0, r.y1 - cy}) * kSize / 2 >
                            kEdge) {
                            ++inside;
                            magenta += got[0] == 1 && got[1] == 0 && got[2] == 1 && got[3] == 1;
                            break;
                        }
                    }
                    other += got[0] != kNone && !(got[0] == 1 && got[1] == 0 && got[2] == 1);
                    other += info[0] != kNone;
                }
            }
            std::printf("   debug view: %ld/%ld pixels magenta, %ld wrong\n", magenta, inside,
                        other);
            if (inside == 0 || magenta != inside || other != 0) {
                Fail("debug view");
            }
            return;
        }
        const Check check = Verify(pixels, rects);
        std::printf("   %s: %ld/%ld pixels covered, max colour error %.6f, %ld bad, %ld stray\n",
                    c.name, check.covered, check.inside, check.max_error, check.bad, check.stray);
        if (check.inside == 0 || check.covered != check.inside || check.bad != 0 ||
            check.stray != 0) {
            Fail(c.name);
        }
    }
    if (!g_debug_view) {
        IndirectCases(device, *pipe);
    }
}

// ---- (d) the pass-through function ----------------------------------------------------------------

void PassThroughCases(Metal::Device& device, Metal::Geometry::PassThroughCache& cache) {
    std::printf("== pass-through function\n");
    Metal::GeometryInfo info;
    info.max_vertices = 4;
    info.varyings = {{1, 4, Metal::ScalarType::Float}, {5, 2, Metal::ScalarType::Uint}};
    // Fragment inputs: location 1 (written), 2 (not written), 5 read wider than written, an int.
    const std::vector<Metal::StageInput> inputs = {{1, Metal::ScalarType::Float, 4},
                                                   {2, Metal::ScalarType::Float, 4},
                                                   {5, Metal::ScalarType::Uint, 4},
                                                   {6, Metal::ScalarType::Int, 1}};
    std::string error;
    const std::string plain = Metal::Geometry::PassThroughSource(info, inputs, false, error);
    const std::string depth = Metal::Geometry::PassThroughSource(info, inputs, true, error);
    if (plain.empty() || depth.empty() ||
        plain.find("(p.z + p.w) * 0.5f") != std::string::npos ||
        depth.find("p.z = (p.z + p.w) * 0.5f;") == std::string::npos ||
        plain.find("[[user(locn2)]]") == std::string::npos) {
        Fail("pass-through source");
    }
    const Metal::CompiledShader* a = cache.Get(info, inputs, false, error);
    const Metal::CompiledShader* b = cache.Get(info, inputs, false, error);
    const Metal::CompiledShader* c = cache.Get(info, inputs, true, error);
    std::printf("-- compiled %s, cached %s, depth variant %s\n", a ? "ok" : error.c_str(),
                a == b ? "same" : "DIFFERENT", c && c != a ? "distinct" : "NOT distinct");
    if (!a || a != b || !c || c == a) {
        Fail("pass-through cache");
    }
    const std::vector<Metal::StageInput> bad = {{0, Metal::ScalarType::Other, 4}};
    if (!Metal::Geometry::PassThroughSource(info, bad, false, error).empty() || error.empty()) {
        Fail("pass-through refusal of an unknown input type");
    }
}

// ---- local check over a game's shader dump ------------------------------------------------------

void GameDumps(Metal::Device& device, Metal::ShaderCompiler& compiler, Metal::Geometry::PassThroughCache& cache,
               const fs::path& dir) {
    std::printf("== game shader dumps in %s\n", dir.c_str());
    std::vector<fs::path> gs_files, es_files, fs_files;
    for (const auto& entry : fs::directory_iterator(dir)) {
        const std::string name = entry.path().filename().string();
        if (entry.path().extension() != ".spv") {
            continue;
        }
        if (name.starts_with("gs_")) {
            gs_files.push_back(entry.path());
        } else if (name.starts_with("es_")) {
            es_files.push_back(entry.path());
        } else if (name.starts_with("fs_")) {
            fs_files.push_back(entry.path());
        }
    }
    std::sort(gs_files.begin(), gs_files.end());
    std::sort(es_files.begin(), es_files.end());
    std::sort(fs_files.begin(), fs_files.end());
    if (gs_files.empty() || es_files.empty()) {
        Fail("no gs_*.spv / es_*.spv in the dump directory");
        return;
    }
    // The export shader with the most outputs: every geometry shader's inputs are a subset.
    Metal::CompileResult best_es;
    for (const fs::path& path : es_files) {
        const std::vector<u32> spirv = ReadSpirv(path);
        Metal::CompileResult es = compiler.Compile(
            spirv, 0xe5, path.filename().string(),
            {.kind = CompileVariant::Kind::TessVertex, .index_type = 0});
        if (es.shader && (!best_es.shader || es.shader->tess_layout.members.size() >
                                                 best_es.shader->tess_layout.members.size())) {
            best_es = std::move(es);
        }
    }
    if (!best_es.shader) {
        Fail("no export shader compiled");
        return;
    }
    std::printf("-- export shader: %u B per vertex, %zu members\n",
                best_es.shader->tess_layout.output, best_es.shader->tess_layout.members.size());
    std::vector<Metal::CompileResult> fragments;
    for (const fs::path& path : fs_files) {
        fragments.push_back(compiler.Compile(ReadSpirv(path), 0xf5, path.filename().string()));
    }
    for (const fs::path& path : gs_files) {
        const std::vector<u32> spirv = ReadSpirv(path);
        const Metal::GeometryRewrite analysis = Metal::AnalyzeGeometryShader(spirv);
        if (!analysis.ok) {
            std::printf("   %s: refused: %s\n", path.filename().c_str(), analysis.error.c_str());
            Fail(path.filename().string());
            continue;
        }
        CompileVariant variant{.kind = CompileVariant::Kind::GeometryKernel};
        variant.es.stride = best_es.shader->tess_layout.output;
        variant.es.members = best_es.shader->tess_layout.members;
        if (const char* out_dir = std::getenv("BB_GEOMETRY_DUMP_DIR")) {
            const Metal::GeometryRewrite rewrite = Metal::RewriteGeometryShader(spirv, variant.es);
            std::ofstream(fs::path{out_dir} / (path.stem().string() + ".kernel.spv"),
                          std::ios::binary)
                .write(reinterpret_cast<const char*>(rewrite.spirv.data()),
                       rewrite.spirv.size() * 4);
        }
        const Metal::CompileResult gs =
            compiler.Compile(spirv, 0x65, path.filename().string(), variant);
        if (!gs.shader) {
            std::printf("   %s: kernel did not compile: %s\n", path.filename().c_str(),
                        gs.error.c_str());
            Fail(path.filename().string());
            continue;
        }
        const Metal::GeometryInfo& info = gs.shader->geometry;
        std::printf("   %s: OK, input vertices %u, max vertices %u, %zu varyings, record stride "
                    "%u B, %u index slots, %u buffers, local size %u\n",
                    path.filename().c_str(), info.input_vertices, info.max_vertices,
                    info.varyings.size(), info.RecordStride(), info.IndexSlots(),
                    gs.shader->num_buffers, gs.shader->local_size[0]);
        for (size_t i = 0; i < fragments.size(); ++i) {
            if (!fragments[i].shader) {
                continue;
            }
            for (bool clip : {false, true}) {
                std::string error;
                const Metal::CompiledShader* pass =
                    cache.Get(info, fragments[i].shader->stage_inputs, clip, error);
                std::printf("      pass-through for %s%s: %s\n", fs_files[i].filename().c_str(),
                            clip ? " (clip -w..w)" : "", pass ? "OK" : error.c_str());
                if (!pass) {
                    Fail("pass-through of " + path.filename().string());
                }
            }
        }
    }
}

} // namespace

int main(int argc, char** argv) {
    // The validation layer: an error asserts. (Before the first device exists.)
    setenv("MTL_DEBUG_LAYER", "1", 0);
    fs::path dumps;
    for (int i = 1; i < argc; ++i) {
        if (std::string{argv[i]} == "--game-dumps" && i + 1 < argc) {
            dumps = argv[++i];
        } else if (std::string{argv[i]} == "--gs-debug") {
            setenv("BB_GS_DEBUG", "1", 1);
            g_debug_view = true;
        } else {
            std::fprintf(stderr, "usage: %s [--game-dumps <dir>]\n", argv[0]);
            return 2;
        }
    }

    const fs::path cache_dir = fs::temp_directory_path() / "bbport-mtl-geometry-test-cache";
    std::error_code ec;
    fs::remove_all(cache_dir, ec); // always convert: the variants are what is under test
    {
        Metal::Device device;
        Metal::ShaderCompiler compiler{device, cache_dir};
        Metal::PsoCache pso{device, cache_dir};
        Metal::Geometry::PassThroughCache pass_through{device};
        RewriteCases();
        GpuCases(device, compiler, pso, pass_through);
        if (!g_debug_view) {
            PassThroughCases(device, pass_through);
        }
        if (!dumps.empty()) {
            GameDumps(device, compiler, pass_through, dumps);
        }
    }
    if (!g_debug_view) {
        // The BB_GS_DEBUG view is a process-wide switch (read once): run it in a child process.
        std::printf("== BB_GS_DEBUG=1 view (child process)\n");
        std::fflush(stdout);
        const std::string command = "'" + std::string{argv[0]} + "' --gs-debug";
        if (std::system(command.c_str()) != 0) {
            Fail("the BB_GS_DEBUG child process");
        }
    }
    std::printf("\n%s\n", g_failures ? "FAIL" : "PASS");
    return g_failures ? 1 : 0;
}
