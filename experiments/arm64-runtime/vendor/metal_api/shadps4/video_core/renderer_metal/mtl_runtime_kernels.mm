#include "video_core/renderer_metal/mtl_local_library.h"
// SPDX-License-Identifier: GPL-2.0-or-later
// The caches' own GPU work on Metal (plan P2.4, docs/metal/cache_backend_design.md group C): the
// D11 host shaders for detiling/tiling and the fault buffer parser, and the fullscreen passes of
// the MSAA copies. Counterparts: vk_tile_kernels.cpp, vk_fault_parser.cpp, vk_blit_helper.cpp.

#include <string>
#include <cstring>
#include <memory>
#include <chrono>
#include <sys/sysctl.h>
#include "common/scope_exit.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_metal/mtl_pso_cache.h"
#include "video_core/texture_cache/tile_manager.h"
#include "bb_tiler_bounds.h"

#include "common/assert.h"
#include "common/logging/log.h"
#include "video_core/renderer_metal/mtl_runtime_impl.h"
#include "video_core/renderer_metal/mtl_format_convert_source.h"
#include "video_core/renderer_metal/mtl_texture_tiling.h"
#include "video_core/frame_capture.h"
#include "video_core/renderer_metal/mtl_scene_targets.h"

#include "video_core/host_shaders_msl/color_to_ms_depth_frag_msl.h"
#include "video_core/host_shaders_msl/fault_buffer_process_comp_msl.h"
#include "video_core/host_shaders_msl/fs_tri_vert_msl.h"
#include "video_core/host_shaders_msl/upscale_merge_comp_msl.h"
#include "video_core/host_shaders_msl/post_process_frag_msl.h"
#include "video_core/host_shaders_msl/camera_motion_comp_msl.h"
#include "video_core/host_shaders_msl/ms_image_blit_frag_msl.h"
#include "video_core/host_shaders_msl/ms_image_blit_msaa_frag_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_macro_96_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_128_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_16_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_32_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_64_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_8_comp_msl.h"
#include "video_core/host_shaders_msl/tiling_micro_96_comp_msl.h"

