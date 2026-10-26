# Isolated ARM64 runtime development snapshot

This is an **unstable development snapshot**, not the production runtime or a Release.
The goal is to replace the game CPU's Rosetta dependency with FEX while reusing
the existing launcher, Metal renderer, localization, saves, input, F3 and network bridges.
The production application and its data are not changed by this source export.

The snapshot contains source, authored regression tests and explicitly selected
non-secret build inputs. It intentionally contains **no game assets, dumped modules,
saves, quick saves, credentials, runtime caches, QA preferences or captured game frames**.
Vendored code retains its original licenses. SOURCE.json records upstream provenance.

## Current status

The 2026-10-26 native PSO preparation checkpoint is described in
[local-pso-preparation.zh-CN.md](docs/local-pso-preparation.zh-CN.md).
The coherent native closure passes source/local-library strict archive and GPU
pixel controls, including the original CameraMotion::RecordMotion and Presenter
post-processing factories. The Camera upload now pads its 108-byte public constants
to the 112 bytes required by MSL without changing fields or shader math. Thirteen helper families now have preparation entries, including the unchanged
RGB/depth FormatConvert factories; other final pipeline families remain open.
[Native format/phase-counter validation](docs/native-format-and-phase-counters.zh-CN.md)
adds 35 active native API boundaries and 17 GPU-process CSV fields; first new-cohort
game integration passes, while cold directory preparation remains partial. No
whole-game, matched performance or zero-driver-compilation claim is made.
Three real complete game recipes previously prepared seven native PSOs, with all
seven strictly reused in independent processes. These are scoped controls, not
whole-game coverage or gameplay acceptance.

Original final-exec orchestration now selects the actual title/cache and pages the
whole candidate manifest with bounded memory and resumable progress. Real packaging
exposed a missing bundled SQLite extension (repaired in new copies) and a load-log
parser that truncated space-containing app paths (fixed with PID/path/SHA checks).
Four original-entry game integrations now pass. The latest warm restart strictly
verifies all 424 policy-eligible current-cache recipes (429 records, five existing
geometry session-span exclusions); 419 prior hints survive, but remain scheduling
hints rather than artifact authority. A subsequent tools-only package removes repeated library audits within one
preparation call and probes current native strict artifacts before recording,
even in a new progress domain. It prepares all 424 inputs in 104 seconds and
reaches the same scene in 156 seconds (previously 471/519 seconds). This is not
a matched FPS gain claim; the scene remains around 49–50 FPS. The original
freeze, native admission, memory and lock guards remain intact. Full-game PSO coverage and cold/warm hitch/peak
memory acceptance remain open; early loading pressure is documented rather than
hidden. Source artifacts and aggregate evidence are published on the development
branch only. Production and Release are unchanged.

Local per-user compilation is the default-on delivery direction, superseding the
earlier average-FPS-based default-off decisions below. Game compiler payloads stay
local; only source, authored controls and aggregate evidence are published.

## Earlier investigation checkpoints

The following records describe their original cohorts and versions. Current
acceptance is stated above and in CURRENT-PLAN.zh-CN.md.

