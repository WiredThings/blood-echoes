# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact controller + PID/path/SHA helpers, with actual authored Popen workers.
No real GPU bytes/library, game, UI, private paths or native worker dependencies.
"""
from pathlib import Path
import argparse,ast,hashlib,importlib.util,json,os,re,stat,sys,tempfile,types,unittest
from unittest.mock import patch
p=argparse.ArgumentParser(add_help=False);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--work-root',type=Path,required=True);a,left=p.parse_known_args();sys.argv=[sys.argv[0],*left]
src=a.source_root/'tools/prepare_game_psos.py';tree=ast.parse(src.read_text());functions={'need','dyld_gpu_image','verify_loaded_gpu'};assigns={'MAX_BLOB','MAX_LINE','HEX','DYLD_PATH_MAX','DYLD_LIBRARY_MAX','_DYLD_IMAGE'};nodes=[]
for n in tree.body:
 if isinstance(n,ast.ClassDef)and n.name=='Refused' or isinstance(n,ast.FunctionDef)and n.name in functions:nodes.append(n)
 elif isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id in assigns for t in n.targets):nodes.append(n)
helpers=types.ModuleType('prepare_game_psos');helpers.__dict__.update(Path=Path,os=os,re=re,stat=stat,hashlib=hashlib);exec(compile(ast.Module(body=nodes,type_ignores=[]),str(src),'exec'),helpers.__dict__)
spec=importlib.util.spec_from_file_location('fixed',a.source_root/'tools/launcher_fixed_helpers.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class Policy:
 def __init__(self,before):self.baseline=before['swap_mib']
 def observe(self,*_):return None
class ControllerDyld(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=a.work_root);self.root=Path(self.temp.name).resolve();self.project=self.root/'project';(self.project/'state').mkdir(parents=True);self.gpu=self.project/'state/gpu';self.gpu.mkdir();self.stage=self.root/'Blood Echoes ARM64 GUI QA Author.app/Contents/Resources/bbport';(self.stage/'bin/gpu metal arm64').mkdir(parents=True);self.library=self.stage/'bin/gpu metal arm64/libbbgpu.author.dylib';self.library.write_bytes(b'project-owned author metadata only; never dlopen or execute');self.sha=hashlib.sha256(self.library.read_bytes()).hexdigest();self.other=self.stage/'bin/gpu metal arm64/libbbgpu.other.dylib';self.other.write_bytes(b'other author metadata only');self.worker=self.stage/'bin/helper worker';self.marker=self.stage/'worker-ran.pid';self.component=dict(fixed_helper_seed_schema=2,worker=self.worker,library=self.library,library_sha256=self.sha)
  self.mods={'prepare_game_psos':helpers,'warm_archive_manifest':types.SimpleNamespace(resolve_gpu_identity=lambda *_:{'library_sha256':self.sha}),'launcher_game_pso':types.SimpleNamespace(title_id=lambda *_:'CUSA00000'),'qa_memory_policy':types.SimpleNamespace(MemoryPolicy=Policy),'qa_build_guard':types.SimpleNamespace(require_no_games=lambda:None)};self.ctx=patch.dict(sys.modules,self.mods);self.ctx.start()
 def tearDown(self):self.ctx.stop();self.temp.cleanup()
 def worker_source(self,case):
  # The worker emits controlled dyld records. It never loads these dummy files.
  from tiling_fixture import libraries
  payload=dict(tiling_libraries=libraries(),status='completed',fixed_helper_seed_schema=2,known=8,ready=8,all_fixed_native_ready=True,artifact_counts={'compile_attempts':0},items=[dict(kind=k,outcome=1,request_sha256='a'*64,descriptor_sha256='b'*64,inputs_verified=True,native_ready=True,archive_sha256='c'*64,actual_routes=[2,2]if k==10 else[2])for k in [4,5,6,9,10,11,12,13]])
  s='#!'+sys.executable+'\nimport os,json\nfrom pathlib import Path\nPath('+repr(str(self.marker))+').write_text(str(os.getpid()))\n'
  if case=='bad-sha':s+='Path('+repr(str(self.library))+').write_bytes(b"changed AFTER admission, author data only")\n'
  pid='os.getpid()+1'if case=='foreign-pid'else'os.getpid()'
  s+='print("dyld["+str('+pid+')+"]: <12345678-1234-1234-1234-123456789ABC> "+'+repr(str(self.library))+',flush=True)\n'
  if case in('other-gpu','duplicate-same'):
   path=self.other if case=='other-gpu'else self.library;s+='print("dyld["+str(os.getpid())+"]: loaded: "+'+repr(str(path))+',flush=True)\n'
  s+='print("FIXED_HELPER_RESULT "+json.dumps('+repr(payload)+'),flush=True)\n';self.worker.write_text(s);self.worker.chmod(0o700)
 def launch(self,case):
  self.worker_source(case);env={'BB_GPU_PROCESS':str(self.stage/'renderer'),'BB_GPU_USER_DIR':str(self.gpu),'BB_MTL_LOCAL_AOT_MODE':'load','BB_MTL_LOCAL_AOT_BUILD_ID':'e'*64,'BB_MTL_LOCAL_AOT_COHORT':'f'*64}
  return module.prepare(project=self.project,execution_root=self.stage,arguments=['original'],environment=env,cohort='f'*64,options={},observe=lambda:{'pressure':1,'locked':False,'swap_mib':0},component_loader=lambda _:self.component,toolchain_probe=lambda:True,local_prepare=lambda **_:(self.fail('completed probe must skip local builder')))
 def test_actual_popen_spaces_path_admitted(self):
  updates,r=self.launch('correct');self.assertEqual(r['status'],'completed');self.assertEqual(len(r['calls']),1);row=r['calls'][0];identity=row['loaded_gpu_identity'];self.assertEqual(row['pid'],int(self.marker.read_text()));self.assertEqual(identity['library'],str(self.library));self.assertEqual(identity['library_sha256'],self.sha);self.assertTrue(identity['owned_post_worker_sha_verified']);self.assertTrue(identity['single_gpu_load_edge']);self.assertIn(' ',identity['library']);self.assertEqual(updates,{})
 def test_actual_popen_foreign_pid_rejected(self):
  with self.assertRaisesRegex(helpers.Refused,'PID mismatch'):self.launch('foreign-pid')
  self.assertTrue(self.marker.is_file())
 def test_actual_popen_same_path_changed_sha_rejected(self):
  with self.assertRaisesRegex(helpers.Refused,'SHA mismatch'):self.launch('bad-sha')
  self.assertTrue(self.marker.is_file());self.assertNotEqual(hashlib.sha256(self.library.read_bytes()).hexdigest(),self.sha)
 def test_actual_popen_two_different_gpu_images_rejected(self):
  with self.assertRaisesRegex(helpers.Refused,'load edge changed'):self.launch('other-gpu')
  self.assertTrue(self.marker.is_file())
 def test_actual_popen_same_gpu_duplicate_is_one_edge(self):
  _,r=self.launch('duplicate-same');self.assertEqual(r['status'],'completed');self.assertTrue(r['calls'][0]['loaded_gpu_identity']['single_gpu_load_edge'])
 def test_helper_pidless_legacy_record_cannot_authorize(self):
  with self.assertRaisesRegex(helpers.Refused,'PID mismatch'):helpers.dyld_gpu_image('dyld: '+str(self.library)+'\n',123)
 def test_helper_preserves_space_bytes_where_old_split_truncates(self):
  text='dyld[123]: <12345678-1234-1234-1234-123456789ABC> '+str(self.library)+'\n';self.assertNotEqual(text.split()[-1],str(self.library));self.assertEqual(helpers.dyld_gpu_image(text,123),str(self.library))
if __name__=='__main__':unittest.main(verbosity=2)
