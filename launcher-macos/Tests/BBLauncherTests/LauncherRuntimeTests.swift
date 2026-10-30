import XCTest
@testable import BBLauncher

final class LauncherRuntimeTests: XCTestCase {
    func testIndependentCPUSelectionIsOneSupervisorIntent() throws {
        let dual = LauncherRuntime.decode(Data(#"{"schema":1,"game_runtime":"arm64","cpu_backends":["fex","bbcpu"],"local_metal_preparation":"auto"}"#.utf8))
        XCTAssertEqual(dual.cpuBackends, [.fex, .bbcpu])
        XCTAssertTrue(dual.isNativeArm64)
        XCTAssertTrue(dual.requiresNativeMetalHost)
        XCTAssertTrue(dual.automaticMetalPreparation)
        XCTAssertFalse(dual.supports(renderer: "vulkan"))
        let original = ["BB_CONFIG": "/original/config", "BB_USER_DIR": "/original/user", "BB_ONLINE": "1", "BB_CPU_BACKEND": "fex", "BB_RECOMP_LIB": "/stale/other-code.dylib"]
        let launch = dual.launchEnvironment(original, cpuBackend: .bbcpu)
        XCTAssertEqual(launch["BB_CPU_BACKEND"], "bbcpu")
        XCTAssertNil(launch["BB_RECOMP_LIB"])
        XCTAssertEqual(original["BB_CPU_BACKEND"], "fex")
        for key in ["BB_CONFIG", "BB_USER_DIR", "BB_ONLINE"] { XCTAssertEqual(launch[key], original[key]) }
        XCTAssertEqual(dual.launchEnvironment(original, cpuBackend: .fex)["BB_CPU_BACKEND"], "fex")
        XCTAssertEqual(launch["BB_CPU_BACKEND"], "bbcpu", "another Play intent must not mutate an existing supervisor")
        let suite = "BloodEchoes-CPUChoice-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        XCTAssertEqual(LauncherPrefs.load(from: defaults).cpuBackend, .fex)
        var prefs = LauncherPrefs(); prefs.cpuBackend = .bbcpu; prefs.save(to: defaults)
        XCTAssertEqual(LauncherPrefs.load(from: defaults).cpuBackend, .bbcpu)
    }

    func testUnknownDuplicateOrMissingCPUIsNeverAdvertised() {
        for backends in [#"[]"#, #"["bbcpu"]"#, #"["fex","fex"]"#, #"["fex","unknown"]"#] {
            let data = Data("{\"schema\":1,\"game_runtime\":\"arm64\",\"cpu_backends\":\(backends)}".utf8)
            XCTAssertEqual(LauncherRuntime.decode(data), .legacy)
        }
        XCTAssertEqual(LauncherRuntime(isFexArm64: true).cpuBackends, [.fex])
        XCTAssertEqual(LauncherRuntime.legacy.cpuBackends, [])
    }
    func testFexSessionIsEphemeralAndSharedWithOriginalSupervisor() {
        let runtime = LauncherRuntime(isFexArm64: true)
        let old = ["BB_ARM_SESSION_NONCE": String(repeating: "c", count: 32),
                   "BB_ARM_RESTART_NONCE": String(repeating: "d", count: 32),
                   "BB_ARM_GPU_BASE_DIR": "/old/cache", "BB_CONFIG": "/original/bbport.ini",
                   "BB_USER_DIR": "/original/user", "BB_QUICKSAVE": "1", "BB_ONLINE": "1"]
        var calls = 0
        let current = runtime.launchEnvironment(old, nonce: {
            calls += 1
            return String(repeating: calls == 1 ? "a" : "b", count: 32)
        })
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(current["BB_ARM_SESSION_NONCE"], String(repeating: "a", count: 32))
        XCTAssertEqual(current["BB_ARM_RESTART_NONCE"], String(repeating: "b", count: 32))
        XCTAssertEqual(current["BB_ARM_LAUNCHER_COMPAT"], "1")
        XCTAssertNil(current["BB_ARM_GPU_BASE_DIR"])
        for key in ["BB_CONFIG", "BB_USER_DIR", "BB_QUICKSAVE", "BB_ONLINE"] { XCTAssertEqual(current[key], old[key]) }
        XCTAssertEqual(old["BB_ARM_GPU_BASE_DIR"], "/old/cache")
        XCTAssertEqual(LauncherRuntime.legacy.launchEnvironment(old), old)
    }

    func testQASessionRetainsOnlyCompleteValidSessionPair() {
        let runtime = LauncherRuntime(isFexArm64: true)
        let good = ["BB_ARM_SESSION_NONCE": String(repeating: "a", count: 32), "BB_ARM_RESTART_NONCE": String(repeating: "b", count: 32)]
        let result = runtime.launchEnvironment(good, isolatedQA: true, nonce: { XCTFail("QA pair regenerated"); return "" })
        XCTAssertEqual(result["BB_ARM_SESSION_NONCE"], good["BB_ARM_SESSION_NONCE"])
        XCTAssertEqual(result["BB_ARM_RESTART_NONCE"], good["BB_ARM_RESTART_NONCE"])
        for invalid in [String(repeating: "A", count: 32), String(repeating: "a", count: 31), "", "../../bad"] {
            var bad = good; bad["BB_ARM_RESTART_NONCE"] = invalid
            var calls = 0
            let next = runtime.launchEnvironment(bad, isolatedQA: true, nonce: { calls += 1; return String(repeating: "c", count: 32) })
            XCTAssertEqual(calls, 2)
            XCTAssertEqual(next["BB_ARM_SESSION_NONCE"], String(repeating: "c", count: 32))
        }
    }
    func testNativePackageDoesNotRequireRosettaButRejectsWrongArchitecture() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        func file(_ name: String, bytes: Data = Data("authored fixture".utf8)) throws {
            let url = root.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try bytes.write(to: url)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        }
        func macho(_ kind: UInt32, cpu: UInt32 = 0x0100000c) -> Data {
            [UInt32(0xfeedfacf), cpu, 0, kind].reduce(into: Data()) { data, word in
                var value = word.littleEndian
                withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
            }
        }
        try file("tools/macos/run.sh"); try file("tools/macos/run_optimized.sh")
        try file("python/bin/python3")
        try file("NATIVE-METAL.json", bytes: Data("{\"schema\":1,\"game_runtime\":\"fex-arm64\"}".utf8))
        for (path, kind) in [("bin/bb-probe-metal", UInt32(2)), ("bin/gpu-metal/libbbgpu.dylib", 6),
                             ("bin/gpu-metal-arm64/bb-gpu", 2), ("bin/gpu-metal-arm64/libbbgpu.dylib", 6)] {
            try file(path, bytes: macho(kind))
        }
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false).level, .ok)
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "vulkan", rosettaAvailable: false).level, .error)
        try file("NATIVE-METAL.json", bytes: Data(#"{"schema":1,"game_runtime":"arm64","cpu_backends":["fex","bbcpu"]}"#.utf8))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false, cpuBackend: .bbcpu).level, .error)
        try file("bin/bbcpu-fixture", bytes: macho(2))
        try file("BBCPU-COMPONENT.json", bytes: Data(#"{"schema":1,"development_only":true,"backend":"bbcpu","cpu":{"path":"bin/bbcpu-fixture"}}"#.utf8))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false, cpuBackend: .bbcpu).level, .ok)
        try file("bin/bbcpu-fixture", bytes: macho(2, cpu: 0x01000007))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false, cpuBackend: .bbcpu).level, .error)
        try file("BBCPU-COMPONENT.json", bytes: Data(#"{"schema":1,"development_only":true,"backend":"bbcpu","cpu":{"path":"../outside"}}"#.utf8))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false, cpuBackend: .bbcpu).level, .error)
        try file("NATIVE-METAL.json", bytes: Data(#"{"schema":1,"game_runtime":"fex-arm64"}"#.utf8))
        try file("bin/gpu-metal-arm64/bb-gpu", bytes: macho(2, cpu: 0x01000007))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false).level, .error)
        try file("NATIVE-METAL.json", bytes: Data("{\"schema\":1}".utf8))
        XCTAssertEqual(checkOptimizedEngine(root, packaged: true, renderer: "metal", rosettaAvailable: false).level, .error)
    }
    func testRecentStagingRequiresExplicitEngineCapability() {
        let ordinary = LauncherRuntime.decode(Data("{\"schema\":1,\"game_runtime\":\"fex-arm64\"}".utf8))
        XCTAssertFalse(ordinary.supportsRecentStagingReuse)
        let capable = LauncherRuntime.decode(Data("{\"schema\":1,\"metal_features\":[\"recent-staging-reuse\"]}".utf8))
        XCTAssertFalse(capable.isFexArm64)
        XCTAssertTrue(capable.supportsRecentStagingReuse)
        var prefs = LauncherPrefs(); prefs.metalOptimizations["BB_MTL_HOT_STAGING_REUSE"] = true
        XCTAssertNil(OptimizedProfile(prefs: prefs, game: GameSettings(), runtime: ordinary).environment["BB_MTL_HOT_STAGING_REUSE"])
        XCTAssertEqual(OptimizedProfile(prefs: prefs, game: GameSettings(), runtime: capable).environment["BB_MTL_HOT_STAGING_REUSE"], "1")
    }

    func testOldPackagesRemainLegacy() {
        for text in ["{}", "{\"schema\":1,\"renderer_architecture\":\"arm64\"}",
                     "{\"schema\":2,\"game_runtime\":\"fex-arm64\"}"] {
            XCTAssertEqual(LauncherRuntime.decode(Data(text.utf8)), .legacy)
        }
        XCTAssertEqual(LauncherRuntime.load(from: nil), .legacy)
        XCTAssertTrue(LauncherRuntime.legacy.supports(renderer: "vulkan"))
    }

    func testFexRequiredHostWinsWithoutEditingStoredPreferenceOrQuality() {
        let runtime = LauncherRuntime.decode(Data("{\"schema\":1,\"game_runtime\":\"fex-arm64\",\"local_metal_preparation\":\"auto\"}".utf8))
        XCTAssertTrue(runtime.requiresNativeMetalHost)
        XCTAssertTrue(runtime.automaticMetalPreparation)
        XCTAssertTrue(runtime.supports(renderer: "metal"))
        XCTAssertFalse(runtime.supports(renderer: "vulkan"))
        var prefs = LauncherPrefs()
        prefs.metalOptimizations["BB_METAL_ARM64"] = false
        var game = GameSettings(); game.upscaler = "off"; game.preset = 0
        let legacy = OptimizedProfile(prefs: prefs, game: game)
        let profile = OptimizedProfile(prefs: prefs, game: game, runtime: runtime)
        let env = profile.applyingEnvironment(to: ["BB_METAL_ARM64": "0"])
        XCTAssertEqual(env["BB_METAL_ARM64"], "1")
        XCTAssertEqual(prefs.metalOptimizations["BB_METAL_ARM64"], false)
        XCTAssertEqual(profile.settings, legacy.settings)
        XCTAssertEqual(profile.environment["BB_QUICKSAVE"], legacy.environment["BB_QUICKSAVE"])
    }
}
