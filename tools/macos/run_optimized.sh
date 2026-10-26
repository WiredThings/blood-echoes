#!/usr/bin/env bash
# Visual launcher's entry point. Metal runs in the Rosetta game process; the Vulkan
# compatibility option retains the native ARM64 renderer. Neither compiles at launch.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
die() { echo "STOP: $*" >&2; exit 1; }
# Dedicated CLI entry only; normal Play always discards automation variables.
benchmark=0
if [[ ${1:-} == --benchmark ]]; then benchmark=1; shift; fi
renderer=${BB_RENDERER:-metal}
[[ $renderer == metal || $renderer == vulkan ]] || die "Unknown renderer: $renderer"
if [[ $renderer == metal ]]; then
    if [[ -f .packaged ]]; then
        probe=$PWD/bin/bb-probe-metal
        metal_lib=$PWD/bin/gpu-metal/libbbgpu.dylib
    else
        probe=$PWD/out/bb-probe-metal
        metal_lib=$PWD/out/gpu-metal/libbbgpu.dylib
    fi
    [[ -x $probe && -f $metal_lib ]] || die "The Metal renderer build is missing"
    export BB_PREBUILT=1 BB_NATIVE_GPU=0 BB_RENDERER=metal BB_PROBE=$probe
    export BB_PC_MODEL=1 BB_GUEST_IN_PLACE=1 BB_LAYER_MEMORY=1 BB_GUEST_MEMORY=host
    export BB_GPU_LIFETIME=${BB_GPU_LIFETIME:-1}
    # Only CPU-invalid, idle, GPU-complete sampled copies; valid cache budgets stay intact.
    export BB_TEXTURE_INVALID_RECLAIM=${BB_TEXTURE_INVALID_RECLAIM:-1}
    export BB_PREUPLOAD=${BB_PREUPLOAD:-1} BB_UFFD=0 BB_COPY_GPU_BUFFERS=1
    export BB_GPU_WRITE_TWINS=1 BB_GPU_WRITE_TWINS_MAX=65536
    export BB_AUTO_NOHIZ=0 BB_GPU_LABELS=0 ROSETTA_ADVERTISE_AVX=1
    export BB_HDR=0
    # Same combined profile accepted after the fixed-scene all-OFF/all-ON ABBA.
    # Keep explicit 0 overrides available for future same-binary comparisons.
    export BB_MTL_BATCH_CLEARS=${BB_MTL_BATCH_CLEARS:-1}
    export BB_MTL_DIRECT_COPY=${BB_MTL_DIRECT_COPY:-1}
    export BB_MTL_ADAPTIVE_STAGING=${BB_MTL_ADAPTIVE_STAGING:-1}
    export BB_MTL_TEXTURE_TILING=${BB_MTL_TEXTURE_TILING:-1}
    export BB_MTL_PRECISE_RESIDENCY=${BB_MTL_PRECISE_RESIDENCY:-1}
    export BB_MTL_COALESCE_BARRIERS=${BB_MTL_COALESCE_BARRIERS:-1}
    export BB_MTL_QUERY_COMPUTE_CHAIN=${BB_MTL_QUERY_COMPUTE_CHAIN:-1}
    export BB_MTL_PRECISE_COMPUTE=${BB_MTL_PRECISE_COMPUTE:-1}
    export BB_MTL_TEXTURE_TILING_HALF=${BB_MTL_TEXTURE_TILING_HALF:-1}
    # Owner accepts neutral/noisy isolated FPS; retain full-surface/hoist-safe fallback.
    export BB_MTL_FOLD_CLEARS=${BB_MTL_FOLD_CLEARS:-1}
    export BB_MTL_CONTINUE_CLEARS=${BB_MTL_CONTINUE_CLEARS:-1}
    export BB_MTL_DEPTH_LAYOUT=${BB_MTL_DEPTH_LAYOUT:-0}
    # Correctness-tested alternatives remain available without becoming recommendations.
    export BB_MTL_NATIVE_CLEAR_BATCH=${BB_MTL_NATIVE_CLEAR_BATCH:-0}
    export BB_MTL_MERGE_OVERLAY=${BB_MTL_MERGE_OVERLAY:-0}
    export BB_MTL_FILL_UPDATES=${BB_MTL_FILL_UPDATES:-0}
    export BB_LAYER_USE_CACHE=${BB_LAYER_USE_CACHE:-0}
    export BB_MTL_COPY_PAIR=${BB_MTL_COPY_PAIR:-0}
    export BB_MTL_RENDER_UPDATES=${BB_MTL_RENDER_UPDATES:-0}
    export BB_MTL_LIBRARY_REUSE=${BB_MTL_LIBRARY_REUSE:-0}
    # The on-disk compilation archive is useful during prewarm; active PSOs stay cached.
    export BB_MTL_RELEASE_WARM_ARCHIVE=${BB_MTL_RELEASE_WARM_ARCHIVE:-1}
    export BB_MTL_PERSIST_WARM_ARCHIVE=${BB_MTL_PERSIST_WARM_ARCHIVE:-0}
    unset BB_GPU_PROCESS BB_GPU_DEPS VK_DRIVER_FILES VK_ICD_FILENAMES
    unset BB_VULKAN_LIBRARY SDL_VULKAN_LIBRARY DYLD_LIBRARY_PATH BB_MAC_UNIFIED_MEMORY
    native_default=0
    # This package's native dependency closure requires macOS 27; older systems
    # retain the complete in-process Metal path without changing saved settings.
    if [[ -f NATIVE-METAL.json ]]; then
        native_min=$(/usr/bin/plutil -extract minimum_os_major raw -o - NATIVE-METAL.json 2>/dev/null || printf '999')
        native_enabled=$(/usr/bin/plutil -extract default_enabled raw -o - NATIVE-METAL.json 2>/dev/null || true)
        if [[ $native_min =~ ^[0-9]+$ && $native_enabled == true && $(sw_vers -productVersion | cut -d. -f1) -ge $native_min ]]; then native_default=1; fi
    fi
    export BB_METAL_ARM64=${BB_METAL_ARM64:-$native_default}
    [[ $BB_METAL_ARM64 == 0 || $BB_METAL_ARM64 == 1 ]] || die "Invalid ARM64 Metal selection"
    if [[ $BB_METAL_ARM64 == 1 ]]; then
        [[ -f NATIVE-METAL.json || -f DEVELOPMENT-ARM64-METAL.json ]] || die "ARM64 Metal components are not included"
        if [[ -f NATIVE-METAL.json && $native_default != 1 ]]; then die "ARM64 Metal is unavailable on this macOS version"; fi
        gpu=$PWD/bin/gpu-metal-arm64/bb-gpu
        native_app=$PWD/bin/Bloodborne\ Metal.app/Contents/MacOS/bb-gpu
        [[ ! -x $native_app ]] || gpu=$native_app
        [[ -x $gpu && -f $PWD/bin/gpu-metal-arm64/libbbgpu.dylib ]] || die "ARM64 Metal backend missing"
        export BB_NATIVE_GPU=1 BB_GPU_PROCESS=$gpu BB_GPU_DEPS=$PWD/deps-metal-arm64
    fi
