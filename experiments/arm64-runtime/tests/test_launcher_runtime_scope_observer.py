"""Real author file observations; injected native fixture results, no native calls."""
from pathlib import Path
import copy
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_launcher_runtime_scope as scope_fixture
import launcher_runtime_scope as scope
import launcher_runtime_scope_observer as observer
import qa_launch_conditions as reader


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve() / 'prototype'; self.project.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(scope, 'PRIVATE_PROJECT', self.project).start()
        patch.object(observer, 'PRIVATE_PROJECT', self.project).start()
        for name in ('run', 'Popen', 'check_output'):
            patch.object(subprocess, name, side_effect=AssertionError('native invocation forbidden')).start()
        self.f = scope_fixture.Controls('test_qa_shared_gate_remains_and_formal_namespace_is_separate')
        self.f.setUp(); self.f.uid = os.getuid(); self.f.qa()
        self.document = self.f.document
        for value in self.f.roles.values():
            Path(value).mkdir(parents=True, exist_ok=True)
        self.app = Path(self.document['bundle']['path'])
        self.code = Path(self.f.roles['code'])
        self.put(self.app / 'Contents/Info.plist', plistlib.dumps(dict(CFBundleIdentifier=self.document['bundle']['id'], CFBundleExecutable='bbport')))
        self.put(self.document['bundle']['launcher_path'], b'author launcher, not executable')
        self.put(self.code / 'NATIVE-METAL.json', json.dumps(dict(schema=1, game_runtime='fex-arm64', production=False)).encode())
        self.document['bundle']['launcher_sha256'] = sha(self.document['bundle']['launcher_path'])
        self.document['bundle']['component_manifest_sha256'] = sha(self.code / 'NATIVE-METAL.json')
        for role, item in self.document['components'].items():
            self.put(item['path'], ('author component ' + role).encode()); item['sha256'] = sha(item['path'])
        out = Path(self.f.roles['out'])
        self.input_paths = dict(boot=out / 'boot-linked.bin', content=out / 'content.bin', patches=out / 'patches.bin',
                               **{k: Path(v) for k, v in self.document['config_files'].items()})
        for key, path in self.input_paths.items():
            self.put(path, ('author final input ' + key).encode()); self.document['input_sha256'][key] = sha(path)
        components = self.document['components']
        self.native = {role: dict(target=components[role]['path'], target_sha256=sha(components[role]['path']),
            library=components[target]['path'], library_sha256=sha(components[target]['path']),
            load_edge_verified=True, strict_signatures=True, architecture='arm64') for role, target in observer.EDGE_TARGETS.items()}
        self.native['preparation'] = dict(kind='blood-echoes-game-pso-component', enabled=True, production=False,
            worker=Path(components['pso_worker']['path']), worker_sha256=sha(components['pso_worker']['path']),
            module_consumer=Path(components['module_worker']['path']), module_consumer_sha256=sha(components['module_worker']['path']),
            library=Path(components['gpu_library']['path']), library_sha256=sha(components['gpu_library']['path']))
        self.native['compatibility'] = {key: value for key, value in self.f.compat.items() if key != 'id'}

    def put(self, path, raw):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def observe(self):
        return observer.observe(self.document, selection=self.f.selection, final_argv=self.f.argv, session=self.f.session,
                                uid=self.f.uid, project=self.project, native_results=self.native)

    def test_actual_files_independent_results_and_original_validator(self):
        before = {str(p): sha(p) for p in self.project.parent.rglob('*') if p.is_file()}
        actual = self.observe()
        result = scope.validate(self.document, selection=self.f.selection, observed=actual, final_argv=self.f.argv,
                                session=self.f.session, uid=self.f.uid)
        self.assertFalse(result.authorization)
        self.assertFalse(actual['observation_receipt']['native_commands_executed'])
        self.assertFalse(actual['observation_receipt']['formal_paths_enabled'])
        for role, item in actual['components'].items():
            self.assertEqual(item['sha256'], sha(item['path']))
            self.assertEqual(actual['nodes'][item['path']]['uid'], os.getuid())
        self.assertEqual(before, {str(p): sha(p) for p in self.project.parent.rglob('*') if p.is_file()})

    def test_formal_profile_rejected_before_any_path_io(self):
        self.document['profile'] = 'formal-prototype'
        with patch.object(Path, 'resolve', side_effect=AssertionError('formal path resolution forbidden')), \
             patch.object(os, 'open', side_effect=AssertionError('formal open forbidden')):
            with self.assertRaisesRegex(ValueError, 'only existing Shared'):
                self.observe()

    def test_wrong_expected_sha_does_not_become_an_observation(self):
        self.document['components']['cpu']['sha256'] = '9' * 64
        original = scope.validate
        with patch.object(scope, 'validate', wraps=original) as validate:
            with self.assertRaisesRegex(ValueError, 'actual component identities'):
                self.observe()
            facts = validate.call_args.kwargs['observed']
        self.assertEqual(facts['components']['cpu']['sha256'], sha(self.document['components']['cpu']['path']))
        self.assertNotEqual(facts['components']['cpu']['sha256'], self.document['components']['cpu']['sha256'])

    def test_wrong_expected_bundle_sha_is_not_copied(self):
        self.document['bundle']['launcher_sha256'] = '9' * 64
        with self.assertRaisesRegex(ValueError, 'actual bundle'):
            self.observe()

    def test_component_path_cannot_leave_exact_role_or_shared(self):
        self.document['components']['metadata_helper']['path'] = '/Users/author/formal-helper'
        with patch.object(Path, 'resolve', side_effect=AssertionError('outside path resolution forbidden')):
            with self.assertRaisesRegex(ValueError, 'precise role'):
                self.observe()

    def test_role_outside_shared_rejected_before_resolve(self):
        self.document['roles']['user'] = '/Users/author/formal-user'
        self.f.selection['roles']['user'] = '/Users/author/formal-user'
        with patch.object(Path, 'resolve', side_effect=AssertionError('outside root resolution forbidden')):
            with self.assertRaisesRegex(ValueError, 'Shared role bounds'):
                self.observe()

    def test_component_symlink_and_hardlink_refused(self):
        target = Path(self.document['components']['cpu']['path']); other = target.with_name('original')
        target.rename(other); target.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            self.observe()
        target.unlink(); os.link(other, target)
        with self.assertRaisesRegex(ValueError, 'hard-linked'):
            self.observe()

    def test_role_directory_link_refused(self):
        game = Path(self.f.roles['game']); other = game.with_name('other'); game.rename(other); game.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            self.observe()

    def test_native_stale_target_library_and_failure_flags_refused(self):
        for key, wrong in [('target_sha256', '9' * 64), ('library_sha256', '9' * 64), ('library', '/wrong'),
                           ('strict_signatures', False), ('load_edge_verified', False), ('architecture', 'x86_64')]:
            old = self.native['cpu'][key]; self.native['cpu'][key] = wrong
            with self.assertRaisesRegex(ValueError, 'injected actual'):
                self.observe()
            self.native['cpu'][key] = old

    def test_native_result_is_required_not_descriptor_self_report(self):
        del self.native['cpu']
        with self.assertRaisesRegex(ValueError, 'separate existing-loader'):
            self.observe()

    def test_preparation_loader_result_must_match_both_actual_workers(self):
        self.native['preparation']['module_consumer_sha256'] = '9' * 64
        with self.assertRaisesRegex(ValueError, 'preparation loader'):
            self.observe()

    def test_actual_compatibility_not_descriptor_value(self):
        self.native['compatibility']['driver_sha256'] = '9' * 64
        with self.assertRaisesRegex(ValueError, 'cache compatibility'):
            self.observe()

    def test_duplicate_json_uses_existing_reader_parser(self):
        self.put(self.code / 'NATIVE-METAL.json', b'{"schema":1,"schema":1,"game_runtime":"fex-arm64","production":false}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            self.observe()

    def test_file_changed_inside_stream_is_refused(self):
        target = self.input_paths['content']; old = reader.os.read; changed = []
        def read(fd, amount):
            raw = old(fd, amount)
            if raw.startswith(b'author final input content') and not changed:
                target.write_bytes(b'changed during input stream'); changed.append(True)
            return raw
        with patch.object(reader.os, 'read', side_effect=read):
            with self.assertRaisesRegex(ValueError, 'changed during read|grew during read'):
                self.observe()

    def test_same_content_inode_replacement_is_refused(self):
        target = self.input_paths['content']; old = observer.StreamReader.read; changed = []
        def read(stream, value, **kw):
            result = old(stream, value, **kw)
            if Path(value) == target and not changed:
                replacement = target.with_name('replacement'); replacement.write_bytes(target.read_bytes())
                os.replace(replacement, target); changed.append(True)
            return result
        with patch.object(observer.StreamReader, 'read', read):
            with self.assertRaisesRegex(ValueError, 'file replaced'):
                self.observe()

    def test_role_replacement_after_file_reads_is_refused(self):
        game = Path(self.f.roles['game']); old = observer.StreamReader.unchanged
        def unchanged(stream):
            old(stream); game.rename(game.with_name('old-game')); game.mkdir()
        with patch.object(observer.StreamReader, 'unchanged', unchanged):
            with self.assertRaisesRegex(ValueError, 'role directory changed'):
                self.observe()

    def test_component_inode_replacement_after_sha_recheck_is_refused(self):
        target = Path(self.document['components']['cpu']['path']); old = observer.StreamReader.unchanged
        def unchanged(stream):
            old(stream)
            other = target.with_name('new-same-content'); other.write_bytes(target.read_bytes()); os.replace(other, target)
        with patch.object(observer.StreamReader, 'unchanged', unchanged):
            with self.assertRaisesRegex(ValueError, 'replaced before observation return'):
                self.observe()

    def test_streaming_does_not_use_whole_file_read_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole component read forbidden')):
            self.assertFalse(self.observe()['observation_receipt']['authorization'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