namespace Metal {
namespace H = HostShaders;
namespace {

// A 32 bit pattern that is not four equal bytes (those use the blit encoder's fillBuffer).
NSString* const FillSource = @"#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "kernel void fill_u32(device uint* dst [[buffer(0)]], constant uint& value [[buffer(1)]],\n"
    "                     constant uint& count [[buffer(2)]], uint id [[thread_position_in_grid]]) {\n"
    "    if (id < count) {\n"
    "        dst[id] = value;\n"
    "    }\n"
    "}\n";

NSString* const InlineUpdateSource=@"#include <metal_stdlib>\nusing namespace metal;\n"
    "kernel void inline_update(device uint* dst [[buffer(0)]], "
    "constant uint* src [[buffer(1)]], uint i [[thread_position_in_grid]]) { dst[i] = src[i]; }\n";
NSString* const RenderInlineUpdateSource=@"#include <metal_stdlib>\nusing namespace metal;\nvertex void update(device uint* dst[[buffer(0)]],constant uint* src[[buffer(1)]],uint i[[vertex_id]]){dst[i]=src[i];}\n";

MTLCompileOptions* CompileOptions() {
    // The options of mtl_shader.mm: Metal 3.1, no fast math (the host shaders are SPIR-V ports).
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    options.mathMode = MTLMathModeSafe;
    return options;
}

const H::Shader& TilingShader(u32 num_bits, bool is_macro) {
    switch (num_bits) {
    case 8:
        return is_macro ? H::TILING_MACRO_8_COMP : H::TILING_MICRO_8_COMP;
    case 16:
        return is_macro ? H::TILING_MACRO_16_COMP : H::TILING_MICRO_16_COMP;
    case 32:
        return is_macro ? H::TILING_MACRO_32_COMP : H::TILING_MICRO_32_COMP;
    case 64:
        return is_macro ? H::TILING_MACRO_64_COMP : H::TILING_MICRO_64_COMP;
    case 96:
        return is_macro ? H::TILING_MACRO_96_COMP : H::TILING_MICRO_96_COMP;
    case 128:
        return is_macro ? H::TILING_MACRO_128_COMP : H::TILING_MICRO_128_COMP;
    default:
        UNREACHABLE_MSG("Unsupported tiling pixel width {}", num_bits);
    }
}

std::string ErrorText(NSError* error) {
    return error ? error.localizedDescription.UTF8String : "unknown";
}
} // namespace

HostKernels::HostKernels(Device::Impl& dev_, Scheduler& scheduler_)
    : dev{dev_}, scheduler{scheduler_} {}

HostKernels::~HostKernels() = default;

#include "video_core/renderer_metal/mtl_host_pso_prepare.inc"

u32 HostKernels::BufferSlot(const H::Shader& shader, u32 binding) {
    for (const H::Binding& b : shader.bindings) {
        if (b.kind == H::Binding::Kind::Buffer && b.set == 0 && b.binding == binding) {
            return b.msl_index;
        }
    }
    UNREACHABLE_MSG("Host shader without buffer binding {}", binding);
}

id<MTLLibrary> HostKernels::Library(const H::Shader& shader, MTLFunctionConstantValues* values, bool source_only, id<MTLFunction> __strong* prepared,LocalLibrary::Origin* origin) {
    if (const auto it = libraries.find(&shader); !source_only && it != libraries.end()) {
        if(origin)*origin=it->second.origin;
        return it->second.library;
    }
    if(origin)*origin={};
    LocalLibrary::Origin actual_origin;
    NSString* source = [[NSString alloc] initWithBytes:shader.msl.data()
                                                length:shader.msl.size()
                                              encoding:NSUTF8StringEncoding];
    NSError* error = nil;
    id<MTLLibrary> library = LocalLibrary::Build(dev.device, source, CompileOptions(), &error,
        [&](id<MTLLibrary> local) {
            NSString* entry = [[NSString alloc] initWithBytes:shader.entry.data()
                length:shader.entry.size() encoding:NSUTF8StringEncoding];
            id<MTLFunction> function = shader.spec_constants.empty()
                ? [local newFunctionWithName:entry]
                : [local newFunctionWithName:entry constantValues:values error:nil];
            if (prepared) *prepared = function;
            return bool(function);
        }, !source_only, &actual_origin);
    if(warming&&!library)return nil;
    ASSERT_MSG(library, "Host shader '{}' did not compile: {}", std::string(shader.entry),
               ErrorText(error));
    if(origin)*origin=actual_origin;
    if(!warming)libraries.insert_or_assign(&shader,LibraryRecord{library,std::move(actual_origin)});
    return library;
}

id<MTLFunction> HostKernels::Function(const H::Shader& shader, const u32* constants, u32 count,LocalLibrary::Origin* origin) {
    if(origin)*origin={};
    LocalLibrary::Origin actual_origin;
    NSString* entry = [[NSString alloc] initWithBytes:shader.entry.data()
                                               length:shader.entry.size()
                                             encoding:NSUTF8StringEncoding];
    id<MTLFunction> function = nil;
    if (shader.spec_constants.empty()) {
        id<MTLLibrary> library = Library(shader, nil, false, &function,&actual_origin);
        if (!function) function = [library newFunctionWithName:entry];
        if (!function) {
            library = Library(shader, nil, true, &function,&actual_origin);
            if (!function) function = [library newFunctionWithName:entry];
        }
        if(warming&&!function)return nil;
        ASSERT_MSG(function, "Host shader function '{}' not found", std::string(shader.entry));
        if(function&&origin)*origin=actual_origin;
        return function;
    }
    MTLFunctionConstantValues* values = [MTLFunctionConstantValues new];
    for (const H::SpecConstant& constant : shader.spec_constants) {
        if (constant.id >= count) {
            continue; // the default of the SPIR-V
        }
        u32 value = constants[constant.id];
        switch (constant.type) {
        case H::SpecConstant::Type::Bool: {
            bool flag = value != 0;
            [values setConstantValue:&flag type:MTLDataTypeBool atIndex:constant.id];
            break;
        }
        case H::SpecConstant::Type::U32:
            [values setConstantValue:&value type:MTLDataTypeUInt atIndex:constant.id];
            break;
        case H::SpecConstant::Type::I32:
            [values setConstantValue:&value type:MTLDataTypeInt atIndex:constant.id];
            break;
        case H::SpecConstant::Type::F32:
            [values setConstantValue:&value type:MTLDataTypeFloat atIndex:constant.id];
            break;
        }
    }
    NSError* error = nil;
    id<MTLLibrary> library = Library(shader, values, false, &function,&actual_origin);
    if (!function) function = [library newFunctionWithName:entry constantValues:values error:&error];
    if (!function) {
        library = Library(shader, values, true, &function,&actual_origin);
        if (!function) function = [library newFunctionWithName:entry constantValues:values error:&error];
    }
    if(warming&&!function)return nil;
    ASSERT_MSG(function, "Host shader function '{}' with constants failed: {}",
               std::string(shader.entry), ErrorText(error));
    if(function&&origin)*origin=actual_origin;
    return function;
}

id<MTLComputePipelineState> HostKernels::ComputePipeline(const H::Shader& shader,
                                                         const u32* constants, u32 count,
                                                         NSString* label, HostPso::Invocation invocation) {
    std::array<LocalLibrary::Origin,1> origins;
    id<MTLFunction> function = Function(shader, constants, count,&origins[0]);
    NSError* error = nil;
    auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);
    descriptor.computeFunction=function;
    id<MTLComputePipelineState> pipeline = function?MakeCompute(descriptor,invocation,origins,&error):nil;
    if(warming&&!pipeline)return nil;
    ASSERT_MSG(pipeline, "Compute pipeline '{}' failed: {}", label.UTF8String, ErrorText(error));
    return pipeline;
}

id<MTLComputePipelineState> HostKernels::Tiling(const VideoCore::TilingDispatch& dispatch,
                                                const H::Shader*& shader_out) {
    const H::Shader& shader = TilingShader(dispatch.key.num_bits, dispatch.is_macro);
    shader_out = &shader;
    ASSERT(dispatch.num_constants <= dispatch.constants.size());
    const bool bounded = VideoCore::TilerBounds::Enabled(dispatch);
    const auto constants = VideoCore::TilerBounds::Constants(
        std::span(dispatch.constants).first(dispatch.num_constants), shader.spec_constants, bounded);
    TilingKey key{dispatch.key.num_bits, dispatch.is_macro, {}, bounded};
    // Canonicalize the actual defaults, so omitted parameters cannot collide with
    // a caller that explicitly supplies a different value for the same constant.
    std::copy_n(constants.begin(), 12, std::get<2>(key).begin());
    if (const auto it = tiling.find(key); !warming && it != tiling.end()) {
        return it->second;
    }
    NSString* label = [NSString stringWithFormat:@"tiling %@ %u %s",
                                                 dispatch.is_macro ? @"macro" : @"micro",
                                                 dispatch.key.num_bits,
                                                 dispatch.key.is_tiler ? "tiler" : "detiler"];
    LOG_INFO(Render, "Creating tiling pipeline {}", label.UTF8String);
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::Tiling;
    invocation.bits=dispatch.key.num_bits;invocation.macro=dispatch.is_macro;invocation.bounded=bounded;
    invocation.count=constants.size();invocation.provided=constants;
    id<MTLComputePipelineState> pipeline =
        ComputePipeline(shader, constants.data(), constants.size(), label,invocation);
    if(!warming)tiling.emplace(key, pipeline);
    return pipeline;
}

id<MTLComputePipelineState> HostKernels::TextureTiling(const VideoCore::TilingDispatch& dispatch, bool proxy) {
    ASSERT(dispatch.num_constants <= dispatch.constants.size());
    const auto canonical=VideoCore::TilerBounds::Constants(std::span(dispatch.constants).first(dispatch.num_constants),TilingShader(dispatch.key.num_bits,true).spec_constants,VideoCore::TilerBounds::Enabled(dispatch));
    std::array<u32,12> key_values{};std::copy_n(canonical.begin(),12,key_values.begin());
    const auto key=std::make_pair(proxy,TilingKey{dispatch.key.num_bits, true, key_values, VideoCore::TilerBounds::Enabled(dispatch)});
    if(const auto i=texture_tiling.find(key);!warming&&i!=texture_tiling.end())return i->second;
    if(!warming&&texture_tiling.size()>=32)return nil;
    const auto& shader=TilingShader(dispatch.key.num_bits,true);
    const auto source=TextureTilingSource(shader.msl,dispatch.key.num_bits==64);
    if(source.empty()) {if(!warming)texture_tiling.emplace(key,nil);return nil;}
    NSError* error=nil;
    MTLFunctionConstantValues* values=[MTLFunctionConstantValues new];
    for(const auto& spec:shader.spec_constants) {
        if(spec.id>=dispatch.num_constants)continue;
        if(spec.type==H::SpecConstant::Type::Bool) {
            const bool value=dispatch.constants[spec.id]!=0;
            [values setConstantValue:&value type:MTLDataTypeBool atIndex:spec.id];
        } else {
            const u32 value=dispatch.constants[spec.id];
            [values setConstantValue:&value type:MTLDataTypeUInt atIndex:spec.id];
        }
    }
    const bool bounded=VideoCore::TilerBounds::Enabled(dispatch);
    [values setConstantValue:&bounded type:MTLDataTypeBool atIndex:13];
    const u32 scale=proxy?2u:1u;[values setConstantValue:&scale type:MTLDataTypeUInt atIndex:12];
    id<MTLFunction> function=nil;
    std::array<LocalLibrary::Origin,1> origins;
    auto library=LocalLibrary::Build(dev.device, @(source.c_str()), CompileOptions(), &error,
        [&](id<MTLLibrary> local) {
            function=[local newFunctionWithName:@"main0" constantValues:values error:nil];
            return bool(function);
        },true,&origins[0]);
    if(!function&&library)function=[library newFunctionWithName:@"main0" constantValues:values error:&error];
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::TextureTiling;
    invocation.bits=dispatch.key.num_bits;invocation.macro=1;invocation.proxy=proxy;invocation.bounded=bounded;invocation.count=dispatch.num_constants;
    std::copy_n(dispatch.constants.begin(),dispatch.num_constants,invocation.provided.begin());
    auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);descriptor.computeFunction=function;
    auto pipeline=function?MakeCompute(descriptor,invocation,origins,&error):nil;
    if(!pipeline)LOG_WARNING(Render,"Texture tiling unavailable; original staging retained: {}",ErrorText(error));
    if(!warming)texture_tiling.emplace(key,pipeline);
    return pipeline;
}

