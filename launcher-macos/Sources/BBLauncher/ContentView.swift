// SPDX-License-Identifier: GPL-2.0-or-later
// Everyday controls first; detailed settings stay in the Advanced drawer.
import AppKit
import Combine
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: LauncherModel
    @Environment(\.openWindow) private var openWindow
    @State private var advanced = false

    var body: some View {
        VStack(spacing: 0) {
            Form {
                if model.repoCheck.level != .ok {
                    RepositoryProblemSection()
                }
                GameSection()
                LanguageSection()
                BasicDisplaySection()
                QuicksaveSection()
                OnlineSection()
                KeyLegend()
                Section {
                    Button {
                        advanced.toggle()
                    } label: {
                        HStack {
                            Image(systemName: advanced ? "chevron.down" : "chevron.right")
                            Text(L10n.key("Advanced"))
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(L10n.key("Advanced"))
                    .accessibilityValue(advanced ? L10n.key("Expanded") : L10n.key("Collapsed"))
                }
                if advanced {
                    if model.prefs.renderer == "metal" { MetalOptimizationSection() }
                    AdvancedGameSection()
                    DisplaySection()
                    FrameRateSection()
                    UpscalerSection()
                        .disabled(model.prefs.ultrawide)
                    EffectsSection()
                    PerformanceSection()
                    ModsSection()
                    PatchesSection()
                    DeveloperSection()
                }
            }
            .formStyle(.grouped)
            .toggleStyle(.checkbox)
            .disabled(model.isRunning || model.uiModBusy || model.saveBusy)

            Divider()
            PlayBar(showLog: { openWindow(id: "log") })
        }
        .frame(minWidth: 640, minHeight: 840)
        .navigationTitle("Blood Echoes")
        .navigationSubtitle(model.statusText)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            model.refreshChecks()
        }
    }
}

