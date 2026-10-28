// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

enum OptimizationEvidence: String, Sendable {
    case adopted = "Adopted"
    case adoptedExperimental = "Experimental · adopted"
    case bundle = "Experimental · adopted bundle"
    case experimental = "Experimental"
    case memoryRisk = "Higher memory use"

    var experimental: Bool { self != .adopted }
}

struct MetalOptimization: Identifiable, Sendable {
    let id: String
    let title: String
    let note: String
    let defaultOn: Bool
    let evidence: OptimizationEvidence
    var requires: [String] = []
    var requiresMetalFX = false
}

enum MetalOptimizations {
    static var nativeHostAvailable: Bool {
        guard ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 27,
              let root = Bundle.main.resourceURL?.appendingPathComponent("bbport"),
              let data = try? Data(contentsOf: root.appendingPathComponent("NATIVE-METAL.json")),
              let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              value["schema"] as? Int == 1, value["default_enabled"] as? Bool == true,
              ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= (value["minimum_os_major"] as? Int ?? 27) else { return false }
        return FileManager.default.isExecutableFile(atPath: root.appendingPathComponent("bin/gpu-metal-arm64/bb-gpu").path)
    }
    static let all: [MetalOptimization] = {
        let legacy: [MetalOptimization] = [
        .init(id: "BB_MTL_TWO_LEVEL_BDA", title: "Two-level GPU address table", note: "Saves about 0.9 GiB versus the flat table. Switching selects compatible shader variants and retains existing caches.", defaultOn: true, evidence: .adopted),
        .init(id: "BB_MTL_CLEAR_DISCARD", title: "Skip loading attachments before a full clear", note: "Keeps the clear result while avoiding an unnecessary previous-content load.", defaultOn: true, evidence: .adoptedExperimental),
        .init(id: "BB_MTL_OVERWRITE_DISCARD", title: "Skip loading fully overwritten copy targets", note: "Only discards previous contents when the copy replaces the complete target.", defaultOn: true, evidence: .adoptedExperimental),
        .init(id: "BB_MTL_PROXY_CLEAR", title: "Clear the existing scene copy directly", note: "Avoids a native-resolution round trip. About 5% faster in the earlier matched scene test.", defaultOn: true, evidence: .adopted),
        .init(id: "BB_MTL_VERTEX_MATH", title: "Inline strict vertex math", note: "Retains explicit FMA and rounding rules. Adopted after correctness checks and scene comparisons.", defaultOn: true, evidence: .adoptedExperimental),
        .init(id: "BB_MTL_PROXY_LEAN", title: "Preserve scene-texture compression eligibility", note: "Avoids unused format-view capabilities; real format reinterpretation retains its original path.", defaultOn: true, evidence: .adoptedExperimental),
        .init(id: "BB_MTL_UPSCALE_ALPHA", title: "Merge MetalFX alpha upscaling", note: "Removes a separate alpha enlargement copy on the MetalFX path without changing output resolution.", defaultOn: true, evidence: .adoptedExperimental, requiresMetalFX: true),
        .init(id: "BB_TEXTURE_INVALID_RECLAIM", title: "Reclaim invalid sampled textures", note: "Reclaims idle CPU-invalid sampled copies after GPU completion. Valid caches and their capacities are retained.", defaultOn: true, evidence: .adopted),
        .init(id: "BB_MTL_IDLE_STAGING_DISCARD", title: "Reclaim idle staging backing", note: "Reclaims only completed, idle backing. Pool capacities and reusable objects are retained.", defaultOn: true, evidence: .adopted),
        .init(id: "BB_MTL_RELEASE_WARM_ARCHIVE", title: "Release the startup compilation archive", note: "Drops the compilation-only object after prewarm while retaining all active pipelines.", defaultOn: true, evidence: .adopted),
        .init(id: "BB_MTL_BATCH_CLEARS", title: "Batch color attachment clears", note: "Combines compatible clears; uncertain cases keep separate operations.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_DIRECT_COPY", title: "Copy directly from the existing scene copy", note: "Avoids materializing an intermediate native texture when the complete copy is equivalent.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_ADAPTIVE_STAGING", title: "Fit GPU staging allocations to requests", note: "Reduces rounding waste in new GPU-only blocks without reducing retained byte budgets.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_TEXTURE_TILING", title: "Write textures directly to tiled game memory", note: "Combines texture reading and PS4 tiling for supported formats; others retain the original path.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_PRECISE_RESIDENCY", title: "Declare precise resource residency", note: "Narrows declarations only for resources whose bindings are known; unknown cases remain conservative.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_COALESCE_BARRIERS", title: "Coalesce compatible resource barriers", note: "Avoids redundant dependency boundaries while retaining required synchronization.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_QUERY_COMPUTE_CHAIN", title: "Keep occlusion query compute work together", note: "Reduces compatible compute encoder transitions while retaining query ordering.", defaultOn: true, evidence: .bundle),
        .init(id: "BB_MTL_PRECISE_COMPUTE", title: "Use precise residency for direct compute", note: "Applies only to verified direct bindings; pointer-based and unknown workloads retain full coverage.", defaultOn: true, evidence: .bundle, requires: ["BB_MTL_PRECISE_RESIDENCY"]),
        .init(id: "BB_MTL_TEXTURE_TILING_HALF", title: "Tile directly from half-size scene copies", note: "Uses the same nearest resolve and tiling result, avoiding an intermediate full-size copy.", defaultOn: true, evidence: .bundle, requires: ["BB_MTL_TEXTURE_TILING"]),
        .init(id: "BB_MTL_FOLD_CLEARS", title: "Fold compatible pending clears", note: "Adopted by choice despite noisy isolated FPS; the safe full-surface fallback remains.", defaultOn: true, evidence: .bundle, requires: ["BB_MTL_BATCH_CLEARS"]),
        .init(id: "BB_MTL_CONTINUE_CLEARS", title: "Continue the pass after a consumed clear", note: "Retains attachment and feedback guards. About 2% faster in one matched chalice comparison; no extra cache.", defaultOn: true, evidence: .adoptedExperimental),
        .init(id: "BB_MTL_DEPTH_LAYOUT", title: "Continue passes across depth/stencil layout changes", note: "Only merges compatible read-only-to-write transitions. Keeps feedback, query and synchronization guards. Performance benefit is unconfirmed.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_LIFETIME_HEAP_HISTORY_PRIORITY", title: "Preserve heap release history", note: "Prioritizes heap release records within the existing capacity. M4 tests found no confirmed performance or net memory benefit.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_NATIVE_CLEAR_BATCH", title: "Batch native-resolution color clears", note: "Combines compatible clears. Two comparison orders showed no repeatable FPS benefit; no new GPU resources.", defaultOn: false, evidence: .experimental, requires: ["BB_MTL_BATCH_CLEARS"]),
        .init(id: "BB_MTL_MERGE_OVERLAY", title: "Merge presentation and overlay passes", note: "Pixel comparison passed; gameplay benefit is unconfirmed. One comparison measured higher memory, with no new GPU allocations.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_FILL_UPDATES", title: "Fill repeated small parameter values natively", note: "Preserves every write. No FPS gain in the M4 comparison; takes priority over compute and in-pass updates for repeated-byte values.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_LAYER_USE_CACHE", title: "Memoize conservative residency bookkeeping", note: "Keeps full resource ranges and invalidates on demotion. Correctness checks passed; the earlier M4 comparison was 3.3% slower.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_COPY_PAIR", title: "Merge identical physical copy stores", note: "Skips the second store only when both resolved pointers match. Correctness checks passed; the earlier M4 comparison was 1% slower.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_RENDER_UPDATES", title: "Write small parameters within the render pass", note: "Retains all writes and barriers; unknown fragment conflicts fall back. The earlier M4 comparison was 8.6% slower; adds a small host pipeline.", defaultOn: false, evidence: .experimental, requires: ["BB_MTL_INLINE_UPDATES"]),
        .init(id: "BB_MTL_HOT_STAGING_REUSE", title: "Reuse recent DeviceLocal staging blocks", note: "Tries recently used DeviceLocal staging blocks first. Capacity and completion fences are unchanged; performance benefit is unconfirmed.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_GATHER_REUSE", title: "Reuse cross-chunk reads within one draw", note: "Reuses complete or contained read snapshots within one draw or dispatch. Writes, mapping changes and ring wrap invalidate them. No extra GPU buffers; FPS benefit is not established.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_FRAGMENT_UNIFORMS", title: "Specialize fragment parameter reads", note: "Correctness tested, but repeatable gameplay benefit is unconfirmed; may increase compilation or resource overhead.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_LIBRARY_REUSE", title: "Reuse identical Metal libraries", note: "Reuses identical libraries within the running renderer. Separate from automatic disk preparation; benefit is unconfirmed.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_INLINE_UPDATES", title: "Write small parameters with a compute kernel", note: "Removes tiny buffer copies, but adds compute transitions. No repeatable FPS gain; memory was about 37 MiB higher in one comparison.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_RELAXED_MATH", title: "Use relaxed fragment and compute math", note: "May change floating-point results; benefit is unconfirmed. Uses separate compatible shader variants.", defaultOn: false, evidence: .experimental),
        .init(id: "BB_MTL_PERSIST_WARM_ARCHIVE", title: "Save and load binary pipeline archives", note: "Earlier archive loading used about 0.6 GiB more memory. Off retains files and does not disable automatic local preparation.", defaultOn: false, evidence: .memoryRisk),
        ]
        guard nativeHostAvailable else { return legacy }
        return [.init(id: "BB_METAL_ARM64", title: "ARM64 renderer host", note: "Runs the renderer in a native process; the game still uses Rosetta. About 6% faster in the matched M4 test.", defaultOn: true, evidence: .adoptedExperimental)] + legacy
    }()

