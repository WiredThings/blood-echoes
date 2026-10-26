// SPDX-License-Identifier: GPL-2.0-or-later
// Unit test of the pieces of renderer_metal/mtl_pipeline_cache (Metal::PipelineCache, plan P4.3a)
// that need no Liverpool: the shader profile, the pipeline capabilities and the serialized part
// of a graphics pipeline. The hooks themselves run with the rasterizer (P4.3b). Needs a Metal
// device, no window or game.
//
//   mtl-pipeline-core-test [profile.bin]
//
// `profile.bin` is the profile of a MoltenVK session's Vulkan pipeline cache
// (<user>/cache/<title id>/profile.bin: 16 bytes of versions, then Shader::Profile). When given
// (or found at macos/user/cache/CUSA03173/ under the repository), the Metal profile must equal it
// in every field the backend decides (the core adds the sparse page shift, paged buffers and the
// game's copy shader). Exit 0 when every check passed.
#import <Metal/Metal.h>
#include "video_core/renderer_metal/mtl_shader.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/renderer_metal/mtl_device_impl.h"
#include <filesystem>
#include <unistd.h>


#include <setjmp.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "common/serdes.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/specialization.h"
#include "video_core/renderer_metal/mtl_device.h"
#include "video_core/renderer_metal/mtl_pipeline_cache.h"

// The runtime services libbbgpu takes from bb-probe; this test has no guest process.
extern "C" {
__thread sigjmp_buf* runtime_fault_recover = nullptr;
uint64_t runtime_disabled_optimizations = 0;
uint64_t runtime_experiment_bits = 0;
}

static int failures = 0;
#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                          \
            ++failures;                                                                            \
        }                                                                                          \
    } while (0)

static void TestProfile(Metal::Device& device, const char* reference_path) {
    std::printf("profile\n");
    VideoCore::BackendLimits limits{};
    limits.storage_min_alignment = 16; // Metal::Runtime's
    const Shader::Profile profile = Metal::MakeShaderProfile(device, limits);
    CHECK(profile.subgroup_size == 32);
    CHECK(profile.supported_spirv == 0x00010600);
    CHECK(profile.max_viewport_width == 16384 && profile.max_viewport_height == 16384);
    CHECK(profile.max_shared_memory_size == 32768);
    CHECK(profile.support_int8 && profile.support_int16 && profile.support_int64);
    CHECK(profile.support_float16 && !profile.support_float64);
    CHECK(profile.supports_fragment_shader_barycentric);
    CHECK(profile.needs_buffer_offsets);
    CHECK(profile.supports_shader_stencil_export);
    CHECK(!profile.needs_manual_interpolation && !profile.needs_integer_interpolation_fix);
    limits.storage_min_alignment = 4;
    CHECK(!Metal::MakeShaderProfile(device, limits).needs_buffer_offsets);
    limits.storage_min_alignment = 16;

    const auto caps = Metal::MakePipelineCapabilities();
    CHECK(!caps.depth_clip_control && !caps.vertex_input_dynamic_state);
    CHECK(caps.geometry_stage && caps.tessellation); // tessellation: P4.4, geometry: P7.1
    CHECK(!caps.mixed_any_samples && !caps.mixed_depth_samples);

    std::ifstream in(reference_path, std::ios::binary);
    if (!in) {
        std::printf("  (no MoltenVK profile at %s: not compared)\n", reference_path);
        return;
    }
    std::vector<char> data((std::istreambuf_iterator<char>(in)), {});
    constexpr size_t header = 16;
    if (data.size() != header + sizeof(Shader::Profile)) {
        std::printf("  (%s has the size of another profile version: not compared)\n",
                    reference_path);
        return;
    }
    Shader::Profile reference{};
    std::memcpy(&reference, data.data() + header, sizeof(reference));
    // What the core adds.
    Shader::Profile mine = profile;
    mine.sparse_page_shift = reference.sparse_page_shift;
    mine.paged_buffers = reference.paged_buffers;
    mine.buffer_copy_shader_hash = reference.buffer_copy_shader_hash;
    CHECK(mine == reference);
    std::printf("  compared with %s: %s\n", reference_path, mine == reference ? "equal" : "DIFFERS");
}

