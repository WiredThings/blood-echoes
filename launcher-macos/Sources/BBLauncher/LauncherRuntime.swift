// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

enum CPUBackend: String, CaseIterable, Hashable, Sendable {
    case fex, bbcpu
    var label: String { self == .fex ? "FEX" : "bbcpu · Static recompilation" }
}

/// Product capabilities declared by the selected engine, not by the machine's CPU.
/// Runtime launch/signature checks remain authoritative; this only governs UI/policy.
struct LauncherRuntime: Equatable, Sendable {
    var isFexArm64 = false
    var automaticMetalPreparation = false
    var supportsRecentStagingReuse = false
    var declaredCPUBackends: [CPUBackend] = []
    var cpuBackends: [CPUBackend] { declaredCPUBackends.isEmpty && isFexArm64 ? [.fex] : declaredCPUBackends }
    var isNativeArm64: Bool { isFexArm64 || !declaredCPUBackends.isEmpty }
    var requiresNativeMetalHost: Bool { isNativeArm64 }
    static let legacy = LauncherRuntime()

    func supports(renderer: String) -> Bool {
        isNativeArm64 ? renderer == "metal" : Catalog.renderers.contains { $0.value == renderer }
    }

    /// A launch session belongs to the original supervisor and all of its F3
    /// children. Generate it once per Play, never in saved preferences or per
    /// game process. This is protocol state, not authority to load native code.
    func launchEnvironment(_ original: [String: String], isolatedQA: Bool = false, cpuBackend: CPUBackend = .fex,
                           nonce: () -> String = { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }) -> [String: String] {
        guard isNativeArm64 else { return original }
        var env = original
        env.removeValue(forKey: "BB_CPU_BACKEND")
        env.removeValue(forKey: "BB_RECOMP_LIB")
        // One immutable intent per original supervisor. UI preference changes
        // after Play do not update the already-started process environment.
        // Actual role/digest admission remains in the existing launch driver.
        if !declaredCPUBackends.isEmpty { env["BB_CPU_BACKEND"] = cpuBackend.rawValue }
        env["BB_ARM_LAUNCHER_COMPAT"] = "1"
        let keys = ["BB_ARM_SESSION_NONCE", "BB_ARM_RESTART_NONCE"]
        let valid: (String?) -> Bool = { value in
            guard let value, value.utf8.count == 32 else { return false }
            return value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
        }
        if !isolatedQA || !keys.allSatisfy({ valid(env[$0]) }) {
            for key in keys { env[key] = nonce() }
        }
        // A previous child's derived cache path cannot select this launch's
        // namespace. The startup component derives it from the original base.
        env.removeValue(forKey: "BB_ARM_GPU_BASE_DIR")
        return env
    }

    static func decode(_ data: Data) -> LauncherRuntime {
        guard data.count <= 65_536,
              let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              value["schema"] as? Int == 1 else { return .legacy }
        let fex = value["game_runtime"] as? String == "fex-arm64"
        var backends: [CPUBackend] = []
        if value["game_runtime"] as? String == "arm64" {
            guard let names = value["cpu_backends"] as? [String], !names.isEmpty,
                  names.count <= CPUBackend.allCases.count, Set(names).count == names.count,
                  names.allSatisfy({ CPUBackend(rawValue: $0) != nil }), names.contains("fex") else { return .legacy }
            backends = names.compactMap(CPUBackend.init(rawValue:))
        }
        let native = fex || !backends.isEmpty
        return LauncherRuntime(isFexArm64: fex,
            automaticMetalPreparation: native && value["local_metal_preparation"] as? String == "auto",
            supportsRecentStagingReuse: (value["metal_features"] as? [String] ?? []).contains("recent-staging-reuse"),
            declaredCPUBackends: backends)
    }

    static func load(from engine: URL?) -> LauncherRuntime {
        guard let file = engine?.appendingPathComponent("NATIVE-METAL.json"),
              let size = (try? file.resourceValues(forKeys: [.fileSizeKey]))?.fileSize,
              size <= 65_536, let data = try? Data(contentsOf: file) else { return .legacy }
        return decode(data)
    }
}
