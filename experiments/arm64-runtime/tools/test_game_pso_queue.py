from pathlib import Path
import collections,json,sqlite3,sys,tempfile,tracemalloc,unittest
E=Path(__file__).resolve().parent
from game_pso_queue import run_manifest,Queue,Stopped,Deferred,digest
from prepare_game_psos import preparation_ready

def unit(i,eligible=True):
 cache='/private/author/cache'
 def ref(name,tag):return dict(path=cache+'/'+name,name=name,bytes=16,sha256=digest([tag,i]))
 profile=ref('profile.bin','profile');key=ref(f'{i}.key','key')
 metadata=ref(f'{i}.meta','metadata');spirv=ref(f'{i}.spv','spirv')
 return dict(schema=2,record_type='offline-pso-input',namespace=dict(cache_id=digest(['cache',i]),cache=cache),
  profile=dict(reference=profile,semantic_sha256=digest(['profile-semantic',i])),
  key=dict(reference=key),stages=[dict(logical_stage='vertex',hardware_stage='vertex',program_hash=f'{i:016x}',
    cache_local_permutation=i,permutation_hash=f'{i+1:016x}',metadata=metadata,spirv=spirv,serialized_spec_sha256=digest(['spec',i]))],
  recipe_blob_identity=digest(['recipe',i]),source_provenance=dict(status='unknown'),
  classification=dict(family='graphics-existing-plain-or-rectlist-chain'),
  execution_policy=dict(runtime_cache_import_allowed=False,candidate_preparation=dict(policy_eligible=eligible,
    frozen_input_set_identity=digest([profile,key,metadata,spirv]))))