void HostKernels::ConfigureFaultParser(u32 page_bits,u32 max_faults){
    fault_arguments={page_bits,max_faults};fault_known=true;fault_with_cache=false;fault_parser=nil;
}
id<MTLComputePipelineState> HostKernels::FaultParser(){
    if(!fault_parser&&fault_known){const bool ok=CreateFaultParser(fault_arguments[0],fault_arguments[1]);ASSERT_MSG(ok,"Fault parser actual creation failed");}
    return fault_parser;
}
bool HostKernels::CreateFaultParser(u32 page_bits, u32 max_faults) {
    const std::array<u32, 2> constants{page_bits, max_faults};
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::FaultParser;invocation.count=2;
    invocation.provided[0]=page_bits;invocation.provided[1]=max_faults;
    auto result=ComputePipeline(H::FAULT_BUFFER_PROCESS_COMP, constants.data(),
                                   static_cast<u32>(constants.size()), @"Fault Buffer Parser",invocation);
    if (!result) return false;
    if(!warming){fault_parser=result;fault_known=true;fault_arguments=constants;fault_with_cache=bool(pso_cache);}
    return true;
}

id<MTLComputePipelineState> HostKernels::InlineUpdate() {
    if(!warming&&inline_update_tried)return inline_update;
    const bool previous_tried=inline_update_tried;inline_update_tried=true;
    SCOPE_EXIT{if(warming)inline_update_tried=previous_tried;};
    @autoreleasepool {
      std::array<LocalLibrary::Origin,1> origins;
      NSError*error=nil;auto library=LocalLibrary::Build(dev.device,InlineUpdateSource,CompileOptions(),&error,[](id<MTLLibrary>local){return LocalLibrary::PlainEntries(local,{@"inline_update"});},true,&origins[0]);
      auto function=library?[library newFunctionWithName:@"inline_update"]:nil;
      HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::InlineUpdate;
      auto*descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);descriptor.computeFunction=function;
      auto result=function?MakeCompute(descriptor,invocation,origins,&error):nil;
      if(!warming)inline_update=result;
      if(!result)LOG_WARNING(Render,"Inline update unavailable; retaining staging copy: {}",ErrorText(error));
      return result;
    }
}
id<MTLRenderPipelineState> HostKernels::RenderInlineUpdate() {
    if(!warming&&render_inline_update_tried)return render_inline_update;
    const bool previous_tried=render_inline_update_tried;render_inline_update_tried=true;
    SCOPE_EXIT{if(warming)render_inline_update_tried=previous_tried;};
    @autoreleasepool {
      std::array<LocalLibrary::Origin,1> origins;
      NSError*error=nil;auto library=LocalLibrary::Build(dev.device,RenderInlineUpdateSource,CompileOptions(),&error,[](id<MTLLibrary>local){return LocalLibrary::PlainEntries(local,{@"update"});},true,&origins[0]);
      HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::RenderInlineUpdate;
      auto*descriptor=(MTLRenderPipelineDescriptor*)Descriptor(invocation);descriptor.vertexFunction=library?[library newFunctionWithName:@"update"]:nil;
      auto result=descriptor.vertexFunction?MakeRender(descriptor,invocation,origins,&error):nil;
      if(!warming)render_inline_update=result;
      if(!result)LOG_WARNING(Render,"Render inline update unavailable; retaining compute: {}",ErrorText(error));
      return result;
    }
}

