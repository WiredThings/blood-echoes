// SPDX-License-Identifier: GPL-2.0-or-later
// Settings shown by the launcher. Mirrors launcher/bbport_launcher.py: launcher-only state lives
// in UserDefaults (the Linux launcher's settings.json), game settings in bbport.ini, which the
// port and its in-game menu also read and write (gpu/shim/bbport_settings.cpp).
import Foundation

/// A picker entry: the stored value and its English label.
struct Choice<Value: Hashable & Sendable>: Identifiable, Sendable {
    let value: Value
    let label: String
    var id: Value { value }
}

/// A game effect switched by a community patch at start (bbport.ini key, launcher EFFECTS).
struct EffectDefinition: Sendable {
    let key: String
    let title: String
    let defaultOn: Bool
    let note: String?
}

enum Catalog {
    static let renderers: [Choice<String>] = [
        Choice(value: "metal", label: "Metal (default)"),
        Choice(value: "vulkan", label: "Vulkan (compatibility)"),
    ]
    // Upscalers offered on macOS (bbport.ini "upscaler", BB_UPSCALER). FSR 4 and 4.1.1 need
    // FP8/WMMA features the Mac Vulkan drivers lack; the port falls back to FSR 3.1 for them.
    static let upscalers: [Choice<String>] = [
        Choice(value: "off", label: "Off"),
        Choice(value: "fsr3", label: "FSR 3.1"),
        Choice(value: "taa", label: "TAA (native anti-aliasing)"),
        Choice(value: "metalfx", label: "MetalFX"),
    ]
    static let defaultUpscaler = "off"

    static let presets: [Choice<Int>] = [
        Choice(value: 0, label: "Native AA"),
        Choice(value: 5, label: "Ultra Quality (x1.25)"),
        Choice(value: 1, label: "Quality (x1.5)"),
        Choice(value: 2, label: "Balanced (x1.7)"),
        Choice(value: 3, label: "Performance (x2)"),
        Choice(value: 4, label: "Ultra Performance (x3)"),
    ]
    // Full-resolution rendering is the optimized launcher default.
    static let defaultPreset = 0

    static let outputResolutions: [Choice<String>] = [
        Choice(value: "1280x720", label: "1280×720"),
        Choice(value: "1920x1080", label: "1920×1080"),
        Choice(value: "2560x1440", label: "2560×1440"),
        Choice(value: "3840x2160", label: "3840×2160"),
    ]

    static let wideResolutions: [Choice<String>] = [
        Choice(value: "2560x720", label: "2560 × 720 · 32:9"),
        Choice(value: "3840x1080", label: "3840 × 1080 · 32:9"),
        Choice(value: "5120x1440", label: "5120 × 1440 · 32:9"),
        Choice(value: "2560x1080", label: "2560 × 1080 · 21:9"),
        Choice(value: "3440x1440", label: "3440 × 1440 · 21:9"),
    ]

    static let liveResolution: [Choice<String>] = [
        Choice(value: "auto", label: "Auto (by GPU)"),
        Choice(value: "0", label: "Off (faster)"),
        Choice(value: "1", label: "On"),
    ]

    static let modelDetail: [Choice<String>] = [
        Choice(value: "0", label: "As in the game"),
        Choice(value: "-2", label: "Highest (-2)"),
        Choice(value: "1", label: "Lower (1)"),
        Choice(value: "2", label: "Lowest (2)"),
    ]