struct LanguageSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Picker(L10n.key("Launcher language"), selection: $model.prefs.uiLanguage) {
                ForEach(L10n.languages) { Text(L10n.key($0.label)).tag($0.value) }
            }
            Picker(L10n.key("Game language"), selection: $model.prefs.language) {
                ForEach(Catalog.languages) { Text(L10n.key($0.label)).tag($0.value) }
            }
            if model.prefs.isChinese {
                Picker(L10n.key("Chinese text"), selection: $model.prefs.communityChinese) {
                    Text(L10n.key("Official Chinese (region unlock)")).tag(false)
                    Text(L10n.key("Community retranslation")).tag(true)
                }
                if model.prefs.usesCommunityChinese {
                    HStack {
                        Button(L10n.key("Import GOTY package…")) { model.chooseChineseTranslation() }
                            .disabled(model.isRunning || model.chineseTranslationBusy)
                        Button(L10n.key("Download from Nexus Mods")) { Links.open(Links.chineseTranslation) }
                        if model.chineseTranslationBusy { ProgressView().controlSize(.small) }
                    }
                    Label(L10n.key(model.chineseTranslationValid ? "Translation verified" : "Import the matching GOTY Chinese package to play."),
                          systemImage: model.chineseTranslationValid ? "checkmark.circle" : "exclamationmark.triangle")
                        .foregroundStyle(model.chineseTranslationValid ? Color.secondary : Color.orange)
                    if let problem = model.chineseTranslationProblem {
                        Text(problem).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
                    }
                    Text(L10n.key("Bloodborne Chinese Retranslation by Supuritto. Choose GOTY zh-cn for Simplified or zh-tw for Traditional Chinese."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        } header: {
            Text(L10n.key("Language"))
        } footer: {
            Text(L10n.key("Launcher language changes immediately. Game text changes on the next launch. Voice audio is unchanged."))
        }
    }
}

// MARK: - Play bar

struct PlayBar: View {
    @EnvironmentObject var model: LauncherModel
    let showLog: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Label {
                    Text(model.statusText)
                        .lineLimit(2)
                } icon: {
                    statusIcon
                }
                .font(.headline)
                if let progress = model.launchPreparation, !progress.finished {
                    HStack {
                        Text(L10n.key(progress.stageKey))
                            .lineLimit(1)
                        Spacer(minLength: 8)
                        Text("\(progress.percent)%")
                            .monospacedDigit()
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    ProgressView(value: Double(progress.percent), total: 100)
                        .tint(progress.failed ? .red : .accentColor)
                        .accessibilityLabel(L10n.key("Launch preparation"))
                        .accessibilityValue("\(progress.percent)%")
                        .help(L10n.key("Progress follows preparation stages, not elapsed time."))
                } else {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            Button(action: showLog) {
                Label(L10n.key("Log"), systemImage: "text.alignleft")
            }
            .controlSize(.large)
            if model.isRunning {
                Button(role: .destructive) {
                    model.stop()
                } label: {
                    Label(L10n.key("Stop"), systemImage: "stop.fill")
                        .frame(minWidth: 70)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .controlSize(.large)
                .keyboardShortcut(".", modifiers: .command)
                .disabled(model.state == .stopping)
            } else {
                Button {
                    model.play()
                } label: {
                    Label(L10n.key("Play"), systemImage: "play.fill")
                        .frame(minWidth: 70)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut("r", modifiers: .command)
                .disabled(!model.canPlay)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private var detail: String {
        switch model.state {
        case .running where model.job == .update, .stopping where model.job == .update:
            return L10n.key("Fetching from GitHub and rebuilding what changed. A new graphics driver takes longest.")
        case let .exited(status, bySignal) where model.job == .update:
            if status == 0 && !bySignal {
                return L10n.key("Up to date. If the log says the Mac app was rebuilt, quit and reopen it.")
            }
            return model.lastProblem ?? L10n.key("Details are in the log.")
        case .running, .stopping:
            return L10n.key("Prepared shaders are reused; newly encountered pipeline combinations may still compile.")
        case let .exited(status, bySignal) where status != 0 && !bySignal:
            return model.lastProblem ?? L10n.key("Details are in the log (Help ▸ Show the Last Run's Log).")
        default:
            return L10n.key("Settings apply on the next game launch.")
        }
    }

    @ViewBuilder private var statusIcon: some View {
        switch model.state {
        case .running:
            Image(systemName: "gamecontroller.fill").foregroundStyle(.green)
        case .stopping:
            Image(systemName: "hourglass").foregroundStyle(.orange)
        case let .exited(status, bySignal):
            if status == 0 || bySignal {
                Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
            } else {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
            }
        case .failed:
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
        case .idle:
            if model.canPlay {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            } else {
                Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
            }
        }
    }
}

// MARK: - Shared rows

/// A status line with a coloured symbol, for the folder checks.
struct CheckLabel: View {
    let check: Check

    var body: some View {
        Label {
            Text(check.message)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
        } icon: {
            switch check.level {
            case .ok:
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            case .warning:
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            case .error:
                Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
            }
        }
    }
}

/// A folder row: title, path, Choose…, Show in Finder and an optional reset.
struct FolderRow: View {
    let title: String
    let path: String
    var placeholder = L10n.key("Not chosen")
    let choose: () -> Void
    let reveal: () -> Void
    /// A custom folder that can go back to the default.
    var canReset = false
    var reset: () -> Void = {}

    var body: some View {
        LabeledContent {
            HStack(spacing: 6) {
                Button(L10n.key("Choose…"), action: choose)
                Button(action: reveal) {
                    Image(systemName: "folder")
                }
                .help(L10n.key("Show in Finder"))
                .disabled(path.isEmpty)
                if canReset {
                    Button(action: reset) {
                        Image(systemName: "arrow.uturn.backward")
                    }
                    .help(L10n.key("Back to the default folder"))
                }
            }
        } label: {
            Text(L10n.key(title))
            Text(path.isEmpty ? L10n.key(placeholder) : path)
                .truncationMode(.middle)
                .textSelection(.enabled)
        }
    }
}

// MARK: - Sections

struct RepositoryProblemSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            CheckLabel(check: model.repoCheck)
            HStack {
                SettingsLink {
                    Text(model.engineURL != nil ? L10n.key("Settings…") : L10n.key("Set the Repository…"))
                }
                Button(L10n.key("Check Again")) { model.refreshChecks() }
            }
        } header: {
            Text(model.engineURL != nil ? L10n.key("Before you play") : L10n.key("bbport repository"))
        }
    }
}

struct GameSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            FolderRow(title: L10n.key("Game folder"), path: model.prefs.gameFolder,
                      choose: { model.chooseGameFolder() },
                      reveal: { model.reveal(URL(fileURLWithPath: model.prefs.gameFolder, isDirectory: true)) })
            CheckLabel(check: model.gameCheck)

        } header: {
            Text(L10n.key("Game"))
        } footer: {
            Text(L10n.key("Choose your game folder containing eboot.bin and update 1.09."))
                .foregroundStyle(.secondary)
        }
    }
}

