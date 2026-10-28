"""Formal preparation vs QA guards; authored state, no game or OS pressure."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from launcher_runtime_profile import RuntimeProfile,preparation_memory_policy
from local_metal_builder import Safety,Stopped,TOTAL_TIMEOUT
from launcher_game_pso import StartupHealth
from qa_memory_policy import MemoryPolicy
import launcher_local_aot as front

def profile(root):
 return RuntimeProfile(bundle=SimpleNamespace(validation_work={}),data_root=root,
  work_root=root,gpu_base=root,gpu_root=root,game_root=root,app0=root,user=root)
def forbidden():raise AssertionError('application invoked isolated-test observation')

class ApplicationPolicy(unittest.TestCase):
 def test_actual_profile_selects_application_and_private_default_keeps_qa(self):
  self.assertIs(preparation_memory_policy(None),MemoryPolicy)
  self.assertIsNone(preparation_memory_policy(profile(Path('/authored'))))
 def test_environment_or_lookalike_cannot_select_application_scope(self):
  with patch.dict(os.environ,{'BB_DISABLE_MEMORY_GUARD':'1','BB_ARM_LAUNCHER_COMPAT':'1'}):
   self.assertIs(preparation_memory_policy(None),MemoryPolicy)
   for obj in({},True,SimpleNamespace(formal=True)):
    with self.assertRaises(ValueError):preparation_memory_policy(obj)
 def test_application_builder_skips_qa_observation_but_keeps_deadline_and_exclusion(self):
  clock=[0];calls=[];observer=SimpleNamespace(system=forbidden)
  g=Safety(observer,lambda:calls.append('exclusion'),None,clock=lambda:clock[0])
  g.check(preflight=True);self.assertEqual(calls,['exclusion','exclusion'])
  clock[0]=TOTAL_TIMEOUT+1
  with self.assertRaisesRegex(Stopped,'total build timeout'):g.check()
 def test_private_builder_still_stops_on_two_gib_growth(self):
  sample=dict(locked=False,pressure=1,swap_mib=0)
  g=Safety(SimpleNamespace(system=lambda:dict(sample)),lambda:None,MemoryPolicy)
  sample['swap_mib']=2049
  with self.assertRaisesRegex(Stopped,'2GiB'):g.check()
 def test_application_startup_health_does_not_poll_or_stop_for_test_metrics(self):
  t=[0];health=StartupHealth(forbidden,None,clock=lambda:t[0])
  t[0]=100;health.check(force=True);self.assertEqual(health.stats['observations'],0)
  t[0]=1
  with self.assertRaisesRegex(ValueError,'monotonic'):health.check()
 def test_private_startup_health_still_stops_on_swap_growth(self):
  sample=dict(locked=False,pressure=1,swap_mib=0);t=[0]
  health=StartupHealth(lambda:dict(sample),MemoryPolicy,clock=lambda:t[0])
  sample['swap_mib']=2049;t[0]=1
  with self.assertRaisesRegex(RuntimeError,'2GiB'):health.check(force=True)
 def call_front(self,scope,samples,*,bad_capture=False):
  local=lambda **kw:({},dict(status='prepared'))
  game=lambda **kw:dict(status='completed',capture_environment={'BB_THD_TOKEN':'bad'}if bad_capture else{})
  return front._prepared('/authored/cpu',[],{'BB_GPU_PROCESS':'/authored/gpu','BB_GPU_USER_DIR':str(scope.work_root)if scope else'/authored/gpu'},
   scope.work_root if scope else Path('/authored'),{}, {},'auto',
   lambda **kw:({},dict(token='a'*64)),local,lambda:next(samples),Path('/authored'),None,
   game,lambda **kw:({},dict(status='completed')),scope)
 def test_actual_front_all_preparation_boundaries_accept_large_swap_and_lock_in_application(self):
  for pressure,locked in((2,False),(4,False),(1,True)):
   with self.subTest(pressure=pressure,locked=locked),tempfile.TemporaryDirectory()as raw:
    scope=profile(Path(raw));samples=iter([dict(locked=locked,pressure=pressure,swap_mib=0),dict(locked=locked,pressure=pressure,swap_mib=16384)])
    with patch('texture_binding_request.apply',side_effect=lambda env:(env,None)):
     _,r=self.call_front(scope,samples)
    self.assertEqual(r['status'],'prepared')
 def test_private_front_still_rejects_same_two_gib_growth(self):
  samples=iter([dict(locked=False,pressure=1,swap_mib=0),dict(locked=False,pressure=2,swap_mib=2049)])
  with self.assertRaisesRegex(RuntimeError,'protection after local Metal'):self.call_front(None,samples)
 def test_application_does_not_weaken_capture_environment_admission(self):
  with tempfile.TemporaryDirectory()as raw:
   samples=iter([dict(locked=False,pressure=1,swap_mib=0)])
   with self.assertRaisesRegex(ValueError,'unexpected runtime capture'):self.call_front(profile(Path(raw)),samples,bad_capture=True)

if __name__=='__main__':unittest.main()
