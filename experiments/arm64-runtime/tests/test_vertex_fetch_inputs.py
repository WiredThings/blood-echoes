"""Authored CPU fixtures: never follow guest pointers or construct GPU resources."""
from pathlib import Path
import json,struct,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import vertex_fetch_inputs as v

def walk(*ops):return b''.join(struct.pack('<3I',*o)for o in ops)
def attr(pointer=2,offset=0):return v.decode_attribute(dict(raw_hex=bytes([0,4,3,pointer,offset,0,0,0,0]).hex()),0)
def flat():return [0xFFFFFFFF]*16+[11,22,33,44]

class FetchInputs(unittest.TestCase):
 def test_fetch_all_nine_fields_and_no_semantic_namespace_guess(self):
  a=v.decode_attribute(dict(raw_hex=bytes([0,8,4,2,16,3,7,11,4]).hex()),0)
  self.assertEqual([a[f]for f in v.FIELDS],[0,8,4,2,16,3,7,11,4]);self.assertEqual(a['semantic_namespace'],'fetch-parser-sequential-index')
 def test_fetch_semantic_order_width_pointer_and_instance_reject(self):
  for data in ([1,4,3,2,0,0,0,0,0],[0,4,0,2,0,0,0,0,0],[0,4,3,3,0,0,0,0,0],[0,4,3,2,0,4,0,0,0]):
   with self.assertRaises(ValueError):v.decode_attribute(dict(raw_hex=bytes(data).hex()),0)
  with self.assertRaises(ValueError):v.decode_attribute(dict(raw_hex='00'),0)
 def test_complete_descriptor_has_four_exact_symbolic_word_origins(self):
  mapping=v.flat_origins(walk((1,2,0),*( (4,i,16+i)for i in range(4)),(3,0,0),(0,0,0)),flat())
  r=v.descriptor_provenance(attr(),mapping);self.assertEqual(r['status'],'exact-flat-descriptor-provenance');self.assertEqual(r['descriptor_words'],[11,22,33,44]);self.assertFalse(r['guest_pointer_followed']);self.assertFalse(r['native_consumer_authorized'])
 def test_wrong_root_cannot_substitute_even_if_all_words_present(self):
  mapping=v.flat_origins(walk((1,6,0),*((4,i,16+i)for i in range(4)),(3,0,0),(0,0,0)),flat())
  r=v.descriptor_provenance(attr(),mapping);self.assertEqual(r['status'],'fetch-Vsharp-not-captured');self.assertEqual(r['missing_words'],[0,1,2,3]);self.assertNotIn('descriptor_words',r)
 def test_nested_pointer_is_not_direct_Vsharp_table(self):
  mapping=v.flat_origins(walk((1,2,0),(1,0,0),*((4,i,16+i)for i in range(4)),(3,0,0),(3,0,0),(0,0,0)),flat())
  self.assertEqual(v.descriptor_provenance(attr(),mapping)['status'],'fetch-Vsharp-not-captured')
 def test_partial_words_and_offset_stay_explicit(self):
  mapping=v.flat_origins(walk((1,2,0),(4,4,16),(4,5,17),(3,0,0),(0,0,0)),flat())
  r=v.descriptor_provenance(attr(offset=4),mapping);self.assertEqual(r['missing_words'],[2,3]);self.assertNotIn('descriptor_words',r)
 def test_dynamic_offsets_are_not_evaluated_from_final_flat(self):
  mapping=v.flat_origins(walk((6,16,0),(2,0,0),(4,0,17),(3,0,0),(0,0,0)),flat())
  self.assertEqual(mapping['status'],'dynamic-or-unsupported-walker');self.assertFalse(mapping['origins'])
 def test_repeated_flat_destination_does_not_prove_earlier_value(self):
  with self.assertRaisesRegex(ValueError,'overwritten'):v.flat_origins(walk((1,2,0),(4,0,16),(4,1,16),(3,0,0),(0,0,0)),flat())
 def test_conflicting_two_destinations_for_same_origin_are_rejected(self):
  mapping=v.flat_origins(walk((1,2,0),(4,0,16),(4,0,17),(3,0,0),(0,0,0)),flat())
  self.assertEqual(v.descriptor_provenance(attr(),mapping)['status'],'conflicting-captured-word')
 def test_malformed_walkers_and_bounds_reject(self):
  cases=[walk((3,0,0),(0,0,0)),walk((1,15,0),(3,0,0),(0,0,0)),walk((1,2,0),(4,0,0),(3,0,0),(0,0,0)),walk((0,0,0),(0,0,0)),walk((1,2,0),(0,0,0)),walk((1,2,0),(4,0,20),(3,0,0),(0,0,0)),b'\0']
  for code in cases:
   with self.assertRaises(ValueError):v.flat_origins(code,flat())
 def test_cache_key_requires_exact_local_permutation_not_program_hash(self):
  m=dict(permutation_hash='000000000000abcd',logical_stage='vertex');key=dict(kind='graphics',stages={'vertex':'000000000000abcd'},name='a.key',raw_key_sha256='a'*64,vertex_formats=[1],vertex_attributes=[])
  self.assertEqual(len(v.key_links(m,[key])),1);key['stages']['vertex']='000000000000bcde';self.assertFalse(v.key_links(m,[key]))
 def test_header_declarations_and_size_must_both_match(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'fetch.h';p.write_text('\n'.join('u8 '+f+';'for f in v.FIELDS));layout={'Gcn::VertexAttribute.size':9,'pointer_bytes':8,'little_endian':True};self.assertEqual(v.fetch_layout(p,layout)['offsets']['num_format'],8)
   layout['Gcn::VertexAttribute.size']=12
   with self.assertRaisesRegex(ValueError,'layout'):v.fetch_layout(p,layout)
   layout['Gcn::VertexAttribute.size']=9;p.write_text('\n'.join('u8 '+f+';'for f in reversed(v.FIELDS)))
   with self.assertRaisesRegex(ValueError,'declaration'):v.fetch_layout(p,layout)
 def test_asset_layout_dedup_never_guesses_shader_or_format(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'schemas.jsonl';streams=[dict(stream=0,stride=12,layout=dict(stride=12,members=[dict(stream=0,offset=0,type=2,semantic=0,index=0)]))]
   p.write_text('\n'.join(json.dumps(dict(id=str(i),complete_pso=False,asset_state=dict(vertex_streams=streams)))for i in range(2)))
   result=v.asset_layouts(p);self.assertEqual(len(result),1);self.assertEqual(result[0]['asset_schema_ids'],['0','1']);self.assertFalse(result[0]['fetch_shader_link_known']);self.assertFalse(result[0]['backend_vertex_formats_known'])
if __name__=='__main__':unittest.main(verbosity=2)
