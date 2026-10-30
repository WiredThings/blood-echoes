# Metal optimization controls

The 0.2.50 launcher exposes up to 35 runtime controls under **Advanced → Metal optimizations**, in English,
Simplified Chinese and Traditional Chinese. The displayed badges are **Verified**,
**Experimental**, **Adopted bundle**, and **Higher memory use**; stored/source keys may retain
older badge wording for localization compatibility. Existing object-motion and occlusion controls stay
in their current sections. No resolution, effects, save state or cache capacity is changed.

The ARM64/FEX development build also has default-ON **Optimize vertex math with
position invariance**, adopted after a short matched M4 comparison (about 1.5%
more FPS). It remains separately switchable and Strict math takes precedence.
An explicit OFF clears inherited values and omits `BB_MTL_RELAX_VERTEX`, preserving
the native OFF path and its existing cache identity. The counts below describe
the earlier release; the FEX UI also hides its now-required legacy host switch.

## Defaults and evidence

The previous 34 controls retain their defaults (21 ON / 13 OFF). Supported native-host packages add the default-ON ARM64 renderer control (22 ON / 13 OFF). A missing preference uses
the current runtime default; existing explicit preferences remain authoritative.

- The two-level address table, direct scene-copy clear and idle/startup object reclamation
  remain adopted. The address table reduces capacity by about 0.9 GiB relative to the flat
  table; direct clear improved the earlier matched scene by about 5%.
- Attachment discard, strict vertex inlining, lean texture usage and alpha merge are marked
  **Experimental** in the default-on group where isolated full-game FPS benefit remains uncertain.
- The ten accepted clear/copy/staging/residency/tiling/barrier/query controls are marked
  **Adopted bundle**. Their ON defaults preserve the owner's previous choice
  and accepted bundle memory tradeoff; the badge does not claim an isolated gain per flag.
- The five existing optional experiments remain OFF: same-packet cross-chunk snapshot reuse,
  fragment-parameter specialization, inline small writes,
  relaxed fragment/compute math and binary pipeline persistence. Notes describe uncertain
  benefit, possible numeric changes and the observed memory costs. Binary persistence has a
  separate higher-memory label (about 0.6 GiB in the earlier archive-load experiment).

Environment names and individual notes live in `MetalOptimizations.swift`. Controls
normally send `0` or `1`; vertex OFF uses the omission rule above. Required parents gate direct-compute precision, half-size texture
tiling and pending-clear folding. Disabling a parent suppresses the child's effective runtime
flag without losing the child's stored choice.

Settings persist in the optimized launcher's own UserDefaults domain. Existing matching `0/1`
developer environment entries are imported once when no optimization dictionary exists.
Thereafter visible controls override matching developer entries; old file-based A/B selectors
are excluded so they cannot silently override checkboxes. Vulkan receives none of the managed
Metal flags. Reset affects this section's controls only; game quality and saves are retained.

## Restored experimental alternatives (2026-10-22)

A neutral or slower M4 result does not establish that a method is useless on another GPU,
CPU bottleneck or workload. These six candidates are retained as **Experimental**, OFF by
default. The three removed implementations were restored as patches 0074–0076, rebased after
0071/0073 rather than replacing their synchronization and query-write fixes.

| Control | Prior evidence and effective dependencies |
| --- | --- |
| `BB_MTL_NATIVE_CLEAR_BATCH` | Exact-pixel clear checks passed; ABBA/BAAB pooled FPS −0.06%, no established saving. Requires color-clear batching. |
| `BB_MTL_MERGE_OVERLAY` | 114,688 pixel bytes matched; one M4 ABBA −1.38% with a higher measured footprint. No additional texture allocation; memory benefit unproven. |
| `BB_MTL_FILL_UPDATES` | Preserves all writes using native repeated-byte fill; M4 ABBA −1.25%. This path takes priority for eligible repeated-byte payloads. |
| `BB_LAYER_USE_CACHE` | Full-range bookkeeping memo; randomized/differential tests passed, M4 −3.34%. 1,040-byte CPU memo, unchanged resource budgets and write clocks. |
| `BB_MTL_COPY_PAIR` | 112 GPU configurations passed; M4 −1.00%. Only the second store to the same resolved physical pointer is omitted; distinct pointers retain both. |
| `BB_MTL_RENDER_UPDATES` | Runtime pixel/ordering/conflict tests passed; M4 −8.63%. Requires inline updates; unknown fragment/alias cases retain the original fallback. A small lazy host pipeline and bounded CPU metadata are added when enabled. |

