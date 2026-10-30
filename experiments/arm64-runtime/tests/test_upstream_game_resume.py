"""Read-only real-file admission tests; never invoke a game or native compiler."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'tools')]
import run_upstream_refresh_game as harness
import run_qa_launch_benchmark as driver
import qa_launch_clone as clone
from qa_launch_conditions import CONFIG_NAMES, ConfigSnapshot
from qa_memory_policy import MemoryPolicy


class ResumePrepareA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='be-upstream-resume-')
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve()
        for name in ('state', 'evidence', 'apps'):
            (self.project / name).mkdir()
        for module, field in ((harness, 'PROJECT'), (driver, 'P'), (clone, 'PRIVATE_PROJECT')):
            p = patch.object(module, field, self.project); p.start(); self.addCleanup(p.stop)
        p = patch('subprocess.Popen', side_effect=AssertionError('no processes allowed'))
        p.start(); self.addCleanup(p.stop)
        self.root = self.project / 'evidence/upstream-refresh-game-111'
        self.run = self.root / 'prepare-A'; self.run.mkdir(parents=True)
        state = self.project / 'state'
        self.data = state / 'benchmark-data-222'; self.user = state / 'benchmark-user-222'
        self.cache = state / 'gui-gpu-local-aot-222'
        source_data = state / 'gui-root-123'; source_user = state / 'gui-user-123'
        for path in (self.data, self.user, self.cache, source_data, source_user): path.mkdir()
        (self.data / 'out').mkdir(); (self.data / 'pad.txt').write_bytes(b'')
        for root in (self.data, source_data):
            for name in ('mods', 'patches'):
                (root/name).mkdir(); (root/name/'resource.bin').write_bytes(b'original resource')
        for path in (self.user, source_user):
            (path / 'savedata').mkdir(); (path / 'savedata/progress.bin').write_bytes(b'isolated progress')
        self.receipt = source_data / 'launch-conditions.json'; self.receipt.write_text('{"original":true}')
        config = {}
        for key, name in CONFIG_NAMES.items():
            raw = ('config ' + key).encode(); (source_data / name).write_bytes(raw); (self.data / name).write_bytes(raw)
            config[key] = ConfigSnapshot(source_data / name, raw, hashlib.sha256(raw).hexdigest())
        stage = self.project / 'evidence/candidate.app/Contents/Resources/bbport'; stage.mkdir(parents=True)
        env = dict(BB_AUDIO='none', BB_ONLINE='0', BB_QUICKSAVE='1', BB_FRAME_STATS='0',
                   BB_USER_DIR=str(source_user), BB_DATA_DIR=str(source_data), BB_GPU_USER_DIR=str(state / 'gui-gpu-local-aot-123'),
                   BB_CONFIG=str(source_data/'bbport.ini'), BB_MODS_CONFIG=str(source_data/'mods.json'),
                   BB_PATCHES_CONFIG=str(source_data/'patches.json'), BB_MODS_DIR=str(source_data/'mods'),
                   BB_PATCHES_DIR=str(source_data/'patches'), BB_ARM_SESSION_NONCE='a'*32, BB_ARM_RESTART_NONCE='b'*32)
        self.source = dict(environment=env, receipt=self.receipt, configs=config, user_root=source_user, data_root=source_data,
                           game_root=self.project/'assets/game', arguments=('tools/macos/run_optimized.sh',), executable='/bin/bash')
        hashes = {key: hashlib.sha256(key.encode()).hexdigest() for key in driver.FORMAL_ROLES}
        self.formal = dict(stage=stage, app=stage.parents[2], runtime_sha256=hashes,
                           runtime={'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu':stage/'bin/gpu'},
                           identity=dict(runtime_sha256=hashes, app=str(stage.parents[2]), native_manifest_sha256='c'*64))
        self.native = dict(cpu={'sha':'d'*64}, gpu={'sha':'e'*64})
        private = dict(BB_DATA_DIR=str(self.data), BB_CONFIG=str(self.data/'bbport.ini'), BB_USER_DIR=str(self.user),
            BB_MODS_DIR=str(self.data/'mods'), BB_MODS_CONFIG=str(self.data/'mods.json'),
            BB_PATCHES_DIR=str(self.data/'patches'), BB_PATCHES_CONFIG=str(self.data/'patches.json'), BB_GPU_USER_DIR=str(self.cache),
            BB_ARM_SESSION_NONCE='c'*32, BB_ARM_RESTART_NONCE='d'*32, BB_PAD_FILE=str(self.data/'pad.txt'),
            BB_ARM_QA_PAD_FILE=str(self.data/'pad.txt'), BB_PAD_FILE_ONLY='1', BB_FRAME_TIMING='1',
            BB_FRAME_LOG_ONLY='1', BB_BENCHMARK_MUTE='1')
        case = dict(environment=dict(env, **private), data_root=self.data, user_root=self.user, cache_root=self.cache,
                    pad=self.data/'pad.txt', allowed_changes=sorted(clone.ALLOWED_CHANGES))
        final, changes, allowed, experiment = harness.controlled_environment(driver, self.source, case, self.formal, 'A', vertex_ab=True)
        marker = f'ARMQA1\n{self.data}/out/boot-linked.bin\n{self.source["game_root"]}\n{self.data}/out/content.bin\n'+private['BB_ARM_SESSION_NONCE']+'\n'
        (self.user/'.arm-runtime-session').write_text(marker)
        self.conditions = dict(command=['/bin/bash',str(stage/'tools/macos/run_optimized.sh'),'--benchmark'], cwd=str(stage),
            environment=final, source_receipt=str(self.receipt), private_data=str(self.data), private_user=str(self.user),
            private_cache=str(self.cache), actual_changes=changes, allowed_changes=allowed, experiment=experiment)
        self.prior = dict(status='incomplete', phase='prepare-A', variant='A', scene_ready=None,
            source_save_unchanged=True, functional_f3_requested=False, performance_sample=True,
            stop='unexpected >2GiB swap growth', memory_policy=MemoryPolicy.MANIFEST,
            source_receipt_sha256=driver.sha(self.receipt), native_identity=self.native, formal_native_identity=self.formal['identity'],
            silent=True,Cua=False,screen_capture=False,profiler=False,formal_data_modified=False,quality_or_effects_changed=False,
            settings_sha256={key:snapshot.sha256 for key,snapshot in config.items()}, experiment=experiment)
        self.flush()

    def flush(self):
        (self.run/'conditions.json').write_text(json.dumps(self.conditions))
        (self.run/'result.json').write_text(json.dumps(self.prior))
        (self.root/'summary.json').write_text(json.dumps(dict(status='incomplete',comparison_mode='same-binary-vertex',
            runs=[self.prior],policy=MemoryPolicy.MANIFEST,source_receipt_sha256=self.prior['source_receipt_sha256'])))

    def resume(self, **kwargs):
        return harness.resume_prepare_a(self.root, self.source, self.formal, self.native, driver,
                                        vertex_ab=kwargs.get('vertex_ab',True), phase=kwargs.get('phase','performance'))

    def test_exact_existing_case_is_reconstructed_read_only(self):
        before={str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        case, record=self.resume()
        self.assertEqual(case['data_root'],self.data)
        self.assertEqual(case['source_save_manifest'],clone.inventory_savedata(self.source['user_root']/'savedata'))
        final,*_=harness.controlled_environment(driver,self.source,case,self.formal,'A',vertex_ab=True)
        self.assertEqual(final,self.conditions['environment'])
        self.assertFalse(record['prior_run_counted_as_sample'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_new_source_receipt_rejected(self):
        self.receipt.write_text('{"original":false}')
        with self.assertRaisesRegex(ValueError,'receipt changed'): self.resume()

    def test_every_actual_native_role_change_rejected(self):
        for role in driver.FORMAL_ROLES:
            with self.subTest(role=role):
                formal=copy.deepcopy(self.formal); formal['identity']['runtime_sha256'][role]='0'*64
                with self.assertRaisesRegex(ValueError,'eight-role'):
                    harness.resume_prepare_a(self.root,self.source,formal,self.native,driver,vertex_ab=True,phase='performance')

    def test_source_native_identity_change_rejected(self):
        self.native=copy.deepcopy(self.native);self.native['cpu']['sha']='0'*64
        with self.assertRaisesRegex(ValueError,'eight-role'):self.resume()

    def test_private_configuration_changed_rejected(self):
        (self.data/'bbport.ini').write_text('different graphics')
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):self.resume()

    def test_source_config_snapshot_changed_rejected(self):
        (self.source['configs']['BB_CONFIG'].path).write_text('changed source')
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):self.resume()

    def test_source_and_private_save_changes_rejected(self):
        for root in (self.user,self.source['user_root']):
            path=root/'savedata/progress.bin';old=path.read_bytes();path.write_bytes(b'other progress')
            with self.assertRaisesRegex(ValueError,'savedata differs'):self.resume()
            path.write_bytes(old)

    def test_private_and_source_mod_patch_bytes_changed_rejected(self):
        for root in (self.data,self.source['data_root']):
            for name in ('mods','patches'):
                with self.subTest(root=root,name=name):
                    file=root/name/'resource.bin';old=file.read_bytes();file.write_bytes(b'changed resource')
                    with self.assertRaisesRegex(ValueError,'resource structure or bytes'):self.resume()
                    file.write_bytes(old)

    def test_added_resource_file_or_directory_rejected(self):
        for name in ('mods','patches'):
            for directory in (False,True):
                with self.subTest(name=name,directory=directory):
                    path=self.data/name/'extra'
                    path.mkdir() if directory else path.write_bytes(b'new resource')
                    with self.assertRaisesRegex(ValueError,'resource structure or bytes'):self.resume()
                    path.rmdir() if directory else path.unlink()

    def test_missing_source_resource_directory_requires_empty_private(self):
        for root in (self.data,self.source['data_root']):(root/'mods/resource.bin').unlink()
        (self.source['data_root']/'mods').rmdir()
        self.resume()
        (self.data/'mods/extra').write_bytes(b'not empty')
        with self.assertRaisesRegex(ValueError,'absent source mods'):self.resume()

    def test_unknown_env_or_injected_host_environment_rejected(self):
        for key,value in [('BB_GAME_DIR','/other/game'),('DYLD_INSERT_LIBRARIES','/bad.dylib'),('PATH','/bad'),('BB_MTL_RELAX_VERTEX','1')]:
            with self.subTest(key=key):
                old=copy.deepcopy(self.conditions);self.conditions['environment'][key]=value;self.flush()
                with self.assertRaisesRegex(ValueError,'environment no longer matches'):self.resume()
                self.conditions=old;self.flush()

    def test_allowlisted_controls_cannot_change_meaning(self):
        self.conditions['environment']['BB_PAD_FILE_ONLY']='0';self.flush()
        with self.assertRaisesRegex(ValueError,'clone fields'):self.resume()

    def test_mismatching_private_tag_or_source_root_rejected(self):
        self.conditions['private_user']=str(self.source['user_root']);self.flush()
        with self.assertRaisesRegex(ValueError,'root tags'):self.resume()

    def test_existing_marker_must_match_without_repair(self):
        marker=self.user/'.arm-runtime-session';marker.write_text('ARMQA1\nwrong\n')
        with self.assertRaisesRegex(ValueError,'marker mismatch'):self.resume()
        self.assertEqual(marker.read_text(),'ARMQA1\nwrong\n')

    def test_noncanonical_or_linked_private_root_rejected(self):
        alias=self.project/'state/benchmark-data-333';alias.symlink_to(self.data,target_is_directory=True)
        self.conditions['private_data']=str(alias);self.flush()
        with self.assertRaisesRegex(ValueError,'canonical'):self.resume()

    def test_marker_hardlink_rejected(self):
        marker=self.user/'.arm-runtime-session';os.link(marker,self.user/'other-marker')
        with self.assertRaisesRegex(ValueError,'hard-linked'):self.resume()

    def test_old_frames_and_pending_input_rejected(self):
        frames=self.data/'out/frames.csv';frames.write_bytes(b'old')
        with self.assertRaisesRegex(ValueError,'archived'):self.resume()
        frames.unlink();(self.data/'pad.txt').write_text('cross\n')
        with self.assertRaisesRegex(ValueError,'pad must be empty'):self.resume()

    def test_interrupted_atomic_pad_update_rejected(self):
        (self.data/'pad.txt.next').write_bytes(b'cross\n')
        with self.assertRaisesRegex(ValueError,'unfinished private pad'):self.resume()

    def test_completed_scene_or_nonprotection_stop_cannot_resume(self):
        for key,value in [('status','sampled'),('scene_ready',20),('stop','driver exception'),('source_save_unchanged',False),('functional_f3_requested',True)]:
            with self.subTest(key=key):
                old=copy.deepcopy(self.prior);self.prior[key]=value;self.flush()
                with self.assertRaises(ValueError):self.resume()
                self.prior=old;self.flush()

    def test_another_run_cannot_be_mixed_into_first_preparation(self):
        (self.root/'warm-A1').mkdir()
        with self.assertRaisesRegex(ValueError,'first preparation'):self.resume()

    def test_f3_or_other_comparison_cannot_resume(self):
        for kwargs in [dict(vertex_ab=False),dict(phase='f3')]:
            with self.assertRaisesRegex(ValueError,'same-binary performance'):self.resume(**kwargs)

    def test_changed_command_actual_changes_or_allowed_domain_rejected(self):
        for key,value in [('command',['/bin/bash','other']),('actual_changes',{}),('allowed_changes',[])]:
            old=copy.deepcopy(self.conditions);self.conditions[key]=value;self.flush()
            with self.assertRaises(ValueError):self.resume()
            self.conditions=old;self.flush()

    def test_summary_must_bind_identical_prior_result(self):
        p=self.root/'summary.json';summary=json.loads(p.read_text());summary['runs'][0]['seconds']=99;p.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError,'one incomplete'):self.resume()


if __name__=='__main__':unittest.main(verbosity=2)
