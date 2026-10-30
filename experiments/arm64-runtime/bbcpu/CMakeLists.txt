cmake_minimum_required(VERSION 3.24)
project(BloodEchoesBbcpu LANGUAGES C CXX ASM)

# This target never links FEX. The application's renderer and lifecycle remain
# separate consumers; building this directory does not build or launch a game.
if(NOT APPLE OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm64|aarch64)$")
  message(FATAL_ERROR "The isolated bbcpu host requires native Apple Silicon")
endif()
find_package(zydis 4.1.1 CONFIG REQUIRED)
# Zydis 4.1's exported interface names "Zycore" even when the dependency
# exports the namespaced target. Resolve that target, not an ambient -l search.
if(NOT TARGET Zycore AND TARGET Zycore::Zycore)
  add_library(Zycore ALIAS Zycore::Zycore)
endif()
set(BBCPU_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../vendor/bbcpu")
# A CPU-only compatibility domain: unrelated application/UI/Metal changes are
# absent. Both the future local builder and this runtime consume this contract.
file(GLOB_RECURSE BBCPU_CONTRACT_HEADERS CONFIGURE_DEPENDS RELATIVE "${BBCPU_SOURCE}"
  "${BBCPU_SOURCE}/src/cpu/*.h" "${BBCPU_SOURCE}/src/recomp/*.h")
list(SORT BBCPU_CONTRACT_HEADERS)
set(BBCPU_CONTRACT "BloodEchoes.bbcpu.arm64.native-tsc.abi5\n")
set(BBCPU_CONTRACT_JSON "")
foreach(header IN LISTS BBCPU_CONTRACT_HEADERS)
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${BBCPU_SOURCE}/${header}")
  file(SHA256 "${BBCPU_SOURCE}/${header}" digest)
  string(APPEND BBCPU_CONTRACT "${header}=${digest}\n")
  if(BBCPU_CONTRACT_JSON)
    string(APPEND BBCPU_CONTRACT_JSON ",")
  endif()
  string(APPEND BBCPU_CONTRACT_JSON "\"${header}\":\"${digest}\"")
endforeach()
string(SHA256 BBCPU_ABI_IDENTITY "${BBCPU_CONTRACT}")
file(WRITE "${CMAKE_BINARY_DIR}/bbcpu-abi-identity.json"
  "{\"schema\":1,\"abi\":5,\"architecture\":\"arm64\",\"native_tsc\":true,\"header_contract_sha256\":\"${BBCPU_ABI_IDENTITY}\",\"headers\":{${BBCPU_CONTRACT_JSON}}}\n")
file(GLOB BBCPU_C CONFIGURE_DEPENDS "${BBCPU_SOURCE}/src/cpu/*.c")
add_library(bbcpu STATIC ${BBCPU_C} "${BBCPU_SOURCE}/src/cpu/hostcall.S")
target_include_directories(bbcpu PUBLIC "${BBCPU_SOURCE}/src/cpu")
target_compile_definitions(bbcpu PUBLIC BBCPU_NATIVE_TSC=1 BBCPU_ABI_IDENTITY="${BBCPU_ABI_IDENTITY}")
target_link_libraries(bbcpu PUBLIC Zydis::Zydis)
target_compile_features(bbcpu PUBLIC c_std_11)
target_compile_options(bbcpu PRIVATE -O2 -UNDEBUG -Wall -Wextra -Werror -Wno-unused-parameter)

add_executable(bbcpu-scan "${BBCPU_SOURCE}/tools/recomp/scan.c")
target_link_libraries(bbcpu-scan PRIVATE Zydis::Zydis)
target_compile_options(bbcpu-scan PRIVATE -O2 -UNDEBUG)
add_executable(bbrecomp "${BBCPU_SOURCE}/tools/recomp/bbrecomp.c")
target_link_libraries(bbrecomp PRIVATE bbcpu)
target_compile_options(bbrecomp PRIVATE -O1 -UNDEBUG -Wall)

add_executable(bbcpu-code-preparer ../src/bbcpu_code_preparer.cpp)
target_include_directories(bbcpu-code-preparer PRIVATE ../src)
target_compile_features(bbcpu-code-preparer PRIVATE cxx_std_23)
target_compile_options(bbcpu-code-preparer PRIVATE -O2 -UNDEBUG -Wall -Wextra -Wno-deprecated-declarations)

add_library(bbcpu-execution STATIC ../src/bbcpu_backend.cpp ../src/bbcpu_hle_service.cpp
 "../vendor/aether/src/core/guest_cpu/hle_call_adapter.cpp")
target_include_directories(bbcpu-execution PUBLIC ../src ../vendor/aether/src)
target_compile_features(bbcpu-execution PUBLIC cxx_std_23)
target_compile_options(bbcpu-execution PRIVATE -O2 -UNDEBUG -Wall -Wextra)
target_link_libraries(bbcpu-execution PUBLIC bbcpu)

option(BB_BUILD_GAME_HOST "Build the shared game host against bbcpu only" OFF)
if(BB_BUILD_GAME_HOST)
  get_filename_component(BB_ARM64_PROTOTYPE_DIR "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
  set(AETHER "${BB_ARM64_PROTOTYPE_DIR}/vendor/aether")
  foreach(required BB_ARM64_GENERATED_DIR BB_ARM64_RUNTIME_ROOT
      BB_ARM64_SDL_LIBRARY BB_ARM64_CPU_HOST_LIBRARY)
    if(NOT IS_ABSOLUTE "${${required}}")
      message(FATAL_ERROR "Explicit ${required} is required for the isolated game host")
    endif()
  endforeach()
  include("${BB_ARM64_PROTOTYPE_DIR}/shared_host_targets.cmake")
  add_executable(bb-game-bbcpu ../src/game_runtime.cpp
    $<TARGET_OBJECTS:bb_gpu_adapters> $<TARGET_OBJECTS:bb_avplayer_adapters>)
  target_compile_features(bb-game-bbcpu PRIVATE cxx_std_23)
  target_include_directories(bb-game-bbcpu BEFORE PRIVATE "${BB_ARM64_CPU_HOST_INCLUDE_DIR}")
  target_include_directories(bb-game-bbcpu PRIVATE "${BB_ARM64_GENERATED_DIR}"
    ../vendor/blood_echoes "${BB_ARM64_CPU_HOST_INCLUDE_DIR}")
  target_compile_definitions(bb-game-bbcpu PRIVATE BB_CPU_BACKEND_BBCPU=1
    BB_PROTOTYPE_ROOT="${BB_ARM64_RUNTIME_ROOT}")
  target_link_libraries(bb-game-bbcpu PRIVATE bbcpu-execution bb_native_runtime
    "${BB_ARM64_CPU_HOST_LIBRARY}" "-framework Security")
  target_link_options(bb-game-bbcpu PRIVATE -Wl,-export_dynamic)
  get_filename_component(host_library_dir "${BB_ARM64_CPU_HOST_LIBRARY}" DIRECTORY)
  set_target_properties(bb-game-bbcpu PROPERTIES BUILD_RPATH "${host_library_dir}")
endif()

include(CTest)
if(BUILD_TESTING)
  add_executable(bbcpu-contract-probe ../tests/bbcpu_contract_probe.c)
  target_link_libraries(bbcpu-contract-probe PRIVATE bbcpu)
  target_compile_options(bbcpu-contract-probe PRIVATE -O2 -UNDEBUG -Wall -Wextra -Werror)
  add_test(NAME bbcpu-contract COMMAND bbcpu-contract-probe)
  add_executable(bbcpu-hle-probe ../tests/bbcpu_hle_probe.cpp)
  target_link_libraries(bbcpu-hle-probe PRIVATE bbcpu-execution)
  target_compile_options(bbcpu-hle-probe PRIVATE -O2 -UNDEBUG -Wall -Wextra)
  add_test(NAME bbcpu-hle COMMAND bbcpu-hle-probe)
  find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
  add_test(NAME bbcpu-hle-refusals COMMAND "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_LIST_DIR}/../tests/test_bbcpu_hle_refusals.py"
    --probe $<TARGET_FILE:bbcpu-hle-probe>)
  add_test(NAME bbcpu-effective-input COMMAND "${Python3_EXECUTABLE}"
    "${CMAKE_CURRENT_LIST_DIR}/../tests/test_bbcpu_effective_input.py"
    --preparer $<TARGET_FILE:bbcpu-code-preparer> --scan $<TARGET_FILE:bbcpu-scan>)
  add_executable(bbcpu-static-admission-probe ../tests/bbcpu_static_admission_probe.cpp)
  target_compile_features(bbcpu-static-admission-probe PRIVATE cxx_std_23)
  target_include_directories(bbcpu-static-admission-probe PRIVATE ../src)
  target_link_libraries(bbcpu-static-admission-probe PRIVATE bbcpu)
  target_compile_options(bbcpu-static-admission-probe PRIVATE -O2 -UNDEBUG -Wno-deprecated-declarations)
  # Header/API regression of the retained FEX wrapper. No FEX execution library.
  add_executable(hle-service-equivalence ../tests/test_hle_service_equivalence.cpp
    ../vendor/aether/src/core/guest_cpu/fex_hle_bridge.cpp
    ../vendor/aether/src/core/guest_cpu/hle_call_adapter.cpp)
  target_include_directories(hle-service-equivalence PRIVATE ../src ../vendor/aether/src
    ../vendor/aether/runtime/sources/fexcore-darwin/FEXCore/include
    ../vendor/aether/runtime/sources/fexcore-darwin/External/unordered_dense/include
    ../vendor/aether/runtime/sources/fexcore-darwin/External/fmt/include)
  target_compile_features(hle-service-equivalence PRIVATE cxx_std_23)
  target_compile_options(hle-service-equivalence PRIVATE -O2 -UNDEBUG)
  add_test(NAME hle-service-equivalence COMMAND hle-service-equivalence)
endif()
