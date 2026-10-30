"""Controller controls only: no native admission, data cloning, or game processes."""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_upstream_refresh_game as harness


def row(label, variant, *, fps=50, camera=1.0, draws=1000, status="sampled"):
    return dict(phase=label, variant=variant, status=status, scene_sample=dict(
        fps=fps, p95_ms=24, p99_ms=30, gpu_ms=18, combined_footprint_mib=6000,
        camera={key: camera for key in ("cam_x", "cam_y", "cam_z")}, draws_median=draws))


def abba():
    return [row("warm-A1", "A", fps=49), row("warm-B1", "B", fps=53),
            row("warm-B2", "B", fps=54), row("warm-A2", "A", fps=50)]


class UpstreamGameHarness(unittest.TestCase):
    def setUp(self):
        self.process_guard = patch("subprocess.Popen", side_effect=AssertionError("controller test must not start processes"))
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)

    def test_performance_schedule_prepares_then_runs_abba(self):
        schedule = harness.schedule("performance")
        self.assertEqual([r[0] for r in schedule[:2]], ["prepare-A", "prepare-B"])
        self.assertEqual([r[1] for r in schedule[2:]], ["A", "B", "B", "A"])
        self.assertTrue(all(not r[2] for r in schedule))

    def test_f3_is_separate_and_has_no_death_input(self):
        self.assertEqual(harness.schedule("f3"), [("functional-F2-F3", "B", True)])
        self.assertEqual(harness.schedule("all")[-1], harness.schedule("f3")[0])
        with self.assertRaises(ValueError):
            harness.schedule("death")

    def test_execute_without_review_is_rejected_before_native_import_or_process(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no process permitted")):
            with self.assertRaisesRegex(ValueError, "independent code review"):
                harness.execute(argparse.Namespace(reviewed_code=False))

    def test_complete_matched_abba_reports_observed_difference(self):
        report = harness.matched_summary(abba())
        self.assertEqual(report["A"]["fps"], 49.5)
        self.assertEqual(report["B"]["fps"], 53.5)
        self.assertAlmostEqual(report["fps_change_percent"], (53.5 / 49.5 - 1) * 100)
        self.assertEqual(report["scene_footprint_change_mib"], 0)

    def test_incomplete_or_protection_stopped_run_is_not_acceptance(self):
        rows = abba()
        rows[1]["status"] = "incomplete"
        with self.assertRaisesRegex(ValueError, "protection-stopped"):
            harness.matched_summary(rows)

    def test_camera_mismatch_is_rejected(self):
        rows = abba()
        rows[2]["scene_sample"]["camera"]["cam_x"] = 5.0
        with self.assertRaisesRegex(ValueError, "camera"):
            harness.matched_summary(rows)

    def test_different_draw_workload_is_rejected(self):
        rows = abba()
        rows[2]["scene_sample"]["draws_median"] = 1200
        with self.assertRaisesRegex(ValueError, "draw workload"):
            harness.matched_summary(rows)

    def test_missing_or_wrong_sequence_is_rejected(self):
        for rows in (abba()[:-1], list(reversed(abba()))):
            # ABBA is palindromic; reverse labels as well to make this genuinely wrong.
            if len(rows) == 4:
                rows[1]["variant"] = "A"
            with self.assertRaisesRegex(ValueError, "ABBA"):
                harness.matched_summary(rows)

    def test_plan_and_execute_cannot_be_selected_together(self):
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                harness.argument_parser().parse_args(["--plan", "--execute", "--app", "/tmp/a.app"])

    def test_formal_application_is_outside_qa_execution_scope(self):
        with self.assertRaisesRegex(ValueError, "plan input"):
            harness.within(Path("/Applications/Blood Echoes.app"), harness.PROJECT / "evidence")

    def environment_fixture(self, source_flag="absent", *, extra_base=None):
        sys.path.insert(0, str(harness.ROOT / "tools"))
        import run_qa_launch_benchmark as original
        environment = dict(BB_AUDIO="none", BB_QUICKSAVE="1", BB_FRAME_STATS="0",
                           BB_PAD_FILE_ONLY="0", BB_GPU_PROCESS="/source/gpu")
        if source_flag != "absent":
            environment[harness.VERTEX_FLAG] = source_flag
        source = dict(environment=environment)
        case = dict(allowed_changes=[])
        calls = []

        def build(source, case, formal, *, functional_f3=False):
            base = dict(source["environment"], BB_PAD_FILE_ONLY="1", BB_GPU_PROCESS="/candidate/gpu",
                        BB_QUICKSAVE="1" if functional_f3 else "0")
            if functional_f3:
                base["BB_FRAME_STATS"] = "1"
            base.update(extra_base or {})
            return base

        def checked(source, case, base, **kwargs):
            calls.append(dict(base))
            return original.benchmark_changes(source, case, base, **kwargs)

        driver = SimpleNamespace(execution_environment=build, benchmark_changes=checked)
        return driver, source, case, calls

    def test_vertex_mode_selects_same_app_and_rejects_other_baseline(self):
        app = harness.PROJECT / "evidence/candidate.app"
        args = harness.argument_parser().parse_args(["--plan", "--app", str(app), "--vertex-ab"])
        self.assertEqual(harness.selected_applications(args), (app, app))
        args.baseline_app = harness.PROJECT / "evidence/other.app"
        with self.assertRaisesRegex(ValueError, "same candidate"):
            harness.selected_applications(args)

    def test_ordinary_mode_keeps_old_new_app_selection(self):
        app = harness.PROJECT / "evidence/candidate.app"
        args = harness.argument_parser().parse_args(["--plan", "--app", str(app)])
        self.assertEqual(harness.selected_applications(args), (app, harness.POLICY_APP))

    def test_vertex_a_keeps_source_default_off_without_creating_flag(self):
        driver, source, case, calls = self.environment_fixture()
        before = dict(source["environment"])
        env, changes, allowed, experiment = harness.controlled_environment(
            driver, source, case, {}, "A", vertex_ab=True)
        self.assertNotIn(harness.VERTEX_FLAG, env)
        self.assertNotIn(harness.VERTEX_FLAG, changes)
        self.assertEqual(experiment["actual_experiment_changes"], {})
        self.assertEqual(experiment["allowed_experiment_variables"], [harness.VERTEX_FLAG])
        self.assertEqual(source["environment"], before)
        self.assertEqual(len(calls), 1)

    def test_vertex_b_changes_only_reviewed_flag_after_ordinary_check(self):
        for source_flag in ("absent", "0"):
            with self.subTest(source_flag=source_flag):
                driver, source, case, calls = self.environment_fixture(source_flag)
                before = dict(source["environment"])
                env, changes, allowed, experiment = harness.controlled_environment(
                    driver, source, case, {}, "B", vertex_ab=True)
                self.assertEqual(env[harness.VERTEX_FLAG], "1")
                self.assertEqual(calls[0].get(harness.VERTEX_FLAG), before.get(harness.VERTEX_FLAG))
                actual = {key for key in set(calls[0]) | set(env) if calls[0].get(key) != env.get(key)}
                self.assertEqual(actual, {harness.VERTEX_FLAG})
                self.assertEqual(changes[harness.VERTEX_FLAG], dict(before=before.get(harness.VERTEX_FLAG), after="1"))
                self.assertEqual(experiment["actual_experiment_changes"], {harness.VERTEX_FLAG: changes[harness.VERTEX_FLAG]})
                self.assertTrue(experiment["ordinary_environment_checked_first"])
                self.assertIn(harness.VERTEX_FLAG, allowed)
                self.assertEqual(source["environment"], before)

    def test_vertex_f3_remains_default_off_and_preserves_existing_f3_options(self):
        for flag in ("absent", "0"):
            driver, source, case, calls = self.environment_fixture(flag)
            env, changes, allowed, experiment = harness.controlled_environment(
                driver, source, case, {}, "B", vertex_ab=True, functional_f3=True)
            self.assertEqual(env.get(harness.VERTEX_FLAG), source["environment"].get(harness.VERTEX_FLAG))
            self.assertEqual(env["BB_QUICKSAVE"], "1")
            self.assertEqual(env["BB_FRAME_STATS"], "1")
            self.assertEqual(experiment["actual_experiment_changes"], {})
            self.assertEqual(harness.schedule("all", vertex_ab=True)[-1], ("functional-F2-F3", "A", True))

    def test_vertex_source_flag_must_be_missing_or_exact_string_zero(self):
        for flag in ("1", "", "false", "00", None, True, 0):
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(ValueError, "source receipt"):
                    harness.vertex_source({harness.VERTEX_FLAG: flag})
        harness.vertex_source({})
        harness.vertex_source({harness.VERTEX_FLAG: "0"})

    def test_other_base_environment_change_is_rejected_before_experiment(self):
        driver, source, case, calls = self.environment_fixture(extra_base={"BB_UNREVIEWED_SETTING": "1"})
        with self.assertRaisesRegex(ValueError, "unapproved game setting"):
            harness.controlled_environment(driver, source, case, {}, "B", vertex_ab=True)
        self.assertEqual(len(calls), 1)

    def test_other_final_environment_change_is_not_an_experiment_exception(self):
        source = dict(BB_AUDIO="none")
        base = dict(source)
        final = dict(base, BB_MTL_RELAX_VERTEX="1", BB_AUDIO="on")
        with self.assertRaisesRegex(ValueError, "unreviewed environment mutation"):
            harness.vertex_delta(source, base, final, "B")

    def test_flag_cannot_already_be_changed_at_original_check_boundary(self):
        with self.assertRaisesRegex(ValueError, "before the ordinary"):
            harness.vertex_delta({}, {harness.VERTEX_FLAG: "0"}, {harness.VERTEX_FLAG: "1"}, "B")

    def test_vertex_mode_does_not_add_a_generic_environment_option(self):
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                harness.argument_parser().parse_args(["--plan", "--app", "/tmp/a.app", "--vertex-ab",
                                                     "--env", "BB_AUDIO=on"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
