"""Self-contained authored bytes for the real Python candidate/owned packet path.
No private metadata layout, game fixture, compiler, GPU or native reader needed.
"""
from pathlib import Path
import copy,hashlib,json,struct,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import complete_pipeline_inputs as tool
from game_module_inputs import module_constraints
from inventory_shader_cache import combine


def layout():
 d={'schema':1,'pointer_bytes':8,'size_t_bytes':8,'little_endian':True,'Profile.size':8,'profile_fields':[{'name':'feature','type':'bool'}],
 'Profile.feature':0,'Profile.feature.bytes':1,'GraphicsPipelineKey.size':128,'Backend::Bindings.size':12,'RuntimeInfo.size':64,'RuntimeInfo.props':0,'RuntimeInfo.hw':24,'RuntimeInfo.sw':40,
 'InfoPersistent.size':24,'PersistentSrtInfo.size':16,'Gcn::FetchShaderData.size':24,'Gcn::VertexAttribute.size':16,
 'VsAttribSpecialization.size':4,'BufferSpecialization.size':4,'ImageSpecialization.size':4,'FMaskSpecialization.size':4,'SamplerSpecialization.size':4}
 for t,fields in {'Backend::Bindings':{'unified':(0,4),'buffer':(4,4),'user_data':(8,4)},'RuntimeInfo':{'hw_stage':(12,4),'sw_stage':(16,4)},'RuntimeInfo::Properties':{'num_user_data':(0,4),'num_input_vgprs':(4,4),'num_allocated_vgprs':(8,4)},'InfoPersistent':{'pgm_hash':(0,8),'hw_stage':(8,4),'sw_stage':(12,4)},'PersistentSrtInfo':{'flattened_bufsize_dw':(0,4),'walker_func_size':(8,8)},'HwComputeRuntimeInfo':{'workgroup_size':(0,12)}}.items():
  for k,(at,size)in fields.items():d[t+'.'+k]=at;d[t+'.'+k+'.bytes']=size
 return d

def fixture(root,program=0x1234,permutation=0,profile_pad=0,flat_value=0):
 root.mkdir(parents=True);ph=combine(program,permutation);runtime=bytearray(64);struct.pack_into('<5I',runtime,0,16,0,4,6,5);struct.pack_into('<3I',runtime,24,1,1,1)
 info=struct.pack('<QII',program,6,5)+bytes(8);srt=struct.pack('<IIQ',16,0,0)
 spec=bytes(12)+runtime+struct.pack('<Q',128)+b'0'*128+struct.pack('<Q',0)+struct.pack('<5Q',0,0,0,0,0)
 metadata=struct.pack('<IIQQ',7,10,ph,permutation)+spec+info+struct.pack('<Q',16)+struct.pack('<16I',*([flat_value]*16))+srt
 (root/'profile.bin').write_bytes(struct.pack('<4I',0x42425043,10,7,5)+bytes([0,profile_pad])+bytes(6));(root/f'0x{program:016x}_{permutation}.spv').write_bytes(struct.pack('<5I',0x07230203,0x10000,0,1,0));(root/f'0x{ph:016x}.meta').write_bytes(metadata);(root/f'c_0x{ph:016x}.key').write_bytes(struct.pack('<IIQ',5,1,ph));return root

