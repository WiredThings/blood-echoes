"""Author-created controller contracts; no private game data or native launch."""
from pathlib import Path
import copy,json,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import launcher_game_modules as m

class ModuleControllerControls(unittest.TestCase):
 def test_required_typed_scope_keeps_unresolved_sources_as_explicit_gaps(self):
  def stage(program,logical='vertex'):
   return dict(program_hash=program,hardware_stage='vertex',logical_stage=logical,permutation_hash='p',
      serialized_spec_sha256='s',metadata=dict(sha256='m'),spirv=dict(sha256='v'))
  unit=dict(record_type='offline-pso-input',profile=dict(reference=dict(sha256='f')),
      stages=[stage('unique'),stage('missing'),stage('ambiguous'),stage('ignored','fragment')])
  targets,code,observed,gaps=m.companion_targets([unit],{('unique','vertex'):[dict(identity=dict(code_sha256='real-code'))],
      ('ambiguous','vertex'):[dict(identity=dict(code_sha256='a')),dict(identity=dict(code_sha256='b'))]})
  self.assertEqual(len(targets),1);self.assertEqual(list(code.values()),['real-code']);self.assertEqual(len(observed),1)
  self.assertEqual(gaps,{'missing-or-ambiguous-static-source':2})
  # Unknown inputs are not promoted to valid products or removed from the unit.
  self.assertEqual(len(unit['stages']),4)
 def test_geometry_recipe_dependency_identity_changes_only_for_used_repaired_module(self):
  u=dict(recipe_blob_identity='a'*64);entries={name:dict(packet_sha256=name*64,receipt_sha256='b'*64,spirv_sha256='c'*64)for name in('1','2','3')}
  original=m.geometry_recipe_identity(u,['1','2'],'d'*64,entries)
  entries['3']['receipt_sha256']='e'*64;self.assertEqual(original,m.geometry_recipe_identity(u,['1','2'],'d'*64,entries))
  entries['2']['receipt_sha256']='e'*64;self.assertNotEqual(original,m.geometry_recipe_identity(u,['1','2'],'d'*64,entries))
  self.assertIsNone(m.geometry_recipe_identity(u,['unknown'],'d'*64,entries))
 def repair_case(self,fail_once=False):
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();gpu=root/'state/gpu';cache=gpu/'cache/CUSA03173-metal-authored';cache.mkdir(parents=True)
   catalog=root/'catalog.json';catalog.write_text(json.dumps(dict(game_root=str(root),game_identity=dict(TITLE_ID='CUSA03173'))));idx=root/'static.jsonl';idx.write_text('{}\n');layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[])
   uid='a'*64;source=dict(record_type='offline-pso-input',recipe_blob_identity='b'*64,profile=dict(reference=dict(sha256='c'*64)),stages=[dict(program_hash='d'*16,hardware_stage='compute',logical_stage='compute',permutation_hash='e'*16,metadata=dict(sha256=uid))])
   def freeze(unit,stage,**kw):kw['output'].mkdir();(kw['output']/'packet.json').write_text('{}');return{}
   attempts=[];failure=[False]
   def native(component,packet,output,*args):
    attempts.append(packet)
    if failure[0]:failure[0]=False;raise ValueError('authored transient repair failure')
    output.mkdir();(output/'module.spv').write_bytes(b'authored module');(output/'result.json').write_text('{}');return{}
   def prepare():return m.prepare(dict(catalog=catalog,index=idx,game_root=root,identity='1'*64,gpu_root=gpu),project=root,caches=[cache],component=dict(coherent_renderer=True,diagnostic_only=False,native_layout=layout,module_consumer_sha256='2'*64,backend_root=root),cohort='3'*64,selected_renderer='/authored',native_runner=native)
   with (patch.object(m,'coherent_component',return_value=dict(library_sha256='4'*64)),patch.object(m,'rows',return_value=[]),patch.object(m,'lookup_table',return_value={('d'*16,'compute'):[dict(identity={})]}),
       patch.object(m,'selected_units',side_effect=lambda *a,**k:iter([source])),patch.object(m,'stage_input_identity',return_value=uid),patch.object(m,'freeze_stage',side_effect=freeze),
       patch.object(m,'canonical_packet',side_effect=lambda path,*a:json.loads(path.read_text())),patch.object(m,'validate_call'),patch('complete_geometry_psos.verify_receipt')):
    first=prepare();index_path=Path(first['module_index']);old=json.loads(index_path.read_text())['modules'][uid];bad=Path(old['receipt']);bad.write_bytes(b'corrupted original result');spirv=Path(old['spirv']);before=spirv.read_bytes()
    failure[0]=fail_once;second=prepare()
    if fail_once:
     self.assertEqual(second['status'],'partial');self.assertEqual(json.loads(index_path.read_text())['modules'][uid],old);self.assertNotIn(uid,json.loads(index_path.read_text())['rejected'])
     second=prepare()
    self.assertEqual(second['status'],'prepared',second);self.assertEqual(second['counts']['native_attempts'],1);self.assertEqual(second['counts']['repaired_existing'],1)
    self.assertNotEqual(json.loads(index_path.read_text())['modules'][uid],old);self.assertEqual(bad.read_bytes(),b'corrupted original result');self.assertEqual(spirv.read_bytes(),before)
    retained=list(first['cache_root'].glob('retained-module-*.json'));self.assertEqual(len(retained),1);self.assertEqual(json.loads(retained[0].read_text())['entry'],old)
    count=len(attempts);third=prepare();self.assertEqual(third['counts']['verified_existing'],1);self.assertEqual(len(attempts),count)
 def test_bad_required_module_repairs_only_it_and_retains_original_files(self):self.repair_case()
 def test_failed_module_repair_keeps_old_entry_and_can_retry_without_permanent_rejection(self):self.repair_case(True)
 def test_actual_controller_does_not_replace_compiler_namespace_for_another_catalogue(self):
  import module_product_schedule as schedule
  with tempfile.TemporaryDirectory()as tmp:
   base=Path(tmp).resolve();project=base/'project';gpu=project/'state/gpu';gpu.mkdir(parents=True)
   component=dict(coherent_renderer=True,diagnostic_only=False,module_consumer_sha256='c'*64,
    library_sha256='d'*64,native_layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[]))
   contexts=[]
   for n in (1,2):
    source=base/('source'+str(n));source.mkdir();source.chmod(0o500)
    catalog=base/('catalog'+str(n)+'.json');catalog.write_text(json.dumps(dict(game_root=str(source),game_identity={'TITLE_ID':'CUSA03173'},packages=[])))
    index=base/('index'+str(n)+'.jsonl');index.write_text('')
    contexts.append(dict(game_root=source,catalog=catalog,index=index,identity=str(n)*64,gpu_root=gpu))
   def prepare(context,comp=component):
    return m.prepare(context,project=project,caches=[],component=comp,cohort='b'*64,
     environment={'BB_GPU_USER_DIR':str(gpu)},selected_renderer='/not-executed',backend_root=base)
   with (patch.object(m,'coherent_component',return_value=dict(library_sha256='d'*64,library='/not-executed')),
         patch.object(schedule,'query',return_value=None)):
    first=prepare(contexts[0]);second=prepare(contexts[1])
    self.assertEqual(first['cache_root'],second['cache_root']);self.assertEqual(first['compiler_identity'],second['compiler_identity'])
    self.assertEqual(len(list(first['cache_root'].glob('source-selection-*.json'))),2)
    changed=prepare(contexts[1],dict(component,module_consumer_sha256='e'*64))
    self.assertNotEqual(first['cache_root'],changed['cache_root'])
    self.assertEqual(first['counts'].get('native_attempts',0),0)
   for context in contexts:context['game_root'].chmod(0o700)
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
  with tempfile.TemporaryDirectory()as tmp:
   with self.assertRaisesRegex(ValueError,'actual automatic'):m._capability(dict(module_consumer='/not-executed'),Path(tmp).resolve(),runner=run)
 def test_diagnostic_or_missing_nonce_capability_refused(self):
  good=dict(schema=1,cpu_only=True,automatic_input_root=m.ROOT_POLICY,requires_nonce_and_cohort=True,diagnostic_only=False,packet_schema=1)
  for key,value in [('diagnostic_only',True),('requires_nonce_and_cohort',False),('automatic_input_root','state')]:
   cap=dict(good,**{key:value})
   def run(*args,**kwargs):return subprocess.CompletedProcess([],0,('GAME_MODULE_CAPABILITY '+json.dumps(cap)+'\n').encode(),None)
   with tempfile.TemporaryDirectory()as tmp:
    with self.assertRaisesRegex(ValueError,'actual automatic'):m._capability(dict(module_consumer='/not-executed'),Path(tmp).resolve(),runner=run)
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
