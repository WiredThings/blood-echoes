import importlib.util,json,pathlib,sys,tempfile,unittest
from unittest.mock import patch
T=pathlib.Path(__file__).resolve().parents[1]/'tools';sys.path.insert(0,str(T))
import build_resource_cpu_runtime as b
import cpu_cache_build_identity as identity

class CpuBuilder(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.addCleanup(self.tmp.cleanup)
 def fixture(self):
  s=self.root/'source';s.mkdir();source=s/'unit.cpp';source.write_text('void x() {}');lib=self.root/'lib.dylib';lib.write_bytes(b'library')
  units=[dict(source=str(source),relative_source='unit.cpp',object=str(self.root/f'{i}.o'),command=['c++','-DBB_DEV_FETCH_CAPTURE=1','-o',str(self.root/f'{i}.o')])for i in range(230)]
  for n in sorted(b.CAPTURE):
   f=s/n;f.write_text('capture');o=str(self.root/(n+'.o'));units.append(dict(source=str(f),relative_source=n,object=o,command=['c++','-DBB_DEV_FETCH_CAPTURE=1','-o',o]))
  files={str(f.relative_to(s)):dict(bytes=f.stat().st_size,sha256=b.sha(f))for f in s.iterdir()};g=dict(source_identity='a'*64,gpu_units=units,snapshot=str(s),source_closure=dict(files=files),external_header_inputs={},external_link_inputs={});gp=self.root/'gpu-plan.json';gp.write_text(json.dumps(g));r=dict(plan_sha256=b.sha(gp),source_identity='a'*64,coherent_renderer=True,targeted_compiler_overrides=False,diagnostic_only=False,all_GPU_units_rebuilt=235,source_closure=g['source_closure'],library=str(lib),library_sha256=b.sha(lib),compiled_header_sha256={});rp=self.root/'gpu-result.json';rp.write_text(json.dumps(r));return gp,rp,g,r
 def test_coherent_exact235(self):
  gp,rp,g,r=self.fixture();self.assertEqual(len(b.coherent_inputs(gp,rp)[0]['gpu_units']),235)
 def test_wrong_result_sha_rejected(self):
  gp,rp,g,r=self.fixture();r['plan_sha256']='0'*64;rp.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'identity'):b.coherent_inputs(gp,rp)
 def test_targeted_not_coherent(self):
  gp,rp,g,r=self.fixture();r['targeted_compiler_overrides']=True;rp.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'non-diagnostic'):b.coherent_inputs(gp,rp)
 def test_changed_snapshot_rejected(self):
  gp,rp,g,r=self.fixture();(self.root/'source/unit.cpp').write_text('changed')
  with self.assertRaisesRegex(ValueError,'changed'):b.coherent_inputs(gp,rp)
 def test_extra_snapshot_file_rejected(self):
  gp,rp,g,r=self.fixture();(self.root/'source/new.h').write_text('new')
  with self.assertRaisesRegex(ValueError,'membership'):b.coherent_inputs(gp,rp)
 def test_compiled_header_change_rejected(self):
  gp,rp,g,r=self.fixture();h=self.root/'sdk.h';h.write_text('sdk');r['compiled_header_sha256']={str(h):'0'*64};rp.write_text(json.dumps(r))
  with self.assertRaisesRegex(ValueError,'compiled header'):b.coherent_inputs(gp,rp)
 def test_existing_output_not_reused(self):
  (self.root/'evidence').mkdir();p=self.root/'evidence/resource-cpu-execution-123';p.mkdir()
  with self.assertRaisesRegex(ValueError,'preserve'):b.output_root(self.root,p,True)
 def test_identity_domain_separation_and_order(self):
  self.assertEqual(identity.combine('runtime',{'a':'a'*64,'b':'b'*64}),identity.combine('runtime',{'b':'b'*64,'a':'a'*64}));self.assertNotEqual(identity.combine('runtime',{'a':'a'*64}),identity.combine('bridge',{'a':'a'*64}))
 def test_identity_symlink_rejected(self):
  f=self.root/'h';f.write_text('h');l=self.root/'l';l.symlink_to(f)
  with self.assertRaisesRegex(ValueError,'canonical'):identity.digest_file(l,[self.root])
 def test_identity_outside_roots_rejected(self):
  f=self.root/'h';f.write_text('h')
  with self.assertRaisesRegex(ValueError,'outside'):identity.digest_file(f,[self.root/'child'])
 def test_private_relative_vendor_headers_preserved(self):
  p=self.root/'project';o=self.root/'snapshot';stage=self.root/'private'
  for base in(p,o):
   (base/'src').mkdir(parents=True);(base/'generated').mkdir()
  for base,rel in[(o,'vendor/bb_runtime/src/runtime_restart_protocol.h'),(p,'vendor/dev_world_warper/world_warper_state.h')]:
   f=base/rel;f.parent.mkdir(parents=True);f.write_text('exact source')
  refs,_=b.stage_small_sources(p,o,stage)
  for rel in['vendor/bb_runtime/src/runtime_restart_protocol.h','vendor/dev_world_warper/world_warper_state.h']:
   f=stage/rel;self.assertTrue(f.is_file());self.assertFalse(f.is_symlink());self.assertEqual(b.sha(f),refs[str(f)])
 def test_current_cpu_and_generated_override_shared_fallback(self):
  p=self.root/'project';o=self.root/'snapshot';c=self.root/'current';stage=self.root/'private'
  for base in(p,o,c):
   (base/'src').mkdir(parents=True);(base/'generated').mkdir()
  (p/'generated/gpu_adapters.cpp').write_text('old generated')
  (c/'generated/gpu_adapters.cpp').write_text('current generated')
  rel='vendor/aether/src/core/fex/fex_guest_engine.h'
  for base,text in[(p,'old engine'),(c,'current engine')]:
   f=base/rel;f.parent.mkdir(parents=True);f.write_text(text)
  refs,_=b.stage_small_sources(p,o,stage,c)
  self.assertEqual((stage/'generated/gpu_adapters.cpp').read_text(),'current generated')
  self.assertEqual((stage/rel).read_text(),'current engine')
  self.assertIn(str(c/rel),refs)
 def test_cpu_third_party_not_lost_to_gpu_src_only_snapshot(self):
  p=self.root/'project';o=self.root/'snapshot';c=self.root/'current';stage=self.root/'private'
  for base in(p,o,c):
   (base/'src').mkdir(parents=True);(base/'generated').mkdir()
  rel='vendor/bb_runtime/third_party/LibAtrac9/C/src/libatrac9.h'
  f=c/rel;f.parent.mkdir(parents=True);f.write_text('current atrac header')
  refs,_=b.stage_small_sources(p,o,stage,c)
  self.assertEqual((stage/rel).read_text(),'current atrac header')
  self.assertIn(str(f),refs)
 def shared_fixture(self):
  o=self.root/'snapshot';c=self.root/'current';(o/'src').mkdir(parents=True);(c/'src').mkdir(parents=True)
  for name,text in [('unit.cpp','GPU TU'),('shared.h','GPU header'),('cpu_only.h','old CPU header'),('helper.mm','old native helper')]:
   (o/'src'/name).write_text(text);(c/'src'/name).write_text(text)
  obj=self.root/'gpu.o';dep=self.root/'gpu.d';source=o/'src/unit.cpp';header=o/'src/shared.h';dep.write_text(str(obj)+': '+str(source)+' '+str(header)+'\n')
  g=dict(gpu_units=[dict(source=str(source),relative_source='src/unit.cpp',object=str(obj),depfile=str(dep))],source_closure=dict(files={'src/unit.cpp':dict(sha256=b.sha(source))}))
  # Aggregated native helper entries must never create a GPU sharing rule.
  r=dict(compiled_header_sha256={str(f):b.sha(f)for f in[source,header,o/'src/helper.mm']})
  return o,c,g,r
 def test_current_cpu_header_wins_over_old_gpu_snapshot(self):
  o,c,g,r=self.shared_fixture();(c/'src/cpu_only.h').write_text('new CPU semantics');p=self.root/'project';(p/'src').mkdir(parents=True);(p/'generated').mkdir()
  shared=b.current_cpu_gpu_shared_inputs(c,o,g,r);stage=self.root/'private';refs,_=b.stage_small_sources(p,o,stage,c)
  self.assertEqual((stage/'src/cpu_only.h').read_text(),'new CPU semantics');self.assertIn(str(c/'src/cpu_only.h'),refs)
  self.assertNotIn(str(c/'src/cpu_only.h'),shared['current_sha256'])
 def test_actual_gpu_header_current_change_rejected(self):
  o,c,g,r=self.shared_fixture();(c/'src/shared.h').write_text('ABI change')
  with self.assertRaisesRegex(ValueError,'actual GPU dependency'):b.current_cpu_gpu_shared_inputs(c,o,g,r)
 def test_actual_gpu_source_current_change_rejected(self):
  o,c,g,r=self.shared_fixture();(c/'src/unit.cpp').write_text('GPU code change')
  with self.assertRaisesRegex(ValueError,'actual GPU dependency'):b.current_cpu_gpu_shared_inputs(c,o,g,r)
 def test_helper_only_main_is_not_gpu_shared_gate(self):
  o,c,g,r=self.shared_fixture();(c/'src/helper.mm').write_text('verified CPU helper repair');shared=b.current_cpu_gpu_shared_inputs(c,o,g,r)
  self.assertNotIn(str(c/'src/helper.mm'),shared['current_sha256'])
 def test_depfile_wrong_object_target_rejected(self):
  o,c,g,r=self.shared_fixture();path=pathlib.Path(g['gpu_units'][0]['depfile']);path.write_text('other.o: '+str(o/'src/unit.cpp')+'\n')
  with self.assertRaisesRegex(ValueError,'target identity'):b.current_cpu_gpu_shared_inputs(c,o,g,r)
 def test_original_target_counts_unchanged(self):
  self.assertEqual(sum(b.EXPECTED.values()),30);self.assertEqual(b.EXPECTED['bb_native_runtime'],23)
if __name__=='__main__':unittest.main()