else
if [[ -f .packaged ]]; then
    # Self-contained release: x86-64 game runtime and native arm64 renderer.
    probe=$PWD/bin/bb-probe
    gpu=$PWD/bin/gpu-arm64/bb-gpu
    deps=$PWD/deps-arm64
else
    if [[ -f src/runtime_quicksave_bindings.h ]]; then default_native=$PWD; else default_native=$PWD/out/native-05-checkout; fi
    native=${BB_NATIVE_CHECKOUT:-$default_native}
    [[ -d $native ]] || die "Optimized native build is missing from $native"
    native=$(cd "$native" && pwd -P)
    probe=$native/out/bb-probe
    gpu=$native/out/gpu-arm64/bb-gpu
    deps=$native/deps-arm64
fi
[[ -x $probe && -x $gpu ]] || die "The optimized runtime or native renderer is missing"
[[ -f $(dirname "$probe")/gpu/libbbgpu.dylib && -f $(dirname "$gpu")/libbbgpu.dylib ]] || die "The optimized renderer libraries are missing"
[[ -f $deps/lib/libvulkan.1.dylib && -f $deps/lib/kosmickrisp/kosmickrisp_mesa_icd.json ]] || die "The native graphics driver is missing"
fi
: "${BB_GAME_DIR:?Choose the game folder in the app}"
export BB_DATA_DIR=${BB_DATA_DIR:-$HOME/Library/Application Support/bbport/optimized-launcher}
export BB_USER_DIR=${BB_USER_DIR:-$HOME/Library/Application Support/bbport/user}
export BB_CONFIG=${BB_CONFIG:-$BB_DATA_DIR/bbport.ini}
if [[ ${BB_ONLINE:-0} == 1 ]]; then
    [[ $renderer == metal ]] || die "Online play currently requires Metal"
    if [[ -f .packaged ]]; then net=$PWD/bin/gpu-net/libbbnet.dylib
    else net=${BB_NET_MODULE:-$PWD/out/online/libbbnet.dylib}; fi
    [[ -f $net ]] || die "Optional online module is missing"
    [[ ${BB_NET_BACKEND:-thd} == thd ]] || die "Unsupported online backend"
    [[ -n ${BB_THD_ONLINE_ID:-} && -n ${BB_THD_TOKEN:-} ]] || die "Sign in on the Online page first"
    export BB_NET_MODULE=$net BB_NET_BACKEND=thd BB_SKIP_NETWORK_CHOICE=0
    export BB_THD_VERIFY_TLS=1 BB_BRIDGE_LIVE_MAP=0