    static let effects: [EffectDefinition] = [
        EffectDefinition(key: "effect_chromatic_aberration", title: "Chromatic aberration", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_dof", title: "Depth of field (DoF)", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_motion_blur", title: "Motion blur", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_ssao", title: "SSAO", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_game_aa", title: "The game's own anti-aliasing", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_dynamic_shadows", title: "Dynamic light shadows", defaultOn: false, note: nil),
        EffectDefinition(key: "effect_ssr", title: "SSR reflections (not in the original game)", defaultOn: false, note: nil),
        EffectDefinition(key: "skip_intro", title: "Skip the intro videos", defaultOn: true, note: nil),
        EffectDefinition(key: "debug_camera", title: "Free camera (Cross + L3 / Space + Z)", defaultOn: false, note: nil),
        EffectDefinition(key: "debug_menu", title: "Debug menu (left touchpad / Tab; needs fonts)", defaultOn: false,
                         note: "Install DbgFont14h.ccm and DbgFont14h.tpf into dvdroot_ps4/font from Nexus mod #253"),
    ]

    static let fpsModes: [Choice<String>] = [
        Choice(value: "uncap", label: "Unlocked (patch)"),
        Choice(value: "60", label: "60"),
        Choice(value: "90", label: "90"),
        Choice(value: "30", label: "30 (as on PS4)"),
    ]
    // The tested M2 profile uses the variable-timestep community patch.
    static let defaultFPSMode = "uncap"

    static let presentModes: [Choice<String>] = [
        Choice(value: "Mailbox", label: "Mailbox"),
        Choice(value: "Fifo", label: "FIFO (VSync)"),
        Choice(value: "FifoRelaxed", label: "FIFO Relaxed"),
        Choice(value: "Immediate", label: "Immediate"),
    ]

    static func presentModes(renderer: String) -> [Choice<String>] {
        if renderer == "metal" {
            // Metal only distinguishes display synchronization, not mailbox queue semantics.
            return [Choice(value: "Fifo", label: "VSync"), Choice(value: "Mailbox", label: "Unsynced")]
        }
        return presentModes
    }

    static let drawPipe: [Choice<String>] = [
        Choice(value: "", label: "Auto (8+ threads)"),
        Choice(value: "1", label: "On"),
        Choice(value: "0", label: "Off (more stable)"),
    ]

    /// BB_PREUPLOAD (bbport 0.3): background upload of the game's GPU memory ahead of use.
    static let preupload: [Choice<String>] = [
        Choice(value: "", label: "Normal (default)"),
        Choice(value: "2", label: "Full (about 3 GB more memory)"),
        Choice(value: "0", label: "Off"),
    ]

    static let readbacks: [Choice<String>] = [
        Choice(value: "", label: "Relaxed (default)"),
        Choice(value: "0", label: "Off"),
        Choice(value: "2", label: "Precise"),
    ]

    static let languages: [Choice<String>] = [
        Choice(value: "1", label: "English"),
        Choice(value: "11", label: "简体中文"),
        Choice(value: "10", label: "繁體中文"),
        Choice(value: "8", label: "Russian"),
        Choice(value: "0", label: "Japanese"),
        Choice(value: "2", label: "French"),
        Choice(value: "3", label: "Spanish"),
        Choice(value: "4", label: "German"),
        Choice(value: "5", label: "Italian"),
        Choice(value: "6", label: "Dutch"),
        Choice(value: "7", label: "Portuguese (Portugal)"),
        Choice(value: "9", label: "Korean"),
        Choice(value: "12", label: "Finnish"),
        Choice(value: "13", label: "Swedish"),
        Choice(value: "14", label: "Danish"),
        Choice(value: "15", label: "Norwegian"),
        Choice(value: "16", label: "Polish"),
        Choice(value: "17", label: "Portuguese (Brazil)"),
        Choice(value: "18", label: "English (United Kingdom)"),
        Choice(value: "19", label: "Turkish"),
        Choice(value: "20", label: "Spanish (Latin America)"),
    ]

    static func contains<Value: Hashable & Sendable>(_ choices: [Choice<Value>], _ value: Value) -> Bool {
        choices.contains { $0.value == value }
    }
}

/// Launcher-only settings (UserDefaults, one key each so `defaults read io.github.bbport.mac`
/// shows them). Empty folder paths mean the default inside the repository.
struct LauncherPrefs: Equatable {
    var gameFolder = ""
    var repoOverride = ""
    /// Branch for Update; empty keeps the current one.
    var branch = ""
    var userFolder = ""
    var cloudSaves = false
    var modsFolder = ""
    var modsEnabled = true
    var patchesFolder = ""
    var language = "1"
    var communityChinese = false
    var chineseSimplifiedFolder = ""
    var chineseTraditionalFolder = ""
    var isChinese: Bool { ["10", "11"].contains(language) }
    var usesCommunityChinese: Bool { isChinese && communityChinese }
    var chineseTranslationFolder: String {
        language == "10" ? chineseTraditionalFolder : chineseSimplifiedFolder
    }
    var uiLanguage = "system"
    var fullscreen = false
    var ultrawide = false
    var uiModFolder = ""
    var resolution = "1920x1080"
    var wideResolution = "3840x1080"
    var quicksave = false
    var quicksaveKey = ShortcutKey.save
    var quickloadKey = ShortcutKey.load
    var menuKey = ShortcutKey.menu
    var hdr = false
    var presentMode = "Fifo"
    var fpsMode = Catalog.defaultFPSMode
    var fpsLimit = 0
    var drawPipe = ""
    var readbacks = ""
    var preupload = ""
    var nativeGpu = false
    var renderer = "metal"
    var cpuBackend = CPUBackend.fex
    var metalObjectMotion = true
    var metalOcclusion = true
    var online = false
    var metalOptimizations: [String: Bool] = [:]
    var frameStats = false
    var gpuProfile = false
    var vkValidation = false
    var extraEnv = ""
    /// bbport 0.5: the port's settings as pages of the game's System menu (BB_GAME_MENU).
    var gameMenu = true
    /// bbport 0.5: skip the game's online/offline choice at start (BB_SKIP_NETWORK_CHOICE).
    var skipNetworkChoice = true
    /// The monitor for the game's window (BB_DISPLAY: part of its name); empty: the main one.
    var display = ""