See GOAL.zh-CN.md, CURRENT-PLAN.zh-CN.md, NIGHT-HANDOFF.zh-CN.md and docs/.
The goal now explicitly includes resolving the nine remaining imports and building
a version-bound shader/material and full-specialization inventory. Bounded discovery
probes find real lead time, mainly during startup; gameplay coverage remains partial.
See docs/shader-preparation-opportunity.zh-CN.md; no async compilation is enabled.
The fixed-title read-only inventory now finds stored code for all 221 observed
program hashes, and indexes 508 existing serialized specializations/409 pipeline
keys. It does not establish whole-game variant coverage or PSO success. The old
GPU host also lacks the nine imports; source resolver parity is byte-verified,
not an execution test of the production CPU. One DATA contract (__progname) is now implemented in the existing guest parameter page;
the ModuleInfo query/matcher pair now uses the original loader and exact guest layout;
msync now uses real file synchronization and protected mapping leases. Six numeric VM
address slots retain output-buffer validation while permitting PROT_NONE operations.
Five contracts remain pending. See docs/msync-and-vm-address-contracts.zh-CN.md,
docs/module-info-contracts.zh-CN.md and
docs/guest-process-data-contracts.zh-CN.md.
See docs/fixed-shader-catalog-and-imports.zh-CN.md. Game/catalog/cache payloads
are local only; only authored tooling, controls and aggregate findings are exported.
Bounded compiler-layout decoding now verifies the 508/409 and 327/259 cohorts
without registering SRT code. Eighteen canonical Writer/Profile controls pass.
Profile padding is excluded using native field equality; no cache is merged.
Motion-vertex preload rejection is zero in these samples, so that hypothesis is
not their cause. See docs/semantic-cache-inputs.zh-CN.md for limitations. Exit-code error/restart separation,
semantic Clang ABI markers, full libc spans and manual-input packaging have regressions.
A real Metal first-upload hash bug has old/fixed pixel tests and an isolated QA candidate;
its relation to the observed combat white models remains unproven. White models and exploration stutters
remain under investigation; no performance improvement or complete compatibility is claimed.
Death/respawn, online and menu live retests are deferred for the owner's acceptance.
All 16 original AvPlayer implementations now have guest callback/handle bridges.
Real FEX callbacks, thread cleanup and layout/bounds controls pass; static ABBA
48.492 versus 48.623 FPS shows no measured improvement. Five imports still need
implementation; __progname, msync and the ModuleInfo query/matcher pair have actual FEX controls. See docs/avplayer-bridge.zh-CN.md for qualifications; the latest host-local contract QA package now freezes CPU67db33e2 and its actual
a500 host library. It is not a production installer or full compatibility acceptance.
The private CPU QoS option remains OFF: its measured static ABBA did not show a benefit.
See docs/inplace-texture-and-cpu-scheduling.zh-CN.md for actual in-place pixel controls,
limited game observations and process-wide scheduling counter qualifications.
See docs/producer-wait-and-range-audit.zh-CN.md for the producer-condition blind spot,
candidate CSChrThread2 notification and complete-range texture intent controls.
These are diagnostic findings, not a combat-white-model fix or measured FPS gain.
The default-off unaligned signal probe uses pre-bound thread state; its first game
window measured 47 repairs in 0.318 ms, separate from the observed long frames.
See docs/unaligned-signal-audit.zh-CN.md for scope and test limitations.
Native game-frame diagnostics reuse the existing completed-present readback, with
18 actual Metal format/orientation controls and bounded private input actions.
They are separate from FPS tests. A real Old Yharnam enemy contact was observed,
but the intermittent combat white model remains unreproduced; one 140 ms frame
overlapped image readback and is explicitly not performance evidence.
See docs/native-game-image-diagnostic.zh-CN.md.
Private GPU candidate admission now verifies the real Mach-O direct load edge,
ARM64 architecture, declared hashes and strict signatures, with 12 authored
Mach-O controls. The combined range/image route observed bloodied character
textures but did not reproduce the intermittent white model. No performance
benefit is claimed. See docs/combined-combat-and-load-identity.zh-CN.md.
Default-off phase probes now distinguish GCN-to-IR, SPIR-V and backend work.
One real 338 ms gameplay frame overlapped a 232 ms new Metal library and 84 ms
PSO creation, with only 0.407 ms of FEX block translation. See
docs/shader-phase-and-uniform-range.zh-CN.md for complete trace qualifications.
A source-suffix-copy reduction retains byte-identical generated text on 367
inputs and 24 actual Metal sampling pixel cases. Its host microbenchmark is
not a measured game FPS improvement; normal QA packaging remains frozen.

## Parallel contract reviews

Mlock remains UNIMPLEMENTED. Its finite accounting model has 22 authored controls,
and a separate actual one-page Darwin test demonstrates duplicate wiring. The
16 MiB per-call / 64 MiB total limits are candidate ceilings, not measured title
requirements. See docs/mlock-contract-review.zh-CN.md and
tests/mlock_contract_proposal/. No page-locking path was enabled.

Seven existing-library Metal controls expose wrong same-address byte-layout reuse,
while preserving lawful sRGB and normalized 1D/2D alias controls. The 26-case
classification predicate remains out of resource selection; the original review
patch is still an unapplied asset. A bounded implementation is now compiled into
a separate, default-off observation candidate. Preserving GPU-only raw bytes
across retile/detile remains an explicit behavior-change gate. This is not proof of
the reported combat white-model cause. See docs/white-model-resource-audit.zh-CN.md,
docs/texture-layout-candidate.zh-CN.md and tests/texture_layout_contract_proposal/.

The bounded material parser now decodes 304 MTD files and has 10 authored tests.
Name-family candidates and stored-code associations are not actual live bindings.
Complete local material/identity ledgers are excluded. See
docs/shader-material-inventory.zh-CN.md and tools/material_inventory.py.

