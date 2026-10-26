// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Product capabilities declared by the selected engine, not by the machine's CPU.
/// Runtime launch/signature checks remain authoritative; this only governs UI/policy.
struct LauncherRuntime: Equatable, Sendable {
    var isFexArm64 = false
    var automaticMetalPreparation = false
    var supportsRecentStagingReuse = false
    var requiresNativeMetalHost: Bool { isFexArm64 }
    static let legacy = LauncherRuntime()

    func supports(renderer: String) -> Bool {
        isFexArm64 ? renderer == "metal" : Catalog.renderers.contains { $0.value == renderer }
    }

    static func decode(_ data: Data) -> LauncherRuntime {
        guard data.count <= 65_536,
              let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              value["schema"] as? Int == 1 else { return .legacy }
        let fex = value["game_runtime"] as? String == "fex-arm64"
        return LauncherRuntime(isFexArm64: fex,
            automaticMetalPreparation: fex && value["local_metal_preparation"] as? String == "auto",
            supportsRecentStagingReuse: (value["metal_features"] as? [String] ?? []).contains("recent-staging-reuse"))
    }

    static func load(from engine: URL?) -> LauncherRuntime {
        guard let file = engine?.appendingPathComponent("NATIVE-METAL.json"),
              let size = (try? file.resourceValues(forKeys: [.fileSizeKey]))?.fileSize,
              size <= 65_536, let data = try? Data(contentsOf: file) else { return .legacy }
        return decode(data)
    }
}
