"""Author-created controller contracts; no private game data or native launch."""
from pathlib import Path
import copy,json,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_game_modules as m

class ModuleControllerControls(unittest.TestCase):
 def test_missing_component_is_unavailable_without_source_or_gpu_access(self):
  self.assertEqual(m.prepare({},project='/missing',caches=[],component=None,cohort='x')['status'],'unavailable')
 def test_targeted_consumer_cannot_be_promoted_by_only_existing_binary(self):
  for c in(dict(coherent_renderer=False,diagnostic_only=False),dict(coherent_renderer=True,diagnostic_only=True)):
   self.assertEqual(m.prepare({},project='/missing',caches=[],component=c,cohort='x')['status'],'unavailable')
 def test_component_assertion_does_not_replace_actual_load_edge(self):
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp).resolve()/'consumer';p.write_bytes(b'authored');c=dict(coherent_renderer=True,diagnostic_only=False,module_consumer=p,module_consumer_sha256=m.file_sha(p),library_sha256='1'*64)
   def resolver(path,root):return dict(library='/different'if path==p else'/original',library_sha256='1'*64)
   with self.assertRaisesRegex(ValueError,'identical actual DSO'):m.coherent_component(c,gpu=Path(tmp),selected_renderer='/renderer',backend_root=tmp,resolver=resolver)
 def test_capability_is_queried_not_inferred_from_manifest(self):
  def run(*args,**kwargs):return subprocess.CompletedProcess([],0,b'GAME_MODULE_CAPABILITY {"schema":1}\n',None)
  with self.assertRaisesRegex(ValueError,'actual automatic'):m._capability(dict(module_consumer='/not-executed'),Path('/fake'),runner=run)
 def test_diagnostic_or_missing_nonce_capability_refused(self):
  good=dict(schema=1,cpu_only=True,automatic_input_root=m.ROOT_POLICY,requires_nonce_and_cohort=True,diagnostic_only=False,packet_schema=1)
  for key,value in [('diagnostic_only',True),('requires_nonce_and_cohort',False),('automatic_input_root','state')]:
   cap=dict(good,**{key:value})
   def run(*args,**kwargs):return subprocess.CompletedProcess([],0,('GAME_MODULE_CAPABILITY '+json.dumps(cap)+'\n').encode(),None)
   with self.assertRaisesRegex(ValueError,'actual automatic'):m._capability(dict(module_consumer='/not-executed'),Path('/fake'),runner=run)
 def test_module_identity_is_not_runtime_hash_or_unrelated_draw_key(self):
  def ref(c):return dict(sha256=c*64,bytes=100)
  stage=dict(metadata=ref('1'),spirv=ref('2'),logical_stage='vertex',permutation_hash='3'*16)
  unit=dict(profile=dict(reference=ref('4')),key=dict(reference=ref('5')));layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[])
  a=m.module_input_identity(unit,stage,dict(code_sha256='6'*64),layout,dict(title='game'));unit['key']['reference']=ref('7');self.assertEqual(a,m.module_input_identity(unit,stage,dict(code_sha256='6'*64),layout,dict(title='game')))
  stage['metadata']=ref('8');self.assertNotEqual(a,m.module_input_identity(unit,stage,dict(code_sha256='6'*64),layout,dict(title='game')))
 def test_projection_proof_is_additional_compilation_identity(self):
  stage=dict(metadata=dict(sha256='1'*64,bytes=100),spirv=dict(sha256='2'*64,bytes=100),logical_stage='vertex',permutation_hash='3'*16);unit=dict(profile=dict(reference=dict(sha256='4'*64,bytes=10)));layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[])
  args=(unit,stage,dict(code_sha256='5'*64),layout,dict(title='game'))
  self.assertNotEqual(m.module_input_identity(*args,projection_proof=dict(format=37)),m.module_input_identity(*args,projection_proof=dict(format=91)))
 def test_original_candidate_manifest_is_reused_without_second_scan(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();gpu=root/'gpu';cache=gpu/'cache/CUSA03173-metal-authored';cache.mkdir(parents=True);path=root/'candidates.jsonl';row=dict(namespace=dict(cache=str(cache)),record_type='rejected-input',reason='authored');path.write_text(json.dumps(row)+'\n')
   with patch.object(m,'cache_records',side_effect=AssertionError('duplicate inventory')):
    self.assertEqual(list(m.selected_units([cache],gpu=gpu,state_root=root,title='CUSA03173',layout={},check=lambda:None,candidate_manifest=dict(path=str(path),sha256=m.file_sha(path)))),[row])
 def test_manifest_cannot_smuggle_another_cache_or_changed_sha(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();gpu=root/'gpu';cache=gpu/'cache/CUSA03173-metal-authored';cache.mkdir(parents=True);path=root/'candidates.jsonl';path.write_text(json.dumps(dict(namespace=dict(cache='/other/cache')))+'\n')
   with self.assertRaisesRegex(ValueError,'selected original cache'):list(m.selected_units([cache],gpu=gpu,state_root=root,title='CUSA03173',layout={},check=lambda:None,candidate_manifest=path))
   path.write_text(json.dumps(dict(namespace=dict(cache=str(cache))))+'\n')
   with self.assertRaisesRegex(ValueError,'snapshot SHA'):list(m.selected_units([cache],gpu=gpu,state_root=root,title='CUSA03173',layout={},check=lambda:None,candidate_manifest=dict(path=str(path),sha256='0'*64)))
 def test_consecutive_pages_run_in_same_startup_not_sixteen_sample_limit(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();gpu=root/'state/gpu';cache=gpu/'cache/CUSA03173-metal-authored';cache.mkdir(parents=True);catalog=root/'catalog.json';catalog.write_text(json.dumps(dict(game_root=str(root),game_identity=dict(TITLE_ID='CUSA03173'))));idx=root/'static.jsonl';idx.write_text('{}\n');layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[])
   source=[]
   for n in range(33):
    source.append(dict(record_type='offline-pso-input',recipe_blob_identity=str(n),profile=dict(reference=dict(sha256='a'*64)),stages=[dict(program_hash='b'*16,hardware_stage='compute',logical_stage='compute',permutation_hash=str(n),metadata=dict(sha256=f'{n:064x}'))]))
   def freezer(unit,stage,**kwargs):
    path=kwargs['output'];path.mkdir();p={};(path/'packet.json').write_text('{}');return p
   def runner(component,packet,output,*args):output.mkdir();(output/'module.spv').write_bytes(b'author');(output/'result.json').write_text('{}');return {}
   with patch.object(m,'coherent_component',return_value=dict(library_sha256='c'*64)),patch.object(m,'rows',return_value=[]),patch.object(m,'lookup_table',return_value={('b'*16,'compute'):[dict(identity={})]}),patch.object(m,'selected_units',return_value=iter(source)),patch.object(m,'stage_input_identity',side_effect=lambda u,s,*args:s['metadata']['sha256']),patch.object(m,'freeze_stage',side_effect=freezer),patch('complete_geometry_psos.verify_receipt'):
    r=m.prepare(dict(catalog=catalog,index=idx,game_root=root,identity='d'*64,gpu_root=gpu),project=root,caches=[cache],component=dict(coherent_renderer=True,diagnostic_only=False,native_layout=layout,module_consumer_sha256='e'*64,backend_root=root),cohort='f'*64,selected_renderer='/authored',native_runner=runner,page_size=16)
   self.assertEqual(r['counts']['native_attempts'],33);self.assertEqual(r['counts']['pages'],3);self.assertEqual(r['counts']['native_completed'],33)
if __name__=='__main__':unittest.main(verbosity=2)
