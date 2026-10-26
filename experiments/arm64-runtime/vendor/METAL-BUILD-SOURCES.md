# Metal test source closure

`metal-build-source-closure.json` is the exact file allowlist, source revision,
and original/final SHA-256 inventory for the neighboring `tests/` and
`macos/build-deps.sh` inputs used by `metal_api/CMakeLists.txt`.

These sources come from the initialized `engines/metal` checkout at
`11992198df6306dacf6ec37de57ee920db1acc11`, including the project's already
applied Metal patches. The original GPL license is retained as
`LICENSE.bloodborne-metal`; individual SPDX declarations are unchanged.
Seven quoted include paths in six test files change `gpu/` to `metal_api/`
to match this source layout. Test behavior and CMake test targets are unchanged.

The 65 test/fixture source files cover the 61 explicit CMake sources and their
local dependencies. The tessellation fixture is synthetic GLSL-generated C++
test source, with its generator retained; it contains no game shader dump.
No game assets, standalone SPIR-V binaries, or built executables are included.

`macos/build-deps.sh` is retained byte-for-byte because CMake reads its pinned
SPIRV-Cross version. It is a legacy x86 build script, **not** the ARM setup entry.
Use the repository's `tools/macos/setup_deps.sh` with `BB_ARCH=arm64` and
`tools/macos/setup_metal_deps.sh` for native dependencies. The former requires
Rosetta only for its x86_64 branch.

This closes the neighboring source-layout gap. A successful configure using
existing dependencies does not prove a fresh dependency build, a full ARM64 CPU
build, or a portable application package; see
`../docs/arm64-clean-build-remaining.zh-CN.md`.