else
    export BB_ONLINE=0
    unset BB_NET_MODULE BB_NET_BACKEND BB_THD_TOKEN BB_THD_ONLINE_ID
    unset BB_THD_SERVER BB_THD_AUTH BB_THD_HTTP_ORIGIN BB_THD_HTTP_ROUTE BB_THD_STUN BB_THD_RULESET BB_P2P_PORT BB_COMMUNITY_SERVER
    unset BB_SHADNET_NPID BB_SHADNET_PASSWORD BB_SHADNET_TOKEN SHADPS4_HTTP_HOST_OVERRIDES_JSON
fi
if [[ $renderer == vulkan ]]; then
export BB_PREBUILT=1 BB_NATIVE_GPU=1 BB_PROBE=$probe BB_GPU_PROCESS=$gpu BB_GPU_DEPS=$deps
export BB_VULKAN_LIBRARY=$deps/lib/libvulkan.1.dylib SDL_VULKAN_LIBRARY=$deps/lib/libvulkan.1.dylib
export VK_DRIVER_FILES=$deps/lib/kosmickrisp/kosmickrisp_mesa_icd.json
unset VK_ICD_FILENAMES DYLD_LIBRARY_PATH
fi
# The precision policy changes generated SPIR-V. Keep the old release's caches intact,
# and separate the strict fallback from this port's optimized shader profile.
precision=stages
[[ ${BB_SPIRV_PRECISE:-} != all ]] || precision=all
if [[ $renderer == metal ]]; then
    export BB_GPU_USER_DIR=${BB_GPU_USER_DIR:-$BB_DATA_DIR/gpu-cache/metal1}
else
    export BB_GPU_USER_DIR=${BB_GPU_USER_DIR:-$BB_DATA_DIR/gpu-cache/perf1-$precision}
fi
export MESA_SHADER_CACHE_DIR=$BB_DATA_DIR/mesa-cache-perf1 PYTHONDONTWRITEBYTECODE=1
if [[ -f .packaged ]]; then
    export PYTHON=$PWD/python/bin/python3 PYTHONHOME=$PWD/python PYTHONNOUSERSITE=1
    unset PYTHONPATH PYTHONSTARTUP
else
    export PYTHON=${PYTHON:-$(command -v python3)}
fi
export BB_QUICKSAVE=${BB_QUICKSAVE:-0}
export BB_QUICKSAVE_HELPER=$PWD/tools/macos/quicksave.py
export BB_QUICKSAVE_PYTHON=$PYTHON BB_QUICKSAVE_STATUS=$BB_DATA_DIR/out/quicksave-status.txt
export BB_SKIP_EMPTY_DRAWS=1 BB_VK_RECORD_THREADS=1 BB_FRAMES_AHEAD=2 BB_GPU_QOS=1
export BB_PIPE_PARK_WAITS=0
[[ $renderer != vulkan ]] || export BB_MAC_UNIFIED_MEMORY=0
export BB_CMDBUF_TRIM=${BB_CMDBUF_TRIM:-1} BB_KK_POOL_FIX=${BB_KK_POOL_FIX:-1}
# These must never turn a normal Play click into an automated input/benchmark session.
if [[ $benchmark == 0 ]]; then
    unset BB_DEV_WORLD_WARPER_DIR BB_WORLD_WARPER BB_WORLD_WARPER_STATE BB_TEXTURE_AUDIT_DIR
    unset BB_PAD_FILE BB_PAD_FILE_ONLY BB_PAD_REPLAY BB_PAD_RECORD BB_PRESET_FILE BB_TOGGLE_FILE
    unset BB_CAPTURE_NATIVE BB_CAPTURE_DIR BB_BENCHMARK_MUTE BB_FRAME_LOG_ONLY
    unset BB_MTL_BUFFER_RANGE_AUDIT BB_MTL_STAGING_AUDIT BB_MTL_COPY_AUDIT BB_MTL_COMPILE_AUDIT
    unset BB_CAPTURE_TRIGGER BB_PRESENT_DUMP_TRIGGER BB_TIMEOUT BB_QUICKLOAD_PENDING
    unset MTL_CAPTURE_ENABLED MTLCAPTURE_DESTINATION MTLCAPTURE_SCOPE