class CompleteInputs(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.p=Path(self.temp.name).resolve();(self.p/'state').mkdir();(self.p/'evidence').mkdir();self.index=self.p/'index.jsonl';self.record=dict(schema=1,identity=dict(program_hash='0000000000001234',hardware_stage='compute',hardware_stage_value=6,code_sha256='a'*64),variants=[dict(id='b'*64,facts={},code_bytes=12,evidence_refs=[])],complete_pso=False);self.index.write_text(json.dumps(self.record)+'\n')
 def tearDown(self):self.temp.cleanup()
 def cache(self,name,**kw):return fixture(self.p/'state'/name/'cache/CUSA03173-metal-controls',**kw)
 def run_complete(self,caches):return tool.complete(project=self.p,caches=caches,layout=layout(),static_index=self.index,output=self.p/'evidence/result')
 def test_actual_owned_packet_audit_and_source_preservation(self):
  c=self.cache('a');before={p.name:p.read_bytes()for p in c.iterdir()};r=self.run_complete([c]);self.assertEqual(r['status'],'passed-input-audit');self.assertEqual(r['counts']['packet_ready_inputs'],1);self.assertEqual(r['counts']['owned_packet_audits'],1);self.assertEqual(r['module_counts']['owned_gcn_consumer_templates'],1);self.assertEqual(before,{p.name:p.read_bytes()for p in c.iterdir()});self.assertFalse(r['GPU_started'])
 def test_exact_clones_deduplicate_with_all_raw_input_digests(self):
  r=self.run_complete([self.cache('a'),self.cache('b')]);self.assertEqual(r['counts']['packet_ready_inputs'],1);self.assertEqual(r['counts']['exact_raw_input_duplicates'],1)
 def test_profile_padding_different_is_not_silently_merged(self):
  r=self.run_complete([self.cache('a'),self.cache('b',profile_pad=7)]);self.assertEqual(r['counts']['packet_ready_inputs'],2)
 def test_unused_opaque_flat_different_is_kept_as_provenance_not_duplicate_worker_compile(self):
  r=self.run_complete([self.cache('a'),self.cache('b',flat_value=7)]);self.assertEqual(r['counts']['unique_raw_input_sets'],2);self.assertEqual(r['counts']['owned_packet_audits'],2);self.assertEqual(r['counts']['packet_ready_inputs'],1);self.assertEqual(r['counts']['exact_worker_payload_duplicates'],1)
 def test_permutation_indices_not_cross_cache_rewritten(self):
  r=self.run_complete([self.cache('a'),self.cache('b',permutation=1)]);self.assertEqual(r['counts']['packet_ready_inputs'],2)
 def test_changed_consumed_specialization_is_never_collapsed(self):
  a=self.cache('a');b=self.cache('b');path=next(b.glob('*.meta'));raw=bytearray(path.read_bytes());struct.pack_into('<I',raw,24+12,12);path.write_bytes(raw);r=self.run_complete([a,b]);self.assertEqual(r['counts']['packet_ready_inputs'],2)
 def test_changed_spirv_is_never_collapsed(self):
  a=self.cache('a');b=self.cache('b');path=next(b.glob('*.spv'));path.write_bytes(path.read_bytes()+bytes(4));r=self.run_complete([a,b]);self.assertEqual(r['counts']['packet_ready_inputs'],2)
 def test_missing_static_identity_is_deferred(self):
  r=self.run_complete([self.cache('a',program=0x7777)]);self.assertEqual(r['counts']['static_identity_gaps'],1);self.assertNotIn('packet_ready_inputs',r['counts'])
 def test_same_hash_stage_multiple_codes_is_not_guessed(self):
  second=copy.deepcopy(self.record);second['identity']['code_sha256']='c'*64;self.index.write_text(json.dumps(self.record)+'\n'+json.dumps(second)+'\n');r=self.run_complete([self.cache('a')]);self.assertEqual(r['counts']['static_identity_gaps'],1)
 def test_native_stage_disambiguates_shared_hash(self):
  second=copy.deepcopy(self.record);second['identity'].update(hardware_stage='vertex',hardware_stage_value=1,code_sha256='c'*64);self.index.write_text(json.dumps(self.record)+'\n'+json.dumps(second)+'\n');r=self.run_complete([self.cache('a')]);self.assertEqual(r['counts']['packet_ready_inputs'],1)
 def test_profile_missing_is_explicit_partial(self):
  c=self.cache('a');(c/'profile.bin').unlink();r=self.run_complete([c]);self.assertEqual(r['status'],'partial-input-audit');self.assertFalse(r['complete_scan']);self.assertIn('profile.bin',r['cache_errors'][0]['error'])
 def test_source_symlink_refused(self):
  c=self.cache('a');(c/'bad.spv').symlink_to(c/'profile.bin');r=self.run_complete([c]);self.assertEqual(r['status'],'partial-input-audit')
 def test_formal_or_outside_state_cache_rejected(self):
  c=fixture(self.p/'formal/CUSA03173-metal-controls')
  with self.assertRaisesRegex(ValueError,'private title QA'):self.run_complete([c])
 def test_existing_output_is_never_overwritten(self):
  (self.p/'evidence/result').mkdir()
  with self.assertRaisesRegex(ValueError,'new private'):self.run_complete([self.cache('a')])
 def test_memory_or_lock_guard_failure_propagates(self):
  def stop():raise RuntimeError('protected stop')
  with self.assertRaisesRegex(RuntimeError,'protected stop'):tool.complete(project=self.p,caches=[self.cache('a')],layout=layout(),static_index=self.index,output=self.p/'evidence/result',check=stop)
 def test_module_constraints_refuse_uncaptured_fetch_and_motion(self):
  meta=dict(logical_stage='vertex',stage='vertex',session_pointer_in_geometry_runtime=False,fetch={},motion_vertex_cache_load_rejected=True,context={},flattened_words=16,srt_declared_flattened_words=16,walker_bytes=0);reasons=module_constraints(meta);self.assertIn('fetch-register-reads-not-captured',reasons);self.assertIn('session-motion-addresses-not-captured',reasons)
if __name__=='__main__':unittest.main(verbosity=2)