id<MTLComputePipelineState> HostKernels::FillU32() {
    if (warming || !fill_u32) {
        NSError* error = nil;
        std::array<LocalLibrary::Origin,1> origins;
        id<MTLLibrary> library = Metal::LocalLibrary::Build(dev.device, FillSource, CompileOptions(), &error, [](id<MTLLibrary> local) { return Metal::LocalLibrary::PlainEntries(local, {@"fill_u32"}); },true,&origins[0]);
        if(warming&&!library)return nil;
        ASSERT_MSG(library, "fill_u32 did not compile: {}", ErrorText(error));
        HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::Fill;
        auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);descriptor.computeFunction=[library newFunctionWithName:@"fill_u32"];
        auto result=MakeCompute(descriptor,invocation,origins,&error);
        if(warming)return result;
        fill_u32=result;
        ASSERT_MSG(fill_u32, "fill_u32 pipeline failed: {}", ErrorText(error));
    }
    return fill_u32;
}

id<MTLComputePipelineState> HostKernels::UpscaleMerge(NSError** error) {
    // The same shader, zero provided constants and CompileOptions as the previous
    // Upscaler::MergePipeline call to Function. Preserve its nullable PSO policy.
    std::array<LocalLibrary::Origin,1> origins;
    id<MTLFunction> function=Function(H::UPSCALE_MERGE_COMP,nullptr,0,&origins[0]);
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::UpscaleMerge;
    auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);
    descriptor.computeFunction=function;
    return function?MakeCompute(descriptor,invocation,origins,error):nil;
}

id<MTLComputePipelineState> HostKernels::FormatConversionPipeline(bool depth,NSError** error) {
    // Preserve the original Converter's language3.1 / Safe options and nil
    // function constants. Both entry checks preserve the old local-library gate.
    LocalLibrary::Origin origin;
    auto options=CompileOptions();
    id<MTLLibrary> library=LocalLibrary::Build(dev.device,FormatConvert::KernelSource,options,error,
        [](id<MTLLibrary> local){return LocalLibrary::PlainEntries(local,{@"rgb_convert",@"depth_convert"});},true,&origin);
    if(!library)return nil;
    id<MTLFunction> function=[library newFunctionWithName:(depth?@"depth_convert":@"rgb_convert")];
    if(!function)return nil;
    HostPso::Invocation invocation{};invocation.kind=depth?HostPso::Kind::FormatDepth:HostPso::Kind::FormatRgb;
    auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);
    descriptor.computeFunction=function;
    const std::array<LocalLibrary::Origin,1> origins{origin};
    return MakeCompute(descriptor,invocation,origins,error);
}

id<MTLComputePipelineState> HostKernels::CameraMotionPipeline(NSError** error) {
    // Same generated source, original Host3.1/Safe options and no provided constants.
    // The caller retains its original camera pipeline slot; warming is temporary.
    std::array<LocalLibrary::Origin,1> origins;
    id<MTLFunction> function=Function(H::CAMERA_MOTION_COMP,nullptr,0,&origins[0]);
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::CameraMotion;
    auto* descriptor=(MTLComputePipelineDescriptor*)Descriptor(invocation);
    descriptor.computeFunction=function;
    return function?MakeCompute(descriptor,invocation,origins,error):nil;
}

id<MTLRenderPipelineState> HostKernels::PostProcessPipeline(MTLPixelFormat format,NSError** error,
    id<MTLFunction> shared_vertex,const LocalLibrary::Origin* shared_origin) {
    std::array<LocalLibrary::Origin,2> origins;
    id<MTLFunction> vertex=shared_vertex;
    if(vertex){if(shared_origin)origins[0]=*shared_origin;}
    else vertex=Function(H::FS_TRI_VERT,nullptr,0,&origins[0]);
    id<MTLFunction> fragment=Function(H::POST_PROCESS_FRAG,nullptr,0,&origins[1]);
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::PostProcess;invocation.format=u32(format);
    auto* descriptor=(MTLRenderPipelineDescriptor*)Descriptor(invocation);
    descriptor.vertexFunction=vertex;descriptor.fragmentFunction=fragment;
    return vertex&&fragment?MakeRender(descriptor,invocation,origins,error):nil;
}

id<MTLRenderPipelineState> HostKernels::MsCopyPipeline(u32 samples, MTLPixelFormat format,
                                                       bool src_msaa) {
    const auto key = std::make_tuple(samples, static_cast<u32>(format), src_msaa);
    if (const auto it = ms_copy.find(key); !warming && it != ms_copy.end()) {
        return it->second;
    }
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::MsCopy;invocation.samples=samples;invocation.format=u32(format);invocation.src_msaa=src_msaa;
    auto* descriptor=(MTLRenderPipelineDescriptor*)Descriptor(invocation);
    std::array<LocalLibrary::Origin,2> origins;
    descriptor.vertexFunction = Function(H::FS_TRI_VERT, nullptr, 0,&origins[0]);
    descriptor.fragmentFunction =
        Function(src_msaa ? H::MS_IMAGE_BLIT_MSAA_FRAG : H::MS_IMAGE_BLIT_FRAG, nullptr, 0,&origins[1]);
    NSError* error = nil;
    id<MTLRenderPipelineState> pipeline = MakeRender(descriptor,invocation,origins,&error);
    if(warming&&!pipeline)return nil;
    ASSERT_MSG(pipeline, "MS copy pipeline failed: {}", ErrorText(error));
    if(!warming)ms_copy.emplace(key, pipeline);
    return pipeline;
}

