// SPDX-License-Identifier: GPL-2.0-or-later
import CryptoKit
import Darwin
import Foundation

/// Private, opt-in observation of the *same* environment passed to Process.
/// It is not a second preferences/profile implementation or a launch adapter.
enum QALaunchConditions {
    private static let project = URL(fileURLWithPath: "/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype")
    private static let prefix = "io.github.bbport.optimized.options-qa.arm64.gui."
    private static let forbidden = ["TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH", "BEARER", "PRIVATE_KEY", "PAIRING_CODE"]
    private static let limit = 256 * 1024
    private struct Refusal: Error { let reason: String }

    /// Nil means this bundle did not opt in; refusals never change game startup.
    static func record(bundle: Bundle = .main, arguments: [String], repository: URL,
                       environment: [String: String]) -> String? {
        guard let identifier = bundle.bundleIdentifier, identifier.hasPrefix(prefix) else { return nil }
        let suffix = String(identifier.dropFirst(prefix.count))
        guard !suffix.isEmpty, suffix.utf8.allSatisfy({ (48...57).contains($0) }),
              let destination = bundle.object(forInfoDictionaryKey: "BBQALaunchReceipt") as? String else { return nil }
        do {
            try write(bundle: bundle, identifier: identifier, suffix: suffix, destination: destination,
                      arguments: arguments, repository: repository, environment: environment)
            return "QA launch receipt: written from original Swift final launch environment"
        } catch let error as Refusal {
            return "QA launch receipt: refused (\(error.reason)); original launch unchanged"
        } catch {
            // Do not serialize paths, error userInfo or environment values.
            return "QA launch receipt: refused (I/O validation or atomic write failed); original launch unchanged"
        }
    }

    private static func require(_ condition: Bool, _ reason: String) throws {
        if !condition { throw Refusal(reason: reason) }
    }

    private static func inspect(_ url: URL, directory: Bool) throws -> stat {
        try require(url.isFileURL && url.path.hasPrefix("/") &&
                    url.standardizedFileURL.path == url.path &&
                    url.resolvingSymlinksInPath().path == url.path, "noncanonical path")
        var info = stat()
        try require(lstat(url.path, &info) == 0 && info.st_uid == getuid(), "missing or unowned input")
        try require((info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG), "linked or wrong file type")
        if !directory { try require(info.st_nlink == 1, "hard-linked input") }
        return info
    }

