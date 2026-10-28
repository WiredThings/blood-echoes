# Blood Echoes for macOS

[简体中文](README.zh-CN.md) · [Download Latest](https://github.com/WiredThings/blood-echoes/releases/latest) · [User guide](docs/USER_GUIDE.md) · [Changes and third-party credits](docs/THIRD_PARTY_CHANGES.md)

The development ARM64 runtime incrementally prepares local Metal libraries and pipelines by default. Ordinary warm starts reuse unchanged completed preparation; F3 retains its original session recovery. This remains separate from the Latest Release described below. See [reuse rules and validation](experiments/arm64-runtime/docs/ordinary-warm-reuse.zh-CN.md).

A standalone macOS launcher and Bloodborne compatibility runtime with **direct Metal rendering**,
**temporal MetalFX**, and English, Simplified Chinese and Traditional Chinese launcher interfaces.
This is the **WiredThings fork** of [DiAvisoo/bbport-mac-optimized](https://github.com/DiAvisoo/bbport-mac-optimized).
The current build is **0.2.51**. It fixes tessellation stage layouts and adds a default-OFF
identical-library reuse experiment. The renderer can run in a separate ARM64 process;
the game itself still uses Rosetta. The earlier 0.2.50 eight-run M4 comparison measured
about 6% more FPS from the native host; this release does not claim another such gain.
Save history and optional iCloud Drive syncing remain available. About shows the source
Git short commit; use the Latest release for the current package.

Version 0.2.36 defaults new configurations to the game's original model detail and warns
about forced low LOD, which can hide walls or floors. If an existing configuration has
missing geometry, select **Model detail → As in the game (0)** and restart.

The launcher is native Apple Silicon. The game runs through **Rosetta 2**; supported
packages run the Metal host natively in a separate ARM64 process. The old renderer
fallback runs through Rosetta as well. The project builds on bbport/shadPS4 and community renderer
work; it is not an official FromSoftware/Sony PC release.

The ARM64 Metal host defaults on in supported macOS 27+ packages; macOS 26 retains
the in-process renderer. Advanced options provide a fallback switch. See the
[ownership, measurements and build notes](docs/release/ARM64-METAL-EXPERIMENT.md).

The `dev/arm64-runtime-audit` branch also contains an [isolated FEX CPU prototype](experiments/arm64-runtime/README.md). This unstable source checkpoint is separate from the production runtime and Release; see its [validation and remaining gaps](experiments/arm64-runtime/NIGHT-HANDOFF.zh-CN.md). It is not yet a standalone clean-checkout build target.

## Install and play

Requires **Apple Silicon, macOS 26+, Rosetta 2**, and your own decrypted Bloodborne game files
with **update 1.09 merged into the base game**. Python and runtime libraries are bundled;
Xcode, Homebrew and compilation are not required to play.

1. Download the **macOS.zip** installation package from [Latest Release](https://github.com/WiredThings/blood-echoes/releases/latest).
2. Extract it and move **Blood Echoes.app** into Applications. Quit the old app before replacing it.
3. Select the merged game folder containing `eboot.bin`, `sce_sys` and `dvdroot_ps4`.
   Select the full installation, rather than the separate `CUSA…-patch` update folder.
4. Choose launcher/game language, output resolution and fullscreen mode, then press **Play** (⌘R).
   Open **Advanced** for MetalFX, effects, frame rate and optimization controls.

The app is ad-hoc signed, not Apple-notarized. If macOS blocks it, use the app-specific
**Open Anyway** option in System Settings → Privacy & Security. See the
[guide](docs/USER_GUIDE.md) for merging updates, controls, save locations and troubleshooting.

## Prepare the game folder

The supported runtime is built for **CUSA03173 with update 1.09**. Unlike an emulator
that mounts the base game and update separately, this launcher needs one **complete merged
folder**. A PKG, an encrypted dump, or the update folder alone is not enough.

1. Prepare your own decrypted base-game and matching 1.09 update dumps. If an extraction
   contains an `app0` wrapper, use the folder *inside* it that contains `eboot.bin`.
2. Copy the complete base game into a **new directory**, then overlay the **contents** of
   the update onto it. Merge subdirectories, overwrite matching files and keep files only
   present in the base. This includes the update's `eboot.bin`, `sce_sys/param.sfo`,
   `sce_module` and `dvdroot_ps4` contents. Do not nest `CUSA03173-patch` inside the base.
3. Select the resulting directory in **Game folder**. Its name is arbitrary; its structure
   should be:

```text
CUSA03173-1.09/          ← select this directory
├── eboot.bin           ← from update 1.09
├── sce_sys/
│   └── param.sfo       ← APP_VER = 01.09
├── sce_module/
│   └── libc.prx
└── dvdroot_ps4/
    ├── menu/
    ├── msg/
    ├── map/
    ├── shader/
    └── …               ← keep all other base/update resources
```

For example, on macOS, adjust these three paths to match your extracted folders, then run:

```sh
GAME_BASE="$HOME/Downloads/shadps4-game/CUSA03173"
GAME_UPDATE="$HOME/Downloads/shadps4-game/CUSA03173-patch"
GAME_MERGED="$HOME/Downloads/shadps4-game/CUSA03173-1.09"

mkdir "$GAME_MERGED" && \
  rsync -aL "$GAME_BASE/" "$GAME_MERGED/" && \
  rsync -aL "$GAME_UPDATE/" "$GAME_MERGED/"
```

Use a **new, nonexistent** destination; the commands stop if it already exists. They leave
the two source folders untouched, overlay without `--delete`, and materialize linked files
(`-L`) into the copy. Allow space for a complete second installation. The trailing `/` means
copy the directory's contents. With Finder, merge subfolders rather than replacing an entire
base resource directory with the smaller update directory.

Select neither the parent holding both dumps nor `dvdroot_ps4` itself. Keep `menu` inside
`dvdroot_ps4`; do not move it to the game root. For ultrawide UI management, `dvdroot_ps4`
and `dvdroot_ps4/menu` must be real directories, not symbolic links. `missing folder or
unsupported link: menu` means that directory is absent or linked, often because only the
update was selected or subfolders were replaced instead of merged.

Seeing **01.09** alone is insufficient: the startup check also verifies the loaded executable
image and tests shader extraction. A 1.09 `param.sfo` alongside a 1.00 `eboot.bin` is invalid;
renaming a folder or editing metadata cannot repair it. This app does not supply game files,
updates or Chinese text/font assets. See the [guide](docs/USER_GUIDE.md#prepare-the-game-folder)
for game-file checks and [Chinese support](#chinese-game-text-and-community-patches).

## What this fork adds

| Area | Work on this branch |
| --- | --- |
| Languages | Three launcher languages; independent game-language selection; Built-in official Chinese via region unlock; optional community-translation import and integrity checks |
| Metal backend | Integrates Huax-1839's Metal engine with the existing launcher, runtime, language, resolution and quicksave paths; Vulkan remains a compatibility option |
| Temporal inputs | Ports IFreemz's game-velocity mirror route to MetalFX; batches eligible velocity draws and preserves velocity generation when motion blur is disabled |
| Memory | Graphics-ledger budgeting without double-counting no-copy guest memory; GPU-complete retirement, idle staging backing reclamation, allocation-generation tracking and invalid sampled-copy reclamation |
| GPU address metadata | Two-level GPU address table with matching shader lookup, replacing the fixed 1 GiB flat table |
| Metal stability | Rejects unusable speculative resource formats and retries on the ordered GPU path; failure-only thread/stack diagnostics |
| Rendering efficiency | Sampler memoization; safe clear/load discard; direct scene-copy clears; alpha merge; compatible clear batching, copy/tiling, residency and synchronization refinements |
| Launcher | Up to 35 Metal optimization controls with dependencies and evidence labels; backend-appropriate options; corrected presentation/limit labels; credited macOS icon formatting |
| Vulkan fallback | Retains earlier imported shader/command-memory fixes and separate caches; Vulkan-specific changes are not presented as Metal optimizations |

The optimized standalone launcher, ultrawide UI management, patch system and quicksave foundation
come from upstream. The Metal engine, original game patches and imported algorithms retain their
own authorship. [The provenance table](docs/THIRD_PARTY_CHANGES.md) separates inherited features,
third-party adaptations and this branch's integration work, with pinned source links.

## Chinese game text and community patches

Select **Game language → 简体中文 / 繁體中文**. A **Chinese text** selector then appears:

- **Official Chinese (region unlock)**: uses the game’s built-in Chinese without any external font pack and
  automatically applies **Unlock Game Region**, credited to **Lance McDonald (manfightdragon)
  and Kyo**. The patch existed upstream; this fork connects it to language selection.
- **Community retranslation**: import the matching **GOTY zh-cn / zh-tw** ZIP or extracted folder
  from [Supuritto's Bloodborne Chinese Retranslation](https://www.nexusmods.com/bloodborne/mods/146?tab=files).
  These GOTY packages replace English text/fonts and work around the western **CUSA03173**
  language restriction. The launcher runs the English US slot while preserving your Chinese
  selection. Chinese variants and imported packages are stored independently. This is a separate
  retranslation from the native Chinese in CUSA01363/CUSA03023; choose according to text preference.

Both modes apply on the next game launch; voice audio is unchanged. Original game files are
preserved through a per-launch overlay. Community translation overrides English text/font mods
in that overlay, even if the general Mods switch is off. Switch to official or another language
to stop applying it. Importing alone does not enable it.

**No translation pack, Chinese game data or fonts are redistributed.** Download the author's
GOTY package separately; a Nexus account may be required. The launcher checks its six English
assets before launch and blocks missing, corrupt or mismatched packages. Official mode uses the game’s built-in Chinese and requires no external font pack.

Other bundled frame-rate, resolution, effect and debug patches keep the authors recorded in
[`patches/Bloodborne.xml`](patches/Bloodborne.xml). Optional ultrawide UI packs and debug fonts
must be obtained separately. Details: [languages](docs/USER_GUIDE.md#launcher-and-game-languages)
and [patch credits](docs/THIRD_PARTY_CHANGES.md#game-language-and-community-patches).

## Settings and performance

- **Resolution** is the output size. **Advanced → Upscaler → Preset** controls MetalFX input size;
  the launcher displays the actual scene-render resolution. MetalFX is temporal, and Native AA is 1:1.
- Supported native-host packages have 36 Metal controls: **22 ON / 14 optional experiments OFF** (35 / 21 ON without the native host). Saved explicit
  choices are retained. Object-motion vectors and experimental occlusion culling have separate controls.
- The original five optional experiments are cross-chunk snapshot reuse, fragment-parameter specialization,
  compute-based small writes, relaxed math and binary pipeline-archive persistence. Their benefits
  remain uncertain, and some have measured memory costs. Six restored alternatives cover native
  clear batching, overlay merging, native fill, residency memoization, identical copy stores and
  in-pass small writes. They remain OFF even where correctness passed; earlier M4 comparisons
  did not establish a gain. These controls ship in 0.2.48. See [control evidence](docs/release/METAL-OPTIMIZATION-CONTROLS.md).
  The depth/stencil layout continuation added in 0.2.49 is experimental OFF: matching attachment
  descriptors alone do not prove feedback/query safety, and the M4 comparison does not establish a gain.
- This fork's local validation focuses on a **base M4 Mac mini, 10-core GPU, 16 GB RAM**.
  Frame rate depends on scene, render size, effects and cache warmth. **60 FPS is not guaranteed.**
  Earlier fixed-scene results and memory tradeoffs are documented in [changes and measurements](docs/THIRD_PARTY_CHANGES.md#measurements-and-limits).

Optimizations preserve the selected resolution/effects and valid cache capacities. Some features
have their own cost, including velocity textures and optional pipeline archives; memory budgeting
is not a hard process-RAM limit. First visits can still stutter during asset loading or shader/pipeline
compilation. The discarded offline-library experiment is not part of this release.

## Experimental online play

Version 0.2.51 includes an opt-in **Online** section for The Hunter’s Dream, using
an optional bbnet module on the Metal backend. Website/device sign-in, the in-game server
announcement and chalice **search → join** were exercised with an isolated client. Message
write/list/delete was exercised in game; a second client has not verified delivery. Direct
glyph lookup, co-op and invasions are not yet validated. Online defaults OFF. Online and offline share game saves; old checkpoints are archived and replaced with a fresh baseline before online play. F2 saves in either mode; F3 always restarts offline and stays offline until explicitly re-enabled. The launcher includes a save manager, click-to-copy device codes. See [setup, backups and test boundaries](docs/ONLINE.md).

## Controls, saves and updates

**F1** settings · **F2** quicksave · **F3** quickload; click the launcher's shortcut labels to remap.
Quicksave is optional and restores a disk-save checkpoint with a game restart, rather than a
full memory save state. [Default keyboard mappings and save paths](docs/USER_GUIDE.md#controls-and-quicksave).

Saves and settings live outside the app, so replacing the complete app preserves them.
The default save root is `~/Library/Application Support/bbport/user`; existing bbport installations
share it. Choose a copied save folder for isolated trials.

Save Manager separates live normal saves and active checkpoints from backup history, with folder, backup and verified-export actions. See [Save Manager](docs/USER_GUIDE.md#save-manager).

## Build from source

Use an **Apple Silicon Mac running macOS 26+**, Xcode 27+ (or matching Command Line Tools
with Swift and a macOS 27+ SDK), Apple Silicon [Homebrew](https://brew.sh), Rosetta 2, Git
and internet access. The build fetches pinned public dependency sources and uses Homebrew
for native build tools, header packages and Python 3.11. It needs no game files, game mods,
previous release or installed Bloodborne app. Dependencies are not all vendored in the root
repository; this is an online source build, not an offline or byte-identical build guarantee.

```sh
xcode-select --install   # if Xcode / Command Line Tools are not already installed
softwareupdate --install-rosetta --agree-to-license

git clone --recurse-submodules --branch optimized https://github.com/WiredThings/blood-echoes.git
cd blood-echoes
export SDKROOT="$(xcrun --sdk macosx --show-sdk-path)"
BB_BUILD_JOBS=2 bash tools/macos/build_from_source.sh 0.2.51-source1
```

The script initializes submodules, builds x86-64 and ARM64 Metal dependencies plus the arm64 Vulkan driver,
installs pinned SPIRV-Cross/xbyak headers, builds the two Vulkan host architectures,
Metal renderer, enhanced runtime and optional network module, tests the launcher, and packages a **complete app**
with both backends, Python and relocated libraries. It applies the ordered Metal adapter patch series
in order. Compilation is sequential across components, with two jobs inside each build;
use `BB_BUILD_JOBS=1` on memory-constrained systems. Quit the game before building.
A first build downloads and compiles substantial dependencies; reserve tens of GiB of disk
space. It never installs into `/Applications`, launches the game or edits saves/settings.

Outputs:

- `out/source-release-0.2.51-source1/Blood Echoes.app`
- `dist/Blood-Echoes-0.2.51-source1.zip` (complete Metal + Vulkan app)
- `dist/blood-echoes-0.2.51-source1-source.tar.gz` (corresponding source/dependencies)
- `dist/source-build-verification.json` (final packaging/signature/link checks)
- `dist/source-build-SHA256SUMS.txt` (final app ZIP and source archive hashes)

Build from a clean clone: the packager refuses uncommitted project files. Use a new version
suffix for another final staging directory. With dependencies already prepared by this
workflow, use `BB_SKIP_DEPS=1 BB_BUILD_JOBS=2 bash tools/macos/build_from_source.sh 0.2.51-source2`.
Keep the `.git` directories: GitHub's automatic source ZIP omits submodules, and the Release
inspection source archive has no Git metadata required by the revision/patch guards.
The `optimized` branch includes this build entry point and receives subsequent fixes.

For an SDK/linker mismatch, set `SDKROOT` to an installed SDK supported by your active compiler;
do not mix an old linker with a newer SDK. See [the detailed build guide](docs/release/BUILD-OPTIMIZED.md)
for individual commands, verification limits and troubleshooting.

## Source, credits and distribution

- [Source retrieval, pinned dependencies and building](docs/release/SOURCE.md)
- [Full branch changes and third-party attribution](docs/THIRD_PARTY_CHANGES.md)
- [0.2.51 release notes](docs/release/0.2.51.md) · [Report a problem](https://github.com/WiredThings/blood-echoes/issues)

Built on **DiAvisoo's optimized launcher, bmy/bbport-mac, deadinside28's bbport, shadPS4,
Huax-1839, IFreemz, Awakenchan, Wooloomooloo2/Mark Hall**, and the component contributors.
See the provenance table, [LICENSE](LICENSE), source-file notices and bundled third-party licenses.
The Hunter's Mark launcher artwork is credited [separately](launcher-macos/Sources/BBLauncher/Resources/Artwork/ATTRIBUTION.txt).

Public assets contain **no game executable/data, saves, shader caches, downloaded mods, World Warper,
QA apps, private route recordings or local sampling artifacts**. The credited Hunter's Mark and normalized game-cover application icons are intentional artwork
assets. The game uses the same cover before window initialization and during restart, and still
reads the player's game-folder cover for its runtime icon. See the [game-icon attribution](tools/macos/resources/ATTRIBUTION.txt).
Historical upstream notes are preserved in [UPSTREAM-README](docs/UPSTREAM-README.md) and Git history.

Blood Echoes was previously named Bloodborne Optimized. The app and repository names
changed; bundle identifiers, preferences and existing save/data paths are retained.
No migration is required.

Save history uses 3 previous checkpoints, 3 pre-F3 copies and 5 automatic archives, with manual/pinned protection. Optional iCloud Drive sync transfers only latest normal saves and requires a choice for concurrent progress. See [Save Manager](docs/USER_GUIDE.md#save-manager) and [sync format](docs/SAVE_SYNC.md).
