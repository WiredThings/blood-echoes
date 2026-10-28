"""Authored native outcomes; no game, actual GPU, save or cache is touched."""
from pathlib import Path
import copy,json,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import game_pso_queue as queue
import test_game_pso_queue as authored
import launcher_game_pso as launcher

class Coverage(unittest.TestCase):
 setUp=authored.Controls.setUp
 tearDown=authored.Controls.tearDown
 write=authored.Controls.write
 run_queue=authored.Controls.run_queue
 def reuse(self,u):
  if int(u['stages'][0]['program_hash'],16)<self.previous:
   return dict(status='required-recipe-schedule-reused',previous_native_verification=True,
       native_worker_invoked=False,runtime_artifact_authority=False,input=queue.execution_identity(u))
 def mixed(self,total=3,previous=1,initial_hits=1,final_hits=1):
  self.previous=previous;self.write(authored.unit(i)for i in range(total));self.native.sources.add('authored fixed helper')
  self.native.artifacts.update(authored.unit(i)['recipe_blob_identity']for i in range(previous,previous+initial_hits))
  original=self.native.local;calls=[0]
  def local(phase,guard):
   if phase=='prepare':
    calls[0]+=1
    if calls[0]==2:self.native.artifacts.update(authored.unit(i)['recipe_blob_identity']for i in range(previous,previous+final_hits))
   return original(phase,guard)
  self.native.local=local
  return self.run_queue(reuse_unit=self.reuse)
 def test_real_shape_573_reused_47_native_hits_65_newly_built(self):
  r=self.mixed(685,573,19,47)
  self.assertEqual((r['completed'],r['scheduled_reused'],r['collection_strict_hits'],r['verified_artifact_hits']),(685,573,19,47))
  self.assertEqual(sum(self.native.compile_calls.values()),65)
  self.assertFalse(r['full_manifest_preparation_verified']);self.assertFalse(launcher.verified_mixed_schedule(r))
  self.assertTrue(queue.verified_completion_schedule(r))
  b=r['completion_coverage']['body'];self.assertEqual((b['scheduled_reused'],b['native_hits'],b['native_built']),(573,47,65))
  self.assertFalse(b['artifact_authority']);self.assertEqual(b['context_sha256'],queue.digest(self.context))
 def test_missing_changed_or_inconsistent_coverage_is_not_success(self):
  original=self.mixed();self.assertTrue(queue.verified_completion_schedule(original))
  for field,value in [('required',4),('scheduled_reused',2),('native_built',0),('native_hits',2),('artifact_authority',True),('environment_identity','a'*64),('manifest_sha256','b'*64),('context_sha256','c'*64),('job_id','d'*64),('cycle_id','e'*32),('bindings_sha256','invalid')]:
   r=copy.deepcopy(original);r['completion_coverage']['body'][field]=value;r['completion_coverage']['sha256']=queue.digest(r['completion_coverage']['body'])
   with self.subTest(field=field):self.assertFalse(queue.verified_completion_schedule(r))
  for key in original['completion_coverage']['body']:
   r=copy.deepcopy(original);del r['completion_coverage']['body'][key];r['completion_coverage']['sha256']=queue.digest(r['completion_coverage']['body']);self.assertFalse(queue.verified_completion_schedule(r),key)
  r=copy.deepcopy(original);r['completion_coverage']['sha256']='0'*64;self.assertFalse(queue.verified_completion_schedule(r))
  del r['completion_coverage'];self.assertFalse(queue.verified_completion_schedule(r))
 def test_partial_provisional_foreign_context_and_protection_are_rejected(self):
  original=self.mixed()
  for field,value in [('status','partial'),('required_preparation_complete',False),('index_complete',False),('cycle_stage','prepare'),('completed',2),('visited',2),('not_visited',1),('deferred',1),('rejected',1),('protection_stops',1),('provisional_native',1),('source_late_deferred',1),('index_rejected',1),('scheduled_reused',True),('budget_deferred',True),('current_context',{'foreign':True}),('final_environment_verification',dict(status='changed'))]:
   r=copy.deepcopy(original);r[field]=value
   with self.subTest(field=field):self.assertFalse(queue.verified_completion_schedule(r))
 def test_failed_environment_does_not_emit_committed_coverage(self):
  original=self.native.local
  def local(phase,guard):return dict(status='changed')if phase=='verify'else original(phase,guard)
  self.native.local=local;r=self.mixed()
  self.assertEqual(r['status'],'partial');self.assertNotIn('completion_coverage',r)
 def test_zero_exit_without_actual_native_ready_cannot_supply_coverage(self):
  self.native.worker_only=True;r=self.mixed()
  self.assertEqual(r['status'],'partial');self.assertNotIn('completion_coverage',r)
 def test_budget_interruption_is_not_a_completed_proof(self):
  self.previous=1;self.write(authored.unit(i)for i in range(3));r=self.run_queue(reuse_unit=self.reuse,max_recipes=1)
  self.assertNotIn('completion_coverage',r);self.assertFalse(queue.verified_completion_schedule(r))
 def test_warm_native_commit_uses_actual_warm_environment_proof(self):
  r=self.mixed(3,1,2,2);self.assertTrue(r['warm_fast_path']);self.assertTrue(queue.verified_completion_schedule(r))
  self.assertEqual(r['completion_coverage']['body']['native_built'],0)
  del r['warm_environment_verification'];self.assertFalse(queue.verified_completion_schedule(r))
 def test_controller_publishes_new_mixed_results_only_with_real_static_context(self):
  import test_launcher_game_pso_automatic as fixtures
  f=fixtures.Entry();f.setUp();self.addCleanup(f.tearDown)
  f.component.update(automatic_capture_schema=1,worker=f.stage/'worker',worker_sha256='a'*64,layout=f.stage/'layout',layout_sha256='b'*64)
  context=f.completed_static_context();r=self.mixed()
  def candidates(**kw):
   result=f.candidates(**kw);result['eligible']=3;return result
  out=f.prepare(prepared_inputs=context,restart_binding=dict(authored=True),capture_configure=f.configured_capture,candidate_builder=candidates,runner=lambda config:r)
  self.assertIn('_restart_context',out);self.assertEqual(out['completion_coverage_counts']['native_built'],1)
  self.assertFalse(out['runtime_cache_import_allowed']);self.assertFalse(out['preparation']['full_manifest_preparation_verified'])
  broken=copy.deepcopy(r);del broken['completion_coverage']
  out=f.prepare(prepared_inputs=context,restart_binding=dict(authored=True),capture_configure=f.configured_capture,candidate_builder=candidates,runner=lambda config:broken)
  self.assertNotIn('_restart_context',out)

if __name__=='__main__':unittest.main()
