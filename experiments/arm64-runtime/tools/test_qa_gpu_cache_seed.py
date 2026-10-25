from pathlib import Path
import hashlib,json,os,sys,tempfile,unittest
from unittest.mock import patch
import qa_gpu_cache_seed as S
RAW='c'*64
class Controls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.p=self.root/'project';(self.p/'apps').mkdir(parents=True);(self.p/'state').mkdir();(self.p/'evidence').mkdir()
  self.app=self.p/'apps/Blood Echoes ARM64 GUI QA 123.app';self.stage=self.app/'Contents/Resources/bbport';self.stage.mkdir(parents=True);self.manifest=self.app/'Contents/Resources/ARM64-QA-MANIFEST.json'
  self.source=self.p/'state/gui-gpu-local-aot-123';self.source.mkdir();self.baseline=self.p/'state/original-qa-gpu';self.baseline.mkdir()
  def put(root,name,data):p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p
  self.gpu=put(self.stage,'bin/Bloodborne Metal.app/Contents/MacOS/bb-gpu',b'author GPU exe not native');self.lib=put(self.stage,'bin/gpu-metal-arm64/libbbgpu.dylib',b'author relocated library distinct from raw source')
  names=['bin/bb-probe-metal','bin/gpu-metal/libbbgpu.dylib','bin/gpu-net/libbbnet.dylib','bin/game-pso-preparer']
  for n in names:put(self.stage,n,b'author frozen '+n.encode())
  frozen={str(p.relative_to(self.stage)):S.sha(p)for p in self.stage.rglob('*')if p.is_file()};tools={}
  for n in('prepare_game_psos.py','game_pso_queue.py'):p=put(self.stage,'native-runtime-tools/'+n,b'# author closure\n');tools[n]=S.sha(p)
  scripts={}
  for i in range(16):p=put(self.stage,f'scripts/author-{i}.py',b'# author script\n');scripts[str(p.relative_to(self.stage))]=S.sha(p)
  cpu=self.p/'evidence/author-cpu.json';cpu.write_text(json.dumps(dict(schema=1,kind='blood-echoes-isolated-cpu-runtime',production=False,gpu_library_sha256=RAW)))
  self.m=dict(schema=2,app=str(self.app),host_local_only=True,portable_installer=False,formal_data_modified=False,upstream_inputs=dict(gpu_library=RAW),cpu_runtime_manifest=dict(path=str(cpu),sha256=S.sha(cpu)),frozen_runtime=frozen,local_metal_preparation=dict(runtime_project=str(self.p),host_local_only=True,runtime_tools=tools),effective_scripts=scripts,private_gpu_cache=str(self.source));self.save()
  put(self.source,'cache/CUSA03173-metal-one/0xold.key',b'429th cached key');put(self.source,'local-metal/cohort/artifacts/good.metallib',b'prepared library');put(self.source,'cache/CUSA03173/metal/artifact.bin',b'final PSO')
  for name in('custom_configs/selected.ini','home/qa-save','trophy/t.dat','shader/override.spv','game-pso-progress/old.sqlite'):put(self.source,name,b'old QA state not selected')
  put(self.baseline,'custom_configs/selected.ini',b'original config');put(self.baseline,'home/qa-save',b'original save seed');put(self.baseline,'shader/override.spv',b'original override');put(self.baseline,'cache/old409.key',b'baseline cache must not merge');put(self.baseline,'local-metal/old.metallib',b'baseline artifacts must not merge');put(self.baseline,'game-pso-progress/old.sqlite',b'baseline hint must not merge')
 def tearDown(self):self.tmp.cleanup()
 def save(self):self.manifest.write_text(json.dumps(self.m))
 def admit(self,**kw):
  args=dict(project=self.p,expected_upstream_gpu_sha256=RAW,gpu_verifier=lambda *a:dict(library=str(self.lib),library_sha256=S.sha(self.lib)),component_loader=lambda *a:dict(library_sha256=S.sha(self.lib)),app_verifier=lambda *a:True);args.update(kw);return S.validate(self.manifest,**args)
 def test_original_vs_relocated_not_confused(self):
  r=self.admit();self.assertEqual(r['upstream_gpu_library_sha256'],RAW);self.assertNotEqual(r['source_packaged_gpu_library_sha256'],RAW)
 def test_cache_only_clone_preserves_baseline_config_without_merging(self):
  old={str(p):p.read_bytes()for p in self.source.rglob('*')if p.is_file()};r=S.clone(self.admit(),self.p/'state/gui-gpu-local-aot-456',project=self.p,baseline_root=self.baseline);dest=Path(r['destination'])
  self.assertEqual((dest/'custom_configs/selected.ini').read_bytes(),b'original config');self.assertEqual((dest/'home/qa-save').read_bytes(),b'original save seed');self.assertEqual((dest/'shader/override.spv').read_bytes(),b'original override')
  self.assertTrue((dest/'cache/CUSA03173-metal-one/0xold.key').exists());self.assertTrue((dest/'local-metal/cohort/artifacts/good.metallib').exists());self.assertFalse((dest/'cache/old409.key').exists());self.assertFalse((dest/'local-metal/old.metallib').exists());self.assertFalse((dest/'game-pso-progress').exists());self.assertFalse((dest/'trophy/t.dat').exists());self.assertEqual(r['copied_files'],3);self.assertEqual(old,{str(p):p.read_bytes()for p in self.source.rglob('*')if p.is_file()})
 def test_different_raw_renderer_refused(self):
  with self.assertRaises(ValueError):self.admit(expected_upstream_gpu_sha256='d'*64)
 def test_formal_flag_refused(self):
  self.m['formal_data_modified']=True;self.save()
  with self.assertRaises(ValueError):self.admit()
 def test_schema_and_host_local_refused(self):
  for name,val in(('schema',1),('host_local_only',False),('portable_installer',True)):
   old=self.m[name];self.m[name]=val;self.save()
   with self.assertRaises(ValueError):self.admit()
   self.m[name]=old;self.save()
 def test_formal_cache_path_refused(self):
  self.m['private_gpu_cache']=str(self.root);self.save()
  with self.assertRaises(ValueError):self.admit()
 def test_manifest_symlink_refused(self):
  original=self.manifest.read_bytes();self.manifest.unlink();other=self.root/'elsewhere';other.write_bytes(original);self.manifest.symlink_to(other)
  with self.assertRaises(ValueError):self.admit()
 def test_source_lib_mutation_refused(self):
  self.lib.write_bytes(b'changed')
  with self.assertRaises(ValueError):self.admit()
 def test_actual_edge_mismatch_refused(self):
  with self.assertRaises(ValueError):self.admit(gpu_verifier=lambda *a:dict(library=str(self.p/'wrong'),library_sha256=S.sha(self.lib)))
 def test_actual_signature_failure_propagates(self):
  def denied(*a):raise ValueError('author fake signature rejection')
  with self.assertRaisesRegex(ValueError,'signature'):self.admit(gpu_verifier=denied)
 def test_tool_closure_mutation_refused(self):
  (self.stage/'native-runtime-tools/game_pso_queue.py').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.admit()
 def test_source_script_closure_mutation_refused(self):
  (self.stage/'scripts/author-1.py').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.admit()
 def test_cpu_provenance_mutation_refused(self):
  Path(self.m['cpu_runtime_manifest']['path']).write_text('{}')
  with self.assertRaises(ValueError):self.admit()
 def test_seed_cache_symlink_refused(self):
  (self.source/'cache/link').symlink_to(self.baseline/'home/qa-save')
  with self.assertRaises(ValueError):S.clone(self.admit(),self.p/'state/gui-gpu-local-aot-456',project=self.p,baseline_root=self.baseline)
 def test_destination_existing_refused(self):
  dest=self.p/'state/gui-gpu-local-aot-456';dest.mkdir();(dest/'preserve').write_bytes(b'preserve')
  with self.assertRaises(ValueError):S.clone(self.admit(),dest,project=self.p,baseline_root=self.baseline)
  self.assertEqual((dest/'preserve').read_bytes(),b'preserve')
 def test_source_app_signature_refused(self):
  with self.assertRaises(ValueError):self.admit(app_verifier=lambda *a:False)
 def test_package_keeps_default_branch_and_explicit_only(self):
  text=Path(S.__file__).with_name('package_local_arm64_qa.py').read_text();self.assertIn('--gpu-cache-seed-manifest',text);self.assertIn('else:shutil.copytree(old_gpu,new_gpu,symlinks=False)',text);self.assertIn('baseline_root=old_gpu',text)
if __name__=='__main__':unittest.main()
