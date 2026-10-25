from pathlib import Path
import hashlib,json,struct,sys,tempfile,unittest
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'tools'))
from decode_shader_cache import decode_meta,decode_key,decode_inventory,semantic_profile
from game_shader_catalog import CatalogError
L=json.loads(Path(json.loads((P/'evidence/metadata-layout-current.json').read_text())['layout']).read_text())
assert 'authored_writer_fixtures'in L,'run tools/build_metadata_layout.py first'
def data(mode):
 fixture=L['authored_writer_fixtures'][mode];b=Path(fixture['path']).read_bytes();assert hashlib.sha256(b).hexdigest()==fixture['sha256'];return b

def cohort(root,wrong_stage=False):
 meta=decode_meta(data('plain'),L);mh=meta['permutation_hash'];graph=bytearray(data('graphics'));base=8+L['GraphicsPipelineKey.stage_hashes'];graph[base:base+8*L['MaxShaderStages']]=bytes(8*L['MaxShaderStages']);struct.pack_into('<Q',graph,base+(0 if wrong_stage else 3)*8,int(mh,16))
 def put(name,content):
  (root/name).write_bytes(content);return dict(name=name,sha256=hashlib.sha256(content).hexdigest())
 profile=put('profile.bin',struct.pack('<4I',0x42425043,10,7,5)+data('profile-padding')[:L['Profile.size']]);m=put('authored.meta',data('plain'));v=put('authored.spv',struct.pack('<5I',0x07230203,0x10000,0,1,0));k=put('g_authored.key',graph)
 return dict(cache=str(root),profile=profile,paired_specializations=[dict(program_hash=meta['program_hash'],permutation=meta['permutation'],meta=m,spirv=v)],pipeline_keys=[k])

class DecodeControls(unittest.TestCase):
 def test_native_profile_padding_equality(self):
  raw=data('profile-padding');size=L['Profile.size'];header=struct.pack('<4I',0x42425043,10,7,5);a=semantic_profile(header+raw[:size],L);b=semantic_profile(header+raw[size:],L);self.assertTrue(L['authored_writer_fixtures']['profile-padding']['native_equality']);self.assertNotEqual(a['raw_sha256'],b['raw_sha256']);self.assertEqual(a['semantic_sha256'],b['semantic_sha256'])
 def test_profile_feature_change_is_not_padding(self):
  raw=bytearray(data('profile-padding')[:L['Profile.size']]);header=struct.pack('<4I',0x42425043,10,7,5);a=semantic_profile(header+raw,L);raw[L['Profile.support_int64']]=1;self.assertNotEqual(a['semantic_sha256'],semantic_profile(header+raw,L)['semantic_sha256'])
 def test_canonical_writer_plain(self):
  m=decode_meta(data('plain'),L);self.assertEqual(m['program_hash'],'0000000011223344');self.assertEqual(m['stage'],'vertex');self.assertIsNone(m['fetch']);self.assertFalse(m['walker_registered_or_executed'])
 def test_canonical_writer_fetch_padding(self):
  m=decode_meta(data('fetch'),L);self.assertEqual(len(m['fetch']['attributes']),1);self.assertEqual(m['canonical_writer_zero_tail_bytes'],L['authored_writer_fixtures']['fetch']['tail'])
 def test_motion_vertex_rejected_for_preload(self):self.assertTrue(decode_meta(data('motion'),L)['motion_vertex_cache_load_rejected'])
 def test_geometry_pointer_not_followed(self):
  m=decode_meta(data('geometry'),L);self.assertTrue(m['session_pointer_in_geometry_runtime']);self.assertFalse(m['context']['geometry_copy']['pointer_followed'])
 def test_truncated_blob_rejected(self):
  with self.assertRaises(CatalogError):decode_meta(data('plain')[:-5],L)
 def test_unexplained_zero_tail_rejected(self):
  with self.assertRaises(CatalogError):decode_meta(data('plain')+bytes(8),L)
 def test_huge_bitset_rejected(self):
  a=bytearray(data('plain'));at=24+L['Backend::Bindings.size']+L['RuntimeInfo.size'];struct.pack_into('<Q',a,at,0xffffffffffffffff)
  with self.assertRaises(CatalogError):decode_meta(a,L)
 def test_nonzero_canonical_tail_rejected(self):
  self.assertGreater(L['authored_writer_fixtures']['fetch']['tail'],0);a=bytearray(data('fetch'));a[-1]=1
  with self.assertRaises(CatalogError):decode_meta(a,L)
 def test_graphics_static_input_decode(self):
  k=decode_key(data('graphics'),L);self.assertEqual(k['color_attachments'],2);self.assertEqual(k['vertex_attributes'],[dict(location=2,per_instance=1,divisor=3)])
 def test_compute_decode(self):self.assertEqual(decode_key(data('compute'),L)['stage_permutation_hashes'],['0000000000001234'])
 def test_invalid_vertex_count(self):
  a=bytearray(data('graphics'));struct.pack_into('<I',a,8+L['GraphicsPipelineKey.size'],999)
  with self.assertRaises(CatalogError):decode_key(a,L)
 def test_complete_cohort_graph(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();inv=cohort(p);r=decode_inventory(inv,L);self.assertFalse(r['errors']);self.assertEqual(r['summary']['decoded_metadata'],1);self.assertEqual(r['summary']['pipelines_with_stage_mismatch'],0)
 def test_spirv_changed_after_inventory(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();inv=cohort(p);(p/'authored.spv').write_bytes(bytes(20));r=decode_inventory(inv,L);self.assertEqual(len(r['errors']),1);self.assertEqual(r['summary']['pipelines_missing_metadata'],1)
 def test_wrong_stage_not_accepted(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();r=decode_inventory(cohort(p,True),L);self.assertEqual(r['summary']['pipelines_with_stage_mismatch'],1)
 def test_profile_cross_directory_refused(self):
  with tempfile.TemporaryDirectory()as name:
   p=Path(name).resolve();inv=cohort(p);inv['profile']['name']='../profile.bin'
   with self.assertRaises(CatalogError):decode_inventory(inv,L)
 def test_invalid_instance_rate(self):
  a=bytearray(data('graphics'));struct.pack_into('<I',a,8+L['GraphicsPipelineKey.size']+8,2)
  with self.assertRaises(CatalogError):decode_key(a,L)

if __name__=='__main__':unittest.main()
