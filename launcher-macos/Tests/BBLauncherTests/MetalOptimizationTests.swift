import XCTest
@testable import BBLauncher

final class MetalOptimizationTests: XCTestCase {
    func testInvariantVertexChoiceIsOffAndDoesNotAddAnUnchangedCompileOption() {
        let option = MetalOptimizations.all.first { $0.id == "BB_MTL_RELAX_VERTEX" }
        XCTAssertEqual(option?.defaultOn, false)
        XCTAssertEqual(option?.evidence, .experimental)
        var prefs = LauncherPrefs()
        XCTAssertNil(MetalOptimizations.environment(prefs: prefs)["BB_MTL_RELAX_VERTEX"])
        prefs.extraEnv = "BB_MTL_RELAX_VERTEX=1"
        prefs.metalOptimizations["BB_MTL_RELAX_VERTEX"] = false
        let off = OptimizedProfile(prefs: prefs, game: GameSettings())
            .applyingEnvironment(to: ["BB_MTL_RELAX_VERTEX": "1"])
        XCTAssertNil(off["BB_MTL_RELAX_VERTEX"])
        prefs.metalOptimizations["BB_MTL_RELAX_VERTEX"] = true
        XCTAssertEqual(MetalOptimizations.environment(prefs: prefs)["BB_MTL_RELAX_VERTEX"], "1")
    }

    func testRequiredNativeHostIsNotAnOptionalFexToggle() {
        let nativeHost = MetalOptimization(id: "BB_METAL_ARM64", title: "ARM64 renderer host", note: "Legacy optional host", defaultOn: true, evidence: .adoptedExperimental)
        let candidates = [nativeHost] + MetalOptimizations.all.filter { $0.id != nativeHost.id }
        let fex = LauncherRuntime(isFexArm64: true, automaticMetalPreparation: true, supportsRecentStagingReuse: true)
        XCTAssertTrue(MetalOptimizations.visibleOptions(candidates, runtime: .legacy).contains { $0.id == nativeHost.id })
        let visible = MetalOptimizations.visibleOptions(candidates, runtime: fex)
        XCTAssertFalse(visible.contains { $0.id == nativeHost.id })
        XCTAssertEqual(visible.map(\.id), candidates.filter { $0.id != nativeHost.id }.map(\.id))
    }

    func testRestoringVisibleDefaultsPreservesHiddenLegacyHostChoice() {
        let fex = LauncherRuntime(isFexArm64: true, automaticMetalPreparation: true)
        let existing = ["BB_METAL_ARM64": false, "BB_MTL_TWO_LEVEL_BDA": false, "BB_MTL_HOT_STAGING_REUSE": true]
        let restored = MetalOptimizations.visibleDefaults(runtime: fex, preserving: existing)
        XCTAssertEqual(restored["BB_METAL_ARM64"], false)
        XCTAssertEqual(restored["BB_MTL_TWO_LEVEL_BDA"], true)
        XCTAssertEqual(restored["BB_MTL_HOT_STAGING_REUSE"], true)
        XCTAssertNil(MetalOptimizations.visibleDefaults(runtime: fex, preserving: [:])["BB_METAL_ARM64"])
        let legacyDefaults = MetalOptimizations.visibleDefaults(runtime: .legacy, preserving: existing)
        XCTAssertEqual(legacyDefaults["BB_MTL_HOT_STAGING_REUSE"], true)
        XCTAssertEqual(legacyDefaults["BB_MTL_TWO_LEVEL_BDA"], true)
        XCTAssertNil(MetalOptimizations.visibleDefaults(runtime: .legacy, preserving: [:])["BB_MTL_HOT_STAGING_REUSE"])
    }

    func testRecentStagingImportsDeveloperChoiceAndSavedSwitchWins() throws {
        let name = "bbport.recent-staging.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertEqual(MetalOptimizations.defaults["BB_MTL_HOT_STAGING_REUSE"], false)
        defaults.set(["BB_MTL_DIRECT_COPY": true], forKey: "metalOptimizations")
        defaults.set("BB_MTL_HOT_STAGING_REUSE=1", forKey: "extraEnv")
        let runtime = LauncherRuntime(supportsRecentStagingReuse: true)
        var prefs = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_HOT_STAGING_REUSE"], true)
        var profile = OptimizedProfile(prefs: prefs, game: GameSettings(), runtime: runtime)
        XCTAssertEqual(profile.applyingEnvironment(to: [:])["BB_MTL_HOT_STAGING_REUSE"], "1")
        prefs.metalOptimizations["BB_MTL_HOT_STAGING_REUSE"] = false
        prefs.save(to: defaults)
        prefs = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_HOT_STAGING_REUSE"], false)
        profile = OptimizedProfile(prefs: prefs, game: GameSettings(), runtime: runtime)
        XCTAssertEqual(profile.applyingEnvironment(to: ["BB_MTL_HOT_STAGING_REUSE": "1"])["BB_MTL_HOT_STAGING_REUSE"], "0")
        XCTAssertEqual(prefs.extraEnv, "BB_MTL_HOT_STAGING_REUSE=1")
    }

