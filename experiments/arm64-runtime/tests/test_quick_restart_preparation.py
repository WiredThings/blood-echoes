"""Pure authored F3 scheduling contracts; no ps/native/GPU processes started."""
from pathlib import Path
import copy,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import quick_restart_preparation as quick
from launcher_game_catalog import output_receipt,write_json

class Restart(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
  self.stage=self.root/'stage';self.stage.mkdir()
  for name in('GAME-PSO-COMPONENT.json','NATIVE-METAL.json','ARM64-LOCAL-PREPARATION.json','SOURCE-PARITY.json'):(self.stage/name).write_text('{}')
  scripts={}
  for name in('run.sh','tools/macos/supervise_restart.sh'):
   path=self.stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# authored '+name+'\n');scripts[name]=quick.file_sha(path)[0]
  write_json(self.stage/'SOURCE-PARITY.json',dict(effective_sources=scripts))
  self.cpu=self.stage/'cpu';self.cpu.write_bytes(b'author cpu')
  self.game=self.root/'original';self.game.mkdir()
  self.gpu=self.root/'gpu';self.cache=self.gpu/'automatic-game-inputs';self.cache.mkdir(parents=True)
  self.view=self.cache/'source-views/source/view';self.view.mkdir(parents=True);self.view.chmod(0o500)
  write_json(self.view.parent/'READY.json',dict(schema=1,identity='b'*64,proof=dict(author=True)))
  self.generation=self.cache/'static';self.generation.mkdir()
  (self.generation/'catalog.json').write_text('{}');(self.generation/'static-program-index.jsonl').write_text('author metadata\n')
  self.static='e'*64;write_json(self.generation/'READY.json',dict(schema=1,identity=self.static,files=output_receipt(self.generation)))
  self.cohort='a'*64;(self.cache/('runtime-'+self.cohort)).mkdir(mode=0o700)
  self.env=dict(BB_RESTART_SUPERVISED='1',BB_ARM_LAUNCHER_COMPAT='1',BB_ONLINE='0',BB_ARM_SESSION_NONCE='1'*32,BB_ARM_RESTART_NONCE='2'*32,BB_GPU_USER_DIR=str(self.gpu),BB_GAME_DIR=str(self.game),BB_DATA_DIR=str(self.root))
  self.archive=dict(manifest=dict(game={n:dict(sha256='f'*64,bytes=12)for n in('boot','content_profile','patches','settings')}))
  self.args=['boot','--app0','/original-temporary-overlay','--user','unchanged-user']
  self.component=dict(automatic_capture_schema=1,source_root=self.stage)
  self.owner=dict(pid=77,parent=1,start='Tue Oct 27 01:02:03 2026',role='supervise_restart.sh',script='tools/macos/supervise_restart.sh')
  self.probe=lambda pid:dict(self.owner,pid=pid)
  self.cwd_probe=lambda pid:self.stage
  self.context=quick.pending(dict(generation=self.generation,identity=self.static,game_root=self.view))
  self.calls=[]
 def tearDown(self):self.view.chmod(0o700);self.temp.cleanup()
 def bound(self,**overrides):
  values=dict(executable=self.cpu,arguments=self.args,environment=self.env,cohort=self.cohort,stage=self.stage,archive_identity=self.archive,probe=self.probe,cwd_probe=self.cwd_probe);values.update(overrides)
  return quick.binding(**values)
 def publish(self,bound=None,**kwargs):
  return quick.publish(bound=bound or self.bound(),context=self.context,gpu=self.gpu,probe=self.probe,cwd_probe=self.cwd_probe,**kwargs)
 def capture(self,**kw):
  self.calls.append(kw);return dict(capture_environment={'BB_FETCH_CAPTURE_ROOT':str(kw['root']),'BB_FETCH_CAPTURE_GAME_ROOT':str(kw['game_root'])})
 def resume(self,**overrides):
  values=dict(bound=self.bound(),environment={**self.env,'BB_QUICKLOAD_PENDING':'1'},gpu=self.gpu,component=self.component,backend=self.stage/'library',cohort=self.cohort,check=lambda:None,configure_capture=self.capture,probe=self.probe,cwd_probe=self.cwd_probe);values.update(overrides)
  return quick.resume(**values)
 def test_completed_same_supervisor_f3_rechecks_metadata_and_capture(self):
  self.assertTrue(self.publish());result=self.resume()
  self.assertEqual(result['status'],'same-supervisor-f3-schedule-reused')
  self.assertIs(result['artifact_authority'],False);self.assertIs(result['static_registry_rescanned'],False)
  self.assertIn('not-captured',result['dynamic_capture_gap']);self.assertEqual(len(self.calls),1)
  self.assertEqual(self.calls[0]['catalog'],self.generation/'catalog.json');self.assertEqual(self.calls[0]['index'],self.generation/'static-program-index.jsonl')
 def local_env(self):
  root=self.gpu/'local-metal';(root/self.cohort/'requests').mkdir(parents=True,exist_ok=True)
  (root/self.cohort/'artifacts'/('d'*64)).mkdir(parents=True,exist_ok=True)
  return dict(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_ROOT=str(root),
   BB_MTL_LOCAL_AOT_COHORT=self.cohort,BB_MTL_LOCAL_AOT_BUILD_ID='d'*64,BB_MTL_LOCAL_AOT_ENVIRONMENT_ID='e'*64)
 def early(self,**overrides):
  values=dict(bound=self.bound(),environment={**self.env,'BB_QUICKLOAD_PENDING':'1'},gpu=self.gpu,probe=self.probe,cwd_probe=self.cwd_probe)
  values.update(overrides);return quick.prepared_local(**values)
 def test_front_local_receipt_has_only_preparation_fields_and_requires_continuous_history(self):
  import startup_change_journal as journal
  local=self.local_env()
  with patch.object(journal,'query',return_value=dict(schema=1,author='journal')):
   self.assertTrue(self.publish(local_environment=local))
  with patch.object(journal,'unchanged',return_value=True)as unchanged:
   self.assertEqual(self.early(),local);self.assertEqual(len(unchanged.call_args.args[1]),2)
   self.assertNotIn('BB_THD_TOKEN',self.early())
  with patch.object(journal,'unchanged',return_value=False):self.assertIsNone(self.early())
  self.assertTrue(self.publish()) # Old receipts remain valid for late game-only reuse.
  with patch.object(journal,'unchanged',return_value=True):self.assertIsNone(self.early())
  self.assertIsNotNone(self.resume())
 def test_front_local_rejects_wrong_roots_keys_secrets_and_mode(self):
  local=self.local_env()
  for field,value in [('BB_MTL_LOCAL_AOT_ROOT',str(self.root)),('BB_MTL_LOCAL_AOT_MODE','record'),
      ('BB_MTL_LOCAL_AOT_COHORT','b'*64),('BB_MTL_LOCAL_AOT_BUILD_ID','bad')]:
   self.assertIsNone(quick.local_selection({**local,field:value},self.gpu,self.cohort))
  self.assertNotIn('BB_THD_TOKEN',quick.local_selection({**local,'BB_THD_TOKEN':'never-in-receipt'},self.gpu,self.cohort)[0])
  self.assertIsNone(quick.local_selection([],self.gpu,self.cohort))
 def test_front_local_requires_f3_offline_and_same_supervisor_and_nonce(self):
  import startup_change_journal as journal
  with patch.object(journal,'query',return_value=dict(author='journal')):self.assertTrue(self.publish(local_environment=self.local_env()))
  with patch.object(journal,'unchanged',return_value=True):
   for field,value in [('BB_QUICKLOAD_PENDING','0'),('BB_ONLINE','1'),('BB_ARM_SESSION_NONCE','3'*32),('BB_RESTART_SUPERVISED','0')]:
    self.assertIsNone(self.early(environment={**self.env,'BB_QUICKLOAD_PENDING':'1',field:value}))
   bound=self.bound();self.owner['start']='different supervisor'
   self.assertIsNone(self.early(bound=bound))
 def test_front_controller_skips_library_helper_and_normal_game_batches(self):
  import launcher_local_aot as local
  import test_launcher_local_aot as fixture
  f=fixture.Startup();f.setUp();self.addCleanup(f.tearDown)
  previous=self.local_env();calls=[]
  def forbidden(**kw):raise AssertionError('ordinary preparation batch invoked')
  def game(**kw):
   calls.append(kw);self.assertTrue(kw['resume_only']);return dict(status='same-supervisor-f3-schedule-reused',capture_environment={})
  with patch.object(quick,'binding',return_value=dict(author='bound')),patch.object(quick,'prepared_local',return_value=previous):
   result,report=local.prepare_launch(str(f.inputs/'runtime'),f.args,{**f.env,'BB_QUICKLOAD_PENDING':'1'},project=f.root,
    archive_prepare=f.identity,local_prepare=forbidden,fixed_helper_prepare=forbidden,game_prepare=game,
    observe=lambda:dict(locked=False,pressure=1,swap_mib=0))
  self.assertEqual(len(calls),1);self.assertFalse(report['local']['builder_invoked'])
  self.assertEqual(result['BB_THD_TOKEN'],f.env['BB_THD_TOKEN']);self.assertEqual(result['BB_MTL_LOCAL_AOT_BUILD_ID'],'d'*64)
  self.assertEqual(report['fixed_helpers']['status'],'same-supervisor-reused')
 def test_resume_only_miss_does_not_prepare_sources_modules_or_probe_toolchain(self):
  import test_launcher_game_pso_automatic as fixture
  f=fixture.Entry();f.setUp();self.addCleanup(f.tearDown)
  with patch.object(quick,'resume',return_value=None):
   result=f.prepare(restart_binding=dict(author='bound'),resume_only=True,
    toolchain_probe=lambda:(_ for _ in ()).throw(AssertionError('toolchain probe on F3')))
  self.assertIsNone(result);self.assertEqual(f.calls,[])
 def test_no_receipt_and_cold_or_menu_restart_do_not_reuse(self):
  self.assertIsNone(self.resume());self.assertTrue(self.publish())
  for value in(None,'0',''):
   env=dict(self.env)
   if value is not None:env['BB_QUICKLOAD_PENDING']=value
   self.assertIsNone(self.resume(environment=env))
  self.assertEqual(self.calls,[])
 def test_unsupervised_unknown_mode_or_missing_nonce_does_not_form_binding(self):
  self.assertTrue(self.publish());bound=self.bound()
  for name,value in [('BB_ONLINE','unknown'),('BB_RESTART_SUPERVISED','0'),('BB_ARM_SESSION_NONCE',''),('BB_ARM_RESTART_NONCE','x'*32)]:
   self.assertIsNone(self.bound(environment={**self.env,name:value}))
   self.assertIsNone(self.resume(bound=bound,environment={**self.env,'BB_QUICKLOAD_PENDING':'1',name:value}))
 def test_online_preparation_can_feed_offline_f3_without_restoring_account_or_weakening_inputs(self):
  import startup_change_journal as journal
  local=self.local_env();online={**self.env,'BB_ONLINE':'1','BB_THD_TOKEN':'private-not-in-proof'}
  bound=self.bound(environment=online)
  self.assertEqual(bound,self.bound());self.assertNotIn('private-not-in-proof',json.dumps(bound))
  with patch.object(journal,'query',return_value=dict(author='journal')):
   self.assertTrue(self.publish(bound=bound,local_environment={**local,**online}))
  with patch.object(journal,'unchanged',return_value=True):
   self.assertEqual(self.early(),local)
   self.assertIsNone(self.early(environment={**online,'BB_QUICKLOAD_PENDING':'1'}))
  self.assertEqual(self.resume()['status'],'same-supervisor-f3-schedule-reused')
  self.assertIsNone(self.resume(environment={**online,'BB_QUICKLOAD_PENDING':'1'}))
  changed=copy.deepcopy(self.archive);changed['manifest']['game']['patches']['sha256']='0'*64
  self.assertIsNone(self.resume(bound=self.bound(archive_identity=changed)))
 def test_new_supervisor_start_or_nonce_cannot_reuse(self):
  self.assertTrue(self.publish())
  self.owner['start']='Tue Oct 27 02:02:03 2026';self.assertIsNone(self.resume())
  self.owner['start']='Tue Oct 27 01:02:03 2026';self.env['BB_ARM_RESTART_NONCE']='3'*32;self.assertIsNone(self.resume())
 def test_mod_run_shell_is_not_mistaken_for_stable_supervisor(self):
  def chain(pid):return dict(pid=pid,parent=77,start='per-launch',role='run.sh',script='run.sh')if pid==os.getppid()else dict(self.owner)
  self.assertEqual(quick.supervisor(self.stage,chain,self.cwd_probe),dict(pid=77,start=self.owner['start']))
  with self.assertRaises(ValueError):quick.supervisor(self.stage,lambda pid:dict(pid=pid,parent=1,start='x',role='other.sh',script='other.sh'),self.cwd_probe)
 def test_same_named_script_in_other_directory_or_wrong_argument_falls_back(self):
  self.assertTrue(self.publish());bound=self.bound()
  for script in(str(self.root/'other/tools/macos/supervise_restart.sh'),'supervise_restart.sh','./tools/macos/supervise_restart.sh'):
   self.owner['script']=script
   self.assertIsNone(self.bound());self.assertIsNone(self.resume(bound=bound))
  self.owner['script']=str(self.stage/'tools/macos/supervise_restart.sh')
  self.assertIsNotNone(self.resume(bound=bound))
 def test_changed_or_unobservable_cwd_falls_back_without_relabeling_stage(self):
  self.assertTrue(self.publish());bound=self.bound();other=self.root/'other';other.mkdir()
  self.assertIsNone(self.bound(cwd_probe=lambda pid:other));self.assertIsNone(self.resume(bound=bound,cwd_probe=lambda pid:other))
  def unavailable(pid):raise OSError('cwd denied')
  self.assertIsNone(self.bound(cwd_probe=unavailable));self.assertIsNone(self.resume(bound=bound,cwd_probe=unavailable))
 def test_original_script_and_parity_mutation_fall_back(self):
  self.assertTrue(self.publish());bound=self.bound();script=self.stage/'tools/macos/supervise_restart.sh';script.write_text('# changed original script')
  self.assertIsNone(self.bound());self.assertIsNone(self.resume(bound=bound))
  parity=json.loads((self.stage/'SOURCE-PARITY.json').read_text());parity['effective_sources']['tools/macos/supervise_restart.sh']=quick.file_sha(script)[0]
  write_json(self.stage/'SOURCE-PARITY.json',parity)
  self.assertIsNone(self.resume(bound=bound))
 def test_changed_boot_settings_cohort_cpu_component_or_selection_misses(self):
  self.assertTrue(self.publish());original=self.bound()['identity']
  changed=copy.deepcopy(self.archive);changed['manifest']['game']['settings']['sha256']='0'*64
  self.assertNotEqual(self.bound(archive_identity=changed)['identity'],original)
  self.assertIsNone(self.resume(bound=self.bound(archive_identity=changed)))
  self.assertIsNone(self.resume(bound=self.bound(cohort='b'*64)))
  self.cpu.write_bytes(b'changed cpu');self.assertIsNone(self.resume());self.cpu.write_bytes(b'author cpu')
  p=self.stage/'GAME-PSO-COMPONENT.json';p.write_text('{"changed":true}');self.assertIsNone(self.resume());p.write_text('{}')
  config=self.root/'mods.json';config.write_text('{"disabled":["author"]}');self.assertIsNone(self.resume())
 def test_changed_catalog_index_ready_or_added_metadata_falls_back(self):
  for name in('catalog.json','static-program-index.jsonl','READY.json','extra.json'):
   self.assertTrue(self.publish());p=self.generation/name;old=p.read_bytes()if p.exists()else None;p.write_bytes(b'changed')
   self.assertIsNone(self.resume());self.assertTrue(p.exists())
   if old is None:p.unlink()
   else:p.write_bytes(old)
 def test_bad_receipt_is_preserved_and_falls_back(self):
  self.assertTrue(self.publish());p=quick._path(self.gpu,self.bound());p.write_bytes(b'not json')
  self.assertIsNone(self.resume());self.assertEqual(p.read_bytes(),b'not json')
 def test_capture_failure_or_unexpected_environment_cannot_be_inherited(self):
  self.assertTrue(self.publish())
  def refuse(**kw):raise ValueError('bad receipt')
  self.assertIsNone(self.resume(configure_capture=refuse))
  self.assertIsNone(self.resume(configure_capture=lambda **kw:dict(capture_environment={'BB_THD_TOKEN':'no'})))
  result=self.resume(environment={**self.env,'BB_QUICKLOAD_PENDING':'1','BB_FETCH_CAPTURE_ROOT':'/old/untrusted','BB_FETCH_CAPTURE_GAME_ROOT':'/old/game'})
  self.assertEqual(result['capture_environment']['BB_FETCH_CAPTURE_GAME_ROOT'],str(self.view))
  proof=self.view.parent/'READY.json';proof.write_text('{}');self.assertIsNone(self.resume())
 def test_protection_errors_propagate_and_do_not_publish(self):
  def stop():raise RuntimeError('protected')
  with self.assertRaisesRegex(RuntimeError,'protected'):self.publish(check=stop)
  self.assertFalse(quick._path(self.gpu,self.bound()).exists())
  self.assertTrue(self.publish())
  with self.assertRaisesRegex(RuntimeError,'protected'):self.resume(check=stop)
 def test_path_digest_tampering_cannot_escape_hint_root(self):
  wrong=self.bound();wrong['identity']='../../other'
  self.assertIsNone(self.resume(bound=wrong));self.assertFalse(self.publish(bound=wrong))
 def test_process_reader_is_bounded_and_role_checked_without_launching_ps(self):
  class Result:returncode=0;stdout=f'1 {os.getuid()} Tue Oct 27 01:02:03 2026 /bin/bash tools/macos/supervise_restart.sh\n'.encode()
  with patch.object(quick.subprocess,'run',return_value=Result())as run:
   self.assertEqual(quick.process(77)['role'],'supervise_restart.sh')
   self.assertEqual(run.call_args.kwargs['timeout'],2);self.assertEqual(set(run.call_args.kwargs['env']),{'PATH'})
  Result.stdout=b'bad'
  with patch.object(quick.subprocess,'run',return_value=Result()),self.assertRaises(ValueError):quick.process(77)
 def test_lsof_cwd_reader_has_exact_pid_bounded_output_and_clean_environment(self):
  class Result:returncode=0;stdout=('p77\nfcwd\nn'+str(self.stage)+'\n').encode()
  with patch.object(quick.subprocess,'run',return_value=Result())as run:
   self.assertEqual(quick.process_cwd(77),self.stage)
   self.assertEqual(run.call_args.args[0],['/usr/sbin/lsof','-a','-p','77','-d','cwd','-Fpn'])
   self.assertEqual(run.call_args.kwargs['timeout'],2);self.assertEqual(set(run.call_args.kwargs['env']),{'PATH'})
  for raw in(b'p78\nfcwd\nn/wrong\n',b'p77\nn/one\n',b'p77\nf0\nn/one\n',b'p77\nfcwd\nn/one\nn/two\n',b'p77\nfcwd\nn/one\nfcwd\nn/two\n',b'X'*4097):
   Result.stdout=raw
   with patch.object(quick.subprocess,'run',return_value=Result()),self.assertRaises(ValueError):quick.process_cwd(77)
 def test_original_game_controller_uses_fast_result_without_scan_or_pso_runner(self):
  import test_launcher_game_pso_automatic as fixture
  f=fixture.Entry();f.setUp();self.addCleanup(f.tearDown)
  reused=dict(status='same-supervisor-f3-schedule-reused',capture_environment={})
  with patch.object(quick,'resume',return_value=reused)as resume:
   result=f.prepare(restart_binding=dict(author='bound'))
  self.assertIs(result,reused);self.assertEqual(f.calls,[])
  self.assertEqual(resume.call_args.kwargs['component'],f.component)
  self.assertEqual(resume.call_args.kwargs['backend'],str(f.stage/'library'))
 def test_original_local_controller_publishes_only_after_completed_preparation_and_guard(self):
  import test_launcher_local_aot as fixture
  import launcher_local_aot as local
  f=fixture.Startup();f.setUp();self.addCleanup(f.tearDown)
  def libraries(**kw):return {},dict(status='prepared')
  context=dict(author='pending-context')
  def game(**kw):return dict(status='completed',preparation={},_restart_context=context)
  values=dict(project=f.root,archive_prepare=f.identity,local_prepare=libraries,
      fixed_helper_prepare=lambda **kw:({},dict(status='completed')),game_prepare=game)
  with patch.object(quick,'binding',return_value=dict(author='binding')),patch.object(quick,'publish',return_value=True)as publish:
   _,report=local.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,observe=lambda:dict(locked=False,pressure=1,swap_mib=0),**values)
   self.assertEqual(publish.call_count,1);self.assertEqual(publish.call_args.kwargs['context'],context)
   self.assertNotIn('_restart_context',report['game_pso'])
  observations=iter([dict(locked=False,pressure=1,swap_mib=0),dict(locked=False,pressure=4,swap_mib=0)])
  with patch.object(quick,'binding',return_value=dict(author='binding')),patch.object(quick,'publish')as publish:
   with self.assertRaises(RuntimeError):local.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,observe=lambda:next(observations),**values)
   publish.assert_not_called()
 def test_empty_context_is_published_through_original_final_local_guard(self):
  import test_launcher_local_aot as fixture
  import launcher_local_aot as local
  f=fixture.Startup();f.setUp();self.addCleanup(f.tearDown);context=dict(author='empty-queue-context')
  kwargs=dict(project=f.root,archive_prepare=f.identity,local_prepare=lambda **kw:({},dict(status='prepared')),
      fixed_helper_prepare=lambda **kw:({},dict(status='completed')),game_prepare=lambda **kw:dict(status='empty',_restart_context=context))
  with patch.object(quick,'binding',return_value=dict(author='binding')),patch.object(quick,'publish',return_value=True)as publish:
   _,report=local.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,observe=lambda:dict(locked=False,pressure=1,swap_mib=0),**kwargs)
   self.assertEqual(publish.call_count,1);self.assertIs(publish.call_args.kwargs['context'],context)
   self.assertEqual(report['game_pso']['status'],'empty');self.assertNotIn('_restart_context',report['game_pso'])
  samples=iter([dict(locked=False,pressure=1,swap_mib=0),dict(locked=False,pressure=4,swap_mib=0)])
  with patch.object(quick,'binding',return_value=dict(author='binding')),patch.object(quick,'publish')as publish:
   with self.assertRaises(RuntimeError):local.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,observe=lambda:next(samples),**kwargs)
   publish.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
