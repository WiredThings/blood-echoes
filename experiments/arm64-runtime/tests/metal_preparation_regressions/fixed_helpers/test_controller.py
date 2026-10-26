# SPDX-License-Identifier: GPL-2.0-or-later
"""Standard-library author controller interfaces; never execute worker/GPU/tools."""
from pathlib import Path
import argparse,contextlib,importlib.util,json,os,sys,tempfile,types,unittest
from unittest.mock import patch
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1]);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
s=importlib.util.spec_from_file_location('fixed_helpers',a.source_root/'tools/launcher_fixed_helpers.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
from tiling_fixture import libraries
def response(ready=8,stopped=False,local=14,processed=14):
 items=[dict(kind=k,outcome=1 if n<ready else 3,request_sha256='a'*64,descriptor_sha256='b'*64,inputs_verified=True,native_ready=n<ready,archive_sha256='c'*64 if n<ready else'',actual_routes=[2,2]if k==10 else[2])for n,k in enumerate([4,5,6,9,10,11,12,13])]
 return dict(exit=0,result=dict(status='stopped'if stopped else'completed'if ready==8 else'inputs-collected',fixed_helper_seed_schema=2,known=8,ready=ready,all_fixed_native_ready=ready==8,artifact_counts={'compile_attempts':0},items=items,tiling_libraries=libraries(local,processed)))
class Policy:
 def __init__(self,before):self.baseline=before['swap_mib']
 def observe(self,*_):return None
class Controller(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();(self.root/'state').mkdir();self.stage=self.root/'stage';self.stage.mkdir();self.gpu=self.root/'state/gpu';self.gpu.mkdir();self.calls=[];self.build_calls=[];self.env={'BB_GPU_PROCESS':str(self.stage/'renderer'),'BB_GPU_USER_DIR':str(self.gpu),'BB_MTL_LOCAL_AOT_MODE':'record','BB_MTL_LOCAL_AOT_ROOT':str(self.gpu/'local-metal'),'BB_MTL_LOCAL_AOT_COHORT':'e'*64};self.component=dict(fixed_helper_seed_schema=2,library_sha256='d'*64,worker=self.stage/'worker',library=self.stage/'library');self.modules={'warm_archive_manifest':types.SimpleNamespace(resolve_gpu_identity=lambda *_:{'library_sha256':'d'*64}),'launcher_game_pso':types.SimpleNamespace(title_id=lambda *_:'CUSA00000'),'qa_memory_policy':types.SimpleNamespace(MemoryPolicy=Policy),'qa_build_guard':types.SimpleNamespace(require_no_games=lambda:None)};self.ctx=patch.dict(sys.modules,self.modules);self.ctx.start()
 def tearDown(self):self.ctx.stop();self.temp.cleanup()
 def observe(self):return {'pressure':1,'locked':False,'swap_mib':0}
 def build(self,**kw):self.build_calls.append(kw);return {'BB_MTL_LOCAL_AOT_MODE':'load','BB_MTL_LOCAL_AOT_BUILD_ID':'f'*64},{'status':'prepared'}
 def call(self,runner=None,**override):
  def run(cmd,env,case,check):self.calls.append((cmd,dict(env)));check();return response(0 if cmd[1]=='helpers-probe'else 8)
  args=dict(project=self.root,execution_root=self.stage,arguments=['original','--app0','original-view'],environment=self.env,cohort='e'*64,options={},observe=self.observe,component_loader=lambda _:self.component,toolchain_probe=lambda:True,local_prepare=self.build,runner=runner or run);args.update(override);return m.prepare(**args)
 def reports(self):return [json.loads(p.read_text())for p in (self.root/'state').glob('fixed-helper-seeds-*/result.json')]
 def test_fresh_collect_build_prepare(self):
  before=dict(self.env);updates,report=self.call();self.assertEqual(report['status'],'completed');self.assertEqual([c[0][1]for c in self.calls],['helpers-probe','helpers-prepare']);self.assertEqual(len(self.build_calls),1);self.assertEqual(updates['BB_MTL_LOCAL_AOT_MODE'],'load');self.assertEqual(before,self.env);self.assertFalse(report['storage_opened']);self.assertFalse(report['renderer_owners_constructed'])
 def test_warm_probe_skips_builder(self):
  def run(*_):return response()
  u,r=self.call(runner=run);self.assertEqual(r['status'],'completed');self.assertEqual(u,{});self.assertEqual(self.build_calls,[])
 def test_old_abi_does_not_probe_toolchain_or_run(self):
  self.component['fixed_helper_seed_schema']=0
  def no():raise AssertionError('unexpected toolchain probe')
  u,r=self.call(toolchain_probe=no);self.assertEqual(r['status'],'unavailable');self.assertEqual(self.calls,[])
 def test_missing_toolchain_preserves_source(self):
  u,r=self.call(toolchain_probe=lambda:False);self.assertEqual(r['status'],'source-fallback');self.assertEqual(u,{});self.assertEqual(self.calls,[])
 def test_explicit_modes_unchanged(self):
  for mode in ['off','record']:
   u,r=self.call(mode=mode);self.assertEqual(r['status'],'inactive');self.assertEqual(self.calls,[])
 def test_progress_resumes_without_false_completion(self):
  values=iter([0,3,7,8])
  def run(cmd,*_):self.calls.append((cmd,{}));return response(next(values))
  u,r=self.call(runner=run);self.assertEqual(r['status'],'completed');self.assertEqual(len(self.calls),4)
 def test_no_progress_is_deferred(self):
  def run(cmd,*_):self.calls.append((cmd,{}));return response(0 if cmd[1]=='helpers-probe'else 7)
  u,r=self.call(runner=run);self.assertEqual(r['status'],'deferred');self.assertEqual(len(self.calls),3)
 def test_late_library_requests_resume_even_when_fixed_psos_are_already_ready(self):
  responses=iter([response(8,local=3,processed=3),response(8,local=3),response(8)])
  def run(cmd,*_):self.calls.append((cmd,{}));return next(responses)
  _,r=self.call(runner=run);self.assertEqual(r['status'],'completed');self.assertEqual(len(self.calls),3);self.assertEqual(len(self.build_calls),2)
 def test_library_no_progress_does_not_claim_complete_or_loop(self):
  def run(cmd,*_):self.calls.append((cmd,{}));return response(8,local=13)
  _,r=self.call(runner=run);self.assertEqual(r['status'],'deferred');self.assertEqual(r['ready'],8);self.assertEqual(r['tiling_libraries_ready'],13);self.assertEqual(len(self.calls),3);self.assertEqual(len(self.build_calls),2)
 def test_local_stop_not_source_fallback(self):
  def stop(**_):return {},{'status':'stopped'}
  with self.assertRaises(RuntimeError):self.call(local_prepare=stop)
  self.assertEqual(self.reports()[0]['status'],'stopped');self.assertEqual(len(self.calls),1)
 def test_critical_result_stops(self):
  with self.assertRaises(RuntimeError):self.call(runner=lambda *_:response(0,True))
  self.assertEqual(self.reports()[0]['status'],'stopped')
 def test_cancel_preserves_primary_if_final_observer_fails(self):
  calls=0
  def observe():
   nonlocal calls
   calls+=1
   if calls==3:raise OSError('author final observation failed')
   return self.observe()
  def cancel(*_):raise KeyboardInterrupt('author primary cancel')
  with self.assertRaises(KeyboardInterrupt):self.call(runner=cancel,observe=observe)
 def test_final_observer_failure_without_primary_stops_success(self):
  calls=0
  def observe():
   nonlocal calls
   calls+=1
   if calls==4:raise OSError('author final observation failed')
   return self.observe()
  with self.assertRaises(OSError):self.call(runner=lambda *_:response(),observe=observe)
 def test_cancel_preserves_primary_even_log_write_fails(self):
  original=Path.write_text
  def write(path,*args,**kw):
   if path.name=='result.json':raise OSError('authored full disk')
   return original(path,*args,**kw)
  def cancel(*_):raise KeyboardInterrupt('authored cancel')
  with patch.object(Path,'write_text',write):
   with self.assertRaises(KeyboardInterrupt):self.call(runner=cancel)
if __name__=='__main__':unittest.main(verbosity=2)
