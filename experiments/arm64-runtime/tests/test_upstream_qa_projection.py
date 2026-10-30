"""Exact optional-UI source transition; authored bundles, no native/game execution."""

from pathlib import Path
import hashlib
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import test_qa_benchmark_formal_stage as fixtures
import run_qa_launch_benchmark as driver

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]


class UpstreamSourceProjection(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.FormalStage("test_real_fullclosure_and_both_exact_cpu_roles")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.name = driver.REVIEWED_OPTIONAL_UI_SCRIPT
        self.old = (ROOT / "tests/fixtures/upstream_ultrawide_ui_00385145.py.txt").read_bytes()
        self.new = (REPOSITORY / self.name).read_bytes()
        self.assertEqual(hashlib.sha256(self.old).hexdigest(), driver.REVIEWED_OPTIONAL_UI_SOURCE)
        self.assertEqual(hashlib.sha256(self.new).hexdigest(), driver.REVIEWED_OPTIONAL_UI_TARGET)
        self.f.source["effective_scripts"] = dict(self.f.source["effective_scripts"],
            **{self.name: driver.REVIEWED_OPTIONAL_UI_SOURCE})
        self.f.put(self.f.source["stage"] / self.name, self.old)
        self.f.put(self.f.stage / self.name, self.new)
        self.f.seal()
        self.target_parity(**{self.name: driver.REVIEWED_OPTIONAL_UI_TARGET})

    def target_parity(self, **updates):
        path = self.f.stage / "SOURCE-PARITY.json"
        record = json.loads(path.read_text())
        record["effective_sources"].update(updates)
        self.f.put(path, json.dumps(record).encode())

    def test_exact_whole_files_admitted_without_mutating_source(self):
        before = dict(self.f.source["effective_scripts"])
        result = self.f.admit()
        projection = result["identity"]["reviewed_script_projections"]
        self.assertEqual(projection, [{
            "script": self.name, "source_revision": "00385145",
            "review": "optional-ui-validation-20261030",
            "source_sha256": driver.REVIEWED_OPTIONAL_UI_SOURCE,
            "target_sha256": driver.REVIEWED_OPTIONAL_UI_TARGET,
            "complete_file_bytes_checked": True,
        }])
        self.assertEqual(self.f.source["effective_scripts"], before)
        self.assertEqual((self.f.source["stage"] / self.name).read_bytes(), self.old)
        self.assertEqual((self.f.stage / self.name).read_bytes(), self.new)
        self.assertEqual(len(self.f.calls), 3)

    def test_wrong_declared_old_sha_is_not_an_allowed_projection(self):
        self.f.source["effective_scripts"][self.name] = "a" * 64
        with self.assertRaises(ValueError):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_changed_old_byte_is_rejected_even_with_reviewed_manifest(self):
        self.f.put(self.f.source["stage"] / self.name, self.old + b"\n")
        with self.assertRaises(ValueError):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_wrong_declared_new_sha_is_not_an_allowed_projection(self):
        self.target_parity(**{self.name: "b" * 64})
        with self.assertRaises(ValueError):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_changed_new_byte_is_rejected_even_with_reviewed_manifest(self):
        self.f.put(self.f.stage / self.name, self.new + b"\n")
        with self.assertRaises(ValueError):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_reviewed_ui_change_does_not_authorize_another_script(self):
        path = self.f.stage / "scripts/patches.py"
        self.f.put(path, b"# unreviewed patch policy\n")
        self.target_parity(**{"scripts/patches.py": hashlib.sha256(path.read_bytes()).hexdigest()})
        with self.assertRaisesRegex(ValueError, "script"):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_exact_ui_and_existing_exact_run_projection_compose(self):
        self.f.image_projection()
        self.target_parity(**{self.name: driver.REVIEWED_OPTIONAL_UI_TARGET})
        result = self.f.admit()
        self.assertTrue(result["identity"]["image_preparation_exact_projection"])
        self.assertEqual(len(result["identity"]["reviewed_script_projections"]), 1)

    def test_reviewed_ui_does_not_hide_extra_run_sh_bytes(self):
        self.f.image_projection(extra="# unreviewed bytes\n")
        self.target_parity(**{self.name: driver.REVIEWED_OPTIONAL_UI_TARGET})
        with self.assertRaisesRegex(ValueError, "sixteen scripts"):
            self.f.admit()
        self.assertFalse(self.f.calls)

    def test_different_target_script_collection_is_rejected(self):
        path = self.f.stage / "SOURCE-PARITY.json"
        record = json.loads(path.read_text())
        record["effective_sources"]["scripts/unreviewed.py"] = "d" * 64
        self.f.put(path, json.dumps(record).encode())
        with self.assertRaisesRegex(ValueError, "script"):
            self.f.admit()
        self.assertFalse(self.f.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
