"""Authored files only; no GUI, subprocess, native loader, cache or save access."""
from pathlib import Path
from dataclasses import FrozenInstanceError
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import qa_launch_conditions as q
from prepare_local_aot_stage import HOOK, LEGACY_RENDER_LOG, runtime_log_revision


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'prototype'
        self.stage = self.project / 'apps/Blood Echoes ARM64 GUI QA 1000.app/Contents/Resources/bbport'
        self.app = self.stage.parents[2]
        self.root = self.project / 'state/gui-root-1000'
        self.data = self.root / 'optimized-launcher'
        self.receipt = self.data / 'out/launch-conditions.json'
        self.evidence = self.project / 'evidence/launcher-gui-run-1000'
        self.baseline = self.project / 'launcher-component-stage'
        for directory in (self.stage, self.receipt.parent, self.evidence, self.baseline,
                          self.project / 'state/gui-user-1000', self.project / 'state/gui-gpu-local-aot-1001',
                          self.base / 'assets/game', self.base / 'assets/translation'):
            directory.mkdir(parents=True, exist_ok=True)
        self.addCleanup(patch.stopall)
        patch.object(q, 'PRIVATE_PROJECT', self.project).start()
        for name in ('Popen', 'run', 'check_output'):
            patch.object(subprocess, name, side_effect=AssertionError('native invocation forbidden')).start()
        self.file(self.app / 'Contents/MacOS/bbport', b'author launcher')
        original, effective = {}, {}
        for name in q.SCRIPTS:
            body = ('author ' + name + '\n').encode()
            if name == 'tools/macos/run_optimized.sh':
                body = LEGACY_RENDER_LOG.encode()
            self.file(self.baseline / name, body)
            changed = body + HOOK.encode() if name == 'run.sh' else runtime_log_revision(body.decode()).encode() if name == 'tools/macos/run_optimized.sh' else body
            self.file(self.stage / name, changed)
            original[name], effective[name] = sha(self.baseline / name), sha(self.stage / name)
        self.json(self.baseline / 'SOURCE-PARITY.json', dict(original_sources=original))
        self.parity = dict(original_sources=original, effective_sources=effective, final_exec_hook_only=True,
            runtime_log_only_correction=True, previous_stage_modified=False, previous_stage=str(self.baseline))
        self.env = dict(BB_ONLINE='0', BB_RENDERER='metal', BB_ARM_LAUNCHER_COMPAT='1', BB_METAL_ARM64='1',
            BB_DATA_DIR=str(self.data), BB_USER_DIR=str(self.project / 'state/gui-user-1000'),
            BB_GPU_USER_DIR=str(self.project / 'state/gui-gpu-local-aot-1001'), BB_GAME_DIR=str(self.base / 'assets/game'),
            BB_CHINESE_TRANSLATION_DIR=str(self.base / 'assets/translation'), BB_MODS_DIR=str(self.data / 'mods'),
            BB_PATCHES_DIR=str(self.data / 'patches'), BB_ARM_SESSION_NONCE='a' * 32, BB_ARM_RESTART_NONCE='b' * 32,
            BB_EXTRA_PRESERVE='not-reconstructed', MTL_DEBUG_LAYER='0', **{k: '1' for k in q.PROTECTION_KEYS})
        for key, name in q.CONFIG_NAMES.items():
            self.env[key] = str(self.data / name)
            self.file(self.data / name, ('authored ' + key).encode())
        self.identifier = q.PREFIX + '1000'
        self.info = dict(CFBundleIdentifier=self.identifier, CFBundleExecutable='bbport',
            BBPreviewDataPath=str(self.root), BBQALaunchReceipt=str(self.receipt))
        self.manifest = dict(schema=2, host_local_only=True, formal_data_modified=False, protected_resource_writeback=True,
            app=str(self.app), private_gpu_cache=self.env['BB_GPU_USER_DIR'],
            private_case=dict(bundle_id=self.identifier, app=str(self.app), root=str(self.root), data=str(self.data),
                evidence=str(self.evidence), user=self.env['BB_USER_DIR'], online=False, formal_data_modified=False),
            original_scripts=original, effective_scripts=effective, frozen_runtime={})
        for name in q.RUNTIME_FILES:
            self.file(self.stage / name, ('authored binary ' + name).encode())
            self.manifest['frozen_runtime'][name] = sha(self.stage / name)
        self.manifest['private_case']['cpu_sha256'] = self.manifest['frozen_runtime']['bin/bb-probe-metal']
        self.json(self.evidence / 'guard-ready.json', dict(pid=12345, path=str(self.app / 'Contents/MacOS/bbport')))
        self.record = dict(schema=1, producer=q.PRODUCER, launch_boundary='before-Process.run', game_started_verified=False,
            original_environment_unchanged=True, offline=True, formal_data_modified=False, bundle_id=self.identifier,
            bundle_path=str(self.app), working_directory=str(self.stage), executable='/bin/bash',
            arguments=['tools/macos/run_optimized.sh'], source_pid=12345, created_unix_ns=2000,
            launch_id='5E9746DB-429A-4975-8EA1-5E3678539F8D', environment=self.env)
        self.seal()

    def file(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def json(self, path, value):
        self.file(path, json.dumps(value).encode())

    def seal(self):
        self.file(self.app / 'Contents/Info.plist', plistlib.dumps(self.info))
        self.json(self.stage.parent / 'ARM64-QA-MANIFEST.json', self.manifest)
        self.json(self.stage / 'SOURCE-PARITY.json', self.parity)
        paths = {'launcher': self.app / 'Contents/MacOS/bbport',
                 'ARM64-QA-MANIFEST.json': self.stage.parent / 'ARM64-QA-MANIFEST.json',
                 **{k: self.stage / k for k in ('run.sh', 'tools/macos/run_optimized.sh', 'SOURCE-PARITY.json')},
                 **{k: Path(self.env[k]) for k in q.CONFIG_NAMES}}
        self.record['identity_sha256'] = {k: sha(v) for k, v in paths.items()}
        self.save()

    def save(self):
        self.json(self.receipt, self.record)

    def load(self):
        return q.load(self.receipt, stage=self.stage, project=self.project)

    def refuses(self, message=None):
        with self.assertRaisesRegex((ValueError, OSError), message or '.*'):
            self.load()

    def test_complete_original_env_and_immutable_configs_without_writes(self):
        before = {str(p): sha(p) for p in self.base.rglob('*') if p.is_file()}
        value = self.load()
        self.assertEqual(dict(value['environment']), self.env)
        self.assertEqual(value['source_pid'], 12345)
        self.assertFalse(value['game_started_verified'])
        self.assertEqual({str(p): sha(p) for p in self.base.rglob('*') if p.is_file()}, before)
        for key, config in value['configs'].items():
            self.assertEqual(config.data, Path(self.env[key]).read_bytes())
            self.assertEqual(config.sha256, sha(config.path))
            with self.assertRaises(FrozenInstanceError):
                config.data = b'no'
        with self.assertRaises(TypeError):
            value['environment']['BB_EXTRA_PRESERVE'] = 'no'
        self.assertTrue(all(not x[1] for x in value['optional_directories'].values()))

    def test_project_cannot_be_another_owned_root(self):
        other = self.base / 'other'; other.mkdir()
        with self.assertRaisesRegex(ValueError, 'fixed Shared'):
            q.load(self.receipt, stage=self.stage, project=other)

    def test_receipt_symlink_refused(self):
        other = self.receipt.with_suffix('.source'); self.receipt.rename(other); self.receipt.symlink_to(other)
        self.refuses('canonical')

    def test_receipt_hardlink_refused(self):
        os.link(self.receipt, self.receipt.with_suffix('.copy'))
        self.refuses('hard-linked')

    def test_unowned_inputs_refused(self):
        with patch.object(q.os, 'getuid', return_value=os.getuid() + 1):
            self.refuses('owned')

    def test_oversized_receipt_refused(self):
        self.record['extra'] = 'x' * (256 * 1024); self.save(); self.refuses('bounded')

    def test_duplicate_json_fields_refused(self):
        self.receipt.write_text('{"schema":1,' + self.receipt.read_text()[1:])
        self.refuses('duplicate JSON')

    def test_schema_boolean_is_not_integer_one(self):
        self.record['schema'] = True; self.save(); self.refuses('schema')

    def test_wrong_producer_or_launch_boundary_refused(self):
        for key, value in (('producer', 'preferences-export'), ('launch_boundary', 'after-guess'), ('game_started_verified', True)):
            original = self.record[key]; self.record[key] = value; self.save(); self.refuses(); self.record[key] = original

    def test_offline_and_original_semantics_required(self):
        for key in ('offline', 'original_environment_unchanged'):
            self.record[key] = False; self.save(); self.refuses(); self.record[key] = True
        self.record['formal_data_modified'] = True; self.save(); self.refuses()

    def test_original_argv_and_shell_required(self):
        self.record['arguments'].append('--benchmark'); self.save(); self.refuses('arguments')
        self.record['arguments'].pop(); self.record['executable'] = '/bin/zsh'; self.save(); self.refuses('arguments')

    def test_info_optin_wrong_path_refused(self):
        self.info['BBQALaunchReceipt'] = str(self.data / 'elsewhere.json'); self.seal(); self.refuses('opt-in')

    def test_formal_bundle_or_nondigit_suffix_refused(self):
        for identifier in ('io.github.bbport.optimized', q.PREFIX + 'not-numeric'):
            self.info['CFBundleIdentifier'] = identifier; self.seal(); self.refuses()

    def test_wrong_receipt_bundle_or_repo_refused(self):
        for key in ('bundle_path', 'working_directory', 'bundle_id'):
            original = self.record[key]; self.record[key] = 'wrong'; self.save(); self.refuses('bundle/shell'); self.record[key] = original

    def test_guard_pid_or_app_mismatch_refused(self):
        for guard in (dict(pid=12346, path=str(self.app / 'Contents/MacOS/bbport')),
                      dict(pid=12345, path='/formal/launcher'), dict(pid=True, path=str(self.app / 'Contents/MacOS/bbport'))):
            self.json(self.evidence / 'guard-ready.json', guard); self.refuses('launch guard')

    def test_wrong_or_boolean_source_pid_refused(self):
        for pid in (True, 0, -1, '12345'):
            self.record['source_pid'] = pid; self.save(); self.refuses('PID/time')

    def test_uuid_and_created_time_required(self):
        self.record['launch_id'] = self.record['launch_id'].lower(); self.save(); self.refuses('UUID')
        self.record['launch_id'] = self.record['launch_id'].upper(); self.record['created_unix_ns'] = 999; self.save(); self.refuses('PID/time')

    def test_environment_never_accepts_inherited_or_illegal_keys(self):
        for key in ('HOME', 'BB_lower', 'MTL_NOT-VALID'):
            self.env[key] = 'bad'; self.save(); self.refuses('environment entry'); del self.env[key]

    def test_all_credential_key_variants_refused(self):
        for word in q.FORBIDDEN:
            key = 'BB_QA_' + word; self.env[key] = 'private-value'; self.save(); self.refuses('credential-like'); del self.env[key]

    def test_environment_value_types_and_nul_refused(self):
        for item in (1, True, None, 'bad\0value'):
            self.env['BB_EXTRA_PRESERVE'] = item; self.save(); self.refuses('environment entry')

    def test_all_four_protections_must_be_preserved(self):
        for key in q.PROTECTION_KEYS:
            del self.env[key]; self.save(); self.refuses('four resource protections')
            self.env[key] = '0'; self.save(); self.refuses('four resource protections'); self.env[key] = '1'

    def test_offline_native_selection_and_nonce_refused_when_wrong(self):
        for key in ('BB_ONLINE', 'BB_RENDERER', 'BB_ARM_LAUNCHER_COMPAT', 'BB_METAL_ARM64', 'BB_ARM_SESSION_NONCE'):
            original = self.env[key]; self.env[key] = 'wrong'; self.save(); self.refuses(); self.env[key] = original

    def test_wrong_or_escaped_user_and_cache_refused(self):
        for key in ('BB_USER_DIR', 'BB_GPU_USER_DIR'):
            original = self.env[key]; self.env[key] = str(self.base / 'assets/game'); self.save(); self.refuses('outside admitted'); self.env[key] = original
        self.manifest['private_gpu_cache'] = '/wrong'; self.seal(); self.refuses('cache mismatch')

    def test_game_and_translation_must_stay_in_assets(self):
        for key in ('BB_GAME_DIR', 'BB_CHINESE_TRANSLATION_DIR'):
            original = self.env[key]; self.env[key] = str(self.root); self.save(); self.refuses('outside admitted'); self.env[key] = original

    def test_changed_launcher_script_manifest_and_config_refused(self):
        for path in (self.app / 'Contents/MacOS/bbport', self.stage / 'run.sh', self.stage.parent / 'ARM64-QA-MANIFEST.json', Path(self.env['BB_CONFIG'])):
            original = path.read_bytes(); path.write_bytes(original + b'changed'); self.refuses('SHA mismatch'); path.write_bytes(original)

    def test_config_path_cannot_escape_same_qa_data(self):
        self.env['BB_CONFIG'] = str(self.project / 'state/gui-user-1000'); self.save(); self.refuses('outside admitted')

    def test_existing_optional_directory_links_refused(self):
        target = self.data / 'mods'; target.symlink_to(self.base / 'assets/translation', target_is_directory=True)
        self.refuses('canonical')

    def test_source_effective_mismatch_refused_even_if_receipt_updated(self):
        self.manifest['effective_scripts'] = dict(self.parity['effective_sources'], **{'run.sh': 'a' * 64})
        self.seal(); self.refuses('source/effective')

    def test_third_script_change_is_not_controlled_delta(self):
        name = 'scripts/online.py'; self.file(self.stage / name, b'other implementation')
        self.parity['effective_sources'][name] = sha(self.stage / name)
        self.seal(); self.refuses('controlled two-script')

    def test_self_consistent_but_extra_run_hook_change_refused(self):
        self.file(self.stage / 'run.sh', (self.stage / 'run.sh').read_bytes() + b'other behavior')
        self.parity['effective_sources']['run.sh'] = sha(self.stage / 'run.sh')
        self.seal(); self.refuses('beyond original final-exec hook')

    def test_wrong_baseline_identity_refused(self):
        self.file(self.baseline / 'run.sh', b'changed original')
        self.refuses('SHA mismatch')

    def test_missing_identity_or_runtime_contract_refused(self):
        del self.record['identity_sha256']['launcher']; self.save(); self.refuses('identity domain')
        self.seal(); del self.manifest['frozen_runtime']['bin/game-module-preparer']; self.seal(); self.refuses('runtime path/identity')

    def test_new_cache_and_source_are_not_written_or_created(self):
        value = self.load()
        self.assertFalse((self.data / 'mods').exists())
        self.assertEqual(list(value['cache_root'].iterdir()), [])
        self.assertEqual(list(value['user_root'].iterdir()), [])

    def test_change_between_input_read_and_return_is_rejected(self):
        old = q.Reader.unchanged
        def changed(reader):
            Path(self.env['BB_CONFIG']).write_bytes(b'replaced after validation')
            return old(reader)
        with patch.object(q.Reader, 'unchanged', changed):
            self.refuses('SHA mismatch')


if __name__ == '__main__':
    unittest.main(verbosity=2)
