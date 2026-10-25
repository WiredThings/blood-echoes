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

See GOAL.zh-CN.md, CURRENT-PLAN.zh-CN.md, NIGHT-HANDOFF.zh-CN.md and docs/.
The goal now explicitly includes resolving the nine remaining imports and building
a version-bound shader/material and full-specialization inventory. Bounded discovery
probes find real lead time, mainly during startup; gameplay coverage remains partial.
See docs/shader-preparation-opportunity.zh-CN.md; no async compilation is enabled.
The fixed-title read-only inventory now finds stored code for all 221 observed
program hashes, and indexes 508 existing serialized specializations/409 pipeline
keys. It does not establish whole-game variant coverage or PSO success. The old
GPU host also lacks the nine imports; source resolver parity is byte-verified,
not an execution test of the production CPU. These contracts remain pending.
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
48.492 versus 48.623 FPS shows no measured improvement. Nine imports still need
classification. See docs/avplayer-bridge.zh-CN.md for qualifications; normal manual
QA packaging remains on the earlier CPU and does not include this checkpoint.
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
python3 tests/test_manual_qa_policy.py
```

All live test helpers are QA tools. Review their explicit private-root checks and
memory/lock policy before using them; never substitute a formal save or cache directory.
Do not publish a Release from this snapshot until its remaining acceptance gates pass.