    func testDefaultsMatchAcceptedProfileAndExperimentsStayOff() {
        let optionalHost = MetalOptimizations.nativeHostAvailable ? 1 : 0
        XCTAssertEqual(MetalOptimizations.all.count, 37 + optionalHost)
        XCTAssertEqual(Set(MetalOptimizations.all.map(\.id)).count, 37 + optionalHost)
        XCTAssertEqual(MetalOptimizations.adopted.count, 21 + optionalHost)
        XCTAssertEqual(MetalOptimizations.optionalExperiments.count, 16)
        let prefs = LauncherPrefs()
        var game = GameSettings(); game.upscaler = "metalfx"
        let env = OptimizedProfile(prefs: prefs, game: game).environment
        for option in MetalOptimizations.visibleOptions(MetalOptimizations.all, runtime: .legacy) {
            let expected: String? = option.id == "BB_MTL_RELAX_VERTEX" ? nil : (option.defaultOn ? "1" : "0")
            XCTAssertEqual(env[option.id], expected, option.id)
            XCTAssertEqual(option.defaultOn, [.adopted, .adoptedExperimental, .bundle].contains(option.evidence))
            for parent in option.requires { XCTAssertTrue(MetalOptimizations.all.contains { $0.id == parent }) }
        }
        XCTAssertEqual(env["BB_MTL_PERSIST_WARM_ARCHIVE"], "0")
        XCTAssertNil(env["BB_MTL_HOT_STAGING_REUSE"])
        XCTAssertFalse(MetalOptimizations.all.contains { $0.id.contains("OFFLINE") })
    }

    func testEveryVisibleToggleReachesTheProfileEnvironment() {
        let runtimes = [LauncherRuntime.legacy,
                        LauncherRuntime(isFexArm64: true, automaticMetalPreparation: true, supportsRecentStagingReuse: true),
                        LauncherRuntime(supportsRecentStagingReuse: true)]
        for runtime in runtimes {
            for option in MetalOptimizations.visibleOptions(MetalOptimizations.all, runtime: runtime) {
                var prefs = LauncherPrefs()
                prefs.metalOptimizations[option.id] = !option.defaultOn
                var game = GameSettings(); game.upscaler = "metalfx"
                for parent in option.requires { prefs.metalOptimizations[parent] = true }
                let env = OptimizedProfile(prefs: prefs, game: game, runtime: runtime).environment
                XCTAssertEqual(env[option.id], option.defaultOn ? "0" : "1", option.id)
            }
        }
    }

    func testRecentStagingVisibilityRequiresDeclaredFeatureNotArchitecture() {
        let options = MetalOptimizations.all
        for runtime in [LauncherRuntime.legacy, LauncherRuntime(isFexArm64: true)] {
            XCTAssertFalse(MetalOptimizations.visibleOptions(options, runtime: runtime).contains { $0.id == "BB_MTL_HOT_STAGING_REUSE" })
        }
        for runtime in [LauncherRuntime(supportsRecentStagingReuse: true), LauncherRuntime(isFexArm64: true, supportsRecentStagingReuse: true)] {
            XCTAssertTrue(MetalOptimizations.visibleOptions(options, runtime: runtime).contains { $0.id == "BB_MTL_HOT_STAGING_REUSE" })
        }
    }

    func testDependenciesDisableRuntimeFlagWithoutLosingPreference() {
        for option in MetalOptimizations.all where !option.requires.isEmpty {
            for parent in option.requires {
                var prefs = LauncherPrefs()
                prefs.metalOptimizations[option.id] = true
                prefs.metalOptimizations[parent] = false
                XCTAssertEqual(MetalOptimizations.environment(prefs: prefs)[option.id], "0")
                XCTAssertTrue(prefs.metalOptimizations[option.id]!)
                prefs.metalOptimizations[parent] = true
                XCTAssertEqual(MetalOptimizations.environment(prefs: prefs)[option.id], "1")
            }
        }
    }