    private static func hash(_ url: URL) throws -> String {
        let before = try inspect(url, directory: false)
        try require(before.st_size >= 0 && before.st_size <= 64 * 1024 * 1024, "oversized identity input")
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        try require(fd >= 0, "identity input unavailable")
        defer { close(fd) }
        var opened = stat()
        try require(fstat(fd, &opened) == 0 && same(before, opened), "identity input changed")
        var digest = SHA256()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        var bytes: Int64 = 0
        while true {
            let count = Darwin.read(fd, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            try require(count >= 0, "identity read failed")
            if count == 0 { break }
            bytes += Int64(count)
            try require(bytes <= before.st_size, "identity input grew")
            digest.update(data: Data(buffer.prefix(count)))
        }
        var after = stat()
        try require(fstat(fd, &after) == 0 && same(before, after) && bytes == before.st_size &&
                    same(before, try inspect(url, directory: false)), "identity input changed")
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func same(_ left: stat, _ right: stat) -> Bool {
        left.st_dev == right.st_dev && left.st_ino == right.st_ino &&
        left.st_uid == right.st_uid && left.st_mode == right.st_mode &&
        left.st_nlink == right.st_nlink && left.st_size == right.st_size &&
        left.st_mtimespec.tv_sec == right.st_mtimespec.tv_sec &&
        left.st_mtimespec.tv_nsec == right.st_mtimespec.tv_nsec &&
        left.st_ctimespec.tv_sec == right.st_ctimespec.tv_sec &&
        left.st_ctimespec.tv_nsec == right.st_ctimespec.tv_nsec
    }

    private static func write(bundle: Bundle, identifier: String, suffix: String, destination: String,
                              arguments: [String], repository: URL, environment: [String: String]) throws {
        try require(environment["BB_ONLINE"] == "0", "offline QA environment required")
        let root = project.appendingPathComponent("state/gui-root-" + suffix)
        let data = root.appendingPathComponent("optimized-launcher")
        let out = data.appendingPathComponent("out")
        try require(bundle.object(forInfoDictionaryKey: "BBPreviewDataPath") as? String == root.path &&
                    environment["BB_DATA_DIR"] == data.path, "wrong QA data identity")
        for folder in [root, data, out] { _ = try inspect(folder, directory: true) }
        let receipt = URL(fileURLWithPath: destination)
        try require(destination == receipt.path && receipt.path == receipt.standardizedFileURL.path &&
                    receipt.deletingLastPathComponent().path == out.path &&
                    !receipt.lastPathComponent.hasPrefix(".") && receipt.pathExtension == "json",
                    "receipt must be in this QA data out directory")
        try require(repository.path == bundle.resourceURL?.appendingPathComponent("bbport").path,
                    "wrong packaged repository")
        _ = try inspect(repository, directory: true)
        try require(repository.path.hasPrefix(project.appendingPathComponent("apps").path + "/"),
                    "repository outside private QA apps")
        try require(arguments == ["tools/macos/run_optimized.sh"], "unexpected original game entry")

        var selected: [String: String] = [:]
        for (key, value) in environment where key.hasPrefix("BB_") || key.hasPrefix("MTL_") {
            try require(key.utf8.allSatisfy({ (65...90).contains($0) || (48...57).contains($0) || $0 == 95 }),
                        "invalid environment key")
            try require(!forbidden.contains(where: { key.contains($0) }), "credential-like key in offline QA environment")
            selected[key] = value
        }
        var identities = [
            "run.sh": try hash(repository.appendingPathComponent("run.sh")),
            "tools/macos/run_optimized.sh": try hash(repository.appendingPathComponent("tools/macos/run_optimized.sh")),
            "SOURCE-PARITY.json": try hash(repository.appendingPathComponent("SOURCE-PARITY.json")),
            "ARM64-QA-MANIFEST.json": try hash(repository.deletingLastPathComponent().appendingPathComponent("ARM64-QA-MANIFEST.json"))
        ]
        guard let executable = bundle.executableURL else { throw Refusal(reason: "missing launcher identity") }
        identities["launcher"] = try hash(executable)
        for key in ["BB_CONFIG", "BB_MODS_CONFIG", "BB_PATCHES_CONFIG"] {
            guard let path = environment[key] else { throw Refusal(reason: "missing original configuration path") }
            let url = URL(fileURLWithPath: path)
            try require(path == url.path && path.hasPrefix(data.path + "/"), "configuration outside this QA data directory")
            identities[key] = try hash(url)
        }
        let value: [String: Any] = [
            "schema": 1, "producer": "original-swift-LauncherModel.start-final-environment",
            "launch_boundary": "before-Process.run", "game_started_verified": false,
            "launch_id": UUID().uuidString, "source_pid": ProcessInfo.processInfo.processIdentifier,
            "created_unix_ns": UInt64(Date().timeIntervalSince1970 * 1_000_000_000),
            "bundle_id": identifier, "bundle_path": bundle.bundleURL.path,
            "executable": "/bin/bash", "arguments": arguments, "working_directory": repository.path,
            "environment": selected, "identity_sha256": identities,
            "original_environment_unchanged": true, "offline": true, "formal_data_modified": false
        ]
        let bytes = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        try require(bytes.count <= limit, "bounded receipt exceeded")
        try atomicWrite(bytes, to: receipt, parent: out)
    }

    private static func atomicWrite(_ bytes: Data, to path: URL, parent: URL) throws {
        let parentInfo = try inspect(parent, directory: true)
        let directory = open(parent.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        try require(directory >= 0, "receipt parent unavailable")
        defer { close(directory) }
        var opened = stat()
        try require(fstat(directory, &opened) == 0 && opened.st_dev == parentInfo.st_dev &&
                    opened.st_ino == parentInfo.st_ino, "receipt parent changed")
        let name = path.lastPathComponent
        var old = stat()
        let existing = fstatat(directory, name, &old, AT_SYMLINK_NOFOLLOW) == 0
        try require(existing || errno == ENOENT, "receipt target unavailable")
        if existing {
            try require(old.st_uid == getuid() && (old.st_mode & S_IFMT) == S_IFREG && old.st_nlink == 1,
                        "existing receipt must be an owned plain file")
        }
        let temporary = ".qa-launch-" + UUID().uuidString
        let fd = openat(directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        try require(fd >= 0, "receipt temporary creation failed")
        defer { close(fd); unlinkat(directory, temporary, 0) }
        try bytes.withUnsafeBytes { raw in
            var count = 0
            while count < raw.count {
                let wrote = Darwin.write(fd, raw.baseAddress!.advanced(by: count), raw.count - count)
                if wrote < 0 && errno == EINTR { continue }
                try require(wrote > 0, "receipt write failed")
                count += wrote
            }
        }
        try require(fsync(fd) == 0, "receipt flush failed")
        var current = stat()
        let nowExists = fstatat(directory, name, &current, AT_SYMLINK_NOFOLLOW) == 0
        try require(existing == nowExists && (existing ? same(old, current) : errno == ENOENT), "receipt target changed")
        let nowParent = try inspect(parent, directory: true)
        try require(nowParent.st_dev == parentInfo.st_dev && nowParent.st_ino == parentInfo.st_ino,
                    "receipt parent changed")
        try require(renameat(directory, temporary, directory, name) == 0, "atomic receipt replacement failed")
    }
}
