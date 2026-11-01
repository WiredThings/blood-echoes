"""Every emitted internal phase must be accepted and visible to the launcher."""
import ast
import contextlib
import io
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from launcher_local_aot import PREPARATION_PHASES, preparation_phase


class PhaseContract(unittest.TestCase):
    def test_emitted_names_have_runtime_and_frontend_contracts(self):
        emitted = set()
        for name in ('launcher_local_aot.py', 'launcher_game_pso.py'):
            for node in ast.walk(ast.parse((TOOLS / name).read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'preparation_phase' and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    emitted.add(node.args[0].value)
        self.assertIn('static-variant-inputs', emitted)
        self.assertLessEqual(emitted, PREPARATION_PHASES)
        ui = (ROOT / 'launcher-macos/Sources/BBLauncher/LaunchPreparationProgress.swift').read_text()
        self.assertLessEqual(emitted, set(re.findall(r'"([a-z-]+)":\s*\(', ui)))
        with contextlib.redirect_stdout(io.StringIO()) as log:
            with preparation_phase('static-variant-inputs'):
                pass
        self.assertIn('"outcome": "returned"', log.getvalue())

    def test_unknown_phase_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown internal'):
            with preparation_phase('unregistered-source-phase'):
                self.fail('must not execute unknown phase')


if __name__ == '__main__':
    unittest.main()
