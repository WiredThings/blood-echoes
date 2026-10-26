"""Real author bundle byte closure + injected native identities; zero processes."""
from pathlib import Path
from types import MappingProxyType
import copy,hashlib,json,os,plistlib,sys,tempfile,unittest,subprocess
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import run_qa_launch_benchmark as driver
import launcher_runtime_profile as profile
from qa_launch_conditions import SCRIPTS

class FormalStage(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.project=Path(self.temp.name).resolve();(self.project/'evidence').mkdir();(self.project/'state').mkdir()
  self.patch=patch.object(driver,'P',self.project);self.patch.start();self.addCleanup(self.patch.stop)
  self.no_native=patch('subprocess.Popen',side_effect=AssertionError('author control must not launch a process'));self.no_native.start();self.addCleanup(self.no_native.stop)
  self.app=self.project/'evidence/local-stage-123/Blood Echoes.app';self.stage=self.app/'Contents/Resources/bbport';self.stage.mkdir(parents=True)
  self.info=dict(CFBundleIdentifier=profile.FORMAL_ID);self.put(self.app/'Contents/Info.plist',plistlib.dumps(self.info))
  for role in driver.FORMAL_ROLES:self.put(self.stage/role,('author role '+role).encode())
  self.hashes={r:driver.sha(self.stage/r)for r in driver.FORMAL_ROLES}
  self.native=dict(schema=1,production=True,game_runtime='fex-arm64',frozen_runtime=self.hashes)
  tools={};support={};scripts={}
  for n in('launcher_local_aot.py','prepare_module_metadata.py'):
   p=self.stage/'native-runtime-tools'/n;self.put(p,b'# authored tool\n');tools[n]=driver.sha(p)
  for n in('vendor/blood_echoes/loader/prepare.py','vendor/blood_echoes/loader/game_check.py'):
   p=self.stage/n;self.put(p,b'# authored original parser\n');support[n]=driver.sha(p)
  for n in SCRIPTS:
   p=self.stage/n;self.put(p,('# author original script '+n+'\n').encode());scripts[n]=driver.sha(p)
  startup=self.stage/'tools/macos/local_metal_startup.py';self.put(startup,b'# author bootstrap\n')
  self.local=dict(schema=1,production=True,runtime_tools=tools,runtime_support=support,startup_bootstrap_sha256=driver.sha(startup))
  self.source=dict(stage=self.project/'apps/sourceQA.app/Contents/Resources/bbport',effective_scripts=MappingProxyType(scripts),
   executable='/bin/bash',arguments=('tools/macos/run_optimized.sh',),user_root=self.project/'state/gui-user-123',
   environment=MappingProxyType(dict(BB_GPU_PROCESS='/source/QA/gpu',BB_AUDIO='none',BB_QUICKSAVE='1',
    BB_LANGUAGE='1',BB_CHINESE_LANGUAGE='11',BB_UPSCALER='off',BB_WIDTH='1920',BB_HEIGHT='1080',
    BB_ARM_PAYLOAD_GATE='1',BB_ARM_RAW_PREWRITE='1',BB_LAYER_READ_TRAPS='1',BB_GPU_LIFETIME='1')))
  self.seal();self.calls=[]
 def put(self,p,b):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 def seal(self):
  for name,obj in [('NATIVE-METAL.json',self.native),('ARM64-LOCAL-PREPARATION.json',self.local),('SOURCE-PARITY.json',dict(effective_sources=dict(self.source['effective_scripts'])))]:
   self.put(self.stage/name,json.dumps(obj).encode())
 def actual(self,target,stage,**kw):
  self.calls.append((target,stage,kw));lib=self.stage/'bin/gpu-metal/libbbgpu.dylib'
  return dict(target=str(target),target_sha256=driver.sha(target),library=str(lib),library_sha256=driver.sha(lib),load_edge_verified=True,strict_signatures=True,architecture='arm64')
 def gpu(self,meta,stage,**kw):
  self.calls.append((meta,stage,kw));return dict(meta,target_sha256=driver.sha(meta['target']),load_edge_verified=True,strict_signatures=True,architecture='arm64')
 def seal_runner(self,command,**kw):
  self.assertEqual(command,['/usr/bin/codesign','--verify','--deep','--strict',str(self.app)])
  return subprocess.CompletedProcess(command,0,'','')
 def admit(self,**kw):return driver.admit_formal(self.app,self.source,project=self.project,identity_resolver=kw.pop('identity_resolver',self.actual),gpu_verifier=kw.pop('gpu_verifier',self.gpu),seal_runner=kw.pop('seal_runner',self.seal_runner),**kw)
 def test_real_fullclosure_and_both_exact_cpu_roles(self):
  r=self.admit();self.assertEqual(set(r['runtime']),driver.FORMAL_ROLES);self.assertEqual(len(self.calls),3)
  self.assertEqual(set(r['identity']['cpu']),set(driver.CPU_ROLES));self.assertTrue(all(x[2]['code_owners']==(0,os.getuid())for x in self.calls))
  json.dumps(r['identity']);self.assertFalse(r['identity']['formal_GUI_started']);self.assertFalse(r['identity']['formal_data_read'])
 def test_wrong_outer_id_rejected_before_native(self):
  self.put(self.app/'Contents/Info.plist',plistlib.dumps(dict(CFBundleIdentifier='io.github.bbport.optimized.options-qa.arm64.gui.123')))
  with self.assertRaisesRegex(ValueError,'outer formal'):self.admit()
  self.assertFalse(self.calls)
 def test_production_false_rejected(self):
  self.native['production']=False;self.seal()
  with self.assertRaisesRegex(ValueError,'formal runtime'):self.admit()
 def test_changed_runtime_or_tool_bytes_rejected(self):
  for p in(self.stage/'bin/bb-probe-metal',self.stage/'native-runtime-tools/prepare_module_metadata.py'):
   before=p.read_bytes();p.write_bytes(b'changed author bytes')
   with self.assertRaisesRegex(ValueError,'identity changed'):self.admit()
   p.write_bytes(before)
  self.assertFalse(self.calls)
 def test_missing_nested_role_cannot_be_fabricated(self):
  del self.hashes[driver.CPU_ROLES[1]];self.seal()
  with self.assertRaisesRegex(ValueError,'eight'):self.admit()
 def test_source_qa_scripts_must_equal_formal_sixteen_scripts(self):
  self.source['effective_scripts']=dict(self.source['effective_scripts'],**{'run.sh':'f'*64})
  # Keep source proof different without changing this real formal byte closure.
  with self.assertRaisesRegex(ValueError,'sixteen scripts'):self.admit()
 def test_wrong_host_edge_or_nested_target_refused(self):
  def wrong(target,stage,**kw):return dict(self.actual(target,stage,**kw),library='/wrong/host')
  with self.assertRaisesRegex(ValueError,'CPU/host'):self.admit(identity_resolver=wrong)
  def alias(target,stage,**kw):return dict(self.actual(target,stage,**kw),target=str(self.stage/driver.CPU_ROLES[0]))
  with self.assertRaisesRegex(ValueError,'CPU/host'):self.admit(identity_resolver=alias)
 def test_unsigned_or_wrong_gpu_refused(self):
  for k,v in[('strict_signatures',False),('architecture','x86_64'),('library_sha256','f'*64)]:
   def wrong(meta,stage,**kw):return dict(self.gpu(meta,stage,**kw),**{k:v})
   with self.assertRaisesRegex(ValueError,'GPU/DSO'):self.admit(gpu_verifier=wrong)
 def test_symlink_app_and_outside_evidence_rejected(self):
  alias=self.project/'evidence/alias.app';alias.symlink_to(self.app)
  with self.assertRaises(ValueError):driver.admit_formal(alias,self.source,project=self.project)
  with self.assertRaises(ValueError):driver.admit_formal(self.project,self.source,project=self.project)
 def test_empty_is_forced_and_seeded_rejected(self):
  self.assertEqual(driver.selected_cache_mode(None,self.app),'empty');self.assertEqual(driver.selected_cache_mode('empty',self.app),'empty')
  with self.assertRaisesRegex(ValueError,'empty cache'):driver.selected_cache_mode('seeded',self.app)
  self.assertEqual(driver.selected_cache_mode(None,None),'seeded')
 def test_execution_view_does_not_change_clone_source(self):
  formal=self.admit();view=driver.execution_conditions(self.source,formal)
  self.assertEqual(view['stage'],self.stage);self.assertEqual(view['user_root'],self.source['user_root'])
  self.assertNotEqual(self.source['stage'],view['stage']);self.assertIs(view['environment'],self.source['environment'])
  self.assertEqual(driver.cpu_paths(view),{str(self.stage/r)for r in driver.CPU_ROLES})
  self.assertIs(driver.execution_conditions(self.source,None),self.source)
 def case(self):
  data=self.project/'state/benchmark-data-1';pad=data/'pad.txt'
  return dict(data_root=data,pad=pad,environment=dict(self.source['environment']),allowed_changes=['BB_PAD_FILE','BB_PAD_FILE_ONLY','BB_QUICKSAVE'])
 def test_environment_only_repoints_gpu_and_wrapper_code(self):
  formal=self.admit();case=self.case();original=dict(case['environment']);env=driver.execution_environment(self.source,case,formal)
  for key,value in original.items():
   if key not in('BB_GPU_PROCESS','BB_QUICKSAVE'):self.assertEqual(env[key],value)
  self.assertEqual(env['BB_GPU_PROCESS'],str(formal['runtime']['bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu']))
  self.assertNotIn('BB_ARM_GPU_BASE_DIR',env);self.assertEqual(env['PYTHON'],str(self.stage/'python/bin/python3'))
  changes,allowed=driver.benchmark_changes(self.source,case,env,formal=formal);self.assertIn('BB_GPU_PROCESS',changes)
  self.assertEqual(case['environment'],original)
 def test_quality_change_not_allowed_even_for_formal(self):
  formal=self.admit();case=self.case();env=driver.execution_environment(self.source,case,formal);env['BB_WIDTH']='1280'
  with self.assertRaisesRegex(ValueError,'unapproved'):driver.benchmark_changes(self.source,case,env,formal=formal)
 def test_quicksave_functional_mode_keeps_original_requirement(self):
  formal=self.admit();case=self.case();env=driver.execution_environment(self.source,case,formal,functional_f3=True)
  self.assertEqual(env['BB_QUICKSAVE'],'1');self.assertEqual(env['BB_FRAME_STATS'],'1')
  driver.benchmark_changes(self.source,case,env,formal=formal,functional_f3=True)
  self.source['environment']=dict(self.source['environment'],BB_QUICKSAVE='0')
  with self.assertRaisesRegex(ValueError,'quicksave'):driver.execution_environment(self.source,case,formal,functional_f3=True)

 def test_whole_app_pre_seal_failure_prevents_native_admission(self):
  def bad(command,**kw):return subprocess.CompletedProcess(command,1,'','unsealed contents present')
  with self.assertRaisesRegex(ValueError,'seal failed before run'):self.admit(seal_runner=bad)
  self.assertFalse(self.calls)
 def test_post_seal_failure_preserves_functional_result_but_is_incomplete(self):
  formal=self.admit();raw=dict(status='functional-checked',functional_f3={'phase':'completed'},stop='functional F3 completed')
  def bad(command,**kw):return subprocess.CompletedProcess(command,1,'','modified stdlib pycache')
  result=driver.finish_app_seal(raw,formal,runner=bad)
  self.assertEqual(result['status'],'incomplete');self.assertEqual(result['raw_run_status'],'functional-checked')
  self.assertEqual(result['functional_f3'],raw['functional_f3']);self.assertEqual(raw['status'],'functional-checked')
  self.assertEqual(result['whole_app_seal']['post']['status'],'failed');self.assertIn('seal',result['seal_error'])
 def test_pre_and_post_seals_both_pass_without_erasing_sample(self):
  formal=self.admit();result=driver.finish_app_seal(dict(status='sampled',scene_sample={'fps':50}),formal,runner=self.seal_runner)
  self.assertEqual(result['status'],'sampled');self.assertEqual(result['raw_run_status'],'sampled')
  self.assertEqual(result['scene_sample'],{'fps':50});self.assertTrue(result['whole_app_seal']['post_after_owned_process_cleanup'])
 def test_seal_timeout_is_failure_and_not_a_functional_success(self):
  def timeout(command,**kw):raise subprocess.TimeoutExpired(command,30)
  self.assertEqual(driver.app_seal(self.app,runner=timeout)['status'],'failed')

if __name__=='__main__':unittest.main()
