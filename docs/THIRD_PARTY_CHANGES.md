# Branch changes and third-party provenance

[English overview](../README.md) · [中文概览](../README.zh-CN.md) · [使用指南](USER_GUIDE.zh-CN.md)

Scope: the public **WiredThings Blood Echoes 0.2.49** release, compared with the optimized upstream
snapshot [`DiAvisoo/bbport-mac-optimized` at `8289f83`](https://github.com/DiAvisoo/bbport-mac-optimized/tree/8289f83).
This is a provenance/evidence guide, not a claim that this fork originated the game runtime,
Metal engine, community patches or every optimization it integrates.

Development branch, 2026-10-30 (not a published Release): ports shadPS4 #5570 (LRU early stop),
#5524 (per-dimension texture extents), the reviewed open #5577 (cache IO counter), FEX #6074
(observable guest loads), and DiAvisoo 8e678b6f (optional UI validation). Huax's 77a4bf4d
invariant vertex optimization is integrated as an explicit default-off experiment, including
local library compilation and versioned profile compatibility. Game/performance acceptance
remains separate from source/unit review. Sources, review fixes and current verification
limits: [development integration record](../experiments/arm64-runtime/docs/upstream-refresh-20261030.zh-CN.md).

0.2.49 adds local depth/stencil-only pass diagnostics and guarded read-only-to-write continuation (0082–0083), default OFF. These are local adaptations, not an upstream import. Pixel/query validation passes; no M4 FPS benefit is established. See [evidence](release/METAL-DEPTH-LAYOUT.md).

0.2.48 adds local speculative vertex-format safety, descriptor-free Metal vertex metadata, removal of unused static-backend vertex preparation, consumed-clear pass continuation and query-write cache invalidation. Six restored experiments and heap-release history priority remain opt-in. See [validation and limits](release/0.2.48.md). No game assets, test hooks or World Warper are included.

Versioned technical notes preserve their original test/experimental state. For current defaults,
use the current user guide and optimization-control reference; an older note saying a path was
unverified is not a new claim about every later revision.

0.2.30 added Chinese-only text-source selection and a private local importer for the community
GOTY retranslation. 0.2.31 removes the incorrect official-Chinese asset warning and isolates QA paths. The production runtime, renderer and graphics patches are unchanged;
0.2.29 previously updated Help/About links.
0.2.32 fixes macOS quickload restarts: a supervisor waits for process exit before restarting,
forwards quickload once, and launches the game in a stable nested application bundle.
The rendering library and translation assets are unchanged.
0.2.33 replaces the nested game bundle's Hunter's Mark placeholder with a normalized game
cover, avoiding icon changes during F3 restart. The launcher retains Hunter's Mark.

0.2.41 adds speculative resource-format rejection and failure-only thread/stack diagnostics. The upstream mechanism is corroborated by [PR #166](https://github.com/deadinside28/bloodborne_pc/pull/166), incorporated by its maintainer in 0.6. This branch uses explicit speculative rejection with strict ordered fallback on the pinned Metal engine; the original live caller remains unverified. See [validation scope](release/METAL-SPECULATIVE-RESOURCE-VALIDATION.md).

0.2.42 fixes two cross-dimensional texture-cache lookup paths. Identical layouts replace the backing through the existing GPU copy/rebind/deferred-destruction lifecycle; incompatible layouts are rejected as reuse candidates. Synthetic Metal tests preserve GPU-only pixels through 32 replacements, and two isolated enemy-death regressions include a live incompatible-candidate rejection. No cache budget or archive format changes; see [validation and limits](release/METAL-DIMENSIONAL-TEXTURE-REUSE.md).


0.2.43 ports the 1D-image lowering from [shadPS4 #5133](https://github.com/shadps4-emu/shadPS4/pull/5133), specifically [`ef144307`](https://github.com/shadps4-emu/shadPS4/commit/ef144307c5a265b19248a87735090c4c3cdc7102). Logical 1D/1D-array resources use 2D/2D-array backing with matching shader coordinates, gradients, offsets, dimension queries and atomics. Metal sampling uses the sole row's center to preserve linear border filtering. Adapter 0038 remains unchanged; adapter 0039's layout, memo and pre-backend validation remains, with identical-layout dimensional aliases sharing backing instead of copying/replacing it. Old core shader caches are preserved in their namespace. See [tests, cache behavior and measured limits](release/METAL-1D-IMAGE-LOWERING.md).

## Inherited foundations and imported work

| Feature / area | Source | This branch's integration and boundary |
| --- | --- | --- |
| Standalone SwiftUI launcher, ultrawide management, patch compiler, quicksave foundation | [DiAvisoo optimized upstream](https://github.com/DiAvisoo/bbport-mac-optimized/tree/8289f83) | Extends/localizes the launcher and preserves its game preparation, saves and patch interfaces |
| PS4 compatibility runtime and shader/game support | [deadinside28/bloodborne_pc](https://github.com/deadinside28/bloodborne_pc), [bmy/bbport-mac](https://github.com/bmy/bbport-mac), [shadPS4](https://github.com/shadps4-emu/shadPS4) | The game still runs as x86-64 code through Rosetta; this fork is not an ARM recompilation of Bloodborne |
| Direct Metal backend | [Huax-1839/bloodborne-macos `11992198`](https://github.com/Huax-1839/bloodborne-macos/tree/11992198df6306dacf6ec37de57ee920db1acc11) | Pinned submodule plus [ordered adapter patch series](../gpu/patches/metal); launcher/runtime ABI, inputs, lifetime and efficiency work are layered on this engine |
| Separate ARM64 Metal host (0.2.50) | Existing [Vulkan process transport](../gpu/shim/remote) and portable SRT interpreter in this repository | Reuses shared mappings and command channels, adding authoritative cross-process lifetime leases and a Metal chunk-import bridge. The game remains Rosetta x86-64; no game assets or extra language mod is added. [Validation boundary](release/ARM64-METAL-EXPERIMENT.md) |
| Process/graphics-ledger budgeting and completed-resource retirement | bmy [`4de5fd1`](https://github.com/bmy/bbport-mac/commit/4de5fd1340c8184dd88687fc07631ee2425cb587), [`a923b61`](https://github.com/bmy/bbport-mac/commit/a923b61ecce51d9d5bfe1deb855d82ed2683eb0c) | Adapts accounting to direct Metal, subtracting imported no-copy aliases to avoid double counting; [details](release/METAL-MEMORY-1.md) |
| Sampler memoization | [Awakenchan/BloodBorneButPc `4f93643e`](https://github.com/Awakenchan/BloodBorneButPc/blob/4f93643eb573ae445a24db1d64eabf663a5d5147/engine/bbhost-changes.patch) | Ports complete-descriptor/LOD/depth keys and GC-generation invalidation to Metal references; [details](release/METAL-PERFORMANCE-1.md) |
| Game-velocity mirror and temporal inputs | [IFreemz/shadPS4-Bloodborne-DLSS-FSR `149f6364`](https://github.com/IFreemz/shadPS4-Bloodborne-DLSS-FSR/tree/149f63645bbb40fa587daa68fbf003b1f6dbcd7d) | Adapts velocity capture/decode to MetalFX; adds Metal lifetime handling, batching and motion-blur bypass compatibility; [route](release/METAL-GAME-MOTION-1.md), [follow-up](release/METAL-GAME-MOTION-2.md) |
| Earlier Vulkan shader/submit improvements | [Wooloomooloo2 / Mark Hall](https://github.com/Wooloomooloo2/bloodborne_mac_port), pins [`5137a3e`](https://github.com/Wooloomooloo2/bloodborne_mac_port/commit/5137a3ec646e0320205f868ea86972b2f45f185d), [`fe06953`](https://github.com/Wooloomooloo2/bloodborne_mac_port/commit/fe06953cebeba0cf116f7d73842623a902db5abe), [`29bd6c8`](https://github.com/Wooloomooloo2/bloodborne_mac_port/commit/29bd6c822a24c4c0cd55ffce0dbf3f8d58d317ea) | Retains compact no-output render areas, selective SPIR-V precision and permutation fixes on the Vulkan fallback; [scope](release/PERFORMANCE-PORT-1.md). These are not changes to MetalFX |
| Vulkan command-memory trimming / KosmicKrisp fixes | bmy/bbport-mac `d6704c6`, `4de5fd1`, PR #8 `827e27c`; pinned KosmicKrisp/Mesa | Compatibility-path import with explicit driver revisions and in-flight checks; [provenance](release/MEMORY-FIX-1.md). Not a change to the default Metal driver |

Huax's existing no-copy memory path, texture/staging lifetime rules and blocked-LRU handling were
reviewed and retained where already present. Metalborne's blocked-prefix collector fix was
already reflected in that pinned backend; its regression coverage informed checks, rather than
being claimed as a newly transplanted implementation. See the existing-fixes section of
[Metal memory notes](release/METAL-MEMORY-1.md).

## Runtime and framework dependencies

Version 0.2.37 separately ports Supermedo's bbnet / shadPS4 / bbhost THD
integration at `de2e38af055f09f819946951075c6bb119e62ae6`. The optional module retains its
GPL-3.0 bbhost notice and OpenSSL, Protobuf, MiniUPnP, nlohmann/json and cpp-httplib notices.
The renderer is unchanged. [Online implementation, source attribution and validation limits](ONLINE.md).

The underlying projects also rely on SDL3 (window/input/audio), SPIRV-Cross and the shader-toolchain
components (translation), FFmpeg/LibAtrac9 (media/audio), Dear ImGui (overlay), and utility libraries
such as fmt, xxHash, miniz, Zydis/Zycore and Boost. Apple **Metal/MetalFX** are system frameworks;
the Vulkan fallback uses **KosmicKrisp/Mesa**, the Vulkan loader and **FireBurn/FSR-Vulkan / AMD
FidelityFX**. This release does not offer a Metal DLSS or FSR 4 path.

Dependency revisions and notices are recorded in the Release source archive's
`SOURCE-MANIFEST.json`, the app's `THIRD-PARTY-NOTICES.txt` / license directories, the
[submodule pins](../.gitmodules), and [source/build instructions](release/SOURCE.md).
The dependency list describes components, not authorship of the branch's adaptations.

## Work added or substantially adapted on this branch

The implementation is published as source and ordered patches, with historical validation notes:

- **Launcher localization and language integration:** English/Simplified/Traditional resources,
  independent language choices, missing-asset checks and Chinese region-patch selection.
- **Two-level address metadata:** coupled CPU updater and shader lookup, sparse leaves, distinct
  cache ABI and lifetime/residency tracking. See [two-level table](release/METAL-BDA-PAGE-TABLE.md).
- **Game allocation/resource association:** guest allocation/free and address-generation tracking,
  association with Metal resources, completed-use retirement and idle staging backing reclamation.
  See [memory categories](release/METAL-MEMORY-2.md), [allocation tracking](release/METAL-MEMORY-3.md)
  and [0.2.28 invalid-copy scope](release/0.2.28.md).
- **Native Metal occlusion:** conservative visibility history and separation of host utility/mirror
  passes from guest query results. Experimental and switchable; see [occlusion](release/METAL-OCCLUSION-1.md).
- **Metal render/transfer efficiency:** safe attachment-load discard, direct scene-proxy clearing,
  alpha merge, strict vertex-math inlining, compatible clear batching/folding, direct copy/tiling,
  precise resource declarations and barrier/query-compute handling. Individual and combined
  evidence differ; see [the current controls](release/METAL-OPTIMIZATION-CONTROLS.md).
- **Shipping controls and packaging:** 25 managed switches, preserved preferences, dependency gates,
  stale diagnostic-override cleanup, backend-specific UI, multilingual wording, production-only
  package filtering and corresponding-source publication.

Original authors retain credit for the imported routes. Adaptation here includes different Metal
APIs, scheduling, host/guest draw isolation, validation and integration; it should not be confused
with directly transplanting every optimization from the referenced projects.

## Game language and community patches

The bundled [Bloodborne patch XML](../patches/Bloodborne.xml) is an author-attributed community
patch database. **Its inclusion is not evidence that every entry is enabled or verified.**

| Patch / asset | Credit / source | Public-release behavior |
| --- | --- | --- |
| **Unlock Game Region** | **Lance McDonald (manfightdragon), Kyo**, `Name="Unlock Game Region"` in the XML | Already present in the optimized upstream database. This fork automatically selects it for Chinese PS4 language IDs 10/11 in official-text mode; it affects the launch image and does not supply or translate text/fonts |
| **Bloodborne Chinese Retranslation (GOTY zh-cn / zh-tw)** | **Supuritto**, [Nexus #146](https://www.nexusmods.com/bloodborne/mods/146); GOTY English-slot solution credits **connlost** | New in 0.2.30: Chinese-only source selector, ZIP/folder import, per-script private copies and hash validation. Runs English US with a temporary text/font overlay; skips region unlock. Author forbids redistribution, so no text/font payload is bundled. Original game and saved patch selections are preserved |
| Uncap FPS++, 60/90 FPS++ and related timestep modes | Lance McDonald (manfightdragon), Kyo; per-entry XML attribution | The launcher selects a mode; frame limits and actual performance remain separate |
| Resolution patches, effect/model-detail patches | Kyo and the other authors named on each XML entry | Existing patch data is integrated with output/render size and Metal velocity compatibility, not reattributed to this fork |
| Restore Debug Camera | Lance McDonald (manfightdragon) | Optional; default game combination Cross + L3 / keyboard Space + Z |
| Restore Debug Menu | Whitehawkx, auser1337; separate [Nexus #253 font package](https://www.nexusmods.com/bloodborne/mods/253) | Optional; validates required font resources. No downloaded font/mod files are bundled |
| Ultrawide UI pack | User-selected [Nexus #202 download](https://www.nexusmods.com/bloodborne/mods/202), retaining the chosen pack's author/terms | Optional install/backup/restore manager; no `.gfx` pack is included |
| Launcher Hunter's Mark artwork | Bloodborne artwork, hosted by [Bloodborne Wiki](https://www.bloodborne-wiki.com/2015/03/hunters-mark.html); [attribution](../launcher-macos/Sources/BBLauncher/Resources/Artwork/ATTRIBUTION.txt) | Credited icon asset with macOS margins/corners. The game additionally bundles a normalized cover as its default icon to match the runtime cover; [game-cover attribution](../tools/macos/resources/ATTRIBUTION.txt). Neither original game files nor saves are included |

**中文支持的归属：**本分支提供启动器汉化和游戏中文选项联动；区域解锁原补丁由 Lance McDonald、Kyo
编写且上游已收录。玩家自己的游戏仍需带有中文文本和字体，配音不变；发行包没有额外的汉化文本包。
原 XML 说明该区域补丁不切换 ×／○，不能据此保证每种区域版本都已实机验证。

The language feature's branch implementation is visible in
[`scripts/patches.py`](../scripts/patches.py),
[`Checks.swift`](../launcher-macos/Sources/BBLauncher/Checks.swift) and the
[region-integration commit](https://github.com/WiredThings/blood-echoes/commit/5ddd80a).
It is targeted at the 01.09 patch format; CUSA03173 was the investigated region.

## Measurements and limits

The main local test machine was a **base M4 Mac mini, 10 GPU cores, 16 GB RAM**. Historical
results below belong to different versions/cohorts, not a single current whole-game benchmark.

| Historical check | Observation | Boundary |
| --- | --- | --- |
| Two-level versus flat table, same-scene check | Approximately 0.88 GiB lower process footprint; table about 3.25 MiB versus 1 GiB; 40.4 versus 42.1 FPS | One earlier comparison, with varying system conditions; memory saving does not imply zero lookup/FPS cost |
| Earlier combined transfer/resource/query profile | 46.88 → 48.59 mean FPS (+3.65%) at 1920×1080 output / 960×540 scene; about 105 MiB more process memory accepted | Fixed Central Yharnam ABBA cohort, with run-to-run variation; not an isolated benefit per option or a whole-game 60 FPS result. [Record](release/METAL-BENCHMARK-GUARD-V3.md) |
| CPU-invalid sampled-copy retirement | Settled footprint 6163.10 → 5880.30 MiB; invalid sampled copies 296.12 → 0 MiB; clean cached backing about 303 MiB retained | Earlier Old Yharnam/Dream travel check. Final camera-matched windows were 36.19 versus 35.99 FPS; no FPS gain claimed. [Release record](release/0.2.28.md) |

The 0.2.28 launcher update retained the tested 0.2.27 production renderer/runtime. It passed
36 Swift and 19 Python release regressions plus extracted-signature and usage-only link checks;
those are not a new gameplay FPS or visual-quality certification. Optional experiments remain
labelled; memory accounting is not a hard RAM cap. First-entry stutter, intermittent audio
crackle and long-session behavior are not declared universally fixed.

## Licenses and release boundaries

The root [LICENSE](../LICENSE) contains GPLv2. Preserve the applicable source-file/component
notices as well: for example, the adapted sampler memo has **GPL-3.0-or-later** headers, while
other imported components carry their own notices. Do not relabel third-party code or artwork as
original work under one blanket license. Bundled third-party notices and dependency licenses,
the pinned [source manifest](release/SOURCE.md), and original source headers document provenance.

Published app assets exclude game executables/data, saves, shader caches, downloaded UI/font
packs, World Warper, QA apps, recorded routes, private automation controllers and local sampling
outputs. Reusable regression source remains in Git; private test assets and tooling are not
part of the public package. The retired offline-library experiment is not included.

## Project name (0.2.34)

The maintained fork is named **Blood Echoes** and lives at
`WiredThings/blood-echoes`. The launcher app is `Blood Echoes.app`. Upstream repository
names and historical release filenames remain as originally published.
Launcher bundle ID `io.github.bbport.optimized`, game bundle ID
`io.github.bbport.optimized.game`, preference keys and Application Support/bbport paths
are deliberately retained, so this branding update requires no save/settings migration.

## Metal pipeline archive safety (0.2.35)

Patch 0037 keeps loaded binary archives read-only. When new pipelines are known, it releases
the read archive, rebuilds a fresh archive from all successful prewarm descriptors (including
hits), and atomically saves a v2 file. Legacy files, SPIR-V/MSL caches and active PSOs are kept.
Private read copies prevent Metal's serialization fast path from moving the original file;
unreadable archives are preserved. Descriptor metadata is released with the warm archive and
is not collected during later gameplay. No shader math, textures, resolution or effects change.
Serialized Metal pipeline-map keys now preserve their complete byte representation across
copies, avoiding duplicate states caused by indeterminate structure padding.


## Forced model LOD and missing surfaces (2026-10-19)

The existing Kyo/auser1337 `Model LOD 1 (Lower)` patch replaces the model-detail
selection at guest address `0x0216fc09` with a forced value. In the owner's Old
Yharnam corridor save, forcing 1 omits both walls and parts of the floor; keeping
the unpatched game selection (0) restores them. A reverse 0 → 1 check reproduced
the holes at the same camera with native 1080p, MetalFX off and occlusion off.
The launcher and new M2 shortcut configurations now default to 0 and warn when a
forced lower detail level is selected. Existing user settings are not silently
migrated; the owner's confirmed repair was applied separately. No renderer,
cache-budget, velocity, occlusion or two-level-page-table code is changed.

Nexus [mod #163 V8 fixed](https://www.nexusmods.com/bloodborne/mods/163?tab=files)
was downloaded for inspection only. Its `PATCH FOR HIGH RESOLUTIONS` is byte-for-byte
identical to the bundled `Increased Graphics Heap Sizes` patch (auser1337): two
writes at `0x04b36dda` and `0x04b36de2`. It does not overlap the active FPS preset.
The same package's `60 FPS (With Deltatime v2)` overlaps 236 bytes of the active
Uncap FPS++/Sprint Fix set, with 70 different byte values; it also differs at 43
bytes from the bundled 60 FPS++ preset. These old FPS patches must not be stacked
with the existing presets. The downloaded XML/ZIP is not redistributed.

Related upstream reports cover different causes: [bbport #99](https://github.com/deadinside28/bloodborne_pc/issues/99)
is a Polaris/Vega HiZ driver problem; [BloodBorneButPc's release notes](https://github.com/Awakenchan/BloodBorneButPc/releases)
report model omissions from PS4-sized graphics heaps. Neither establishes the
cause on Metal. The matching local LOD comparison is the basis of this repair.
The large-heap trial reached critical memory pressure and was stopped before
visual validation; the larger heap is not enabled.

## Shared save management and online usability (0.2.38)

Adds a native save manager with streamed SHA-256 verification, whole-directory atomic replacement and pre-restore backups. Formal offline/online saves now share one root; legacy formal online copies are backed up before migration. QA roots are never scanned or imported. Checkpoints retain the existing upstream file format; online mode archives/clears them and offline return seeds a new one. The runtime now holds the save-session lock even with quicksave disabled.

The launcher retains The Hunter’s Dream as its only online service and keeps the existing Keychain identity. Device codes can be clicked to copy. The Mac credential destination policy accepts only explicitly permitted THD HTTPS service origins, including the game service on port 20443. Renderer code, Metal patches and game localization payloads are unchanged.

## Always-offline checkpoint restore (0.2.39)

Revises the shared-save policy: online entry archives old checkpoints and seeds a fresh baseline; F2 saves in both modes. Every F3 quickload strips network credentials/routes and restores in a new offline process after the old process exits. Offline state persists across retries and launcher restarts until explicitly re-enabled. A per-save marker is written before restore mutations, so a launcher crash cannot silently reconnect a restored save. Save Manager restores follow the same offline rule. Replacing online checkpoint trees is staged and verified before atomic publication. The network module, renderer binaries and graphics/cache policies are unchanged. Remote server actions are not rolled back by local checkpoints.

## Build provenance display and release organization (0.2.40)

The launcher About panel and embedded-engine version display now show the short project Git revision instead of repeating the numeric build number. Full provenance is embedded as `BBSourceCommit`; Git-less exports resolve it from the source manifest, without borrowing a parent repository identity. The numeric macOS build number is retained. Existing gameplay, saves, settings, networking and renderer binaries are unchanged. Older GitHub releases are retained as Pre-release; Latest points to the current package.

## F3 automatic Continue (0.2.44)

Local runtime fix: replace writable-mount-only autoload cancellation with the signature-verified Bloodborne 1.09 character-readiness gate already used by fresh checkpoints. Menu input is checked before each tap, retains its timeout, and releases once. Network credential removal and supervised restart ordering are unchanged.

## Complete Save Manager (0.2.45)

Local SwiftUI/SaveStore work separates live game saves and active checkpoints from archive history, older checkpoints and pre-F3 backups. Adds metadata-only background refresh, individual backup and verified export, complete normal-save wrappers, helper-compatible checkpoint exports, unique atomic publication and destination checks. Viewing/exporting does not change active pointers or formal saves. Existing lock, offline restore, network and F3 policies are retained. No new third-party patch or game resource is bundled.

### 0.2.46 — bounded save history and optional iCloud Drive

Original launcher work adds 3/3/5 automatic history retention, explicit pinning, content-based automatic archive deduplication, and coordinated normal-save syncing through a user-selected iCloud Drive folder. Per-device latest packages and vector clocks preserve concurrent offline progress; restore uses existing verified backup/offline/F3 rules. No third-party mod, runtime host or renderer binary is changed. See [SAVE_SYNC.md](SAVE_SYNC.md) and the localized user guides.

### 0.2.47 — automatic iCloud save folder

Local launcher work removes the sync directory picker and stored custom-path preference. Enabling normal-save sync automatically creates or opens `iCloud Drive/Blood Echoes Saves`; another Mac uses the same location. Missing Drive and unrelated existing folders fail safely, and isolated QA uses its own fake home. The 3/3/5 retention, verified backups/imports, conflict handling, F3/offline protection, networking and renderer are unchanged.

## 0.2.51 Metal follow-up

Fork-authored patches 0086/0087 link tessellation patch layouts through the existing SPIRV-Cross API and protect per-module bindings when Metal libraries/functions are shared. Optional weak library reuse stays OFF; it uses the system CommonCrypto SHA-256 implementation and adds no bundled dependency. See [validation and limits](release/METAL-TESSELLATION-AND-LIBRARY-REUSE.md). Existing third-party game/language patches and credits are unchanged.
