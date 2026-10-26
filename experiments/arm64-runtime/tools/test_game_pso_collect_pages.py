"""CPU-only page-order/lifecycle authors. No native/GPU/process execution."""
import collections,unittest
import game_pso_queue as q
import test_game_pso_queue as authored
from test_game_pso_queue import unit

class Controls(unittest.TestCase):
 setUp=authored.Controls.setUp
 tearDown=authored.Controls.tearDown
 write=authored.Controls.write
 run_queue=authored.Controls.run_queue
 def prepared_helper_only(self):self.native.sources.add('author-finite-helper-not-game-pso')
 def trace(self):
  events=[];execute=self.native.execute
  def worker(u,phase,allow,ordinal):events.append((phase,allow,u['recipe_blob_identity']));return execute(u,phase,allow,ordinal)
  self.native.execute=worker;return events
 @staticmethod
 def mock_process_count(events):
  # Models only the already tested <=16 same-phase/policy worker admission.
  # This is never an actual process/timing measurement.
  count=0;last=None;size=0
  for phase,allow,rid in events:
   key=(phase,allow)
   if key!=last or size==16:count+=1;last=key;size=0
   size+=1
  return count
 def test_mock_cold409_preserves1227_calls_but_groups_phase_by_page(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(409));events=self.trace();r=self.run_queue()
  self.assertEqual((r['completed'],r['strict_lookup_attempts'],r['recorded']),(409,409,409));self.assertEqual(len(events),1227)
  self.assertEqual(events[:16], [('prepare',False,unit(i)['recipe_blob_identity'])for i in range(16)])
  self.assertEqual(events[16:32],[('record',False,unit(i)['recipe_blob_identity'])for i in range(16)])
  self.assertEqual(self.mock_process_count(events),78);self.assertEqual(r['final_local_calls'],1);self.assertEqual(self.native.phase_calls.count('record'),1)
 def test_mixed_hit_local_inputs_and_miss_keep_original_native_decisions(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(18));hits={unit(i)['recipe_blob_identity']for i in(0,16,17)};local=unit(1)['recipe_blob_identity'];self.native.artifacts.update(hits);self.native.sources.add(local);events=self.trace();r=self.run_queue()
  self.assertEqual((r['completed'],r['recorded'],r['collection_strict_hits']),(18,14,3));self.assertEqual(sum(self.native.probe_calls.values()),18)
  self.assertTrue(all(self.native.record_calls[k]==0 for k in hits|{local}));self.assertEqual([v[0]for v in events[:16]],['prepare']*16);self.assertEqual([v[0]for v in events[16:30]],['record']*14)
 def test_strict_protection_discards_only_pending_refs_then_revalidates_on_resume(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(6));self.native.stop=unit(3)['recipe_blob_identity'];r=self.run_queue();self.assertEqual((r['status'],r['recorded'],r['completed'],r['cycle_collected']),('stopped',0,0,0));self.assertEqual(r['final_local_calls'],0)
  self.native.stop=None;self.native.elapsed=0;second=self.run_queue();self.assertEqual((second['status'],second['completed']),('completed',6));self.assertGreaterEqual(self.native.probe_calls[unit(0)['recipe_blob_identity']],2);self.assertTrue(all(n==1 for n in self.native.record_calls.values()))
 def test_record_protection_keeps_completed_record_and_resumes_remaining(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(6));execute=self.native.execute;stop=[True]
  def worker(u,phase,allow,ordinal):
   if stop[0]and phase=='record'and u['recipe_blob_identity']==unit(1)['recipe_blob_identity']:raise q.Stopped('author record protection')
   return execute(u,phase,allow,ordinal)
  self.native.execute=worker;r=self.run_queue();self.assertEqual((r['status'],r['recorded'],r['cycle_collected'],r['completed']),('stopped',1,1,0));self.assertEqual(r['final_local_calls'],0)
  stop[0]=False;self.native.elapsed=0;second=self.run_queue();self.assertEqual((second['status'],second['completed']),('completed',6));self.assertEqual(self.native.record_calls[unit(0)['recipe_blob_identity']],1);self.assertTrue(all(n==1 for n in self.native.record_calls.values()))
 def test_bad_packet_not_turned_into_hit_or_completed_by_reordering(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(6));bad=unit(1)['recipe_blob_identity'];self.native.reject.add(bad);events=self.trace();r=self.run_queue();self.assertEqual((r['status'],r['completed'],r['rejected']),('partial',5,1));self.assertFalse(r['full_manifest_preparation_verified']);self.assertNotIn(bad,self.native.artifacts);self.assertEqual([v[0]for v in events[:6]],['prepare']*6)
 def test_recipe_budget_finishes_record_of_admitted_items_without_double_count(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(20));r=self.run_queue(max_recipes=7);self.assertEqual((r['work_units'],r['cycle_collected'],r['recorded'],r['strict_lookup_attempts']),(7,7,7,7));self.assertEqual(r['status'],'partial');self.assertEqual(r['final_local_calls'],0)
  self.native.elapsed=0;second=self.run_queue();self.assertEqual(second['completed'],20);self.assertTrue(all(n==1 for n in self.native.record_calls.values()))
 def test_time_budget_during_record_keeps_partial_page_resumable(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(9));r=self.run_queue(seconds=5,page_size=4);self.assertEqual((r['status'],r['cycle_collected'],r['recorded']),('partial',1,1));self.assertEqual(r['final_local_calls'],0)
  self.native.elapsed=0;second=self.run_queue();self.assertEqual((second['status'],second['completed']),('completed',9));self.assertTrue(all(n==1 for n in self.native.record_calls.values()))
 def test_old_64_page_request_is_bounded_to16_item_window(self):
  self.prepared_helper_only();self.write(unit(i)for i in range(37));events=self.trace();r=self.run_queue(page_size=64);self.assertEqual(r['max_page_items'],16);self.assertEqual([v[0]for v in events[:16]],['prepare']*16);self.assertEqual(events[16][0],'record');self.assertEqual(r['completed'],37)
if __name__=='__main__':unittest.main(verbosity=2)