    static var adopted: [MetalOptimization] { all.filter { $0.defaultOn } }
    static var optionalExperiments: [MetalOptimization] { all.filter { !$0.defaultOn } }
    static var defaults: [String: Bool] { Dictionary(uniqueKeysWithValues: all.map { ($0.id, $0.defaultOn) }) }

    /// Runtime requirements are read-only capabilities, independent of saved preferences.
    static func visibleOptions(_ options: [MetalOptimization], runtime: LauncherRuntime) -> [MetalOptimization] {
        options.filter { option in
            if option.id == "BB_METAL_ARM64", runtime.requiresNativeMetalHost { return false }
            if option.id == "BB_MTL_HOT_STAGING_REUSE", !runtime.supportsRecentStagingReuse { return false }
            return true
        }
    }

    /// Restoring visible defaults must not rewrite a hidden legacy engine preference.
    static func visibleDefaults(runtime: LauncherRuntime, preserving existing: [String: Bool]) -> [String: Bool] {
        var restored = Dictionary(uniqueKeysWithValues: visibleOptions(all, runtime: runtime).map { ($0.id, $0.defaultOn) })
        let hidden = ["BB_METAL_ARM64": runtime.requiresNativeMetalHost,
                      "BB_MTL_HOT_STAGING_REUSE": !runtime.supportsRecentStagingReuse]
        for (key, isHidden) in hidden where isHidden {
            if let oldChoice = existing[key] { restored[key] = oldChoice }
        }
        return restored
    }

