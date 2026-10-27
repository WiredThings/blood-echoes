# SPDX-License-Identifier: GPL-2.0-or-later
# Compile-time source closure only. The consumers retain their existing private
# runtime input/directory admission; this parameter is never a game/data root.
function(bb_add_game_preparation_targets)
    if(NOT APPLE OR NOT (CMAKE_OSX_ARCHITECTURES STREQUAL "arm64" OR
       (NOT CMAKE_OSX_ARCHITECTURES AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm64|aarch64)$")))
        message(FATAL_ERROR "Game preparation helpers require a native arm64 macOS build")
    endif()
    if(NOT BB_DEV_FETCH_CAPTURE)
        message(FATAL_ERROR "Game preparation requires the same renderer's owned capture producer")
    endif()
    if(NOT IS_ABSOLUTE "${BB_GAME_PREPARATION_SOURCE_ROOT}" OR
       NOT IS_DIRECTORY "${BB_GAME_PREPARATION_SOURCE_ROOT}")
        message(FATAL_ERROR "Set BB_GAME_PREPARATION_SOURCE_ROOT to the ARM runtime source tree")
    endif()
    file(REAL_PATH "${BB_GAME_PREPARATION_SOURCE_ROOT}" bb_runtime_source)
    file(REAL_PATH "${CMAKE_CURRENT_SOURCE_DIR}" bb_renderer_source)
    file(REAL_PATH "${bb_runtime_source}/vendor/metal_api" bb_runtime_renderer)
    if(NOT bb_runtime_renderer STREQUAL bb_renderer_source)
        message(FATAL_ERROR "Preparation source root must contain this exact renderer source tree")
    endif()
    foreach(relative IN ITEMS
            src/game_pso_preparer.mm src/game_module_preparer.mm
            src/game_pso_host_services.cpp src/preparation_json.h
            src/preparation_runtime_paths.h src/bundled_runtime_admission.h src/gpu_runtime_state.h
            src/fetch_compile_producer.h src/runtime_exit_codes.h
            vendor/bb_runtime/src/runtime_read_arm.h
            vendor/bb_runtime/src/runtime_restart_protocol.h tests/metadata_layout.cpp)
        if(NOT EXISTS "${bb_runtime_source}/${relative}" OR
           IS_DIRECTORY "${bb_runtime_source}/${relative}")
            message(FATAL_ERROR "Incomplete game preparation source closure: ${relative}")
        endif()
    endforeach()

    # These are the same canonical renderer headers, not a copy or an overlay.
    # Keep the logging shim before upstream common/logging and derive dependency
    # headers from their current CMake targets, not Homebrew/prepared paths.
    add_library(bb-game-preparation-headers INTERFACE)
    target_include_directories(bb-game-preparation-headers INTERFACE
        "${bb_runtime_source}/src" "${bb_runtime_source}/vendor/bb_runtime/src"
        "${bb_renderer_source}" "${bb_renderer_source}/shim"
        "${bb_renderer_source}/shadps4" "${SHADER_INCLUDE}"
        "${bb_renderer_source}/third_party/gcn/include"
        "${bb_renderer_source}/third_party/half/include"
        "$<TARGET_PROPERTY:fmt::fmt,INTERFACE_INCLUDE_DIRECTORIES>"
        "$<TARGET_PROPERTY:sirit,INTERFACE_INCLUDE_DIRECTORIES>")
    target_compile_definitions(bb-game-preparation-headers INTERFACE
        BOOST_ASIO_STANDALONE BB_DEV_FETCH_CAPTURE=1
        "$<TARGET_PROPERTY:fmt::fmt,INTERFACE_COMPILE_DEFINITIONS>")
    target_compile_features(bb-game-preparation-headers INTERFACE cxx_std_23)
    target_compile_options(bb-game-preparation-headers INTERFACE
        -include "${bb_renderer_source}/shim/bbport_platform.h"
        -fno-omit-frame-pointer)
    # Header-only usage requirements. In particular this interface must not
    # introduce a renderer/third-party DSO into the metadata emitter.
    target_link_libraries(bb-game-preparation-headers INTERFACE
        Vulkan::Headers Boost::headers magic_enum::magic_enum tsl::robin_map
        GPUOpen::VulkanMemoryAllocator)

    # The production host-only service implementation explicitly fences guest
    # operations. No runtime, fake VM success, alternate compiler or override TU.
    add_library(game-preparation-host-services OBJECT
        "${bb_runtime_source}/src/game_pso_host_services.cpp")
    target_link_libraries(game-preparation-host-services PRIVATE bb-game-preparation-headers)
    target_compile_options(game-preparation-host-services PRIVATE -UNDEBUG)
    foreach(name IN ITEMS game-pso-preparer game-module-preparer)
        if(name STREQUAL "game-pso-preparer")
            set(main "${bb_runtime_source}/src/game_pso_preparer.mm")
        else()
            set(main "${bb_runtime_source}/src/game_module_preparer.mm")
        endif()
        add_executable(${name} "${main}" $<TARGET_OBJECTS:game-preparation-host-services>)
        target_link_libraries(${name} PRIVATE bb-game-preparation-headers bbgpu
            "-framework Metal" "-framework Foundation")
        target_compile_options(${name} PRIVATE -UNDEBUG -fobjc-arc)
        # libbbgpu's production runtime imports resolve to the fenced host
        # services above. Both consumers load this one normal bbgpu target.
        target_link_options(${name} PRIVATE -Wl,-export_dynamic)
        set_target_properties(${name} PROPERTIES BUILD_RPATH "$<TARGET_FILE_DIR:bbgpu>")
    endforeach()
    target_compile_definitions(game-module-preparer PRIVATE BB_WITH_OWNED_COMPANION_CONSUMER=1)

    # Same parser domain as build_metadata_layout.py: every current u32/u64/bool
    # Profile member, preserving declaration order. Reconfigure if it changes.
    set(profile "${bb_renderer_source}/shadps4/shader_recompiler/profile.h")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${profile}")
    file(STRINGS "${profile}" fields REGEX
        "^[ \t]*(u32|u64|bool)[ \t]+[A-Za-z_][A-Za-z_0-9]*[ \t]*(\\{[^{}]*\\})?;")
    set(names "")
    set(contents "")
    foreach(field IN LISTS fields)
        string(REGEX MATCH "^[ \t]*(u32|u64|bool)[ \t]+([A-Za-z_][A-Za-z_0-9]*)" match "${field}")
        set(name "${CMAKE_MATCH_2}")
        if(NOT name OR name IN_LIST names)
            message(FATAL_ERROR "Invalid/duplicate canonical Profile member")
        endif()
        list(APPEND names "${name}")
        string(APPEND contents "PROFILE_FIELD(${name})\n")
    endforeach()
    list(LENGTH names field_count)
    if(field_count LESS 41)
        message(FATAL_ERROR "Incomplete canonical Profile member declarations")
    endif()
    set(layout_include "${CMAKE_CURRENT_BINARY_DIR}/game-preparation")
    file(GENERATE OUTPUT "${layout_include}/profile_fields.inc" CONTENT "${contents}")
    add_executable(metadata-layout "${bb_runtime_source}/tests/metadata_layout.cpp")
    target_link_libraries(metadata-layout PRIVATE bb-game-preparation-headers)
    target_include_directories(metadata-layout PRIVATE "${layout_include}")
    target_compile_definitions(metadata-layout PRIVATE NDEBUG)
    target_compile_options(metadata-layout PRIVATE -Wno-invalid-offsetof)
    # Deliberately no bbgpu or host-services link here: this only emits ABI
    # metadata and synthetic writer fixtures; it must not load a renderer.
endfunction()
