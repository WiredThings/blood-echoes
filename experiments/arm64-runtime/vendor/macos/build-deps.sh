#!/usr/bin/env bash
# macOS (Apple Silicon): builds the x86-64 libraries bbport links into macos/deps/x86_64.
# The game's code is x86-64 and runs under Rosetta 2, so the whole process is x86-64: every
# library in it too. Host build tools (cmake, ninja, pkgconf, glslang) come from Homebrew (arm64).
set -euo pipefail
cd -- "$(dirname -- "$0")"
root=$PWD/deps
src=$root/src
prefix=$root/x86_64
mkdir -p "$src" "$prefix"
jobs=$(sysctl -n hw.ncpu)
export MACOSX_DEPLOYMENT_TARGET=14.0
cmake_common=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=x86_64
              -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_PREFIX_PATH="$prefix" -DBUILD_SHARED_LIBS=OFF
              -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET)

fetch() { # fetch name repository tag
    [[ -d $src/$1 ]] || git clone --quiet --depth 1 --branch "$3" "$2" "$src/$1"
}
cmake_build() { # cmake_build name [cmake options...]
    local name=$1; shift
    [[ -f $root/.done-$name ]] && return
    echo "== $name"
    cmake -S "$src/$name" -B "$src/$name/build-x86_64" "${cmake_common[@]}" "$@" >/dev/null
    ninja -C "$src/$name/build-x86_64" install >/dev/null
    touch "$root/.done-$name"
}

fetch vulkan-headers https://github.com/KhronosGroup/Vulkan-Headers.git v1.4.365
cmake_build vulkan-headers
fetch fmt https://github.com/fmtlib/fmt.git 12.1.0
cmake_build fmt -DFMT_TEST=OFF -DFMT_DOC=OFF
fetch magic_enum https://github.com/Neargye/magic_enum.git v0.9.7
cmake_build magic_enum -DMAGIC_ENUM_OPT_BUILD_EXAMPLES=OFF -DMAGIC_ENUM_OPT_BUILD_TESTS=OFF
fetch robin-map https://github.com/Tessil/robin-map.git v1.4.0
cmake_build robin-map
fetch vma https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator.git v3.3.0
cmake_build vma
fetch xxhash https://github.com/Cyan4973/xxHash.git v0.8.3
if [[ ! -f $root/.done-xxhash ]]; then
    echo "== xxhash"
    cmake -S "$src/xxhash/cmake_unofficial" -B "$src/xxhash/build-x86_64" "${cmake_common[@]}" \
        -DXXHASH_BUILD_XXHSUM=OFF >/dev/null
    ninja -C "$src/xxhash/build-x86_64" install >/dev/null
    touch "$root/.done-xxhash"
fi
fetch miniz https://github.com/richgel999/miniz.git 3.1.0
cmake_build miniz -DBUILD_EXAMPLES=OFF -DBUILD_TESTS=OFF -DBUILD_FUZZERS=OFF
fetch zycore https://github.com/zyantific/zycore-c.git v1.5.2
cmake_build zycore -DZYCORE_BUILD_TESTS=OFF -DZYCORE_BUILD_EXAMPLES=OFF
fetch zydis https://github.com/zyantific/zydis.git v4.1.1
cmake_build zydis -DCMAKE_C_FLAGS="-I$prefix/include" -DZYAN_SYSTEM_ZYCORE=ON -DZYDIS_BUILD_TOOLS=OFF -DZYDIS_BUILD_EXAMPLES=OFF -DZYDIS_BUILD_DOXYGEN=OFF
fetch xbyak https://github.com/herumi/xbyak.git v7.43
[[ -d $prefix/include/xbyak ]] || cp -R "$src/xbyak/xbyak" "$prefix/include/"
# Metal backend shaders: SPIR-V -> MSL (plan D5).
fetch spirv-cross https://github.com/KhronosGroup/SPIRV-Cross.git vulkan-sdk-1.4.363.0
cmake_build spirv-cross -DSPIRV_CROSS_SHARED=OFF -DSPIRV_CROSS_STATIC=ON -DSPIRV_CROSS_CLI=OFF \
    -DSPIRV_CROSS_ENABLE_TESTS=OFF -DSPIRV_CROSS_ENABLE_HLSL=OFF -DSPIRV_CROSS_ENABLE_CPP=OFF \
    -DSPIRV_CROSS_ENABLE_REFLECT=ON -DSPIRV_CROSS_ENABLE_C_API=OFF -DSPIRV_CROSS_ENABLE_UTIL=ON
