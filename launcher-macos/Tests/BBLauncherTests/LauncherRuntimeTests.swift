import XCTest
@testable import BBLauncher

final class LauncherRuntimeTests: XCTestCase {
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
