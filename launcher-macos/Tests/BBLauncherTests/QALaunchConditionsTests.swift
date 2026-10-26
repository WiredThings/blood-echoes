import Darwin
import Foundation
import XCTest
#if !QA_RECEIPT_STANDALONE
@testable import BBLauncher
#endif

final class QALaunchConditionsTests: XCTestCase {
    private let project = URL(fileURLWithPath: "/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype")
    private var root: URL!, app: URL!, repo: URL!, receipt: URL!
    private var info: [String: Any] = [:]
    private var environment: [String: String] = [:]
    private let args = ["tools/macos/run_optimized.sh"]

    override func setUpWithError() throws {
        let suffix = String(UInt64(Date().timeIntervalSince1970 * 1_000_000_000))
        root = project.appendingPathComponent("state/gui-root-" + suffix)
        app = project.appendingPathComponent("apps/QA Launch Receipt Controls " + suffix + ".app")
        repo = app.appendingPathComponent("Contents/Resources/bbport")
        let data = root.appendingPathComponent("optimized-launcher")
        receipt = data.appendingPathComponent("out/launch-conditions.json")
        try FileManager.default.createDirectory(at: receipt.deletingLastPathComponent(), withIntermediateDirectories: true)
        try file("authored launcher", at: app.appendingPathComponent("Contents/MacOS/bbport"))
        for name in ["run.sh", "tools/macos/run_optimized.sh", "SOURCE-PARITY.json"] {
            try file("authored " + name, at: repo.appendingPathComponent(name))
        }
        try file("authored manifest", at: repo.deletingLastPathComponent().appendingPathComponent("ARM64-QA-MANIFEST.json"))
        environment = ["BB_ONLINE": "0", "BB_DATA_DIR": data.path, "BB_ARM_PAYLOAD_GATE": "1",
                       "BB_ARM_RAW_PREWRITE": "1", "BB_LAYER_READ_TRAPS": "1", "BB_GPU_LIFETIME": "1",
                       "MTL_DEBUG_LAYER": "0", "HOME": "/must/not/be/exported", "INHERITED_SECRET": "must-not-export"]
        for key in ["BB_CONFIG", "BB_MODS_CONFIG", "BB_PATCHES_CONFIG"] {
            let path = data.appendingPathComponent(key + ".fixture")
            try file("authored " + key, at: path); environment[key] = path.path
        }
        info = ["CFBundleIdentifier": "io.github.bbport.optimized.options-qa.arm64.gui." + suffix,
                "CFBundleExecutable": "bbport", "CFBundlePackageType": "APPL",
                "BBPreviewDataPath": root.path, "BBQALaunchReceipt": receipt.path]
    }

    override func tearDownWithError() throws {
        if let root { try FileManager.default.removeItem(at: root) }
        if let app { try FileManager.default.removeItem(at: app) }
    }

    private func file(_ text: String, at url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }

    private func record(_ change: [String: String] = [:], args: [String]? = nil, repository: URL? = nil) throws -> String? {
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Contents/Info.plist"))
        return QALaunchConditions.record(bundle: try XCTUnwrap(Bundle(url: app)), arguments: args ?? self.args,
                                        repository: repository ?? repo, environment: environment.merging(change) { _, value in value })
    }