fetch sdl3 https://github.com/libsdl-org/SDL.git release-3.4.18
cmake_build sdl3 -DSDL_SHARED=ON -DSDL_STATIC=OFF -DBUILD_SHARED_LIBS=ON -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF \
    -DCMAKE_INSTALL_NAME_DIR=@rpath

# FFmpeg (the game's movies, through shadPS4's AvPlayer): only what PS4 videos need.
fetch ffmpeg https://github.com/FFmpeg/FFmpeg.git n8.0.3
if [[ ! -f $root/.done-ffmpeg ]]; then
    echo "== ffmpeg"
    mkdir -p "$src/ffmpeg/build-x86_64"
    (cd "$src/ffmpeg/build-x86_64" && ../configure --prefix="$prefix" --arch=x86_64 --enable-cross-compile \
        --target-os=darwin --cc="clang -arch x86_64" --cxx="clang++ -arch x86_64" \
        --extra-cflags="-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET" \
        --extra-ldflags="-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET" \
        --disable-programs --disable-doc --disable-network --disable-autodetect --disable-x86asm \
        --enable-static --disable-shared --enable-pic --enable-videotoolbox --enable-audiotoolbox \
        >/dev/null && make -j"$jobs" >/dev/null && make install >/dev/null)
    touch "$root/.done-ffmpeg"
fi

# Boost: header-only parts, Homebrew's headers (architecture independent) behind a minimal config.
if [[ ! -f $prefix/lib/cmake/Boost/BoostConfig.cmake ]]; then
    boost_include=$(brew --prefix boost)/include/boost
    [[ -d $boost_include ]] || { echo 'brew install boost (its headers are used)' >&2; exit 1; }
    mkdir -p "$prefix/boost-include" "$prefix/lib/cmake/Boost"
    ln -sfn "$boost_include" "$prefix/boost-include/boost"
    cat > "$prefix/lib/cmake/Boost/BoostConfig.cmake" <<'CONFIG'
# Header-only Boost for the x86-64 build: Homebrew's headers (architecture independent).
get_filename_component(_bb_boost_root "${CMAKE_CURRENT_LIST_DIR}/../../../boost-include" ABSOLUTE)
if (NOT TARGET Boost::headers)
    add_library(Boost::headers INTERFACE IMPORTED)
    set_target_properties(Boost::headers PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "${_bb_boost_root}")
endif()
set(Boost_FOUND TRUE)
set(Boost_VERSION 1.92.0)
CONFIG
fi

# MoltenVK (Vulkan on Metal), the universal release build with Metal's private API features.
if [[ ! -f $prefix/lib/libMoltenVK.dylib ]]; then
    echo "== MoltenVK"
    curl -sSL -o "$src/MoltenVK-macos-privateapi.tar" \
        https://github.com/KhronosGroup/MoltenVK/releases/download/v1.4.2/MoltenVK-macos-privateapi.tar
    tar -xf "$src/MoltenVK-macos-privateapi.tar" -C "$src"
    dylib=$(find "$src/MoltenVK" -path '*dynamic*' -name libMoltenVK.dylib | head -1)
    lipo "$dylib" -thin x86_64 -output "$prefix/lib/libMoltenVK.dylib"
    install_name_tool -id @rpath/libMoltenVK.dylib "$prefix/lib/libMoltenVK.dylib" 2>/dev/null
    codesign -f -s - "$prefix/lib/libMoltenVK.dylib"
fi
# The renderer loads "libvulkan.1.dylib" (Vulkan-Hpp's DynamicLoader): MoltenVK itself, no loader.
ln -sf libMoltenVK.dylib "$prefix/lib/libvulkan.1.dylib"
ln -sf libMoltenVK.dylib "$prefix/lib/libvulkan.dylib"
echo "x86-64 dependencies in $prefix"