    func testPersistenceAndResetDoNotChangeQualityOrReimportOldOverrides() throws {
        let name = "bbport.metal-options.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        var prefs = LauncherPrefs()
        prefs.extraEnv = "BB_MTL_DIRECT_COPY=0"
        prefs.resolution = "2560x1440"
        prefs.metalOptimizations = MetalOptimizations.defaults.mapValues { !$0 }
        prefs.save(to: defaults)
        XCTAssertEqual(LauncherPrefs.load(from: defaults), prefs)
        var game = GameSettings(); game.upscaler = "metalfx"; game.preset = 2
        let quality = OptimizedProfile(prefs: prefs, game: game).settings
        prefs.metalOptimizations = MetalOptimizations.defaults
        prefs.save(to: defaults)
        let restored = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(restored.resolution, "2560x1440")
        XCTAssertEqual(restored.metalOptimizations["BB_MTL_DIRECT_COPY"], true)
        XCTAssertEqual(OptimizedProfile(prefs: restored, game: game).settings, quality)
    }

    func testLegacyDeveloperOverridesMigrateOnceAndControlsWin() throws {
        let name = "bbport.metal-options.legacy.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set("BB_MTL_DIRECT_COPY=0 BB_MTL_INLINE_UPDATES=1 BB_OTHER=1", forKey: "extraEnv")
        var prefs = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(prefs.metalOptimizations, ["BB_MTL_DIRECT_COPY":false, "BB_MTL_INLINE_UPDATES":true])
        prefs.metalOptimizations["BB_MTL_DIRECT_COPY"] = true
        prefs.save(to: defaults)
        prefs = LauncherPrefs.load(from: defaults)
        let profile = OptimizedProfile(prefs: prefs, game: GameSettings())
        let env = profile.applyingEnvironment(to: ["BB_MTL_DIRECT_COPY":"0", "BB_OTHER":"1"])
        XCTAssertEqual(env["BB_MTL_DIRECT_COPY"], "1")
        XCTAssertEqual(env["BB_OTHER"], "1")
        prefs.renderer = "vulkan"
        let vk = OptimizedProfile(prefs: prefs, game: GameSettings()).applyingEnvironment(to: env)
        for option in MetalOptimizations.all { XCTAssertNil(vk[option.id]) }
        XCTAssertFalse(vk.keys.contains { $0.hasPrefix("BB_MTL_") })
    }

    func testRelaxedModeIsExplicitAndCanOverrideOldStrictDebugFlag() {
        var prefs = LauncherPrefs()
        prefs.metalOptimizations["BB_MTL_RELAXED_MATH"] = true
        let env = OptimizedProfile(prefs: prefs, game: GameSettings()).applyingEnvironment(to: ["BB_MTL_STRICT_FLOAT":"1"])
        XCTAssertEqual(env["BB_MTL_RELAXED_MATH"], "1")
        XCTAssertEqual(env["BB_MTL_STRICT_FLOAT"], "0")
    }

    func testOldDiagnosticSelectorsCannotOverrideVisibleControls() {
        let prefs = LauncherPrefs()
        let profile = OptimizedProfile(prefs: prefs, game: GameSettings())
        let env = profile.applyingEnvironment(to: ["BB_MTL_PROXY_CLEAR_FILE":"/tmp/old-ab-control",
                                                   "BB_MTL_FULL_USAGE":"1"])
        XCTAssertNil(env["BB_MTL_PROXY_CLEAR_FILE"])
        XCTAssertEqual(env["BB_MTL_FULL_USAGE"], "0")
        XCTAssertEqual(env["BB_MTL_PROXY_CLEAR"], "1")
    }

    func testMetalFXDependencyPreservesStoredChoiceAndQuality() {
        var prefs = LauncherPrefs()
        prefs.metalOptimizations["BB_MTL_UPSCALE_ALPHA"] = true
        var game = GameSettings(); game.upscaler = "off"
        XCTAssertEqual(OptimizedProfile(prefs: prefs, game: game).environment["BB_MTL_UPSCALE_ALPHA"], "0")
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_UPSCALE_ALPHA"], true)
        game.upscaler = "metalfx"
        XCTAssertEqual(OptimizedProfile(prefs: prefs, game: game).environment["BB_MTL_UPSCALE_ALPHA"], "1")
        prefs.ultrawide = true
        XCTAssertEqual(OptimizedProfile(prefs: prefs, game: game).environment["BB_MTL_UPSCALE_ALPHA"], "0")
    }

