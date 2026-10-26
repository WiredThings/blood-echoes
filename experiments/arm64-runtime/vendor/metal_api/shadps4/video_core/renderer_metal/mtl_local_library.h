// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "video_core/renderer_metal/mtl_phase_counters.h"
#include "video_core/renderer_metal/mtl_local_aot.h"
#include "video_core/renderer_metal/mtl_library_origin.h"
#include <cstdio>
#include <initializer_list>
#include <optional>

namespace Metal::LocalLibrary {
// Full libraries have no function-constant identity: the caller supplies its real
// entry factory for admission. Final functions/PSOs retain their own exact keys.
inline constexpr const char* Recipe="bb-metal-local-library-v2";
inline std::optional<std::string> Language(NSUInteger raw) {
    const auto major=raw>>16, minor=raw&0xffff;
    if ((major==1&&minor<=2)||(major==2&&minor<=4)||(major==3&&minor<=2)||(major==4&&minor<=1))
        return "metal"+std::to_string(major)+"."+std::to_string(minor);
    return {};
}
inline NSDictionary* Options(MTLCompileOptions* input) {
    if (@available(macOS 15.0,*)) {
        // Reading concrete defaults preserves the original API's language/math
        // choices. In particular, default and explicit3.1 are not presumed Safe.
        MTLCompileOptions* o=input?:[MTLCompileOptions new];
        const auto language=Language(o.languageVersion);
        MTLCompileOptions* defaults=[MTLCompileOptions new];
        if (!language || o.preprocessorMacros.count || o.libraries.count || o.installName.length ||
            o.libraryType!=MTLLibraryTypeExecutable || o.preserveInvariance ||
            o.optimizationLevel!=defaults.optimizationLevel ||
            o.compileSymbolVisibility!=defaults.compileSymbolVisibility ||
            o.allowReferencingUndefinedSymbols || o.maxTotalThreadsPerThreadgroup || o.enableLogging)
            return nil;
        if (@available(macOS 26.0,*)) {
            const auto r=o.requiredThreadsPerThreadgroup;
            if (r.width||r.height||r.depth) return nil;
        }
        if (@available(macOS 27.0,*)) {
            if(o.floatingPointConversionRoundingMode!=MTLFloatingPointConversionRoundingModeToNearestEven)
                return nil;
        }
        NSString* math=nil;
        switch(o.mathMode) {
        case MTLMathModeSafe: math=@"safe"; break;
        case MTLMathModeRelaxed: math=@"relaxed"; break;
        case MTLMathModeFast: math=@"fast"; break;
        default: return nil;
        }
        NSString* fp=nil;
        switch(o.mathFloatingPointFunctions) {
        case MTLMathFloatingPointFunctionsFast: fp=@"fast"; break;
        case MTLMathFloatingPointFunctionsPrecise: fp=@"precise"; break;
        default: return nil;
        }
        return @{@"language":@((*language).c_str()),@"math_mode":math,@"math_fp32":fp};
    }
    return nil; // Preserve the original legacy source call, never guess old semantics.
}

inline std::string Key(std::string_view cohort,std::string_view source,std::string_view language,
                       std::string_view math,std::string_view fp) {
    std::string bytes;
    for(auto part:std::array<std::string_view,6>{Recipe,cohort,source,language,math,fp}) {
        bytes+=std::to_string(part.size()); bytes+=':'; bytes+=part;
    }
    return LocalAot::Sha(bytes.data(),bytes.size());
}
inline bool PlainEntries(id<MTLLibrary> library,std::initializer_list<NSString*> entries) {
    if(!library || entries.size()==0) return false;
    for(NSString* entry:entries) if(![library newFunctionWithName:entry]) return false;
    return true;
}

template<class Factory>
inline id<MTLLibrary> BuildInner(id<MTLDevice> device, NSString* source,
                           MTLCompileOptions* options, NSError** error, Factory&& factory,
                           bool allow_lookup=true,Origin* success_origin=nullptr) {
    if(success_origin)*success_origin={};
    MTLCompileOptions* actual_options=options;
    std::string source_sha;
    const auto accepted_source_origin=[&] {
        if(!success_origin)return;
        if(source_sha.empty()) {
            NSData* bytes=[source dataUsingEncoding:NSUTF8StringEncoding];
            if(!bytes||!bytes.length||bytes.length>LocalAot::MaxSource||std::memchr(bytes.bytes,0,bytes.length))return;
            source_sha=LocalAot::Sha(bytes.bytes,bytes.length);
        }
        *success_origin=Origin{.route=Route::Source,.source_sha256=source_sha};
    };
    const auto source_build=[&] {
        if(error)*error=nil;
        BbMetalPhase::Call bb_metal_call_90{BbMetalPhase::Kind::SourceLibrary}; id<MTLLibrary> bb_metal_result=[device newLibraryWithSource:source options:actual_options error:error]; bb_metal_call_90.Finish(bool(bb_metal_result)); return bb_metal_result;
    };
    const auto source_admitted=[&] {
        auto library=source_build();
        // Optional provenance is valid only after the caller's real entry and
        // actual typed constants succeeded. Non-opted-in legacy calls unchanged.
        if(success_origin&&library&&factory(library))accepted_source_origin();
        return library;
    };
    const char* mode_env=std::getenv("BB_MTL_LOCAL_AOT_MODE");
    if(!mode_env || (std::string_view(mode_env)!="load"&&std::string_view(mode_env)!="record"))
        return source_admitted(); // Zero AOT filesystem/hash/factory work when disabled.
    actual_options=[options copy];
    const char* root_env=std::getenv("BB_MTL_LOCAL_AOT_ROOT");
    const char* cohort_env=std::getenv("BB_MTL_LOCAL_AOT_COHORT");
    const char* gpu_env=std::getenv("BB_GPU_USER_DIR");
    auto profile=Options(actual_options);
    NSData* source_data=[source dataUsingEncoding:NSUTF8StringEncoding];
    if(!device||!profile||!root_env||!gpu_env||!cohort_env||!LocalAot::Hex(cohort_env)||
       !source_data||!source_data.length||source_data.length>LocalAot::MaxSource||
       std::memchr(source_data.bytes,0,source_data.length)) return source_admitted();
    const std::filesystem::path root=root_env,gpu=gpu_env;
    if(!LocalAot::Directory(gpu,false)||!LocalAot::Directory(root,false)||!LocalAot::Child(root,gpu))
        return source_admitted();
    const std::string cohort=cohort_env;
    source_sha=LocalAot::Sha(source_data.bytes,source_data.length);
    const auto key=Key(cohort,source_sha,[profile[@"language"] UTF8String],
                       [profile[@"math_mode"] UTF8String],[profile[@"math_fp32"] UTF8String]);
    auto folder=root/cohort; auto requests=folder/"requests";
    const auto accepts=[&](NSDictionary* object) {
        return LocalAot::Integer(object,@"schema",2)&&
            LocalAot::String(object,@"recipe",Recipe)&&LocalAot::String(object,@"key",key)&&
            LocalAot::String(object,@"cohort",cohort)&&LocalAot::String(object,@"source_sha256",source_sha)&&
            LocalAot::String(object,@"language",[profile[@"language"] UTF8String])&&
            LocalAot::String(object,@"math_mode",[profile[@"math_mode"] UTF8String])&&
            LocalAot::String(object,@"math_fp32",[profile[@"math_fp32"] UTF8String]);
    };
    if(allow_lookup&&std::string_view(mode_env)=="load") {
        const char* build=std::getenv("BB_MTL_LOCAL_AOT_BUILD_ID");
        if(build&&LocalAot::Hex(build)) {
            const auto artifacts=folder/"artifacts"/build;
            if(LocalAot::Directory(folder,false)&&LocalAot::Directory(artifacts,false)) {
                auto receipt=LocalAot::Json(artifacts/(key+".json"));
                auto blob=LocalAot::Read(artifacts/(key+".metallib"),LocalAot::MaxLibrary);
                const auto library_sha=blob?LocalAot::Sha(blob.bytes,blob.length):std::string{};
                if(receipt&&blob&&accepts(receipt)&&LocalAot::String(receipt,@"build_id",build)&&
                   LocalAot::Integer(receipt,@"library_bytes",blob.length)&&
                   LocalAot::String(receipt,@"library_sha256",library_sha)) {
                    dispatch_data_t data=dispatch_data_create(blob.bytes,blob.length,
                        dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{(void)blob.length;});
                    BbMetalPhase::Call bb_metal_call_140{BbMetalPhase::Kind::LocalLibraryData}; id<MTLLibrary> library=[device newLibraryWithData:data error:nil]; bb_metal_call_140.Finish(bool(library));
                    // Reflection names alone cannot accept an artifact. The caller
                    // creates its real entries using its actual constant values.
                    if(library&&factory(library)) {
                        if(success_origin)*success_origin=Origin{.route=Route::LocalMetallib,
                            .source_sha256=source_sha,.cohort=cohort,.build_id=build,.key=key,.library_sha256=library_sha};
                        std::printf("Metal helper local AOT: hit key=%s\n",key.c_str());
                        return library;
                    }
                }
            }
        }
    }
    auto library=source_build();
    if(!library||!factory(library)) return library; // Original caller owns failure policy.
    accepted_source_origin();
    if(!LocalAot::RequestShard(folder,key,requests)) return library;
    NSDictionary* request=@{@"schema":@2,@"recipe":@(Recipe),@"key":@(key.c_str()),
        @"cohort":@(cohort.c_str()),@"source_sha256":@(source_sha.c_str()),
        @"language":profile[@"language"],@"math_mode":profile[@"math_mode"],@"math_fp32":profile[@"math_fp32"]};
    auto manifest=[NSJSONSerialization dataWithJSONObject:request options:NSJSONWritingSortedKeys error:nil];
    if(LocalAot::Publish(requests/(key+".metal"),source_data)) {
        auto saved=LocalAot::Read(requests/(key+".metal"),LocalAot::MaxSource);
        if(saved&&LocalAot::Sha(saved.bytes,saved.length)==source_sha)
            LocalAot::Publish(requests/(key+".json"),manifest);
    }
    return library;
}

template<class Factory>
inline id<MTLLibrary> Build(id<MTLDevice> device, NSString* source,
                           MTLCompileOptions* options, NSError** error, Factory&& factory,
                           bool allow_lookup=true,Origin* success_origin=nullptr) {
    // Keep returned objects AND NSError alive across the temporary-source pool.
    // Factory captures use caller-owned strong function slots, never a lease.
    if(success_origin)*success_origin={};
    id<MTLLibrary> accepted=nil;
    NSError* retained_error=nil;
    @autoreleasepool {
        accepted=BuildInner(device,source,options,&retained_error,factory,allow_lookup,success_origin);
    }
    if(error)*error=retained_error;
    return accepted;
}
} // namespace Metal::LocalLibrary
