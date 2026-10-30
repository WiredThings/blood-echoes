"""Real-file, read-only resume controls; no admission/tool/game execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent/'tools')]
import run_upstream_death_case as case
import run_qa_launch_benchmark as driver
import qa_launch_clone as clone
from qa_launch_conditions import CONFIG_NAMES,ConfigSnapshot
from qa_memory_policy import MemoryPolicy


class DeathResume(unittest.TestCase):
    def setUp(self):
        actual_stage=case.APP/'Contents/Resources/bbport'
        source_bytes={name:(actual_stage/name).read_bytes() for name in case.EXPECTED}
        self.tmp=tempfile.TemporaryDirectory(prefix='be-death-resume-');self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name).resolve()
        for name in ('state','evidence','apps'):(self.project/name).mkdir()
        for module,key in ((case,'PROJECT'),(driver,'P'),(clone,'PRIVATE_PROJECT')):
            p=patch.object(module,key,self.project);p.start();self.addCleanup(p.stop)
        p=patch('subprocess.Popen',side_effect=AssertionError('no process allowed'));p.start();self.addCleanup(p.stop)
        tag=case.RESUME_TAG;state=self.project/'state'
        self.data=state/('benchmark-data-'+tag);self.user=state/('benchmark-user-'+tag)
        self.cache=state/('gui-gpu-local-aot-'+tag);source_data=state/'gui-root-123';source_user=state/'gui-user-123'
        for path in (self.data,self.user,self.cache,source_data,source_user):path.mkdir(mode=0o700)
        (self.data/'out').mkdir();(self.data/'pad.txt').write_bytes(b'')
        for root in (self.data,source_data):
            for name in ('mods','patches'):
                (root/name).mkdir();(root/name/'resource.bin').write_bytes(b'same resource')
        for root in (self.user,source_user):
            (root/'savedata').mkdir();(root/'savedata/progress.bin').write_bytes(b'isolated same progress')
        self.receipt=source_data/'launch-conditions.json';self.receipt.write_text('{"original":true}')
        configs={}
        for key,name in CONFIG_NAMES.items():
            raw=('config '+key).encode();(source_data/name).write_bytes(raw);(self.data/name).write_bytes(raw)
            configs[key]=ConfigSnapshot(source_data/name,raw,hashlib.sha256(raw).hexdigest())
        stage=self.project/'evidence/reviewed.app/Contents/Resources/bbport';stage.mkdir(parents=True)
        for name,raw in source_bytes.items():
            p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        env=dict(BB_AUDIO='none',BB_ONLINE='0',BB_QUICKSAVE='1',BB_FRAME_STATS='0',BB_RENDERER='metal',BB_ARM_LAUNCHER_COMPAT='1',
            BB_USER_DIR=str(source_user),BB_DATA_DIR=str(source_data),BB_GPU_USER_DIR=str(state/'gui-gpu-local-aot-123'),
            BB_CONFIG=str(source_data/'bbport.ini'),BB_MODS_CONFIG=str(source_data/'mods.json'),BB_PATCHES_CONFIG=str(source_data/'patches.json'),
            BB_MODS_DIR=str(source_data/'mods'),BB_PATCHES_DIR=str(source_data/'patches'),BB_ARM_SESSION_NONCE='a'*32,BB_ARM_RESTART_NONCE='b'*32)
        self.source=dict(environment=env,receipt=self.receipt,configs=configs,user_root=source_user,data_root=source_data,
            game_root=self.project/'assets/game',arguments=('tools/macos/run_optimized.sh',),executable='/bin/bash')
        hashes={key:hashlib.sha256(key.encode()).hexdigest() for key in driver.FORMAL_ROLES}
        self.formal=dict(stage=stage,app=stage.parents[2],runtime_sha256=hashes,
            runtime={key:stage/key for key in hashes},identity=dict(runtime_sha256=copy.deepcopy(hashes)))
        (stage/'SOURCE-PARITY.json').write_text('{"synthetic-unit-fixture":true}')
        self.package_path=self.formal['app'].parent/'package-reviewed-result.json'
        self.package_path.write_text(json.dumps(dict(status='reviewed-normal-package-completed',app=str(self.formal['app']),
            packaging_revision='214c843a6f5119c9219529f5f50ea25632cacba9',native_product_revision='5c0f2b72bb2f1153513ce018f2a26814d8c61e7e',
            native_products_unchanged=True,strict_deep_signature_verified=True,source_parity_entries_verified=16,
            actual_frozen_roles={key:dict(sha256=value,architecture='arm64') for key,value in hashes.items()},
            source_parity_sha256=driver.sha(stage/'SOURCE-PARITY.json'))))
        private=dict(BB_DATA_DIR=str(self.data),BB_CONFIG=str(self.data/'bbport.ini'),BB_USER_DIR=str(self.user),
            BB_MODS_DIR=str(self.data/'mods'),BB_MODS_CONFIG=str(self.data/'mods.json'),BB_PATCHES_DIR=str(self.data/'patches'),
            BB_PATCHES_CONFIG=str(self.data/'patches.json'),BB_GPU_USER_DIR=str(self.cache),BB_ARM_SESSION_NONCE='c'*32,
            BB_ARM_RESTART_NONCE='d'*32,BB_PAD_FILE=str(self.data/'pad.txt'),BB_ARM_QA_PAD_FILE=str(self.data/'pad.txt'),
            BB_PAD_FILE_ONLY='1',BB_FRAME_TIMING='1',BB_FRAME_LOG_ONLY='1',BB_BENCHMARK_MUTE='1')
        original_case=dict(environment=dict(env,**private),data_root=self.data,user_root=self.user,cache_root=self.cache,
            pad=self.data/'pad.txt',allowed_changes=sorted(clone.ALLOWED_CHANGES))
        self.env=driver.execution_environment(self.source,original_case,self.formal,functional_f3=False)
        changes,allowed=driver.benchmark_changes(self.source,original_case,self.env,formal=self.formal,functional_f3=False)
        marker=f'ARMQA1\n{self.data}/out/boot-linked.bin\n{self.source["game_root"]}\n{self.data}/out/content.bin\n'+'c'*32+'\n'
        (self.user/'.arm-runtime-session').write_text(marker);(self.user/'.private-world-warper').write_text('c'*32)
        self.directory=self.user/'world-warper';self.directory.mkdir(mode=0o700)
        self.root=self.project/'evidence'/('upstream-private-death-run-'+tag)
        routes=case.projected_routes(source_bytes,stage,self.root)
        self.root.mkdir(mode=0o700)
        for name,raw in routes.items():(self.root/name).write_bytes(raw)
        (self.root/'console.log').write_text('ARM PRIVATE DEATH checked SetHp guest thunk installed\n')
        (self.root/'frames.csv').write_bytes(b'')
        self.cpu_root=self.project/'evidence/upstream-private-death-cache-20261030'
        self.cpu=self.cpu_root/'code/bb-game-arm64';self.cpu_result=dict(executable_sha256='e'*64)
        self.cpu_plan=dict(private_cpu_cache_build=dict(bridge_build='f'*64))
        controls=dict(BB_DEV_WORLD_WARPER_DIR=str(self.directory),BB_ARM_QA_PAD_FILE=str(self.data/'pad.txt'),
            BB_ARM_CPU_CACHE=str(self.root/'cpu-cache'/('f'*64)/'startup.bin'))
        required={key:self.env[key] for key in ('BB_ONLINE','BB_AUDIO','BB_QUICKSAVE','BB_PAD_FILE_ONLY','BB_ARM_SESSION_NONCE')}
        required.update(controls,BB_RESTART_SUPERVISED='1',BB_RENDERER='metal',BB_ARM_LAUNCHER_COMPAT='1',
            BB_GPU_PROCESS=self.env['BB_GPU_PROCESS'],BB_USER_DIR=str(self.user))
        self.manifest=dict(schema=1,stage=str(stage),cpu_root=str(self.cpu_root),private_cpu=str(self.cpu),private_cpu_sha256='e'*64,
            original_cpu=str(stage/case.CPU_ROLE),original_cpu_sha256=hashes[case.CPU_ROLE],required_environment=required,
            user=str(self.user),boot=str(self.data/'out/boot-linked.bin'),content=str(self.data/'out/content.bin'),patches=str(self.data/'out/patches.bin'))
        argv=[self.manifest['original_cpu'],self.manifest['boot'],'--content-profile',self.manifest['content'],
            '--patches',self.manifest['patches'],'--app0',str(self.data/'already-cleaned-view'),'--user',str(self.user),'--timeout','0']
        self.transfer=dict(schema=1,original_cpu=argv[0],private_cpu=str(self.cpu),original_argv=argv,
            actual_argv=[str(self.cpu),*argv[1:]],argv_tail_unchanged=True,original_preparation_ran=True,
            normal_role_impersonated=False,performance_sample=False,private_bridge_build='f'*64,final_mod_app0=str(self.data/'already-cleaned-view'))
        seal=dict(status='verified',command=['/usr/bin/codesign','--verify','--deep','--strict',str(self.formal['app'])],exit=0)
        self.old=dict(status='incomplete',raw_run_status='incomplete',stop='driver exception',error='missing bounded frame header',
            scene_ready=None,source_save_unchanged=True,memory_policy=MemoryPolicy.MANIFEST,performance_sample=False,
            silent=True,Cua=False,formal_data_modified=False,normal_package_modified=False,
            source_receipt_sha256=driver.sha(self.receipt),whole_app_seal=dict(pre=seal,post=copy.deepcopy(seal),post_after_owned_process_cleanup=True),
            projected_source_sha256=case.EXPECTED,projected_routes_sha256={k:hashlib.sha256(v).hexdigest() for k,v in routes.items()},
            private_cpu=self.manifest,ordinary_changes=changes,ordinary_allowed=allowed,private_control_after=controls,
            private_control_before={key:self.env.get(key) for key in controls})
        self.flush()

    def flush(self):
        for name,value in (('result.json',self.old),('handoff.json',self.manifest),('handoff-receipt.json',self.transfer)):
            (self.root/name).write_text(json.dumps(value))

    def resume(self):
        return case.resume_case(self.root,self.source,self.formal,driver,cpu=self.cpu,cpu_plan=self.cpu_plan,cpu_result=self.cpu_result)

    def test_exact_reconstruction_is_completely_read_only(self):
        before={str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        value,record=self.resume()
        self.assertEqual(value['data_root'],self.data)
        self.assertFalse(record['previous_run_counted_as_pass']);self.assertFalse(record['old_evidence_modified'])
        self.assertEqual(self.env,driver.execution_environment(self.source,value,self.formal,functional_f3=False))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_other_failure_scene_or_policy_is_rejected(self):
        for key,value in (('error','different'),('stop','unexpected >2GiB swap growth'),('scene_ready',0.5),('status','functional-checked'),('source_save_unchanged',False)):
            old=self.old[key];self.old[key]=value;self.flush()
            with self.subTest(key=key),self.assertRaises(ValueError):self.resume()
            self.old[key]=old;self.flush()

    def test_changed_source_receipt_or_either_cpu_identity_rejected(self):
        self.receipt.write_text('changed')
        with self.assertRaises(ValueError):self.resume()
        self.receipt.write_text('{"original":true}')
        for field in ('private_cpu_sha256','original_cpu_sha256'):
            old=self.manifest[field];self.manifest[field]='0'*64;self.flush()
            with self.subTest(field=field),self.assertRaises(ValueError):self.resume()
            self.manifest[field]=old;self.flush()

    def test_each_config_source_and_private_change_is_rejected(self):
        for directory in (self.data,self.source['data_root']):
            for name in CONFIG_NAMES.values():
                p=directory/name;raw=p.read_bytes();p.write_bytes(b'changed')
                with self.subTest(directory=directory,name=name),self.assertRaises(ValueError):self.resume()
                p.write_bytes(raw)

    def test_save_resource_bytes_and_structure_must_match(self):
        for directory in (self.user,self.source['user_root']):
            p=directory/'savedata/progress.bin';raw=p.read_bytes();p.write_bytes(b'other progress')
            with self.assertRaises(ValueError):self.resume()
            p.write_bytes(raw)
        for directory in (self.data,self.source['data_root']):
            for name in ('mods','patches'):
                p=directory/name/'resource.bin';raw=p.read_bytes();p.write_bytes(b'other resource')
                with self.assertRaises(ValueError):self.resume()
                p.write_bytes(raw)
                extra=directory/name/'new-directory';extra.mkdir()
                with self.assertRaises(ValueError):self.resume()
                extra.rmdir()

    def test_commands_nonempty_pad_frames_markers_or_nonce_are_rejected(self):
        for p,raw in ((self.directory/'armed',b'c'*32),(self.directory/'death-command.txt',b'1'),
                      (self.data/'out/frames.csv',b'old'),(self.data/'pad.txt.next',b'cross')):
            p.write_bytes(raw)
            with self.subTest(path=p),self.assertRaises(ValueError):self.resume()
            p.unlink()
        for p in (self.user/'.private-world-warper',self.user/'.arm-runtime-session',self.data/'pad.txt'):
            raw=p.read_bytes();p.write_bytes(b'changed')
            with self.assertRaises(ValueError):self.resume()
            p.write_bytes(raw)
        for key in ('BB_ARM_RESTART_NONCE','BB_ARM_SESSION_NONCE'):
            old=self.old['ordinary_changes'][key]['after'];self.old['ordinary_changes'][key]['after']='x'*32;self.flush()
            with self.assertRaises(ValueError):self.resume()
            self.old['ordinary_changes'][key]['after']=old;self.flush()

    def test_unknown_after_field_or_ordinary_before_change_rejected(self):
        for mode in ('extra','before','after','allowed'):
            old=copy.deepcopy(self.old)
            if mode=='extra':self.old['ordinary_changes']['BB_TEST_EXTRA']=dict(before=None,after='1')
            elif mode=='allowed':self.old['ordinary_allowed'].append('BB_TEST_EXTRA')
            else:self.old['ordinary_changes']['BB_QUICKSAVE'][mode]='wrong'
            self.flush()
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.resume()
            self.old=old;self.flush()

    def test_wrong_bridge_argv_seal_tag_or_root_mode_rejected(self):
        self.transfer['private_bridge_build']='0'*64;self.flush()
        with self.assertRaises(ValueError):self.resume()
        self.transfer['private_bridge_build']='f'*64;self.flush()
        self.transfer['actual_argv'][1]='/different';self.flush()
        with self.assertRaises(ValueError):self.resume()
        self.transfer['actual_argv'][1]=self.manifest['boot'];self.flush()
        self.old['whole_app_seal']['post']['status']='failed';self.flush()
        with self.assertRaises(ValueError):self.resume()
        self.old['whole_app_seal']['post']['status']='verified';self.flush()
        self.user.chmod(0o755)
        with self.assertRaises(ValueError):self.resume()

    def test_existing_world_directory_is_only_for_explicit_resume(self):
        (self.directory/'unrelated').write_bytes(b'preserve me')
        with self.assertRaises(ValueError):self.resume()
        self.assertEqual((self.directory/'unrelated').read_bytes(),b'preserve me')

    def test_historical_package_binds_each_actual_current_role(self):
        original=copy.deepcopy(self.formal)
        for key in driver.FORMAL_ROLES:
            self.formal=copy.deepcopy(original)
            self.formal['runtime_sha256'][key]='0'*64;self.formal['identity']['runtime_sha256'][key]='0'*64
            with self.subTest(role=key),self.assertRaisesRegex(ValueError,'package/eight-role'):
                self.resume()

    def test_original_package_record_and_source_parity_cannot_change(self):
        raw=self.package_path.read_bytes();self.package_path.write_bytes(raw+b' ')
        with self.assertRaises(ValueError):self.resume()
        self.package_path.write_bytes(raw)
        # Restore only this synthetic fixture's pre-run timestamp, never real evidence.
        ns=(self.root/'handoff.json').stat().st_mtime_ns-1000
        os.utime(self.package_path,ns=(ns,ns))
        p=self.formal['stage']/'SOURCE-PARITY.json';p.write_text('changed')
        with self.assertRaises(ValueError):self.resume()


if __name__=='__main__':unittest.main()
