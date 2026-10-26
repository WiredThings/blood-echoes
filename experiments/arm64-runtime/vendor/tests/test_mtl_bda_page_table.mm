// SPDX-License-Identifier: GPL-2.0-or-later
// Actual production table updates and SPIR-V -> MSL addressing on a Metal device; no game.
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "video_core/buffer_cache/bda_page_table.h"
#include "video_core/render_state.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include "video_core/renderer_metal/mtl_runtime.h"
#include "video_core/renderer_metal/mtl_scheduler_impl.h"
#include "video_core/renderer_metal/mtl_shader.h"
#import <Metal/Metal.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <thread>
#include <vector>
using namespace VideoCore;
struct Fixture {
    Metal::Device device;
    Metal::Scheduler scheduler{device, "bda-test"};
    Metal::Runtime runtime{device, scheduler};
    Metal::ShaderCompiler compiler{device, std::filesystem::temp_directory_path() / "bb-bda-msl"};
    void Settle() {
        runtime.Finish();
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
};
static std::unique_ptr<Buffer> Make(Fixture& f, u64 bytes) {
    return std::make_unique<Buffer>(f.runtime, 0, bytes, MemoryType::HostCached, "BDA test data");
}
static void Update(BdaPageTable& table, Buffer& upload, u64 first, std::span<const u64> current,
                   std::span<const u64> guest = {}) {
    assert(current.size() + guest.size() <= upload.SizeBytes() / 8);
    std::memcpy(upload.mapped_data.data(), current.data(), current.size_bytes());
    if (!guest.empty())
        std::memcpy(upload.mapped_data.data() + current.size_bytes(), guest.data(),
                    guest.size_bytes());
    const vk::BufferCopy a{0, first * 8, current.size_bytes()},
        b{current.size_bytes(), first * 8, guest.size_bytes()};
    table.Copy(&upload, std::span{&a, 1},
               guest.empty() ? std::span<const vk::BufferCopy>{} : std::span{&b, 1});
}
static std::vector<u32> Program(bool two_level, u32 shift, bool bench, bool random = false,
                                bool dma = false) {
    using namespace Shader;
    constexpr u64 Hash = 0xbda20001;
    Info info{};
    info.hw_stage = HwStage::Compute;
    info.sw_stage = SwStage::Compute;
    info.pgm_hash = Hash;
    info.uses_paged_buffers = true;
    if (dma) {
        info.uses_dma = true;
        info.readconst_types = Info::ReadConstType::Dynamic;
    }
    for (u32 i = 0; i < 3; ++i) {
        BufferResource resource{};
        resource.buffer_type = i == 2 ? BufferType::BdaPagetable : BufferType::Guest;
        resource.used_types = i == 2 ? IR::Type::U64 : IR::Type::U32;
        resource.is_written = i == 1;
        const auto sharp = AmdGpu::Buffer::Placeholder(64 * 1024 * 1024);
        std::memcpy(resource.sharp_fetch.immediates.data(), &sharp, sizeof(sharp));
        info.buffers.push_back(resource);
    }
    if (dma) {
        BufferResource fault{};
        fault.buffer_type = BufferType::FaultBuffer;
        fault.used_types = IR::Type::U32;
        fault.is_written = true;
        info.buffers.push_back(fault);
    }
    Common::ObjectPool<IR::Inst> pool;
    IR::Block block(pool);
    IR::IREmitter ir(block);
    if (!bench) {
        const u32 page = 1u << shift;
        const std::array<u32, 6> offsets{
            0, page / 4 - 1, page / 4, page * 2 / 4, page * 3 / 4, 0xffffffffU / 4};
        for (u32 i = 0; i < offsets.size(); ++i) {
            const auto value =
                dma ? IR::Value{ir.ReadConst(
                          ir.CompositeConstruct(ir.Imm32(0x40000000U), ir.Imm32(0x70U)),
                          ir.Imm32(i % 4))}
                    : ir.LoadBufferU32(1, ir.Imm32(0U), ir.Imm32(i % 4), {});
            ir.StoreBufferU32(1, ir.Imm32(1U), ir.Imm32(offsets[i]), value, {});
        }
    } else {
        info.loads.Set(IR::Attribute::WorkgroupId, 0);
        info.loads.Set(IR::Attribute::LocalInvocationId, 0);
        const auto group = ir.GetAttributeU32(IR::Attribute::WorkgroupId, 0);
        const auto local = ir.GetAttributeU32(IR::Attribute::LocalInvocationId, 0);
        const auto id = ir.IAdd(ir.IMul(group, ir.Imm32(64U)), local);
        IR::U32 sum = ir.Imm32(0U);
        for (u32 j = 0; j < 16; ++j) {
            const auto page =
                random
                    ? ir.BitwiseAnd(ir.IAdd(ir.IMul(id, ir.Imm32(104729U)), ir.Imm32(j * 14713)),
                                    ir.Imm32(65535U))
                    : ir.BitwiseAnd(ir.IAdd(ir.ShiftRightLogical(id, ir.Imm32(10U)), ir.Imm32(j)),
                                    ir.Imm32(65535U));
            const auto offset = ir.IAdd(ir.ShiftLeftLogical(page, ir.Imm32(shift - 2)),
                                        ir.BitwiseAnd(id, ir.Imm32(1023U)));
            sum = ir.IAdd(sum, IR::U32{ir.LoadBufferU32(1, ir.Imm32(0U), offset, {})});
        }
        ir.StoreBufferU32(1, ir.Imm32(1U), id, sum, {});
    }
    ir.Epilogue();
    IR::Program program(info);
    program.blocks.push_back(&block);
    program.syntax_list.push_back(
        {.data = {.block = &block}, .type = IR::AbstractSyntaxNode::Type::Block});
    program.syntax_list.push_back({.type = IR::AbstractSyntaxNode::Type::Return});
    RuntimeInfo runtime{};
    runtime.Initialize(info.hw_stage, info.sw_stage);
    runtime.hw.cs.workgroup_size = {bench ? 64u : 1u, 1, 1};
    Profile profile{};
    profile.supported_spirv = 0x00010600;
    profile.support_int64 = true;
    profile.paged_buffers = true;
    profile.sparse_page_shift = shift;
    profile.two_level_bda = two_level;
    profile.buffer_copy_shader_hash = bench ? 0 : Hash;
    Backend::Bindings bindings{};
    return Backend::SPIRV::EmitSPIRV(profile, runtime, program, bindings);
}
struct Kernel {
    std::unique_ptr<Metal::CompiledShader> shader;
    id<MTLComputePipelineState> pipeline;
};
static Kernel Compile(Fixture& f, bool two, u32 shift, bool bench = false, bool random = false,
                      bool dma = false) {
    const auto code = Program(two, shift, bench, random, dma);
    auto compiled =
        f.compiler.Compile(code, 0xbda20000 + two + 2 * bench + 4 * random + shift, "bda-test");
    if (compiled.status != Metal::CompileStatus::Ok)
        std::fprintf(stderr, "%s\n", compiled.error.c_str());
    assert(compiled.status == Metal::CompileStatus::Ok);
    NSError* error = nil;
    auto state = [f.device.GetImpl().device
        newComputePipelineStateWithFunction:(__bridge id<MTLFunction>)compiled.shader->function
                                      error:&error];
    if (!state)
        std::fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
    assert(state);
    return {std::move(compiled.shader), state};
}
static id<MTLCommandBuffer> Dispatch(Fixture& f, Kernel& k, BdaPageTable& table, Buffer& records,
                                     std::span<Buffer* const> data, u32 threads = 1,
                                     Buffer* fault = nullptr) {
    f.runtime.FlushBarriers();
    f.scheduler.ComputeEncoder("BDA shader");
    auto encoder = f.scheduler.GetImpl().compute;
    [encoder setComputePipelineState:k.pipeline];
    for (const auto& binding : k.shader->bindings)
        if (binding.kind == Metal::ShaderBinding::Kind::Buffer) {
            assert(binding.binding < 4 && (binding.binding < 3 || fault));
            Buffer* buffer = binding.binding == 3   ? fault
                             : binding.binding == 2 ? table.Root()
                                                    : &records;
            [encoder setBuffer:(__bridge id<MTLBuffer>)buffer->buffer.native
                        offset:binding.binding == 1 ? 256 : 0
                       atIndex:binding.msl_index];
        }
    std::array<u32, 32> push{};
    [encoder setBytes:push.data() length:sizeof(push) atIndex:0];
    if (k.shader->buffer_size_buffer != ~0u) {
        std::array<u32, 32> sizes{};
        sizes.fill(~0u);
        [encoder setBytes:sizes.data() length:sizeof(sizes) atIndex:k.shader->buffer_size_buffer];
    }
    for (auto* buffer : data)
        [encoder useResource:(__bridge id<MTLBuffer>)buffer->buffer.native
                       usage:MTLResourceUsageRead | MTLResourceUsageWrite];
    [encoder dispatchThreads:MTLSizeMake(threads, 1, 1)
        threadsPerThreadgroup:MTLSizeMake(std::min<u32>(threads, 64), 1, 1)];
    f.runtime.AccessBuffer(table.Root(), 0, table.Root()->SizeBytes(),
                           vk::PipelineStageFlagBits2::eComputeShader,
                           vk::AccessFlagBits2::eShaderRead);
    f.runtime.AccessGlobal(vk::PipelineStageFlagBits2::eComputeShader,
                           vk::AccessFlagBits2::eShaderWrite);
    return f.scheduler.GetImpl().cmdbuf;
}
static void Records(Buffer& records, u64 source, u64 dest, u32 bytes, u64 trash, u32 guest_offset,
                    bool through = true) {
    auto* out = reinterpret_cast<u32*>(records.mapped_data.data());
    const auto put = [&](u32 at, u64 base, bool write) {
        const u32 words[] = {u32(base),  u32(base >> 32),  bytes,        u32(write),
                             u32(trash), u32(trash >> 32), guest_offset, 0};
        std::memcpy(out + at, words, sizeof(words));
    };
    put(0, source, false);
    put(64, dest, through);
}
static void Correctness(Fixture& f, u32 shift) {
    const u32 page = 1u << shift;
    auto guest = Make(f, page * 3), mirror = Make(f, page * 3), source = Make(f, page),
         alternate = Make(f, page), records = Make(f, 4096), upload = Make(f, 4096),
         trash = Make(f, 65536);
    for (auto* data : {guest.get(), mirror.get(), trash.get()})
        std::memset(data->mapped_data.data(), 0, data->SizeBytes());
    auto* input = reinterpret_cast<u32*>(source->mapped_data.data());
    auto* host = reinterpret_cast<u32*>(guest->mapped_data.data());
    auto* vram = reinterpret_cast<u32*>(mirror->mapped_data.data());
    const u32 values[] = {0x12345678, 0x23456789, 0x3456789a, 0x456789ab};
    std::memcpy(input, values, sizeof(values));
    auto kernel = Compile(f, true, shift);
    {
        BdaPageTable table{f.runtime, shift, true, true};
        const u64 src_page = 0x7040000000ull >> shift;
        const u64 dst_page = ((0x70c0000000ull >> shift) & ~4095ull) + 4095;
        const std::array<u64, 1> cur_src{source->BufferDeviceAddress()},
            guest_src{guest->BufferDeviceAddress() + 2 * page};
        Update(table, *upload, src_page, cur_src, guest_src);
        f.Settle();
        const std::array<u64, 2> cur_dst{mirror->BufferDeviceAddress(),
                                         guest->BufferDeviceAddress() + page};
        const std::array<u64, 2> guest_dst{guest->BufferDeviceAddress(),
                                           guest->BufferDeviceAddress() + page};
        Update(table, *upload, dst_page, cur_dst, guest_dst);
        Records(*records, src_page * page, dst_page * page, page * 3, trash->BufferDeviceAddress(),
                table.GuestOffset());
        const std::array<Buffer*, 5> data{guest.get(), mirror.get(), source.get(), alternate.get(),
                                          trash.get()};
        Dispatch(f, kernel, table, *records, data);
        f.Settle();
        assert(host[0] == values[0] && vram[0] == values[0]);
        assert(host[page / 4 - 1] == values[1] && vram[page / 4 - 1] == values[1]);
        assert(host[page / 4] == values[2] && host[page * 2 / 4] == 0 && vram[page * 2 / 4] == 0);
        assert(table.LeafCount() == 3 && table.AllocatedBytes() < 1048576);
        auto dma = Compile(f, true, shift, false, false, true);
        auto faults = Make(f, Common::BdaTable::Pages(shift) / 8);
        std::memset(faults->mapped_data.data(), 0, faults->SizeBytes());
        Dispatch(f, dma, table, *records, data, 1, faults.get());
        f.Settle();
        assert(host[0] == values[0]);
        std::puts("PASS production DMA/ReadConst uses the same two-level addressing");
        // Same leaf replacement while the first dispatch is still pending; snapshot its output.
        auto snapshot = Make(f, page);
        std::fill_n(reinterpret_cast<u32*>(alternate->mapped_data.data()), 4, 0xdeadbeefU);
        Dispatch(f, kernel, table, *records, data);
        const vk::BufferCopy snap{0, 0, page};
        f.runtime.CopyBuffer(guest.get(), snapshot.get(), std::span{&snap, 1});
        const std::array<u64, 1> replacement{alternate->BufferDeviceAddress()};
        Update(table, *upload, src_page, replacement, guest_src);
        Dispatch(f, kernel, table, *records, data);
        f.Settle();
        assert(reinterpret_cast<u32*>(snapshot->mapped_data.data())[0] == values[0] &&
               host[0] == 0xdeadbeefU);
        const auto leaves = table.LeafCount();
        const std::array<u64, 1> zero{0};
        Update(table, *upload, src_page, zero, zero);
        Dispatch(f, kernel, table, *records, data);
        f.Settle();
        assert(host[0] == 0 && table.LeafCount() == leaves);
        // Entirely absent root, then 40-bit overflow, must read zero without a null dereference.
        for (const u64 bad : {0x100000000ull, 1ull << 40, 0xffffffffffff0000ull}) {
            Records(*records, bad, dst_page * page, page * 3, trash->BufferDeviceAddress(),
                    table.GuestOffset());
            Dispatch(f, kernel, table, *records, data);
            f.Settle();
            assert(host[0] == 0);
        }
        const u64 last = Common::BdaTable::Pages(shift) - 1;
        Update(table, *upload, last, cur_src, guest_src);
        f.Settle();
        Records(*records, last * page, dst_page * page, page * 3, trash->BufferDeviceAddress(),
                table.GuestOffset());
        Dispatch(f, kernel, table, *records, data);
        f.Settle();
        assert(host[0] == values[0]);
        Records(*records, last * page, dst_page * page, page * 3, trash->BufferDeviceAddress(),
                table.GuestOffset(), false);
        host[0] = vram[0] = 0;
        Dispatch(f, kernel, table, *records, data);
        f.Settle();
        assert(host[0] == 0 && vram[0] == values[0]);
    }
    f.Settle();
    assert(f.device.GetImpl().bda_leaves.empty());
    std::printf("PASS two-level BDA shift=%u: high addresses, leaf/page boundaries, dual views, "
                "pending remap, zero/unmap, overflow, write-through ownership, retirement\n",
                shift);
}
static void RenderUpdateOrdering(Fixture& f) {
    auto source = Make(f, 16384), alternate = Make(f, 16384), upload = Make(f, 4096);
    *reinterpret_cast<u32*>(source->mapped_data.data()) = 17;
    *reinterpret_cast<u32*>(alternate->mapped_data.data()) = 23;
    f.runtime.RegisterBdaLeaf(source.get(), true);
    f.runtime.RegisterBdaLeaf(alternate.get(), true);
    NSString* text = @"#include <metal_stdlib>\nusing namespace metal;\n"
                      "vertex float4 v(uint i [[vertex_id]]) {float2 "
                      "p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
                      "fragment float4 f(device const ulong* root [[buffer(0)]],constant uint& p "
                      "[[buffer(1)]]) {"
                      "device const ulong* leaf=reinterpret_cast<device const ulong*>(root[p>>12]);"
                      "device const uint* data=reinterpret_cast<device const uint*>(leaf[p&4095]);"
                      "uint a=data[0];return float4(a==17?1.0:0.0,0,a==23?1.0:0.0,1);}";
    NSError* error = nil;
    auto device = f.device.GetImpl().device;
    auto library = [device newLibraryWithSource:text options:nil error:&error];
    assert(library);
    auto descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction = [library newFunctionWithName:@"v"];
    descriptor.fragmentFunction = [library newFunctionWithName:@"f"];
    descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
    auto pipeline = [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
    assert(pipeline);
    auto td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                                 width:8
                                                                height:8
                                                             mipmapped:NO];
    td.storageMode = MTLStorageModeShared;
    td.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> before = [device newTextureWithDescriptor:td],
                   before_replay = [device newTextureWithDescriptor:td],
                   after = [device newTextureWithDescriptor:td];
    {
        BdaPageTable table{f.runtime, 14, true, true};
        const u32 page = 0x7040000000ull >> 14;
        const std::array<u64, 1> first{source->BufferDeviceAddress()},
            second{alternate->BufferDeviceAddress()};
        Update(table, *upload, page, first, first);
        f.Settle();
        auto draw = [&](id<MTLTexture> target) {
            f.runtime.FlushBarriers();
            auto pass = [MTLRenderPassDescriptor renderPassDescriptor];
            pass.colorAttachments[0].texture = target;
            pass.colorAttachments[0].loadAction = MTLLoadActionClear;
            pass.colorAttachments[0].storeAction = MTLStoreActionStore;
            RenderState state{};
            f.scheduler.RenderEncoder((__bridge const void*)pass, state);
            auto& list = f.scheduler.Commands();
            list.SetRenderPipelineState((__bridge void*)pipeline);
            list.SetBuffer(Metal::RenderList::Stage::Fragment, table.Root()->buffer.native, 0, 0);
            list.SetBytes(Metal::RenderList::Stage::Fragment, &page, 4, 1);
            Metal::RenderList::Viewport vp{0, 0, 8, 8, 0, 1};
            list.SetViewports(&vp, 1);
            list.DrawPrimitives(MTLPrimitiveTypeTriangle, 0, 3, 1, 0);
            f.runtime.AccessBuffer(table.Root(), 0, table.Root()->SizeBytes(),
                                   vk::PipelineStageFlagBits2::eAllGraphics,
                                   vk::AccessFlagBits2::eShaderRead);
        };
        draw(before);
        draw(before_replay);
        Update(table, *upload, page, second, second);
        draw(after);
        f.Settle();
        std::array<u8, 256> red{}, red_replay{}, blue{};
        [before getBytes:red.data()
             bytesPerRow:32
              fromRegion:MTLRegionMake2D(0, 0, 8, 8)
             mipmapLevel:0];
        [before_replay getBytes:red_replay.data()
                    bytesPerRow:32
                     fromRegion:MTLRegionMake2D(0, 0, 8, 8)
                    mipmapLevel:0];
        [after getBytes:blue.data()
            bytesPerRow:32
             fromRegion:MTLRegionMake2D(0, 0, 8, 8)
            mipmapLevel:0];
        for (unsigned i = 0; i < 64; ++i)
            assert(red[i * 4] == 255 && red[i * 4 + 2] == 0 && red_replay[i * 4] == 255 &&
                   red_replay[i * 4 + 2] == 0 && blue[i * 4] == 0 && blue[i * 4 + 2] == 255);
    }
    f.runtime.RegisterBdaLeaf(source.get(), false);
    f.runtime.RegisterBdaLeaf(alternate.get(), false);
    f.Settle();
    assert(f.device.GetImpl().bda_leaves.empty());
    std::puts("PASS render/held replay: first draw sees old leaf, later draw sees remap, indirect "
              "buffers declared");
}
static void Benchmark(Fixture& f, bool two, bool random) {
    constexpr u32 Shift = 14, Page = 1u << Shift, Pages = 65536, PhysicalPages = 1024,
                  Threads = 262144;
    const u64 source_base = 0x7040000000ull, dest_base = 0x70c0000000ull;
    auto source = Make(f, u64(PhysicalPages) * Page), output = Make(f, u64(Threads) * 4),
         records = Make(f, 4096), trash = Make(f, 65536);
    auto upload = Make(f, (Pages + Threads * 4 / Page) * 2 * 8);
    auto* data = reinterpret_cast<u32*>(source->mapped_data.data());
    for (u32 i = 0; i < source->SizeBytes() / 4; ++i)
        data[i] = i * 17 + 3;
    std::memset(output->mapped_data.data(), 0, output->SizeBytes());
    auto kernel = Compile(f, two, Shift, true, random);
    {
        BdaPageTable table{f.runtime, Shift, true, two};
        std::vector<u64> entries(Pages);
        for (u32 i = 0; i < Pages; ++i)
            entries[i] = source->BufferDeviceAddress() + u64(i % PhysicalPages) * Page;
        Update(table, *upload, source_base / Page, entries, entries);
        f.Settle();
        entries.resize(output->SizeBytes() / Page);
        for (u32 i = 0; i < entries.size(); ++i)
            entries[i] = output->BufferDeviceAddress() + u64(i) * Page;
        Update(table, *upload, dest_base / Page, entries, entries);
        f.Settle();
        Records(*records, source_base, dest_base, Pages * Page, trash->BufferDeviceAddress(),
                table.GuestOffset(), false);
        // Source and destination sizes differ, as in game descriptors.
        reinterpret_cast<u32*>(records->mapped_data.data())[64 + 2] = output->SizeBytes();
        const std::array<Buffer*, 3> resources{source.get(), output.get(), trash.get()};
        std::vector<double> times;
        for (unsigned n = 0; n < 26; ++n) {
            id<MTLCommandBuffer> command = nil;
            for (unsigned batch = 0; batch < 16; ++batch)
                command = Dispatch(f, kernel, table, *records, resources, Threads);
            f.Settle();
            assert(command.status == MTLCommandBufferStatusCompleted);
            if (n >= 6)
                times.push_back((command.GPUEndTime - command.GPUStartTime) * 1000.0 / 16.0);
        }
        const auto* result = reinterpret_cast<u32*>(output->mapped_data.data());
        for (u32 i = 0; i < Threads; ++i) {
            u32 expected = 0;
            for (u32 j = 0; j < 16; ++j) {
                const u32 page = (random ? (i * 104729 + j * 14713) : ((i >> 10) + j)) & 65535;
                const u32 index = (page % PhysicalPages) * (Page / 4) + (i & 1023);
                expected += data[index];
            }
            assert(result[i] == expected);
        }
        std::sort(times.begin(), times.end());
        const double median = (times[9] + times[10]) / 2, p95 = times[18];
        std::printf(
            "BDA benchmark: mode=%s pattern=%s gpu-median-ms=%.6f gpu-p95-ms=%.6f table-bytes=%llu "
            "leaf-count=%llu samples=%zu threads=%u lookups=%u checksum=passed\n",
            two ? "two-level" : "flat", random ? "random" : "local", median, p95,
            (unsigned long long)table.AllocatedBytes(), (unsigned long long)table.LeafCount(),
            times.size(), Threads, Threads * 16);
    }
    f.Settle();
}
int main(int argc, char** argv) {
    @autoreleasepool {
        setenv("BB_DIRECT_MEMORY_ACCESS", "1", 1);
        Fixture f;
        if (argc > 1 && std::strcmp(argv[1], "--bench") == 0) {
            for (bool random : {false, true}) {
                for (bool two : {false, true})
                    Benchmark(f, two, random);
                for (bool two : {true, false})
                    Benchmark(f, two, random);
            }
        } else {
            Correctness(f, 14);
            Correctness(f, 16);
            RenderUpdateOrdering(f);
        }
        f.Settle();
    }
}
