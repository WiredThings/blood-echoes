set(AETHER ${BB_ARM64_PROTOTYPE_DIR}/vendor/aether)
add_library(bb_fex_embedding STATIC
 ${AETHER}/src/core/fex/fex_guest_engine.cpp
 ${AETHER}/src/core/guest_cpu/fex_guest_cpu.cpp
 ${AETHER}/src/core/guest_cpu/hle_call_adapter.cpp
 ${AETHER}/src/core/guest_cpu/fex_hle_bridge.cpp)
target_compile_features(bb_fex_embedding PUBLIC cxx_std_20)
target_include_directories(bb_fex_embedding PUBLIC ${AETHER}/src ${CMAKE_BINARY_DIR}/generated ${CMAKE_SOURCE_DIR}/Source/Tools/CommonTools ${CMAKE_SOURCE_DIR}/FEXCore/Source)
target_link_libraries(bb_fex_embedding PUBLIC FEXCore Common CommonTools JemallocLibs ${PTHREAD_LIB})
add_executable(test_lookup ${BB_ARM64_PROTOTYPE_DIR}/tests/test_lookup.cpp)
target_compile_features(test_lookup PRIVATE cxx_std_20)
target_compile_definitions(test_lookup PRIVATE AETHER_SMOKE_SOURCE="${AETHER}/runtime/probes/fexcore-smoke.cpp")
target_link_libraries(test_lookup PRIVATE bb_fex_embedding)
add_executable(test_abi ${BB_ARM64_PROTOTYPE_DIR}/tests/test_abi.cpp)
target_link_libraries(test_abi PRIVATE bb_fex_embedding)
add_executable(bb-runtime-arm64 ${BB_ARM64_PROTOTYPE_DIR}/src/runtime.cpp ${BB_ARM64_PROTOTYPE_DIR}/src/metal_peer.cpp)
target_include_directories(bb-runtime-arm64 PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-runtime-arm64 PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_PROTOTYPE_DIR}")
target_link_libraries(bb-runtime-arm64 PRIVATE bb_fex_embedding)
add_executable(test_lifetime ${BB_ARM64_PROTOTYPE_DIR}/tests/test_lifetime.cpp ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes/bbport_resource_lifetime.cpp)
target_include_directories(test_lifetime PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_features(test_lifetime PRIVATE cxx_std_20)
target_compile_options(test_lifetime PRIVATE -UNDEBUG)
add_executable(test_lookup_original ${BB_ARM64_PROTOTYPE_DIR}/tests/test_lookup.cpp ${BB_ARM64_PROTOTYPE_DIR}/tests/baseline_lookup.cpp ${BB_ARM64_PROTOTYPE_DIR}/tests/baseline_reset_compat.cpp)
target_compile_definitions(test_lookup_original PRIVATE AETHER_SMOKE_SOURCE="${AETHER}/runtime/probes/fexcore-smoke.cpp")
target_link_libraries(test_lookup_original PRIVATE bb_fex_embedding)
add_executable(test_abi_original ${BB_ARM64_PROTOTYPE_DIR}/tests/test_abi.cpp)
target_compile_features(test_abi_original PRIVATE cxx_std_20)
target_include_directories(test_abi_original PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/tests ${AETHER}/src ${AETHER}/src/core/guest_cpu)
target_compile_definitions(test_abi_original PRIVATE BB_ABI_ORIGINAL)
enable_language(C)
file(GLOB BB_NATIVE_BRIDGES CONFIGURE_DEPENDS ${BB_ARM64_PROTOTYPE_DIR}/generated/*_bridge.c)
add_library(bb_native_runtime STATIC ${BB_NATIVE_BRIDGES} ${BB_ARM64_PROTOTYPE_DIR}/generated/native_registry.c ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src/low_heap.c ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src/runtime_quicksave.c ${BB_ARM64_PROTOTYPE_DIR}/src/native_stubs.c ${BB_ARM64_PROTOTYPE_DIR}/src/native_restart.c)
target_include_directories(bb_native_runtime PUBLIC ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src)
target_include_directories(bb_native_runtime PRIVATE /opt/homebrew/include)
target_include_directories(bb_native_runtime PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_sources(bb_native_runtime PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src/native_net_threads.c)
target_sources(bb_native_runtime PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src/native_network.c)
target_include_directories(bb_native_runtime PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
target_compile_options(bb_native_runtime PRIVATE -Wno-incompatible-pointer-types-discards-qualifiers)
target_compile_features(bb_native_runtime PRIVATE c_std_11)
file(GLOB BB_ATRAC_SOURCES CONFIGURE_DEPENDS ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/third_party/LibAtrac9/C/src/*.c)
add_library(bb_atrac STATIC ${BB_ATRAC_SOURCES})
target_link_libraries(bb_native_runtime PUBLIC bb_atrac ${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/deps-metal-arm64/lib/libSDL3.0.dylib)
add_executable(bb-game-arm64 ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp)
target_include_directories(bb-game-arm64 PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/generated ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-game-arm64 PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_PROTOTYPE_DIR}")
target_link_libraries(bb-game-arm64 PRIVATE bb_fex_embedding bb_native_runtime)
add_executable(test_jit_scope ${BB_ARM64_PROTOTYPE_DIR}/tests/test_jit_scope.cpp)
target_link_libraries(test_jit_scope PRIVATE bb_fex_embedding)
add_executable(test_jit_scope_original ${BB_ARM64_PROTOTYPE_DIR}/tests/test_jit_scope.cpp)
target_compile_definitions(test_jit_scope_original PRIVATE BB_OLD_JIT_SCOPE)
target_link_libraries(test_jit_scope_original PRIVATE bb_fex_embedding)
include(${BB_ARM64_PROTOTYPE_DIR}/generated/gpu_flags.cmake)
add_library(bb_gpu_adapters OBJECT ${BB_ARM64_PROTOTYPE_DIR}/generated/gpu_adapters.cpp)
target_compile_options(bb_gpu_adapters PRIVATE ${BB_GPU_HEADER_FLAGS} -Wno-ignored-attributes)
target_include_directories(bb_gpu_adapters BEFORE PRIVATE ${BB_GPU_HEADER_INCLUDES})
target_link_libraries(bb_gpu_adapters PRIVATE bb_fex_embedding)

target_compile_definitions(bb_native_runtime PRIVATE BB_WITH_NATIVE_GPU=1)
target_sources(bb-game-arm64 PRIVATE $<TARGET_OBJECTS:bb_gpu_adapters>)
set(BB_ARM_GPU_LIBRARY "${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/bin/gpu-metal-arm64/libbbgpu.menu-candidate.dylib" CACHE FILEPATH "Private CPU-side GPU library")
target_link_libraries(bb-game-arm64 PRIVATE ${BB_ARM_GPU_LIBRARY})
target_link_options(bb-game-arm64 PRIVATE -Wl,-export_dynamic)
set_target_properties(bb-game-arm64 PROPERTIES BUILD_RPATH "${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/bin/gpu-metal-arm64")

add_executable(test_heap_bridge ${BB_ARM64_PROTOTYPE_DIR}/tests/test_heap_bridge.cpp ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim/bbport_heap_sites.cpp)
target_include_directories(test_heap_bridge PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_compile_features(test_heap_bridge PRIVATE cxx_std_20)
target_compile_options(test_heap_bridge PRIVATE -UNDEBUG)
add_executable(test_heap_bridge_original ${BB_ARM64_PROTOTYPE_DIR}/tests/test_heap_bridge.cpp ${BB_ARM64_PROTOTYPE_DIR}/tests/heap_sites_original.cpp)
target_include_directories(test_heap_bridge_original PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_compile_features(test_heap_bridge_original PRIVATE cxx_std_20)
target_compile_definitions(test_heap_bridge_original PRIVATE BB_HEAP_OLD)

add_executable(test_darwin_fault ${BB_ARM64_PROTOTYPE_DIR}/tests/test_darwin_fault.cpp)
target_include_directories(test_darwin_fault PRIVATE ${AETHER}/src/core/fex)
target_compile_features(test_darwin_fault PRIVATE cxx_std_20)
target_compile_options(test_darwin_fault PRIVATE -UNDEBUG)

foreach(test_target test_writer_handoff test_writer_handoff_original)
 add_executable(${test_target} ${BB_ARM64_PROTOTYPE_DIR}/tests/test_writer_handoff.cpp)
 target_include_directories(${test_target} PRIVATE ${AETHER}/runtime/sources/fexcore-darwin/FEXCore/include ${AETHER}/runtime/sources/fexcore-darwin/External/fmt/include)
 target_compile_features(${test_target} PRIVATE cxx_std_20)
 target_link_libraries(${test_target} PRIVATE bb_fex_embedding)
endforeach()
target_compile_definitions(test_writer_handoff_original PRIVATE BB_OLD_WRITER_MUTEX)

add_executable(test_startup_patches ${BB_ARM64_PROTOTYPE_DIR}/tests/test_startup_patches.cpp)
target_include_directories(test_startup_patches PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_startup_patches PRIVATE cxx_std_20)
target_compile_options(test_startup_patches PRIVATE -UNDEBUG)

foreach(host_target test_host_page_protection test_host_page_protection_original)
 add_executable(${host_target} ${BB_ARM64_PROTOTYPE_DIR}/tests/test_host_page_protection.c)
 target_include_directories(${host_target} PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime)
 target_compile_options(${host_target} PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
 target_link_options(${host_target} PRIVATE -Wl,-dead_strip)
endforeach()
target_compile_definitions(test_host_page_protection_original PRIVATE BB_OLD_HOST_PROTECTION)

add_executable(test_static_range_index ${BB_ARM64_PROTOTYPE_DIR}/tests/test_static_range_index.cpp)
target_include_directories(test_static_range_index PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_static_range_index PRIVATE cxx_std_20)
target_compile_options(test_static_range_index PRIVATE -UNDEBUG)
add_executable(test_published_ranges ${BB_ARM64_PROTOTYPE_DIR}/tests/test_published_ranges.cpp)
target_link_libraries(test_published_ranges PRIVATE bb_fex_embedding)
target_compile_options(test_published_ranges PRIVATE -UNDEBUG)

target_compile_definitions(bb_native_runtime PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_PROTOTYPE_DIR}")

add_executable(test_native_restart ${BB_ARM64_PROTOTYPE_DIR}/tests/test_native_restart.c ${BB_ARM64_PROTOTYPE_DIR}/src/native_restart.c)
target_include_directories(test_native_restart PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src)
target_compile_definitions(test_native_restart PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_PROTOTYPE_DIR}")
add_executable(test_session_state ${BB_ARM64_PROTOTYPE_DIR}/tests/test_session_state.cpp)
target_include_directories(test_session_state PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_session_state PRIVATE cxx_std_20)
target_compile_options(test_session_state PRIVATE -UNDEBUG)

add_executable(test_thread_bootstrap ${BB_ARM64_PROTOTYPE_DIR}/tests/test_thread_bootstrap.cpp)
target_include_directories(test_thread_bootstrap PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_link_libraries(test_thread_bootstrap PRIVATE bb_fex_embedding)
target_compile_options(test_thread_bootstrap PRIVATE -UNDEBUG)

add_executable(bb-game-arm64-old-stack-snapshot ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp)
target_include_directories(bb-game-arm64-old-stack-snapshot PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/generated ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-game-arm64-old-stack-snapshot PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_PROTOTYPE_DIR}" BB_OLD_THREAD_BOOTSTRAP)
target_sources(bb-game-arm64-old-stack-snapshot PRIVATE $<TARGET_OBJECTS:bb_gpu_adapters>)
target_link_libraries(bb-game-arm64-old-stack-snapshot PRIVATE bb_fex_embedding bb_native_runtime ${BB_ARM_GPU_LIBRARY})
target_link_options(bb-game-arm64-old-stack-snapshot PRIVATE -Wl,-export_dynamic)
set_target_properties(bb-game-arm64-old-stack-snapshot PROPERTIES BUILD_RPATH "${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/bin/gpu-metal-arm64")

# Private diagnostic and immutable hardware-TSO admission headers.
set_property(SOURCE "${CMAKE_SOURCE_DIR}/FEXCore/Source/Interface/Core/Core.cpp" TARGET_DIRECTORY FEXCore_object APPEND PROPERTY INCLUDE_DIRECTORIES "${BB_ARM64_PROTOTYPE_DIR}/src")
target_include_directories(bb_fex_embedding PUBLIC ${BB_ARM64_PROTOTYPE_DIR}/src)
add_executable(test_stutter_audit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_stutter_audit.cpp)
target_compile_features(test_stutter_audit PRIVATE cxx_std_20)
target_include_directories(test_stutter_audit PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_options(test_stutter_audit PRIVATE -UNDEBUG)
add_executable(test_hardware_tso ${BB_ARM64_PROTOTYPE_DIR}/tests/test_hardware_tso.cpp)
target_compile_features(test_hardware_tso PRIVATE cxx_std_20)
target_include_directories(test_hardware_tso PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_options(test_hardware_tso PRIVATE -UNDEBUG)

target_include_directories(fexcore-guest-harness PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)

add_executable(test_native_net_threads ${BB_ARM64_PROTOTYPE_DIR}/tests/test_native_net_threads.c ${BB_ARM64_PROTOTYPE_DIR}/src/native_net_threads.c)
target_include_directories(test_native_net_threads PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime)
target_compile_options(test_native_net_threads PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
target_link_options(test_native_net_threads PRIVATE -Wl,-dead_strip)

foreach(audit_source Interface/Core/LookupCache.cpp Interface/Core/JIT/JIT.cpp Interface/Core/CPUBackend.cpp Interface/Core/Dispatcher/Dispatcher.cpp)
 set_property(SOURCE "${CMAKE_SOURCE_DIR}/FEXCore/Source/${audit_source}" TARGET_DIRECTORY FEXCore_object APPEND PROPERTY INCLUDE_DIRECTORIES "${BB_ARM64_PROTOTYPE_DIR}/src")
endforeach()

# Network byte-span checks are private to the optional ABI3 module; offline has no work.
foreach(span_target test_bbnet_byte_ranges test_bbnet_byte_ranges_original)
 add_executable(${span_target} ${BB_ARM64_PROTOTYPE_DIR}/tests/test_bbnet_byte_ranges.cpp)
 target_include_directories(${span_target} PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
 target_link_libraries(${span_target} PRIVATE bb_fex_embedding)
 target_compile_options(${span_target} PRIVATE -UNDEBUG)
endforeach()
target_compile_definitions(test_bbnet_byte_ranges_original PRIVATE BB_NET_OLD_MINIMUM_RANGE)

add_executable(test_channel_audit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_channel_audit.cpp)
target_include_directories(test_channel_audit PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_compile_features(test_channel_audit PRIVATE cxx_std_20)
target_compile_options(test_channel_audit PRIVATE -UNDEBUG)

add_executable(test_sleep_audit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_sleep_audit.cpp)
target_link_libraries(test_sleep_audit PRIVATE bb_fex_embedding)
target_compile_options(test_sleep_audit PRIVATE -UNDEBUG)

add_executable(test_hle_published_lookup ${BB_ARM64_PROTOTYPE_DIR}/tests/test_hle_published_lookup.cpp)
target_link_libraries(test_hle_published_lookup PRIVATE bb_fex_embedding)
target_compile_options(test_hle_published_lookup PRIVATE -UNDEBUG)

add_executable(test_guest_header_string ${BB_ARM64_PROTOTYPE_DIR}/tests/test_guest_header_string.cpp)
target_include_directories(test_guest_header_string PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
target_compile_features(test_guest_header_string PRIVATE cxx_std_23)
target_compile_options(test_guest_header_string PRIVATE -UNDEBUG)

add_executable(test_guest_resource_detours ${BB_ARM64_PROTOTYPE_DIR}/tests/test_guest_resource_detours.cpp)
target_include_directories(test_guest_resource_detours PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_guest_resource_detours PRIVATE cxx_std_20)
target_compile_options(test_guest_resource_detours PRIVATE -UNDEBUG)

target_include_directories(bb-game-arm64 BEFORE PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_include_directories(bb-game-arm64-old-stack-snapshot BEFORE PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)

add_executable(test_resource_leaf_profile ${BB_ARM64_PROTOTYPE_DIR}/tests/test_resource_leaf_profile.cpp)
target_include_directories(test_resource_leaf_profile PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_resource_leaf_profile PRIVATE cxx_std_20)
target_compile_options(test_resource_leaf_profile PRIVATE -UNDEBUG)

add_executable(test_arm_travel_state ${BB_ARM64_PROTOTYPE_DIR}/tests/test_arm_travel_state.c)
target_compile_options(test_arm_travel_state PRIVATE -UNDEBUG)
add_executable(test_arm_travel_hooks ${BB_ARM64_PROTOTYPE_DIR}/tests/test_arm_travel_hooks.cpp)
target_include_directories(test_arm_travel_hooks PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_arm_travel_hooks PRIVATE cxx_std_20)
target_compile_options(test_arm_travel_hooks PRIVATE -UNDEBUG)

add_executable(test_resource_query ${BB_ARM64_PROTOTYPE_DIR}/tests/test_resource_query.cpp)
target_include_directories(test_resource_query PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_resource_query PRIVATE cxx_std_20)
target_compile_options(test_resource_query PRIVATE -UNDEBUG)
add_executable(test_memory_inspection ${BB_ARM64_PROTOTYPE_DIR}/tests/test_memory_inspection.c)
target_include_directories(test_memory_inspection PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime)
target_compile_options(test_memory_inspection PRIVATE -UNDEBUG -ffunction-sections -fdata-sections)
target_link_options(test_memory_inspection PRIVATE -Wl,-dead_strip)

add_executable(test_allocation_probe ${BB_ARM64_PROTOTYPE_DIR}/tests/test_allocation_probe.cpp)
target_include_directories(test_allocation_probe PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_allocation_probe PRIVATE cxx_std_20)
target_compile_options(test_allocation_probe PRIVATE -UNDEBUG)

add_executable(test_extra_heaps ${BB_ARM64_PROTOTYPE_DIR}/tests/test_extra_heaps.cpp)
target_include_directories(test_extra_heaps PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_extra_heaps PRIVATE cxx_std_20)
target_compile_options(test_extra_heaps PRIVATE -UNDEBUG)

add_executable(test_live_metadata ${BB_ARM64_PROTOTYPE_DIR}/tests/test_live_metadata.cpp ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim/bbport_resource_lifetime.cpp)
target_include_directories(test_live_metadata PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_compile_features(test_live_metadata PRIVATE cxx_std_20)
target_compile_options(test_live_metadata PRIVATE -UNDEBUG)

add_executable(test_sparse_packet ${BB_ARM64_PROTOTYPE_DIR}/tests/test_sparse_packet.cpp)
target_compile_features(test_sparse_packet PRIVATE cxx_std_20)
target_include_directories(test_sparse_packet PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_options(test_sparse_packet PRIVATE -UNDEBUG)

add_executable(test_packet_metadata ${BB_ARM64_PROTOTYPE_DIR}/tests/test_packet_metadata.cpp)
target_compile_features(test_packet_metadata PRIVATE cxx_std_20)
target_include_directories(test_packet_metadata PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_options(test_packet_metadata PRIVATE -UNDEBUG)

add_executable(test_guest_frame ${BB_ARM64_PROTOTYPE_DIR}/tests/test_guest_frame.cpp)
target_link_libraries(test_guest_frame PRIVATE bb_fex_embedding)
target_compile_options(test_guest_frame PRIVATE -UNDEBUG)

add_executable(test_guest_sync ${BB_ARM64_PROTOTYPE_DIR}/tests/test_guest_sync.cpp)
target_link_libraries(test_guest_sync PRIVATE bb_fex_embedding)
target_compile_options(test_guest_sync PRIVATE -UNDEBUG)

add_executable(test_fex_map_audit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_fex_map_audit.cpp)
target_include_directories(test_fex_map_audit PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_fex_map_audit PRIVATE cxx_std_20)
target_compile_options(test_fex_map_audit PRIVATE -UNDEBUG)

add_executable(test_pmr_lifetime ${BB_ARM64_PROTOTYPE_DIR}/tests/test_pmr_lifetime.cpp)
target_link_libraries(test_pmr_lifetime PRIVATE bb_fex_embedding)
target_compile_options(test_pmr_lifetime PRIVATE -UNDEBUG)

target_include_directories(test_pmr_lifetime PRIVATE ${AETHER}/runtime/sources/fexcore-darwin/FEXCore/Source)

# Isolated CPU persistent-cache feasibility probe, never linked into the game.
add_executable(test_cpu_disk_cache ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_disk_cache.cpp)
target_compile_definitions(test_cpu_disk_cache PRIVATE AETHER_SMOKE_SOURCE="${AETHER}/runtime/probes/fexcore-smoke.cpp")
target_link_libraries(test_cpu_disk_cache PRIVATE bb_fex_embedding)

# Authored persistent cache using the actual syscall/HLE bridge source in the fixture TU.
add_executable(test_cpu_bridge_disk_cache ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_bridge_disk_cache.cpp)
target_compile_definitions(test_cpu_bridge_disk_cache PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp")
target_link_libraries(test_cpu_bridge_disk_cache PRIVATE bb_fex_embedding)

add_executable(test_cpu_cache_identity ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_cache_identity.cpp)
target_include_directories(test_cpu_cache_identity PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_cpu_cache_identity PRIVATE cxx_std_20)

add_executable(test_static_code_files ${BB_ARM64_PROTOTYPE_DIR}/tests/test_static_code_files.cpp)
target_link_libraries(test_static_code_files PRIVATE bb_fex_embedding)

# Quarantined small-batch offline translation, never linked into bb-game-arm64.
add_executable(bb-cpu-cache-generator ${BB_ARM64_PROTOTYPE_DIR}/src/cpu_cache_generator.cpp)
target_compile_definitions(bb-cpu-cache-generator PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp")
target_link_libraries(bb-cpu-cache-generator PRIVATE bb_fex_embedding)

add_executable(test_cpu_cache_validation ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_cache_validation.cpp)
target_include_directories(test_cpu_cache_validation PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_cpu_cache_validation PRIVATE cxx_std_20)
target_compile_options(test_cpu_cache_validation PRIVATE -UNDEBUG)

add_executable(test_cpu_cache_moves ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_cache_moves.cpp)
target_compile_definitions(test_cpu_cache_moves PRIVATE AETHER_SMOKE_SOURCE="${AETHER}/runtime/probes/fexcore-smoke.cpp")
target_link_libraries(test_cpu_cache_moves PRIVATE bb_fex_embedding)
target_compile_options(test_cpu_cache_moves PRIVATE -UNDEBUG)

add_executable(test_cpu_bridge_map_jit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_bridge_disk_cache.cpp)
target_compile_definitions(test_cpu_bridge_map_jit PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp" BB_CACHE_MAP_JIT)
target_link_libraries(test_cpu_bridge_map_jit PRIVATE bb_fex_embedding)

add_executable(test_cpu_bridge_normal_consumer ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_bridge_disk_cache.cpp)
target_compile_definitions(test_cpu_bridge_normal_consumer PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp" BB_CACHE_MAP_JIT BB_CACHE_NORMAL_CONSUMER)
target_link_libraries(test_cpu_bridge_normal_consumer PRIVATE bb_fex_embedding)

add_executable(test_cpu_bridge_engine_consumer ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_bridge_disk_cache.cpp)
target_compile_definitions(test_cpu_bridge_engine_consumer PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp" BB_CACHE_MAP_JIT BB_CACHE_NORMAL_CONSUMER BB_CACHE_ENGINE_CONSUMER)
target_link_libraries(test_cpu_bridge_engine_consumer PRIVATE bb_fex_embedding)

# Generate a common key only after the exact translator/bridge artifacts exist.
# Embedding does not include this header, so no archive/header dependency cycle.
file(GLOB BB_CPU_FRONTEND_HEADERS CONFIGURE_DEPENDS ${BB_ARM64_PROTOTYPE_DIR}/src/*.h)
set(BB_CPU_FRONTEND_IDENTITY_ARGS)
foreach(header IN LISTS BB_CPU_FRONTEND_HEADERS)
 get_filename_component(header_name ${header} NAME)
 list(APPEND BB_CPU_FRONTEND_IDENTITY_ARGS --bridge-part "frontend-header-${header_name}=${header}")
endforeach()
set(BB_CPU_CACHE_BUILD_HEADER ${CMAKE_BINARY_DIR}/generated/bb_cpu_cache_build.h)
set(BB_CPU_CACHE_BUILD_MANIFEST ${CMAKE_BINARY_DIR}/generated/cpu_cache_build.json)
add_custom_command(OUTPUT ${BB_CPU_CACHE_BUILD_HEADER} ${BB_CPU_CACHE_BUILD_MANIFEST}
 COMMAND /usr/bin/env python3 ${BB_ARM64_PROTOTYPE_DIR}/tools/make_cpu_cache_build.py
  --runtime-part fex=$<TARGET_FILE:FEXCore>
  --runtime-part embedding=$<TARGET_FILE:bb_fex_embedding>
  --runtime-part common=$<TARGET_FILE:Common>
  --runtime-part common-tools=$<TARGET_FILE:CommonTools>
  --runtime-part generator-frontend=${BB_ARM64_PROTOTYPE_DIR}/src/cpu_cache_generator.cpp
  --runtime-part target-policy=${BB_ARM64_PROTOTYPE_DIR}/targets.cmake
  --runtime-part build-config=${CMAKE_BINARY_DIR}/CMakeCache.txt
  --bridge-part native=$<TARGET_FILE:bb_native_runtime>
  --bridge-part atrac=$<TARGET_FILE:bb_atrac>
  --bridge-part gpu-adapters=$<TARGET_OBJECTS:bb_gpu_adapters>
  --bridge-part game-frontend=${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp
  ${BB_CPU_FRONTEND_IDENTITY_ARGS}
  --bridge-part gpu-library=${BB_ARM_GPU_LIBRARY}
  --header ${BB_CPU_CACHE_BUILD_HEADER} --manifest ${BB_CPU_CACHE_BUILD_MANIFEST}
 DEPENDS FEXCore bb_fex_embedding Common CommonTools bb_native_runtime bb_atrac bb_gpu_adapters
  ${BB_CPU_FRONTEND_HEADERS}
  ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp
  ${BB_ARM64_PROTOTYPE_DIR}/src/cpu_cache_generator.cpp
  ${BB_ARM64_PROTOTYPE_DIR}/targets.cmake ${CMAKE_BINARY_DIR}/CMakeCache.txt
  ${BB_ARM_GPU_LIBRARY}
  ${BB_ARM64_PROTOTYPE_DIR}/tools/make_cpu_cache_build.py
 VERBATIM COMMAND_EXPAND_LISTS)
add_custom_target(bb_cpu_cache_build_identity DEPENDS ${BB_CPU_CACHE_BUILD_HEADER} ${BB_CPU_CACHE_BUILD_MANIFEST})
add_dependencies(bb-game-arm64 bb_cpu_cache_build_identity)
add_dependencies(bb-cpu-cache-generator bb_cpu_cache_build_identity)

add_executable(test_hle_veneer_pages ${BB_ARM64_PROTOTYPE_DIR}/tests/test_hle_veneer_pages.cpp)
target_link_libraries(test_hle_veneer_pages PRIVATE bb_fex_embedding)
target_compile_options(test_hle_veneer_pages PRIVATE -UNDEBUG)
add_executable(test_hle_veneer_execution ${BB_ARM64_PROTOTYPE_DIR}/tests/test_hle_veneer_execution.cpp)
target_link_libraries(test_hle_veneer_execution PRIVATE bb_fex_embedding)
target_compile_options(test_hle_veneer_execution PRIVATE -UNDEBUG)

add_executable(test_bbnet_callback_tokens ${BB_ARM64_PROTOTYPE_DIR}/tests/test_bbnet_callback_tokens.cpp)
target_include_directories(test_bbnet_callback_tokens PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
target_link_libraries(test_bbnet_callback_tokens PRIVATE bb_fex_embedding)
target_compile_options(test_bbnet_callback_tokens PRIVATE -UNDEBUG)

add_executable(test_bbnet_thd_pointers ${BB_ARM64_PROTOTYPE_DIR}/tests/test_bbnet_thd_pointers.cpp)
target_include_directories(test_bbnet_thd_pointers PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
target_link_libraries(test_bbnet_thd_pointers PRIVATE bb_fex_embedding)
target_compile_options(test_bbnet_thd_pointers PRIVATE -UNDEBUG)

add_executable(test_thd_region_url ${BB_ARM64_PROTOTYPE_DIR}/tests/test_thd_region_url.cpp)
target_include_directories(test_thd_region_url PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bbnet)
target_compile_features(test_thd_region_url PRIVATE cxx_std_23)
target_compile_options(test_thd_region_url PRIVATE -UNDEBUG)

add_executable(test_launcher_inputs ${BB_ARM64_PROTOTYPE_DIR}/tests/test_launcher_inputs.cpp)
target_include_directories(test_launcher_inputs PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_launcher_inputs PRIVATE cxx_std_20)
target_compile_options(test_launcher_inputs PRIVATE -UNDEBUG)

add_executable(test_game_menu_fex ${BB_ARM64_PROTOTYPE_DIR}/tests/test_game_menu_bridge.cpp)
target_compile_definitions(test_game_menu_fex PRIVATE BB_MENU_REAL_FEX)
target_include_directories(test_game_menu_fex PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim)
target_compile_options(test_game_menu_fex PRIVATE -UNDEBUG -include ${BB_ARM64_PROTOTYPE_DIR}/vendor/metal_api/shim/bbport_platform.h)
target_link_libraries(test_game_menu_fex PRIVATE bb_fex_embedding)

add_executable(test_producer_wait_policy ${BB_ARM64_PROTOTYPE_DIR}/tests/test_producer_wait_policy.cpp)
target_include_directories(test_producer_wait_policy PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_producer_wait_policy PRIVATE cxx_std_20)
target_compile_options(test_producer_wait_policy PRIVATE -UNDEBUG)
