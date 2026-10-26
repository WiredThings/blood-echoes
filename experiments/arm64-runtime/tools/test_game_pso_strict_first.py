"""Author-only scheduler/native outcome controls; no process/GPU/game launch."""
from pathlib import Path
import copy,json,sys,unittest
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import game_pso_queue as q
import test_game_pso_queue as authored
from test_game_pso_queue import NativeAuthor,unit
from prepare_game_psos import preparation_ready
class Controls(unittest.TestCase):
 setUp=authored.Controls.setUp
 tearDown=authored.Controls.tearDown
 write=authored.Controls.write
 run_queue=authored.Controls.run_queue
 def seed_hint(self,u):
  self.write([u]);queue=q.Queue(self.manifest,self.progress,self.context,lambda:None);queue.index();item=queue.page('seed',16)[0];queue.mark(item,'seed',ready=True,recorded=True);queue.close()
 def ready_artifacts(self,*values):
  self.native.artifacts.update(unit(i)['recipe_blob_identity']for i in values)
 def test_new_nohint_domain_uses_real_strict_hit_no_record(self):
  self.write([unit(1)]);self.ready_artifacts(1);r=self.run_queue()
  self.assertEqual((r['completed'],r['recorded'],r['verified_artifact_hits'],r['strict_lookup_attempts']),(1,0,1,1));self.assertEqual(dict(self.native.record_calls),{})
 def test_new_context_does_not_import_hint_and_can_still_probe_actual_artifact(self):
  self.write([unit(1)]);self.ready_artifacts(1);a=self.run_queue();self.native.elapsed=0;self.context['worker']='f'*64;b=self.run_queue()
  self.assertNotEqual(a['job_id'],b['job_id']);self.assertEqual((b['completed'],b['recorded'],b['verified_artifact_hits']),(1,0,1))
 def test_wrong_hint_missing_product_falls_back_original_sequence(self):
  u=unit(1);self.seed_hint(u);r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(1,1,0));self.assertEqual(self.native.probe_calls[u['recipe_blob_identity']],0);self.assertEqual(self.native.compile_calls[u['recipe_blob_identity']],1)
 def test_source_fallback_cannot_be_admitted_even_with_strict_hit_counts(self):
  self.write([unit(1)]);self.ready_artifacts(1);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   row=original(u,phase,allow,ordinal)
   if phase=='prepare'and not allow:row['worker']['source_fallback']=True;row['worker']['functions'][0]['actual_route']='source'
   return row
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(1,1,0))
 def test_factory_helper_source_route_not_covered_by_worker_summary_falls_back(self):
  self.write([unit(1)]);self.ready_artifacts(1);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   row=original(u,phase,allow,ordinal)
   if phase=='prepare'and not allow:row['artifact_events'][0]['primary_route']=1
   return row
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(1,1,0))
 def test_local_function_identity_missing_cannot_count_as_lookup_hit(self):
  self.write([unit(1)]);self.ready_artifacts(1);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   row=original(u,phase,allow,ordinal)
   if phase=='prepare'and not allow:row['worker']['functions'][0]['identity']=''
   return row
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(1,1,0))
 def test_cold_missing_requests_record_environment_does_not_spawn_probe(self):
  self.write([unit(1)]);r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['strict_lookup_attempts']),(1,1,0));self.assertEqual(dict(self.native.probe_calls),{})
 def test_same_page_existing_hit_and_new_missing_only_records_new(self):
  self.write([unit(1),unit(2)]);self.ready_artifacts(1);r=self.run_queue()
  self.assertEqual((r['completed'],r['recorded'],r['verified_artifact_hits']),(2,1,1));self.assertNotIn(unit(1)['recipe_blob_identity'],self.native.record_calls);self.assertEqual(self.native.record_calls[unit(2)['recipe_blob_identity']],1)
 def test_malformed_probe_result_goes_original_record_path(self):
  self.write([unit(1)]);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   if phase=='prepare'and not allow:return dict(exit=0,worker=None)
   return original(u,phase,allow,ordinal)
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['completed'],r['recorded']),(1,1))
 def test_malformed_probe_counter_causing_ready_typeerror_is_record_fallback(self):
  self.write([unit(1)]);self.ready_artifacts(1);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   row=original(u,phase,allow,ordinal)
   if phase=='prepare'and not allow:row['worker']['artifact_counts']['hits']='one'
   return row
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(1,1,0))
 def test_native_protection_stop_not_caught_as_miss(self):
  self.write([unit(1)]);self.native.stop=unit(1)['recipe_blob_identity'];r=self.run_queue();self.assertEqual(r['status'],'stopped');self.assertEqual(r['recorded'],0);self.assertFalse(self.native.record_calls)
 def test_local_protection_stop_never_invokes_worker(self):
  self.write([unit(1)]);self.native.local=lambda *args:dict(status='stopped',error='author pressure');r=self.run_queue();self.assertEqual(r['status'],'stopped');self.assertEqual(r['recorded'],0);self.assertFalse(self.native.probe_calls)
 def test_record_phase_preserves_original_record_only_semantics(self):
  self.write([unit(1)]);self.ready_artifacts(1);r=self.run_queue(phase='record');self.assertEqual((r['completed'],r['recorded'],r['strict_lookup_attempts']),(0,1,0));self.assertFalse(self.native.probe_calls)
 def test_built_or_compile_attempt_on_false_cannot_be_lookup_success(self):
  self.write([unit(1)]);self.ready_artifacts(1);original=self.native.execute
  def execute(u,phase,allow,ordinal):
   row=original(u,phase,allow,ordinal)
   if phase=='prepare'and not allow:row['worker']['artifact_counts'].update(built=1,hits=0,compile_attempts=1);row['artifact_events'][0]['state']=2
   return row
  self.native.execute=execute;r=self.run_queue();self.assertEqual((r['recorded'],r['collection_strict_hits']),(1,0))
 def test_actual_tess_event_accounting_does_not_assume_archive_counter_equality(self):
  self.write([unit(1)]);self.ready_artifacts(1);row=self.native.execute(unit(1),'prepare',False,1);row['worker']['artifact_counts']['attempts']=5;row['worker']['artifact_counts']['hits']=5;row['worker']['strict_archive_hits']=2;row['artifact_events']*=5
  self.assertTrue(q.strict_lookup_ready(row,preparation_ready))
 def test_false_or_missing_ready_and_bad_events_never_admitted(self):
  self.ready_artifacts(1);base=self.native.execute(unit(1),'prepare',False,1)
  for changed in ('ready','source','allow','events','compile','identity'):
   row=copy.deepcopy(base)
   if changed=='ready':row['worker']['native_pipeline_ready']=False
   if changed=='source':row['worker'].pop('source_fallback')
   if changed=='allow':row['worker']['allow_compile']=True
   if changed=='events':row['artifact_events'][0]['archive_sha256']='BAD'
   if changed=='compile':row['worker']['artifact_counts']['compile_attempts']=1
   if changed=='identity':row['worker']['functions']=[]
   self.assertFalse(q.strict_lookup_ready(row,preparation_ready),changed)
if __name__=='__main__':unittest.main(verbosity=2)
