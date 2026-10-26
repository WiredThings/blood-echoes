"""Author-only frame/lifecycle controls: no process, Metal, or game launch."""
from pathlib import Path
import copy,json,tempfile,unittest
from unittest.mock import patch
import game_pso_batch as b

class Controls(unittest.TestCase):
 def frame(self,**updates):
  args=dict(phase='prepare',allow_compile=False,sequence=1,packet='/private/packet.json',packet_sha256='a'*64,scratch='/private/source',ordinal=8);args.update(updates);return b.request_frame(**args)[0]
 def segment(self,f,code=0):
  mark={k:f[k]for k in('schema','sequence','request_id','packet_sha256')}
  return ['GAME_PSO_BATCH_BEGIN '+json.dumps(mark)+'\n','GAME_PSO_RESULT {}\n','GAME_PSO_BATCH_END '+json.dumps(dict(mark,exit=code))+'\n']
 def test_actual_exact_capability_only(self):
  self.assertTrue(b.capability('GAME_PSO_BATCH_CAPABILITY '+json.dumps(b.CAPABILITY)))
  self.assertFalse(b.capability('usage: old worker'))
  wrong=dict(b.CAPABILITY,schema=True);self.assertFalse(b.capability('GAME_PSO_BATCH_CAPABILITY '+json.dumps(wrong)))
 def test_duplicate_capability_not_admitted(self):
  s='GAME_PSO_BATCH_CAPABILITY '+json.dumps(b.CAPABILITY)+'\n';self.assertFalse(b.capability(s+s))
 def test_frame_hash_binds_packet_policy_sequence(self):
  a=self.frame();c=self.frame(allow_compile=True);self.assertNotEqual(a['request_id'],c['request_id']);self.assertNotEqual(a['request_id'],self.frame(packet_sha256='b'*64)['request_id'])
 def test_scope_never_mixes_phase_policy_or_effective_environment(self):
  env=dict(BB_MTL_LOCAL_AOT_MODE='load',BB_MTL_LOCAL_AOT_BUILD_ID='a'*64,HOME='/case/one',TMPDIR='/case/one/tmp')
  a=b.scope_identity('prepare',False,env)
  self.assertEqual(a,b.scope_identity('prepare',False,dict(env,HOME='/case/two',TMPDIR='/case/two/tmp')))
  self.assertNotEqual(a,b.scope_identity('record',False,env));self.assertNotEqual(a,b.scope_identity('prepare',True,env))
  self.assertNotEqual(a,b.scope_identity('prepare',False,dict(env,BB_MTL_LOCAL_AOT_BUILD_ID='b'*64)))
 def test_more_than16_or_boolean_sequence_rejected(self):
  for sequence in(0,17,True):
   with self.assertRaises(ValueError):self.frame(sequence=sequence)
 def test_record_cannot_native_compile(self):
  with self.assertRaises(ValueError):self.frame(phase='record',allow_compile=True)
 def test_own_response_binds_native_result(self):
  f=self.frame();self.assertEqual(b.parse_segment(self.segment(f),f),0)
 def test_partial_recipe_is65(self):
  f=self.frame();self.assertEqual(b.parse_segment(self.segment(f,65),f),65)
 def test_other_request_response_rejected(self):
  f=self.frame();other=self.frame(packet_sha256='c'*64)
  with self.assertRaises(ValueError):b.parse_segment(self.segment(other),f)
 def test_missing_or_duplicate_native_result_rejected(self):
  f=self.frame();lines=self.segment(f)
  for altered in([lines[0],lines[2]],[lines[0],lines[1],lines[1],lines[2]]):
   with self.assertRaises(ValueError):b.parse_segment(altered,f)
 def test_result_outside_frame_rejected(self):
  f=self.frame();lines=self.segment(f)
  with self.assertRaises(ValueError):b.parse_segment([lines[1],lines[0],lines[2]],f)
 def test_reserved_restart_codes_rejected(self):
  f=self.frame()
  for code in(75,76,True):
   with self.assertRaises(ValueError):b.parse_segment(self.segment(f,code),f)
 def test_partial_batch_cannot_claim_success(self):
  value=dict(schema=1,processed=2,failed=1,phase='prepare',status='partial');lines=['GAME_PSO_BATCH_DONE '+json.dumps(value)]
  b.verify_done(lines,phase='prepare',processed=2,failed=1,exit_code=65)
  with self.assertRaises(ValueError):b.verify_done(lines,phase='prepare',processed=2,failed=1,exit_code=0)
 def test_final_phase_and_counts_must_match(self):
  value=dict(schema=1,processed=2,failed=0,phase='record',status='completed')
  with self.assertRaises(ValueError):b.verify_done(['GAME_PSO_BATCH_DONE '+json.dumps(value)],phase='prepare',processed=2,failed=0,exit_code=0)
 def test_boolean_summary_schema_rejected(self):
  value=dict(schema=True,processed=2,failed=0,phase='prepare',status='completed')
  with self.assertRaises(ValueError):b.verify_done(['GAME_PSO_BATCH_DONE '+json.dumps(value)],phase='prepare',processed=2,failed=0,exit_code=0)
 def fake_worker(self,root,fail=()):
  outer=self;state=dict(n=0,failed=0)
  class Proc:
   pid=123456789;returncode=None
   def poll(self):return self.returncode
  proc=Proc()
  class Pipe:
   def write(self,raw):
    f=json.loads(raw);code=65 if f['sequence']in fail else 0;state['n']+=1;state['failed']+=code!=0
    with(root/'console.log').open('a')as out:out.writelines(outer.segment(f,code))
    return len(raw)
   def flush(self):pass
   def close(self):
    proc.returncode=65 if state['failed']else 0
    value=dict(schema=1,processed=state['n'],failed=state['failed'],phase='prepare',status='partial'if state['failed']else'completed')
    with(root/'console.log').open('a')as out:out.write('GAME_PSO_BATCH_DONE '+json.dumps(value)+'\n')
  proc.stdin=Pipe()
  with patch.object(b.subprocess,'Popen',return_value=proc):
   w=b.Worker(executable='/private/worker',phase='prepare',allow_compile=False,environment={},root=root,check=lambda:None,clock=lambda:0,start=0,seconds=600,wait=lambda *args:None)
  return w
 def test_same_process16_sequential_requests_only_one_input_inflight(self):
  with tempfile.TemporaryDirectory()as tmp:
   w=self.fake_worker(Path(tmp));pids=[]
   for n in range(16):pids.append(w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=n)['pid'])
   self.assertEqual(len(set(pids)),1);self.assertEqual(w.count,16)
   with self.assertRaises(ValueError):w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=17)
   w.close()
 def test_protection_exit_between_requests_is_stop_not_cache_miss(self):
  with tempfile.TemporaryDirectory()as tmp:
   w=self.fake_worker(Path(tmp));w.proc.returncode=79
   with self.assertRaises(b.Stopped):w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=1)
   w.close(abort=True)
 def test_original_guard_stop_is_not_accepted_as_ready(self):
  with tempfile.TemporaryDirectory()as tmp:
   w=self.fake_worker(Path(tmp))
   def stopped():raise b.Stopped('author lock')
   w.check=stopped
   with self.assertRaises(b.Stopped):w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=1)
   self.assertEqual(w.count,0);w.close(abort=True)
 def test_whole_preparation_deadline_not_reset_per_recipe(self):
  with tempfile.TemporaryDirectory()as tmp:
   w=self.fake_worker(Path(tmp));w.clock=lambda:601
   with self.assertRaises(b.Deferred):w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=1)
   self.assertEqual(w.count,0);w.close(abort=True)
 def test_per_recipe_failure_preserved_not_promoted_to_batch_success(self):
  with tempfile.TemporaryDirectory()as tmp:
   w=self.fake_worker(Path(tmp),fail=(2,));codes=[w.request(packet='/private/p.json',packet_sha256='a'*64,scratch='/private/s',ordinal=n)['exit']for n in range(3)]
   self.assertEqual(codes,[0,65,0]);self.assertEqual(w.failed,1);w.close()

