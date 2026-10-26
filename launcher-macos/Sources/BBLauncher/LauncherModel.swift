// SPDX-License-Identifier: GPL-2.0-or-later
// Launcher state: settings, checks, mods and patches, and the running game (Process).
import AppKit
import Foundation

struct ModEntry: Identifiable, Equatable {
    let name: String
    var isOn: Bool
    var id: String { name }
}

struct PatchEntry: Identifiable, Equatable {
    let key: String
    let title: String
    let subtitle: String
    let note: String?
    let enabledByDefault: Bool
    var isOn: Bool
    var id: String { key }
}

struct LogLine: Identifiable {
    let id: Int
    let text: String
}

/// What the started process is: the game (run.sh) or an update (tools/macos/update.sh).
enum Job: Equatable {
    case game
    case update
}

enum RunState: Equatable {
    case idle
    case running
    case stopping
    case exited(status: Int32, bySignal: Bool)
    case failed(String)
}

@MainActor
final class LauncherModel: ObservableObject {
    static let shared = LauncherModel()

    static let maxLogLines = 5000
    let onlineController = OnlineController()
    @Published var saveBusy = false
    @Published var saveStatus = ""
    @Published var saveBackups: [SaveBackup] = []
    @Published var quickCheckpoints: [QuickCheckpoint] = []
    @Published var currentGameSave: SaveSummary?
    @Published var saveRefreshing = false
    @Published var saveReadError = ""
    @Published var saveCleanupStatus = ""
    @Published var cloudSaveStatus = ""
    @Published var cloudSaveConflicts: [CloudSaveVersion] = []
    var saveRefreshID = UUID()

    @Published var prefs: LauncherPrefs {
        didSet { prefsChanged(from: oldValue) }
    }
    /// bbport.ini values edited by the form; written when the game starts.
    @Published var game = GameSettings()
    @Published var mods: [ModEntry] = []
    @Published var patches: [PatchEntry] = []

    @Published private(set) var repoCheck = Check(level: .error, message: "")
    @Published private(set) var gameCheck = Check(level: .error, message: "")
    @Published private(set) var uiModStatus = UIModStatus()
    @Published private(set) var uiModBusy = false
    @Published private(set) var chineseTranslationBusy = false
    @Published private(set) var chineseTranslationValid = false
    @Published private(set) var chineseTranslationProblem: String?
    private var chineseTranslationToken: UUID?
    @Published private(set) var state: RunState = .idle
    @Published private(set) var job: Job = .game
    @Published private(set) var logLines: [LogLine] = []
    /// Log window: keep scrolled to the newest line.
    @Published var followLog = true
    /// The last "STOP:" or error line, shown when the game exits with an error.
    @Published private(set) var lastProblem: String? = nil

    /// BBRepoPath from Info.plist, written by tools/macos/build_launcher.sh.
    let embeddedRepoPath: String?
    /// The release app's own game engine (Contents/Resources/bbport, tools/macos/package.sh).
    let engineURL: URL?
    /// The release's version (CFBundleShortVersionString).
    let appVersion: String

    private var process: Process?
    private var collector: OutputCollector?
    private var pollTask: Task<Void, Never>?
    private var exitSeen: Date?
    private var nextLineID = 0
    private var ini = IniFile()
    /// The repository bbport.ini, mods and patches were last read from.
    private var loadedRepo: URL?
    private var gameCheckToken: UUID?
    private var checkingGameFolder: String?
    private var checkedGameFolder: String?

    private init() {
        embeddedRepoPath = Bundle.main.object(forInfoDictionaryKey: "BBRepoPath") as? String
        let engine = Bundle.main.resourceURL.map { $0.appendingPathComponent("bbport", isDirectory: true) }
            .flatMap { FileManager.default.fileExists(atPath: $0.appendingPathComponent(".packaged").path) ? $0 : nil }
        engineURL = engine
        // The full release version (0.4-v1) from the engine; About shows only the number part.
        let release = engine.flatMap { try? String(contentsOf: $0.appendingPathComponent(".version"), encoding: .utf8) }
        appVersion = release?.trimmingCharacters(in: .whitespacesAndNewlines)
            ?? Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        var initialPrefs = LauncherPrefs.load(from: UserDefaults.standard)
        // Import only folder locations on first use, not the old engine's performance options.
        if !UserDefaults.standard.bool(forKey: "optimizedFoldersImported") {
            let legacy = UserDefaults.standard.persistentDomain(forName: "io.github.bbport.mac") ?? [:]
            if initialPrefs.gameFolder.isEmpty { initialPrefs.gameFolder = legacy["gameFolder"] as? String ?? "" }
            if initialPrefs.userFolder.isEmpty { initialPrefs.userFolder = legacy["userFolder"] as? String ?? "" }
            initialPrefs.save(to: UserDefaults.standard)
            UserDefaults.standard.set(true, forKey: "optimizedFoldersImported")
        }
        prefs = initialPrefs
        refreshChecks()
        reloadRepositoryFiles()
        synchronizeOfflineRestore()
        let store = saveStore, online = prefs.online
        let legacy = dataURL.appendingPathComponent("online-user")
        runSaveOperation({ try store.prepareMode(online: online, legacy: legacy) }) { [weak self] in
            if self?.prefs.cloudSaves == true { self?.synchronizeCloudSaves() }
        }
    }

