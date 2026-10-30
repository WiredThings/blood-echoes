#!/usr/bin/env bash
# tools/recomp/toolchain.sh OUT_DIR: the compiler the launcher downloads to build the recompiled
# library (build_library.py): LLVM's official macOS arm64 release, checked against the SHA-256 it
# had when first trimmed (pinned below), trimmed to what the build uses (clang, ld64.lld and
# clang's own headers: 342 MB of the 1.5 GB archive) and packed as
# OUT_DIR/llvm-<version>-macos-arm64.tar.xz (about 70 MB), with its SHA-256 beside it. Published as an asset of bloodborne-recomp's toolchain-llvm-<version> release
# (Apache-2.0 with LLVM exception: LICENSE.TXT goes in the archive); the launcher pins that SHA-256.
set -euo pipefail
VERSION=23.1.4
NAME=LLVM-$VERSION-macOS-ARM64
SHA256=5d3179db39adeca5b17a34d82708fe976bc60e055b05fe557878fb3fb64d39ef # $NAME.tar.xz, 1,570,051,292 bytes
out=${1:?usage: tools/recomp/toolchain.sh OUT_DIR}
mkdir -p "$out"
work=$(mktemp -d "$out/toolchain.XXXXXX")
archive=$work/$NAME.tar.xz
# LLVM_ARCHIVE=FILE: an already downloaded copy (checked the same way).
if [[ -n ${LLVM_ARCHIVE:-} ]]; then
    cp -- "$LLVM_ARCHIVE" "$archive"
else
    curl -fL --progress-bar -o "$archive" \
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-$VERSION/$NAME.tar.xz"
fi
echo "$SHA256  $archive" | shasum -a 256 -c -
major=${VERSION%%.*}
tar -xJf "$archive" -C "$work" "$NAME/bin/clang" "$NAME/bin/clang-$major" "$NAME/bin/lld" \
    "$NAME/bin/ld64.lld" "$NAME/lib/clang/$major/include"
# The release archive carries only short notices; the full licence from the same tag.
curl -fsSL -o "$work/$NAME/LICENSE.TXT" \
    "https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-$VERSION/llvm/LICENSE.TXT"
packed=$out/llvm-$VERSION-macos-arm64.tar.xz
tar -cf - -C "$work" "$NAME" | xz -6 -T0 > "$packed"
shasum -a 256 "$packed" | tee "$packed.sha256"
rm -- "$archive"
echo "Unpacked: $(du -sh "$work/$NAME" | cut -f1); delete $work when done."
