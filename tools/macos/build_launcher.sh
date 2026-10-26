#!/usr/bin/env bash
# tools/macos/build_launcher.sh: builds the native launcher (launcher-macos/, SwiftUI) into
# out/Blood Echoes.app. The app is native arm64; it starts run_optimized.sh with the
# tested native renderer and x86-64 game under Rosetta 2. Needs Xcode or Command Line Tools.
#   bash tools/macos/build_launcher.sh
#   open "out/Blood Echoes.app"
# The repository path is embedded in the app (Info.plist BBRepoPath; Settings can override it).
# The Hunter's Mark AppIcon.icns is bundled for Finder, Dock and running instances.
# Release builds (tools/macos/package.sh): BB_RELEASE=<version> embeds no repository path and no
# game artwork; BB_APP sets where the app goes.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
repo=$PWD
pkg=launcher-macos
app=${BB_APP:-out/Blood Echoes.app}
work=${BB_LAUNCHER_WORK:-out/launcher-build}
release=${BB_RELEASE:-}

[[ $(uname -s) == Darwin ]] || { echo "STOP: run this on the Mac" >&2; exit 1; }
command -v swift >/dev/null || { echo "STOP: swift missing: xcode-select --install" >&2; exit 1; }

identity_args=("$repo")
[[ -z $release ]] || identity_args+=(--required)
source_commit=$(python3 tools/macos/build_identity.py "${identity_args[@]}")

echo "=== swift build (release, $(uname -m))"
swift --version 2>&1 | head -1
# Plain `swift build` output goes to the terminal: send all of it back if this step fails.
build_args=(--package-path "$pkg" -c release)
[[ -z ${BB_SWIFT_SCRATCH_PATH:-} ]] || build_args+=(--scratch-path "$BB_SWIFT_SCRATCH_PATH")
[[ -z ${BB_SWIFT_BUILD_SYSTEM:-} ]] || build_args+=(--build-system "$BB_SWIFT_BUILD_SYSTEM")
[[ -z ${BB_SWIFT_JOBS:-} ]] || build_args+=(--jobs "$BB_SWIFT_JOBS")
if ! swift build "${build_args[@]}"; then
    echo "STOP: swift build failed. Send the whole output above." >&2
    exit 1
fi
bin=$(swift build "${build_args[@]}" --show-bin-path)/BBLauncher
[[ -x $bin ]] || { echo "STOP: $bin missing after the build" >&2; exit 1; }

echo "=== $app"
rm -rf "$app" "$work"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$work"
cp "$bin" "$app/Contents/MacOS/bbport"
# SwiftPM keeps localized resources in a separate bundle. Bundle.module resolves it from
# the app's Resources directory, including after the app leaves the build checkout.
resources=$(dirname "$bin")/BBLauncher_BBLauncher.bundle
[[ -d $resources ]] || { echo "STOP: launcher localization resources missing" >&2; exit 1; }
cp -R "$resources" "$app/Contents/Resources/"
# macOS wants numbers here: the upstream part (0.4 of 0.4-v1); the app shows the full version.
short_version=${release%%-*}
short_version=${short_version:-0.1}
version=${short_version//./}
# KosmicKrisp needs macOS 26; a checkout build keeps the old minimum so its own checks explain.
min_system=14.0
[[ -n $release ]] && min_system=26.0
cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleLocalizations</key>
    <array><string>en</string><string>zh-Hans</string><string>zh-Hant</string></array>
    <key>CFBundleDisplayName</key>
    <string>Blood Echoes</string>
    <key>CFBundleExecutable</key>
    <string>bbport</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.bbport.optimized</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Blood Echoes</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$short_version</string>
    <key>CFBundleVersion</key>
    <string>$version</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.games</string>
    <key>LSMinimumSystemVersion</key>
    <string>$min_system</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
EOF
# The full Git revision is separate from Apple's numeric build-version field.
[[ -z $source_commit ]] || plutil -insert BBSourceCommit -string "$source_commit" "$app/Contents/Info.plist"
# plutil escapes the path for XML.
[[ -n $release ]] || plutil -insert BBRepoPath -string "$repo" "$app/Contents/Info.plist"

icon="$pkg/Sources/BBLauncher/Resources/AppIcon.icns"
[[ -f $icon ]] || { echo "STOP: bundled AppIcon.icns missing" >&2; exit 1; }
cp "$icon" "$app/Contents/Resources/AppIcon.icns"
plutil -insert CFBundleIconFile -string AppIcon "$app/Contents/Info.plist"
plutil -lint "$app/Contents/Info.plist" >/dev/null

# Ad-hoc signature: Apple Silicon refuses unsigned arm64 code. Release builds are signed again
# by tools/macos/package.sh once the game engine is inside.
codesign --force --sign - --deep "$app"
[[ -z $release ]] || exit 0
echo
echo "Built $app ($(lipo -archs "$app/Contents/MacOS/bbport"))."
echo "Open it with:  open \"$app\""
echo "or drag the app to /Applications (it keeps using this checkout: $repo)."
