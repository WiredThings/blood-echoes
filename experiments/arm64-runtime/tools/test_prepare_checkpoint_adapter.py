"""Actual prepare() closure/safety translation, injected author services only."""
from pathlib import Path
import hashlib,json,sys,tempfile,unittest
from unittest.mock import patch
E=Path(__file__).resolve().parent
sys.path.insert(0,str(E))
import prepare_game_psos as p
import game_pso_queue as q
class BuilderStopped(RuntimeError):pass
class Observer:
 def system(self):return dict(pressure=1,locked=False,swap_mib=0)
 def close(self):pass
class Policy:
 MANIFEST={'author':True}
 def __init__(self,*args):pass
 def observe(self,*args):return None
class Anchor:
 def unchanged(self,guard):guard.check(preflight=True);return True
 def close(self):pass
class Session:
 def __init__(self,*args):self.stats={};self.direct_stop=False
 def begin_validation(self,root,cohort,guard):
  guard.check(preflight=True)
  if self.direct_stop:raise BuilderStopped('author fixed query timeout')
  return Anchor()
 def invalidate(self,*args):pass
 def close(self):pass
class Controls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.P=Path(self.temp.name).resolve();state=self.P/'state';state.mkdir();self.gpu=state/'gpu';self.gpu.mkdir();self.artifact=self.gpu/'artifact';self.artifact.mkdir();self.run=state/'run';self.run.mkdir()
  worker=self.P/'worker';worker.write_bytes(b'AUTHOR-NOT-EXECUTED');library=self.P/'library';library.write_bytes(b'AUTHOR-NOT-LOADED');layout=state/'layout';layout.write_text('{}');manifest=state/'manifest';manifest.write_text('{}\n')
  self.session=Session();self.config=dict(project=self.P,worker=worker,library_sha256=p.sha(library.read_bytes()),manifest=manifest,layout=layout,layout_sha256=p.sha(layout.read_bytes()),source_root=self.P,cohort='a'*64,gpu_root=self.gpu,run_root=self.run,artifact_cache_dir=self.artifact,seconds=10,environment={})
  self.services=dict(require_no_games=lambda:None,resolve_identity=lambda *args:dict(library=str(library),library_sha256=p.sha(library.read_bytes())),load_layout=lambda *args:{},observer=Observer(),memory_policy=Policy,audit_stage=lambda *args:None,audit_key_profile=lambda *args:None,local_prepare=lambda **kwargs:({'BB_MTL_LOCAL_AOT_MODE':'load'},dict(status='prepared')),builder_stopped=BuilderStopped,builder_session_factory=lambda *args:self.session)
 def tearDown(self):self.temp.cleanup()
 def drive(self,checkpoint,error_type):
  def run_manifest(*args,prepare_phase,**kwargs):
   prepare_phase('prepare',lambda:None)
   if checkpoint=='verify':prepare_phase('snapshot',lambda:None)
   calls=0
   def guard():
    nonlocal calls
    calls+=1
    if calls==2:raise error_type('author protected boundary')
   try:prepare_phase(checkpoint,guard)
   except q.Stopped:return dict(status='stopped',completed=0,protection_stops=1)
   except q.Deferred:return dict(status='partial',completed=0,budget_deferred=True,protection_stops=0)
   raise AssertionError('safety exception missing')
  with patch.object(q,'run_manifest',side_effect=run_manifest):return p.prepare(self.config,services=self.services)
 def test_snapshot_memory_stop_type_preserved(self):self.assertEqual(self.drive('snapshot',q.Stopped)['status'],'stopped')
 def test_snapshot_budget_type_preserved(self):self.assertTrue(self.drive('snapshot',q.Deferred)['budget_deferred'])
 def test_verify_memory_stop_type_preserved(self):self.assertEqual(self.drive('verify',q.Stopped)['status'],'stopped')
 def test_verify_budget_type_preserved(self):self.assertTrue(self.drive('verify',q.Deferred)['budget_deferred'])
 def test_fixed_tool_query_stop_becomes_protection_not_source_fallback(self):
  self.session.direct_stop=True
  def driver(*args,prepare_phase,**kwargs):
   prepare_phase('prepare',lambda:None)
   try:prepare_phase('snapshot',lambda:None)
   except q.Stopped:return dict(status='stopped',completed=0,protection_stops=1)
   raise AssertionError('query stop missing')
  with patch.object(q,'run_manifest',side_effect=driver):r=p.prepare(self.config,services=self.services)
  self.assertEqual(r['status'],'stopped')
if __name__=='__main__':unittest.main(verbosity=2)