    static func requested(_ option: MetalOptimization, prefs: LauncherPrefs) -> Bool {
        prefs.metalOptimizations[option.id] ?? option.defaultOn
    }
    static func dependenciesMet(_ option: MetalOptimization, prefs: LauncherPrefs, metalFXActive: Bool = true) -> Bool {
        if option.requiresMetalFX && !metalFXActive { return false }
        return option.requires.allSatisfy { id in
            guard let parent = all.first(where: { $0.id == id }) else { return false }
            return enabled(parent, prefs: prefs, metalFXActive: metalFXActive)
        }
    }
    static func enabled(_ option: MetalOptimization, prefs: LauncherPrefs, metalFXActive: Bool = true) -> Bool {
        requested(option, prefs: prefs) && dependenciesMet(option, prefs: prefs, metalFXActive: metalFXActive)
    }
    static func environment(prefs: LauncherPrefs, metalFXActive: Bool = true) -> [String: String] {
        guard prefs.renderer == "metal" else { return [:] }
        var env = Dictionary(uniqueKeysWithValues: all.map { ($0.id, enabled($0, prefs: prefs, metalFXActive: metalFXActive) ? "1" : "0") })
        if env["BB_MTL_PROXY_LEAN"] == "1" { env["BB_MTL_FULL_USAGE"] = "0" }
        if env["BB_MTL_RELAXED_MATH"] == "1" { env["BB_MTL_STRICT_FLOAT"] = "0" }
        return env
    }
}
