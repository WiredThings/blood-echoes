"""Authored GS metadata/source pair; no pointer read, GPU or private game fixture."""
from pathlib import Path
import copy,hashlib,json,struct,sys,tempfile,unittest
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/'tools')]
from test_pipeline_input_completion import layout as base_layout
from test_game_shader_catalog import shader,binder,wrap,sfo
from game_shader_catalog import game_identity
from game_pso_owned_audit import metadata_sections
from inventory_shader_cache import combine
from static_program_links import lookup_table
from owned_geometry_inputs import sanitized_spec,freeze_geometry
from run_owned_geometry_contracts import require_geometry_result

def layout():
 l=base_layout();l.update({'RuntimeInfo.size':280,'RuntimeInfo.sw_stage':0,'RuntimeInfo.hw_stage':4,'RuntimeInfo.props':8,'RuntimeInfo.sw':36,'RuntimeInfo.hw':64,'RuntimeInfo::Properties.num_user_data':16,'RuntimeInfo::Properties.num_input_vgprs':20,'RuntimeInfo::Properties.num_allocated_vgprs':24,'HwGeometryRuntimeInfo.vs_copy':56,'HwGeometryRuntimeInfo.vs_copy.bytes':16,'HwGeometryRuntimeInfo.vs_copy_hash':72,'HwGeometryRuntimeInfo.vs_copy_hash.bytes':8});return l