    private enum Key {
        static let gameFolder = "gameFolder"
        static let repoOverride = "repoOverride"
        static let branch = "branch"
        static let userFolder = "userFolder"
        static let cloudSaves = "cloudSaves"
        static let modsFolder = "modsFolder"
        static let modsEnabled = "modsEnabled"
        static let patchesFolder = "patchesFolder"
        static let language = "language"
        static let communityChinese = "communityChinese"
        static let chineseSimplifiedFolder = "chineseSimplifiedFolder"
        static let chineseTraditionalFolder = "chineseTraditionalFolder"
        static let uiLanguage = "uiLanguage"
        static let fullscreen = "fullscreen"
        static let ultrawide = "ultrawide"
        static let uiModFolder = "uiModFolder"
        static let resolution = "resolution"
        static let wideResolution = "wideResolution"
        static let quicksave = "quicksave"
        static let quicksaveKey = "quicksaveKey"
        static let quickloadKey = "quickloadKey"
        static let menuKey = "menuKey"
        static let hdr = "hdr"
        static let presentMode = "presentMode"
        static let fpsMode = "fpsMode"
        static let fpsLimit = "fpsLimit"
        static let drawPipe = "drawPipe"
        static let readbacks = "readbacks"
        static let preupload = "preupload"
        static let nativeGpu = "nativeGpu"
        static let renderer = "renderer"
        static let cpuBackend = "cpuBackend"
        static let metalObjectMotion = "metalObjectMotion"
        static let metalOcclusion = "metalOcclusion"
        static let online = "online"
        static let metalOptimizations = "metalOptimizations"
        static let frameStats = "frameStats"
        static let gpuProfile = "gpuProfile"
        static let vkValidation = "vkValidation"
        static let extraEnv = "extraEnv"
        static let gameMenu = "gameMenu"
        static let skipNetworkChoice = "skipNetworkChoice"
        static let display = "display"
    }

