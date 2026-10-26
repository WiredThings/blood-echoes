"""Authored projection proofs: no raw descriptor synthesis or private fixture."""
from pathlib import Path
import copy,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from fetch_projection_inputs import project,FIELDS,input_identity,iter_candidates

def row(fmt=106):
 fields=dict(zip(FIELDS,[0,4,3,2,0,0,0,0,0]))
 return dict(fetch=dict(code_size=32,vertex_sgpr_offset=-1,instance_sgpr_offset=-1,attributes=[dict(fields=fields,vertex_specialization=dict(divisor=0,num_class=0,dst_select=0x01060504))]),pipeline_key_vertex_inputs=[dict(vertex_formats=[fmt]+[0]*31)],step_rates=dict(step_rate_0=2,step_rate_1=3))

class ProjectionControls(unittest.TestCase):
 def test_unique_semantic_projection_has_no_descriptor_members(self):
  p=project(row());a=p['attributes'][0];self.assertEqual(a['read_number_conversion'],0);self.assertEqual(a['number_class'],0);self.assertTrue(a['nonnull']);self.assertFalse(p['raw_Vsharp_reconstructed']);self.assertEqual(a['effective_dst_select'],[4,5,6,1]);self.assertFalse(any(k in str(p)for k in('base_address','stride','num_records')))
 def test_all_six_proven_format_classes(self):
  for fmt in(37,91,100,103,106,109):
   with self.subTest(fmt=fmt):self.assertEqual(project(row(fmt))['attributes'][0]['read_number_conversion'],0)
 def test_ambiguous_formats_cannot_be_projected_as_none(self):
  for fmt in(41,96,82,14):
   r=row(fmt);r['fetch']['attributes'][0]['vertex_specialization']['num_class']=2 if fmt==41 else 1
   with self.subTest(fmt=fmt),self.assertRaisesRegex(ValueError,'conversion-ambiguous'):project(r)
 def test_one_ambiguous_key_invalidates_whole_record(self):
  r=row();r['pipeline_key_vertex_inputs'].append(dict(vertex_formats=[41]+[0]*31))
  with self.assertRaisesRegex(ValueError,'conversion-ambiguous'):project(r)
 def test_distinct_unique_formats_can_prove_same_compile_equivalence(self):
  r=row();r['pipeline_key_vertex_inputs'].append(dict(vertex_formats=[103]+[0]*31));self.assertEqual(project(r)['attributes'][0]['proof']['backend_formats'],[103,106])
 def test_null_cannot_be_guessed_from_key_format(self):
  r=row();r['fetch']['attributes'][0]['vertex_specialization'].update(divisor=0,num_class=0,dst_select=0)
  with self.assertRaisesRegex(ValueError,'nonnull'):project(r)
 def test_all_nine_fields_preserved_including_original_equality_omissions(self):
  r=row();r['fetch']['attributes'][0]['fields']['inst_offset']=7;p=project(r);self.assertEqual(p['attributes'][0]['fields']['inst_offset'],7);self.assertEqual(set(p['attributes'][0]['fields']),set(FIELDS))
 def test_typed_override_requires_separate_proof(self):
  r=row();r['fetch']['attributes'][0]['fields']['data_format']=10
  with self.assertRaisesRegex(ValueError,'typed overrides'):project(r)
 def test_observed_class_cannot_be_changed_to_match_a_guess(self):
  r=row();r['fetch']['attributes'][0]['vertex_specialization']['num_class']=2
  with self.assertRaisesRegex(ValueError,'class must match'):project(r)
 def test_effective_swizzle_enum_must_be_legal(self):
  r=row();r['fetch']['attributes'][0]['vertex_specialization']['dst_select']=0x03060504
  with self.assertRaisesRegex(ValueError,'component enum'):project(r)
 def test_step_rate_matches_recorded_divisor(self):
  r=row();r['fetch']['attributes'][0]['fields']['instance_data']=1;r['fetch']['attributes'][0]['vertex_specialization']['divisor']=2;self.assertEqual(project(r)['attributes'][0]['divisor'],2);r['fetch']['attributes'][0]['vertex_specialization']['divisor']=3
  with self.assertRaisesRegex(ValueError,'divisor'):project(r)
class IdentityControls(unittest.TestCase):
 def source(self):
  r=row();r.update(static_identity=dict(program_hash='1'*16,hardware_stage='vertex',code_sha256='2'*64),observed=dict(metadata=dict(path='/private/a.meta',sha256='3'*64,bytes=300)))
  r['pipeline_key_vertex_inputs'][0]['source_reference']=dict(path='/private/a.key',sha256='4'*64,bytes=400)
  return r
 def ident(self,r,**changes):
  args=dict(profile_ref=dict(path='/a/profile.bin',sha256='5'*64,bytes=100),spirv_ref=dict(path='/a/a.spv',sha256='6'*64,bytes=200),layout=dict(pointer_bytes=8,little_endian=True,profile_fields=[]),game_identity=dict(TITLE_ID='CUSA03173',APP_VER='01.09'));args.update(changes);return input_identity(r,**args)['source_input_id']
 def test_paths_and_key_proof_order_are_not_content_identity(self):
  a=self.source();b=copy.deepcopy(a);b['observed']['metadata']['path']='/other/b.meta';b['pipeline_key_vertex_inputs'][0]['source_reference']['path']='/other/b.key';b['pipeline_key_vertex_inputs']*=2;self.assertEqual(self.ident(a),self.ident(b))
 def test_same_program_different_metadata_profile_or_spirv_never_collapses(self):
  a=self.source();b=copy.deepcopy(a);b['observed']['metadata']['sha256']='7'*64;self.assertNotEqual(self.ident(a),self.ident(b))
  for name in('profile_ref','spirv_ref'):
   self.assertNotEqual(self.ident(a),self.ident(a,**{name:dict(sha256='8'*64,bytes=100)}))
 def test_all_nine_fields_participate_in_identity(self):
  a=self.source();b=copy.deepcopy(a);b['fetch']['attributes'][0]['fields']['inst_offset']=1;self.assertNotEqual(self.ident(a),self.ident(b))
 def test_default_iterator_visits_all_and_preserves_explicit_gaps(self):
  with tempfile.TemporaryDirectory()as tmp:
   path=Path(tmp)/'rows.jsonl';values=[row()for _ in range(7)]+[row(41)];path.write_text(''.join(json.dumps(r)+'\n'for r in values));result=list(iter_candidates(path));self.assertEqual([x['status']for x in result],['eligible']*7+['deferred'])
if __name__=='__main__':unittest.main(verbosity=2)