static void TestSerialization() {
    std::printf("serialization\n");
    Metal::GraphicsSerialization written;
    written.vertex_attributes = {
        {.location = 0, .per_instance = false, .divisor = 1},
        {.location = 3, .per_instance = true, .divisor = 4},
        {.location = 7, .per_instance = true, .divisor = 0},
    };
    Serialization::Archive archive;
    written.Serialize(archive);
    // A written archive's offset is at its end: read from its taken-off container, as the core
    // reads entries from disk.
    Serialization::Archive in{archive.TakeOff()};
    Metal::GraphicsSerialization read;
    CHECK(read.Deserialize(in));
    CHECK(read.vertex_attributes == written.vertex_attributes);

    // More attributes than a draw can have: a damaged entry.
    Serialization::Archive damaged;
    Serialization::Writer writer{damaged};
    writer.Write(static_cast<uint32_t>(VideoCore::MaxVertexBufferCount + 1));
    Serialization::Archive damaged_in{damaged.TakeOff()};
    Metal::GraphicsSerialization rejected;
    bool thrown = false;
    try {
        rejected.Deserialize(damaged_in);
    } catch (const Serialization::CorruptData&) {
        thrown = true;
    }
    CHECK(thrown);

    // Cut short.
    Serialization::Archive cut;
    Serialization::Writer cut_writer{cut};
    cut_writer.Write(static_cast<uint32_t>(2));
    cut_writer.Write(static_cast<uint32_t>(1));
    Serialization::Archive cut_in{cut.TakeOff()};
    thrown = false;
    try {
        Metal::GraphicsSerialization incomplete;
        incomplete.Deserialize(cut_in);
    } catch (const Serialization::CorruptData&) {
        thrown = true;
    }
    CHECK(thrown);

    // Compute: nothing written, nothing read.
    Metal::ComputeSerialization compute;
    Serialization::Archive empty;
    compute.Serialize(empty);
    CHECK(empty.SizeBytes() == 0);
    CHECK(compute.Deserialize(empty));
}


// Review finding #6: a vertex shader with no resources of its own (the RectList / full-screen
// vertex shaders) is reused across start bindings (StageSpecialization::operator==), but the
// ClipPlanes buffer LowerUserClipPlanes lists in every vertex shader was declared in the shader,
// numbered for the pipeline the permutation was first made for, so the draws of another pipeline
// had no resource at that binding (Metal: "missing binding"). It is now declared only when the
// shader reads it, and a permutation that reads it is told apart by its start binding.
static void TestClipPlanes() {
    std::printf("clip planes\n");
    std::array<u32, Shader::ShaderParams::NumShaderUserData> user_data{};
    const Shader::ShaderParams params{user_data, {}, 0x1234};
    Shader::Profile profile{};

    // The pass: a vertex shader exporting a position, with and without enabled planes.
    const auto run_pass = [&](u32 mask, Shader::Info& info) {
        Shader::Pools pools;
        Shader::IR::Program program{info};
        Shader::IR::Block* block = pools.block_pool.Create(pools.inst_pool);
        program.blocks.push_back(block);
        Shader::IR::IREmitter ir{*block};
        for (u32 comp = 0; comp < 4; ++comp) {
            ir.SetAttribute(Shader::IR::Attribute::Position0, ir.Imm32(float(comp)), comp);
        }
        Shader::RuntimeInfo runtime_info{};
        runtime_info.hw_stage = Shader::HwStage::Vertex;
        runtime_info.hw.vs.user_clip_plane_mask = mask;
        Shader::Optimization::LowerUserClipPlanes(program, runtime_info);
        return block->size();
    };
    Shader::Info off(Shader::HwStage::Vertex, Shader::SwStage::Vertex, params);
    const size_t off_size = run_pass(0, off);
    CHECK(off.buffers.size() == 1 && off.buffers[0].buffer_type == Shader::BufferType::ClipPlanes);
    CHECK(off.buffers[0].used_types == Shader::IR::Type::Void); // listed, not declared
    CHECK(off_size == 4);
    Shader::Info on(Shader::HwStage::Vertex, Shader::SwStage::Vertex, params);
    const size_t on_size = run_pass(0x3, on);
    CHECK(on.buffers.size() == 1 && on.buffers[0].used_types == Shader::IR::Type::F32);
    CHECK(on_size > off_size);

    // The permutation identity: the same shader at two start bindings.
    const auto make = [&](const Shader::Info& info, u32 mask, u32 start) {
        Shader::RuntimeInfo runtime_info{};
        runtime_info.hw_stage = Shader::HwStage::Vertex;
        runtime_info.hw.vs.user_clip_plane_mask = mask;
        Shader::Backend::Bindings bindings{};
        bindings.buffer = bindings.unified = start;
        return Shader::StageSpecialization(info, runtime_info, profile, bindings);
    };
    const auto a = make(off, 0, 0), b = make(off, 0, 5);
    CHECK(a.bitset.none()); // nothing but the ClipPlanes buffer: the shortcut applies
    CHECK(a == b);          // one module serves every start (and declares no binding)
    const auto c = make(on, 0x3, 0), d = make(on, 0x3, 5);
    CHECK(!(c == d));       // the module reads the buffer at its own binding
    CHECK(c == make(on, 0x3, 0));
    CHECK(!(a == c));       // different runtime info
}

