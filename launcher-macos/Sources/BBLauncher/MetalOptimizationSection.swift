// SPDX-License-Identifier: GPL-2.0-or-later
import SwiftUI

struct MetalOptimizationSection: View {
    @EnvironmentObject var model: LauncherModel
    @State private var showAdopted = false
    @State private var showExperiments = true

    var body: some View {
        Section {
            Text(L10n.key("Applies on the next game launch. Resolution, effects and cache capacities are not changed by these defaults."))
                .font(.caption).foregroundStyle(.secondary)
            if model.runtimeCapabilities.requiresNativeMetalHost {
                Text(L10n.key("This runtime requires a native ARM64 Metal renderer host."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if model.runtimeCapabilities.automaticMetalPreparation {
                Text(L10n.key("Metal libraries are prepared on this Mac when needed; unchanged results are reused."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            DisclosureGroup(L10n.key("Adopted optimizations (default on)"), isExpanded: $showAdopted) {
                ForEach(MetalOptimizations.visibleOptions(MetalOptimizations.adopted, runtime: model.runtimeCapabilities)) { option in row(option) }
            }
            DisclosureGroup(L10n.key("Experimental optimizations (default off)"), isExpanded: $showExperiments) {
                ForEach(MetalOptimizations.visibleOptions(MetalOptimizations.optionalExperiments, runtime: model.runtimeCapabilities)) { option in row(option) }
            }
            Button(L10n.key("Restore recommended optimization defaults")) {
                model.prefs.metalOptimizations = MetalOptimizations.visibleDefaults(runtime: model.runtimeCapabilities, preserving: model.prefs.metalOptimizations)
            }
        } header: {
            Text(L10n.key("Metal optimizations"))
        }
    }

    private func row(_ option: MetalOptimization) -> some View {
        let metalFXActive = model.profile.settings.upscaler == "metalfx"
        let available = MetalOptimizations.dependenciesMet(option, prefs: model.prefs, metalFXActive: metalFXActive)
        let requirements = (option.requiresMetalFX && !metalFXActive ? ["MetalFX"] : []) + option.requires.compactMap { id in
            MetalOptimizations.all.first { $0.id == id }.map { L10n.key($0.title) }
        }
        return Toggle(isOn: Binding(
            get: { MetalOptimizations.enabled(option, prefs: model.prefs, metalFXActive: metalFXActive) },
            set: { model.prefs.metalOptimizations[option.id] = $0 }
        )) {
            HStack {
                Text(L10n.key(option.title))
                Spacer()
                Text(L10n.key(option.evidence.rawValue))
                    .font(.caption)
                    .foregroundStyle(option.evidence.experimental ? Color.orange : Color.secondary)
            }
            if !option.defaultOn {
                Text(L10n.key(option.note)).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !available {
                Text(L10n.format("Requires: %@", requirements.joined(separator: ", ")))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .disabled(!available)
        .help(option.id + "\n" + L10n.key(option.note))
        .accessibilityIdentifier(option.id)
    }
}