The four remaining finalizer/context contracts have a separate review and 12
dual-architecture structural assertions. None is implemented by that review.
Module cleanup must not be confused with thread TLS cleanup; guest context
restoration cannot call Darwin sigreturn. See docs/thread-signal-contracts.zh-CN.md.

## Latest layout observation and local QA checkpoint

The opt-in helper has six native controls and 13 real-Metal observation controls.
It records at most 64 same-shape byte-layout mismatches per process, never changes
selection or cache budgets, and preserves lawful aliases. The successful original-
launcher short scene run recorded no mismatch; it is not white-model or FPS proof.
Two earlier attempts stopped at swap-growth guards; a later user-closed-apps run
completed, with transient critical-pressure samples still explicitly reported.

GPU-only roundtrips expose padding errors in 64x60 and small mip chains. Nine
GPU coverage-merge controls prove the required semantics, but their CPU-generated
coverage oracle is NOT a production design. The integrated 13-case suite retains
two known original defects with target_expected=0 and actual exit=7; its report
all_preservation_paths_correct is false. See
docs/layout-observation-and-raw-preservation.zh-CN.md,
docs/texture-layout-roundtrip.zh-CN.md and docs/texture-layout-coverage-merge.zh-CN.md.

The current host-local contract QA freezes CPU67db33e2 with the actual a500 host
load edge and original first-upload renderer. It has no layout conversion, layout
logging, automated input or QoS enabled. Package signature/architecture, actual
packaged-library FEX positive/negative and archive-content checks pass; GUI/live
package F3/death/network acceptance is not claimed. The ZIP is local only, not in
this Git snapshot or a Release. See the checkpoint report for its SHA and limits.

## Opt-in logical tiler coverage candidate

A private 652-byte parameter block preserves the public 460-byte prefix and adds
logical extents only for canonical full color exports. Exact range and direction
select constant 13, which also belongs to the full pipeline key. Physical addressing,
proxy/MetalFX semantics and legacy callers retain their previous behavior. The
new raw-padding fix passes 24 GPU controls, including the two original failures;
old/new same-process pipeline separation and shape/range controls also pass.
This candidate is OFF by default, has not changed FindImage selection and is not
in the ordinary local QA package. A short silent isolated scene run completed,
but swap increased about 1.73 GiB. It is not an FPS/memory equivalence or a
white-model fix claim. See docs/tiler-logical-coverage.zh-CN.md and
 docs/tiler-parameter-abi-review/report.zh-CN.md for measured ABI and limitations.

Prepared isolated-host controls (never point them at formal caches or saves):

```sh
python3 tools/generate_tiler_bounds_shaders.py
python3 tests/test_tiler_bounds_rewriter.py
python3 tools/build_tiler_bounds_candidate.py
python3 tests/test_tiler_bounds_cache.py
python3 tests/test_texture_layout_preservation.py --full --candidate evidence/tiler-bounds-candidate-current.json --bounds
```

## Development build limitations

This is a source checkpoint of an active local prototype, **not yet a clean-checkout
standalone build target**. The current configure/Metal candidate scripts require the
private ARM64 dependency/object stage described in their manifests and several local
paths. Do not run them against production state. Making the prototype build independent
of that stage is pending; the repository's normal production build is unchanged.

On the prepared isolated development host:

```sh
python3 tools/generate_native.py
ninja -C build/fex -j1
python3 tools/run_current_regressions.py
# rebuilds all 56 binaries used by the 121-case authored suite before testing
python3 tests/test_runtime_exit_contract.py
python3 tests/test_libc_spans.py
python3 tests/test_native_abi_declarations.py
python3 tests/test_msync_fex.py
python3 tests/mlock_contract_proposal/test_bounded_mlock_model.py
python3 tests/test_material_inventory.py
python3 tests/test_layout_audit.py
python3 tests/test_texture_layout_audit.py
python3 tests/test_texture_layout_preservation.py
python3 tests/test_manual_qa_policy.py
```

All live test helpers are QA tools. Review their explicit private-root checks and
memory/lock policy before using them; never substitute a formal save or cache directory.
Do not publish a Release from this snapshot until its remaining acceptance gates pass.

## Bounded raw-write ordering investigation

