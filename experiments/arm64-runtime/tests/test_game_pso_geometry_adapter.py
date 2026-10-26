"""Authored adapter gates, not native GCN/Metal success evidence.

Only the canonical binary decoder is replaced with the existing identity
validator: these invented tiny buffers are deliberately not serialized shaders.
All SHA/reference/schema/receipt/copy/queue bindings below use product code.
"""
import copy,hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import game_pso_geometry_adapter as adapter
from game_pso_progress_identity import input_identity,digest
from prepare_game_psos import new_bytes

class Adapter(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='authored-gs-adapter-');self.root=Path(self.tmp.name).resolve();self.cache=self.root/'old-cache';self.cache.mkdir();self.index=self.root/'owned-index';self.index.mkdir();self.base=self.index/'source';self.base.mkdir()
  self.decode=patch.object(adapter,'require_geometry',lambda unit,layout:input_identity(unit));self.decode.start();self.addCleanup(self.decode.stop);self.addCleanup(self.tmp.cleanup)
  ref=lambda name,data:new_bytes(self.cache,name,data)
  profile=ref('profile.bin',b'authored-profile');key=ref('key.bin',b'authored-key');stages=[]
  for n,(logical,hw)in enumerate((('vertex','export'),('geometry','geometry'))):
   raw=bytes([n+1])*40;meta=ref(logical+'.meta',raw);spv=ref(logical+'.spv',b'authored-spv-'+logical.encode());stages.append(dict(logical_stage=logical,hardware_stage=hw,program_hash=f'{n+1:016x}',cache_local_permutation=n,permutation_hash=f'{n+7:016x}',metadata=meta,spirv=spv,serialized_spec_sha256=hashlib.sha256(raw).hexdigest()))
  refs=[profile,key]
  for s in stages:refs.extend((s['metadata'],s['spirv']))
  self.unit=dict(schema=2,record_type='offline-pso-input',namespace=dict(cache=str(self.cache),cache_id='a'*64),profile=dict(reference=profile,semantic_sha256='b'*64),key=dict(reference=key,decoded=dict(kind='graphics')),stages=stages,recipe_blob_identity='c'*64,classification=dict(family='geometry-existing-session-span-rejected'),session_rejections=['geometry-session-chain-not-admitted'],execution_policy=dict(runtime_cache_import_allowed=False,candidate_preparation=dict(policy_eligible=False,frozen_input_set_identity=digest(refs)),actual_final_read_admission=dict(allowed=False)))
  self.layout={'Backend::Bindings.size':4,'RuntimeInfo.size':32,'RuntimeInfo.hw':0,'HwGeometryRuntimeInfo.vs_copy':4,'GraphicsPipelineKey.size':12}
  take=lambda name,path:new_bytes(self.base,name,Path(path).read_bytes());self.packet=dict(packet_schema=2,manifest_schema=2,geometry_pso_schema=1,kind='owned-gcn-geometry-pso-input',candidate_only=True,runtime_cache_import_allowed=False,native_info_srt_allowed=False,walker_bytes_passed=0,original_cache=str(self.cache),cache_id='a'*64,recipe_blob_identity='c'*64,frozen_input_set_identity=digest(refs),layout=dict(bindings_size=4,runtime_size=32,graphics_key_size=12),current_cohort='old-cohort',allow_compile=True,artifact_cache_dir='/old-artifact',key=take('key.bin',key['path']),retranslation_profile=take('profile.bin',profile['path']),stages=[])
  for n,s in enumerate(stages):
   spec=bytes([n+1])*40
   if n:spec=spec[:8]+bytes(16)+spec[24:]
   proof=dict(title_id='CUSA03173',app_ver='01.09',code_prefix_verified=True,code_sha256='d'*64,container_sha256='e'*64,entry_sha256='f'*64)
   r=dict(logical_stage=s['logical_stage'],hardware_stage=s['hardware_stage'],program_hash=s['program_hash'],permutation=s['cache_local_permutation'],permutation_hash=s['permutation_hash'],spec=new_bytes(self.base,f'spec-{n}',spec),spirv=take(f'spv-{n}',s['spirv']['path']),module_input_packet_sha256=str(n+1)*64,source_provenance=proof);r['spec'].update(source_offset=24,native_spec_only_reader_allowed=True)
   receipt=dict(module_retranslation_schema=1,status='completed',candidate_only=True,runtime_cache_import_allowed=False,gpu_started=False,native_info_srt_deserialized=False,old_walker_executed=False,guest_memory_read=False,expected_spirv_equal=True,current_generated_srt_verified=True,unknown_program_base_rejected_before_use=True,input_packet_sha256=r['module_input_packet_sha256'],input_code_sha256=proof['code_sha256'],input_spec_sha256=r['spec']['sha256'],input_profile_sha256=profile['sha256'],spirv_sha256=r['spirv']['sha256'],program_hash=r['program_hash'],hardware_stage=r['hardware_stage'],logical_stage=r['logical_stage'],source_provenance=proof)
   if n:
    code=new_bytes(self.base,'copy.bin',b'authored-GS-copy');cp=dict(proof,code_sha256=code['sha256']);r.update(spec_rebinding=dict(original_spec_sha256=s['serialized_spec_sha256'],runtime_span_offset=8,bytes=16,serialized_span_zeroed=True,retired_pointer_exported=False),gs_copy=dict(code=code,program_hash='1'*16,source_provenance=cp));receipt.update(owned_geometry_copy_rebound=True,geometry_copy_bytes=code['bytes'],gs_copy_sha256=code['sha256'],gs_copy_source_provenance=cp)
   r['retranslation_receipt']=new_bytes(self.base,f'receipt-{n}.json',json.dumps(receipt).encode());self.packet['stages'].append(r)
  self.path=self.base/'packet.json';self.write_packet()
 def write_packet(self):
  self.path.write_text(json.dumps(self.packet));self.reference=adapter.packet_reference(self.path,self.index)
 def test_admit_and_original_queue_freeze(self):
  u=adapter.admit(self.unit,self.layout,self.reference,self.index);self.assertTrue(u['execution_policy']['candidate_preparation']['policy_eligible']);self.assertFalse(self.unit['execution_policy']['candidate_preparation']['policy_eligible']);self.assertEqual(input_identity(u),input_identity(self.unit))
  target=self.root/'worker-packet';target.mkdir();p,held=adapter.freeze(u,self.layout,target,'9'*64,False,artifact_cache_dir=self.root/'selected-cache')
  self.assertEqual(p['packet_schema'],2);self.assertEqual(p['current_cohort'],'9'*64);self.assertFalse(p['allow_compile']);self.assertEqual(p['artifact_cache_dir'],str(self.root/'selected-cache'));self.assertTrue((target/'packet.json').is_file());self.assertGreater(len(held),0)
  for r in p['stages']:
   for name in('spec','spirv','retranslation_receipt'):self.assertEqual(Path(r[name]['path']).parent,target)
 def module_results(self):
  results={}
  for s,r in zip(self.unit['stages'],self.packet['stages']):
   m=dict(program_hash=r['program_hash'],hardware_stage=r['hardware_stage'],logical_stage=r['logical_stage'],profile=self.packet['retranslation_profile'],code=dict(sha256=r['source_provenance']['code_sha256']),spec=r['spec'],expected_spirv=r['spirv'],source_provenance=r['source_provenance'],source_metadata=dict(sha256=s['metadata']['sha256']))
   if s['hardware_stage']=='geometry':m.update(gs_copy=r['gs_copy'],spec_rebinding=r['spec_rebinding'])
   module=new_bytes(self.base,r['logical_stage']+'-module.json',json.dumps(m).encode());receipt=json.loads(Path(r['retranslation_receipt']['path']).read_text());receipt['input_packet_sha256']=module['sha256'];proof=new_bytes(self.base,r['logical_stage']+'-module-result.json',json.dumps(receipt).encode())
   results[r['logical_stage']]=dict(input_id='a'*64,packet=module['path'],packet_sha256=module['sha256'],receipt=proof['path'],receipt_sha256=proof['sha256'],spirv=r['spirv']['path'],spirv_sha256=r['spirv']['sha256'],metadata_sha256=s['metadata']['sha256'],profile_sha256=self.unit['profile']['reference']['sha256'],program_hash=s['program_hash'],hardware_stage=s['hardware_stage'],logical_stage=s['logical_stage'],permutation_hash=s['permutation_hash'])
  return results
 def test_generic_controller_freeze_recipe(self):
  results=self.module_results();dest=self.index/'from-controller';ref=adapter.freeze_recipe(self.unit,self.layout,results,dest,'9'*64,self.root/'selected-cache');u=adapter.admit(self.unit,self.layout,ref,self.index);self.assertIn('owned_geometry_pso',u);self.assertEqual(json.loads((dest/'packet.json').read_text())['packet_schema'],2)
 def test_generic_controller_cross_metadata_and_missing_stage_rejected(self):
  results=self.module_results();bad=copy.deepcopy(results);bad['geometry']['metadata_sha256']='0'*64
  with self.assertRaises(ValueError):adapter.freeze_recipe(self.unit,self.layout,bad,self.index/'bad','9'*64,self.root/'selected-cache')
  del results['geometry']
  with self.assertRaises(ValueError):adapter.freeze_recipe(self.unit,self.layout,results,self.index/'missing','9'*64,self.root/'selected-cache')
 def test_old_schema_missing_stage_and_cross_recipe_rejected(self):
  changes=[('packet_schema',1),('recipe_blob_identity','0'*64),('original_cache','/other-cache'),('stages',self.packet['stages'][:1]),('walker_bytes_passed',1)]
  saved=copy.deepcopy(self.packet)
  for k,v in changes:
   with self.subTest(field=k):
    self.packet=copy.deepcopy(saved);self.packet[k]=v;self.write_packet()
    with self.assertRaises(ValueError):adapter.admit(self.unit,self.layout,self.reference,self.index)
 def test_same_program_different_permutation_rejected(self):
  self.packet['stages'][0]['permutation']+=1;self.write_packet()
  with self.assertRaises(ValueError):adapter.admit(self.unit,self.layout,self.reference,self.index)
 def test_index_root_and_link_refused(self):
  other=self.root/'other';other.mkdir()
  with self.assertRaises(ValueError):adapter.packet_reference(self.path,other)
  link=self.base/'linked';link.symlink_to(self.path)
  with self.assertRaises(ValueError):adapter.packet_reference(link,self.index)
 def test_resolver_miss_keeps_original_exclusion(self):
  source=self.root/'candidates';source.write_text(json.dumps(self.unit)+'\n');dest=self.root/'combined';result=adapter.augment_manifest(source,dest,layout=self.layout,resolver=lambda *a:None,cache_root=self.index);u=json.loads(dest.read_text());self.assertEqual(result['missing'],1);self.assertFalse(u['execution_policy']['candidate_preparation']['policy_eligible']);self.assertNotIn('owned_geometry_pso',u)
 def test_resolver_success_and_rejected_source_use_same_manifest(self):
  source=self.root/'candidates';source.write_text(json.dumps(self.unit)+'\n');dest=self.root/'combined';result=adapter.augment_manifest(source,dest,layout=self.layout,resolver=lambda *a:self.reference,cache_root=self.index);self.assertEqual(result['admitted'],1);self.assertIn('owned_geometry_pso',json.loads(dest.read_text()))
  self.packet['stages'][1]['spec_rebinding']['serialized_span_zeroed']=False;self.write_packet();bad=self.root/'rejected';result=adapter.augment_manifest(source,bad,layout=self.layout,resolver=lambda *a:self.reference,cache_root=self.index);self.assertEqual(result['rejected'],1);self.assertFalse(json.loads(bad.read_text())['execution_policy']['candidate_preparation']['policy_eligible'])
 def test_no_other_legacy_exclusion_is_upgraded(self):
  self.unit['session_rejections'].append('motion-vertex-existing-preload-rejection')
  with self.assertRaises(ValueError):adapter.admit(self.unit,self.layout,self.reference,self.index)

if __name__=='__main__':unittest.main()
