# Texture byte-layout contract investigation

These are authored diagnostic controls and an UNAPPLIED observation-only patch.
They do not change runtime selection, dirty tracking, copies or collection.
The existing-library controls deliberately assert the old wrong-selection behavior;
a passing run is evidence of the bug, not a successful fix.

The Metal control reuses tests/metal/test_texture_expansion.mm and the prepared
private library/dependency stage. Before running, use a NEW private BB_GPU_USER_DIR
inside the isolated prototype state directory, and a private HOME. Never point it
at production or existing QA caches. No game input is required.
The pure predicate controls are classification tests only. See source snapshot
docs/white-model-resource-audit.zh-CN.md and docs/texture-layout-candidate.zh-CN.md
for actual results, source identity, lawful aliases, and GPU-only preservation gates.

GPU-only raw/coverage controls now use the copied GPL CPU address oracle in
tests/metal/tiling_cpu_reference.h. Run tests/test_texture_layout_preservation.py
on the prepared private host for 13 targeted controls, or --full for all 24
roundtrip cases plus nine merge controls. The unmerged padding errors are
intentional defect expectations. Coverage mask generation is test-only and
MUST NOT be installed as a per-pixel CPU production path.
