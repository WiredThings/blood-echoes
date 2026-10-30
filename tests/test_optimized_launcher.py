"""Exercise the app entry point without starting the game or touching live saves."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OptimizedLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bbport launcher ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / 'frontend'
        (self.repo / 'tools/macos').mkdir(parents=True)
        shutil.copy2(ROOT / 'tools/macos/run_optimized.sh', self.repo / 'tools/macos/run_optimized.sh')
        shutil.copy2(ROOT / 'tools/macos/supervise_restart.sh', self.repo / 'tools/macos/supervise_restart.sh')
        self.native = self.repo / 'out/native-05-checkout'
        for name in ['out/bb-probe', 'out/gpu/libbbgpu.dylib', 'out/gpu-arm64/bb-gpu',
                     'out/gpu-arm64/libbbgpu.dylib', 'deps-arm64/lib/libvulkan.1.dylib',
                     'deps-arm64/lib/kosmickrisp/kosmickrisp_mesa_icd.json']:
            path = self.native / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o755)
        self.capture = Path(self.temp.name) / 'capture.json'
        (self.repo / 'run.sh').write_text('exec "$PYTHON" -c \'import json,os; from pathlib import Path; '
                                        'Path(os.environ["CAPTURE"]).write_text(json.dumps(dict(os.environ)))\'\n')
        self.env = dict(os.environ, BB_RENDERER='vulkan', BB_GAME_DIR='/not-used', CAPTURE=str(self.capture), PYTHON=sys.executable,
                        BB_DATA_DIR=str(Path(self.temp.name) / 'profile'), BB_USER_DIR=str(Path(self.temp.name) / 'saves'))

    def run_launcher(self):
        return subprocess.run(['bash', str(self.repo / 'tools/macos/run_optimized.sh')],
                              env=self.env, text=True, capture_output=True, timeout=10)

    def test_vertex_choice_passes_through_without_a_shell_default_or_strict_override(self):
        for name in ['out/bb-probe-metal', 'out/gpu-metal/libbbgpu.dylib']:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o755)
        self.env.update(BB_RENDERER='metal', BB_MTL_RELAX_VERTEX='1', BB_MTL_STRICT_FLOAT='1')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = json.loads(self.capture.read_text())
        self.assertEqual(env['BB_MTL_RELAX_VERTEX'], '1')
        self.assertEqual(env['BB_MTL_STRICT_FLOAT'], '1')
        self.env.pop('BB_MTL_RELAX_VERTEX')  # The frontend clears this managed OFF key.
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('BB_MTL_RELAX_VERTEX', json.loads(self.capture.read_text()))

    def test_native_runtime_paths_and_quicksave_on_off(self):
        for on in ['1', '0']:
            with self.subTest(quicksave=on):
                self.env.update(BB_QUICKSAVE=on, BB_PATCHES='Resolution Patch 3840x1080 (32:9)',
                                BB_LANGUAGE='11',
                                BB_DMEM_MB='9152', BB_PAD_FILE='/bad-input', BB_QUICKLOAD_PENDING='1',
                                BB_MENU_SCANCODE='61', BB_QUICKSAVE_SCANCODE='58', BB_QUICKLOAD_SCANCODE='60')
                result = self.run_launcher()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                env = json.loads(self.capture.read_text())
                self.assertEqual(env['BB_NATIVE_GPU'], '1')
                self.assertEqual(env['BB_PROBE'], str(self.native / 'out/bb-probe'))
                self.assertEqual(env['BB_GPU_PROCESS'], str(self.native / 'out/gpu-arm64/bb-gpu'))
                self.assertEqual(env['BB_GPU_DEPS'], str(self.native / 'deps-arm64'))
                self.assertEqual(env['BB_QUICKSAVE'], on)
                self.assertEqual(env['BB_LANGUAGE'], '11')
                self.assertEqual(env['BB_GPU_USER_DIR'], str(Path(self.env['BB_DATA_DIR']) / 'gpu-cache/perf1-stages'))
                self.assertEqual(env['MESA_SHADER_CACHE_DIR'], str(Path(self.env['BB_DATA_DIR']) / 'mesa-cache-perf1'))
                for name in ['BB_MENU_SCANCODE', 'BB_QUICKSAVE_SCANCODE', 'BB_QUICKLOAD_SCANCODE']:
                    self.assertEqual(env[name], self.env[name])
                self.assertEqual(env['BB_QUICKSAVE_PYTHON'], sys.executable)
                self.assertEqual(env['BB_QUICKSAVE_HELPER'], str(self.repo / 'tools/macos/quicksave.py'))
                self.assertEqual(env['BB_USER_DIR'], self.env['BB_USER_DIR'])
                self.assertEqual(env['BB_DMEM_MB'], '9152')
                self.assertEqual(env['BB_PATCHES'], self.env['BB_PATCHES'])
                self.assertNotIn('BB_PAD_FILE', env)
                self.assertNotIn('BB_QUICKLOAD_PENDING', env)
                self.assertEqual(env['BB_MAC_UNIFIED_MEMORY'], '0')
                self.assertEqual(env['BB_FRAMES_AHEAD'], '2')
                self.assertEqual(env['BB_CMDBUF_TRIM'], '1')
                self.assertEqual(env['BB_KK_POOL_FIX'], '1')

    def test_strict_precision_uses_its_own_cache_and_preserves_overrides(self):
        self.env['BB_SPIRV_PRECISE'] = 'all'
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = json.loads(self.capture.read_text())
        self.assertTrue(env['BB_GPU_USER_DIR'].endswith('/perf1-all'))
        self.env['BB_GPU_USER_DIR'] = str(Path(self.temp.name) / 'custom-cache')
        self.assertEqual(self.run_launcher().returncode, 0)
        self.assertEqual(json.loads(self.capture.read_text())['BB_GPU_USER_DIR'], self.env['BB_GPU_USER_DIR'])

    def test_missing_native_build_never_falls_back_to_release(self):
        (self.native / 'out/gpu-arm64/bb-gpu').unlink()
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('optimized runtime or native renderer is missing', result.stderr)
        self.assertFalse(self.capture.exists())

    def test_metal_default_keeps_runtime_features_and_isolates_driver_and_cache(self):
        for name in ['out/bb-probe-metal', 'out/gpu-metal/libbbgpu.dylib']:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o755)
        self.env.pop('BB_RENDERER')
        self.env.update(BB_GPU_PROCESS='/old-gpu', VK_DRIVER_FILES='/old-icd',
                        BB_MAC_UNIFIED_MEMORY='0', BB_QUICKSAVE='1', BB_LANGUAGE='11',
                        BB_MENU_SCANCODE='58', BB_DMEM_MB='9152', BB_HDR='1')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = json.loads(self.capture.read_text())
        for key, value in dict(BB_RENDERER='metal', BB_NATIVE_GPU='0', BB_PC_MODEL='1',
                              BB_GUEST_IN_PLACE='1', BB_LAYER_MEMORY='1', BB_GUEST_MEMORY='host',
                              BB_QUICKSAVE='1', BB_LANGUAGE='11', BB_MENU_SCANCODE='58',
                              BB_DMEM_MB='9152', BB_HDR='0', BB_AUTO_NOHIZ='0').items():
            self.assertEqual(env[key], value, key)
        self.assertEqual(env['BB_PROBE'], str(self.repo / 'out/bb-probe-metal'))
        self.assertTrue(env['BB_GPU_USER_DIR'].endswith('/gpu-cache/metal1'))
        for key in ['BB_GPU_PROCESS', 'VK_DRIVER_FILES', 'BB_MAC_UNIFIED_MEMORY']:
            self.assertNotIn(key, env)

    def test_missing_metal_stops_instead_of_silently_using_vulkan(self):
        self.env['BB_RENDERER'] = 'metal'
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Metal renderer build is missing', result.stderr)
        self.assertFalse(self.capture.exists())


if __name__ == '__main__':
    unittest.main()
