"""Scheduling controls with authored inputs; no native/GPU success is asserted."""
from pathlib import Path
import json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from pso_startup_schedule import Schedule
from game_pso_queue import fingerprint,digest
from prepare_game_psos import rows
from test_game_pso_queue import unit

@unittest.skipUnless(sys.platform=='darwin','Darwin history')
class Scheduling(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve();self.gpu=self.root/'gpu';self.cache=self.gpu/'cache/CUSA03173-metal-author';self.artifact=self.gpu/'cache/CUSA03173/metal';self.progress=self.gpu/'game-pso-progress';self.cohort='a'*64;self.build='b'*64;self.local=self.gpu/'local-metal';self.requests=self.local/self.cohort/'requests';self.libraries=self.local/self.cohort/'artifacts'/self.build
  for p in(self.cache,self.artifact,self.progress,self.requests,self.libraries):p.mkdir(parents=True,exist_ok=True)
  self.manifest=self.root/'candidates.jsonl';self.u=unit(1);self.u['namespace']['cache']=str(self.cache)
  refs=[self.u['profile']['reference'],self.u['key']['reference'],self.u['stages'][0]['metadata'],self.u['stages'][0]['spirv']]
  for ref in refs:ref['path']=str(self.cache/ref['name']);Path(ref['path']).write_bytes(b'authored-input')
  self.u['execution_policy']['candidate_preparation']['frozen_input_set_identity']=digest(refs)
  self.write();self.environment=dict(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_ROOT=str(self.local),BB_MTL_LOCAL_AOT_COHORT=self.cohort,BB_MTL_LOCAL_AOT_BUILD_ID=self.build,BB_MTL_LOCAL_AOT_ENVIRONMENT_ID='e'*64);self.context={'worker':'a'*64,'options':'b'*64};self.code={'tool.py':'c'*64};self.full={'status':'completed','full_manifest_preparation_verified':True}
 def write(self):self.manifest.write_text(json.dumps(self.u)+'\n')
 def schedule(self):return Schedule(self.manifest,context=self.context,environment=self.environment,gpu=self.gpu,artifact=self.artifact,progress=self.progress,code_identity=self.code,rows=rows,fingerprint=fingerprint,check=lambda:None)
 def seed(self):
  s=self.schedule();self.assertIsNone(s.reuse());self.assertIsNotNone(s.token);self.assertTrue(s.publish(self.full));return s
 def test_verified_unchanged_work_is_only_a_scheduling_hit(self):
  self.seed();s=self.schedule();r=s.reuse();self.assertIsNotNone(r);self.assertEqual(r['status'],'unchanged-schedule-reused');self.assertFalse(r['full_manifest_preparation_verified']);self.assertFalse(r['native_verification_invoked_this_call']);self.assertFalse(r['runtime_cache_import_allowed'])
 def test_failed_partial_or_generic_success_cannot_publish(self):
  for r in [dict(status='failed',full_manifest_preparation_verified=True),dict(status='completed'),dict(status='completed',full_manifest_preparation_verified=False),dict(status='stopped')]:
   s=self.schedule();self.assertFalse(s.publish(r));self.assertFalse(s.path.exists())
 def test_same_size_changed_input_and_late_change_are_not_reused(self):
  self.seed();s=self.schedule();path=self.cache/self.u['stages'][0]['metadata']['name'];before=path.stat();path.write_bytes(b'X'*before.st_size);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns));self.assertIsNone(s.reuse());self.assertIsNone(self.schedule().reuse())
 def test_output_or_local_library_changes_are_not_reused(self):
  self.seed();(self.artifact/'new.archive').write_bytes(b'new');self.assertIsNone(self.schedule().reuse());s=self.schedule();s.publish(self.full);(self.libraries/'new.metallib').write_bytes(b'new');self.assertIsNone(self.schedule().reuse())
 def test_current_code_options_and_manifest_changes_invalidate(self):
  self.seed();self.context={'worker':'d'*64};self.assertIsNone(self.schedule().reuse());self.context={'worker':'a'*64,'options':'b'*64};self.code={'tool.py':'d'*64};self.assertIsNone(self.schedule().reuse());self.code={'tool.py':'c'*64};self.u['classification']['family']='changed';self.write();self.assertIsNone(self.schedule().reuse())
 def test_sdk_environment_identity_change_or_missing_cannot_reuse(self):
  self.seed();self.environment['BB_MTL_LOCAL_AOT_ENVIRONMENT_ID']='f'*64;self.assertIsNone(self.schedule().reuse());del self.environment['BB_MTL_LOCAL_AOT_ENVIRONMENT_ID'];self.assertIsNone(self.schedule().path)
 def test_bad_hint_and_unavailable_journal_use_original_path(self):
  old=self.seed();old.path.write_text('bad');self.assertIsNone(self.schedule().reuse());s=self.schedule();s.publish(self.full)
  with patch('pso_startup_schedule.unchanged',return_value=False):self.assertIsNone(self.schedule().reuse())
 def test_geometry_and_unknown_protocol_cannot_shortcut(self):
  self.u['owned_geometry_pso']={};self.write();self.assertIsNone(self.schedule().path);del self.u['owned_geometry_pso'];self.u['record_type']='unknown';self.write();self.assertIsNone(self.schedule().path)

if __name__=='__main__':unittest.main()
