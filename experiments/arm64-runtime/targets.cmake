# Project tools use Python 3.11 APIs. Reuse CMake's selected interpreter;
# never rediscover python3 from a build subprocess's restricted PATH.
find_package(Python 3.11 REQUIRED COMPONENTS Interpreter)
# Compile-time source/generated/dependency locations are independent of the
# existing private runtime root. Legacy defaults keep old developer builds valid;
# configure.py's normal path supplies every location explicitly.
set(BB_ARM64_GENERATED_DIR "${BB_ARM64_PROTOTYPE_DIR}/generated" CACHE PATH "Native bridge generated output")
set(BB_ARM64_RUNTIME_ROOT "${BB_ARM64_PROTOTYPE_DIR}" CACHE PATH "Existing private development runtime root")
set(BB_ARM64_DEPENDENCY_INCLUDE_DIRS "/opt/homebrew/include" CACHE STRING "Explicit native dependency headers")
set(BB_ARM64_SDL_LIBRARY "${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/deps-metal-arm64/lib/libSDL3.0.dylib" CACHE FILEPATH "SDL used by the selected native renderer")
set(BB_ARM64_RENDERER_BUILD_DIR "" CACHE PATH "Normal configured renderer build directory")
set(BB_ARM64_RENDERER_LIBRARY "" CACHE FILEPATH "Normal GPU-side renderer artifact")
set(BB_ARM64_CPU_HOST_LIBRARY "" CACHE FILEPATH "Explicit CPU-side host artifact; distinct from GPU renderer")
set(BB_ARM64_RENDERER_TARGET "bbgpu" CACHE STRING "Normal renderer CMake target")
set(BB_ARM64_RENDERER_BUILD_JOBS "1" CACHE STRING "Bounded renderer build parallelism")
option(BB_ARM64_BUILD_RENDERER "Build renderer dependency (disable only for an already configured, verified renderer artifact)" ON)
if(BB_ARM64_RENDERER_BUILD_DIR)
 if(NOT IS_ABSOLUTE "${BB_ARM64_CPU_HOST_LIBRARY}")
  message(FATAL_ERROR "Declare the explicit CPU-side host artifact/candidate for this normal build")
 endif()
 set(BB_ARM_GPU_LIBRARY "${BB_ARM64_CPU_HOST_LIBRARY}" CACHE FILEPATH "Explicit CPU-side host artifact" FORCE)
endif()
set(AETHER ${BB_ARM64_PROTOTYPE_DIR}/vendor/aether)
add_library(bb_fex_disk_service STATIC ${BB_ARM64_PROTOTYPE_DIR}/src/fex_disk_service.cpp)
target_compile_features(bb_fex_disk_service PUBLIC cxx_std_20)
target_include_directories(bb_fex_disk_service PUBLIC ${BB_ARM64_PROTOTYPE_DIR}/src ${AETHER}/src ${CMAKE_BINARY_DIR}/generated ${BB_ARM64_GENERATED_DIR} ${CMAKE_SOURCE_DIR}/Source/Tools/CommonTools ${CMAKE_SOURCE_DIR}/FEXCore/Source)
target_link_libraries(bb_fex_disk_service PUBLIC FEXCore Common CommonTools JemallocLibs ${PTHREAD_LIB})
add_library(bb_fex_embedding STATIC
 ${AETHER}/src/core/fex/fex_guest_engine.cpp
 ${AETHER}/src/core/guest_cpu/fex_guest_cpu.cpp
 ${AETHER}/src/core/guest_cpu/hle_call_adapter.cpp
 ${AETHER}/src/core/guest_cpu/fex_hle_bridge.cpp)