class NativeAuthor:
 def __init__(self):self.artifacts=set();self.record_calls=collections.Counter();self.compile_calls=collections.Counter();self.probe_calls=collections.Counter();self.elapsed=0;self.reject=set();self.defer=set();self.stop=None;self.worker_only=False;self.phase_calls=[]
 def clock(self):return self.elapsed
 def check(self):pass
 def local(self,phase,guard):guard();self.phase_calls.append(phase);return dict(status='prepared'if phase=='prepare'else'record')
 def execute(self,u,phase,allow,ordinal):
  rid=u['recipe_blob_identity'];self.elapsed+=1
  if self.stop==rid:raise Stopped('author native safety')
  if rid in self.reject:return dict(phase=phase,exit=65,worker=dict(status='rejected'),artifact_events=[])
  if phase=='record':self.record_calls[rid]+=1;return dict(phase=phase,exit=0,worker=dict(status='source-requests-recorded'),artifact_events=[])
  if not allow:self.probe_calls[rid]+=1
  hit=rid in self.artifacts
  built=not hit and allow and rid not in self.defer and not self.worker_only
  if built:self.compile_calls[rid]+=1;self.artifacts.add(rid)
  good=(hit or built) and not self.worker_only
  return dict(phase=phase,exit=0,worker=dict(native_pipeline_ready=good,artifact_publication_observed=good,allow_compile=allow,source_fallback=False,
   functions=[dict(actual_route='local-metallib',identity=digest(['actual-author-function',rid]))],
   artifact_counts=dict(attempts=1,hits=int(hit),built=int(built),deferred=int(not good),failed=0,critical=0,unavailable=0,compile_attempts=int(built))),
   artifact_events=[dict(state=1 if hit else 2 if built else 3,archive_sha256='a'*64 if good else'',
    key=digest(['author-key',rid]),descriptor=digest(['author-descriptor',rid]),primary=digest(['actual-author-function',rid]),primary_route=2,fragment='',fragment_route=0)])
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.progress=self.root/'progress';self.progress.mkdir();self.manifest=self.root/'manifest.jsonl';self.native=NativeAuthor();self.context=dict(worker='a'*64,dso='b'*64,cohort='c'*64,layout='d'*64,options='e'*64)
 def tearDown(self):self.temp.cleanup()
 def write(self,units):self.manifest.write_text(''.join(json.dumps(u,separators=(',',':'))+'\n'for u in units))
 def run_queue(self,**opts):
  options=dict(progress_root=self.progress,context=self.context,execute=self.native.execute,prepare_phase=self.native.local,
   check=self.native.check,ready=preparation_ready,clock=self.native.clock,seconds=100000,page_size=16);options.update(opts)
  return run_manifest(self.manifest,**options)
 def test_full_manifest_over_4096_and_old_16mib_queue_capacity_is_not_cap(self):
  # 4,111 eligible lines * 5KiB extra author metadata > former 16MiB selected cap.
  rows=[]
  for i in range(4111):u=unit(i);u['author_padding']='x'*5120;rows.append(u)
  self.write(rows);del rows
  self.assertGreater(self.manifest.stat().st_size,16*1024*1024)
  tracemalloc.start();r=self.run_queue();_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
  self.assertEqual((r['status'],r['completed'],r['unique_units']),('completed',4111,4111),r)
  self.assertEqual(len(self.native.compile_calls),4111);self.assertLessEqual(r['max_page_items'],16)
  self.assertTrue(r['full_manifest_preparation_verified']);self.assertLess(peak,16*1024*1024)
  (self.root/'large-control-metrics.json').write_text(json.dumps(dict(eligible=4111,manifest_bytes=self.manifest.stat().st_size,completed=r['completed'],max_page_items=r['max_page_items'],python_tracemalloc_peak_bytes=peak,real_native_or_gpu_called=False),indent=2)+'\n')
 def test_time_budget_resume_does_not_recompile_success(self):
  self.write(unit(i)for i in range(80))
  r=run_manifest(self.manifest,progress_root=self.progress,context=self.context,execute=self.native.execute,prepare_phase=self.native.local,
   check=self.native.check,ready=preparation_ready,clock=self.native.clock,seconds=50,page_size=16)
  self.assertEqual(r['status'],'partial');self.assertTrue(r['budget_deferred']);self.assertGreater(r['completed'],0)
  successful=set(self.native.artifacts);before=dict(self.native.compile_calls);self.native.elapsed=0
  r2=self.run_queue();self.assertEqual(r2['status'],'completed',r2)
  self.assertTrue(all(self.native.compile_calls[k]==before[k]for k in successful))
  self.assertTrue(all(self.native.probe_calls[k]>=1 for k in successful));self.assertEqual(len(self.native.artifacts),80)
 def test_prior_state_is_not_native_success_authority(self):
  self.write([unit(1)]);r=self.run_queue();self.assertEqual(r['completed'],1)
  self.native.artifacts.clear();self.native.elapsed=0
  r2=self.run_queue();self.assertEqual(r2['completed'],1);self.assertEqual(self.native.compile_calls[unit(1)['recipe_blob_identity']],2)
  self.assertEqual(self.native.probe_calls[unit(1)['recipe_blob_identity']],2)
 def test_worker_exit_zero_is_not_preparation_success(self):
  self.write([unit(1)]);self.native.worker_only=True;r=self.run_queue()
  self.assertEqual((r['status'],r['completed'],r['deferred']),('partial',0,1));self.assertFalse(r['full_manifest_preparation_verified'])
 def test_rejected_deferred_completed_count_separately(self):
  self.write([unit(1),unit(2),unit(3)]);self.native.reject.add(unit(1)['recipe_blob_identity']);self.native.defer.add(unit(2)['recipe_blob_identity'])
  r=self.run_queue();self.assertEqual((r['completed'],r['deferred'],r['rejected']),(1,1,1),r)
 def test_native_protection_stops_and_is_not_source_fallback(self):
  self.write([unit(1)]);self.native.stop=unit(1)['recipe_blob_identity'];r=self.run_queue()
  self.assertEqual((r['status'],r['protection_stops']),('stopped',1));self.assertEqual(r['completed'],0)
 def test_os_guard_preserves_stop(self):
  self.write([unit(1)])
  def check():raise Stopped('lock screen')
  r=run_manifest(self.manifest,progress_root=self.progress,context=self.context,execute=self.native.execute,prepare_phase=self.native.local,check=check,ready=preparation_ready)
  self.assertEqual(r['status'],'stopped');self.assertFalse(self.native.record_calls)
 def test_keyboard_interrupt_stops(self):
  self.write([unit(1)])
  def execute(*a):raise KeyboardInterrupt()
  r=run_manifest(self.manifest,progress_root=self.progress,context=self.context,execute=execute,prepare_phase=self.native.local,check=lambda:None,ready=preparation_ready)
  self.assertEqual(r['status'],'stopped')
 def test_explicit_recipe_budget_is_resumable_not_total_cap(self):
  self.write(unit(i)for i in range(9));r=self.run_queue(max_recipes=3)
  self.assertEqual((r['completed'],r['not_visited']),(3,6));self.assertEqual(r['status'],'partial')
  self.native.elapsed=0;r2=self.run_queue();self.assertEqual(r2['completed'],9);self.assertEqual(len(self.native.artifacts),9)
 def test_no_cross_cache_merge_on_same_recipe_blob(self):
  a=unit(1);b=unit(2);b['recipe_blob_identity']=a['recipe_blob_identity'];self.write([a,b]);r=self.run_queue()
  self.assertEqual(r['unique_units'],2);self.assertEqual(r['completed'],2)
 def test_exact_duplicate_lines_accounted_not_silently_new_units(self):
  a=unit(1);self.write([a,a]);r=self.run_queue();self.assertEqual((r['exact_duplicates'],r['unique_units'],r['completed']),(1,1,1))
 def test_ineligible_and_input_rejection_counts(self):
  self.write([unit(1),unit(2,False),dict(schema=2,record_type='rejected-input',reason='bad original ref')]);r=self.run_queue()
  self.assertEqual((r['policy_excluded'],r['index_rejected'],r['completed']),(1,1,1));self.assertFalse(r['full_manifest_preparation_verified'])
 def test_record_phase_never_marks_native_preparation_done(self):
  self.write([unit(1)]);r=self.run_queue(phase='record');self.assertEqual((r['completed'],r['recorded'],r['deferred']),(0,1,1))
  self.assertFalse(r['full_manifest_preparation_verified'])
 def test_index_protection_resume_keeps_all_records(self):
  self.write(unit(i)for i in range(300));calls=[0]
  def once():
   calls[0]+=1
   if calls[0]>100:raise Stopped('index pressure stop')
  r=run_manifest(self.manifest,progress_root=self.progress,context=self.context,execute=self.native.execute,
    prepare_phase=self.native.local,check=once,ready=preparation_ready,seconds=100000)
  self.assertEqual(r['status'],'stopped');self.native.elapsed=0
  r2=self.run_queue();self.assertEqual((r2['manifest_records'],r2['policy_eligible_records'],r2['completed']),(300,300,300))
 def test_context_change_is_separate_scheduling_domain(self):
  self.write([unit(1)]);a=self.run_queue();self.context['options']='f'*64;self.native.elapsed=0;b=self.run_queue()
  self.assertNotEqual(a['job_id'],b['job_id'])
 def test_tampered_state_payload_is_not_trusted(self):
  self.write([unit(1)]);r=self.run_queue();db=self.progress/'incremental-v2'/r['job_id']/'queue.sqlite'
  con=sqlite3.connect(db);con.execute("UPDATE units SET payload='{}'");con.commit();con.close();self.native.elapsed=0
  r2=self.run_queue();self.assertEqual(r2['status'],'failed');self.assertIn('payload changed',r2['error'])
 def test_deferred_library_phase_does_not_become_success(self):
  self.write([unit(1)]);self.native.local=lambda phase,guard:dict(status='record')
  r=self.run_queue();self.assertEqual((r['completed'],r['deferred']),(0,1))
 def test_empty_manifest_no_fabricated_preparation(self):
  self.write([]);r=self.run_queue();self.assertEqual(r['completed'],0)
 def test_page_max_is_memory_window_not_full_count(self):
  self.write([unit(1)])
  with self.assertRaises(ValueError):self.run_queue(page_size=65)

if __name__=='__main__':unittest.main()
