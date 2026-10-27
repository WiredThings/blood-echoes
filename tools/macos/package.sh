#!/usr/bin/env bash
# Native ARM64 packaging from a successful normal source build receipt.
# BB_PACKAGE_PYTHON=/path/to/python3.11 bash tools/macos/package.sh \
#     --build-manifest /path/build/build-manifest.json \
#     --output "/fresh/staging/Blood Echoes.app"
# Local staging only: no install, push, tag, upload or publication.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
[[ $(uname -s) == Darwin ]] || { echo 'Build this package on macOS.' >&2; exit 1; }
exec "${BB_PACKAGE_PYTHON:-python3}" -B experiments/arm64-runtime/tools/package_native_from_source.py "$@"