    // MARK: - Paths

    /// The release app runs its built-in engine unless Settings name a checkout.
    var usesBuiltInEngine: Bool { engineURL != nil && prefs.repoOverride.isEmpty }

    /// Settings override, else the release app's engine, else the path embedded at build time,
    /// else a repository above the app.
    var repoURL: URL? {
        if !prefs.repoOverride.isEmpty {
            return URL(fileURLWithPath: (prefs.repoOverride as NSString).expandingTildeInPath, isDirectory: true)
        }
        if let engineURL { return engineURL }
        if let embeddedRepoPath, !embeddedRepoPath.isEmpty {
            let url = URL(fileURLWithPath: embeddedRepoPath, isDirectory: true)
            if isRepository(url) { return url }
        }
        return findRepository(above: Bundle.main.bundleURL) ?? embeddedRepoPath.map { URL(fileURLWithPath: $0, isDirectory: true) }
    }

    /// Separate settings/cache for the optimized app; the saves folder remains shared.
    var dataURL: URL { Self.applicationSupportURL.appendingPathComponent("optimized-launcher", isDirectory: true) }

    var keyboardMap: KeyboardMap { KeyboardMap(ini: ini.values, prefs: prefs) }

    var runtimeCapabilities: LauncherRuntime { LauncherRuntime.load(from: repoURL) }
    var profile: OptimizedProfile { OptimizedProfile(prefs: prefs, game: game, runtime: runtimeCapabilities) }

