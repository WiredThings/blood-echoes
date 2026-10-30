import XCTest
@testable import BBLauncher

final class UIModTests: XCTestCase {
    func testStatusDistinguishesAcceptedFolderFromRecoverableInstall() throws {
        let json = """
        {"folderValid":false,"enabled":true,"managed":true,"needsRecovery":false,
         "canEnable":false,"canDisable":true,"blocksPlay":false,
         "message":"Download moved; you can still restore."}
        """
        let status = try JSONDecoder().decode(UIModStatus.self, from: Data(json.utf8))
        XCTAssertTrue(status.canDisable)
        XCTAssertFalse(status.blocksPlay)
        XCTAssertEqual(status.check.level, .warning)
        var accepted = status
        accepted.folderValid = true
        XCTAssertEqual(accepted.check.level, .ok)
        accepted.blocksPlay = true
        XCTAssertEqual(accepted.check.level, .error)
    }

    func testAcceptedPackWithUnsupportedDestinationIsOnlyAWarning() {
        let status = UIModStatus(folderValid: true, canEnable: false, blocksPlay: false,
                                 message: "Missing folder: /game/dvdroot_ps4/menu")
        XCTAssertEqual(status.check.level, .warning)
        XCTAssertTrue(status.check.allowsPlay)
    }

    func testHelperUsesLiteralPathsAndReportsValidationErrors() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let python = try XCTUnwrap(findPython())
        let missing = "/tmp/UI test '; $(touch should-never-run) /missing"
        let helper = UIModHelper(repo: root, python: python, data: URL(fileURLWithPath: missing))
        XCTAssertThrowsError(try helper.run("inspect", game: missing, folder: missing)) { error in
            XCTAssertTrue(error.localizedDescription.contains("No such file") ||
                          error.localizedDescription.contains("does not exist"), error.localizedDescription)
        }
    }
}
