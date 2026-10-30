// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

struct UIModStatus: Decodable, Equatable {
    var folderValid = false
    var enabled = false
    var managed = false
    var needsRecovery = false
    var canEnable = false
    var canDisable = false
    var blocksPlay = false
    var message = L10n.key("Choose the extracted UI mod folder (optional).")

    var check: Check {
        Check(level: blocksPlay ? .error : (folderValid && (canEnable || enabled) ? .ok : .warning), message: message)
    }
}

/// Runs only the app's helper and bundled Python; paths are arguments, never shell text.
struct UIModHelper {
    let repo: URL
    let python: String
    let data: URL

    func run(_ action: String, game: String, folder: String) throws -> UIModStatus {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [repo.appendingPathComponent("scripts/ultrawide_ui.py").path,
                             action, "--game", game, "--data", data.path,
                             "--folder", folder]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env.removeValue(forKey: "PYTHONPATH")
        env.removeValue(forKey: "PYTHONHOME")
        process.environment = env
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        let output = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let error = (try? JSONSerialization.jsonObject(with: output)) as? [String: String]
            throw NSError(domain: "UI Mod", code: Int(process.terminationStatus), userInfo: [
                NSLocalizedDescriptionKey: error?["error"] ?? L10n.key("Could not check the UI files. Check folder access and try again.")])
        }
        return try JSONDecoder().decode(UIModStatus.self, from: output)
    }
}