    static func load(from defaults: UserDefaults) -> LauncherPrefs {
        var p = LauncherPrefs()
        func text(_ key: String, _ fallback: String) -> String { defaults.string(forKey: key) ?? fallback }
        func flag(_ key: String, _ fallback: Bool) -> Bool { defaults.object(forKey: key) as? Bool ?? fallback }
        p.gameFolder = text(Key.gameFolder, p.gameFolder)
        p.cpuBackend = CPUBackend(rawValue: text(Key.cpuBackend, p.cpuBackend.rawValue)) ?? .fex
        p.repoOverride = text(Key.repoOverride, p.repoOverride)
        p.branch = text(Key.branch, p.branch)
        p.userFolder = text(Key.userFolder, p.userFolder)
        p.cloudSaves = flag(Key.cloudSaves, p.cloudSaves)
        p.modsFolder = text(Key.modsFolder, p.modsFolder)
        p.modsEnabled = flag(Key.modsEnabled, p.modsEnabled)
        p.patchesFolder = text(Key.patchesFolder, p.patchesFolder)
        p.language = text(Key.language, p.language)
        p.communityChinese = flag(Key.communityChinese, p.communityChinese)
        p.chineseSimplifiedFolder = text(Key.chineseSimplifiedFolder, p.chineseSimplifiedFolder)
        p.chineseTraditionalFolder = text(Key.chineseTraditionalFolder, p.chineseTraditionalFolder)
        p.uiLanguage = text(Key.uiLanguage, p.uiLanguage)
        if !Catalog.contains(L10n.languages, p.uiLanguage) { p.uiLanguage = "system" }
        p.fullscreen = flag(Key.fullscreen, p.fullscreen)
        p.ultrawide = flag(Key.ultrawide, p.ultrawide)
        p.uiModFolder = text(Key.uiModFolder, p.uiModFolder)
        p.resolution = text(Key.resolution, p.resolution)
        p.wideResolution = text(Key.wideResolution, p.wideResolution)
        p.quicksave = flag(Key.quicksave, p.quicksave)
        func shortcut(_ key: String, fallback: ShortcutKey) -> ShortcutKey {
            guard let value = defaults.dictionary(forKey: key), let code = value["code"] as? Int,
                  ShortcutKey.valid(code) else { return fallback }
            let label = value["label"] as? String ?? ShortcutKey.name(code)
            let result = ShortcutKey(code: code, label: label.count <= 16 ? label : ShortcutKey.name(code))
            return result.reservedAction == nil ? result : fallback
        }
        p.quicksaveKey = shortcut(Key.quicksaveKey, fallback: .save)
        p.quickloadKey = shortcut(Key.quickloadKey, fallback: .load)
        p.menuKey = shortcut(Key.menuKey, fallback: .menu)
        if Set([p.quicksaveKey.code, p.quickloadKey.code, p.menuKey.code]).count < 3 {
            p.quicksaveKey = .save; p.quickloadKey = .load; p.menuKey = .menu
        }
        if !Catalog.contains(Catalog.outputResolutions, p.resolution) { p.resolution = "1920x1080" }
        if !Catalog.contains(Catalog.wideResolutions, p.wideResolution) { p.wideResolution = "3840x1080" }
        p.hdr = flag(Key.hdr, p.hdr)
        p.presentMode = text(Key.presentMode, p.presentMode)
        p.fpsMode = text(Key.fpsMode, p.fpsMode)
        p.fpsLimit = defaults.object(forKey: Key.fpsLimit) as? Int ?? p.fpsLimit
        p.drawPipe = text(Key.drawPipe, p.drawPipe)
        p.readbacks = text(Key.readbacks, p.readbacks)
        p.preupload = text(Key.preupload, p.preupload)
        p.nativeGpu = flag(Key.nativeGpu, p.nativeGpu)
        p.renderer = text(Key.renderer, p.renderer)
        p.metalObjectMotion = flag(Key.metalObjectMotion, p.metalObjectMotion)
        p.metalOcclusion = flag(Key.metalOcclusion, p.metalOcclusion)
        p.online = flag(Key.online, p.online)
        if !Catalog.contains(Catalog.renderers, p.renderer) { p.renderer = "metal" }
        p.frameStats = flag(Key.frameStats, p.frameStats)
        p.gpuProfile = flag(Key.gpuProfile, p.gpuProfile)
        p.vkValidation = flag(Key.vkValidation, p.vkValidation)
        p.extraEnv = text(Key.extraEnv, p.extraEnv)
        let knownMetalKeys = Set(MetalOptimizations.all.map(\.id))
        if let stored = defaults.dictionary(forKey: Key.metalOptimizations) {
            p.metalOptimizations = stored.reduce(into: [:]) { result, item in
                if knownMetalKeys.contains(item.key), let value = item.value as? Bool {
                    result[item.key] = value
                }
            }
        } else {
            // Preserve explicit old developer overrides when introducing visible controls.
            for token in p.extraEnv.split(whereSeparator: { $0.isWhitespace }) {
                let pair = token.split(separator: "=", maxSplits: 1)
                if pair.count == 2, knownMetalKeys.contains(String(pair[0])), ["0", "1"].contains(pair[1]) {
                    p.metalOptimizations[String(pair[0])] = pair[1] == "1"
                }
            }
        }
        // New controls also import an existing developer choice, even if the original
        // optimization dictionary already exists. A saved checkbox remains authoritative.
        let laterControls: Set<String> = ["BB_TEXTURE_INVALID_RECLAIM", "BB_MTL_CONTINUE_CLEARS", "BB_MTL_DEPTH_LAYOUT",
            "BB_MTL_NATIVE_CLEAR_BATCH", "BB_MTL_MERGE_OVERLAY", "BB_MTL_FILL_UPDATES",
            "BB_LAYER_USE_CACHE", "BB_MTL_COPY_PAIR", "BB_MTL_RENDER_UPDATES", "BB_MTL_LIBRARY_REUSE", "BB_MTL_HOT_STAGING_REUSE", "BB_MTL_RELAX_VERTEX"]
        for token in p.extraEnv.split(whereSeparator: { $0.isWhitespace }) {
            let pair = token.split(separator: "=", maxSplits: 1)
            if pair.count == 2, laterControls.contains(String(pair[0])),
               p.metalOptimizations[String(pair[0])] == nil, ["0", "1"].contains(pair[1]) {
                p.metalOptimizations[String(pair[0])] = pair[1] == "1"
            }
        }
        p.gameMenu = flag(Key.gameMenu, p.gameMenu)
        p.skipNetworkChoice = flag(Key.skipNetworkChoice, p.skipNetworkChoice)
        p.display = text(Key.display, p.display)
        // Values from an older version that the pickers no longer offer.
        if !Catalog.contains(Catalog.languages, p.language) { p.language = "1" }
        if !Catalog.contains(Catalog.presentModes, p.presentMode) { p.presentMode = "Fifo" }
        if !Catalog.contains(Catalog.fpsModes, p.fpsMode) { p.fpsMode = Catalog.defaultFPSMode }
        if !Catalog.contains(Catalog.drawPipe, p.drawPipe) { p.drawPipe = "" }
        if !Catalog.contains(Catalog.readbacks, p.readbacks) { p.readbacks = "" }
        if !Catalog.contains(Catalog.preupload, p.preupload) { p.preupload = "" }
        return p
    }

