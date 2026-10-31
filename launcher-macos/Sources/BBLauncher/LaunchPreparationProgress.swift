// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Stage milestones, not elapsed time or a claim that every shader is compiled.
/// Consumes existing bounded log events; no timer, filesystem polling or runtime IPC.
struct LaunchPreparationProgress: Equatable {
    private(set) var percent = 0
    private(set) var stageKey = "Preparing game…"
    private(set) var failed = false
    private(set) var finished = false
    private var cpuBackend: String?
    private var cpuPlanned: Bool { cpuBackend != nil }
    private struct CPUWork: Decodable {
        let event: String
        let done: Int
        let total: Int
        let active: Int
        let capacity: Int
        let backend: String?
    }

    private static let phases: [String: (Int, Int, String)] = [
        "archive-policy": (2, 5, "Checking preparation cache…"),
        "local-libraries": (5, 15, "Preparing Metal libraries…"),
        "fixed-helpers": (15, 20, "Preparing shared pipelines…"),
        "game-preparation": (20, 93, "Preparing game pipelines…"),
        "source-view": (20, 45, "Preparing game resources…"),
        "static-catalog": (45, 75, "Indexing game resources…"),
        "capture-receipt": (75, 77, "Checking pipeline inputs…"),
        "candidate-scan": (77, 79, "Checking pipeline inputs…"),
        "module-inputs": (79, 84, "Preparing game shaders…"),
        "geometry-inputs": (84, 86, "Preparing game shaders…"),
        "complete-pso": (86, 93, "Preparing game pipelines…"),
        "local-finalize": (93, 98, "Saving preparation results…"),
    ]

    mutating func consume(_ line: String) {
        if line == "Runtime: previous game exited; preparing supervised restart" {
            self = Self()
            return
        }
        guard !failed, !finished else { return }
        if line.hasPrefix("CPU preparation planned: "),
           let value = Self.record(String(line.dropFirst("CPU preparation planned: ".count))),
           let backend = value["backend"] as? String,
           ["bbcpu", "fex"].contains(backend), cpuBackend == nil || cpuBackend == backend {
            cpuBackend = backend
            return
        }
        if cpuPlanned && line.hasPrefix("CPU preparation progress: ") {
            let text = String(line.dropFirst("CPU preparation progress: ".count))
            let maximum = StaticCompilationConcurrency.maximum
            guard text.utf8.count <= 4096, let data = text.data(using: .utf8),
                  let work = try? JSONDecoder().decode(CPUWork.self, from: data),
                  work.event == "compile-progress",
                  (1...(cpuBackend == "fex" ? 4_000_000 : 8192)).contains(work.total),
                  work.backend == nil || work.backend == cpuBackend,
                  (0...work.total).contains(work.done), (0...maximum).contains(work.active),
                  (0...maximum).contains(work.capacity) else { return }
            advance(25 + 70 * work.done / work.total,
                    work.capacity == 0 && work.done < work.total ? "Waiting for compiler memory…" : "Compiling CPU code…", cpu: true)
            return
        }
        if cpuPlanned && line.hasPrefix("CPU local preparation: "),
           let value = Self.record(String(line.dropFirst("CPU local preparation: ".count))),
           value["backend"] as? String == cpuBackend {
            advance(100, "Starting game…", cpu: true)
            return
        }
        if Self.isFailure(line) {
            fail()
            return
        }
        if line.hasPrefix("Image/link preparation: "),
           let value = Self.record(String(line.dropFirst("Image/link preparation: ".count))),
           let status = value["status"] as? String, ["built", "reused"].contains(status) {
            advance(2, "Preparing game…")
            return
        }
        let prefix = "Metal preparation phase: "
        if line.hasPrefix(prefix) {
            guard let value = Self.record(String(line.dropFirst(prefix.count))),
                  let phase = value["phase"] as? String,
                  let milestone = Self.phases[phase],
                  let event = value["event"] as? String else { return }
            if event == "start" {
                advance(milestone.0, milestone.2)
            } else if event == "end", let outcome = value["outcome"] as? String {
                if outcome == "raised" { fail() }
                else if outcome == "returned" { advance(milestone.1, milestone.2) }
            }
        } else if line.hasPrefix("Metal local preparation: ") {
            let prefix = "Metal local preparation: "
            guard let value = Self.record(String(line.dropFirst(prefix.count))),
                  let status = value["status"] as? String,
                  ["prepared", "record", "source-fallback"].contains(status) else { return }
            // Preparation has returned, including its supported source fallback.
            // 100% means this stage ended, never that the scene is playable.
            advance(100, "Starting game…")
        } else if percent == 100 && line.hasPrefix("ARM GPU frontend ready symbols=") {
            finished = true
        }
    }

    static func isFailure(_ line: String) -> Bool {
        ["Metal local preparation failed:", "Metal local preparation stage failed:",
         "Image/link preparation failed:", "Unsupported game files:"].contains { line.hasPrefix($0) }
    }

    private static func record(_ text: String) -> [String: Any]? {
        guard text.utf8.count <= 4096, let data = text.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    private mutating func advance(_ value: Int, _ label: String, cpu: Bool = false) {
        let adjusted = cpuPlanned && !cpu ? min(25, value / 4) : value
        guard adjusted >= percent else { return }
        percent = adjusted
        stageKey = label
    }

    private mutating func fail() {
        failed = true
        stageKey = "Preparation failed"
    }
}
