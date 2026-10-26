"""Two-stage code packaging with real file hashes and injected native queries."""
from pathlib import Path
import json,os,plistlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import game_pso_component as component
import launcher_runtime_profile as profile

class Finalize(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.normal=self.root/'normal';self.normal.mkdir();(self.normal/'state').mkdir()
  self.app=self.root/'Normal.app';self.stage=self.app/'Contents/Resources/bbport';(self.stage/'bin').mkdir(parents=True)
  self.source_library=self.normal/'libbbgpu.dylib';self.source_library.write_bytes(b'author coherent GPU library')
  self.library=self.stage/'bin/gpu-metal-arm64/libbbgpu.dylib';self.library.parent.mkdir();self.library.write_bytes(self.source_library.read_bytes())
  self.headers=self.normal/'shadps4';(self.headers/'test').mkdir(parents=True);(self.headers/'test/header.h').write_bytes(b'author canonical layout header')
  self.layout_data=dict(source_sha256={'test/header.h':component.sha(self.headers/'test/header.h')},authored_writer_fixtures={})
  self.layout=self.normal/'layout.json';self.layout.write_text(json.dumps(self.layout_data))
  self.m=dict(schema=1,kind='blood-echoes-game-pso-component',production=False,enabled=True,library=str(self.source_library),library_sha256=component.sha(self.source_library),layout=str(self.layout),layout_sha256=component.sha(self.layout),source_root=str(self.headers),coherent_renderer=True,diagnostic_only=False,automatic_capture_schema=1)
  for name in('worker','module_consumer'):
   path=self.normal/name;path.write_bytes(('author normal '+name).encode());self.m.update({name:str(path),name+'_sha256':component.sha(path)})
  self.source=self.normal/'component.json';self.source.write_text(json.dumps(self.m));self.query_calls=[]
  def resolve(path,root,**kw):
   library=self.source_library if Path(path).parent==self.normal else self.library
   return dict(library=str(library),library_sha256=component.sha(library))
  self.resolver=patch('warm_archive_manifest.resolve_gpu_identity',side_effect=resolve);self.resolver.start();self.addCleanup(self.resolver.stop)
  self.layout_patch=patch.object(component,'load_canonical_layout',side_effect=self.load_layout);self.layout_patch.start();self.addCleanup(self.layout_patch.stop)
  self.no_process=patch('subprocess.Popen',side_effect=AssertionError('author control cannot start native processes'));self.no_process.start();self.addCleanup(self.no_process.stop)
 def load_layout(self,path,source,expected,**kw):
  if component.sha(Path(path))!=expected:raise ValueError('source-layout identity changed')
  for relative,want in self.layout_data['source_sha256'].items():
   if component.sha(Path(source)/relative)!=want:raise ValueError('source-layout header changed')
  return self.layout_data
 def rewrite(self,path,old,new):Path(path).write_bytes(Path(path).read_bytes()+b'\nauthor relative load edge')
 def prepare(self):return component.prepare_inputs(self.stage,self.source,self.library,rewrite=self.rewrite,admission_root=self.normal)
 def formal_closure(self):
  # All actual code-only roles/tools exist; no game/boot/user/config/cache inputs.
  for name in (*profile.ROLES,'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib'):
   p=self.stage/name
   if not p.exists():p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('author role '+name).encode())
  (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=profile.FORMAL_ID)))
  tools={};support={};scripts={}
  for name in('launcher_local_aot.py','prepare_module_metadata.py'):
   p=self.stage/'native-runtime-tools'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'# authored helper\n');tools[name]=component.sha(p)
  for name in('vendor/blood_echoes/loader/prepare.py','vendor/blood_echoes/loader/game_check.py'):
   p=self.stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'# original parser\n');support[name]=component.sha(p)
  for name in profile.ORIGINAL_SCRIPTS:
   p=self.stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(('# author script '+name).encode());scripts[name]=component.sha(p)
  startup=self.stage/'tools/macos/local_metal_startup.py';startup.write_bytes(b'# authored startup\n')
  native=dict(schema=1,game_runtime='fex-arm64',production=True,frozen_runtime={n:component.sha(self.stage/n)for n in(*profile.ROLES,'bin/Bloodborne Game.app/Contents/MacOS/bb-probe-metal','bin/gpu-net/libbbnet.dylib')})
  local=dict(schema=1,production=True,runtime_tools=tools,runtime_support=support,startup_bootstrap_sha256=component.sha(startup))
  for n,value in [('NATIVE-METAL.json',native),('ARM64-LOCAL-PREPARATION.json',local),('SOURCE-PARITY.json',dict(effective_sources=scripts))]:(self.stage/n).write_text(json.dumps(value))
 def query(self,command,**kwargs):
  self.query_calls.append((command,kwargs));env=kwargs['env']
  self.assertEqual(command,[str(self.stage/'bin/game-pso-preparer'),'--fixed-helper-seed-schema'])
  self.assertEqual(env['HOME'],env['TMPDIR']);self.assertEqual(env['HOME'],env['BB_GPU_USER_DIR'])
  self.assertEqual(set(env),{'PATH','HOME','TMPDIR','BB_GPU_USER_DIR','BB_DATA_DIR'})
  self.assertTrue(Path(env['HOME']).is_relative_to(Path(env['BB_DATA_DIR'])/profile.WORK/'capabilities'))
  self.assertNotIn('BB_GAME_DIR',env);self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
  return subprocess.CompletedProcess(command,0,b'FIXED_HELPER_CAPABILITY 2\n',None)
 def test_prepare_only_copies_verified_inputs_and_never_queries_or_publishes(self):
  with patch.object(component,'fixed_helper_capability',side_effect=AssertionError('no early query')):candidate=self.prepare()
  self.assertFalse(candidate['component']['enabled']);self.assertFalse(candidate['capability_queried']);self.assertFalse(candidate['component_published']);self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
  self.assertNotIn('fixed_helper_seed_schema',candidate['component']);self.assertFalse((self.root/'old-QA').exists())
 def test_formal_finalization_real_code_scope_then_actual_scalar(self):
  candidate=self.prepare();self.formal_closure()
  with profile.packaging_capabilities(self.stage,scratch_parent=self.root)as caps:
   data=caps.data_root;result=component.finalize_inputs(self.stage,candidate,admission_root=self.normal,runtime_profile=caps,production=True,capability_runner=self.query)
   self.assertTrue(result['enabled']);self.assertTrue(result['production']);self.assertEqual(result['fixed_helper_seed_schema'],2);self.assertEqual(len(self.query_calls),1)
   self.assertEqual(json.loads((self.stage/'GAME-PSO-COMPONENT.json').read_text()),result)
  self.assertFalse(data.exists());self.assertFalse((self.root/'state').exists())
 def test_no_final_component_is_needed_for_native_and_scalar_admission(self):
  native=(Path(__file__).resolve().parents[1]/'src/bundled_runtime_admission.mm').read_text();self.assertNotIn('GAME-PSO-COMPONENT',native)
  main=(Path(__file__).resolve().parents[1]/'src/game_pso_preparer.mm').read_text().split('int main(',1)[1]
  self.assertLess(main.index('--fixed-helper-seed-schema'),main.index('return RunRecipe('))
  self.prepare();self.formal_closure();self.assertIsNotNone(profile.bundle(self.stage));self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
 def test_packaging_scope_cannot_be_used_as_game_profile(self):
  self.prepare();self.formal_closure()
  with profile.packaging_capabilities(self.stage,scratch_parent=self.root)as caps:
   for name in('game_root','app0','user','gpu_root','work_root'):self.assertFalse(hasattr(caps,name))
   for fn in(lambda:profile.kw(caps),lambda:profile.work(self.root,caps),lambda:profile.cache_root(self.root,caps)):
    with self.assertRaisesRegex(ValueError,'not a game'):fn()
 def test_missing_real_bundle_or_scratch_inside_signed_app_rejected(self):
  with self.assertRaises((ValueError,FileNotFoundError)):
   with profile.packaging_capabilities(self.stage,scratch_parent=self.root):pass
  self.prepare();self.formal_closure()
  with self.assertRaisesRegex(ValueError,'outside signed'):
   with profile.packaging_capabilities(self.stage,scratch_parent=self.stage):pass
 def test_formal_rejects_no_profile_and_ordinary_runtime_profile(self):
  candidate=self.prepare()
  for value in(None,object()):
   with self.assertRaisesRegex(ValueError,'packaging-only'):component.finalize_inputs(self.stage,candidate,admission_root=self.normal,runtime_profile=value,production=True,capability_runner=self.query)
  self.assertFalse(self.query_calls);self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
 def test_actual_scalar_failure_or_old_schema_never_publishes_formal_success(self):
  candidate=self.prepare();self.formal_closure()
  with profile.packaging_capabilities(self.stage,scratch_parent=self.root)as caps:
   for status,raw in[(78,b''),(0,b'FIXED_HELPER_CAPABILITY 1\n'),(0,b'FIXED_HELPER_CAPABILITY 2\nextra\n')]:
    with self.assertRaises(ValueError):component.finalize_inputs(self.stage,candidate,admission_root=self.normal,runtime_profile=caps,production=True,capability_runner=lambda command,**kw:subprocess.CompletedProcess(command,status,raw,None))
    self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
 def test_mutation_before_or_during_scalar_is_refused_without_final_manifest(self):
  candidate=self.prepare();self.formal_closure()
  with profile.packaging_capabilities(self.stage,scratch_parent=self.root)as caps:
   worker=self.stage/'bin/game-pso-preparer';old=worker.read_bytes();worker.write_bytes(b'modified')
   with self.assertRaises(ValueError):component.finalize_inputs(self.stage,candidate,admission_root=self.normal,runtime_profile=caps,production=True,capability_runner=self.query)
   worker.write_bytes(old)
   def mutate(command,**kwargs):result=self.query(command,**kwargs);worker.write_bytes(b'changed during query');return result
   with self.assertRaises(ValueError):component.finalize_inputs(self.stage,candidate,admission_root=self.normal,runtime_profile=caps,production=True,capability_runner=mutate)
   self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
 def test_original_source_layout_and_actual_source_edge_checks_still_run(self):
  self.layout.write_bytes(b'changed source layout')
  with self.assertRaisesRegex(ValueError,'source-layout identity'):self.prepare()
  self.assertFalse((self.stage/'GAME-PSO-COMPONENT.json').exists())
 def test_legacy_freeze_is_composition_and_preserves_old_QA_capability_root(self):
  def query(command,**kwargs):
   self.assertTrue(Path(kwargs['env']['BB_GPU_USER_DIR']).is_relative_to(self.normal/'state'));self.assertNotIn('BB_DATA_DIR',kwargs['env']);return subprocess.CompletedProcess(command,0,b'FIXED_HELPER_CAPABILITY 2\n',None)
  with patch.object(component,'_run_bounded_capability',side_effect=query):result=component.freeze_inputs(self.stage,self.source,self.library,rewrite=self.rewrite,admission_root=self.normal)
  self.assertTrue(result['enabled']);self.assertFalse(result['production']);self.assertEqual(result['fixed_helper_seed_schema'],2)
 def test_candidate_or_published_manifest_cannot_be_overwritten(self):
  candidate=self.prepare();self.formal_closure();candidate['component']['enabled']=True
  with self.assertRaisesRegex(ValueError,'cannot claim'):component.finalize_inputs(self.stage,candidate,admission_root=self.normal)
  final=self.stage/'GAME-PSO-COMPONENT.json';final.write_text('retain existing')
  with self.assertRaisesRegex(ValueError,'overwriting'):component.finalize_inputs(self.stage,candidate,admission_root=self.normal)
  self.assertEqual(final.read_text(),'retain existing')

if __name__=='__main__':unittest.main()
