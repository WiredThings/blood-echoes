"""Result-admission controls; native invocation is a separate CPU-only runner.

No private data, game, GPU, Metal compiler or profile/layout assumptions needed.
"""
from pathlib import Path
import copy,hashlib,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_game_module_contracts import result_record,validate_call
from audit_game_module_variants import summarize

class NativeModuleContract(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.module=b'authored observable module bytes';self.sha=hashlib.sha256(self.module).hexdigest();self.packet=dict(program_hash='0000000000001234',expected_spirv=dict(sha256=self.sha,bytes=len(self.module)))
  self.base=dict(candidate_only=True,runtime_cache_import_allowed=False,gpu_started=False,native_info_srt_deserialized=False,old_walker_executed=False,guest_memory_read=False)
  self.success=dict(self.base,status='completed',program_hash=self.packet['program_hash'],expected_spirv_equal=True,current_generated_srt_verified=True,unknown_program_base_rejected_before_use=True,full_final_pso_ready=False,spirv_bytes=len(self.module),spirv_sha256=self.sha)
 def tearDown(self):self.tmp.cleanup()
 def outputs(self):
  (self.root/'module.spv').write_bytes(self.module);(self.root/'result.json').write_text(json.dumps(self.success));return self.root
 def test_native_completed_requires_exact_bytes_and_all_gates(self):validate_call(0,self.success,self.outputs(),self.packet)
 def test_clean_native_rejection_is_not_compile_success(self):validate_call(65,dict(self.base,status='rejected',error='authored negative'),self.root,self.packet,True)
 def test_rejection_with_partial_artifact_is_never_accepted(self):
  (self.root/'module.spv').write_bytes(b'partial')
  with self.assertRaises(ValueError):validate_call(65,dict(self.base,status='rejected'),self.root,self.packet,True)
 def test_explicit_rejected_diagnostic_requires_control_bytes(self):
  (self.root/'rejected-generated.spv').write_bytes(self.module);r=dict(self.base,status='rejected',error='retranslated SPIR-V differs from observed module')
  with self.assertRaises(ValueError):validate_call(65,r,self.root,self.packet,True)
  validate_call(65,r,self.root,self.packet,True,rejected_diagnostic=self.packet['expected_spirv'])
  (self.root/'rejected-generated.spv').write_bytes(b'wrong')
  with self.assertRaises(ValueError):validate_call(65,r,self.root,self.packet,True,rejected_diagnostic=self.packet['expected_spirv'])
 def test_success_with_forbidden_side_effect_flag_rejected(self):
  out=self.outputs()
  for key in('runtime_cache_import_allowed','gpu_started','native_info_srt_deserialized','old_walker_executed','guest_memory_read'):
   r=dict(self.success);r[key]=True
   with self.subTest(key=key),self.assertRaises(ValueError):validate_call(0,r,out,self.packet)
 def test_missing_or_false_equivalence_gate_rejected(self):
  out=self.outputs()
  for key in('expected_spirv_equal','current_generated_srt_verified','unknown_program_base_rejected_before_use'):
   for value in(False,None):
    r=dict(self.success);r[key]=value
    with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate_call(0,r,out,self.packet)
 def test_wrong_program_and_final_pso_claim_rejected(self):
  out=self.outputs()
  for key,value in [('program_hash','ffffffffffffffff'),('full_final_pso_ready',True),('candidate_only',False)]:
   r=dict(self.success);r[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):validate_call(0,r,out,self.packet)
 def test_native_error_exit_cannot_masquerade_as_success(self):
  with self.assertRaises(ValueError):validate_call(65,self.success,self.outputs(),self.packet)
 def test_spirv_digest_claim_without_real_byte_equality_rejected(self):
  out=self.outputs();(out/'module.spv').write_bytes(b'corrupt observed output')
  with self.assertRaises(ValueError):validate_call(0,self.success,out,self.packet)
 def test_extra_output_never_silently_accepted(self):
  out=self.outputs();(out/'unexpected-cache').write_bytes(b'x')
  with self.assertRaises(ValueError):validate_call(0,self.success,out,self.packet)
 def test_duplicate_or_missing_native_results_rejected(self):
  good='GAME_MODULE_RESULT '+json.dumps(self.success)
  self.assertEqual(result_record('ordinary log\n'+good+'\n'),self.success)
  for text in('',good+'\n'+good):
   with self.assertRaises(ValueError):result_record(text)
 def variant(self,profile='p',spec='s',spv='a',stage='compute'):
  return dict(program_hash='0000000000001234',hardware_stage=stage,static=dict(identity=dict(code_sha256='c'*64)),profile=dict(semantic_sha256=profile,reference=dict(sha256='r'*64)),stage=dict(serialized_spec_sha256=spec,spirv=dict(sha256=spv,bytes=20)))
 def test_same_recorded_inputs_with_different_spv_is_conflict_not_winner(self):
  r=summarize([self.variant(spv='a'),self.variant(spv='b')]);self.assertEqual(r['conflicting_recorded_input_groups'],1);self.assertFalse(r['root_cause_proven']);self.assertFalse(r['producer_compiler_identity_recorded']);self.assertFalse(r['runtime_cache_import_allowed'])
 def test_different_profile_or_spec_not_a_false_conflict(self):
  r=summarize([self.variant(profile='p'),self.variant(profile='q'),self.variant(spec='t')]);self.assertEqual(r['conflicting_recorded_input_groups'],0);self.assertEqual(len(r['groups']),3)
 def test_no_cross_hardware_stage_variant_merge(self):
  with self.assertRaises(ValueError):summarize([self.variant(),self.variant(stage='vertex')])
 def test_identical_profile_spec_and_spv_never_prove_producer_compatibility(self):
  r=summarize([self.variant(),self.variant()]);self.assertEqual(r['conflicting_recorded_input_groups'],0);self.assertFalse(r['producer_compatibility_proven']);self.assertFalse(r['observed_gcn_sha_recorded']);self.assertFalse(r['runtime_cache_import_allowed'])
if __name__=='__main__':unittest.main(verbosity=2)