target_compile_features(bb_fex_embedding PUBLIC cxx_std_20)
target_include_directories(bb_fex_embedding PUBLIC ${AETHER}/src ${CMAKE_BINARY_DIR}/generated ${BB_ARM64_GENERATED_DIR} ${CMAKE_SOURCE_DIR}/Source/Tools/CommonTools ${CMAKE_SOURCE_DIR}/FEXCore/Source)
target_link_libraries(bb_fex_embedding PUBLIC bb_fex_disk_service FEXCore Common CommonTools JemallocLibs ${PTHREAD_LIB})
add_executable(test_lookup ${BB_ARM64_PROTOTYPE_DIR}/tests/test_lookup.cpp)
target_compile_features(test_lookup PRIVATE cxx_std_20)
target_compile_definitions(test_lookup PRIVATE AETHER_SMOKE_SOURCE="${AETHER}/runtime/probes/fexcore-smoke.cpp")
target_link_libraries(test_lookup PRIVATE bb_fex_embedding)
add_executable(test_abi ${BB_ARM64_PROTOTYPE_DIR}/tests/test_abi.cpp)
target_link_libraries(test_abi PRIVATE bb_fex_embedding)
add_executable(test_upstream_fex_guest_load_fault
 ${BB_ARM64_PROTOTYPE_DIR}/tests/test_upstream_fex_guest_load_fault.cpp)
target_compile_features(test_upstream_fex_guest_load_fault PRIVATE cxx_std_20)
target_compile_options(test_upstream_fex_guest_load_fault PRIVATE -UNDEBUG)
target_link_libraries(test_upstream_fex_guest_load_fault PRIVATE bb_fex_embedding)
add_executable(bb-runtime-arm64 ${BB_ARM64_PROTOTYPE_DIR}/src/runtime.cpp ${BB_ARM64_PROTOTYPE_DIR}/src/metal_peer.cpp)
target_include_directories(bb-runtime-arm64 PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-runtime-arm64 PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_RUNTIME_ROOT}")
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
include(${BB_ARM64_PROTOTYPE_DIR}/shared_host_targets.cmake)
add_executable(bb-game-arm64 ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp)
target_include_directories(bb-game-arm64 PRIVATE ${BB_ARM64_GENERATED_DIR} ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-game-arm64 PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_RUNTIME_ROOT}")
target_link_libraries(bb-game-arm64 PRIVATE bb_fex_embedding bb_native_runtime)
add_executable(test_jit_scope ${BB_ARM64_PROTOTYPE_DIR}/tests/test_jit_scope.cpp)
target_link_libraries(test_jit_scope PRIVATE bb_fex_embedding)
add_executable(test_jit_scope_original ${BB_ARM64_PROTOTYPE_DIR}/tests/test_jit_scope.cpp)
target_compile_definitions(test_jit_scope_original PRIVATE BB_OLD_JIT_SCOPE)
target_link_libraries(test_jit_scope_original PRIVATE bb_fex_embedding)

target_sources(bb-game-arm64 PRIVATE $<TARGET_OBJECTS:bb_gpu_adapters> $<TARGET_OBJECTS:bb_avplayer_adapters>)
set(BB_ARM_GPU_LIBRARY "${BB_ARM64_PROTOTYPE_DIR}/backend/bbport/bin/gpu-metal-arm64/libbbgpu.avplayer-candidate.dylib" CACHE FILEPATH "Selected normal native renderer library")
target_link_libraries(bb-game-arm64 PRIVATE ${BB_ARM_GPU_LIBRARY})
target_link_options(bb-game-arm64 PRIVATE -Wl,-export_dynamic)
get_filename_component(BB_ARM_GPU_LIBRARY_DIR "${BB_ARM_GPU_LIBRARY}" DIRECTORY)
set_target_properties(bb-game-arm64 PROPERTIES BUILD_RPATH "${BB_ARM_GPU_LIBRARY_DIR}")

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



add_executable(test_native_restart ${BB_ARM64_PROTOTYPE_DIR}/tests/test_native_restart.c ${BB_ARM64_PROTOTYPE_DIR}/src/native_restart.c)
target_include_directories(test_native_restart PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/vendor/bb_runtime/src)
target_compile_definitions(test_native_restart PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_RUNTIME_ROOT}")
add_executable(test_session_state ${BB_ARM64_PROTOTYPE_DIR}/tests/test_session_state.cpp)
target_include_directories(test_session_state PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_session_state PRIVATE cxx_std_20)
target_compile_options(test_session_state PRIVATE -UNDEBUG)

