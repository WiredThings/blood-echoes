// SPDX-License-Identifier: GPL-2.0-or-later
import XCTest
@testable import BBLauncher

final class LaunchPreparationProgressTests: XCTestCase {
    private func phase(_ name: String, _ event: String = "start", _ outcome: String = "returned") -> String {
        "Metal preparation phase: {\"phase\":\"\(name)\",\"event\":\"\(event)\",\"outcome\":\"\(outcome)\"}"
    }
    private let done = "Metal local preparation: {\"status\":\"prepared\"}"
    func testCPUUsesOriginalProgressWithoutPrematureMetalCompletion() {
        var p = LaunchPreparationProgress()
        p.consume("CPU preparation planned: {\"backend\":\"bbcpu\"}")
        p.consume(done); XCTAssertEqual(p.percent, 25)
        p.consume("CPU preparation progress: {\"event\":\"compile-progress\",\"done\":192,\"total\":384,\"active\":2,\"capacity\":2}")
        XCTAssertEqual(p.percent, 60); XCTAssertEqual(p.stageKey, "Compiling CPU code…")
        p.consume("CPU preparation progress: {\"event\":\"compile-progress\",\"done\":200,\"total\":384,\"active\":1,\"capacity\":0}")
        XCTAssertEqual(p.stageKey, "Waiting for compiler memory…")
        p.consume("CPU local preparation: {\"backend\":\"bbcpu\"}"); XCTAssertEqual(p.percent, 100)
        p.consume("ARM GPU frontend ready symbols=327"); XCTAssertTrue(p.finished)
    }
    func testBadCPUProgressDoesNotBypassBounds() {
        var p = LaunchPreparationProgress(); p.consume("CPU preparation planned: {\"backend\":\"bbcpu\"}")
        let before = p
        for text in ["{}", "{\"event\":\"compile-progress\",\"done\":false,\"total\":2,\"active\":0,\"capacity\":0}",
                     "{\"event\":\"compile-progress\",\"done\":3,\"total\":2,\"active\":0,\"capacity\":0}",
                     "{\"event\":\"compile-progress\",\"done\":0,\"total\":384,\"active\":3,\"capacity\":3}"] {
            p.consume("CPU preparation progress: " + text); XCTAssertEqual(p, before)
        }
    }

    func testNestedPhasesNeverReverseOrPrematurelyFinish() {
        var p = LaunchPreparationProgress()
        p.consume(phase("source-view")); XCTAssertEqual(p.percent, 20)
        p.consume(phase("source-view", "end")); XCTAssertEqual(p.percent, 45)
        p.consume(phase("static-catalog", "end")); XCTAssertEqual(p.percent, 75)
        p.consume(phase("complete-pso", "end")); XCTAssertEqual(p.percent, 93)
        p.consume(phase("game-preparation", "end")); XCTAssertEqual(p.percent, 93)
        p.consume(phase("archive-policy", "end")); XCTAssertEqual(p.percent, 93)
        XCTAssertFalse(p.finished)
        p.consume(phase("local-finalize", "end")); XCTAssertEqual(p.percent, 98)
        p.consume(done); XCTAssertEqual(p.percent, 100)
        XCTAssertFalse(p.finished) // Preparation ended; game still starting.
        p.consume("ARM GPU frontend ready symbols=327"); XCTAssertTrue(p.finished)
    }

    func testFailedPhaseCannotTurnIntoSuccessFromLaterOutput() {
        for error in [phase("source-view", "end", "raised"), "Metal local preparation failed: source changed",
                      "Metal local preparation stage failed: damaged library", "Image/link preparation failed: changed output",
                      "Unsupported game files: version 1.00"] {
            var p = LaunchPreparationProgress()
            p.consume(phase("source-view")); p.consume(error)
            p.consume(done); p.consume("ARM GPU frontend ready symbols=327")
            XCTAssertTrue(p.failed); XCTAssertFalse(p.finished); XCTAssertLessThan(p.percent, 100)
        }
    }

    func testMalformedUnknownAndOrdinaryLogsDoNotMoveProgress() {
        var p = LaunchPreparationProgress()
        for line in ["GPU core ready", phase("unknown"), phase("source-view", "unexpected"),
                     "Metal preparation phase: {}", "Metal preparation phase: []",
                     "Metal preparation phase: invalid", "Metal local preparation: {\"status\":\"failed\"}",
                     "Metal preparation phase: " + String(repeating: " ", count: 4097)] {
            p.consume(line)
        }
        XCTAssertEqual(p, LaunchPreparationProgress())
    }

    func testImageResultAndFailureClassificationUseActualProtocol() {
        for status in ["built", "reused"] {
            var p = LaunchPreparationProgress()
            p.consume("Image/link preparation: {\"status\":\"\(status)\"}")
            XCTAssertEqual(p.percent, 2)
            XCTAssertFalse(p.finished)
            p.consume(phase("local-libraries", "end"))
            p.consume("Image/link preparation: {\"status\":\"reused\"}")
            XCTAssertEqual(p.percent, 15)
        }
        XCTAssertFalse(LaunchPreparationProgress.isFailure("Image/link preparation: {\"status\":\"reused\"}"))
        XCTAssertFalse(LaunchPreparationProgress.isFailure("ordinary log mentions Unsupported game files:"))
        XCTAssertTrue(LaunchPreparationProgress.isFailure("Unsupported game files: bad version"))
    }

    func testFastCachedPathMaySkipStages() {
        var p = LaunchPreparationProgress()
        p.consume(phase("game-preparation")); p.consume(phase("game-preparation", "end"))
        XCTAssertEqual(p.percent, 93)
        p.consume(done); XCTAssertEqual(p.percent, 100)
    }

    func testF3RestartsProgressAfterFinishedAndFailedStates() {
        for failed in [false, true] {
            var p = LaunchPreparationProgress()
            p.consume(failed ? phase("source-view", "end", "raised") : done)
            p.consume("ARM GPU frontend ready symbols=327")
            p.consume("Runtime: previous game exited; preparing supervised restart")
            XCTAssertEqual(p, LaunchPreparationProgress())
            p.consume(phase("archive-policy")); XCTAssertEqual(p.percent, 2)
        }
    }

    func testSupportedSourceFallbackCompletesOnlyPreparation() {
        var p = LaunchPreparationProgress()
        p.consume("Metal local preparation: {\"status\":\"source-fallback\"}")
        XCTAssertEqual(p.percent, 100); XCTAssertEqual(p.stageKey, "Starting game…")
        XCTAssertFalse(p.finished)
    }

    func testNativeReadyBeforePreparationReportCannotFinish() {
        var p = LaunchPreparationProgress()
        p.consume("ARM GPU frontend ready symbols=327")
        XCTAssertEqual(p.percent, 0); XCTAssertFalse(p.finished)
    }

    func testProgressLabelsHaveBothChineseTranslations() {
        for language in ["zh-Hans", "zh-Hant"] {
            for key in ["Preparing game…", "Preparing game resources…", "Indexing game resources…",
                        "Preparing Metal libraries…", "Starting game…", "Preparation failed", "Launch preparation"] {
                XCTAssertNotEqual(L10n.key(key, language: language), key)
            }
        }
    }
}