struct BasicDisplaySection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Toggle(L10n.key("Ultrawide"), isOn: $model.prefs.ultrawide)
            if model.prefs.ultrawide {
                Picker(L10n.key("Resolution"), selection: $model.prefs.wideResolution) {
                    ForEach(Catalog.wideResolutions) { Text(L10n.key($0.label)).tag($0.value) }
                }
            } else {
                Picker(L10n.key("Resolution"), selection: $model.prefs.resolution) {
                    ForEach(Catalog.outputResolutions) { Text(L10n.key($0.label)).tag($0.value) }
                }
            }
            Toggle(L10n.key("Fullscreen"), isOn: $model.prefs.fullscreen)
            if model.prefs.ultrawide && model.gameCheck.level == .ok {
                HStack(alignment: .center, spacing: 12) {
                    Toggle(L10n.key("Enable UI fix"), isOn: Binding(
                        get: { model.uiModStatus.enabled || model.uiModStatus.needsRecovery },
                        set: { model.setUIModEnabled($0) }))
                        .disabled(!model.uiModStatus.canEnable && !model.uiModStatus.canDisable)
                        .fixedSize()
                    FolderRow(title: L10n.key("UI mod folder"), path: model.prefs.uiModFolder,
                              choose: { model.chooseUIModFolder() },
                              reveal: { model.reveal(URL(fileURLWithPath: model.prefs.uiModFolder)) })
                        .disabled(model.uiModStatus.enabled || model.uiModStatus.needsRecovery)
                }
                CheckLabel(check: model.uiModStatus.check)
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.key("Optional: download and extract your preferred UI fix, then choose its folder to back up and install it. Uncheck Enable UI fix to restore the previous files."))
                    Link(destination: URL(string: "https://www.nexusmods.com/bloodborne/mods/202")!) {
                        Text(L10n.key("Ultrawide UI Fixes on Nexus Mods ↗"))
                            .underline()
                            .foregroundStyle(.blue)
                    }
                    .buttonStyle(.plain)
                    Text(L10n.key("Choose any UI layout you prefer. Turning off Ultrawide also restores your previous files. If those files already had a UI mod, that layout will remain. Stop the game before making changes."))
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        } header: {
            Text(L10n.key("Display"))
        } footer: {
            Text(model.prefs.ultrawide
                 ? L10n.key("Ultrawide uses native rendering; upscaling is disabled.")
                 : L10n.key("Resolution is the output size. Upscaling is in Advanced."))
                .foregroundStyle(.secondary)
        }
    }
}