    func save(to defaults: UserDefaults) {
        defaults.set(gameFolder, forKey: Key.gameFolder)
        defaults.set(cpuBackend.rawValue, forKey: Key.cpuBackend)
        defaults.set(repoOverride, forKey: Key.repoOverride)
        defaults.set(branch, forKey: Key.branch)
        defaults.set(userFolder, forKey: Key.userFolder)
        defaults.set(cloudSaves, forKey: Key.cloudSaves)
        defaults.set(modsFolder, forKey: Key.modsFolder)
        defaults.set(modsEnabled, forKey: Key.modsEnabled)
        defaults.set(patchesFolder, forKey: Key.patchesFolder)
        defaults.set(language, forKey: Key.language)
        defaults.set(communityChinese, forKey: Key.communityChinese)
        defaults.set(chineseSimplifiedFolder, forKey: Key.chineseSimplifiedFolder)
        defaults.set(chineseTraditionalFolder, forKey: Key.chineseTraditionalFolder)
        defaults.set(uiLanguage, forKey: Key.uiLanguage)
        defaults.set(fullscreen, forKey: Key.fullscreen)
        defaults.set(ultrawide, forKey: Key.ultrawide)
        defaults.set(uiModFolder, forKey: Key.uiModFolder)
        defaults.set(resolution, forKey: Key.resolution)
        defaults.set(wideResolution, forKey: Key.wideResolution)
        defaults.set(quicksave, forKey: Key.quicksave)
        defaults.set(["code": quicksaveKey.code, "label": quicksaveKey.label], forKey: Key.quicksaveKey)
        defaults.set(["code": quickloadKey.code, "label": quickloadKey.label], forKey: Key.quickloadKey)
        defaults.set(["code": menuKey.code, "label": menuKey.label], forKey: Key.menuKey)
        defaults.set(hdr, forKey: Key.hdr)
        defaults.set(presentMode, forKey: Key.presentMode)
        defaults.set(fpsMode, forKey: Key.fpsMode)
        defaults.set(fpsLimit, forKey: Key.fpsLimit)
        defaults.set(drawPipe, forKey: Key.drawPipe)
        defaults.set(readbacks, forKey: Key.readbacks)
        defaults.set(preupload, forKey: Key.preupload)
        defaults.set(nativeGpu, forKey: Key.nativeGpu)
        defaults.set(renderer, forKey: Key.renderer)
        defaults.set(metalObjectMotion, forKey: Key.metalObjectMotion)
        defaults.set(metalOcclusion, forKey: Key.metalOcclusion)
        defaults.set(online, forKey: Key.online)
        defaults.set(metalOptimizations, forKey: Key.metalOptimizations)
        defaults.set(frameStats, forKey: Key.frameStats)
        defaults.set(gpuProfile, forKey: Key.gpuProfile)
        defaults.set(vkValidation, forKey: Key.vkValidation)
        defaults.set(extraEnv, forKey: Key.extraEnv)
        defaults.set(gameMenu, forKey: Key.gameMenu)
        defaults.set(skipNetworkChoice, forKey: Key.skipNetworkChoice)
        defaults.set(display, forKey: Key.display)
    }
}

/// One effect switch as edited in the form.
struct EffectSetting: Identifiable, Equatable {
    let key: String
    let title: String
    let note: String?
    var isOn: Bool
    var id: String { key }
}

/// The bbport.ini keys the launcher edits (the Linux launcher's INI_DEFAULTS); other keys stay.
struct GameSettings: Equatable {
    var upscaler = Catalog.defaultUpscaler
    var preset = Catalog.defaultPreset
    var sharpen = true
    var sharpness = 0.5
    var objectMotion = true
    var showFPS = true
    var outputRes = "1920x1080"
    // Global forced low LOD omits some corridor walls/floors; keep the game's selection.
    var modelLOD = "0"
    var liveResolution = "0"
    var effects: [EffectSetting] = Catalog.effects.map {
        EffectSetting(key: $0.key, title: $0.title, note: $0.note, isOn: $0.defaultOn)
    }
    /// An upscaler from the ini that macOS does not offer (FSR 4 set on Linux or in the menu).
    var replacedUpscaler: String?

