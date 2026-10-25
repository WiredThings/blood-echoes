// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "video_core/renderer_metal/mtl_function_identity.h"
#include "video_core/renderer_metal/mtl_library_origin.h"
namespace Metal::FunctionIdentity {
// The ObjC adapter captures actual effective options and successful entry first.
// No pointers or diagnostic flags participate. Origin comes from LocalLibrary::Build.
template<class Shader>
PersistentFunctionIdentity FromSuccessfulOrigin(const Context& context,const CompileOptions& options,
    const LocalLibrary::Origin& origin,std::string_view entry,bool entry_succeeded,const Shader& shader) {
 if(!origin.valid())return {};
 const auto route=origin.route==LocalLibrary::Route::Source?PersistentLibraryRoute::Source:
    origin.route==LocalLibrary::Route::LocalMetallib?PersistentLibraryRoute::LocalMetallib:
    PersistentLibraryRoute::None;
 const auto function=Make(context,options,{.source_sha=origin.source_sha256,.entry=entry,
    .local_cohort=origin.cohort,.local_build=origin.build_id,.local_key=origin.key,
    .local_library_sha=origin.library_sha256,.route=route,
    .requested_entry_succeeded=entry_succeeded});
 return BindModule(function,shader);
}
}