struct QuicksaveSection: View {
    @EnvironmentObject var model: LauncherModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Section {
            Toggle(L10n.key("Enable quicksave / quickload"), isOn: $model.prefs.quicksave)
            Button(L10n.key("Save Manager…")) { openWindow(id: "saves") }
            if !model.saveStatus.isEmpty { Text(model.saveStatus).font(.caption).textSelection(.enabled) }
            if model.prefs.cloudSaves && !model.cloudSaveStatus.isEmpty {
                Text(model.cloudSaveStatus).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        } header: {
            Text(L10n.key("Save & load"))
        } footer: {
            Text(L10n.key("Going online archives old checkpoints and creates a new one. F2 saves in either mode; F3 always restores offline."))
                .foregroundStyle(.secondary)
        }
    }
}

struct OnlineSection: View {
    @EnvironmentObject var model: LauncherModel
    var body: some View {
        OnlineControls(network: model.onlineController)
            .environmentObject(model)
    }
}

private struct OnlineControls: View {
    @EnvironmentObject var model: LauncherModel
    @ObservedObject var network: OnlineController
    var body: some View {
        Section {
            Toggle(L10n.key("Play online (experimental)"), isOn: Binding(get: { model.prefs.online }, set: { model.setOnline($0) }))
                .onChange(of: model.prefs.online) { on in if on { network.load() } else { network.cancel() } }
            if model.prefs.online {
                LabeledContent(L10n.key("Server"), value: "The Hunter’s Dream")
                if model.prefs.renderer != "metal" {
                    Text(L10n.key("Online play currently requires the Metal backend."))
                        .font(.caption).foregroundStyle(.orange)
                }
                if let account = network.account {
                    HStack {
                        Label(account.name, systemImage: "person.crop.circle.badge.checkmark")
                        Spacer()
                        Button(L10n.key("Sign out")) { network.signOut() }.disabled(network.busy)
                    }
                } else {
                    HStack {
                        Button(L10n.key("Sign in on website…")) { network.signIn() }.disabled(network.busy)
                        if network.busy {
                            ProgressView().controlSize(.small)
                            Button(L10n.key("Cancel")) { network.cancel() }
                        }
                    }
                }
                if !network.code.isEmpty {
                    LabeledContent(L10n.key("Enter this code on the website")) {
                        Button { network.copyCode() } label: {
                            Label(network.code, systemImage: "doc.on.doc").monospaced()
                        }.help(L10n.key("Click to copy code")).accessibilityLabel(L10n.key("Copy pairing code"))
                    }
                }
                Button(L10n.key("Check server connection")) { network.checkConnection() }.disabled(network.busy)
                if !network.status.isEmpty { Text(network.status).font(.caption).textSelection(.enabled) }
            }
        } header: {
            Text(L10n.key("Online"))
        } footer: {
            if model.prefs.online {
                Text(L10n.key("Online and offline share saves. Quickload switches to offline until you enable online again. Multiplayer compatibility is experimental."))
            }
        }
        .onAppear { if model.prefs.online { network.load() } }
    }
}

struct AdvancedGameSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            FolderRow(title: L10n.key("Saves folder"),
                      path: model.prefs.userFolder.isEmpty ? L10n.format("Default: %@", String(describing: model.userFolderURL.path)) : model.prefs.userFolder,
                      choose: { model.chooseUserFolder() },
                      reveal: { model.reveal(model.userFolderURL) },
                      canReset: !model.prefs.userFolder.isEmpty,
                      reset: { model.prefs.userFolder = "" })
        } header: {
            Text(L10n.key("Saves"))
        } footer: {
            Text(L10n.key("The default saves folder is shared with your existing bbport installation."))
        }
    }
}

