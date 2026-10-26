"""Pure CPU authors: disk orchestration, current native outcomes, no real worker."""
from pathlib import Path
import collections,json,sys,unittest
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
import game_pso_queue as q
import test_game_pso_queue as authored
from test_game_pso_queue import unit
class Controls(unittest.TestCase):
 setUp=authored.Controls.setUp
 tearDown=authored.Controls.tearDown
 write=authored.Controls.write
 run_queue=authored.Controls.run_queue
 def test_cold_whole_directory_collected_before_one_final_build(self):
  self.write(unit(i)for i in range(37));timeline=[];local=self.native.local;execute=self.native.execute
  def phase(p,g):timeline.append(('local',p,len(self.native.sources)));return local(p,g)
  def worker(u,p,a,o):timeline.append(('worker',p));return execute(u,p,a,o)
  self.native.local=phase;self.native.execute=worker;r=self.run_queue(page_size=4)
  self.assertEqual((r['status'],r['completed'],r['recorded']),('completed',37,37),r)
  self.assertEqual((r['initial_local_calls'],r['final_local_calls']),(1,1));self.assertEqual(self.native.phase_calls.count('prepare'),2);self.assertEqual(self.native.phase_calls.count('record'),1)
  finals=[i for i,x in enumerate(timeline)if x==('local','prepare',37)];self.assertEqual(len(finals),1)
  self.assertTrue(all(x!=('worker','record')for x in timeline[finals[0]+1:]));self.assertEqual(r['max_page_items'],4)
 def test_warm_all_strict_one_native_pass_then_metadata_verification(self):
  values=list(range(37));self.write(unit(i)for i in values);self.native.artifacts.update(unit(i)['recipe_blob_identity']for i in values)
  r=self.run_queue(page_size=4);self.assertTrue(r['warm_fast_path']);self.assertEqual((r['completed'],r['recorded'],r['strict_lookup_attempts']),(37,0,37));self.assertEqual(self.native.phase_calls.count('prepare'),1);self.assertEqual(sum(self.native.probe_calls.values()),37);self.assertFalse(self.native.compile_calls)
 def test_existing_local_functions_missing_only_pso_do_not_record_again(self):
  self.write([unit(1)]);self.native.sources.add(unit(1)['recipe_blob_identity']);self.native.libraries.update(self.native.sources);r=self.run_queue();self.assertEqual((r['completed'],r['recorded']), (1,0));self.assertEqual(self.native.compile_calls[unit(1)['recipe_blob_identity']],1)
 def test_collection_interruption_resumes_without_reemitting_recorded_sources(self):
  self.write(unit(i)for i in range(20));r=self.run_queue(max_recipes=7);self.assertEqual((r['completed'],r['recorded'],r['cycle_collected']),(0,7,7));cycle=r['cycle_id'];self.native.elapsed=0;r2=self.run_queue()
  self.assertEqual((r2['status'],r2['completed'],r2['recorded']),('completed',20,13),r2);self.assertEqual(r2['cycle_id'],cycle);self.assertEqual(set(self.native.record_calls.values()),{1});self.assertFalse(r2['warm_fast_path'])
 def test_changed_manifest_invalidates_collection_cycle_but_not_native_authority(self):
  self.write([unit(1),unit(2)]);a=self.run_queue(max_recipes=1);self.native.elapsed=0;self.write([unit(1),unit(2),unit(3)]);b=self.run_queue();self.assertNotEqual(a['cycle_id'],b['cycle_id']);self.assertEqual(b['completed'],3)
 def test_final_pass_resume_keeps_products_and_requires_current_revalidation(self):
  self.write(unit(i)for i in range(8));first=self.run_queue(max_recipes=8);self.assertEqual(first['cycle_stage'],'build');self.native.elapsed=0;second=self.run_queue(max_recipes=3);self.assertEqual(second['completed'],0);self.assertEqual(second['provisional_native'],3);self.assertEqual(len(self.native.artifacts),3)
  self.native.elapsed=0;third=self.run_queue();self.assertEqual((third['status'],third['completed']),('completed',8),third);self.assertEqual(len(self.native.artifacts),8);self.assertTrue(all(n==1 for n in self.native.compile_calls.values()))
 def test_warm_dependency_change_prevents_fast_commit_and_revalidates(self):
  self.write(unit(i)for i in range(5));self.native.artifacts.update(unit(i)['recipe_blob_identity']for i in range(5));execute=self.native.execute
  def worker(u,p,a,o):
   row=execute(u,p,a,o)
   if o==5:self.native.environment_epoch+=1
   return row
  self.native.execute=worker;r=self.run_queue();self.assertFalse(r['warm_fast_path']);self.assertEqual(r['final_local_calls'],1);self.assertEqual(r['completed'],5);self.assertEqual(self.native.phase_calls.count('prepare'),2)
 def test_late_helper_source_deferred_without_extra_build_loop(self):
  self.write([unit(1),unit(2)]);execute=self.native.execute;late=[True]
  def worker(u,p,a,o):
   row=execute(u,p,a,o)
   if late[0]and p=='prepare'and a and u['recipe_blob_identity']==unit(2)['recipe_blob_identity']:
    row['artifact_events'][0]['primary_route']=1;self.native.environment_epoch+=1
   return row
  self.native.execute=worker;r=self.run_queue();self.assertEqual((r['status'],r['completed'],r['source_late_deferred']),('partial',0,1));self.assertEqual(r['final_local_calls'],1);self.assertEqual(self.native.phase_calls.count('prepare'),2);self.assertGreater(r['provisional_native'],0)
  late[0]=False;self.native.elapsed=0;r2=self.run_queue();self.assertEqual((r2['status'],r2['completed'],r2['recorded']),('completed',2,0),r2)
 def test_stop_during_collection_does_not_build_or_fallback(self):
  self.write([unit(1)]);self.native.stop=unit(1)['recipe_blob_identity'];r=self.run_queue();self.assertEqual(r['status'],'stopped');self.assertEqual(r['final_local_calls'],0)
 def test_stop_at_final_anchor_not_promoted_or_fallback(self):
  self.write([unit(1)]);local=self.native.local
  def phase(p,g):
   if p=='verify':raise q.Stopped('author final pressure')
   return local(p,g)
  self.native.local=phase;r=self.run_queue();self.assertEqual(r['status'],'stopped');self.assertEqual(r['completed'],0);self.assertEqual(r['provisional_native'],1)
 def test_failed_verification_not_success_and_no_hidden_rebuild(self):
  self.write([unit(1)]);local=self.native.local
  def phase(p,g):
   if p=='verify':return dict(status='changed',environment_identity='f'*64)
   return local(p,g)
  self.native.local=phase;r=self.run_queue();self.assertEqual(r['status'],'partial');self.assertFalse(r['full_manifest_preparation_verified']);self.assertEqual(r['completed'],0);self.assertEqual(r['final_local_calls'],1)
 def test_record_only_no_prepare_or_snapshot_or_build(self):
  self.write(unit(i)for i in range(9));r=self.run_queue(phase='record',page_size=2);self.assertEqual((r['recorded'],r['completed'],r['deferred']),(9,0,9));self.assertEqual(self.native.phase_calls,['record']);self.assertEqual((r['initial_local_calls'],r['final_local_calls']),(0,0))
 def test_prepare_only_no_record_and_one_current_build(self):
  self.write([unit(1)]);self.native.sources.add(unit(1)['recipe_blob_identity']);r=self.run_queue(phase='prepare');self.assertEqual((r['completed'],r['recorded']),(1,0));self.assertEqual(self.native.phase_calls.count('prepare'),1);self.assertNotIn('record',self.native.phase_calls)
 def test_missing_request_after_resumed_marker_cannot_approve_local_success(self):
  self.write([unit(1)]);r=self.run_queue(max_recipes=1);self.assertEqual(r['cycle_stage'],'build');self.native.sources.clear();self.native.libraries.clear();self.native.elapsed=0;r2=self.run_queue();self.assertEqual((r2['status'],r2['completed'],r2['deferred']),('partial',0,1));self.assertFalse(r2['full_manifest_preparation_verified'])
 def test_no_environment_proof_never_turns_native_ready_into_final(self):
  self.write([unit(1)]);local=self.native.local
  def phase(p,g):return dict(status='unavailable')if p=='snapshot'else local(p,g)
  self.native.local=phase;r=self.run_queue();self.assertEqual((r['status'],r['completed'],r['provisional_native']),('partial',0,1));self.assertFalse(r['full_manifest_preparation_verified'])
if __name__=='__main__':unittest.main(verbosity=2)
