"""Bounded source-closure and actual setup-preflight controls; never builds deps."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

D = Path(__file__).resolve().parents[1]
REPO = D.parents[1]
VENDOR = D / "vendor"


def validate_inventory(root, manifest):
    allowed = {".c", ".cpp", ".mm", ".h", ".S", ".comp", ".py"}
    for relative, entry in manifest["files"].items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular source file: " + relative)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("source escaped inventory root")
        data = path.read_bytes()
        data.decode("utf-8")
        if b"\0" in data or len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("source inventory mismatch: " + relative)
        if relative.startswith("vendor/tests/") and path.suffix not in allowed:
            raise ValueError("non-source test payload")


class MetalSourceClosure(unittest.TestCase):
    def test_inventory(self):
        manifest = json.loads((VENDOR / "metal-build-source-closure.json").read_text())
        validate_inventory(D, manifest)
        actual = {str(p.relative_to(D)) for p in (VENDOR / "tests").rglob("*") if p.is_file()}
        listed = {p for p in manifest["files"] if p.startswith("vendor/tests/")}
        self.assertEqual(actual, listed)
        self.assertEqual(len(actual), 65)
        self.assertEqual(sum(r["includes"] for r in manifest["relocations"]), 7)

    def test_every_cmake_source_exists(self):
        cmake = (VENDOR / "metal_api/CMakeLists.txt").read_text()
        refs = set(re.findall(r"\.\./tests/([^\s)\";]+)", cmake))
        self.assertEqual(len(refs), 61)
        for relative in refs | {"tiling_reference.comp"}:
            self.assertTrue((VENDOR / "tests" / relative).is_file(), relative)
        self.assertRegex((VENDOR / "macos/build-deps.sh").read_text(), r"(?m)^fetch spirv-cross ")

    def test_local_includes_and_relocations(self):
        tests = VENDOR / "tests"
        fixture = tests / "fixtures/tess_patch_layout/shaders.h"
        self.assertIn("synthetic GLSL, no game data", fixture.read_text())
        self.assertTrue((fixture.parent / "generate.py").is_file())
        for path in tests.rglob("*"):
            if not path.is_file():
                continue
            for include in re.findall(r'^\s*#\s*include\s*"([^"]+)"', path.read_text(), re.M):
                self.assertFalse(include.startswith(("gpu/", "../gpu/")), (path.name, include))
                local = path.parent / include
                alias = VENDOR / include
                if include.startswith(("metal_api/", "../metal_api/", "fixtures/")) or include in ("test_mapping_rasterizer.h", "tiling_cpu_reference.h"):
                    self.assertTrue(local.is_file() or alias.is_file(), (path.name, include))

    def test_inventory_rejects_mutation_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "vendor/tests/control.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("// synthetic control\n")
            data = source.read_bytes()
            manifest = {"files": {str(source.relative_to(root)): {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}
            validate_inventory(root, manifest)
            source.write_text("// mutated\n")
            with self.assertRaises(ValueError):
                validate_inventory(root, manifest)
            source.unlink()
            source.symlink_to(Path(__file__))
            with self.assertRaises(ValueError):
                validate_inventory(root, manifest)


class ArchitecturePreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = REPO / "tools/macos/setup_deps.sh"
        text = cls.script.read_text()
        # Execute the real validation and Rosetta block, without downloads/builds.
        cls.select = text[text.index('ARCH=${BB_ARCH:'):text.index('PREFIX=${1:')]
        start = text.index('if [[ $ARCH == x86_64 ]]; then')
        cls.rosetta = text[start:text.index('\nfi', start) + len('\nfi')]

    def run_preflight(self, architecture, arch_status):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = root / "calls"
            arch = root / "arch"
            arch.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CONTROL_CALLS"\nexit "$CONTROL_ARCH_STATUS"\n')
            arch.chmod(0o755)
            env = dict(os.environ, BB_ARCH=architecture, CONTROL_ARCH_STATUS=str(arch_status), CONTROL_CALLS=str(calls), PATH=str(root) + os.pathsep + os.environ["PATH"])
            code = 'set -euo pipefail\ndie() { printf "%s\\n" "$*" >&2; exit 1; }\n' + self.select + self.rosetta
            result = subprocess.run(["/bin/bash", "-c", code], env=env, text=True, capture_output=True, timeout=5)
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_arm_does_not_require_rosetta(self):
        result, calls = self.run_preflight("arm64", 1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_x86_missing_rosetta_rejected(self):
        result, calls = self.run_preflight("x86_64", 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rosetta 2 missing", result.stderr)
        self.assertEqual(calls, ["-x86_64 /usr/bin/true"])

    def test_x86_with_rosetta_accepted(self):
        result, calls = self.run_preflight("x86_64", 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["-x86_64 /usr/bin/true"])

    def test_invalid_architecture_rejected(self):
        result, calls = self.run_preflight("not-an-architecture", 0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BB_ARCH must be", result.stderr)
        self.assertEqual(calls, [])

    def test_shell_syntax(self):
        subprocess.run(["/bin/bash", "-n", str(self.script)], check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