    func testRecordsOriginalSelectedEnvironmentAndIdentity() throws {
        let before = environment
        XCTAssertTrue(try record()?.contains("written from original Swift") == true)
        let result = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: receipt)) as? [String: Any])
        let env = try XCTUnwrap(result["environment"] as? [String: String])
        XCTAssertEqual(env, environment.filter { $0.key.hasPrefix("BB_") || $0.key.hasPrefix("MTL_") })
        XCTAssertEqual(environment, before)
        XCTAssertEqual(result["producer"] as? String, "original-swift-LauncherModel.start-final-environment")
        XCTAssertEqual(result["arguments"] as? [String], args)
        XCTAssertEqual(result["working_directory"] as? String, repo.path)
        XCTAssertEqual((result["identity_sha256"] as? [String: String])?.count, 8)
        XCTAssertFalse(String(decoding: try Data(contentsOf: receipt), as: UTF8.self).contains("must-not-export"))
    }

    func testNormalBundleCannotWriteEvenWhenOptedIn() throws {
        info["CFBundleIdentifier"] = "io.github.bbport.optimized"
        XCTAssertNil(try record()); XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
    }

    func testNoOptInDoesNotWrite() throws {
        info.removeValue(forKey: "BBQALaunchReceipt")
        XCTAssertNil(try record())
        XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
    }

    func testInvalidSuffixDoesNotWrite() throws {
        info["CFBundleIdentifier"] = "io.github.bbport.optimized.options-qa.arm64.gui.not-digits"
        XCTAssertNil(try record()); XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
    }

    func testOnlineAndCredentialCandidatesRefuseWithoutLoggingValues() throws {
        XCTAssertTrue(try record(["BB_ONLINE": "1"])?.contains("offline QA") == true)
        for word in ["TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH", "BEARER", "PRIVATE_KEY", "PAIRING_CODE"] {
            let message = try record(["BB_TEST_" + word: "private-value-must-not-appear"])
            XCTAssertTrue(message?.contains("credential-like key") == true, word)
            XCTAssertFalse(message?.contains("private-value-must-not-appear") == true)
            XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
        }
    }

    func testWrongDataDirectoryRefuses() throws {
        XCTAssertTrue(try record(["BB_DATA_DIR": root.path])?.contains("wrong QA data identity") == true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
    }

    func testWrongReceiptDirectoryRefuses() throws {
        info["BBQALaunchReceipt"] = root.appendingPathComponent("escape.json").path
        XCTAssertTrue(try record()?.contains("receipt must be") == true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("escape.json").path))
    }

    func testSymlinkParentRefuses() throws {
        let out = receipt.deletingLastPathComponent(), other = root.appendingPathComponent("other")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: false)
        try FileManager.default.removeItem(at: out)
        try FileManager.default.createSymbolicLink(at: out, withDestinationURL: other)
        XCTAssertTrue(try record()?.contains("noncanonical") == true)
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: other.path).isEmpty)
    }

    func testLinkedExistingReceiptIsNotFollowedOrReplaced() throws {
        let other = root.appendingPathComponent("original")
        try file("preserved", at: other)
        try FileManager.default.createSymbolicLink(at: receipt, withDestinationURL: other)
        XCTAssertTrue(try record()?.contains("owned plain file") == true)
        XCTAssertEqual(try String(contentsOf: other), "preserved")
        XCTAssertEqual(try FileManager.default.destinationOfSymbolicLink(atPath: receipt.path), other.path)
    }

    func testHardLinkedReceiptRefuses() throws {
        let other = root.appendingPathComponent("original")
        try file("preserved", at: other)
        try FileManager.default.linkItem(at: other, to: receipt)
        XCTAssertTrue(try record()?.contains("owned plain file") == true)
        XCTAssertEqual(try String(contentsOf: receipt), "preserved")
    }

    func testOwnedReceiptIsAtomicallyReplacedWithPrivatePermissions() throws {
        try file("old", at: receipt)
        XCTAssertTrue(try record()?.contains("written") == true)
        let first = try Data(contentsOf: receipt)
        XCTAssertTrue(try record()?.contains("written") == true)
        XCTAssertNotEqual(first, try Data(contentsOf: receipt))
        let attributes = try FileManager.default.attributesOfItem(atPath: receipt.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: receipt.deletingLastPathComponent().path), [receipt.lastPathComponent])
    }

    func testWrongRepositoryOrEntryRefuses() throws {
        XCTAssertTrue(try record(repository: root)?.contains("wrong packaged repository") == true)
        XCTAssertTrue(try record(args: ["tools/macos/run_optimized.sh", "--benchmark"])?.contains("unexpected original game entry") == true)
        XCTAssertFalse(FileManager.default.fileExists(atPath: receipt.path))
    }

    func testIdentityLinkAndEscapedConfigRefuse() throws {
        XCTAssertTrue(try record(["BB_CONFIG": root.appendingPathComponent("other").path])?.contains("configuration outside") == true)
        let script = repo.appendingPathComponent("run.sh"), other = repo.appendingPathComponent("other")
        try file("other", at: other); try FileManager.default.removeItem(at: script)
        try FileManager.default.createSymbolicLink(at: script, withDestinationURL: other)
        XCTAssertTrue(try record()?.contains("noncanonical") == true)
    }

    func testBoundedJSONRefusalPreservesExistingReceipt() throws {
        try file("existing-good-record", at: receipt)
        XCTAssertTrue(try record(["BB_AUTHORED_LARGE": String(repeating: "x", count: 300_000)])?.contains("credential-like") == true)
        XCTAssertTrue(try record(["BB_LARGE": String(repeating: "x", count: 300_000)])?.contains("bounded receipt") == true)
        XCTAssertEqual(try String(contentsOf: receipt), "existing-good-record")
    }
}