The latter three results are historical comparisons, not new performance results for the
restored combination. Do not attribute a combined test's FPS to an individual option. No
resolution, effects, texture quality, cache capacity or retirement deadline is reduced.
The notes retain measured slowdowns and memory uncertainty instead of promising a benefit.

`BB_MTL_CONTINUE_CLEARS` exposes the already retained consumed-clear continuation (0071),
ON by default, with attachment/layout/feedback guards. One matched chalice ABBA measured
about +2%; it does not eliminate any of the 35 parameter writes per frame. Query-write epoch
fix 0073 stays enabled independently, including when users toggle snapshot reuse.

The rejected alias/write-clock narrowing, precheck-only upload deletion, historical
GpuModified clearing and first-fault-only upload masks have concrete correctness counterexamples.
They are not offered as optimization checkboxes. Released-storage retirement 0052 remains a
development-only candidate until actual admission/alias/lifecycle proof is complete. The
host-fragment fence prototype recorded no eligible waits in the measured native scene and
lacks complete handoff validation; it is not exposed. Diagnostic capture controls do not
become performance options. The separately owner-removed offline-library experiment stays
removed; restoring it was not part of this inventory.

## Binary archive OFF now means no disk use

Patch 0033 gates loading an existing `MTLBinaryArchive` and automatic destructor serialization
with `BB_MTL_PERSIST_WARM_ARCHIVE`. Previously only the startup save was gated, so disabling it
could still load an old file. OFF now ignores existing files without deleting them. A fresh
compilation archive and the accepted live-PSO lifetime remain as before; all active pipeline
maps, functions and source/SPIR-V caches retain their capacities. Explicit low-level `Flush()`
remains available to native tests; ordinary startup already calls it only when opted in.

Native tests verify ON save/next-start hits, OFF with an existing file (zero archive hits and
byte-identical file preservation), OFF without release (no destructor-created file), retained
pipeline identity, late pipeline creation and GPU output. The archive-persistence check originally covered 33 patches; the current 0.2.35 series has
37 patches and reconstructs idempotently. Swift tests exercise every toggle, defaults, parent gates, persistence, migration,
reset, environment precedence, backend isolation and all three localized descriptions.

## Retired offline-library experiment

The owner requested removal after eight same-renderer ABBA runs. The loader/export/compiler
helper, its patch and local compiled library artifacts were removed. No offline-library option
is present, and the experiment was never enabled in the installed application. Apple's Metal
Toolchain remains an installed development tool.

| Same-scene comparison | Source path | Offline library |
| --- | ---: | ---: |
| Warm cohort, mean FPS | 47.63 | 47.03 |
| Warm cohort, process median MiB | 5585.2 | 5575.9 |
| First-use proxy, mean FPS | 47.89 | 48.26 |
| First-use proxy, first world observed | 26.90 s | 28.31 s |
| First-use proxy, mean of first-5-second maximum frame intervals | 187.43 ms | 222.72 ms |
| First-use proxy, process median MiB | 5586.1 | 5502.2 |

The warm source-path prewarm times were 0.656/0.664 s. Offline first load was 8.845 s and its
subsequent warm load 0.824 s. Driver-cache warmth differs on the first artifact load; these
numbers do not prove a universal offline-code regression. No repeatable startup/hitch benefit
was established, and small steady-FPS differences were not accepted as a gain.

The first-use proxy removes only 560 recorded pipeline-key files from a private copy while
retaining MSL/SPIR-V and artifacts; it forces creation while loading Central Yharnam. It is not
an unvisited-region walkthrough. Each run used the frozen save/camera, unchanged 1080p output,
540p internal resolution and accepted patch defaults, 15 s quiet warmup and 30 s scoring.
Compiler audit/export/capture/stage timing were OFF in scored runs. All score windows had
warning-level system pressure and zero swap growth; original saves and preferences were
preserved. Memory differences vary with retirement phases and are not exact causal savings.

Retained local evidence: `outputs/benchmark-m4-offline-libraries/retirement.json`.
The experiment illustrates that offline Metal IR does not eliminate final GPU pipeline
compilation or supply unknown runtime descriptors. It does not establish that precompilation
is useless in engines with a complete pipeline manifest.

