"""Cross-Play reuse contracts, using authored inputs and a deterministic journal.

No real game, save, GPU or system tool is invoked by these tests.
"""
from pathlib import Path
import copy,hashlib,json,os,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import quick_restart_preparation as quick
import startup_change_journal as journal
import test_quick_restart_preparation as fixture

class Warm(unittest.TestCase):
 def setUp(self):
  self.f=fixture.Restart();self.f.setUp();self.addCleanup(self.f.tearDown)
  self.local=self.f.local_env();self.bound=self.f.bound()
  self.cache=self.f.gpu/'cache';self.cache.mkdir();(self.cache/'pso.bin').write_bytes(b'authored pipeline')
  self.capture=self.f.cache/('runtime-'+self.f.cohort)/'fetch-compiler-companions-v1';self.capture.mkdir()
  self.patches=[patch.object(journal,'query',side_effect=self.query),patch.object(journal,'unchanged',side_effect=self.unchanged)]
  for p in self.patches:p.start();self.addCleanup(p.stop)
 def query(self,roots,previous=None,**kw):
  # Simulate continuous FileEvents, including unknown additions and inode
  # replacement. No metadata-only equivalence is accepted by this test journal.
  rows={}
  for root in roots:
   p=Path(root)
   for f in [p,*p.rglob('*')]:
    st=f.lstat();rows[str(f)]=[st.st_ino,st.st_mode,st.st_size,st.st_mtime_ns,hashlib.sha256(f.read_bytes()).hexdigest()if f.is_file()else None]
  return dict(rows=rows)
 def unchanged(self,token,roots,**kw):return token==self.query(roots)
 def publish(self,**overrides):
  values=dict(anchor=quick.warm_anchor(self.bound),context=self.f.context,gpu=self.f.gpu,local_environment=self.local);values.update(overrides)
  return quick.publish_warm(**values)
 def load(self,**overrides):
  values=dict(bound=self.f.bound(),environment=self.f.env,gpu=self.f.gpu,tool_check=lambda local,check:True);values.update(overrides)
  return quick.prepared_warm(**values)
 def resume(self,schedule,**overrides):
  values=dict(schedule=schedule,gpu=self.f.gpu,component=self.f.component,backend=self.f.stage/'library',cohort=self.f.cohort,check=lambda:None,configure_capture=self.f.capture);values.update(overrides)
  return quick.resume_warm(**values)
 def test_new_supervisor_and_nonces_reuse_preparation_without_quickload(self):
  self.assertTrue(self.publish());old=self.bound
  self.f.owner['start']='new Play';self.f.env['BB_ARM_SESSION_NONCE']='3'*32;self.f.env['BB_ARM_RESTART_NONCE']='4'*32
  self.assertNotEqual(old,self.f.bound());self.assertEqual(quick.warm_binding(old),quick.warm_binding(self.f.bound()))
  saved=self.load();self.assertIsNotNone(saved)
  result=self.resume(saved);self.assertEqual(result['status'],'unchanged-preparation-schedule-reused')
  self.assertFalse(result['artifact_authority']);self.assertTrue(result['runtime_full_key_checks_retained'])
  self.assertEqual(set(saved['local']),quick.LOCAL_ENV)
  self.assertIsNone(self.f.resume(bound=old)) # Existing F3 authority is unchanged.
 def test_normal_save_changes_do_not_invalidate_or_enter_receipt(self):
  self.assertTrue(self.publish());save=self.f.root/'savedata/character';save.parent.mkdir();save.write_text('new progress')
  self.assertIsNotNone(self.load());path=quick._warm_path(self.f.gpu,quick.warm_binding(self.bound))
  self.assertNotIn('new progress',path.read_text());self.assertNotIn('savedata',path.read_text())
 def test_online_mode_and_credentials_are_never_restored_or_serialized(self):
  self.f.env.update(BB_ONLINE='1',BB_THD_TOKEN='private-current-token');self.assertTrue(self.publish())
  saved=self.load();self.assertIsNotNone(saved);self.assertNotIn('private-current-token',json.dumps(saved))
  self.assertIsNone(self.load(environment={**self.f.env,'BB_QUICKLOAD_PENDING':'1'}))
 def test_external_game_change_missing_mod_root_creation_or_captured_input_invalidates(self):
  for folder,name in [(self.f.game,'shader.bin'),(self.cache,'new.key'),(self.capture,'new.json')]:
   self.assertTrue(self.publish());p=folder/name;p.write_text('new input');self.assertIsNone(self.load());p.unlink()
  self.assertTrue(self.publish());mods=self.f.root/'mods';mods.mkdir();self.assertIsNone(self.load())
 def test_mod_selection_order_compiled_inputs_and_compatibility_identity_miss(self):
  self.assertTrue(self.publish())
  changed=copy.deepcopy(self.f.archive);changed['manifest']['game']['boot']['sha256']='0'*64
  for bound in (self.f.bound(archive_identity=changed),self.f.bound(cohort='b'*64),self.f.bound(environment={**self.f.env,'BB_MODS_ENABLED':'0'})):
   self.assertIsNone(self.load(bound=bound))
  self.assertIsNone(self.load(tool_check=lambda local,check:False))
  self.f.cpu.write_text('new actual CPU');self.assertIsNone(self.load())
 def test_local_damage_catalog_damage_and_lost_history_preserve_files_and_fall_back(self):
  for folder,name in [(Path(self.local['BB_MTL_LOCAL_AOT_ROOT'])/self.f.cohort/'artifacts'/('d'*64),'damaged.metallib'),(self.f.generation,'catalog.json')]:
   old=(folder/name).read_bytes()if(folder/name).exists()else None
   self.assertTrue(self.publish());(folder/name).write_bytes(b'corrupt');self.assertIsNone(self.load());self.assertEqual((folder/name).read_bytes(),b'corrupt')
   if old is None:(folder/name).unlink()
   else:(folder/name).write_bytes(old)
  self.assertTrue(self.publish())
  with patch.object(journal,'unchanged',return_value=False):self.assertIsNone(self.load())
 def test_source_change_during_full_preparation_cannot_publish(self):
  anchor=quick.warm_anchor(self.bound);(self.f.game/'changed').write_text('changed while preparing')
  self.assertFalse(self.publish(anchor=anchor))
 def test_change_between_load_and_capture_cannot_be_adopted(self):
  self.assertTrue(self.publish());saved=self.load();(self.cache/'pso.bin').write_text('changed after lookup')
  self.assertIsNone(self.resume(saved))
 def test_bad_receipt_unknown_schema_and_extra_local_fields_are_rejected(self):
  for field,value in [('schema',2),('artifact_authority',True),('kind','same-supervisor-f3-preparation-schedule'),('local',{**self.local,'BB_THD_TOKEN':'forbidden'})]:
   self.assertTrue(self.publish());p=quick._warm_path(self.f.gpu,quick.warm_binding(self.bound));doc=json.loads(p.read_text());doc[field]=value;p.write_text(json.dumps(doc));self.assertIsNone(self.load())
  p.write_bytes(b'not json');self.assertIsNone(self.load());self.assertEqual(p.read_bytes(),b'not json')
 def test_unknown_capture_fields_and_protection_failure_are_not_suppressed(self):
  self.assertTrue(self.publish());saved=self.load()
  self.assertIsNone(self.resume(saved,configure_capture=lambda **kw:dict(capture_environment={'BB_THD_TOKEN':'wrong'})))
  def stop():raise RuntimeError('severe pressure')
  with self.assertRaisesRegex(RuntimeError,'severe pressure'):self.load(check=stop)
 def test_front_controller_preserves_online_and_normal_save_semantics(self):
  import launcher_local_aot as local
  from test_launcher_local_aot import Startup
  f=Startup();f.setUp();self.addCleanup(f.tearDown)
  def forbidden(**kw):raise AssertionError('repeated ordinary batch')
  saved=dict(local=self.local,context=self.f.context)
  result=dict(status='unchanged-preparation-schedule-reused',capture_environment={})
  with patch.object(quick,'binding',return_value=self.bound),patch.object(quick,'prepared_warm',return_value=saved),patch.object(quick,'publish',return_value=True),patch.object(local,'launcher_inputs',wraps=local.launcher_inputs):
   env,report=local.prepare_launch(str(f.inputs/'runtime'),f.args,{**f.env,'BB_ONLINE':'1'},project=f.root,archive_prepare=f.identity,
     local_prepare=forbidden,fixed_helper_prepare=forbidden,game_prepare=lambda **kw:result,observe=lambda:dict(locked=False,pressure=1,swap_mib=0))
  self.assertEqual(env['BB_ONLINE'],'1');self.assertEqual(env['BB_THD_TOKEN'],f.env['BB_THD_TOKEN']);self.assertNotIn('BB_QUICKLOAD_PENDING',env)
  self.assertFalse(report['local']['builder_invoked']);self.assertTrue(report['local']['unchanged_schedule_reused'])

if __name__=='__main__':unittest.main()