struct DisplaySection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Picker(L10n.key("Renderer backend"), selection: $model.prefs.renderer) {
                ForEach(Catalog.renderers) {
                    Text(L10n.key($0.label)).tag($0.value)
                        .disabled(!model.runtimeCapabilities.supports(renderer: $0.value))
                }
            }
            .onChange(of: model.prefs.renderer) { _ in model.normalizeRendererSettings() }
            if model.runtimeCapabilities.isFexArm64 {
                Text(L10n.key("This ARM64 runtime currently supports Metal only."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if model.prefs.renderer == "metal" {
                Toggle(L10n.key("Occlusion culling (experimental)"), isOn: $model.prefs.metalOcclusion)
                Text(L10n.key("Conservative visibility queries. Disable if objects or lights flicker; applies on next launch."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Picker(selection: $model.prefs.display) {
                Text(L10n.key("Main display")).tag("")
                ForEach(NSScreen.screens.map(\.localizedName), id: \.self) { Text($0).tag($0) }
                if !model.prefs.display.isEmpty && !NSScreen.screens.contains(where: { $0.localizedName == model.prefs.display }) {
                    Text(L10n.format("%@ (not connected)", String(describing: model.prefs.display))).tag(model.prefs.display)
                }
            } label: {
                Text(L10n.key("Display"))
            }
            Picker(selection: Binding(get: { model.profile.presentMode }, set: { model.prefs.presentMode = $0 })) {
                ForEach(Catalog.presentModes(renderer: model.prefs.renderer)) { Text(L10n.key($0.label)).tag($0.value) }
            } label: {
                Text(L10n.key("Present mode"))
            }
            if model.prefs.renderer != "metal" {
                Toggle(L10n.key("Allow HDR"), isOn: $model.prefs.hdr)
            }
            Toggle(L10n.key("Show FPS"), isOn: $model.game.showFPS)
        } header: {
            Text(L10n.key("Display"))
        }
    }
}

struct FrameRateSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Picker(selection: $model.prefs.fpsMode) {
                ForEach(Catalog.fpsModes) { Text(L10n.key($0.label)).tag($0.value) }
            } label: {
                Text(L10n.key("Mode"))
            }
            LabeledContent {
                HStack(spacing: 4) {
                    TextField(L10n.key("FPS limit"), value: $model.prefs.fpsLimit, format: .number)
                        .labelsHidden()
                        .multilineTextAlignment(.trailing)
                        .frame(width: 56)
                    Stepper(L10n.key("FPS limit"), value: $model.prefs.fpsLimit, in: 0...480, step: 5)
                        .labelsHidden()
                }
            } label: {
                Text(L10n.key("FPS limit"))
                Text(L10n.key("0: the display refresh rate (up to 120 Hz); set a number for another limit"))
            }
        } header: {
            Text(L10n.key("Frame rate"))
        } footer: {
            Text(L10n.key("Unlocked is the tested M2 setting. The 60 and 90 modes use fixed-timestep patches."))
                .foregroundStyle(.secondary)
        }
    }
}

