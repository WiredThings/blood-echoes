#!/usr/bin/env python3
"""Thin composition of existing normal-layout QA controllers; explicit plan/run.

Execution requires both --execute and --reviewed-code after independent review.
All admission, source cloning, environment, monitoring, F3 and owned cleanup use
existing controllers. It neither forges a receipt nor implements frontend policy.
"""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/Shared/BloodEchoesDevelopment/arm64-runtime-prototype")
QA_TAG = "1793046050156764000"
RECEIPT = PROJECT / f"state/gui-root-{QA_TAG}/optimized-launcher/out/launch-conditions.json"
SOURCE_STAGE = PROJECT / f"apps/Blood Echoes ARM64 GUI QA {QA_TAG}.app/Contents/Resources/bbport"
POLICY_APP = PROJECT / "evidence/startup-registration-1793198075786342000/Blood Echoes.app"
VERTEX_FLAG = "BB_MTL_RELAX_VERTEX"


def within(value, root):
    value = Path(value)
    if not value.is_absolute() or not value.is_relative_to(root) or not value.resolve().is_relative_to(root):
        raise ValueError(f"plan input must remain under {root}")
    return value


def read_object(path):
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("plan metadata must be a bounded plain file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("plan metadata must be an object")
    return value


def vertex_on_f3_request(args):
    enabled = bool(getattr(args, "vertex_on_f3", False))
    if enabled and (args.phase != "f3" or args.vertex_ab or getattr(args, "resume_from", None) is not None):
        raise ValueError("--vertex-on-f3 requires only --phase f3, without --vertex-ab or --resume-from")
    return enabled


def selected_applications(args):
    app = within(args.app, PROJECT / "evidence")
    vertex = bool(getattr(args, "vertex_ab", False))
    vertex_on_f3 = vertex_on_f3_request(args)
    requested_baseline = args.baseline_app
    baseline = within(requested_baseline or (app if vertex or vertex_on_f3 else POLICY_APP), PROJECT / "evidence")
    if app.suffix != ".app" or baseline.suffix != ".app":
        raise ValueError("normal-layout staged applications required")
    if vertex and baseline.resolve() != app.resolve():
        raise ValueError("--vertex-ab requires the same candidate app, not a different baseline")
    if vertex_on_f3 and baseline.resolve() != app.resolve():
        raise ValueError("--vertex-on-f3 uses only the single candidate app")
    return app, baseline


def vertex_source(environment):
    if VERTEX_FLAG in environment and environment[VERTEX_FLAG] != "0":
        raise ValueError("--vertex-ab source receipt must omit the vertex flag or contain exactly 0")


def vertex_delta(source_environment, base, final, variant, *, functional_f3=False):
    """Permit one exact compiler control after the ordinary environment admission."""
    vertex_source(source_environment)
    if variant not in ("A", "B"):
        raise ValueError("unknown vertex comparison variant")
    if (VERTEX_FLAG in base) != (VERTEX_FLAG in source_environment) or base.get(VERTEX_FLAG) != source_environment.get(VERTEX_FLAG):
        raise ValueError("vertex flag changed before the ordinary environment check")
    expected = dict(base)
    if variant == "B" and not functional_f3:
        expected[VERTEX_FLAG] = "1"
    if final != expected:
        raise ValueError("unreviewed environment mutation outside the exact vertex control")
    changes = {} if expected == base else {VERTEX_FLAG: dict(before=base.get(VERTEX_FLAG), after="1")}
    return dict(mode="same-binary-vertex", variable=VERTEX_FLAG, variant=variant,
                before=base.get(VERTEX_FLAG), after=final.get(VERTEX_FLAG),
                source_present=VERTEX_FLAG in source_environment,
                allowed_experiment_variables=[VERTEX_FLAG], actual_experiment_changes=changes,
                ordinary_environment_checked_first=True, functional_f3_default_off=functional_f3)


def vertex_on_f3_delta(source_environment, base, final, *, functional_f3):
    if not functional_f3:
        raise ValueError("explicit vertex-on control is only a functional F3 test")
    # Reuse the exact one-field validator; do not change old F3/default-off logic.
    evidence = vertex_delta(source_environment, base, final, "B", functional_f3=False)
    return dict(evidence, mode="vertex-on-functional-f3", functional_f3_default_off=False,
                functional_f3_explicit_on=True, comparison_sample=False)


def controlled_environment(driver, source, case, formal, variant, *, vertex_ab=False, functional_f3=False,
                           vertex_on_f3=False):
    if vertex_on_f3 and (vertex_ab or not functional_f3 or variant != "B"):
        raise ValueError("explicit vertex-on control requires the single candidate functional F3 path")
    base = driver.execution_environment(source, case, formal, functional_f3=functional_f3)
    # Run the original full allowlist check BEFORE adding the single compiler control.
    changes, allowed = driver.benchmark_changes(source, case, base, formal=formal,
                                               functional_f3=functional_f3)
    if not vertex_ab and not vertex_on_f3:
        return base, changes, allowed, None
    vertex_source(source["environment"])
    final = dict(base)
    if vertex_on_f3 or (variant == "B" and not functional_f3):
        final[VERTEX_FLAG] = "1"
    experiment = (vertex_on_f3_delta(source["environment"], base, final, functional_f3=functional_f3)
                  if vertex_on_f3 else vertex_delta(source["environment"], base, final, variant, functional_f3=functional_f3))
    changes = dict(changes, **experiment["actual_experiment_changes"])
    return final, changes, sorted(set(allowed) | {VERTEX_FLAG}), experiment


def plan(args):
    app, baseline = selected_applications(args)
    vertex_on_f3 = vertex_on_f3_request(args)
    stage = within(args.source_stage, PROJECT / "apps")
    receipt = within(args.receipt, PROJECT / "state")
    record = read_object(receipt)
    if record is None:
        raise ValueError("existing original Swift receipt is required")
    env = record.get("environment", {})
    if record.get("producer") != "original-swift-LauncherModel.start-final-environment":
        raise ValueError("original Swift launch intent required")
    if env.get("BB_AUDIO") != "none" or env.get("BB_ONLINE") != "0":
        raise ValueError("existing silent offline QA selection required")
    if args.vertex_ab or vertex_on_f3:
        vertex_source(env)
    source_parity = read_object(stage / "SOURCE-PARITY.json")
    target_stage = app / "Contents/Resources/bbport"
    target_parity = read_object(target_stage / "SOURCE-PARITY.json")
    old = (source_parity or {}).get("effective_sources", {})
    new = (target_parity or {}).get("effective_sources", {})
    differences = sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
    command = [sys.executable, str(ROOT / "tools/run_qa_launch_benchmark.py"),
               "--receipt", str(receipt), "--stage", str(stage),
               "--formal-stage", str(app), "--cache-mode", "empty",
               "--startup-seconds", "1800", "--scene-seconds", "60"]
    return {
        "schema": 1, "status": "plan-only-no-game", "execution_available": True,
        "execution_requires_independent_review_and_explicit_flags": True,
        "admission_performed": False, "production_data_read_or_modified": False,
        "candidate_app": str(app), "baseline_app": str(baseline),
        "comparison_mode": "vertex-on-functional-f3" if vertex_on_f3 else "same-binary-vertex" if args.vertex_ab else "old-new-app",
        "vertex_control": (dict(variable=VERTEX_FLAG, source=env.get(VERTEX_FLAG), F3="1",
                                functional_f3_explicit_on=True, comparison_sample=False,
                                allowed_experiment_variables=[VERTEX_FLAG]) if vertex_on_f3 else
                           dict(variable=VERTEX_FLAG, source=env.get(VERTEX_FLAG),
                                 A=env.get(VERTEX_FLAG), B="1", F3=env.get(VERTEX_FLAG),
                                 independent_private_cache_roots=True,
                                 allowed_experiment_variables=[VERTEX_FLAG]) if args.vertex_ab else None),
        "source_receipt": str(receipt), "source_stage": str(stage),
        "resume_from": str(args.resume_from) if getattr(args, "resume_from", None) is not None else None,
        "qa_source_user": env.get("BB_USER_DIR"), "qa_source_gpu": env.get("BB_GPU_USER_DIR"),
        "qa_source_data": env.get("BB_DATA_DIR"),
        "source_receipt_edited": False,
        "script_parity": {
            "candidate_metadata_present": target_parity is not None,
            "different_script_names": differences,
            "qualification": "Metadata comparison only. Existing admit_formal checks actual code, signatures and all sixteen scripts; only the exact reviewed optional-UI SHA pair and existing exact run.sh projection may differ. This plan adds no exception.",
        },
        "existing_cold_or_scene_command_after_review": command,
        "existing_f2_f3_command_after_review": command + ["--functional-f3"],
        "thin_controller_command_after_review": [sys.executable, str(Path(__file__).resolve()),
            "--execute", "--reviewed-code", "--app", str(app), "--baseline-app", str(baseline),
            "--phase", args.phase] + (["--vertex-ab"] if args.vertex_ab else []) +
            (["--vertex-on-f3"] if vertex_on_f3 else []) +
            (["--resume-from", str(args.resume_from)] if getattr(args, "resume_from", None) is not None else []),
        "warm_series": {
            "reuse": ["qa_launch_conditions.load", "run_qa_launch_benchmark.admit",
                      "run_qa_launch_benchmark.admit_formal", "qa_launch_clone.create",
                      "run_qa_launch_benchmark.execution_conditions",
                      "run_qa_launch_benchmark.execution_environment",
                      "run_qa_launch_benchmark.benchmark_changes",
                      "run_qa_launch_benchmark.sample"],
            "cache_policy": "Existing formal-stage CLI starts an empty private GPU case. A thin controller must reuse that exact admitted private case for subsequent warm launches; never seed formal caches or edit the receipt.",
            "sequence": (["single candidate explicit vertex-on F2/F3 session"] if vertex_on_f3 else
                         ["baseline private preparation", "candidate private preparation",
                          "matched warmed A/B/B/A scene samples", "separate candidate F2/F3 session"]),
            "required_observations": ["actual nested CPU/GPU paths and hashes", "whole-app seal before/after",
                                      "source QA savedata digest unchanged", "scene camera and draw count",
                                      "preparation/module/PSO/source-compile counters", "time to real scene",
                                      "FPS/p95/p99/GPU time", "owned-tree peak and scene footprint"],
        },
        "death_regression": {
            "separate_from_f3": True,
            "existing_contract": str(ROOT / "tests/private_death_contract.h"),
            "existing_instrumentation": str(ROOT / "tests/private_death_runtime.h"),
            "known_passed_run": str(PROJECT / "evidence/private-death-native-1793068526046053000/run-1793068777443668000"),
            "requirements": ["private overlay rebuilt from current candidate CPU source/objects",
                             "existing signature-checked FlipperUpdate/SetHp guest thunk",
                             "private world-warper marker and same session nonce",
                             "file-only pad, quicksave=0, two-command cap",
                             "observe HP zero, world unload/loading, new stable healthy player twice"],
            "do_not_reuse_old_binary": True,
        },
        "safety": ["no Cua, screenshots, screen recording or profiler",
                   "audio remains none; graphics/effects/scene conditions unchanged",
                   "lock/severe pressure/swap-growth policy remains in outer QA harness",
                   "one game tree at a time; cleanup only owned processes",
                   "no formal saves/settings/cache access or game launches by this command"],
        "remaining_review": ["independent review of exact optional-UI admission and this thin controller",
                             "current-source private death overlay, separate from F3/performance"],
    }


def schedule(phase, *, vertex_ab=False):
    if phase not in ("performance", "f3", "all"):
        raise ValueError("unknown QA phase")
    rows = []
    if phase != "f3":
        rows += [("prepare-A", "A", False), ("prepare-B", "B", False)]
        rows += [("warm-A1", "A", False), ("warm-B1", "B", False),
                 ("warm-B2", "B", False), ("warm-A2", "A", False)]
    if phase != "performance":
        rows += [("functional-F2-F3", "A" if vertex_ab else "B", True)]
    return rows


def matched_summary(rows):
    import math
    import statistics
    samples = [row for row in rows if row["phase"].startswith("warm-")]
    if [row["variant"] for row in samples] != ["A", "B", "B", "A"]:
        raise ValueError("complete warmed ABBA sequence required")
    if any(row.get("status") != "sampled" for row in samples):
        raise ValueError("incomplete or protection-stopped sample is not an ABBA result")
    reference = samples[0]["scene_sample"]
    for row in samples:
        current = row["scene_sample"]
        for key in ("cam_x", "cam_y", "cam_z"):
            difference = abs(float(current["camera"][key]) - float(reference["camera"][key]))
            if not math.isfinite(difference) or difference > 0.05:
                raise ValueError("stationary scene camera did not match")
        if abs(current["draws_median"] - reference["draws_median"]) > max(2, reference["draws_median"] * .02):
            raise ValueError("draw workload did not match within 2 percent")
    result = {}
    for variant in ("A", "B"):
        group = [row["scene_sample"] for row in samples if row["variant"] == variant]
        result[variant] = {key: statistics.mean(row[key] for row in group)
                           for key in ("fps", "p95_ms", "p99_ms", "gpu_ms", "combined_footprint_mib")}
    result["fps_change_percent"] = (result["B"]["fps"] / result["A"]["fps"] - 1) * 100
    result["scene_footprint_change_mib"] = result["B"]["combined_footprint_mib"] - result["A"]["combined_footprint_mib"]
    result["qualification"] = ("Matched short stationary ABBA, camera tolerance 0.05 and draw-count tolerance 2%; "
        "not pixel, combat, route, networking or sustained-memory acceptance. Summed footprints may double-count shared pages.")
    return result


def resume_prepare_a(root, source, formal, native, driver, *, vertex_ab, phase):
    """Read-only recovery of one admitted, protection-stopped private preparation.

    No receipt/marker is manufactured. Reconstruct only the existing clone's
    allowlisted private values, then require the ordinary execution admission to
    reproduce every recorded environment byte before returning a driver case.
    """
    import os
    import re
    import stat
    from qa_launch_conditions import Reader, canonical, CONFIG_NAMES
    from qa_launch_clone import ALLOWED_CHANGES, inventory_savedata, _tree_inventory

    def need(ok, why):
        if not ok:
            raise ValueError("resume prepare-A: " + why)

    need(vertex_ab and phase in ("performance", "all"), "only this same-binary performance sequence may resume")
    root = canonical(root, directory=True, within=PROJECT / "evidence")
    need(root.parent == PROJECT / "evidence" and re.fullmatch(r"upstream-refresh-game-[0-9]{1,24}", root.name),
         "owned original regression evidence root required")
    need({p.name for p in root.iterdir()} == {"summary.json", "prepare-A"}, "only interrupted first preparation may resume")
    run = canonical(root / "prepare-A", directory=True, within=root)
    reader = Reader()
    summary = reader.json(root / "summary.json")
    prior = reader.json(run / "result.json")
    recorded = reader.json(run / "conditions.json")
    need(summary.get("status") == prior.get("status") == "incomplete" and summary.get("runs") == [prior] and
         summary.get("comparison_mode") == "same-binary-vertex", "one incomplete prepare-A record required")
    need(prior.get("phase") == "prepare-A" and prior.get("variant") == "A" and
         prior.get("scene_ready") is None and "scene_sample" not in prior and
         prior.get("source_save_unchanged") is True and prior.get("functional_f3_requested") is False and
         prior.get("performance_sample") is True, "no prior scene/F3 or changed source saves allowed")
    need(prior.get("stop") in {"screen locked", "unexpected >2GiB swap growth",
         "unexpected >1GiB swap growth within5s", "severe pressure sustained5s"}, "previous stop was not original protection")
    from qa_memory_policy import MemoryPolicy
    need(prior.get("memory_policy") == summary.get("policy") == MemoryPolicy.MANIFEST,
         "original unchanged protection policy required")
    receipt_sha = driver.sha(source["receipt"])
    reader.read(source["receipt"], expected=receipt_sha)
    need(prior.get("source_receipt_sha256") == summary.get("source_receipt_sha256") == receipt_sha and
         recorded.get("source_receipt") == str(source["receipt"]), "source receipt changed")
    need(prior.get("native_identity") == native and prior.get("formal_native_identity") == formal["identity"] and
         set(formal["identity"]["runtime_sha256"]) == driver.FORMAL_ROLES,
         "admitted source/native eight-role application identity changed")
    need(all(prior.get(k) is v for k, v in {"silent": True, "Cua": False, "screen_capture": False,
         "profiler": False, "formal_data_modified": False, "quality_or_effects_changed": False}.items()),
         "original isolated silent comparison required")
    old_env = recorded.get("environment")
    need(isinstance(old_env, dict) and all(type(k) is str and type(v) is str for k, v in old_env.items()),
         "recorded string environment required")
    data = canonical(recorded.get("private_data"), directory=True, within=PROJECT / "state")
    user = canonical(recorded.get("private_user"), directory=True, within=PROJECT / "state")
    cache = canonical(recorded.get("private_cache"), directory=True, within=PROJECT / "state")
    match = re.fullmatch(r"benchmark-data-([0-9]{1,24})", data.name)
    need(match is not None and data.parent == user.parent == cache.parent == PROJECT / "state",
         "direct owned private roots required")
    tag = match[1]
    need(user.name == "benchmark-user-" + tag and cache.name == "gui-gpu-local-aot-" + tag,
         "private root tags differ")
    canonical(data / "out", directory=True, within=data)
    need(not os.path.lexists(data / "out/frames.csv"), "previous frame log must already be archived")
    pad = data / "pad.txt"
    need(reader.read(pad, limit=1024) == b"", "existing private pad must be empty")
    need(not os.path.lexists(pad.with_name(pad.name + ".next")), "unfinished private pad update exists")
    for key in ("BB_ARM_SESSION_NONCE", "BB_ARM_RESTART_NONCE"):
        need(re.fullmatch(r"[0-9a-f]{32}", old_env.get(key, "")) is not None, "existing private nonce required")
    marker = ("ARMQA1\n" + str(data / "out/boot-linked.bin") + "\n" + str(source["game_root"]) +
              "\n" + str(data / "out/content.bin") + "\n" + old_env["BB_ARM_SESSION_NONCE"] + "\n").encode()
    need(reader.read(user / ".arm-runtime-session", limit=4096) == marker, "existing session marker mismatch")
    expected_private = dict(BB_DATA_DIR=str(data), BB_CONFIG=str(data / "bbport.ini"), BB_USER_DIR=str(user),
        BB_MODS_DIR=str(data / "mods"), BB_MODS_CONFIG=str(data / "mods.json"),
        BB_PATCHES_DIR=str(data / "patches"), BB_PATCHES_CONFIG=str(data / "patches.json"), BB_GPU_USER_DIR=str(cache),
        BB_ARM_SESSION_NONCE=old_env["BB_ARM_SESSION_NONCE"], BB_ARM_RESTART_NONCE=old_env["BB_ARM_RESTART_NONCE"],
        BB_PAD_FILE=str(pad), BB_ARM_QA_PAD_FILE=str(pad), BB_PAD_FILE_ONLY="1",
        BB_FRAME_TIMING="1", BB_FRAME_LOG_ONLY="1", BB_BENCHMARK_MUTE="1")
    need(set(expected_private) == ALLOWED_CHANGES and
         all(old_env.get(k) == value for k, value in expected_private.items()), "private clone fields changed")
    config_sha = {key: source["configs"][key].sha256 for key in CONFIG_NAMES}
    need(prior.get("settings_sha256") == config_sha, "source configuration changed")
    for key, name in CONFIG_NAMES.items():
        reader.read(source["configs"][key].path, limit=64 * 1024 * 1024, expected=config_sha[key], retain=False)
        reader.read(data / name, limit=64 * 1024 * 1024, expected=config_sha[key], retain=False)
    for name in ("mods", "patches"):
        original, private = source["data_root"] / name, data / name
        before = _tree_inventory(original) if os.path.lexists(original) else None
        private_nodes, private_files = _tree_inventory(private)
        private_kinds = {str(path.relative_to(private)): stat.S_IFMT(identity[2])
                         for path, identity in private_nodes.items()}
        if before is None:
            need(private_kinds == {".": stat.S_IFDIR} and not private_files,
                 "private resources differ from absent source " + name)
            need(not os.path.lexists(original), "source resources appeared: " + name)
        else:
            source_nodes, source_files = before
            source_kinds = {str(path.relative_to(original)): stat.S_IFMT(identity[2])
                            for path, identity in source_nodes.items()}
            need(private_kinds == source_kinds and private_files == source_files,
                 "private/source resource structure or bytes differ: " + name)
            need(_tree_inventory(original) == before, "source resources changed during resume: " + name)
    # No historical save digest was recorded. Accept only byte-identical current
    # private/source QA saves, rather than inventing a retrospective proof.
    source_saves = inventory_savedata(source["user_root"] / "savedata")
    need(inventory_savedata(user / "savedata") == source_saves, "private/source savedata differs")
    clone_env = dict(source["environment"])
    clone_env.update(expected_private)
    case = dict(environment=clone_env, data_root=data, user_root=user, cache_root=cache, pad=pad,
                source_save_manifest=source_saves, config_sha256=config_sha,
                allowed_changes=sorted(ALLOWED_CHANGES), source_unchanged=True)
    env, changes, allowed, experiment = controlled_environment(driver, source, case, formal, "A", vertex_ab=True)
    need(env == old_env and changes == recorded.get("actual_changes") and allowed == recorded.get("allowed_changes") and
         experiment == recorded.get("experiment") == prior.get("experiment"), "ordinary execution environment no longer matches")
    execution = driver.execution_conditions(source, formal)
    command = [source["executable"], str(execution["stage"] / source["arguments"][0]), "--benchmark"]
    need(recorded.get("command") == command and recorded.get("cwd") == str(execution["stage"]), "execution command changed")
    reader.unchanged()
    return case, dict(root=str(root), prior_status="incomplete", prior_stop=prior["stop"],
                      partial_preparation_reused=True, prior_run_counted_as_sample=False,
                      source_receipt_sha256=receipt_sha, private_tag=tag)


def execute(args):
    if not args.reviewed_code:
        raise ValueError("independent code review is required before --execute --reviewed-code")
    import signal
    import subprocess
    import time
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "tools"))
    import run_qa_launch_benchmark as driver
    from qa_launch_conditions import load
    from qa_launch_clone import create
    from qa_build_guard import require_no_games
    from native_observer import NativeObserver
    from qa_memory_policy import MemoryPolicy

    if not 60 <= args.startup_seconds <= 1800 or not 45 <= args.scene_seconds <= 180:
        raise ValueError("existing startup/scene budget bounds required")
    candidate, baseline = selected_applications(args)
    vertex_on_f3 = vertex_on_f3_request(args)
    if not args.vertex_ab and args.phase != "f3" and candidate.resolve() == baseline.resolve():
        raise ValueError("baseline and candidate must be distinct applications")
    receipt = within(args.receipt, PROJECT / "state")
    stage = within(args.source_stage, PROJECT / "apps")
    source = load(receipt, stage=stage, project=PROJECT)
    if args.vertex_ab or vertex_on_f3:
        vertex_source(source["environment"])
    require_no_games()
    status = subprocess.run(["/Users/k.d./.local/bin/cua-driver", "status"],
                            capture_output=True, text=True, timeout=10)
    if "daemon is not running" not in (status.stdout + status.stderr).lower():
        raise ValueError("Cua must be stopped before performance/game regression")
    native, cache_admission = driver.admit(source)
    candidate_admission = driver.admit_formal(candidate, source)
    if args.vertex_ab:
        variants = {"A": candidate_admission}
        if args.phase != "f3":
            variants["B"] = candidate_admission
    else:
        variants = {"B": candidate_admission}
        if args.phase != "f3":
            variants["A"] = driver.admit_formal(baseline, source)
    resumed = None
    resume_record = None
    if getattr(args, "resume_from", None) is not None:
        resumed, resume_record = resume_prepare_a(args.resume_from, source, variants["A"], native, driver,
                                                 vertex_ab=args.vertex_ab, phase=args.phase)
    tag = str(time.time_ns())
    root = PROJECT / "evidence" / ("upstream-refresh-game-" + tag)
    root.mkdir(mode=0o700)
    cases = {key: (resumed if key == "A" and resumed is not None else
                  create(source, cache_admission, project=PROJECT,
                         tag=str(time.time_ns()), cache_mode="empty")) for key in variants}
    observer = NativeObserver()
    protection_start = time.monotonic()
    before = observer.system()
    policy = MemoryPolicy(before)
    results = []
    old_handlers = {sig: signal.signal(sig, driver.stop_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    summary = dict(schema=1, status="running", source_receipt_sha256=driver.sha(receipt),
                   formal_data_modified=False, silent=True, Cua=False, screen_capture=False,
                   comparison_mode="vertex-on-functional-f3" if vertex_on_f3 else "same-binary-vertex" if args.vertex_ab else "old-new-app",
                   policy=MemoryPolicy.MANIFEST, runs=results)
    if resume_record is not None:
        summary["resume"] = resume_record
    try:
        if before["locked"] or before["pressure"] == 4:
            raise ValueError("locked or severe pressure before regression")
        for label, variant, functional_f3 in schedule(args.phase, vertex_ab=args.vertex_ab):
            require_no_games()
            formal, case = variants[variant], cases[variant]
            execution = driver.execution_conditions(source, formal)
            env, changes, allowed, experiment = controlled_environment(driver, source, case, formal,
                variant, vertex_ab=args.vertex_ab, functional_f3=functional_f3, vertex_on_f3=vertex_on_f3)
            command = [source["executable"], str(execution["stage"] / source["arguments"][0])]
            if not functional_f3:
                command += ["--benchmark"]
            run = root / label
            run.mkdir(mode=0o700)
            driver.write(run / "conditions.json", dict(command=command, cwd=str(execution["stage"]),
                environment=env, source_receipt=str(receipt), private_data=str(case["data_root"]),
                private_user=str(case["user_root"]), private_cache=str(case["cache_root"]),
                actual_changes=changes, allowed_changes=allowed, experiment=experiment))
            report = dict(schema=1, status="running", phase=label, variant=variant, silent=True,
                Cua=False, screen_capture=False, profiler=False, performance_sample=not functional_f3,
                formal_data_modified=False, quality_or_effects_changed=False,
                settings_sha256=case["config_sha256"], source_receipt_sha256=driver.sha(receipt),
                formal_native_identity=formal["identity"], native_identity=native,
                original_shell_entry_preserved=True, functional_f3_requested=functional_f3,
                experiment=experiment)
            row = driver.sample(execution, case, env, command, report, run,
                startup_seconds=args.startup_seconds,
                scene_seconds=45 if label.startswith("prepare-") else args.scene_seconds,
                observer=observer, policy=policy, protection_start=protection_start,
                functional_f3=functional_f3, track_tool_memory=True)
            results.append(row)
            if row["status"] not in ("sampled", "functional-checked"):
                raise ValueError("incomplete/protection-stopped run; inspect " + str(run))
            driver.write(root / "summary.json", summary)
        if args.phase != "f3":
            summary["matched_abba"] = matched_summary(results)
        summary["status"] = "completed"
    except BaseException as error:
        summary.update(status="incomplete", error=str(error)[:1024])
        raise
    finally:
        summary["after"] = observer.system()
        observer.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        driver.write(root / "summary.json", summary)
        print("Game regression evidence:", root)


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="print a read-only plan; no admission or game")
    mode.add_argument("--execute", action="store_true", help="run only after independent review and explicit release")
    parser.add_argument("--reviewed-code", action="store_true", help="explicit independent-review gate for execution")
    parser.add_argument("--app", type=Path, required=True, help="new normal-layout app under Shared evidence")
    parser.add_argument("--baseline-app", type=Path,
                        help="old normal-layout app; in --vertex-ab only the same candidate is permitted")
    parser.add_argument("--vertex-ab", action="store_true",
                        help="same candidate binary, A source default off, B only BB_MTL_RELAX_VERTEX=1")
    parser.add_argument("--vertex-on-f3", action="store_true",
                        help="single functional --phase f3 case, explicitly enable only BB_MTL_RELAX_VERTEX=1")
    parser.add_argument("--resume-from", type=Path,
                        help="strictly reuse this same-source/app protection-stopped prepare-A private case")
    parser.add_argument("--receipt", type=Path, default=RECEIPT)
    parser.add_argument("--source-stage", type=Path, default=SOURCE_STAGE)
    parser.add_argument("--phase", choices=("performance", "f3", "all"), default="all")
    parser.add_argument("--startup-seconds", type=int, default=1800)
    parser.add_argument("--scene-seconds", type=int, default=60)
    return parser


def main():
    args = argument_parser().parse_args()
    if args.plan:
        print(json.dumps(plan(args), indent=2))
    else:
        execute(args)


if __name__ == "__main__":
    main()