static void TestDirectComputeMetadata() {
    Metal::CompiledShader shader;
    CHECK(!shader.HasOnlyDirectComputeResources());
    shader.stage=Metal::ShaderStage::Compute;
    CHECK(shader.HasOnlyDirectComputeResources());
    shader.from_disk_cache=true;CHECK(shader.HasOnlyDirectComputeResources());
    shader.uses_device_address=true;CHECK(!shader.HasOnlyDirectComputeResources());
    shader.uses_device_address=false;shader.uses_bda_pagetable=true;
    CHECK(shader.HasOnlyDirectComputeResources());shader.uses_bda_pagetable=false;
    for(auto kind:{Metal::CompileVariant::Kind::TessVertex,Metal::CompileVariant::Kind::TessControl,
                   Metal::CompileVariant::Kind::TessEval,Metal::CompileVariant::Kind::GeometryKernel}) {
        shader.variant.kind=kind;CHECK(!shader.HasOnlyDirectComputeResources());
    }
    shader.variant.kind=Metal::CompileVariant::Kind::Plain;
    for(auto stage:{Metal::ShaderStage::Vertex,Metal::ShaderStage::Fragment,Metal::ShaderStage::Unknown}) {
        shader.stage=stage;CHECK(!shader.HasOnlyDirectComputeResources());
    }
    std::puts("direct compute residency: plain and cached metadata accepted; unknown, graphics, synthesized, physical-pointer cases rejected; unused page-table name does not imply pointer access");
}