def reference(path,raw):
 path.write_bytes(raw);return dict(path=str(path),name=path.name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
def metadata(pointer=0xDEADBEEF,words=4):
 runtime=bytearray(280);struct.pack_into('<II',runtime,0,4,2);struct.pack_into('<3I',runtime,24,16,0,4);hw=64
 for at,value in [(0,0),(16,1),(20,8),(24,28),(28,12),(32,1),(52,3)]:struct.pack_into('<I',runtime,hw+at,value)
 struct.pack_into('<4I',runtime,hw+36,2,2,2,2);struct.pack_into('<QQQ',runtime,hw+56,pointer,words,0x1234)
 spec=bytes(12)+runtime+struct.pack('<Q',128)+b'0'*128+struct.pack('<6Q',0,0,0,0,0,0);info=struct.pack('<QII',0x1234,2,4)+bytes(8)
 return struct.pack('<IIQQ',7,10,combine(0x1234,0),0)+spec+info+struct.pack('<Q',16)+bytes(64)+struct.pack('<IIQ',16,0,0)

class GeometryInputs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.game=self.root/'game';(self.game/'sce_sys').mkdir(parents=True);(self.game/'sce_sys/param.sfo').write_bytes(sfo());self.main=shader(fill=0);self.copy=shader(fill=1);entry=self.main+self.copy;container=wrap(binder(entry));package='authored.shaderbnd.dcx';(self.game/package).write_bytes(container);sha=hashlib.sha256(entry).hexdigest();self.catalog=dict(game_root=str(self.game),game_identity=game_identity(sfo()),packages=[dict(path=package,kind='shader',debug=False,sha256=hashlib.sha256(container).hexdigest())])
  main_ref=dict(package=package,entry='Authored.vpo',source_sha256=sha,header_offset=36,code_offset=0);copy_ref=dict(package=package,entry='Authored.vpo',source_sha256=sha,header_offset=None,outer_header_offset=36,code_offset=60)
  self.index=[dict(identity=dict(program_hash='0000000000001234',hardware_stage='geometry',hardware_stage_value=2,code_sha256=hashlib.sha256(self.main[:16]).hexdigest()),variants=[dict(id='m',facts=dict(file_stage=3,binary_type=5),code_bytes=16,evidence_refs=[main_ref])]),dict(identity=dict(program_hash='0000000000001234',hardware_stage='vertex',hardware_stage_value=1,code_sha256=hashlib.sha256(self.copy[:16]).hexdigest()),variants=[dict(id='c',facts=dict(file_stage=1,binary_type=6,embedded_gs_copy=True),code_bytes=16,evidence_refs=[copy_ref])])]
  self.meta_ref=reference(self.root/'meta.bin',metadata());self.spv_ref=reference(self.root/'spv.bin',struct.pack('<5I',0x07230203,0x10000,0,1,0));self.profile_ref=reference(self.root/'profile.bin',struct.pack('<4I',0x42425043,10,7,5)+bytes(8))
 def tearDown(self):self.tmp.cleanup()
 def freeze(self):return freeze_geometry(metadata_ref=self.meta_ref,spirv_ref=self.spv_ref,profile_ref=self.profile_ref,layout=layout(),game_root=self.game,catalog=self.catalog,table=lookup_table(self.index),output=self.root/'frozen')
 def test_real_readers_freeze_owned_copy_and_zero_entire_retired_span(self):
  before=Path(self.meta_ref['path']).read_bytes();p=self.freeze();span=p['spec_rebinding']['runtime_span_offset'];spec=Path(p['spec']['path']).read_bytes();self.assertEqual(spec[span:span+16],bytes(16));self.assertEqual(p['gs_copy']['words'],4);self.assertEqual(Path(p['gs_copy']['code']['path']).read_bytes(),self.copy[:16]);self.assertEqual(Path(p['code']['path']).read_bytes(),self.main[:16]);self.assertFalse(p['spec_rebinding']['retired_pointer_exported']);self.assertEqual(p['source_metadata']['original_span_bytes_forwarded'],0);self.assertEqual(before,Path(self.meta_ref['path']).read_bytes());self.assertNotIn('deadbeef',json.dumps(p).lower())
 def test_pointer_value_never_changes_sanitized_input(self):
  a=metadata(0xDEADBEEF);b=metadata(0xBADCAFE);ma,sa=metadata_sections(a,layout());mb,sb=metadata_sections(b,layout());xa,pa,_=sanitized_spec(a,ma,sa,layout());xb,pb,_=sanitized_spec(b,mb,sb,layout());self.assertEqual(xa,xb);self.assertNotEqual(pa['original_spec_sha256'],pb['original_spec_sha256'])
 def test_span_length_wrong_source_not_closed(self):
  self.meta_ref=reference(self.root/'meta.bin',metadata(words=5))
  with self.assertRaisesRegex(ValueError,'owned-copy provenance'):self.freeze()
 def test_copy_wrong_hardware_stage_or_ambiguous_identity_refused(self):
  self.index[1]['identity']['hardware_stage']='geometry'
  with self.assertRaisesRegex(ValueError,'GS main static identity'):self.freeze()
 def test_two_copy_code_candidates_not_guessed(self):
  other=copy.deepcopy(self.index[1]);other['identity']['code_sha256']='f'*64;self.index.append(other)
  with self.assertRaisesRegex(ValueError,'owned-copy provenance'):self.freeze()
 def test_cross_container_or_outer_header_refused(self):
  self.index[1]['variants'][0]['evidence_refs'][0]['outer_header_offset']=40
  with self.assertRaisesRegex(ValueError,'owned-copy provenance'):self.freeze()
 def test_copy_hash_changed_refused(self):
  raw=bytearray(metadata());struct.pack_into('<Q',raw,24+12+64+72,0x9999);self.meta_ref=reference(self.root/'meta.bin',raw)
  with self.assertRaisesRegex(ValueError,'owned-copy provenance'):self.freeze()
 def test_geometry_enum_and_array_count_rejected_before_native(self):
  for offset,value in [(0,4),(16,0),(20,0),(32,8),(52,99)]:
   raw=bytearray(metadata());struct.pack_into('<I',raw,24+12+64+offset,value);meta,sec=metadata_sections(raw,layout())
   with self.subTest(offset=offset),self.assertRaises(ValueError):sanitized_spec(raw,meta,sec,layout())
 def test_canonical_permutation_hash_required(self):
  raw=bytearray(metadata());struct.pack_into('<Q',raw,8,0);meta,sec=metadata_sections(raw,layout())
  with self.assertRaisesRegex(ValueError,'permutation hash'):sanitized_spec(raw,meta,sec,layout())
 def test_existing_output_never_overwritten(self):
  (self.root/'frozen').mkdir();(self.root/'frozen/keep').write_text('yes')
  with self.assertRaisesRegex(ValueError,'new owned'):self.freeze()
  self.assertEqual((self.root/'frozen/keep').read_text(),'yes')
 def test_changed_canonical_span_layout_refused(self):
  raw=metadata();l=layout();meta,sec=metadata_sections(raw,l);l['HwGeometryRuntimeInfo.vs_copy']=48
  with self.assertRaisesRegex(ValueError,'layout anchors'):sanitized_spec(raw,meta,sec,l)
 def test_explicit_native_rebound_proof_and_exact_owned_size_required(self):
  p={'gs_copy':{'code':{'bytes':172}}};require_geometry_result(dict(owned_geometry_copy_rebound=True,geometry_copy_bytes=172),p)
  for r in[{},dict(owned_geometry_copy_rebound=False,geometry_copy_bytes=172),dict(owned_geometry_copy_rebound=True,geometry_copy_bytes=360)]:
   with self.subTest(result=r),self.assertRaises(ValueError):require_geometry_result(r,p)
if __name__=='__main__':unittest.main(verbosity=2)
