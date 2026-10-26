"""Authored non-GPU controls for independent GS producer admission.

The fixture is data invented here, never a game/cache payload. Actual 77-recipe
closure and native receipt validation are a separate evidence ledger.
"""
import copy,sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from complete_geometry_psos import module_identity,stage_identity,verify_receipt,verify_stage_binding,require_plan,cpu_state_root,require_cpu_state

def fixture(geometry=True):
 h=lambda n:format(n,'064x')
 stage=dict(logical_stage='geometry'if geometry else'fragment',hardware_stage='geometry'if geometry else'fragment',program_hash='0123456789abcdef',cache_local_permutation=3,permutation_hash=h(4),metadata=dict(sha256=h(5)),spirv=dict(sha256=h(6)),serialized_spec_sha256=h(7))
 unit=dict(namespace=dict(cache='/authored/cache-A'),profile=dict(reference=dict(sha256=h(8))))
 p=dict(program_hash=stage['program_hash'],hardware_stage=stage['hardware_stage'],logical_stage=stage['logical_stage'],code=dict(sha256=h(9)),spec=dict(sha256=h(10)if geometry else h(7)),profile=dict(sha256=h(8)),expected_spirv=dict(sha256=h(6)),source_provenance=dict(code_sha256=h(9),container_sha256=h(11)),source_metadata=dict(sha256=h(5)))
 if geometry:p.update(spec_rebinding=dict(original_spec_sha256=h(7)),gs_copy=dict(code=dict(sha256=h(12),bytes=16),source_provenance=dict(code_sha256=h(12),container_sha256=h(11))))
 r=dict(module_retranslation_schema=1,status='completed',candidate_only=True,runtime_cache_import_allowed=False,gpu_started=False,native_info_srt_deserialized=False,old_walker_executed=False,guest_memory_read=False,expected_spirv_equal=True,current_generated_srt_verified=True,unknown_program_base_rejected_before_use=True,input_packet_sha256=h(13),input_code_sha256=h(9),input_spec_sha256=p['spec']['sha256'],input_profile_sha256=h(8),spirv_sha256=h(6),program_hash=p['program_hash'],hardware_stage=p['hardware_stage'],logical_stage=p['logical_stage'],source_provenance=p['source_provenance'])
 if geometry:r.update(owned_geometry_copy_rebound=True,geometry_copy_bytes=16,gs_copy_sha256=h(12),gs_copy_source_provenance=p['gs_copy']['source_provenance'])
 return unit,stage,p,r,h(13)

class GeometryProducer(unittest.TestCase):
 def test_constructor_state_is_isolated_and_evidence_rejected(self):
  with tempfile.TemporaryDirectory(prefix='authored-geometry-state-')as tmp:
   p=Path(tmp).resolve();(p/'state').mkdir();(p/'evidence').mkdir();s=cpu_state_root(p);self.assertTrue(s.is_relative_to(p/'state'));self.assertEqual(require_cpu_state(p,s),s)
   for rejected in(p/'state',p/'evidence'):
    with self.subTest(rejected=rejected),self.assertRaises(ValueError):require_cpu_state(p,rejected)
   link=p/'state'/'linked';link.symlink_to(p/'evidence',target_is_directory=True)
   with self.assertRaises(ValueError):require_cpu_state(p,link)
 def test_plan_cannot_claim_native_ready_or_drop_all_modules(self):
  j=dict(module_id='a'*64,packet='/authored/module/packet.json',packet_sha256='b'*64,native_ready=False)
  p=dict(schema=1,kind='owned-gcn-geometry-pso-plan',GPU_started=False,game_started=False,native_PSOs_ready=False,old_GS_gate_changed=False,modules=[j]);require_plan(p)
  for field in('GPU_started','game_started','native_PSOs_ready','old_GS_gate_changed'):
   bad=copy.deepcopy(p);bad[field]=True
   with self.subTest(field=field),self.assertRaises(ValueError):require_plan(bad)
  for jobs in([], [j,j], [dict(j,native_ready=True)]):
   with self.subTest(jobs=jobs),self.assertRaises(ValueError):require_plan(dict(p,modules=jobs))
 def test_exact_export_and_geometry_receipts(self):
  for gs in(False,True):
   u,s,p,r,h=fixture(gs);verify_receipt(p,r,h);verify_stage_binding(u,s,p)
 def test_each_producer_receipt_gate_rejects(self):
  u,s,p,r,h=fixture()
  # Every required claim must independently be present and exact, including
  # copy ownership and byte extent; a plain successful process is insufficient.
  for key in r:
   with self.subTest(missing=key):
    bad=copy.deepcopy(r);del bad[key]
    with self.assertRaises(ValueError):verify_receipt(p,bad,h)
  for key,value in [('module_retranslation_schema',True),('input_code_sha256','0'*64),('input_spec_sha256','0'*64),('input_profile_sha256','0'*64),('hardware_stage','export'),('geometry_copy_bytes',True),('geometry_copy_bytes',12),('gs_copy_sha256','0'*64),('source_provenance',{})]:
   with self.subTest(changed=key,value=value):
    bad=copy.deepcopy(r);bad[key]=value
    with self.assertRaises(ValueError):verify_receipt(p,bad,h)
 def test_packet_digest_is_not_optional(self):
  u,s,p,r,h=fixture()
  with self.assertRaises(ValueError):verify_receipt(p,r,'f'*64)
 def test_same_program_is_not_same_module(self):
  u,s,p,r,h=fixture();base=module_identity(u,s,'a'*64)
  for field in('cache_local_permutation','permutation_hash','logical_stage','hardware_stage','serialized_spec_sha256'):
   with self.subTest(field=field):
    bad=copy.deepcopy(s);bad[field]=4 if field=='cache_local_permutation'else'changed';self.assertNotEqual(base,module_identity(u,bad,'a'*64))
  for ref in('metadata','spirv'):
   bad=copy.deepcopy(s);bad[ref]['sha256']='f'*64;self.assertNotEqual(base,module_identity(u,bad,'a'*64))
  bad=copy.deepcopy(u);bad['profile']['reference']['sha256']='f'*64;self.assertNotEqual(base,module_identity(bad,s,'a'*64));self.assertNotEqual(base,module_identity(u,s,'b'*64))
 def test_cache_local_stage_identity_preserves_actual_cache(self):
  u,s,p,r,h=fixture();bad=copy.deepcopy(u);bad['namespace']['cache']='/authored/cache-B';self.assertNotEqual(stage_identity(u,s),stage_identity(bad,s))
 def test_cross_profile_spec_metadata_and_spirv_refused(self):
  for gs in(False,True):
   u,s,p,r,h=fixture(gs)
   for path in(('profile','sha256'),('expected_spirv','sha256'),('source_metadata','sha256'),('spec_rebinding','original_spec_sha256')if gs else('spec','sha256')):
    with self.subTest(gs=gs,path=path):
     bad=copy.deepcopy(p);bad[path[0]][path[1]]='f'*64
     with self.assertRaises(ValueError):verify_stage_binding(u,s,bad)

if __name__=='__main__':unittest.main()
