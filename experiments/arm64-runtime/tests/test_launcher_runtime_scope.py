"""Pure authored role facts only; never inspects a real formal path."""
from pathlib import Path, PurePosixPath
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import launcher_runtime_scope as scope


class Controls(unittest.TestCase):
    def setUp(self):
        self.uid = 1234
        self.session = dict(scope_id='bd0d548b-c2aa-4317-934a-414caf93decc', nonce='a' * 32, restart_nonce='b' * 32)
        self.compat = dict(source_sha256='c' * 64, driver_sha256='d' * 64, profile_sha256='3' * 64, device_id='author-device')
        self.compat['id'] = scope.compatibility_id(**self.compat)
        app = PurePosixPath('/Applications/Blood Echoes.app')
        data = PurePosixPath('/Users/author/Library/Application Support/bbport/optimized-launcher')
        gpu = data / 'gpu-cache/metal1'
        self.roles = dict(code=str(app / 'Contents/Resources/bbport'), data=str(data), out=str(data / 'out'),
            user='/Volumes/AuthorSaveDisk/ChosenBloodborneUser', gpu=str(gpu), gpu_namespace=str(gpu / ('native-arm64-' + self.compat['id'])),
            game='/Volumes/AuthorGameDisk/Bloodborne', mod=str(data / 'out/mod-game-author'),
            work=str(data / 'out/runtime-sessions' / self.session['nonce']))
        self.document = dict(schema=1, kind='blood-echoes-launcher-runtime-scope', prototype_only=True, production=False,
            profile='formal-prototype', roles=self.roles, session=self.session, compatibility=self.compat,
            bundle=dict(id='io.github.bbport.optimized', path=str(app), launcher_path=str(app / 'Contents/MacOS/bbport'),
                        launcher_sha256='e' * 64, component_manifest_sha256='f' * 64),
            state_policy=dict(progress_role='user', session_marker_role='work', module_metadata_role='work', restart_request_role='work'))
        self.selection = dict(producer='original-launcher-final-exec', home='/Users/author', roles=copy.deepcopy(self.roles))
        self.refresh()

    def refresh(self):
        d = self.document; self.roles = d['roles']
        code = PurePosixPath(self.roles['code']); out = PurePosixPath(self.roles['out']); data = PurePosixPath(self.roles['data'])
        d['components'] = {role: dict(path=str(code / scope.COMPONENT_PATHS[role][0]), sha256='1' * 64) for role in scope.COMPONENTS | scope.OPTIONAL_COMPONENTS}
        d['actual_load_edges'] = {role: d['components'][target]['path'] for role, target in
            (('cpu', 'cpu_host'), ('gpu', 'gpu_library'), ('pso_worker', 'gpu_library'), ('module_worker', 'gpu_library'))}
        d['executable'] = d['components']['cpu']['path']
        self.argv = [str(out / 'boot-linked.bin'), '--content-profile', str(out / 'content.bin'), '--patches', str(out / 'patches.bin'),
                     '--app0', self.roles['mod'], '--user', self.roles['user'], '--timeout', '0']
        d['argv'] = self.argv; d['argv_sha256'] = scope.argv_sha256(self.argv)
        d['config_files'] = {key: str(data / name) for key, name in
            (('settings', 'bbport.ini'), ('mods_config', 'mods.json'), ('patches_config', 'patches.json'))}
        self.selection['config_files'] = copy.deepcopy(d['config_files']); self.selection['roles'] = copy.deepcopy(self.roles)
        d['input_sha256'] = {name: '2' * 64 for name in ('boot', 'content', 'patches', 'settings', 'mods_config', 'patches_config')}
        self.observed = dict(producer='caller-held-actual-file-observations', bundle=copy.deepcopy(d['bundle']),
            compatibility=copy.deepcopy(self.compat), components=copy.deepcopy(d['components']),
            actual_load_edges=copy.deepcopy(d['actual_load_edges']), nodes={})
        for path in self.roles.values():
            self.observed['nodes'][path] = dict(canonical=path, kind='directory', uid=self.uid, is_symlink=False)
        def file(path, sha):
            self.observed['nodes'][path] = dict(canonical=path, kind='file', uid=self.uid, is_symlink=False, nlink=1, sha256=sha)
        file(d['bundle']['launcher_path'], d['bundle']['launcher_sha256'])
        file(str(code / 'NATIVE-METAL.json'), d['bundle']['component_manifest_sha256'])
        for item in d['components'].values():file(item['path'], item['sha256'])
        for key, path in dict(boot=str(out / 'boot-linked.bin'), content=str(out / 'content.bin'), patches=str(out / 'patches.bin'), **d['config_files']).items():
            file(path, d['input_sha256'][key])

    def validate(self):
        return scope.validate(self.document, selection=self.selection, observed=self.observed,
                              final_argv=self.argv, session=self.session, uid=self.uid)

    def refuses(self, message=None):
        with self.assertRaisesRegex(ValueError, message or '.*'):
            self.validate()

    def qa(self):
        p = PurePosixPath(str(scope.PRIVATE_PROJECT)); app = p / 'apps/Blood Echoes ARM64 GUI QA 7.app'
        data = p / 'state/gui-root-7/optimized-launcher'; gpu = p / 'state/gui-gpu-local-aot-7'
        self.document['profile'] = 'qa-shared'
        self.document['bundle'].update(id='io.github.bbport.optimized.options-qa.arm64.gui.7', path=str(app), launcher_path=str(app / 'Contents/MacOS/bbport'))
        self.document['roles'] = dict(code=str(app / 'Contents/Resources/bbport'), data=str(data), out=str(data / 'out'),
            user=str(p / 'state/gui-user-7'), gpu=str(gpu), gpu_namespace=str(gpu), game=str(p.parent / 'assets/game'),
            mod=str(data / 'out/mod-game-author'), work=str(data / 'out/runtime-sessions' / self.session['nonce']))
        self.document['state_policy'] = dict(progress_role='user', session_marker_role='work', module_metadata_role='work', restart_request_role='work')
        self.refresh()

    def test_pure_formal_candidate_never_inspects_formal_paths_or_authorizes(self):
        with patch.object(Path, 'stat', side_effect=AssertionError('formal stat forbidden')), \
             patch.object(Path, 'lstat', side_effect=AssertionError('formal lstat forbidden')), \
             patch.object(Path, 'resolve', side_effect=AssertionError('formal resolve forbidden')), \
             patch.object(os, 'open', side_effect=AssertionError('formal open forbidden')), \
             patch.object(subprocess, 'run', side_effect=AssertionError('native forbidden')):
            result = self.validate()
            self.assertFalse(result.authorization)
            self.assertFalse(result.native_integration)
            self.assertFalse(result.filesystem_verified_by_this_reader)
            classification = result.classify('gpu', 'gpu_namespace', self.roles['gpu_namespace'] + '/cache/a', 'write')
            self.assertTrue(classification['structural_candidate']); self.assertFalse(classification['authorization'])

    def test_default_and_custom_user_are_exact_selections(self):
        custom = self.validate(); self.assertEqual(custom.roles['user'], '/Volumes/AuthorSaveDisk/ChosenBloodborneUser')
        self.document['roles']['user'] = '/Users/author/Library/Application Support/bbport/user'; self.refresh()
        self.assertEqual(self.validate().roles['user'], self.document['roles']['user'])

    def test_qa_shared_gate_remains_and_formal_namespace_is_separate(self):
        self.qa(); result = self.validate(); self.assertEqual(result.profile, 'qa-shared')
        self.assertFalse(result.classify('metadata_helper', 'user', self.roles['user'] + '/.arm-module-info.bin', 'write')['structural_candidate'])
        self.assertTrue(result.classify('metadata_helper', 'work', self.roles['work'] + '/.arm-module-info.bin', 'write')['structural_candidate'])
        self.assertFalse(result.classify('cpu', 'user', self.roles['user'] + '/.arm-runtime-session', 'write')['structural_candidate'])
        self.document['roles']['user'] = '/Users/author/formal-user'; self.refresh(); self.refuses('Shared QA boundary')

    def test_formal_cannot_reuse_qa_bundle_or_data(self):
        self.document['bundle']['id'] += '.options-qa.arm64.gui.7'; self.observed['bundle'] = copy.deepcopy(self.document['bundle'])
        self.refuses('formal prototype')
        self.document['bundle']['id'] = 'io.github.bbport.optimized'; self.document['roles']['user'] = str(scope.PRIVATE_PROJECT / 'state/gui-user-7')
        self.refresh(); self.refuses('formal prototype')

    def test_entire_home_or_ancestor_is_not_a_scope(self):
        for broad in ('/Users/author', '/Users', '/', '/Applications'):
            self.document['roles']['user'] = broad; self.selection['roles'] = copy.deepcopy(self.document['roles'])
            self.refuses('broad root')

    def test_role_selection_mismatch_cannot_be_fixed_by_descriptor_only(self):
        self.document['roles']['user'] = '/Volumes/OtherSaveDisk/User'; self.refuses('exact role selection')

    def test_production_flag_or_extra_allow_home_field_never_enables(self):
        self.document['production'] = True; self.refuses('prototype-only')
        self.document['production'] = False; self.document['allow_home'] = True; self.refuses('closed descriptor')

    def test_actual_bundle_launcher_and_component_manifest_sha_required(self):
        for key in ('launcher_sha256', 'component_manifest_sha256'):
            original = self.document['bundle'][key]; self.document['bundle'][key] = '9' * 64
            self.refuses('bundle/manifest/launcher'); self.document['bundle'][key] = original
        p = self.roles['code'] + '/NATIVE-METAL.json'; self.observed['nodes'][p]['sha256'] = '9' * 64
        self.refuses('SHA observation')

    def test_undeclared_component_or_wrong_role_path_refused(self):
        self.document['components']['cpu']['path'] = self.roles['code'] + '/bin/other'
        self.observed['components'] = copy.deepcopy(self.document['components']); self.refuses('component role')

    def test_nested_game_exec_is_explicit_not_implicitly_selected(self):
        role = 'cpu'; old = self.document['components'][role]['path']
        new = self.roles['code'] + '/' + scope.COMPONENT_PATHS[role][1]
        self.document['components'][role]['path'] = new; self.document['executable'] = new
        self.observed['components'] = copy.deepcopy(self.document['components']); self.observed['nodes'][new] = dict(self.observed['nodes'][old], canonical=new)
        self.assertEqual(self.validate().components['cpu']['path'], new)

    def test_actual_load_edge_mismatch_refused(self):
        self.observed['actual_load_edges']['cpu'] = self.document['components']['gpu_library']['path']
        self.refuses('load edges')

    def test_owner_symlink_hardlink_and_canonical_facts_are_not_self_claims(self):
        node = self.observed['nodes'][self.document['components']['cpu']['path']]
        for key, value in (('uid', 5678), ('uid', True), ('is_symlink', True), ('nlink', 2), ('canonical', '/other')):
            original = node[key]; node[key] = value; self.refuses(); node[key] = original

    def test_source_and_driver_identity_bind_new_cache_namespace(self):
        self.document['roles']['gpu_namespace'] = self.document['roles']['gpu']; self.refresh()
        self.refuses('new exact compatibility namespace')

    def test_cache_compatibility_cannot_be_self_reported(self):
        self.document['compatibility']['driver_sha256'] = '9' * 64
        self.refuses('compatibility')

    def test_old_session_and_nonce_are_rejected(self):
        self.document['session'] = dict(self.session, nonce='9' * 32); self.refuses('supervisor session')

    def test_runtime_metadata_cannot_be_redirected_to_formal_user(self):
        self.document['state_policy']['module_metadata_role'] = 'user'; self.refuses('metadata/progress')

    def test_progress_work_and_cache_cannot_overlap(self):
        self.document['roles']['user'] = self.document['roles']['out']; self.refresh(); self.refuses('overlap')

    def test_original_argv_not_recreated_from_approximate_environment(self):
        self.document['argv'] = self.argv + ['--cpu-only']; self.refuses('argv binding')

    def test_argv_duplicate_unknown_and_misdirected_roles_refused(self):
        for addition in (['--user', self.roles['user']], ['--unknown', '1']):
            original = self.argv[:]; self.argv += addition; self.document['argv'] = self.argv; self.document['argv_sha256'] = scope.argv_sha256(self.argv)
            self.refuses(); self.argv = original
        self.document['argv'] = self.argv; self.argv[self.argv.index('--app0') + 1] = self.roles['game']
        self.document['argv_sha256'] = scope.argv_sha256(self.argv); self.refuses('argument role')

    def test_changed_final_input_hash_and_config_path_refused(self):
        self.document['input_sha256']['content'] = '9' * 64; self.refuses('SHA observation')
        self.document['input_sha256']['content'] = '2' * 64
        self.document['config_files']['settings'] = self.selection['home'] + '/settings'; self.refuses('configuration roles')

    def test_structural_operations_do_not_broaden_through_out_or_code(self):
        result = self.validate()
        for actor, role, target in [('gpu', 'user', self.roles['user'] + '/savedata/a'),
                                    ('metadata_helper', 'user', self.roles['user'] + '/.arm-module-info.bin'),
                                    ('cpu', 'code', self.roles['code'] + '/bin/undeclared'),
                                    ('cpu', 'out', self.roles['mod'] + '/asset'),
                                    ('cpu', 'out', self.roles['out'] + '/content.bin'),
                                    ('cpu', 'user', self.roles['user'] + '/.arm-runtime-session')]:
            self.assertFalse(result.classify(actor, role, target, 'write')['structural_candidate'])
        self.assertFalse(result.classify('cpu', 'data', self.roles['data'] + '/other.ini', 'read')['structural_candidate'])

    def test_path_dotdot_control_and_double_slash_refused(self):
        for value in ('/Users/author/x/../other', '//host/share', '/Users/author/a\nfile', '/Users//author'):
            with self.assertRaises(ValueError):scope.path(value)

    def test_result_is_deeply_readonly_and_non_authorizing(self):
        result = self.validate()
        with self.assertRaises(TypeError):result.roles['user'] = '/other'
        with self.assertRaises(TypeError):result.components['cpu']['sha256'] = '9' * 64
        self.assertFalse(result.authorization)

    def test_shared_descriptor_reader_rejects_symlinks_hardlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp).resolve() / 'prototype'; folder = p / 'evidence/scope'; folder.mkdir(parents=True)
            f = folder / 'scope.json'; f.write_text(json.dumps(self.document))
            facts = dict(selection=self.selection, observed=self.observed, final_argv=self.argv, session=self.session, uid=self.uid)
            with patch.object(scope, 'PRIVATE_PROJECT', p):
                self.assertFalse(scope.load_shared(f, project=p, **facts).authorization)
                alias = folder / 'link'; alias.symlink_to(f)
                with self.assertRaises(ValueError):scope.load_shared(alias, project=p, **facts)
                other = folder / 'hard'; os.link(f, other)
                with self.assertRaises(ValueError):scope.load_shared(f, project=p, **facts)
                other.unlink(); f.write_bytes(b'x' * (256 * 1024 + 1))
                with self.assertRaises(ValueError):scope.load_shared(f, project=p, **facts)

    def test_shared_reader_does_not_open_descriptor_outside_dev_root(self):
        with patch.object(os, 'open', side_effect=AssertionError('must not open outside Shared')):
            with self.assertRaises((ValueError, FileNotFoundError)):
                scope.load_shared('/Applications/nonexistent-formal-scope.json', project=scope.PRIVATE_PROJECT,
                    selection=self.selection, observed=self.observed, final_argv=self.argv, session=self.session, uid=self.uid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
