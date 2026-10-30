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


def selected_applications(args):
    app = within(args.app, PROJECT / "evidence")
    vertex = bool(getattr(args, "vertex_ab", False))
    requested_baseline = args.baseline_app
    baseline = within(requested_baseline or (app if vertex else POLICY_APP), PROJECT / "evidence")
    if app.suffix != ".app" or baseline.suffix != ".app":
        raise ValueError("normal-layout staged applications required")
    if vertex and baseline.resolve() != app.resolve():
        raise ValueError("--vertex-ab requires the same candidate app, not a different baseline")
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


def controlled_environment(driver, source, case, formal, variant, *, vertex_ab=False, functional_f3=False):
    base = driver.execution_environment(source, case, formal, functional_f3=functional_f3)
    # Run the original full allowlist check BEFORE adding the single compiler control.
    changes, allowed = driver.benchmark_changes(source, case, base, formal=formal,
                                               functional_f3=functional_f3)
    if not vertex_ab:
        return base, changes, allowed, None
    vertex_source(source["environment"])
    final = dict(base)
    if variant == "B" and not functional_f3:
        final[VERTEX_FLAG] = "1"
    experiment = vertex_delta(source["environment"], base, final, variant, functional_f3=functional_f3)
    changes = dict(changes, **experiment["actual_experiment_changes"])
    return final, changes, sorted(set(allowed) | {VERTEX_FLAG}), experiment


def plan(args):
    app, baseline = selected_applications(args)
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
    if args.vertex_ab:
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
        "comparison_mode": "same-binary-vertex" if args.vertex_ab else "old-new-app",
        "vertex_control": (dict(variable=VERTEX_FLAG, source=env.get(VERTEX_FLAG),
                                 A=env.get(VERTEX_FLAG), B="1", F3=env.get(VERTEX_FLAG),
                                 independent_private_cache_roots=True,
                                 allowed_experiment_variables=[VERTEX_FLAG]) if args.vertex_ab else None),
        "source_receipt": str(receipt), "source_stage": str(stage),
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
            "--phase", args.phase] + (["--vertex-ab"] if args.vertex_ab else []),
        "warm_series": {
            "reuse": ["qa_launch_conditions.load", "run_qa_launch_benchmark.admit",
                      "run_qa_launch_benchmark.admit_formal", "qa_launch_clone.create",
                      "run_qa_launch_benchmark.execution_conditions",
                      "run_qa_launch_benchmark.execution_environment",
                      "run_qa_launch_benchmark.benchmark_changes",
                      "run_qa_launch_benchmark.sample"],
            "cache_policy": "Existing formal-stage CLI starts an empty private GPU case. A thin controller must reuse that exact admitted private case for subsequent warm launches; never seed formal caches or edit the receipt.",
            "sequence": ["baseline private preparation", "candidate private preparation",
                         "matched warmed A/B/B/A scene samples", "separate candidate F2/F3 session"],
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
    if not args.vertex_ab and args.phase != "f3" and candidate.resolve() == baseline.resolve():
        raise ValueError("baseline and candidate must be distinct applications")
    receipt = within(args.receipt, PROJECT / "state")
    stage = within(args.source_stage, PROJECT / "apps")
    source = load(receipt, stage=stage, project=PROJECT)
    if args.vertex_ab:
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
    tag = str(time.time_ns())
    root = PROJECT / "evidence" / ("upstream-refresh-game-" + tag)
    root.mkdir(mode=0o700)
    cases = {key: create(source, cache_admission, project=PROJECT,
                         tag=str(time.time_ns()), cache_mode="empty") for key in variants}
    observer = NativeObserver()
    protection_start = time.monotonic()
    before = observer.system()
    policy = MemoryPolicy(before)
    results = []
    old_handlers = {sig: signal.signal(sig, driver.stop_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    summary = dict(schema=1, status="running", source_receipt_sha256=driver.sha(receipt),
                   formal_data_modified=False, silent=True, Cua=False, screen_capture=False,
                   comparison_mode="same-binary-vertex" if args.vertex_ab else "old-new-app",
                   policy=MemoryPolicy.MANIFEST, runs=results)
    try:
        if before["locked"] or before["pressure"] == 4:
            raise ValueError("locked or severe pressure before regression")
        for label, variant, functional_f3 in schedule(args.phase, vertex_ab=args.vertex_ab):
            require_no_games()
            formal, case = variants[variant], cases[variant]
            execution = driver.execution_conditions(source, formal)
            env, changes, allowed, experiment = controlled_environment(driver, source, case, formal,
                variant, vertex_ab=args.vertex_ab, functional_f3=functional_f3)
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
