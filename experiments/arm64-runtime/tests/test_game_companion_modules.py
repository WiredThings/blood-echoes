"""Typed companion receipt controls; no game/GCN/private input fixture."""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,time,unittest
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
if __name__=='__main__':unittest.main(verbosity=2)
