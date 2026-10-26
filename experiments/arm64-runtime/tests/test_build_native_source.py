"""Author-only controls; never invoke compilers, native helpers or games."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_native_source as build
from qa_memory_policy import MemoryPolicy


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'
        self.source = self.repo / 'experiments/arm64-runtime'
        self.prefix = self.base / 'prefix'
        self.provider = self.base / 'python'
        for name in ('tools/configure_native_build.py', 'tools/build_metadata_layout.py',
                     'vendor/metal_api/CMakeLists.txt', 'vendor/bbnet/CMakeLists.txt', 'targets.cmake'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# author source\n')
        script = self.repo / 'tools/macos/build_launcher.sh'
        script.parent.mkdir(parents=True)
        script.write_text('work=${BB_LAUNCHER_WORK:-out/launcher-build}\n')
        for root, names in ((self.prefix, ('cmake', 'ninja', 'pkg-config', 'protoc')),
                            (self.provider, ('python3.11',))):
            (root / 'bin').mkdir(parents=True)
            for name in names:
                path = root / 'bin' / name
                path.write_text('author tool; must not execute\n')
                path.chmod(0o700)
        self.args = dict(repository=self.repo, build_root=self.base / 'fresh',
                         dependency_prefixes=[self.prefix], protoc=self.prefix / 'bin/protoc',
                         python_provider=self.provider, version='0.2.52', revision='a' * 40)

    def plan(self, **kw):
        return build.plan(**(self.args | kw))

    def test_default_plan_has_no_side_effects_or_native_queries(self):
        before = sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))
        with patch('subprocess.Popen', side_effect=AssertionError('native call')):
            value = self.plan()
        self.assertEqual(before, sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*')))
        self.assertFalse(Path(value['build_root']).exists())
        self.assertEqual(value['status'], 'plan-only')
        self.assertFalse(value['targets_built'])
        self.assertFalse(value['app_assembled'])
        self.assertEqual(value['commands'][3]['argv'], None)
        self.assertEqual(value['sdk_query'], ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'])

    def test_plan_reuses_normal_targets_and_original_gui(self):
        value = self.plan()
        commands = {c['name']: c for c in value['commands']}
        self.assertEqual(commands['renderer-build']['argv'][-5:],
                         ['bbgpu', 'game-pso-preparer', 'game-module-preparer', 'bb-gpu', 'metadata-layout'])
        self.assertIn('--prepare-only', commands['cpu-generate']['argv'])
        self.assertTrue(commands['launcher-build']['argv'][1].endswith('tools/macos/build_launcher.sh'))
        self.assertIn('-DBBNET_PROTOC_EXECUTABLE=' + str(self.prefix / 'bin/protoc'), commands['net-configure']['argv'])
        for command in commands.values():
            if command['name'].endswith('-build') and command['name'] != 'launcher-build':
                self.assertEqual(command['argv'][command['argv'].index('--parallel') + 1], '1')
        for key in ('BB_APP', 'BB_LAUNCHER_WORK', 'BB_SWIFT_SCRATCH_PATH'):
            self.assertTrue(Path(value['environment'][key]).is_relative_to(self.args['build_root']))
        self.assertNotIn('launcher-component-stage', json.dumps(value))
        self.assertNotIn('/Users/Shared', json.dumps(value))

    def test_explicit_sdk_and_openssl(self):
        sdk = self.base / 'SDK'
        sdk.mkdir()
        value = self.plan(sdk_root=sdk, openssl_root=self.prefix)
        self.assertIsNone(value['sdk_query'])
        self.assertNotIn('<SDKROOT>', json.dumps(value))
        self.assertIn('-DOPENSSL_ROOT_DIR=' + str(self.prefix), value['commands'][5]['argv'])

    def test_existing_output_rejected_without_overwrite(self):
        self.args['build_root'].mkdir()
        sentinel = self.args['build_root'] / 'old'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'fresh canonical'):
            self.plan()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_source_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.plan(build_root=self.repo / 'new')

    def test_symlink_parent_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            self.plan(build_root=alias / 'fresh')

    def test_dangling_output_rejected(self):
        self.args['build_root'].symlink_to(self.base / 'missing')
        with self.assertRaisesRegex(ValueError, 'fresh canonical'):
            self.plan()

    def test_app_output_rejected(self):
        with self.assertRaisesRegex(ValueError, 'application'):
            self.plan(build_root=self.base / 'Bad.app')

    def test_nonexplicit_missing_tool_rejected(self):
        (self.prefix / 'bin/cmake').unlink()
        with self.assertRaisesRegex(ValueError, 'lack tool: cmake'):
            self.plan()

    def test_protoc_outside_declared_provider_rejected(self):
        other = self.base / 'other-protoc'
        other.write_text('never run')
        other.chmod(0o700)
        with self.assertRaisesRegex(ValueError, 'explicit dependency prefix'):
            self.plan(protoc=other)

    def test_python_escape_rejected(self):
        path = self.provider / 'bin/python3.11'
        path.unlink()
        path.symlink_to(self.prefix / 'bin/protoc')
        with self.assertRaisesRegex(ValueError, 'escaped explicit provider'):
            self.plan()

    def test_original_gui_work_hook_required(self):
        (self.repo / 'tools/macos/build_launcher.sh').write_text('work=out/launcher-build\n')
        with self.assertRaisesRegex(ValueError, 'BB_LAUNCHER_WORK'):
            self.plan()

    def test_hardlink_source_rejected(self):
        os.link(self.source / 'targets.cmake', self.base / 'linked')
        with self.assertRaisesRegex(ValueError, 'single-link'):
            self.plan()

    def test_revision_and_version_not_shell_fragments(self):
        for kw in (dict(revision='HEAD'), dict(version='0.2.52;rm'), dict(revision='A' * 40)):
            with self.assertRaises(ValueError):
                self.plan(**kw)

    def test_execute_rejects_changed_source_before_observer_or_output(self):
        value = self.plan()
        (self.source / 'targets.cmake').write_text('changed source\n')
        with patch('native_observer.NativeObserver', side_effect=AssertionError('must not observe')):
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                build.execute(value)
        self.assertFalse(self.args['build_root'].exists())

    def test_artifact_comes_from_actual_file_api(self):
        normal = self.base / 'normal'
        reply = normal / '.cmake/api/v1/reply'
        reply.mkdir(parents=True)
        binary = normal / 'sub/bb-game-arm64'
        binary.parent.mkdir()
        binary.write_text('author only')
        (reply / 'index-a.json').write_text(json.dumps({'reply': {'codemodel-v2': {'jsonFile': 'model.json'}}}))
        (reply / 'model.json').write_text(json.dumps({'configurations': [{'targets': [{'name': 'bb-game-arm64', 'jsonFile': 'target.json'}]}]}))
        target = reply / 'target.json'
        target.write_text(json.dumps({'artifacts': [{'path': 'sub/bb-game-arm64'}]}))
        self.assertEqual(build.target_artifact(normal, 'bb-game-arm64'), binary)
        target.write_text(json.dumps({'artifacts': [{'path': '../repo/experiments/arm64-runtime/targets.cmake'}]}))
        with self.assertRaisesRegex(ValueError, 'escaped build'):
            build.target_artifact(normal, 'bb-game-arm64')


class GuardTests(unittest.TestCase):
    def guard(self, samples, no_games=lambda: None, times=None):
        class Observer:
            def system(self):
                return samples.pop(0)
        clock = iter(times or [0, 1, 2, 3, 4, 5, 6]).__next__
        return build.BuildGuard(Observer(), MemoryPolicy(dict(pressure=1, swap_mib=0, locked=False)), no_games, clock=clock)

    def test_new_game_during_compile_stops_immediately(self):
        calls = []
        def games():
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('game appeared')
        guard = self.guard([dict(pressure=1, swap_mib=0, locked=False)], games)
        guard.check(preflight=True)
        with self.assertRaisesRegex(RuntimeError, 'game appeared'):
            guard.check()
        self.assertEqual(len(calls), 2)

    def test_warning_allowed_and_lock_rejected(self):
        guard = self.guard([dict(pressure=2, swap_mib=0, locked=False), dict(pressure=2, swap_mib=0, locked=True)])
        guard.check(preflight=True)
        with self.assertRaisesRegex(ValueError, 'screen locked'):
            guard.check()

    def test_existing_burst_rule_unchanged(self):
        guard = self.guard([dict(pressure=1, swap_mib=1025, locked=False)])
        with self.assertRaisesRegex(ValueError, '1GiB'):
            guard.check()

    def test_severe_preflight_rejected(self):
        guard = self.guard([dict(pressure=4, swap_mib=0, locked=False)])
        with self.assertRaisesRegex(ValueError, 'severe pressure'):
            guard.check(preflight=True)

    def test_total_timeout(self):
        guard = self.guard([dict(pressure=1, swap_mib=0, locked=False)], times=[0, 7201])
        with self.assertRaisesRegex(ValueError, 'total timeout'):
            guard.check()


if __name__ == '__main__':
    unittest.main()