    static var applicationSupportURL: URL {
        if let root = qaApplicationSupportURL(
            bundleIdentifier: Bundle.main.bundleIdentifier,
            previewPath: Bundle.main.object(forInfoDictionaryKey: "BBPreviewDataPath") as? String) {
            return root
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent("bbport", isDirectory: true)
    }

    /// Nothing is written outside a real checkout (a mistyped override, no embedded path) or the
    /// release app's data folder.
    private var hasRepository: Bool { usesBuiltInEngine || (repoURL.map(isRepository) ?? false) }

    var iniURL: URL { dataURL.appendingPathComponent("bbport.ini") }
    var lastRunLogURL: URL { dataURL.appendingPathComponent("out/last-run.log") }

    var userFolderURL: URL {
        prefs.userFolder.isEmpty ? Self.applicationSupportURL.appendingPathComponent("user", isDirectory: true)
            : URL(fileURLWithPath: (prefs.userFolder as NSString).expandingTildeInPath, isDirectory: true)
    }
    var modsFolderURL: URL { folder(prefs.modsFolder, default: "mods") }
    var patchesFolderURL: URL { folder(prefs.patchesFolder, default: "patches") }
    private var modsConfigURL: URL { dataURL.appendingPathComponent("mods.json") }
    private var patchesConfigURL: URL { dataURL.appendingPathComponent("patches.json") }

    private func folder(_ custom: String, default name: String) -> URL {
        if custom.isEmpty { return dataURL.appendingPathComponent(name, isDirectory: true) }
        return URL(fileURLWithPath: (custom as NSString).expandingTildeInPath, isDirectory: true)
    }

    // MARK: - State

    var isRunning: Bool { process != nil }

    var canPlay: Bool {
        !isRunning && !saveBusy && !uiModBusy && !chineseTranslationBusy && !uiModStatus.blocksPlay
            && (!prefs.usesCommunityChinese || chineseTranslationValid)
            && repoCheck.allowsPlay && gameCheck.allowsPlay
            && runtimeCapabilities.supports(renderer: prefs.renderer)
    }


    var statusText: String {
        if uiModBusy { return L10n.key("Checking or updating UI files…") }
        if uiModStatus.blocksPlay { return uiModStatus.message }
        if !isRunning && prefs.usesCommunityChinese {
            if chineseTranslationBusy { return L10n.key("Checking translation files…") }
            if !chineseTranslationValid { return L10n.key("Import the matching GOTY Chinese package to play.") }
        }
        switch state {
        case .idle:
            if !repoCheck.allowsPlay { return repoCheck.message }
            if !gameCheck.allowsPlay {
                return checkingGameFolder != nil ? L10n.key("Checking game folder…") : L10n.key("Choose the game folder")
            }
            return L10n.key("Ready")
        case .running:
            return job == .update ? L10n.key("Updating…") : L10n.key("Running")
        case .stopping:
            return L10n.key("Stopping…")
        case let .exited(status, bySignal):
            if bySignal { return L10n.format("Stopped (signal %@)", String(describing: status)) }
            if job == .update {
                return status == 0 ? L10n.key("Updated") : L10n.key("The update failed: see the log")
            }
            return status == 0 ? L10n.key("The game exited") : L10n.format("The game exited with code %@: see the log", String(describing: status))
        case let .failed(message):
            return L10n.format("Could not start: %@", String(describing: message))
        }
    }

    private func prefsChanged(from old: LauncherPrefs) {
        prefs.save(to: UserDefaults.standard)
        if prefs.renderer != old.renderer {
            normalizeRendererSettings()
            refreshChecks()
        }
        if prefs.uiLanguage != old.uiLanguage { refreshChecks() }
        if prefs.repoOverride != old.repoOverride {
            refreshChecks()
            if repoURL != loadedRepo { reloadRepositoryFiles() }
            return
        }
        if prefs.gameFolder != old.gameFolder {
            uiModStatus = UIModStatus()
            refreshGameCheck()
        }
        if prefs.ultrawide != old.ultrawide { refreshUIMod() }
        if prefs.modsFolder != old.modsFolder { refreshMods() }
        if prefs.patchesFolder != old.patchesFolder { refreshPatches() }
        if prefs.language != old.language || prefs.communityChinese != old.communityChinese
            || prefs.chineseSimplifiedFolder != old.chineseSimplifiedFolder
            || prefs.chineseTraditionalFolder != old.chineseTraditionalFolder {
            refreshChineseTranslation()
        }
    }

    /// bbport.ini and the mod and patch lists of the current repository.
    private func reloadRepositoryFiles() {
        loadedRepo = repoURL
        reloadIni()
        refreshMods()
        refreshPatches()
    }

    /// Cheap checks, also run when the app becomes active (a build may have finished meanwhile).
    func refreshChecks() {
        repoCheck = checkOptimizedEngine(repoURL, packaged: usesBuiltInEngine, renderer: prefs.renderer)
        refreshGameCheck()
        refreshChineseTranslation()
    }

    func chooseChineseTranslation() {
        guard !isRunning, !chineseTranslationBusy, prefs.isChinese else { return }
        let panel = NSOpenPanel()
        panel.message = L10n.key("Choose the GOTY zh-cn / zh-tw ZIP or extracted folder")
        panel.prompt = L10n.key("Import")
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        refreshChineseTranslation(importing: url.path)
    }

    private func refreshChineseTranslation(importing source: String? = nil) {
        guard !isRunning else { return }
        guard !chineseTranslationBusy else { return }
        let token = UUID()
        chineseTranslationToken = token
        chineseTranslationValid = false
        chineseTranslationProblem = nil
        guard prefs.usesCommunityChinese || source != nil else { return }
        let language = prefs.language
        let folder = source ?? prefs.chineseTranslationFolder
        guard !folder.isEmpty, let repo = repoURL else { return }
        let bundled = repo.appendingPathComponent("python/bin/python3").path
        guard let python = FileManager.default.isExecutableFile(atPath: bundled) ? bundled : findPython() else { return }
        let helper = ChineseTranslationHelper(repo: repo, python: python, data: dataURL)
        chineseTranslationBusy = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let result = Result { try helper.run(source == nil ? "inspect" : "import", folder: folder, language: language) }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.chineseTranslationToken == token else { return }
                self.chineseTranslationBusy = false
                // A source, script or repository change invalidates an outstanding inspection.
                guard self.repoURL == repo, self.prefs.language == language,
                      source != nil || self.prefs.chineseTranslationFolder == folder else {
                    self.refreshChineseTranslation()
                    return
                }
                switch result {
                case .success(let report):
                    if source != nil {
                        if language == "10" { self.prefs.chineseTraditionalFolder = report.folder }
                        else { self.prefs.chineseSimplifiedFolder = report.folder }
                    }
                    self.chineseTranslationValid = self.prefs.usesCommunityChinese && report.valid
                case .failure(let error):
                    self.chineseTranslationProblem = error.localizedDescription
                }
            }
        }
    }

    private func refreshGameCheck() {
        let path = prefs.gameFolder
        guard checkingGameFolder != path else { return }
        let token = UUID()
        gameCheckToken = token
        checkingGameFolder = path
        if checkedGameFolder != path {
            gameCheck = Check(level: .error, message: L10n.key("Checking game folder…"))
        }
        // A sleeping external/network drive can block even a tiny metadata read. Never
        // hold the interface thread, and discard results if the user chooses another folder.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let check = checkGameFolder(path)
            DispatchQueue.main.async { [weak self] in
                guard let self, self.gameCheckToken == token, self.prefs.gameFolder == path else { return }
                self.checkingGameFolder = nil
                self.checkedGameFolder = path
                self.gameCheck = check
                self.refreshUIMod()
            }
        }
    }

    func reloadIni() {
        ini = IniFile.load(iniURL)
        game = GameSettings(ini: ini.values)
        normalizeRendererSettings()
    }

    func normalizeRendererSettings() {
        if prefs.renderer == "metal" {
            prefs.presentMode = profile.presentMode
            if !["off", "metalfx"].contains(game.upscaler) { game.upscaler = "metalfx" }
            game.objectMotion = false
            game.sharpen = false
        } else if game.upscaler == "metalfx" {
            game.upscaler = "fsr3"
        }
    }

    private func saveIni() throws {
        guard hasRepository else { return }
        ini = IniFile.load(iniURL)  // the in-game menu may have rewritten it
        var edits = profile.settings.iniValues
        if ini.values["pad.r1"] == nil {
            edits.append((key: "pad.r1", value: "rightshoulder,paddle3,paddle4"))
        }
        let text = ini.text(applying: edits)
        try FileManager.default.createDirectory(at: iniURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: iniURL, atomically: true, encoding: .utf8)
        ini = IniFile.load(iniURL)
    }

    // MARK: - Folders

    func chooseGameFolder() {
        if let path = chooseFolder(message: L10n.key("Choose the game folder (with eboot.bin)"), startingAt: prefs.gameFolder) {
            prefs.gameFolder = path
        }
    }

    // Choosing a valid folder enables its UI fix. Turning Ultrawide off
    // restores a managed install; changing between wide aspect ratios never restricts it.
    func chooseUIModFolder() {
        guard !isRunning, !uiModBusy else { return }
        if let path = chooseFolder(message: L10n.key("Choose the extracted UI fix folder to back up and install its .gfx files"),
                                   startingAt: prefs.uiModFolder) {
            requestUIMod("enable", candidate: path)
        }
    }

    func setUIModEnabled(_ enabled: Bool) {
        requestUIMod(enabled ? "enable" : "disable")
    }

    private func refreshUIMod() { requestUIMod("inspect") }

    private func requestUIMod(_ action: String, candidate: String? = nil, playAfter: Bool = false) {
        guard !isRunning, !uiModBusy, gameCheck.level == .ok, let repo = repoURL else { return }
        let bundled = repo.appendingPathComponent("python/bin/python3").path
        guard let python = FileManager.default.isExecutableFile(atPath: bundled) ? bundled : findPython() else { return }
        let helper = UIModHelper(repo: repo, python: python, data: dataURL)
        let gamePath = prefs.gameFolder
        let selected = candidate ?? prefs.uiModFolder
        let wide = prefs.ultrawide
        uiModBusy = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var report = UIModStatus()
            var problem: String?
            do {
                report = try helper.run(action, game: gamePath, folder: selected)
                if !wide && (report.enabled || report.needsRecovery) {
                    report = try helper.run("disable", game: gamePath, folder: selected)
                }
                if candidate != nil && !report.folderValid { problem = report.message }
            } catch {
                problem = error.localizedDescription
                report = (try? helper.run("inspect", game: gamePath, folder: selected))
                    ?? UIModStatus(blocksPlay: true, message: error.localizedDescription)
            }
            if !wide && (report.enabled || report.needsRecovery) { report.blocksPlay = true }
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.uiModBusy = false
                guard self.prefs.gameFolder == gamePath, self.repoURL == repo else {
                    self.refreshUIMod()
                    return
                }
                self.uiModStatus = report
                if let problem {
                    self.uiModStatus.message = problem
                    self.uiModStatus.folderValid = false
                    self.uiModStatus.canEnable = false
                } else if let candidate, report.folderValid {
                    self.prefs.uiModFolder = candidate
                }
                if playAfter && problem == nil && !report.blocksPlay { self.startGame() }
            }
        }
    }

    func chooseUserFolder() {
        if let path = chooseFolder(message: L10n.key("Choose the saves folder"), startingAt: userFolderURL.path) {
            prefs.userFolder = path
        }
    }

    func chooseModsFolder() {
        saveModProfile()
        if let path = chooseFolder(message: L10n.key("Choose the mods folder"), startingAt: modsFolderURL.path) {
            prefs.modsFolder = path
        }
    }

    func choosePatchesFolder() {
        savePatchProfile()
        if let path = chooseFolder(message: L10n.key("Choose the patches folder"), startingAt: patchesFolderURL.path) {
            prefs.patchesFolder = path
        }
    }

    func chooseRepository() {
        if let path = chooseFolder(message: L10n.key("Choose the bbport repository (with tools/macos/run.sh)"),
                                   startingAt: repoURL?.path) {
            prefs.repoOverride = path
        }
    }

    private func chooseFolder(message: String, startingAt path: String?) -> String? {
        let panel = NSOpenPanel()
        panel.message = message
        panel.prompt = L10n.key("Choose")
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        if let path, !path.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true)
        }
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }

    /// Opens a folder in Finder, creating it first so that a new saves or mods folder opens.
    func reveal(_ url: URL) {
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    // MARK: - Mods (mods.json: {"order": [...], "disabled": [...]})

    func refreshMods() {
        let profile = readJSON(modsConfigURL)
        let order = profile["order"] as? [String] ?? []
        let disabled = Set(profile["disabled"] as? [String] ?? [])
        let available = discoverMods(in: modsFolderURL)
        let availableSet = Set(available)
        var seen = Set<String>()
        var names: [String] = []
        // Saved order first; new folders are appended alphabetically (mods.py selected()).
        for name in order + available where availableSet.contains(name) && !seen.contains(name) {
            seen.insert(name)
            names.append(name)
        }
        mods = names.map { ModEntry(name: $0, isOn: !disabled.contains($0)) }
    }

    func moveMod(_ name: String, by offset: Int) {
        guard let index = mods.firstIndex(where: { $0.name == name }) else { return }
        let target = index + offset
        guard mods.indices.contains(target) else { return }
        mods.swapAt(index, target)
        saveModProfile()
    }

    func saveModProfile() {
        guard hasRepository else { return }
        writeJSON(["order": mods.map(\.name), "disabled": mods.filter { !$0.isOn }.map(\.name)], to: modsConfigURL)
    }

    // MARK: - Third-party patches (patches.json: the switches that differ from isEnabled)

    func refreshPatches() {
        let profile = readJSON(patchesConfigURL)
        let enabled = Set(profile["enabled"] as? [String] ?? [])
        let disabled = Set(profile["disabled"] as? [String] ?? [])
        let builtIn = (repoURL ?? dataURL).appendingPathComponent("patches/Bloodborne.xml")
        patches = discoverPatches(in: patchesFolderURL, builtIn: builtIn).map { meta in
            var subtitle = String(meta.key.split(separator: "/", maxSplits: 1).first ?? "")
            if let author = meta.author { subtitle += " · Author: \(author)" }
            let isOn = enabled.contains(meta.key) || (meta.enabledByDefault && !disabled.contains(meta.key))
            return PatchEntry(key: meta.key, title: meta.name, subtitle: subtitle, note: meta.note,
                              enabledByDefault: meta.enabledByDefault, isOn: meta.name.hasPrefix("Resolution Patch ") ? false : isOn)
        }
    }

    func savePatchProfile() {
        guard hasRepository else { return }
        let profile = readJSON(patchesConfigURL)
        let shown = Set(patches.map(\.key))
        // Patches of files not listed now (another folder) keep their choice.
        var enabled = Set((profile["enabled"] as? [String] ?? []).filter { !shown.contains($0) })
        var disabled = Set((profile["disabled"] as? [String] ?? []).filter { !shown.contains($0) })
        for patch in patches where patch.isOn != patch.enabledByDefault {
            if patch.isOn { enabled.insert(patch.key) } else { disabled.insert(patch.key) }
        }
        writeJSON(["enabled": enabled.sorted(), "disabled": disabled.sorted()], to: patchesConfigURL)
    }

    private func readJSON(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }

    private func writeJSON(_ object: [String: [String]], to url: URL) {
        guard var data = try? JSONSerialization.data(withJSONObject: object,
                                                     options: [.prettyPrinted, .sortedKeys]) else { return }
        data.append(0x0A)
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }

    // MARK: - Environment for tools/macos/run.sh

    /// Every variable the launcher controls is set explicitly (or removed), so the result does
    /// not depend on run.sh's defaults or on the environment the app was opened from.
    func environment() -> [String: String] {
        var env = OptimizedProfile.cleanEnvironment(ProcessInfo.processInfo.environment)
        let p = prefs
        for item in p.extraEnv.split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" }) {
            guard let eq = item.firstIndex(of: "="), eq != item.startIndex else { continue }
            env[String(item[..<eq])] = String(item[item.index(after: eq)...])
        }
        // Source checkouts can use an installed Python; releases carry their own runtime.
        let path = env["PATH"].flatMap { $0.isEmpty ? nil : $0 } ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + path
        env["PYTHONUNBUFFERED"] = "1"
        if usesBuiltInEngine, let engineURL {
            env["PYTHON"] = engineURL.appendingPathComponent("python/bin/python3").path
            env["PYTHONHOME"] = engineURL.appendingPathComponent("python").path
            env["PYTHONNOUSERSITE"] = "1"
            env.removeValue(forKey: "PYTHONPATH")
        } else if env["PYTHON"] == nil, let python = findPython() { env["PYTHON"] = python }
        // run.sh derives these from bbport.ini (output_res, live_resolution).
        for key in ["BB_RENDER_RES", "BB_OUTPUT_RES", "BB_AUTO_RENDER_RES"] {
            env.removeValue(forKey: key)
        }
        env["BB_CONFIG"] = iniURL.path
        env["BB_DATA_DIR"] = dataURL.path
        env["BB_GAME_DIR"] = (p.gameFolder as NSString).expandingTildeInPath
        env["BB_USER_DIR"] = userFolderURL.path
        env["BB_MODS_DIR"] = modsFolderURL.path
        env["BB_MODS_CONFIG"] = modsConfigURL.path
        env["BB_MODS_ENABLED"] = p.modsEnabled ? "1" : "0"
        env["BB_PATCHES_DIR"] = patchesFolderURL.path
        env["BB_PATCHES_CONFIG"] = patchesConfigURL.path
        env["BB_LANGUAGE"] = profile.environment["BB_LANGUAGE"]
        env["BB_FULLSCREEN"] = p.fullscreen ? "1" : "0"
        env["BB_GAME_MENU"] = p.gameMenu ? "1" : "0"
        env["BB_SKIP_NETWORK_CHOICE"] = p.skipNetworkChoice ? "1" : "0"
        if p.display.isEmpty { env.removeValue(forKey: "BB_DISPLAY") } else { env["BB_DISPLAY"] = p.display }
        env["BB_PRESENT_MODE"] = profile.presentMode
        env["BB_HDR"] = p.hdr && p.renderer != "metal" ? "1" : "0"
        env["BB_FPS"] = p.fpsMode
        // The ini's upscaler is overridden by BB_UPSCALER at start; both carry the same value.
        env["BB_UPSCALER"] = game.upscaler
        let limit = min(max(p.fpsLimit, 0), 480)
        if limit > 0 { env["BB_FPS_LIMIT"] = String(limit) } else { env.removeValue(forKey: "BB_FPS_LIMIT") }
        if p.drawPipe.isEmpty { env.removeValue(forKey: "BB_DRAW_PIPE") } else { env["BB_DRAW_PIPE"] = p.drawPipe }
        if p.readbacks.isEmpty { env.removeValue(forKey: "BB_READBACKS") } else { env["BB_READBACKS"] = p.readbacks }
        if p.preupload.isEmpty { env.removeValue(forKey: "BB_PREUPLOAD") } else { env["BB_PREUPLOAD"] = p.preupload }
        // Set only when on: parts of the port test these for presence, not value ("0" is on).
        for (key, on) in [("BB_NATIVE_GPU", p.nativeGpu),
                          ("BB_FRAME_STATS", p.frameStats),
                          ("BB_VK_VALIDATION", p.vkValidation)] {
            if on { env[key] = "1" } else { env.removeValue(forKey: key) }
        }
        // Do not allow diagnostics to capture controller input or trigger a restore at launch.
        for key in ["BB_PAD_FILE", "BB_PAD_REPLAY", "BB_PAD_RECORD", "BB_PRESET_FILE", "BB_TOGGLE_FILE",
                    "BB_CAPTURE_TRIGGER", "BB_PRESENT_DUMP_TRIGGER", "BB_TIMEOUT", "BB_QUICKLOAD_PENDING",
                    "BB_RENDER_RES", "BB_OUTPUT_RES", "BB_UI_RES", "BB_AUTO_RENDER_RES", "BB_DMEM_MB",
                    "BB_NATIVE_CHECKOUT", "BB_PROBE", "BB_GPU_PROCESS", "BB_GPU_DEPS"] {
            env.removeValue(forKey: key)
        }
        return profile.applyingEnvironment(to: env)
    }

    // MARK: - Running the game

    func play() {
        guard canPlay else { return }
        // Recheck on Play: downloaded files, backups, and the game can change outside the app.
        if gameCheck.level == .ok { requestUIMod("inspect", playAfter: true) }
        else { startGame() }
    }

    private func startGame() {
        guard !isRunning, !saveBusy else { return }
        if prefs.cloudSaves {
            synchronizeCloudSaves { [weak self] in self?.prepareGameSaves() }
        } else { prepareGameSaves() }
    }

    private func prepareGameSaves() {
        guard !isRunning, !saveBusy else { return }
        synchronizeOfflineRestore()
        let store = saveStore, online = prefs.online
        let legacy = dataURL.appendingPathComponent("online-user")
        runSaveOperation({ try store.prepareMode(online: online, legacy: legacy, newSession: true) }) { [weak self] in self?.startPreparedGame() }
    }

    private func startPreparedGame() {
        guard process == nil, let repo = repoURL, repoCheck.allowsPlay, gameCheck.allowsPlay else { return }
        var env = environment()
        if prefs.online {
            guard profile.onlineActive else {
                state = .failed(L10n.key("Online play currently requires the Metal backend.")); return
            }
            onlineController.load()
            guard let account = onlineController.account else {
                state = .failed(L10n.key("Sign in on the Online page before playing online.")); return
            }
            env.merge(HuntersDream.environment) { _, value in value }
            env["BB_NET_BACKEND"] = "thd"
            env["BB_THD_ONLINE_ID"] = account.name
            env["BB_THD_TOKEN"] = account.token
            env["BB_THD_VERIFY_TLS"] = "1"
            env["BB_COMMUNITY_SERVER"] = HuntersDream.game.absoluteString
            env["BB_CLIENT_VERSION"] = "BloodEchoes/\(appVersion)"
        }
        saveModProfile()
        savePatchProfile()
        do {
            try saveIni()
        } catch {
            state = .failed(L10n.format("cannot write %@: %@", String(describing: iniURL.path), String(describing: error.localizedDescription)))
            return
        }
        let summary = ["BB_GAME_DIR", "BB_LANGUAGE", "BB_FPS", "BB_UPSCALER", "BB_FULLSCREEN", "BB_PRESENT_MODE"]
            .map { "\($0)=\(env[$0] ?? "")" }.joined(separator: " ")
        start(.game, arguments: ["tools/macos/run_optimized.sh"], in: repo, env: env,
              echo: "$ \(summary) bash tools/macos/run_optimized.sh")
    }

    private func start(_ job: Job, arguments: [String], in repo: URL, env: [String: String], echo: String) {
        logLines = []
        lastProblem = nil
        exitSeen = nil
        self.job = job
        // ~/… rather than the full path: logs get pasted into reports.
        appendLines(["$ cd \((repo.path as NSString).abbreviatingWithTildeInPath)", echo])

        let pipe = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = arguments
        process.currentDirectoryURL = repo
        process.environment = env
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = pipe
        process.standardError = pipe
        // The reader owns a duplicate of the read end (closed by the reader at end of file).
        let readFD = dup(pipe.fileHandleForReading.fileDescriptor)
        do {
            try process.run()
        } catch {
            if readFD >= 0 { close(readFD) }
            state = .failed(error.localizedDescription)
            appendLines([L10n.format("Could not start: %@", String(describing: error.localizedDescription))])
            return
        }
        let collector = OutputCollector()
        if readFD >= 0 {
            pumpOutput(from: readFD, into: collector)
        } else {
            collector.finish()
        }
        self.process = process
        self.collector = collector
        state = .running
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self else { return }
                if self.poll() { return }
            }
        }
    }

    /// SIGTERM; SIGKILL after 3 s. tools/macos/run.sh and run.sh exec into bb-probe (or forward
    /// TERM to it when mods are merged), so the game is the process we started.
    func stop() {
        guard let process, process.isRunning else { return }
        state = .stopping
        process.terminate()
        let pid = process.processIdentifier
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard let self, let current = self.process, current.processIdentifier == pid, current.isRunning else {
                return
            }
            kill(pid, SIGKILL)
        }
    }

    /// The app is quitting: ask the game to stop, as the Linux launcher does on close.
    func prepareForQuit() {
        if process == nil {
            saveModProfile()
            savePatchProfile()
            try? saveIni()
        }
        process?.terminate()
    }

    /// Moves new output into the log; true once the game has exited and the log is complete.
    private func poll() -> Bool {
        guard let collector, let process else { return true }
        let (lines, ended) = collector.drain()
        if !lines.isEmpty { appendLines(lines) }
        if job == .game { synchronizeOfflineRestore() }
        if process.isRunning { return false }
        if !ended {
            // A leftover child (tee, a Python step) may still hold the pipe: wait briefly.
            let now = Date()
            if exitSeen == nil { exitSeen = now }
            if let seen = exitSeen, now.timeIntervalSince(seen) < 2 { return false }
        }
        let bySignal = process.terminationReason == .uncaughtSignal
        let status = process.terminationStatus
        self.process = nil
        self.collector = nil
        pollTask = nil
        state = .exited(status: status, bySignal: bySignal)
        if job == .update {
            appendLines(["", status == 0 && !bySignal ? "— update finished —" : "— the update stopped (code \(status)) —"])
            // New code may bring new checks, mods or patches.
            refreshChecks()
            reloadRepositoryFiles()
            return true
        }
        appendLines(["", bySignal ? "— the game was stopped (signal \(status)) —" : "— the game exited (code \(status)) —"])
        // Pick up changes made in the in-game menu (it saves bbport.ini).
        reloadIni()
        if prefs.cloudSaves { synchronizeCloudSaves() }
        else { cleanSaveHistory() }
        return true
    }

    private func appendLines(_ lines: [String]) {
        var added: [LogLine] = []
        added.reserveCapacity(lines.count)
        for line in lines {
            if job == .game && line == "Runtime: quickload switched to offline" { useOfflineAfterRestore() }
            if line.contains("STOP:") || line.contains("Traceback") || line.range(of: "error:", options: .caseInsensitive) != nil {
                lastProblem = line
            }
            added.append(LogLine(id: nextLineID, text: line))
            nextLineID += 1
        }
        var all = logLines
        all.append(contentsOf: added)
        if all.count > Self.maxLogLines {
            all.removeFirst(all.count - Self.maxLogLines)
        }
        logLines = all
    }

    /// Help menu: the last run's log in Finder.
    func revealLastLog() {
        if FileManager.default.fileExists(atPath: lastRunLogURL.path) {
            NSWorkspace.shared.activateFileViewerSelecting([lastRunLogURL])
        } else {
            reveal(lastRunLogURL.deletingLastPathComponent())
        }
    }

    func clearLog() {
        logLines = []
    }

    func copyLog() {
        let text = logLines.map(\.text).joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

/// Web pages the app links to (Help menu, Updates).
enum Links {
    static let home = "https://github.com/WiredThings/blood-echoes"
    static var userGuide: String {
        let filename = L10n.language.hasPrefix("zh") ? "USER_GUIDE.zh-CN.md" : "USER_GUIDE.md"
        return "\(home)/blob/optimized/docs/\(filename)"
    }
    static let releases = "\(home)/releases"
    static let issues = "\(home)/issues"
    static let chineseTranslation = "https://www.nexusmods.com/bloodborne/mods/146?tab=files"

    @MainActor static func open(_ address: String) {
        if let url = URL(string: address) { NSWorkspace.shared.open(url) }
    }
}