struct UpscalerSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        let upscaler = model.game.upscaler
        Section {
            Picker(selection: $model.game.upscaler) {
                ForEach(Catalog.upscalers.filter { model.prefs.renderer == "metal" ? ["off", "metalfx"].contains($0.value) : $0.value != "metalfx" }) {
                    Text(L10n.key($0.label)).tag($0.value)
                }
            } label: {
                Text(L10n.key("Upscaler"))
                Text(hint(for: upscaler))
            }
            Picker(L10n.key("Preset"), selection: $model.game.preset) {
                ForEach(Catalog.presets) { Text(L10n.key($0.label)).tag($0.value) }
            }
            .disabled(upscaler == "off" || upscaler == "taa")
            if model.prefs.renderer == "metal" && !model.prefs.ultrawide {
                LabeledContent(L10n.key("Scene render resolution")) {
                    Text(model.profile.metalRenderResolution).monospacedDigit()
                }
            }
            if model.prefs.renderer != "metal" {
                Toggle(L10n.key("Sharpening (RCAS)"), isOn: $model.game.sharpen)
                    .disabled(upscaler == "off")
                LabeledContent(L10n.key("Sharpness")) {
                    HStack {
                        Slider(value: $model.game.sharpness, in: 0...2, step: 0.05)
                            .frame(minWidth: 140)
                        Text(String(format: "%.2f", model.game.sharpness))
                            .monospacedDigit()
                            .frame(width: 36, alignment: .trailing)
                    }
                }
                .disabled(upscaler == "off" || !model.game.sharpen)
            }
            if model.prefs.renderer == "metal" {
                Toggle(isOn: $model.prefs.metalObjectMotion) {
                    Text(L10n.key("Game object motion vectors (experimental)"))
                    Text(L10n.key("Preserves game velocity; motion blur follows the Effects setting. Restart required."))
                }
                .disabled(upscaler != "metalfx")
            } else {
                Toggle(isOn: $model.game.objectMotion) {
                    Text(L10n.key("Object motion vectors"))
                    Text(L10n.key("Less ghosting on characters; costs about 10% FPS"))
                }
            }
        } header: {
            Text(L10n.key("Upscaler"))
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if model.prefs.renderer == "metal" {
                    Text(L10n.key("MetalFX uses camera motion and supported object velocity."))
                }
                if let replaced = model.game.replacedUpscaler {
                    Text(L10n.format("Replaced unsupported upscaler %@ with %@.", replaced, model.prefs.renderer == "metal" ? "MetalFX" : "FSR 3.1"))
                }
                if model.prefs.ultrawide { Text(L10n.key("Ultrawide uses native rendering; upscaling is disabled.")) }
            }
            .foregroundStyle(.secondary)
        }
    }

    private func hint(for upscaler: String) -> String {
        switch upscaler {
        case "fsr3": return L10n.key("AMD FSR 3.1 temporal upscaling")
        case "taa": return L10n.key("Anti-aliasing at the output resolution, no FSR model")
        case "metalfx": return L10n.key("Apple MetalFX temporal upscaling; Native AA renders at 1:1")
        default: return L10n.key("No upscaling or temporal anti-aliasing")
        }
    }
}

struct EffectsSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Picker(L10n.key("Model detail"), selection: $model.game.modelLOD) {
                ForEach(Catalog.modelDetail) { Text(L10n.key($0.label)).tag($0.value) }
            }
            if ["1", "2"].contains(model.game.modelLOD) {
                Text(L10n.key("Forced lower detail can hide walls or floors. Use As in the game if geometry is missing."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Toggle(isOn: $model.prefs.gameMenu) {
                Text(L10n.key("Settings in the game's System menu"))
                Text(L10n.key("The port's display, effects and patch settings as pages after Screen/Sound"))
            }
            Toggle(isOn: $model.prefs.skipNetworkChoice) {
                Text(L10n.key("Skip the online/offline screen"))
                Text(L10n.key("The game starts straight at its main menu, offline"))
            }
            .disabled(model.prefs.online)
            ForEach($model.game.effects) { $effect in
                Toggle(isOn: $effect.isOn) {
                    Text(L10n.key(effect.title))
                    if let note = effect.note {
                        Text(L10n.key(note))
                    }
                }
            }
        } header: {
            Text(L10n.key("Game effects"))
        }
    }
}

struct PerformanceSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Picker(selection: $model.prefs.drawPipe) {
                ForEach(Catalog.drawPipe) { Text(L10n.key($0.label)).tag($0.value) }
            } label: {
                Text(L10n.key("Two-stage GPU pipeline"))
            }
            if model.prefs.renderer != "metal" {
                Picker(selection: $model.prefs.preupload) {
                    ForEach(Catalog.preupload) { Text(L10n.key($0.label)).tag($0.value) }
                } label: {
                    Text(L10n.key("Background upload of game data"))
                    Text(L10n.key("Full can mean fewer hitches when areas load"))
                }
            }
            Picker(L10n.key("GPU data readbacks by the CPU"), selection: $model.prefs.readbacks) {
                ForEach(Catalog.readbacks) { Text(L10n.key($0.label)).tag($0.value) }
            }
        } header: {
            Text(L10n.key("Performance"))
        }
    }
}