    init() {}

    init(ini values: [String: String]) {
        if let raw = values["upscaler"] {
            if Catalog.contains(Catalog.upscalers, raw) {
                upscaler = raw
            } else if raw == "fsr4" || raw == "fsr411" {
                // What the port itself does when FSR 4 is unsupported.
                upscaler = "fsr3"
                replacedUpscaler = raw
            }
        }
        if let raw = values["preset"], let value = Int(raw), Catalog.contains(Catalog.presets, value) {
            preset = value
        }
        sharpen = GameSettings.flag(values["sharpen"], sharpen)
        if let raw = values["sharpness"], let value = Double(raw) {
            sharpness = min(max(value, 0), 2)
        }
        objectMotion = GameSettings.flag(values["object_motion"], objectMotion)
        showFPS = GameSettings.flag(values["show_fps"], showFPS)
        if let raw = values["output_res"], Catalog.contains(Catalog.outputResolutions, raw) {
            outputRes = raw
        }
        if let raw = values["model_lod"], Catalog.contains(Catalog.modelDetail, raw) {
            modelLOD = raw
        }
        if let raw = values["live_resolution"], Catalog.contains(Catalog.liveResolution, raw) {
            liveResolution = raw
        }
        for index in effects.indices {
            effects[index].isOn = GameSettings.flag(values[effects[index].key], effects[index].isOn)
        }
    }

