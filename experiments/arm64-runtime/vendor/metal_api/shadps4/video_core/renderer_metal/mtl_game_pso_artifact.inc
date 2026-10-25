// SPDX-License-Identifier: GPL-2.0-or-later
// Included by the original PsoCache TU, after CreateWithArchive. Both game and
// helper producers use its one artifact implementation and publication policy.
namespace GamePso {
struct Attempt {
    id state = nil;
    bool handled{};
    bool archive_hit{};
};

Attempt TryArtifact(PsoCache& cache, void* descriptor, bool compute,
                    const CompiledShader& primary, const CompiledShader* fragment,
                    std::string& error) {
    Attempt result;
    const bool prepare = cache.ArtifactPreparation();
    // Preparation may not silently fall through to an untracked source PSO.
    result.handled = prepare;
    try {
    if (!primary.persistent_identity.valid() ||
        (fragment && !fragment->persistent_identity.valid())) {
        if (prepare) error = "current successful function identity unavailable";
        return result;
    }
    const auto descriptor_id = PsoCache::ArtifactDescriptorIdentity(descriptor, compute);
    const auto primary_id = primary.persistent_identity.hex();
    const auto fragment_id = fragment ? fragment->persistent_identity.hex() : std::string{};
    const std::array<std::string_view, 2> functions{primary_id, fragment_id};
    const auto recipe = CanonicalRecipe(compute, descriptor_id,
        std::span<const std::string_view>(functions).first(compute ? 1 : 2));
    if (recipe.empty()) {
        if (prepare) error = "current complete pipeline descriptor identity unavailable";
        return result;
    }
    const auto key = LocalAot::Sha(recipe.data(), recipe.size());
    HostPso::ArtifactOutcome outcome;
    auto raw = cache.CreateArtifactPipeline(descriptor, compute, key, prepare, outcome,
        error, prepare && cache.ArtifactCompilationAllowed());
    id state = (__bridge_transfer id)raw;
    if (prepare) {
        // Only digests/enums, no inherited environment, game bytes or account data.
        std::printf("Metal game PSO artifact event: {\"key\":\"%s\",\"descriptor\":\"%s\","
                    "\"primary\":\"%s\",\"fragment\":\"%s\",\"compute\":%s,"
                    "\"primary_route\":%u,\"fragment_route\":%u,\"state\":%u,"
                    "\"archive_sha256\":\"%s\"}\n",
                    key.c_str(), descriptor_id.c_str(), primary_id.c_str(), fragment_id.c_str(),
                    compute ? "true" : "false", unsigned(primary.persistent_identity.route),
                    fragment ? unsigned(fragment->persistent_identity.route) : 0u,
                    unsigned(outcome.state), outcome.archive_sha256.c_str());
    }
    if (outcome.state == HostPso::ArtifactState::Critical) {
        // The existing non-restart protection status; never bypass with source
        // fallback or report a partially prepared recipe as successful.
        std::_Exit(BB_ARM_EXIT_HOST_PROTECTION);
    }
    if (state && outcome.ready()) {
        result.state = state;
        result.handled = true;
        result.archive_hit = outcome.state == HostPso::ArtifactState::Hit;
    } else if (!prepare) {
        // Missing/stale artifacts are optional. The original factory below owns
        // runtime compilation and its main archive, including all failure rules.
        error.clear();
    }
    } catch (...) {
        if (!prepare) error.clear();
        else std::fputs("Metal game PSO candidate preparation failed\n", stderr);
    }
    return result;
}
} // namespace GamePso