See docs/raw-write-order.zh-CN.md and docs/raw-authority-review/.
Two 19-case real-Metal cohorts preserve four known invalidation defect patterns
instead of declaring the broken runtime correct. GPU-only same-submission controls
prove ordered export/partial-write composition, not actual BufferCache integration.
The default-off bounded observer changes no selection, dirty state or cache budget.
A corrected silent private scene run recorded 16 potentially missed overlap rows,
with at most four images per probe, no critical pressure and 57.5 MiB final swap rise.
Writable binding extent is not proof of every byte being written or of the reported
white-model cause. The initial observer-limit failure remains documented.
No FPS improvement, whole-game acceptance or safe production conversion is claimed.

This is a reviewed overlay on f349bd44. Unbuilt Mlock integration and owned-shader
parallel drafts are intentionally excluded from this source checkpoint. Neither is
enabled. The latest ordinary local QA package remains c22970cc; no Release changes.

## Owned shader compilation foundation (scheduler still pending)

See docs/owned-shader-validation.zh-CN.md and docs/owned-shader-source-review.zh-CN.md.
All 230 GPU translation units were rebuilt together after the shared Info change.
Authored real GCN/SPIR-V/Metal tests pass with owned input poisoning, independent
compiler contexts, detached-module destruction and read-only speculative caches.
The ordinary compiler's phase-probe off/on output matches the saved authored reference;
24 GPU byte-preservation/alias/mip/padding controls also retain their expected outcomes.
This does not establish whole-game shader coverage or a performance improvement.

No asynchronous scheduler, first-program capture or complete GPU adoption is wired.
The original path remains synchronous. Motion-vector owned jobs are rejected until
their session inputs are captured explicitly; ordinary motion rendering is unchanged.
The short game run is startup/action smoke only: the user observed the script striking
a wall. It is neither real exploration nor combat/white-model acceptance; blind attack
scripts will not be reused for those purposes. Production, normal QA package and Release
remain unchanged. Mlock integration is still an unapplied draft, excluded from this overlay.

Old-ABI diagnostic libraries now obtain f349bd44 sources through tools/frozen_gpu_headers.py,
with revision-specific EOL attributes and Git-blob verification. They no longer compile
against the newer Info structure. This still requires the prepared private dependency stage;
it is not a clean-checkout standalone prototype build claim.

## Bounded guest page wiring

See docs/mlock-integrated-validation.zh-CN.md. Real Darwin locking is capped at
16 MiB per request / 64 MiB total / 4096 pages; duplicate guest locks are a set.
Mapping leases, successful replacement retirement, MS_INVALIDATE EBUSY and fatal
partial-commit handling are covered by native and FEX controls. No game performance
or full-game lock-demand claim is made. Five of the original nine missing imports
now have bounded implementations and tests; four remain. The normal QA package
and production app are unchanged. This supersedes the earlier unapplied-Mlock note.

## Safe speculative shader reads (preparation scheduler still pending)

See docs/shader-guest-capture-validation.zh-CN.md. The full 230-unit GPU rebuild
and authored signal-recovery, owned-GCN/Metal, phase-output and 24 byte-preservation
controls pass. Preparation uses bounded shader-header reads and POD fault leaves;
checked decoding rejects truncated owned inputs. The older SRT/fetch wide recovery
region still needs replacement. No asynchronous first-miss compilation or performance
improvement is claimed, and no game run or production update was performed.

## Stable mirror registration identity

See docs/stable-mirror-validation.zh-CN.md. Real Layer source passes 44 authored
identity/concurrency controls. The new API is not called by game resource paths,
and the complete GPU rebuild is still pending. The lease protects the registry,
not allocator payload or native Metal lifetimes. No white-model fix is claimed.

## Bounded preparation reads and coherent GPU rebuild

See docs/shader-readset-validation.zh-CN.md. The existing lookup-only preparation
path now uses bounded SRT/fetch/V# leaf reads and ordinary C++ unwinding instead
of a wide longjmp region. The full 230-unit rebuild passes real preparation,
owned shader, phase-output and 24 GPU preservation controls. Stable mirror tests
now pass 46 controls against both real Layer source and the actual new library,
superseding the prior pending-rebuild note. First-miss async compilation/adoption
is still pending; no white-model fix, game FPS or memory improvement is claimed.

## Module finalization and shader strategy

See docs/module-finalizer-validation.zh-CN.md. Real C and eleven FEX controls,
plus the fully rebuilt baseline suite pass. The module-specific TSD retirement
preserves global/DSO atexit ordering, callback leases, key generations and the
74 versus 75/76 exit contract. Six original missing imports now have bounded
implementations; three context interfaces remain unsupported. This is not a
generic dlclose guarantee or a game performance result. Formal app/data/Release
and the normal manual QA package remain unchanged.