add_executable(test_thread_bootstrap ${BB_ARM64_PROTOTYPE_DIR}/tests/test_thread_bootstrap.cpp)
target_include_directories(test_thread_bootstrap PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_link_libraries(test_thread_bootstrap PRIVATE bb_fex_embedding)
target_compile_options(test_thread_bootstrap PRIVATE -UNDEBUG)

add_executable(bb-game-arm64-old-stack-snapshot ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp)
target_include_directories(bb-game-arm64-old-stack-snapshot PRIVATE ${BB_ARM64_GENERATED_DIR} ${BB_ARM64_PROTOTYPE_DIR}/vendor/blood_echoes)
target_compile_definitions(bb-game-arm64-old-stack-snapshot PRIVATE BB_PROTOTYPE_ROOT="${BB_ARM64_RUNTIME_ROOT}" BB_OLD_THREAD_BOOTSTRAP)
target_sources(bb-game-arm64-old-stack-snapshot PRIVATE $<TARGET_OBJECTS:bb_gpu_adapters> $<TARGET_OBJECTS:bb_avplayer_adapters>)
target_link_libraries(bb-game-arm64-old-stack-snapshot PRIVATE bb_fex_embedding bb_native_runtime ${BB_ARM_GPU_LIBRARY})
target_link_options(bb-game-arm64-old-stack-snapshot PRIVATE -Wl,-export_dynamic)
set_target_properties(bb-game-arm64-old-stack-snapshot PROPERTIES BUILD_RPATH "${BB_ARM_GPU_LIBRARY_DIR}")

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
add_executable(test_fex_prepared_disk ${BB_ARM64_PROTOTYPE_DIR}/tests/test_fex_prepared_disk.cpp)
target_compile_definitions(test_fex_prepared_disk PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp")
target_compile_options(test_fex_prepared_disk PRIVATE -UNDEBUG)
target_link_libraries(test_fex_prepared_disk PRIVATE bb_fex_embedding)
add_executable(probe_fex_paged_catalog ${BB_ARM64_PROTOTYPE_DIR}/tests/probe_fex_paged_catalog.cpp)
target_compile_options(probe_fex_paged_catalog PRIVATE -UNDEBUG)
target_link_libraries(probe_fex_paged_catalog PRIVATE bb_fex_embedding)
target_compile_definitions(test_cpu_bridge_disk_cache PRIVATE AETHER_ENGINE_SOURCE="${AETHER}/src/core/fex/fex_guest_engine.cpp")
target_link_libraries(test_cpu_bridge_disk_cache PRIVATE bb_fex_embedding)

add_executable(test_cpu_cache_identity ${BB_ARM64_PROTOTYPE_DIR}/tests/test_cpu_cache_identity.cpp)
add_executable(test_fex_disk_format ${BB_ARM64_PROTOTYPE_DIR}/tests/test_fex_disk_format.cpp)
target_include_directories(test_fex_disk_format PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_fex_disk_format PRIVATE cxx_std_20)
target_compile_options(test_fex_disk_format PRIVATE -UNDEBUG)
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
 if(header_name MATCHES "^(fex_disk_(loader|io|format)|fex_(unit_catalog|paged_catalog|verified_shard|compiled_units))\\.h$")
  list(APPEND BB_CPU_FRONTEND_IDENTITY_ARGS --provenance-part "storage-header-${header_name}=${header}")
 else()
  list(APPEND BB_CPU_FRONTEND_IDENTITY_ARGS --bridge-part "frontend-header-${header_name}=${header}")
 endif()
endforeach()
set(BB_CPU_CACHE_BUILD_HEADER ${CMAKE_BINARY_DIR}/generated/bb_cpu_cache_build.h)
set(BB_CPU_CACHE_BUILD_MANIFEST ${CMAKE_BINARY_DIR}/generated/cpu_cache_build.json)
if(BB_ARM64_RENDERER_BUILD_DIR)
 set(BB_CPU_CACHE_RENDERER_ROOT "${BB_ARM64_RENDERER_BUILD_DIR}")
else()
 set(BB_CPU_CACHE_RENDERER_ROOT "${BB_ARM_GPU_LIBRARY_DIR}")
endif()
add_custom_command(OUTPUT ${BB_CPU_CACHE_BUILD_HEADER} ${BB_CPU_CACHE_BUILD_MANIFEST}
 COMMAND "${Python_EXECUTABLE}" ${BB_ARM64_PROTOTYPE_DIR}/tools/make_cpu_cache_build.py
  --source-root "${BB_ARM64_PROTOTYPE_DIR}" --build-root "${CMAKE_BINARY_DIR}"
  --renderer-root "${BB_CPU_CACHE_RENDERER_ROOT}"
  --runtime-part fex=$<TARGET_FILE:FEXCore>
  --runtime-part embedding=$<TARGET_FILE:bb_fex_embedding>
  --runtime-part common=$<TARGET_FILE:Common>
  --runtime-part common-tools=$<TARGET_FILE:CommonTools>
  --runtime-part generator-frontend=${BB_ARM64_PROTOTYPE_DIR}/src/cpu_cache_generator.cpp
  --provenance-part target-policy=${BB_ARM64_PROTOTYPE_DIR}/targets.cmake
  --provenance-part build-config=${CMAKE_BINARY_DIR}/CMakeCache.txt
  --provenance-part disk-service=$<TARGET_FILE:bb_fex_disk_service>
  --runtime-part disk-service-frontend=${BB_ARM64_PROTOTYPE_DIR}/src/fex_disk_service.cpp
  --bridge-part native=$<TARGET_FILE:bb_native_runtime>
  --bridge-part atrac=$<TARGET_FILE:bb_atrac>
  --bridge-part gpu-adapters=$<TARGET_OBJECTS:bb_gpu_adapters>
  --bridge-part avplayer-adapters=$<TARGET_OBJECTS:bb_avplayer_adapters>
  --bridge-part game-frontend=${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp
  ${BB_CPU_FRONTEND_IDENTITY_ARGS}
  --bridge-part gpu-library=${BB_ARM_GPU_LIBRARY}
  --header ${BB_CPU_CACHE_BUILD_HEADER} --manifest ${BB_CPU_CACHE_BUILD_MANIFEST}
 DEPENDS FEXCore bb_fex_embedding bb_fex_disk_service Common CommonTools bb_native_runtime bb_atrac bb_gpu_adapters bb_avplayer_adapters
  ${BB_CPU_FRONTEND_HEADERS}
  ${BB_ARM64_PROTOTYPE_DIR}/src/game_runtime.cpp
  ${BB_ARM64_PROTOTYPE_DIR}/src/cpu_cache_generator.cpp
  ${BB_ARM64_PROTOTYPE_DIR}/src/fex_disk_service.cpp
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

add_executable(test_signal_audit ${BB_ARM64_PROTOTYPE_DIR}/tests/test_signal_audit.cpp)
target_include_directories(test_signal_audit PRIVATE ${BB_ARM64_PROTOTYPE_DIR}/src)
target_compile_features(test_signal_audit PRIVATE cxx_std_20)
target_compile_options(test_signal_audit PRIVATE -UNDEBUG)

# Optional ordinary cross-build edge, never a historical compiler command table.
# Configuring does not invoke this; only an explicit CPU target build does.
if(BB_ARM64_RENDERER_BUILD_DIR AND BB_ARM64_BUILD_RENDERER)
 if(NOT IS_ABSOLUTE "${BB_ARM64_RENDERER_BUILD_DIR}" OR
    NOT EXISTS "${BB_ARM64_RENDERER_BUILD_DIR}/CMakeCache.txt" OR
    NOT BB_ARM64_RENDERER_TARGET MATCHES "^[A-Za-z_][A-Za-z_0-9.-]*$" OR
    NOT BB_ARM64_RENDERER_BUILD_JOBS MATCHES "^[1-9][0-9]*$")
  message(FATAL_ERROR "Invalid explicit native renderer build target")
 endif()
 add_custom_target(bb-normal-native-renderer
  COMMAND ${CMAKE_COMMAND} --build "${BB_ARM64_RENDERER_BUILD_DIR}"
   --target "${BB_ARM64_RENDERER_TARGET}" --parallel "${BB_ARM64_RENDERER_BUILD_JOBS}"
  BYPRODUCTS "${BB_ARM64_RENDERER_LIBRARY}"
  VERBATIM)
 add_dependencies(bb-game-arm64 bb-normal-native-renderer)
 add_dependencies(bb-game-arm64-old-stack-snapshot bb-normal-native-renderer)
endif()
