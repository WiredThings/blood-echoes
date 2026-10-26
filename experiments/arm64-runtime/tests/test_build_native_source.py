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
        identity_source = Path(__file__).resolve().parents[3] / 'tools/macos/build_identity.py'
        (script.parent / 'build_identity.py').write_bytes(identity_source.read_bytes())
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
        self.assertIn('-DBB_LTO=OFF', commands['renderer-configure']['argv'])
        self.assertIn('-DBB_PGO=off', commands['renderer-configure']['argv'])
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

    def test_xcode_sdk_alias_is_normalized_without_output_access(self):
        sdk = self.base / 'Xcode.app/Contents/Developer/SDKs/MacOSX.sdk'
        sdk.mkdir(parents=True)
        alias = sdk.with_name('MacOSX27.0.sdk')
        alias.symlink_to(sdk.name, target_is_directory=True)
        value = self.plan(sdk_root=alias)
        self.assertEqual(value['sdk_root'], str(sdk))
        self.assertFalse(Path(value['build_root']).exists())

    def test_owned_built_gui_directory_is_valid_input(self):
        app = self.base / 'build/gui/Blood Echoes.app'
        app.mkdir(parents=True)
        self.assertEqual(build.directory(app), app)

    def test_output_inside_application_still_rejected(self):
        app = self.base / 'Existing.app'
        app.mkdir()
        with self.assertRaisesRegex(ValueError, 'application'):
            self.plan(build_root=app / 'fresh')

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

    def test_unpackageable_release_identity_is_rejected_before_build(self):
        for kwargs in (dict(version='0.2.52-beta'), dict(revision='a' * 64)):
            with self.assertRaisesRegex(ValueError, 'version|revision'):
                self.plan(**kwargs)
            self.assertFalse(self.args['build_root'].exists())

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

    def identify_export(self, *, top=None, revision=None):
        def reject_parent_git(*args):
            raise AssertionError('parent HEAD/status must not be consumed')
        return build.identify_source(self.repo, revision or self.args['revision'], git_top=lambda: top, git=reject_parent_git)

    def export_manifest(self, value=None):
        (self.repo / 'SOURCE-MANIFEST.json').write_text(json.dumps(value or {'source_commit': self.args['revision']}))

    def test_gitless_export_reuses_original_manifest_parser_without_writes(self):
        self.export_manifest([{'component': 'blood-echoes', 'revision': 'A' * 40}])
        before = sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))
        with patch('subprocess.Popen', side_effect=AssertionError('native call')):
            result = self.identify_export()
        self.assertEqual(result['mode'], 'source-export')
        self.assertEqual(result['revision'], 'a' * 40)
        self.assertNotIn('tree', result)
        self.assertIn('tools/macos/build_launcher.sh', result['source_files'])
        self.assertEqual(before, sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*')))

    def test_gitless_missing_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'requires SOURCE-MANIFEST'):
            self.identify_export()

    def test_gitless_wrong_manifest_revision_rejected(self):
        self.export_manifest({'source_commit': 'b' * 40})
        with self.assertRaisesRegex(ValueError, 'export revision differs'):
            self.identify_export()

    def test_parent_repository_is_not_export_identity(self):
        with self.assertRaisesRegex(ValueError, 'requires SOURCE-MANIFEST'):
            self.identify_export(top=self.base)
        self.export_manifest()
        result = self.identify_export(top=self.base)
        self.assertEqual(result['mode'], 'source-export')
        self.assertNotIn('tree', result)

    def test_repository_marker_with_wrong_top_rejected(self):
        self.export_manifest()
        (self.repo / '.git').mkdir()
        with self.assertRaisesRegex(ValueError, 'marker disagrees'):
            self.identify_export(top=self.base)

    def test_source_export_snapshot_covers_source_gui_resource_and_scripts(self):
        self.export_manifest()
        paths = [self.source / 'src/runtime.cpp', self.repo / 'scripts/prepare.py',
                 self.repo / 'launcher-macos/Sources/BBLauncher/Resources/AppIcon.icns']
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original')
        before = self.identify_export()
        for path in paths:
            path.write_text('changed')
            after = self.identify_export()
            self.assertNotEqual(before['snapshot_sha256'], after['snapshot_sha256'])
            with self.assertRaisesRegex(ValueError, 'source changed during build'):
                build.need(after == before, 'source changed during build')
            path.write_text('original')
        self.assertEqual(before, self.identify_export())

    def test_source_export_snapshot_does_not_read_development_evidence(self):
        self.export_manifest()
        evidence = self.source / 'evidence/private/game.bin'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('never source')
        result = self.identify_export()
        self.assertFalse(any('evidence' in p for p in result['source_files']))

    def test_exact_git_top_keeps_head_clean_and_tree_contract(self):
        calls = []
        def git(*args):
            calls.append(args)
            return {'HEAD': 'a' * 40, 'HEAD^{tree}': 'b' * 40}.get(args[-1], '')
        result = build.identify_source(self.repo, 'a' * 40, git_top=lambda: self.repo, git=git)
        self.assertEqual(result, dict(mode='git', revision='a' * 40, tree='b' * 40))
        self.assertTrue(any(args[0] == 'status' for args in calls))

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
