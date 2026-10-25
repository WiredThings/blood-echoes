"""Original launcher exec/identity contracts; authored files, no game resources."""
from pathlib import Path
import hashlib, importlib.util, json, os, signal, subprocess, sys, tempfile, time, unittest

TOOLS = Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0, str(TOOLS))
from launcher_local_aot import prepare_launch


class Startup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()/'prototype'
        self.root.mkdir()
        self.gpu = self.root/'state/gpu'; self.gpu.mkdir(parents=True)
        self.inputs = self.root/'state/inputs'; self.inputs.mkdir()
        self.backend = self.root/'backend'; self.backend.mkdir()
        (self.backend/'gpu').write_text('authored GPU code path; verifier mocked')
        for name in ['boot', 'content', 'patches', 'settings', 'runtime']:
            (self.inputs/name).write_text(name)
        self.args = [str(self.inputs/'boot'), '--content-profile', str(self.inputs/'content'),
                     '--patches', str(self.inputs/'patches'), '--app0', '/original/merged view',
                     '--user', '/original/normal save', '--timeout', '0', '--strict-imports']
        self.env = {'BB_CONFIG':str(self.inputs/'settings'), 'BB_RENDERER':'metal',
                    'BB_ARM_LAUNCHER_COMPAT':'1', 'BB_GPU_PROCESS':str(self.backend/'gpu'),
                    'BB_GPU_USER_DIR':str(self.gpu), 'BB_THD_TOKEN':'authored-secret',
                    'BB_ARM_RESTART_NONCE':'authored-nonce', 'BB_LANGUAGE':'11',
                    'BB_MTL_DIRECT_COPY':'1', 'BB_MTL_COMPILE_AUDIT':'1',
                    'BB_MTL_LOCAL_AOT_BUILD_ID':'old', 'BB_MTL_LOCAL_AOT_COHORT':'old'}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def identity(self, **kw):
        self.calls.append(('identity', kw))
        token = hashlib.sha256(b''.join(Path(v).read_bytes() for v in kw['game_artifacts'].values())).hexdigest()
        return {'BB_MTL_ARCHIVE_COMPATIBILITY_ID':token}, {'token':token}

    def policy(self, **kw):
        self.calls.append(('policy', kw))
        return {'BB_MTL_LOCAL_AOT_MODE':'record'}, {'status':'record', 'builder_invoked':False}

    def run_prepare(self, *, policy=None, observe=None, env=None):
        return prepare_launch(str(self.inputs/'runtime'), self.args, env or self.env,
            project=self.root, archive_prepare=self.identity, local_prepare=policy or self.policy,
            observe=observe or (lambda:dict(locked=False, pressure=1, swap_mib=100)))

    def test_default_prepares_auto_and_preserves_original_contract(self):
        before = dict(self.env); original = list(self.args)
        env, report = self.run_prepare()
        self.assertEqual(self.calls[1][1]['mode'], 'auto')
        self.assertEqual(env['BB_THD_TOKEN'], self.env['BB_THD_TOKEN'])
        self.assertEqual(env['BB_ARM_RESTART_NONCE'], self.env['BB_ARM_RESTART_NONCE'])
        self.assertEqual(env['BB_LANGUAGE'], '11')
        self.assertNotIn('BB_MTL_LOCAL_AOT_BUILD_ID', env)
        self.assertNotIn('BB_MTL_LOCAL_AOT_COHORT', env)
        self.assertEqual(self.env, before); self.assertEqual(self.args, original)
        opts = self.calls[0][1]['options']
        self.assertEqual(opts, {'BB_MTL_DIRECT_COPY':'1'})
        self.assertNotIn('authored-secret', json.dumps(report))

    def test_explicit_off_does_not_inspect_files_or_services(self):
        env = {'BB_MTL_LOCAL_AOT_MODE':'off'}
        def fail(**kw): raise AssertionError('off performed work')
        result, report = prepare_launch('/nonexistent', [], env, project=self.root,
            archive_prepare=fail, local_prepare=fail, observe=fail)
        self.assertEqual(report['status'], 'off'); self.assertEqual(result, env)

    def test_final_patch_changes_compatibility_cohort(self):
        _, a = self.run_prepare()
        (self.inputs/'patches').write_text('different actual patch payload')
        _, b = self.run_prepare()
        self.assertNotEqual(a['cohort'], b['cohort'])

    def test_does_not_select_or_create_save_directories(self):
        self.run_prepare()
        self.assertFalse((self.root/'savedata').exists())
        self.assertFalse(any('save' in k for k in self.calls[0][1]['game_artifacts']))

    def test_duplicate_original_options_rejected(self):
        self.args += ['--patches', str(self.inputs/'patches')]
        with self.assertRaises(ValueError): self.run_prepare()
        self.assertEqual(self.calls, [])

    def test_linked_boot_refused(self):
        link=self.inputs/'linked';link.symlink_to(self.inputs/'boot');self.args[0]=str(link)
        with self.assertRaises(ValueError): self.run_prepare()

    def test_formal_gpu_path_refused(self):
        self.env['BB_GPU_USER_DIR']=str(self.root)
        with self.assertRaises(ValueError): self.run_prepare()

    def test_warning_pressure_continues(self):
        _, report=self.run_prepare(observe=lambda:dict(locked=False,pressure=2,swap_mib=100))
        self.assertEqual(report['status'],'record')

    def test_lock_and_severe_pressure_do_not_start_builder(self):
        for sample in [dict(locked=True,pressure=1,swap_mib=100),dict(locked=False,pressure=4,swap_mib=100)]:
            self.calls.clear()
            with self.assertRaises(RuntimeError):self.run_prepare(observe=lambda:sample)
            self.assertEqual(self.calls,[])

    def test_total_swap_budget_survives_preparation_boundary(self):
        samples=iter([dict(locked=False,pressure=1,swap_mib=100),dict(locked=False,pressure=2,swap_mib=2149)])
        with self.assertRaises(RuntimeError):self.run_prepare(observe=lambda:next(samples))

    def test_toolchain_absence_is_visible_source_fallback(self):
        def missing(**kw):return {'BB_MTL_LOCAL_AOT_MODE':'record'}, {'status':'source-fallback','reason':'toolchain unavailable'}
        env,report=self.run_prepare(policy=missing)
        self.assertEqual(report['status'],'source-fallback')
        self.assertEqual(env['BB_MTL_LOCAL_AOT_MODE'],'record')

    def test_safety_stop_is_not_swallowed_as_source_fallback(self):
        def stopped(**kw):return {},{'status':'stopped'}
        with self.assertRaises(RuntimeError):self.run_prepare(policy=stopped)

    def test_exec_retains_arguments_signals_and_original_restart_statuses(self):
        # OFF isolates exec semantics from compiler/toolchain tests. The wrapper
        # must never translate 75/76 into its own meaning or leave a child shell.
        helper=TOOLS/'launcher_local_aot.py'
        for status in [0, 74, 75, 76, 134]:
            body='import sys,json; print(json.dumps(sys.argv[1:])); sys.exit('+str(status)+')'
            args=['-c',body,'space value','--user','unmodified path']
            env=dict(os.environ,BB_MTL_LOCAL_AOT_MODE='off')
            result=subprocess.run([sys.executable,str(helper),sys.executable,*args],
                                  env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,status,result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]),args[2:])

    def test_stop_during_preparation_reaps_owned_unresponsive_compiler(self):
        for number in [signal.SIGTERM,signal.SIGINT]:
            with self.subTest(number=number):
                pid_file=self.root/('compiler-'+str(number)+'.pid')
                child='import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open('+repr(str(pid_file))+',"w").write(str(os.getpid()));time.sleep(60)'
                bootstrap=('import sys;sys.path.insert(0,'+repr(str(TOOLS))+');'
                    'import launcher_local_aot as h,local_metal_builder as b;'
                    'from pathlib import Path;'
                    'Guard=type("Guard",(),{"check":lambda self,**kw:None});'
                    'h.prepare_launch=lambda *a,**kw:b.FixedRunner(Path('+repr(str(self.root))+')).run([sys.executable,"-c",'+repr(child)+'],60,Guard());'
                    'h.cli()')
                proc=subprocess.Popen([sys.executable,'-c',bootstrap,'ignored-native','authored-boot'],
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
                child_pid=None
                try:
                    deadline=time.monotonic()+5
                    while not pid_file.exists() and proc.poll()is None and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue(pid_file.exists(),'owned fake compiler did not start')
                    child_pid=int(pid_file.read_text())
                    started=time.monotonic();os.kill(proc.pid,number)
                    stdout,stderr=proc.communicate(timeout=2.8)
                    self.assertEqual(proc.returncode,-number,stderr.decode())
                    self.assertLess(time.monotonic()-started,2.8)
                    with self.assertRaises(ProcessLookupError):os.kill(child_pid,0)
                finally:
                    if proc.poll()is None:proc.kill();proc.wait()
                    if child_pid is not None:
                        try:os.killpg(child_pid,signal.SIGKILL)
                        except ProcessLookupError:pass


    def test_packaged_code_root_can_move_without_moving_data(self):
        stage=Path(self.temp.name).resolve()/'Extracted QA stage'; stage.mkdir()
        cpu=stage/'cpu';cpu.write_text('authored CPU')
        gpu=stage/'gpu';gpu.write_text('authored GPU')
        env=dict(self.env,BB_GPU_PROCESS=str(gpu))
        result,report=prepare_launch(str(cpu),self.args,env,project=self.root,execution_root=stage,
            archive_prepare=self.identity,local_prepare=self.policy,
            observe=lambda:dict(locked=False,pressure=1,swap_mib=100))
        self.assertEqual(self.calls[0][1]['backend_root'],stage)
        self.assertEqual(self.calls[1][1]['gpu_root'],str(self.gpu))
        self.assertEqual(result['BB_GPU_USER_DIR'],str(self.gpu))

    def test_packaged_code_root_never_expands_data_root(self):
        stage=Path(self.temp.name).resolve()/'Stage';stage.mkdir()
        cpu=stage/'cpu';cpu.write_text('cpu');gpu=stage/'gpu';gpu.write_text('gpu')
        data=stage/'state/gpu';data.mkdir(parents=True)
        env=dict(self.env,BB_GPU_PROCESS=str(gpu),BB_GPU_USER_DIR=str(data))
        with self.assertRaises(ValueError):
            prepare_launch(str(cpu),self.args,env,project=self.root,execution_root=stage,
                archive_prepare=self.identity,local_prepare=self.policy,
                observe=lambda:dict(locked=False,pressure=1,swap_mib=100))
        self.assertEqual(self.calls,[])

    def test_unrelated_code_outside_declared_roots_refused(self):
        outside=Path(self.temp.name).resolve()/'outside';outside.write_text('cpu')
        with self.assertRaises(ValueError):
            prepare_launch(str(outside),self.args,self.env,project=self.root,
                archive_prepare=self.identity,local_prepare=self.policy,
                observe=lambda:dict(locked=False,pressure=1,swap_mib=100))
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
