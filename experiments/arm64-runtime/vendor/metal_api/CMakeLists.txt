# bbport GPU library: shadPS4 video core + shader recompiler + Gnm/VideoOut
# drivers, adapted to the native_probe runtime through shims in shim/.
cmake_minimum_required(VERSION 3.24)
project(bbgpu LANGUAGES C CXX)
if (APPLE)
    # The Metal backend (renderer_metal/) is Objective-C++ with ARC.
    enable_language(OBJCXX)
    set(CMAKE_OBJCXX_STANDARD 23)
    set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)
endif()
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()
# Frame pointers keep the loader's thread dumps (watchdog, SIGUSR2 sampling) walkable.
add_compile_options(-fno-omit-frame-pointer)

# bbport: link-time optimization (BB_LTO, default on) and profile-guided optimization
# (BB_PGO: off, generate = instrumented build writing into BB_PGO_DIR, use = build with that
# profile). Covers bbgpu and the static libraries linked into it. No -march: the same build
# runs on the Steam Deck.
option(BB_LTO "Link-time optimization of bbgpu" ON)
set(BB_PGO "off" CACHE STRING "Profile-guided optimization: off, generate, use")
set(BB_PGO_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../pgo" CACHE PATH "Profile directory")
if (BB_LTO)
    include(CheckIPOSupported)
    check_ipo_supported(RESULT bb_ipo_ok OUTPUT bb_ipo_error)
    if (bb_ipo_ok)
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
    else()
        message(WARNING "LTO unavailable: ${bb_ipo_error}")
    endif()
endif()
if (BB_PGO STREQUAL "generate")
    # Atomic counters: the GPU, recording, preparation and copy threads run the same code.
    add_compile_options(-fprofile-generate=${BB_PGO_DIR} -fprofile-update=atomic)
    add_link_options(-fprofile-generate=${BB_PGO_DIR})
    add_compile_definitions(BB_PGO_GENERATE)
elseif (BB_PGO STREQUAL "use")
    # Partial training keeps unprofiled code optimized normally; counters from concurrent
    # threads can be slightly inconsistent (correction). Functions changed since the profile
    # (or built differently for it, bbgpu_init) are compiled without it.
    add_compile_options(-fprofile-use=${BB_PGO_DIR} -fprofile-partial-training
                        -fprofile-correction -Wno-missing-profile -Wno-coverage-mismatch)
endif()

find_package(Vulkan REQUIRED)
find_package(fmt REQUIRED)
find_package(Boost REQUIRED)
find_package(magic_enum REQUIRED)
find_package(tsl-robin-map REQUIRED)
find_package(VulkanMemoryAllocator CONFIG REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(XXHASH REQUIRED IMPORTED_TARGET libxxhash)
pkg_check_modules(SDL3 REQUIRED IMPORTED_TARGET sdl3)
if (NOT APPLE)
    pkg_check_modules(X11 REQUIRED IMPORTED_TARGET x11)
endif()
find_package(miniz REQUIRED)
find_package(Zydis REQUIRED)
# Some Zydis packages export a bare Zycore dependency while Zycore exports
# only its namespaced CMake target. Preserve its actual library location.
if (TARGET Zycore::Zycore AND NOT TARGET Zycore)
    add_library(Zycore ALIAS Zycore::Zycore)
endif()
find_program(GLSLANG_COMPILER NAMES glslang glslangValidator REQUIRED)

set(SIRIT_USE_SYSTEM_SPIRV_HEADERS OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/sirit EXCLUDE_FROM_ALL)

# bbport: FSR 3.1 temporal upscaling / frame generation, native Vulkan (MIT, FireBurn/FSR-Vulkan).
set(FFX_VK_PORTABLE_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(FFX_VK_PORTABLE_BUILD_PROBE OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/fsr-vulkan EXCLUDE_FROM_ALL)

# Host shaders -> headers (same scheme as shadPS4's host_shaders/CMakeLists.txt).
set(SHADER_SRC ${CMAKE_CURRENT_SOURCE_DIR}/shadps4/video_core/host_shaders)
set(SHADER_INCLUDE ${CMAKE_CURRENT_BINARY_DIR}/include)
set(SHADER_DIR ${SHADER_INCLUDE}/video_core/host_shaders)
set(SHADER_HEADERS "")
# bbport (docs/METAL_BACKEND_PLAN.md D11): on APPLE every host shader except the FSR4 ones (not
# ported, D10) is also compiled to MSL: the same GLSL and defines -> a binary .spv (glslang) ->
# <SHADER_NAME>_msl.h (tools/spirv_to_msl_header.cpp, SPIRV-Cross) for renderer_metal/.
set(SHADER_MSL_DIR ${SHADER_INCLUDE}/video_core/host_shaders_msl)
set(SHADER_MSL_SPV_DIR ${CMAKE_CURRENT_BINARY_DIR}/host_shaders_spv)
set(SHADER_MSL_HEADERS "")
function(add_host_shader_msl FILENAME SHADER_NAME)
    set(OUT ${SHADER_MSL_DIR}/${SHADER_NAME}_msl.h)
    set(SPV ${SHADER_MSL_SPV_DIR}/${SHADER_NAME}.spv)
    set(DEFINES ${ARGN})
    list(TRANSFORM DEFINES PREPEND "-D")
    get_filename_component(EXT ${FILENAME} LAST_EXT)
    if (EXT STREQUAL ".comp")
        set(STAGE comp)
    elseif (EXT STREQUAL ".frag")
        set(STAGE frag)
    else()
        set(STAGE vert)
    endif()
    file(GLOB_RECURSE SHADER_INCLUDES CONFIGURE_DEPENDS ${SHADER_SRC}/fsr/*.h)
    add_custom_command(OUTPUT ${SPV}
        COMMAND ${CMAKE_COMMAND} -E make_directory ${SHADER_MSL_SPV_DIR}
        COMMAND ${GLSLANG_COMPILER} --target-env vulkan1.3 -o ${SPV} ${DEFINES} ${SHADER_SRC}/${FILENAME}
        DEPENDS ${SHADER_SRC}/${FILENAME} ${SHADER_INCLUDES} VERBATIM)
    add_custom_command(OUTPUT ${OUT}
        COMMAND ${CMAKE_COMMAND} -E make_directory ${SHADER_MSL_DIR}
        COMMAND spirv_to_msl_header ${SPV} ${OUT} ${SHADER_NAME} ${STAGE}
        DEPENDS ${SPV} spirv_to_msl_header VERBATIM)
    set(SHADER_MSL_HEADERS ${SHADER_MSL_HEADERS} ${OUT} PARENT_SCOPE)
endfunction()
function(add_host_shader FILENAME SHADER_NAME)
    if (APPLE AND NOT SHADER_NAME MATCHES "^fsr4_")
        add_host_shader_msl(${FILENAME} ${SHADER_NAME} ${ARGN})
        set(SHADER_MSL_HEADERS ${SHADER_MSL_HEADERS} PARENT_SCOPE)
    endif()
    set(OUT ${SHADER_DIR}/${SHADER_NAME}.h)
    string(TOUPPER ${SHADER_NAME} SYMBOL)
    set(DEFINES ${ARGN})
    list(TRANSFORM DEFINES PREPEND "-D")
    add_custom_command(OUTPUT ${OUT}
        COMMAND ${CMAKE_COMMAND} -E make_directory ${SHADER_DIR}
        COMMAND ${GLSLANG_COMPILER} --target-env vulkan1.3 -o ${OUT} --vn ${SYMBOL} ${DEFINES} ${SHADER_SRC}/${FILENAME}
        COMMAND ${CMAKE_COMMAND} "-DHEADER_FILE=${OUT}" -P ${SHADER_SRC}/AddShaderHeaderInclude.cmake
        DEPENDS ${SHADER_SRC}/${FILENAME} VERBATIM)
    set(SHADER_HEADERS ${SHADER_HEADERS} ${OUT} PARENT_SCOPE)
endfunction()
add_host_shader(color_to_ms_depth.frag color_to_ms_depth_frag)
add_host_shader(ms_image_blit.frag ms_image_blit_frag)
add_host_shader(ms_image_blit.frag ms_image_blit_msaa_frag SRC_MSAA=1)
add_host_shader(fault_buffer_process.comp fault_buffer_process_comp)
add_host_shader(fs_tri.vert fs_tri_vert)
add_host_shader(fsr.comp fsr_easu_comp SAMPLE_EASU=1)
add_host_shader(fsr.comp fsr_rcas_comp SAMPLE_RCAS=1)
add_host_shader(post_process.frag post_process_frag)
add_host_shader(camera_motion.comp camera_motion_comp)
add_host_shader(upscale_merge.comp upscale_merge_comp)
add_host_shader(upscale_reactive.comp upscale_reactive_comp)
add_host_shader(taa.comp taa_comp)
add_host_shader(taa_sharpen.comp taa_sharpen_comp)
add_host_shader(taa_sharpen.comp taa_sharpen_ldr_comp LDR_OUTPUT=1)
add_host_shader(fsr4_color.comp fsr4_decode_comp)
add_host_shader(fsr4_color.comp fsr4_encode_comp ENCODE=1)
add_host_shader(fsr4_reactive.comp fsr4_reactive_comp)
add_host_shader(buffer_multi_copy.comp buffer_multi_copy_comp)
add_host_shader(occlusion_resolve.comp occlusion_resolve_comp)
add_host_shader(gpu_timestamp.comp gpu_timestamp_comp)
add_host_shader(indirect_guard.comp indirect_guard_comp)
add_host_shader(depth_resample.frag depth_resample_frag)
add_host_shader(depth_resample.frag depth_stencil_resample_frag HAS_STENCIL=1)
add_host_shader(depth_resample.frag depth_stencil_bits_frag STENCIL_BITS=1)
add_host_shader(camera_motion.comp camera_motion_debug_comp DEBUG_OVERLAY=1)
foreach(BPP IN ITEMS 8 16 32 64 96 128)
    add_host_shader(tiling.comp tiling_micro_${BPP}_comp BITS_PER_PIXEL=${BPP} IS_MACRO_TILED=0)
    add_host_shader(tiling.comp tiling_macro_${BPP}_comp BITS_PER_PIXEL=${BPP} IS_MACRO_TILED=1)
endforeach()
add_custom_target(host_shaders DEPENDS ${SHADER_HEADERS})
if (APPLE)
    add_custom_target(host_shaders_msl DEPENDS ${SHADER_MSL_HEADERS})
endif()

file(GLOB_RECURSE VIDEO_CORE CONFIGURE_DEPENDS shadps4/video_core/*.cpp)
file(GLOB_RECURSE RECOMPILER CONFIGURE_DEPENDS shadps4/shader_recompiler/*.cpp)
list(FILTER VIDEO_CORE EXCLUDE REGEX "renderdoc\\.cpp$")
# The Metal backend (renderer_metal/) is added on Apple only, below.
list(FILTER VIDEO_CORE EXCLUDE REGEX "/renderer_metal/")
set(COMMON
    shadps4/common/decoder.cpp shadps4/common/error.cpp
    shadps4/common/io_file.cpp shadps4/common/native_clock.cpp shadps4/common/number_utils.cpp
    shadps4/common/path_util.cpp shadps4/common/rdtsc.cpp shadps4/common/spin_lock.cpp shadps4/common/recursive_lock.cpp
    shadps4/common/string_util.cpp shadps4/common/thread.cpp shadps4/common/signal_context.cpp
    shim/common/logging/log.cpp)
file(GLOB AVPLAYER CONFIGURE_DEPENDS shadps4/core/libraries/avplayer/*.cpp)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavformat libavcodec libavutil libswscale libswresample)
file(GLOB SHIM_SOURCES CONFIGURE_DEPENDS shim/*.cpp shim/*/*.cpp)
# The layer's own modules (independent of the shadPS4 code): layer/.
file(GLOB LAYER_SOURCES CONFIGURE_DEPENDS layer/*.cpp)
# Game profiles (what the translator knows about one game, out of its core): games/.
file(GLOB GAME_SOURCES CONFIGURE_DEPENDS games/*.cpp)
# The translator's own command processor: cp/.
file(GLOB CP_SOURCES CONFIGURE_DEPENDS cp/*.cpp)
add_library(bbgpu SHARED ${VIDEO_CORE} ${RECOMPILER} ${COMMON} ${SHIM_SOURCES} ${LAYER_SOURCES} ${GAME_SOURCES} ${CP_SOURCES}
    shadps4/core/libraries/gnmdriver/gnmdriver.cpp
    shadps4/core/libraries/videoout/driver.cpp shadps4/core/libraries/videoout/video_out.cpp
    shadps4/core/libraries/kernel/equeue.cpp ${AVPLAYER}
    shadps4/core/libraries/videodec/video_utils.cpp)
add_dependencies(bbgpu host_shaders)
add_executable(bb-gpu EXCLUDE_FROM_ALL bb_gpu/bb_gpu_main.cpp)
target_include_directories(bb-gpu PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(bb-gpu PRIVATE bbgpu)
target_link_options(bb-gpu PRIVATE -Wl,-export_dynamic)
if (APPLE)
    add_dependencies(bbgpu host_shaders_msl)
    target_sources(bbgpu PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../src/bundled_runtime_admission.mm)
    target_compile_definitions(bbgpu PRIVATE BB_BUNDLED_RUNTIME_ADMISSION=1)
    set_source_files_properties(${CMAKE_CURRENT_SOURCE_DIR}/../../src/bundled_runtime_admission.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_sources(bbgpu PRIVATE shim/macos_app_icon.mm)
    set_source_files_properties(shim/macos_app_icon.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_link_libraries(bbgpu PRIVATE "-framework AppKit")
    add_executable(macos-app-icon-test EXCLUDE_FROM_ALL ../tests/test_macos_app_icon.mm ../tests/test_gpu_runtime_stubs.cpp)
    target_compile_options(macos-app-icon-test PRIVATE -UNDEBUG)
    target_link_options(macos-app-icon-test PRIVATE -Wl,-export_dynamic)
    set_source_files_properties(../tests/test_macos_app_icon.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_include_directories(macos-app-icon-test PRIVATE shim shadps4)
    target_link_libraries(macos-app-icon-test PRIVATE bbgpu "-framework AppKit")
endif()
if (APPLE)
    # bbport: the Metal backend, Objective-C++ with ARC (docs/METAL_BACKEND_PLAN.md D4). It uses
    # macOS 15 APIs (residency sets) without per-call checks; the factory (presenter_api.cpp)
    # refuses BB_RENDERER=metal below macOS 15.
    file(GLOB_RECURSE RENDERER_METAL CONFIGURE_DEPENDS shadps4/video_core/renderer_metal/*.mm)
    target_sources(bbgpu PRIVATE ${RENDERER_METAL}
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/mtl_index_expand.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_rectlist.cpp)
    set_source_files_properties(${RENDERER_METAL} PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    # SPIRV-Cross (static, macos/deps; D5): SPIR-V -> MSL in renderer_metal/mtl_shader.mm. Its
    # version is the tag macos/build-deps.sh fetches (part of the MSL disk cache key).
    find_path(SPIRV_CROSS_INCLUDE_DIR spirv_cross/spirv_msl.hpp REQUIRED)
    find_library(SPIRV_CROSS_MSL_LIBRARY spirv-cross-msl REQUIRED)
    find_library(SPIRV_CROSS_GLSL_LIBRARY spirv-cross-glsl REQUIRED)
    find_library(SPIRV_CROSS_CORE_LIBRARY spirv-cross-core REQUIRED)
    file(STRINGS ${CMAKE_CURRENT_SOURCE_DIR}/../macos/build-deps.sh SPIRV_CROSS_FETCH_LINE
        REGEX "^fetch spirv-cross ")
    string(REGEX REPLACE "^fetch spirv-cross [^ ]+ ([^ ]+).*$" "\\1" SPIRV_CROSS_VERSION
        "${SPIRV_CROSS_FETCH_LINE}")
    if (NOT SPIRV_CROSS_VERSION OR SPIRV_CROSS_VERSION STREQUAL SPIRV_CROSS_FETCH_LINE)
        set(SPIRV_CROSS_VERSION "unknown")
    endif()
    set_source_files_properties(shadps4/video_core/renderer_metal/mtl_shader.mm PROPERTIES
        COMPILE_DEFINITIONS "BB_SPIRV_CROSS_VERSION=\"${SPIRV_CROSS_VERSION}\""
        INCLUDE_DIRECTORIES "${SPIRV_CROSS_INCLUDE_DIR}")
    # spirv.hpp only (the PerVertexKHR and RectList rewrites are plain C++ on SPIR-V words).
    set_source_files_properties(shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_rectlist.cpp PROPERTIES
        INCLUDE_DIRECTORIES "${SPIRV_CROSS_INCLUDE_DIR}")
    target_link_libraries(bbgpu PRIVATE ${SPIRV_CROSS_MSL_LIBRARY} ${SPIRV_CROSS_GLSL_LIBRARY}
        ${SPIRV_CROSS_CORE_LIBRARY})
    # Build-time tool of the host shaders' MSL headers (add_host_shader_msl above; D11).
    add_executable(spirv_to_msl_header tools/spirv_to_msl_header.cpp)
    target_include_directories(spirv_to_msl_header PRIVATE shadps4 ${SPIRV_CROSS_INCLUDE_DIR})
    target_link_libraries(spirv_to_msl_header PRIVATE ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY})
endif()
# In-game settings menu: Dear ImGui (third_party/imgui, MIT) with its Vulkan backend, and an
# embedded Cyrillic font (third_party/fonts).
target_sources(bbgpu PRIVATE third_party/imgui/imgui.cpp third_party/imgui/imgui_draw.cpp
    third_party/imgui/imgui_tables.cpp third_party/imgui/imgui_widgets.cpp
    third_party/imgui/backends/imgui_impl_vulkan.cpp)
target_include_directories(bbgpu PRIVATE third_party/imgui third_party/imgui/backends)
# Dear ImGui stays private to libbbgpu.so. The library is linked into bb-probe, so its exports are
# in the global symbol scope; a Vulkan layer carrying its own Dear ImGui without -Bsymbolic
# (vkShade 0.1) had its ImGui_ImplVulkan_Init bound to this copy, which called the Vulkan loader
# with the layer's down-chain physical device handle ("Invalid physicalDevice", abort).
set_source_files_properties(third_party/imgui/imgui.cpp third_party/imgui/imgui_draw.cpp
    third_party/imgui/imgui_tables.cpp third_party/imgui/imgui_widgets.cpp
    third_party/imgui/backends/imgui_impl_vulkan.cpp PROPERTIES
    COMPILE_OPTIONS "-fvisibility=hidden;-fvisibility-inlines-hidden")
if (APPLE)
    # The Metal backend's renderer (renderer_metal/mtl_overlay.mm, P5.2). It needs ARC: its
    # __bridge_retained/__bridge_transfer casts do nothing without, and NewFrame's
    # framebufferDescriptor is only autoreleased in the metal-cpp build (IMGUI_IMPL_METAL_CPP).
    target_sources(bbgpu PRIVATE third_party/imgui/backends/imgui_impl_metal.mm)
    set_source_files_properties(third_party/imgui/backends/imgui_impl_metal.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-fvisibility=hidden;-fvisibility-inlines-hidden")
endif()
target_compile_definitions(bbgpu PRIVATE IMGUI_IMPL_VULKAN_NO_PROTOTYPES
    BB_FONT_PATH="${CMAKE_CURRENT_SOURCE_DIR}/third_party/fonts/DejaVuSans.ttf")
set_source_files_properties(shim/bbport_overlay.cpp PROPERTIES
    OBJECT_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/third_party/fonts/DejaVuSans.ttf")
target_include_directories(bbgpu PRIVATE shim layer games cp shadps4 ${SHADER_INCLUDE} third_party/gcn/include third_party/half/include)
target_compile_definitions(bbgpu PRIVATE BOOST_ASIO_STANDALONE NDEBUG)

# Automatic preparation capture: built by default for the supported native
# backend, but no service/thread/IO exists until a verified cohort is configured.
# The historical option name remains a build-only baseline escape hatch.
option(BB_DEV_FETCH_CAPTURE "Automatic validated local compiler-input capture" ON)
option(BB_BUILD_GAME_PREPARATION "Build the native game preparation helpers" OFF)
set(BB_GAME_PREPARATION_SOURCE_ROOT "" CACHE PATH
    "Compile-time ARM runtime source root; does not select runtime data directories")
# The scheduler also uses the real header-only audit scope (disabled unless requested).
# Its include path must not depend on whether automatic shader capture is built.
if(BB_GAME_PREPARATION_SOURCE_ROOT)
    set(BB_FETCH_CAPTURE_SRC "${BB_GAME_PREPARATION_SOURCE_ROOT}/src")
else()
    set(BB_FETCH_CAPTURE_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../../src")
endif()
target_include_directories(bbgpu PRIVATE ${BB_FETCH_CAPTURE_SRC})
if(BB_DEV_FETCH_CAPTURE AND APPLE AND
   (CMAKE_OSX_ARCHITECTURES STREQUAL "arm64" OR
    (NOT CMAKE_OSX_ARCHITECTURES AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm64|aarch64)$")))
    target_sources(bbgpu PRIVATE
        ${BB_FETCH_CAPTURE_SRC}/fetch_compile_observer.cpp
        ${BB_FETCH_CAPTURE_SRC}/fetch_compile_producer.cpp
        ${BB_FETCH_CAPTURE_SRC}/fetch_compile_canonical.cpp
        ${BB_FETCH_CAPTURE_SRC}/fetch_companion_store.cpp
        ${BB_FETCH_CAPTURE_SRC}/fetch_register_store.cpp)
    target_compile_definitions(bbgpu PRIVATE BB_DEV_FETCH_CAPTURE=1)
endif()
# bbport: the Linux interfaces used here, for macOS (shim/bbport_platform.h), in every source.
target_compile_options(bbgpu PRIVATE -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_compile_options(bbgpu PRIVATE -Wno-deprecated-declarations -Wno-missing-field-initializers)
if (APPLE)
    # bbport (macOS): runtime symbols (runtime_memory_*, runtime_process_*) resolve against
    # bb-probe when it loads the library.
    target_link_options(bbgpu PRIVATE -undefined dynamic_lookup)
    # Its libraries (SDL3, MoltenVK as libvulkan) come from ../lib next to bb-probe only: a
    # second rpath into the build tree loaded MoltenVK twice.
    set_target_properties(bbgpu PROPERTIES INSTALL_NAME_DIR "@rpath"
        BUILD_WITH_INSTALL_RPATH ON INSTALL_RPATH "@loader_path/../lib")
else()
    # Loaded with bb-probe at startup (DT_NEEDED, never dlopen): thread_local access without a
    # TLS descriptor call (_dl_tlsdesc_return was ~2% of the draw recording thread).
    target_compile_options(bbgpu PRIVATE -ftls-model=initial-exec)
    # Runtime symbols (runtime_memory_*, runtime_process_*) resolve against bb-probe.
    target_link_options(bbgpu PRIVATE -Wl,--allow-shlib-undefined)
endif()
target_link_libraries(bbgpu PRIVATE ffx-vulkan::portable ffx-vulkan::fsr4-v07-vulkan)
target_link_libraries(bbgpu PUBLIC Vulkan::Headers fmt::fmt Boost::headers magic_enum::magic_enum
    tsl::robin_map GPUOpen::VulkanMemoryAllocator PkgConfig::XXHASH PkgConfig::SDL3 miniz::miniz Zydis::Zydis sirit)
if (APPLE)
    # bbport (macOS): static FFmpeg by name; its pkg-config "-framework X" flags would be split.
    target_include_directories(bbgpu PUBLIC ${FFMPEG_INCLUDE_DIRS})
    target_link_directories(bbgpu PUBLIC ${FFMPEG_LIBRARY_DIRS})
    target_link_libraries(bbgpu PUBLIC avformat avcodec swscale swresample avutil)
    # bbport (macOS): FFmpeg's VideoToolbox/AudioToolbox decoders and SDL's Metal view.
    target_link_libraries(bbgpu PUBLIC "-framework VideoToolbox" "-framework AudioToolbox"
        "-framework CoreMedia" "-framework CoreVideo" "-framework CoreFoundation"
        "-framework CoreServices" "-framework QuartzCore" "-framework Metal" "-framework MetalFX"
        "-framework Foundation" "-framework Security" -lbz2 -lz -liconv)
else()
    target_link_libraries(bbgpu PUBLIC PkgConfig::FFMPEG PkgConfig::X11)
endif()

# Explicit source targets, never an import of a historical compile/link plan.
# The native application build opts in; standalone renderer builds do not
# implicitly build these preparation consumers.
if(BB_BUILD_GAME_PREPARATION)
    include(cmake/GamePreparation.cmake)
    bb_add_game_preparation_targets()
endif()

# Explicit test targets; neither launches the game nor needs a Vulkan device.
add_executable(residency-use-memo-test EXCLUDE_FROM_ALL ../tests/test_residency_use_memo.cpp)
target_compile_options(residency-use-memo-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(residency-use-memo-test PRIVATE shadps4)

add_executable(copy-ring-scope-test EXCLUDE_FROM_ALL ../tests/test_copy_ring_scope.cpp)
add_executable(fragment-access-guard-test EXCLUDE_FROM_ALL ../tests/test_fragment_access_guard.cpp)
target_compile_options(fragment-access-guard-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(fragment-access-guard-test PRIVATE shadps4)
add_executable(mtl-fragment-guard-test EXCLUDE_FROM_ALL ../tests/test_mtl_fragment_guard.mm
    shadps4/video_core/renderer_metal/mtl_render_list.mm)
target_compile_options(mtl-fragment-guard-test PRIVATE -UNDEBUG -fobjc-arc -Wall -Wextra -Werror)
target_include_directories(mtl-fragment-guard-test PRIVATE shadps4)
target_link_libraries(mtl-fragment-guard-test PRIVATE "-framework Foundation" "-framework Metal")
target_compile_options(copy-ring-scope-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(copy-ring-scope-test PRIVATE shim shadps4)
target_link_libraries(copy-ring-scope-test PRIVATE fmt::fmt)

add_executable(mtl-memory-budget-test EXCLUDE_FROM_ALL ../tests/test_mtl_memory_budget.cpp)
if(APPLE)
    add_executable(mtl-copy-pair-test EXCLUDE_FROM_ALL ../tests/test_mtl_copy_pair.mm)
    target_compile_options(mtl-copy-pair-test PRIVATE -UNDEBUG -fobjc-arc)
    target_include_directories(mtl-copy-pair-test PRIVATE shadps4)
    target_link_libraries(mtl-copy-pair-test PRIVATE "-framework Metal" "-framework Foundation")
    add_executable(mtl-copy-watch-test EXCLUDE_FROM_ALL ../tests/test_mtl_copy_watch.mm)
    target_compile_options(mtl-copy-watch-test PRIVATE -UNDEBUG -fobjc-arc)
    target_include_directories(mtl-copy-watch-test PRIVATE shadps4)
    target_link_libraries(mtl-copy-watch-test PRIVATE "-framework Metal" "-framework Foundation")
endif()
target_compile_options(mtl-memory-budget-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(mtl-memory-budget-test PRIVATE shadps4)
add_executable(texture-cache-gc-test EXCLUDE_FROM_ALL ../tests/test_texture_cache_gc.cpp)
target_compile_options(texture-cache-gc-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(texture-cache-gc-test PRIVATE .. shadps4)

add_executable(motion-history-test EXCLUDE_FROM_ALL ../tests/test_motion_history.cpp)
target_compile_options(motion-history-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(motion-history-test PRIVATE ..)
add_executable(motion-shader-test EXCLUDE_FROM_ALL ../tests/test_motion_shaders.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(motion-shader-test PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(motion-shader-test PRIVATE bbgpu)
if(APPLE)
    target_link_options(motion-shader-test PRIVATE -Wl,-export_dynamic)
endif()
add_executable(spirv-float-rules-test EXCLUDE_FROM_ALL ../tests/test_spirv_float_rules.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(spirv-float-rules-test PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(spirv-float-rules-test PRIVATE bbgpu)
add_executable(camera-tracker-test EXCLUDE_FROM_ALL ../tests/test_camera_tracker.cpp
    shadps4/video_core/camera_tracker.cpp)
target_compile_options(camera-tracker-test PRIVATE -UNDEBUG -Wall -Wextra -Werror
    -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_include_directories(camera-tracker-test PRIVATE shim shadps4)
# (ImageId is a Common::SlotId; its header wants the logging headers, nothing of them is linked.)
target_link_libraries(camera-tracker-test PRIVATE fmt::fmt Boost::headers magic_enum::magic_enum)
add_executable(ui-composition-test EXCLUDE_FROM_ALL ../tests/test_ui_composition.cpp)
target_compile_options(ui-composition-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(ui-composition-test PRIVATE ..)
add_executable(ui-compositor-test EXCLUDE_FROM_ALL ../tests/test_ui_compositor.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_compile_options(ui-compositor-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(ui-compositor-test PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(ui-compositor-test PRIVATE bbgpu)
add_executable(scene-targets-core-test EXCLUDE_FROM_ALL ../tests/test_scene_targets_core.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_compile_options(scene-targets-core-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(scene-targets-core-test PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(scene-targets-core-test PRIVATE bbgpu)
if (APPLE)
    # The Metal dylib resolves its guest ABI from these standalone test executables.
    target_link_options(scene-targets-core-test PRIVATE -Wl,-export_dynamic)
    target_link_options(ui-compositor-test PRIVATE -Wl,-export_dynamic)
endif()
add_executable(cp-decoder-test EXCLUDE_FROM_ALL ../tests/test_cp_decoder.cpp cp/cp_decoder.cpp)
target_compile_options(cp-decoder-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(cp-decoder-test PRIVATE cp)
add_executable(settings-test EXCLUDE_FROM_ALL ../tests/test_settings.cpp shim/bbport_settings.cpp)
target_compile_options(settings-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(settings-test PRIVATE .. shim)

if (APPLE)
    # bbport: index expansion for topologies Metal lacks (renderer_metal/mtl_index_expand, P4.3d);
    # plain C++, no Metal device.
    add_executable(mtl-index-expand-test EXCLUDE_FROM_ALL ../tests/test_mtl_index_expand.cpp
        shadps4/video_core/renderer_metal/mtl_index_expand.cpp)
    # (Only the AmdGpu::PrimitiveType enum comes from the frontend; its header wants the logging
    # headers, nothing of them is linked.)
    target_compile_options(mtl-index-expand-test PRIVATE -UNDEBUG -Wall -Wextra -Werror
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-index-expand-test PRIVATE shim shadps4)
    target_link_libraries(mtl-index-expand-test PRIVATE fmt::fmt Boost::headers
        magic_enum::magic_enum)

    # bbport: unit test of the Vulkan -> Metal enum conversions; needs a Metal device only.
    add_executable(vk-to-mtl-test EXCLUDE_FROM_ALL ../tests/test_vk_to_mtl.mm
        shadps4/video_core/renderer_metal/vk_to_mtl.mm)
    set_source_files_properties(../tests/test_vk_to_mtl.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(vk-to-mtl-test PRIVATE -UNDEBUG)
    target_include_directories(vk-to-mtl-test PRIVATE shadps4)
    target_link_libraries(vk-to-mtl-test PRIVATE Vulkan::Headers "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    add_executable(mtl-vertex-math-test EXCLUDE_FROM_ALL ../tests/test_mtl_vertex_math.mm)
    set_source_files_properties(../tests/test_mtl_vertex_math.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_compile_options(mtl-vertex-math-test PRIVATE -UNDEBUG)
    target_include_directories(mtl-vertex-math-test PRIVATE shadps4)
    target_link_libraries(mtl-vertex-math-test PRIVATE "-framework Metal" "-framework Foundation")

    # bbport: compiles every SPIR-V module of a shader cache directory to MSL and a MTLLibrary
    # (renderer_metal/mtl_shader); needs a Metal device only, so it builds the few sources it
    # uses instead of linking bbgpu.
    add_executable(mtl-shader-test EXCLUDE_FROM_ALL ../tests/test_mtl_shader.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_shader.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    # (mtl_shader.mm keeps its SPIRV-Cross include directory and version from the block above.)
    target_compile_options(mtl-shader-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-shader-test PRIVATE shim shadps4)
    target_link_libraries(mtl-shader-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    # bbport: PerVertexKHR rewrite (renderer_metal/mtl_spirv_pervertex, P3.1b): the game's shaders
    # compile and small shaders render the right vertex values on the GPU (see mtl-shader-test).
    add_executable(mtl-pervertex-test EXCLUDE_FROM_ALL ../tests/test_mtl_pervertex.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_pervertex.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-pervertex-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-pervertex-test PRIVATE shim shadps4)
    target_link_libraries(mtl-pervertex-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    # bbport: RectList vertex shader rewrite (renderer_metal/mtl_spirv_rectlist, P4.3d): a small
    # shader renders the extrapolated 4th corner on the GPU and the game's RectList vertex shaders
    # rewrite and compile (see mtl-pervertex-test).
    add_executable(mtl-rectlist-test EXCLUDE_FROM_ALL ../tests/test_mtl_rectlist.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_rectlist.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_rectlist.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-rectlist-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-rectlist-test PRIVATE shim shadps4)
    target_link_libraries(mtl-rectlist-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    # bbport: command buffers, ticks, deferred operations and the fence chain of the Metal
    # backend (renderer_metal/mtl_scheduler, P4.2); needs a Metal device only (see mtl-shader-test).
    add_executable(mtl-scheduler-test EXCLUDE_FROM_ALL ../tests/test_mtl_scheduler.mm
        shadps4/video_core/frame_capture.cpp
        shadps4/video_core/renderer_metal/mtl_scheduler.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_device.mm
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_scheduler.mm
        shadps4/video_core/renderer_metal/mtl_scheduler.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_device.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-scheduler-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-scheduler-test PRIVATE shim shadps4 ${BB_FETCH_CAPTURE_SRC})
    target_link_libraries(mtl-scheduler-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH "-framework Metal" "-framework Foundation" "-framework QuartzCore")
endif()

if (APPLE)
    # bbport: the render command list of the Metal backend (renderer_metal/mtl_render_list, P6.2a):
    # the same commands issued directly and recorded then replayed draw the same pixels; needs a
    # Metal device only (see mtl-shader-test).
    add_executable(mtl-render-list-test EXCLUDE_FROM_ALL ../tests/test_mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm)
    set_source_files_properties(../tests/test_mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-render-list-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-render-list-test PRIVATE shim shadps4)
    target_link_libraries(mtl-render-list-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: the Metal implementation of the cache backend (renderer_metal/mtl_runtime, P2.4):
    # buffers, textures, views, samplers, transfers, the detiling and fault parser kernels. Links
    # bbgpu (the shared Buffer/Image/StreamBuffer code is in it) and needs a Metal device only.
    add_executable(mtl-runtime-test EXCLUDE_FROM_ALL ../tests/test_mtl_runtime.mm)
    set_source_files_properties(../tests/test_mtl_runtime.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-runtime-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-runtime-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    # bbgpu looks the runtime_* services up in the executable (the test defines them).
    target_link_options(mtl-runtime-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-runtime-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: the sampler LOD bias of the Metal backend (renderer_metal/mtl_runtime MakeLimits and
    # CreateSampler): a mip chain of solid colours sampled in a fragment shader with the guest and
    # the scene mip bias, the selected mip checked per pixel; needs a Metal device only.
    add_executable(mtl-sampler-bias-test EXCLUDE_FROM_ALL ../tests/test_mtl_sampler_bias.mm)
    set_source_files_properties(../tests/test_mtl_sampler_bias.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-sampler-bias-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-sampler-bias-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    # bbgpu looks the runtime_* services up in the executable (the test defines them).
    target_link_options(mtl-sampler-bias-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-sampler-bias-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: transfers of the guest formats Metal has no texture for (renderer_metal/
    # mtl_format_convert, P2.4 follow-up): RGB8/16/32 and the packed depth formats, uploaded,
    # sampled and downloaded byte exact through the runtime's entry points. Links bbgpu, needs a
    # Metal device only.
    add_executable(mtl-format-convert-test EXCLUDE_FROM_ALL ../tests/test_mtl_format_convert.mm)
    set_source_files_properties(../tests/test_mtl_format_convert.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-format-convert-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-format-convert-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(mtl-format-convert-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-format-convert-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: the reduced scene targets of the live-resolution path on Metal (renderer_metal/
    # mtl_scene_targets, P7.5 L2): proxy copies in both directions, values checked; links bbgpu,
    # needs a Metal device only.
    add_executable(mtl-scene-targets-test EXCLUDE_FROM_ALL ../tests/test_mtl_scene_targets.mm)
    set_source_files_properties(../tests/test_mtl_scene_targets.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-scene-targets-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-scene-targets-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(mtl-scene-targets-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-scene-targets-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: the Metal pipeline cache core's profile, capabilities and serialized pipeline part
    # (renderer_metal/mtl_pipeline_cache, P4.3a); links bbgpu, needs a Metal device only.
    add_executable(mtl-pipeline-core-test EXCLUDE_FROM_ALL ../tests/test_mtl_pipeline_core.mm)
    set_source_files_properties(../tests/test_mtl_pipeline_core.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-pipeline-core-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-pipeline-core-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(mtl-pipeline-core-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-pipeline-core-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: the coordinate conventions of the Metal rasterizer's recording (renderer_metal/
    # mtl_raster_util.h, P4.3b): viewport conversion, front face, scissor clamp; links bbgpu
    # (VkToMtl), needs a Metal device only.
    add_executable(mtl-raster-test EXCLUDE_FROM_ALL ../tests/test_mtl_raster.mm)
    set_source_files_properties(../tests/test_mtl_raster.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-raster-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-raster-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(mtl-raster-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-raster-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if (APPLE)
    # bbport: compiles the MSL of every host shader header (host_shaders_msl, D11) and creates its
    # function (and a compute pipeline); needs a Metal device only (see mtl-shader-test).
    add_executable(msl-host-shaders-test EXCLUDE_FROM_ALL ../tests/test_msl_host_shaders.mm)
    set_source_files_properties(../tests/test_msl_host_shaders.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(msl-host-shaders-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(msl-host-shaders-test PRIVATE shim shadps4 ${SHADER_INCLUDE})
    target_link_libraries(msl-host-shaders-test PRIVATE "-framework Metal" "-framework Foundation")
    add_dependencies(msl-host-shaders-test host_shaders_msl)
endif()

if (APPLE)
    # bbport: the tiling kernel (host_shaders/tiling.comp) and a one-thread-per-texel reference
    # kernel (tests/tiling_reference.comp, compiled to MSL like the shipped one, names
    # tiling_ref_*) checked byte for byte in both directions against an addrlib CPU reference
    # (tests/tiling_cpu_reference.h: padded layout, unique in-bounds addresses), on the game's
    # tile modes; any nondeterministic or skipped check fails; and the GPU time of both. Links bbgpu (TileManager::MakeDispatch, the tiling tables) and needs a Metal device
    # only.
    set(SAVED_SHADER_MSL_HEADERS ${SHADER_MSL_HEADERS})
    set(SHADER_MSL_HEADERS "")
    foreach(BPP IN ITEMS 8 16 32 64 96 128)
        add_host_shader_msl(../../../../tests/tiling_reference.comp tiling_ref_micro_${BPP}_comp BITS_PER_PIXEL=${BPP} IS_MACRO_TILED=0)
        add_host_shader_msl(../../../../tests/tiling_reference.comp tiling_ref_macro_${BPP}_comp BITS_PER_PIXEL=${BPP} IS_MACRO_TILED=1)
    endforeach()
    add_custom_target(mtl_tiling_reference_shaders DEPENDS ${SHADER_MSL_HEADERS})
    set(SHADER_MSL_HEADERS ${SAVED_SHADER_MSL_HEADERS})
    add_executable(mtl-tiling-test EXCLUDE_FROM_ALL ../tests/test_mtl_tiling.mm
        ../tests/test_gpu_runtime_stubs.cpp)
    set_source_files_properties(../tests/test_mtl_tiling.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-tiling-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-tiling-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(mtl-tiling-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-tiling-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
    add_dependencies(mtl-tiling-test host_shaders_msl mtl_tiling_reference_shaders)
endif()

if (APPLE)
    # bbport: builds a Metal pipeline for every key of a Vulkan pipeline cache directory
    # (renderer_metal/mtl_pso_cache); needs a Metal device only (see mtl-shader-test).
    add_executable(mtl-pipeline-test EXCLUDE_FROM_ALL ../tests/test_mtl_pipeline.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        shadps4/video_core/renderer_vulkan/liverpool_to_vk.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_blend.cpp shadps4/common/number_utils.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
        shadps4/video_core/amdgpu/pixel_format.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_pipeline.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-pipeline-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-pipeline-test PRIVATE shim shadps4 third_party/half/include)
    target_link_libraries(mtl-pipeline-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    # bbport: the tessellation path (renderer_metal/mtl_tessellation, mtl_shader's variants,
    # PsoCache::GetTessellationPipeline, P4.4): small GLSL shaders draw patches into targets that
    # are read back, and the game's tessellated pipelines build; needs a Metal device only (see
    # mtl-pipeline-test).
    add_executable(mtl-tessellation-test EXCLUDE_FROM_ALL ../tests/test_mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        shadps4/video_core/renderer_vulkan/liverpool_to_vk.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_blend.cpp shadps4/common/number_utils.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
        shadps4/video_core/amdgpu/pixel_format.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-tessellation-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-tessellation-test PRIVATE shim shadps4 third_party/half/include)
    target_link_libraries(mtl-tessellation-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

if (APPLE)
    # bbport: the geometry-shader path (renderer_metal/mtl_geometry, mtl_spirv_geometry, mtl_shader's
    # GeometryKernel variant, PsoCache::GetGeometryPipeline, P7.1): small GLSL shaders (points ->
    # two quads) run as kernels and draw strips into targets that are read back; needs a Metal
    # device only (see mtl-pipeline-test).
    add_executable(mtl-geometry-test EXCLUDE_FROM_ALL ../tests/test_mtl_geometry.mm
        shadps4/video_core/renderer_metal/mtl_geometry.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/mtl_spirv_pervertex.cpp
        shadps4/video_core/renderer_metal/mtl_spirv_geometry.cpp
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        shadps4/video_core/renderer_vulkan/liverpool_to_vk.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_blend.cpp shadps4/common/number_utils.cpp
        shadps4/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
        shadps4/video_core/amdgpu/pixel_format.cpp
        shim/common/logging/log.cpp shadps4/common/error.cpp
        shadps4/common/path_util.cpp shadps4/common/string_util.cpp shadps4/common/io_file.cpp
        shadps4/common/thread.cpp)
    set_source_files_properties(../tests/test_mtl_geometry.mm
        shadps4/video_core/renderer_metal/mtl_geometry.mm
        shadps4/video_core/renderer_metal/mtl_pso_cache.mm
        shadps4/video_core/renderer_metal/mtl_tessellation.mm
        shadps4/video_core/renderer_metal/mtl_render_list.mm
        shadps4/video_core/renderer_metal/mtl_shader.mm shadps4/video_core/renderer_metal/mtl_device.mm
        shadps4/video_core/renderer_metal/vk_to_mtl.mm
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-geometry-test PRIVATE -UNDEBUG
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-geometry-test PRIVATE shim shadps4 third_party/half/include)
    target_link_libraries(mtl-geometry-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers
        magic_enum::magic_enum PkgConfig::XXHASH ${SPIRV_CROSS_MSL_LIBRARY}
        ${SPIRV_CROSS_GLSL_LIBRARY} ${SPIRV_CROSS_CORE_LIBRARY} "-framework Metal"
        "-framework Foundation")
endif()

add_executable(scene-resolution-test EXCLUDE_FROM_ALL ../tests/test_scene_resolution.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(scene-resolution-test PRIVATE shim shadps4 third_party/gcn/include)
# The test's Vulkan-Hpp inline methods have a different set of platform extensions
# from vk_platform.cpp. Keep them local and use an explicit dispatcher in the test.
target_compile_options(scene-resolution-test PRIVATE -UNDEBUG -fPIC)
set_source_files_properties(../tests/test_scene_resolution.cpp PROPERTIES
    COMPILE_OPTIONS "-fvisibility=hidden;-fvisibility-inlines-hidden")
target_link_libraries(scene-resolution-test PRIVATE bbgpu)
add_executable(taa-shader-test EXCLUDE_FROM_ALL ../tests/test_taa_shader.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(taa-shader-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/gcn/include)
target_compile_options(taa-shader-test PRIVATE -UNDEBUG -fPIC)
set_source_files_properties(../tests/test_taa_shader.cpp PROPERTIES
    COMPILE_OPTIONS "-fvisibility=hidden;-fvisibility-inlines-hidden")
target_link_libraries(taa-shader-test PRIVATE bbgpu)
add_dependencies(taa-shader-test host_shaders)

add_executable(camera-motion-test EXCLUDE_FROM_ALL ../tests/test_camera_motion.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(camera-motion-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/gcn/include)
target_compile_options(camera-motion-test PRIVATE -UNDEBUG -fPIC)
target_link_libraries(camera-motion-test PRIVATE bbgpu)
add_dependencies(camera-motion-test host_shaders)

add_executable(translation-memory-test EXCLUDE_FROM_ALL ../tests/test_translation_memory.cpp
    ../tests/test_gpu_runtime_stubs.cpp)
target_include_directories(translation-memory-test PRIVATE shim shadps4 third_party/gcn/include)
target_compile_options(translation-memory-test PRIVATE -UNDEBUG -fPIC)
set_source_files_properties(../tests/test_translation_memory.cpp PROPERTIES
    COMPILE_OPTIONS "-fvisibility=hidden;-fvisibility-inlines-hidden")
target_link_libraries(translation-memory-test PRIVATE bbgpu)

# bbport: FSR 4 benchmark outside the game (tools/fsr4_bench.cpp): ninja -C out/gpu fsr4-bench.
add_executable(upscaler-support-test EXCLUDE_FROM_ALL ../tests/test_upscaler_support.cpp
    shim/bbport_settings.cpp)
target_include_directories(upscaler-support-test PRIVATE shim)
target_compile_options(upscaler-support-test PRIVATE -UNDEBUG)

add_executable(fsr4-bench EXCLUDE_FROM_ALL tools/fsr4_bench.cpp
    shadps4/video_core/renderer_vulkan/fsr411/fsr411.cpp)
target_include_directories(fsr4-bench PRIVATE shadps4/video_core/renderer_vulkan/fsr411)
target_link_libraries(fsr4-bench PRIVATE ffx-vulkan::fsr4-v07-vulkan ffx-vulkan::fsr4-v07-assets Vulkan::Vulkan)

# bbport online module (gpu/bbnet, libbbnet.so): built when its libraries are there (shell.nix);
# BB_NET=OFF leaves it out. Without it the port plays offline.
option(BB_NET "Build the online module (shadNet)" ON)
if (BB_NET)
    find_package(Protobuf CONFIG QUIET)
    find_package(nlohmann_json CONFIG QUIET)
    find_package(OpenSSL QUIET)
    find_package(ZLIB QUIET)
    find_path(MINIUPNPC_INCLUDE miniupnpc.h PATH_SUFFIXES miniupnpc)
    find_library(MINIUPNPC_LIBRARY NAMES miniupnpc)
    if (Protobuf_FOUND AND nlohmann_json_FOUND AND OpenSSL_FOUND AND ZLIB_FOUND AND
        MINIUPNPC_INCLUDE AND MINIUPNPC_LIBRARY)
        add_subdirectory(bbnet)
        message(STATUS "Online module: on (libbbnet.so)")
    else()
        message(STATUS "Online module: off (protobuf, nlohmann_json, OpenSSL, zlib or miniupnpc missing)")
    endif()
endif()

add_executable(sampler-memo-test EXCLUDE_FROM_ALL ../tests/test_sampler_memo.cpp)
target_compile_options(sampler-memo-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(sampler-memo-test PRIVATE .. shadps4)


# Internal GPU suballocator tracking; bounded histories and native detour ABI regressions.
add_executable(guest-resource-lifetime-test EXCLUDE_FROM_ALL
    ../tests/test_guest_resource_lifetime.cpp shim/bbport_resource_lifetime.cpp)
target_compile_options(guest-resource-lifetime-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(guest-resource-lifetime-test PRIVATE shim)

add_executable(submit-timing-test EXCLUDE_FROM_ALL ../tests/test_submit_timing.cpp)
target_compile_options(submit-timing-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(submit-timing-test PRIVATE shim)

add_executable(trace-hint-test EXCLUDE_FROM_ALL ../tests/test_trace_hint.cpp)
target_compile_options(trace-hint-test PRIVATE -UNDEBUG -Wall -Wextra -Werror)
target_include_directories(trace-hint-test PRIVATE shim shadps4)

if(APPLE)
    add_executable(mtl-overlay-test EXCLUDE_FROM_ALL ../tests/test_mtl_overlay.mm
        shadps4/video_core/renderer_metal/mtl_overlay.mm
        third_party/imgui/imgui.cpp third_party/imgui/imgui_draw.cpp
        third_party/imgui/imgui_tables.cpp third_party/imgui/imgui_widgets.cpp
        third_party/imgui/backends/imgui_impl_metal.mm)
    set_source_files_properties(../tests/test_mtl_overlay.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    target_compile_options(mtl-overlay-test PRIVATE -UNDEBUG -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-overlay-test PRIVATE shim shadps4 third_party/imgui third_party/imgui/backends)
    target_link_libraries(mtl-overlay-test PRIVATE "-framework Metal" "-framework Foundation")
endif()
if(APPLE AND CMAKE_OSX_ARCHITECTURES STREQUAL "x86_64")
    enable_language(ASM)
    add_executable(guest-resource-hooks-test EXCLUDE_FROM_ALL
        ../tests/test_guest_resource_hooks.cpp ../tests/test_guest_resource_hook_state.S
        ../tests/test_gpu_runtime_stubs.cpp)
    target_compile_options(guest-resource-hooks-test PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-UNDEBUG;-include;${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h>)
    target_include_directories(guest-resource-hooks-test PRIVATE shim shadps4)
    target_link_options(guest-resource-hooks-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(guest-resource-hooks-test PRIVATE bbgpu)
endif()

if(APPLE)
    add_executable(mtl-bda-page-table-test EXCLUDE_FROM_ALL ../tests/test_mtl_bda_page_table.mm
        ../tests/test_gpu_runtime_stubs.cpp)
    set_source_files_properties(../tests/test_mtl_bda_page_table.mm PROPERTIES
        COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_compile_options(mtl-bda-page-table-test PRIVATE -UNDEBUG -fPIC
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(mtl-bda-page-table-test PRIVATE shim shadps4 third_party/gcn/include)
    target_link_options(mtl-bda-page-table-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(mtl-bda-page-table-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if(APPLE)
    add_executable(mtl-fragment-uniforms-test EXCLUDE_FROM_ALL ../tests/test_mtl_fragment_uniforms.mm)
    set_source_files_properties(../tests/test_mtl_fragment_uniforms.mm PROPERTIES COMPILE_OPTIONS "-fobjc-arc;-Wno-unguarded-availability-new")
    target_include_directories(mtl-fragment-uniforms-test PRIVATE shadps4)
    target_link_libraries(mtl-fragment-uniforms-test PRIVATE "-framework Metal" "-framework Foundation")
endif()

# Bounded metadata capture has no Metal resource snapshots or GPU dependency.
add_executable(frame-capture-metadata-test EXCLUDE_FROM_ALL ../tests/test_frame_capture_metadata.cpp
    shadps4/video_core/frame_capture.cpp)
target_compile_options(frame-capture-metadata-test PRIVATE -UNDEBUG -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_include_directories(frame-capture-metadata-test PRIVATE shim shadps4)
target_link_libraries(frame-capture-metadata-test PRIVATE Vulkan::Headers fmt::fmt Boost::headers magic_enum::magic_enum)

add_executable(speculative-descriptor-test EXCLUDE_FROM_ALL ../tests/test_speculative_descriptor.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_compile_options(speculative-descriptor-test PRIVATE -UNDEBUG -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_include_directories(speculative-descriptor-test PRIVATE shim shadps4 third_party/gcn/include)
if(APPLE)
    target_link_options(speculative-descriptor-test PRIVATE -Wl,-export_dynamic)
endif()
target_link_libraries(speculative-descriptor-test PRIVATE bbgpu)

# Descriptor-free Metal pipeline metadata must match the legacy transformation exactly.
add_executable(metal-vertex-metadata-test EXCLUDE_FROM_ALL ../tests/test_metal_vertex_metadata.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_compile_options(metal-vertex-metadata-test PRIVATE -UNDEBUG -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_include_directories(metal-vertex-metadata-test PRIVATE shim shadps4 third_party/gcn/include)
if(APPLE)
    target_link_options(metal-vertex-metadata-test PRIVATE -Wl,-export_dynamic)
endif()
target_link_libraries(metal-vertex-metadata-test PRIVATE bbgpu)

add_executable(srt-watch-test EXCLUDE_FROM_ALL ../tests/test_srt_watch.cpp ../tests/test_gpu_runtime_stubs.cpp)
target_compile_options(srt-watch-test PRIVATE -UNDEBUG -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
target_include_directories(srt-watch-test PRIVATE shim shadps4 third_party/gcn/include)
target_link_libraries(srt-watch-test PRIVATE bbgpu)
if(APPLE)
    target_link_options(srt-watch-test PRIVATE -Wl,-export_dynamic)
endif()

if(APPLE)
    add_executable(texture-cache-views-test EXCLUDE_FROM_ALL ../tests/test_texture_cache_views.mm)
    target_compile_options(texture-cache-views-test PRIVATE -UNDEBUG -fPIC -fobjc-arc
        -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(texture-cache-views-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(texture-cache-views-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(texture-cache-views-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

if(APPLE)
    add_executable(image-1d-lowering-test EXCLUDE_FROM_ALL ../tests/test_image_1d_lowering.mm ../tests/test_gpu_runtime_stubs.cpp)
    target_compile_options(image-1d-lowering-test PRIVATE -UNDEBUG -fPIC -fobjc-arc -include ${CMAKE_CURRENT_SOURCE_DIR}/shim/bbport_platform.h)
    target_include_directories(image-1d-lowering-test PRIVATE shim shadps4 ${SHADER_INCLUDE} third_party/half/include)
    target_link_options(image-1d-lowering-test PRIVATE -Wl,-export_dynamic)
    target_link_libraries(image-1d-lowering-test PRIVATE bbgpu "-framework Metal" "-framework Foundation")
endif()

# State-policy test needs Vulkan vocabulary only; no GPU, game data or app instance.
add_executable(mtl-pass-continuation-test EXCLUDE_FROM_ALL ../tests/test_mtl_pass_continuation.cpp)
target_include_directories(mtl-pass-continuation-test PRIVATE shim shadps4)
target_link_libraries(mtl-pass-continuation-test PRIVATE Vulkan::Headers)

add_executable(remote-lifetime-test EXCLUDE_FROM_ALL ../tests/test_remote_lifetime.cpp shim/bbport_resource_lifetime.cpp)
target_include_directories(remote-lifetime-test PRIVATE shim)

target_compile_options(remote-lifetime-test PRIVATE -UNDEBUG)