# Full controller seam, with authored worker/selector/queue only. It starts no
# subprocess and does not claim native Metal validation from synthetic outcomes.
class ControllerControls(unittest.TestCase):
 def exercise(self,requests,*,batch=True,failed_ordinal=None,stop_discovery=False):
  import prepare_game_psos as prep,game_pso_queue as queue,hashlib,types
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp).resolve();state=p/'state';state.mkdir();run=state/'run';run.mkdir();gpu=state/'gpu';gpu.mkdir();artifact=gpu/'artifact';artifact.mkdir();worker=p/'worker';worker.write_bytes(b'author worker');lib=p/'libbbgpu.dylib';lib.write_bytes(b'author library');manifest=p/'manifest';manifest.write_text('author');layout=p/'layout';layout.write_text('author')
   library_sha=hashlib.sha256(lib.read_bytes()).hexdigest();spawned=[]
   def outcome():return dict(native_pipeline_ready=True,artifact_publication_observed=True,source_fallback=False,functions=[dict(actual_route='local-metallib',identity='a'*64)],allow_compile=False,artifact_counts=dict(attempts=1,hits=1,built=0,compile_attempts=0,deferred=0,failed=0,critical=0,unavailable=0))
   def lines():return ['GAME_PSO_RESULT '+json.dumps(outcome())+'\n','Metal game PSO artifact event: '+json.dumps(dict(state=1,archive_sha256='b'*64))+'\n']
   class Batch:
    def __init__(self,**kw):
     self.phase=kw['phase'];self.log=kw['root']/'console.log';self.log.write_text('author batch');self.proc=types.SimpleNamespace(pid=100+len(spawned));self.loaded={str(lib)};self.count=0;self.failed=0;spawned.append(kw)
    def request(self,**kw):
     self.count+=1;code=65 if kw['ordinal']==failed_ordinal else 0;self.failed+=code!=0
     return dict(exit=code,lines=lines(),sequence=self.count,request_id='c'*64)
    def close(self,**kw):pass
   def popen(command,**kw):
    spawned.append(dict(command=command));pid=100+len(spawned);kw['stdout'].write(f'dyld[{pid}]: {lib}\n'+''.join(lines()));kw['stdout'].flush()
    return types.SimpleNamespace(pid=pid,returncode=0,poll=lambda:0)
   def freeze(unit,layout,root,cohort,allow_compile,**kw):
    prep.new_bytes(root,'packet.json',b'{"author":true}\n');return {},{}
   def orchestrate(manifest,*,execute,prepare_phase,**kw):
    prepare_phase('prepare',lambda:None);rows=[]
    for n,(phase,allow)in enumerate(requests,1):
     if phase=='record':prepare_phase('record',lambda:None)
     rows.append(execute(dict(recipe_blob_identity=f'{n:064x}'),phase,allow,n))
    prepare_phase('verify',lambda:None)
    return dict(status='partial'if any(row['exit'] for row in rows)else'completed',completed=sum(row['exit']==0 for row in rows))
   closed=[];observer=types.SimpleNamespace(system=lambda:dict(locked=False,pressure=1,swap_mib=0),close=lambda:closed.append(True))
   class Policy:
    MANIFEST={}
    def __init__(self,seed):pass
    def observe(self,*args):return None
   services=dict(batch_capability=batch,require_no_games=lambda:None,memory_policy=Policy,resolve_identity=lambda *args:dict(library=str(lib),library_sha256=library_sha),load_layout=lambda *args:{},observer_factory=lambda:observer,audit_stage=None,audit_key_profile=None,local_prepare=lambda **kw:({'BB_MTL_LOCAL_AOT_MODE':'record'if kw['mode']=='record'else'load','BB_MTL_LOCAL_AOT_BUILD_ID':'e'*64},dict(status='prepared')),builder_stopped=RuntimeError)
   if stop_discovery:
    class Services(dict):
     def get(self,key,default=None):
      if key=='batch_capability':raise queue.Stopped('author discovery pressure')
      return super().get(key,default)
    services=Services(services)
   config=dict(project=p,run_root=run,gpu_root=gpu,artifact_cache_dir=artifact,worker=worker,manifest=manifest,layout=layout,source_root=p,cohort='f'*64,library_sha256=library_sha,layout_sha256='d'*64,environment={})
   with patch.object(queue,'run_manifest',side_effect=orchestrate),patch.object(prep,'freeze',side_effect=freeze),patch.object(b,'Worker',Batch),patch.object(prep.subprocess,'Popen',side_effect=popen):result=prep.prepare(config,services=services)
   self.assertTrue(closed);return result,spawned
 def test_controller17_recipes_become_two_bounded_workers(self):
  r,spawned=self.exercise([('prepare',False)]*17);self.assertEqual((r['completed'],len(spawned),r['native_batch']['recipes']),(17,2,17))
 def test_controller_phase_and_policy_switch_start_new_worker(self):
  r,spawned=self.exercise([('prepare',False),('prepare',True),('record',False)]);self.assertEqual(len(spawned),3);self.assertEqual([v['phase']for v in spawned],['prepare','prepare','record'])
 def test_old_component_keeps_original_single_item_cli(self):
  r,spawned=self.exercise([('prepare',False)]*3,batch=False);self.assertEqual(len(spawned),3);self.assertTrue(r['native_batch']['legacy_fallback']);self.assertTrue(all('--batch'not in v['command']for v in spawned))
 def test_capability_discovery_stop_still_closes_original_observer(self):
  r,spawned=self.exercise([('prepare',False)],stop_discovery=True);self.assertEqual(r['status'],'stopped');self.assertEqual(spawned,[]);self.assertFalse(r['full_manifest_preparation_verified'])
 def test_partial_child_not_hidden_in_controller_result(self):
  r,spawned=self.exercise([('prepare',False)]*3,failed_ordinal=2);self.assertEqual((r['status'],r['completed'],r['native_batch']['failed_recipes']),('partial',2,1))