else
    [[ $renderer == metal && ${BB_PAD_FILE_ONLY:-} == 1 && -f ${BB_PAD_FILE:-} ]] || die "Benchmark requires Metal and isolated scripted input"
    [[ ${BB_QUICKSAVE:-0} == 0 ]] || die "Benchmark does not restore live quicksaves"
fi
mkdir -p "$BB_DATA_DIR/out"
if [[ ${BB_ONLINE:-0} == 1 ]]; then
    export BB_THD_SERVER=${BB_THD_SERVER:-https://thehuntersdream.com:18671} BB_THD_AUTH=${BB_THD_AUTH:-https://thehuntersdream.com}
    export BB_THD_HTTP_ORIGIN=${BB_THD_HTTP_ORIGIN:-https://thehuntersdream.com:20443}
    export BB_THD_HTTP_ROUTE=${BB_THD_HTTP_ROUTE:-$BB_THD_HTTP_ORIGIN}
    export SHADPS4_HTTP_HOST_OVERRIDES_JSON
    SHADPS4_HTTP_HOST_OVERRIDES_JSON=$("$PYTHON" scripts/online.py --user "$BB_USER_DIR" --output "$BB_DATA_DIR/out/online-routes")
    export BB_THD_RULESET=${BB_THD_RULESET:-vanilla} BB_CLIENT_VERSION=${BB_CLIENT_VERSION:-BloodEchoes}
    unset BB_SHADNET_SERVER BB_SHADNET_WEBAPI BB_SHADNET_NPID BB_SHADNET_PASSWORD BB_SHADNET_TOKEN
fi
export BB_FRAME_LOG=$BB_DATA_DIR/out/frames.csv
log=$BB_DATA_DIR/out/last-run.log
[[ ! -f $log ]] || cp "$log" "$BB_DATA_DIR/out/previous-run.log"
exec > >(tee "$log") 2>&1
echo "Blood Echoes: renderer=$renderer; quicksave=$BB_QUICKSAVE"
echo "Runtime: $BB_PROBE"
echo "Renderer: ${metal_lib:-$BB_GPU_PROCESS}"
echo "Resolution patch: ${BB_PATCHES:-none (16:9)}"
echo "Game language: ${BB_LANGUAGE:-1}; Chinese source: ${BB_CHINESE_SOURCE:-official} (community request: ${BB_CHINESE_LANGUAGE:-none})"
if [[ $renderer == metal ]]; then
    if [[ ${BB_METAL_ARM64:-0} == 1 ]]; then
        if [[ ${BB_ARM_LAUNCHER_COMPAT:-0} == 1 ]]; then
            echo "Metal: direct Metal API, ARM64 host, FEX game runtime, shared no-copy buffers"
        else
            echo "Metal: direct Metal API, ARM64 host, Rosetta game, shared no-copy buffers"
        fi
    else
        echo "Metal: direct Metal API, Rosetta host, no-copy guest buffers; cache=metal1"
    fi
    echo "Metal combined profile: clear=$BB_MTL_BATCH_CLEARS direct-copy=$BB_MTL_DIRECT_COPY staging=$BB_MTL_ADAPTIVE_STAGING tiling=$BB_MTL_TEXTURE_TILING residency=$BB_MTL_PRECISE_RESIDENCY barriers=$BB_MTL_COALESCE_BARRIERS query=$BB_MTL_QUERY_COMPUTE_CHAIN compute=$BB_MTL_PRECISE_COMPUTE half=$BB_MTL_TEXTURE_TILING_HALF fold-clears=$BB_MTL_FOLD_CLEARS"
else
    echo "Renderer performance port 1: precision=$precision, compact-no-output=${BB_COMPACT_NO_OUTPUT:-1}"
    echo "Renderer memory fixes: command-trim=$BB_CMDBUF_TRIM, driver-pool-fix=$BB_KK_POOL_FIX"
fi
if [[ $(uname -s) == Darwin && $renderer == metal ]]; then
    exec bash tools/macos/supervise_restart.sh "$@"
fi
exec bash run.sh "$@"
