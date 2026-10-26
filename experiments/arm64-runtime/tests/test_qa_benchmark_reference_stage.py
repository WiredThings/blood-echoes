"""Code-only original Rosetta reference controls; native commands are mocked."""
from pathlib import Path
from types import MappingProxyType
import hashlib,json,os,plistlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import run_qa_launch_benchmark as driver
from qa_launch_conditions import SCRIPTS

class Reference(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.project=Path(self.temp.name).resolve()
  (self.project/'evidence').mkdir();self.app=self.project/'evidence/reference-123/Blood Echoes.app';self.stage=self.app/'Contents/Resources/bbport';self.stage.mkdir(parents=True)
  p=patch.object(driver,'P',self.project);p.start();self.addCleanup(p.stop)
  p=patch('subprocess.Popen',side_effect=AssertionError('no author native processes'));p.start();self.addCleanup(p.stop)
  self.info=dict(CFBundleIdentifier='io.github.bbport.optimized',CFBundleShortVersionString='0.2.51')
  self.native=dict(default_enabled=True,minimum_os_major=27,game_runtime='rosetta')
  self.put(self.app/'Contents/Info.plist',plistlib.dumps(self.info));self.seal()
  self.images={};self.calls=[]
  for role in (*driver.CPU_ROLES,'bin/gpu-metal/libbbgpu.dylib','bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu','bin/gpu-metal-arm64/libbbgpu.dylib'):
   path=self.stage/role;self.put(path,('author binary '+role).encode());cpu=role in driver.CPU_ROLES;arch='x86_64'if cpu or role=='bin/gpu-metal/libbbgpu.dylib'else'arm64';deps=[]
   if cpu:deps=['@loader_path/'+os.path.relpath(self.stage/'bin/gpu-metal/libbbgpu.dylib',path.parent)]
   elif path.name=='bb-gpu':deps=['@loader_path/'+os.path.relpath(self.stage/'bin/gpu-metal-arm64/libbbgpu.dylib',path.parent)]
   self.images[str(path)]=dict(path=str(path),architecture=arch,filetype=2 if cpu or path.name=='bb-gpu'else 6,sha256=driver.sha(path),dependencies=deps,strict_signature=True)
  scripts={}
  for name in SCRIPTS:
   self.put(self.stage/name,('# author original '+name+'\n').encode());scripts[name]=driver.sha(self.stage/name)
  self.source=dict(environment=MappingProxyType(dict(BB_METAL_ARM64='1',BB_AUDIO='none',BB_QUICKSAVE='1',BB_GPU_PROCESS='/QA/gpu',BB_UPSCALER='off',BB_WIDTH='1920',BB_MTL_GATHER_REUSE='1')),original_scripts=MappingProxyType(scripts),effective_scripts={},stage=self.project/'apps/QA.app/Contents/Resources/bbport',user_root=self.project/'state/gui-user-1')
 def put(self,path,raw):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
 def seal(self):self.put(self.stage/'NATIVE-METAL.json',json.dumps(self.native).encode())
 def seal_runner(self,command,**kw):return subprocess.CompletedProcess(command,0,'','')
 def inspector(self,path):self.calls.append(path);return dict(self.images[str(path)])
 def gpu(self,meta,stage,**kw):return dict(meta,target_sha256=driver.sha(meta['target']),architecture='arm64',strict_signatures=True,load_edge_verified=True)
 def admit(self,**kw):return driver.admit_reference(self.app,self.source,project=self.project,seal_runner=kw.pop('seal_runner',self.seal_runner),image_inspector=kw.pop('image_inspector',self.inspector),gpu_verifier=kw.pop('gpu_verifier',self.gpu),**kw)
 def test_actual_x86_cpu_and_arm_gpu_are_not_mislabelled(self):
  ref=self.admit();self.assertEqual(ref['identity']['cpu_architecture'],'x86_64');self.assertEqual(ref['identity']['GPU_architecture'],'arm64')
  self.assertEqual(set(ref['identity']['cpu']),set(driver.CPU_ROLES));self.assertTrue(all(v['architecture']=='x86_64'for v in ref['identity']['cpu'].values()));json.dumps(ref['identity'])
 def test_original_scripts_not_effective_are_required(self):
  self.source['effective_scripts']={'unused':'must not match'};self.admit()
  self.source['original_scripts']=dict(self.source['original_scripts'],**{'run.sh':'f'*64})
  with self.assertRaisesRegex(ValueError,'original sixteen'):self.admit()
 def test_wrong_version_id_or_fex_not_a_reference(self):
  for field,value in [('CFBundleIdentifier','other'),('CFBundleShortVersionString','0.2.50')]:
   old=self.info[field];self.info[field]=value;self.put(self.app/'Contents/Info.plist',plistlib.dumps(self.info))
   with self.assertRaisesRegex(ValueError,'0.2.51'):self.admit()
   self.info[field]=old
  self.put(self.app/'Contents/Info.plist',plistlib.dumps(self.info));self.native['game_runtime']='fex-arm64';self.seal()
  with self.assertRaisesRegex(ValueError,'original enabled'):self.admit()
 def test_no_backend_selection_override(self):
  self.source['environment']=dict(self.source['environment'],BB_METAL_ARM64='0')
  with self.assertRaisesRegex(ValueError,'already select'):self.admit()
 def test_cpu_must_have_exact_unique_host_edge(self):
  cpu=str(self.stage/driver.CPU_ROLES[0]);original=list(self.images[cpu]['dependencies'])
  for deps in [[],original*2,['@loader_path/gpu-metal-arm64/libbbgpu.dylib']]:
   self.images[cpu]['dependencies']=deps
   with self.assertRaisesRegex(ValueError,'host edge'):self.admit()
 def test_host_architecture_and_signature_are_actual(self):
  host=str(self.stage/'bin/gpu-metal/libbbgpu.dylib');self.images[host]['architecture']='arm64'
  with self.assertRaisesRegex(ValueError,'architecture'):self.admit()
  self.images[host]['architecture']='x86_64';self.images[host]['strict_signature']=False
  with self.assertRaisesRegex(ValueError,'actual image'):self.admit()
 def test_recursive_relative_dependency_is_verified(self):
  host=str(self.stage/'bin/gpu-metal/libbbgpu.dylib');lib=self.stage/'deps-metal/lib/extra.dylib';self.put(lib,b'author x86 dependent library')
  self.images[str(lib)]=dict(path=str(lib),architecture='x86_64',filetype=6,sha256=driver.sha(lib),dependencies=['/usr/lib/libSystem.B.dylib'],strict_signature=True)
  self.images[host]['dependencies']=['@loader_path/'+os.path.relpath(lib,Path(host).parent)]
  self.assertIn(str(lib),self.admit()['identity']['native_closure']);self.assertIn(lib,self.calls)
  self.images[str(lib)]['architecture']='arm64'
  with self.assertRaisesRegex(ValueError,'architecture'):self.admit()
 def test_external_and_absolute_non_system_dependencies_refused(self):
  host=str(self.stage/'bin/gpu-metal/libbbgpu.dylib');external=self.project/'outside.dylib';external.write_bytes(b'outside code')
  for edge in [str(external),'@loader_path/'+os.path.relpath(external,Path(host).parent)]:
   self.images[host]['dependencies']=[edge]
   with self.assertRaisesRegex(ValueError,'not relative|escapes'):self.admit()
 def test_whole_app_pre_post_seals_still_gate_acceptance(self):
  def fail(command,**kw):return subprocess.CompletedProcess(command,1,'','unsealed pyc')
  with self.assertRaisesRegex(ValueError,'seal failed'):self.admit(seal_runner=fail)
  ref=self.admit();result=driver.finish_app_seal(dict(status='sampled',scene_sample={'fps':50}),ref,runner=fail)
  self.assertEqual(result['status'],'incomplete');self.assertEqual(result['raw_run_status'],'sampled');self.assertEqual(result['scene_sample']['fps'],50)
 def test_reference_f3_and_mixed_code_views_refused(self):
  with self.assertRaisesRegex(ValueError,'performance sampling only'):driver.execution_request(None,self.app,functional_f3=True)
  with self.assertRaisesRegex(ValueError,'mutually exclusive'):driver.execution_request(self.app,self.app)
  self.assertEqual(driver.selected_cache_mode(None,driver.execution_request(None,self.app)),'empty')
 def test_reference_execution_only_changes_code_and_preserves_source(self):
  ref=self.admit();case=dict(data_root=self.project/'state/new-data',pad=self.project/'state/new-data/pad',environment=dict(self.source['environment']),allowed_changes=['BB_PAD_FILE','BB_PAD_FILE_ONLY','BB_QUICKSAVE'])
  view=driver.execution_conditions(self.source,ref);env=driver.execution_environment(self.source,case,ref)
  self.assertIs(view['environment'],self.source['environment']);self.assertEqual(view['user_root'],self.source['user_root']);self.assertEqual(driver.cpu_paths(view),{str(self.stage/r)for r in driver.CPU_ROLES})
  self.assertEqual(env['BB_WIDTH'],'1920');self.assertNotIn('BB_ARM_GPU_BASE_DIR',env);self.assertEqual(env['PYTHON'],str(self.stage/'python/bin/python3'))
  changes,_=driver.benchmark_changes(self.source,case,env,formal=ref);self.assertEqual(set(changes),{'BB_GPU_PROCESS','BB_QUICKSAVE','BB_PAD_FILE','BB_PAD_FILE_ONLY'})

if __name__=='__main__':unittest.main()