static void TestRenderArchiveRebuild(Metal::Device& device) {
    const auto folder=std::filesystem::temp_directory_path()/
        ("bb-render-archive-stages-"+std::to_string(getpid()));
    CHECK(!std::filesystem::exists(folder));
    if(std::filesystem::exists(folder)) return;
    std::filesystem::create_directories(folder);
    const auto path=folder/"pipelines.binaryarchive";
    const auto active_path=folder/"pipelines-v2.binaryarchive";
    auto native=device.GetImpl().device;
    NSError* error=nil;
    auto vl=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 main0(uint i [[vertex_id]]) {float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0.5,1);}"
        options:nil error:&error];
    auto fl=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "fragment float4 main0(){return float4(0.25,0.5,0.75,1);}"
        options:nil error:&error];
    auto fl2=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "fragment float4 main0(){return float4(0.75,0.5,0.25,1);}"
        options:nil error:&error];
    CHECK(vl&&fl&&fl2);if(!vl||!fl||!fl2)return;
    Metal::CompiledShader vs,fs,fs2;
    vs.stage=Metal::ShaderStage::Vertex;fs.stage=fs2.stage=Metal::ShaderStage::Fragment;
    vs.library=(__bridge_retained void*)vl;
    vs.function=(__bridge_retained void*)[vl newFunctionWithName:@"main0"];
    fs.library=(__bridge_retained void*)fl;
    fs.function=(__bridge_retained void*)[fl newFunctionWithName:@"main0"];
    fs2.library=(__bridge_retained void*)fl2;
    fs2.function=(__bridge_retained void*)[fl2 newFunctionWithName:@"main0"];
    auto descriptor=[MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction=(__bridge id<MTLFunction>)vs.function;
    descriptor.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA8Unorm;
    // Preserve an older mixed-format archive while using a safe fresh-writer generation.
    {
        auto legacy=[native newBinaryArchiveWithDescriptor:[MTLBinaryArchiveDescriptor new] error:&error];
        CHECK([legacy addRenderPipelineFunctionsWithDescriptor:descriptor error:&error]);
        CHECK([legacy serializeToURL:[NSURL fileURLWithPath:@(path.c_str())] error:&error]);
    }
    Vulkan::GraphicsPipelineKey key{};
    key.stage_hashes[u32(Shader::SwStage::Vertex)]=0x12345;
    key.stage_hashes[u32(Shader::SwStage::Fragment)]=0x6789;
    key.prim_type=AmdGpu::PrimitiveType::TriangleList;
    key.num_samples=1;key.num_color_attachments=1;key.mrt_mask=1;
    key.color_buffers[0].data_format=AmdGpu::DataFormat::Format8_8_8_8;
    key.color_buffers[0].num_format=AmdGpu::NumberFormat::Unorm;
    key.write_masks[0]=vk::ColorComponentFlagBits::eR|vk::ColorComponentFlagBits::eG|
                       vk::ColorComponentFlagBits::eB|vk::ColorComponentFlagBits::eA;
    auto vertex_only=key;vertex_only.stage_hashes[u32(Shader::SwStage::Fragment)]=0;
    const char* previous=std::getenv("BB_MTL_PERSIST_WARM_ARCHIVE");
    const bool had_previous=previous!=nullptr;const std::string previous_value=previous?previous:"";
    setenv("BB_MTL_PERSIST_WARM_ARCHIVE","1",1);
    const auto draw=[&](const Metal::RenderPipeline* pipeline,bool inverse=false) {
        CHECK(pipeline&&pipeline->state);if(!pipeline)return;
        auto td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:8 height:8 mipmapped:NO];
        td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
        auto texture=[native newTextureWithDescriptor:td];
        auto pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;
        pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        auto command=[[native newCommandQueue] commandBuffer];auto encoder=[command renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:(__bridge id<MTLRenderPipelineState>)pipeline->state];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [encoder endEncoding];[command commit];[command waitUntilCompleted];
        CHECK(command.status==MTLCommandBufferStatusCompleted);
        unsigned char pixel[4]{};
        [texture getBytes:pixel bytesPerRow:4 fromRegion:MTLRegionMake2D(4,4,1,1) mipmapLevel:0];
        CHECK(pixel[0]==(inverse?191:64)&&pixel[1]==128&&pixel[2]==(inverse?64:191)&&pixel[3]==255);
    };
    {
        Metal::PsoCache cache{device,folder};CHECK(cache.ArchiveLoaded());
        auto plain=cache.GetRenderPipeline(vertex_only,&vs,nullptr,{});
        auto colored=cache.GetRenderPipeline(key,&vs,&fs,{});
        CHECK(plain&&colored);
        if(plain&&colored)CHECK(plain->vertex_function==vs.function&&colored->vertex_function==vs.function);
        NSData* legacy=[NSData dataWithContentsOfFile:@(path.c_str())];
        draw(colored);CHECK(cache.Flush());
        CHECK([legacy isEqualToData:[NSData dataWithContentsOfFile:@(path.c_str())]]);
        // The legacy mixed archive remains byte-identical and usable for rollback.
        auto ad=[MTLBinaryArchiveDescriptor new];ad.url=[NSURL fileURLWithPath:@(path.c_str())];
        auto retained=[native newBinaryArchiveWithDescriptor:ad error:&error];
        descriptor.binaryArchives=@[retained];
        CHECK([native newRenderPipelineStateWithDescriptor:descriptor
            options:MTLPipelineOptionFailOnBinaryArchiveMiss reflection:nil error:&error]!=nil);
        cache.ReleaseWarmArchive();CHECK(cache.GetRenderPipeline(key,&vs,&fs,{})==colored);draw(colored);
    }
    {
        Metal::PsoCache cache{device,folder};CHECK(cache.ArchiveLoaded());
        CHECK(cache.GetRenderPipeline(vertex_only,&vs,nullptr,{}));
        draw(cache.GetRenderPipeline(key,&vs,&fs,{}));
        CHECK(cache.GetCounters().archive_hits==2);
        auto other=key;other.stage_hashes[u32(Shader::SwStage::Fragment)]+=1;
        draw(cache.GetRenderPipeline(other,&vs,&fs2,{}),true);
        CHECK(cache.Flush());cache.ReleaseWarmArchive();
    }
    {
        Metal::PsoCache cache{device,folder};CHECK(cache.ArchiveLoaded());
        auto other=key;other.stage_hashes[u32(Shader::SwStage::Fragment)]+=1;
        draw(cache.GetRenderPipeline(other,&vs,&fs2,{}),true);
        CHECK(cache.GetCounters().archive_hits==1);
        // Force a genuinely new render state, then deny writes to this TEST directory.
        auto library=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;fragment float4 main0(){return float4(0,1,0,1);}"
            options:nil error:&error];
        Metal::CompiledShader third;third.stage=Metal::ShaderStage::Fragment;
        third.library=(__bridge_retained void*)library;
        third.function=(__bridge_retained void*)[library newFunctionWithName:@"main0"];
        other.stage_hashes[u32(Shader::SwStage::Fragment)]+=1;
        CHECK(cache.GetRenderPipeline(other,&vs,&third,{}));
        NSData* original=[NSData dataWithContentsOfFile:@(active_path.c_str())];
        std::filesystem::permissions(folder,std::filesystem::perms::owner_read|std::filesystem::perms::owner_exec);
        CHECK(!cache.Flush());
        std::filesystem::permissions(folder,std::filesystem::perms::owner_all);
        CHECK([original isEqualToData:[NSData dataWithContentsOfFile:@(active_path.c_str())]]);
        // A failed driver serialization can poison that compiler object; release it.
        cache.ReleaseWarmArchive();
    }
    {
        const auto bad=folder/"invalid";std::filesystem::create_directory(bad);
        const auto file=bad/"pipelines-v2.binaryarchive";std::ofstream(file)<<"invalid-test-archive";
        NSData* original=[NSData dataWithContentsOfFile:@(file.c_str())];
        {Metal::PsoCache cache{device,bad};CHECK(!cache.ArchiveLoaded());}
        CHECK([original isEqualToData:[NSData dataWithContentsOfFile:@(file.c_str())]]);
    }
    if(had_previous)setenv("BB_MTL_PERSIST_WARM_ARCHIVE",previous_value.c_str(),1);
    else unsetenv("BB_MTL_PERSIST_WARM_ARCHIVE");
    const auto prefix="bbport-metal-read-"+std::to_string(getpid())+"-";
    for(const auto& entry:std::filesystem::directory_iterator(std::filesystem::temp_directory_path()))
        CHECK(!entry.path().filename().string().starts_with(prefix));
    std::filesystem::remove_all(folder);
    std::puts("render archive: legacy file retained, incremental rebuild/reload hits, GPU pixels/lifetime unchanged, failed writes and invalid inputs preserved");
}