## 0.2.28 launcher audit

All 25 optimization IDs were matched to concrete current Metal engine consumers. The new
CPU-invalid sampled-copy reclaimer is now visible, retaining its already enabled 0.2.27
default. Existing dictionaries and legacy explicit overrides are preserved. Valid cache
capacity, image quality, game saves and existing effect choices are unchanged.

- Metal shows only its actual VSync / unsynchronized presentation choices. Legacy Immediate
  and relaxed-FIFO selections use their previous effective unsynchronized behavior, without
  rewriting the stored preference merely to display it. Vulkan retains all four choices.
- HDR, RCAS/sharpness, preupload and Vulkan validation are shown only for Vulkan. Their stored
  preferences survive backend changes. CPU readback controls remain because the current
  memory tracker still consumes them; they were not classified as unused without evidence.
- MetalFX alpha merging is gated by actual MetalFX availability, including ultrawide forcing
  upscaling off. Its requested preference is retained when temporarily unavailable.
- GPU timing OFF removes both legacy profiling variables from the effective environment;
  Metal also drops Vulkan validation and unused preupload entries.
- Frame-limit zero now correctly says no additional limit. Old M2/Max benchmark recommendations,
  generic restart advice, a misleading native-renderer claim and repeated explanatory footers
  were removed. Adopted-row detail is in help; optional experiments keep concise visible
  uncertainty/cost notes. Badges distinguish verified, experimental and adopted-bundle evidence.
- English, Simplified Chinese and Traditional Chinese resources remain in sync. This is a
  launcher-only update; the production probe and renderer are byte-identical to 0.2.27.

The full-app QA bundle uses a distinct preference domain and a QA-only data path. Production
ignores that path override. Tests cover every flag, dependency gating, legacy migration,
backend isolation, presentation aliases, diagnostic precedence and localized format arguments.

## Binary archive write safety (0.2.35)

Persistence remains experimental/OFF by default; existing explicit choices are unchanged.
Loaded archives are lookup-only. On a miss, the next startup save rebuilds the complete known
prewarm set into a fresh object, releasing the read object first. The CPU descriptor ledger
contains no texture/buffer contents and is cleared by warm-archive release; late game-time
pipelines do not accumulate serialization metadata after release.

The new files are `pipelines-v2.binaryarchive` / `pipelines-relaxed-math-v2.binaryarchive`.
The old files remain in place and may supply first-upgrade lookup hits; no cache clearing is
required. Existing SPIR-V/MSL file formats, capacity and paths are unchanged. This repair
addresses archive persistence, not a claimed gameplay FPS gain.

## Same-capacity heap history candidate (0078, validation in progress)

`BB_LIFETIME_HEAP_HISTORY_PRIORITY` is exposed as **Preserve heap release history**,
Experimental and OFF by default. It prioritizes heap release evidence over transient Range
history while retaining the same 32,768-record total limit. It does not change cache capacity,
resource roles, GPU completion, mapping/alias checks or release authorization. Native tests
reproduce the original history displacement and retain reuse/overlap rejection; five actual
runtime/Metal cases pass. Paired map travel and clean ABBA are pending, so no memory or FPS
benefit is claimed. The installed application has not been replaced.

## Guarded depth/stencil layout continuation (0.2.49)

`BB_MTL_DEPTH_LAYOUT` is experimental OFF, independent of consumed-clear continuation. It only
merges identical attachments across known read-only-to-write plane promotions, with no pending
barriers, fences, hoisted work, clears or render tails. Prior and current attachment feedback
exclude continuation. Unknown visibility callbacks and reduced-density production query targets
retain the original path. No resource allocation or cache budget is added. Native pixel/query
tests pass, but the M4 A/B does not establish a benefit; see [evidence](METAL-DEPTH-LAYOUT.md).


## 0.2.50 native host

Supported macOS 27+ packages add `BB_METAL_ARM64` (default ON), bringing the visible
controls to 35 (22 ON / 13 OFF). It moves the renderer to a separate ARM64 process;
the game and networking still use Rosetta. Eight fixed-scene M4 comparisons measured
about 6% higher FPS without higher memory pressure. Turn it off to retain in-process
Metal. The option is hidden when the matching native package or OS is unavailable.
See [architecture and validation](ARM64-METAL-EXPERIMENT.md). No cache capacity changes.
