"""Preparation deadlines and code admission; no game/user data is used."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ctypes,hashlib,json,os,sys,tempfile,unittest
TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import local_metal_builder as builder
import qa_build_guard as exclusion
from prepare_game_psos import LoadedGpuAdmission,Refused
from qa_memory_policy import MemoryPolicy

class NativeInventory:
 def __init__(self,paths):self.paths=paths;self.denied={};self.calls=0
 def proc_listallpids(self,buffer,size):
  self.calls+=1
  if buffer is None:return len(self.paths)
  for i,pid in enumerate(self.paths):buffer[i]=pid
  return len(self.paths)
 def proc_pidpath(self,pid,buffer,size):
  if pid in self.denied:return 0
  buffer.value=self.paths[pid].encode();return len(buffer.value)
 def proc_name(self,pid,buffer,size):
  buffer.value=self.denied.get(pid,'').encode();return len(buffer.value)

class InventoryTests(unittest.TestCase):
 def test_each_boundary_reads_current_inventory_including_prefixes(self):
  api=NativeInventory({1:'/sbin/launchd',2:'/private/bb-probe-metal',3:'/private/bb-game-arm64-test',4:'/private/python3'})
  self.assertEqual(exclusion._native_games(api),[(2,api.paths[2]),(3,api.paths[3])])
  api.paths.pop(2);api.paths.pop(3)
  self.assertEqual(exclusion._native_games(api),[]);self.assertEqual(api.calls,4)
 def test_unreadable_game_does_not_become_empty_inventory(self):
  api=NativeInventory({1:'/private/bb-probe-metal'});api.denied[1]='bb-probe-metal'
  self.assertIsNone(exclusion._native_games(api))
 def test_unavailable_native_inventory_uses_original_process_query(self):
  with patch.object(exclusion,'_native_games',return_value=None),patch.object(exclusion.subprocess,'check_output',return_value=' 11 /private/bb-probe-metal\n')as original:
   self.assertEqual(exclusion.running_games(),[(11,'/private/bb-probe-metal')]);original.assert_called_once()
 def test_valid_empty_native_result_does_not_spawn_process(self):
  with patch.object(exclusion,'_native_games',return_value=[]),patch.object(exclusion.subprocess,'check_output',side_effect=AssertionError('unneeded ps')):
   self.assertEqual(exclusion.running_games(),[])

class DeadlineTests(unittest.TestCase):
 def test_idle_other_phases_do_not_consume_later_build_deadline(self):
  now=[0];g=builder.Safety(SimpleNamespace(system=lambda:None),lambda:None,None,clock=lambda:now[0])
  now[0]=builder.TOTAL_TIMEOUT+100
  with g.build_scope():
   g.check();now[0]+=builder.TOTAL_TIMEOUT+1
   with self.assertRaises(builder.BudgetExpired):g.check()
  with g.build_scope():g.check()
 def test_new_deadline_keeps_original_cumulative_qa_memory_policy(self):
  now=[0];sample=dict(locked=False,pressure=1,swap_mib=100)
  g=builder.Safety(SimpleNamespace(system=lambda:dict(sample)),lambda:None,MemoryPolicy,clock=lambda:now[0]);original=g.policy
  with g.build_scope():g.check()
  now[0]=builder.TOTAL_TIMEOUT+100;sample['swap_mib']=2200
  with self.assertRaisesRegex(builder.Stopped,'2GiB'):
   with g.build_scope():g.check()
  self.assertIs(g.policy,original)

class LoadedCodeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name).resolve()/'libbbgpu.dylib';self.path.write_bytes(b'authored library bytes')
  self.identity=dict(library=str(self.path),library_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())
  self.admission=LoadedGpuAdmission();self.loaded={str(self.path)}
 def test_same_dependency_bytes_read_once_but_each_recipe_has_live_pin(self):
  for _ in range(40):self.assertTrue(self.admission.verify(self.loaded,self.identity)['owned_post_worker_sha_verified'])
  self.assertEqual(self.admission.stats,dict(full_hashes=1,hashed_bytes=self.path.stat().st_size,pin_reuses=39))
 def test_byte_change_with_restored_mtime_cannot_reuse_digest(self):
  self.admission.verify(self.loaded,self.identity);before=self.path.stat();self.path.write_bytes(b'changed library bytes!');os.utime(self.path,ns=(before.st_atime_ns,before.st_mtime_ns))
  with self.assertRaisesRegex(Refused,'SHA mismatch'):self.admission.verify(self.loaded,self.identity)
 def test_same_bytes_replaced_inode_rehashed_and_wrong_loaded_library_refused(self):
  self.admission.verify(self.loaded,self.identity);p=self.path.with_name('other');p.write_bytes(self.path.read_bytes());os.replace(p,self.path)
  self.admission.verify(self.loaded,self.identity);self.assertEqual(self.admission.stats['full_hashes'],2)
  with self.assertRaisesRegex(Refused,'load edge changed'):self.admission.verify({str(p)},self.identity)
 def test_linked_or_missing_dependency_never_reuses_admission(self):
  self.admission.verify(self.loaded,self.identity);other=self.path.with_name('hardlink');os.link(self.path,other)
  with self.assertRaisesRegex(Refused,'owned unchanged'):self.admission.verify(self.loaded,self.identity)
  other.unlink();self.path.unlink()
  with self.assertRaises(OSError):self.admission.verify(self.loaded,self.identity)

class DispatchTests(unittest.TestCase):
 def test_only_automatic_time_budget_retains_source_fallback(self):
  import local_aot_policy as policy
  with tempfile.TemporaryDirectory()as raw:
   p=Path(raw).resolve();gpu=p/'state/gpu';(gpu/'local-metal'/('a'*64)/'requests').mkdir(parents=True)
   with patch.object(policy,'P',p):
    for kind,mode in [('time-budget','auto'),('protection','auto'),('time-budget','load')]:
     env,r=policy.prepare(mode=mode,gpu_root=gpu,cohort='a'*64,engine='arm64',graphics=True,toolchain_probe=lambda:True,
       builder=lambda *a,**kw:dict(status='stopped',stop_kind=kind,error='authored stop'))
     if (kind,mode)==('time-budget','auto'):
      self.assertEqual(env['BB_MTL_LOCAL_AOT_MODE'],'record');self.assertEqual(r['reason'],'preparation-budget-deferred');self.assertNotIn('BB_MTL_LOCAL_AOT_BUILD_ID',env)
     else:self.assertEqual((env,r['status']),({},'stopped'))
 def test_pending_queue_does_not_restart_full_library_work_or_publish_ready(self):
  import test_launcher_local_aot as fixtures
  import launcher_local_aot as front
  f=fixtures.Startup();f.setUp();self.addCleanup(f.tearDown);calls=[]
  def local(**kw):calls.append(kw);return {'BB_MTL_LOCAL_AOT_MODE':'record'},dict(status='record',built=0)
  _,r=front.prepare_launch(str(f.inputs/'runtime'),f.args,f.env,project=f.root,archive_prepare=f.identity,
     local_prepare=local,observe=lambda:dict(locked=False,pressure=1,swap_mib=100),
     fixed_helper_prepare=lambda **kw:({},dict(status='completed')),
     game_prepare=lambda **kw:dict(status='partial',preparation=dict(status='partial',budget_deferred=True,error='preparation time budget',completed=0,not_visited=1597)))
  self.assertEqual(len(calls),1);self.assertNotIn('restart_schedule_published',r['game_pso'])
  self.assertEqual(r['game_pso']['preparation']['not_visited'],1597)
 def test_application_queue_can_finish_large_registration_private_budget_is_unchanged(self):
  import test_launcher_game_pso_automatic as fixtures
  f=fixtures.Entry();f.setUp();self.addCleanup(f.tearDown)
  f.component.update(automatic_capture_schema=1,worker=f.stage/'worker',worker_sha256='a'*64,layout=f.stage/'layout',layout_sha256='b'*64)
  context=f.completed_static_context();seen=[]
  def candidates(**kw):r=f.candidates(**kw);r['eligible']=1;return r
  def run(config):seen.append(config);return dict(status='partial',budget_deferred=True)
  f.prepare(prepared_inputs=context,capture_configure=f.configured_capture,candidate_builder=candidates,runner=run)
  self.assertEqual(seen[-1]['seconds'],600)
  # Internal dispatch seam only; actual application identity is independently
  # tested by the production RuntimeProfile/path/component regressions.
  with patch('launcher_runtime_profile.preparation_memory_policy',return_value=None):
   f.prepare(prepared_inputs=context,capture_configure=f.configured_capture,candidate_builder=candidates,runner=run)
  self.assertEqual(seen[-1]['seconds'],7200)

class AdoptionTests(unittest.TestCase):
 def fixture(self):
  import test_local_metal_builder as fixtures
  if not hasattr(fixtures.BuilderTests,'top'):fixtures.BuilderTests.setUpClass()
  f=fixtures.BuilderTests();f.setUp();self.addCleanup(f.doCleanups)
  return f
 def test_retained_libraries_check_environment_at_both_pass_boundaries(self):
  f=self.fixture()
  for i in range(40):f.export(source=('kernel void main0() { /* %d */ }\n'%i).encode())
  first=f.run_builder();self.assertEqual(first['status'],'passed')
  f.select_cohort('b'*64)
  for i in range(40):f.export(source=('kernel void main0() { /* %d */ }\n'%i).encode())
  original=builder.NativeToolchain.unchanged;checks=[]
  def checked(chain,guard):checks.append(True);return original(chain,guard)
  with patch.object(builder.NativeToolchain,'unchanged',checked):second=f.run_builder()
  self.assertEqual(second['status'],'passed');self.assertEqual(second['reused_products'],40);self.assertEqual(second['built'],0)
  self.assertEqual(len(checks),2)
 def test_changed_environment_during_adoption_cannot_publish_success(self):
  f=self.fixture();f.export();first=f.run_builder();self.assertEqual(first['status'],'passed')
  f.select_cohort('b'*64);f.export();original=builder.publish_new_file
  def publish(src,dst):
   original(src,dst)
   if str(dst).endswith('.metallib'):f.runner.tools['metal'].write_bytes(b'changed during adoption')
  with patch.object(builder,'publish_new_file',publish):second=f.run_builder()
  self.assertNotEqual(second['status'],'passed');self.assertIn('changed',second['error'])
 def test_progress_is_bounded_and_never_authorizes_missing_output(self):
  f=self.fixture();f.export();updates=[]
  chain=builder.NativeToolchain(f.runner,fingerprint=f.runner.fingerprint)
  result=builder.build(f.root,f.cohort,enabled=True,guard=f.guard,toolchain=chain,progress=updates.append)
  self.assertEqual(result['status'],'passed');self.assertEqual(updates[0],dict(kind='libraries',done=0,total=1));self.assertEqual(updates[-1],dict(kind='libraries',done=1,total=1))

if __name__=='__main__':unittest.main()