static void TestWarmArchiveLifetime(Metal::Device& device) {
    const auto folder=std::filesystem::temp_directory_path()/
        ("bb-warm-archive-lifetime-"+std::to_string(getpid()));
    CHECK(!std::filesystem::exists(folder));
    if(std::filesystem::exists(folder))return;
    auto native=device.GetImpl().device;
    NSError* error=nil;
    auto library=[native newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "kernel void write_value(device uint* dst [[buffer(0)]], constant uint& x [[buffer(1)]]) {dst[0]=x;}"
        options:nil error:&error];
    CHECK(library!=nil);if(!library)return;
    Metal::CompiledShader shader;
    shader.stage=Metal::ShaderStage::Compute;shader.local_size={1,1,1};
    shader.library=(__bridge_retained void*)library;
    shader.function=(__bridge_retained void*)[library newFunctionWithName:@"write_value"];
    Vulkan::ComputePipelineKey key{};key.value=0x12345;
    auto queue=[native newCommandQueue];
    auto output=[native newBufferWithLength:16 options:MTLResourceStorageModeShared];
    const auto run=[&](const Metal::ComputePipeline* pipeline,u32 value) {
        CHECK(pipeline&&pipeline->state);if(!pipeline)return;
        auto command=[queue commandBuffer];auto encoder=[command computeCommandEncoder];
        [encoder setComputePipelineState:(__bridge id<MTLComputePipelineState>)pipeline->state];
        [encoder setBuffer:output offset:0 atIndex:0];[encoder setBytes:&value length:4 atIndex:1];
        [encoder dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];
        [encoder endEncoding];[command commit];[command waitUntilCompleted];
        CHECK(command.status==MTLCommandBufferStatusCompleted);
        CHECK(*(const u32*)output.contents==value);
    };
    const char* previous = std::getenv("BB_MTL_PERSIST_WARM_ARCHIVE");
    const bool had_previous = previous != nullptr;
    const std::string previous_value = previous ? previous : "";
    setenv("BB_MTL_PERSIST_WARM_ARCHIVE", "1", 1);
    {
        Metal::PsoCache cache{device,folder};
        auto pipeline=cache.GetComputePipeline(key,&shader);CHECK(pipeline);
        CHECK(cache.Flush());CHECK(std::filesystem::file_size(cache.ArchivePath())>0);
        cache.ReleaseWarmArchive();
        CHECK(cache.GetComputePipeline(key,&shader)==pipeline);
        CHECK(cache.GetCounters().compute_pipelines==1);run(pipeline,0x12345678);
        auto late=key;late.value+=1;run(cache.GetComputePipeline(late,&shader),0x87654321);
        CHECK(cache.GetCounters().compute_pipelines==2);
    }
    {
        Metal::PsoCache cache{device,folder};CHECK(cache.ArchiveLoaded());
        auto pipeline=cache.GetComputePipeline(key,&shader);CHECK(pipeline);
        CHECK(cache.GetCounters().archive_hits==1);
        cache.ReleaseWarmArchive();CHECK(cache.GetComputePipeline(key,&shader)==pipeline);
        CHECK(cache.GetCounters().compute_pipelines==1);run(pipeline,0xaabbccdd);
    }
    setenv("BB_MTL_PERSIST_WARM_ARCHIVE", "0", 1);
    const auto archive_file=folder/"pipelines-v2.binaryarchive";
    NSData* saved_archive=[NSData dataWithContentsOfFile:@(archive_file.c_str())];
    CHECK(saved_archive!=nil);
    {
        Metal::PsoCache cache{device,folder};CHECK(!cache.ArchiveLoaded());
        auto pipeline=cache.GetComputePipeline(key,&shader);CHECK(pipeline);
        CHECK(cache.GetCounters().archive_hits==0);run(pipeline,0xabc01234);
        // Do not release the archive: OFF must suppress destructor serialization too.
    }
    CHECK([saved_archive isEqualToData:[NSData dataWithContentsOfFile:@(archive_file.c_str())]]);
    {
        const auto no_auto_flush=folder/"without-auto-persistence";
        {
            Metal::PsoCache cache{device,no_auto_flush};
            run(cache.GetComputePipeline(key,&shader),0x13572468);
        }
        CHECK(!std::filesystem::exists(no_auto_flush/"pipelines-v2.binaryarchive"));
    }
    {
        const auto no_flush=folder/"without-persistence";
        Metal::PsoCache cache{device,no_flush};
        auto pipeline=cache.GetComputePipeline(key,&shader);CHECK(pipeline);
        cache.ReleaseWarmArchive();CHECK(cache.GetComputePipeline(key,&shader)==pipeline);
        CHECK(cache.GetCounters().compute_pipelines==1);run(pipeline,0xfeed1234);
        CHECK(!std::filesystem::exists(cache.ArchivePath()));
    }
    if (had_previous) setenv("BB_MTL_PERSIST_WARM_ARCHIVE",previous_value.c_str(),1);
    else unsetenv("BB_MTL_PERSIST_WARM_ARCHIVE");
    std::filesystem::remove_all(folder);
    std::puts("warm archive: retained PSO identity/GPU output, late creation, no-persistence release and next-start hit passed");
}

int main(int argc, char** argv) {
    @autoreleasepool {
        std::string reference = argc > 1 ? argv[1] : "";
        if (reference.empty()) {
            // The checkout this test was built from (__FILE__ is its absolute path).
            const std::string source = __FILE__;
            reference = source.substr(0, source.rfind("/tests/")) +
                        "/macos/user/cache/CUSA03173/profile.bin";
        }
        Metal::Device device;
        TestProfile(device, reference.c_str());
        TestWarmArchiveLifetime(device);
        TestRenderArchiveRebuild(device);
        TestSerialization();
        TestDirectComputeMetadata();
        TestClipPlanes();
    }
    std::printf(failures ? "%d check(s) FAILED\n" : "all checks passed\n", failures);
    return failures ? 1 : 0;
}