GOAL.zh-CN.md now prioritizes persistent complete combinations and load-time
preparation, with first-seen async compilation as fallback. Warm-state validation
cost and actual cross-process cache hits must be measured, not inferred.

## Persistent startup archives and exact-module load reuse

See docs/warm-persistence-validation.zh-CN.md and its machine-readable companion.
The opt-in ARM archive cohort now binds actual translator/game/patch/options/device
identity, preserves legacy files and the Rosetta branch, and publishes only successful
atomic writes. Invalid parent directories are rejected before the driver's serializer.
Already-loaded exact modules avoid duplicate SPIR-V reads during startup; corrupt first
loads cannot publish null programs. Fifteen authored native processes, actual Storage
controls, nine producer branches, and existing shader/texture regressions pass.
This is not a game FPS, whole-game precompile coverage, white-model fix, or clean-build
claim. The ordinary manual QA app and production app/data/caches/Release remain unchanged.

## Actual archive comparison and local build delivery

See docs/warm-archive-game-validation.zh-CN.md. Four isolated matched 75-second
ON/OFF/OFF/ON cases completed after background apps were closed. Native archive
hits reduce PSO construction, while 538 Metal source-library builds still occur.
The single cohort's FPS/memory variation is qualified; archives remain opt-in.
Two earlier protected stops are retained as incomplete, not performance passes.
The explicit private candidate selector reuses existing architecture/signature/load-edge
validation. No formal app, saves, settings, caches or Release were updated.

The requested offline-library delivery is code that builds locally from each user's
own game and complete effective shader combinations. Developer-generated game
metallibs/PSO archives are not distributable project assets. The local metallib
builder is now implemented as the separate experiment below; PSO archive reuse
and offline source-library building remain distinct stages.

## Local Metal library experiment (not default)

See docs/local-metal-aot-validation.zh-CN.md. Effective-source capture, a bounded
local-only serial toolchain builder, strict receipts, native library loading and
startup preparation now exist in isolated code. Four authored libraries and 428
local game requests were actually built; gameplay hit all 538 module loads twice.
No user/game-generated source or compiled artifact is included in Git or packages.
The first batch cost about 6m20s; warm library load was not faster than warm source,
and final GPU PSO compilation remains. This is functional evidence, not an accepted
performance gain. Entry admission, source fallback, exclusive publication and read-only recovery
are hardened and tested. Other source call-site coverage, final PSO preparation,
GUI integration and hardened-version AOT game performance remain pending. Normal QA/production apps and Release stay
unchanged. Incremental tools require prepared local build metadata, not a claimed
fresh-checkout clean build of this experiment.

## Offline-library / PSO coexistence audit

See docs/local-metal-aot-archive.zh-CN.md. Matched source/local/local/source runs
showed archive hits 500/0/500/0: the two library routes replaced one shared
archive. The isolated fix binds the local toolchain build identity into PSO
archive scope; 19 native children prove true local building/loading, strict hits
for both routes and preservation of both archives, with real GPU output.
Texture regression selection now binds the actual chosen candidate SHA; earlier
24-case runs accidentally used the prior warm-stage candidate and do not prove
their then-current AOT library. No stable FPS gain or white-model fix is claimed.
A separate 28-check authored probe proves GS ring/invocation and compute shared
size omissions in RuntimeInfo equality. It was not applied at that audit; the
later full build and validation are described below.

## Runtime specialization key correctness

See docs/runtime-key-correctness.zh-CN.md. Actual compiler consumers and old-library
cache counterexamples prove GS invocation/ring and compute shared-size omissions.
The isolated fix is now fully rebuilt across all 230 GPU units; actual cache
selection, unchanged compiler-derived layout, native archive and real texture
preservation controls pass. Six fixed-scene comparisons show no meaningful FPS
gain or additional variants. One higher footprint sample was not sustained; this
does not prove long-run memory neutrality or that the white-model cause is solved.
Production, ordinary QA packages, saves/settings/caches and Release stay unchanged.
The historical three-header probe now requires an explicit --baseline-header.

Current header changes require the complete build_owned_shader_candidate.py path;
the earlier body-only incremental tool deliberately rejects that baseline mismatch.
Local AOT regressions accept --candidate evidence/owned-shader-gpu-build-current.json
to validate the actual full-build library. Prepared local dependencies are required.
