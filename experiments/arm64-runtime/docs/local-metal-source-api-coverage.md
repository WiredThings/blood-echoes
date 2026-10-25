# Metal library API coverage — read-only source inventory

Current M=vendor/metal_api has **18 newLibraryWithSource sites + 1 newLibraryWithData site**
in .mm/.h/.cpp source. This is a static inventory, not a per-process call count. Only the game
module BuildLibrary route in mtl_shader.mm currently enters LocalAot. 538/538 module hits do
not mean the process issued zero source-library API calls or compiled zero final PSOs.

All renderer MTLCompileOptions sites were checked for preprocessorMacros: no API override was
found. Embedded source #defines/template substitutions remain part of the actual source bytes;
function constants below are a distinct function/PSO specialization stage, not preprocessor macros.
Actual SDK MTLLibrary.h says mathMode defaults depend on language version; fp32-functions default
is Fast; preprocessorMacros default nil. Defaults below are deliberately marked as defaults,
not rewritten or assumed Safe. SDK: Xcode.app/.../Metal.framework/Headers/MTLLibrary.h:314–344.

| Call site | Purpose / entry | Language | Math / FP32 | LocalAot coverage / qualification |
| --- | --- | --- | --- | --- |
| renderer_metal/mtl_shader.mm:958 | Game translated effective MSL; parsed entry, normally main0; rewrites applied first | explicit3.1 | Safe or Relaxed explicit; Fast explicit on macOS15+; legacy fastMathEnabled=NO below15 | **Yes**, Try then source fallback. Scope is module library, not final PSO. |
| renderer_metal/mtl_local_aot.h:102 | admitted .metallib data, request entry | receipt metal3.1 | receipt safe/relaxed + fast | **Yes**, sole binary-data site. Current helper requires actual newFunction success before admission. |
| renderer_metal/mtl_guest_memory.mm:58 | guest-memory self-test probe | options:nil/default | defaults; fp32Fast on current API | No. Its source compilation and self-test PSO are outside game-module hit count. |
| renderer_metal/mtl_occlusion.mm:111 | visibility snapshot | unset/default | Safe explicit; fp32Fast default | No. Constructor creates snapshot compute PSO. |
| renderer_metal/mtl_format_convert.mm:239 | rgb_convert / depth_convert | explicit3.1 | Safe explicit; fp32Fast default | No. Source library contains both entries; callers choose by conversion kind. |
| renderer_metal/mtl_pso_cache.mm:1133 | bb_gs_debug fragment | options:nil/default | defaults; fp32Fast on current API | No. **BB_GS_DEBUG-only** path; cannot attribute normal-run source cost here without flag/call evidence. |
| renderer_metal/mtl_upscaler.mm:279 | ui_tri_main, ui_copy_main, ui_depth_copy_main | explicit3.1 | math default dependent on3.1; fp32Fast default | No. Multi-entry UI library. |
| renderer_metal/mtl_geometry.mm:230 | bb_gs_prep / bb_gs_copy_indices | explicit3.1 | math default; fp32Fast default | No. Multi-entry indirect helper library. |
| renderer_metal/mtl_geometry.mm:484 | generated bb_gs_pass passthrough | explicit3.1 | Safe explicit (legacy fastMathEnabled=NO); fp32Fast default | No. Effective source varies with GS output/FS inputs/clip convention. |
| renderer_metal/mtl_presenter.mm:270 | present_main | explicit3.1 | math default; fp32Fast default | No. Fullscreen vertex comes separately from HostKernels. |
| renderer_metal/mtl_scene_targets.mm:238 | scene_tri, scene_copy_float/uint/sint/depth/depth_stencil, scene_upscale_alpha | explicit3.1 | math default; fp32Fast default | No. Multi-entry scene proxy library; alpha helper is conditional/lazy. |
| renderer_metal/mtl_runtime_kernels.mm:102 | HostShaders::Shader.entry | explicit3.1 | Safe explicit; fp32Fast default | No. Includes fault parser, detiling/tiling/fullscreen/MSAA host shaders. **Function constants** are applied later in Function(). |
| renderer_metal/mtl_runtime_kernels.mm:198 | generated texture-tiling main0 | explicit3.1 | Safe explicit; fp32Fast default | No. Function constants include original spec IDs plus scale12 and bounds13. |
| renderer_metal/mtl_runtime_kernels.mm:236 | inline_update | explicit3.1 | Safe explicit; fp32Fast default | No. Optional/lazy compute update. |
| renderer_metal/mtl_runtime_kernels.mm:249 | update vertex | explicit3.1 | Safe explicit; fp32Fast default | No. Optional/lazy render inline update, rasterization disabled. |
| renderer_metal/mtl_runtime_kernels.mm:263 | fill_u32 | explicit3.1 | Safe explicit; fp32Fast default | No. Lazy fill helper plus its own final compute PSO. |
| renderer_metal/mtl_velocity_mirror.mm:73 | merge_velocity | unset/default | Safe explicit; fp32Fast default | No. Constructor returns early when velocity mirror disabled. |
| third_party/imgui/backends/imgui_impl_metal.mm:628 | vertex_main / fragment_main | options:nil/default | defaults | No. CMake explicitly includes this backend in bbgpu on Apple. |
| third_party/imgui/backends/imgui_impl_metal4.mm:686 | vertex_main / fragment_main | options:nil/default | defaults | No. Present in source; no current CMake reference found. Source inventory does not claim it is linked or called. |

## Future common wrapper boundaries (proposal only)

A bulk wrapper should preserve each caller's effective compile options instead of forcing every
helper into the current game-module recipe. Current recipe-v1 encodes only language3.1,
safe|relaxed, fp32Fast and no option macros. An unset/default language or mathMode cannot safely
be relabeled as that recipe. Resolve/encode the actual options, or keep the original source route;
unsupported option profiles must not silently change rendering to obtain an AOT hit.

Use a library loader plus a caller-supplied **function factory**. Ordinary game modules use plain
newFunction(entry); HostKernels/texture tiling must pass their actual function-constant values;
multi-entry libraries request and validate each used entry independently. A base library need
not be compiled repeatedly per constant set: function/PSO specialization still needs complete
constant identity and real function creation. Never share per-module functions or binding metadata
just because source/library identity matches. Memo entries remain library-only, with a new function
request per consuming module. Do not claim distinct Objective-C addresses are guaranteed: Metal
may intern objects; module binding identities must remain separate regardless.

The bulk wrapper is deliberately not patched into these call sites during the active comparison.
Admission/publication/recovery changes and a future correctly keyed helper wrapper can be batched
into one candidate build to avoid repeatedly changing the full renderer identity/cohort and
repreparing the existing428 artifacts. Existing cohorts/artifacts should remain intact; this
proposal does not weaken compatibility identities or import receipts across cohorts.

root's active-run evidence remains distinct: 428 requests compiled locally, 538 module AOT hits,
520 final PSO creations and first-IR final-PSO cumulative time about5.84s were reported to this
review; root also supplied the863ms source-library ledger value. This review did not rerun or
reparse those traces and does not equate a library-scope time with all process source API calls.
The code inventory explains why module hit coverage cannot cover every built-in helper. Offline
Metal IR has **not eliminated final GPU pipeline compilation**; none of these counts establish
that overall hitches or white-model symptoms are fixed. Warm-state measurement is root's ongoing
work and is not replaced by this static coverage review.
