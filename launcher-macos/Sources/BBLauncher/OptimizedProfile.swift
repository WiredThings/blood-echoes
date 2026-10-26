// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Display choices are independent of the container resolution in bbport.ini. The community
/// wide patches set scene/UI viewports and camera coordinates; output_res must stay 1080p.
/// UI assets come from the selected game installation, independently of display choices.
struct OptimizedProfile {
    let prefs: LauncherPrefs
    let game: GameSettings
    let runtime: LauncherRuntime

    init(prefs: LauncherPrefs, game: GameSettings, runtime: LauncherRuntime = .legacy) {
        self.prefs = prefs
        self.game = game
        self.runtime = runtime
    }

    var resolution: String { prefs.ultrawide ? prefs.wideResolution : prefs.resolution }

    var aspect: String {
        if !prefs.ultrawide { return "16:9" }
        return ["2560x1080", "3440x1440"].contains(resolution) ? "21:9" : "32:9"
    }

    var settings: GameSettings {
        var settings = game
        settings.outputRes = prefs.ultrawide ? "1920x1080" : prefs.resolution
        settings.liveResolution = "0"
        if prefs.renderer == "metal" {
            if !["off", "metalfx"].contains(settings.upscaler) { settings.upscaler = "metalfx" }
            settings.objectMotion = false
            settings.sharpen = false
        } else if settings.upscaler == "metalfx" {
            settings.upscaler = "fsr3"
        }
        if prefs.ultrawide {
            settings.upscaler = "off"
            settings.preset = 0
        }
        return settings
    }

    var presentMode: String {
        prefs.renderer == "metal" && ["FifoRelaxed", "Immediate"].contains(prefs.presentMode) ? "Mailbox" : prefs.presentMode
    }

    var gpuProfileVariable: String { prefs.renderer == "metal" ? "BB_MTL_GPU_TIMING" : "BB_GPU_PROFILE" }
    var onlineActive: Bool { prefs.online && prefs.renderer == "metal" }

    /// Mirror the pinned Metal SceneResolution::ForPreset, including even-pixel rounding.
    /// The preview is not an override and does not change the selected quality or effects.
    var metalRenderResolution: String {
        let size = settings.outputRes.split(separator: "x").compactMap { Int($0) }
        guard size.count == 2 else { return settings.outputRes }
        if settings.upscaler == "off" { return "\(size[0]) × \(size[1])" }
        let scale = [1.0, 1.5, 1.7, 2.0, 3.0, 1.25][min(max(settings.preset, 0), 5)]
        let render = size.map { max(1, Int((Double($0) / scale / 2).rounded(.toNearestOrAwayFromZero))) * 2 }
        return "\(render[0]) × \(render[1])"
    }