struct ModsSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            Toggle(L10n.key("Load mods"), isOn: $model.prefs.modsEnabled)
            FolderRow(title: L10n.key("Mods folder"), path: model.modsFolderURL.path,
                      choose: { model.chooseModsFolder() },
                      reveal: { model.reveal(model.modsFolderURL) },
                      canReset: !model.prefs.modsFolder.isEmpty,
                      reset: { model.prefs.modsFolder = "" })
            if model.mods.isEmpty {
                Text(L10n.key("No mods"))
                    .foregroundStyle(.secondary)
            }
            ForEach($model.mods) { $mod in
                HStack {
                    Text(mod.name)
                    Spacer()
                    Button {
                        model.moveMod(mod.name, by: -1)
                    } label: {
                        Image(systemName: "chevron.up")
                    }
                    .buttonStyle(.borderless)
                    .help(L10n.key("Load earlier"))
                    Button {
                        model.moveMod(mod.name, by: 1)
                    } label: {
                        Image(systemName: "chevron.down")
                    }
                    .buttonStyle(.borderless)
                    .help(L10n.key("Load later"))
                    Toggle(mod.name, isOn: $mod.isOn)
                        .labelsHidden()
                }
            }
            .disabled(!model.prefs.modsEnabled)
            Button(L10n.key("Refresh the List")) {
                model.saveModProfile()
                model.refreshMods()
            }
        } header: {
            Text(L10n.key("Mods"))
        } footer: {
            Text(L10n.key("Extract each mod into its own folder (with dvdroot_ps4, or chr/, parts/, … directly). When files collide, the mod lower in the list wins. Applied at start."))
                .foregroundStyle(.secondary)
        }
    }
}

struct PatchesSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {
            FolderRow(title: L10n.key("Patches folder"), path: model.patchesFolderURL.path,
                      choose: { model.choosePatchesFolder() },
                      reveal: { model.reveal(model.patchesFolderURL) },
                      canReset: !model.prefs.patchesFolder.isEmpty,
                      reset: { model.prefs.patchesFolder = "" })
            if model.patches.isEmpty {
                Text(L10n.key("No patches"))
                    .foregroundStyle(.secondary)
            }
            ForEach($model.patches) { $patch in
                Toggle(isOn: $patch.isOn) {
                    Text(patch.title)
                    Text(patch.subtitle)
                }
                .help(patch.note ?? "")
                .disabled(patch.title.hasPrefix("Resolution Patch "))
            }
            Button(L10n.key("Refresh the List")) {
                model.savePatchProfile()
                model.refreshPatches()
            }
        } header: {
            Text(L10n.key("Third-party patches"))
        } footer: {
            Text(L10n.key("shadPS4-format patches for version 01.09. Resolution patches are controlled by the Display section."))
                .foregroundStyle(.secondary)
        }
    }
}

struct DeveloperSection: View {
    @EnvironmentObject var model: LauncherModel

    var body: some View {
        Section {

            Toggle(isOn: $model.prefs.frameStats) {
                Text(L10n.key("Frame statistics in the log"))
            }
            Toggle(isOn: $model.prefs.gpuProfile) {
                Text(L10n.key("GPU profile in the log"))
                Text(L10n.key("Diagnostic timing can lower FPS; leave off for normal play."))
            }
            if model.prefs.renderer != "metal" {
                Toggle(isOn: $model.prefs.vkValidation) {
                    Text(L10n.key("Vulkan validation layers"))
                    Text(L10n.key("Much slower; needs the validation layers installed"))
                }
            }
            TextField(text: $model.prefs.extraEnv, prompt: Text("NAME=value NAME2=value")) {
                Text(L10n.key("Extra variables"))
            }
        } header: {
            Text(L10n.key("Developer"))
        } footer: {
            Text(L10n.key("Extra variables are space-separated. The app manages resolution, renderer, saves and input settings."))
                .foregroundStyle(.secondary)
        }
    }
}
