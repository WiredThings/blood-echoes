"""Typed companion receipt controls; no game/GCN/private input fixture."""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from game_companion_modules import validate_call,association_key,stage_key,FIELDS
from complete_pipeline_inputs import file_sha
class TypedContracts(unittest.TestCase):
 def fixture(self,root):
  inp=root/'input';out=root/'output';inp.mkdir();out.mkdir();(inp/'profile.bin').write_bytes(b'authored profile')
  spv=b'authored output';spec=b'authored typed canonical';facts=dict(companion_key='1'*64,input_integrity_sha256='2'*64,program_hash='3'*16,hardware_stage='vertex',logical_stage='vertex',permutation=7,permutation_hash='4'*16,reference_origin=2,canonical_spec_sha256=hashlib.sha256(spec).hexdigest(),code_sha256='5'*64,observed_spirv_sha256=hashlib.sha256(spv).hexdigest(),observed_spirv_bytes=len(spv))
  p=dict(kind='owned-fetch-companion-module-input',selected_backend_sha256='6'*64,profile=dict(path=str(inp/'profile.bin')),expected=facts,target_specs=[]);(inp/'packet.json').write_text(json.dumps(p));(out/'module.spv').write_bytes(spv);(out/'canonical-spec.bin').write_bytes(spec);(out/'result.json').write_text('{}')
  r=dict(candidate_only=True,companion_contract_schema=1,input_packet_sha256=file_sha(inp/'packet.json'),source_compiler_sha256='6'*64,status='completed',association=facts,spirv_sha256=facts['observed_spirv_sha256'],spirv_bytes=len(spv),target_spec_sha256s=[])
  for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read','full_final_pso_ready'):r[k]=False
  for k in('native_module_compiled','expected_spirv_equal','runtime_after_equal','canonical_specialization_equal','program_base_from_owned_snapshot','current_generated_srt_verified','target_canonical_specs_verified'):r[k]=True
  return p,r,out
 def test_typed_receipt_has_own_real_base_contract(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,r,out=self.fixture(Path(tmp).resolve());validate_call(0,r,out,p)
 def test_unknown_base_boolean_cannot_masquerade_as_captured_base(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,r,out=self.fixture(Path(tmp).resolve());r['unknown_program_base_rejected_before_use']=True
   with self.assertRaisesRegex(ValueError,'unknown-base'):validate_call(0,r,out,p)
 def test_actual_permutation_never_replaced_with_input_id(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,r,out=self.fixture(Path(tmp).resolve());r=copy.deepcopy(r);r['association']['permutation_hash']=p['expected']['input_integrity_sha256']
   with self.assertRaisesRegex(ValueError,'runtime stage'):validate_call(0,r,out,p)
 def test_omitted_canonical_or_runtime_equality_rejected(self):
  for field in('canonical_specialization_equal','runtime_after_equal','current_generated_srt_verified'):
   with tempfile.TemporaryDirectory()as tmp:
    p,r,out=self.fixture(Path(tmp).resolve());r[field]=False
    with self.assertRaisesRegex(ValueError,'translation gates'):validate_call(0,r,out,p)
 def test_different_compiler_or_packet_cannot_reuse_receipt(self):
  for field in('source_compiler_sha256','input_packet_sha256'):
   with tempfile.TemporaryDirectory()as tmp:
    p,r,out=self.fixture(Path(tmp).resolve());r[field]='0'*64
    with self.assertRaises(ValueError):validate_call(0,r,out,p)
 def test_modified_spirv_rejected_even_when_boolean_is_true(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,r,out=self.fixture(Path(tmp).resolve());(out/'module.spv').write_bytes(b'different')
   with self.assertRaises(ValueError):validate_call(0,r,out,p)
 def test_actual_canonical_target_spec_list_is_complete(self):
  with tempfile.TemporaryDirectory()as tmp:
   p,r,out=self.fixture(Path(tmp).resolve());r['target_spec_sha256s']=['0'*64]
   with self.assertRaisesRegex(ValueError,'target canonical'):validate_call(0,r,out,p)
 def test_source_locator_is_not_stage_join_key(self):
  f=dict(program_hash='1'*16,hardware_stage='local',logical_stage='vertex',permutation_hash='2'*16,observed_spirv_sha256='3'*64,companion_key='4'*64,input_integrity_sha256='5'*64)
  a=association_key(f,'6'*64);f['companion_key']='7'*64;f['input_integrity_sha256']='8'*64;self.assertEqual(a,association_key(f,'6'*64))
 def test_generic_prepare_visits_all_and_reuses_exact_index(self):
  from game_companion_modules import prepare
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();gpu=root/'gpu';auto=gpu/'automatic-game-inputs';capture=auto/'capture';capture.mkdir(parents=True);store=auto/'module-store';store.mkdir();profile=root/'profile.bin';profile.write_bytes(b'authored');profile_sha=file_sha(profile);index=dict(companions={});counter=dict(inventory=0,module=0);nonce='a'*64;cohort='b'*64
   def native(component,path,out,*args):
    p=json.loads(Path(path).read_text());out.mkdir()
    if p['kind']=='owned-fetch-companion-inventory':
     counter['inventory']+=1;values=[]
     for n in range(21):
      spec=b'canonical'+bytes([n]);spv=b'authored'+bytes([n]);values.append(dict(companion_key=f'{n+1:064x}',input_integrity_sha256=f'{n+100:064x}',program_hash=f'{n+1:016x}',hardware_stage='vertex',logical_stage='vertex',permutation=0,permutation_hash=f'{n+200:016x}',reference_origin=1,canonical_spec_sha256=hashlib.sha256(spec).hexdigest(),code_sha256='c'*64,observed_spirv_sha256=hashlib.sha256(spv).hexdigest(),observed_spirv_bytes=len(spv)))
     (out/'inventory.jsonl').write_text(''.join(json.dumps(v)+'\n'for v in values));return dict(accepted=21,rejected=0)
    counter['module']+=1;f=p['expected'];n=int(f['program_hash'],16)-1;(out/'module.spv').write_bytes(b'authored'+bytes([n]));(out/'canonical-spec.bin').write_bytes(b'canonical'+bytes([n]));r=dict(candidate_only=True,companion_contract_schema=1,input_packet_sha256=file_sha(path),source_compiler_sha256='d'*64,status='completed',association=f,spirv_sha256=f['observed_spirv_sha256'],spirv_bytes=f['observed_spirv_bytes'],target_spec_sha256s=[])
    for k in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read','full_final_pso_ready'):r[k]=False
    for k in('native_module_compiled','expected_spirv_equal','runtime_after_equal','canonical_specialization_equal','program_base_from_owned_snapshot','current_generated_srt_verified','target_canonical_specs_verified'):r[k]=True
    (out/'result.json').write_text(json.dumps(r));return r
   args=dict(context=dict(capture_root=capture,game_root=root),gpu=gpu,root=store,component=dict(library='/not-executed',library_sha256='d'*64,module_consumer_sha256='e'*64),identity={},catalog={},cohort=cohort,nonce=nonce,profiles={profile_sha:dict(path=str(profile),sha256=profile_sha)},targets={},index=index,index_path=store/'index.json',check=lambda:None,deadline=time.monotonic()+60,native_runner=native)
   report,ready=prepare(**args);self.assertEqual(counter['module'],21);self.assertEqual(len(ready),21);self.assertEqual(report['counts']['native_completed'],21)
   report,ready=prepare(**args);self.assertEqual(counter['module'],21);self.assertEqual(report['counts']['verified_existing'],21);self.assertEqual(counter['inventory'],2)
class TargetProductReuse(unittest.TestCase):
 fixture=TypedContracts.fixture
 def entry(self,root):
  p,r,out=self.fixture(root);profile=Path(p['profile']['path']);p['profile']['sha256']=file_sha(profile);p.update(selected_backend='/authored-library',game_root=str(root),automatic_preparation=dict(schema=1,nonce='a'*64,cohort='b'*64));target=profile.parent/'target.bin';target.write_bytes(b'authored target');p['target_specs']=[dict(path=str(target),sha256=file_sha(target))]
  packet=profile.parent/'packet.json';packet.write_text(json.dumps(p));r['input_packet_sha256']=file_sha(packet);r['target_spec_sha256s']=[file_sha(target)];receipt=out/'result.json';receipt.write_text(json.dumps(r))
  entry=dict(packet=str(packet),packet_sha256=file_sha(packet),receipt=str(receipt),receipt_sha256=file_sha(receipt),spirv=str(out/'module.spv'),spirv_sha256=r['spirv_sha256'],facts=p['expected'],source_input_identity=p['expected']['input_integrity_sha256'],native_typed_companion=True,target_spec_sha256s=r['target_spec_sha256s'])
  args=dict(profile_sha=p['profile']['sha256'],component=dict(library='/authored-library',library_sha256='6'*64,module_consumer_sha256='e'*64),nonce='a'*64,cohort='b'*64,game_root=root)
  return p,entry,args
 def test_different_capture_is_same_product_without_claiming_same_source(self):
  from game_companion_modules import target_product_key
  with tempfile.TemporaryDirectory()as tmp:
   p,e,a=self.entry(Path(tmp).resolve());other=dict(e['facts'],companion_key='7'*64,input_integrity_sha256='8'*64,reference_origin=1)
   self.assertNotEqual(other,e['facts']);self.assertEqual(target_product_key(other,e['target_spec_sha256s']),target_product_key(e['facts'],e['target_spec_sha256s']))
 def test_every_product_dimension_and_complete_target_set_matters(self):
  from game_companion_modules import target_product_key
  with tempfile.TemporaryDirectory()as tmp:
   p,e,a=self.entry(Path(tmp).resolve());f=e['facts'];key=target_product_key(f,e['target_spec_sha256s'])
   for field in set(FIELDS)-{'companion_key','input_integrity_sha256','reference_origin'}:
    changed=dict(f);changed[field]=999 if type(f[field])is int else 'different';self.assertNotEqual(key,target_product_key(changed,e['target_spec_sha256s']),field)
   self.assertNotEqual(key,target_product_key(f,[]))
 def test_old_source_and_actual_bytes_must_still_verify(self):
  from game_companion_modules import verified_entry
  for part in ('packet','receipt','spirv'):
   with tempfile.TemporaryDirectory()as tmp:
    p,e,a=self.entry(Path(tmp).resolve());self.assertEqual(verified_entry(e,**a),e);Path(e[part]).write_bytes(b'corrupt')
    with self.assertRaises(ValueError):verified_entry(e,**a)
 def test_profile_compiler_nonce_cohort_and_game_root_cannot_cross(self):
  from game_companion_modules import verified_entry
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();p,e,a=self.entry(root)
   for k,v in [('profile_sha','9'*64),('nonce','9'*64),('cohort','9'*64),('game_root',root/'other'),('component',dict(library='/other',library_sha256='6'*64))]:
    with self.assertRaises(ValueError):verified_entry(e,**dict(a,**{k:v}))
 def test_target_and_original_full_source_cannot_be_forged(self):
  from game_companion_modules import verified_entry
  with tempfile.TemporaryDirectory()as tmp:
   p,e,a=self.entry(Path(tmp).resolve())
   for k,v in [('source_input_identity','9'*64),('target_spec_sha256s',[]),('spirv_sha256','9'*64)]:
    with self.assertRaises(ValueError):verified_entry(dict(e,**{k:v}),**a)
 def test_repeated_capture_does_not_spawn_another_module_or_alias_source_uid(self):
  from game_companion_modules import prepare,association_key
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();p,e,a=self.entry(root);gpu=root/'gpu';capture=gpu/'automatic-game-inputs/capture';capture.mkdir(parents=True);store=gpu/'automatic-game-inputs/store';store.mkdir();index=dict(companions={'original-source-uid':e});facts=dict(e['facts'],companion_key='7'*64,input_integrity_sha256='8'*64,reference_origin=1);count=[]
   def native(component,path,out,*args):
    packet=json.loads(Path(path).read_text());self.assertEqual(packet['kind'],'owned-fetch-companion-inventory');out.mkdir();(out/'inventory.jsonl').write_text(json.dumps(facts)+'\n');count.append(1);return dict(accepted=1,rejected=0)
   report,ready=prepare(context=dict(capture_root=capture,game_root=root),gpu=gpu,root=store,component=a['component'],identity={},catalog={},cohort=a['cohort'],nonce=a['nonce'],profiles={a['profile_sha']:p['profile']},targets={association_key(facts,a['profile_sha']):{e['target_spec_sha256s'][0]:{}}},index=index,index_path=store/'index.json',check=lambda:None,deadline=time.monotonic()+20,native_runner=native)
   self.assertEqual(report['counts']['verified_target_reuses'],1);self.assertEqual(report['counts'].get('native_attempts',0),0);self.assertEqual(set(index['companions']),{'original-source-uid'});self.assertEqual(next(iter(ready.values()))['source_input_identity'],'2'*64)

class RequiredProductLookup(unittest.TestCase):
 fixture=TypedContracts.fixture
 entry=TargetProductReuse.entry
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.gpu=self.base/'gpu';self.auto=self.gpu/'automatic-game-inputs'
  self.store=self.auto/('d'*64);self.store.mkdir(parents=True);case=self.store/('companion-module-'+'1'*64+'-'+'2'*16);case.mkdir()
  self.p,self.e,self.a=self.entry(case);self.sources=[]
  for name in ('a','b'):
   source=self.auto/'source-views'/name/'view';source.mkdir(parents=True);source.chmod(0o500);self.sources.append(source)
  self.p['game_root']=str(self.sources[0]);packet=Path(self.e['packet']);packet.write_text(json.dumps(self.p));self.e['packet_sha256']=file_sha(packet)
  receipt=Path(self.e['receipt']);r=json.loads(receipt.read_text());r['input_packet_sha256']=file_sha(packet);receipt.write_text(json.dumps(r));self.e['receipt_sha256']=file_sha(receipt)
  self.capture=self.auto/'capture';self.capture.mkdir();cache=self.gpu/'cache/CUSA03173-metal-authored';cache.mkdir(parents=True)
  profile=cache/'profile.bin';profile.write_bytes(Path(self.p['profile']['path']).read_bytes());self.profile={'path':str(profile),'sha256':file_sha(profile)}
  self.spec=Path(self.p['target_specs'][0]['path']).read_bytes();meta=cache/'current.meta';meta.write_bytes(self.spec);self.meta={'path':str(meta),'sha256':file_sha(meta)}
  spv=cache/'current.spv';spv.write_bytes(Path(self.e['spirv']).read_bytes());self.spv={'path':str(spv),'sha256':file_sha(spv)}
  self.key=association_key(self.e['facts'],self.profile['sha256']);self.targets={self.key:{self.p['target_specs'][0]['sha256']:self.meta}}
  self.index=dict(companions={'original-owned-source':self.e});self.calls=[]
 def tearDown(self):
  for p in self.sources:p.chmod(0o700)
  self.tmp.cleanup()
 def native(self,component,path,out,*args):
  p=json.loads(Path(path).read_text());self.calls.append(p['kind']);self.assertEqual(p['kind'],'owned-fetch-companion-inventory');out.mkdir()
  return dict(accepted=0,rejected=0)
 def prepare(self,**changes):
  from game_companion_modules import prepare
  values=dict(context=dict(capture_root=self.capture,game_root=self.sources[0]),gpu=self.gpu,root=self.store,
   component=dict(self.a['component'],native_layout={}),identity={},catalog={},cohort=self.a['cohort'],nonce=self.a['nonce'],
   profiles={self.profile['sha256']:self.profile},targets=self.targets,index=self.index,index_path=self.store/'index.json',
   check=lambda:None,deadline=time.monotonic()+20,native_runner=self.native,required_only=True,
   required_code={self.key:self.e['facts']['code_sha256']},observed_modules={self.key:self.spv})
  values.update(changes)
  with patch('game_companion_modules.metadata_sections',return_value=({},dict(specialization=dict(offset=0,bytes=len(self.spec))))):return prepare(**values)
 def test_all_required_targets_use_original_proof_without_inventory_process(self):
  report,ready=self.prepare();self.assertEqual(self.calls,[]);self.assertTrue(report['historical_capture_inventory_skipped'])
  self.assertEqual(ready[self.key]['source_input_identity'],self.e['source_input_identity']);self.assertEqual(report['counts']['verified_required_products'],1)
 def test_other_registered_mod_view_with_same_complete_target_reuses_product(self):
  before=Path(self.e['packet']).read_bytes();report,ready=self.prepare(context=dict(capture_root=self.capture,game_root=self.sources[1]))
  self.assertEqual(self.calls,[]);self.assertEqual(ready[self.key],self.e);self.assertEqual(Path(self.e['packet']).read_bytes(),before)
  self.assertTrue(report['historical_capture_inventory_skipped'])
 def test_changed_actual_profile_spirv_or_spec_does_not_reuse(self):
  p=Path(self.profile['path']);before=p.read_bytes();p.write_bytes(b'changed profile')
  with self.assertRaises(ValueError):self.prepare()
  p.write_bytes(before)
  for ref in(self.spv,self.meta):
   p=Path(ref['path']);before=p.read_bytes();p.write_bytes(b'changed')
   report,ready=self.prepare();self.assertFalse(ready);self.assertEqual(report['gaps']['required-targets-unresolved'],1)
   p.write_bytes(before)
  self.assertEqual(len(self.calls),2)
 def test_new_required_state_or_different_gcn_still_requests_original_inventory(self):
  for change in(dict(required_code={self.key:'f'*64}),dict(targets={self.key:{'f'*64:self.meta}})):
   report,ready=self.prepare(**change);self.assertFalse(ready);self.assertEqual(report['status'],'partial')
  self.assertEqual(len(self.calls),2)
 def test_bad_lookup_hint_is_rebuilt_but_bad_native_receipt_is_not_promoted(self):
  self.index['companion_required_index_schema']=1;self.index['companion_required_index']='bad hint'
  report,ready=self.prepare();self.assertIn(self.key,ready);self.assertEqual(self.calls,[])
  Path(self.e['receipt']).write_text('bad actual native receipt')
  report,ready=self.prepare();self.assertFalse(ready);self.assertEqual(len(self.calls),1)
 def test_wrong_automatic_scope_or_unregistered_origin_never_reuses(self):
  report,ready=self.prepare(nonce='f'*64);self.assertFalse(ready)
  self.sources[0].chmod(0o700);report,ready=self.prepare();self.assertFalse(ready)
  self.assertEqual(len(self.calls),2)

if __name__=='__main__':unittest.main(verbosity=2)