    var environment: [String: String] {
        var env = [
            "BB_LANGUAGE": prefs.usesCommunityChinese ? "1" : prefs.language,
            "BB_CHINESE_SOURCE": prefs.usesCommunityChinese ? "community" : "official",
            "BB_NATIVE_GPU": prefs.renderer == "metal" ? "0" : "1", "BB_PREBUILT": "1",
            "BB_RENDERER": prefs.renderer,
            "BB_METAL_OBJECT_MOTION": prefs.renderer == "metal" && prefs.metalObjectMotion && settings.upscaler == "metalfx" ? "1" : "0",
            "BB_METAL_OCCLUSION": prefs.renderer == "metal" && prefs.metalOcclusion ? "1" : "0",
            "BB_SKIP_EMPTY_DRAWS": "1", "BB_VK_RECORD_THREADS": "1",
            "BB_FRAMES_AHEAD": "2", "BB_GPU_QOS": "1",
            "BB_MAC_UNIFIED_MEMORY": "0", "BB_PIPE_PARK_WAITS": "0",
            "BB_ONLINE": onlineActive ? "1" : "0",
            "BB_QUICKSAVE": prefs.quicksave ? "1" : "0",
            "BB_QUICKSAVE_SCANCODE": String(prefs.quicksaveKey.code),
            "BB_QUICKLOAD_SCANCODE": String(prefs.quickloadKey.code),
            "BB_MENU_SCANCODE": String(prefs.menuKey.code),
            "BB_UPSCALER": settings.upscaler, "BB_UPSCALE_PRESET": String(settings.preset),
            "BB_LIVE_RES": "0", "BB_PATCHES": "",
        ]
        env.merge(MetalOptimizations.environment(prefs: prefs, metalFXActive: settings.upscaler == "metalfx")) { _, selected in selected }
        if !runtime.supportsRecentStagingReuse { env.removeValue(forKey: "BB_MTL_HOT_STAGING_REUSE") }
        if runtime.requiresNativeMetalHost && prefs.renderer == "metal" {
            // FEX has no in-process Metal execution route. Keep any old optional
            // Rosetta preference stored, but never apply it to this engine.
            env["BB_METAL_ARM64"] = "1"
        }
        if prefs.usesCommunityChinese {
            env["BB_CHINESE_LANGUAGE"] = prefs.language
            env["BB_CHINESE_TRANSLATION_DIR"] = prefs.chineseTranslationFolder
        }
        if prefs.ultrawide {
            env["BB_PATCHES"] = "Resolution Patch \(resolution) (\(aspect))"
            env["BB_DMEM_MB"] = "9152"
        }
        return env
    }

    /// Keep visible controls authoritative even when an old developer entry names the same flag.
    func applyingEnvironment(to base: [String: String]) -> [String: String] {
        var env = base
        if prefs.renderer != "metal" { env = env.filter { !$0.key.hasPrefix("BB_MTL_") } }
        for option in MetalOptimizations.all { env.removeValue(forKey: option.id) }
        for key in ["BB_MTL_CLEAR_DISCARD_FILE", "BB_MTL_OVERWRITE_DISCARD_FILE", "BB_MTL_PROXY_CLEAR_FILE",
                    "BB_MTL_VERTEX_MATH_FILE", "BB_MTL_UPSCALE_ALPHA_FILE"] { env.removeValue(forKey: key) }
        // Diagnostic controls must also override stale entries when switched OFF.
        env.removeValue(forKey: "BB_GPU_PROFILE")
        env.removeValue(forKey: "BB_MTL_GPU_TIMING")
        env.removeValue(forKey: "BB_CHINESE_LANGUAGE")
        env.removeValue(forKey: "BB_CHINESE_TRANSLATION_DIR")
        if prefs.gpuProfile { env[gpuProfileVariable] = "1" }
        if prefs.renderer == "metal" {
            env.removeValue(forKey: "BB_VK_VALIDATION")
            env.removeValue(forKey: "BB_PREUPLOAD")
        }
        env.merge(environment) { _, selected in selected }
        if onlineActive { env["BB_SKIP_NETWORK_CHOICE"] = "0" }
        else {
            for key in Array(env.keys) where key.hasPrefix("BB_THD_") || key.hasPrefix("BB_SHADNET_") ||
                ["BB_NET_MODULE", "BB_NET_BACKEND", "SHADPS4_HTTP_HOST_OVERRIDES_JSON"].contains(key) {
                env.removeValue(forKey: key)
            }
        }
        return env
    }

    /// Ignore inherited debug input and stale paths from a different port. Explicit developer
    /// overrides are parsed later, then the app's display/save/runtime settings win.
    static func cleanEnvironment(_ inherited: [String: String]) -> [String: String] {
        inherited.filter { key, _ in
            !key.hasPrefix("BB_") && !["VK_DRIVER_FILES", "VK_ICD_FILENAMES",
                "DYLD_LIBRARY_PATH", "SDL_VULKAN_LIBRARY", "MESA_SHADER_CACHE_DIR", "PYTHON"].contains(key)
        }
    }
}