class CapabilityDataRootControls(unittest.TestCase):
 def project(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);p=Path(tmp.name).resolve();(p/'state').mkdir();return p
 def answer(self,command,code=0):
  import subprocess
  return subprocess.CompletedProcess(command,code,('GAME_PSO_BATCH_CAPABILITY '+json.dumps(b.CAPABILITY)+'\n').encode(),None)
 def test_native_probe_has_fresh_private_state_and_clean_environment(self):
  import os,subprocess
  p=self.project();seen=[];checks=[]
  def runner(command,**kw):
   env=kw['env'];self.assertEqual(set(env),{'PATH','HOME','TMPDIR','BB_GPU_USER_DIR'});self.assertEqual(env['PATH'],'/usr/bin:/bin:/usr/sbin:/sbin');root=Path(env['BB_GPU_USER_DIR']);self.assertEqual(root.parent,p/'state');self.assertEqual(root,root.resolve(strict=True));self.assertEqual(root,Path(env['HOME']));self.assertEqual(root,Path(env['TMPDIR']));self.assertTrue(root.is_dir());self.assertFalse(root.is_symlink());self.assertEqual(root.stat().st_uid,os.getuid());self.assertEqual(kw['stdin'],subprocess.DEVNULL);self.assertEqual(kw['stdout'],subprocess.PIPE);self.assertEqual(kw['stderr'],subprocess.DEVNULL);seen.append(root);return self.answer(command)
  with patch.dict(os.environ,{'BB_NET_SECRET':'author-secret','AUTH_TOKEN':'author-token'}):self.assertTrue(b.probe_capability('/private/worker',p,lambda:checks.append(1),runner=runner))
  self.assertEqual(len(checks),2);self.assertEqual(len(seen),1);self.assertFalse(seen[0].exists());self.assertEqual(list((p/'state').iterdir()),[])
 def test_exit78_cannot_grant_capability_even_with_valid_text(self):
  p=self.project()
  for code in(64,78):self.assertFalse(b.probe_capability('/private/worker',p,lambda:None,runner=lambda command,**kw:self.answer(command,code)))
  self.assertEqual(list((p/'state').iterdir()),[])
 def test_protection_exit_preserved_and_private_root_removed(self):
  p=self.project()
  for code in(70,79):
   with self.assertRaises(b.Stopped):b.probe_capability('/private/worker',p,lambda:None,runner=lambda command,**kw:self.answer(command,code))
  self.assertEqual(list((p/'state').iterdir()),[])
 def test_interrupted_post_probe_check_still_removes_private_root(self):
  p=self.project();count=[]
  def check():
   count.append(1)
   if len(count)==2:raise b.Stopped('author locked')
  with self.assertRaises(b.Stopped):b.probe_capability('/private/worker',p,check,runner=lambda command,**kw:self.answer(command))
  self.assertEqual(list((p/'state').iterdir()),[])
 def test_timeout_cleanup_uses_original_bounded_runner_contract(self):
  import subprocess
  p=self.project()
  def runner(command,**kw):raise subprocess.TimeoutExpired(command,kw['timeout'])
  with self.assertRaises(subprocess.TimeoutExpired):b.probe_capability('/private/worker',p,lambda:None,runner=runner)
  self.assertEqual(list((p/'state').iterdir()),[])
 def test_linked_state_root_rejected_before_probe(self):
  p=self.project();(p/'state').rmdir();outside=p/'outside';outside.mkdir();(p/'state').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(ValueError):b.probe_capability('/private/worker',p,lambda:None,runner=lambda command,**kw:self.answer(command))

if __name__=='__main__':unittest.main(verbosity=2)
