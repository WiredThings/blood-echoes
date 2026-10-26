"""Authored metadata only: no game, GPU, native deserializer or code execution."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import static_program_links as links
import gnm_shader_metadata as gnm
from test_gnm_shader_metadata import fixture,gs_fixture

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'vendor/metal_api/shadps4/shader_recompiler/runtime_info.h'

def header(stage=1,code='a'*64,program='0000000000001234',binary=None):
 h=gnm.shader_headers(fixture(stage,binary_type=binary))['headers'][0]
 h.update(program_hash=program,code_sha256=code);return h

def catalog(headers):return dict(packages=[dict(kind='shader',debug=False,path='author.shaderbnd',entries=[dict(name='author.vpo',binary_info=[dict(program_hash=h['program_hash'],code_sha256=h['code_sha256'],binary_type_raw=h['binary_type'],code_prefix_verified=True,code_bytes=64)for h in headers])])])

class Links(unittest.TestCase):
 def run_headers(self,headers):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'headers.jsonl';p.write_text(json.dumps(dict(package='author.shaderbnd',entry='author.vpo',entry_sha256='d'*64,headers=headers,rejected=[],unassociated_programs=[]))+'\n');return links.collect(p,catalog(headers),RUNTIME)
 def test_native_stage_is_not_binary_or_file_enum(self):
  # Compute file-stage4/BinaryInfo4 is native hardware-stage6.
  rows,_,_=self.run_headers([header(4)]);self.assertEqual(rows[0]['identity']['hardware_stage_value'],6);self.assertEqual(rows[0]['variants'][0]['facts']['file_stage'],4)
 def test_shared_hash_different_hardware_stages_remains_two_codes(self):
  g=gnm.shader_headers(gs_fixture())['headers'];g[0].update(program_hash='0000000000001234',code_sha256='a'*64);g[1].update(program_hash='0000000000001234',code_sha256='b'*64)
  records,_,summary=self.run_headers(g);self.assertEqual(summary['hashes_with_multiple_codes'],1);self.assertEqual(summary['stage_pairs_with_multiple_codes'],0)
  self.assertEqual(links.resolve(records,g[0]['program_hash'],'geometry')['identity']['code_sha256'],'a'*64);self.assertEqual(links.resolve(records,g[0]['program_hash'],'vertex')['identity']['code_sha256'],'b'*64)
  self.assertEqual(links.resolve(records,g[0]['program_hash'],'geometry','b'*64)['status'],'missing-static-code')
 def test_same_stage_multicode_is_not_guessed(self):
  records,_,s=self.run_headers([header(code='a'*64),header(code='b'*64)]);self.assertEqual(s['stage_pairs_with_multiple_codes'],1);self.assertEqual(links.resolve(records,'0000000000001234','vertex')['status'],'ambiguous-static-code')
 def test_default_variants_are_never_fieldwise_merged(self):
  a=header();b=copy.deepcopy(a);b['registers']['rsrc2']^=2;b['runtime_property_defaults']=gnm.properties(b['registers']);records,_,_=self.run_headers([a,b]);self.assertEqual(len(records),1);variants=records[0]['variants'];self.assertEqual(len(variants),2);self.assertNotEqual(variants[0]['id'],variants[1]['id']);self.assertTrue(all(len(v['evidence_refs'])==1 for v in variants))
 def test_references_and_raw_relocations_preserved(self):
  h=header();records,_,_=self.run_headers([h,h]);r=records[0];self.assertEqual(r['header_count'],2);v=r['variants'][0];self.assertEqual(v['facts']['registers'],h['registers']);self.assertEqual(len(v['evidence_refs']),2);self.assertEqual(v['code_bytes'],64);self.assertFalse(r['complete_pso'])
 def test_missing_or_bad_source_provenance_refused(self):
  h=header();h['stage']='geometry'
  with self.assertRaisesRegex(ValueError,'file-stage'):self.run_headers([h])
 def test_unsupported_binary_type_refused(self):
  h=header();h['binary_type']=4
  with self.assertRaisesRegex(ValueError,'BinaryInfo'):self.run_headers([h])
 def test_changed_defaults_and_bool_register_refused(self):
  h=header();h['runtime_property_defaults']['num_user_data']+=1
  with self.assertRaisesRegex(ValueError,'decode changed'):self.run_headers([h])
  h=header();h['registers']['rsrc1']=True
  with self.assertRaisesRegex(ValueError,'uint32'):self.run_headers([h])
 def geometry(self):
  g=gnm.shader_headers(gs_fixture())['headers'];g[0].update(program_hash='0000000000001234',code_sha256='a'*64);g[1].update(program_hash='0000000000001234',code_sha256='b'*64);records,_,_=self.run_headers(g);table=links.lookup_table(records);return records,table,table['0000000000001234','geometry'][0]
 def test_geometry_copy_joins_exact_embedded_container_and_word_count(self):
  _,table,main=self.geometry();r=links.geometry_copy_provenance(main,dict(words=16,hash='0000000000001234'),table);self.assertEqual(r['status'],'owned-static-copy-source-identified');self.assertEqual(r['identity']['code_sha256'],'b'*64);self.assertFalse(r['serialized_pointer_followed']);self.assertFalse(r['native_deserialization_authorized'])
 def test_geometry_copy_wrong_length_or_different_source_refused(self):
  _,table,main=self.geometry();self.assertEqual(links.geometry_copy_provenance(main,dict(words=17,hash='0000000000001234'),table)['status'],'copy-length-or-container-provenance-mismatch');table['0000000000001234','vertex'][0]['variants'][0]['evidence_refs'][0]['source_sha256']='c'*64;self.assertEqual(links.geometry_copy_provenance(main,dict(words=16,hash='0000000000001234'),table)['status'],'copy-length-or-container-provenance-mismatch')
 def test_geometry_copy_does_not_substitute_ordinary_vertex_or_ambiguous_hash(self):
  _,table,main=self.geometry();table['0000000000001234','vertex'][0]['variants'][0]['facts']['embedded_gs_copy']=False;self.assertEqual(links.geometry_copy_provenance(main,dict(words=16,hash='0000000000001234'),table)['status'],'copy-length-or-container-provenance-mismatch');table['0000000000001234','vertex'].append(copy.deepcopy(table['0000000000001234','vertex'][0]));self.assertEqual(links.geometry_copy_provenance(main,dict(words=16,hash='0000000000001234'),table)['status'],'missing-or-ambiguous-copy-code')
 def test_semantic_arrays_cannot_bypass_record_budget(self):
  h=header();h['input_semantic_words']=[0]*256
  with self.assertRaisesRegex(ValueError,'semantic words'):self.run_headers([h])
  h=header();h['uninterpreted_stage_words']=[0]*33
  with self.assertRaisesRegex(ValueError,'word budget'):self.run_headers([h])
 def test_native_enum_reordering_is_detected(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve()/'runtime.h';p.write_text('enum class HwStage : u32 {Vertex,Fragment,Geometry,Export,Hull,Local,Compute};')
   with self.assertRaisesRegex(ValueError,'decoder disagree'):links.runtime_stages(p)
 def test_new_unmodeled_header_field_cannot_silently_disappear(self):
  h=header();h['unmodeled_baked_state']=7
  with self.assertRaisesRegex(ValueError,'unmodeled static'):self.run_headers([h])
 def test_symlink_input_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d).resolve();(p/'real').write_text('{}');(p/'link').symlink_to(p/'real')
   with self.assertRaisesRegex(ValueError,'canonical regular'):links.read_json(p/'link')

if __name__=='__main__':unittest.main(verbosity=2)