    func testNewReclaimerImportsLegacyOverrideEvenWithOlderControlDictionary() throws {
        let name = "bbport.reclaim-migration.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(["BB_MTL_DIRECT_COPY": true], forKey: "metalOptimizations")
        defaults.set("BB_TEXTURE_INVALID_RECLAIM=0 BB_MTL_DIRECT_COPY=0", forKey: "extraEnv")
        var prefs = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(prefs.metalOptimizations["BB_TEXTURE_INVALID_RECLAIM"], false)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_DIRECT_COPY"], true)
        XCTAssertEqual(OptimizedProfile(prefs: prefs, game: GameSettings()).environment["BB_TEXTURE_INVALID_RECLAIM"], "0")
        prefs.metalOptimizations = MetalOptimizations.defaults
        prefs.save(to: defaults)
        XCTAssertEqual(LauncherPrefs.load(from: defaults).metalOptimizations["BB_TEXTURE_INVALID_RECLAIM"], true)
    }

    func testDiagnosticsAndUnsupportedBackendFlagsCannotOutliveControls() {
        var prefs = LauncherPrefs()
        let stale = ["BB_MTL_GPU_TIMING": "1", "BB_GPU_PROFILE": "1", "BB_PREUPLOAD": "1", "BB_VK_VALIDATION": "1"]
        var env = OptimizedProfile(prefs: prefs, game: GameSettings()).applyingEnvironment(to: stale)
        for key in stale.keys { XCTAssertNil(env[key], key) }
        prefs.gpuProfile = true
        env = OptimizedProfile(prefs: prefs, game: GameSettings()).applyingEnvironment(to: stale)
        XCTAssertEqual(env["BB_MTL_GPU_TIMING"], "1")
        XCTAssertNil(env["BB_GPU_PROFILE"])
        prefs.renderer = "vulkan"
        env = OptimizedProfile(prefs: prefs, game: GameSettings()).applyingEnvironment(to: stale)
        XCTAssertEqual(env["BB_GPU_PROFILE"], "1")
        XCTAssertNil(env["BB_MTL_GPU_TIMING"])
    }

    func testNewExperimentsImportExplicitDeveloperChoicesWithoutOverwritingSavedControls() throws {
        let name = "bbport.restored-options.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set(["BB_MTL_DIRECT_COPY": true, "BB_MTL_COPY_PAIR": false], forKey: "metalOptimizations")
        defaults.set("BB_MTL_NATIVE_CLEAR_BATCH=1 BB_MTL_COPY_PAIR=1 BB_LAYER_USE_CACHE=1 BB_MTL_CONTINUE_CLEARS=0", forKey: "extraEnv")
        let prefs = LauncherPrefs.load(from: defaults)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_NATIVE_CLEAR_BATCH"], true)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_COPY_PAIR"], false)
        XCTAssertEqual(prefs.metalOptimizations["BB_LAYER_USE_CACHE"], true)
        XCTAssertEqual(prefs.metalOptimizations["BB_MTL_CONTINUE_CLEARS"], false)
    }

    func testRestoredCandidatesDefaultOffAndUnsafeAlgorithmsAreNotOptions() {
        for id in ["BB_MTL_NATIVE_CLEAR_BATCH", "BB_MTL_MERGE_OVERLAY", "BB_MTL_FILL_UPDATES",
                   "BB_LAYER_USE_CACHE", "BB_MTL_COPY_PAIR", "BB_MTL_RENDER_UPDATES", "BB_MTL_LIBRARY_REUSE"] {
            XCTAssertEqual(MetalOptimizations.defaults[id], false, id)
        }
        for id in ["BB_COPY_RING_SCOPE", "BB_TEXTURE_RELEASED_STORAGE", "BB_MTL_QUERY_WRITE_EPOCH"] {
            XCTAssertFalse(MetalOptimizations.all.contains { $0.id == id }, id)
        }
        var prefs = LauncherPrefs()
        prefs.metalOptimizations["BB_MTL_RENDER_UPDATES"] = true
        XCTAssertEqual(MetalOptimizations.environment(prefs: prefs)["BB_MTL_RENDER_UPDATES"], "0")
        prefs.metalOptimizations["BB_MTL_INLINE_UPDATES"] = true
        XCTAssertEqual(MetalOptimizations.environment(prefs: prefs)["BB_MTL_RENDER_UPDATES"], "1")
    }

    func testEveryOptionHasEnglishAndChineseDescriptions() {
        for language in ["en", "zh-Hans", "zh-Hant"] {
            let bundle = L10n.bundle(for: language)
            for option in MetalOptimizations.all {
                for key in [option.title, option.note, option.evidence.rawValue] {
                    XCTAssertNotEqual(bundle.localizedString(forKey: key, value: "missing", table: nil), "missing", key)
                }
            }
        }
    }
}