    private static func flag(_ value: String?, _ fallback: Bool) -> Bool {
        guard let value else { return fallback }
        return value == "1"
    }

    /// The edited keys in the Linux launcher's order (missing ones are appended in this order).
    var iniValues: [(key: String, value: String)] {
        var out: [(key: String, value: String)] = [
            (key: "upscaler", value: upscaler),
            (key: "preset", value: String(preset)),
            (key: "sharpen", value: sharpen ? "1" : "0"),
            (key: "sharpness", value: String(format: "%.2f", sharpness)),
            (key: "object_motion", value: objectMotion ? "1" : "0"),
            (key: "show_fps", value: showFPS ? "1" : "0"),
            (key: "output_res", value: outputRes),
            (key: "model_lod", value: modelLOD),
            (key: "live_resolution", value: liveResolution),
        ]
        for effect in effects {
            out.append((key: effect.key, value: effect.isOn ? "1" : "0"))
        }
        return out
    }
}

/// bbport.ini as the Linux launcher reads and writes it: `key=value` lines, `#` comments; edited
/// keys are rewritten in place, missing ones appended, everything else kept.
struct IniFile {
    var lines: [String] = []
    var values: [String: String] = [:]

    static func load(_ url: URL) -> IniFile {
        var ini = IniFile()
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return ini }
        var lines = text.components(separatedBy: "\n")
        if lines.last == "" { lines.removeLast() }
        ini.lines = lines.map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
        for line in ini.lines {
            guard let setting = IniFile.split(line) else { continue }
            ini.values[setting.key] = setting.value
        }
        return ini
    }

    /// (key, value) of a setting line, nil for comments and other lines.
    static func split(_ line: String) -> (key: String, value: String)? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { return nil }
        let key = line[..<eq].trimmingCharacters(in: .whitespaces)
        let value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
        return (key: key, value: value)
    }

    func text(applying edits: [(key: String, value: String)]) -> String {
        var pending = edits
        var out: [String] = []
        for line in lines {
            if let setting = IniFile.split(line), let index = pending.firstIndex(where: { $0.key == setting.key }) {
                out.append("\(setting.key)=\(pending[index].value)")
                // A key listed twice in the file gets the new value both times, as in Python.
                continue
            }
            out.append(line)
        }
        let written = Set(out.compactMap { IniFile.split($0)?.key })
        if lines.isEmpty {
            out.append("# bbport settings (in-game menu: F1, ` or § / L3+R3)")
        }
        pending.removeAll { written.contains($0.key) }
        for edit in pending {
            out.append("\(edit.key)=\(edit.value)")
        }
        return out.joined(separator: "\n") + "\n"
    }
}

/// QA builds never use protected Documents/Downloads paths or the production data folder.
/// Per-run bundle identifiers prevent LaunchServices from selecting an older QA copy.
func qaApplicationSupportURL(bundleIdentifier: String?, previewPath: String?) -> URL? {
    let prefix = "io.github.bbport.optimized.options-qa"
    guard let identifier = bundleIdentifier,
          identifier == prefix || identifier.hasPrefix(prefix + ".") else { return nil }
    let fallback = URL(fileURLWithPath: "/private/tmp/bbport-launcher-qa/" + identifier + "/data", isDirectory: true)
    guard let path = previewPath, path.hasPrefix("/") else { return fallback }
    let candidate = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL.resolvingSymlinksInPath()
    // Foundation resolves existing /private/tmp directories to the /tmp alias on macOS.
    let normalized = candidate.path.hasPrefix("/tmp/") ? "/private" + candidate.path : candidate.path
    let developmentState = "/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype/state/"
    guard normalized.hasPrefix("/private/tmp/") || normalized.hasPrefix(developmentState) else { return fallback }
    return URL(fileURLWithPath: normalized, isDirectory: true)
}