id<MTLRenderPipelineState> HostKernels::MsDepthPipeline(u32 samples, MTLPixelFormat format) {
    const auto key = std::make_pair(samples, static_cast<u32>(format));
    if (const auto it = ms_depth.find(key); !warming && it != ms_depth.end()) {
        return it->second;
    }
    HostPso::Invocation invocation{};invocation.kind=HostPso::Kind::MsDepth;invocation.samples=samples;invocation.format=u32(format);
    auto* descriptor=(MTLRenderPipelineDescriptor*)Descriptor(invocation);
    std::array<LocalLibrary::Origin,2> origins;
    descriptor.vertexFunction = Function(H::FS_TRI_VERT, nullptr, 0,&origins[0]);
    descriptor.fragmentFunction = Function(H::COLOR_TO_MS_DEPTH_FRAG, nullptr, 0,&origins[1]);
    NSError* error = nil;
    id<MTLRenderPipelineState> pipeline = MakeRender(descriptor,invocation,origins,&error);
    if(warming&&!pipeline)return nil;
    ASSERT_MSG(pipeline, "Color to MS depth pipeline failed: {}", ErrorText(error));
    if(!warming)ms_depth.emplace(key, pipeline);
    return pipeline;
}

void HostKernels::CopyBetweenMsImages(id<MTLTexture> src, id<MTLTexture> dst, u32 samples,
                                      bool src_msaa) {
    id<MTLRenderPipelineState> pipeline = MsCopyPipeline(samples, dst.pixelFormat, src_msaa);
    // The triangle covers every sample of the target: nothing to load.
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = dst;
    pass.colorAttachments[0].loadAction = MTLLoadActionDontCare;
    pass.colorAttachments[0].storeAction = MTLStoreActionStore;
    scheduler.HostRenderEncoder((__bridge const void*)pass);
    id<MTLRenderCommandEncoder> render = scheduler.GetImpl().render;
    [render setRenderPipelineState:pipeline];
    [render setFragmentTexture:src atIndex:0];
    [render setViewport:MTLViewport{0, 0, double(dst.width), double(dst.height), 0, 1}];
    [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    scheduler.EndRendering();
}

void HostKernels::ColorToMsDepth(id<MTLTexture> src, id<MTLTexture> dst) {
    id<MTLRenderPipelineState> pipeline = MsDepthPipeline(static_cast<u32>(dst.sampleCount),
                                                          dst.pixelFormat);
    if (!depth_always_write) {
        MTLDepthStencilDescriptor* descriptor = [MTLDepthStencilDescriptor new];
        descriptor.depthCompareFunction = MTLCompareFunctionAlways;
        descriptor.depthWriteEnabled = YES;
        depth_always_write = [dev.device newDepthStencilStateWithDescriptor:descriptor];
    }
    MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.depthAttachment.texture = dst;
    pass.depthAttachment.loadAction = MTLLoadActionClear;
    pass.depthAttachment.clearDepth = 1.0;
    pass.depthAttachment.storeAction = MTLStoreActionStore;
    scheduler.HostRenderEncoder((__bridge const void*)pass);
    id<MTLRenderCommandEncoder> render = scheduler.GetImpl().render;
    [render setRenderPipelineState:pipeline];
    [render setDepthStencilState:depth_always_write];
    [render setFragmentTexture:src atIndex:0];
    [render setViewport:MTLViewport{0, 0, double(dst.width), double(dst.height), 0, 1}];
    [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    scheduler.EndRendering();
}

// ---- Runtime: the kernels behind the CacheBackend ------------------------------------------

void Runtime::DispatchTiling(const VideoCore::TilingDispatch& dispatch) {
    if (dispatch.group_count_x == 0) {
        return;
    }
    const H::Shader* shader = nullptr;
    id<MTLComputePipelineState> pipeline = impl->kernels.Tiling(dispatch, shader);
    const u32 tiled_slot = HostKernels::BufferSlot(*shader, 0);
    const u32 linear_slot = HostKernels::BufferSlot(*shader, 1);
    const u32 params_slot = HostKernels::BufferSlot(*shader, 2);
    scheduler.ComputeEncoder(__func__, true);
    id<MTLComputeCommandEncoder> compute = impl->sched.compute;
    [compute setComputePipelineState:pipeline];
    [compute setBuffer:NativeBuffer(*dispatch.tiled.buffer)
                offset:dispatch.tiled.offset
               atIndex:tiled_slot];
    [compute setBuffer:NativeBuffer(*dispatch.linear.buffer)
                offset:dispatch.linear.offset
               atIndex:linear_slot];
    [compute setBuffer:NativeBuffer(*dispatch.params.buffer)
                offset:dispatch.params.offset
               atIndex:params_slot];
    [compute dispatchThreadgroups:MTLSizeMake(dispatch.group_count_x, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(shader->local_size[0], 1, 1)];
}

bool Runtime::DownloadTiledImage(VideoCore::Image* image, const VideoCore::TilingDispatch& dispatch) {
    static const bool enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING");return v&&v[0]=='1';}();
    if(!enabled || !image || !image->backing || !dispatch.is_macro ||
       !dispatch.key.is_tiler || (dispatch.key.num_bits!=32 && dispatch.key.num_bits!=64) || dispatch.key.num_samples!=1 ||
       dispatch.num_constants!=12 || dispatch.constants[3]!=1 || dispatch.group_count_x==0 ||
       !dispatch.tiled.buffer || !dispatch.params.buffer)return false;
    const auto& info=image->info;
    static const bool half_enabled=[] {const char* v=std::getenv("BB_MTL_TEXTURE_TILING_HALF");return v&&v[0]=='1';}();
    const bool half=info.pixel_format==vk::Format::eR16G16B16A16Sfloat;
    // Native RGBA16F uses an exact bitcast of texture2d<half>. All other formats,
    // arrays, mip chains and padded linear rows retain the original path.
    if(!((!half && info.pixel_format==vk::Format::eR8G8B8A8Unorm && dispatch.key.num_bits==32) ||
         (half && half_enabled && dispatch.key.num_bits==64)) || info.num_samples!=1 ||
       info.resources.levels!=1 || info.resources.layers!=1 || info.size.depth!=1 ||
       info.pitch!=info.size.width || info.mips_layout[0].pitch!=info.size.width ||
       info.size.width%8!=0 || image->backing->num_samples!=1 ||
       dispatch.tiled.offset>dispatch.tiled.buffer->SizeBytes() ||
       info.guest_size>dispatch.tiled.buffer->SizeBytes()-dispatch.tiled.offset ||
       dispatch.tiled.range<info.guest_size)return false;
    auto source=NativeTexture(*image);
    if(source.pixelFormat!=(half?MTLPixelFormatRGBA16Float:MTLPixelFormatRGBA8Unorm) || source.textureType!=MTLTextureType2D ||
       source.sampleCount!=1 || source.mipmapLevelCount!=1 || source.arrayLength!=1 ||
       source.width!=info.size.width || source.height!=info.size.height || source.buffer ||
       !(source.usage&MTLTextureUsageShaderRead))return false;
    BeforeImageAccess();
    const void* proxy=scene_targets?scene_targets->ExistingTilingProxy(*image):nullptr;
    auto pipeline=impl->kernels.TextureTiling(dispatch,proxy!=nullptr);if(!pipeline)return false;
    // Resolve reduced proxies exactly as the old DownloadImage did before reading native data.
    bool flush=false;
    if(proxy) source=(__bridge id<MTLTexture>)proxy; // native remains dirty until a real native reader
    else flush=Transit(image,vk::ImageLayout::eShaderReadOnlyOptimal,
                       vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderRead);
    flush|=IsBufferAccessed(dispatch.tiled.buffer,dispatch.tiled.offset,info.guest_size,true);
    if(flush)FlushBarriers();
    scheduler.ComputeEncoder("Texture to guest tiling", true);
    auto compute=impl->sched.compute;
    [compute setComputePipelineState:pipeline];
    [compute setTexture:source atIndex:0];
    [compute setBuffer:NativeBuffer(*dispatch.tiled.buffer) offset:dispatch.tiled.offset atIndex:1];
    [compute setBuffer:NativeBuffer(*dispatch.params.buffer) offset:dispatch.params.offset atIndex:3];
    [compute dispatchThreadgroups:MTLSizeMake(dispatch.group_count_x,1,1) threadsPerThreadgroup:MTLSizeMake(64,1,1)];
    AccessBuffer(dispatch.tiled.buffer,dispatch.tiled.offset,info.guest_size,
                 vk::PipelineStageFlagBits2::eComputeShader,vk::AccessFlagBits2::eShaderWrite);
    if(VideoCore::FrameCapture::NativeRecording())
        VideoCore::FrameCapture::Native(std::format("{{\"op\":\"texture_tiling\",\"queue\":{},\"bits\":{},\"proxy\":{},\"width\":{},\"height\":{},\"linear_bytes\":{}}}",(uintptr_t)&scheduler,dispatch.key.num_bits,proxy!=nullptr,info.size.width,info.size.height,info.guest_size));
    static u64 count=0,bytes=0,proxies=0;++count;bytes+=info.guest_size;proxies+=proxy!=nullptr;
    if(count==1 || count%600==0)std::printf("Metal texture tiling: copies=%llu linear-staging-avoided=%llu bytes proxy-resolves-avoided=%llu; exact RGBA8/half-bit reads, original tiled addresses\n",(unsigned long long)count,(unsigned long long)bytes,(unsigned long long)proxies);
    return true;
}

void Runtime::CreateFaultParser(u32 page_bits, u32 max_faults) {
    impl->kernels.ConfigureFaultParser(page_bits, max_faults);
}

void Runtime::ParseFaultBuffer(const VideoCore::FaultParseDispatch& dispatch) {
    id<MTLComputePipelineState> pipeline = impl->kernels.FaultParser();
    ASSERT_MSG(pipeline, "ParseFaultBuffer before CreateFaultParser");
    if (dispatch.group_count_x == 0) {
        return;
    }
    const auto& shader = H::FAULT_BUFFER_PROCESS_COMP;
    scheduler.ComputeEncoder(__func__, true);
    // The game's shaders wrote the fault buffer through its device address, which Metal does not
    // track: order the parser after them (the barrier also waits for the earlier encoders).
    scheduler.MemoryBarrier();
    id<MTLComputeCommandEncoder> compute = impl->sched.compute;
    [compute setComputePipelineState:pipeline];
    [compute setBuffer:NativeBuffer(*dispatch.fault_buffer)
                offset:0
               atIndex:HostKernels::BufferSlot(shader, 0)];
    [compute setBuffer:NativeBuffer(*dispatch.download.buffer)
                offset:dispatch.download.offset
               atIndex:HostKernels::BufferSlot(shader, 1)];
    [compute dispatchThreadgroups:MTLSizeMake(dispatch.group_count_x, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(shader.local_size[0], 1, 1)];
    // And everything after the parser (its zeroed bitmap, the list): the next encoder waits for
    // this one.
    scheduler.EndEncoder();
    scheduler.MemoryBarrier();
}

} // namespace Metal

namespace Metal {
struct Runtime::ExactExportOwner {
    std::mutex gate;
    Runtime* runtime{};
    std::atomic<u64> epoch{0};
    explicit ExactExportOwner(Runtime* r):runtime(r){}
};
struct Runtime::ExportAttemptToken final : VideoCore::ExactImageExportAttempt {
    std::shared_ptr<ExactExportOwner> owner;
    u64 tick{},epoch{};
    bool consumed{};
    ExportAttemptToken(std::shared_ptr<ExactExportOwner> o,u64 t)
        :owner(std::move(o)),tick(t),epoch(owner->epoch.load(std::memory_order_acquire)){}
    const void* OwnerCookie() const noexcept override {return owner.get();}
};
struct Runtime::PreparedExportState {
    Scheduler::PreparedComputeStamp stamp{};
    u64 owner_epoch{},uid{},write_epoch{};
    u64 raw_offset{},linear_offset{},params_offset{};
    u32 groups{},tiled_slot{},linear_slot{},params_slot{},threads{};
    id<MTLComputePipelineState> pipeline=nil;
    id<MTLBuffer> raw=nil,linear=nil,params=nil;
    id<MTLTexture> texture=nil;
    id<MTLComputeCommandEncoder> encoder=nil;
    // No Image*, StreamBuffer*, Buffer* or Runtime* survives preparation.
};
class Runtime::ExactExportToken final : public VideoCore::PreparedExactImageExport {
    std::weak_ptr<ExactExportOwner> owner;
    std::weak_ptr<PreparedExportState> state;
    std::atomic<bool> consumed{false};
public:
    ExactExportToken(const std::shared_ptr<ExactExportOwner>& o,
                    const std::shared_ptr<PreparedExportState>& s):owner(o),state(s){}
    ~ExactExportToken() override {
        const auto o=owner.lock();const auto s=state.lock();
        if(!o||!s||!s->stamp.epoch)return;
        std::unique_lock lk{o->gate,std::try_to_lock};
        if(lk.owns_lock()&&o->runtime)o->runtime->scheduler.CancelPreparedCompute(s->stamp);
    }
    VideoCore::ExactImageExportResult Record() noexcept override {
        using R=VideoCore::ExactImageExportResult;
        if(consumed.exchange(true,std::memory_order_acq_rel))return R::Consumed;
        const auto o=owner.lock();const auto s=state.lock();
        if(!o||!s)return R::Stale;
        // No blocking acquisition in Record. Shutdown holds the same gate before
        // nulling runtime and touching scheduler/fields, preventing owner UAF.
        std::unique_lock lk{o->gate,std::try_to_lock};
        if(!lk.owns_lock()||!o->runtime)return R::Stale;
        return o->runtime->RecordExactExport(*s,*o);
    }
};
std::unique_ptr<VideoCore::ExactImageExportAttempt> Runtime::ClaimExactImageExportAttempt() {
    const u64 tick=CurrentTick();
    auto prior=exact_export_attempt_tick.load(std::memory_order_acquire);
    do {if(prior>=tick)return {};} while(!exact_export_attempt_tick.compare_exchange_weak(
        prior,tick,std::memory_order_acq_rel,std::memory_order_acquire));
    // The attempt is already spent before all validation, hooks and allocations.
    auto owner=std::atomic_load_explicit(&exact_export_owner,std::memory_order_acquire);
    if(!owner) {
        auto fresh=std::make_shared<ExactExportOwner>(this);
        std::shared_ptr<ExactExportOwner> empty;
        if(std::atomic_compare_exchange_strong_explicit(&exact_export_owner,&empty,fresh,std::memory_order_acq_rel,std::memory_order_acquire))owner=std::move(fresh);
        else owner=std::move(empty);
    }
    return std::make_unique<ExportAttemptToken>(std::move(owner),tick);
}
void Runtime::InvalidateExactExports() noexcept {
    if(const auto owner=std::atomic_load_explicit(&exact_export_owner,std::memory_order_acquire))
        owner->epoch.fetch_add(1,std::memory_order_acq_rel);
}
void Runtime::InvalidatePreparedImageExport(u64) noexcept {InvalidateExactExports();}
void Runtime::CloseExactExportOwner() noexcept {
    if(const auto owner=std::atomic_load_explicit(&exact_export_owner,std::memory_order_acquire)) {
        std::scoped_lock lk{owner->gate};
        owner->runtime=nullptr;
        owner->epoch.fetch_add(1,std::memory_order_acq_rel);
    }
}
bool Runtime::CanPrepareExactImageExport(const VideoCore::Image& image) const {
    static const bool enabled=[] {const auto* e=std::getenv("BB_ARM_TILER_BOUNDS");
        return e && std::strcmp(e,"1")==0;}();
    if(!enabled || scheduler.HasPreparedCompute() || VideoCore::FrameCapture::NativeRecording() ||
       !image.backing || !image.SafeToDownload() || False(image.flags&VideoCore::ImageFlagBits::Registered) ||
       image.scene_proxy || image.depth_id || image.usage.render_target || image.usage.depth_target ||
       image.usage.vo_surface || image.usage.storage || image.binding.is_bound || image.binding.is_target ||
       image.binding.needs_rebind || image.binding.force_general || image.info.num_samples!=1 ||
       image.info.num_bits!=32 || !image.info.props.is_tiled || image.info.props.is_block ||
       image.info.props.is_volume || image.info.props.is_depth || image.info.props.has_stencil ||
       !image.info.guest_size || image.info.guest_size>(8ull<<20) ||
       image.info.pixel_format!=vk::Format::eR8G8B8A8Unorm || !image.info.resources.levels ||
       image.info.resources.levels>16 || !image.info.resources.layers || image.info.resources.layers>4 ||
       image.backing->retired || image.backing->num_samples!=1 ||
       image.backing->image.image_ci.format!=vk::Format::eR8G8B8A8Unorm)return false;
    const auto native=NativeTexture(image);
    return native&&native.pixelFormat==MTLPixelFormatRGBA8Unorm&&native.sampleCount==1&&
        (native.textureType==MTLTextureType2D||native.textureType==MTLTextureType2DArray)&&
        native.width==image.info.size.width&&native.height==image.info.size.height&&
        native.mipmapLevelCount==image.info.resources.levels&&native.arrayLength==image.info.resources.layers&&
        !native.buffer&&!native.parentTexture&&!native.iosurface&&!native.heap;
}
std::unique_ptr<VideoCore::PreparedExactImageExport> Runtime::PrepareExactImageExport(
    std::unique_ptr<VideoCore::ExactImageExportAttempt> generic,VideoCore::Image* image,
    const VideoCore::Buffer* raw,u64 raw_offset,std::span<const vk::BufferImageCopy> copies,
    std::span<const u8> parameter_bytes,const std::function<void()>& release_input_lease) {
    const auto owner=std::atomic_load_explicit(&exact_export_owner,std::memory_order_acquire);
    if(!generic||!owner||generic->OwnerCookie()!=owner.get())return {};
    auto& attempt=static_cast<ExportAttemptToken&>(*generic);
    if(attempt.consumed||attempt.tick!=CurrentTick())return {};
    attempt.consumed=true;
    std::unique_lock owner_lock{owner->gate}; // preparation may block; never a guest payload lock
    if(owner->runtime!=this||owner->epoch.load(std::memory_order_acquire)!=attempt.epoch||
       !image||!raw||!CanPrepareExactImageExport(*image))return {};
    // Freeze native ownership and scalar metadata while input references are valid.
    // Nothing below the first hook dereferences any caller cache slot again.
    const auto info=image->info;
    auto state=std::make_shared<PreparedExportState>();
    state->uid=image->image_uid;state->texture=NativeTexture(*image);state->raw=NativeBuffer(*raw);
    state->raw_offset=raw_offset;state->owner_epoch=attempt.epoch;
    const auto bytes=info.guest_size;
    if(!state->raw||raw_offset>raw->SizeBytes()||bytes>raw->SizeBytes()-raw_offset||
       raw_offset>state->raw.length||bytes>state->raw.length-raw_offset||
       copies.size()!=info.resources.levels||parameter_bytes.size()!=652)return {};
    VideoCore::TilerBounds::ParamsV1 bounds{
        VideoCore::TileManager::MakeTilingInfo(info,info.resources.levels)};
    if(!VideoCore::TilerBounds::Describe(info,copies,bounds)||
       std::memcmp(&bounds,parameter_bytes.data(),sizeof(bounds)))return {};
    const auto unchanged=[&]{return CurrentTick()==attempt.tick&&
        owner->epoch.load(std::memory_order_acquire)==attempt.epoch;};
    // All caller slot/native/scalar reads are finished. Drop the caller cache
    // lock before any hook, pool/driver work or pipeline compilation.
    if(release_input_lease)release_input_lease();
    // Drain all callbacks/replay/tails BEFORE allocating private pool ranges.
    BeforeImageAccess();
    if(!unchanged())return {};
    FlushBarriers();
    if(!unchanged())return {};
    scheduler.BeginOrderedHostCompute("Exact export cold preparation");
    if(!unchanged())return {};
    exact_export_pool_requests.fetch_add(1,std::memory_order_relaxed);
    const auto params=StagingRequest(sizeof(bounds),VideoCore::MemoryType::HostUncached,
                                     Limits().uniform_min_alignment);
    if(!unchanged()||!params.buffer||!params.mapped)return {};
    std::memcpy(params.mapped,&bounds,sizeof(bounds));params.Flush();
    exact_export_pool_requests.fetch_add(1,std::memory_order_relaxed);
    const auto linear=StagingRequest(bytes,VideoCore::MemoryType::DeviceLocal);
    if(!unchanged()||!linear.buffer)return {};
    state->params=NativeBuffer(*params.buffer);state->linear=NativeBuffer(*linear.buffer);
    state->params_offset=params.offset;state->linear_offset=linear.offset;
    if(!state->params||!state->linear||state->raw==state->params||state->raw==state->linear||
       params.offset>state->params.length||652>state->params.length-params.offset||
       linear.offset>state->linear.length||bytes>state->linear.length-linear.offset)return {};
    // HostKernels::Tiling uses only value keys/ranges, not Buffer pointers.
    const auto dispatch=VideoCore::TileManager::MakeDispatch(info,true,
        {nullptr,raw_offset,bytes},{nullptr,linear.offset,bytes},{nullptr,params.offset,652});
    const HostShaders::Shader* shader=nullptr;
    state->pipeline=impl->kernels.Tiling(dispatch,shader);
    if(!unchanged()||!state->pipeline||!shader)return {};
    state->groups=dispatch.group_count_x;state->threads=shader->local_size[0];
    state->tiled_slot=HostKernels::BufferSlot(*shader,0);
    state->linear_slot=HostKernels::BufferSlot(*shader,1);
    state->params_slot=HostKernels::BufferSlot(*shader,2);
    std::array<vk::BufferImageCopy,16> snapshot_copies{};
    std::copy(copies.begin(),copies.end(),snapshot_copies.begin());
    for(auto& c:std::span(snapshot_copies).first(copies.size()))c.bufferOffset+=linear.offset;
    PrepareExactSnapshot((__bridge const void*)state->texture,(__bridge const void*)state->linear,
                          std::span(snapshot_copies).first(copies.size()));
    if(!unchanged())return {};
    state->encoder=scheduler.GetImpl().compute;
    if(!state->encoder)return {};
    // Only deferred scheduler ownership keeps resources through this tick. An
    // escaped token holds weak state, so it cannot retain old snapshot pools forever.
    scheduler.DeferOperation([state]{(void)state;});
    state->write_epoch=buffer_write_epoch;
    auto token=std::make_unique<ExactExportToken>(owner,state);
    const auto stamp=scheduler.ArmPreparedCompute();
    if(!stamp||!unchanged())return {};
    state->stamp=*stamp;
    return token;
}
VideoCore::ExactImageExportResult Runtime::RecordExactExport(
    PreparedExportState& s,ExactExportOwner& owner) noexcept {
    using R=VideoCore::ExactImageExportResult;
    const auto stale=[&]{scheduler.CancelPreparedCompute(s.stamp);return R::Stale;};
    // Native strong refs + scalar values only. No caller Image/StreamBuffer/
    // Buffer slot is read even when it has already been destroyed/reused.
    if(owner.runtime!=this||owner.epoch.load(std::memory_order_acquire)!=s.owner_epoch||
       buffer_write_epoch!=s.write_epoch||
       CurrentTick()!=s.stamp.tick||std::this_thread::get_id()!=s.stamp.thread||
       VideoCore::FrameCapture::NativeRecording())return stale();
    if(!scheduler.ConsumePreparedCompute(s.stamp))return stale();
    const auto compute=s.encoder;
    [compute setComputePipelineState:s.pipeline];
    [compute setBuffer:s.raw offset:s.raw_offset atIndex:s.tiled_slot];
    [compute setBuffer:s.linear offset:s.linear_offset atIndex:s.linear_slot];
    [compute setBuffer:s.params offset:s.params_offset atIndex:s.params_slot];
    [compute dispatchThreadgroups:MTLSizeMake(s.groups,1,1)
            threadsPerThreadgroup:MTLSizeMake(s.threads,1,1)];
    [compute memoryBarrierWithScope:MTLBarrierScopeBuffers|MTLBarrierScopeTextures];
    ++buffer_write_epoch;global_write=true;memory_pending=true;
    return R::Recorded;
}
} // namespace Metal
